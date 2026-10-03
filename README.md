# IronCandle — Trading OS

**Scan. Risk. Journal. Review.**

IronCandle is a personal trading command center for a NIFTY 500 breakout strategy with a dashboard-only workflow.

## Strategy engine

### Technical

- NIFTY 500 universe
- Monthly ATH: last completed monthly candle closes above the prior all-time monthly high
- Daily breakout: latest daily close above the prior 252-trading-day (52-week) high
- Close above daily EMA 10 / 25 / 50 / 100 / 200
- Near latest completed weekly EMA30; default threshold ±3%, dashboard-editable
- Stock 52-week breakout date earlier than NIFTY 50's latest 52-week breakout date
- RS60: stock 60-session return minus NIFTY 50 60-session return

### Fundamental core

- Market cap ₹5,000–₹25,000 Cr
- Latest annual net profit YoY growth ≥25%
- Debt/equity <0.5 for non-financial sectors

### Optional quality overlay

- ROE ≥15%
- Annual sales growth ≥10%
- Latest annual operating cash flow positive

Financial-sector names are flagged because debt/equity is not directly comparable with ordinary operating companies.

## Data architecture

```text
Official NSE EOD files ──┐
                         ├─> Python scanner ─> scanner.json ─> static dashboard
Screener fundamentals ──┘        │
                                 └─> SQLite market DB ─gzip─> Google Drive archive

Supabase ───────────────> cloud trading journal / auth / analytics
GitHub Actions ─────────> daily + weekly automation
```

The baseline design intentionally avoids a paid live-market API.

See `docs/SETUP.md` for the exact setup steps and GitHub secrets.
