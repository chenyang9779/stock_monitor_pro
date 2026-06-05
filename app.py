from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
from typing import Optional, List
from datetime import datetime
import contextvars
import hashlib
import hmac
import secrets
import time

try:
    from .database import get_connection, init_db, DB_PATH
    from .models import *
    from .services import *
except ImportError:
    from database import get_connection, init_db, DB_PATH
    from models import *
    from services import *

app = FastAPI(title='Stock Monitor Pro', version='1.0.0')

BASE_DIR = Path(__file__).resolve().parent
SESSION_COOKIE = 'stock_monitor_session'
SESSION_MAX_AGE = 60 * 60 * 24 * 14
SECRET_KEY = 'stock-monitor-local-secret-change-me'
_current_user_id = contextvars.ContextVar('current_user_id', default=None)

# Mount static files and templates
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')
templates = Jinja2Templates(directory=str(BASE_DIR / 'templates'))


@app.middleware('http')
async def auth_middleware(request: Request, call_next):
    public_api = request.url.path.startswith('/api/auth')
    user_id = read_session(request.cookies.get(SESSION_COOKIE))
    user_token = None
    api_token = None
    if user_id:
        user_token = _current_user_id.set(user_id)
        api_token = set_finnhub_api_key(get_user_setting(user_id, 'finnhub_api_key'))
    elif request.url.path.startswith('/api/') and not public_api:
        return JSONResponse(status_code=401, content={'detail': 'Not authenticated'})
    try:
        return await call_next(request)
    finally:
        if api_token is not None:
            reset_finnhub_api_key(api_token)
        if user_token is not None:
            _current_user_id.reset(user_token)

@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    application.state.initialized = True
    print('Stock Monitor Pro started!')
    yield

app.router.lifespan_context = lifespan

# ============================================================
# HELPER FUNCTIONS
# ============================================================
_db_lock = None

def _get_lock():
    global _db_lock
    if _db_lock is None:
        import threading
        _db_lock = threading.Lock()
    return _db_lock

def db_query(sql: str, params: Optional[list] = None) -> List[dict]:
    with _get_lock():
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

def db_execute(sql: str, params: Optional[list] = None) -> int:
    with _get_lock():
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        return affected


def db_execute_returning_id(sql: str, params: Optional[list] = None) -> int:
    with _get_lock():
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id


def current_user_id() -> int:
    user_id = _current_user_id.get()
    if not user_id:
        raise HTTPException(status_code=401, detail='Not authenticated')
    return int(user_id)


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 120000).hex()
    return f'pbkdf2_sha256${salt}${digest}'


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        _, salt, digest = stored_hash.split('$', 2)
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 120000).hex()
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


