from datetime import datetime, timedelta
from models import TradeSignal, Trade, TradeAction
from trading.portfolio import (
    get_cash, update_cash, get_positions, get_position,
    get_portfolio_snapshot, check_daily_trade_limit, increment_daily_trades
)
from config import Config
from database import get_db
import logging

logger = logging.getLogger(__name__)


async def execute_signal(signal: TradeSignal, target_amount: float = 0) -> Trade | None:
    """执行交易信号，target_amount > 0 时指定买入金额"""
    if not await check_daily_trade_limit():
        logger.warning(f"已达今日交易上限 ({Config.MAX_DAILY_TRADES})")
        return None

    if signal.action == TradeAction.BUY:
        return await _execute_buy(signal, target_amount)
    elif signal.action == TradeAction.SELL:
        return await _execute_sell(signal)
    return None


# ==================== 金字塔买入 ====================

async def _execute_buy(signal: TradeSignal, target_amount: float = 0) -> Trade | None:
    """执行首次买入 (金字塔第1层, 30%)"""
    cash = await get_cash()
    positions = await get_positions()
    snapshot = await get_portfolio_snapshot()

    if len(positions) >= Config.MAX_HOLDINGS:
        logger.info(f"持仓已满 ({Config.MAX_HOLDINGS})，跳过买入 {signal.symbol}")
        return None

    existing = await get_position(signal.symbol)
    if existing:
        logger.info(f"已持有 {signal.symbol}，跳过重复买入")
        return None

    # 检查冷却期
    if await _has_cooldown(signal.symbol):
        logger.info(f"{signal.symbol} 在止损冷却期内，跳过买入")
        return None

    # 计算计划总投入 (全部3层的总金额)
    if target_amount > 0:
        planned_total = target_amount
    else:
        max_amount = snapshot.total_value * Config.MAX_POSITION_PCT
        planned_total = min(max_amount, cash * 0.9)
        planned_total *= min(1.0, signal.confidence + 0.3)

    # 金字塔第1层: 只买30%
    buy_amount = planned_total * Config.PYRAMID_WEIGHTS[0]
    buy_amount = min(buy_amount, cash - Config.MIN_TRADE_AMOUNT)

    if buy_amount < Config.MIN_TRADE_AMOUNT:
        return None

    price = signal.suggested_amount if signal.suggested_amount > 0 else _get_current_price(signal.symbol)
    if price <= 0:
        return None

    slippage = price * Config.SLIPPAGE_PCT
    fill_price = price + slippage
    commission = Config.COMMISSION_PER_TRADE

    shares = (buy_amount - commission) / fill_price
    actual_amount = shares * fill_price + commission

    if shares <= 0:
        return None

    trade = Trade(
        symbol=signal.symbol,
        action=TradeAction.BUY,
        shares=round(shares, 4),
        price=round(fill_price, 2),
        amount=round(actual_amount, 2),
        commission=commission,
        slippage=round(slippage * shares, 2),
        reason=f"[金字塔L1/30%] {signal.reason}",
        technical_score=signal.technical_score,
        llm_score=signal.llm_score,
        timestamp=datetime.now(),
    )

    await _save_trade(trade)
    await update_cash(cash - actual_amount)
    await _update_position_buy(signal.symbol, shares, fill_price)
    await increment_daily_trades()
    await _create_pyramid_state(signal.symbol, fill_price, planned_total, actual_amount)
    await _log_audit("BUY", signal.symbol,
                     f"金字塔L1 买入 {shares:.2f}股 @ ${fill_price:.2f} (计划总额${planned_total:.0f})")

    logger.info(f"✅ 金字塔L1 买入 {signal.symbol}: {shares:.2f}股 @ ${fill_price:.2f}")
    return trade


