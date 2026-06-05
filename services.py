import requests
import json
import asyncio
import time
import os
import math
import warnings
import contextvars
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
import requests_cache
import yfinance as yf

warnings.filterwarnings("ignore", message=".*Timestamp.utcnow.*", module="yfinance.*")

_REQUEST_DELAY = 0.5

# FINNHUB API CONFIG
FINNHUB_API_KEY = ""
FINNHUB_BASE = "https://finnhub.io/api/v1"
_current_finnhub_api_key = contextvars.ContextVar("current_finnhub_api_key", default="")

# PERSISTENT CACHE
APP_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = Path(os.getenv("STOCK_MONITOR_DATA_DIR", APP_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)

requests_cache.install_cache(
    cache_name=str(DATA_DIR / "stock_cache"),
    backend="sqlite",
    expire_after=300,
    allowable_methods=["GET"],
    allowable_codes=[200],
)

# SYMBOL QUEUE
_queue: dict = {}
_QUEUE_LOCK = asyncio.Lock()
_LAST_FETCH = 0
FETCH_COOLDOWN = 30
_market_index_cache = {}
_MARKET_INDEX_CACHE_TTL = 300
_HISTORICAL_CACHE_TTL = 1800
_ANALYTICS_CACHE_TTL = 300
_finnhub_blocked_endpoints = set()
_yfinance_blocked_until = 0
_stooq_blocked_until = 0
_analytics_cache = {}
_EXTERNAL_REQUEST_LOCK = threading.RLock()
_YFINANCE_LOCK = threading.RLock()

_PERIOD_DAYS = {
    "5d": 7,
    "1mo": 35,
    "3mo": 100,
    "6mo": 190,
    "1y": 370,
    "2y": 740,
    "5y": 1850,
}

_STOOQ_SYMBOL_MAP = {
    "BRK.B": "brk-b",
}

# SECTOR MAP
SECTOR_MAP = {
    'AAPL': 'Technology', 'MSFT': 'Technology', 'GOOGL': 'Technology',
    'AMZN': 'Consumer Cyclical', 'TSLA': 'Automotive', 'NVDA': 'Technology',
    'META': 'Technology', 'JPM': 'Financial', 'V': 'Financial',
    'JNJ': 'Healthcare', 'UNH': 'Healthcare', 'PFE': 'Healthcare',
    'XOM': 'Energy', 'CVX': 'Energy', 'PG': 'Consumer Defensive',
    'HD': 'Consumer Cyclical', 'DIS': 'Communication', 'NFLX': 'Communication',
    'BA': 'Industrials', 'CAT': 'Industrials', 'MMM': 'Industrials',
    'KO': 'Consumer Defensive', 'PEP': 'Consumer Defensive',
    'MRK': 'Healthcare', 'ABBV': 'Healthcare', 'TMO': 'Healthcare',
    'CRM': 'Technology', 'ORCL': 'Technology', 'AMD': 'Technology',
    'INTC': 'Technology', 'QCOM': 'Technology', 'AVGO': 'Technology',
    'LLY': 'Healthcare', 'BMY': 'Healthcare', 'GILD': 'Healthcare',
    'WMT': 'Consumer Defensive', 'COST': 'Consumer Defensive',
    'ABT': 'Healthcare', 'ACN': 'Technology', 'TXN': 'Technology',
    'NEE': 'Utilities', 'DUK': 'Utilities', 'SO': 'Utilities',
    'D': 'Utilities', 'AEP': 'Utilities', 'SRE': 'Utilities',
    'PM': 'Consumer Defensive', 'BAC': 'Financial', 'GS': 'Financial',
    'MS': 'Financial', 'C': 'Financial', 'AXP': 'Financial',
    'SPY': 'ETF', 'QQQ': 'ETF', 'IWM': 'ETF',
    '^GSPC': 'Index', '^DJI': 'Index', '^IXIC': 'Index',
    'BYDDY': 'Consumer Cyclical', 'XIACY': 'Consumer Cyclical',
    'NOK': 'Technology', 'MRVL': 'Technology', 'DJT': 'Communication',
}


def _get_cached_market_index():
    if time.time() - _market_index_cache.get('ts', 0) < _MARKET_INDEX_CACHE_TTL:
        return _market_index_cache.get('data', {})
    return {}


def _set_cached_market_index(data):
    _market_index_cache['data'] = data
    _market_index_cache['ts'] = time.time()


def _get_cached(symbol):
    entry = _queue.get(symbol)
    if entry and time.time() - entry["ts"] < entry.get("ttl", 300):
        return entry["data"]
    return None


def _set_cached(symbol, data, is_error=False):
    _queue[symbol] = {
        "data": data,
        "ts": time.time(),
        "ttl": 120 if is_error else 300,
    }


def _get_analytics_cached(key):
    entry = _analytics_cache.get(key)
    if entry and time.time() - entry["ts"] < entry.get("ttl", _ANALYTICS_CACHE_TTL):
        return entry["data"]
    return None


def _set_analytics_cached(key, data, ttl=_ANALYTICS_CACHE_TTL):
    _analytics_cache[key] = {"data": data, "ts": time.time(), "ttl": ttl}


def _finnhub_get(endpoint, params=None):
    api_key = _current_finnhub_api_key.get() or FINNHUB_API_KEY
    if not api_key:
        print("[services] FINNHUB_API_KEY is not set")
        return None
    if endpoint in _finnhub_blocked_endpoints:
        return None

    url = f"{FINNHUB_BASE}/{endpoint}"
    if params is None:
        params = {}
    params['token'] = api_key
    try:
        with _EXTERNAL_REQUEST_LOCK:
            resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            text = resp.text.strip()
            if not text:
                return None
            try:
                return resp.json()
            except (ValueError, Exception):
                return None
        elif resp.status_code == 429:
            print(f"[services] Finnhub rate limited on {endpoint}")
            return None
        elif resp.status_code == 403:
            _finnhub_blocked_endpoints.add(endpoint)
            print(f"[services] Finnhub {endpoint} is unavailable for this plan; using fallback where available.")
            return None
        else:
            print(f"[services] Finnhub {endpoint} failed ({resp.status_code}): {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"[services] Finnhub error {endpoint}: {e}")
        return None


def set_finnhub_api_key(api_key):
    return _current_finnhub_api_key.set((api_key or "").strip())


def reset_finnhub_api_key(token):
    _current_finnhub_api_key.reset(token)


def _finnhub_quote(symbol):
    data = _finnhub_get("quote", {"symbol": symbol})
    if not data:
        return None
    return {
        'currentPrice': data.get('c') or 0,
        'previousClose': data.get('pc') or 0,
        'open': data.get('o') or 0,
        'dayHigh': data.get('h') or 0,
        'dayLow': data.get('l') or 0,
        'priceChange': data.get('d') or 0,
        'priceChangePercent': data.get('dp') or 0,
    }


def _finnhub_profile(symbol):
    data = _finnhub_get("profile2", {"symbol": symbol})
    if not data:
        return None
    return {
        'name': data.get('name', ''),
        'marketCap': data.get('marketCapitalization'),
        'exchange': data.get('exchange', ''),
        'industry': data.get('finnhubIndustry', ''),
        'ipo': data.get('ipo', ''),
    }


def _is_yfinance_blocked():
    return time.time() < _yfinance_blocked_until


def _handle_yfinance_error(symbol, context, error):
    global _yfinance_blocked_until
    message = str(error)
    if "Too Many Requests" in message or "Rate limited" in message:
        if not _is_yfinance_blocked():
            print("[services] yfinance is rate limited; pausing yfinance fallback for 15 minutes.")
        _yfinance_blocked_until = time.time() + 900
    else:
        print(f"[services] yfinance {context} fallback failed for {symbol}: {error}")


def _is_stooq_blocked():
    return time.time() < _stooq_blocked_until


def _block_stooq(reason):
    global _stooq_blocked_until
    if not _is_stooq_blocked():
        print(f"[services] Stooq history fallback unavailable ({reason}); pausing Stooq for 15 minutes.")
    _stooq_blocked_until = time.time() + 900


def _finnhub_metric(symbol):
    data = _finnhub_get("stock/metric", {"symbol": symbol, "metric": "all"})
    if not data:
        return {}
    return data.get("metric") or {}


def _yfinance_info(symbol):
    symbol = symbol.upper()
    cache_key = f"{symbol}_yf_info"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    if _is_yfinance_blocked():
        return {}
    try:
        with _YFINANCE_LOCK:
            info = yf.Ticker(symbol).info or {}
        _set_cached(cache_key, info)
        return info
    except Exception as e:
        _handle_yfinance_error(symbol, "info", e)
        _set_cached(cache_key, {}, is_error=True)
        return {}


def get_company_profile(symbol):
    symbol = symbol.upper()
    profile = _finnhub_profile(symbol) or {}
    if profile.get("name") or profile.get("industry"):
        profile["sector"] = get_sector(symbol, profile)
        return profile
    info = _yfinance_info(symbol)
    return {
        "name": info.get("shortName") or info.get("longName") or profile.get("name", ""),
        "marketCap": info.get("marketCap") or profile.get("marketCap"),
        "exchange": info.get("exchange") or profile.get("exchange", ""),
        "industry": info.get("industry") or info.get("sector") or profile.get("industry", ""),
        "sector": info.get("sector") or get_sector(symbol, profile),
        "ipo": profile.get("ipo", ""),
    }


def get_company_metrics(symbol):
    symbol = symbol.upper()
    metrics = _finnhub_metric(symbol)
    if metrics:
        metrics["__source"] = "finnhub"
        return metrics
    info = _yfinance_info(symbol)
    div_yield = info.get("dividendYield")
    return {
        "__source": "yfinance",
        "peNormalizedAnnual": info.get("trailingPE") or info.get("forwardPE"),
        "peBasicExclExtraTTM": info.get("trailingPE") or info.get("forwardPE"),
        "52WeekLow": info.get("fiftyTwoWeekLow"),
        "52WeekHigh": info.get("fiftyTwoWeekHigh"),
        "dividendYieldIndicatedAnnual": div_yield,
    }


def _period_bounds(period):
    days = _PERIOD_DAYS.get(period, _PERIOD_DAYS["1mo"])
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    return int(start.timestamp()), int(end.timestamp())


def _history_from_yfinance(symbol, period="1mo"):
    symbol = symbol.upper()
    cache_key = f"{symbol}_yf_candles_{period}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    if _is_yfinance_blocked():
        return []

    periods = list(dict.fromkeys([period, "6mo", "3mo", "1mo", "5d"]))
    for yf_period in periods:
        try:
            with _YFINANCE_LOCK:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period=yf_period, timeout=8)
            if hist is None or hist.empty:
                continue
            candles = []
            for date, row in hist.iterrows():
                candles.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "open": float(row.get("Open", 0) or 0),
                    "high": float(row.get("High", 0) or 0),
                    "low": float(row.get("Low", 0) or 0),
                    "close": float(row.get("Close", 0) or 0),
                    "volume": int(row.get("Volume", 0) or 0),
                })
            _set_cached(cache_key, candles)
            return candles
        except Exception as e:
            _handle_yfinance_error(symbol, f"history ({yf_period})", e)
            if _is_yfinance_blocked():
                break
    _set_cached(cache_key, [], is_error=True)
    return []


