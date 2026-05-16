"""
Polymarket 量化交易系统 - 通知模块
支持 Discord Webhook 和 Telegram Bot
"""
import json
import logging
import requests
from config import Config

logger = logging.getLogger("polymarket")


class Notifier:
    """多渠道通知"""

    def __init__(self, config: Config):
        self.discord_webhook = config.DISCORD_WEBHOOK
        self.telegram_token = config.TELEGRAM_BOT_TOKEN
        self.telegram_chat_id = config.TELEGRAM_CHAT_ID
        self.dry_run = config.DRY_RUN

    def send(self, message: str, level: str = "INFO"):
        """发送通知到所有已配置的渠道"""
        prefix = "[模拟]" if self.dry_run else "[实盘]"
        full_msg = f"{prefix} {level} | {message}"
        logger.info(f"通知: {full_msg}")

        if self.discord_webhook:
            self._send_discord(full_msg)
        if self.telegram_token and self.telegram_chat_id:
            self._send_telegram(full_msg)

    def _send_discord(self, message: str):
        try:
            payload = {"content": message}
            resp = requests.post(self.discord_webhook, json=payload, timeout=10)
            if resp.status_code not in (200, 204):
                logger.warning(f"Discord通知失败: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Discord通知异常: {e}")

    def _send_telegram(self, message: str):
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {"chat_id": self.telegram_chat_id, "text": message}
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Telegram通知失败: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Telegram通知异常: {e}")

    def trade_alert(self, action: str, market: str, side: str, price: float, amount: float):
        """交易提醒"""
        emoji = "🟢" if action == "BUY" else "🔴"
        msg = (
            f"{emoji} 交易信号 | {market}\n"
            f"  方向: {side} | 价格: {price:.3f} | 金额: ${amount:.2f}"
        )
        self.send(msg, "TRADE")

    def risk_alert(self, reason: str, details: str = ""):
        """风控警报"""
        msg = f"⚠️ 风控警报 | {reason}"
        if details:
            msg += f"\n  {details}"
        self.send(msg, "RISK")

    def profit_alert(self, pnl: float, total_pnl: float):
        """盈亏提醒"""
        emoji = "📈" if pnl >= 0 else "📉"
        msg = (
            f"{emoji} 盈亏更新\n"
            f"  本次: ${pnl:+.2f} | 累计: ${total_pnl:+.2f}"
        )
        self.send(msg, "PnL")

    def system_alert(self, message: str):
        """系统警报"""
        self.send(f"🔧 系统 | {message}", "SYSTEM")
