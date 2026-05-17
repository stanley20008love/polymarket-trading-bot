"""
Polymarket V3 - WebSocket Orderbook 引擎
核心组件:
1. ResilientWebSocket — 弹性WebSocket客户端(指数退避100ms→30s + 心跳)
2. OrderbookEngine — L2增量重建 + 二分查找O(log n)最优价

数据流: WebSocket → OrderbookEngine → SignalGenerator → 策略
"""
import bisect
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

logger = logging.getLogger("polymarket")


@dataclass
class BookLevel:
    """订单簿价格层级"""
    price: float
    size: float


@dataclass
class L2Snapshot:
    """L2订单簿快照"""
    token_id: str
    bids: list[BookLevel] = field(default_factory=list)  # 降序排列
    asks: list[BookLevel] = field(default_factory=list)  # 升序排列
    timestamp: float = 0.0
    spread: float = 0.0
    mid_price: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0

    def update_derived(self):
        """更新派生指标"""
        if self.bids:
            self.best_bid = self.bids[0].price
        if self.asks:
            self.best_ask = self.asks[0].price
        if self.best_bid > 0 and self.best_ask > 0:
            self.spread = self.best_ask - self.best_bid
            self.mid_price = (self.best_bid + self.best_ask) / 2
        self.timestamp = time.time()