async def check_pyramid_additions() -> list[Trade]:
    """检查是否需要金字塔加仓 (L2/L3)"""
    trades = []
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT symbol, level, entry_price, planned_amount, invested_amount FROM pyramid_states WHERE status = 'active' AND level < 3"
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()

    for row in rows:
        symbol, current_level, entry_price, planned_amount, invested_amount = row
        next_level = current_level + 1  # 2 or 3 (1-indexed)
        trigger_pct = Config.PYRAMID_DROP_TRIGGERS[next_level - 1]  # -0.05 or -0.10
        trigger_price = entry_price * (1 + trigger_pct)

        # 获取当前价格
        current_price = _get_current_price(symbol)
        if current_price <= 0:
            continue

        # 价格跌到触发点 → 加仓
        if current_price <= trigger_price:
            weight = Config.PYRAMID_WEIGHTS[next_level - 1]
            buy_amount = planned_amount * weight
            cash = await get_cash()
            buy_amount = min(buy_amount, cash - Config.MIN_TRADE_AMOUNT)

            if buy_amount < Config.MIN_TRADE_AMOUNT:
                logger.info(f"{symbol} 金字塔L{next_level} 现金不足，跳过")
                continue

            if not await check_daily_trade_limit():
                continue

            slippage = current_price * Config.SLIPPAGE_PCT
            fill_price = current_price + slippage
            commission = Config.COMMISSION_PER_TRADE
            shares = (buy_amount - commission) / fill_price
            actual_amount = shares * fill_price + commission

            trade = Trade(
                symbol=symbol,
                action=TradeAction.BUY,
                shares=round(shares, 4),
                price=round(fill_price, 2),
                amount=round(actual_amount, 2),
                commission=commission,
                slippage=round(slippage * shares, 2),
                reason=f"[金字塔L{next_level}/{int(weight*100)}%] 价格跌至${current_price:.2f} (入场价${entry_price:.2f}跌{trigger_pct*100:.0f}%)，自动加仓",
                technical_score=0,
                llm_score=0,
                timestamp=datetime.now(),
            )

            await _save_trade(trade)
            await update_cash(cash - actual_amount)
            await _update_position_buy(symbol, shares, fill_price)
            await increment_daily_trades()
            await _update_pyramid_level(symbol, next_level, actual_amount)
            await _log_audit("PYRAMID_ADD", symbol,
                             f"金字塔L{next_level} 加仓 {shares:.2f}股 @ ${fill_price:.2f}")

            logger.info(f"✅ 金字塔L{next_level} 加仓 {symbol}: {shares:.2f}股 @ ${fill_price:.2f}")
            trades.append(trade)

    return trades


# ==================== 分阶段止盈 / 止损 ====================

async def _execute_sell(signal: TradeSignal, sell_shares: float = 0) -> Trade | None:
    """执行卖出, sell_shares > 0 时部分卖出"""
    position = await get_position(signal.symbol)
    if not position:
        logger.info(f"未持有 {signal.symbol}，无法卖出")
        return None

    cash = await get_cash()
    price = position.current_price
    slippage = price * Config.SLIPPAGE_PCT
    fill_price = price - slippage
    commission = Config.COMMISSION_PER_TRADE

    shares = sell_shares if sell_shares > 0 else position.shares
    shares = min(shares, position.shares)
    actual_amount = shares * fill_price - commission
    is_partial = shares < position.shares

    trade = Trade(
        symbol=signal.symbol,
        action=TradeAction.SELL,
        shares=round(shares, 4),
        price=round(fill_price, 2),
        amount=round(actual_amount, 2),
        commission=commission,
        slippage=round(slippage * shares, 2),
        reason=signal.reason,
        technical_score=signal.technical_score,
        llm_score=signal.llm_score,
        timestamp=datetime.now(),
    )

    await _save_trade(trade)
    await update_cash(cash + actual_amount)

    if is_partial:
        await _update_position_reduce(signal.symbol, shares)
    else:
        await _remove_position(signal.symbol)
        await _complete_pyramid_state(signal.symbol)

    await increment_daily_trades()

    pnl = actual_amount - (shares * position.avg_cost)
    sell_type = f"部分卖出({shares:.2f}/{position.shares:.2f})" if is_partial else f"全部卖出({shares:.2f})"
    await _log_audit("SELL", signal.symbol,
                     f"{sell_type} @ ${fill_price:.2f}, 盈亏: ${pnl:.2f}, 原因: {signal.reason}")

    logger.info(f"✅ {sell_type} {signal.symbol} @ ${fill_price:.2f}, 盈亏: ${pnl:.2f}")
    return trade


