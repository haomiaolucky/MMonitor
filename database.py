import aiosqlite
import os
from config import Config


async def get_db() -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(Config.DB_PATH), exist_ok=True)
    db = await aiosqlite.connect(Config.DB_PATH, timeout=30)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA busy_timeout=30000")
    # WAL mode and shared locking don't work on Azure Files (SMB)
    if os.getenv("DB_PATH"):
        await db.execute("PRAGMA journal_mode=DELETE")
        await db.execute("PRAGMA locking_mode=EXCLUSIVE")
    else:
        await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


_CREATE_TABLES = [
    """CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        action TEXT NOT NULL,
        shares REAL NOT NULL,
        price REAL NOT NULL,
        amount REAL NOT NULL,
        commission REAL DEFAULT 0,
        slippage REAL DEFAULT 0,
        reason TEXT,
        technical_score REAL,
        llm_score REAL,
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS positions (
        symbol TEXT PRIMARY KEY,
        shares REAL NOT NULL,
        avg_cost REAL NOT NULL,
        opened_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        total_value REAL NOT NULL,
        cash REAL NOT NULL,
        positions_value REAL NOT NULL,
        daily_pnl REAL DEFAULT 0,
        daily_pnl_pct REAL DEFAULT 0,
        total_pnl REAL DEFAULT 0,
        total_pnl_pct REAL DEFAULT 0,
        max_drawdown REAL DEFAULT 0,
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS account (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        cash REAL NOT NULL,
        initial_capital REAL NOT NULL,
        peak_value REAL NOT NULL,
        daily_trades_count INTEGER DEFAULT 0,
        last_trade_date TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        symbol TEXT,
        details TEXT,
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_type TEXT NOT NULL,
        symbol TEXT NOT NULL,
        value REAL NOT NULL,
        threshold REAL NOT NULL,
        message TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS profit_takes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        stage TEXT NOT NULL,
        original_cost REAL NOT NULL,
        original_shares REAL NOT NULL,
        sold_shares REAL NOT NULL,
        sold_price REAL NOT NULL,
        remaining_shares REAL NOT NULL,
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pyramid_states (
        symbol TEXT PRIMARY KEY,
        level INTEGER NOT NULL DEFAULT 1,
        entry_price REAL NOT NULL,
        planned_amount REAL NOT NULL,
        invested_amount REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trend_pyramid_states (
        symbol TEXT PRIMARY KEY,
        level INTEGER NOT NULL DEFAULT 0,
        entry_price REAL NOT NULL,
        planned_amount REAL NOT NULL,
        invested_amount REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS stop_loss_cooldowns (
        symbol TEXT PRIMARY KEY,
        stop_loss_price REAL NOT NULL,
        original_entry_price REAL NOT NULL,
        stop_loss_date TEXT NOT NULL,
        cooldown_until TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS qqq_account (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        cash REAL NOT NULL,
        initial_capital REAL NOT NULL,
        peak_value REAL NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS qqq_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        action TEXT NOT NULL,
        shares REAL NOT NULL,
        price REAL NOT NULL,
        amount REAL NOT NULL,
        reason TEXT,
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS qqq_positions (
        symbol TEXT PRIMARY KEY,
        shares REAL NOT NULL,
        avg_cost REAL NOT NULL,
        opened_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS qqq_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        total_value REAL NOT NULL,
        cash REAL NOT NULL,
        positions_value REAL NOT NULL,
        holding TEXT NOT NULL,
        total_pnl REAL DEFAULT 0,
        total_pnl_pct REAL DEFAULT 0,
        max_drawdown REAL DEFAULT 0,
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS qqq_rotation_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        current_holding TEXT NOT NULL DEFAULT 'NONE',
        vix_spike_started_at TEXT,
        last_switch_date TEXT,
        last_switch_reason TEXT
    )""",
]


async def init_db():
    db = await get_db()
    try:
        for sql in _CREATE_TABLES:
            await db.execute(sql)
        await db.commit()

        # 初始化账户
        cursor = await db.execute("SELECT COUNT(*) FROM account")
        count = (await cursor.fetchone())[0]
        if count == 0:
            from datetime import datetime
            now = datetime.now().isoformat()
            await db.execute(
                "INSERT INTO account (id, cash, initial_capital, peak_value, created_at) VALUES (1, ?, ?, ?, ?)",
                (Config.INITIAL_CAPITAL, Config.INITIAL_CAPITAL, Config.INITIAL_CAPITAL, now)
            )
            await db.commit()
    finally:
        await db.close()