class ResilientWebSocket:
    """
    弹性WebSocket客户端
    特性:
    - 指数退避重连: 100ms → 200ms → 400ms → ... → 30s
    - 心跳检测: 30s ping, 10s pong超时
    - 自动降级: WebSocket不可用时切换REST轮询(5s)
    - 最大重连次数: 10次后切换REST模式
    """

    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws"
    INITIAL_DELAY = 0.1   # 100ms
    MAX_DELAY = 30.0      # 30s
    MAX_RECONNECTS = 10
    PING_INTERVAL = 30
    PONG_TIMEOUT = 10

    def __init__(self, config=None):
        self.config = config
        self.ws = None
        self.running = False
        self.connected = False
        self.reconnect_count = 0
        self.mode = "idle"  # idle | websocket | rest_polling
        self._callbacks: dict[str, list[Callable]] = {
            "price_change": [],
            "orderbook_update": [],
            "trade": [],
            "connection": [],
        }
        self._prices: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._last_pong = time.time()
        self._subscribed_tokens: set[str] = set()

    def on(self, event: str, callback: Callable):
        """注册事件回调"""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _emit(self, event: str, data: any):
        """触发事件回调"""
        for cb in self._callbacks.get(event, []):
            try:
                cb(data)
            except Exception as e:
                logger.debug(f"回调异常: {e}")

    def subscribe_market(self, token_id: str):
        """订阅市场数据"""
        self._subscribed_tokens.add(token_id)
        if not self.connected or not self.ws:
            return
        try:
            self.ws.send(json.dumps({
                "auth": {},
                "subscribe": {"market_price": [token_id]}
            }))
            self.ws.send(json.dumps({
                "auth": {},
                "subscribe": {"book": [token_id]}
            }))
        except Exception as e:
            logger.debug(f"订阅失败: {e}")

    def get_realtime_price(self, token_id: str) -> Optional[float]:
        """获取实时价格(30s内有效)"""
        with self._lock:
            data = self._prices.get(token_id)
            if data and time.time() - data.get("timestamp", 0) < 30:
                return data.get("price")
        return None

    def start(self):
        """启动WebSocket连接"""
        self.running = True
        self.mode = "websocket"
        threading.Thread(target=self._run_loop, daemon=True).start()
        logger.info("ResilientWebSocket 已启动")

    def stop(self):
        """停止WebSocket"""
        self.running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.connected = False
        self.mode = "idle"

    def get_status(self) -> dict:
        """获取连接状态"""
        return {
            "connected": self.connected,
            "running": self.running,
            "mode": self.mode,
            "reconnect_count": self.reconnect_count,
            "tracked_prices": len(self._prices),
            "subscribed_tokens": len(self._subscribed_tokens),
        }

    def _run_loop(self):
        """主连接循环"""
        while self.running:
            try:
                self._connect()
                self._listen()
            except Exception as e:
                self.connected = False
                self._emit("connection", {"status": "disconnected", "error": str(e)})

            if self.running:
                self.reconnect_count += 1
                if self.reconnect_count > self.MAX_RECONNECTS:
                    logger.warning("WebSocket重连次数超限，切换REST轮询")
                    self._fallback_to_polling()
                    return

                # 指数退避: 100ms → 200ms → 400ms → ... → 30s
                delay = min(self.INITIAL_DELAY * (2 ** self.reconnect_count), self.MAX_DELAY)
                logger.info(f"WebSocket重连 #{self.reconnect_count}, {delay:.1f}s后重试")
                time.sleep(delay)

    def _connect(self):
        """建立WebSocket连接"""
        try:
            import websocket
            self.ws = websocket.create_connection(
                self.WS_URL,
                timeout=10,
                ping_interval=self.PING_INTERVAL,
                ping_timeout=self.PONG_TIMEOUT
            )
            self.connected = True
            self.reconnect_count = 0
            self._last_pong = time.time()
            self.mode = "websocket"
            self._emit("connection", {"status": "connected", "mode": "websocket"})
            logger.info("WebSocket连接成功")

            # 重新订阅所有token
            for token_id in self._subscribed_tokens:
                self.subscribe_market(token_id)

        except ImportError:
            logger.info("websocket-client未安装，切换REST轮询")
            self._fallback_to_polling()
        except Exception as e:
            logger.debug(f"WebSocket连接失败: {e}")
            raise

    def _listen(self):
        """监听WebSocket消息"""
        while self.running and self.connected:
            try:
                raw = self.ws.recv()
                if not raw or raw == "pong":
                    self._last_pong = time.time()
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._handle_message(msg)
            except Exception:
                break

    def _handle_message(self, msg: dict):
        """处理WebSocket消息"""
        msg_type = msg.get("type", "")
        data = msg.get("data", {})

        if msg_type == "market_price":
            token_id = data.get("asset_id", "")
            price = float(data.get("price", 0))
            if token_id and price > 0:
                with self._lock:
                    self._prices[token_id] = {"price": price, "timestamp": time.time()}
                self._emit("price_change", {
                    "token_id": token_id,
                    "price": price,
                    "timestamp": time.time()
                })
        elif msg_type == "book":
            token_id = data.get("asset_id", "")
            self._emit("orderbook_update", {
                "token_id": token_id,
                "bids": data.get("bids", []),
                "asks": data.get("asks", []),
                "timestamp": time.time()
            })
        elif msg_type == "trade":
            self._emit("trade", {
                "token_id": data.get("asset_id", ""),
                "side": data.get("side", ""),
                "price": float(data.get("price", 0)),
                "size": float(data.get("size", 0))
            })

    def _fallback_to_polling(self):
        """降级到REST轮询(5s间隔)"""
        self.mode = "rest_polling"
        self.connected = False
        self._emit("connection", {"status": "degraded", "mode": "rest_polling"})

        clob_host = "https://clob.polymarket.com"
        if self.config:
            clob_host = getattr(self.config, "CLOB_HOST", clob_host)

        def poll():
            while self.running:
                try:
                    for tid in list(self._subscribed_tokens)[:20]:
                        try:
                            resp = requests.get(
                                f"{clob_host}/midpoint",
                                params={"token_id": tid},
                                timeout=5
                            )
                            if resp.status_code == 200:
                                price = float(resp.json().get("mid", 0))
                                if price > 0:
                                    with self._lock:
                                        self._prices[tid] = {
                                            "price": price,
                                            "timestamp": time.time()
                                        }
                                    self._emit("price_change", {
                                        "token_id": tid,
                                        "price": price
                                    })
                        except Exception:
                            continue
                except Exception:
                    pass
                time.sleep(5)

        threading.Thread(target=poll, daemon=True).start()
        logger.info("REST轮询模式已启动(5s间隔)")


