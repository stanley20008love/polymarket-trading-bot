"""
Polymarket 量化交易系统 V2 - 配置管理
基于对250万钱包真实数据分析的升级版
关键修正：手续费模型、风控参数、策略优先级
"""
import os
from dotenv import load_dotenv

load_dotenv()


# ===== Polymarket 真实手续费表 (2026年5月实测) =====
# fee = shares × feeRate × price × (1 - price)
# Maker永远不付费，Taker才付费
# 来源: https://docs.polymarket.com/trading/fees
FEE_SCHEDULE = {
    "crypto":    {"taker_rate": 0.07, "maker_rate": 0.00, "maker_rebate": 0.20},
    "sports":    {"taker_rate": 0.03, "maker_rate": 0.00, "maker_rebate": 0.25},
    "finance":   {"taker_rate": 0.04, "maker_rate": 0.00, "maker_rebate": 0.25},
    "politics":  {"taker_rate": 0.04, "maker_rate": 0.00, "maker_rebate": 0.25},
    "economics": {"taker_rate": 0.05, "maker_rate": 0.00, "maker_rebate": 0.25},
    "culture":   {"taker_rate": 0.05, "maker_rate": 0.00, "maker_rebate": 0.25},
    "weather":   {"taker_rate": 0.05, "maker_rate": 0.00, "maker_rebate": 0.25},
    "tech":      {"taker_rate": 0.04, "maker_rate": 0.00, "maker_rebate": 0.25},
    "mentions":  {"taker_rate": 0.04, "maker_rate": 0.00, "maker_rebate": 0.25},
    "geopolitics": {"taker_rate": 0.00, "maker_rate": 0.00, "maker_rebate": 0.00},
    "general":   {"taker_rate": 0.05, "maker_rate": 0.00, "maker_rebate": 0.25},
}


def calc_taker_fee(shares: float, price: float, category: str = "general") -> float:
    """
    计算Polymarket真实Taker手续费
    fee = shares × feeRate × price × (1 - price)
    手续费在50%概率时最高，极端价格时趋近0
    """
    rate = FEE_SCHEDULE.get(category, FEE_SCHEDULE["general"])["taker_rate"]
    return shares * rate * price * (1 - price)


def calc_maker_rebate(shares: float, price: float, category: str = "general") -> float:
    """
    计算Maker返佣
    rebate = taker_fee × rebateRate（由taker付费中抽取）
    """
    sched = FEE_SCHEDULE.get(category, FEE_SCHEDULE["general"])
    taker_fee = shares * sched["taker_rate"] * price * (1 - price)
    return taker_fee * sched["maker_rebate"]


