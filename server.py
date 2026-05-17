"""
Polymarket 量化交易系统 V3 - 完整 REST API 服务器 + 专业前端仪表盘
提供前端仪表盘所需的全部数据端点
"""
import json
import os
import threading
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

# ============================================================
# 全局共享状态 - bot主线程写入，API线程读取
# ============================================================
bot_state = {
    "version": "3.5",
    "status": "starting",
    "scan_count": 0,
    "positions_count": 0,
    "daily_pnl": 0.0,
    "total_pnl": 0.0,
    "trade_count": 0,
    "circuit_breaker": False,
    "circuit_breaker_reason": "",
    "uptime_seconds": 0,
    "last_scan_time": "",
    "start_time": "",
    "last_error": "",
    "mode": "dry_run",
    "capital": 100.0,
    "strategies": {
        "arbitrage": "disabled",
        "mean_reversion": "disabled",
        "event_driven": "disabled",
        "zero_fee": "disabled",
        "multi_market_arb": "disabled",
    },
    "v3_modules": {
        "smart_money": "disabled",
        "orderbook_analyzer": "disabled",
        "kelly_sizing": "disabled",
        "data_store": "disabled",
        "websocket": "disabled",
        "backtester": "disabled",
        "probability_calibration": "disabled",
        "dynamic_stop_loss": "disabled",
        "portfolio_risk": "disabled",
    },
    "positions": [],
    "recent_trades": [],
    "smart_money_signals": [],
    "orderbook_signals": [],
    "pnl_history": [],
    "config": {},
    "data_store_stats": {},
    "strategy_performance": {},
}

# 全局模块引用（由 run_bot 设置）
_risk_manager = None
_smart_money = None
_orderbook = None
_data_store = None
_executor = None
_scanner = None
_config = None
_kelly = None
_orderbook_engine = None
_calibration = None
_dynamic_stop = None
_portfolio_risk = None
_backtester_v3 = None
_ws_client = None


