from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "iron_candle_market.db"

def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_convert(None)
    frame = frame.sort_index().dropna(how="all")
    return frame


def _normalize_batch(batch: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if batch is None or batch.empty:
        return pd.DataFrame()
    try:
        frame = _clean(batch[symbol])
    except Exception:
        frame = _clean(batch)
    if frame.empty:
        return pd.DataFrame()
    frame.columns = [str(c).title() for c in frame.columns]
    needed = ["Open", "High", "Low", "Close", "Volume"]
    for c in needed:
        if c not in frame.columns:
            frame[c] = 0.0
    out = frame[needed].reset_index().rename(columns={frame.index.name or "Date": "trade_date"})
    if "trade_date" not in out.columns:
        out = out.rename(columns={out.columns[0]: "trade_date"})
    out["symbol"] = symbol
    out = out.rename(columns={"Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"})
    return out[["symbol","trade_date","open","high","low","close","volume"]]


def main():
    p = argparse.ArgumentParser(description="One-time free historical bootstrap using Yahoo Finance")
    p.add_argument("--years", type=int, default=20, help="Lookback years if max is unavailable; default 20")
    p.add_argument("--batch", type=int, default=35)
    args = p.parse_args()

    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from nse_client import NSEClient, fetch_nifty500_universe
    from market_db import connect, upsert_index_prices, upsert_prices, compress_db

    client = NSEClient()
    universe = fetch_nifty500_universe(client)
    symbols = [str(s).upper().strip() for s in universe["Symbol"].dropna().tolist()]
    con = connect(DB)

    index = _clean(yf.download("^NSEI", period="max", interval="1d", auto_adjust=False, progress=False, threads=False))
    if not index.empty:
        if isinstance(index.columns, pd.MultiIndex):
            index = index.xs("^NSEI", axis=1, level=-1)
        idx_df = index.reset_index()
        idx_df.columns = [str(c).title() for c in idx_df.columns]
        idx_df = idx_df.rename(columns={"Date":"trade_date","Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"})
        idx_df["index_name"] = "NIFTY 50"
        for c in ["open","high","low","close","volume"]: idx_df[c] = pd.to_numeric(idx_df[c], errors="coerce")
        upsert_index_prices(con, idx_df[["index_name","trade_date","open","high","low","close","volume"]].dropna(subset=["close"]))

    for start in range(0, len(symbols), args.batch):
        subset = symbols[start:start+args.batch]
        tickers = [f"{s}.NS" for s in subset]
        print(f"Bootstrap {start+1}-{min(start+args.batch,len(symbols))} / {len(symbols)}")
        try:
            batch = yf.download(tickers, period="max", interval="1d", auto_adjust=False, group_by="ticker", progress=False, threads=True)
        except Exception as exc:
            print("Batch failed:", exc)
            continue
        for s in subset:
            df = _normalize_batch(batch, f"{s}.NS")
            if not df.empty:
                upsert_prices(con, df)

    gz = DB.with_suffix(".db.gz")
    con.close()
    compress_db(DB, gz)
    print(f"Bootstrapped {DB}; compressed archive at {gz}")

if __name__ == "__main__":
    main()
