from datetime import datetime, time
from config import Config
import logging

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo


def is_market_open() -> bool:
    """检查美股是否在交易时段 (不含节假日检测)"""
    et_now = datetime.now(ZoneInfo(Config.MARKET_TIMEZONE))

    # 周末不开盘
    if et_now.weekday() >= 5:
        return False

    market_open = time(Config.MARKET_OPEN_HOUR, Config.MARKET_OPEN_MINUTE)
    market_close = time(Config.MARKET_CLOSE_HOUR, Config.MARKET_CLOSE_MINUTE)

    return market_open <= et_now.time() <= market_close


def get_market_status() -> str:
    """获取当前市场状态描述"""
    et_now = datetime.now(ZoneInfo(Config.MARKET_TIMEZONE))

    if et_now.weekday() >= 5:
        return "休市 (周末)"

    market_open = time(Config.MARKET_OPEN_HOUR, Config.MARKET_OPEN_MINUTE)
    market_close = time(Config.MARKET_CLOSE_HOUR, Config.MARKET_CLOSE_MINUTE)
    now_time = et_now.time()

    if now_time < market_open:
        return f"盘前 (开盘 {Config.MARKET_OPEN_HOUR}:{Config.MARKET_OPEN_MINUTE:02d} ET)"
    elif now_time > market_close:
        return "盘后"
    else:
        return "交易中"
