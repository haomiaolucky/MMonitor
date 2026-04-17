# AI 全产业链龙头股票池 (2026.04 精选版 - 15只核心)
# 按板块分类，priority 越小越优先买入

AI_STOCKS = {
    # ===== 芯片层 =====
    "NVDA":  {"name": "NVIDIA",      "sector": "芯片",     "tier": 1, "priority": 2, "target_pct": 0.20},
    "TSM":   {"name": "TSMC",        "sector": "芯片",     "tier": 1, "priority": 5, "target_pct": 0.15},
    "AVGO":  {"name": "Broadcom",    "sector": "芯片",     "tier": 1, "priority": 4, "target_pct": 0.10},
    "AMD":   {"name": "AMD",         "sector": "芯片",     "tier": 2, "priority": 8, "target_pct": 0.05},
    # ===== 内存 =====
    "MU":    {"name": "Micron",      "sector": "内存",     "tier": 1, "priority": 1, "target_pct": 0.25},
    # ===== 云平台 =====
    "MSFT":  {"name": "Microsoft",   "sector": "云平台",   "tier": 1, "priority": 3, "target_pct": 0.15},
    "META":  {"name": "Meta",        "sector": "云平台",   "tier": 1, "priority": 6, "target_pct": 0.15},
    "AMZN":  {"name": "Amazon",      "sector": "云平台",   "tier": 2, "priority": 10, "target_pct": 0.10},
    "GOOGL": {"name": "Alphabet",    "sector": "云平台",   "tier": 2, "priority": 11, "target_pct": 0.10},
    # ===== AI 瓶颈 (电力/散热/网络) =====
    "GEV":   {"name": "GE Vernova",  "sector": "电力设备", "tier": 1, "priority": 7, "target_pct": 0.10},
    "VRT":   {"name": "Vertiv",      "sector": "散热配电", "tier": 2, "priority": 9, "target_pct": 0.08},
    "ANET":  {"name": "Arista",      "sector": "网络",     "tier": 2, "priority": 12, "target_pct": 0.08},
    # ===== 应用/EDA =====
    "CRM":   {"name": "Salesforce",  "sector": "AI应用",   "tier": 3, "priority": 13, "target_pct": 0.05},
    "PLTR":  {"name": "Palantir",    "sector": "AI应用",   "tier": 3, "priority": 14, "target_pct": 0.05},
    "SNPS":  {"name": "Synopsys",    "sector": "EDA",      "tier": 3, "priority": 15, "target_pct": 0.05},
}

# 首批建仓名单 — Top 5 Tier1 龙头
INITIAL_BUY_LIST = [
    {"symbol": "MU",   "weight": 0.25},  # PE 4.6, HBM爆发, 最低估
    {"symbol": "NVDA", "weight": 0.20},  # AI芯片绝对龙头
    {"symbol": "MSFT", "weight": 0.15},  # 云+AI双龙头, 回调24%
    {"symbol": "AVGO", "weight": 0.15},  # 定制芯片+网络
    {"symbol": "TSM",  "weight": 0.15},  # 代工垄断
    # 10% 保留现金 (金字塔加仓备用)
]


def get_symbols() -> list[str]:
    return list(AI_STOCKS.keys())


def get_stock_info(symbol: str) -> dict | None:
    return AI_STOCKS.get(symbol)


def get_tier1_symbols() -> list[str]:
    return [s for s, info in AI_STOCKS.items() if info["tier"] == 1]


def get_buy_list() -> list[dict]:
    return INITIAL_BUY_LIST


def get_priority_sorted() -> list[str]:
    """按优先级排序返回股票代码"""
    return sorted(AI_STOCKS.keys(), key=lambda s: AI_STOCKS[s]["priority"])