async def check_stop_loss_take_profit() -> list[Trade]:
    """检查止损 (从入场价算) + 分阶段止盈 (从均价算)"""
    positions = await get_positions()
    trades = []

    for pos in positions:
        # 获取金字塔入场价 (止损基准)
        pyramid = await _get_pyramid_state(pos.symbol)
        entry_price = pyramid["entry_price"] if pyramid else pos.avg_cost
        drop_from_entry = ((pos.current_price - entry_price) / entry_price) * 100

        # === 止损: 从入场价跌15% ===
        if drop_from_entry <= Config.STOP_LOSS_PCT * 100:
            signal = TradeSignal(
                symbol=pos.symbol,
                action=TradeAction.SELL,
                confidence=1.0,
                technical_score=0, llm_score=0, combined_score=-1.0,
                reason=f"🛑 止损触发: 从入场价${entry_price:.2f}跌{drop_from_entry:.1f}% (现价${pos.current_price:.2f})",
                suggested_amount=0,
                timestamp=datetime.now(),
            )
            trade = await _execute_sell(signal)
            if trade:
                await _create_cooldown(pos.symbol, pos.current_price, entry_price)
                trades.append(trade)
            continue

        # === 二阶段止盈 +30%: 已减半的清仓 ===
        if pos.unrealized_pnl_pct >= Config.TAKE_PROFIT_ALL_PCT * 100:
            has_half_sold = await _has_profit_take(pos.symbol, "half")
            if has_half_sold:
                signal = TradeSignal(
                    symbol=pos.symbol,
                    action=TradeAction.SELL,
                    confidence=0.9,
                    technical_score=0, llm_score=0, combined_score=-0.5,
                    reason=f"💰💰 二阶段止盈: 盈利{pos.unrealized_pnl_pct:.1f}% (≥{Config.TAKE_PROFIT_ALL_PCT*100:.0f}%)，清仓剩余",
                    suggested_amount=0,
                    timestamp=datetime.now(),
                )
                trade = await _execute_sell(signal)
                if trade:
                    await _record_profit_take(pos.symbol, "full", pos.avg_cost,
                                              pos.shares, pos.shares, pos.current_price, 0)
                    trades.append(trade)
            else:
                # 没减过半仓直接涨到30%，先减半
                half_shares = round(pos.shares / 2, 4)
                signal = TradeSignal(
                    symbol=pos.symbol,
                    action=TradeAction.SELL,
                    confidence=0.85,
                    technical_score=0, llm_score=0, combined_score=-0.3,
                    reason=f"💰 一阶段止盈: 盈利{pos.unrealized_pnl_pct:.1f}% (≥{Config.TAKE_PROFIT_PCT*100:.0f}%)，卖出一半锁定利润",
                    suggested_amount=0,
                    timestamp=datetime.now(),
                )
                trade = await _execute_sell(signal, sell_shares=half_shares)
                if trade:
                    remaining = pos.shares - half_shares
                    await _record_profit_take(pos.symbol, "half", pos.avg_cost,
                                              pos.shares, half_shares, pos.current_price, remaining)
                    trades.append(trade)

        # === 一阶段止盈 +15%: 卖一半 ===
        elif pos.unrealized_pnl_pct >= Config.TAKE_PROFIT_PCT * 100:
            has_half_sold = await _has_profit_take(pos.symbol, "half")
            if not has_half_sold:
                half_shares = round(pos.shares / 2, 4)
                signal = TradeSignal(
                    symbol=pos.symbol,
                    action=TradeAction.SELL,
                    confidence=0.8,
                    technical_score=0, llm_score=0, combined_score=-0.3,
                    reason=f"💰 一阶段止盈: 盈利{pos.unrealized_pnl_pct:.1f}% (≥{Config.TAKE_PROFIT_PCT*100:.0f}%)，卖出一半锁定利润",
                    suggested_amount=0,
                    timestamp=datetime.now(),
                )
                trade = await _execute_sell(signal, sell_shares=half_shares)
                if trade:
                    remaining = pos.shares - half_shares
                    await _record_profit_take(pos.symbol, "half", pos.avg_cost,
                                              pos.shares, half_shares, pos.current_price, remaining)
                    trades.append(trade)

    return trades


# ==================== 再入场检测 ====================

