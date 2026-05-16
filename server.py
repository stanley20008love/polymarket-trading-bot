"""
Polymarket 量化交易系统 V3 - Web 服务器 + 仪表盘
Zeabur 需要一个 HTTP 端口来判断服务是否存活
此模块在后台启动 bot 的同时，在前台提供健康检查和仪表盘端点
"""
import json
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

# 全局状态 - bot主线程会更新
bot_status = {
    "version": "3.0",
    "status": "starting",
    "scan_count": 0,
    "positions": 0,
    "daily_pnl": 0.0,
    "total_pnl": 0.0,
    "trade_count": 0,
    "circuit_breaker": False,
    "uptime_seconds": 0,
    "last_scan_time": "",
    "strategies": {
        "arbitrage": "disabled",
        "mean_reversion": "disabled",
        "event_driven": "disabled",
    },
    "v3_modules": {
        "smart_money": "disabled",
        "orderbook_analyzer": "disabled",
        "kelly_sizing": "disabled",
        "data_store": "disabled",
        "websocket": "disabled",
        "backtester": "disabled",
    },
    "mode": "dry_run",
    "capital": 0,
    "start_time": "",
    "last_error": "",
}

# V3 仪表盘 HTML
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polymarket V3 Trading Bot</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0d1117; color:#c9d1d9; font-family:'Segoe UI',system-ui,sans-serif; }
.container { max-width:1200px; margin:0 auto; padding:20px; }
header { display:flex; justify-content:space-between; align-items:center; padding:16px 0; border-bottom:1px solid #21262d; margin-bottom:24px; }
header h1 { font-size:20px; color:#58a6ff; }
header .version { font-size:12px; color:#8b949e; background:#161b22; padding:4px 8px; border-radius:4px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; margin-bottom:24px; }
.card { background:#161b22; border:1px solid #21262d; border-radius:8px; padding:16px; }
.card h3 { font-size:12px; color:#8b949e; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; }
.card .value { font-size:28px; font-weight:600; }
.card .value.positive { color:#3fb950; }
.card .value.negative { color:#f85149; }
.card .value.neutral { color:#58a6ff; }
.status-bar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:24px; }
.badge { display:inline-flex; align-items:center; gap:4px; padding:4px 10px; border-radius:12px; font-size:12px; font-weight:500; }
.badge.running { background:#1a3a2a; color:#3fb950; }
.badge.starting { background:#3a2e1a; color:#d29922; }
.badge.error { background:#3a1a1a; color:#f85149; }
.badge.disabled { background:#1a1a2a; color:#8b949e; }
.badge.enabled { background:#1a2a3a; color:#58a6ff; }
.badge.dry_run { background:#1a2a3a; color:#79c0ff; }
.badge::before { content:''; width:6px; height:6px; border-radius:50%; }
.badge.running::before { background:#3fb950; }
.badge.starting::before { background:#d29922; animation:pulse 1.5s infinite; }
.badge.error::before { background:#f85149; }
.badge.disabled::before { background:#484f58; }
.badge.enabled::before { background:#58a6ff; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
.module-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:8px; }
.module-item { background:#0d1117; border:1px solid #21262d; border-radius:6px; padding:10px 12px; display:flex; justify-content:space-between; align-items:center; }
.module-item .name { font-size:13px; color:#c9d1d9; }
.module-item .dot { width:8px; height:8px; border-radius:50%; }
.module-item .dot.on { background:#3fb950; }
.module-item .dot.off { background:#484f58; }
.refresh-info { text-align:center; color:#484f58; font-size:12px; padding:16px 0; }
pre.error-log { background:#0d1117; border:1px solid #21262d; border-radius:6px; padding:12px; font-family:monospace; font-size:12px; color:#f85149; overflow-x:auto; max-height:200px; }
</style>
</head>
<body>
<div class="container">
<header>
  <h1>Polymarket V3 Trading Bot</h1>
  <span class="version">v3.0.0</span>
</header>

<div class="status-bar" id="statusBar"></div>

<div class="grid" id="statsGrid"></div>

<div class="card" style="margin-bottom:24px">
  <h3>V3 Modules</h3>
  <div class="module-grid" id="moduleGrid"></div>
</div>

<div id="errorSection" style="display:none;margin-bottom:24px">
  <div class="card">
    <h3>Last Error</h3>
    <pre class="error-log" id="errorLog"></pre>
  </div>
</div>

<div class="refresh-info">
  Auto-refresh every 10s &middot; <a href="/status" style="color:#58a6ff">JSON API</a>
</div>
</div>

<script>
let lastData = null;
function update() {
  fetch('/status')
    .then(r => r.json())
    .then(data => {
      lastData = data;
      // Status bar
      const sb = document.getElementById('statusBar');
      const statusClass = data.status === 'running' ? 'running' : data.status === 'error' ? 'error' : 'starting';
      const modeLabel = data.mode === 'dry_run' ? 'DRY RUN' : 'LIVE';
      const modeClass = data.mode === 'dry_run' ? 'dry_run' : 'running';
      sb.innerHTML = `
        <span class="badge ${statusClass}">${data.status.toUpperCase()}</span>
        <span class="badge ${modeClass}">${modeLabel}</span>
        <span class="badge disabled">Capital: $${data.capital}</span>
        <span class="badge disabled">Scans: ${data.scan_count}</span>
      `;

      // Stats grid
      const pnlClass = data.total_pnl > 0 ? 'positive' : data.total_pnl < 0 ? 'negative' : 'neutral';
      const dailyClass = data.daily_pnl > 0 ? 'positive' : data.daily_pnl < 0 ? 'negative' : 'neutral';
      document.getElementById('statsGrid').innerHTML = `
        <div class="card"><h3>Total PnL</h3><div class="value ${pnlClass}">$${data.total_pnl.toFixed(2)}</div></div>
        <div class="card"><h3>Daily PnL</h3><div class="value ${dailyClass}">$${data.daily_pnl.toFixed(2)}</div></div>
        <div class="card"><h3>Positions</h3><div class="value neutral">${data.positions}</div></div>
        <div class="card"><h3>Trades</h3><div class="value neutral">${data.trade_count}</div></div>
        <div class="card"><h3>Uptime</h3><div class="value neutral">${formatTime(data.uptime_seconds)}</div></div>
        <div class="card"><h3>Circuit Breaker</h3><div class="value ${data.circuit_breaker ? 'negative' : 'positive'}">${data.circuit_breaker ? 'TRIGGERED' : 'OK'}</div></div>
      `;

      // V3 Modules
      const mg = document.getElementById('moduleGrid');
      const v3 = data.v3_modules || {};
      mg.innerHTML = Object.entries(v3).map(([k,v]) => `
        <div class="module-item">
          <span class="name">${k.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase())}</span>
          <span class="dot ${v === 'enabled' ? 'on' : 'off'}"></span>
        </div>
      `).join('');

      // Error
      const es = document.getElementById('errorSection');
      const el = document.getElementById('errorLog');
      if (data.last_error) { es.style.display='block'; el.textContent=data.last_error; }
      else { es.style.display='none'; }
    })
    .catch(e => console.error('Fetch error:', e));
}
function formatTime(s) {
  if (!s) return '0s';
  const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=s%60;
  return h>0 ? h+'h '+m+'m' : m>0 ? m+'m '+sec+'s' : sec+'s';
}
update();
setInterval(update, 10000);
</script>
</body>
</html>"""


class HealthHandler(BaseHTTPRequestHandler):
    """健康检查 HTTP 处理器"""

    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "bot": bot_status,
                "message": "Polymarket V3 Trading Bot is running",
            })
        elif self.path == "/status":
            self._send_json(200, bot_status)
        elif self.path == "/dashboard":
            self._send_html(200, DASHBOARD_HTML)
        elif self.path == "/ping":
            self._send_text(200, "pong")
        else:
            self._send_text(404, "Not Found")

    def _send_json(self, code, data):
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, html):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """静默日志，避免刷屏"""
        pass


def start_health_server(port=8000):
    """启动健康检查服务器"""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"[Health] 健康检查服务器启动在端口 {port}")
    server.serve_forever()


def run_bot():
    """在后台线程中运行交易机器人"""
    import logging
    import sys

    # 延迟导入，确保所有模块可用
    sys.path.insert(0, os.path.dirname(__file__))

    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    logger = logging.getLogger("polymarket")

    try:
        from config import Config
        from market_scanner import MarketScanner
        from risk_manager import RiskManager
        from executor import OrderExecutor
        from notifier import Notifier

        config = Config()

        # 更新全局状态
        bot_status["mode"] = "dry_run" if config.DRY_RUN else "LIVE"
        bot_status["capital"] = config.INITIAL_CAPITAL
        bot_status["strategies"]["arbitrage"] = "enabled" if config.ENABLE_ARBITRAGE else "disabled"
        bot_status["strategies"]["mean_reversion"] = "enabled" if config.ENABLE_MEAN_REVERSION else "disabled"
        bot_status["strategies"]["event_driven"] = "enabled" if config.ENABLE_EVENT_DRIVEN else "disabled"

        # V3 模块状态
        bot_status["v3_modules"]["smart_money"] = "enabled" if getattr(config, 'ENABLE_SMART_MONEY', False) else "disabled"
        bot_status["v3_modules"]["kelly_sizing"] = "enabled" if getattr(config, 'KELLY_FRACTION', 0) > 0 else "disabled"
        bot_status["v3_modules"]["data_store"] = "enabled"
        bot_status["v3_modules"]["websocket"] = "enabled" if getattr(config, 'WS_ENABLED', False) else "disabled"

        # 初始化模块
        scanner = MarketScanner(config)
        risk = RiskManager(config)
        executor = OrderExecutor(config)
        notifier = Notifier(config)

        # 尝试初始化 V3 模块
        try:
            from data_store import DataStore
            data_store = DataStore()
            bot_status["v3_modules"]["data_store"] = "enabled"
            logger.info("V3 DataStore 初始化成功")
        except Exception as e:
            logger.warning(f"V3 DataStore 初始化失败: {e}")
            data_store = None
            bot_status["v3_modules"]["data_store"] = f"error: {e}"

        try:
            from smart_money_tracker import SmartMoneyTracker
            smart_money = SmartMoneyTracker(config)
            bot_status["v3_modules"]["smart_money"] = "enabled"
            logger.info("V3 SmartMoneyTracker 初始化成功")
        except Exception as e:
            logger.warning(f"V3 SmartMoneyTracker 初始化失败: {e}")
            smart_money = None
            bot_status["v3_modules"]["smart_money"] = f"error: {e}"

        try:
            from orderbook_analyzer import OrderBookAnalyzer
            orderbook = OrderBookAnalyzer(config)
            bot_status["v3_modules"]["orderbook_analyzer"] = "enabled"
            logger.info("V3 OrderbookAnalyzer 初始化成功")
        except Exception as e:
            logger.warning(f"V3 OrderbookAnalyzer 初始化失败: {e}")
            orderbook = None
            bot_status["v3_modules"]["orderbook_analyzer"] = f"error: {e}"

        # 加载状态
        state_file = os.path.join(os.path.dirname(__file__), "bot_state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    state = json.load(f)
                risk.total_pnl = state.get("total_pnl", 0.0)
                risk.trade_count = state.get("trade_count", 0)
            except Exception:
                pass

        # 初始化执行器
        if not executor.initialize():
            logger.warning("执行器初始化失败，模拟模式运行")
            config.DRY_RUN = True
            bot_status["mode"] = "dry_run"

        bot_status["status"] = "running"
        bot_status["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        start_time = time.time()
        scan_count = 0

        mode = "模拟" if config.DRY_RUN else "实盘"
        logger.info(f"V3 交易系统启动 [{mode}] ${config.INITIAL_CAPITAL}")
        try:
            notifier.system_alert(f"V3 交易系统启动 [{mode}] ${config.INITIAL_CAPITAL}")
        except Exception:
            pass

        # 主循环
        while True:
            try:
                scan_count += 1
                bot_status["scan_count"] = scan_count
                bot_status["uptime_seconds"] = int(time.time() - start_time)
                bot_status["last_scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                bot_status["last_error"] = ""

                logger.info(f"--- 扫描周期 #{scan_count} ---")

                # 1. 扫描市场
                opportunities = scanner.scan_all()

                # 2. V3 Smart Money 分析
                smart_signals = {}
                if smart_money and getattr(config, 'ENABLE_SMART_MONEY', False):
                    try:
                        smart_signals = smart_money.analyze_all()
                        if smart_signals:
                            logger.info(f"Smart Money: {len(smart_signals)} signals")
                    except Exception as e:
                        logger.warning(f"Smart Money 分析失败: {e}")

                # 3. V3 Orderbook 分析
                book_analysis = {}
                if orderbook:
                    try:
                        for opp in opportunities.get("arbitrage", []) + opportunities.get("mean_reversion", []):
                            m = opp["market"]
                            analysis = orderbook.analyze_orderbook(m.id)
                            if analysis:
                                book_analysis[m.id] = analysis
                    except Exception as e:
                        logger.warning(f"Orderbook 分析失败: {e}")

                # 4. 更新持仓价格
                for pos in risk.positions:
                    try:
                        mid = executor.get_midpoint(pos.token_id)
                        if mid and mid > 0:
                            pos.current_price = mid
                    except Exception:
                        pass

                # 5. 检查止损止盈
                to_close = risk.check_stop_loss_take_profit()
                for pos, reason in to_close:
                    logger.info(f"平仓: {pos.question[:40]} | {reason}")
                    if config.DRY_RUN:
                        from risk_manager import TradeRecord
                        record = TradeRecord(
                            timestamp=time.time(),
                            market_id=pos.market_id,
                            question=pos.question,
                            side=pos.side,
                            action="SELL",
                            price=pos.current_price,
                            amount=pos.amount,
                            pnl=pos.pnl,
                            strategy="STOP_LOSS_TP",
                        )
                        risk.record_trade(record)
                        risk.remove_position(pos.market_id)

                # 6. 处理交易机会
                from risk_manager import Position, TradeRecord

                # 套利
                if config.ENABLE_ARBITRAGE:
                    for opp in opportunities.get("arbitrage", []):
                        m = opp["market"]
                        if opp["arb_spread"] * 100 < config.ARB_MIN_SPREAD:
                            continue
                        if any(p.market_id == m.id for p in risk.positions):
                            continue
                        trade_amount = risk.calculate_position_size(config.INITIAL_CAPITAL)
                        can, reason = risk.check_can_trade(trade_amount * 2)
                        if not can:
                            continue
                        logger.info(f"套利: {m.question[:40]} 空间={opp['arb_spread']*100:.2f}%")
                        if config.DRY_RUN:
                            yes_shares = trade_amount / m.yes_price if m.yes_price > 0 else 0
                            yes_pos = Position(
                                market_id=m.id, question=m.question,
                                token_id=m.yes_token_id, side="YES",
                                entry_price=m.yes_price, amount=yes_shares,
                                current_price=m.yes_price,
                            )
                            risk.add_position(yes_pos)
                            no_shares = trade_amount / m.no_price if m.no_price > 0 else 0
                            no_pos = Position(
                                market_id=f"{m.id}_NO", question=m.question,
                                token_id=m.no_token_id, side="NO",
                                entry_price=m.no_price, amount=no_shares,
                                current_price=m.no_price,
                            )
                            risk.add_position(no_pos)
                            record = TradeRecord(
                                timestamp=time.time(), market_id=m.id,
                                question=m.question, side="YES+NO", action="BUY",
                                price=m.total_price, amount=trade_amount * 2,
                                strategy="ARBITRAGE",
                            )
                            risk.record_trade(record)

                # 均值回归
                if config.ENABLE_MEAN_REVERSION:
                    for opp in opportunities.get("mean_reversion", []):
                        m = opp["market"]
                        if opp["confidence"] == "LOW":
                            continue
                        if any(p.market_id == m.id for p in risk.positions):
                            continue
                        trade_amount = risk.calculate_position_size(config.INITIAL_CAPITAL)
                        can, reason = risk.check_can_trade(trade_amount)
                        if not can:
                            continue
                        side = opp["side"]
                        price = opp["price"]
                        logger.info(f"均值回归: {m.question[:40]} {side}@{price:.3f}")
                        if config.DRY_RUN:
                            shares = trade_amount / price if price > 0 else 0
                            pos = Position(
                                market_id=m.id, question=m.question,
                                token_id=m.yes_token_id if side == "YES" else m.no_token_id,
                                side=side, entry_price=price, amount=shares,
                                current_price=price,
                            )
                            risk.add_position(pos)
                            record = TradeRecord(
                                timestamp=time.time(), market_id=m.id,
                                question=m.question, side=side, action="BUY",
                                price=price, amount=trade_amount,
                                strategy="MEAN_REVERSION",
                            )
                            risk.record_trade(record)

                # 事件驱动
                if config.ENABLE_EVENT_DRIVEN:
                    for opp in opportunities.get("event_driven", []):
                        m = opp["market"]
                        if not m.is_extreme_price:
                            continue
                        if any(p.market_id == m.id for p in risk.positions):
                            continue
                        trade_amount = risk.calculate_position_size(config.INITIAL_CAPITAL) * 0.5
                        trade_amount = max(trade_amount, config.MIN_TRADE_SIZE)
                        can, reason = risk.check_can_trade(trade_amount)
                        if not can:
                            continue
                        side = opp["side"]
                        price = opp["price"]
                        logger.info(f"事件驱动: {m.question[:40]} {side}@{price:.3f}")
                        if config.DRY_RUN:
                            shares = trade_amount / price if price > 0 else 0
                            pos = Position(
                                market_id=m.id, question=m.question,
                                token_id=m.yes_token_id if side == "YES" else m.no_token_id,
                                side=side, entry_price=price, amount=shares,
                                current_price=price,
                            )
                            risk.add_position(pos)
                            record = TradeRecord(
                                timestamp=time.time(), market_id=m.id,
                                question=m.question, side=side, action="BUY",
                                price=price, amount=trade_amount,
                                strategy="EVENT_DRIVEN",
                            )
                            risk.record_trade(record)

                # 更新状态
                status = risk.get_status()
                bot_status["positions"] = status["positions_count"]
                bot_status["daily_pnl"] = status["daily_pnl"]
                bot_status["total_pnl"] = status["total_pnl"]
                bot_status["trade_count"] = status["trade_count"]
                bot_status["circuit_breaker"] = status["circuit_breaker"]

                # V3: 保存到 SQLite
                if data_store:
                    try:
                        data_store.save_market_snapshot(opportunities)
                    except Exception:
                        pass

                # 定期保存状态
                if scan_count % 10 == 0:
                    try:
                        with open(state_file, "w") as f:
                            json.dump({
                                "total_pnl": risk.total_pnl,
                                "trade_count": risk.trade_count,
                            }, f)
                    except Exception:
                        pass

                logger.info(
                    f"扫描#{scan_count} | 持仓{status['positions_count']}/{config.MAX_POSITIONS} | "
                    f"日PnL ${status['daily_pnl']:+.2f} | 累计 ${status['total_pnl']:+.2f}"
                )

                # 等待下一轮
                time.sleep(config.SCAN_INTERVAL)

            except Exception as e:
                logger.error(f"主循环异常: {e}", exc_info=True)
                bot_status["status"] = "error"
                bot_status["last_error"] = str(e)
                time.sleep(30)

    except Exception as e:
        logger.error(f"Bot 初始化失败: {e}", exc_info=True)
        bot_status["status"] = "error"
        bot_status["last_error"] = str(e)


def setup_logging(level: str = "INFO"):
    import logging
    import sys
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


def main():
    """主入口 - 启动健康检查服务器 + 交易机器人"""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))

    port = int(os.getenv("PORT", "8000"))
    mode = "模拟" if os.getenv("DRY_RUN", "true").lower() == "true" else "实盘"
    capital = os.getenv("INITIAL_CAPITAL", "100")

    print()
    print("=" * 55)
    print("  Polymarket V3 量化交易系统 - Zeabur部署版")
    print("=" * 55)
    print(f"  模式:     {mode}")
    print(f"  初始资金: ${capital}")
    print(f"  健康检查: http://0.0.0.0:{port}/health")
    print(f"  仪表盘:  http://0.0.0.0:{port}/dashboard")
    print("=" * 55)
    print()

    # 在后台线程启动交易机器人
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # 在前台启动健康检查服务器
    start_health_server(port)


if __name__ == "__main__":
    main()
