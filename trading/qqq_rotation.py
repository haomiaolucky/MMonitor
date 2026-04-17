"""QQQ/TQQQ 轮动策略账户 (Account 2 - Benchmark)

Rules:
- Default: Hold QQQ
- Switch to TQQQ when: QQQ > 200-day SMA AND VIX < 22
- Switch to TQQQ when: VIX was > 35 and drops back below 28 (panic recovery)
- Switch back to QQQ when: QQQ < 200-day SMA OR VIX > 25
- Max 1 switch per day (debounce)
- Only execute during market hours
"""

import logging
from datetime import datetime, date
from data.fetcher import get_history, get_realtime_quotes, get_vix
from database import get_db
from config import Config
from utils import is_market_open

logger = logging.getLogger(__name__)

# SMA200 daily cache: (date, value)
_sma200_cache: tuple[date, float] | None = None


def get_qqq_sma200() -> float | None:
    """Get QQQ 200-day SMA, cached per trading day"""
    global _sma200_cache
    today = date.today()

    if _sma200_cache and _sma200_cache[0] == today:
        return _sma200_cache[1]

    try:
        df = get_history("QQQ", period="1y", interval="1d")
        if df is None or len(df) < Config.QQQ_SMA_PERIOD:
            logger.warning(f"QQQ history insufficient: {len(df) if df is not None else 0} bars (need {Config.QQQ_SMA_PERIOD})")
            return None
        close = df["Close"].squeeze()
        sma = float(close.rolling(Config.QQQ_SMA_PERIOD).mean().iloc[-1])
        _sma200_cache = (today, round(sma, 2))
        return round(sma, 2)
    except Exception as e:
        logger.error(f"Error calculating QQQ SMA200: {e}")
        return None


async def init_qqq_account() -> dict:
    """Initialize QQQ account: deposit capital, buy QQQ"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) FROM qqq_account")
        count = (await cursor.fetchone())[0]
        if count > 0:
            return {"status": "already_initialized"}

        now = datetime.now().isoformat()
        capital = Config.QQQ_INITIAL_CAPITAL

        await db.execute(
            "INSERT INTO qqq_account (id, cash, initial_capital, peak_value, created_at) VALUES (1, ?, ?, ?, ?)",
            (capital, capital, capital, now),
        )
        await db.execute(
            "INSERT INTO qqq_rotation_state (id, current_holding) VALUES (1, 'NONE')",
        )
        await db.commit()
    finally:
        await db.close()

    # Buy QQQ with all capital
    trade = await _buy_etf("QQQ", capital, "🏁 初始建仓: $50,000 → QQQ")
    if trade:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE qqq_rotation_state SET current_holding = 'QQQ' WHERE id = 1"
            )
            await db.commit()
        finally:
            await db.close()

    return {"status": "initialized", "trade": trade}


async def _buy_etf(symbol: str, amount: float, reason: str) -> dict | None:
    """Buy an ETF with given amount"""
    quotes = get_realtime_quotes([symbol])
    if not quotes:
        logger.error(f"Cannot get quote for {symbol}")
        return None

    price = quotes[0].price
    slippage = price * Config.SLIPPAGE_PCT
    fill_price = price + slippage
    shares = (amount - Config.COMMISSION_PER_TRADE) / fill_price
    actual_amount = shares * fill_price

    if shares <= 0:
        return None

    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO qqq_trades (symbol, action, shares, price, amount, reason, timestamp) VALUES (?,?,?,?,?,?,?)",
            (symbol, "BUY", round(shares, 4), round(fill_price, 2), round(actual_amount, 2), reason, now),
        )

        cursor = await db.execute("SELECT shares, avg_cost FROM qqq_positions WHERE symbol = ?", (symbol,))
        existing = await cursor.fetchone()
        if existing:
            old_shares, old_cost = existing
            new_shares = old_shares + shares
            new_cost = (old_shares * old_cost + shares * fill_price) / new_shares
            await db.execute(
                "UPDATE qqq_positions SET shares = ?, avg_cost = ? WHERE symbol = ?",
                (round(new_shares, 4), round(new_cost, 2), symbol),
            )
        else:
            await db.execute(
                "INSERT INTO qqq_positions (symbol, shares, avg_cost, opened_at) VALUES (?,?,?,?)",
                (symbol, round(shares, 4), round(fill_price, 2), now),
            )

        cursor = await db.execute("SELECT cash FROM qqq_account WHERE id = 1")
        cash = (await cursor.fetchone())[0]
        await db.execute("UPDATE qqq_account SET cash = ? WHERE id = 1", (round(cash - actual_amount, 2),))
        await db.commit()

        logger.info(f"✅ QQQ账户 买入 {symbol}: {shares:.2f}股 @ ${fill_price:.2f}")
        return {
            "symbol": symbol, "action": "BUY", "shares": round(shares, 4),
            "price": round(fill_price, 2), "amount": round(actual_amount, 2),
            "reason": reason, "timestamp": now,
        }
    finally:
        await db.close()


async def _sell_etf(symbol: str, reason: str) -> dict | None:
    """Sell all shares of an ETF"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT shares FROM qqq_positions WHERE symbol = ?", (symbol,))
        pos = await cursor.fetchone()
        if not pos:
            return None
        shares = pos[0]
    finally:
        await db.close()

    quotes = get_realtime_quotes([symbol])
    if not quotes:
        logger.error(f"Cannot get quote for {symbol}")
        return None

    price = quotes[0].price
    slippage = price * Config.SLIPPAGE_PCT
    fill_price = price - slippage
    actual_amount = shares * fill_price
    now = datetime.now().isoformat()

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO qqq_trades (symbol, action, shares, price, amount, reason, timestamp) VALUES (?,?,?,?,?,?,?)",
            (symbol, "SELL", round(shares, 4), round(fill_price, 2), round(actual_amount, 2), reason, now),
        )
        await db.execute("DELETE FROM qqq_positions WHERE symbol = ?", (symbol,))

        cursor = await db.execute("SELECT cash FROM qqq_account WHERE id = 1")
        cash = (await cursor.fetchone())[0]
        await db.execute("UPDATE qqq_account SET cash = ? WHERE id = 1", (round(cash + actual_amount, 2),))
        await db.commit()

        logger.info(f"✅ QQQ账户 卖出 {symbol}: {shares:.2f}股 @ ${fill_price:.2f}")
        return {
            "symbol": symbol, "action": "SELL", "shares": round(shares, 4),
            "price": round(fill_price, 2), "amount": round(actual_amount, 2),
            "reason": reason, "timestamp": now,
        }
    finally:
        await db.close()


