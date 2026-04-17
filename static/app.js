// ========== WebSocket ==========
let ws = null;
let equityChart = null;

function connectWS() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        document.getElementById('ws-status').textContent = '已连接';
        document.getElementById('ws-status').className = 'text-xs px-2 py-1 rounded bg-green-900 text-green-300';
    };

    ws.onclose = () => {
        document.getElementById('ws-status').textContent = '断开';
        document.getElementById('ws-status').className = 'text-xs px-2 py-1 rounded bg-red-900 text-red-300';
        setTimeout(connectWS, 3000);
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        switch (msg.type) {
            case 'portfolio': updatePortfolio(msg.data); break;
            case 'quotes': updateQuotes(msg.data); break;
            case 'trade': addTrade(msg.data); break;
            case 'chat': addChatMessage('assistant', msg.data.content); break;
            case 'vix': updateVix(msg.data); break;
            case 'alert': showAlert(msg.data); break;
            case 'qqq_portfolio': updateQqqPortfolio(msg.data); break;
        }
    };
}

// ========== Portfolio ==========
function updatePortfolio(data) {
    document.getElementById('total-value').textContent = formatMoney(data.total_value);
    document.getElementById('cash').textContent = formatMoney(data.cash);

    const pnlEl = document.getElementById('total-pnl');
    pnlEl.textContent = formatMoney(data.total_pnl);
    pnlEl.className = `text-2xl font-bold ${data.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`;

    const pctEl = document.getElementById('total-pnl-pct');
    pctEl.textContent = `${data.total_pnl_pct >= 0 ? '+' : ''}${data.total_pnl_pct.toFixed(2)}%`;
    pctEl.className = `text-xs ${data.total_pnl_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}`;

    document.getElementById('max-drawdown').textContent = `${data.max_drawdown.toFixed(2)}%`;
    _acct1PnlPct = data.total_pnl_pct;
    document.getElementById('cmp-acct1').textContent = `${_acct1PnlPct >= 0 ? '+' : ''}${_acct1PnlPct.toFixed(2)}%`;
    document.getElementById('cmp-acct1').className = `text-lg font-bold ${_acct1PnlPct >= 0 ? 'pnl-positive' : 'pnl-negative'}`;

    // 持仓表
    const tbody = document.getElementById('positions-table');
    const positions = data.positions || [];
    document.getElementById('position-count').textContent = `${positions.length}/5`;

    if (positions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="px-4 py-8 text-center text-gray-500">空仓 - 等待 AI 分析...</td></tr>';
    } else {
        tbody.innerHTML = positions.map(p => `
            <tr class="border-t border-gray-700/50 hover:bg-gray-700/30">
                <td class="px-4 py-2 font-medium">${p.symbol}</td>
                <td class="px-4 py-2 text-right">${p.shares.toFixed(2)}</td>
                <td class="px-4 py-2 text-right">$${p.avg_cost.toFixed(2)}</td>
                <td class="px-4 py-2 text-right">$${p.current_price.toFixed(2)}</td>
                <td class="px-4 py-2 text-right">$${p.market_value.toFixed(2)}</td>
                <td class="px-4 py-2 text-right ${p.unrealized_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">
                    $${p.unrealized_pnl.toFixed(2)}
                </td>
                <td class="px-4 py-2 text-right ${p.unrealized_pnl_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}">
                    ${p.unrealized_pnl_pct >= 0 ? '+' : ''}${p.unrealized_pnl_pct.toFixed(2)}%
                </td>
            </tr>
        `).join('');
    }
}

// ========== Quotes ==========
function updateQuotes(quotes) {
    const grid = document.getElementById('quotes-grid');
    grid.innerHTML = quotes.map(q => `
        <div class="quote-card" onclick="viewStock('${q.symbol}')">
            <div class="flex items-center justify-between mb-1">
                <span class="font-bold text-sm">${q.symbol}</span>
                <span class="text-xs ${q.change_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}">
                    ${q.change_pct >= 0 ? '▲' : '▼'} ${Math.abs(q.change_pct).toFixed(2)}%
                </span>
            </div>
            <div class="text-lg font-mono ${q.change_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}">
                $${q.price.toFixed(2)}
            </div>
            <div class="text-xs text-gray-500 mt-1">Vol: ${formatVolume(q.volume)}</div>
        </div>
    `).join('');
}

// ========== Trades ==========
function addTrade(trade) {
    loadTrades();
}

