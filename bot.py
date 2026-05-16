"""
Polymarket 量化交易系统 - 主程序
"""
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime

from config import Config
from market_scanner import MarketScanner, MarketInfo
from risk_manager import RiskManager, Position, TradeRecord
from executor import OrderExecutor
from notifier import Notifier

# 配置日志
def setup_logging(level: str = "INFO"):
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # 控制台
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))

    # 文件
    file_handler = logging.FileHandler(
        os.path.join(log_dir, f"trading_{datetime.now().strftime('%Y%m%d')}.log"),
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(log_format, date_format))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[console_handler, file_handler],
    )


logger = logging.getLogger("polymarket")


class PolymarketBot:
    """Polymarket 量化交易机器人"""

    def __init__(self):
        # 加载配置
        self.config = Config()
        errors = self.config.validate()
        if errors:
            for e in errors:
                logger.error(f"配置错误: {e}")
            if not self.config.DRY_RUN:
                logger.critical("实盘模式配置错误，退出")
                sys.exit(1)

        # 初始化模块
        self.scanner = MarketScanner(self.config)
        self.risk = RiskManager(self.config)
        self.executor = OrderExecutor(self.config)
        self.notifier = Notifier(self.config)

        # 运行状态
        self.running = False
        self.scan_count = 0
        self.start_time = None
        self.state_file = os.path.join(
            os.path.dirname(__file__), "bot_state.json"
        )

        # 加载持久化状态
        self._load_state()

    def _load_state(self):
        """加载持久化状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    state = json.load(f)
                self.risk.total_pnl = state.get("total_pnl", 0.0)
                self.risk.trade_count = state.get("trade_count", 0)
                logger.info(
                    f"状态已恢复: 累计PnL=${self.risk.total_pnl:.2f}, "
                    f"交易次数={self.risk.trade_count}"
                )
            except Exception as e:
                logger.warning(f"加载状态失败: {e}")

    def _save_state(self):
        """保存持久化状态"""
        state = {
            "total_pnl": self.risk.total_pnl,
            "trade_count": self.risk.trade_count,
            "last_save": datetime.now().isoformat(),
        }
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning(f"保存状态失败: {e}")

    def start(self):
        """启动交易机器人"""
        mode = "模拟" if self.config.DRY_RUN else "实盘"
        print()
        print("=" * 60)
        print("  Polymarket 量化交易系统")
        print("=" * 60)
        print(f"  模式:     {mode}")
        print(f"  初始资金: ${self.config.INITIAL_CAPITAL:.0f}")
        print(f"  资金上限: ${self.config.MAX_CAPITAL:.0f}")
        print(f"  单笔比例: {self.config.TRADE_SIZE_PERCENT}%")
        print(f"  最大持仓: {self.config.MAX_POSITIONS}个")
        print(f"  扫描间隔: {self.config.SCAN_INTERVAL}秒")
        print()
        print("  策略:")
        if self.config.ENABLE_ARBITRAGE:
            print(f"    ✅ YES+NO套利 (最小套利空间{self.config.ARB_MIN_SPREAD}%)")
        if self.config.ENABLE_MEAN_REVERSION:
            print(f"    ✅ 均值回归 (极端价格<={self.config.MEAN_REV_LOW_THRESHOLD}或>={self.config.MEAN_REV_HIGH_THRESHOLD})")
        if self.config.ENABLE_EVENT_DRIVEN:
            print(f"    ✅ 事件驱动 (24h成交量>${self.config.EVENT_MIN_VOLUME_24H:,.0f}+1h变化>{self.config.EVENT_PRICE_CHANGE_THRESHOLD}%)")
        if self.config.ENABLE_COPY_TRADING:
            print(f"    ✅ 智能跟单")
        print()
        print("  风控:")
        print(f"    止损: {self.config.STOP_LOSS_PERCENT}% | 止盈: {self.config.TAKE_PROFIT_PERCENT}%")
        print(f"    日亏损限制: {self.config.DAILY_LOSS_LIMIT}% | 周亏损限制: {self.config.WEEKLY_LOSS_LIMIT}%")
        print()
        print("  按 Ctrl+C 停止")
        print("=" * 60)
        print()

        self.notifier.system_alert(
            f"交易系统启动 | {mode} | ${self.config.INITIAL_CAPITAL:.0f}"
        )

        # 初始化执行器
        if not self.executor.initialize():
            logger.error("执行器初始化失败，将在模拟模式下运行")
            self.config.DRY_RUN = True

        self.running = True
        self.start_time = time.time()

        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # 主循环
        self._main_loop()

    def _signal_handler(self, signum, frame):
        """优雅退出"""
        logger.info("收到退出信号，正在停止...")
        self.running = False

    def _main_loop(self):
        """主交易循环"""
        while self.running:
            try:
                self.scan_count += 1
                cycle_start = time.time()

                logger.info(f"--- 扫描周期 #{self.scan_count} ---")

                # 1. 扫描市场
                opportunities = self.scanner.scan_all()

                # 2. 更新持仓价格
                self._update_position_prices()

                # 3. 检查止损止盈
                self._check_stop_loss_take_profit()

                # 4. 执行交易
                self._process_opportunities(opportunities)

                # 5. 打印状态
                self._print_status()

                # 6. 保存状态
                if self.scan_count % 10 == 0:
                    self._save_state()

                # 等待下一轮
                elapsed = time.time() - cycle_start
                sleep_time = max(1, self.config.SCAN_INTERVAL - elapsed)
                time.sleep(sleep_time)

            except Exception as e:
                logger.error(f"主循环异常: {e}", exc_info=True)
                time.sleep(10)

    def _update_position_prices(self):
        """更新所有持仓的当前价格"""
        for pos in self.risk.positions:
            try:
                mid = self.executor.get_midpoint(pos.token_id)
                if mid and mid > 0:
                    pos.current_price = mid
            except Exception as e:
                logger.debug(f"更新价格失败 {pos.question[:30]}: {e}")

    def _check_stop_loss_take_profit(self):
        """检查止损止盈"""
        to_close = self.risk.check_stop_loss_take_profit()
        for pos, reason in to_close:
            logger.info(f"执行平仓: {pos.question[:40]} | {reason}")

            if self.config.DRY_RUN:
                logger.info(f"[模拟] 平仓: {pos.side} @ {pos.current_price:.3f}")
                # 模拟平仓记录
                record = TradeRecord(
                    timestamp=time.time(),
                    market_id=pos.market_id,
                    question=pos.question,
                    side=pos.side,
                    action="SELL",
                    price=pos.current_price,
                    amount=pos.amount,
                    pnl=pos.pnl,
                    strategy="STOP_LOSS_TP",
                )
                self.risk.record_trade(record)
                self.risk.remove_position(pos.market_id)
                self.notifier.profit_alert(pos.pnl, self.risk.total_pnl)
            else:
                # 实盘平仓
                resp = self.executor.place_market_order(
                    pos.token_id, pos.amount, "SELL"
                )
                if resp:
                    record = TradeRecord(
                        timestamp=time.time(),
                        market_id=pos.market_id,
                        question=pos.question,
                        side=pos.side,
                        action="SELL",
                        price=pos.current_price,
                        amount=pos.amount,
                        pnl=pos.pnl,
                        strategy="STOP_LOSS_TP",
                    )
                    self.risk.record_trade(record)
                    self.risk.remove_position(pos.market_id)
                    self.notifier.profit_alert(pos.pnl, self.risk.total_pnl)

    def _process_opportunities(self, opportunities: dict):
        """处理交易机会"""
        # 套利机会
        if self.config.ENABLE_ARBITRAGE:
            for opp in opportunities.get("arbitrage", []):
                self._handle_arbitrage(opp)

        # 均值回归机会
        if self.config.ENABLE_MEAN_REVERSION:
            for opp in opportunities.get("mean_reversion", []):
                self._handle_mean_reversion(opp)

        # 事件驱动机会
        if self.config.ENABLE_EVENT_DRIVEN:
            for opp in opportunities.get("event_driven", []):
                self._handle_event_driven(opp)

    def _handle_arbitrage(self, opp: dict):
        """
        处理YES+NO套利机会
        策略: 同时买入YES和NO，锁定差价利润
        """
        market: MarketInfo = opp["market"]
        arb_spread = opp["arb_spread"]

        # 套利空间检查 (扣除手续费后)
        if arb_spread * 100 < self.config.ARB_MIN_SPREAD:
            return

        # 已持有此市场则跳过
        if any(p.market_id == market.id for p in self.risk.positions):
            return

        # 计算仓位
        trade_amount = self.risk.calculate_position_size(self.config.INITIAL_CAPITAL)
        can_trade, reason = self.risk.check_can_trade(trade_amount * 2)  # 买两边

        if not can_trade:
            logger.debug(f"套利跳过: {reason}")
            return

        logger.info(
            f"🎯 套利机会: {market.question[:40]} "
            f"YES={market.yes_price:.3f} NO={market.no_price:.3f} "
            f"空间={arb_spread*100:.2f}%"
        )

        if self.config.DRY_RUN:
            logger.info(f"[模拟] 套利买入: YES @ {market.yes_price:.3f} + NO @ {market.no_price:.3f}")

            # 模拟买入YES
            yes_shares = trade_amount / market.yes_price if market.yes_price > 0 else 0
            yes_pos = Position(
                market_id=market.id,
                question=market.question,
                token_id=market.yes_token_id,
                side="YES",
                entry_price=market.yes_price,
                amount=yes_shares,
                current_price=market.yes_price,
            )
            self.risk.add_position(yes_pos)

            # 模拟买入NO
            no_shares = trade_amount / market.no_price if market.no_price > 0 else 0
            no_pos = Position(
                market_id=f"{market.id}_NO",
                question=market.question,
                token_id=market.no_token_id,
                side="NO",
                entry_price=market.no_price,
                amount=no_shares,
                current_price=market.no_price,
            )
            self.risk.add_position(no_pos)

            # 记录交易
            record = TradeRecord(
                timestamp=time.time(),
                market_id=market.id,
                question=market.question,
                side="YES+NO",
                action="BUY",
                price=market.total_price,
                amount=trade_amount * 2,
                strategy="ARBITRAGE",
            )
            self.risk.record_trade(record)

            self.notifier.trade_alert(
                "BUY", market.question[:30], "YES+NO", market.total_price, trade_amount * 2
            )
        else:
            # 实盘: 先买YES再买NO
            yes_pos = self.executor.execute_opportunity(
                market, "YES", market.yes_price, trade_amount, "ARBITRAGE"
            )
            if yes_pos:
                self.risk.add_position(yes_pos)
                no_pos = self.executor.execute_opportunity(
                    market, "NO", market.no_price, trade_amount, "ARBITRAGE"
                )
                if no_pos:
                    self.risk.add_position(no_pos)
                    self.notifier.trade_alert(
                        "BUY", market.question[:30], "YES+NO", market.total_price, trade_amount * 2
                    )
                else:
                    # NO买入失败，撤回YES
                    logger.warning("NO买入失败，需要手动处理YES持仓")

    def _handle_mean_reversion(self, opp: dict):
        """
        处理均值回归机会
        策略: 在极端价格时买入便宜的一方
        """
        market: MarketInfo = opp["market"]
        side = opp["side"]
        price = opp["price"]

        # 已持有此市场则跳过
        if any(p.market_id == market.id for p in self.risk.positions):
            return

        # 只交易高置信度机会 (100U资金需要更谨慎)
        if opp["confidence"] == "LOW":
            return

        trade_amount = self.risk.calculate_position_size(self.config.INITIAL_CAPITAL)
        can_trade, reason = self.risk.check_can_trade(trade_amount)

        if not can_trade:
            logger.debug(f"均值回归跳过: {reason}")
            return

        logger.info(
            f"📊 均值回归: {market.question[:40]} | {side} @ {price:.3f} | {opp['reason']}"
        )

        if self.config.DRY_RUN:
            logger.info(f"[模拟] 买入: {side} @ {price:.3f} 金额=${trade_amount:.2f}")
            shares = trade_amount / price if price > 0 else 0
            pos = Position(
                market_id=market.id,
                question=market.question,
                token_id=market.yes_token_id if side == "YES" else market.no_token_id,
                side=side,
                entry_price=price,
                amount=shares,
                current_price=price,
            )
            self.risk.add_position(pos)

            record = TradeRecord(
                timestamp=time.time(),
                market_id=market.id,
                question=market.question,
                side=side,
                action="BUY",
                price=price,
                amount=trade_amount,
                strategy="MEAN_REVERSION",
            )
            self.risk.record_trade(record)

            self.notifier.trade_alert(
                "BUY", market.question[:30], side, price, trade_amount
            )
        else:
            pos = self.executor.execute_opportunity(
                market, side, price, trade_amount, "MEAN_REVERSION"
            )
            if pos:
                self.risk.add_position(pos)
                self.notifier.trade_alert(
                    "BUY", market.question[:30], side, price, trade_amount
                )

    def _handle_event_driven(self, opp: dict):
        """
        处理事件驱动机会
        策略: 价格异动后的过度反应反转
        注意: 100U资金下此策略风险较高，默认只做模拟
        """
        market: MarketInfo = opp["market"]
        side = opp["side"]
        price = opp["price"]

        # 已持有此市场则跳过
        if any(p.market_id == market.id for p in self.risk.positions):
            return

        # 事件驱动置信度低，100U资金需更严格
        # 只在价格极端时才考虑
        if not market.is_extreme_price:
            logger.debug(f"事件驱动跳过: 价格非极端 YES={market.yes_price:.3f}")
            return

        trade_amount = self.risk.calculate_position_size(self.config.INITIAL_CAPITAL) * 0.5  # 半仓
        trade_amount = max(trade_amount, self.config.MIN_TRADE_SIZE)
        can_trade, reason = self.risk.check_can_trade(trade_amount)

        if not can_trade:
            logger.debug(f"事件驱动跳过: {reason}")
            return

        logger.info(
            f"⚡ 事件驱动: {market.question[:40]} | {side} @ {price:.3f} | {opp['reason']}"
        )

        if self.config.DRY_RUN:
            logger.info(f"[模拟] 买入: {side} @ {price:.3f} 金额=${trade_amount:.2f}")
            shares = trade_amount / price if price > 0 else 0
            pos = Position(
                market_id=market.id,
                question=market.question,
                token_id=market.yes_token_id if side == "YES" else market.no_token_id,
                side=side,
                entry_price=price,
                amount=shares,
                current_price=price,
            )
            self.risk.add_position(pos)

            record = TradeRecord(
                timestamp=time.time(),
                market_id=market.id,
                question=market.question,
                side=side,
                action="BUY",
                price=price,
                amount=trade_amount,
                strategy="EVENT_DRIVEN",
            )
            self.risk.record_trade(record)

            self.notifier.trade_alert(
                "BUY", market.question[:30], side, price, trade_amount
            )

    def _print_status(self):
        """打印当前状态"""
        status = self.risk.get_status()
        uptime = time.time() - self.start_time if self.start_time else 0
        hours = uptime / 3600

        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] "
            f"扫描#{self.scan_count} | "
            f"持仓{status['positions_count']}/{self.config.MAX_POSITIONS} | "
            f"日PnL ${status['daily_pnl']:+.2f} | "
            f"累计 ${status['total_pnl']:+.2f} | "
            f"交易{status['trade_count']}次 | "
            f"运行{hours:.1f}h"
        )

        if self.risk.circuit_breaker:
            print(f"  ⚠️ 熔断已触发: {self.risk.circuit_breaker_reason}")

        # 持仓明细
        for pos in self.risk.positions:
            pnl_pct = pos.pnl_percent
            emoji = "🟢" if pnl_pct >= 0 else "🔴"
            print(
                f"  {emoji} {pos.question[:35]} | {pos.side} "
                f"@{pos.entry_price:.3f}→{pos.current_price:.3f} "
                f"({pnl_pct:+.1f}%)"
            )

    def stop(self):
        """停止交易机器人"""
        self.running = False
        self._save_state()
        self.notifier.system_alert("交易系统已停止")

        # 打印最终状态
        status = self.risk.get_status()
        print()
        print("=" * 60)
        print("  交易系统已停止")
        print("=" * 60)
        print(f"  总交易次数: {status['trade_count']}")
        print(f"  累计PnL: ${status['total_pnl']:+.2f}")
        print(f"  日PnL: ${status['daily_pnl']:+.2f}")
        print(f"  剩余持仓: {status['positions_count']}个")
        if status['circuit_breaker']:
            print(f"  熔断原因: {status['circuit_breaker_reason']}")
        print("=" * 60)


def main():
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    bot = PolymarketBot()
    try:
        bot.start()
    except KeyboardInterrupt:
        bot.stop()
    except Exception as e:
        logger.critical(f"系统异常: {e}", exc_info=True)
        bot.stop()


if __name__ == "__main__":
    main()
