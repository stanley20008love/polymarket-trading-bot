"""
Polymarket V3 - 高级风险管理系统
核心组件:
1. DynamicStopLoss — ATR动态止损 + 时间止损
2. PortfolioRiskManager — VaR/CVaR + 相关性矩阵 + 断路器

数据流: RiskManager → DynamicStopLoss → 平仓决策
        PortfolioRiskManager → 全局仓位控制 → 断路器触发
"""
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("polymarket")


@dataclass
class StopLossResult:
    """止损检查结果"""
    should_stop: bool = False
    stop_type: str = ""       # atr_stop | fixed_stop | time_stop | take_profit
    stop_price: float = 0.0
    reason: str = ""


class DynamicStopLoss:
    """
    ATR动态止损系统

    特性:
    1. ATR动态止损: stop = entry - N * ATR (N=1.5默认)
       - ATR基于价格波动计算, 自适应市场波动
       - 无历史数据时退化为固定百分比止损
    2. 时间止损: 持仓超过max_hold_hours且未盈利时平仓
    3. 移动止损(trailing stop): 盈利超过trail_trigger后激活

    参数说明:
    - atr_period: ATR计算周期(默认14)
    - atr_multiplier: ATR止损倍数(默认1.5, 越大越宽松)
    - max_hold_hours: 最大持仓时间(默认72h)
    - trail_trigger: 移动止损触发盈利%(默认5%)
    - trail_distance: 移动止损距离%(默认2%)
    """

    def __init__(self, config=None):
        self.config = config
        # 从config读取或使用默认值
        self.atr_multiplier = 1.5
        self.max_hold_hours = 72
        self.trail_trigger_pct = 5.0
        self.trail_distance_pct = 2.0
        self.fixed_stop_pct = 8.0  # V3: 3%→8% 与config.py同步
        self.take_profit_pct = 15.0  # V3: 8%→15% 让利润跑

        if config:
            self.atr_multiplier = getattr(config, 'ATR_MULTIPLIER', 1.5)
            self.max_hold_hours = getattr(config, 'MAX_HOLD_HOURS', 72)
            self.trail_trigger_pct = getattr(config, 'TRAIL_TRIGGER_PCT', 5.0)
            self.trail_distance_pct = getattr(config, 'TRAIL_DISTANCE_PCT', 2.0)
            self.fixed_stop_pct = getattr(config, 'STOP_LOSS_PERCENT', 3.0)
            self.take_profit_pct = getattr(config, 'TAKE_PROFIT_PERCENT', 8.0)

        # ATR历史数据: token_id -> [(price, timestamp), ...]
        self._price_history: dict[str, list[tuple[float, float]]] = {}

    def compute_atr(self, token_id: str, period: int = 14) -> Optional[float]:
        """
        计算Average True Range
        ATR = average of true ranges over period

        简化版本: 使用价格变化绝对值的均值
        True Range = max(high - low, |high - prev_close|, |low - prev_close|)
        在二元市场中: TR = |price_change|
        """
        history = self._price_history.get(token_id, [])
        if len(history) < 2:
            return None

        # 计算价格变化
        changes = []
        for i in range(1, len(history)):
            change = abs(history[i][0] - history[i-1][0])
            changes.append(change)

        if not changes:
            return None

        # 取最近period个变化
        recent_changes = changes[-period:]
        atr = sum(recent_changes) / len(recent_changes)
        return atr

    def update_price(self, token_id: str, price: float):
        """更新价格历史"""
        if token_id not in self._price_history:
            self._price_history[token_id] = []

        self._price_history[token_id].append((price, time.time()))

        # 保留最近100条
        if len(self._price_history[token_id]) > 100:
            self._price_history[token_id] = self._price_history[token_id][-100:]

    def check_stop_loss(
        self,
        entry_price: float,
        current_price: float,
        token_id: str = "",
        entry_time: float = 0,
        highest_price: float = 0,
        side: str = "YES"
    ) -> StopLossResult:
        """
        综合止损检查

        参数:
            entry_price: 入场价格
            current_price: 当前价格
            token_id: Token ID (用于ATR计算)
            entry_time: 入场时间戳
            highest_price: 持仓期间最高价(用于移动止损)
            side: YES/NO

        返回:
            StopLossResult
        """
        if entry_price <= 0 or current_price <= 0:
            return StopLossResult(should_stop=False)

        pnl_pct = ((current_price - entry_price) / entry_price) * 100

        # 1. ATR动态止损
        atr = self.compute_atr(token_id) if token_id else None
        if atr and atr > 0:
            atr_stop = entry_price - self.atr_multiplier * atr
            if current_price <= atr_stop:
                return StopLossResult(
                    should_stop=True,
                    stop_type="atr_stop",
                    stop_price=atr_stop,
                    reason=f"ATR止损: price={current_price:.4f} <= atr_stop={atr_stop:.4f} (ATR={atr:.4f})"
                )
        else:
            # 退化: 固定百分比止损
            fixed_stop = entry_price * (1 - self.fixed_stop_pct / 100)
            if current_price <= fixed_stop:
                return StopLossResult(
                    should_stop=True,
                    stop_type="fixed_stop",
                    stop_price=fixed_stop,
                    reason=f"固定止损: price={current_price:.4f} <= stop={fixed_stop:.4f} ({self.fixed_stop_pct}%)"
                )

        # 2. 移动止损(Trailing Stop)
        if highest_price > entry_price and pnl_pct >= self.trail_trigger_pct:
            trail_stop = highest_price * (1 - self.trail_distance_pct / 100)
            if current_price <= trail_stop:
                return StopLossResult(
                    should_stop=True,
                    stop_type="trailing_stop",
                    stop_price=trail_stop,
                    reason=f"移动止损: price={current_price:.4f} <= trail={trail_stop:.4f} (high={highest_price:.4f})"
                )

        # 3. 时间止损
        if entry_time > 0:
            hold_hours = (time.time() - entry_time) / 3600
            if hold_hours > self.max_hold_hours and pnl_pct < 0:
                return StopLossResult(
                    should_stop=True,
                    stop_type="time_stop",
                    stop_price=current_price,
                    reason=f"时间止损: 持仓{hold_hours:.1f}h > {self.max_hold_hours}h, PnL={pnl_pct:.1f}%"
                )

        # 4. 止盈
        if pnl_pct >= self.take_profit_pct:
            return StopLossResult(
                should_stop=True,
                stop_type="take_profit",
                stop_price=current_price,
                reason=f"止盈: PnL={pnl_pct:.1f}% >= {self.take_profit_pct}%"
            )

        return StopLossResult(should_stop=False)

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "tracked_tokens": len(self._price_history),
            "atr_multiplier": self.atr_multiplier,
            "max_hold_hours": self.max_hold_hours,
            "fixed_stop_pct": self.fixed_stop_pct,
            "take_profit_pct": self.take_profit_pct,
        }