async function loadTrades() {
    try {
        const res = await fetch('/api/trades?limit=20');
        const trades = await res.json();
        const tbody = document.getElementById('trades-table');

        if (trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="px-4 py-8 text-center text-gray-500">暂无交易记录</td></tr>';
            return;
        }

        tbody.innerHTML = trades.map(t => `
            <tr class="border-t border-gray-700/50 hover:bg-gray-700/30">
                <td class="px-4 py-2 text-xs text-gray-400">${formatTime(t.timestamp)}</td>
                <td class="px-4 py-2">
                    <span class="px-2 py-0.5 rounded text-xs font-bold ${t.action === 'BUY' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'}">
                        ${t.action === 'BUY' ? '买入' : '卖出'}
                    </span>
                </td>
                <td class="px-4 py-2 font-medium">${t.symbol}</td>
                <td class="px-4 py-2 text-right">${t.shares.toFixed(2)}</td>
                <td class="px-4 py-2 text-right">$${t.price.toFixed(2)}</td>
                <td class="px-4 py-2 text-right">$${t.amount.toFixed(2)}</td>
                <td class="px-4 py-2 text-xs text-gray-400 max-w-48 truncate" title="${t.reason || ''}">${t.reason || '-'}</td>
            </tr>
        `).join('');
    } catch (e) {
        console.error('加载交易记录失败:', e);
    }
}

// ========== Equity Chart ==========
async function loadEquityChart() {
    try {
        const res = await fetch('/api/snapshots?limit=200');
        const snapshots = await res.json();

        if (snapshots.length === 0) return;

        const sorted = snapshots.reverse();
        const labels = sorted.map(s => formatTime(s.timestamp));
        const values = sorted.map(s => s.total_value);

        const ctx = document.getElementById('equity-chart').getContext('2d');

        if (equityChart) equityChart.destroy();

        equityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: '总资产',
                    data: values,
                    borderColor: '#3b82f6',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                }, {
                    label: '初始资金',
                    data: values.map(() => 50000),
                    borderColor: '#6b7280',
                    borderDash: [5, 5],
                    pointRadius: 0,
                    borderWidth: 1,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: '#9ca3af', font: { size: 11 } } },
                },
                scales: {
                    x: { ticks: { color: '#6b7280', maxTicksLimit: 10, font: { size: 10 } }, grid: { color: '#1f2937' } },
                    y: { ticks: { color: '#6b7280', callback: v => '$' + v.toLocaleString() }, grid: { color: '#1f2937' } },
                },
            }
        });
    } catch (e) {
        console.error('加载资产曲线失败:', e);
    }
}

// ========== VIX & Alerts ==========
function updateVix(data) {
    const val = data.value;
    if (val == null) return;
    const el = document.getElementById('vix-value');
    const statusEl = document.getElementById('vix-status');
    const card = document.getElementById('vix-card');

    el.textContent = val.toFixed(2);

    // 颜色和状态
    card.className = 'bg-gray-800 rounded-lg p-4 border';
    if (val <= 16) {
        el.className = 'text-2xl font-bold text-green-400';
        statusEl.textContent = '😌 低波动 (市场平静)';
        card.className += ' border-green-700';
    } else if (val >= 30) {
        el.className = 'text-2xl font-bold text-red-400';
        statusEl.textContent = '😱 高波动 (市场恐慌)';
        card.className += ' border-red-700';
    } else if (val >= 20) {
        el.className = 'text-2xl font-bold text-yellow-400';
        statusEl.textContent = '⚠️ 偏高波动';
        card.className += ' border-yellow-700';
    } else {
        el.className = 'text-2xl font-bold text-gray-100';
        statusEl.textContent = '😐 正常波动';
        card.className += ' border-gray-700';
    }
}

function showAlert(data) {
    const banner = document.getElementById('alert-banner');
    const text = document.getElementById('alert-text');
    text.textContent = data.message;

    const styles = {
        vix_high: 'bg-red-900/50 border-red-700 text-red-200',
        vix_low: 'bg-green-900/50 border-green-700 text-green-200',
        profit_take: 'bg-yellow-900/50 border-yellow-700 text-yellow-200',
        stop_loss: 'bg-red-900/50 border-red-700 text-red-200',
        strategy_trade: 'bg-indigo-900/50 border-indigo-700 text-indigo-200',
        rebuy_profit: 'bg-blue-900/50 border-blue-700 text-blue-200',
        cooldown_expired: 'bg-purple-900/50 border-purple-700 text-purple-200',
        pyramid_add: 'bg-cyan-900/50 border-cyan-700 text-cyan-200',
        qqq_rotation: 'bg-orange-900/50 border-orange-700 text-orange-200',
    };
    const style = styles[data.alert_type] || 'bg-gray-900/50 border-gray-700 text-gray-200';
    banner.className = `rounded-lg p-3 border text-sm font-medium flex items-center justify-between ${style}`;
    banner.classList.remove('hidden');

    // 浏览器桌面通知 (后台也能看到)
    if (Notification.permission === 'granted') {
        new Notification('AI 股市模拟', { body: data.message, icon: '🤖' });
    }

    // 播放提示音
    try { new Audio('data:audio/wav;base64,UklGRl9vT19teleUFZRSQAAABkAAAACABAAAA=').play().catch(()=>{}); } catch(e) {}

    // 自动消失 60 秒
    setTimeout(() => banner.classList.add('hidden'), 60000);

    // 同时在聊天显示
    addChatMessage('assistant', `⚠️ 监控警报: ${data.message}`);
}

