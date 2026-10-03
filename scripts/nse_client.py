from __future__ import annotations

import io
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0 Safari/537.36"
NSE_HOME = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/"
NSE_ARCHIVE = "https://nsearchives.nseindia.com/"
NIFTY_INDICES = "https://www.niftyindices.com/"


@dataclass
class NSEClient:
    timeout: int = 45
    min_delay: float = 0.35
    retries: int = 4

    def __post_init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": NSE_HOME,
            "Connection": "keep-alive",
        })
        self._last_request = 0.0
        self._warm = False

    def warm_up(self) -> None:
        if self._warm:
            return
        for url in (NSE_HOME, "https://www.nseindia.com/market-data/live-equity-market"):
            try:
                self.s.get(url, timeout=self.timeout)
            except requests.RequestException:
                pass
            time.sleep(self.min_delay)
        self._warm = True

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.warm_up()
        for attempt in range(self.retries):
            wait = self.min_delay - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kwargs)
                self._last_request = time.monotonic()
                if r.status_code in (401, 403, 406, 429):
                    self._warm = False
                    if attempt < self.retries - 1:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                r.raise_for_status()
                return r
            except requests.RequestException:
                self._last_request = time.monotonic()
                if attempt == self.retries - 1:
                    raise
                self._warm = False
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("NSE request failed")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = NSE_API + path.lstrip("/")
        return self._request("GET", url, params=params).json()

    def get_text(self, url: str) -> str:
        r = self._request("GET", url)
        return r.content.decode("utf-8-sig", errors="replace")

    def get_bytes(self, url: str) -> bytes:
        return self._request("GET", url).content


def fetch_nifty500_universe(client: NSEClient) -> pd.DataFrame:
    urls = [
        NIFTY_INDICES + "IndexConstituent/ind_nifty500list.csv",
        NSE_ARCHIVE + "content/indices/ind_nifty500list.csv",
    ]
    last: Exception | None = None
    for url in urls:
        try:
            text = client.get_text(url)
            df = pd.read_csv(io.StringIO(text))
            if {"Company Name", "Symbol"}.issubset(df.columns):
                cols = [c for c in ["Company Name", "Industry", "Symbol", "Series", "ISIN Code"] if c in df.columns]
                return df[cols].dropna(subset=["Symbol"]).drop_duplicates("Symbol")
        except Exception as exc:
            last = exc
    if last:
        raise RuntimeError(f"Could not fetch NIFTY 500 universe: {last}")
    raise RuntimeError("Could not fetch NIFTY 500 universe")


def fetch_bhavcopy(client: NSEClient, trade_date: date) -> bytes:
    ds = trade_date.strftime("%Y%m%d")
    url = NSE_ARCHIVE + f"content/cm/BhavCopy_NSE_CM_0_0_0_{ds}_F_0000.csv.zip"
    return client.get_bytes(url)


def parse_udiff_bhavcopy(blob: bytes, trade_date: date) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt"))]
        if not members:
            raise ValueError("No CSV found in NSE UDiFF bhavcopy ZIP")
        raw = zf.read(members[0])
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [str(c).strip() for c in df.columns]

    aliases = {
        "symbol": ["CH_SYMBOL", "SYMBOL", "Symbol"],
        "series": ["CH_SERIES", "SERIES", "Series"],
        "open": ["CH_OPENING_PRICE", "OPEN", "OPEN_PRICE", "Open"],
        "high": ["CH_TRADE_HIGH_PRICE", "HIGH", "HIGH_PRICE", "High"],
        "low": ["CH_TRADE_LOW_PRICE", "LOW", "LOW_PRICE", "Low"],
        "close": ["CH_CLOSING_PRICE", "CLOSE", "CLOSE_PRICE", "Close"],
        "volume": ["CH_TOT_TRADED_QTY", "TOTTRDQTY", "TOTAL_TRADED_QUANTITY", "VOLUME", "Volume"],
    }
    found: dict[str, str] = {}
    for target, names in aliases.items():
        for name in names:
            if name in df.columns:
                found[target] = name
                break
    required = ["symbol", "open", "high", "low", "close"]
    missing = [x for x in required if x not in found]
    if missing:
        raise ValueError(f"NSE bhavcopy missing fields: {missing}; columns={list(df.columns)}")

    out = pd.DataFrame({
        "symbol": df[found["symbol"]].astype(str).str.strip().str.upper(),
        "trade_date": pd.Timestamp(trade_date),
        "open": pd.to_numeric(df[found["open"]], errors="coerce"),
        "high": pd.to_numeric(df[found["high"]], errors="coerce"),
        "low": pd.to_numeric(df[found["low"]], errors="coerce"),
        "close": pd.to_numeric(df[found["close"]], errors="coerce"),
        "volume": pd.to_numeric(df[found["volume"]], errors="coerce") if "volume" in found else 0,
    })
    if "series" in found:
        series = df[found["series"]].astype(str).str.upper().str.strip()
        out = out[series.eq("EQ")]
    else:
        # UDiFF includes non-cash instruments; keep common equity-like rows only.
        out = out[out["symbol"].notna()]
    return out.dropna(subset=["symbol", "close"]).drop_duplicates("symbol")


def fetch_index_daily(client: NSEClient, trade_date: date) -> pd.DataFrame:
    ds = trade_date.strftime("%d%m%Y")
    url = NSE_ARCHIVE + f"content/indices/ind_close_all_{ds}.csv"
    text = client.get_text(url)
    df = pd.read_csv(io.StringIO(text))
    df.columns = [str(c).strip() for c in df.columns]
    rename = {
        "Index Name": "index_name",
        "Index Date": "trade_date",
        "Open Index Value": "open",
        "High Index Value": "high",
        "Low Index Value": "low",
        "Closing Index Value": "close",
        "Volume": "volume",
    }
    df = df.rename(columns=rename)
    required = ["index_name", "trade_date", "open", "high", "low", "close"]
    if not all(c in df.columns for c in required):
        raise ValueError(f"Unexpected NSE index CSV columns: {list(df.columns)}")
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce", dayfirst=True).dt.normalize()
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["index_name", "trade_date", "close"])


def fetch_quote(client: NSEClient, symbol: str) -> dict[str, Any]:
    return client.get_json("quote-equity", params={"symbol": symbol})


def fetch_results_comparison(client: NSEClient, symbol: str) -> dict[str, Any]:
    return client.get_json("results-comparision", params={"symbol": symbol})


def previous_trading_day(client: NSEClient, start: date, lookback: int = 7) -> tuple[date, bytes, pd.DataFrame]:
    for i in range(0, lookback + 1):
        d = start - timedelta(days=i)
        try:
            blob = fetch_bhavcopy(client, d)
            return d, blob, parse_udiff_bhavcopy(blob, d)
        except Exception:
            continue
    raise RuntimeError(f"Could not locate NSE bhavcopy in the previous {lookback} days from {start}")
