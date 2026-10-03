from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import requests
from bs4 import BeautifulSoup

SCREENER_URL = "https://www.screener.in/company/{symbol}/consolidated/"
SCREENER_FALLBACK_URL = "https://www.screener.in/company/{symbol}/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154.0 Safari/537.36 IronCandle/2.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.screener.in/",
}


def _clean_num(text: str | None) -> float | None:
    if text is None:
        return None
    s = str(text).replace("₹", "").replace(",", "").replace("%", "").strip()
    if not s or s in {"-", "—", "NA", "N/A"}:
        return None
    # Screener can use parenthesized negatives.
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        value = float(s)
        return -value if negative else value
    except ValueError:
        return None


def _annual_columns(table) -> list[tuple[int, str, pd.Timestamp]]:
    """Return (td_index, label, date) for annual columns, excluding TTM."""
    rows = table.find_all("tr")
    if not rows:
        return []
    header = rows[0].find_all(["th", "td"])
    out: list[tuple[int, str, pd.Timestamp]] = []
    for idx, cell in enumerate(header):
        label = " ".join(cell.stripped_strings)
        match = re.search(r"\b(?:Mar|Jun|Sep|Dec)\s+(20\d{2})\b", label, re.I)
        if not match or "TTM" in label.upper():
            continue
        try:
            dt = pd.to_datetime(label, format="%b %Y")
        except Exception:
            continue
        out.append((idx, label, dt))
    return out


def _find_section_table(soup: BeautifulSoup, section_id: str):
    section = soup.find("section", id=section_id)
    if section:
        return section.find("table")
    return None


def _find_row(table, aliases: Iterable[str]):
    aliases_lower = [a.lower() for a in aliases]
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        label = " ".join(cells[0].stripped_strings).strip().lower()
        if any(label == a or label.startswith(a + " ") or a in label for a in aliases_lower):
            return cells
    return None


def _row_annual_values(table, aliases: Iterable[str]) -> list[tuple[pd.Timestamp, float]]:
    cols = _annual_columns(table)
    row = _find_row(table, aliases)
    if not row:
        return []
    result: list[tuple[pd.Timestamp, float]] = []
    for idx, _label, dt in cols:
        if idx >= len(row):
            continue
        val = _clean_num(" ".join(row[idx].stripped_strings))
        if val is not None:
            result.append((dt, val))
    return sorted(result, key=lambda x: x[0])


def _market_cap(soup: BeautifulSoup) -> float | None:
    # Public company pages expose a compact market-cap field near the top.
    text = " ".join(soup.stripped_strings)
    m = re.search(r"Market\s*Cap\s*[^₹\d]{0,20}₹?\s*([\d,]+(?:\.\d+)?)\s*Cr", text, re.I)
    if not m:
        # Be tolerant of pages using 'Cr.'
        m = re.search(r"Market\s*Cap.*?([\d,]+(?:\.\d+)?)\s*Cr\.?", text, re.I)
    return _clean_num(m.group(1)) if m else None