def sign_session(user_id: int) -> str:
    expires = int(time.time()) + SESSION_MAX_AGE
    payload = f'{user_id}:{expires}'
    signature = hmac.new(SECRET_KEY.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return f'{payload}:{signature}'


def read_session(cookie_value: Optional[str]) -> Optional[int]:
    if not cookie_value:
        return None
    try:
        user_id, expires, signature = cookie_value.split(':', 2)
        payload = f'{user_id}:{expires}'
        expected = hmac.new(SECRET_KEY.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or int(expires) < int(time.time()):
            return None
        return int(user_id)
    except Exception:
        return None


def get_user_setting(user_id: int, key: str, default: str = '') -> str:
    rows = db_query('SELECT value FROM settings WHERE user_id = ? AND key = ?', [user_id, key])
    return rows[0]['value'] if rows else default


def set_user_setting(user_id: int, key: str, value: str):
    db_execute(
        'INSERT OR REPLACE INTO settings (user_id, key, value) VALUES (?, ?, ?)',
        [user_id, key, value],
    )


# ============================================================
# AUTH + ACCOUNT SETTINGS API
# ============================================================
@app.post('/api/auth/register')
async def register(data: AuthRequest, response: Response):
    username = data.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail='Username is required')
    existing = db_query('SELECT id FROM users WHERE lower(username) = lower(?)', [username])
    if existing:
        raise HTTPException(status_code=409, detail='Username already exists')
    user_id = db_execute_returning_id(
        'INSERT INTO users (username, password_hash) VALUES (?, ?)',
        [username, password_hash(data.password)],
    )
    response.set_cookie(SESSION_COOKIE, sign_session(user_id), max_age=SESSION_MAX_AGE, httponly=True, samesite='lax')
    return {'id': user_id, 'username': username}


@app.post('/api/auth/login')
async def login(data: AuthRequest, response: Response):
    username = data.username.strip()
    rows = db_query('SELECT * FROM users WHERE lower(username) = lower(?)', [username])
    if not rows or not verify_password(data.password, rows[0]['password_hash']):
        raise HTTPException(status_code=401, detail='Invalid username or password')
    response.set_cookie(SESSION_COOKIE, sign_session(rows[0]['id']), max_age=SESSION_MAX_AGE, httponly=True, samesite='lax')
    return {'id': rows[0]['id'], 'username': rows[0]['username']}


@app.post('/api/auth/logout')
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {'message': 'Logged out'}


@app.put('/api/auth/password')
async def change_password(data: PasswordChangeRequest):
    user_id = current_user_id()
    rows = db_query('SELECT password_hash FROM users WHERE id = ?', [user_id])
    if not rows or not verify_password(data.current_password, rows[0]['password_hash']):
        raise HTTPException(status_code=400, detail='Current password is incorrect')
    db_execute('UPDATE users SET password_hash = ? WHERE id = ?', [password_hash(data.new_password), user_id])
    return {'message': 'Password changed'}


@app.put('/api/auth/username')
async def change_username(data: UsernameUpdateRequest):
    user_id = current_user_id()
    username = data.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail='Username is required')
    existing = db_query('SELECT id FROM users WHERE lower(username) = lower(?) AND id != ?', [username, user_id])
    if existing:
        raise HTTPException(status_code=409, detail='Username already exists')
    db_execute('UPDATE users SET username = ? WHERE id = ?', [username, user_id])
    return {'id': user_id, 'username': username}


@app.get('/api/auth/me')
async def me():
    user_id = current_user_id()
    rows = db_query('SELECT id, username, created_at FROM users WHERE id = ?', [user_id])
    if not rows:
        raise HTTPException(status_code=401, detail='Not authenticated')
    return rows[0]


@app.get('/api/settings')
async def get_settings():
    user_id = current_user_id()
    api_key = get_user_setting(user_id, 'finnhub_api_key')
    return {
        'finnhub_api_key': api_key,
        'has_finnhub_api_key': bool(api_key),
    }


@app.put('/api/settings')
async def update_settings(data: ApiSettingsUpdate):
    user_id = current_user_id()
    set_user_setting(user_id, 'finnhub_api_key', (data.finnhub_api_key or '').strip())
    return {'message': 'Settings saved'}

def extract_price(stock_data, fallback=0.0):
    """Extract price from stock data dict, trying multiple keys."""
    if not stock_data:
        return float(fallback or 0)
    for key in ('regularMarketPrice', 'currentPrice', 'previousClose'):
        value = stock_data.get(key)
        if value is None:
            continue
        try:
            value = float(value)
            if value >= 0:
                return value
        except (TypeError, ValueError):
            continue
    return float(fallback or 0)


_last_snapshot_time = 0
_SNAPSHOT_THRESHOLD = 1800  # 30 minutes