@dataclass
class PositionInfo:
    """持仓信息(用于组合风险计算)"""
    market_id: str
    question: str
    side: str
    entry_price: float
    current_price: float
    amount: float
    pnl: float


class PortfolioRiskManager:
    """
    组合风险管理系统

    功能:
    1. VaR (Value at Risk): 95%置信度下的最大日亏损
    2. CVaR (Conditional VaR): 超过VaR时的平均亏损
    3. 相关性矩阵: 市场间关联度
    4. 断路器: 日亏≥1.5%、回撤≥20%、VaR95%>2%

    断路器规则:
    - 日亏损 ≥ 初始资金的1.5% → 日亏断路
    - 最大回撤 ≥ 20% → 回撤断路
    - VaR95% > 初始资金的2% → VaR断路
    """

    def __init__(self, initial_capital: float = 100.0):
        self.initial_capital = initial_capital
        self.peak_capital = initial_capital
        self.current_capital = initial_capital

        # 断路器阈值
        self.daily_loss_limit_pct = 1.5   # 日亏限制%
        self.max_drawdown_pct = 20.0      # 最大回撤%
        self.var95_limit_pct = 2.0        # VaR限制%
        self.var_confidence = 0.95        # VaR置信度

        # 断路器状态
        self.circuit_breaker = False
        self.circuit_breaker_reason = ""

        # PnL历史(用于VaR计算)
        self.daily_pnl_history: list[float] = []

        # 市场相关性(简化: 基于同类别市场的经验估计)
        self._correlation_matrix: dict[str, dict[str, float]] = {}

    def update_capital(self, current_capital: float):
        """更新资金"""
        self.current_capital = current_capital
        if current_capital > self.peak_capital:
            self.peak_capital = current_capital

    def record_daily_pnl(self, pnl: float):
        """记录每日PnL"""
        self.daily_pnl_history.append(pnl)
        # 保留最近90天
        if len(self.daily_pnl_history) > 90:
            self.daily_pnl_history = self.daily_pnl_history[-90:]

    def compute_var(self, positions: list[PositionInfo], confidence: float = 0.95) -> float:
        """
        计算VaR (Value at Risk)
        使用历史模拟法

        参数:
            positions: 当前持仓列表
            confidence: 置信度(默认95%)

        返回:
            VaR金额(正数表示可能亏损)
        """
        if not positions:
            return 0

        # 如果有历史PnL数据，使用历史模拟法
        if len(self.daily_pnl_history) >= 20:
            sorted_pnl = sorted(self.daily_pnl_history)
            idx = int((1 - confidence) * len(sorted_pnl))
            var_amount = abs(sorted_pnl[idx])
            return var_amount

        # 否则使用参数法(正态分布假设)
        total_exposure = sum(p.amount for p in positions)
        # 简化: 假设日波动率5%(二元市场)
        daily_vol = 0.05
        z_score = 1.645 if confidence == 0.95 else 2.326  # 95% or 99%

        var_amount = total_exposure * daily_vol * z_score
        return var_amount

    def compute_cvar(self, positions: list[PositionInfo], confidence: float = 0.95) -> float:
        """
        计算CVaR (Conditional VaR / Expected Shortfall)
        超过VaR时的平均亏损

        参数:
            positions: 当前持仓列表
            confidence: 置信度

        返回:
            CVaR金额(正数)
        """
        if len(self.daily_pnl_history) < 20:
            # 参数法: CVaR ≈ VaR × 1.2 (经验系数)
            return self.compute_var(positions, confidence) * 1.2

        sorted_pnl = sorted(self.daily_pnl_history)
        idx = int((1 - confidence) * len(sorted_pnl))
        tail_losses = [abs(p) for p in sorted_pnl[:idx+1]]
        if tail_losses:
            return sum(tail_losses) / len(tail_losses)
        return 0

    def compute_max_drawdown(self) -> float:
        """计算当前最大回撤百分比"""
        if self.peak_capital <= 0:
            return 0
        return ((self.peak_capital - self.current_capital) / self.peak_capital) * 100

    def check_circuit_breakers(
        self,
        daily_pnl: float,
        positions: list[PositionInfo] = None
    ) -> tuple[bool, str]:
        """
        检查所有断路器

        参数:
            daily_pnl: 今日PnL
            positions: 当前持仓列表

        返回:
            (是否触发, 原因)
        """
        # 1. 日亏损断路器
        daily_limit = self.initial_capital * (self.daily_loss_limit_pct / 100)
        if daily_pnl < -daily_limit:
            reason = f"日亏损断路: PnL=${daily_pnl:.2f} < -${daily_limit:.2f} ({self.daily_loss_limit_pct}%)"
            self.circuit_breaker = True
            self.circuit_breaker_reason = reason
            logger.critical(reason)
            return True, reason

        # 2. 最大回撤断路器
        drawdown = self.compute_max_drawdown()
        if drawdown >= self.max_drawdown_pct:
            reason = f"回撤断路: drawdown={drawdown:.1f}% >= {self.max_drawdown_pct}%"
            self.circuit_breaker = True
            self.circuit_breaker_reason = reason
            logger.critical(reason)
            return True, reason

        # 3. VaR断路器
        if positions:
            var = self.compute_var(positions, self.var_confidence)
            var_limit = self.initial_capital * (self.var95_limit_pct / 100)
            if var > var_limit:
                reason = f"VaR断路: VaR95%=${var:.2f} > ${var_limit:.2f} ({self.var95_limit_pct}%)"
                self.circuit_breaker = True
                self.circuit_breaker_reason = reason
                logger.critical(reason)
                return True, reason

        return False, ""

    def reset_circuit_breaker(self):
        """重置断路器"""
        self.circuit_breaker = False
        self.circuit_breaker_reason = ""
        logger.info("断路器已重置")

    def get_portfolio_heat(self, positions: list[PositionInfo]) -> float:
        """
        计算组合热度 [0, 1]
        0 = 冷(无仓位), 1 = 过热(满仓亏损)

        公式: heat = Σ(amount_i / capital) × |pnl_pct_i| / max_pnl_pct
        """
        if not positions:
            return 0

        total_weighted_risk = 0
        for p in positions:
            weight = p.amount / self.initial_capital if self.initial_capital > 0 else 0
            pnl_pct = abs(p.pnl / p.amount) if p.amount > 0 else 0
            total_weighted_risk += weight * min(pnl_pct, 1.0)

        return min(total_weighted_risk, 1.0)

    def get_correlation_adjusted_exposure(
        self,
        positions: list[PositionInfo],
        default_correlation: float = 0.3
    ) -> float:
        """
        计算经相关性调整的总敞口

        简化公式: adjusted = Σ amount_i² + 2 × ρ × Σ(i<j) amount_i × amount_j
        """
        if not positions:
            return 0

        amounts = [p.amount for p in positions]
        n = len(amounts)

        if n == 1:
            return amounts[0]

        # 对角线
        total = sum(a * a for a in amounts)

        # 非对角线(使用默认相关性)
        for i in range(n):
            for j in range(i + 1, n):
                corr = self._get_correlation(
                    positions[i].market_id,
                    positions[j].market_id,
                    default_correlation
                )
                total += 2 * corr * amounts[i] * amounts[j]

        return math.sqrt(total)

    def _get_correlation(self, market_id_1: str, market_id_2: str, default: float = 0.3) -> float:
        """获取两个市场间的相关性"""
        if market_id_1 in self._correlation_matrix:
            return self._correlation_matrix[market_id_1].get(market_id_2, default)
        return default

    def get_stats(self) -> dict:
        """获取风险管理统计"""
        return {
            "initial_capital": self.initial_capital,
            "current_capital": round(self.current_capital, 2),
            "peak_capital": round(self.peak_capital, 2),
            "max_drawdown_pct": round(self.compute_max_drawdown(), 2),
            "circuit_breaker": self.circuit_breaker,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "daily_pnl_history_count": len(self.daily_pnl_history),
            "daily_loss_limit_pct": self.daily_loss_limit_pct,
            "var95_limit_pct": self.var95_limit_pct,
        }