class Config:
    """系统配置 V2"""

    # 钱包
    PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    WALLET_ADDRESS = os.getenv("POLYMARKET_WALLET_ADDRESS", "")
    FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "0"))

    # 资金
    INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100"))
    MAX_CAPITAL = float(os.getenv("MAX_CAPITAL", "1000"))
    TRADE_SIZE_PERCENT = float(os.getenv("TRADE_SIZE_PERCENT", "8"))  # V2: 10%→8%更保守
    MIN_TRADE_SIZE = float(os.getenv("MIN_TRADE_SIZE", "5"))
    MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "5"))  # V3: 3→5 允许更多分散持仓

    # 风控 V3 - 根据学术研究调整
    # 3%止损对二元市场太紧(正常波动就触发)，8%更合理
    # 来源: 顶级bot(WeatherBot, ProbablyProfit)普遍使用20-30%止损
    STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "8"))    # V3: 3%→8%
    TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "15"))  # V3: 8%→15% 让利润跑
    DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "3"))    # V3: 1.5%→3%
    WEEKLY_LOSS_LIMIT = float(os.getenv("WEEKLY_LOSS_LIMIT", "8"))    # V3: 5%→8%

    # 策略开关
    ENABLE_ARBITRAGE = os.getenv("ENABLE_ARBITRAGE", "true").lower() == "true"
    ENABLE_MULTI_MARKET_ARB = os.getenv("ENABLE_MULTI_MARKET_ARB", "true").lower() == "true"  # V2新增
    ENABLE_MEAN_REVERSION = os.getenv("ENABLE_MEAN_REVERSION", "true").lower() == "true"
    ENABLE_EVENT_DRIVEN = os.getenv("ENABLE_EVENT_DRIVEN", "true").lower() == "true"
    ENABLE_ZERO_FEE = os.getenv("ENABLE_ZERO_FEE", "true").lower() == "true"  # V2新增：地缘政治0手续费
    ENABLE_COPY_TRADING = os.getenv("ENABLE_COPY_TRADING", "false").lower() == "true"

    # 套利参数 V2
    ARB_MIN_SPREAD = float(os.getenv("ARB_MIN_SPREAD", "1.5"))
    ARB_MAX_TOTAL_PRICE = float(os.getenv("ARB_MAX_TOTAL_PRICE", "0.985"))
    # 多市场套利：negRisk事件所有YES总和偏离1.0的最小值
    MULTI_ARB_MIN_GAP = float(os.getenv("MULTI_ARB_MIN_GAP", "2.0"))  # 2%以上才考虑

    # 均值回归 — 放宽范围以捕获更多机会
    MEAN_REV_LOW_THRESHOLD = float(os.getenv("MEAN_REV_LOW_THRESHOLD", "0.20"))   # V3: 0.10→0.20
    MEAN_REV_HIGH_THRESHOLD = float(os.getenv("MEAN_REV_HIGH_THRESHOLD", "0.80"))  # V3: 0.90→0.80
    MEAN_REV_MIN_VOLUME = float(os.getenv("MEAN_REV_MIN_VOLUME", "30000"))         # V3: 50000→30000

    # 事件驱动 — 降低门槛以捕获更多机会
    EVENT_MIN_VOLUME_24H = float(os.getenv("EVENT_MIN_VOLUME_24H", "50000"))  # V3: 100000→50000
    EVENT_PRICE_CHANGE_THRESHOLD = float(os.getenv("EVENT_PRICE_CHANGE_THRESHOLD", "5"))  # V3: 10→5

    # 0手续费策略（地缘政治市场）
    ZERO_FEE_MIN_VOLUME = float(os.getenv("ZERO_FEE_MIN_VOLUME", "100000"))
    ZERO_FEE_MIN_LIQUIDITY = float(os.getenv("ZERO_FEE_MIN_LIQUIDITY", "10000"))

    # 跟单
    COPY_TARGET_ADDRESS = os.getenv("COPY_TARGET_ADDRESS", "")
    COPY_TRADE_AMOUNT = float(os.getenv("COPY_TRADE_AMOUNT", "5"))

    # Kelly Criterion V3
    KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))  # Quarter-Kelly，推荐0.25
    ENABLE_SMART_MONEY = os.getenv("ENABLE_SMART_MONEY", "true").lower() == "true"
    WS_ENABLED = os.getenv("WS_ENABLED", "true").lower() == "true"  # V3.5: WebSocket默认开启，自动降级REST轮询

    # 安全
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

    # 通知
    DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # 运行
    SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "15"))  # V3: 20s→15s 更快捕捉
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # API端点
    CLOB_HOST = "https://clob.polymarket.com"
    GAMMA_HOST = "https://gamma-api.polymarket.com"
    CHAIN_ID = 137  # Polygon

    def validate(self) -> list[str]:
        """验证配置完整性"""
        errors = []
        if not self.DRY_RUN:
            if not self.PRIVATE_KEY:
                errors.append("实盘模式需要设置 POLYMARKET_PRIVATE_KEY")
            if not self.WALLET_ADDRESS:
                errors.append("实盘模式需要设置 POLYMARKET_WALLET_ADDRESS")
            if not self.FUNDER_ADDRESS:
                errors.append("实盘模式需要设置 POLYMARKET_FUNDER_ADDRESS")
        if self.INITIAL_CAPITAL < 5:
            errors.append("初始资金不能低于5 USDC")
        if self.TRADE_SIZE_PERCENT < 1 or self.TRADE_SIZE_PERCENT > 50:
            errors.append("单笔交易比例应在1%-50%之间")
        return errors
