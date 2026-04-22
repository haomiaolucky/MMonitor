# MMonitor — AI 美股模拟交易系统

一套基于 **FastAPI + WebSocket + APScheduler** 的实时美股模拟盘系统，专注 **AI 全产业链** 龙头股。系统并行运行两个独立账户：

1. **AI 主策略账户** — 技术指标 + LLM 情绪分析，金字塔买入 / 分阶段止盈止损
2. **QQQ/TQQQ 轮动账户** — 基于 200 日均线 + VIX 的 ETF 趋势轮动 (Benchmark)

每账户初始资金 **$50,000**，全程模拟成交（含滑点），数据持久化到 SQLite。

---

## 🎯 核心特性

- 🤖 **双引擎信号**：技术指标 (60%) + LLM 分析 (40%)，LLM 置信度低时自动降权
- 📐 **金字塔建仓**：30% / 30% / 40% 分三层，跌 0% / -5% / -10% 触发加仓
- 💰 **分阶段止盈**：+15% 卖一半 → +30% 清仓 → 回调 18% 可重新入场
- 🛑 **多重风控**：单股止损 -15% + 7 天冷却 / 单股仓位上限 25% / 最多 5 持仓 / 日交易 ≤ 10 笔 / 日亏损 -3% / 组合回撤 -15%
- 🔄 **QQQ/TQQQ 轮动**：QQQ > SMA200 且 VIX < 22 → 上 TQQQ；VIX > 25 或 QQQ < SMA200 → 切回 QQQ；并支持 VIX 恐慌退潮抓反弹
- 🚨 **VIX 警报**：≤16 低波动警报 / ≥30 高波动警报，30 分钟冷却
- 📡 **实时推送**：WebSocket 广播行情 / 交易 / 警报 / 资产快照
- 💬 **AI 助手**：内嵌 Chat 接口，可对当前持仓做问答
- 📲 **微信通知**：通过 Server酱 推送交易与警报

---

## 📊 股票池 (15 只 AI 龙头)

| 板块 | 代码 |
| --- | --- |
| 芯片 | NVDA, TSM, AVGO, AMD |
| 内存 | MU |
| 云平台 | MSFT, META, AMZN, GOOGL |
| 电力/散热/网络 | GEV, VRT, ANET |
| AI 应用 / EDA | CRM, PLTR, SNPS |

**首批建仓**：MU 25% · NVDA 20% · MSFT 15% · AVGO 15% · TSM 15%（保留 10% 现金）

---

## 🧠 策略逻辑

### 主策略 (`strategy/engine.py`)
```
combined_score = tech_score * (1 - LLM_W * llm_conf) + llm_score * (LLM_W * llm_conf)
                 LLM_W = 0.4

combined ≥  0.30  → BUY
combined ≤ -0.25  → SELL
否则              → HOLD
```

**技术评分** (`strategy/technical.py`，加权汇总到 -1.0 ~ +1.0)：
- 均线趋势 30%（多头/空头排列）
- RSI(14) 25%（<30 超卖加分，>70 超买减分）
- MACD 柱 25%
- 布林带位置 20%

**LLM 分析** (`strategy/llm_analyzer.py`)：调用 Azure OpenAI / OpenAI，输出情绪评分、置信度、看多/风险因素，仅在**交易时段**触发以节省 API 费用。

### 仓位管理 (`trading/executor.py`)
- 金字塔：首层 30% → 跌 5% 加 30% → 跌 10% 加 40%
- 分层止盈：+15% 卖一半，+30% 清仓
- 止损：从首次买入价跌 15% → 清仓 + 7 天冷却
- 回补：止盈清仓后从卖出价回调 18% 可重新入场

### QQQ/TQQQ 轮动 (`trading/qqq_rotation.py`)

带**滞回（hysteresis）**的趋势跟随：进 TQQQ 门槛高（VIX<22），退 TQQQ 门槛低（VIX>25），22~25 是无人区，保持原仓位不动，避免阈值附近反复横跳。

#### QQQ → TQQQ（满足任一即切换）

**路径 A：趋势 + 低波动（常规进攻）**
- `QQQ 现价 > QQQ 200 日均线` **且** `VIX < 22`

**路径 B：恐慌退潮抓反弹（抄底）**
- 历史：VIX 曾飙到 **≥ 35**（系统自动打"恐慌标记" `vix_spike_started_at`）
- 当前：5 天内 VIX 回落到 **< 28**
- 注：本条**不要求 QQQ > SMA200**，专门用于大跌后第一时间博反弹；触发后恐慌标记自动清除
- 恐慌标记 5 天未触发自动失效（`QQQ_VIX_SPIKE_EXPIRY_DAYS`）

