from pydantic import BaseModel
from datetime import datetime
from enum import Enum
from typing import Optional


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalStrength(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class StockQuote(BaseModel):
    symbol: str
    price: float
    open: float
    high: float
    low: float
    volume: int
    change_pct: float
    timestamp: datetime


class TechnicalSignal(BaseModel):
    symbol: str
    rsi: float
    macd: float
    macd_signal: float
    macd_hist: float
    sma_5: float
    sma_20: float
    sma_60: float
    bollinger_upper: float
    bollinger_lower: float
    atr: float  # Average True Range (波动率)
    score: float  # -1.0 到 1.0
    signal: SignalStrength
    timestamp: datetime


class LLMAnalysis(BaseModel):
    symbol: str
    sentiment_score: float       # -1.0 到 1.0
    confidence: float            # 0.0 到 1.0
    key_catalysts: list[str]
    key_risks: list[str]
    summary: str
    timestamp: datetime


class TradeSignal(BaseModel):
    symbol: str
    action: TradeAction
    confidence: float            # 0.0 到 1.0
    technical_score: float
    llm_score: float
    combined_score: float
    reason: str
    suggested_amount: float      # 建议交易金额
    timestamp: datetime


class Position(BaseModel):
    symbol: str
    shares: float
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    opened_at: datetime


class Trade(BaseModel):
    id: Optional[int] = None
    symbol: str
    action: TradeAction
    shares: float
    price: float
    amount: float
    commission: float
    slippage: float
    reason: str
    technical_score: float
    llm_score: float
    timestamp: datetime


class PortfolioSnapshot(BaseModel):
    total_value: float
    cash: float
    positions_value: float
    positions: list[Position]
    daily_pnl: float
    daily_pnl_pct: float
    total_pnl: float
    total_pnl_pct: float
    max_drawdown: float
    timestamp: datetime


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime
