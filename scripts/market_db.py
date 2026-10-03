from __future__ import annotations

import gzip
import shutil
import sqlite3
from pathlib import Path

import pandas as pd

DB_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL,
    PRIMARY KEY(symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_prices_symbol_date ON prices(symbol, trade_date);
CREATE TABLE IF NOT EXISTS index_prices (
    index_name TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL,
    PRIMARY KEY(index_name, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_index_prices_name_date ON index_prices(index_name, trade_date);
CREATE TABLE IF NOT EXISTS fundamentals (
    symbol TEXT PRIMARY KEY,
    company_name TEXT,
    industry TEXT,
    market_cap_cr REAL,
    profit_growth_yoy_pct REAL,
    debt_to_equity REAL,
    roe_pct REAL,
    sales_growth_yoy_pct REAL,
    ocf_positive INTEGER,
    source TEXT,
    source_url TEXT,
    as_of TEXT,
    refreshed_at TEXT
);
CREATE TABLE IF NOT EXISTS scanner_snapshots (
    trade_date TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(DB_SCHEMA)
    # Lightweight migration for DBs created by earlier IronCandle builds.
    cols = {r[1] for r in con.execute("PRAGMA table_info(fundamentals)")}
    if "source_url" not in cols:
        con.execute("ALTER TABLE fundamentals ADD COLUMN source_url TEXT")
        con.commit()
    return con


def upsert_prices(con: sqlite3.Connection, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ["symbol", "trade_date", "open", "high", "low", "close", "volume"]
    rows = []
    for r in df[cols].itertuples(index=False, name=None):
        rows.append((str(r[0]).upper(), pd.Timestamp(r[1]).strftime("%Y-%m-%d"), *r[2:]))
    con.executemany("""
        INSERT INTO prices(symbol, trade_date, open, high, low, close, volume)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(symbol, trade_date) DO UPDATE SET
          open=excluded.open, high=excluded.high, low=excluded.low,
          close=excluded.close, volume=excluded.volume
    """, rows)
    con.commit()
    return len(rows)


def upsert_index_prices(con: sqlite3.Connection, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ["index_name", "trade_date", "open", "high", "low", "close", "volume"]
    rows = []
    for r in df[cols].fillna(0).itertuples(index=False, name=None):
        rows.append((str(r[0]), pd.Timestamp(r[1]).strftime("%Y-%m-%d"), *r[2:]))
    con.executemany("""
        INSERT INTO index_prices(index_name, trade_date, open, high, low, close, volume)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(index_name, trade_date) DO UPDATE SET
          open=excluded.open, high=excluded.high, low=excluded.low,
          close=excluded.close, volume=excluded.volume
    """, rows)
    con.commit()
    return len(rows)


def upsert_fundamentals(con: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    q = """
    INSERT INTO fundamentals(symbol, company_name, industry, market_cap_cr, profit_growth_yoy_pct,
        debt_to_equity, roe_pct, sales_growth_yoy_pct, ocf_positive, source, source_url, as_of, refreshed_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(symbol) DO UPDATE SET
        company_name=excluded.company_name, industry=excluded.industry,
        market_cap_cr=excluded.market_cap_cr, profit_growth_yoy_pct=excluded.profit_growth_yoy_pct,
        debt_to_equity=excluded.debt_to_equity, roe_pct=excluded.roe_pct,
        sales_growth_yoy_pct=excluded.sales_growth_yoy_pct, ocf_positive=excluded.ocf_positive,
        source=excluded.source, source_url=excluded.source_url, as_of=excluded.as_of, refreshed_at=excluded.refreshed_at
    """
    vals = [(
        r.get("symbol"), r.get("company_name"), r.get("industry"), r.get("market_cap_cr"),
        r.get("profit_growth_yoy_pct"), r.get("debt_to_equity"), r.get("roe_pct"),
        r.get("sales_growth_yoy_pct"), 1 if r.get("ocf_positive") is True else 0 if r.get("ocf_positive") is False else None,
        r.get("source"), r.get("source_url"), r.get("as_of"), r.get("refreshed_at")
    ) for r in rows]
    con.executemany(q, vals)
    con.commit()
    return len(vals)


def get_fundamentals(con: sqlite3.Connection) -> dict[str, dict]:
    df = pd.read_sql_query("SELECT * FROM fundamentals", con)
    if df.empty:
        return {}
    result = {}
    for row in df.to_dict("records"):
        row["fundamentalsAvailable"] = True
        if row.get("ocf_positive") is not None:
            row["ocf_positive"] = bool(row["ocf_positive"])
        result[str(row["symbol"]).upper()] = row
    return result


def get_symbol_history(con: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT trade_date, open, high, low, close, volume FROM prices WHERE symbol=? ORDER BY trade_date",
        con, params=[symbol.upper()], parse_dates=["trade_date"])


def get_index_history(con: sqlite3.Connection, index_name: str = "NIFTY 50") -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT trade_date, open, high, low, close, volume FROM index_prices WHERE index_name LIKE ? ORDER BY trade_date",
        con, params=[f"%{index_name}%"], parse_dates=["trade_date"])


def put_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    con.commit()


def get_meta(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def compress_db(db_path: Path, gz_path: Path) -> None:
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    with open(db_path, "rb") as src, gzip.open(gz_path, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)


def decompress_db(gz_path: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(gz_path, "rb") as src, open(db_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
