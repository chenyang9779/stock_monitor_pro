# Stock Monitor Pro

A local FastAPI + SQLite stock portfolio monitor with account-based portfolios, watchlists, alerts, news, and portfolio analytics.

## Features

- Multi-account login/register/logout
- Per-account holdings, watchlist, alerts, news, portfolio snapshots, and settings
- Per-account Finnhub API key stored in Account Settings
- Dashboard with portfolio value, cost basis, P&L, market indices, holdings, alerts, and recent news
- Portfolio analysis with allocation, sector allocation, risk metrics, beta, volatility, dividends, and correlation table
- CSV export for portfolio holdings
- Dark/light theme toggle

## Data Sources

The app uses multiple market data sources:

- Finnhub for live quotes, search, company metrics, and news
- Yahoo chart direct API as a historical-price fallback
- Stooq as another historical fallback when available
- yfinance as the final fallback for historical/company data

Finnhub free plans do not include some premium historical candle endpoints. In that case the app falls back automatically.

## Requirements

- Python 3.12 recommended
- Windows PowerShell commands below assume you are running from the project folder

Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Run Locally

From the project folder:

```powershell
python -c "from database import init_db; init_db()"
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Run With Docker

Build the image:

```powershell
docker build -t stock-monitor-pro .
```

Run with a named volume so `stocks.db` persists across container restarts:

```powershell
docker volume create stock-monitor-data
docker run --rm -p 8000:8000 -v stock-monitor-data:/app stock-monitor-pro
```

Open:

```text
http://127.0.0.1:8000
```

Alternative bind mount for the database file only:

```powershell
docker run --rm -p 8000:8000 -v ${PWD}\stocks.db:/app/stocks.db stock-monitor-pro
```

The container runs database migrations automatically on startup.

## Default Account

Existing local data is migrated to a default account:

```text
Username: default
Password: changeme
```

After first login, go to Settings and change the username/password.

## Finnhub API Key

Finnhub keys are no longer hardcoded globally. Each account has its own API key.

To set it:

1. Log in.
2. Open Settings.
3. Paste your Finnhub API key under Market Data API.
4. Save Settings.

New accounts start without an API key.

## Account Settings

Settings page includes:

- Change username
- Change password
- Save Finnhub API key for the current account

All account data is isolated by user.

## Database

The app stores data in:

```text
stocks.db
```

Important tables:

- `users`
- `holdings`
- `watchlist`
- `price_alerts`
- `news`
- `settings`
- `portfolio_snapshots`

Running `init_db()` applies migrations automatically.

## API Notes

Most `/api/*` endpoints require login. If not logged in, they return:

```text
401 Not authenticated
```

Auth endpoints:

- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `PUT /api/auth/username`
- `PUT /api/auth/password`

Settings endpoints:

- `GET /api/settings`
- `PUT /api/settings`

## Common Issues

### Finnhub `stock/candle` returns 403

This is expected on Finnhub free plans:

```text
You don't have access to this resource.
```

The app falls back to Yahoo chart direct, Stooq, then yfinance.

### yfinance says Too Many Requests

yfinance can rate-limit. The app pauses yfinance fallback for a cooldown period to avoid log spam.

### Correlation/risk/volatility is empty

Those sections need historical prices. If all fallback sources fail or are rate-limited, those sections may temporarily be empty.

### Portfolio chart dropped to zero

Old invalid snapshots with nonzero cost and zero value are filtered from the history API. New invalid zero-value snapshots are no longer saved.

## Development Checks

Run Python syntax checks:

```powershell
python -m py_compile app.py database.py models.py services.py
```

Run JavaScript syntax check:

```powershell
node --check static\js\app.js
```

## Project Structure

```text
app.py                 FastAPI routes, auth middleware, app startup
database.py            SQLite schema and migrations
models.py              Pydantic request/response models
services.py            Market data, analytics, risk, dividend, export helpers
Dockerfile             Docker image definition
templates/index.html   Main UI
static/js/app.js       Frontend app logic
static/css/style.css   UI styles
requirements.txt       Python dependencies
stocks.db              Local SQLite database
```
