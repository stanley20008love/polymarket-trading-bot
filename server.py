"""
Polymarket 量化交易系统 - 健康检查 Web 服务器
Zeabur 需要一个 HTTP 端口来判断服务是否存活
此模块在后台启动 bot 的同时，在前台提供健康检查端点
"""
import json
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

# 全局状态 - bot主线程会更新
bot_status = {
    "status": "starting",
    "scan_count": 0,
    "positions": 0,
    "daily_pnl": 0.0,
    "total_pnl": 0.0,
    "trade_count": 0,
    "circuit_breaker": False,
    "uptime_seconds": 0,
    "last_scan_time": "",
    "strategies": {
        "arbitrage": "disabled",
        "mean_reversion": "disabled",
        "event_driven": "disabled",
    },
    "mode": "dry_run",
    "capital": 0,
    "start_time": "",
}


class HealthHandler(BaseHTTPRequestHandler):
    """健康检查 HTTP 处理器"""

    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "bot": bot_status,
                "message": "Polymarket Trading Bot is running",
            })
        elif self.path == "/status":
            self._send_json(200, bot_status)
        elif self.path == "/ping":
            self._send_text(200, "pong")
        else:
            self._send_text(404, "Not Found")

    def _send_json(self, code, data):
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """静默日志，避免刷屏"""
        pass


def start_health_server(port=8000):
    """启动健康检查服务器"""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"[Health] 健康检查服务器启动在端口 {port}")
    server.serve_forever()


