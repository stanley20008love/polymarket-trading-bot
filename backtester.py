"""
Polymarket V3 - 简易回测框架
验证策略、计算夏普比率、最大回撤、Kelly推荐
"""
import logging
import math
import time
from dataclasses import dataclass, field
from config import Config, calc_taker_fee
from data_store import DataStore

logger = logging.getLogger("polymarket")


@dataclass
class BacktestTrade:
    entry_time: float; entry_price: float; side: str; amount: float; shares: float; strategy: str
    signal_strength: float = 0.5; exit_time: float = 0; exit_price: float = 0
    pnl: float = 0; pnl_pct: float = 0; fee_paid: float = 0; hold_hours: float = 0; closed: bool = False


@dataclass
class BacktestResult:
    strategy: str; total_trades: int; winning_trades: int; losing_trades: int; win_rate: float
    total_pnl: float; avg_pnl: float; max_win: float; max_loss: float; sharpe_ratio: float
    max_drawdown: float; max_drawdown_duration_hours: float; avg_hold_hours: float
    total_fees: float; net_pnl: float; kelly_fraction: float; initial_capital: float; final_capital: float
    trades: list = field(default_factory=list)


class Backtester:
    def __init__(self, config: Config, data_store: DataStore):
        self.config = config
        self.db = data_store

    def run_backtest(self, strategy: str = "", capital: float = 100.0, days: int = 30) -> BacktestResult:
        trades = self._load(strategy, days)
        if not trades:
            return BacktestResult(strategy=strategy or "all", total_trades=0, winning_trades=0, losing_trades=0, win_rate=0, total_pnl=0, avg_pnl=0, max_win=0, max_loss=0, sharpe_ratio=0, max_drawdown=0, max_drawdown_duration_hours=0, avg_hold_hours=0, total_fees=0, net_pnl=0, kelly_fraction=0, initial_capital=capital, final_capital=capital)
        simulated = self._simulate(trades, capital)
        return self._stats(strategy or "all", simulated, capital)

    def _load(self, strategy: str, days: int) -> list[dict]:
        cutoff = time.time() - days * 86400
        try:
            q = "SELECT timestamp,market_id,question,side,action,price,amount,pnl,strategy,signal_strength FROM trades WHERE timestamp>?"
            p = [cutoff]
            if strategy: q += " AND strategy=?"; p.append(strategy)
            q += " ORDER BY timestamp ASC"
            return [dict(r) for r in self.db.conn.execute(q, p).fetchall()]
        except: return []

    def _simulate(self, historical: list[dict], capital: float) -> list[BacktestTrade]:
        simulated, current = [], capital
        open_pos = {}
        for t in historical:
            mid, action = t["market_id"], t["action"]
            if action == "BUY" and mid not in open_pos:
                price = t["price"]
                if price <= 0: continue
                amount = min(capital * 0.08, current * 0.15, 15.0)
                amount = max(amount, 5.0)
                shares = amount / price
                fee = calc_taker_fee(shares, price, t.get("strategy", "general"))
                bt = BacktestTrade(entry_time=t["timestamp"], entry_price=price, side=t["side"], amount=amount - fee, shares=shares, strategy=t.get("strategy", ""), signal_strength=t.get("signal_strength", 0.5), fee_paid=fee)
                open_pos[mid] = bt
                current -= amount
            elif action == "SELL" and mid in open_pos:
                pos = open_pos.pop(mid)
                pos.exit_price, pos.exit_time = t["price"], t["timestamp"]
                pos.pnl = (pos.exit_price - pos.entry_price) * pos.shares if pos.side == "YES" else (pos.entry_price - pos.exit_price) * pos.shares
                exit_fee = calc_taker_fee(pos.shares, pos.exit_price, pos.strategy or "general")
                pos.pnl -= exit_fee; pos.fee_paid += exit_fee
                pos.pnl_pct = (pos.pnl / pos.amount * 100) if pos.amount > 0 else 0
                pos.hold_hours = (pos.exit_time - pos.entry_time) / 3600
                pos.closed = True
                simulated.append(pos)
                current += pos.amount + pos.pnl
        return simulated

    def _stats(self, strategy: str, trades: list[BacktestTrade], capital: float) -> BacktestResult:
        if not trades:
            return BacktestResult(strategy=strategy, total_trades=0, winning_trades=0, losing_trades=0, win_rate=0, total_pnl=0, avg_pnl=0, max_win=0, max_loss=0, sharpe_ratio=0, max_drawdown=0, max_drawdown_duration_hours=0, avg_hold_hours=0, total_fees=0, net_pnl=0, kelly_fraction=0, initial_capital=capital, final_capital=capital)
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        total_pnl = sum(t.pnl for t in trades)
        total_fees = sum(t.fee_paid for t in trades)
        net_pnl = total_pnl - total_fees
        pnl_list = [t.pnl for t in trades]
        avg_pnl = sum(pnl_list) / len(pnl_list)
        std = (sum((p - avg_pnl)**2 for p in pnl_list) / len(pnl_list))**0.5 if len(pnl_list) > 1 else 1
        sharpe = avg_pnl / std * (365**0.5) if std > 0 else 0
        equity = [capital]
        running = capital
        for t in sorted(trades, key=lambda x: x.entry_time):
            running += t.pnl - t.fee_paid; equity.append(running)
        peak, max_dd = equity[0], 0
        for v in equity:
            if v > peak: peak = v
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        wr = len(wins) / len(trades)
        avg_w = sum(t.pnl for t in wins) / len(wins) if wins else 0
        avg_l = abs(sum(t.pnl for t in losses) / len(losses)) if losses else 0.01
        kelly = max(0, (wr * (avg_w/avg_l) - (1-wr)) / (avg_w/avg_l)) if avg_w > 0 and avg_l > 0 else 0
        return BacktestResult(strategy=strategy, total_trades=len(trades), winning_trades=len(wins), losing_trades=len(losses), win_rate=round(wr,3), total_pnl=round(total_pnl,2), avg_pnl=round(avg_pnl,4), max_win=round(max(t.pnl for t in trades),2), max_loss=round(min(t.pnl for t in trades),2), sharpe_ratio=round(sharpe,2), max_drawdown=round(max_dd,2), max_drawdown_duration_hours=0, avg_hold_hours=round(sum(t.hold_hours for t in trades)/len(trades),1), total_fees=round(total_fees,2), net_pnl=round(net_pnl,2), kelly_fraction=round(kelly,4), initial_capital=capital, final_capital=round(capital+net_pnl,2))

    def compare_strategies(self, capital: float = 100.0, days: int = 30) -> dict:
        results = {}
        for s in ["ARBITRAGE","MULTI_MARKET_ARB","MEAN_REVERSION","ZERO_FEE_VALUE","EVENT_DRIVEN","INSIDER_DETECT","WALLET_FOLLOW"]:
            results[s] = self.run_backtest(strategy=s, capital=capital, days=days)
        return results
