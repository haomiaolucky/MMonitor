import json
from datetime import datetime
from openai import OpenAI, AzureOpenAI
from config import Config
from trading.portfolio import get_portfolio_snapshot, get_positions
from database import get_db
import logging

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if Config.USE_AZURE:
            _client = AzureOpenAI(
                api_key=Config.OPENAI_API_KEY,
                azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
                api_version=Config.AZURE_OPENAI_API_VERSION,
            )
        else:
            _client = OpenAI(api_key=Config.OPENAI_API_KEY)
    return _client


SYSTEM_PROMPT = """你是一个AI股市模拟交易系统的智能助手。你的角色:
1. 回答用户关于持仓、收益、交易历史的问题
2. 解释为什么买入/卖出某只股票
3. 分析市场状况和AI板块走势
4. 给出投资建议（注意这是模拟交易，不是真实投资建议）

当前系统信息会通过 context 提供给你。请用中文回答，风格专业但友好。
注意：这是模拟交易系统，初始资金 $50,000。"""


async def chat(user_message: str) -> str:
    """处理用户聊天消息"""
    # 获取当前持仓信息作为上下文
    context = await _build_context()

    # 获取聊天历史
    history = await _get_chat_history(limit=10)

    messages = [{"role": "system", "content": SYSTEM_PROMPT + f"\n\n当前状态:\n{context}"}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    if not Config.OPENAI_API_KEY:
        response = _offline_response(user_message, context)
    else:
        try:
            client = _get_client()
            completion = client.chat.completions.create(
                model=Config.LLM_CHAT_MODEL,
                messages=messages,
                temperature=0.7,
                max_tokens=800,
            )
            response = completion.choices[0].message.content
        except Exception as e:
            logger.error(f"聊天 API 调用失败: {e}")
            response = _offline_response(user_message, context)

    # 保存聊天记录
    await _save_chat(user_message, response)
    return response


async def _build_context() -> str:
    """构建当前状态上下文"""
    try:
        snapshot = await get_portfolio_snapshot()
        lines = [
            f"📊 总资产: ${snapshot.total_value:,.2f}",
            f"💵 现金: ${snapshot.cash:,.2f}",
            f"📈 持仓市值: ${snapshot.positions_value:,.2f}",
            f"💰 总盈亏: ${snapshot.total_pnl:,.2f} ({snapshot.total_pnl_pct:+.2f}%)",
            f"📉 最大回撤: {snapshot.max_drawdown:.2f}%",
            "",
            "当前持仓:",
        ]
        if snapshot.positions:
            for p in snapshot.positions:
                emoji = "🟢" if p.unrealized_pnl >= 0 else "🔴"
                lines.append(
                    f"  {emoji} {p.symbol}: {p.shares:.2f}股 @ ${p.avg_cost:.2f} "
                    f"→ ${p.current_price:.2f} ({p.unrealized_pnl_pct:+.2f}%)"
                )
        else:
            lines.append("  空仓")

        # 最近交易
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT symbol, action, shares, price, reason, timestamp FROM trades ORDER BY timestamp DESC LIMIT 5"
            )
            trades = await cursor.fetchall()
            if trades:
                lines.append("\n最近交易:")
                for t in trades:
                    lines.append(f"  {t[1]} {t[0]}: {t[2]:.2f}股 @ ${t[3]:.2f} - {t[4]}")
        finally:
            await db.close()

        return "\n".join(lines)
    except Exception as e:
        return f"获取状态失败: {e}"


def _offline_response(user_message: str, context: str) -> str:
    """无API Key时的离线回复"""
    msg = user_message.lower()
    if any(w in msg for w in ["持仓", "仓位", "portfolio"]):
        return f"当前持仓信息：\n{context}"
    elif any(w in msg for w in ["收益", "盈亏", "赚", "亏"]):
        return f"收益情况：\n{context}"
    else:
        return f"⚠️ 未配置 OpenAI API Key，聊天功能受限。\n\n当前系统状态：\n{context}\n\n请设置 OPENAI_API_KEY 环境变量以启用完整聊天功能。"


async def _get_chat_history(limit: int = 10) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT role, content FROM chat_history ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]
    finally:
        await db.close()


async def _save_chat(user_msg: str, assistant_msg: str):
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        await db.execute(
            "INSERT INTO chat_history (role, content, timestamp) VALUES (?, ?, ?)",
            ("user", user_msg, now)
        )
        await db.execute(
            "INSERT INTO chat_history (role, content, timestamp) VALUES (?, ?, ?)",
            ("assistant", assistant_msg, now)
        )
        await db.commit()
    finally:
        await db.close()
