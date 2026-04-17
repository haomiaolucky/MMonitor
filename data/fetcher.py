import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from models import StockQuote
from data.stocks import get_symbols
import logging

logger = logging.getLogger(__name__)

# 缓存: symbol -> (timestamp, data)
_quote_cache: dict[str, tuple[datetime, StockQuote]] = {}
_history_cache: dict[str, tuple[datetime, pd.DataFrame]] = {}
CACHE_TTL = 30  # 秒


def get_realtime_quotes(symbols: list[str] | None = None) -> list[StockQuote]:
    """获取实时(延迟15分钟)报价"""
    if symbols is None:
        symbols = get_symbols()

    now = datetime.now()
    results = []

    # 检查缓存
    uncached = []
    for s in symbols:
        if s in _quote_cache:
            ts, quote = _quote_cache[s]
            if (now - ts).total_seconds() < CACHE_TTL:
                results.append(quote)
                continue
        uncached.append(s)

    if uncached:
        try:
            tickers = yf.Tickers(" ".join(uncached))
            for symbol in uncached:
                try:
                    ticker = tickers.tickers[symbol]
                    info = ticker.fast_info
                    hist = ticker.history(period="2d")

                    if hist.empty:
                        logger.warning(f"No data for {symbol}")
                        continue

                    last = hist.iloc[-1]
                    prev_close = hist.iloc[-2]["Close"] if len(hist) > 1 else last["Open"]
                    change_pct = ((last["Close"] - prev_close) / prev_close) * 100

                    quote = StockQuote(
                        symbol=symbol,
                        price=round(last["Close"], 2),
                        open=round(last["Open"], 2),
                        high=round(last["High"], 2),
                        low=round(last["Low"], 2),
                        volume=int(last["Volume"]),
                        change_pct=round(change_pct, 2),
                        timestamp=now,
                    )
                    _quote_cache[symbol] = (now, quote)
                    results.append(quote)
                except Exception as e:
                    logger.error(f"Error fetching {symbol}: {e}")
        except Exception as e:
            logger.error(f"Error fetching batch quotes: {e}")

    return results


def get_history(symbol: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame:
    """获取历史K线数据"""
    cache_key = f"{symbol}_{period}_{interval}"
    now = datetime.now()

    if cache_key in _history_cache:
        ts, df = _history_cache[cache_key]
        if (now - ts).total_seconds() < 300:  # 5分钟缓存
            return df

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if not df.empty:
            _history_cache[cache_key] = (now, df)
        return df
    except Exception as e:
        logger.error(f"Error fetching history for {symbol}: {e}")
        return pd.DataFrame()


def get_batch_history(symbols: list[str] | None = None, period: str = "3mo") -> dict[str, pd.DataFrame]:
    """批量获取历史数据"""
    if symbols is None:
        symbols = get_symbols()

    result = {}
    for symbol in symbols:
        df = get_history(symbol, period=period)
        if not df.empty:
            result[symbol] = df
    return result


def get_vix() -> float | None:
    """获取当前 VIX 恐慌指数"""
    cache_key = "^VIX"
    now = datetime.now()

    if cache_key in _quote_cache:
        ts, cached = _quote_cache[cache_key]
        if (now - ts).total_seconds() < CACHE_TTL:
            return cached.price

    try:
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="1d")
        if hist.empty:
            logger.warning("No VIX data")
            return None
        vix_value = round(float(hist.iloc[-1]["Close"]), 2)
        # 复用 quote cache 存 VIX 值
        _quote_cache[cache_key] = (now, StockQuote(
            symbol="^VIX", price=vix_value,
            open=round(float(hist.iloc[-1]["Open"]), 2),
            high=round(float(hist.iloc[-1]["High"]), 2),
            low=round(float(hist.iloc[-1]["Low"]), 2),
            volume=int(hist.iloc[-1]["Volume"]),
            change_pct=0.0, timestamp=now,
        ))
        return vix_value
    except Exception as e:
        logger.error(f"Error fetching VIX: {e}")
        return None


def clear_cache():
    """清除缓存"""
    _quote_cache.clear()
    _history_cache.clear()
