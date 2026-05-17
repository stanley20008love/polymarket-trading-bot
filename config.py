"""
Polymarket 量化交易系统 V6.0 - 配置管理 (聚焦策略版)

V6.0 核心变更 (基于顶级交易员诊断):
1. 关闭8个无效策略，仅保留Weather (唯一有学术/实证支撑的策略)
2. $100本金全部投入单策略，避免策略间争抢资源
3. 仓位管理: Quarter-Kelly → $1-2/笔 (正edge下复利增长)
4. 启用校准反馈回路，目标100样本后Brier<0.18
5. 风控参数策略专属化 (Weather SL=15%, TP=25%)
6. 接入GFS/Open-Meteo真实集合预报数据

参考文献:
- ColdMath ($300→$219K) 天气套利机器人
- alteregoeth-ai/weatherbot (开源天气策略)
- arXiv:2412.14144 (Kelly Criterion在预测市场的应用)
- ResearchGate "Statistical Arbitrage in Binary Prediction Markets"
"""
import os
from dotenv import load_dotenv

load_dotenv()


# ===== Polymarket 真实手续费表 (2026年5月实测) =====
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
    rate = FEE_SCHEDULE.get(category, FEE_SCHEDULE["general"])["taker_rate"]
    return shares * rate * price * (1 - price)


def calc_maker_rebate(shares: float, price: float, category: str = "general") -> float:
    sched = FEE_SCHEDULE.get(category, FEE_SCHEDULE["general"])
    taker_fee = shares * sched["taker_rate"] * price * (1 - price)
    return taker_fee * sched["maker_rebate"]