function dismissAlert() {
    document.getElementById('alert-banner').classList.add('hidden');
}

// ========== QQQ/TQQQ Account ==========
let _acct1PnlPct = 0;

function updateQqqPortfolio(data) {
    if (data.status) return; // not initialized

    document.getElementById('qqq-total-value').textContent = formatMoney(data.total_value);

    const pnlEl = document.getElementById('qqq-total-pnl');
    pnlEl.textContent = formatMoney(data.total_pnl);
    pnlEl.className = `text-lg font-bold ${data.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`;

    const pctEl = document.getElementById('qqq-total-pnl-pct');
    pctEl.textContent = `${data.total_pnl_pct >= 0 ? '+' : ''}${data.total_pnl_pct.toFixed(2)}%`;
    pctEl.className = `text-xs ${data.total_pnl_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}`;

    document.getElementById('qqq-max-drawdown').textContent = `${data.max_drawdown.toFixed(2)}%`;

    // Holding badge
    const holdEl = document.getElementById('qqq-holding');
    const holding = data.current_holding || 'NONE';
    if (holding === 'TQQQ') {
        holdEl.textContent = '🚀 TQQQ (3x)';
        holdEl.className = 'text-xs px-2 py-1 rounded bg-green-900 text-green-300 font-bold';
    } else if (holding === 'QQQ') {
        holdEl.textContent = '🛡️ QQQ (1x)';
        holdEl.className = 'text-xs px-2 py-1 rounded bg-blue-900 text-blue-300 font-bold';
    } else {
        holdEl.textContent = holding;
        holdEl.className = 'text-xs px-2 py-1 rounded bg-gray-700 text-gray-400';
    }

    const statusEl = document.getElementById('qqq-status-text');
    if (data.last_switch_date) {
        statusEl.textContent = `上次切换: ${data.last_switch_date}`;
    }

    // Position detail
    const posBox = document.getElementById('qqq-position-detail');
    if (data.positions && data.positions.length > 0) {
        const pos = data.positions[0];
        posBox.classList.remove('hidden');
        document.getElementById('qqq-pos-symbol').textContent = pos.symbol;
        document.getElementById('qqq-pos-shares').textContent = `${pos.shares} 股`;
        document.getElementById('qqq-pos-cost').textContent = `$${pos.avg_cost.toFixed(2)}`;
        document.getElementById('qqq-pos-price').textContent = `$${pos.current_price.toFixed(2)}`;
        const pnlEl2 = document.getElementById('qqq-pos-pnl');
        pnlEl2.textContent = `${pos.pnl >= 0 ? '+' : ''}$${pos.pnl.toFixed(2)} (${pos.pnl_pct >= 0 ? '+' : ''}${pos.pnl_pct.toFixed(2)}%)`;
        pnlEl2.className = `text-sm ml-2 ${pos.pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
    } else {
        posBox.classList.add('hidden');
    }

    // Comparison
    document.getElementById('cmp-acct2').textContent = `${data.total_pnl_pct >= 0 ? '+' : ''}${data.total_pnl_pct.toFixed(2)}%`;
    document.getElementById('cmp-acct2').className = `text-lg font-bold ${data.total_pnl_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}`;

    document.getElementById('cmp-acct1').textContent = `${_acct1PnlPct >= 0 ? '+' : ''}${_acct1PnlPct.toFixed(2)}%`;
    document.getElementById('cmp-acct1').className = `text-lg font-bold ${_acct1PnlPct >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
}

async function loadQqqSma() {
    try {
        const res = await fetch('/api/qqq/sma200');
        const data = await res.json();
        const smaEl = document.getElementById('qqq-sma200');
        const statusEl = document.getElementById('qqq-sma-status');
        if (data.sma200) {
            smaEl.textContent = '$' + data.sma200.toFixed(2);
            if (data.above_sma) {
                statusEl.textContent = `QQQ $${data.qqq_price.toFixed(2)} ▲ 上方`;
                statusEl.className = 'text-xs pnl-positive';
            } else {
                statusEl.textContent = `QQQ $${data.qqq_price.toFixed(2)} ▼ 下方`;
                statusEl.className = 'text-xs pnl-negative';
            }
        } else {
            smaEl.textContent = 'N/A';
            statusEl.textContent = '数据不足';
        }
    } catch (e) {}
}

// ========== Chat ==========
function sendChat() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg || !ws || ws.readyState !== WebSocket.OPEN) return;

    addChatMessage('user', msg);
    ws.send(JSON.stringify({ type: 'chat', content: msg }));
    input.value = '';

    // 显示加载动画
    const container = document.getElementById('chat-messages');
    const loadingDiv = document.createElement('div');
    loadingDiv.id = 'chat-loading';
    loadingDiv.className = 'chat-assistant text-sm';
    loadingDiv.innerHTML = '<span class="loading-dot">●</span> <span class="loading-dot" style="animation-delay:0.3s">●</span> <span class="loading-dot" style="animation-delay:0.6s">●</span>';
    container.appendChild(loadingDiv);
    container.scrollTop = container.scrollHeight;
}