#### TQQQ → QQQ（满足任一即切换）
- **趋势转弱**：`QQQ < SMA200`（趋势优先于波动率，无论 VIX 多低都立即退）
- **波动升高**：`VIX > 25`

#### 通用门槛（路径 A/B 都需满足）
1. 仅在**美东交易时段**执行（`is_market_open()`）
2. **每日最多 1 次切换**（`last_switch_date` debounce）
3. 当前持仓状态非 `NONE`（账户已初始化）

#### VIX 区间行为速查表

| VIX | QQQ vs SMA200 | 当前持 QQQ | 当前持 TQQQ |
| --- | --- | --- | --- |
| < 22 | 上方 | → 切 TQQQ 🚀 | 持有 |
| < 22 | 下方 | 持有 | → 切回 QQQ 🛡️ |
| 22~25 | 上方 | 持有（不动） | 持有（不动） |
| 22~25 | 下方 | 持有 | → 切回 QQQ 🛡️ |
| > 25 | 任意 | 持有 | → 切回 QQQ 🛡️ |

---

## 🗂 项目结构

```
MMonitor/
├── main.py              FastAPI 入口 + 调度器 + WebSocket + REST API
├── config.py            全局参数 (资金/阈值/调度间隔/LLM/Server酱)
├── database.py          SQLite 初始化与连接
├── models.py            Pydantic 数据模型
├── utils.py             市场时段判断
├── notify.py            Server酱微信推送
├── data/
│   ├── fetcher.py       yfinance 行情/历史/VIX 抓取
│   └── stocks.py        AI 股票池定义
├── strategy/
│   ├── engine.py        信号合成 (技术+LLM)
│   ├── technical.py     技术指标 (RSI/MACD/SMA/BBands/ATR)
│   └── llm_analyzer.py  Azure/OpenAI 情绪分析
├── trading/
│   ├── executor.py      下单/金字塔/止盈止损/回补
│   ├── portfolio.py     现金/持仓/快照/日交易统计
│   └── qqq_rotation.py  QQQ/TQQQ 轮动账户
├── chat/                AI 聊天助手
├── backtest/            历史回测
├── static/              前端 UI (HTML/JS)
└── requirements.txt
```

---

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量 (.env)
OPENAI_API_KEY=your_key
USE_AZURE=true
AZURE_OPENAI_ENDPOINT=https://xxx.cognitiveservices.azure.com/
AZURE_OPENAI_API_VERSION=2025-01-01-preview
SERVERCHAN_KEY=SCTxxx                    # 可选

# 3. 启动
python main.py
# 或
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

打开 <http://localhost:8000> 查看面板。

---

## 🔌 主要 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/portfolio` | 主账户资产快照 |
| GET | `/api/quotes` | 股票池实时行情 |
| GET | `/api/trades?limit=50` | 历史交易 |
| GET | `/api/snapshots?limit=100` | 历史资产快照 |
| GET | `/api/history/{symbol}?period=3mo` | K 线历史 |
| GET | `/api/vix` | 当前 VIX |
| GET | `/api/alerts?limit=50` | 历史警报 |
| GET | `/api/market-status` | 市场开闭状态 |
| POST | `/api/initial-buy` | 执行首批建仓 |
| POST | `/api/run-strategy` | 手动触发策略 |
| GET | `/api/qqq/portfolio` | QQQ 账户资产 |
| GET | `/api/qqq/trades` | QQQ 账户交易 |
| GET | `/api/qqq/snapshots` | QQQ 账户快照 |
| GET | `/api/qqq/sma200` | QQQ 200 日均线 |
| POST | `/api/qqq/init` | 初始化 QQQ 账户 |
| WS  | `/ws` | 实时推送 + 聊天 |

---

## ⏱ 调度任务

| 任务 | 间隔 | 说明 |
| --- | --- | --- |
| 行情刷新 | 60s | 推送股票池实时报价 |
| 策略执行 | 5min | 加仓 → 止损止盈 → 回补 → 信号 → 快照 |
| VIX 监控 | 2min | 阈值警报 + 历史落库 |
| QQQ 轮动 | 5min | SMA200 + VIX 切换判断 |

---

## ⚠️ 免责声明

本项目仅用于**教学与策略研究**，所有交易均为模拟。实盘投资风险自担，作者不承担任何损失。