class Config:
    """
    系统配置 V6.0 - 聚焦策略版
    
    核心原则: 一个经过验证的策略 > 九个未验证的策略
    Kelly说Edge=-4%? → 关掉8个，聚焦有正Edge的Weather
    """

    # 钱包
    PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    WALLET_ADDRESS = os.getenv("POLYMARKET_WALLET_ADDRESS", "")
    FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "0"))

    # 资金 — $100全部投入Weather策略
    INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100"))
    MAX_CAPITAL = float(os.getenv("MAX_CAPITAL", "1000"))
    # V6.0: 单笔仓位从8%降到3% (Quarter-Kelly for $100)
    # $100 × 3% = $3/笔，在正edge下复利增长
    TRADE_SIZE_PERCENT = float(os.getenv("TRADE_SIZE_PERCENT", "3"))
    MIN_TRADE_SIZE = float(os.getenv("MIN_TRADE_SIZE", "1"))
    # V6.0: 最大持仓从5降到3 (单策略不需要5个持仓位)
    MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))

    # ===== 风控 V6.0 - 策略专属参数 =====
    # Weather策略: 温度预报有自然不确定性，需要更宽的止损
    # ColdMath使用20-30%止损，我们用15%起步
    STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "15"))    # V6: 8%→15% 天气预报波动大
    TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "25"))  # V6: 15%→25% 让利润跑
    DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "5"))       # V6: 3%→5% 允许正常波动序列
    WEEKLY_LOSS_LIMIT = float(os.getenv("WEEKLY_LOSS_LIMIT", "12"))     # V6: 8%→12%

    # ===== V6.0 策略开关 — 只开Weather =====
    # 关闭8个无效策略 (诊断: Edge=-4%, Kelly=0%, 0校准样本)
    # 仅Weather有学术支撑(ColdMath)和实证数据
    ENABLE_ARBITRAGE = os.getenv("ENABLE_ARBITRAGE", "false").lower() == "true"          # V6: 关闭 (零售延迟无法捕获)
    ENABLE_MULTI_MARKET_ARB = os.getenv("ENABLE_MULTI_MARKET_ARB", "false").lower() == "true"  # V6: 关闭 (跨平台不可行)
    ENABLE_MEAN_REVERSION = os.getenv("ENABLE_MEAN_REVERSION", "false").lower() == "true"     # V6: 关闭 (二元市场无均值)
    ENABLE_EVENT_DRIVEN = os.getenv("ENABLE_EVENT_DRIVEN", "false").lower() == "true"       # V6: 关闭 (10s扫描太慢)
    ENABLE_ZERO_FEE = os.getenv("ENABLE_ZERO_FEE", "false").lower() == "true"            # V6: 关闭 (非独立策略)
    ENABLE_COPY_TRADING = os.getenv("ENABLE_COPY_TRADING", "false").lower() == "true"

    # V6.0 核心: Weather策略 (唯一启用)
    ENABLE_WEATHER = os.getenv("ENABLE_WEATHER", "true").lower() == "true"              # 唯一保留
    WEATHER_MIN_VOLUME = float(os.getenv("WEATHER_MIN_VOLUME", "10000"))               # V6: 50K→10K 次要城市机会更多
    WEATHER_MIN_LIQUIDITY = float(os.getenv("WEATHER_MIN_LIQUIDITY", "3000"))            # V6: 10K→3K
    WEATHER_CITIES = os.getenv("WEATHER_CITIES",
        "new-york,los-angeles,chicago,miami,dallas,denver,atlanta,seattle,boston,phoenix,"
        "san-francisco,houston,washington-dc,philadelphia,london,tokyo,"
        "buenos-aires,cape-town,sydney,mumbai")
    # GFS集合预报偏差阈值 (低于此偏差不值得交易)
    WEATHER_MIN_DEVIATION = float(os.getenv("WEATHER_MIN_DEVIATION", "5"))  # 5%以上偏差

    # 关闭其他策略 (保留配置以便后续启用)
    ENABLE_DUMP_HEDGE = os.getenv("ENABLE_DUMP_HEDGE", "false").lower() == "true"         # V6: 关闭 (BTC市场非核心)
    ENABLE_COUNTER_WALLET = os.getenv("ENABLE_COUNTER_WALLET", "false").lower() == "true"  # V6: 关闭 (信号源未验证)
    ENABLE_SMART_MONEY = os.getenv("ENABLE_SMART_MONEY", "false").lower() == "true"        # V6: 关闭 (25%刷量交易)

    # 套利参数 (保留但默认关闭)
    ARB_MIN_SPREAD = float(os.getenv("ARB_MIN_SPREAD", "1.5"))
    ARB_MAX_TOTAL_PRICE = float(os.getenv("ARB_MAX_TOTAL_PRICE", "0.985"))
    MULTI_ARB_MIN_GAP = float(os.getenv("MULTI_ARB_MIN_GAP", "2.0"))

    # 均值回归参数 (保留但默认关闭)
    MEAN_REV_LOW_THRESHOLD = float(os.getenv("MEAN_REV_LOW_THRESHOLD", "0.20"))
    MEAN_REV_HIGH_THRESHOLD = float(os.getenv("MEAN_REV_HIGH_THRESHOLD", "0.80"))
    MEAN_REV_MIN_VOLUME = float(os.getenv("MEAN_REV_MIN_VOLUME", "30000"))

    # 事件驱动参数 (保留但默认关闭)
    EVENT_MIN_VOLUME_24H = float(os.getenv("EVENT_MIN_VOLUME_24H", "50000"))
    EVENT_PRICE_CHANGE_THRESHOLD = float(os.getenv("EVENT_PRICE_CHANGE_THRESHOLD", "5"))

    # 0手续费参数 (保留但默认关闭)
    ZERO_FEE_MIN_VOLUME = float(os.getenv("ZERO_FEE_MIN_VOLUME", "100000"))
    ZERO_FEE_MIN_LIQUIDITY = float(os.getenv("ZERO_FEE_MIN_LIQUIDITY", "10000"))

    # 跟单
    COPY_TARGET_ADDRESS = os.getenv("COPY_TARGET_ADDRESS", "")
    COPY_TRADE_AMOUNT = float(os.getenv("COPY_TRADE_AMOUNT", "5"))
    DUMP_HEDGE_MAX_CYCLE_MINUTES = int(os.getenv("DUMP_HEDGE_MAX_CYCLE_MINUTES", "15"))
    DUMP_HEDGE_HEDGE_THRESHOLD = float(os.getenv("DUMP_HEDGE_HEDGE_THRESHOLD", "0.02"))
    COUNTER_WALLET_MIN_LOSS_RATE = float(os.getenv("COUNTER_WALLET_MIN_LOSS_RATE", "0.7"))

    # Kelly Criterion V6.0 — Quarter-Kelly保守策略
    # $100 + 8% Weather Edge → Kelly建议约12%单笔
    # Quarter-Kelly: 3%单笔 = $3/笔 (安全、可持续)
    KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))  # Quarter-Kelly
    WS_ENABLED = os.getenv("WS_ENABLED", "true").lower() == "true"

    # 安全
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

    # 通知
    DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # 运行
    SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "10"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # API端点
    CLOB_HOST = "https://clob.polymarket.com"
    GAMMA_HOST = "https://gamma-api.polymarket.com"
    CHAIN_ID = 137  # Polygon

    # V6.0 校准反馈
    CALIBRATION_FEEDBACK_ENABLED = os.getenv("CALIBRATION_FEEDBACK_ENABLED", "true").lower() == "true"
    CALIBRATION_FEEDBACK_INTERVAL = int(os.getenv("CALIBRATION_FEEDBACK_INTERVAL", "10"))  # 每10周期调整权重
    CALIBRATION_MIN_SAMPLES = int(os.getenv("CALIBRATION_MIN_SAMPLES", "20"))  # 20样本后开始调整

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
        # V6.0: 检查策略一致性
        active_strategies = sum([
            self.ENABLE_ARBITRAGE, self.ENABLE_MEAN_REVERSION, self.ENABLE_EVENT_DRIVEN,
            self.ENABLE_ZERO_FEE, self.ENABLE_WEATHER, self.ENABLE_DUMP_HEDGE,
            self.ENABLE_COUNTER_WALLET, self.ENABLE_MULTI_MARKET_ARB,
        ])
        if not self.ENABLE_WEATHER and active_strategies == 0:
            errors.append("至少需要一个启用的策略 (推荐: Weather)")
        return errors
