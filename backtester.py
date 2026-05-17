"""
Polymarket V3 - 事件驱动回测引擎
核心组件:
1. CostModel — 真实手续费+滑点模型
2. BaseStrategy — 策略基类(零前瞻偏差)
3. BacktestEngine — 事件驱动回测引擎

数据流: 历史数据 → BacktestEngine → CostModel → 策略 → 结果统计
"""
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from config import calc_taker_fee

logger = logging.getLogger("polymarket")


@dataclass
class BacktestTrade:
    """回测交易记录"""
    entry_time: float
    entry_price: float
    side: str
    amount: float
    shares: float
    strategy: str
    signal_strength: float = 0.5
    exit_time: float = 0
    exit_price: float = 0
    pnl: float = 0
    pnl_pct: float = 0
    fee_paid: float = 0
    hold_hours: float = 0
    closed: bool = False


@dataclass
class BacktestResult:
    """回测结果"""
    strategy: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    max_win: float
    max_loss: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_duration_hours: float
    avg_hold_hours: float
    total_fees: float
    net_pnl: float
    kelly_fraction: float
    initial_capital: float
    final_capital: float
    trades: list = field(default_factory=list)


class CostModel:
    """
    真实成本模型
    - Taker Fee: shares × feeRate × price × (1-price)
    - 滑点模型: sqrt(amount / depth) × spread
    - Maker返佣: taker_fee × rebateRate
    """

    # 默认滑点系数
    SLIPPAGE_COEFFICIENT = 0.001  # 每单位金额的滑点

    def __init__(self, taker_fee_rate: float = 0.05, maker_rebate_rate: float = 0.25):
        self.taker_fee_rate = taker_fee_rate
        self.maker_rebate_rate = maker_rebate_rate

    def compute_entry_cost(
        self,
        shares: float,
        price: float,
        amount: float,
        is_maker: bool = False,
        category: str = "general"
    ) -> dict:
        """
        计算入场成本

        返回:
            {
                "fee": 手续费金额,
                "slippage": 滑点成本,
                "effective_price": 实际成交价(含滑点),
                "total_cost": 总成本(手续费+滑点)
            }
        """
        # 手续费
        if is_maker:
            fee = 0  # Maker不付费
        else:
            fee = calc_taker_fee(shares, price, category)

        # 滑点(简化模型)
        slippage = amount * self.SLIPPAGE_COEFFICIENT * math.sqrt(amount / 100)

        # 实际成交价(买入时滑点推高价格)
        effective_price = price * (1 + slippage / amount) if amount > 0 else price

        return {
            "fee": round(fee, 4),
            "slippage": round(slippage, 4),
            "effective_price": round(effective_price, 6),
            "total_cost": round(fee + slippage, 4),
        }

    def compute_exit_cost(
        self,
        shares: float,
        price: float,
        amount: float,
        side: str = "YES",
        is_maker: bool = False,
        category: str = "general"
    ) -> dict:
        """计算出场成本"""
        # 手续费
        if is_maker:
            fee = 0
        else:
            fee = calc_taker_fee(shares, price, category)

        # 滑点(卖出时滑点压低价格)
        slippage = amount * self.SLIPPAGE_COEFFICIENT * math.sqrt(amount / 100)

        # 实际成交价(卖出时滑点压低价格)
        effective_price = price * (1 - slippage / amount) if amount > 0 else price

        return {
            "fee": round(fee, 4),
            "slippage": round(slippage, 4),
            "effective_price": round(effective_price, 6),
            "total_cost": round(fee + slippage, 4),
        }


class BaseStrategy:
    """
    策略基类(零前瞻偏差)

    子类必须实现:
    - generate_signal(event) → Signal | None

    原则:
    1. 只使用当前及之前的数据(零前瞻偏差)
    2. 不在__init__中预计算未来数据
    3. 信号产生时记录时间戳
    """

    def __init__(self, name: str, config=None):
        self.name = name
        self.config = config
        self.positions_opened = 0
        self.positions_closed = 0
        self.total_pnl = 0

    def generate_signal(self, event: dict) -> Optional[dict]:
        """
        根据事件产生交易信号

        参数:
            event: {
                "type": "price_update" | "orderbook_update" | "trade",
                "data": ...,
                "timestamp": float,
            }

        返回:
            None 或 {
                "side": "YES" | "NO",
                "probability": float,
                "confidence": float,
                "size_pct": float,  # 建议仓位比例
                "reason": str,
            }
        """
        raise NotImplementedError("子类必须实现 generate_signal()")

    def on_fill(self, fill: dict):
        """订单成交回调"""
        pass

    def get_stats(self) -> dict:
        """获取策略统计"""
        return {
            "name": self.name,
            "positions_opened": self.positions_opened,
            "positions_closed": self.positions_closed,
            "total_pnl": round(self.total_pnl, 4),
        }


