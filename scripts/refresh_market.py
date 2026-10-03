from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PUBLIC_DIR = ROOT / "public" / "data"
DB_PATH = DATA_DIR / "iron_candle_market.db"
ARCHIVE_PATH = DATA_DIR / "iron_candle_market.db.gz"
SCANNER_PATH = PUBLIC_DIR / "scanner.json"
HISTORY_PATH = PUBLIC_DIR / "scanner_history.json"

sys.path.insert(0, str(ROOT / "scripts"))
from market_db import connect, compress_db, get_fundamentals, get_index_history, get_meta, get_symbol_history, put_meta, upsert_index_prices, upsert_prices
from nse_client import NSEClient, fetch_bhavcopy, fetch_index_daily, fetch_nifty500_universe, previous_trading_day


DAILY_EMAS = (10, 25, 50, 100, 200)


def clean_series(df: pd.DataFrame, col: str) -> pd.Series:
    s = df[col]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return pd.to_numeric(s, errors="coerce")


def completed_monthly_ath(df: pd.DataFrame) -> tuple[bool, str | None]:
    if df.empty:
        return False, None
    d = df.set_index("trade_date") if "trade_date" in df.columns else df
    high = clean_series(d, "high").dropna()
    close = clean_series(d, "close").reindex(high.index).dropna()
    monthly = pd.DataFrame({"high": high, "close": close}).resample("ME").agg({"high": "max", "close": "last"}).dropna()
    if monthly.empty:
        return False, None
    last_daily = d.index[-1]
    if monthly.index[-1].to_period("M") == last_daily.to_period("M"):
        monthly = monthly.iloc[:-1]
    if monthly.empty:
        return False, None
    prior_ath = monthly["high"].cummax().shift(1)
    breakout = monthly["close"] > prior_ath
    dates = monthly.index[breakout.fillna(False)]
    return bool(breakout.iloc[-1]), dates[-1].strftime("%Y-%m-%d") if len(dates) else None


def current_52w_breakout(df: pd.DataFrame) -> tuple[bool, str | None, float | None]:
    if df.empty:
        return False, None, None
    d = df.sort_values("trade_date").copy()
    high = pd.to_numeric(d["high"], errors="coerce")
    close = pd.to_numeric(d["close"], errors="coerce")
    prior_high = high.rolling(252, min_periods=252).max().shift(1)
    breakout = close > prior_high
    dates = pd.to_datetime(d.loc[breakout.fillna(False), "trade_date"])
    prior_now = prior_high.iloc[-1] if len(prior_high) else math.nan
    return bool(breakout.iloc[-1]) if len(breakout) else False, dates.iloc[-1].strftime("%Y-%m-%d") if len(dates) else None, float(prior_now) if pd.notna(prior_now) else None


def all_52w_breakout_dates(df: pd.DataFrame) -> pd.Series:
    d = df.sort_values("trade_date").copy()
    high = pd.to_numeric(d["high"], errors="coerce")
    close = pd.to_numeric(d["close"], errors="coerce")
    prior_high = high.rolling(252, min_periods=252).max().shift(1)
    return pd.to_datetime(d.loc[(close > prior_high).fillna(False), "trade_date"])


