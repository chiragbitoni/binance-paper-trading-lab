from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  strategy_key TEXT NOT NULL,
  symbol TEXT NOT NULL,
  interval TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  starting_balance REAL NOT NULL,
  ending_balance REAL NOT NULL,
  total_return_pct REAL NOT NULL,
  trades INTEGER NOT NULL,
  wins INTEGER NOT NULL,
  losses INTEGER NOT NULL,
  win_rate REAL NOT NULL,
  profit_factor REAL,
  max_drawdown_pct REAL NOT NULL,
  parameters TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  strategy_key TEXT NOT NULL,
  entry_time TEXT NOT NULL,
  exit_time TEXT NOT NULL,
  entry_price REAL NOT NULL,
  exit_price REAL NOT NULL,
  quantity REAL NOT NULL,
  pnl REAL NOT NULL,
  pnl_pct REAL NOT NULL,
  exit_reason TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES backtest_runs(id)
);
CREATE TABLE IF NOT EXISTS paper_accounts (
  symbol TEXT NOT NULL,
  strategy_key TEXT NOT NULL,
  cash REAL NOT NULL,
  quantity REAL NOT NULL DEFAULT 0,
  entry_price REAL,
  stop_price REAL,
  high_water REAL,
  pending_order TEXT,
  last_candle_time TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(symbol, strategy_key)
);
CREATE TABLE IF NOT EXISTS paper_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  symbol TEXT NOT NULL,
  strategy_key TEXT NOT NULL,
  side TEXT NOT NULL,
  price REAL NOT NULL,
  quantity REAL NOT NULL,
  fee REAL NOT NULL,
  realized_pnl REAL,
  reason TEXT NOT NULL,
  candle_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_snapshots (
  symbol TEXT PRIMARY KEY,
  candle_time TEXT NOT NULL,
  close REAL NOT NULL,
  delta_base REAL NOT NULL,
  delta_quote REAL NOT NULL,
  delta_ratio REAL,
  cvd_quote_24h REAL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS market_candles (
  symbol TEXT NOT NULL,
  candle_time TEXT NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  PRIMARY KEY(symbol, candle_time)
);
CREATE TABLE IF NOT EXISTS futures_wallet (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  cash REAL NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS futures_position (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
  quantity REAL NOT NULL,
  entry_price REAL NOT NULL,
  stop_price REAL NOT NULL,
  take_profit REAL NOT NULL,
  margin REAL NOT NULL,
  high_water REAL NOT NULL,
  low_water REAL NOT NULL,
  entry_time TEXT NOT NULL,
  last_candle_time TEXT,
  last_funding_time INTEGER,
  funding_paid REAL NOT NULL DEFAULT 0,
  pending_exit INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS futures_pending_order (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
  signal_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS futures_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  action TEXT NOT NULL,
  price REAL NOT NULL,
  quantity REAL NOT NULL,
  margin REAL NOT NULL,
  fee REAL NOT NULL,
  realized_pnl REAL,
  funding_paid REAL NOT NULL DEFAULT 0,
  reason TEXT NOT NULL,
  candle_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS futures_snapshots (
  symbol TEXT PRIMARY KEY,
  candle_time TEXT NOT NULL,
  close REAL NOT NULL,
  mark_price REAL,
  funding_rate REAL,
  next_funding_time INTEGER,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS futures_candles (
  symbol TEXT NOT NULL,
  candle_time TEXT NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  PRIMARY KEY(symbol, candle_time)
);
"""


class Storage:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate_multi_symbol()

    def _migrate_multi_symbol(self) -> None:
        account_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(paper_accounts)")}
        if "symbol" not in account_columns:
            self.connection.executescript("""
            ALTER TABLE paper_accounts RENAME TO paper_accounts_single_symbol;
            CREATE TABLE paper_accounts (
              symbol TEXT NOT NULL, strategy_key TEXT NOT NULL, cash REAL NOT NULL,
              quantity REAL NOT NULL DEFAULT 0, entry_price REAL, stop_price REAL,
              high_water REAL, last_candle_time TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(symbol, strategy_key)
            );
            INSERT INTO paper_accounts
              (symbol,strategy_key,cash,quantity,entry_price,stop_price,high_water,last_candle_time,updated_at)
            SELECT 'BTCUSDT',strategy_key,cash,quantity,entry_price,stop_price,high_water,last_candle_time,updated_at
            FROM paper_accounts_single_symbol;
            DROP TABLE paper_accounts_single_symbol;
            """)
        account_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(paper_accounts)")}
        if "pending_order" not in account_columns:
            self.connection.execute("ALTER TABLE paper_accounts ADD COLUMN pending_order TEXT")
        trade_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(paper_trades)")}
        if "symbol" not in trade_columns:
            self.connection.execute("ALTER TABLE paper_trades ADD COLUMN symbol TEXT NOT NULL DEFAULT 'BTCUSDT'")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def save_backtest(self, result: dict, trades: list[dict]) -> int:
        fields = ["strategy_key", "symbol", "interval", "start_time", "end_time", "starting_balance",
                  "ending_balance", "total_return_pct", "trades", "wins", "losses", "win_rate",
                  "profit_factor", "max_drawdown_pct"]
        values = [result[field] for field in fields] + [json.dumps(result.get("parameters", {}), sort_keys=True)]
        marks = ",".join("?" for _ in values)
        cursor = self.connection.execute(
            f"INSERT INTO backtest_runs ({','.join(fields)},parameters) VALUES ({marks})", values
        )
        run_id = int(cursor.lastrowid)
        for trade in trades:
            self.connection.execute(
                """INSERT INTO backtest_trades
                (run_id,strategy_key,entry_time,exit_time,entry_price,exit_price,quantity,pnl,pnl_pct,exit_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (run_id, result["strategy_key"], trade["entry_time"], trade["exit_time"], trade["entry_price"],
                 trade["exit_price"], trade["quantity"], trade["pnl"], trade["pnl_pct"], trade["exit_reason"]),
            )
        self.connection.commit()
        return run_id

    def latest_backtests(self) -> list[dict]:
        query = """
        SELECT b.* FROM backtest_runs b
        JOIN (SELECT symbol, strategy_key, MAX(id) id FROM backtest_runs
              GROUP BY symbol, strategy_key) latest
        ON b.id = latest.id ORDER BY b.symbol, b.total_return_pct DESC
        """
        return [dict(row) for row in self.connection.execute(query)]

    def initialize_paper_accounts(self, symbols: list[str], strategies: list[str], starting_balance: float) -> None:
        for symbol in symbols:
            for key in strategies:
                self.connection.execute(
                    "INSERT OR IGNORE INTO paper_accounts(symbol,strategy_key,cash) VALUES (?,?,?)",
                    (symbol, key, starting_balance),
                )
        self.connection.commit()

    def paper_accounts(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM paper_accounts ORDER BY symbol,strategy_key"
        )]

    def update_paper_account(self, account: dict) -> None:
        self.connection.execute(
            """UPDATE paper_accounts SET cash=?,quantity=?,entry_price=?,stop_price=?,high_water=?,pending_order=?,
            last_candle_time=?,updated_at=CURRENT_TIMESTAMP WHERE symbol=? AND strategy_key=?""",
            (account["cash"], account["quantity"], account.get("entry_price"), account.get("stop_price"),
             account.get("high_water"), account.get("pending_order"), account.get("last_candle_time"),
             account["symbol"], account["strategy_key"]),
        )
        self.connection.commit()

    def save_paper_trade(self, trade: dict) -> None:
        self.connection.execute(
            """INSERT INTO paper_trades(symbol,strategy_key,side,price,quantity,fee,realized_pnl,reason,candle_time)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (trade["symbol"], trade["strategy_key"], trade["side"], trade["price"], trade["quantity"], trade["fee"],
             trade.get("realized_pnl"), trade["reason"], trade["candle_time"]),
        )
        self.connection.commit()

    def recent_paper_trades(self, limit: int = 50) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)
        )]

    def save_market_snapshot(self, snapshot: dict) -> None:
        self.connection.execute(
            """INSERT INTO market_snapshots
            (symbol,candle_time,close,delta_base,delta_quote,delta_ratio,cvd_quote_24h)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET candle_time=excluded.candle_time,close=excluded.close,
              delta_base=excluded.delta_base,delta_quote=excluded.delta_quote,
              delta_ratio=excluded.delta_ratio,cvd_quote_24h=excluded.cvd_quote_24h,
              updated_at=CURRENT_TIMESTAMP""",
            (snapshot["symbol"], snapshot["candle_time"], snapshot["close"], snapshot["delta_base"],
             snapshot["delta_quote"], snapshot["delta_ratio"], snapshot["cvd_quote_24h"]),
        )
        self.connection.commit()

    def market_snapshots(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM market_snapshots ORDER BY symbol"
        )]

    def save_market_candles(self, symbol: str, candles) -> None:
        """Persist a compact completed-candle window for the position charts."""
        window = candles.tail(180)
        if window.empty:
            return
        first_time = str(window.iloc[0].open_time)
        self.connection.execute(
            "DELETE FROM market_candles WHERE symbol=? AND candle_time < ?", (symbol, first_time)
        )
        self.connection.executemany(
            """INSERT INTO market_candles(symbol,candle_time,open,high,low,close,volume)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(symbol,candle_time) DO UPDATE SET open=excluded.open,high=excluded.high,
              low=excluded.low,close=excluded.close,volume=excluded.volume""",
            [(symbol, str(row.open_time), row.open, row.high, row.low, row.close, row.volume)
             for row in window.itertuples(index=False)],
        )
        self.connection.commit()

    def market_candles(self, symbol: str, limit: int = 180) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM market_candles WHERE symbol=? ORDER BY candle_time DESC LIMIT ?", (symbol, limit)
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def initialize_futures_wallet(self, starting_balance: float) -> None:
        self.connection.execute("INSERT OR IGNORE INTO futures_wallet(id,cash) VALUES (1,?)", (starting_balance,))
        self.connection.commit()

    def futures_wallet(self) -> dict:
        row = self.connection.execute("SELECT * FROM futures_wallet WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("futures wallet has not been initialized")
        return dict(row)

    def set_futures_wallet(self, cash: float) -> None:
        self.connection.execute("UPDATE futures_wallet SET cash=?,updated_at=CURRENT_TIMESTAMP WHERE id=1", (cash,))
        self.connection.commit()

    def futures_position(self) -> dict | None:
        row = self.connection.execute("SELECT * FROM futures_position WHERE id=1").fetchone()
        return dict(row) if row else None

    def save_futures_position(self, position: dict) -> None:
        fields = ("symbol", "side", "quantity", "entry_price", "stop_price", "take_profit", "margin",
                  "high_water", "low_water", "entry_time", "last_candle_time", "last_funding_time",
                  "funding_paid", "pending_exit")
        values = tuple(position.get(field) for field in fields)
        self.connection.execute(
            f"INSERT INTO futures_position(id,{','.join(fields)}) VALUES (1,{','.join('?' for _ in fields)}) "
            f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{field}=excluded.{field}' for field in fields)}", values
        )
        self.connection.commit()

    def clear_futures_position(self) -> None:
        self.connection.execute("DELETE FROM futures_position WHERE id=1")
        self.connection.commit()

    def futures_pending_order(self) -> dict | None:
        row = self.connection.execute("SELECT * FROM futures_pending_order WHERE id=1").fetchone()
        return dict(row) if row else None

    def set_futures_pending_order(self, symbol: str, side: str, signal_time: str) -> None:
        self.connection.execute(
            "INSERT INTO futures_pending_order(id,symbol,side,signal_time) VALUES (1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET symbol=excluded.symbol,side=excluded.side,signal_time=excluded.signal_time",
            (symbol, side, signal_time),
        )
        self.connection.commit()

    def clear_futures_pending_order(self) -> None:
        self.connection.execute("DELETE FROM futures_pending_order WHERE id=1")
        self.connection.commit()

    def save_futures_trade(self, trade: dict) -> None:
        fields = ("symbol", "side", "action", "price", "quantity", "margin", "fee", "realized_pnl",
                  "funding_paid", "reason", "candle_time")
        self.connection.execute(
            f"INSERT INTO futures_trades({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
            tuple(trade.get(field) for field in fields),
        )
        self.connection.commit()

    def recent_futures_trades(self, limit: int = 50) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM futures_trades ORDER BY id DESC LIMIT ?", (limit,)
        )]

    def save_futures_snapshot(self, snapshot: dict) -> None:
        self.connection.execute(
            """INSERT INTO futures_snapshots(symbol,candle_time,close,mark_price,funding_rate,next_funding_time)
            VALUES (?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET candle_time=excluded.candle_time,
            close=excluded.close,mark_price=excluded.mark_price,funding_rate=excluded.funding_rate,
            next_funding_time=excluded.next_funding_time,updated_at=CURRENT_TIMESTAMP""",
            (snapshot["symbol"], snapshot["candle_time"], snapshot["close"], snapshot.get("mark_price"),
             snapshot.get("funding_rate"), snapshot.get("next_funding_time")),
        )
        self.connection.commit()

    def futures_snapshots(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM futures_snapshots ORDER BY symbol")]

    def save_futures_candles(self, symbol: str, candles) -> None:
        window = candles.tail(180)
        if window.empty:
            return
        first_time = str(window.iloc[0].open_time)
        self.connection.execute("DELETE FROM futures_candles WHERE symbol=? AND candle_time < ?", (symbol, first_time))
        self.connection.executemany(
            """INSERT INTO futures_candles(symbol,candle_time,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(symbol,candle_time) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,
            close=excluded.close,volume=excluded.volume""",
            [(symbol, str(row.open_time), row.open, row.high, row.low, row.close, row.volume)
             for row in window.itertuples(index=False)],
        )
        self.connection.commit()

    def futures_candles(self, symbol: str, limit: int = 180) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM futures_candles WHERE symbol=? ORDER BY candle_time DESC LIMIT ?", (symbol, limit)
        ).fetchall()
        return [dict(row) for row in reversed(rows)]