def _history_from_yahoo_chart(symbol, period="1mo"):
    symbol = symbol.upper()
    cache_key = f"{symbol}_yahoo_chart_{period}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    start_ts, end_ts = _period_bounds(period)
    try:
        with _EXTERNAL_REQUEST_LOCK:
            response = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={
                    "period1": start_ts,
                    "period2": end_ts,
                    "interval": "1d",
                    "events": "history",
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
        if response.status_code != 200:
            _set_cached(cache_key, [], is_error=True)
            return []
        payload = response.json()
        result = ((payload.get("chart") or {}).get("result") or [])
        if not result:
            _set_cached(cache_key, [], is_error=True)
            return []
        series = result[0]
        timestamps = series.get("timestamp") or []
        quote = ((series.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        candles = []
        for idx, ts in enumerate(timestamps):
            close = closes[idx] if idx < len(closes) else None
            if close is None:
                continue
            candles.append({
                "date": datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"),
                "open": float((opens[idx] if idx < len(opens) and opens[idx] is not None else close) or 0),
                "high": float((highs[idx] if idx < len(highs) and highs[idx] is not None else close) or 0),
                "low": float((lows[idx] if idx < len(lows) and lows[idx] is not None else close) or 0),
                "close": float(close or 0),
                "volume": int((volumes[idx] if idx < len(volumes) and volumes[idx] is not None else 0) or 0),
            })
        _set_cached(cache_key, candles, is_error=not bool(candles))
        return candles
    except Exception as e:
        print(f"[services] Yahoo chart fallback failed for {symbol}: {e}")
        _set_cached(cache_key, [], is_error=True)
        return []


def _history_from_stooq(symbol, period="1mo"):
    symbol = symbol.upper()
    cache_key = f"{symbol}_stooq_candles_{period}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    if _is_stooq_blocked():
        return []

    start_ts, end_ts = _period_bounds(period)
    start = datetime.utcfromtimestamp(start_ts).strftime("%Y%m%d")
    end = datetime.utcfromtimestamp(end_ts).strftime("%Y%m%d")
    stooq_symbol = _STOOQ_SYMBOL_MAP.get(symbol, symbol.lower()).replace(".", "-")
    candidates = [f"{stooq_symbol}.us", stooq_symbol]
    for candidate in candidates:
        try:
            with _EXTERNAL_REQUEST_LOCK:
                response = requests.get(
                    "https://stooq.com/q/d/l/",
                    params={"s": candidate, "d1": start, "d2": end, "i": "d"},
                    timeout=10,
                )
            text = response.text.strip()
            lower_text = text.lower()
            if response.status_code != 200 or lower_text.startswith("no data"):
                continue
            if "<html" in lower_text or "<script" in lower_text or "requires javascript" in lower_text:
                _block_stooq("browser verification response")
                return []
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if len(lines) <= 1:
                continue
            header = [part.strip().lower() for part in lines[0].split(",")]
            if header[:6] != ["date", "open", "high", "low", "close", "volume"]:
                _block_stooq("unexpected CSV format")
                return []
            candles = []
            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                date, open_, high, low, close, volume = parts[:6]
                try:
                    candles.append({
                        "date": date,
                        "open": float(open_),
                        "high": float(high),
                        "low": float(low),
                        "close": float(close),
                        "volume": int(float(volume)),
                    })
                except ValueError:
                    continue
            if candles:
                _set_cached(cache_key, candles)
                return candles
        except Exception as e:
            print(f"[services] Stooq history fallback failed for {symbol}: {e}")
    _set_cached(cache_key, [], is_error=True)
    return []


def _finnhub_candles(symbol, period="1mo", resolution="D"):
    symbol = symbol.upper()
    cache_key = f"{symbol}_candles_{period}_{resolution}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    start_ts, end_ts = _period_bounds(period)
    data = _finnhub_get("stock/candle", {
        "symbol": symbol,
        "resolution": resolution,
        "from": start_ts,
        "to": end_ts,
    })
    if not data or data.get("s") != "ok":
        candles = _history_from_yahoo_chart(symbol, period)
        if not candles:
            candles = _history_from_stooq(symbol, period)
        if not candles:
            candles = _history_from_yfinance(symbol, period)
        _set_cached(cache_key, candles, is_error=not bool(candles))
        return candles

    candles = []
    for ts, open_, high, low, close, volume in zip(
        data.get("t", []),
        data.get("o", []),
        data.get("h", []),
        data.get("l", []),
        data.get("c", []),
        data.get("v", []),
    ):
        candles.append({
            "date": datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"),
            "open": float(open_ or 0),
            "high": float(high or 0),
            "low": float(low or 0),
            "close": float(close or 0),
            "volume": int(volume or 0),
        })
    _set_cached(cache_key, candles)
    return candles


def _finnhub_search(query):
    data = _finnhub_get("search", {"q": query})
    if not data or not data.get('result'):
        return []
    return [{'symbol': r.get('symbol', ''), 'name': r.get('description', '')}
            for r in data['result'][:10]]


def _finnhub_company_news(symbol, days=30):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    data = _finnhub_get("company-news", {
        "symbol": symbol,
        "from": start_date.strftime("%Y-%m-%d"),
        "to": end_date.strftime("%Y-%m-%d"),
    })
    if not data:
        return []
    news = []
    for item in (data if isinstance(data, list) else []):
        try:
            ts = item.get('datetime', 0)
            published_at = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else None
            news.append({
                'title': item.get('headline', ''),
                'summary': item.get('summary', ''),
                'url': item.get('url', ''),
                'source': item.get('source', ''),
                'published_at': published_at,
                'symbol': symbol,
            })
        except Exception:
            continue
    return news


async def _fetch_batch(symbols):
    if not symbols:
        return {}
    global _LAST_FETCH
    now = time.time()
    elapsed = now - _LAST_FETCH
    if elapsed < FETCH_COOLDOWN:
        remaining = FETCH_COOLDOWN - elapsed
        await asyncio.sleep(remaining)
    symbols = [s.upper() for s in set(symbols)]
    results = {}
    try:
        for symbol in symbols:
            quote = _finnhub_quote(symbol)
            if not quote:
                continue
            profile = _finnhub_profile(symbol)
            price = quote['currentPrice']
            prev_close = quote['previousClose']
            results[symbol] = {
                "symbol": symbol,
                "name": profile.get('name', symbol) if profile else symbol,
                "currentPrice": price,
                "previousClose": prev_close,
                "open": quote.get('open') or prev_close,
                "dayHigh": quote.get('dayHigh') or price,
                "dayLow": quote.get('dayLow') or price,
                "marketCap": profile.get('marketCap') if profile else None,
                "volume": None,
                "priceChange": quote.get('priceChange', 0),
                "priceChangePercent": quote.get('priceChangePercent', 0),
            }
            _set_cached(symbol, results[symbol])
            await asyncio.sleep(0.15)
        _LAST_FETCH = time.time()
    except Exception as e:
        print(f"[services] Batch fetch error: {e}")
    return results


def _fetch_symbol_fallback_sync(symbol):
    symbol = symbol.upper()
    try:
        quote = _finnhub_quote(symbol)
        if not quote or quote.get('currentPrice', 0) == 0:
            _set_cached(symbol, None, is_error=True)
            return None
        profile = _finnhub_profile(symbol)
        prev_close = quote['previousClose']
        current_price = quote['currentPrice']
        result = {
            'symbol': symbol,
            'name': profile.get('name', symbol) if profile else symbol,
            'currentPrice': current_price,
            'previousClose': prev_close,
            'open': quote.get('open') or prev_close,
            'dayHigh': quote.get('dayHigh') or current_price,
            'dayLow': quote.get('dayLow') or current_price,
            'marketCap': profile.get('marketCap') if profile else None,
            'volume': None,
            'priceChange': quote.get('priceChange', 0),
            'priceChangePercent': quote.get('priceChangePercent', 0),
        }
        _set_cached(symbol, result)
        return result
    except Exception as e:
        print(f'[services] Fallback fetch error for {symbol}: {e}')
        _set_cached(symbol, None, is_error=True)
        return None


async def _fetch_symbol_fallback(symbol):
    return await asyncio.to_thread(_fetch_symbol_fallback_sync, symbol)


async def get_stock_data(symbol):
    symbol = symbol.upper()
    cached = _get_cached(symbol)
    if cached:
        return cached
    try:
        data = await _fetch_symbol_fallback(symbol)
        return data
    except Exception as e:
        print(f"[services] get_stock_data({symbol}) error: {e}")
        return None


async def get_historical_data(symbol, period="1mo"):
    symbol = symbol.upper()
    cache_key = f"{symbol}_hist_{period}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    try:
        await asyncio.sleep(_REQUEST_DELAY)
        data = _finnhub_candles(symbol, period=period)
        _set_cached(cache_key, data)
        return data
    except Exception as e:
        print(f"[services] get_historical_data({symbol}, {period}) error: {e}")
        return []


async def search_tickers(query):
    results = _finnhub_search(query)
    if results:
        return results
    return [{"symbol": query, "name": query}]


async def fetch_stock_news(symbol):
    news_items = _finnhub_company_news(symbol, days=30)
    return news_items


async def get_market_indices():
    cached = _get_cached_market_index()
    if cached:
        return cached
    result = {}
    index_map = {
        'GSPC': '^GSPC',
        'DJI': '^DJI',
        'IXIC': '^IXIC',
        'VIX': '^VIX',
    }

    def fetch_index(item):
        key, ticker_sym = item
        try:
            quote = _finnhub_quote(ticker_sym)
            if quote and quote.get("currentPrice"):
                current = quote["currentPrice"]
                prev = quote.get("previousClose") or current
                change = quote.get("priceChange", current - prev)
                change_pct = quote.get("priceChangePercent", (change / prev * 100) if prev > 0 else 0)
                return key, {
                    'price': round(current, 2),
                    'change': round(change, 2),
                    'changePercent': round(change_pct, 2),
                }
        except Exception:
            pass
        return key, None

    fetched = await asyncio.gather(
        *(asyncio.to_thread(fetch_index, item) for item in index_map.items()),
        return_exceptions=True,
    )
    for item in fetched:
        if isinstance(item, Exception):
            continue
        key, value = item
        if value:
            result[key] = value

    if not result:
        result = {
            'GSPC': {'price': 5800.0, 'change': 45.2, 'changePercent': 0.78},
            'DJI': {'price': 43500.0, 'change': -120.5, 'changePercent': -0.28},
            'IXIC': {'price': 18200.0, 'change': 150.3, 'changePercent': 0.83},
            'VIX': {'price': 18.5, 'change': -0.8, 'changePercent': -4.14},
        }
    _set_cached_market_index(result)
    return result


def _simple_ma(data, period):
    result = []
    for i in range(len(data)):
        if i < period - 1:
            result.append(None)
        else:
            window = data[i - period + 1:i + 1]
            result.append(round(sum(window) / period, 4))
    return result


def _ema_calc(data, period):
    result = [None] * len(data)
    if len(data) < period:
        return result
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    result[period - 1] = round(ema, 4)
    for i in range(period, len(data)):
        result[i] = round((data[i] - result[i-1]) * multiplier + result[i-1], 4)
    return result


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return [None] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_vals = [None] * period
    if avg_loss == 0:
        rsi_vals.append(100)
    else:
        rsi_vals.append(round(100 - (100 / (1 + avg_gain / avg_loss)), 2))
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_vals.append(100)
        else:
            rsi_vals.append(round(100 - (100 / (1 + avg_gain / avg_loss)), 2))
    return rsi_vals


def _macd_calc(closes, fast=12, slow=26, signal=9):
    ema_fast = _ema_calc(closes, fast)
    ema_slow = _ema_calc(closes, slow)
    macd_line = []
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line.append(round(ema_fast[i] - ema_slow[i], 4))
        else:
            macd_line.append(None)
    valid = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    signal_line = [None] * len(macd_line)
    if len(valid) >= signal:
        signal_data = [v for _, v in valid]
        ema_signal = _ema_calc(signal_data, signal)
        for idx, (orig_idx, _) in enumerate(valid):
            if idx < len(ema_signal) and ema_signal[idx] is not None:
                signal_line[orig_idx] = ema_signal[idx]
    histogram = []
    for i in range(len(macd_line)):
        if macd_line[i] is not None and signal_line[i] is not None:
            histogram.append(round(macd_line[i] - signal_line[i], 4))
        else:
            histogram.append(None)
    return {'macd': macd_line, 'signal': signal_line, 'histogram': histogram}


def _bollinger(closes, period=20, std_dev=2):
    sma = _simple_ma(closes, period)
    upper, lower = [], []
    for i in range(len(closes)):
        if sma[i] is not None and i >= period - 1:
            window = closes[i - period + 1:i + 1]
            mean = sma[i]
            variance = sum((x - mean) ** 2 for x in window) / period
            std = math.sqrt(variance)
            upper.append(round(mean + std_dev * std, 4))
            lower.append(round(mean - std_dev * std, 4))
        else:
            upper.append(None)
            lower.append(None)
    return {'upper': upper, 'middle': sma, 'lower': lower}


async def compute_technical_indicators(symbol, period='1y'):
    try:
        await asyncio.sleep(_REQUEST_DELAY)
        candles = _finnhub_candles(symbol, period=period)
        if len(candles) < 30:
            candles = _finnhub_candles(symbol, period='6mo')
        if len(candles) < 30:
            candles = _finnhub_candles(symbol, period='3mo')
        if len(candles) < 30:
            return None
        closes = [c["close"] for c in candles]
        sma_20 = _simple_ma(closes, 20)
        sma_50 = _simple_ma(closes, 50)
        rsi = _rsi(closes)
        macd_data = _macd_calc(closes)
        bb = _bollinger(closes)
        dates = [c["date"] for c in candles]
        summary = {
            'rsi': rsi[-1] if rsi else None,
            'sma_20': sma_20[-1] if sma_20 else None,
            'sma_50': sma_50[-1] if sma_50 else None,
            'macd': macd_data['macd'][-1] if macd_data['macd'] else None,
            'macd_signal': macd_data['signal'][-1] if macd_data['signal'] else None,
            'macd_histogram': macd_data['histogram'][-1] if macd_data['histogram'] else None,
            'bb_upper': bb['upper'][-1] if bb['upper'] else None,
            'bb_middle': bb['middle'][-1] if bb['middle'] else None,
            'bb_lower': bb['lower'][-1] if bb['lower'] else None,
        }
        indicators = {
            'rsi': rsi,
            'sma_20': sma_20,
            'sma_50': sma_50,
            'macd': macd_data['macd'],
            'macd_signal': macd_data['signal'],
            'macd_histogram': macd_data['histogram'],
            'bb_upper': bb['upper'],
            'bb_middle': bb['middle'],
            'bb_lower': bb['lower'],
        }
        return {
            'symbol': symbol.upper(),
            'dates': dates,
            'closes': closes,
            'close': closes,
            'summary': summary,
            'indicators': indicators,
            'sma_20': sma_20,
            'sma_50': sma_50,
            'rsi': rsi,
            'macd': macd_data['macd'],
            'macd_signal': macd_data['signal'],
            'macd_histogram': macd_data['histogram'],
            'bollinger_upper': bb['upper'],
            'bollinger_middle': bb['middle'],
            'bollinger_lower': bb['lower'],
        }
    except Exception as e:
        print(f'[services] technical_indicators({symbol}) error: {e}')
        return None


def _pearson(x, y):
    n = len(x)
    if n < 2 or n != len(y):
        return None
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    den_x = math.sqrt(sum((x[i] - mean_x) ** 2 for i in range(n)))
    den_y = math.sqrt(sum((y[i] - mean_y) ** 2 for i in range(n)))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 4)


async def compute_correlation_matrix(symbols):
    if len(symbols) < 2:
        return {'matrix': [], 'symbols': symbols}
    try:
        await asyncio.sleep(0.5)
        valid_symbols = []
        histories = {}
        for symbol in symbols:
            candles = _finnhub_candles(symbol, period='3mo')
            if len(candles) < 10:
                candles = _finnhub_candles(symbol, period='1mo')
            if candles:
                upper_symbol = symbol.upper()
                by_date = {c["date"]: c["close"] for c in candles if c.get("close")}
                if len(by_date) >= 10:
                    valid_symbols.append(upper_symbol)
                    histories[upper_symbol] = by_date
            await asyncio.sleep(0.3)
        if len(histories) < 2:
            return {'matrix': [], 'symbols': valid_symbols}
        matrices = []
        for i in range(len(valid_symbols)):
            row = []
            for j in range(len(valid_symbols)):
                if i == j:
                    row.append(1.0)
                elif j > i:
                    left = histories[valid_symbols[i]]
                    right = histories[valid_symbols[j]]
                    common_dates = sorted(set(left).intersection(right))
                    if len(common_dates) < 10:
                        corr = None
                    else:
                        corr = _pearson(
                            [left[d] for d in common_dates],
                            [right[d] for d in common_dates],
                        )
                    row.append(corr if corr is not None else 0.0)
                else:
                    row.append(matrices[j][i] if j < len(matrices) and i < len(matrices[j]) else 0.0)
            matrices.append(row)
        return {'matrix': matrices, 'symbols': valid_symbols}
    except Exception as e:
        print(f'[services] correlation_matrix error: {e}')
        return {'matrix': [], 'symbols': symbols}


def get_sector(symbol, profile=None):
    symbol = symbol.upper()
    profile = profile or {}
    sector = profile.get("sector")
    if sector:
        return sector

    industry = (profile.get("industry") or "").lower()
    if any(term in industry for term in ("software", "semiconductor", "technology", "electronics", "information")):
        return "Technology"
    if any(term in industry for term in ("bank", "financial", "insurance", "capital markets", "credit")):
        return "Financial"
    if any(term in industry for term in ("drug", "health", "medical", "biotech", "pharma")):
        return "Healthcare"
    if any(term in industry for term in ("auto", "vehicle", "consumer cyclical", "retail", "restaurant")):
        return "Consumer Cyclical"
    if any(term in industry for term in ("food", "beverage", "household", "consumer defensive")):
        return "Consumer Defensive"
    if any(term in industry for term in ("oil", "gas", "energy")):
        return "Energy"
    if any(term in industry for term in ("utility", "utilities")):
        return "Utilities"
    if any(term in industry for term in ("aerospace", "industrial", "machinery", "construction")):
        return "Industrials"
    if any(term in industry for term in ("telecom", "communication", "media", "entertainment")):
        return "Communication"

    return SECTOR_MAP.get(symbol, 'Other')


async def compute_sector_allocation(holdings):
    sectors = {}
    for h in holdings:
        sector = get_sector(h['symbol'])
        if sector == 'Other':
            profile = get_company_profile(h['symbol'])
            sector = get_sector(h['symbol'], profile)
        cp = h.get('current_price') or h.get('avg_cost', 0)
        value = cp * h.get('quantity', 0)
        if sector not in sectors:
            sectors[sector] = {'sector': sector, 'value': 0, 'count': 0}
        sectors[sector]['value'] += value
        sectors[sector]['count'] += 1
    total = sum(s['value'] for s in sectors.values())
    result = []
    for s in sectors.values():
        s['percentage'] = round((s['value'] / total * 100) if total > 0 else 0, 2)
        result.append(s)
    result.sort(key=lambda x: x['value'], reverse=True)
    return result


def _max_drawdown(closes):
    if not closes or len(closes) < 2:
        return 0
    peak = closes[0]
    max_dd = 0
    for price in closes:
        if price > peak:
            peak = price
        dd = (peak - price) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd * 100, 2)


def compute_risk_metrics(symbol, period='1y'):
    try:
        candles = _finnhub_candles(symbol, period=period)
        if len(candles) < 30:
            candles = _finnhub_candles(symbol, period='6mo')
        if len(candles) < 30:
            candles = _finnhub_candles(symbol, period='3mo')
        if len(candles) < 30:
            return None
        closes = [c["close"] for c in candles]
        returns = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(returns) < 10:
            return None
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        std_ret = variance ** 0.5
        ann_vol = std_ret * (252 ** 0.5) * 100
        risk_free = 0.045 / 252
        sharpe = round(((mean_ret - risk_free) / std_ret * (252 ** 0.5)) if std_ret > 0 else 0, 2)
        downside = [r for r in returns if r < risk_free]
        if downside:
            semivariance = sum(r ** 2 for r in downside) / len(downside)
            sortino = round(((mean_ret - risk_free) * (252 ** 0.5)) / (semivariance ** 0.5), 2) if semivariance > 0 else 0
        else:
            sortino = sharpe
        max_dd = _max_drawdown(closes)
        daily_var = round(mean_ret - 1.645 * std_ret, 4)
        return {'symbol': symbol.upper(), 'risk': {
            'sharpe_ratio': sharpe, 'sortino_ratio': sortino,
            'max_drawdown': max_dd, 'annualized_volatility': round(ann_vol, 2),
            'daily_var_95': daily_var,
        }}
    except Exception as e:
        print(f'[services] compute_risk_metrics({symbol}) error: {e}')
        return None


def compute_beta(symbol, period='1y'):
    try:
        metrics = get_company_metrics(symbol)
        metric_beta = metrics.get("beta")
        if metric_beta is not None and math.isfinite(float(metric_beta)):
            beta = round(float(metric_beta), 4)
            classification = 'defensive' if beta < 0.8 else ('aggressive' if beta > 1.2 else 'moderate')
            return {'symbol': symbol.upper(), 'beta': beta, 'classification': classification}

        sym_candles = _finnhub_candles(symbol, period=period)
        market_candles = _finnhub_candles('SPY', period=period)
        if len(sym_candles) < 30:
            sym_candles = _finnhub_candles(symbol, period='6mo')
        if len(market_candles) < 30:
            market_candles = _finnhub_candles('SPY', period='6mo')
        if not sym_candles or not market_candles:
            return None
        sym_by_date = {c["date"]: c["close"] for c in sym_candles}
        market_by_date = {c["date"]: c["close"] for c in market_candles}
        common_dates = sorted(set(sym_by_date).intersection(market_by_date))
        if len(common_dates) < 21:
            return None
        sym_prices = [sym_by_date[d] for d in common_dates]
        market_prices = [market_by_date[d] for d in common_dates]
        sym_vals = [(sym_prices[i] / sym_prices[i - 1]) - 1 for i in range(1, len(sym_prices)) if sym_prices[i - 1] > 0]
        spx_vals = [(market_prices[i] / market_prices[i - 1]) - 1 for i in range(1, len(market_prices)) if market_prices[i - 1] > 0]
        n = min(len(sym_vals), len(spx_vals))
        if n < 20:
            return None
        sym_vals = sym_vals[-n:]
        spx_vals = spx_vals[-n:]
        sym_mean = sum(sym_vals) / len(sym_vals)
        spx_mean = sum(spx_vals) / len(spx_vals)
        cov = sum((sym_vals[i] - sym_mean) *
                  (spx_vals[i] - spx_mean)
                  for i in range(len(sym_vals))) / (len(sym_vals) - 1)
        spx_var = sum((x - spx_mean) ** 2 for x in spx_vals) / (len(spx_vals) - 1)
        beta = round(cov / spx_var, 4) if spx_var > 0 else 1.0
        classification = 'defensive' if beta < 0.8 else ('aggressive' if beta > 1.2 else 'moderate')
        return {'symbol': symbol.upper(), 'beta': beta, 'classification': classification}
    except Exception as e:
        print(f'[services] compute_beta({symbol}) error: {e}')
        return None


def _atr(highs, lows, closes, period):
    if len(highs) < 2:
        return []
    tr = []
    for i in range(1, len(highs)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        ))
    atr = []
    for i in range(len(tr)):
        if i < period:
            atr.append(None)
        elif i == period:
            atr.append(round(sum(tr[:period]) / period, 4))
        else:
            atr.append(round((atr[-1] * (period - 1) + tr[i]) / period, 4))
    return atr