def run_bot():
    """在后台线程中运行交易机器人"""
    import logging
    import sys

    # 延迟导入，确保所有模块可用
    sys.path.insert(0, os.path.dirname(__file__))
    from config import Config
    from market_scanner import MarketScanner
    from risk_manager import RiskManager
    from executor import OrderExecutor
    from notifier import Notifier

    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    logger = logging.getLogger("polymarket")

    config = Config()

    # 更新全局状态
    bot_status["mode"] = "dry_run" if config.DRY_RUN else "LIVE"
    bot_status["capital"] = config.INITIAL_CAPITAL
    bot_status["strategies"]["arbitrage"] = "enabled" if config.ENABLE_ARBITRAGE else "disabled"
    bot_status["strategies"]["mean_reversion"] = "enabled" if config.ENABLE_MEAN_REVERSION else "disabled"
    bot_status["strategies"]["event_driven"] = "enabled" if config.ENABLE_EVENT_DRIVEN else "disabled"

    # 初始化模块
    scanner = MarketScanner(config)
    risk = RiskManager(config)
    executor = OrderExecutor(config)
    notifier = Notifier(config)

    # 加载状态
    state_file = os.path.join(os.path.dirname(__file__), "bot_state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
            risk.total_pnl = state.get("total_pnl", 0.0)
            risk.trade_count = state.get("trade_count", 0)
        except:
            pass

    # 初始化执行器
    if not executor.initialize():
        logger.warning("执行器初始化失败，模拟模式运行")
        config.DRY_RUN = True
        bot_status["mode"] = "dry_run"

    bot_status["status"] = "running"
    bot_status["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()
    scan_count = 0

    mode = "模拟" if config.DRY_RUN else "实盘"
    logger.info(f"交易系统启动 [{mode}] ${config.INITIAL_CAPITAL}")
    notifier.system_alert(f"交易系统启动 [{mode}] ${config.INITITAL_CAPITAL}")

    # 主循环
    while True:
        try:
            scan_count += 1
            bot_status["scan_count"] = scan_count
            bot_status["uptime_seconds"] = int(time.time() - start_time)
            bot_status["last_scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S")

            logger.info(f"--- 扫描周期 #{scan_count} ---")

            # 1. 扫描市场
            opportunities = scanner.scan_all()

            # 2. 更新持仓价格
            for pos in risk.positions:
                try:
                    mid = executor.get_midpoint(pos.token_id)
                    if mid and mid > 0:
                        pos.current_price = mid
                except:
                    pass

            # 3. 检查止损止盈
            to_close = risk.check_stop_loss_take_profit()
            for pos, reason in to_close:
                logger.info(f"平仓: {pos.question[:40]} | {reason}")
                if config.DRY_RUN:
                    from risk_manager import TradeRecord
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
                    risk.record_trade(record)
                    risk.remove_position(pos.market_id)

            # 4. 处理交易机会
            from risk_manager import Position, TradeRecord

            # 套利
            if config.ENABLE_ARBITRAGE:
                for opp in opportunities.get("arbitrage", []):
                    m = opp["market"]
                    if opp["arb_spread"] * 100 < config.ARB_MIN_SPREAD:
                        continue
                    if any(p.market_id == m.id for p in risk.positions):
                        continue
                    trade_amount = risk.calculate_position_size(config.INITIAL_CAPITAL)
                    can, reason = risk.check_can_trade(trade_amount * 2)
                    if not can:
                        continue
                    logger.info(f"套利: {m.question[:40]} 空间={opp['arb_spread']*100:.2f}%")
                    if config.DRY_RUN:
                        yes_shares = trade_amount / m.yes_price if m.yes_price > 0 else 0
                        yes_pos = Position(
                            market_id=m.id, question=m.question,
                            token_id=m.yes_token_id, side="YES",
                            entry_price=m.yes_price, amount=yes_shares,
                            current_price=m.yes_price,
                        )
                        risk.add_position(yes_pos)
                        no_shares = trade_amount / m.no_price if m.no_price > 0 else 0
                        no_pos = Position(
                            market_id=f"{m.id}_NO", question=m.question,
                            token_id=m.no_token_id, side="NO",
                            entry_price=m.no_price, amount=no_shares,
                            current_price=m.no_price,
                        )
                        risk.add_position(no_pos)
                        record = TradeRecord(
                            timestamp=time.time(), market_id=m.id,
                            question=m.question, side="YES+NO", action="BUY",
                            price=m.total_price, amount=trade_amount * 2,
                            strategy="ARBITRAGE",
                        )
                        risk.record_trade(record)

            # 均值回归
            if config.ENABLE_MEAN_REVERSION:
                for opp in opportunities.get("mean_reversion", []):
                    m = opp["market"]
                    if opp["confidence"] == "LOW":
                        continue
                    if any(p.market_id == m.id for p in risk.positions):
                        continue
                    trade_amount = risk.calculate_position_size(config.INITIAL_CAPITAL)
                    can, reason = risk.check_can_trade(trade_amount)
                    if not can:
                        continue
                    side = opp["side"]
                    price = opp["price"]
                    logger.info(f"均值回归: {m.question[:40]} {side}@{price:.3f}")
                    if config.DRY_RUN:
                        shares = trade_amount / price if price > 0 else 0
                        pos = Position(
                            market_id=m.id, question=m.question,
                            token_id=m.yes_token_id if side == "YES" else m.no_token_id,
                            side=side, entry_price=price, amount=shares,
                            current_price=price,
                        )
                        risk.add_position(pos)
                        record = TradeRecord(
                            timestamp=time.time(), market_id=m.id,
                            question=m.question, side=side, action="BUY",
                            price=price, amount=trade_amount,
                            strategy="MEAN_REVERSION",
                        )
                        risk.record_trade(record)

            # 事件驱动
            if config.ENABLE_EVENT_DRIVEN:
                for opp in opportunities.get("event_driven", []):
                    m = opp["market"]
                    if not m.is_extreme_price:
                        continue
                    if any(p.market_id == m.id for p in risk.positions):
                        continue
                    trade_amount = risk.calculate_position_size(config.INITIAL_CAPITAL) * 0.5
                    trade_amount = max(trade_amount, config.MIN_TRADE_SIZE)
                    can, reason = risk.check_can_trade(trade_amount)
                    if not can:
                        continue
                    side = opp["side"]
                    price = opp["price"]
                    logger.info(f"事件驱动: {m.question[:40]} {side}@{price:.3f}")
                    if config.DRY_RUN:
                        shares = trade_amount / price if price > 0 else 0
                        pos = Position(
                            market_id=m.id, question=m.question,
                            token_id=m.yes_token_id if side == "YES" else m.no_token_id,
                            side=side, entry_price=price, amount=shares,
                            current_price=price,
                        )
                        risk.add_position(pos)
                        record = TradeRecord(
                            timestamp=time.time(), market_id=m.id,
                            question=m.question, side=side, action="BUY",
                            price=price, amount=trade_amount,
                            strategy="EVENT_DRIVEN",
                        )
                        risk.record_trade(record)

            # 更新状态
            status = risk.get_status()
            bot_status["positions"] = status["positions_count"]
            bot_status["daily_pnl"] = status["daily_pnl"]
            bot_status["total_pnl"] = status["total_pnl"]
            bot_status["trade_count"] = status["trade_count"]
            bot_status["circuit_breaker"] = status["circuit_breaker"]

            # 定期保存状态
            if scan_count % 10 == 0:
                try:
                    with open(state_file, "w") as f:
                        json.dump({
                            "total_pnl": risk.total_pnl,
                            "trade_count": risk.trade_count,
                        }, f)
                except:
                    pass

            logger.info(
                f"扫描#{scan_count} | 持仓{status['positions_count']}/{config.MAX_POSITIONS} | "
                f"日PnL ${status['daily_pnl']:+.2f} | 累计 ${status['total_pnl']:+.2f}"
            )

            # 等待下一轮
            time.sleep(config.SCAN_INTERVAL)

        except Exception as e:
            logger.error(f"主循环异常: {e}", exc_info=True)
            bot_status["status"] = "error"
            bot_status["last_error"] = str(e)
            time.sleep(30)


def setup_logging(level: str = "INFO"):
    import logging
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                os.path.join(log_dir, f"trading_{time.strftime('%Y%m%d')}.log"),
                encoding="utf-8",
            ),
        ],
    )


def main():
    """主入口 - 启动健康检查服务器 + 交易机器人"""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))

    port = int(os.getenv("PORT", "8000"))
    mode = "模拟" if os.getenv("DRY_RUN", "true").lower() == "true" else "实盘"
    capital = os.getenv("INITIAL_CAPITAL", "100")

    print()
    print("=" * 55)
    print("  Polymarket 量化交易系统 - Zeabur部署版")
    print("=" * 55)
    print(f"  模式:     {mode}")
    print(f"  初始资金: ${capital}")
    print(f"  健康检查: http://0.0.0.0:{port}/health")
    print("=" * 55)
    print()

    # 在后台线程启动交易机器人
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # 在前台启动健康检查服务器
    start_health_server(port)


if __name__ == "__main__":
    main()
