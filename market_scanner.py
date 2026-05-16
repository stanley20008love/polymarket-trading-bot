"""
Polymarket 量化交易系统 - 市场扫描器
使用 Gamma API 获取活跃市场数据，筛选交易机会
"""
import json
import logging
import time
import requests
from config import Config

logger = logging.getLogger("polymarket")


class MarketInfo:
    """市场信息"""

    def __init__(self, data: dict):
        self.id = data.get("id", "")
        self.question = data.get("question", "")
        self.condition_id = data.get("conditionId", "")
        self.slug = data.get("slug", "")

        # 价格
        prices = data.get("outcomePrices", "[]")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except json.JSONDecodeError:
                prices = []
        self.yes_price = float(prices[0]) if len(prices) > 0 else 0.0
        self.no_price = float(prices[1]) if len(prices) > 1 else 0.0

        # Token IDs
        token_ids = data.get("clobTokenIds", "[]")
        if isinstance(token_ids, str):
            try:
                token_ids = json.loads(token_ids)
            except json.JSONDecodeError:
                token_ids = []
        self.yes_token_id = token_ids[0] if len(token_ids) > 0 else ""
        self.no_token_id = token_ids[1] if len(token_ids) > 1 else ""

        # 成交量与流动性
        self.volume = float(data.get("volumeNum", 0) or 0)
        self.volume_24h = float(data.get("volume24hrClob", 0) or 0)
        self.liquidity = float(data.get("liquidityNum", 0) or 0)

        # 价格变化
        self.price_change_1h = float(data.get("oneHourPriceChange", 0) or 0)
        self.price_change_24h = float(data.get("oneDayPriceChange", 0) or 0)
        self.price_change_1w = float(data.get("oneWeekPriceChange", 0) or 0)

        # 交易参数
        self.min_tick_size = float(data.get("orderPriceMinTickSize", 0.01) or 0.01)
        self.min_order_size = float(data.get("orderMinSize", 5) or 5)
        self.best_bid = float(data.get("bestBid", 0) or 0)
        self.best_ask = float(data.get("bestAsk", 0) or 0)
        self.spread = float(data.get("spread", 0) or 0)
        self.last_trade_price = float(data.get("lastTradePrice", 0) or 0)

        # 手续费
        self.maker_fee = float(data.get("makerBaseFee", 0) or 0) / 10000  # 基点转比例
        self.taker_fee = float(data.get("takerBaseFee", 0) or 0) / 10000

        # 状态
        self.active = data.get("active", False)
        self.closed = data.get("closed", False)
        self.accepting_orders = data.get("acceptingOrders", False)
        self.neg_risk = data.get("negRisk", False)

        # 原始数据
        self.raw = data

    @property
    def total_price(self) -> float:
        """YES + NO 总价"""
        return self.yes_price + self.no_price

    @property
    def arb_spread(self) -> float:
        """套利空间 (1 - YES - NO - 手续费)"""
        total_fees = self.taker_fee * 2  # 买入YES和NO各付一次
        return 1.0 - self.yes_price - self.no_price - total_fees

    @property
    def is_extreme_price(self) -> bool:
        """价格是否极端"""
        return self.yes_price < 0.10 or self.yes_price > 0.90

    def __repr__(self):
        return f"Market({self.question[:30]} YES={self.yes_price:.3f} NO={self.no_price:.3f} Vol=${self.volume:,.0f})"