# ============================================================
# 专业前端仪表盘 HTML
# ============================================================
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polymarket V3 Quant Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg-primary:#0a0e17;--bg-secondary:#111827;--bg-card:#1a1f2e;--bg-card-hover:#222840;
  --border:#2a3040;--text-primary:#e2e8f0;--text-secondary:#94a3b8;--text-muted:#64748b;
  --accent:#3b82f6;--accent-hover:#2563eb;--green:#10b981;--green-bg:rgba(16,185,129,.1);
  --red:#ef4444;--red-bg:rgba(239,68,68,.1);--yellow:#f59e0b;--yellow-bg:rgba(245,158,11,.1);
  --purple:#8b5cf6;--cyan:#06b6d4;
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg-primary);color:var(--text-primary);min-height:100vh}
.header{background:linear-gradient(135deg,#0f172a 0%,#1e1b4b 50%,#0f172a 100%);border-bottom:1px solid var(--border);padding:16px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.header-left{display:flex;align-items:center;gap:16px}
.logo{font-size:22px;font-weight:800;background:linear-gradient(135deg,#3b82f6,#8b5cf6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.version{font-size:11px;background:rgba(139,92,246,.2);color:var(--purple);padding:2px 8px;border-radius:10px;font-weight:600}
.status-badge{display:flex;align-items:center;gap:6px;font-size:13px;padding:4px 12px;border-radius:20px;font-weight:600}
.status-running{background:var(--green-bg);color:var(--green)}
.status-error{background:var(--red-bg);color:var(--red)}
.status-starting{background:var(--yellow-bg);color:var(--yellow)}
.status-dot{width:8px;height:8px;border-radius:50%;animation:pulse 2s infinite}
.status-running .status-dot{background:var(--green)}
.status-error .status-dot{background:var(--red)}
.status-starting .status-dot{background:var(--yellow)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.header-right{display:flex;align-items:center;gap:12px;font-size:13px;color:var(--text-secondary)}
.mode-badge{padding:4px 12px;border-radius:6px;font-weight:700;font-size:12px}
.mode-dry{background:var(--yellow-bg);color:var(--yellow)}
.mode-live{background:var(--green-bg);color:var(--green)}
.container{max-width:1440px;margin:0 auto;padding:20px}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px}
.kpi-card{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;padding:20px;transition:all .2s}
.kpi-card:hover{background:var(--bg-card-hover);border-color:var(--accent);transform:translateY(-2px)}
.kpi-label{font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
.kpi-value{font-size:28px;font-weight:800;line-height:1}
.kpi-sub{font-size:12px;color:var(--text-secondary);margin-top:6px}
.kpi-positive{color:var(--green)}
.kpi-negative{color:var(--red)}
.section{margin-bottom:24px}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.section-title{font-size:16px;font-weight:700;display:flex;align-items:center;gap:8px}
.section-title .icon{font-size:18px}
.section-count{font-size:12px;background:rgba(59,130,246,.15);color:var(--accent);padding:2px 8px;border-radius:10px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.grid-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px}
@media(max-width:1024px){.grid-2,.grid-3{grid-template-columns:1fr}}
.card{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.card-header{padding:14px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.card-title{font-size:14px;font-weight:600}
.card-body{padding:16px 18px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;padding:10px 12px;border-bottom:1px solid var(--border);font-weight:600}
td{padding:10px 12px;font-size:13px;border-bottom:1px solid rgba(42,48,64,.5)}
tr:hover td{background:rgba(59,130,246,.03)}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.tag-green{background:var(--green-bg);color:var(--green)}
.tag-red{background:var(--red-bg);color:var(--red)}
.tag-yellow{background:var(--yellow-bg);color:var(--yellow)}
.tag-blue{background:rgba(59,130,246,.15);color:var(--accent)}
.tag-purple{background:rgba(139,92,246,.15);color:var(--purple)}
.tag-cyan{background:rgba(6,182,212,.15);color:var(--cyan)}
.module-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}
.module-item{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:var(--bg-secondary);border-radius:8px;border:1px solid var(--border)}
.module-name{font-size:13px;font-weight:500}
.module-status{font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px}
.module-enabled{background:var(--green-bg);color:var(--green)}
.module-disabled{background:rgba(100,116,139,.1);color:var(--text-muted)}
.module-error{background:var(--red-bg);color:var(--red)}
.pnl-chart{height:200px;position:relative;background:var(--bg-secondary);border-radius:8px;overflow:hidden;margin-top:8px}
.chart-canvas{width:100%;height:100%}
.signal-item{padding:12px;border-bottom:1px solid rgba(42,48,64,.5);display:flex;align-items:center;gap:12px}
.signal-item:last-child{border-bottom:none}
.signal-icon{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}
.signal-icon-bull{background:var(--green-bg)}
.signal-icon-bear{background:var(--red-bg)}
.signal-icon-neutral{background:var(--yellow-bg)}
.signal-info{flex:1;min-width:0}
.signal-title{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.signal-detail{font-size:12px;color:var(--text-secondary);margin-top:2px}
.signal-strength{width:60px;text-align:right;font-weight:700;font-size:13px}
.btn{padding:8px 16px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s}
.btn-danger{background:var(--red-bg);color:var(--red);border:1px solid rgba(239,68,68,.3)}
.btn-danger:hover{background:rgba(239,68,68,.2)}
.btn-primary{background:var(--accent);color:white}
.btn-primary:hover{background:var(--accent-hover)}
.circuit-banner{background:var(--red-bg);border:1px solid rgba(239,68,68,.3);border-radius:12px;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}
.circuit-text{color:var(--red);font-weight:600;font-size:14px}
.empty-state{text-align:center;padding:40px;color:var(--text-muted);font-size:14px}
.refresh-indicator{font-size:11px;color:var(--text-muted);display:flex;align-items:center;gap:4px}
.spinning{animation:spin 1s linear infinite}
@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}
.config-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 24px}
.config-item{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(42,48,64,.3)}
.config-key{color:var(--text-secondary);font-size:13px}
.config-val{font-size:13px;font-weight:600}
.trade-side-yes{color:var(--green)}
.trade-side-no{color:var(--red)}
.scrollable{max-height:400px;overflow-y:auto}
.scrollable::-webkit-scrollbar{width:4px}
.scrollable::-webkit-scrollbar-track{background:var(--bg-secondary)}
.scrollable::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
</style>
</head>
<body>
<!-- Header -->
<div class="header">
  <div class="header-left">
    <div class="logo">Polymarket V3</div>
    <span class="version" id="version">v3.2</span>
    <div id="statusBadge" class="status-badge status-starting">
      <span class="status-dot"></span>
      <span id="statusText">Starting</span>
    </div>
  </div>
  <div class="header-right">
    <span id="modeBadge" class="mode-badge mode-dry">DRY RUN</span>
    <span>Uptime: <strong id="uptime">0:00:00</strong></span>
    <span>Scan: <strong id="scanCount">0</strong></span>
    <div class="refresh-indicator">
      <span id="refreshIcon" class="spinning">&#9696;</span>
      <span>Auto 5s</span>
    </div>
  </div>
</div>

<div class="container">
  <!-- Circuit Breaker Banner -->
  <div id="circuitBanner" class="circuit-banner" style="display:none">
    <div class="circuit-text">&#9888;&#65039; Circuit Breaker Triggered: <span id="circuitReason"></span></div>
    <button class="btn btn-danger" onclick="resetCircuitBreaker()">Reset Circuit Breaker</button>
  </div>

  <!-- KPI Cards -->
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-label">Capital</div>
      <div class="kpi-value" id="kpiCapital">$100.00</div>
      <div class="kpi-sub">Initial Fund</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Daily PnL</div>
      <div class="kpi-value" id="kpiDailyPnl">$0.00</div>
      <div class="kpi-sub" id="kpiDailyPnlSub">-</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Total PnL</div>
      <div class="kpi-value" id="kpiTotalPnl">$0.00</div>
      <div class="kpi-sub" id="kpiTotalPnlSub">Cumulative</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Trades</div>
      <div class="kpi-value" id="kpiTrades">0</div>
      <div class="kpi-sub" id="kpiTradesSub">Total executed</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Positions</div>
      <div class="kpi-value" id="kpiPositions">0</div>
      <div class="kpi-sub" id="kpiPositionsSub">Open now</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Last Scan</div>
      <div class="kpi-value" id="kpiLastScan" style="font-size:16px">-</div>
      <div class="kpi-sub" id="kpiLastScanAgo">-</div>
    </div>
  </div>

  <!-- Strategy & Module Status -->
  <div class="grid-2" style="margin-bottom:24px">
    <div class="card">
      <div class="card-header"><span class="card-title">&#9878; Strategy Status</span></div>
      <div class="card-body">
        <div class="module-grid" id="strategiesGrid"></div>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><span class="card-title">&#9881; V3 Modules</span></div>
      <div class="card-body">
        <div class="module-grid" id="modulesGrid"></div>
      </div>
    </div>
  </div>

  <!-- PnL Chart -->
  <div class="card" style="margin-bottom:24px">
    <div class="card-header">
      <span class="card-title">&#128200; PnL History</span>
      <span class="section-count" id="pnlPoints">0 points</span>
    </div>
    <div class="card-body">
      <div class="pnl-chart"><canvas id="pnlCanvas" class="chart-canvas"></canvas></div>
    </div>
  </div>

  <!-- Positions & Trades -->
  <div class="grid-2" style="margin-bottom:24px">
    <div class="card">
      <div class="card-header">
        <span class="card-title">&#128176; Open Positions</span>
        <span class="section-count" id="posCount">0</span>
      </div>
      <div class="card-body scrollable" id="positionsBody">
        <div class="empty-state">No open positions</div>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <span class="card-title">&#128203; Recent Trades</span>
        <span class="section-count" id="tradeCount">0</span>
      </div>
      <div class="card-body scrollable" id="tradesBody">
        <div class="empty-state">No recent trades</div>
      </div>
    </div>
  </div>

  <!-- Signals -->
  <div class="grid-2" style="margin-bottom:24px">
    <div class="card">
      <div class="card-header">
        <span class="card-title">&#129504; Smart Money Signals</span>
        <span class="section-count" id="smCount">0</span>
      </div>
      <div class="card-body scrollable" id="smartMoneyBody">
        <div class="empty-state">No smart money signals</div>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <span class="card-title">&#128225; Orderbook Signals</span>
        <span class="section-count" id="obCount">0</span>
      </div>
      <div class="card-body scrollable" id="orderbookBody">
        <div class="empty-state">No orderbook signals</div>
      </div>
    </div>
  </div>

  <!-- Strategy Performance & Config -->
  <div class="grid-2" style="margin-bottom:24px">
    <div class="card">
      <div class="card-header"><span class="card-title">&#127942; Strategy Performance</span></div>
      <div class="card-body" id="perfBody">
        <div class="empty-state">No performance data yet</div>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><span class="card-title">&#9881; Configuration</span></div>
      <div class="card-body" id="configBody">
        <div class="empty-state">Loading...</div>
      </div>
    </div>
  </div>

  <!-- Data Store Stats -->
  <div class="card" style="margin-bottom:24px">
    <div class="card-header"><span class="card-title">&#128451; Data Store Stats</span></div>
    <div class="card-body" id="dataStoreBody">
      <div class="empty-state">Loading...</div>
    </div>
  </div>
</div>

<script>
// ============ State ============
let lastData = {};
const REFRESH_MS = 5000;

// ============ API Fetch ============
async function api(path) {
  try {
    const r = await fetch(path);
    return await r.json();
  } catch(e) {
    console.error('API error:', path, e);
    return null;
  }
}

// ============ Formatters ============
function fmtMoney(v, showSign=true) {
  const s = showSign && v > 0 ? '+' : '';
  return s + '$' + Math.abs(v).toFixed(2);
}
function fmtPct(v) { return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'; }
function fmtUptime(s) {
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = s%60;
  return h + ':' + String(m).padStart(2,'0') + ':' + String(sec).padStart(2,'0');
}
function pnlClass(v) { return v >= 0 ? 'kpi-positive' : 'kpi-negative'; }

// ============ Render Functions ============
function renderKPIs(d) {
  document.getElementById('kpiCapital').textContent = '$' + (d.capital||100).toFixed(2);
  const dp = d.daily_pnl||0;
  const tp = d.total_pnl||0;
  const dpEl = document.getElementById('kpiDailyPnl');
  dpEl.textContent = fmtMoney(dp);
  dpEl.className = 'kpi-value ' + pnlClass(dp);
  document.getElementById('kpiDailyPnlSub').textContent = fmtPct(dp/(d.capital||100)*100) + ' of capital';
  const tpEl = document.getElementById('kpiTotalPnl');
  tpEl.textContent = fmtMoney(tp);
  tpEl.className = 'kpi-value ' + pnlClass(tp);
  document.getElementById('kpiTotalPnlSub').textContent = fmtPct(tp/(d.capital||100)*100) + ' return';
  document.getElementById('kpiTrades').textContent = d.trade_count||0;
  document.getElementById('kpiPositions').textContent = d.positions_count||0;
  document.getElementById('kpiLastScan').textContent = d.last_scan||'-';
  document.getElementById('scanCount').textContent = d.scan_count||0;
  document.getElementById('uptime').textContent = fmtUptime(d.uptime||0);
  document.getElementById('version').textContent = 'v' + (d.version||'3.2');
  // Status
  const badge = document.getElementById('statusBadge');
  badge.className = 'status-badge status-' + (d.status||'starting');
  document.getElementById('statusText').textContent = (d.status||'starting').toUpperCase();
  // Mode
  const modeBadge = document.getElementById('modeBadge');
  if ((d.mode||'dry_run') === 'LIVE') {
    modeBadge.className = 'mode-badge mode-live';
    modeBadge.textContent = 'LIVE';
  } else {
    modeBadge.className = 'mode-badge mode-dry';
    modeBadge.textContent = 'DRY RUN';
  }
  // Circuit breaker
  const banner = document.getElementById('circuitBanner');
  if (d.circuit_breaker) {
    banner.style.display = 'flex';
    document.getElementById('circuitReason').textContent = d.circuit_breaker_reason||'Unknown';
  } else {
    banner.style.display = 'none';
  }
}

function renderModules(modules) {
  const nameMap = {
    arbitrage:'Arbitrage', mean_reversion:'Mean Reversion', event_driven:'Event Driven',
    zero_fee:'Zero Fee', multi_market_arb:'Multi-Market Arb',
    smart_money:'Smart Money', orderbook_analyzer:'OrderBook', kelly_sizing:'Kelly Sizing',
    data_store:'Data Store', websocket:'WebSocket', backtester:'Backtester',
    probability_calibration:'Calibration', dynamic_stop_loss:'Dynamic SL', portfolio_risk:'Portfolio Risk',
  };
  function renderGrid(data, elId) {
    const el = document.getElementById(elId);
    el.innerHTML = Object.entries(data).map(([k,v]) => {
      const cls = v==='enabled'?'module-enabled': v==='error'?'module-error':'module-disabled';
      return '<div class="module-item"><span class="module-name">'+(nameMap[k]||k)+'</span><span class="module-status '+cls+'">'+v+'</span></div>';
    }).join('');
  }
  renderGrid(modules.strategies||{}, 'strategiesGrid');
  renderGrid(modules.v3_modules||{}, 'modulesGrid');
}

function renderPositions(data) {
  document.getElementById('posCount').textContent = data.count||0;
  const el = document.getElementById('positionsBody');
  const positions = data.positions||[];
  if (!positions.length) { el.innerHTML = '<div class="empty-state">No open positions</div>'; return; }
  let html = '<table><tr><th>Market</th><th>Side</th><th>Entry</th><th>Current</th><th>Size</th><th>PnL</th><th>PnL%</th><th>Hold</th></tr>';
  positions.forEach(p => {
    const sideCls = p.side==='YES'?'trade-side-yes':'trade-side-no';
    const pnlCls = p.pnl>=0?'tag-green':'tag-red';
    const pnlPCls = p.pnl_percent>=0?'tag-green':'tag-red';
    html += '<tr><td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+escHtml(p.question)+'">'+escHtml(p.question)+'</td>';
    html += '<td><span class="tag '+sideCls+'">'+p.side+'</span></td>';
    html += '<td>'+p.entry_price.toFixed(4)+'</td>';
    html += '<td>'+p.current_price.toFixed(4)+'</td>';
    html += '<td>'+p.amount.toFixed(1)+'</td>';
    html += '<td><span class="tag '+pnlCls+'">'+fmtMoney(p.pnl)+'</span></td>';
    html += '<td><span class="tag '+pnlPCls+'">'+fmtPct(p.pnl_percent)+'</span></td>';
    html += '<td>'+p.hold_time_hours.toFixed(1)+'h</td></tr>';
  });
  html += '</table>';
  el.innerHTML = html;
}

function renderTrades(data) {
  document.getElementById('tradeCount').textContent = data.count||0;
  const el = document.getElementById('tradesBody');
  const trades = data.trades||[];
  if (!trades.length) { el.innerHTML = '<div class="empty-state">No recent trades</div>'; return; }
  let html = '<table><tr><th>Time</th><th>Market</th><th>Side</th><th>Action</th><th>Price</th><th>Amount</th><th>PnL</th><th>Strategy</th></tr>';
  trades.slice().reverse().forEach(t => {
    const sideCls = t.side.includes('YES')?'trade-side-yes':'trade-side-no';
    const pnlCls = t.pnl>=0?'tag-green':'tag-red';
    html += '<tr><td style="font-size:11px;white-space:nowrap">'+escHtml(t.time||'-')+'</td>';
    html += '<td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+escHtml(t.question)+'">'+escHtml(t.question)+'</td>';
    html += '<td><span class="tag '+sideCls+'">'+escHtml(t.side)+'</span></td>';
    html += '<td><span class="tag tag-blue">'+escHtml(t.action)+'</span></td>';
    html += '<td>'+t.price.toFixed(4)+'</td>';
    html += '<td>$'+t.amount.toFixed(2)+'</td>';
    html += '<td><span class="tag '+pnlCls+'">'+fmtMoney(t.pnl)+'</span></td>';
    html += '<td><span class="tag tag-purple">'+escHtml(t.strategy)+'</span></td></tr>';
  });
  html += '</table>';
  el.innerHTML = html;
}

function renderSmartMoney(data) {
  document.getElementById('smCount').textContent = data.count||0;
  const el = document.getElementById('smartMoneyBody');
  const signals = data.signals||[];
  if (!signals.length) { el.innerHTML = '<div class="empty-state">No smart money signals (Wallets: '+(data.wallets_tracked||0)+')</div>'; return; }
  let html = '';
  signals.forEach(s => {
    const isBull = s.direction==='YES';
    const iconCls = isBull?'signal-icon-bull':'signal-icon-bear';
    const arrow = isBull?'&#9650;':'&#9660;';
    const confCls = s.confidence==='HIGH'?'tag-green':s.confidence==='MEDIUM'?'tag-yellow':'tag-red';
    html += '<div class="signal-item">';
    html += '<div class="signal-icon '+iconCls+'">'+arrow+'</div>';
    html += '<div class="signal-info">';
    html += '<div class="signal-title">'+escHtml(s.question||s.market_id)+' <span class="tag '+confCls+'">'+s.confidence+'</span></div>';
    html += '<div class="signal-detail">'+escHtml(s.reason)+' | '+s.signal_type+' | Kelly Edge: '+(s.kelly_edge||0).toFixed(4)+'</div>';
    html += '</div>';
    html += '<div class="signal-strength" style="color:'+(isBull?'var(--green)':'var(--red)')+'">'+(s.strength*100).toFixed(0)+'%</div>';
    html += '</div>';
  });
  el.innerHTML = html;
}

function renderOrderbook(data) {
  document.getElementById('obCount').textContent = data.count||0;
  const el = document.getElementById('orderbookBody');
  const signals = data.signals||[];
  if (!signals.length) { el.innerHTML = '<div class="empty-state">No orderbook signals (Tokens: '+(data.tracked_tokens||0)+')</div>'; return; }
  let html = '';
  signals.forEach(s => {
    const isBull = s.direction==='BULLISH';
    const iconCls = isBull?'signal-icon-bull':s.direction==='BEARISH'?'signal-icon-bear':'signal-icon-neutral';
    const arrow = isBull?'&#9650;':s.direction==='BEARISH'?'&#9660;':'&#9644;';
    html += '<div class="signal-item">';
    html += '<div class="signal-icon '+iconCls+'">'+arrow+'</div>';
    html += '<div class="signal-info">';
    html += '<div class="signal-title">'+escHtml(s.question||s.market_id||s.signal_type)+'</div>';
    html += '<div class="signal-detail">'+escHtml(s.reason)+' | '+s.signal_type+'</div>';
    html += '</div>';
    html += '<div class="signal-strength">'+(s.strength*100).toFixed(0)+'%</div>';
    html += '</div>';
  });
  el.innerHTML = html;
}

function renderPerformance(data) {
  const el = document.getElementById('perfBody');
  const entries = Object.entries(data);
  if (!entries.length) { el.innerHTML = '<div class="empty-state">No performance data yet</div>'; return; }
  let html = '<table><tr><th>Strategy</th><th>Trades</th><th>Win Rate</th><th>Total PnL</th><th>Avg PnL</th></tr>';
  entries.forEach(([name, s]) => {
    const wr = (s.win_rate*100).toFixed(1);
    const wrCls = s.win_rate>=0.5?'tag-green':'tag-red';
    const pnlCls = s.total_pnl>=0?'tag-green':'tag-red';
    html += '<tr><td><span class="tag tag-purple">'+escHtml(name)+'</span></td>';
    html += '<td>'+s.total_trades+'</td>';
    html += '<td><span class="tag '+wrCls+'">'+wr+'%</span></td>';
    html += '<td><span class="tag '+pnlCls+'">'+fmtMoney(s.total_pnl||0)+'</span></td>';
    html += '<td>'+fmtMoney(s.avg_pnl||0)+'</td></tr>';
  });
  html += '</table>';
  el.innerHTML = html;
}

function renderConfig(data) {
  const el = document.getElementById('configBody');
  const items = Object.entries(data);
  if (!items.length) { el.innerHTML = '<div class="empty-state">No config loaded</div>'; return; }
  let html = '<div class="config-grid">';
  items.forEach(([k,v]) => {
    let valStr = String(v);
    if (typeof v === 'boolean') valStr = v ? '<span style="color:var(--green)">ON</span>' : '<span style="color:var(--text-muted)">OFF</span>';
    html += '<div class="config-item"><span class="config-key">'+escHtml(k)+'</span><span class="config-val">'+valStr+'</span></div>';
  });
  html += '</div>';
  el.innerHTML = html;
}

function renderDataStore(data) {
  const el = document.getElementById('dataStoreBody');
  if (data.error) { el.innerHTML = '<div class="empty-state">Error: '+escHtml(data.error)+'</div>'; return; }
  const items = Object.entries(data);
  if (!items.length) { el.innerHTML = '<div class="empty-state">No data store stats</div>'; return; }
  let html = '<div class="config-grid">';
  items.forEach(([k,v]) => {
    html += '<div class="config-item"><span class="config-key">'+escHtml(k.replace(/_/g,' '))+'</span><span class="config-val">'+v+'</span></div>';
  });
  html += '</div>';
  el.innerHTML = html;
}

// ============ PnL Chart ============
function drawPnLChart(history) {
  const canvas = document.getElementById('pnlCanvas');
  const ctx = canvas.getContext('2d');
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * 2;
  canvas.height = rect.height * 2;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  ctx.scale(2, 2);
  const W = rect.width, H = rect.height;
  ctx.clearRect(0, 0, W, H);
  document.getElementById('pnlPoints').textContent = (history.length||0) + ' points';
  if (!history || history.length < 2) {
    ctx.fillStyle = '#64748b';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Waiting for data...', W/2, H/2);
    return;
  }
  const pad = {top:20, right:20, bottom:30, left:60};
  const cw = W - pad.left - pad.right;
  const ch = H - pad.top - pad.bottom;
  const values = history.map(h => h.total_pnl);
  const minV = Math.min(0, ...values);
  const maxV = Math.max(0, ...values);
  const range = maxV - minV || 1;
  // Grid lines
  ctx.strokeStyle = 'rgba(42,48,64,.5)';
  ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + ch * (1 - i/4);
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + cw, y); ctx.stroke();
    ctx.fillStyle = '#64748b'; ctx.font = '10px sans-serif'; ctx.textAlign = 'right';
    const val = minV + range * i / 4;
    ctx.fillText('$' + val.toFixed(2), pad.left - 6, y + 3);
  }
  // Zero line
  const zeroY = pad.top + ch * (1 - (0 - minV) / range);
  ctx.strokeStyle = 'rgba(148,163,184,.3)'; ctx.lineWidth = 1;
  ctx.setLineDash([4,4]); ctx.beginPath(); ctx.moveTo(pad.left, zeroY); ctx.lineTo(pad.left+cw, zeroY); ctx.stroke(); ctx.setLineDash([]);
  // Line
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = pad.left + (i / (values.length - 1)) * cw;
    const y = pad.top + ch * (1 - (v - minV) / range);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 2; ctx.stroke();
  // Fill
  const lastX = pad.left + cw;
  const lastY = pad.top + ch * (1 - (values[values.length-1] - minV) / range);
  ctx.lineTo(lastX, zeroY); ctx.lineTo(pad.left, zeroY); ctx.closePath();
  const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + ch);
  grad.addColorStop(0, 'rgba(59,130,246,.25)');
  grad.addColorStop(1, 'rgba(59,130,246,.01)');
  ctx.fillStyle = grad; ctx.fill();
  // Last point dot
  ctx.beginPath(); ctx.arc(lastX, lastY, 4, 0, Math.PI*2);
  ctx.fillStyle = '#3b82f6'; ctx.fill();
  ctx.strokeStyle = '#0a0e17'; ctx.lineWidth = 2; ctx.stroke();
  // X axis labels
  const step = Math.max(1, Math.floor(values.length / 6));
  ctx.fillStyle = '#64748b'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  for (let i = 0; i < values.length; i += step) {
    const x = pad.left + (i / (values.length-1)) * cw;
    ctx.fillText(history[i].time || '', x, H - 6);
  }
}

// ============ Actions ============
async function resetCircuitBreaker() {
  if (!confirm('Reset circuit breaker?')) return;
  const r = await api('/api/circuit-breaker/reset');
  if (r && r.ok) { alert('Circuit breaker reset!'); refreshAll(); }
  else alert('Failed: ' + JSON.stringify(r));
}

// ============ Utility ============
function escHtml(s) { const d = document.createElement('div'); d.textContent = s||''; return d.innerHTML; }

// ============ Main Refresh ============
async function refreshAll() {
  const [dashboard, positions, trades, smartMoney, orderbook, pnlHist, modules, perf, config, dsStats] = await Promise.all([
    api('/api/dashboard'),
    api('/api/positions'),
    api('/api/trades'),
    api('/api/signals/smart-money'),
    api('/api/signals/orderbook'),
    api('/api/pnl/history'),
    api('/api/modules'),
    api('/api/strategy-performance'),
    api('/api/config'),
    api('/api/data-store/stats'),
  ]);
  if (dashboard) renderKPIs(dashboard);
  if (positions) renderPositions(positions);
  if (trades) renderTrades(trades);
  if (smartMoney) renderSmartMoney(smartMoney);
  if (orderbook) renderOrderbook(orderbook);
  if (pnlHist) drawPnLChart(pnlHist.history || []);
  if (modules) renderModules(modules);
  if (perf) renderPerformance(perf);
  if (config) renderConfig(config);
  if (dsStats) renderDataStore(dsStats);
}

// Start
refreshAll();
setInterval(refreshAll, REFRESH_MS);
window.addEventListener('resize', () => {
  if (lastData.pnlHistory) drawPnLChart(lastData.pnlHistory);
});
</script>
</body>
</html>"""


# ============================================================
# REST API 处理器
# ============================================================
class APIHandler(BaseHTTPRequestHandler):
    """完整 REST API + 前端静态文件"""

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        routes = {
            "/": self._dashboard_page,
            "/dashboard": self._dashboard_page,
            "/health": self._health,
            "/status": self._status,
            "/ping": self._ping,
            "/api/status": self._status,
            "/api/positions": self._positions,
            "/api/trades": self._trades,
            "/api/signals/smart-money": self._smart_money_signals,
            "/api/signals/orderbook": self._orderbook_signals,
            "/api/pnl": self._pnl,
            "/api/pnl/history": self._pnl_history,
            "/api/config": self._config_endpoint,
            "/api/modules": self._modules,
            "/api/data-store/stats": self._data_store_stats,
            "/api/strategy-performance": self._strategy_performance,
            "/api/opportunities": self._opportunities,
            "/api/dashboard": self._dashboard_summary,
            "/api/calibration": self._calibration,
            "/api/backtest": self._backtest,
            "/api/v3-modules-stats": self._v3_modules_stats,
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._send_json(404, {"error": "Not Found", "path": path})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/circuit-breaker/reset":
            self._reset_circuit_breaker()
        else:
            self._send_json(404, {"error": "Not Found"})

    # ---------- API 端点 ----------

    def _dashboard_page(self):
        """提供专业前端仪表盘页面"""
        body = DASHBOARD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _health(self):
        self._send_json(200, {
            "status": "ok" if bot_state["status"] == "running" else "degraded",
            "uptime": bot_state["uptime_seconds"],
            "mode": bot_state["mode"],
        })

    def _status(self):
        self._send_json(200, bot_state)

    def _ping(self):
        self._send_text(200, "pong")

    def _positions(self):
        positions = bot_state.get("positions", [])
        # 实时从 risk_manager 获取最新数据
        if _risk_manager:
            positions = []
            for p in _risk_manager.positions:
                positions.append({
                    "market_id": p.market_id,
                    "question": p.question,
                    "token_id": p.token_id,
                    "side": p.side,
                    "entry_price": round(p.entry_price, 4),
                    "current_price": round(p.current_price, 4),
                    "amount": round(p.amount, 2),
                    "current_value": round(p.current_value, 2),
                    "pnl": round(p.pnl, 4),
                    "pnl_percent": round(p.pnl_percent, 2),
                    "stop_loss": round(p.stop_loss, 4),
                    "take_profit": round(p.take_profit, 4),
                    "entry_time": p.entry_time,
                    "hold_time_hours": round((time.time() - p.entry_time) / 3600, 1),
                })
            bot_state["positions"] = positions
        self._send_json(200, {
            "count": len(positions),
            "total_value": round(sum(p.get("current_value", 0) for p in positions), 2),
            "positions": positions,
        })

    def _trades(self):
        trades = bot_state.get("recent_trades", [])
        # 从 risk_manager 获取最新交易
        if _risk_manager:
            trades = []
            for t in _risk_manager.trade_history[-50:]:
                trades.append({
                    "timestamp": t.timestamp,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t.timestamp)),
                    "market_id": t.market_id,
                    "question": t.question,
                    "side": t.side,
                    "action": t.action,
                    "price": round(t.price, 4),
                    "amount": round(t.amount, 2),
                    "pnl": round(t.pnl, 4),
                    "strategy": t.strategy,
                })
            bot_state["recent_trades"] = trades
        self._send_json(200, {
            "count": len(trades),
            "trades": trades,
        })

    def _smart_money_signals(self):
        signals = bot_state.get("smart_money_signals", [])
        if _smart_money:
            signals = []
            for s in _smart_money.active_signals[:20]:
                signals.append({
                    "signal_type": s.signal_type,
                    "direction": s.direction,
                    "strength": round(s.strength, 3),
                    "market_id": s.market_id,
                    "question": s.question,
                    "confidence": s.confidence,
                    "reason": s.reason,
                    "source_wallets": s.source_wallets,
                    "kelly_edge": round(s.kelly_edge, 4),
                    "timestamp": s.timestamp,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.timestamp)),
                    "is_expired": s.is_expired,
                })
            bot_state["smart_money_signals"] = signals
        self._send_json(200, {
            "count": len(signals),
            "wallets_tracked": len(_smart_money.known_wallets) if _smart_money else 0,
            "signals": signals,
        })

    def _orderbook_signals(self):
        signals = bot_state.get("orderbook_signals", [])
        if _orderbook:
            signals = []
            for s in _orderbook.signals[:20]:
                signals.append({
                    "signal_type": s.signal_type,
                    "direction": s.direction,
                    "strength": round(s.strength, 3),
                    "market_id": s.market_id,
                    "question": s.question,
                    "reason": s.reason,
                    "timestamp": s.timestamp,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.timestamp)),
                })
            bot_state["orderbook_signals"] = signals
        self._send_json(200, {
            "count": len(signals),
            "tracked_tokens": len(_orderbook.snapshots) if _orderbook else 0,
            "signals": signals,
        })

    def _pnl(self):
        self._send_json(200, {
            "daily_pnl": round(bot_state.get("daily_pnl", 0), 2),
            "total_pnl": round(bot_state.get("total_pnl", 0), 2),
            "trade_count": bot_state.get("trade_count", 0),
            "capital": bot_state.get("capital", 100),
        })

    def _pnl_history(self):
        history = bot_state.get("pnl_history", [])
        self._send_json(200, {
            "count": len(history),
            "history": history[-100:],
        })

    def _config_endpoint(self):
        if _config:
            bot_state["config"] = {
                "DRY_RUN": _config.DRY_RUN,
                "INITIAL_CAPITAL": _config.INITIAL_CAPITAL,
                "MAX_POSITIONS": _config.MAX_POSITIONS,
                "TRADE_SIZE_PERCENT": _config.TRADE_SIZE_PERCENT,
                "STOP_LOSS_PERCENT": _config.STOP_LOSS_PERCENT,
                "TAKE_PROFIT_PERCENT": _config.TAKE_PROFIT_PERCENT,
                "DAILY_LOSS_LIMIT": _config.DAILY_LOSS_LIMIT,
                "SCAN_INTERVAL": _config.SCAN_INTERVAL,
                "ENABLE_ARBITRAGE": _config.ENABLE_ARBITRAGE,
                "ENABLE_MEAN_REVERSION": _config.ENABLE_MEAN_REVERSION,
                "ENABLE_EVENT_DRIVEN": _config.ENABLE_EVENT_DRIVEN,
                "ENABLE_SMART_MONEY": getattr(_config, 'ENABLE_SMART_MONEY', False),
                "KELLY_FRACTION": getattr(_config, 'KELLY_FRACTION', 0),
                "CLOB_HOST": _config.CLOB_HOST,
            }
        self._send_json(200, bot_state.get("config", {}))

    def _modules(self):
        self._send_json(200, {
            "strategies": bot_state.get("strategies", {}),
            "v3_modules": bot_state.get("v3_modules", {}),
        })

    def _data_store_stats(self):
        stats = {}
        if _data_store:
            try:
                stats = _data_store.get_stats()
            except Exception:
                stats = {"error": "failed to read stats"}
        bot_state["data_store_stats"] = stats
        self._send_json(200, stats)

    def _strategy_performance(self):
        perf = {}
        if _data_store:
            try:
                perf = _data_store.get_strategy_performance()
            except Exception:
                perf = {}
        bot_state["strategy_performance"] = perf
        self._send_json(200, perf)

    def _opportunities(self):
        self._send_json(200, {
            "last_scan": bot_state.get("last_scan_time", ""),
            "scan_count": bot_state.get("scan_count", 0),
            "note": "Opportunities are generated in real-time by the bot loop",
        })

    def _calibration(self):
        """概率校准数据"""
        if _calibration:
            stats = _calibration.get_stats()
            metrics = _calibration.get_metrics()
            return self._send_json(200, {
                "stats": stats,
                "overall_metrics": {
                    "brier_score": metrics.brier_score,
                    "ece": metrics.ece,
                    "brier_skill_score": metrics.brier_skill_score,
                    "reliability": metrics.reliability,
                    "sample_size": metrics.sample_size,
                },
                "confidence_adjustment": _calibration.get_confidence_adjustment(),
            })
        self._send_json(200, {"stats": {}, "overall_metrics": {}, "confidence_adjustment": 1.0})

    def _backtest(self):
        """回测数据"""
        if _backtester_v3 and _data_store:
            try:
                results = _backtester_v3.compare_strategies(
                    capital=_config.INITIAL_CAPITAL if _config else 100,
                    days=30
                )
                return self._send_json(200, {"results": results})
            except Exception as e:
                return self._send_json(200, {"results": {}, "error": str(e)})
        self._send_json(200, {"results": {}})

    def _v3_modules_stats(self):
        """V3.5模块统计"""
        stats = {}
        if _dynamic_stop:
            stats["dynamic_stop_loss"] = _dynamic_stop.get_stats()
        if _portfolio_risk:
            stats["portfolio_risk"] = _portfolio_risk.get_stats()
        if _orderbook_engine:
            stats["orderbook_engine"] = _orderbook_engine.get_stats()
        if _ws_client:
            stats["websocket_client"] = _ws_client.get_status()
        if _calibration:
            stats["calibration"] = _calibration.get_stats()
        stats["kelly_fraction"] = getattr(_config, 'KELLY_FRACTION', 0.25) if _config else 0.25
        self._send_json(200, stats)

    def _dashboard_summary(self):
        """聚合所有仪表盘需要的数据到一个端点，减少前端请求次数"""
        self._send_json(200, {
            "status": bot_state["status"],
            "version": bot_state["version"],
            "mode": bot_state["mode"],
            "capital": bot_state["capital"],
            "uptime": bot_state["uptime_seconds"],
            "scan_count": bot_state["scan_count"],
            "last_scan": bot_state["last_scan_time"],
            "daily_pnl": round(bot_state.get("daily_pnl", 0), 2),
            "total_pnl": round(bot_state.get("total_pnl", 0), 2),
            "trade_count": bot_state.get("trade_count", 0),
            "circuit_breaker": bot_state.get("circuit_breaker", False),
            "circuit_breaker_reason": bot_state.get("circuit_breaker_reason", ""),
            "last_error": bot_state.get("last_error", ""),
            "positions_count": bot_state.get("positions_count", 0),
            "strategies": bot_state.get("strategies", {}),
            "v3_modules": bot_state.get("v3_modules", {}),
        })

    def _reset_circuit_breaker(self):
        if _risk_manager:
            _risk_manager.reset_circuit_breaker()
            bot_state["circuit_breaker"] = False
            bot_state["circuit_breaker_reason"] = ""
            self._send_json(200, {"ok": True, "message": "Circuit breaker reset"})
        else:
            self._send_json(400, {"error": "Risk manager not initialized"})

    # ---------- 工具方法 ----------

    def _send_json(self, code, data):
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # 静默日志


# ============================================================
# 服务器启动
# ============================================================
def start_api_server(port=8000):
    server = HTTPServer(("0.0.0.0", port), APIHandler)
    print(f"[API] REST API 服务器启动在端口 {port}")
    server.serve_forever()


# ============================================================
# Bot 主循环
# ============================================================
def run_bot():
    """在后台线程中运行交易机器人"""
    global _risk_manager, _smart_money, _orderbook, _data_store, _executor, _scanner, _config
    global _kelly, _orderbook_engine, _calibration, _dynamic_stop, _portfolio_risk, _backtester_v3, _ws_client

    import logging
    import sys
    sys.path.insert(0, os.path.dirname(__file__))

    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    logger = logging.getLogger("polymarket")

    try:
        from config import Config
        from market_scanner import MarketScanner
        from risk_manager import RiskManager, Position, TradeRecord
        from executor import OrderExecutor
        from notifier import Notifier

        _config = Config()

        # 更新全局状态
        bot_state["mode"] = "dry_run" if _config.DRY_RUN else "LIVE"
        bot_state["capital"] = _config.INITIAL_CAPITAL
        bot_state["strategies"]["arbitrage"] = "enabled" if _config.ENABLE_ARBITRAGE else "disabled"
        bot_state["strategies"]["mean_reversion"] = "enabled" if _config.ENABLE_MEAN_REVERSION else "disabled"
        bot_state["strategies"]["event_driven"] = "enabled" if _config.ENABLE_EVENT_DRIVEN else "disabled"
        bot_state["strategies"]["zero_fee"] = "enabled" if getattr(_config, 'ENABLE_ZERO_FEE', False) else "disabled"
        bot_state["strategies"]["multi_market_arb"] = "enabled" if getattr(_config, 'ENABLE_MULTI_MARKET_ARB', False) else "disabled"

        # 初始化核心模块
        _scanner = MarketScanner(_config)
        _risk_manager = RiskManager(_config)
        _executor = OrderExecutor(_config)
        _notifier = Notifier(_config)

        # V3 模块
        try:
            from data_store import DataStore
            _data_store = DataStore()
            bot_state["v3_modules"]["data_store"] = "enabled"
            logger.info("V3 DataStore 初始化成功")
        except Exception as e:
            logger.warning(f"V3 DataStore 初始化失败: {e}")
            _data_store = None
            bot_state["v3_modules"]["data_store"] = f"error"

        try:
            from smart_money_tracker import SmartMoneyTracker
            _smart_money = SmartMoneyTracker(_config)
            bot_state["v3_modules"]["smart_money"] = "enabled"
            logger.info("V3 SmartMoneyTracker 初始化成功")
        except Exception as e:
            logger.warning(f"V3 SmartMoneyTracker 初始化失败: {e}")
            _smart_money = None
            bot_state["v3_modules"]["smart_money"] = f"error"

        try:
            from orderbook_analyzer import OrderBookAnalyzer
            _orderbook = OrderBookAnalyzer(_config)
            bot_state["v3_modules"]["orderbook_analyzer"] = "enabled"
            logger.info("V3 OrderbookAnalyzer 初始化成功")
        except Exception as e:
            logger.warning(f"V3 OrderbookAnalyzer 初始化失败: {e}")
            _orderbook = None
            bot_state["v3_modules"]["orderbook_analyzer"] = f"error"

        # ===== V3.5 五大模块初始化 =====
        # 模块一: Kelly Criterion仓位管理
        try:
            from kelly_criterion import kellyBinary, combinedKelly, confidenceAdjustedKelly, calculate_position_size_kelly
            _kelly = {
                "kellyBinary": kellyBinary,
                "combinedKelly": combinedKelly,
                "confidenceAdjustedKelly": confidenceAdjustedKelly,
                "calculate_position_size_kelly": calculate_position_size_kelly,
            }
            kelly_frac = getattr(_config, 'KELLY_FRACTION', 0.25)
            bot_state["v3_modules"]["kelly_sizing"] = "enabled" if kelly_frac > 0 else "disabled"
            logger.info(f"V3.5 Kelly Criterion 初始化成功 (fraction={kelly_frac})")
        except Exception as e:
            logger.warning(f"V3.5 Kelly Criterion 初始化失败: {e}")
            _kelly = None
            bot_state["v3_modules"]["kelly_sizing"] = "error"

        # 模块二: WebSocket + OrderbookEngine
        try:
            from orderbook_engine import ResilientWebSocket, OrderbookEngine
            _orderbook_engine = OrderbookEngine(max_snapshots=100, stale_seconds=60)
            ws_enabled = getattr(_config, 'WS_ENABLED', False)
            if ws_enabled:
                _ws_client = ResilientWebSocket(_config)
                _ws_client.on("orderbook_update", lambda d: _orderbook_engine.update_book(
                    d.get("token_id", ""), d.get("bids", []), d.get("asks", [])))
                _ws_client.start()
            bot_state["v3_modules"]["websocket"] = "enabled" if ws_enabled else "disabled"
            logger.info(f"V3.5 OrderbookEngine 初始化成功 (WS={'ON' if ws_enabled else 'OFF'})")
        except Exception as e:
            logger.warning(f"V3.5 OrderbookEngine 初始化失败: {e}")
            _orderbook_engine = None
            _ws_client = None
            bot_state["v3_modules"]["websocket"] = "error"

        # 模块三: 事件驱动回测引擎
        try:
            from backtester import BacktestEngine
            _backtester_v3 = BacktestEngine(_config, _data_store)
            bot_state["v3_modules"]["backtester"] = "enabled" if _data_store else "disabled"
            logger.info("V3.5 BacktestEngine 初始化成功")
        except Exception as e:
            logger.warning(f"V3.5 BacktestEngine 初始化失败: {e}")
            _backtester_v3 = None
            bot_state["v3_modules"]["backtester"] = "error"

        # 模块四: 概率校准引擎
        try:
            from probability_calibration import ProbabilityCalibration
            _calibration = ProbabilityCalibration()
            bot_state["v3_modules"]["probability_calibration"] = "enabled"
            logger.info("V3.5 ProbabilityCalibration 初始化成功")
        except Exception as e:
            logger.warning(f"V3.5 ProbabilityCalibration 初始化失败: {e}")
            _calibration = None
            bot_state["v3_modules"]["probability_calibration"] = "error"

        # 模块五: 动态止损 + 组合风险管理
        try:
            from risk_manager_v3 import DynamicStopLoss, PortfolioRiskManager
            _dynamic_stop = DynamicStopLoss(_config)
            _portfolio_risk = PortfolioRiskManager(_config.INITIAL_CAPITAL)
            bot_state["v3_modules"]["dynamic_stop_loss"] = "enabled"
            bot_state["v3_modules"]["portfolio_risk"] = "enabled"
            logger.info("V3.5 DynamicStopLoss + PortfolioRiskManager 初始化成功")
        except Exception as e:
            logger.warning(f"V3.5 风险管理模块初始化失败: {e}")
            _dynamic_stop = None
            _portfolio_risk = None
            bot_state["v3_modules"]["dynamic_stop_loss"] = "error"
            bot_state["v3_modules"]["portfolio_risk"] = "error"

        # 加载状态
        state_file = os.path.join(os.path.dirname(__file__), "bot_state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    state = json.load(f)
                _risk_manager.total_pnl = state.get("total_pnl", 0.0)
                _risk_manager.trade_count = state.get("trade_count", 0)
            except Exception:
                pass

        # 初始化执行器
        if not _executor.initialize():
            logger.warning("执行器初始化失败，模拟模式运行")
            _config.DRY_RUN = True
            bot_state["mode"] = "dry_run"

        bot_state["status"] = "running"
        bot_state["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        start_time = time.time()
        scan_count = 0

        mode = "模拟" if _config.DRY_RUN else "实盘"
        logger.info(f"V3 交易系统启动 [{mode}] ${_config.INITIAL_CAPITAL}")
        try:
            _notifier.system_alert(f"V3 交易系统启动 [{mode}] ${_config.INITIAL_CAPITAL}")
        except Exception:
            pass

        # 主循环
        while True:
            try:
                scan_count += 1
                bot_state["scan_count"] = scan_count
                bot_state["uptime_seconds"] = int(time.time() - start_time)
                bot_state["last_scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                bot_state["last_error"] = ""
                bot_state["circuit_breaker"] = _risk_manager.circuit_breaker
                bot_state["circuit_breaker_reason"] = _risk_manager.circuit_breaker_reason

                logger.info(f"--- 扫描周期 #{scan_count} ---")

                # 1. 扫描市场
                opportunities = _scanner.scan_all()

                # 2. V3 Smart Money 分析
                if _smart_money and getattr(_config, 'ENABLE_SMART_MONEY', False):
                    try:
                        smart_signals = _smart_money.scan_smart_money([])
                        if smart_signals:
                            logger.info(f"Smart Money: {len(smart_signals)} signals")
                            if _data_store:
                                for sig in smart_signals:
                                    _data_store.save_smart_signal(sig)
                    except Exception as e:
                        logger.warning(f"Smart Money 分析失败: {e}")

                # 3. 更新持仓价格
                for pos in _risk_manager.positions:
                    try:
                        mid = _executor.get_midpoint(pos.token_id)
                        if mid and mid > 0:
                            pos.current_price = mid
                    except Exception:
                        pass

                # 4. 检查止损止盈
                to_close = _risk_manager.check_stop_loss_take_profit()
                for pos, reason in to_close:
                    logger.info(f"平仓: {pos.question[:40]} | {reason}")
                    if _config.DRY_RUN:
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
                        _risk_manager.record_trade(record)
                        if _data_store:
                            _data_store.save_trade(record)
                        _risk_manager.remove_position(pos.market_id)

                # 5. V3 OrderBook分析
                if _orderbook and _executor and _executor.initialized:
                    try:
                        for pos in _risk_manager.positions[:5]:
                            book = _executor.get_order_book(pos.token_id)
                            if book:
                                _orderbook.full_analysis(book, pos.token_id, pos.market_id, pos.question)
                    except Exception as e:
                        logger.debug(f"Orderbook分析失败: {e}")

                # 6. 处理交易机会
                # 套利
                if _config.ENABLE_ARBITRAGE:
                    for opp in opportunities.get("single_arb", []):
                        m = opp["market"]
                        if opp["arb_spread"] * 100 < _config.ARB_MIN_SPREAD:
                            continue
                        if any(p.market_id == m.id for p in _risk_manager.positions):
                            continue
                        trade_amount = _risk_manager.calculate_position_size(_config.INITIAL_CAPITAL)
                        can, reason = _risk_manager.check_can_trade(trade_amount * 2)
                        if not can:
                            continue
                        logger.info(f"套利: {m.question[:40]} 空间={opp['arb_spread']*100:.2f}%")
                        if _config.DRY_RUN:
                            yes_shares = trade_amount / m.yes_price if m.yes_price > 0 else 0
                            yes_pos = Position(
                                market_id=m.id, question=m.question,
                                token_id=m.yes_token_id, side="YES",
                                entry_price=m.yes_price, amount=yes_shares,
                                current_price=m.yes_price,
                            )
                            _risk_manager.add_position(yes_pos)
                            no_shares = trade_amount / m.no_price if m.no_price > 0 else 0
                            no_pos = Position(
                                market_id=f"{m.id}_NO", question=m.question,
                                token_id=m.no_token_id, side="NO",
                                entry_price=m.no_price, amount=no_shares,
                                current_price=m.no_price,
                            )
                            _risk_manager.add_position(no_pos)
                            record = TradeRecord(
                                timestamp=time.time(), market_id=m.id,
                                question=m.question, side="YES+NO", action="BUY",
                                price=m.total_price, amount=trade_amount * 2,
                                strategy="ARBITRAGE",
                            )
                            _risk_manager.record_trade(record)
                            if _data_store:
                                _data_store.save_trade(record)

                # 多市场套利
                if getattr(_config, 'ENABLE_MULTI_MARKET_ARB', False):
                    for opp in opportunities.get("multi_arb", []):
                        markets = opp.get("markets", [])
                        if not markets:
                            continue
                        direction = opp.get("direction", "")
                        net_spread = opp.get("net_spread", 0)
                        if net_spread <= 0:
                            continue
                        logger.info(f"多市场套利: {opp.get('event_title','')[:40]} 空间={net_spread*100:.2f}%")

                # 0手续费策略
                if getattr(_config, 'ENABLE_ZERO_FEE', False):
                    for opp in opportunities.get("zero_fee", []):
                        m = opp["market"]
                        if any(p.market_id == m.id for p in _risk_manager.positions):
                            continue
                        trade_amount = _risk_manager.calculate_position_size(_config.INITIAL_CAPITAL)
                        can, reason = _risk_manager.check_can_trade(trade_amount)
                        if not can:
                            continue
                        side = opp["side"]
                        price = opp["price"]
                        logger.info(f"0手续费: {m.question[:40]} {side}@{price:.3f}")
                        if _config.DRY_RUN:
                            shares = trade_amount / price if price > 0 else 0
                            pos = Position(
                                market_id=m.id, question=m.question,
                                token_id=m.yes_token_id if side == "YES" else m.no_token_id,
                                side=side, entry_price=price, amount=shares,
                                current_price=price,
                            )
                            _risk_manager.add_position(pos)
                            record = TradeRecord(
                                timestamp=time.time(), market_id=m.id,
                                question=m.question, side=side, action="BUY",
                                price=price, amount=trade_amount,
                                strategy="ZERO_FEE_VALUE",
                            )
                            _risk_manager.record_trade(record)
                            if _data_store:
                                _data_store.save_trade(record)

                # 均值回归
                if _config.ENABLE_MEAN_REVERSION:
                    for opp in opportunities.get("mean_reversion", []):
                        m = opp["market"]
                        if opp["confidence"] == "LOW":
                            continue
                        if any(p.market_id == m.id for p in _risk_manager.positions):
                            continue
                        trade_amount = _risk_manager.calculate_position_size(_config.INITIAL_CAPITAL)
                        can, reason = _risk_manager.check_can_trade(trade_amount)
                        if not can:
                            continue
                        side = opp["side"]
                        price = opp["price"]
                        logger.info(f"均值回归: {m.question[:40]} {side}@{price:.3f}")
                        if _config.DRY_RUN:
                            shares = trade_amount / price if price > 0 else 0
                            pos = Position(
                                market_id=m.id, question=m.question,
                                token_id=m.yes_token_id if side == "YES" else m.no_token_id,
                                side=side, entry_price=price, amount=shares,
                                current_price=price,
                            )
                            _risk_manager.add_position(pos)
                            record = TradeRecord(
                                timestamp=time.time(), market_id=m.id,
                                question=m.question, side=side, action="BUY",
                                price=price, amount=trade_amount,
                                strategy="MEAN_REVERSION",
                            )
                            _risk_manager.record_trade(record)
                            if _data_store:
                                _data_store.save_trade(record)

                # 事件驱动
                if _config.ENABLE_EVENT_DRIVEN:
                    for opp in opportunities.get("event_driven", []):
                        m = opp["market"]
                        if not m.is_extreme_price:
                            continue
                        if any(p.market_id == m.id for p in _risk_manager.positions):
                            continue
                        trade_amount = _risk_manager.calculate_position_size(_config.INITIAL_CAPITAL) * 0.5
                        trade_amount = max(trade_amount, _config.MIN_TRADE_SIZE)
                        can, reason = _risk_manager.check_can_trade(trade_amount)
                        if not can:
                            continue
                        side = opp["side"]
                        price = opp["price"]
                        logger.info(f"事件驱动: {m.question[:40]} {side}@{price:.3f}")
                        if _config.DRY_RUN:
                            shares = trade_amount / price if price > 0 else 0
                            pos = Position(
                                market_id=m.id, question=m.question,
                                token_id=m.yes_token_id if side == "YES" else m.no_token_id,
                                side=side, entry_price=price, amount=shares,
                                current_price=price,
                            )
                            _risk_manager.add_position(pos)
                            record = TradeRecord(
                                timestamp=time.time(), market_id=m.id,
                                question=m.question, side=side, action="BUY",
                                price=price, amount=trade_amount,
                                strategy="EVENT_DRIVEN",
                            )
                            _risk_manager.record_trade(record)
                            if _data_store:
                                _data_store.save_trade(record)

                # 7. 更新全局状态
                status = _risk_manager.get_status()
                bot_state["positions_count"] = status["positions_count"]
                bot_state["daily_pnl"] = status["daily_pnl"]
                bot_state["total_pnl"] = status["total_pnl"]
                bot_state["trade_count"] = status["trade_count"]
                bot_state["circuit_breaker"] = status["circuit_breaker"]

                # PnL 时间线
                pnl_point = {
                    "time": time.strftime("%H:%M:%S"),
                    "total_pnl": round(status["total_pnl"], 2),
                    "daily_pnl": round(status["daily_pnl"], 2),
                    "positions": status["positions_count"],
                }
                bot_state.setdefault("pnl_history", []).append(pnl_point)
                if len(bot_state["pnl_history"]) > 200:
                    bot_state["pnl_history"] = bot_state["pnl_history"][-200:]

                # 8. 保存状态
                if scan_count % 10 == 0:
                    try:
                        with open(state_file, "w") as f:
                            json.dump({
                                "total_pnl": _risk_manager.total_pnl,
                                "trade_count": _risk_manager.trade_count,
                            }, f)
                    except Exception:
                        pass

                logger.info(
                    f"扫描#{scan_count} | 持仓{status['positions_count']}/{_config.MAX_POSITIONS} | "
                    f"日PnL ${status['daily_pnl']:+.2f} | 累计 ${status['total_pnl']:+.2f}"
                )

                time.sleep(_config.SCAN_INTERVAL)

            except Exception as e:
                logger.error(f"主循环异常: {e}", exc_info=True)
                bot_state["status"] = "error"
                bot_state["last_error"] = str(e)
                time.sleep(30)

    except Exception as e:
        logger.error(f"Bot 初始化失败: {e}", exc_info=True)
        bot_state["status"] = "error"
        bot_state["last_error"] = str(e)


def setup_logging(level: str = "INFO"):
    import logging
    import sys
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main():
    import sys
    sys.path.insert(0, os.path.dirname(__file__))

    port = int(os.getenv("PORT", "8000"))
    mode = "模拟" if os.getenv("DRY_RUN", "true").lower() == "true" else "实盘"

    print()
    print("=" * 55)
    print("  Polymarket V3 量化交易系统 - REST API")
    print("=" * 55)
    print(f"  模式:     {mode}")
    print(f"  API端口:  {port}")
    print(f"  端点:     /api/status /api/positions /api/trades ...")
    print("=" * 55)
    print()

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    start_api_server(port)


if __name__ == "__main__":
    main()