class BacktestEngine:
    """
    事件驱动回测引擎

    特性:
    1. 零前瞻偏差: 严格按时间顺序处理事件
    2. CostModel: 真实手续费+滑点
    3. 策略隔离: 每个策略独立回测
    4. 完整统计: Sharpe/MaxDD/Kelly/WinRate
    """

    def __init__(self, config=None, data_store=None):
        self.config = config
        self.db = data_store
        self.cost_model = CostModel()

    def run_backtest(
        self,
        strategy: str = "",
        capital: float = 100.0,
        days: int = 30,
        kelly_fraction: float = 0.25
    ) -> BacktestResult:
        """
        运行回测

        参数:
            strategy: 策略名(空=所有策略)
            capital: 初始资金
            days: 回测天数
            kelly_fraction: Kelly比例(用于仓位计算)

        返回:
            BacktestResult
        """
        trades = self._load(strategy, days)
        if not trades:
            return BacktestResult(
                strategy=strategy or "all",
                total_trades=0, winning_trades=0, losing_trades=0,
                win_rate=0, total_pnl=0, avg_pnl=0, max_win=0, max_loss=0,
                sharpe_ratio=0, max_drawdown=0, max_drawdown_duration_hours=0,
                avg_hold_hours=0, total_fees=0, net_pnl=0, kelly_fraction=0,
                initial_capital=capital, final_capital=capital
            )

        simulated = self._simulate(trades, capital, kelly_fraction)
        return self._compute_stats(strategy or "all", simulated, capital)

    def _load(self, strategy: str, days: int) -> list[dict]:
        """加载历史交易数据"""
        if not self.db:
            return []

        cutoff = time.time() - days * 86400
        try:
            q = "SELECT timestamp,market_id,question,side,action,price,amount,pnl,strategy,signal_strength FROM trades WHERE timestamp>?"
            p = [cutoff]
            if strategy:
                q += " AND strategy=?"
                p.append(strategy)
            q += " ORDER BY timestamp ASC"
            return [dict(r) for r in self.db.conn.execute(q, p).fetchall()]
        except Exception:
            return []

    def _simulate(
        self,
        historical: list[dict],
        capital: float,
        kelly_fraction: float
    ) -> list[BacktestTrade]:
        """
        模拟交易(零前瞻偏差)

        参数:
            historical: 按时间排序的历史数据
            capital: 初始资金
            kelly_fraction: Kelly仓位比例

        返回:
            模拟交易列表
        """
        simulated = []
        current_capital = capital
        open_positions = {}  # market_id → BacktestTrade

        for t in historical:
            market_id = t["market_id"]
            action = t["action"]
            timestamp = t["timestamp"]

            if action == "BUY" and market_id not in open_positions:
                price = t["price"]
                if price <= 0:
                    continue

                # Kelly仓位: capital × kelly_fraction
                amount = min(capital * kelly_fraction, current_capital * 0.15, 15.0)
                amount = max(amount, 5.0)

                shares = amount / price

                # 入场成本
                entry_cost = self.cost_model.compute_entry_cost(shares, price, amount)
                net_amount = amount - entry_cost["total_cost"]
                net_shares = net_amount / price if price > 0 else 0

                bt = BacktestTrade(
                    entry_time=timestamp,
                    entry_price=entry_cost["effective_price"],
                    side=t["side"],
                    amount=net_amount,
                    shares=net_shares,
                    strategy=t.get("strategy", ""),
                    signal_strength=t.get("signal_strength", 0.5),
                    fee_paid=entry_cost["fee"],
                )
                open_positions[market_id] = bt
                current_capital -= amount

            elif action == "SELL" and market_id in open_positions:
                pos = open_positions.pop(market_id)
                pos.exit_price = t["price"]
                pos.exit_time = timestamp

                # 出场成本
                exit_amount = pos.shares * pos.exit_price
                exit_cost = self.cost_model.compute_exit_cost(
                    pos.shares, pos.exit_price, exit_amount
                )

                # 计算PnL
                if pos.side == "YES":
                    raw_pnl = (pos.exit_price - pos.entry_price) * pos.shares
                else:
                    raw_pnl = (pos.entry_price - pos.exit_price) * pos.shares

                pos.fee_paid += exit_cost["fee"]
                pos.pnl = raw_pnl - exit_cost["total_cost"]
                pos.pnl_pct = (pos.pnl / pos.amount * 100) if pos.amount > 0 else 0
                pos.hold_hours = (pos.exit_time - pos.entry_time) / 3600
                pos.closed = True

                simulated.append(pos)
                current_capital += pos.amount + pos.pnl

        return simulated

    def _compute_stats(
        self,
        strategy: str,
        trades: list[BacktestTrade],
        capital: float
    ) -> BacktestResult:
        """计算回测统计"""
        if not trades:
            return BacktestResult(
                strategy=strategy,
                total_trades=0, winning_trades=0, losing_trades=0,
                win_rate=0, total_pnl=0, avg_pnl=0, max_win=0, max_loss=0,
                sharpe_ratio=0, max_drawdown=0, max_drawdown_duration_hours=0,
                avg_hold_hours=0, total_fees=0, net_pnl=0, kelly_fraction=0,
                initial_capital=capital, final_capital=capital
            )

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]

        total_pnl = sum(t.pnl for t in trades)
        total_fees = sum(t.fee_paid for t in trades)
        net_pnl = total_pnl

        pnl_list = [t.pnl for t in trades]
        avg_pnl = sum(pnl_list) / len(pnl_list)
        std = (sum((p - avg_pnl) ** 2 for p in pnl_list) / len(pnl_list)) ** 0.5 if len(pnl_list) > 1 else 1

        # Sharpe Ratio (年化)
        sharpe = avg_pnl / std * (365 ** 0.5) if std > 0 else 0

        # Max Drawdown
        equity_curve = [capital]
        running = capital
        for t in sorted(trades, key=lambda x: x.entry_time):
            running += t.pnl - t.fee_paid
            equity_curve.append(running)

        peak = equity_curve[0]
        max_dd = 0
        for v in equity_curve:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # Kelly Fraction
        wr = len(wins) / len(trades)
        avg_w = sum(t.pnl for t in wins) / len(wins) if wins else 0
        avg_l = abs(sum(t.pnl for t in losses) / len(losses)) if losses else 0.01
        kelly = max(0, (wr * (avg_w / avg_l) - (1 - wr)) / (avg_w / avg_l)) if avg_w > 0 and avg_l > 0 else 0

        return BacktestResult(
            strategy=strategy,
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(wr, 3),
            total_pnl=round(total_pnl, 2),
            avg_pnl=round(avg_pnl, 4),
            max_win=round(max(t.pnl for t in trades), 2),
            max_loss=round(min(t.pnl for t in trades), 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_dd, 2),
            max_drawdown_duration_hours=0,
            avg_hold_hours=round(sum(t.hold_hours for t in trades) / len(trades), 1),
            total_fees=round(total_fees, 2),
            net_pnl=round(net_pnl, 2),
            kelly_fraction=round(kelly, 4),
            initial_capital=capital,
            final_capital=round(capital + net_pnl, 2),
            trades=[{
                "strategy": t.strategy,
                "side": t.side,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": round(t.pnl, 4),
                "hold_hours": round(t.hold_hours, 1),
                "fee": round(t.fee_paid, 4),
            } for t in trades[-20:]],
        )

    def compare_strategies(self, capital: float = 100.0, days: int = 30) -> dict:
        """比较所有策略"""
        results = {}
        for s in ["ARBITRAGE", "MULTI_MARKET_ARB", "MEAN_REVERSION",
                   "ZERO_FEE_VALUE", "EVENT_DRIVEN", "INSIDER_DETECT",
                   "WALLET_FOLLOW", "STOP_LOSS_TP"]:
            result = self.run_backtest(strategy=s, capital=capital, days=days)
            results[s] = {
                "total_trades": result.total_trades,
                "win_rate": result.win_rate,
                "total_pnl": result.total_pnl,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown": result.max_drawdown,
                "kelly_fraction": result.kelly_fraction,
                "net_pnl": result.net_pnl,
            }
        return results