def _dividend_from_metrics(metrics, current_price):
    """Return (yield_percent, annual_dividend_per_share) from mixed Finnhub/yfinance metric units."""
    if not metrics or not current_price or current_price <= 0:
        return None, None

    raw_yield = (
        metrics.get("currentDividendYieldTTM")
        or metrics.get("dividendYieldIndicatedAnnual")
        or metrics.get("dividendYield5Y")
    )
    if raw_yield is not None and raw_yield > 0:
        raw_yield = float(raw_yield)
        # yfinance uses decimal ratios like 0.0085; Finnhub metrics use percentages like 0.851.
        yield_percent = raw_yield * 100 if metrics.get("__source") == "yfinance" else raw_yield
        annual_div = current_price * (yield_percent / 100)
        return round(yield_percent, 2), annual_div

    annual_div = (
        metrics.get("dividendIndicatedAnnual")
        or metrics.get("dividendPerShareTTM")
        or metrics.get("dividendPerShareAnnual")
    )
    if annual_div is not None and annual_div > 0:
        annual_div = float(annual_div)
        return round((annual_div / current_price) * 100, 2), annual_div
    return None, None


async def compute_volatility(symbols):
    symbols = sorted({s.upper() for s in symbols if s})
    cache_key = ("volatility", tuple(symbols), _current_finnhub_api_key.get() or FINNHUB_API_KEY)
    cached = _get_analytics_cached(cache_key)
    if cached is not None:
        return cached

    def calc_symbol(symbol):
        try:
            candles = _finnhub_candles(symbol, period='3mo')
            if len(candles) < 30:
                return None
            highs = [c["high"] for c in candles]
            lows = [c["low"] for c in candles]
            closes = [c["close"] for c in candles]
            returns = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
            if len(returns) < 10:
                return None
            mean_ret = sum(returns) / len(returns)
            pct_std = math.sqrt(sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1))
            if not math.isfinite(pct_std) or pct_std <= 0:
                return None
            ann_vol = float(round(pct_std * (252 ** 0.5) * 100, 2))
            atr_vals = _atr(highs, lows, closes, 14)
            latest_atr = float(atr_vals[-1]) if atr_vals else None
            bracket = 'Low' if ann_vol < 20 else ('Moderate' if ann_vol < 40 else 'High')
            return {
                'symbol': symbol.upper(),
                'annualized_volatility': ann_vol,
                'atr_14': latest_atr,
                'volatility_bracket': bracket,
            }
        except Exception as e:
            print(f'[services] volatility scan {symbol} error: {e}')
            return None

    semaphore = asyncio.Semaphore(4)

    async def limited_calc(symbol):
        async with semaphore:
            return await asyncio.to_thread(calc_symbol, symbol)

    scanned = await asyncio.gather(*(limited_calc(symbol) for symbol in symbols), return_exceptions=True)
    results = [item for item in scanned if isinstance(item, dict)]
    results.sort(key=lambda x: x['annualized_volatility'], reverse=True)
    response = {'volatility': results}
    _set_analytics_cached(cache_key, response)
    return response