class MarketScanner:
    """市场扫描器 - 从Gamma API获取市场数据"""

    def __init__(self, config: Config):
        self.config = config
        self.gamma_host = config.GAMMA_HOST
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._cache: dict[str, tuple[float, list[MarketInfo]]] = {}
        self.cache_ttl = 60  # 缓存60秒

    def fetch_active_markets(self, limit: int = 100) -> list[MarketInfo]:
        """获取活跃市场列表"""
        cache_key = f"active_{limit}"
        now = time.time()

        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if now - ts < self.cache_ttl:
                logger.debug(f"使用缓存的市场数据 ({len(data)}条)")
                return data

        try:
            url = f"{self.gamma_host}/markets"
            params = {
                "limit": limit,
                "active": "true",
                "closed": "false",
                "order": "volume",
                "ascending": "false",
            }
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            markets = []
            for m_data in data:
                try:
                    m = MarketInfo(m_data)
                    if m.active and not m.closed and m.yes_token_id and m.no_token_id:
                        markets.append(m)
                except Exception as e:
                    logger.debug(f"跳过无效市场: {e}")

            self._cache[cache_key] = (now, markets)
            logger.info(f"获取到 {len(markets)} 个活跃市场")
            return markets

        except Exception as e:
            logger.error(f"获取市场数据失败: {e}")
            return []

    def find_arbitrage_opportunities(self, markets: list[MarketInfo]) -> list[dict]:
        """
        寻找 YES+NO 套利机会
        当 YES价格 + NO价格 < 1 - 手续费 时，买入双方锁定利润
        """
        opportunities = []
        for m in markets:
            arb_spread = m.arb_spread
            if arb_spread > 0:
                # 计算年化收益
                # 需要知道到期时间，简化处理
                profit_per_dollar = arb_spread
                opportunities.append({
                    "market": m,
                    "type": "YES+NO_ARBITRAGE",
                    "yes_price": m.yes_price,
                    "no_price": m.no_price,
                    "total_price": m.total_price,
                    "arb_spread": arb_spread,
                    "profit_per_dollar": profit_per_dollar,
                    "confidence": "HIGH" if arb_spread > 0.02 else "MEDIUM",
                })

        # 按套利空间排序
        opportunities.sort(key=lambda x: x["arb_spread"], reverse=True)
        return opportunities

    def find_mean_reversion_opportunities(self, markets: list[MarketInfo]) -> list[dict]:
        """
        寻找均值回归机会
        当价格极端 (<10% 或 >90%) 且有足够流动性时
        思路: 如果 YES 价格 < 0.10，买入 YES (便宜赌注)
              如果 YES 价格 > 0.90，买入 NO (便宜赌注)
        """
        opportunities = []
        for m in markets:
            # 跳过低成交量市场
            if m.volume < self.config.MEAN_REV_MIN_VOLUME:
                continue
            if m.liquidity < 5000:
                continue

            if m.yes_price <= self.config.MEAN_REV_LOW_THRESHOLD and m.yes_price > 0.01:
                # YES价格极低 → 买入YES (低成本高回报)
                potential_return = (1.0 - m.yes_price) / m.yes_price  # 潜在回报率
                opportunities.append({
                    "market": m,
                    "type": "MEAN_REVERSION_BUY_YES",
                    "side": "YES",
                    "price": m.yes_price,
                    "potential_return": potential_return,
                    "confidence": "MEDIUM" if potential_return > 5 else "LOW",
                    "reason": f"YES价格极低({m.yes_price:.3f}), 潜在回报{potential_return:.1f}x",
                })

            elif m.yes_price >= self.config.MEAN_REV_HIGH_THRESHOLD:
                # YES价格极高 → 买入NO (低成本高回报)
                no_price = m.no_price
                if no_price > 0.01:
                    potential_return = (1.0 - no_price) / no_price
                    opportunities.append({
                        "market": m,
                        "type": "MEAN_REVERSION_BUY_NO",
                        "side": "NO",
                        "price": no_price,
                        "potential_return": potential_return,
                        "confidence": "MEDIUM" if potential_return > 5 else "LOW",
                        "reason": f"YES价格极高({m.yes_price:.3f}), NO价{no_price:.3f}, 潜在回报{potential_return:.1f}x",
                    })

        opportunities.sort(key=lambda x: x["potential_return"], reverse=True)
        return opportunities

    def find_event_driven_opportunities(self, markets: list[MarketInfo]) -> list[dict]:
        """
        寻找事件驱动机会
        高成交量 + 价格异动 = 可能有新信息导致误定价
        """
        opportunities = []
        for m in markets:
            # 高成交量筛选
            if m.volume_24h < self.config.EVENT_MIN_VOLUME_24H:
                continue

            # 价格异动筛选
            price_change = abs(m.price_change_1h or 0) * 100  # 转为百分比
            if price_change < self.config.EVENT_PRICE_CHANGE_THRESHOLD:
                continue

            # 判断方向
            if m.price_change_1h and m.price_change_1h > 0:
                # 价格上涨 → 可能过度反应 → 买入NO
                side = "NO"
                price = m.no_price
                reason = f"1h涨幅{m.price_change_1h*100:.1f}%, 可能过度反应"
            else:
                # 价格下跌 → 可能过度反应 → 买入YES
                side = "YES"
                price = m.yes_price
                reason = f"1h跌幅{abs(m.price_change_1h or 0)*100:.1f}%, 可能过度反应"

            if price > 0.01 and price < 0.95:
                opportunities.append({
                    "market": m,
                    "type": "EVENT_DRIVEN",
                    "side": side,
                    "price": price,
                    "volume_24h": m.volume_24h,
                    "price_change_1h": m.price_change_1h,
                    "confidence": "LOW",  # 事件驱动天然低置信度
                    "reason": reason,
                })

        opportunities.sort(key=lambda x: abs(x["price_change_1h"] or 0), reverse=True)
        return opportunities

    def scan_all(self) -> dict[str, list[dict]]:
        """执行全部扫描，返回按策略分类的机会"""
        markets = self.fetch_active_markets()

        results = {
            "total_markets": len(markets),
            "arbitrage": [],
            "mean_reversion": [],
            "event_driven": [],
        }

        if self.config.ENABLE_ARBITRAGE:
            results["arbitrage"] = self.find_arbitrage_opportunities(markets)
            logger.info(f"套利机会: {len(results['arbitrage'])}个")

        if self.config.ENABLE_MEAN_REVERSION:
            results["mean_reversion"] = self.find_mean_reversion_opportunities(markets)
            logger.info(f"均值回归机会: {len(results['mean_reversion'])}个")

        if self.config.ENABLE_EVENT_DRIVEN:
            results["event_driven"] = self.find_event_driven_opportunities(markets)
            logger.info(f"事件驱动机会: {len(results['event_driven'])}个")

        return results