def _extract_company_name(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    return " ".join(h1.stripped_strings).strip() if h1 else None


def fetch_company(session: requests.Session, symbol: str, delay_seconds: float = 0.65) -> tuple[BeautifulSoup, str]:
    last_err: Exception | None = None
    for url_tmpl in (SCREENER_URL, SCREENER_FALLBACK_URL):
        url = url_tmpl.format(symbol=symbol.upper())
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and "Company" in r.text and len(r.text) > 10_000:
                time.sleep(delay_seconds)
                return BeautifulSoup(r.text, "html.parser"), url
            last_err = RuntimeError(f"HTTP {r.status_code}")
        except Exception as exc:
            last_err = exc
        time.sleep(delay_seconds)
    raise RuntimeError(f"Screener fetch failed for {symbol}: {last_err}")


def parse_company(soup: BeautifulSoup, symbol: str, source_url: str, industry: str | None) -> dict:
    pl = _find_section_table(soup, "profit-loss")
    bs = _find_section_table(soup, "balance-sheet")
    cf = _find_section_table(soup, "cash-flow")

    sales = _row_annual_values(pl, ["sales", "revenue"] ) if pl else []
    pat = _row_annual_values(pl, ["net profit", "profit after tax", "profit after tax (pat)"]) if pl else []
    borrowings = _row_annual_values(bs, ["borrowings", "total borrowings"]) if bs else []
    equity_capital = _row_annual_values(bs, ["equity capital"]) if bs else []
    reserves = _row_annual_values(bs, ["reserves"]) if bs else []
    ocf = _row_annual_values(cf, ["cash from operating activity", "cash from operating activities"]) if cf else []

    latest_pat, prev_pat = (pat[-1], pat[-2]) if len(pat) >= 2 else ((pat[-1], None) if pat else (None, None))
    latest_sales, prev_sales = (sales[-1], sales[-2]) if len(sales) >= 2 else ((sales[-1], None) if sales else (None, None))

    profit_growth = None
    if latest_pat and prev_pat and prev_pat[1] not in (0, None):
        profit_growth = (latest_pat[1] / prev_pat[1] - 1) * 100

    sales_growth = None
    if latest_sales and prev_sales and prev_sales[1] not in (0, None):
        sales_growth = (latest_sales[1] / prev_sales[1] - 1) * 100

    # Balance-sheet rows are reported in the same units on the annual statement.
    latest = latest_date = None
    if latest_pat:
        latest_date = latest_pat[0]
    elif latest_sales:
        latest_date = latest_sales[0]

    def latest_value(rows: list[tuple[pd.Timestamp, float]]) -> float | None:
        return rows[-1][1] if rows else None

    debt = latest_value(borrowings)
    eq_cap = latest_value(equity_capital)
    res = latest_value(reserves)
    equity = (eq_cap or 0) + (res or 0) if eq_cap is not None or res is not None else None
    debt_to_equity = debt / equity if debt is not None and equity not in (None, 0) else None

    latest_ocf = latest_value(ocf)
    ocf_positive = latest_ocf > 0 if latest_ocf is not None else None

    # ROE for display/quality overlay, using average equity where possible.
    roe = None
    if latest_pat and equity not in (None, 0):
        prior_eq_cap = equity_capital[-2][1] if len(equity_capital) >= 2 else None
        prior_res = reserves[-2][1] if len(reserves) >= 2 else None
        prior_equity = (prior_eq_cap or 0) + (prior_res or 0) if prior_eq_cap is not None or prior_res is not None else None
        avg_equity = ((equity + prior_equity) / 2) if prior_equity not in (None, 0) else equity
        if avg_equity:
            roe = latest_pat[1] / avg_equity * 100

    return {
        "symbol": symbol.upper(),
        "company_name": _extract_company_name(soup) or symbol.upper(),
        "industry": industry or "",
        "market_cap_cr": _market_cap(soup),
        "profit_growth_yoy_pct": round(profit_growth, 2) if profit_growth is not None else None,
        "debt_to_equity": round(debt_to_equity, 3) if debt_to_equity is not None else None,
        "roe_pct": round(roe, 2) if roe is not None else None,
        "sales_growth_yoy_pct": round(sales_growth, 2) if sales_growth is not None else None,
        "ocf_positive": ocf_positive,
        "source": "Screener public company page",
        "source_url": source_url,
        "as_of": latest_date.strftime("%Y-%m-%d") if latest_date is not None else None,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }


def refresh_fundamentals(universe: pd.DataFrame, existing: dict[str, dict] | None = None, delay_seconds: float = 0.65) -> list[dict]:
    existing = existing or {}
    session = requests.Session()
    output: list[dict] = []
    for i, row in universe.iterrows():
        symbol = str(row["Symbol"]).strip().upper()
        industry = str(row.get("Industry", ""))
        try:
            soup, url = fetch_company(session, symbol, delay_seconds=delay_seconds)
            parsed = parse_company(soup, symbol, url, industry)
            output.append(parsed)
        except Exception as exc:
            print(f"WARN fundamentals {symbol}: {exc}")
            old = existing.get(symbol)
            if old:
                output.append(old)
    return output