async def check_rebuy_opportunities() -> list[dict]:
    """
    检查再入场机会:
    1. 止盈清仓后，从卖出价回调18% → 自动开始新金字塔
    2. 止损冷却到期后 → 发出提醒 (不自动买，由策略引擎决定)
    """
    opportunities = []

    # --- 止盈后高点回调再入场 ---
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT symbol, original_cost, sold_price, timestamp FROM profit_takes WHERE stage = 'full' ORDER BY timestamp DESC LIMIT 20"
        )
        profit_rows = await cursor.fetchall()
    finally:
        await db.close()

    for row in profit_rows:
        symbol, original_cost, sold_price, ts = row[0], row[1], row[2], row[3]

        existing = await get_position(symbol)
        if existing:
            continue

        from data.fetcher import get_realtime_quotes
        quotes = get_realtime_quotes([symbol])
        if not quotes:
            continue
        current_price = quotes[0].price

        # 从卖出价回调18%
        rebuy_price = sold_price * (1 - Config.REBUY_FROM_PEAK_PCT)
        if current_price <= rebuy_price:
            drop_pct = ((current_price - sold_price) / sold_price) * 100
            opportunities.append({
                "symbol": symbol,
                "current_price": current_price,
                "sold_price": sold_price,
                "original_cost": original_cost,
                "drop_from_sell": round(drop_pct, 2),
                "alert_type": "rebuy_profit",
                "message": f"🔄 回调再入场: {symbol} 从止盈价${sold_price:.2f}回调{abs(drop_pct):.1f}%至${current_price:.2f}，可开启新金字塔",
            })

    # --- 止损冷却到期提醒 ---
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "SELECT symbol, stop_loss_price, original_entry_price, cooldown_until FROM stop_loss_cooldowns WHERE cooldown_until <= ?",
            (now,)
        )
        cooldown_rows = await cursor.fetchall()
    finally:
        await db.close()

    for row in cooldown_rows:
        symbol, sl_price, entry_price, cooldown_until = row[0], row[1], row[2], row[3]

        existing = await get_position(symbol)
        if existing:
            continue

        from data.fetcher import get_realtime_quotes
        quotes = get_realtime_quotes([symbol])
        if not quotes:
            continue
        current_price = quotes[0].price

        change_from_sl = ((current_price - sl_price) / sl_price) * 100
        opportunities.append({
            "symbol": symbol,
            "current_price": current_price,
            "stop_loss_price": sl_price,
            "original_entry_price": entry_price,
            "change_from_sl": round(change_from_sl, 2),
            "alert_type": "cooldown_expired",
            "message": f"⏰ 冷却到期: {symbol} 止损价${sl_price:.2f}，现价${current_price:.2f} ({change_from_sl:+.1f}%)，可重新评估",
        })
        # 清除已过期的冷却
        db = await get_db()
        try:
            await db.execute("DELETE FROM stop_loss_cooldowns WHERE symbol = ?", (symbol,))
            await db.commit()
        finally:
            await db.close()

    return opportunities


# ==================== 辅助函数 ====================

def _get_current_price(symbol: str) -> float:
    from data.fetcher import get_realtime_quotes
    quotes = get_realtime_quotes([symbol])
    return quotes[0].price if quotes else 0


async def _save_trade(trade: Trade):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO trades (symbol, action, shares, price, amount, commission,
               slippage, reason, technical_score, llm_score, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trade.symbol, trade.action.value, trade.shares, trade.price,
             trade.amount, trade.commission, trade.slippage, trade.reason,
             trade.technical_score, trade.llm_score, trade.timestamp.isoformat())
        )
        await db.commit()
    finally:
        await db.close()


async def _update_position_buy(symbol: str, shares: float, price: float):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT shares, avg_cost FROM positions WHERE symbol = ?", (symbol,))
        row = await cursor.fetchone()
        now = datetime.now().isoformat()

        if row:
            old_shares, old_cost = row[0], row[1]
            new_shares = old_shares + shares
            new_avg_cost = (old_shares * old_cost + shares * price) / new_shares
            await db.execute(
                "UPDATE positions SET shares = ?, avg_cost = ?, updated_at = ? WHERE symbol = ?",
                (new_shares, new_avg_cost, now, symbol)
            )
        else:
            await db.execute(
                "INSERT INTO positions (symbol, shares, avg_cost, opened_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (symbol, shares, price, now, now)
            )
        await db.commit()
    finally:
        await db.close()


