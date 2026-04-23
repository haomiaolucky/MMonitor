import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # 初始资金
    INITIAL_CAPITAL = 50_000.0

    # API Keys
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

    # Azure OpenAI 配置
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "https://stocktest-resource.cognitiveservices.azure.com/")
    AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    USE_AZURE = os.getenv("USE_AZURE", "true").lower() == "true"

    # 数据库
    DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "simulator.db"))

    # 交易规则
    MAX_POSITION_PCT = 0.25          # 单股最大仓位 25%
    STOP_LOSS_PCT = -0.15            # 从首次买入价跌15%止损 (金字塔耗尽后)
    TAKE_PROFIT_PCT = 0.15           # 第一阶段止盈 +15% → 卖一半
    TAKE_PROFIT_ALL_PCT = 0.30       # 第二阶段止盈 +30% → 清仓
    MAX_HOLDINGS = 5                 # 最大同时持仓
    MAX_DAILY_TRADES = 10            # 每日最大交易次数
    MAX_PORTFOLIO_DRAWDOWN = -0.15   # 组合最大回撤 -15%
    DAILY_LOSS_LIMIT = -0.03         # 日亏损上限 -3%

    # 金字塔买入 (跌破加仓 / Averaging Down)
    PYRAMID_WEIGHTS = [0.30, 0.30, 0.40]       # 各层仓位比例
    PYRAMID_DROP_TRIGGERS = [0.0, -0.05, -0.10] # 各层触发跌幅 (从首次买入价)

    # 顺势金字塔加仓 (Trend Pyramid / Add on Rally)
    # 盈利时加仓，越往上加得越少 — 标准金字塔结构 (底重顶轻)
    TREND_PYRAMID_ENABLED = True
    TREND_PYRAMID_RISE_TRIGGERS = [0.05, 0.10, 0.15]  # 各层触发涨幅 (相对入场价)
    TREND_PYRAMID_WEIGHTS = [0.50, 0.30, 0.20]        # 各层加仓比例 (相对单股加仓预算)
    TREND_PYRAMID_BUDGET_PCT = 0.10                   # 单股加仓预算占组合总值的比例

    # AI 卖弱换强轮动 (Rotation Swap)
    # 持仓满时，AI 自动用强信号未持仓股替换最弱在持股
    ROTATION_ENABLED = True
    ROTATION_SCORE_GAP = 0.4                # 候选 - 在持最弱: 综合分差距阈值
    ROTATION_MIN_CANDIDATE_SCORE = 0.5      # 候选股最低综合评分
    ROTATION_MIN_CANDIDATE_CONFIDENCE = 0.7 # 候选股最低置信度 (LLM)
    ROTATION_PROTECT_WINNERS_PCT = 0.10     # 浮盈 ≥10% 的在持股被保护，不被换出
    ROTATION_HOLD_COOLDOWN_DAYS = 3         # 新换入持仓 N 天内不被换出
    ROTATION_MAX_PER_DAY = 1                # 每日最多换仓次数

    # 止损冷却与再入场
    STOP_LOSS_COOLDOWN_DAYS = 7      # 止损后冷却天数
    REBUY_FROM_PEAK_PCT = 0.18       # 止盈清仓后，从卖出价回调18%可重新入场

    # 模拟交易参数
    SLIPPAGE_PCT = 0.001             # 滑点 0.1%
    COMMISSION_PER_TRADE = 0.0       # 佣金 (模拟免佣)
    MIN_TRADE_AMOUNT = 100.0         # 最小交易金额

    # VIX 监控
    VIX_LOW_THRESHOLD = 16.0         # VIX <= 此值触发低波动警报
    VIX_HIGH_THRESHOLD = 30.0        # VIX >= 此值触发高波动警报
    VIX_CHECK_INTERVAL_SECONDS = 120 # VIX 检查频率 (2分钟)
    ALERT_COOLDOWN_SECONDS = 1800    # 同类警报冷却时间 (30分钟)

    # 调度
    FETCH_INTERVAL_SECONDS = 60      # 数据刷新频率
    STRATEGY_INTERVAL_SECONDS = 300  # 策略运行频率 (5分钟)

    # LLM
    LLM_MODEL = "gpt-4o-mini"       # Azure 部署名 / OpenAI 模型名
    LLM_CHAT_MODEL = "gpt-4o-mini"  # 聊天用同一部署 (Azure 需单独部署才能用 gpt-4o)
    LLM_TEMPERATURE = 0.3

    # 交易时段 (美东时间)
    MARKET_OPEN_HOUR = 9             # 开盘 9:30
    MARKET_OPEN_MINUTE = 30
    MARKET_CLOSE_HOUR = 16           # 收盘 16:00
    MARKET_CLOSE_MINUTE = 0
    MARKET_TIMEZONE = "US/Eastern"

    # ===== QQQ/TQQQ 轮动账户 (Account 2 - Benchmark) =====
    QQQ_INITIAL_CAPITAL = 50_000.0
    QQQ_SMA_PERIOD = 200              # 200日均线
    QQQ_TQQQ_ENTER_VIX = 22.0        # VIX < 此值 → 可切TQQQ
    QQQ_TQQQ_EXIT_VIX = 25.0         # VIX > 此值 → 切回QQQ
    QQQ_VIX_SPIKE_THRESHOLD = 35.0   # VIX恐慌标记阈值
    QQQ_VIX_SPIKE_RECOVERY = 28.0    # VIX退潮信号 (从35+降到此值 → 上TQQQ)
    QQQ_VIX_SPIKE_EXPIRY_DAYS = 5    # 恐慌标记有效天数
    QQQ_ROTATION_INTERVAL_SECONDS = 300  # 轮动检查频率 (5分钟)

    # Web
    HOST = "0.0.0.0"
    PORT = 8000

    # Server酱 微信通知
    SERVERCHAN_KEY = os.getenv("SERVERCHAN_KEY", "SCT339456TEPlsK61Vw8hFnCrdVcbe1ASP")