def latest_completed_week_ema30(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    d = df.sort_values("trade_date").copy().set_index("trade_date")
    weekly = pd.to_numeric(d["close"], errors="coerce").resample("W-FRI").last().dropna()
    if weekly.empty:
        return None
    if weekly.index[-1].to_period("W-FRI") == d.index[-1].to_period("W-FRI"):
        weekly = weekly.iloc[:-1]
    if len(weekly) < 30:
        return None
    return float(weekly.ewm(span=30, adjust=False).mean().iloc[-1])


def stock_tech(df: pd.DataFrame, nifty: pd.DataFrame) -> dict:
    d = df.sort_values("trade_date").copy()
    close = pd.to_numeric(d["close"], errors="coerce")
    latest = float(close.iloc[-1])
    emas = {n: float(close.ewm(span=n, adjust=False).mean().iloc[-1]) for n in DAILY_EMAS}
    above = {n: latest > emas[n] for n in DAILY_EMAS}

    d52_today, d52_date, prior_52_high = current_52w_breakout(d)
    month_ok, month_date = completed_monthly_ath(d)
    wema = latest_completed_week_ema30(d)
    wdist = ((latest / wema) - 1) * 100 if wema else None

    # Relative strength uses the last 60 completed sessions available on both series.
    nclose = pd.to_numeric(nifty["close"], errors="coerce").dropna() if not nifty.empty else pd.Series(dtype=float)
    stock60 = float(close.iloc[-61]) if len(close) > 61 else None
    nifty60 = float(nclose.iloc[-61]) if len(nclose) > 61 else None
    rs60 = ((latest / stock60 - 1) - (float(nclose.iloc[-1]) / nifty60 - 1)) * 100 if stock60 and nifty60 else None

    return {
        "latest": latest,
        "emas": emas,
        "above": above,
        "emaOrdered": emas[10] > emas[25] > emas[50] > emas[100] > emas[200],
        "aboveAll": all(above.values()),
        "daily52wBreakoutToday": d52_today,
        "daily52wBreakoutDate": d52_date,
        "prior52wHigh": prior_52_high,
        "monthlyAthConfirmed": month_ok,
        "monthlyAthBreakoutDate": month_date,
        "weeklyEma30": wema,
        "weeklyEma30DistancePct": wdist,
        "rs60Pct": rs60,
    }


def latest_nifty_52w_breakout(nifty: pd.DataFrame) -> tuple[bool, str | None]:
    today, latest, _ = current_52w_breakout(nifty.rename(columns={"close":"close", "high":"high"}))
    return today, latest


def lead_days(nifty_dates: pd.Series, stock_date: str | None, nifty_date: str | None) -> int:
    if not stock_date or not nifty_date:
        return -999
    s = pd.Timestamp(stock_date)
    n = pd.Timestamp(nifty_date)
    dates = pd.to_datetime(nifty_dates).sort_values().drop_duplicates()
    if s < n:
        return max(0, int(((dates > s) & (dates <= n)).sum()))
    if s > n:
        return -max(1, int(((dates > n) & (dates <= s)).sum()))
    return 0


def financial_sector(industry: str) -> bool:
    text = (industry or "").lower()
    return any(term in text for term in ("bank", "financial", "insurance", "capital market", "asset management", "finance"))


def build_scanner(con: sqlite3.Connection, universe: pd.DataFrame, trade_date: str) -> dict:
    nifty = get_index_history(con, "NIFTY 50")
    if nifty.empty:
        raise RuntimeError("NIFTY 50 history is empty. Run bootstrap_history.py once before the first refresh.")
    nifty = nifty.sort_values("trade_date")
    nifty_breakout_today, nifty_breakout_date = latest_nifty_52w_breakout(nifty)
    fundamentals = get_fundamentals(con)
    nifty_dates = nifty["trade_date"]

    rows = []
    for meta in universe.to_dict("records"):
        symbol = str(meta.get("Symbol", "")).strip().upper()
        if not symbol:
            continue
        history = get_symbol_history(con, symbol)
        if len(history) < 260:
            continue
        try:
            tech = stock_tech(history, nifty)
        except Exception as exc:
            print(f"WARN tech {symbol}: {exc}")
            continue

        fund = fundamentals.get(symbol, {})
        is_fin = financial_sector(str(meta.get("Industry", "")))
        mcap = fund.get("market_cap_cr")
        pat = fund.get("profit_growth_yoy_pct")
        de = fund.get("debt_to_equity")
        roe = fund.get("roe_pct")
        sales = fund.get("sales_growth_yoy_pct")
        ocf = fund.get("ocf_positive")
        market_cap_pass = mcap is not None and 5000 <= mcap <= 25000
        profit_pass = pat is not None and pat >= 25
        de_pass = (not is_fin) and de is not None and de < 0.5
        core = bool(fund.get("fundamentalsAvailable") and market_cap_pass and profit_pass and de_pass)
        quality = bool(core and roe is not None and roe >= 15 and sales is not None and sales >= 10 and ocf is True)
        is_leading = bool(tech["daily52wBreakoutDate"] and nifty_breakout_date and tech["daily52wBreakoutDate"] < nifty_breakout_date)
        lead = lead_days(nifty_dates, tech["daily52wBreakoutDate"], nifty_breakout_date)
        weekly_near = tech["weeklyEma30DistancePct"] is not None and abs(tech["weeklyEma30DistancePct"]) <= 3
        strict = bool(tech["monthlyAthConfirmed"] and tech["daily52wBreakoutToday"] and tech["aboveAll"] and weekly_near and is_leading and (tech["rs60Pct"] is not None and tech["rs60Pct"] >= 0) and core)

        rows.append({
            "symbol": symbol,
            "companyName": str(meta.get("Company Name", symbol)),
            "industry": str(meta.get("Industry", "")),
            "close": round(tech["latest"], 4),
            "monthlyAthConfirmed": tech["monthlyAthConfirmed"],
            "monthlyAthBreakoutDate": tech["monthlyAthBreakoutDate"],
            "daily52wBreakoutToday": tech["daily52wBreakoutToday"],
            "daily52wBreakoutDate": tech["daily52wBreakoutDate"],
            "prior52wHigh": round(tech["prior52wHigh"], 4) if tech["prior52wHigh"] else None,
            "nifty52wBreakoutDate": nifty_breakout_date,
            "nifty52wBreakoutToday": nifty_breakout_today,
            "leadDays": lead,
            "isLeadingNifty": is_leading,
            "rs60Pct": round(tech["rs60Pct"], 2) if tech["rs60Pct"] is not None else None,
            "weeklyEma30": round(tech["weeklyEma30"], 4) if tech["weeklyEma30"] is not None else None,
            "weeklyEma30DistancePct": round(tech["weeklyEma30DistancePct"], 2) if tech["weeklyEma30DistancePct"] is not None else None,
            "aboveAllDailyEmas": tech["aboveAll"],
            "emaOrdered": tech["emaOrdered"],
            **{f"closeAboveEma{n}": bool(tech["above"][n]) for n in DAILY_EMAS},
            "isFinancialSector": is_fin,
            "fundamentalsAvailable": bool(fund.get("fundamentalsAvailable")),
            "fundamentalAsOf": fund.get("as_of"),
            "fundamentalRefreshedAt": fund.get("refreshed_at"),
            "fundamentalSource": fund.get("source"),
            "fundamentalSourceUrl": fund.get("source_url"),
            "marketCapCr": mcap,
            "netProfitGrowthYoYPct": pat,
            "debtToEquity": de,
            "roePct": roe,
            "salesGrowthYoYPct": sales,
            "ocfPositive": ocf,
            "marketCapPass": market_cap_pass,
            "profitGrowthPass": profit_pass,
            "debtEquityPass": de_pass,
            "fundamentalCoreQualified": core,
            "qualityOverlayQualified": quality,
            "strictQualified": strict,
            "asOf": trade_date,
        })

    rows.sort(key=lambda r: (not r["strictQualified"], not r["fundamentalCoreQualified"], -(r["leadDays"] or -999), -(r["rs60Pct"] or -9999)))
    return {
        "demo": False,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "asOf": trade_date,
        "source": "NSE official UDiFF final bhavcopy + NSE official index close file + Screener public fundamentals (weekly cache) + free Yahoo Finance one-time historical bootstrap",
        "meta": {
            "universeCount": int(len(universe)),
            "processedCount": len(rows),
            "fundamentalsProvider": "Screener public company pages" if fundamentals else "NOT_CONFIGURED",
            "priceProvider": "NSE official UDiFF final bhavcopy",
            "historyBootstrapProvider": "Yahoo Finance via yfinance (one-time only)",
            "benchmark": "NIFTY 50",
            "refreshSchedule": "Weekdays 18:30 IST",
            "weeklyEmaNearDefaultPct": 3,
            "minimumNiftyLeadDaysDefault": 1,
            "minimumRs60DefaultPct": 0,
            "marketCapMinCrDefault": 5000,
            "marketCapMaxCrDefault": 25000,
            "netProfitGrowthMinDefaultPct": 25,
            "debtEquityMaxDefault": 0.5,
            "qualityRoeMinDefaultPct": 15,
            "qualitySalesGrowthMinDefaultPct": 10,
            "niftyLatest52wBreakoutDate": nifty_breakout_date,
            "latestFundamentalRefresh": get_meta(con, "last_fundamentals_refresh", None),
        },
        "methodology": {
            "monthlyAth": "last completed monthly close > prior all-time monthly high",
            "daily52w": "latest daily close > prior 252-trading-day high",
            "dailyEma": "close > EMA10, EMA25, EMA50, EMA100, EMA200",
            "weeklyEma30": "latest completed weekly EMA30; near means absolute distance <= dashboard threshold",
            "niftyLead": "stock 52-week breakout date earlier than NIFTY 50 52-week breakout date",
            "rs60": "stock 60-session return minus NIFTY 50 60-session return",
            "fundamentalCore": "market cap 5,000–25,000 crore + latest annual net profit growth >=25% YoY + D/E <0.5 for non-financial sectors",
            "qualityOverlay": "ROE >=15% + annual sales growth >=10% + latest annual operating cash flow positive",
            "financials": "financial-sector names are flagged because D/E is not directly comparable to non-financial businesses",
        },
        "rows": rows,
    }


def write_history(con: sqlite3.Connection, keep: int = 120) -> None:
    raw = con.execute("SELECT trade_date, generated_at, payload_json FROM scanner_snapshots ORDER BY trade_date DESC LIMIT ?", (keep,)).fetchall()
    items = []
    for trade_date, generated_at, payload_json in raw:
        try:
            payload = json.loads(payload_json)
        except Exception:
            continue
        rows = payload.get("rows", [])
        strict = [r for r in rows if r.get("strictQualified")]
        items.append({
            "tradeDate": trade_date,
            "generatedAt": generated_at,
            "strictCount": len(strict),
            "strictSymbols": [r.get("symbol") for r in strict],
            "breakoutCount": sum(bool(r.get("daily52wBreakoutToday")) for r in rows),
            "coreFundamentalsCount": sum(bool(r.get("fundamentalCoreQualified")) for r in rows),
        })
    HISTORY_PATH.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    client = NSEClient()
    today = date.today()
    trade_date, blob, prices = previous_trading_day(client, today)
    index = fetch_index_daily(client, trade_date)

    con = connect(DB_PATH)
    n_prices = upsert_prices(con, prices)
    n_idx = upsert_index_prices(con, index)
    universe = fetch_nifty500_universe(client)
    payload = build_scanner(con, universe, trade_date.strftime("%Y-%m-%d"))
    con.execute(
        "INSERT INTO scanner_snapshots(trade_date, generated_at, payload_json) VALUES(?,?,?) ON CONFLICT(trade_date) DO UPDATE SET generated_at=excluded.generated_at,payload_json=excluded.payload_json",
        (payload["asOf"], payload["generatedAt"], json.dumps(payload, separators=(",", ":")))
    )
    put_meta(con, "last_price_refresh", payload["generatedAt"])
    put_meta(con, "last_trade_date", payload["asOf"])
    put_meta(con, "last_price_rows", str(n_prices))
    put_meta(con, "last_index_rows", str(n_idx))
    SCANNER_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_history(con)
    con.commit()
    con.close()
    compress_db(DB_PATH, ARCHIVE_PATH)
    print(f"IronCandle refreshed {payload['asOf']}: {n_prices} price rows, {n_idx} index rows, {len(payload['rows'])} scanner rows")

if __name__ == "__main__":
    main()
