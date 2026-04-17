import pandas as pd
import ta
from datetime import datetime
from models import TechnicalSignal, SignalStrength
from data.fetcher import get_history
import logging

logger = logging.getLogger(__name__)


def calculate_indicators(symbol: str, period: str = "6mo") -> TechnicalSignal | None:
    """计算技术指标并生成信号"""
    df = get_history(symbol, period=period)
    if df.empty or len(df) < 60:
        logger.warning(f"{symbol}: 数据不足，无法计算指标")
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # 均线
    sma_5 = ta.trend.sma_indicator(close, window=5).iloc[-1]
    sma_20 = ta.trend.sma_indicator(close, window=20).iloc[-1]
    sma_60 = ta.trend.sma_indicator(close, window=60).iloc[-1]

    # RSI
    rsi = ta.momentum.rsi(close, window=14).iloc[-1]

    # MACD
    macd_ind = ta.trend.MACD(close)
    macd_val = macd_ind.macd().iloc[-1]
    macd_signal = macd_ind.macd_signal().iloc[-1]
    macd_hist = macd_ind.macd_diff().iloc[-1]

    # 布林带
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband().iloc[-1]
    bb_lower = bb.bollinger_lband().iloc[-1]

    # ATR (波动率)
    atr = ta.volatility.average_true_range(high, low, close, window=14).iloc[-1]

    # 综合评分 (-1.0 到 1.0)
    score = _calculate_score(close.iloc[-1], sma_5, sma_20, sma_60, rsi, macd_hist, bb_upper, bb_lower)
    signal = _score_to_signal(score)

    return TechnicalSignal(
        symbol=symbol,
        rsi=round(rsi, 2),
        macd=round(macd_val, 4),
        macd_signal=round(macd_signal, 4),
        macd_hist=round(macd_hist, 4),
        sma_5=round(sma_5, 2),
        sma_20=round(sma_20, 2),
        sma_60=round(sma_60, 2),
        bollinger_upper=round(bb_upper, 2),
        bollinger_lower=round(bb_lower, 2),
        atr=round(atr, 2),
        score=round(score, 3),
        signal=signal,
        timestamp=datetime.now(),
    )


def _calculate_score(price, sma5, sma20, sma60, rsi, macd_hist, bb_upper, bb_lower) -> float:
    """综合技术指标评分"""
    score = 0.0

    # 均线趋势 (权重 30%)
    if price > sma5 > sma20 > sma60:
        score += 0.3   # 多头排列
    elif price > sma20:
        score += 0.15
    elif price < sma5 < sma20 < sma60:
        score -= 0.3   # 空头排列
    elif price < sma20:
        score -= 0.15

    # RSI (权重 25%)
    if rsi < 30:
        score += 0.25  # 超卖
    elif rsi < 40:
        score += 0.1
    elif rsi > 70:
        score -= 0.25  # 超买
    elif rsi > 60:
        score -= 0.1

    # MACD (权重 25%)
    if macd_hist > 0:
        score += min(0.25, macd_hist * 10)
    else:
        score += max(-0.25, macd_hist * 10)

    # 布林带位置 (权重 20%)
    bb_range = bb_upper - bb_lower
    if bb_range > 0:
        bb_position = (price - bb_lower) / bb_range
        if bb_position < 0.2:
            score += 0.2   # 接近下轨，超卖
        elif bb_position > 0.8:
            score -= 0.2   # 接近上轨，超买

    return max(-1.0, min(1.0, score))


def _score_to_signal(score: float) -> SignalStrength:
    if score >= 0.5:
        return SignalStrength.STRONG_BUY
    elif score >= 0.2:
        return SignalStrength.BUY
    elif score <= -0.5:
        return SignalStrength.STRONG_SELL
    elif score <= -0.2:
        return SignalStrength.SELL
    return SignalStrength.HOLD
