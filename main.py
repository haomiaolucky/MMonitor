import sys
import os
import asyncio
import json
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from database import init_db
from data.fetcher import get_realtime_quotes, get_history, get_vix
from data.stocks import get_symbols, AI_STOCKS
from models import TradeAction
from strategy.engine import generate_signals
from utils import get_market_status
from trading.executor import execute_signal, check_stop_loss_take_profit, check_rebuy_opportunities, check_pyramid_additions, check_trend_pyramid_additions, check_rotation_swap
from trading.portfolio import get_portfolio_snapshot, save_snapshot, get_positions, get_cash
from trading.qqq_rotation import check_rotation, get_qqq_portfolio, save_qqq_snapshot, init_qqq_account, get_qqq_sma200
from notify import notify_trade, notify_alert, notify_rotation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
connected_clients: list[WebSocket] = []
# VIX 警报冷却: alert_type -> last_alert_timestamp
_alert_cooldowns: dict[str, datetime] = {}


async def broadcast(data: dict):
    """向所有连接的客户端广播消息"""
    message = json.dumps(data, default=str, ensure_ascii=False)
    disconnected = []
    for ws in connected_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        connected_clients.remove(ws)


async def scheduled_fetch():
    """定时获取行情"""
    try:
        quotes = get_realtime_quotes()
        await broadcast({
            "type": "quotes",
            "data": [q.model_dump() for q in quotes]
        })
    except Exception as e:
        logger.error(f"定时获取行情失败: {e}")


async def scheduled_vix_check():
    """定时检查 VIX 恐慌指数"""
    try:
        vix = get_vix()
        if vix is None:
            return

        await broadcast({"type": "vix", "data": {"value": vix}})

        now = datetime.now()
        cooldown = Config.ALERT_COOLDOWN_SECONDS
        alert_msg = None
        alert_type = None

        if vix <= Config.VIX_LOW_THRESHOLD:
            alert_type = "vix_low"
            alert_msg = f"🟢 VIX 低波动警报: VIX={vix} (≤{Config.VIX_LOW_THRESHOLD}) — 市场情绪乐观，可能过度自满"
        elif vix >= Config.VIX_HIGH_THRESHOLD:
            alert_type = "vix_high"
            alert_msg = f"🔴 VIX 高波动警报: VIX={vix} (≥{Config.VIX_HIGH_THRESHOLD}) — 市场恐慌，注意风险"

        if alert_type and alert_msg:
            last = _alert_cooldowns.get(alert_type)
            if last is None or (now - last).total_seconds() >= cooldown:
                _alert_cooldowns[alert_type] = now
                logger.warning(alert_msg)
                await broadcast({"type": "alert", "data": {
                    "alert_type": alert_type, "symbol": "^VIX",
                    "value": vix, "message": alert_msg,
                    "timestamp": now.isoformat(),
                }})
                await notify_alert({"alert_type": alert_type, "message": alert_msg})
                # 存入数据库
                from database import get_db
                db = await get_db()
                try:
                    threshold = Config.VIX_LOW_THRESHOLD if alert_type == "vix_low" else Config.VIX_HIGH_THRESHOLD
                    await db.execute(
                        "INSERT INTO alerts (alert_type, symbol, value, threshold, message, timestamp) VALUES (?,?,?,?,?,?)",
                        (alert_type, "^VIX", vix, threshold, alert_msg, now.isoformat()),
                    )
                    await db.commit()
                finally:
                    await db.close()

    except Exception as e:
        logger.error(f"VIX 检查失败: {e}")


