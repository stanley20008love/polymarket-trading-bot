"""
Polymarket 量化交易系统 - 配置管理
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """系统配置，从 .env 文件加载"""

    # 钱包
    PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    WALLET_ADDRESS = os.getenv("POLYMARKET_WALLET_ADDRESS", "")
    FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "0"))

    # 资金
    INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100"))
    MAX_CAPITAL = float(os.getenv("MAX_CAPITAL", "1000"))
    TRADE_SIZE_PERCENT = float(os.getenv("TRADE_SIZE_PERCENT", "10"))
    MIN_TRADE_SIZE = float(os.getenv("MIN_TRADE_SIZE", "5"))
    MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))

    # 风控
    STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "5"))
    TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "15"))
    DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "3"))
    WEEKLY_LOSS_LIMIT = float(os.getenv("WEEKLY_LOSS_LIMIT", "8"))

    # 策略开关
    ENABLE_ARBITRAGE = os.getenv("ENABLE_ARBITRAGE", "true").lower() == "true"
    ENABLE_MEAN_REVERSION = os.getenv("ENABLE_MEAN_REVERSION", "true").lower() == "true"
    ENABLE_EVENT_DRIVEN = os.getenv("ENABLE_EVENT_DRIVEN", "true").lower() == "true"
    ENABLE_COPY_TRADING = os.getenv("ENABLE_COPY_TRADING", "false").lower() == "true"

    # 套利参数
    ARB_MIN_SPREAD = float(os.getenv("ARB_MIN_SPREAD", "1.5"))
    ARB_MAX_TOTAL_PRICE = float(os.getenv("ARB_MAX_TOTAL_PRICE", "0.985"))

    # 均值回归
    MEAN_REV_LOW_THRESHOLD = float(os.getenv("MEAN_REV_LOW_THRESHOLD", "0.10"))
    MEAN_REV_HIGH_THRESHOLD = float(os.getenv("MEAN_REV_HIGH_THRESHOLD", "0.90"))
    MEAN_REV_MIN_VOLUME = float(os.getenv("MEAN_REV_MIN_VOLUME", "50000"))

    # 事件驱动
    EVENT_MIN_VOLUME_24H = float(os.getenv("EVENT_MIN_VOLUME_24H", "100000"))
    EVENT_PRICE_CHANGE_THRESHOLD = float(os.getenv("EVENT_PRICE_CHANGE_THRESHOLD", "10"))

    # 跟单
    COPY_TARGET_ADDRESS = os.getenv("COPY_TARGET_ADDRESS", "")
    COPY_TRADE_AMOUNT = float(os.getenv("COPY_TRADE_AMOUNT", "5"))

    # 安全
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

    # 通知
    DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # 运行
    SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # API端点
    CLOB_HOST = "https://clob.polymarket.com"
    GAMMA_HOST = "https://gamma-api.polymarket.com"
    CHAIN_ID = 137  # Polygon

    def validate(self) -> list[str]:
        """验证配置完整性，返回错误列表"""
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