async def check_rotation() -> dict | None:
    """Check rotation signal and execute if needed. Returns rotation info or None."""
    if not is_market_open():
        return None

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT current_holding, vix_spike_started_at, last_switch_date FROM qqq_rotation_state WHERE id = 1"
        )
        state = await cursor.fetchone()
        if not state:
            return None

        current = state[0]
        spike_started = state[1]
        last_switch = state[2]

        if current == "NONE":
            return None

        # Debounce: max 1 switch per day
        today = date.today().isoformat()
        if last_switch == today:
            return None
    finally:
        await db.close()

    # Get market signals
    vix = get_vix()
    sma200 = get_qqq_sma200()
    if vix is None:
        return None

    qqq_quotes = get_realtime_quotes(["QQQ"])
    if not qqq_quotes:
        return None
    qqq_price = qqq_quotes[0].price

    # Track VIX spike
    db = await get_db()
    try:
        if vix >= Config.QQQ_VIX_SPIKE_THRESHOLD and spike_started is None:
            await db.execute(
                "UPDATE qqq_rotation_state SET vix_spike_started_at = ? WHERE id = 1",
                (datetime.now().isoformat(),),
            )
            await db.commit()
            logger.info(f"🔴 VIX恐慌标记: VIX={vix} >= {Config.QQQ_VIX_SPIKE_THRESHOLD}")
    finally:
        await db.close()

    # Check spike expiry
    spike_active = False
    if spike_started:
        spike_dt = datetime.fromisoformat(spike_started)
        if (datetime.now() - spike_dt).days <= Config.QQQ_VIX_SPIKE_EXPIRY_DAYS:
            spike_active = True
        else:
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE qqq_rotation_state SET vix_spike_started_at = NULL WHERE id = 1"
                )
                await db.commit()
            finally:
                await db.close()

    # Determine target
    target = current
    reason = ""
    above_sma = sma200 is not None and qqq_price > sma200
    sma_str = f"${sma200:.2f}" if sma200 else "N/A"

    if current == "QQQ":
        if above_sma and vix < Config.QQQ_TQQQ_ENTER_VIX:
            target = "TQQQ"
            reason = f"🚀 趋势+低波动: QQQ ${qqq_price:.2f} > SMA200 {sma_str}, VIX={vix:.1f}<{Config.QQQ_TQQQ_ENTER_VIX}"
        elif spike_active and vix < Config.QQQ_VIX_SPIKE_RECOVERY:
            target = "TQQQ"
            reason = f"🚀 恐慌退潮: VIX从35+回落到{vix:.1f}<{Config.QQQ_VIX_SPIKE_RECOVERY}, 上TQQQ抓反弹"
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE qqq_rotation_state SET vix_spike_started_at = NULL WHERE id = 1"
                )
                await db.commit()
            finally:
                await db.close()

    elif current == "TQQQ":
        if not above_sma:
            target = "QQQ"
            reason = f"🛡️ 趋势转弱: QQQ ${qqq_price:.2f} < SMA200 {sma_str}, 切回QQQ"
        elif vix > Config.QQQ_TQQQ_EXIT_VIX:
            target = "QQQ"
            reason = f"🛡️ 波动升高: VIX={vix:.1f}>{Config.QQQ_TQQQ_EXIT_VIX}, 切回QQQ"

    if target == current:
        return None

    # Execute rotation: sell current → buy target
    logger.info(f"🔄 QQQ账户轮动: {current} → {target} | {reason}")

    sell_trade = await _sell_etf(current, f"[轮动] {reason}")
    if not sell_trade:
        logger.error(f"轮动卖出 {current} 失败")
        return None

    db = await get_db()
    try:
        cursor = await db.execute("SELECT cash FROM qqq_account WHERE id = 1")
        cash = (await cursor.fetchone())[0]
    finally:
        await db.close()

    buy_trade = await _buy_etf(target, cash, f"[轮动] {reason}")
    if not buy_trade:
        logger.error(f"轮动买入 {target} 失败! 账户暂为全现金")
        db = await get_db()
        try:
            await db.execute(
                "UPDATE qqq_rotation_state SET current_holding = 'CASH', last_switch_date = ? WHERE id = 1",
                (today,),
            )
            await db.commit()
        finally:
            await db.close()
        return {"from": current, "to": "CASH", "reason": reason, "error": "buy_failed"}

    # Update state
    db = await get_db()
    try:
        await db.execute(
            "UPDATE qqq_rotation_state SET current_holding = ?, last_switch_date = ?, last_switch_reason = ? WHERE id = 1",
            (target, today, reason),
        )
        await db.commit()
    finally:
        await db.close()

    return {"from": current, "to": target, "reason": reason, "sell": sell_trade, "buy": buy_trade}