async def scheduled_strategy():
    """定时运行策略"""
    try:
        # 检查金字塔加仓
        pyramid_trades = await check_pyramid_additions()
        for t in pyramid_trades:
            await broadcast({"type": "trade", "data": t.model_dump()})
            await notify_trade(t.model_dump())
            await broadcast({"type": "alert", "data": {
                "alert_type": "pyramid_add",
                "symbol": t.symbol,
                "value": t.price,
                "message": f"📐 {t.reason}",
                "timestamp": datetime.now().isoformat(),
            }})

        # 检查顺势金字塔加仓 (盈利触发, 加仓量递减)
        trend_pyramid_trades = await check_trend_pyramid_additions()
        for t in trend_pyramid_trades:
            await broadcast({"type": "trade", "data": t.model_dump()})
            await notify_trade(t.model_dump())
            await broadcast({"type": "alert", "data": {
                "alert_type": "trend_pyramid_add",
                "symbol": t.symbol,
                "value": t.price,
                "message": f"📈 {t.reason}",
                "timestamp": datetime.now().isoformat(),
            }})

        # 检查止损/分阶段止盈
        sl_trades = await check_stop_loss_take_profit()
        for t in sl_trades:
            await broadcast({"type": "trade", "data": t.model_dump()})
            await notify_trade(t.model_dump())
            if "止损" in t.reason:
                await broadcast({"type": "alert", "data": {
                    "alert_type": "stop_loss",
                    "symbol": t.symbol,
                    "value": t.price,
                    "message": f"🛑 {t.reason}",
                    "timestamp": datetime.now().isoformat(),
                }})
            elif "止盈" in t.reason:
                await broadcast({"type": "alert", "data": {
                    "alert_type": "profit_take",
                    "symbol": t.symbol,
                    "value": t.price,
                    "message": f"💰 {t.reason} — {t.symbol} {t.shares}股 @ ${t.price:.2f}",
                    "timestamp": datetime.now().isoformat(),
                }})

        # 检查回补机会
        rebuy_opps = await check_rebuy_opportunities()
        for opp in rebuy_opps:
            await broadcast({"type": "alert", "data": {
                "alert_type": "rebuy",
                "symbol": opp["symbol"],
                "value": opp["current_price"],
                "message": opp["message"],
                "timestamp": datetime.now().isoformat(),
            }})

        # 生成信号
        signals = await generate_signals()

        # AI 轮动: 持仓满时用强信号未持仓股替换最弱在持股
        rotation_trades = await check_rotation_swap(signals)
        for t in rotation_trades:
            await broadcast({"type": "trade", "data": t.model_dump()})
            await notify_trade(t.model_dump())
            await broadcast({"type": "alert", "data": {
                "alert_type": "ai_rotation",
                "symbol": t.symbol,
                "value": t.price,
                "message": f"🔁 {t.reason}",
                "timestamp": datetime.now().isoformat(),
            }})

        for signal in signals:
            if signal.action.value != "HOLD":
                trade = await execute_signal(signal)
                if trade:
                    await broadcast({"type": "trade", "data": trade.model_dump()})
                    await notify_trade(trade.model_dump())
                    action_label = "买入" if trade.action == TradeAction.BUY else "卖出"
                    await broadcast({"type": "alert", "data": {
                        "alert_type": "strategy_trade",
                        "symbol": trade.symbol,
                        "value": trade.price,
                        "message": f"🤖 AI策略{action_label}: {trade.symbol} {trade.shares}股 @ ${trade.price:.2f} — {trade.reason}",
                        "timestamp": datetime.now().isoformat(),
                    }})

        # 快照
        snapshot = await get_portfolio_snapshot()
        await save_snapshot(snapshot)
        await broadcast({"type": "portfolio", "data": snapshot.model_dump()})

    except Exception as e:
        logger.error(f"定时策略运行失败: {e}")


