"""
Polymarket V3 - 订单簿深度分析器
核心能力：深度分析、鲸鱼检测、冰山订单、流动性缺口、价格冲击估计
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from config import Config

logger = logging.getLogger("polymarket")


@dataclass
class OrderBookLevel:
    price: float
    size: float
    total: float
    num_orders: int = 1


@dataclass
class OrderBookSnapshot:
    token_id: str
    timestamp: float
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2 if self.best_bid > 0 and self.best_ask > 0 else 0.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid if self.best_bid > 0 else 0.0

    @property
    def spread_bps(self) -> float:
        return (self.spread / self.mid_price * 10000) if self.mid_price > 0 else 0.0

    @property
    def bid_depth_usd(self) -> float:
        return sum(l.price * l.size for l in self.bids[:5])

    @property
    def ask_depth_usd(self) -> float:
        return sum(l.price * l.size for l in self.asks[:5])

    @property
    def depth_imbalance(self) -> float:
        bid, ask = self.bid_depth_usd, self.ask_depth_usd
        total = bid + ask
        return (bid - ask) / total if total > 0 else 0.0


@dataclass
class WhaleOrder:
    side: str
    price: float
    size: float
    size_usd: float
    is_iceberg: bool = False
    confidence: float = 0.5


@dataclass
class MicrostructureSignal:
    signal_type: str
    direction: str
    strength: float
    market_id: str = ""
    question: str = ""
    reason: str = ""
    timestamp: float = field(default_factory=time.time)


class OrderBookAnalyzer:
    WHALE_SIZE_THRESHOLD = 2000
    ICEBERG_DETECT_WINDOW = 5
    ICEBERG_SIZE_VARIANCE = 0.15

    def __init__(self, config: Config):
        self.config = config
        self.snapshots: dict[str, list[OrderBookSnapshot]] = {}
        self.whale_orders: dict[str, list[WhaleOrder]] = {}
        self.signals: list[MicrostructureSignal] = []
        self.max_history = 50

    def parse_order_book(self, raw_book: dict, token_id: str) -> OrderBookSnapshot:
        snapshot = OrderBookSnapshot(token_id=token_id, timestamp=time.time())
        for bid in (raw_book.get("bids") or []):
            price, size = float(bid.get("price", 0)), float(bid.get("size", 0))
            if price > 0 and size > 0:
                level = OrderBookLevel(price=price, size=size, total=size + (snapshot.bids[-1].total if snapshot.bids else 0), num_orders=int(bid.get("numOrders", 1)))
                snapshot.bids.append(level)
        for ask in (raw_book.get("asks") or []):
            price, size = float(ask.get("price", 0)), float(ask.get("size", 0))
            if price > 0 and size > 0:
                level = OrderBookLevel(price=price, size=size, total=size + (snapshot.asks[-1].total if snapshot.asks else 0), num_orders=int(ask.get("numOrders", 1)))
                snapshot.asks.append(level)
        if token_id not in self.snapshots:
            self.snapshots[token_id] = []
        self.snapshots[token_id].append(snapshot)
        if len(self.snapshots[token_id]) > self.max_history:
            self.snapshots[token_id] = self.snapshots[token_id][-self.max_history:]
        return snapshot

    def detect_whales(self, snapshot: OrderBookSnapshot) -> list[WhaleOrder]:
        whales = []
        for level in snapshot.bids:
            size_usd = level.price * level.size
            if size_usd >= self.WHALE_SIZE_THRESHOLD:
                whales.append(WhaleOrder(side="BID", price=level.price, size=level.size, size_usd=size_usd, is_iceberg=self._detect_iceberg(snapshot.token_id, "BID", level), confidence=0.8))
        for level in snapshot.asks:
            size_usd = level.price * level.size
            if size_usd >= self.WHALE_SIZE_THRESHOLD:
                whales.append(WhaleOrder(side="ASK", price=level.price, size=level.size, size_usd=size_usd, is_iceberg=self._detect_iceberg(snapshot.token_id, "ASK", level), confidence=0.8))
        if whales:
            self.whale_orders[snapshot.token_id] = whales
        return whales

    def _detect_iceberg(self, token_id: str, side: str, level: OrderBookLevel) -> bool:
        history = self.snapshots.get(token_id, [])
        if len(history) < self.ICEBERG_DETECT_WINDOW:
            return False
        matching = 0
        for snap in history[-self.ICEBERG_DETECT_WINDOW:]:
            for l in (snap.bids if side == "BID" else snap.asks):
                if abs(l.size - level.size) / max(level.size, 1) < self.ICEBERG_SIZE_VARIANCE and abs(l.price - level.price) < 0.01:
                    matching += 1
                    break
        return matching >= self.ICEBERG_DETECT_WINDOW * 0.6

    def analyze_pressure(self, snapshot: OrderBookSnapshot) -> MicrostructureSignal:
        imbalance = snapshot.depth_imbalance
        whales = self.detect_whales(snapshot)
        whale_bid = sum(w.size_usd for w in whales if w.side == "BID")
        whale_ask = sum(w.size_usd for w in whales if w.side == "ASK")
        pressure = imbalance * 0.4
        reasons = []
        if abs(imbalance) > 0.3:
            reasons.append(f"深度失衡{'买方' if imbalance > 0 else '卖方'}{abs(imbalance):.0%}")
        if whale_bid + whale_ask > 0:
            wi = (whale_bid - whale_ask) / (whale_bid + whale_ask)
            pressure += wi * 0.4
            if abs(wi) > 0.3:
                reasons.append(f"鲸鱼偏向{'买方' if wi > 0 else '卖方'}")
        if snapshot.spread_bps < 100:
            pressure += 0.1 * (1 if imbalance > 0 else -1)
            reasons.append("价差收窄")
        if pressure > 0.2:
            return MicrostructureSignal(signal_type="PRESSURE_SHIFT", direction="BULLISH", strength=min(1.0, pressure), reason=" | ".join(reasons) or "无明确方向")
        elif pressure < -0.2:
            return MicrostructureSignal(signal_type="PRESSURE_SHIFT", direction="BEARISH", strength=min(1.0, abs(pressure)), reason=" | ".join(reasons) or "无明确方向")
        return MicrostructureSignal(signal_type="PRESSURE_SHIFT", direction="NEUTRAL", strength=0, reason="无明确方向")

    def estimate_price_impact(self, snapshot: OrderBookSnapshot, amount_usd: float, side: str) -> dict:
        levels = snapshot.asks if side == "BUY" else snapshot.bids
        remaining, total_shares, worst_price, consumed = amount_usd, 0.0, 0.0, 0
        for level in levels:
            cost = level.price * level.size
            if remaining <= cost:
                total_shares += remaining / level.price
                worst_price = level.price
                consumed += 1
                remaining = 0
                break
            total_shares += level.size
            worst_price = level.price
            remaining -= cost
            consumed += 1
        avg_price = amount_usd / total_shares if total_shares > 0 else 0
        mid = snapshot.mid_price
        impact = abs(avg_price - mid) / mid * 100 if mid > 0 else 0
        return {"amount_usd": amount_usd, "side": side, "avg_fill_price": round(avg_price, 4), "worst_price": round(worst_price, 4), "total_shares": round(total_shares, 2), "levels_consumed": consumed, "price_impact_pct": round(impact, 2), "is_liquid": impact < 1.0 and total_shares > 5}

    def full_analysis(self, raw_book: dict, token_id: str, market_id: str = "", question: str = "") -> dict:
        snapshot = self.parse_order_book(raw_book, token_id)
        whales = self.detect_whales(snapshot)
        pressure = self.analyze_pressure(snapshot)
        impact_buy = self.estimate_price_impact(snapshot, 8, "BUY")
        impact_sell = self.estimate_price_impact(snapshot, 8, "SELL")
        if pressure.strength > 0.3:
            self.signals.append(MicrostructureSignal(signal_type="PRESSURE_SHIFT", direction=pressure.direction, strength=pressure.strength, market_id=market_id, question=question, reason=pressure.reason))
        for w in whales:
            d = "BULLISH" if w.side == "BID" else "BEARISH"
            self.signals.append(MicrostructureSignal(signal_type="WHALE_BID" if w.side == "BID" else "WHALE_ASK", direction=d, strength=w.confidence * (w.size_usd / 10000), market_id=market_id, question=question, reason=f"{'买方' if w.side == 'BID' else '卖方'}鲸鱼${w.size_usd:.0f}{'(冰山)' if w.is_iceberg else ''}"))
        self.signals = [s for s in self.signals if time.time() - s.timestamp < 3600]
        return {"snapshot": snapshot, "whales": whales, "pressure": pressure, "impact_buy": impact_buy, "impact_sell": impact_sell, "is_tradable": impact_buy["is_liquid"] and impact_sell["is_liquid"], "whale_count": len(whales), "iceberg_count": sum(1 for w in whales if w.is_iceberg), "spread_bps": snapshot.spread_bps, "depth_imbalance": snapshot.depth_imbalance}

    def get_signal_for_market(self, market_id: str) -> Optional[MicrostructureSignal]:
        market_signals = [s for s in self.signals if s.market_id == market_id]
        return max(market_signals, key=lambda s: s.strength) if market_signals else None

    def get_status(self) -> dict:
        return {"tracked_tokens": len(self.snapshots), "total_snapshots": sum(len(v) for v in self.snapshots.values()), "whale_orders": sum(len(v) for v in self.whale_orders.values()), "active_signals": len(self.signals), "signals": [{"type": s.signal_type, "direction": s.direction, "strength": round(s.strength, 2), "market": s.question[:30]} for s in self.signals[:10]]}
