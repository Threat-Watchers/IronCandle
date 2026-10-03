# IronCandle — Data/API Requirements

## Required for the baseline production system

### GitHub
A GitHub repository is needed for source code, GitHub Actions and the static dashboard deployment.

### Google Drive / Google Cloud OAuth
Used only for the compressed SQLite market-history archive.

Create a Google Cloud project, enable Google Drive API, create a Desktop OAuth client, then run:

```bash
python scripts/google_oauth_bootstrap.py --client-json client_secret.json
```

Copy the output into GitHub Actions Secrets:

```text
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REFRESH_TOKEN
GOOGLE_DRIVE_FOLDER_ID
```

The automation only manages the `IronCandle_Backups` folder/files it creates with the per-file `drive.file` scope.

### Supabase
Used for the private cloud trading journal and authentication.

Required browser configuration:

```text
supabaseUrl
supabaseAnonKey / publishable key
```

Run `supabase-schema.sql` first. Keep RLS enabled and never use a service-role key in the browser.

## No key required

### NSE
No API key is required for the baseline. IronCandle downloads official public NSE EOD files:

- UDiFF final equity bhavcopy ZIP
- daily index-close CSV
- NIFTY 500 constituent CSV

### BSE
No key is required if we add the BSE adapter later. It is not required for the NIFTY 500 baseline because the strategy universe is NSE/Nifty-based.

### Screener.in
No official API key is used. IronCandle reads public company pages at a low weekly rate and extracts annual P&L, balance sheet, cash flow and market-cap data. This is deliberately isolated as a replaceable adapter because website markup or access rules can change.

### Chartink
No Chartink API key is required. Chartink is optional validation/reference only; IronCandle calculates its own technical indicators so the dashboard is not dependent on an undocumented scraper.

## One-time historical bootstrap

The first bootstrap uses Yahoo Finance/yfinance because downloading years of official NSE daily archive files one-by-one would be unnecessarily large. It does not need a Yahoo API key. After bootstrap, the scheduled daily refresh uses official NSE EOD data.

## Optional future integrations

### Broker
Dhan/Zerodha API credentials if you later want:

- live positions
- order history
- executed orders
- broker-side P&L reconciliation

### OpenAI
An OpenAI API key is optional only if you want AI-powered trade review, journal summaries or natural-language queries later. It is not required for scanning, risk management or journaling.
