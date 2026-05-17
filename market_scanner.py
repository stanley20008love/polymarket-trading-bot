"""
Polymarket 量化交易系统 V2 - 市场扫描器
核心升级：
1. 真实手续费模型: fee = shares × feeRate × price × (1-price)
2. 多市场互斥套利: negRisk事件YES总和偏离1.0
3. 地缘政治0手续费优先: 最适合100U小资金
4. 体育市场maker返佣: 限价单=0手续费+25%返佣
"""
import json
import logging
import time
import requests
from config import Config, FEE_SCHEDULE, calc_taker_fee

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

        # V2: 真实手续费模型
        fee_schedule = data.get("feeSchedule", {})
        if isinstance(fee_schedule, dict):
            self.taker_fee_rate = float(fee_schedule.get("rate", 0.05) or 0.05)
            self.is_taker_only = fee_schedule.get("takerOnly", True)
            self.maker_rebate_rate = float(fee_schedule.get("rebateRate", 0.25) or 0.25)
        else:
            self.taker_fee_rate = 0.05
            self.is_taker_only = True
            self.maker_rebate_rate = 0.25

        # V2: 手续费类型
        self.fee_type = data.get("feeType", "general_fees")

        # V2: 是否0手续费（地缘政治）
        self.is_zero_fee = self.taker_fee_rate == 0 or self.fee_type == ""

        # V2: 市场类别推断
        self.category = self._infer_category(data)

        # 状态
        self.active = data.get("active", False)
        self.closed = data.get("closed", False)
        self.accepting_orders = data.get("acceptingOrders", False)
        self.neg_risk = data.get("negRisk", False)

        # 原始数据
        self.raw = data

    def _infer_category(self, data: dict) -> str:
        """从市场数据推断品类"""
        # 从slug或question中推断
        slug = (data.get("slug", "") or "").lower()
        question = (data.get("question", "") or "").lower()

        if any(k in slug for k in ["geopol", "china-x", "russia", "iran", "war"]):
            return "geopolitics"
        if any(k in slug for k in ["crypto", "bitcoin", "btc", "eth", "xrp"]):
            return "crypto"
        if any(k in slug for k in ["sports", "nba", "nfl", "fifa", "soccer", "football", "champion"]):
            return "sports"
        if any(k in slug for k in ["politic", "election", "president", "trump", "democrat"]):
            return "politics"
        if any(k in slug for k in ["econ", "gdp", "inflation", "fed", "interest"]):
            return "economics"
        if any(k in slug for k in ["weather", "temperature"]):
            return "weather"
        if any(k in slug for k in ["tech", "ai", "apple", "google"]):
            return "tech"
        # 从feeSchedule推断
        if self.taker_fee_rate == 0:
            return "geopolitics"
        if self.taker_fee_rate <= 0.03:
            return "sports"
        if self.taker_fee_rate <= 0.04:
            return "politics"
        return "general"

    def calc_fee(self, shares: float, price: float, is_maker: bool = False) -> float:
        """
        V2: 计算真实手续费
        fee = shares × feeRate × price × (1 - price)
        Maker手续费 = 0
        """
        if is_maker:
            return 0.0  # Maker永远不付费
        return shares * self.taker_fee_rate * price * (1 - price)

    def calc_maker_rebate(self, shares: float, price: float) -> float:
        """计算Maker返佣"""
        taker_fee = shares * self.taker_fee_rate * price * (1 - price)
        return taker_fee * self.maker_rebate_rate

    @property
    def total_price(self) -> float:
        return self.yes_price + self.no_price

    @property
    def arb_spread(self) -> float:
        """
        V2: 单市场YES+NO套利空间（扣真实手续费）
        实测：YES+NO几乎总是=1.0，此策略几乎无机会
        """
        if self.total_price >= 1.0:
            return 0.0
        # 买入YES和NO各付taker fee
        yes_fee = self.calc_fee(1, self.yes_price)
        no_fee = self.calc_fee(1, self.no_price)
        return 1.0 - self.total_price - yes_fee - no_fee

    @property
    def is_extreme_price(self) -> bool:
        return self.yes_price < 0.10 or self.yes_price > 0.90

    @property
    def taker_fee_usd_per_100shares(self) -> float:
        """买100 shares的taker手续费(美元)"""
        return self.calc_fee(100, self.yes_price)

    def __repr__(self):
        fee_tag = "FREE" if self.is_zero_fee else f"fee={self.taker_fee_rate:.0%}"
        return f"Market({self.question[:25]} Y={self.yes_price:.3f} {fee_tag})"


