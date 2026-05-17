"""
Polymarket V3 - SQLite数据持久化
6张表：market_snapshots, trades, position_history, wallet_trades, smart_signals, system_state
"""
import json
import logging
import os
import sqlite3
import time
from typing import Optional

logger = logging.getLogger("polymarket")


class DataStore:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_dir = os.path.join(os.path.dirname(__file__), "data")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "polymarket.db")
        self.db_path = db_path
        self.conn = None
        self._init_db()

    def _init_db(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # ===== 修复: 启用WAL模式解决并发"database is locked"问题 =====
        # WAL模式允许读写并发，不会互相阻塞
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")  # 等待5秒而不是立即失败
        self.conn.execute("PRAGMA synchronous=NORMAL")  # 平衡安全性和性能
        c = self.conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS market_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, market_id TEXT NOT NULL, question TEXT, yes_price REAL, no_price REAL, volume REAL, volume_24h REAL, liquidity REAL, price_change_1h REAL, price_change_24h REAL, category TEXT, fee_rate REAL, timestamp REAL NOT NULL, raw_data TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, market_id TEXT NOT NULL, question TEXT, side TEXT, action TEXT, price REAL, amount REAL, pnl REAL DEFAULT 0, strategy TEXT, signal_strength REAL DEFAULT 0, fee_paid REAL DEFAULT 0, dry_run INTEGER DEFAULT 1)""")
        c.execute("""CREATE TABLE IF NOT EXISTS position_history (id INTEGER PRIMARY KEY AUTOINCREMENT, market_id TEXT NOT NULL, question TEXT, side TEXT, entry_price REAL, exit_price REAL, amount REAL, pnl REAL, pnl_percent REAL, hold_time_hours REAL, strategy TEXT, signal_strength REAL, entry_time REAL, exit_time REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS wallet_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, wallet_address TEXT NOT NULL, market_id TEXT, question TEXT, side TEXT, outcome TEXT, price REAL, amount_usd REAL, shares REAL, timestamp REAL, source TEXT DEFAULT 'gamma_api')""")
        c.execute("""CREATE TABLE IF NOT EXISTS smart_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, signal_type TEXT NOT NULL, direction TEXT, strength REAL, market_id TEXT, question TEXT, confidence TEXT, reason TEXT, timestamp REAL NOT NULL, acted_upon INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, value TEXT, updated_at REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_market_ts ON market_snapshots(market_id, timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_signals_ts ON smart_signals(timestamp)")
        self.conn.commit()
        logger.info(f"数据库已初始化: {self.db_path}")

    def save_market_snapshot(self, market_id: str, data: dict):
        try:
            self.conn.execute("INSERT INTO market_snapshots (market_id,question,yes_price,no_price,volume,volume_24h,liquidity,price_change_1h,price_change_24h,category,fee_rate,timestamp,raw_data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (market_id, data.get("question",""), data.get("yes_price",0), data.get("no_price",0), data.get("volume",0), data.get("volume_24h",0), data.get("liquidity",0), data.get("price_change_1h",0), data.get("price_change_24h",0), data.get("category",""), data.get("fee_rate",0), time.time(), json.dumps(data, default=str)[:2000]))
            self.conn.commit()
        except Exception as e:
            logger.warning(f"保存市场快照失败: {e}")

    def save_trade(self, trade_record) -> int:
        try:
            cursor = self.conn.execute("INSERT INTO trades (timestamp,market_id,question,side,action,price,amount,pnl,strategy,signal_strength,dry_run) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (trade_record.timestamp, trade_record.market_id, trade_record.question, trade_record.side, trade_record.action, trade_record.price, trade_record.amount, trade_record.pnl, trade_record.strategy, getattr(trade_record,'signal_strength',0), 1 if os.getenv("DRY_RUN","true")=="true" else 0))
            self.conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.warning(f"保存交易记录失败: {e}")
            return -1

    def save_position_close(self, position, exit_price: float, pnl: float):
        try:
            pnl_pct = ((exit_price - position.entry_price) / position.entry_price * 100) if position.entry_price > 0 else 0
            self.conn.execute("INSERT INTO position_history (market_id,question,side,entry_price,exit_price,amount,pnl,pnl_percent,hold_time_hours,strategy,signal_strength,entry_time,exit_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (position.market_id, position.question, position.side, position.entry_price, exit_price, position.amount, pnl, pnl_pct, (time.time() - position.entry_time) / 3600, getattr(position, 'strategy', ''), getattr(position, 'signal_strength', 0), position.entry_time, time.time()))
            self.conn.commit()
        except Exception as e:
            logger.warning(f"保存持仓记录失败: {e}")

    def save_smart_signal(self, signal) -> int:
        try:
            cursor = self.conn.execute("INSERT INTO smart_signals (signal_type,direction,strength,market_id,question,confidence,reason,timestamp) VALUES (?,?,?,?,?,?,?,?)", (signal.signal_type, signal.direction, signal.strength, getattr(signal,'market_id',''), getattr(signal,'question',''), getattr(signal,'confidence',''), getattr(signal,'reason',''), time.time()))
            self.conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.warning(f"保存信号失败: {e}")
            return -1

    def get_strategy_performance(self, strategy: str = "", days: int = 30) -> dict:
        cutoff = time.time() - days * 86400
        try:
            query = "SELECT strategy, COUNT(*) as total_trades, SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins, SUM(pnl) as total_pnl, AVG(pnl) as avg_pnl FROM trades WHERE timestamp>? AND strategy!=''"
            params = [cutoff]
            if strategy:
                query += " AND strategy=?"
                params.append(strategy)
            query += " GROUP BY strategy"
            cursor = self.conn.execute(query, params)
            results = {}
            for row in cursor.fetchall():
                s = dict(row)
                s["win_rate"] = s["wins"] / s["total_trades"] if s["total_trades"] > 0 else 0
                results[s["strategy"]] = s
            return results
        except Exception as e:
            return {}

    def save_state(self, key: str, value: any):
        try:
            self.conn.execute("INSERT OR REPLACE INTO system_state (key,value,updated_at) VALUES (?,?,?)", (key, json.dumps(value, default=str), time.time()))
            self.conn.commit()
        except Exception as e:
            logger.warning(f"保存状态失败: {e}")

    def load_state(self, key: str, default=None):
        try:
            cursor = self.conn.execute("SELECT value FROM system_state WHERE key=?", (key,))
            row = cursor.fetchone()
            return json.loads(row["value"]) if row else default
        except:
            return default

    def get_stats(self) -> dict:
        try:
            stats = {}
            for t in ["market_snapshots", "trades", "position_history", "wallet_trades", "smart_signals"]:
                cursor = self.conn.execute(f"SELECT COUNT(*) as cnt FROM {t}")
                stats[t] = cursor.fetchone()["cnt"]
            return stats
        except Exception as e:
            return {"error": str(e)}

    def cleanup_old_data(self, days: int = 90):
        cutoff = time.time() - days * 86400
        try:
            for t in ["market_snapshots", "wallet_trades"]:
                self.conn.execute(f"DELETE FROM {t} WHERE timestamp<?", (cutoff,))
            self.conn.commit()
        except:
            pass

    def close(self):
        if self.conn:
            self.conn.close()
