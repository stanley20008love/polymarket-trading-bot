"""
Polymarket 量化交易系统 - 风控模块
硬规则: 日/周亏损限制、仓位限制、止损止盈、熔断机制
"""
import logging
import time
from dataclasses import dataclass, field
from config import Config

logger = logging.getLogger("polymarket")


@dataclass
class Position:
    """持仓记录"""
    market_id: str
    question: str
    token_id: str
    side: str  # "YES" or "NO"
    entry_price: float
    amount: float
    entry_time: float = field(default_factory=time.time)
    stop_loss: float = 0.0
    take_profit: float = 0.0
    current_price: float = 0.0

    @property
    def current_value(self) -> float:
        return self.current_price * self.amount if self.current_price > 0 else self.entry_price * self.amount

    @property
    def pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.amount

    @property
    def pnl_percent(self) -> float:
        if self.entry_price <= 0:
            return 0
        return ((self.current_price - self.entry_price) / self.entry_price) * 100


@dataclass
class TradeRecord:
    """交易记录"""
    timestamp: float
    market_id: str
    question: str
    side: str
    action: str  # "BUY" or "SELL"
    price: float
    amount: float
    pnl: float = 0.0
    strategy: str = ""


class RiskManager:
    """风控引擎"""

    def __init__(self, config: Config):
        self.config = config
        self.positions: list[Position] = []
        self.trade_history: list[TradeRecord] = []
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.total_pnl = 0.0
        self.daily_reset_time = time.time()
        self.weekly_reset_time = time.time()
        self.circuit_breaker = False
        self.circuit_breaker_reason = ""
        self.trade_count = 0

    def reset_daily_if_needed(self):
        """每日重置PnL计数"""
        now = time.time()
        if now - self.daily_reset_time > 86400:  # 24小时
            self.daily_pnl = 0.0
            self.daily_reset_time = now
            logger.info("日PnL已重置")
        if now - self.weekly_reset_time > 604800:  # 7天
            self.weekly_pnl = 0.0
            self.weekly_reset_time = now
            logger.info("周PnL已重置")

    def check_can_trade(self, trade_amount: float) -> tuple[bool, str]:
        """检查是否允许交易，返回(是否允许, 原因)"""
        self.reset_daily_if_needed()

        # 熔断检查
        if self.circuit_breaker:
            return False, f"熔断已触发: {self.circuit_breaker_reason}"

        # 日亏损限制
        daily_limit = self.config.INITIAL_CAPITAL * (self.config.DAILY_LOSS_LIMIT / 100)
        if self.daily_pnl < -daily_limit:
            self.trigger_circuit_breaker(f"日亏损达{self.config.DAILY_LOSS_LIMIT}%")
            return False, f"日亏损已达限制 ${self.daily_pnl:.2f}"

        # 周亏损限制
        weekly_limit = self.config.INITIAL_CAPITAL * (self.config.WEEKLY_LOSS_LIMIT / 100)
        if self.weekly_pnl < -weekly_limit:
            self.trigger_circuit_breaker(f"周亏损达{self.config.WEEKLY_LOSS_LIMIT}%")
            return False, f"周亏损已达限制 ${self.weekly_pnl:.2f}"

        # 持仓数量限制
        if len(self.positions) >= self.config.MAX_POSITIONS:
            return False, f"已达最大持仓数 {self.config.MAX_POSITIONS}"

        # 单笔金额检查
        max_trade = self.config.INITIAL_CAPITAL * (self.config.TRADE_SIZE_PERCENT / 100)
        if trade_amount > max_trade:
            return False, f"单笔金额 ${trade_amount:.2f} 超限 ${max_trade:.2f}"

        # 最小交易金额
        if trade_amount < self.config.MIN_TRADE_SIZE:
            return False, f"单笔金额 ${trade_amount:.2f} 低于最小 ${self.config.MIN_TRADE_SIZE}"

        return True, "通过"

    def calculate_position_size(self, capital: float) -> float:
        """根据资金计算仓位大小"""
        size = capital * (self.config.TRADE_SIZE_PERCENT / 100)
        # 确保 >= 最小下单量
        size = max(size, self.config.MIN_TRADE_SIZE)
        # 确保 <= 20%资金 (安全上限)
        size = min(size, capital * 0.20)
        # 向下取整到0.01
        size = round(size, 2)
        return size

    def add_position(self, position: Position):
        """添加持仓"""
        position.stop_loss = position.entry_price * (1 - self.config.STOP_LOSS_PERCENT / 100)
        position.take_profit = position.entry_price * (1 + self.config.TAKE_PROFIT_PERCENT / 100)
        self.positions.append(position)
        logger.info(
            f"新增持仓: {position.question[:40]} | {position.side} "
            f"@ {position.entry_price:.3f} x{position.amount:.1f} "
            f"止损={position.stop_loss:.3f} 止盈={position.take_profit:.3f}"
        )

    def remove_position(self, market_id: str) -> Position | None:
        """移除持仓"""
        for i, pos in enumerate(self.positions):
            if pos.market_id == market_id:
                return self.positions.pop(i)
        return None

    def update_position_price(self, market_id: str, current_price: float):
        """更新持仓当前价格"""
        for pos in self.positions:
            if pos.market_id == market_id:
                pos.current_price = current_price

    def check_stop_loss_take_profit(self) -> list[tuple[Position, str]]:
        """检查止损止盈，返回需要平仓的列表[(position, reason)]"""
        to_close = []
        for pos in self.positions:
            if pos.current_price <= 0:
                continue
            pnl_pct = pos.pnl_percent
            if pnl_pct <= -self.config.STOP_LOSS_PERCENT:
                to_close.append((pos, f"止损 {pnl_pct:.1f}%"))
                logger.warning(f"触发止损: {pos.question[:40]} | PnL={pnl_pct:.1f}%")
            elif pnl_pct >= self.config.TAKE_PROFIT_PERCENT:
                to_close.append((pos, f"止盈 {pnl_pct:.1f}%"))
                logger.info(f"触发止盈: {pos.question[:40]} | PnL={pnl_pct:.1f}%")
        return to_close

    def record_trade(self, record: TradeRecord):
        """记录交易"""
        self.trade_history.append(record)
        self.trade_count += 1
        self.daily_pnl += record.pnl
        self.weekly_pnl += record.pnl
        self.total_pnl += record.pnl

    def trigger_circuit_breaker(self, reason: str):
        """触发熔断"""
        self.circuit_breaker = True
        self.circuit_breaker_reason = reason
        logger.critical(f"熔断触发: {reason}")

    def reset_circuit_breaker(self):
        """手动重置熔断"""
        self.circuit_breaker = False
        self.circuit_breaker_reason = ""
        logger.info("熔断已重置")

    def get_status(self) -> dict:
        """获取风控状态"""
        total_value = sum(p.current_value for p in self.positions)
        return {
            "circuit_breaker": self.circuit_breaker,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "positions_count": len(self.positions),
            "daily_pnl": round(self.daily_pnl, 2),
            "weekly_pnl": round(self.weekly_pnl, 2),
            "total_pnl": round(self.total_pnl, 2),
            "trade_count": self.trade_count,
            "total_position_value": round(total_value, 2),
            "capital": self.config.INITIAL_CAPITAL,
        }
