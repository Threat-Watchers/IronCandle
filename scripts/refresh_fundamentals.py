from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "iron_candle_market.db"
ARCHIVE_PATH = ROOT / "data" / "iron_candle_market.db.gz"
SCANNER_PATH = ROOT / "public" / "data" / "scanner.json"

sys.path.insert(0, str(ROOT / "scripts"))
from fundamentals import refresh_fundamentals
from market_db import compress_db, connect, get_fundamentals, get_meta, upsert_fundamentals
from nse_client import NSEClient, fetch_nifty500_universe


def main():
    if not DB_PATH.exists():
        raise SystemExit("Market DB not found. Run scripts/bootstrap_history.py once first.")
    client = NSEClient()
    universe = fetch_nifty500_universe(client)
    con = connect(DB_PATH)
    existing = get_fundamentals(con)
    rows = refresh_fundamentals(universe, existing=existing, delay_seconds=0.55)
    updated = upsert_fundamentals(con, rows)
    con.execute("INSERT INTO meta(key,value) VALUES('last_fundamentals_refresh',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (date.today().isoformat(),))
    con.commit()
    # Preserve the last scanner rows but update fundamental fields only by rerunning market scanner if market data exists.
    try:
        from refresh_market import build_scanner
        payload = build_scanner(con, universe, get_meta(con, "last_trade_date", date.today().isoformat()) or date.today().isoformat())
        SCANNER_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        con.execute(
            "INSERT INTO scanner_snapshots(trade_date, generated_at, payload_json) VALUES(?,?,?) ON CONFLICT(trade_date) DO UPDATE SET generated_at=excluded.generated_at,payload_json=excluded.payload_json",
            (payload["asOf"], payload["generatedAt"], json.dumps(payload, separators=(",", ":")))
        )
        con.commit()
    except Exception as exc:
        print(f"WARN scanner rebuild after fundamentals: {exc}")
    con.close()
    compress_db(DB_PATH, ARCHIVE_PATH)
    print(f"IronCandle fundamentals refresh complete: {updated} rows processed.")

if __name__ == "__main__":
    main()