def save_portfolio_snapshot(total_value=None, total_cost=None, holdings_count=None, watchlist_count=None):
    """Save portfolio snapshot with throttling (max once per 30 min)."""
    global _last_snapshot_time
    import time
    now = time.time()
    if now - _last_snapshot_time < _SNAPSHOT_THRESHOLD:
        return None
    try:
        user_id = current_user_id()
        if total_value is None or total_cost is None or holdings_count is None:
            holdings = db_query("SELECT * FROM holdings WHERE user_id = ?", [user_id])
            total_value = 0
            total_cost = 0
            for h in holdings:
                quantity = max(h.get('quantity', 0), 0)
                total_value += h.get('avg_cost', 0) * quantity
                total_cost += h.get('avg_cost', 0) * quantity
            holdings_count = len(holdings)
        if watchlist_count is None:
            watchlist_count = db_query("SELECT COUNT(*) as cnt FROM watchlist WHERE user_id = ?", [user_id])[0]["cnt"]
        snapshot = {
            "timestamp": datetime.utcnow().isoformat(),
            "holdings_count": holdings_count,
            "watchlist_count": watchlist_count,
            "total_value": round(total_value, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": round(total_value - total_cost, 2),
            "total_pnl_percent": round(((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0, 2),
        }
        if snapshot["holdings_count"] > 0 and snapshot["total_cost"] > 0 and snapshot["total_value"] <= 0:
            return None
        db_execute(
            """
            INSERT INTO portfolio_snapshots
                (user_id, timestamp, total_value, total_cost, total_pnl, total_pnl_percent, holdings_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                user_id,
                snapshot["timestamp"],
                snapshot["total_value"],
                snapshot["total_cost"],
                snapshot["total_pnl"],
                snapshot["total_pnl_percent"],
                snapshot["holdings_count"],
            ],
        )
        _last_snapshot_time = now
        return snapshot
    except Exception:
        return None

async def evaluate_alerts():
    """Check all active alerts and trigger those that crossed the threshold."""
    user_id = current_user_id()
    alerts = db_query('SELECT * FROM price_alerts WHERE user_id = ? AND active = 1 AND triggered = 0', [user_id])
    if not alerts:
        return 0
    triggered_count = 0
    for alert in alerts:
        symbol = alert['symbol']
        target = alert['target_price']
        condition = alert['condition']
        stock = await get_stock_data(symbol)
        if not stock:
            continue
        current = extract_price(stock, 0)
        if current <= 0:
            continue
        crossed = False
        if condition == 'above' and current >= target:
            crossed = True
        elif condition == 'below' and current <= target:
            crossed = True
        if crossed:
            db_execute('UPDATE price_alerts SET triggered = 1, triggered_at = ? WHERE user_id = ? AND id = ?',
                       [datetime.utcnow().isoformat(), user_id, alert['id']])
            triggered_count += 1
            print(f"[alerts] Triggered: {symbol} {condition} {target} @ {current}")
    return triggered_count



@app.get('/api/dashboard')
async def get_dashboard():
    user_id = current_user_id()
    # Portfolio stats
    holdings = db_query('SELECT * FROM holdings WHERE user_id = ?', [user_id])
    watchlist_count = db_query('SELECT COUNT(*) as cnt FROM watchlist WHERE user_id = ?', [user_id])[0]['cnt']
    alert_count = db_query('SELECT COUNT(*) as cnt FROM price_alerts WHERE user_id = ? AND active = 1', [user_id])[0]['cnt']

    total_value = 0
    total_cost = 0
    best_pnl = -float('inf')
    worst_pnl = float('inf')
    best_stock = None
    worst_stock = None
    alerts_info = []

    for h in holdings:
        stock_data = await get_stock_data(h['symbol'])
        if stock_data:
            # Use regularMarketPrice for pre/after-hours, fallback to currentPrice
            current_price = stock_data.get('regularMarketPrice') or stock_data.get('currentPrice')
            if current_price is None:
                current_price = 0
            current_value = current_price * h['quantity']
            cost = h['avg_cost'] * h['quantity']
            pnl = current_value - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else 0

            h['current_price'] = current_price
            h['current_value'] = round(current_value, 2)
            h['pnl'] = round(pnl, 2)
            h['pnl_percent'] = round(pnl_pct, 2)

            total_value += current_value
            total_cost += cost

            if pnl > best_pnl:
                best_pnl = pnl
                best_stock = h['symbol']
            if pnl < worst_pnl:
                worst_pnl = pnl
                worst_stock = h['symbol']
        else:
            h['current_price'] = None
            h['current_value'] = round(h['avg_cost'] * h['quantity'], 2)
            h['pnl'] = 0
            h['pnl_percent'] = 0
            total_cost += round(h['avg_cost'] * h['quantity'], 2)

    # Fetch alerts
    alerts = db_query('SELECT * FROM price_alerts WHERE user_id = ? AND active = 1 LIMIT 5', [user_id])
    for a in alerts:
        alerts_info.append(AlertResponse(**a).model_dump())

    # Fetch recent news - plain dicts, already JSON serializable
    recent_news = db_query('SELECT * FROM news WHERE user_id = ? ORDER BY published_at DESC LIMIT 5', [user_id])
    recent_news_list = [n for n in recent_news]

    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    save_portfolio_snapshot(total_value, total_cost, len(holdings), watchlist_count)

    return JSONResponse(content={
        'total_value': round(total_value, 2),
        'total_cost': round(total_cost, 2),
        'total_pnl': round(total_pnl, 2),
        'total_pnl_percent': round(total_pnl_pct, 2),
        'holdings_count': len(holdings),
        'watchlist_count': watchlist_count,
        'alert_count': alert_count,
        'top_performer': best_stock,
        'top_performer_pnl': round(best_pnl, 2) if best_stock else None,
        'worst_performer': worst_stock,
        'worst_performer_pnl': round(worst_pnl, 2) if worst_stock else None,
        'holdings': [HoldingResponse(**h).model_dump() for h in holdings],
        'alerts': alerts_info,
        'recent_news': recent_news_list,
    })

@app.get('/api/market/indices')
async def get_market_indices_api():
    return await get_market_indices()

# ============================================================
# HOLDINGS API
# ============================================================
@app.get('/api/holdings')
async def get_holdings():
    user_id = current_user_id()
    holdings = db_query('SELECT * FROM holdings WHERE user_id = ? ORDER BY symbol', [user_id])
    result = []
    for h in holdings:
        stock_data = await get_stock_data(h['symbol'])
        if stock_data:
            current_price = stock_data.get('currentPrice') or stock_data.get('regularMarketPrice') or h['avg_cost']
            current_value = current_price * h['quantity']
            cost = round(h['avg_cost'] * h['quantity'], 2)
            pnl = current_value - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else 0
            h['current_price'] = current_price
            h['current_value'] = round(current_value, 2)
            h['pnl'] = round(pnl, 2)
            h['pnl_percent'] = round(pnl_pct, 2)
        else:
            h['current_price'] = h['avg_cost']
            h['current_value'] = round(h['avg_cost'] * h['quantity'], 2)
            h['pnl'] = 0
            h['pnl_percent'] = 0

        result.append(HoldingResponse(**h).model_dump())
    return result

@app.post('/api/holdings')
async def add_holding(data: HoldingCreate):
    user_id = current_user_id()
    symbol = normalize_symbol(data.symbol)
    try:
        db_execute(
            '''INSERT OR REPLACE INTO holdings (user_id, symbol, name, quantity, avg_cost, currency) VALUES (?, ?, ?, ?, ?, ?)''',
            [user_id, symbol, data.name, data.quantity, data.avg_cost, data.currency]
        )
        return {'message': f'{symbol} added successfully', 'symbol': symbol}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put('/api/holdings/{symbol}')
async def update_holding(symbol: str, data: HoldingUpdate):
    user_id = current_user_id()
    symbol = normalize_symbol(symbol)
    updates = []
    params = []
    if data.name is not None:
        updates.append('name = ?')
        params.append(data.name)
    if data.quantity is not None:
        updates.append('quantity = ?')
        params.append(data.quantity)
    if data.avg_cost is not None:
        updates.append('avg_cost = ?')
        params.append(data.avg_cost)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.extend([user_id, symbol])
        db_execute(f'UPDATE holdings SET {", ".join(updates)} WHERE user_id = ? AND symbol = ?', params)
    return {'message': f'{symbol} updated'}

@app.delete('/api/holdings/{symbol}')
async def delete_holding(symbol: str):
    user_id = current_user_id()
    symbol = normalize_symbol(symbol)
    db_execute('DELETE FROM price_alerts WHERE user_id = ? AND symbol = ?', [user_id, symbol])
    db_execute('DELETE FROM holdings WHERE user_id = ? AND symbol = ?', [user_id, symbol])
    return {'message': f'{symbol} removed'}


@app.delete('/api/holdings_by_id/{holding_id}')
async def delete_holding_by_id(holding_id: int):
    user_id = current_user_id()
    try:
        holding = db_query('SELECT symbol FROM holdings WHERE user_id = ? AND id = ?', [user_id, holding_id])
        if not holding:
            raise HTTPException(status_code=404, detail='Holding not found')
        symbol = holding[0]['symbol']
        db_execute('DELETE FROM price_alerts WHERE user_id = ? AND symbol = ?', [user_id, symbol])
        db_execute('DELETE FROM holdings WHERE user_id = ? AND id = ?', [user_id, holding_id])
        return {'message': f'{symbol} removed'}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get('/api/holdings/{symbol}/history')
async def get_holding_history(symbol: str, period: str = '1mo'):
    return await get_historical_data(normalize_symbol(symbol), period)

# ============================================================
# PRICE ALERTS API
# ============================================================
@app.get('/api/alerts')
async def get_alerts():
    user_id = current_user_id()
    alerts = db_query('SELECT * FROM price_alerts WHERE user_id = ? ORDER BY created_at DESC', [user_id])
    return [AlertResponse(**a).model_dump() for a in alerts]

@app.post('/api/alerts')
async def add_alert(data: PriceAlertCreate):
    user_id = current_user_id()
    symbol = normalize_symbol(data.symbol)
    # Get current price
    stock_data = await get_stock_data(symbol)
    current_price = 0
    if stock_data:
        current_price = extract_price(stock_data)
    else:
        # Fallback: get avg_cost from holdings
        existing = db_query('SELECT avg_cost FROM holdings WHERE user_id = ? AND symbol = ?', [user_id, symbol])
        if existing:
            current_price = existing[0]['avg_cost']
    
    db_execute(
        '''INSERT INTO price_alerts (user_id, symbol, condition, target_price, current_price) VALUES (?, ?, ?, ?, ?)''',
        [user_id, symbol, data.condition.value, data.target_price, current_price]
    )
    return {'message': f'Alert created for {symbol}'}

@app.put('/api/alerts/{alert_id}/toggle')
async def toggle_alert(alert_id: int):
    user_id = current_user_id()
    alert = db_query('SELECT * FROM price_alerts WHERE user_id = ? AND id = ?', [user_id, alert_id])
    if not alert:
        raise HTTPException(status_code=404, detail='Alert not found')
    
    new_state = 0 if alert[0]['active'] else 1
    db_execute('UPDATE price_alerts SET active = ? WHERE user_id = ? AND id = ?', [new_state, user_id, alert_id])
    return {'message': f'Alert {alert_id} toggled'}

@app.delete('/api/alerts/{alert_id}')
async def delete_alert(alert_id: int):
    user_id = current_user_id()
    db_execute('DELETE FROM price_alerts WHERE user_id = ? AND id = ?', [user_id, alert_id])
    return {'message': f'Alert {alert_id} deleted'}

# ============================================================
# WATCHLIST API
# ============================================================

@app.get("/api/alert-symbols")
async def get_alert_symbols():
    """Return unique symbols from holdings and watchlist for alert creation."""
    user_id = current_user_id()
    holdings = db_query("SELECT symbol, name FROM holdings WHERE user_id = ?", [user_id])
    watchlist = db_query("SELECT symbol, name FROM watchlist WHERE user_id = ?", [user_id])
    by_symbol = {}
    for row in holdings + watchlist:
        sym = row["symbol"].upper()
        by_symbol[sym] = {"symbol": sym, "name": row.get("name") or sym}
    return sorted(by_symbol.values(), key=lambda x: x["symbol"])

@app.get('/api/watchlist')
async def get_watchlist():
    user_id = current_user_id()
    items = db_query('SELECT * FROM watchlist WHERE user_id = ? ORDER BY symbol', [user_id])
    result = []
    for item in items:
        stock_data = await get_stock_data(item['symbol'])
        if stock_data:
            item['current_price'] = stock_data.get('regularMarketPrice') or stock_data.get('currentPrice')
            item['price_change'] = stock_data.get('priceChange')
            item['price_change_percent'] = stock_data.get('priceChangePercent')
        result.append(WatchlistItem(**item).model_dump())
    return result

@app.post('/api/watchlist')
async def add_to_watchlist(data: WatchlistAdd):
    user_id = current_user_id()
    symbol = normalize_symbol(data.symbol)
    name = data.name or symbol
    try:
        db_execute('INSERT OR IGNORE INTO watchlist (user_id, symbol, name) VALUES (?, ?, ?)', [user_id, symbol, name])
        return {'message': f'{symbol} added to watchlist'}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete('/api/watchlist/{symbol}')
async def remove_from_watchlist(symbol: str):
    user_id = current_user_id()
    symbol = normalize_symbol(symbol)
    db_execute('DELETE FROM watchlist WHERE user_id = ? AND symbol = ?', [user_id, symbol])
    return {'message': f'{symbol} removed from watchlist'}

# ============================================================
# NEWS API
# ============================================================
@app.get('/api/news')
async def get_news(symbol: Optional[str] = None, is_read: Optional[bool] = None, limit: int = 50):
    user_id = current_user_id()
    conditions = ['user_id = ?']
    params = [user_id]
    if symbol:
        conditions.append('symbol = ?')
        params.append(symbol.upper())
    if is_read is not None:
        conditions.append('is_read = ?')
        params.append(1 if is_read else 0)
    
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    news = db_query(f'SELECT * FROM news{where} ORDER BY published_at DESC LIMIT ?', params + [limit])
    return [NewsItem(**n).model_dump() for n in news]

@app.post('/api/news/fetch')
async def fetch_news_for_symbols():
    user_id = current_user_id()
    holdings = db_query('SELECT symbol FROM holdings WHERE user_id = ?', [user_id])
    watchlist = db_query('SELECT symbol FROM watchlist WHERE user_id = ?', [user_id])
    symbols = [h['symbol'] for h in holdings] + [w['symbol'] for w in watchlist]
    
    new_items = []
    for symbol in symbols:
        news_list = await fetch_stock_news(symbol)
        for item in news_list:
            db_execute(
                '''INSERT OR IGNORE INTO news (user_id, symbol, title, summary, url, source, published_at) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                [user_id, symbol, item['title'], item['summary'], item['url'], item['source'], item['published_at']]
            )
            new_items.append(item)
    
    return {'message': f'Fetched news for {len(set(symbols))} symbols', 'new_count': len(new_items)}

@app.put('/api/news/{news_id}/read')
async def mark_news_read(news_id: int):
    user_id = current_user_id()
    db_execute('UPDATE news SET is_read = 1 WHERE user_id = ? AND id = ?', [user_id, news_id])
    return {'message': 'Marked as read'}

# ============================================================
# PORTFOLIO ANALYSIS API
# ============================================================
@app.get('/api/portfolio/history')
async def get_portfolio_history():
    user_id = current_user_id()
    snapshots = db_query(
        """
        SELECT *
        FROM portfolio_snapshots
        WHERE user_id = ? AND NOT (
            (holdings_count > 0 AND total_cost > 0 AND total_value <= 0)
            OR (total_value <= 0 AND total_cost <= 0)
        )
        ORDER BY timestamp ASC
        """,
        [user_id],
    )

    filtered = []
    previous = None
    for snapshot in snapshots:
        if previous:
            prev_value = previous.get('total_value') or 0
            curr_value = snapshot.get('total_value') or 0
            same_cost = round(previous.get('total_cost') or 0, 2) == round(snapshot.get('total_cost') or 0, 2)
            if same_cost and prev_value > 0 and curr_value > 0:
                change_pct = abs(curr_value - prev_value) / prev_value
                if change_pct > 0.35:
                    continue
        filtered.append(snapshot)
        previous = snapshot

    buckets = {}
    for snapshot in filtered:
        try:
            parsed = datetime.fromisoformat(str(snapshot['timestamp']).replace('Z', '+00:00'))
            bucket = parsed.strftime('%Y-%m-%d %H:00')
        except Exception:
            bucket = str(snapshot['timestamp'])[:13]
        buckets[bucket] = snapshot

    return [PortfolioSnapshot(**s).model_dump() for s in buckets.values()]

@app.get('/api/health')
async def health_check():
    return {'status': 'ok'}

@app.get('/api/stocks/search')
async def search_stocks(query: str = Query(..., min_length=1)):
    results = await search_tickers(query)
    if results:
        return results

    return [{'symbol': query.upper(), 'name': query.upper()}]

@app.get('/api/stocks/details/{symbol}')
async def get_stock_details(symbol: str):
    upper = symbol.upper()
    data = await get_stock_data(upper)
    if not data:
        hist = await get_historical_data(upper, '5d')
        if hist:
            latest = hist[-1]
            previous = hist[-2] if len(hist) > 1 else latest
            current = latest.get('close') or 0
            prev_close = previous.get('close') or current
            change = current - prev_close
            data = {
                'symbol': upper,
                'name': upper,
                'currentPrice': current,
                'previousClose': prev_close,
                'open': latest.get('open') or current,
                'dayHigh': latest.get('high') or current,
                'dayLow': latest.get('low') or current,
                'volume': latest.get('volume'),
                'priceChange': change,
                'priceChangePercent': (change / prev_close * 100) if prev_close else 0,
            }
        else:
            hint = ''
            # Common typo hints
            typo_map = {'APPL': 'AAPL', 'AMZN': 'AMZN', 'TSLA': 'TSLA', 'NVDA': 'NVDA', 'META': 'META'}
            if upper in typo_map:
                hint = f' Did you mean {typo_map[upper]}?'
            raise HTTPException(status_code=404, detail=f'Stock {upper} not found. Check the symbol.{hint}')
    profile = get_company_profile(upper)
    metrics = get_company_metrics(upper)
    data.update({
        'sector': profile.get('sector') or get_sector(upper, profile),
        'industry': profile.get('industry'),
        'peRatio': metrics.get('peNormalizedAnnual') or metrics.get('peBasicExclExtraTTM'),
        'fiftyTwoWeekLow': metrics.get('52WeekLow'),
        'fiftyTwoWeekHigh': metrics.get('52WeekHigh'),
        'volume': data.get('volume'),
        'marketCap': profile.get('marketCap') or data.get('marketCap'),
        'currency': data.get('currency') or 'USD',
    })
    # Add historicPrice for mini charts
    try:
        hist = await get_historical_data(upper, '1mo')
        data['historicPrice'] = hist if hist else []
    except Exception:
        data['historicPrice'] = []
    return data

@app.get('/api/portfolio/performance')
async def get_portfolio_performance():
    user_id = current_user_id()
    holdings = db_query('SELECT * FROM holdings WHERE user_id = ?', [user_id])
    performance = {}
    for h in holdings:
        stock_data = await get_stock_data(h['symbol'])
        if stock_data:
            current_price = stock_data.get('currentPrice') or stock_data.get('regularMarketPrice') or h['avg_cost']
            cost = h['avg_cost']
            pnl_pct = ((current_price - cost) / cost * 100) if cost > 0 else 0
            performance[h['symbol']] = {
                'symbol': h['symbol'],
                'quantity': h['quantity'],
                'avg_cost': cost,
                'current_price': current_price,
                'pnl_percent': round(pnl_pct, 2),
                'allocation': 0,  # Will be calculated
            }
    
    total_value = sum(
        p['current_price'] * p['quantity'] for p in performance.values()
        if p['current_price']
    )
    for p in performance.values():
        if total_value > 0 and p['current_price']:
            p['allocation'] = round((p['current_price'] * p['quantity']) / total_value * 100, 2)
    
    return list(performance.values())

# ============================================================
# WEB INTERFACE
# ============================================================
@app.get('/', response_class=HTMLResponse)
async def index():
    with open(BASE_DIR / 'templates' / 'index.html', 'r', encoding='utf-8') as f:
        return f.read()


# ============================================================
# QUANT ANALYSIS API
# ============================================================
@app.get('/api/quant/indicators/{symbol}')
async def get_technical_indicators(symbol: str, period: str = '1y'):
    data = await compute_technical_indicators(symbol.upper(), period)
    if not data:
        raise HTTPException(status_code=404, detail=f'No data for {symbol.upper()}')
    return data

@app.get('/api/quant/correlation')
async def get_correlation_matrix():
    try:
        user_id = current_user_id()
        holdings = db_query('SELECT symbol FROM holdings WHERE user_id = ?', [user_id])
        if not holdings:
            return {'matrix': [], 'symbols': []}
        symbols = [h['symbol'] for h in holdings]
        return await compute_correlation_matrix(symbols)
    except Exception as e:
        print(f'[app] correlation error: {e}')
        return {'matrix': [], 'symbols': []}

@app.get('/api/quant/sectors')
async def get_sector_allocation():
    user_id = current_user_id()
    holdings = db_query('SELECT * FROM holdings WHERE user_id = ? ORDER BY symbol', [user_id])
    result = []
    for h in holdings:
        stock_data = await get_stock_data(h['symbol'])
        if stock_data:
            current_price = stock_data.get('currentPrice') or stock_data.get('regularMarketPrice') or h['avg_cost']
        else:
            current_price = h['avg_cost']
        h['current_price'] = current_price
    return await compute_sector_allocation(holdings)

# ============================================================
# RISK METRICS API
# ============================================================
@app.get('/api/quant/risk/portfolio')
async def get_portfolio_risk():
    user_id = current_user_id()
    holdings = db_query('SELECT * FROM holdings WHERE user_id = ?', [user_id])
    if not holdings:
        return {'portfolio_risk': {}, 'holdings': []}
    total_value = 0
    weighted_sharpe = 0
    weighted_sortino = 0
    weighted_vol = 0
    weighted_dd = 0
    holding_risks = []
    for h in holdings:
        r = compute_risk_metrics(h['symbol'])
        if r:
            stock_data = await get_stock_data(h['symbol'])
            cp = extract_price(stock_data, h.get('avg_cost', 0))
            val = cp * h.get('quantity', 0)
            total_value += val
            holding_risks.append({
                'symbol': r['symbol'],
                'sharpe': r['risk']['sharpe_ratio'],
                'sortino': r['risk']['sortino_ratio'],
                'max_drawdown': r['risk']['max_drawdown'],
                'annualized_volatility': r['risk']['annualized_volatility'],
                'daily_var_95': r['risk']['daily_var_95'],
                'value': round(val, 2),
            })
    for hr in holding_risks:
        if total_value > 0:
            w = hr['value'] / total_value
            weighted_sharpe += w * hr['sharpe']
            weighted_sortino += w * hr['sortino']
            weighted_vol += w * hr['annualized_volatility']
            weighted_dd += w * hr['max_drawdown']
    portfolio_risk = {
        'weighted_sharpe': round(weighted_sharpe, 2),
        'weighted_sortino': round(weighted_sortino, 2),
        'weighted_max_drawdown': round(weighted_dd, 2),
        'weighted_annualized_volatility': round(weighted_vol, 2),
        'total_value': round(total_value, 2),
    }
    return {'portfolio_risk': portfolio_risk, 'holdings': holding_risks}


@app.get('/api/quant/risk/{symbol}')
async def get_risk_metrics(symbol: str, period: str = '1y'):
    data = compute_risk_metrics(symbol.upper(), period)
    if not data:
        raise HTTPException(status_code=404, detail=f'No risk data for {symbol.upper()}')
    return data



# ============================================================
# BETA API
# ============================================================
@app.get('/api/quant/beta')
async def get_all_betas():
    user_id = current_user_id()
    holdings = db_query('SELECT * FROM holdings WHERE user_id = ?', [user_id])
    if not holdings:
        return {'betas': []}
    betas = []
    for h in holdings:
        b = compute_beta(h['symbol'])
        if b:
            stock_data = await get_stock_data(h['symbol'])
            cp = extract_price(stock_data, h.get('avg_cost', 0))
            b['value'] = round(cp * h.get('quantity', 0), 2)
            b['allocation'] = 0
            betas.append(b)
    total_val = sum(x['value'] for x in betas) if betas else 1
    for b in betas:
        b['allocation'] = round((b['value'] / total_val * 100) if total_val > 0 else 0, 2)
    return {'betas': betas}


# ============================================================
# VOLATILITY SCANNER API
# ============================================================
@app.get('/api/quant/volatility')
async def get_volatility_scanner():
    try:
        user_id = current_user_id()
        holdings = db_query('SELECT * FROM holdings WHERE user_id = ?', [user_id])
        if not holdings:
            return {'volatility': []}
        symbols = [h['symbol'] for h in holdings]
        return await compute_volatility(symbols)
    except Exception as e:
        print(f'[app] volatility error: {e}')
        return {'volatility': []}


# ============================================================
# DIVIDEND TRACKER API
# ============================================================
@app.get('/api/quant/dividends')
async def get_dividend_tracker():
    user_id = current_user_id()
    holdings = db_query('SELECT * FROM holdings WHERE user_id = ?', [user_id])
    if not holdings:
        return {'dividends': [], 'total_annual_forecast': 0}
    return await compute_dividends(holdings)


# ============================================================
# EXPORT CSV API
# ============================================================
@app.get('/api/export/portfolio-csv')
async def export_csv():
    user_id = current_user_id()
    holdings = db_query('SELECT * FROM holdings WHERE user_id = ? ORDER BY symbol', [user_id])
    if not holdings:
        raise HTTPException(status_code=404, detail='No holdings to export')
    rows = await export_portfolio_csv(holdings)
    if not rows:
        raise HTTPException(status_code=404, detail='No data to export')
    from fastapi.responses import Response
    import csv, io
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    csv_data = output.getvalue()
    output.close()
    return Response(
        content=csv_data,
        media_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename=portfolio_export.csv'}
    )
