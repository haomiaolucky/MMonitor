import aiosqlite
from datetime import datetime, date
from models import Position, PortfolioSnapshot, Trade, TradeAction
from config import Config
from database import get_db
from data.fetcher import get_realtime_quotes
import logging

logger = logging.getLogger(__name__)


async def get_cash() -> float:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT cash FROM account WHERE id = 1")
        row = await cursor.fetchone()
        return row[0] if row else Config.INITIAL_CAPITAL
    finally:
        await db.close()


async def update_cash(new_cash: float):
    db = await get_db()
    try:
        await db.execute("UPDATE account SET cash = ? WHERE id = 1", (new_cash,))
        await db.commit()
    finally:
        await db.close()


async def get_positions() -> list[Position]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM positions")
        rows = await cursor.fetchall()
        positions = []
        symbols = [row[0] for row in rows]

        # 获取当前报价
        quotes = get_realtime_quotes(symbols) if symbols else []
        quote_map = {q.symbol: q.price for q in quotes}

        for row in rows:
            symbol = row[0]
            shares = row[1]
            avg_cost = row[2]
            current_price = quote_map.get(symbol, avg_cost)
            market_value = shares * current_price
            cost_basis = shares * avg_cost
            unrealized_pnl = market_value - cost_basis
            unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0

            positions.append(Position(
                symbol=symbol,
                shares=shares,
                avg_cost=round(avg_cost, 2),
                current_price=round(current_price, 2),
                market_value=round(market_value, 2),
                unrealized_pnl=round(unrealized_pnl, 2),
                unrealized_pnl_pct=round(unrealized_pnl_pct, 2),
                opened_at=datetime.fromisoformat(row[3]),
            ))
        return positions
    finally:
        await db.close()


async def get_position(symbol: str) -> Position | None:
    positions = await get_positions()
    for p in positions:
        if p.symbol == symbol:
            return p
    return None


async def get_portfolio_snapshot() -> PortfolioSnapshot:
    cash = await get_cash()
    positions = await get_positions()
    positions_value = sum(p.market_value for p in positions)
    total_value = cash + positions_value

    db = await get_db()
    try:
        cursor = await db.execute("SELECT initial_capital, peak_value FROM account WHERE id = 1")
        row = await cursor.fetchone()
        initial_capital = row[0]
        peak_value = row[1]

        # 更新峰值
        if total_value > peak_value:
            peak_value = total_value
            await db.execute("UPDATE account SET peak_value = ? WHERE id = 1", (peak_value,))
            await db.commit()

        max_drawdown = ((total_value - peak_value) / peak_value * 100) if peak_value > 0 else 0
        total_pnl = total_value - initial_capital
        total_pnl_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0

        # 日收益 (简化: 用今天第一个快照做基准)
        today = date.today().isoformat()
        cursor = await db.execute(
            "SELECT total_value FROM portfolio_snapshots WHERE timestamp LIKE ? ORDER BY timestamp ASC LIMIT 1",
            (f"{today}%",)
        )
        first_today = await cursor.fetchone()
        if first_today:
            daily_pnl = total_value - first_today[0]
            daily_pnl_pct = (daily_pnl / first_today[0] * 100) if first_today[0] > 0 else 0
        else:
            daily_pnl = 0
            daily_pnl_pct = 0

        return PortfolioSnapshot(
            total_value=round(total_value, 2),
            cash=round(cash, 2),
            positions_value=round(positions_value, 2),
            positions=positions,
            daily_pnl=round(daily_pnl, 2),
            daily_pnl_pct=round(daily_pnl_pct, 2),
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 2),
            max_drawdown=round(max_drawdown, 2),
            timestamp=datetime.now(),
        )
    finally:
        await db.close()


async def save_snapshot(snapshot: PortfolioSnapshot):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO portfolio_snapshots 
               (total_value, cash, positions_value, daily_pnl, daily_pnl_pct, 
                total_pnl, total_pnl_pct, max_drawdown, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot.total_value, snapshot.cash, snapshot.positions_value,
             snapshot.daily_pnl, snapshot.daily_pnl_pct, snapshot.total_pnl,
             snapshot.total_pnl_pct, snapshot.max_drawdown, snapshot.timestamp.isoformat())
        )
        await db.commit()
    finally:
        await db.close()


async def check_daily_trade_limit() -> bool:
    """检查今日交易次数是否超限"""
    db = await get_db()
    try:
        today = date.today().isoformat()
        cursor = await db.execute("SELECT last_trade_date, daily_trades_count FROM account WHERE id = 1")
        row = await cursor.fetchone()
        if row and row[0] == today:
            return row[1] < Config.MAX_DAILY_TRADES
        return True
    finally:
        await db.close()


async def increment_daily_trades():
    db = await get_db()
    try:
        today = date.today().isoformat()
        cursor = await db.execute("SELECT last_trade_date FROM account WHERE id = 1")
        row = await cursor.fetchone()
        if row and row[0] == today:
            await db.execute(
                "UPDATE account SET daily_trades_count = daily_trades_count + 1 WHERE id = 1"
            )
        else:
            await db.execute(
                "UPDATE account SET daily_trades_count = 1, last_trade_date = ? WHERE id = 1",
                (today,)
            )
        await db.commit()
    finally:
        await db.close()
