"""
Polymarket V3 - WebSocket实时数据客户端
替代20秒REST轮询，实时价格+订单簿推送
"""
import json
import logging
import threading
import time
from typing import Callable, Optional
import requests
from config import Config

logger = logging.getLogger("polymarket")


class WebSocketClient:
    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws"

    def __init__(self, config: Config):
        self.config = config
        self.ws = None
        self.running = False
        self.connected = False
        self.reconnect_count = 0
        self.max_reconnects = 10
        self._callbacks: dict[str, list[Callable]] = {"price_change": [], "orderbook_update": [], "trade": [], "connection": []}
        self._prices: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._last_pong = time.time()

    def on(self, event: str, callback: Callable):
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _emit(self, event: str, data: any):
        for cb in self._callbacks.get(event, []):
            try: cb(data)
            except: pass

    def subscribe_market(self, token_id: str):
        if not self.connected or not self.ws: return
        try:
            self.ws.send(json.dumps({"auth": {}, "subscribe": {"market_price": [token_id]}}))
            self.ws.send(json.dumps({"auth": {}, "subscribe": {"book": [token_id]}}))
        except: pass

    def get_realtime_price(self, token_id: str) -> Optional[float]:
        with self._lock:
            data = self._prices.get(token_id)
            if data and time.time() - data.get("timestamp", 0) < 30:
                return data.get("price")
        return None

    def start(self):
        self.running = True
        threading.Thread(target=self._run_loop, daemon=True).start()
        logger.info("WebSocket客户端已启动")

    def stop(self):
        self.running = False
        if self.ws:
            try: self.ws.close()
            except: pass
        self.connected = False

    def _run_loop(self):
        while self.running:
            try:
                self._connect()
                self._listen()
            except Exception as e:
                self.connected = False
                self._emit("connection", {"status": "disconnected"})
            if self.running:
                self.reconnect_count += 1
                if self.reconnect_count > self.max_reconnects:
                    self.running = False
                    break
                delay = min(2 ** self.reconnect_count, 60)
                time.sleep(delay)

    def _connect(self):
        try:
            import websocket
            self.ws = websocket.create_connection(self.WS_URL, timeout=10, ping_interval=30, ping_timeout=10)
            self.connected = True
            self.reconnect_count = 0
            self._last_pong = time.time()
            self._emit("connection", {"status": "connected"})
        except ImportError:
            self.running = False
            self._fallback_to_polling()
        except:
            raise

    def _listen(self):
        while self.running and self.connected:
            try:
                raw = self.ws.recv()
                if not raw or raw == "pong":
                    self._last_pong = time.time()
                    continue
                try:
                    msg = json.loads(raw)
                except: continue
                self._handle_message(msg)
            except:
                break

    def _handle_message(self, msg: dict):
        msg_type, data = msg.get("type", ""), msg.get("data", {})
        if msg_type == "market_price":
            token_id, price = data.get("asset_id", ""), float(data.get("price", 0))
            with self._lock:
                self._prices[token_id] = {"price": price, "timestamp": time.time()}
            self._emit("price_change", {"token_id": token_id, "price": price, "timestamp": time.time()})
        elif msg_type == "book":
            self._emit("orderbook_update", {"token_id": data.get("asset_id", ""), "bids": data.get("bids", []), "asks": data.get("asks", []), "timestamp": time.time()})
        elif msg_type == "trade":
            self._emit("trade", {"token_id": data.get("asset_id", ""), "side": data.get("side", ""), "price": float(data.get("price", 0)), "size": float(data.get("size", 0))})

    def _fallback_to_polling(self):
        logger.info("WebSocket不可用，使用REST轮询(5s)")
        def poll():
            while True:
                try:
                    for tid in list(self._prices.keys())[:20]:
                        try:
                            resp = requests.get(f"{self.config.CLOB_HOST}/midpoint", params={"token_id": tid}, timeout=5)
                            if resp.status_code == 200:
                                price = float(resp.json().get("mid", 0))
                                if price > 0:
                                    with self._lock:
                                        self._prices[tid] = {"price": price, "timestamp": time.time()}
                                    self._emit("price_change", {"token_id": tid, "price": price})
                        except: continue
                except: pass
                time.sleep(5)
        threading.Thread(target=poll, daemon=True).start()

    def get_status(self) -> dict:
        return {"connected": self.connected, "running": self.running, "reconnect_count": self.reconnect_count, "tracked_prices": len(self._prices), "mode": "websocket" if self.connected else "rest_polling"}
