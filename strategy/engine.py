from datetime import datetime
from models import TradeSignal, TradeAction, TechnicalSignal, LLMAnalysis
from strategy.technical import calculate_indicators
from strategy.llm_analyzer import analyze_stock
from data.fetcher import get_realtime_quotes
from data.stocks import get_symbols, get_buy_list, get_priority_sorted, AI_STOCKS
from utils import is_market_open
import logging

logger = logging.getLogger(__name__)

# 权重配置
TECHNICAL_WEIGHT = 0.6
LLM_WEIGHT = 0.4

# 阈值
BUY_THRESHOLD = 0.3
SELL_THRESHOLD = -0.25


async def generate_signals(symbols: list[str] | None = None) -> list[TradeSignal]:
    """综合技术指标和LLM分析生成交易信号"""
    if symbols is None:
        symbols = get_priority_sorted()  # 按优先级排序

    quotes = get_realtime_quotes(symbols)
    quote_map = {q.symbol: q for q in quotes}

    signals = []
    for symbol in symbols:
        try:
            signal = await _analyze_single(symbol, quote_map.get(symbol))
            if signal:
                signals.append(signal)
        except Exception as e:
            logger.error(f"生成 {symbol} 信号失败: {e}")

    # 按优先级加权的综合评分排序
    signals.sort(key=lambda s: _priority_score(s), reverse=True)
    return signals


def _priority_score(signal: TradeSignal) -> float:
    """结合优先级和技术评分的综合排序分"""
    stock_info = AI_STOCKS.get(signal.symbol, {})
    priority = stock_info.get("priority", 99)
    # 优先级越小越好，转换为 0~1 的加分 (priority 1 -> +0.5, priority 22 -> +0.02)
    priority_bonus = max(0, (23 - priority) / 44)
    return signal.combined_score + priority_bonus


async def _analyze_single(symbol: str, quote=None) -> TradeSignal | None:
    """分析单只股票"""
    # 技术分析
    tech = calculate_indicators(symbol)
    if tech is None:
        return None

    # 获取报价
    price = quote.price if quote else tech.sma_5
    change_pct = quote.change_pct if quote else 0

    # LLM 分析: 仅在交易时段调用 (省API费)
    llm = None
    if is_market_open():
        llm = await analyze_stock(
            symbol=symbol,
            price=price,
            change_pct=change_pct,
            rsi=tech.rsi,
            macd_hist=tech.macd_hist,
            sma_5=tech.sma_5,
            sma_20=tech.sma_20,
        )
    else:
        logger.debug(f"{symbol} 非交易时段，跳过LLM分析，仅用技术指标")

    # 综合评分
    tech_score = tech.score
    llm_score = llm.sentiment_score if llm else 0
    llm_confidence = llm.confidence if llm else 0.3

    # LLM 置信度低时降低其权重
    effective_llm_weight = LLM_WEIGHT * llm_confidence
    effective_tech_weight = 1 - effective_llm_weight
    combined = tech_score * effective_tech_weight + llm_score * effective_llm_weight

    # 确定动作
    if combined >= BUY_THRESHOLD:
        action = TradeAction.BUY
    elif combined <= SELL_THRESHOLD:
        action = TradeAction.SELL
    else:
        action = TradeAction.HOLD

    # 构建理由
    reasons = []
    if tech.score > 0.2:
        reasons.append(f"技术面看多(RSI:{tech.rsi}, MACD柱:{tech.macd_hist:.4f})")
    elif tech.score < -0.2:
        reasons.append(f"技术面看空(RSI:{tech.rsi}, MACD柱:{tech.macd_hist:.4f})")
    if llm and llm.summary:
        reasons.append(llm.summary)
    reason = "; ".join(reasons) if reasons else "信号中性"

    return TradeSignal(
        symbol=symbol,
        action=action,
        confidence=abs(combined),
        technical_score=round(tech_score, 3),
        llm_score=round(llm_score, 3),
        combined_score=round(combined, 3),
        reason=reason,
        suggested_amount=0,  # 由 portfolio 计算
        timestamp=datetime.now(),
    )