async def get_qqq_portfolio() -> dict:
    """Get QQQ account portfolio snapshot"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT cash, initial_capital, peak_value FROM qqq_account WHERE id = 1")
        acc = await cursor.fetchone()
        if not acc:
            return {"status": "not_initialized"}

        cash, initial_capital, peak_value = acc

        cursor = await db.execute("SELECT symbol, shares, avg_cost, opened_at FROM qqq_positions")
        rows = await cursor.fetchall()

        positions = []
        positions_value = 0.0
        for row in rows:
            symbol, shares, avg_cost, opened_at = row
            quotes = get_realtime_quotes([symbol])
            cp = quotes[0].price if quotes else avg_cost
            mv = shares * cp
            pnl = mv - shares * avg_cost
            pnl_pct = (pnl / (shares * avg_cost) * 100) if avg_cost > 0 else 0
            positions_value += mv
            positions.append({
                "symbol": symbol, "shares": round(shares, 4),
                "avg_cost": round(avg_cost, 2), "current_price": round(cp, 2),
                "market_value": round(mv, 2),
                "unrealized_pnl": round(pnl, 2), "unrealized_pnl_pct": round(pnl_pct, 2),
            })

        total_value = cash + positions_value
        if total_value > peak_value:
            peak_value = total_value
            await db.execute("UPDATE qqq_account SET peak_value = ? WHERE id = 1", (peak_value,))
            await db.commit()

        max_dd = ((total_value - peak_value) / peak_value * 100) if peak_value > 0 else 0
        total_pnl = total_value - initial_capital
        total_pnl_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0

        cursor = await db.execute(
            "SELECT current_holding, last_switch_date, last_switch_reason FROM qqq_rotation_state WHERE id = 1"
        )
        state = await cursor.fetchone()

        return {
            "total_value": round(total_value, 2),
            "cash": round(cash, 2),
            "positions_value": round(positions_value, 2),
            "positions": positions,
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "max_drawdown": round(max_dd, 2),
            "current_holding": state[0] if state else "NONE",
            "last_switch_date": state[1] if state else None,
            "last_switch_reason": state[2] if state else None,
            "timestamp": datetime.now().isoformat(),
        }
    finally:
        await db.close()


async def save_qqq_snapshot():
    """Save QQQ account snapshot to DB"""
    p = await get_qqq_portfolio()
    if "status" in p:
        return

    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO qqq_snapshots
               (total_value, cash, positions_value, holding, total_pnl, total_pnl_pct, max_drawdown, timestamp)
               VALUES (?,?,?,?,?,?,?,?)""",
            (p["total_value"], p["cash"], p["positions_value"],
             p["current_holding"], p["total_pnl"], p["total_pnl_pct"],
             p["max_drawdown"], p["timestamp"]),
        )
        await db.commit()
    finally:
        await db.close()
