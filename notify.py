"""Server酱 (ServerChan) 微信推送通知"""
import logging
import asyncio
import aiohttp
from config import Config

logger = logging.getLogger(__name__)

SERVERCHAN_URL = "https://sctapi.ftqq.com/{key}.send"


async def send_wechat(title: str, content: str = "") -> bool:
    """发送微信通知 via Server酱
    
    Args:
        title: 通知标题 (最长 32 字符)
        content: 通知正文 (支持 Markdown)
    Returns:
        True if sent successfully
    """
    key = Config.SERVERCHAN_KEY
    if not key:
        return False

    url = SERVERCHAN_URL.format(key=key)
    payload = {"title": title[:32], "desp": content[:1000]}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                result = await resp.json()
                if result.get("code") == 0:
                    logger.info(f"✅ 微信通知已发送: {title}")
                    return True
                else:
                    logger.warning(f"⚠️ 微信通知失败: {result}")
                    return False
    except Exception as e:
        logger.error(f"❌ 微信通知异常: {e}")
        return False


async def notify_trade(trade_data: dict):
    """交易执行通知"""
    symbol = trade_data.get("symbol", "")
    action = trade_data.get("action", "")
    price = trade_data.get("price", 0)
    shares = trade_data.get("shares", 0)
    amount = trade_data.get("amount", 0)
    reason = trade_data.get("reason", "")

    emoji = "🟢" if action == "BUY" else "🔴"
    title = f"{emoji} {action} {symbol} ${price:.0f}"
    content = (
        f"### {emoji} 交易执行\n\n"
        f"- **股票**: {symbol}\n"
        f"- **操作**: {action}\n"
        f"- **价格**: ${price:.2f}\n"
        f"- **数量**: {shares} 股\n"
        f"- **金额**: ${amount:.2f}\n"
        f"- **原因**: {reason}\n"
    )
    await send_wechat(title, content)


async def notify_alert(alert_data: dict):
    """VIX 警报 / 止损止盈通知"""
    msg = alert_data.get("message", "")
    alert_type = alert_data.get("alert_type", "")
    title = msg[:32] if msg else f"⚠️ {alert_type}"
    content = f"### ⚠️ 警报\n\n{msg}"
    await send_wechat(title, content)


async def notify_rotation(rotation_data: dict):
    """QQQ/TQQQ 轮动通知"""
    msg = rotation_data.get("message", "")
    title = msg[:32] if msg else "🔄 QQQ轮动"
    content = f"### 🔄 QQQ/TQQQ 轮动\n\n{msg}"
    await send_wechat(title, content)
