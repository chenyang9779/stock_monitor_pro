import sqlite3
import os
import secrets
import hashlib
from pathlib import Path

DB_PATH = Path(os.path.dirname(os.path.abspath(__file__))) / 'stocks.db'

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn


def migrate_price_alerts(cursor):
    """Remove foreign key from price_alerts if it exists (SQLite cannot drop FK directly)."""
    cursor.execute("PRAGMA foreign_key_list(price_alerts)")
    fks = cursor.fetchall()
    if not fks:
        return
    cursor.execute("ALTER TABLE price_alerts RENAME TO price_alerts_old")
    cursor.execute("""
        CREATE TABLE price_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            condition TEXT NOT NULL CHECK(condition IN ('above', 'below')),
            target_price REAL NOT NULL CHECK(target_price >= 0),
            current_price REAL NOT NULL DEFAULT 0,
            triggered INTEGER DEFAULT 0,
            triggered_at TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    cursor.execute("""
        INSERT INTO price_alerts
        (id, symbol, condition, target_price, current_price, triggered, triggered_at, active, created_at)
        SELECT id, symbol, condition, target_price, current_price, triggered, triggered_at, active, created_at
        FROM price_alerts_old
    """)
    cursor.execute("DROP TABLE price_alerts_old")


def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def table_exists(cursor, table):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [table])
    return cursor.fetchone() is not None


def make_password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 120000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def ensure_default_user(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    cursor.execute("SELECT id FROM users WHERE username = 'default'")
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ['default', make_password_hash('changeme')])
    return cursor.lastrowid


def migrate_user_scoped_tables(cursor, default_user_id):
    if table_exists(cursor, 'holdings') and not column_exists(cursor, 'holdings', 'user_id'):
        cursor.execute("ALTER TABLE holdings RENAME TO holdings_old")
        cursor.execute("""
            CREATE TABLE holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                quantity REAL NOT NULL DEFAULT 0,
                avg_cost REAL NOT NULL DEFAULT 0,
                currency TEXT DEFAULT 'USD',
                added_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, symbol),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            INSERT INTO holdings (id, user_id, symbol, name, quantity, avg_cost, currency, added_at, updated_at)
            SELECT id, ?, symbol, name, quantity, avg_cost, currency, added_at, updated_at FROM holdings_old
        """, [default_user_id])
        cursor.execute("DROP TABLE holdings_old")

    if table_exists(cursor, 'watchlist') and not column_exists(cursor, 'watchlist', 'user_id'):
        cursor.execute("ALTER TABLE watchlist RENAME TO watchlist_old")
        cursor.execute("""
            CREATE TABLE watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                added_at TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, symbol),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            INSERT INTO watchlist (id, user_id, symbol, name, added_at)
            SELECT id, ?, symbol, name, added_at FROM watchlist_old
        """, [default_user_id])
        cursor.execute("DROP TABLE watchlist_old")

    if table_exists(cursor, 'news') and not column_exists(cursor, 'news', 'user_id'):
        cursor.execute("ALTER TABLE news RENAME TO news_old")
        cursor.execute("""
            CREATE TABLE news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT,
                title TEXT NOT NULL,
                summary TEXT,
                url TEXT,
                source TEXT,
                published_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                is_read INTEGER DEFAULT 0,
                UNIQUE(user_id, symbol, title, published_at),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO news (id, user_id, symbol, title, summary, url, source, published_at, created_at, is_read)
            SELECT id, ?, symbol, title, summary, url, source, published_at, created_at, is_read FROM news_old
        """, [default_user_id])
        cursor.execute("DROP TABLE news_old")

    if table_exists(cursor, 'settings') and not column_exists(cursor, 'settings', 'user_id'):
        cursor.execute("ALTER TABLE settings RENAME TO settings_old")
        cursor.execute("""
            CREATE TABLE settings (
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                PRIMARY KEY(user_id, key),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("INSERT INTO settings (user_id, key, value) SELECT ?, key, value FROM settings_old", [default_user_id])
        cursor.execute("DROP TABLE settings_old")

    if table_exists(cursor, 'price_alerts') and not column_exists(cursor, 'price_alerts', 'user_id'):
        cursor.execute("ALTER TABLE price_alerts ADD COLUMN user_id INTEGER")
        cursor.execute("UPDATE price_alerts SET user_id = ?", [default_user_id])

    if table_exists(cursor, 'portfolio_snapshots') and not column_exists(cursor, 'portfolio_snapshots', 'user_id'):
        cursor.execute("ALTER TABLE portfolio_snapshots ADD COLUMN user_id INTEGER")
        cursor.execute("UPDATE portfolio_snapshots SET user_id = ?", [default_user_id])


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    default_user_id = ensure_default_user(cursor)
    
    # Holdings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            quantity REAL NOT NULL DEFAULT 0,
            avg_cost REAL NOT NULL DEFAULT 0,
            currency TEXT DEFAULT 'USD',
            added_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, symbol),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Price history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(symbol, date)
        )
    """)
    
    # Price alerts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            condition TEXT NOT NULL CHECK(condition IN ('above', 'below')),
            target_price REAL NOT NULL CHECK(target_price >= 0),
            current_price REAL NOT NULL DEFAULT 0,
            triggered INTEGER DEFAULT 0,
            triggered_at TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Watchlist table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, symbol),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # News table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT,
            title TEXT NOT NULL,
            summary TEXT,
            url TEXT,
            source TEXT,
            published_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            is_read INTEGER DEFAULT 0,
            UNIQUE(user_id, symbol, title, published_at),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Settings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY(user_id, key),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Portfolio snapshot table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            timestamp TEXT DEFAULT (datetime('now')),
            total_value REAL,
            total_cost REAL,
            total_pnl REAL,
            total_pnl_percent REAL,
            holdings_count INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Run migrations for existing databases
    migrate_price_alerts(cursor)
    migrate_user_scoped_tables(cursor, default_user_id)
    cursor.execute(
        "INSERT OR IGNORE INTO settings (user_id, key, value) VALUES (?, 'finnhub_api_key', ?)",
        [default_user_id, 'd8gqtd9r01qhjpmp8s90d8gqtd9r01qhjpmp8s9g'],
    )
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print(f'Database initialized at: {DB_PATH}')
