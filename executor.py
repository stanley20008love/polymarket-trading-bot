"""
Polymarket 量化交易系统 - 交易执行器
封装 py-clob-client，处理订单创建、签名、提交
"""
import logging
import time
from config import Config
from risk_manager import Position, TradeRecord

logger = logging.getLogger("polymarket")


class OrderExecutor:
    """订单执行器"""

    def __init__(self, config: Config):
        self.config = config
        self.client = None
        self.initialized = False

    def initialize(self) -> bool:
        """初始化 CLOB 客户端"""
        try:
            from py_clob_client.client import ClobClient

            if self.config.DRY_RUN:
                # 模拟模式 - 只创建只读客户端
                self.client = ClobClient(self.config.CLOB_HOST)
                logger.info("模拟模式: 只读CLOB客户端已初始化")
            else:
                # 实盘模式 - 创建认证客户端
                self.client = ClobClient(
                    self.config.CLOB_HOST,
                    key=self.config.PRIVATE_KEY,
                    chain_id=self.config.CHAIN_ID,
                    signature_type=self.config.SIGNATURE_TYPE,
                    funder=self.config.FUNDER_ADDRESS,
                )
                # 创建或获取API凭证
                creds = self.client.create_or_derive_api_creds()
                self.client.set_api_creds(creds)
                logger.info("实盘模式: 认证CLOB客户端已初始化")

            self.initialized = True
            return True

        except ImportError:
            logger.error("py-clob-client 未安装! 运行: pip install py-clob-client")
            return False
        except Exception as e:
            logger.error(f"CLOB客户端初始化失败: {e}")
            return False

    def get_order_book(self, token_id: str) -> dict | None:
        """获取订单簿"""
        if not self.initialized:
            return None
        try:
            book = self.client.get_order_book(token_id)
            return book
        except Exception as e:
            logger.warning(f"获取订单簿失败: {e}")
            return None

    def get_midpoint(self, token_id: str) -> float | None:
        """获取中间价"""
        if not self.initialized:
            return None
        try:
            mid = self.client.get_midpoint(token_id)
            return float(mid) if mid else None
        except Exception as e:
            logger.warning(f"获取中间价失败: {e}")
            return None

    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
    ) -> dict | None:
        """
        提交限价单
        side: "BUY" or "SELL"
        返回订单响应或None
        """
        if not self.initialized:
            logger.error("执行器未初始化")
            return None

        if self.config.DRY_RUN:
            logger.info(
                f"[模拟] 限价单: {side} token={token_id[:20]}... "
                f"price={price:.3f} size={size:.2f}"
            )
            return {
                "orderID": f"dry_run_{int(time.time())}",
                "status": "LIVE",
                "simulated": True,
            }

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL

            order_side = BUY if side == "BUY" else SELL
            order = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side,
            )
            signed_order = self.client.create_order(order)
            resp = self.client.post_order(signed_order, OrderType.GTC)
            logger.info(f"限价单已提交: {resp}")
            return resp

        except Exception as e:
            logger.error(f"限价单提交失败: {e}")
            return None

    def place_market_order(
        self,
        token_id: str,
        amount: float,
        side: str,
    ) -> dict | None:
        """
        提交市价单 (FOK - Fill or Kill)
        """
        if not self.initialized:
            logger.error("执行器未初始化")
            return None

        if self.config.DRY_RUN:
            logger.info(
                f"[模拟] 市价单: {side} token={token_id[:20]}... amount=${amount:.2f}"
            )
            return {
                "orderID": f"dry_run_market_{int(time.time())}",
                "status": "FILLED",
                "simulated": True,
            }

        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL

            order_side = BUY if side == "BUY" else SELL
            mo = MarketOrderArgs(
                token_id=token_id,
                amount=amount,
                side=order_side,
                order_type=OrderType.FOK,
            )
            signed_order = self.client.create_market_order(mo)
            resp = self.client.post_order(signed_order, OrderType.FOK)
            logger.info(f"市价单已提交: {resp}")
            return resp

        except Exception as e:
            logger.error(f"市价单提交失败: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """取消订单"""
        if self.config.DRY_RUN:
            logger.info(f"[模拟] 取消订单: {order_id}")
            return True

        try:
            self.client.cancel(order_id)
            logger.info(f"订单已取消: {order_id}")
            return True
        except Exception as e:
            logger.error(f"取消订单失败: {e}")
            return False

    def cancel_all_orders(self) -> bool:
        """取消所有订单"""
        if self.config.DRY_RUN:
            logger.info("[模拟] 取消所有订单")
            return True

        try:
            self.client.cancel_all()
            logger.info("所有订单已取消")
            return True
        except Exception as e:
            logger.error(f"取消所有订单失败: {e}")
            return False

    def get_open_orders(self) -> list[dict]:
        """获取当前挂单"""
        if self.config.DRY_RUN:
            return []

        try:
            from py_clob_client.clob_types import OpenOrderParams
            orders = self.client.get_orders(OpenOrderParams())
            return orders or []
        except Exception as e:
            logger.warning(f"获取挂单失败: {e}")
            return []

    def execute_opportunity(
        self,
        market_info,
        side: str,
        price: float,
        amount: float,
        strategy: str,
    ) -> Position | None:
        """
        执行交易机会
        返回新持仓或None
        """
        token_id = (
            market_info.yes_token_id if side == "YES" else market_info.no_token_id
        )

        # 计算可买入的份额
        if price <= 0:
            logger.warning(f"价格无效: {price}")
            return None

        shares = amount / price  # 用amount美元买shares份

        # 先尝试限价单 (更好的价格)
        # 使用最佳买价或更低
        limit_price = min(price, market_info.best_ask or price)
        # 对齐到tick size
        tick = market_info.min_tick_size
        limit_price = round(limit_price / tick) * tick

        logger.info(
            f"执行交易: {market_info.question[:40]} | {side} "
            f"limit={limit_price:.3f} shares={shares:.2f} "
            f"策略={strategy}"
        )

        resp = self.place_limit_order(token_id, limit_price, round(shares, 2), "BUY")

        if resp:
            position = Position(
                market_id=market_info.id,
                question=market_info.question,
                token_id=token_id,
                side=side,
                entry_price=limit_price,
                amount=shares,
                entry_time=time.time(),
                current_price=limit_price,
            )
            return position

        return None