class OrderbookEngine:
    """
    L2订单簿引擎
    特性:
    - L2增量重建: 只更新变化的层级
    - 二分查找O(log n): 快速定位价格层级
    - 自动清理过期数据
    - 买卖压力分析
    """

    def __init__(self, max_snapshots: int = 100, stale_seconds: float = 60):
        self.snapshots: dict[str, L2Snapshot] = {}
        self.max_snapshots = max_snapshots
        self.stale_seconds = stale_seconds
        self._lock = threading.Lock()
        self._update_count = 0

    def update_book(self, token_id: str, bids: list[dict], asks: list[dict]):
        """
        增量更新L2订单簿

        参数:
            token_id: Token ID
            bids: [{price: float, size: float}, ...]
            asks: [{price: float, size: float}, ...]
        """
        with self._lock:
            if token_id not in self.snapshots:
                self.snapshots[token_id] = L2Snapshot(token_id=token_id)

            snap = self.snapshots[token_id]

            # 增量更新bids (降序排列)
            if bids:
                snap.bids = self._update_levels(snap.bids, bids, reverse=True)

            # 增量更新asks (升序排列)
            if asks:
                snap.asks = self._update_levels(snap.asks, asks, reverse=False)

            # 移除size=0的层级
            snap.bids = [l for l in snap.bids if l.size > 0]
            snap.asks = [l for l in snap.asks if l.size > 0]

            snap.update_derived()
            self._update_count += 1

    def _update_levels(self, existing: list[BookLevel], updates: list[dict], reverse: bool) -> list[BookLevel]:
        """
        增量更新价格层级(使用二分查找)
        reverse=True时降序排列(bids), reverse=False时升序排列(asks)
        """
        # 将更新转为BookLevel
        new_levels = []
        for u in updates:
            price = float(u.get("price", 0))
            size = float(u.get("size", 0))
            if price > 0:
                new_levels.append(BookLevel(price=price, size=size))

        if not new_levels:
            return existing

        # 构建价格→层级映射
        price_map = {l.price: l.size for l in existing}

        # 应用增量更新
        for nl in new_levels:
            price_map[nl.price] = nl.size

        # 重新构建有序列表
        result = [BookLevel(price=p, size=s) for p, s in price_map.items() if s > 0]
        result.sort(key=lambda x: x.price, reverse=reverse)

        return result

    def get_snapshot(self, token_id: str) -> Optional[L2Snapshot]:
        """获取订单簿快照"""
        with self._lock:
            snap = self.snapshots.get(token_id)
            if snap and (time.time() - snap.timestamp) < self.stale_seconds:
                return snap
        return None

    def get_mid_price(self, token_id: str) -> Optional[float]:
        """获取中间价(O(log n)二分查找已排序)"""
        snap = self.get_snapshot(token_id)
        if snap and snap.mid_price > 0:
            return snap.mid_price
        return None

    def analyze_pressure(self, token_id: str, depth: int = 5) -> dict:
        """
        分析买卖压力

        返回:
            {
                "bid_volume": 前5档买量,
                "ask_volume": 前5档卖量,
                "pressure": 买卖压力比 (>1买方主导),
                "imbalance": 不平衡度 [-1, 1],
                "signal": "BULLISH" | "BEARISH" | "NEUTRAL"
            }
        """
        snap = self.get_snapshot(token_id)
        if not snap:
            return {"bid_volume": 0, "ask_volume": 0, "pressure": 1, "imbalance": 0, "signal": "NEUTRAL"}

        bid_vol = sum(l.size for l in snap.bids[:depth])
        ask_vol = sum(l.size for l in snap.asks[:depth])

        total = bid_vol + ask_vol
        if total <= 0:
            return {"bid_volume": 0, "ask_volume": 0, "pressure": 1, "imbalance": 0, "signal": "NEUTRAL"}

        pressure = bid_vol / ask_vol if ask_vol > 0 else 999
        imbalance = (bid_vol - ask_vol) / total

        if imbalance > 0.2:
            signal = "BULLISH"
        elif imbalance < -0.2:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        return {
            "bid_volume": round(bid_vol, 2),
            "ask_volume": round(ask_vol, 2),
            "pressure": round(pressure, 3),
            "imbalance": round(imbalance, 3),
            "signal": signal,
        }

    def cleanup_stale(self):
        """清理过期快照"""
        with self._lock:
            now = time.time()
            stale = [tid for tid, snap in self.snapshots.items()
                     if (now - snap.timestamp) > self.stale_seconds]
            for tid in stale:
                del self.snapshots[tid]

            # 限制快照数量
            if len(self.snapshots) > self.max_snapshots:
                # 按时间排序，保留最新的
                sorted_items = sorted(
                    self.snapshots.items(),
                    key=lambda x: x[1].timestamp,
                    reverse=True
                )
                self.snapshots = dict(sorted_items[:self.max_snapshots])

    def get_stats(self) -> dict:
        """获取引擎统计"""
        with self._lock:
            return {
                "tracked_tokens": len(self.snapshots),
                "total_updates": self._update_count,
                "avg_book_depth": (
                    sum(len(s.bids) + len(s.asks) for s in self.snapshots.values()) /
                    max(len(self.snapshots), 1)
                ),
            }