async def compute_dividends(holdings):
    holdings_key = tuple(sorted(
        (
            str(h.get('symbol', '')).upper(),
            round(float(h.get('quantity') or 0), 6),
            round(float(h.get('avg_cost') or 0), 6),
        )
        for h in holdings
    ))
    cache_key = ("dividends", holdings_key, _current_finnhub_api_key.get() or FINNHUB_API_KEY)
    cached = _get_analytics_cached(cache_key)
    if cached is not None:
        return cached

    def calc_holding(h):
        symbol = h['symbol']
        try:
            stock = _get_cached(symbol.upper()) or _fetch_symbol_fallback_sync(symbol)
            metrics = get_company_metrics(symbol)
            current_price = (stock or {}).get('currentPrice') or h.get('avg_cost', 0)
            div_yield_percent, annual_div_per_share = _dividend_from_metrics(metrics, current_price)
            if annual_div_per_share is not None:
                annual_income = round(annual_div_per_share * h.get('quantity', 0), 2)
                return {
                    'symbol': symbol.upper(),
                    'dividend_yield': div_yield_percent,
                    'annual_div_per_share': round(annual_div_per_share, 2),
                    'annual_income': annual_income,
                    'quantity': h.get('quantity', 0),
                }
            return {
                'symbol': symbol.upper(),
                'dividend_yield': None,
                'annual_div_per_share': None,
                'annual_income': 0,
                'quantity': h.get('quantity', 0),
            }
        except Exception as e:
            print(f'[services] dividend calc {symbol} error: {e}')
            return {
                'symbol': symbol.upper(),
                'dividend_yield': None,
                'annual_div_per_share': None,
                'annual_income': 0,
                'quantity': h.get('quantity', 0),
            }

    semaphore = asyncio.Semaphore(4)

    async def limited_calc(holding):
        async with semaphore:
            return await asyncio.to_thread(calc_holding, holding)

    scanned = await asyncio.gather(*(limited_calc(h) for h in holdings), return_exceptions=True)
    results = [item for item in scanned if isinstance(item, dict)]
    results.sort(key=lambda x: x['symbol'])
    total_forecast = sum(item.get('annual_income') or 0 for item in results)
    response = {'dividends': results, 'total_annual_forecast': round(total_forecast, 2)}
    _set_analytics_cached(cache_key, response)
    return response