function addChatMessage(role, content) {
    const container = document.getElementById('chat-messages');
    const loading = document.getElementById('chat-loading');
    if (loading) loading.remove();

    const div = document.createElement('div');
    div.className = role === 'user' ? 'chat-user text-sm' : 'chat-assistant text-sm';

    const label = role === 'user' ? '👤 你' : '🤖 AI 助手';
    div.innerHTML = `<div class="text-xs ${role === 'user' ? 'text-blue-300' : 'text-blue-400'} mb-1">${label}</div>${escapeHtml(content).replace(/\n/g, '<br>')}`;

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// ========== Actions ==========
async function runStrategy() {
    try {
        const btn = event.target;
        btn.textContent = '⏳ 运行中...';
        btn.disabled = true;

        const res = await fetch('/api/run-strategy', { method: 'POST' });
        const result = await res.json();

        if (result.error) {
            alert('策略运行失败: ' + result.error);
        } else {
            const msg = `策略运行完成: ${result.signals.length} 个信号, ${result.trades.length} 笔交易`;
            addChatMessage('assistant', msg);
            loadTrades();
            loadEquityChart();
        }

        btn.textContent = '▶ 运行策略';
        btn.disabled = false;
    } catch (e) {
        alert('策略运行出错: ' + e.message);
    }
}

function viewStock(symbol) {
    const msg = `帮我分析一下 ${symbol} 的当前情况`;
    document.getElementById('chat-input').value = msg;
    sendChat();
}

// ========== Helpers ==========
function formatMoney(val) {
    return '$' + val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatVolume(vol) {
    if (vol >= 1e9) return (vol / 1e9).toFixed(1) + 'B';
    if (vol >= 1e6) return (vol / 1e6).toFixed(1) + 'M';
    if (vol >= 1e3) return (vol / 1e3).toFixed(1) + 'K';
    return vol.toString();
}

function formatTime(ts) {
    const d = new Date(ts);
    return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ========== Market Status ==========
async function updateMarketStatus() {
    try {
        const res = await fetch('/api/market-status');
        const data = await res.json();
        const el = document.getElementById('market-status');
        el.textContent = data.status;
        if (data.is_open) {
            el.className = 'text-xs px-2 py-1 rounded bg-green-900 text-green-300';
        } else {
            el.className = 'text-xs px-2 py-1 rounded bg-gray-700 text-gray-400';
        }
    } catch (e) {}
}

// ========== Clock ==========
function updateClock() {
    const now = new Date();
    document.getElementById('clock').textContent = now.toLocaleString('zh-CN');
}

// ========== Init ==========
async function init() {
    // 请求浏览器通知权限
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }

    connectWS();
    updateClock();
    updateMarketStatus();
    setInterval(updateClock, 1000);
    setInterval(updateMarketStatus, 60000);

    // 初始加载
    try {
        const [quotesRes, portfolioRes, vixRes, qqqRes] = await Promise.all([
            fetch('/api/quotes'),
            fetch('/api/portfolio'),
            fetch('/api/vix'),
            fetch('/api/qqq/portfolio'),
        ]);
        const quotes = await quotesRes.json();
        const portfolio = await portfolioRes.json();
        const vix = await vixRes.json();
        const qqqPortfolio = await qqqRes.json();

        updateQuotes(quotes);
        updatePortfolio(portfolio);
        updateVix(vix);
        updateQqqPortfolio(qqqPortfolio);
    } catch (e) {
        console.error('初始加载失败:', e);
    }

    loadTrades();
    loadEquityChart();
    loadQqqSma();

    // 定时刷新
    setInterval(loadEquityChart, 60000);
    setInterval(loadQqqSma, 300000);
}

init();