async def scheduled_qqq_rotation():
    """定时检查 QQQ/TQQQ 轮动"""
    try:
        rotation = await check_rotation()
        if rotation:
            # 广播轮动警报
            alert_msg = f"🔄 QQQ账户轮动: {rotation['from']} → {rotation['to']} | {rotation['reason']}"
            await broadcast({"type": "alert", "data": {
                "alert_type": "qqq_rotation",
                "symbol": rotation["to"],
                "value": rotation.get("buy", {}).get("price", 0),
                "message": alert_msg,
                "timestamp": datetime.now().isoformat(),
            }})
            await notify_rotation({"message": alert_msg})
            # 存入警报数据库
            from database import get_db
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO alerts (alert_type, symbol, value, threshold, message, timestamp) VALUES (?,?,?,?,?,?)",
                    ("qqq_rotation", rotation["to"], rotation.get("buy", {}).get("price", 0),
                     0, alert_msg, datetime.now().isoformat()),
                )
                await db.commit()
            finally:
                await db.close()

        # 保存 QQQ 快照并广播
        await save_qqq_snapshot()
        qqq_portfolio = await get_qqq_portfolio()
        if "status" not in qqq_portfolio:
            await broadcast({"type": "qqq_portfolio", "data": qqq_portfolio})

    except Exception as e:
        logger.error(f"QQQ轮动检查失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from db_backup import restore_db, backup_db
    await restore_db()
    await init_db()
    logger.info("=" * 50)
    logger.info("🚀 AI 股市模拟交易系统启动")
    logger.info(f"💰 初始资金: ${Config.INITIAL_CAPITAL:,.2f}")
    logger.info(f"📊 股票池: {', '.join(get_symbols())}")
    logger.info(f"📐 策略: 金字塔买入 {[f'{w*100:.0f}%' for w in Config.PYRAMID_WEIGHTS]} | 跌{[f'{d*100:.0f}%' for d in Config.PYRAMID_DROP_TRIGGERS]}触发")
    if Config.TREND_PYRAMID_ENABLED:
        logger.info(f"📈 顺势金字塔: {[f'{w*100:.0f}%' for w in Config.TREND_PYRAMID_WEIGHTS]} | 涨{[f'+{r*100:.0f}%' for r in Config.TREND_PYRAMID_RISE_TRIGGERS]}触发 | 单股预算{Config.TREND_PYRAMID_BUDGET_PCT*100:.0f}%")
    if Config.ROTATION_ENABLED:
        logger.info(f"🔁 AI轮动: 候选评分≥{Config.ROTATION_MIN_CANDIDATE_SCORE} 置信≥{Config.ROTATION_MIN_CANDIDATE_CONFIDENCE} 差距≥{Config.ROTATION_SCORE_GAP} | 浮盈≥{Config.ROTATION_PROTECT_WINNERS_PCT*100:.0f}%保护 | 新仓{Config.ROTATION_HOLD_COOLDOWN_DAYS}天冷却 | 每日≤{Config.ROTATION_MAX_PER_DAY}次")
    logger.info(f"💰 止盈: +{Config.TAKE_PROFIT_PCT*100:.0f}%卖半 → +{Config.TAKE_PROFIT_ALL_PCT*100:.0f}%清仓")
    logger.info(f"🛑 止损: 入场价跌{Config.STOP_LOSS_PCT*100:.0f}% → 清仓+冷却{Config.STOP_LOSS_COOLDOWN_DAYS}天")
    logger.info(f"🤖 策略模型: {Config.LLM_MODEL} (盘中)")
    logger.info(f"🕐 市场状态: {get_market_status()} | 盘后仅跑技术指标 (省API)")
    logger.info(f"🔑 OpenAI API: {'已配置' if Config.OPENAI_API_KEY else '❌ 未配置'}")
    logger.info(f"📈 QQQ/TQQQ 轮动账户: ${Config.QQQ_INITIAL_CAPITAL:,.0f} | TQQQ条件: VIX<{Config.QQQ_TQQQ_ENTER_VIX}+QQQ>SMA200")
    logger.info("=" * 50)

    # 启动定时任务
    scheduler.add_job(scheduled_fetch, 'interval', seconds=Config.FETCH_INTERVAL_SECONDS, id='fetch', misfire_grace_time=30)
    scheduler.add_job(scheduled_strategy, 'interval', seconds=Config.STRATEGY_INTERVAL_SECONDS, id='strategy', misfire_grace_time=60)
    scheduler.add_job(scheduled_vix_check, 'interval', seconds=Config.VIX_CHECK_INTERVAL_SECONDS, id='vix_check', misfire_grace_time=30)
    scheduler.add_job(scheduled_qqq_rotation, 'interval', seconds=Config.QQQ_ROTATION_INTERVAL_SECONDS, id='qqq_rotation', max_instances=1, misfire_grace_time=60)

    # 每5分钟备份数据库到 Azure Blob
    async def scheduled_backup():
        try:
            await backup_db()
        except Exception as e:
            logger.error(f"DB备份失败: {e}")
    scheduler.add_job(scheduled_backup, 'interval', seconds=300, id='db_backup', misfire_grace_time=60)

    scheduler.start()

    yield

    # 关闭前最后备份一次
    await backup_db()
    scheduler.shutdown()
    logger.info("系统关闭")


app = FastAPI(title="AI Stock Simulator", lifespan=lifespan)

# 静态文件
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/api/portfolio")
async def api_portfolio():
    snapshot = await get_portfolio_snapshot()
    return snapshot.model_dump()


@app.get("/api/quotes")
async def api_quotes():
    quotes = get_realtime_quotes()
    return [q.model_dump() for q in quotes]


@app.get("/api/stocks")
async def api_stocks():
    return AI_STOCKS


@app.get("/api/market-status")
async def api_market_status():
    from utils import is_market_open, get_market_status
    return {"is_open": is_market_open(), "status": get_market_status()}


@app.get("/api/trades")
async def api_trades(limit: int = 50):
    from database import get_db
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


@app.get("/api/history/{symbol}")
async def api_history(symbol: str, period: str = "3mo"):
    df = get_history(symbol, period=period)
    if df.empty:
        return []
    df = df.reset_index()
    records = []
    for _, row in df.iterrows():
        records.append({
            "date": row["Date"].isoformat() if hasattr(row["Date"], "isoformat") else str(row["Date"]),
            "open": round(row["Open"], 2),
            "high": round(row["High"], 2),
            "low": round(row["Low"], 2),
            "close": round(row["Close"], 2),
            "volume": int(row["Volume"]),
        })
    return records


@app.get("/api/snapshots")
async def api_snapshots(limit: int = 100):
    from database import get_db
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


@app.get("/api/vix")
async def api_vix():
    """获取当前 VIX 值"""
    vix = get_vix()
    return {"value": vix}


@app.get("/api/alerts")
async def api_alerts(limit: int = 50):
    """获取历史警报"""
    from database import get_db
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


@app.post("/api/initial-buy")
async def api_initial_buy():
    """按分析结果执行首批建仓"""
    from data.stocks import get_buy_list
    from models import TradeSignal, TradeAction

    buy_list = get_buy_list()
    snapshot = await get_portfolio_snapshot()
    total = snapshot.total_value
    executed = []

    for item in buy_list:
        symbol = item["symbol"]
        weight = item["weight"]
        buy_amount = total * weight

        quotes = get_realtime_quotes([symbol])
        if not quotes:
            continue
        price = quotes[0].price

        signal = TradeSignal(
            symbol=symbol,
            action=TradeAction.BUY,
            confidence=0.9,
            technical_score=0.5,
            llm_score=0.5,
            combined_score=0.8,
            reason=f"首批建仓: 目标仓位{weight*100:.0f}%, 基于估值+增速综合分析",
            suggested_amount=price,
            timestamp=datetime.now(),
        )
        trade = await execute_signal(signal, target_amount=buy_amount)
        if trade:
            executed.append(trade.model_dump())

    snapshot = await get_portfolio_snapshot()
    await save_snapshot(snapshot)
    return {
        "message": f"首批建仓完成: {len(executed)} 笔交易",
        "trades": executed,
        "portfolio": snapshot.model_dump(),
    }


@app.post("/api/run-strategy")
async def api_run_strategy():
    """手动触发策略运行"""
    try:
        signals = await generate_signals()
        executed = []
        for signal in signals:
            if signal.action.value != "HOLD":
                trade = await execute_signal(signal)
                if trade:
                    executed.append(trade.model_dump())

        snapshot = await get_portfolio_snapshot()
        await save_snapshot(snapshot)

        return {
            "signals": [s.model_dump() for s in signals],
            "trades": executed,
            "portfolio": snapshot.model_dump(),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ==================== QQQ/TQQQ 轮动账户 API ====================

@app.get("/api/qqq/portfolio")
async def api_qqq_portfolio():
    """获取 QQQ 轮动账户资产"""
    return await get_qqq_portfolio()


@app.get("/api/qqq/trades")
async def api_qqq_trades(limit: int = 50):
    """获取 QQQ 账户交易记录"""
    from database import get_db
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM qqq_trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


@app.get("/api/qqq/snapshots")
async def api_qqq_snapshots(limit: int = 100):
    """获取 QQQ 账户历史快照"""
    from database import get_db
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM qqq_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


@app.get("/api/qqq/sma200")
async def api_qqq_sma200():
    """获取 QQQ 200日均线"""
    sma = get_qqq_sma200()
    quotes = get_realtime_quotes(["QQQ"])
    qqq_price = quotes[0].price if quotes else None
    return {
        "sma200": sma,
        "qqq_price": qqq_price,
        "above_sma": qqq_price > sma if (sma and qqq_price) else None,
    }


@app.post("/api/qqq/init")
async def api_qqq_init():
    """初始化 QQQ 轮动账户"""
    result = await init_qqq_account()
    return result


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    logger.info(f"WebSocket 客户端连接 (总数: {len(connected_clients)})")

    try:
        # 发送初始数据
        snapshot = await get_portfolio_snapshot()
        await websocket.send_text(json.dumps(
            {"type": "portfolio", "data": snapshot.model_dump()},
            default=str, ensure_ascii=False
        ))

        # 发送 QQQ 账户数据
        qqq_p = await get_qqq_portfolio()
        if "status" not in qqq_p:
            await websocket.send_text(json.dumps(
                {"type": "qqq_portfolio", "data": qqq_p},
                default=str, ensure_ascii=False
            ))

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "run_strategy":
                await scheduled_strategy()

    except WebSocketDisconnect:
        connected_clients.remove(websocket)
        logger.info(f"WebSocket 客户端断开 (总数: {len(connected_clients)})")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=Config.HOST, port=Config.PORT, reload=True)