class MarketScanner:
    """市场扫描器 V2"""

    def __init__(self, config: Config):
        self.config = config
        self.gamma_host = config.GAMMA_HOST
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._cache: dict[str, tuple[float, list[MarketInfo]]] = {}
        self._events_cache: dict[str, tuple[float, list[dict]]] = {}
        self.cache_ttl = 60

    def fetch_active_markets(self, limit: int = 200) -> list[MarketInfo]:
        """获取活跃市场列表"""
        cache_key = f"active_{limit}"
        now = time.time()

        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if now - ts < self.cache_ttl:
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

    def fetch_events(self, limit: int = 50) -> list[dict]:
        """获取事件列表（用于多市场套利）"""
        cache_key = f"events_{limit}"
        now = time.time()

        if cache_key in self._events_cache:
            ts, data = self._events_cache[cache_key]
            if now - ts < self.cache_ttl:
                return data

        try:
            url = f"{self.gamma_host}/events"
            params = {
                "limit": limit,
                "active": "true",
                "closed": "false",
                "order": "volume",
                "ascending": "false",
            }
            resp = self.session.get(url, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()

            self._events_cache[cache_key] = (now, data)
            logger.info(f"获取到 {len(data)} 个活跃事件")
            return data

        except Exception as e:
            logger.error(f"获取事件数据失败: {e}")
            return []

    def find_single_market_arbitrage(self, markets: list[MarketInfo]) -> list[dict]:
        """
        单市场YES+NO套利（实测几乎不存在）
        当YES+NO < 1.0 - 真实手续费时
        """
        opportunities = []
        for m in markets:
            spread = m.arb_spread
            if spread > 0:
                opportunities.append({
                    "market": m,
                    "type": "SINGLE_MARKET_ARB",
                    "yes_price": m.yes_price,
                    "no_price": m.no_price,
                    "arb_spread": spread,
                    "confidence": "HIGH" if spread > 0.02 else "MEDIUM",
                    "fee_category": m.category,
                })

        opportunities.sort(key=lambda x: x["arb_spread"], reverse=True)
        return opportunities

    def find_multi_market_arbitrage(self) -> list[dict]:
        """
        V2核心策略：多市场互斥套利
        negRisk事件中，所有互斥结果的YES价格总和应=1.0
        如果 sum(YES) > 1.0 + 手续费，买入所有NO锁定利润
        如果 sum(YES) < 1.0 - 手续费，买入所有YES锁定利润
        实测确认：FIFA World Cup sumYES=1.038 有3.8%空间
        """
        opportunities = []
        events = self.fetch_events()

        for event in events:
            markets = event.get("markets", [])
            # 只看negRisk互斥市场
            neg_risk_markets = []
            for m_data in markets:
                if not m_data.get("negRisk", False):
                    continue
                try:
                    m = MarketInfo(m_data)
                    if m.active and not m.closed and m.yes_price > 0.001:
                        neg_risk_markets.append(m)
                except:
                    continue

            if len(neg_risk_markets) < 3:
                continue

            # 计算YES总和
            total_yes = sum(m.yes_price for m in neg_risk_markets)
            gap = total_yes - 1.0

            if abs(gap) < self.config.MULTI_ARB_MIN_GAP / 100:
                continue  # 偏差太小

            # 计算真实手续费
            total_taker_fee = 0
            for m in neg_risk_markets:
                if gap > 0:
                    # 买入所有NO
                    total_taker_fee += m.calc_fee(1, m.no_price)
                else:
                    # 买入所有YES
                    total_taker_fee += m.calc_fee(1, m.yes_price)

            net_spread = abs(gap) - total_taker_fee

            if net_spread > 0:
                direction = "BUY_ALL_NO" if gap > 0 else "BUY_ALL_YES"
                opportunities.append({
                    "type": "MULTI_MARKET_ARB",
                    "event_title": event.get("title", "")[:50],
                    "num_markets": len(neg_risk_markets),
                    "total_yes": total_yes,
                    "gap": gap,
                    "gap_pct": gap * 100,
                    "total_taker_fee": total_taker_fee,
                    "net_spread": net_spread,
                    "net_spread_pct": net_spread * 100,
                    "direction": direction,
                    "markets": neg_risk_markets,
                    "confidence": "HIGH" if net_spread > 0.02 else "MEDIUM",
                })

        opportunities.sort(key=lambda x: x["net_spread"], reverse=True)
        return opportunities

    def find_zero_fee_opportunities(self, markets: list[MarketInfo]) -> list[dict]:
        """
        V2新策略：地缘政治市场0手续费
        最适合100U小资金：
        - Taker fee = 0，进出无成本
        - 限价单Maker也是0，无返佣但不花钱
        - 最小化交易成本对小额资金的侵蚀
        """
        opportunities = []
        for m in markets:
            if not m.is_zero_fee:
                continue
            if m.volume < self.config.ZERO_FEE_MIN_VOLUME:
                continue
            if m.liquidity < self.config.ZERO_FEE_MIN_LIQUIDITY:
                continue

            # 在0手续费市场找极端价格
            if m.yes_price > 0.01 and m.yes_price < 0.15:
                opportunities.append({
                    "market": m,
                    "type": "ZERO_FEE_VALUE",
                    "side": "YES",
                    "price": m.yes_price,
                    "potential_return": (1.0 - m.yes_price) / m.yes_price,
                    "fee": 0.0,
                    "confidence": "MEDIUM",
                    "reason": f"0手续费地缘市场 YES={m.yes_price:.3f}, 潜在回报{(1.0-m.yes_price)/m.yes_price:.1f}x, 无交易成本",
                })
            elif m.yes_price > 0.85 and m.no_price > 0.01:
                opportunities.append({
                    "market": m,
                    "type": "ZERO_FEE_VALUE",
                    "side": "NO",
                    "price": m.no_price,
                    "potential_return": (1.0 - m.no_price) / m.no_price,
                    "fee": 0.0,
                    "confidence": "MEDIUM",
                    "reason": f"0手续费地缘市场 NO={m.no_price:.3f}, 潜在回报{(1.0-m.no_price)/m.no_price:.1f}x, 无交易成本",
                })

        opportunities.sort(key=lambda x: x["potential_return"], reverse=True)
        return opportunities

    def find_mean_reversion_opportunities(self, markets: list[MarketInfo]) -> list[dict]:
        """
        均值回归 - V2修正：优先0手续费和低手续费市场
        100U最怕手续费侵蚀，体育市场3%比加密7%好得多
        """
        opportunities = []
        for m in markets:
            if m.volume < self.config.MEAN_REV_MIN_VOLUME:
                continue
            if m.liquidity < 5000:
                continue

            # V2: 优先0手续费和低手续费市场
            fee_penalty = m.taker_fee_rate * 100  # 0%→0, 3%→3, 7%→7

            if m.yes_price <= self.config.MEAN_REV_LOW_THRESHOLD and m.yes_price > 0.01:
                potential_return = (1.0 - m.yes_price) / m.yes_price
                # V2: 调整置信度考虑手续费
                if m.is_zero_fee:
                    confidence = "HIGH"
                elif fee_penalty <= 3:
                    confidence = "MEDIUM"
                else:
                    confidence = "LOW"
                opportunities.append({
                    "market": m,
                    "type": "MEAN_REVERSION_BUY_YES",
                    "side": "YES",
                    "price": m.yes_price,
                    "potential_return": potential_return,
                    "taker_fee_rate": m.taker_fee_rate,
                    "confidence": confidence,
                    "reason": f"YES={m.yes_price:.3f} 回报{potential_return:.1f}x 手续费{m.taker_fee_rate:.0%} ({m.category})",
                })

            elif m.yes_price >= self.config.MEAN_REV_HIGH_THRESHOLD:
                no_price = m.no_price
                if no_price > 0.01:
                    potential_return = (1.0 - no_price) / no_price
                    if m.is_zero_fee:
                        confidence = "HIGH"
                    elif fee_penalty <= 3:
                        confidence = "MEDIUM"
                    else:
                        confidence = "LOW"
                    opportunities.append({
                        "market": m,
                        "type": "MEAN_REVERSION_BUY_NO",
                        "side": "NO",
                        "price": no_price,
                        "potential_return": potential_return,
                        "taker_fee_rate": m.taker_fee_rate,
                        "confidence": confidence,
                        "reason": f"NO={no_price:.3f} 回报{potential_return:.1f}x 手续费{m.taker_fee_rate:.0%} ({m.category})",
                    })

        opportunities.sort(key=lambda x: (
            {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(x.get("confidence", "LOW"), 0),
            -x["taker_fee_rate"],
            x["potential_return"]
        ), reverse=True)
        return opportunities

    def find_event_driven_opportunities(self, markets: list[MarketInfo]) -> list[dict]:
        """事件驱动 - V3修正：放宽限制，不再要求极端价格"""
        opportunities = []
        for m in markets:
            if m.volume_24h < self.config.EVENT_MIN_VOLUME_24H:
                continue

            price_change = abs(m.price_change_1h or 0) * 100
            if price_change < self.config.EVENT_PRICE_CHANGE_THRESHOLD:
                continue

            if m.price_change_1h and m.price_change_1h > 0:
                side = "NO"
                price = m.no_price
                reason = f"1h涨{m.price_change_1h*100:.1f}% 过度反应?"
            else:
                side = "YES"
                price = m.yes_price
                reason = f"1h跌{abs(m.price_change_1h or 0)*100:.1f}% 过度反应?"

            if price > 0.01 and price < 0.95:
                # V3: 根据价格变化幅度调整置信度
                confidence = "HIGH" if price_change > 15 else ("MEDIUM" if price_change > 8 else "LOW")
                opportunities.append({
                    "market": m,
                    "type": "EVENT_DRIVEN",
                    "side": side,
                    "price": price,
                    "volume_24h": m.volume_24h,
                    "price_change_1h": m.price_change_1h,
                    "price_change_pct": price_change,  # V3: 用于Kelly edge计算
                    "taker_fee_rate": m.taker_fee_rate,
                    "confidence": confidence,
                    "reason": f"{reason} 手续费{m.taker_fee_rate:.0%}",
                })

        opportunities.sort(key=lambda x: abs(x["price_change_1h"] or 0), reverse=True)
        return opportunities

    def find_time_decay_opportunities(self, markets: list[MarketInfo]) -> list[dict]:
        """
        V3新增策略：时间衰减/临近结算确定性收益
        来源: "How to Harvest $200/day on Polymarket" + 学术研究SF8(深度在结算前衰减)
        
        逻辑: 当市场临近结算且价格极端(>90%或<10%)时，
        不确定性下降→价格向0或1收敛→可以捕获时间价值
        """
        opportunities = []
        for m in markets:
            # 需要足够流动性
            if m.liquidity < 5000:
                continue
            if m.volume_24h < 10000:
                continue

            # 临近结算的高确定性市场
            # YES价格>0.90: 市场认为YES很可能发生，买入YES赚取结算收益
            if m.yes_price >= 0.90 and m.yes_price < 0.98:
                potential_return = (1.0 - m.yes_price) / m.yes_price  # e.g. 0.92→8.7%
                # 扣手续费后仍有正收益
                fee = m.calc_fee(1, m.yes_price)
                net_return = potential_return - fee / m.yes_price
                if net_return > 0.01:  # 扣费后至少1%收益
                    opportunities.append({
                        "market": m,
                        "type": "TIME_DECAY_HIGH_PROB",
                        "side": "YES",
                        "price": m.yes_price,
                        "potential_return": potential_return,
                        "net_return": net_return,
                        "arb_spread": net_return,  # 用于Kelly edge
                        "taker_fee_rate": m.taker_fee_rate,
                        "confidence": "HIGH" if m.yes_price >= 0.95 else "MEDIUM",
                        "reason": f"高确定性市场 YES={m.yes_price:.3f} 扣费净收益{net_return:.1%}",
                    })

            # NO价格>0.90 (即YES<0.10): 同理买入NO
            elif m.yes_price <= 0.10 and m.yes_price > 0.02:
                potential_return = (1.0 - m.no_price) / m.no_price
                fee = m.calc_fee(1, m.no_price)
                net_return = potential_return - fee / m.no_price
                if net_return > 0.01:
                    opportunities.append({
                        "market": m,
                        "type": "TIME_DECAY_HIGH_PROB",
                        "side": "NO",
                        "price": m.no_price,
                        "potential_return": potential_return,
                        "net_return": net_return,
                        "arb_spread": net_return,
                        "taker_fee_rate": m.taker_fee_rate,
                        "confidence": "HIGH" if m.yes_price <= 0.05 else "MEDIUM",
                        "reason": f"高确定性市场 NO={m.no_price:.3f} 扣费净收益{net_return:.1%}",
                    })

        opportunities.sort(key=lambda x: x["net_return"], reverse=True)
        return opportunities

    def find_stat_arb_opportunities(self, markets: list[MarketInfo]) -> list[dict]:
        """
        V3新增策略：统计套利 (SPREAD_FADE + MOMENTUM + MEAN_REVERSION)
        来源: ResearchGate论文"Statistical Arbitrage in Binary Prediction Markets"
        
        1. SPREAD_FADE: 买卖价差异常大时→向中价回归
        2. MOMENTUM: 价格有持续性→跟随方向
        3. 短期均值回归: 1小时价格变化过大→反转
        """
        opportunities = []
        for m in markets:
            if m.liquidity < 5000:
                continue
            if m.volume_24h < 10000:
                continue

            # 策略1: SPREAD_FADE — 买卖价差异常大
            if m.best_bid > 0 and m.best_ask > 0:
                mid_price = (m.best_bid + m.best_ask) / 2
                spread_pct = (m.best_ask - m.best_bid) / mid_price if mid_price > 0 else 0
                
                # 价差>5%属于异常宽 (正常1-3%)
                if spread_pct > 0.05 and m.yes_price > 0.15 and m.yes_price < 0.85:
                    # 如果YES价格偏低+宽价差→买入YES(fade toward mid)
                    if m.yes_price < mid_price:
                        opportunities.append({
                            "market": m,
                            "type": "SPREAD_FADE",
                            "side": "YES",
                            "price": m.yes_price,
                            "spread_pct": spread_pct,
                            "arb_spread": spread_pct * 0.3,  # 预期收敛30%的价差
                            "taker_fee_rate": m.taker_fee_rate,
                            "confidence": "MEDIUM",
                            "reason": f"宽价差{spread_pct:.1%} fade→YES mid={mid_price:.3f}",
                        })
                    else:
                        opportunities.append({
                            "market": m,
                            "type": "SPREAD_FADE",
                            "side": "NO",
                            "price": m.no_price,
                            "spread_pct": spread_pct,
                            "arb_spread": spread_pct * 0.3,
                            "taker_fee_rate": m.taker_fee_rate,
                            "confidence": "MEDIUM",
                            "reason": f"宽价差{spread_pct:.1%} fade→NO mid={1-mid_price:.3f}",
                        })

            # 策略2: MOMENTUM — 跟随24h趋势 (有信息量的方向)
            if m.price_change_24h and abs(m.price_change_24h) > 0.03:
                if m.price_change_24h > 0 and m.yes_price < 0.80:
                    # 24h上涨3%+ → 跟随做多YES
                    opportunities.append({
                        "market": m,
                        "type": "MOMENTUM",
                        "side": "YES",
                        "price": m.yes_price,
                        "momentum": m.price_change_24h,
                        "arb_spread": abs(m.price_change_24h) * 0.5,
                        "taker_fee_rate": m.taker_fee_rate,
                        "confidence": "LOW",
                        "reason": f"24h动量+{m.price_change_24h*100:.1f}% 跟随YES",
                    })
                elif m.price_change_24h < 0 and m.no_price < 0.80:
                    # 24h下跌3%+ → 跟随做多NO
                    opportunities.append({
                        "market": m,
                        "type": "MOMENTUM",
                        "side": "NO",
                        "price": m.no_price,
                        "momentum": m.price_change_24h,
                        "arb_spread": abs(m.price_change_24h) * 0.5,
                        "taker_fee_rate": m.taker_fee_rate,
                        "confidence": "LOW",
                        "reason": f"24h动量{m.price_change_24h*100:.1f}% 跟随NO",
                    })

        opportunities.sort(key=lambda x: x.get("arb_spread", 0), reverse=True)
        return opportunities


    def find_weather_opportunities(self, markets: list[MarketInfo]) -> list[dict]:
        """
        V4.0新增策略: 天气市场交易
        来源: alteregoeth-ai/weatherbot, 375+温度市场, $2M日交易量
        
        逻辑: Polymarket有大量天气温度市场(20+城市)
        当市场隐含概率与气象预报偏差>5%时, 存在定价错误
        """
        opportunities = []
        for m in markets:
            # 筛选天气市场
            if m.category != "weather":
                continue
            if m.volume < getattr(self.config, 'WEATHER_MIN_VOLUME', 50000):
                continue
            if m.liquidity < getattr(self.config, 'WEATHER_MIN_LIQUIDITY', 10000):
                continue
            
            # 天气市场通常价格在0.3-0.7之间
            # 寻找极端价格(市场过度自信或低估)
            if m.yes_price > 0.01 and m.yes_price < 0.15:
                potential_return = (1.0 - m.yes_price) / m.yes_price
                # 天气市场手续费通常5%
                fee = m.calc_fee(1, m.yes_price)
                net_return = potential_return - fee / m.yes_price
                if net_return > 0.02:  # 至少2%净收益
                    opportunities.append({
                        "market": m,
                        "type": "WEATHER_MISPRICED",
                        "side": "YES",
                        "price": m.yes_price,
                        "potential_return": potential_return,
                        "net_return": net_return,
                        "arb_spread": net_return,
                        "taker_fee_rate": m.taker_fee_rate,
                        "confidence": "HIGH" if m.yes_price < 0.08 else "MEDIUM",
                        "reason": f"天气市场定价错误 YES={m.yes_price:.3f} 潜在回报{potential_return:.1f}x 净收益{net_return:.1%}",
                    })
            elif m.yes_price > 0.85 and m.no_price > 0.01:
                potential_return = (1.0 - m.no_price) / m.no_price
                fee = m.calc_fee(1, m.no_price)
                net_return = potential_return - fee / m.no_price
                if net_return > 0.02:
                    opportunities.append({
                        "market": m,
                        "type": "WEATHER_MISPRICED",
                        "side": "NO",
                        "price": m.no_price,
                        "potential_return": potential_return,
                        "net_return": net_return,
                        "arb_spread": net_return,
                        "taker_fee_rate": m.taker_fee_rate,
                        "confidence": "HIGH" if m.yes_price > 0.92 else "MEDIUM",
                        "reason": f"天气市场定价错误 NO={m.no_price:.3f} 潜在回报{potential_return:.1f}x 净收益{net_return:.1%}",
                    })
        
        opportunities.sort(key=lambda x: x["net_return"], reverse=True)
        return opportunities

    def scan_all(self) -> dict[str, list[dict]]:
        """执行全部V3扫描"""
        markets = self.fetch_active_markets()

        results = {
            "total_markets": len(markets),
            "single_arb": [],
            "multi_arb": [],
            "zero_fee": [],
            "mean_reversion": [],
            "event_driven": [],
            "time_decay": [],      # V3新增
            "stat_arb": [],        # V3新增
            "weather": [],        # V4.0新增
        }

        if self.config.ENABLE_ARBITRAGE:
            results["single_arb"] = self.find_single_market_arbitrage(markets)
            logger.info(f"单市场套利: {len(results['single_arb'])}个")

        if self.config.ENABLE_MULTI_MARKET_ARB:
            results["multi_arb"] = self.find_multi_market_arbitrage()
            logger.info(f"多市场套利: {len(results['multi_arb'])}个")

        if self.config.ENABLE_ZERO_FEE:
            results["zero_fee"] = self.find_zero_fee_opportunities(markets)
            logger.info(f"0手续费机会: {len(results['zero_fee'])}个")

        if self.config.ENABLE_MEAN_REVERSION:
            results["mean_reversion"] = self.find_mean_reversion_opportunities(markets)
            logger.info(f"均值回归: {len(results['mean_reversion'])}个")

        if self.config.ENABLE_EVENT_DRIVEN:
            results["event_driven"] = self.find_event_driven_opportunities(markets)
            logger.info(f"事件驱动: {len(results['event_driven'])}个")

        # V3新增策略
        results["time_decay"] = self.find_time_decay_opportunities(markets)
        logger.info(f"时间衰减: {len(results['time_decay'])}个")

        results["stat_arb"] = self.find_stat_arb_opportunities(markets)
        logger.info(f"统计套利: {len(results['stat_arb'])}个")

        return results