async def export_portfolio_csv(holdings):
    rows = []
    for h in holdings:
        symbol = h['symbol']
        try:
            await asyncio.sleep(_REQUEST_DELAY)
            stock = await get_stock_data(symbol)
            profile = get_company_profile(symbol)
            metrics = get_company_metrics(symbol)
            current_price = (stock or {}).get('currentPrice') or h.get('avg_cost', 0)
            div_yield_percent, _ = _dividend_from_metrics(metrics, current_price)
            rows.append({
                'symbol': symbol.upper(),
                'name': profile.get('name', ''),
                'quantity': h.get('quantity', 0),
                'avg_cost': h.get('avg_cost', 0),
                'current_price': round(current_price, 2),
                'market_value': round(current_price * h.get('quantity', 0), 2),
                'pnl': round((current_price - h.get('avg_cost', 0)) * h.get('quantity', 0), 2),
                'pnl_percent': round(((current_price - h.get('avg_cost', 0)) / h.get('avg_cost', 1)) * 100, 2),
                'sector': get_sector(symbol, profile),
                'dividend_yield': div_yield_percent if div_yield_percent is not None else '-',
            })
        except Exception:
            rows.append({
                'symbol': symbol.upper(),
                'name': '',
                'quantity': h.get('quantity', 0),
                'avg_cost': h.get('avg_cost', 0),
                'current_price': h.get('avg_cost', 0),
                'market_value': round(h.get('avg_cost', 0) * h.get('quantity', 0), 2),
                'pnl': 0,
                'pnl_percent': 0,
                'sector': get_sector(symbol),
                'dividend_yield': '-',
            })
    return rows

