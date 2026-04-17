import json
from datetime import datetime
from openai import OpenAI, AzureOpenAI
from models import LLMAnalysis
from config import Config
from data.stocks import get_stock_info
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


ANALYSIS_PROMPT = """你是一个专业的美股分析师，专注于AI和科技板块。
请分析以下股票的当前状况，给出结构化的分析结果。

股票: {symbol} ({name})
板块: {sector}
当前价格: ${price}
今日涨跌: {change_pct}%
技术指标:
- RSI(14): {rsi}
- MACD柱: {macd_hist}
- 5日均线: ${sma_5}
- 20日均线: ${sma_20}

请以JSON格式返回分析结果（不要返回其他内容）:
{{
    "sentiment_score": <-1.0到1.0的情绪评分, 负为看空, 正为看多>,
    "confidence": <0.0到1.0的置信度>,
    "key_catalysts": [<最多3个看多因素>],
    "key_risks": [<最多3个风险因素>],
    "summary": "<一句话总结>"
}}"""


async def analyze_stock(
    symbol: str,
    price: float,
    change_pct: float,
    rsi: float = 50,
    macd_hist: float = 0,
    sma_5: float = 0,
    sma_20: float = 0,
) -> LLMAnalysis | None:
    """用LLM分析单只股票（结构化输出，非决策）"""
    if not Config.OPENAI_API_KEY:
        logger.warning("未配置 OPENAI_API_KEY，跳过 LLM 分析")
        return _fallback_analysis(symbol)

    stock_info = get_stock_info(symbol)
    if not stock_info:
        return None

    prompt = ANALYSIS_PROMPT.format(
        symbol=symbol,
        name=stock_info["name"],
        sector=stock_info["sector"],
        price=price,
        change_pct=change_pct,
        rsi=rsi,
        macd_hist=macd_hist,
        sma_5=sma_5,
        sma_20=sma_20,
    )

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=Config.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=Config.LLM_TEMPERATURE,
            max_tokens=500,
            response_format={"type": "json_object"},
        )

        result = json.loads(response.choices[0].message.content)

        return LLMAnalysis(
            symbol=symbol,
            sentiment_score=max(-1, min(1, float(result.get("sentiment_score", 0)))),
            confidence=max(0, min(1, float(result.get("confidence", 0.5)))),
            key_catalysts=result.get("key_catalysts", [])[:3],
            key_risks=result.get("key_risks", [])[:3],
            summary=result.get("summary", "分析不可用"),
            timestamp=datetime.now(),
        )
    except Exception as e:
        logger.error(f"LLM 分析 {symbol} 失败: {e}")
        return _fallback_analysis(symbol)


def _fallback_analysis(symbol: str) -> LLMAnalysis:
    """LLM 不可用时的回退分析"""
    return LLMAnalysis(
        symbol=symbol,
        sentiment_score=0.0,
        confidence=0.3,
        key_catalysts=["LLM分析不可用，仅依赖技术指标"],
        key_risks=["缺少基本面/情绪分析"],
        summary="回退模式：仅参考技术指标",
        timestamp=datetime.now(),
    )