async def _update_position_reduce(symbol: str, sold_shares: float):
    """减少持仓 (部分卖出)"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT shares FROM positions WHERE symbol = ?", (symbol,))
        row = await cursor.fetchone()
        if row:
            remaining = row[0] - sold_shares
            if remaining <= 0.0001:
                await db.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
            else:
                now = datetime.now().isoformat()
                await db.execute(
                    "UPDATE positions SET shares = ?, updated_at = ? WHERE symbol = ?",
                    (remaining, now, symbol)
                )
        await db.commit()
    finally:
        await db.close()


async def _remove_position(symbol: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        await db.commit()
    finally:
        await db.close()


async def _log_audit(event_type: str, symbol: str, details: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO audit_log (event_type, symbol, details, timestamp) VALUES (?, ?, ?, ?)",
            (event_type, symbol, details, datetime.now().isoformat())
        )
        await db.commit()
    finally:
        await db.close()


# --- 金字塔状态管理 ---

async def _get_pyramid_state(symbol: str) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM pyramid_states WHERE symbol = ?", (symbol,))
        row = await cursor.fetchone()
        if row:
            return {"symbol": row[0], "level": row[1], "entry_price": row[2],
                    "planned_amount": row[3], "invested_amount": row[4], "status": row[5]}
        return None
    finally:
        await db.close()


async def _create_pyramid_state(symbol: str, entry_price: float, planned_amount: float, invested: float):
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        await db.execute(
            """INSERT OR REPLACE INTO pyramid_states
               (symbol, level, entry_price, planned_amount, invested_amount, status, created_at, updated_at)
               VALUES (?, 1, ?, ?, ?, 'active', ?, ?)""",
            (symbol, entry_price, planned_amount, invested, now, now)
        )
        await db.commit()
    finally:
        await db.close()


async def _update_pyramid_level(symbol: str, new_level: int, additional_invested: float):
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        await db.execute(
            "UPDATE pyramid_states SET level = ?, invested_amount = invested_amount + ?, updated_at = ? WHERE symbol = ?",
            (new_level, additional_invested, now, symbol)
        )
        await db.commit()
    finally:
        await db.close()


async def _complete_pyramid_state(symbol: str):
    """清仓时标记金字塔完成"""
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        await db.execute(
            "UPDATE pyramid_states SET status = 'completed', updated_at = ? WHERE symbol = ?",
            (now, symbol)
        )
        await db.commit()
    finally:
        await db.close()


# --- 止盈记录 ---

async def _has_profit_take(symbol: str, stage: str) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM profit_takes WHERE symbol = ? AND stage = ?", (symbol, stage)
        )
        return (await cursor.fetchone())[0] > 0
    finally:
        await db.close()


async def _record_profit_take(symbol: str, stage: str, original_cost: float,
                               original_shares: float, sold_shares: float,
                               sold_price: float, remaining_shares: float):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO profit_takes (symbol, stage, original_cost, original_shares,
               sold_shares, sold_price, remaining_shares, timestamp) VALUES (?,?,?,?,?,?,?,?)""",
            (symbol, stage, original_cost, original_shares, sold_shares,
             sold_price, remaining_shares, datetime.now().isoformat())
        )
        await db.commit()
    finally:
        await db.close()


# --- 止损冷却 ---

async def _has_cooldown(symbol: str) -> bool:
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM stop_loss_cooldowns WHERE symbol = ? AND cooldown_until > ?",
            (symbol, now)
        )
        return (await cursor.fetchone())[0] > 0
    finally:
        await db.close()


async def _create_cooldown(symbol: str, stop_loss_price: float, entry_price: float):
    db = await get_db()
    try:
        now = datetime.now()
        cooldown_until = now + timedelta(days=Config.STOP_LOSS_COOLDOWN_DAYS)
        await db.execute(
            """INSERT OR REPLACE INTO stop_loss_cooldowns
               (symbol, stop_loss_price, original_entry_price, stop_loss_date, cooldown_until)
               VALUES (?, ?, ?, ?, ?)""",
            (symbol, stop_loss_price, entry_price, now.isoformat(), cooldown_until.isoformat())
        )
        await db.commit()
        logger.warning(f"⏸️ {symbol} 止损冷却 {Config.STOP_LOSS_COOLDOWN_DAYS} 天，至 {cooldown_until.strftime('%m/%d')}")
    finally:
        await db.close()
