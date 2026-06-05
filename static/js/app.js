// XSS Helpers
function esc(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
}
function safeUrl(url) {
    if (!url) return '#';
    try { var u = new URL(url); return (u.protocol === 'https:' || u.protocol === 'http:') ? u.href : '#'; } catch(e) { return '#'; }
}
function jsArg(val) {
    return esc(JSON.stringify(val));
}
// STOCK MONITOR PRO - Frontend Application
var API_BASE = '';
var AUTO_REFRESH_INTERVAL = 180000;
var state = { currentPage: 'dashboard', charts: {}, refreshTimer: null, theme: localStorage.getItem('theme') || 'light', searchTimer: null, user: null, authMode: 'login', appStarted: false };
var Q = String.fromCharCode(34); // quote char
async function apiFetch(url, options) {
    options = options || {};
    try {
        var res = await fetch(url, { headers: { 'Content-Type': 'application/json', ...options.headers }, ...options });
        if (res.status === 401) { showAuthGate(); throw new Error('Not authenticated'); }
        if (!res.ok) throw new Error(`API ` + res.status + `: ` + res.statusText);
        return await res.json();
    } catch (err) { console.error(`API Error (` + url + `):`, err.message); return null; }
}
function formatCurrency(val, currency) { currency = currency || 'USD'; if (val == null) return '-'; return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency }).format(val); }
function formatNum(val, decimals) { decimals = decimals || 2; if (val == null) return '-'; return Number(val).toFixed(decimals); }
function formatPnl(val) { if (val == null) return '-'; return (val >= 0 ? '+' : '') + formatCurrency(val); }
function formatPct(val) { if (val == null) return '-'; return (val >= 0 ? '+' : '') + val.toFixed(2) + '%'; }
function pnlClass(val) { if (val == null) return ''; return val >= 0 ? 'positive' : 'negative'; }
function timeAgo(dateStr) {
    if (!dateStr) return '-';
    var diff = Date.now() - new Date(dateStr).getTime();
    var mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    var hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + 'h ago';
    return Math.floor(hrs / 24) + 'd ago';
}
function showNotification(msg, type) {
    type = type || 'info';
    var colors = { success: '#22c55e', error: '#ef4444', info: '#3b82f6', warning: '#f59e0b' };
    var bg = colors[type] || colors.info;
    var notif = document.createElement('div');
    Object.assign(notif.style, { position: 'fixed', top: '20px', right: '20px', zIndex: '10000', padding: '12px 20px', borderRadius: '8px', color: '#fff', fontSize: '14px', background: bg, boxShadow: '0 4px 12px rgba(0,0,0,0.15)', animation: 'slideIn 0.3s ease' });
    notif.textContent = msg;
    document.body.appendChild(notif);
    setTimeout(function() { notif.style.opacity = '0'; notif.style.transition = 'opacity 0.3s'; }, 2500);
    setTimeout(function() { notif.remove(); }, 3000);
}
// NAVIGATION
document.querySelectorAll('.nav-item').forEach(function(item) {
    item.addEventListener('click', function(e) { e.preventDefault(); switchPage(item.dataset.page); });
});
async function switchPage(page) {
    document.querySelectorAll('.page').forEach(function(p) { p.classList.remove('active'); });
    document.querySelectorAll('.nav-item').forEach(function(n) { n.classList.remove('active'); });
    var pageEl = document.getElementById('page-' + page);
    if (pageEl) pageEl.classList.add('active');
    var navEl = document.querySelector('.nav-item[data-page="' + page + '"]');
    if (navEl) navEl.classList.add('active');
    state.currentPage = page;
    switch (page) {
        case 'dashboard': await loadDashboard(); break;
        case 'holdings': await loadHoldingsPage(); break;
        case 'watchlist': await loadWatchlist(); break;
        case 'alerts': await loadAlerts(); break;
        case 'news': await loadNews(); break;
        case 'analysis': await loadAnalysis(); break;
        case 'settings': await loadSettings(); break;
    }
}

// AUTH
function showAuthGate() {
    var gate = document.getElementById('authGate');
    if (gate) gate.classList.add('show');
}
function hideAuthGate() {
    var gate = document.getElementById('authGate');
    if (gate) gate.classList.remove('show');
}
function toggleAuthMode() {
    state.authMode = state.authMode === 'login' ? 'register' : 'login';
    var isRegister = state.authMode === 'register';
    document.getElementById('authTitle').textContent = isRegister ? 'Create account' : 'Sign in';
    document.getElementById('authSubmitBtn').textContent = isRegister ? 'Create account' : 'Sign in';
    document.getElementById('authSwitchBtn').textContent = isRegister ? 'I already have an account' : 'Create an account';
}
async function submitAuth(event) {
    event.preventDefault();
    var fd = new FormData(event.target);
    var payload = { username: fd.get('username'), password: fd.get('password') };
    var path = state.authMode === 'register' ? '/api/auth/register' : '/api/auth/login';
    var user = await apiFetch(path, { method: 'POST', body: JSON.stringify(payload) });
    if (!user) { showNotification('Login failed. Check username/password.', 'error'); return; }
    state.user = user;
    updateUserLabel();
    hideAuthGate();
    event.target.reset();
    await startAuthedApp();
}
async function checkAuth() {
    var user = await apiFetch('/api/auth/me');
    if (!user) { showAuthGate(); return false; }
    state.user = user;
    updateUserLabel();
    hideAuthGate();
    return true;
}
function updateUserLabel() {
    var el = document.getElementById('currentUserLabel');
    if (el) el.textContent = state.user ? state.user.username : '';
}
async function logout() {
    await apiFetch('/api/auth/logout', { method: 'POST' });
    state.user = null;
    state.appStarted = false;
    if (state.refreshTimer) { clearInterval(state.refreshTimer); state.refreshTimer = null; }
    updateUserLabel();
    showAuthGate();
}

async function startAuthedApp() {
    if (state.appStarted) {
        await refreshCurrentPage();
        return;
    }
    state.appStarted = true;
    await loadDashboard();
    loadIndices();
    loadDashboardIndicators();
    populateNewsFilter();
    setInterval(function() { loadIndices(); }, 300000);
    setInterval(loadMarketStatus, 60000);
    startRefreshCountdown();
    state.refreshTimer = setInterval(refreshCurrentPage, AUTO_REFRESH_INTERVAL);
}

// THEME
function toggleTheme() {
    state.theme = state.theme === 'light' ? 'dark' : 'light';
    localStorage.setItem('theme', state.theme);
    applyTheme();
}
function applyTheme() {
    if (state.theme === 'light') { document.body.setAttribute('data-theme', 'light'); }
    else { document.body.removeAttribute('data-theme'); }
    var icon = document.getElementById('themeIcon');
    if (icon) icon.className = state.theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
}

// MARKET INDICES
async function loadIndices() {
    var data = await apiFetch('/api/market/indices');
    if (!data) return;
    var indices = ['GSPC', 'DJI', 'IXIC', 'VIX'];
    var names = { GSPC: 'S&P 500', DJI: 'Dow Jones', IXIC: 'NASDAQ', VIX: 'VIX' };
    indices.forEach(function(sym) {
        var info = data[sym];
        if (!info) return;
        var el = document.getElementById('index-' + sym);
        if (!el) return;
        var price = info.price || 0;
        var change = info.change || 0;
        var changePct = info.changePercent || 0;
        var cls = change >= 0 ? 'positive' : 'negative';
        el.innerHTML = '<div class=' + Q + 'index-name' + Q + '>' + names[sym] + '</div>'
            + '<div class=' + Q + 'index-price' + Q + '>' + formatNum(price, 2) + '</div>'
            + '<div class=' + Q + 'index-change ' + cls + Q + '>' + (change >= 0 ? '+' : '') + formatNum(change, 2) + ' (' + (changePct >= 0 ? '+' : '') + formatNum(changePct, 2) + '%)</div>';
    });
    flashPriceUpdate();
}

// PORTFOLIO CHART
function loadPortfolioChart() {
    var canvas = document.getElementById('portfolioChart');
    if (!canvas) return;
    if (state.charts.portfolio) state.charts.portfolio.destroy();
    apiFetch('/api/portfolio/history').then(function(data) {
        if (!data || data.length === 0) {
            var ctx = canvas.getContext('2d');
            if (ctx) {
                ctx.fillStyle = '#6b7280';
                ctx.font = '14px sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('No portfolio data available yet.', canvas.width / 2, canvas.height / 2);
            }
            return;
        }
        var recent = data
            .filter(function(s) { return (s.total_value || 0) > 0 && !isNaN(new Date(s.timestamp).getTime()); })
            .slice(-90); // last 90 valid snapshots
        if (recent.length === 0) {
            var emptyCtx = canvas.getContext('2d');
            if (emptyCtx) {
                emptyCtx.fillStyle = '#6b7280';
                emptyCtx.font = '14px sans-serif';
                emptyCtx.textAlign = 'center';
                emptyCtx.fillText('No valid portfolio history yet.', canvas.width / 2, canvas.height / 2);
            }
            return;
        }
        state.charts.portfolio = new Chart(canvas, {
            type: 'line',
            data: {
                datasets: [
                    { label: 'Portfolio Value', data: recent.map(function(s) { return { x: new Date(s.timestamp), y: s.total_value }; }), borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', borderWidth: 2, fill: true, tension: 0.2, pointRadius: 0 },
                    { label: 'Cost Basis', data: recent.map(function(s) { return { x: new Date(s.timestamp), y: s.total_cost }; }), borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.1)', borderWidth: 1.5, fill: true, tension: 0.2, pointRadius: 0 },
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: { padding: { bottom: 12 } },
                plugins: {
                    legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
                    tooltip: { callbacks: { title: function(items) { return items[0].parsed.x ? new Date(items[0].parsed.x).toLocaleString() : ''; } } }
                },
                scales: {
                    x: {
                        type: 'time',
                        time: {
                            unit: recent.length > 36 ? 'day' : 'hour',
                            displayFormats: { hour: 'M/d HH:mm', day: 'M/d' },
                            tooltipFormat: 'PPpp'
                        },
                        offset: true,
                        bounds: 'ticks',
                        ticks: {
                            autoSkip: true,
                            maxTicksLimit: 6,
                            maxRotation: 0,
                            minRotation: 0,
                            padding: 8,
                            font: { size: 10 }
                        }
                    },
                    y: { ticks: { callback: function(v) { return formatCurrency(v); } } }
                },
                interaction: { intersect: false, mode: 'index' }
            }
        });
    });
}

// DASHBOARD
async function loadDashboard() {
    var data = await apiFetch('/api/dashboard');
    if (!data) return;
    var el = function(id) { return document.getElementById(id); };
    if (el('totalValue')) el('totalValue').textContent = formatCurrency(data.total_value);
    if (el('totalCost')) el('totalCost').textContent = formatCurrency(data.total_cost);
    if (el('totalPnl')) { el('totalPnl').textContent = formatPnl(data.total_pnl); el('totalPnl').className = pnlClass(data.total_pnl); }
    if (el('totalPnlPercent')) { el('totalPnlPercent').textContent = formatPct(data.total_pnl_percent); el('totalPnlPercent').className = pnlClass(data.total_pnl_percent); }
    if (el('holdingsCount')) el('holdingsCount').textContent = data.holdings_count;
    if (el('watchlistCount')) el('watchlistCount').textContent = data.watchlist_count;
    if (el('alertCount')) el('alertCount').textContent = data.alert_count;
    if (el('topPerformer') && data.top_performer) el('topPerformer').textContent = data.top_performer;
    if (el('worstPerformer') && data.worst_performer) el('worstPerformer').textContent = data.worst_performer;
    if (el('lastUpdated')) el('lastUpdated').textContent = 'Updated: ' + new Date().toLocaleTimeString();
    flashPriceUpdate();
    loadPortfolioChart();
    var hTable = el('holdingsBody');
    if (hTable) {
        if (data.holdings && data.holdings.length > 0) {
            hTable.innerHTML = data.holdings.map(function(h, i) {
                return '<tr><td><strong>' + h.symbol + '</strong></td><td>' + (h.name || '-') + '</td><td>' + h.quantity + '</td><td>' + formatCurrency(h.avg_cost) + '</td><td>' + formatCurrency(h.current_price) + '</td><td>' + formatCurrency(h.current_value) + '</td><td class=' + Q + pnlClass(h.pnl) + Q + '>' + formatPnl(h.pnl) + '</td><td class=' + Q + pnlClass(h.pnl_percent) + Q + '>' + formatPct(h.pnl_percent) + '</td><td><canvas id=' + Q + 'miniChart-' + h.id + Q + '></canvas></td><td><button class=' + Q + 'btn-sm btn-primary' + Q + ' onclick=' + Q + 'editHolding(' + jsArg(h.id) + ')' + Q + '><i class=' + Q + 'fas fa-edit' + Q + '></i></button><button class=' + Q + 'btn-sm btn-danger' + Q + ' onclick=' + Q + 'deleteHolding(' + jsArg(h.id) + ')' + Q + '><i class=' + Q + 'fas fa-trash' + Q + '></i></button></td></tr>';
            }).join('');
            data.holdings.forEach(function(h) { loadMiniChart(h.symbol, 'miniChart-' + h.id); });
        } else { hTable.innerHTML = '<tr><td colspan=' + Q + '10' + Q + ' class=' + Q + 'empty-state' + Q + '>' + Q + 'No holdings yet. Click Add Holding to get started.' + Q + '</td></tr>'; }
    }
    var aList = el('alertsList');
    if (aList) {
        if (data.alerts && data.alerts.length > 0) {
            aList.innerHTML = data.alerts.slice(0, 3).map(function(a) {
                return '<div class=' + Q + 'alert-item' + Q + '><span class=' + Q + 'alert-symbol' + Q + '>' + esc(a.symbol) + '</span><span class=' + Q + 'alert-condition' + Q + '>' + a.condition.toUpperCase() + '</span><span class=' + Q + 'alert-target' + Q + '>' + formatCurrency(a.target_price) + '</span><span class=' + Q + 'alert-status' + Q + '></span></div>';
            }).join('');
        } else { aList.innerHTML = '<p class=' + Q + 'empty-state' + Q + '>No alerts set.</p>'; }
    }
    var nList = el('recentNewsList');
    if (nList) {
        if (data.recent_news && data.recent_news.length > 0) {
            nList.innerHTML = data.recent_news.slice(0, 3).map(function(n) {
                return '<div class=' + Q + 'news-item' + Q + '><div class=' + Q + 'news-header' + Q + '><span class=' + Q + 'news-symbol' + Q + '>' + esc(n.symbol || '') + '</span><span class=' + Q + 'news-time' + Q + '>' + timeAgo(n.published_at) + '</span></div><h3 class=' + Q + 'news-title' + Q + '><a href=' + Q + safeUrl(n.url) + Q + ' target=' + Q + '_blank' + Q + '>' + esc(n.title) + '</a></h3></div>';
            }).join('');
        } else { nList.innerHTML = '<p class=' + Q + 'empty-state' + Q + '>No recent news. Click Fetch News to get started.</p>'; }
    }
}

// MINI CHARTS
async function loadMiniChart(symbol, canvasId) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    // Destroy existing chart to prevent memory leaks
    var existingKey = 'chart_' + canvasId;
    if (state.charts[existingKey]) { state.charts[existingKey].destroy(); }
    var data = await apiFetch('/api/stocks/details/' + symbol);
    if (!data) return;
    var priceData = data.historicPrice || data.chartData;
    if (!priceData || priceData.length === 0) {
        // Draw placeholder text on canvas
        var ctx = canvas.getContext('2d');
        if (ctx) {
            ctx.fillStyle = '#6b7280';
            ctx.font = '8px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(symbol, canvas.width / 2, canvas.height / 2 + 3);
        }
        return;
    }
    var recent = priceData.slice(-30);
    try {
        state.charts['chart_' + canvasId] = new Chart(canvas, { type: 'line', data: { labels: recent.map(function() { return 0; }), datasets: [{ data: recent.map(function(p) { return p.close || p; }), borderColor: '#3b82f6', borderWidth: 1.5, pointRadius: 0, fill: false }] }, options: { responsive: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } } } });
    } catch (e) { console.error('Mini chart error for ' + symbol + ':', e); }
}

async function loadDashboardIndicators() {
    var betaData = await apiFetch('/api/quant/beta');
    if (betaData && betaData.betas && betaData.betas.length > 0) {
        var betaTable = document.getElementById('betaTableBody');
        if (betaTable) {
            betaTable.innerHTML = betaData.betas.map(function(b) {
                var cls = b.beta < 0.8 ? 'defensive' : (b.beta > 1.2 ? 'aggressive' : 'moderate');
                var label = b.classification || cls;
                return '<tr><td><strong>' + b.symbol + '</strong></td><td class=' + Q + pnlClass(b.beta - 1) + Q + '>' + b.beta.toFixed(2) + '</td><td><span class=' + Q + 'beta-badge ' + cls + Q + '>' + label + '</span></td><td>' + (b.allocation != null ? b.allocation.toFixed(1) + '%' : '-') + '</td></tr>';
            }).join('');
        }
        var betaCanvas = document.getElementById('betaChart');
        if (betaCanvas) {
            if (state.charts.beta) state.charts.beta.destroy();
            state.charts.beta = new Chart(betaCanvas, { type: 'bar', data: { labels: betaData.betas.map(function(b) { return b.symbol; }), datasets: [{ label: 'Beta', data: betaData.betas.map(function(b) { return b.beta; }), backgroundColor: betaData.betas.map(function(b) { if (b.beta < 0.8) return '#3b82f6'; if (b.beta > 1.2) return '#ef4444'; return '#f59e0b'; }), borderWidth: 0 }] }, options: { responsive: true, plugins: { legend: { display: false }, tooltip: { callbacks: { label: function(ctx) { return 'Beta: ' + ctx.parsed.y.toFixed(2); } } } }, scales: { y: { min: 0, max: 2, ticks: { callback: function(v) { return v.toFixed(1); } } }, x: { ticks: { font: { size: 10 } } } } } });
        }
    }
    var volData = await apiFetch('/api/quant/volatility');
    if (volData && volData.volatility && volData.volatility.length > 0) {
        var volTable = document.getElementById('volTableBody');
        if (volTable) {
            volTable.innerHTML = volData.volatility.map(function(v) {
                var cls = v.volatility_bracket === 'Low' ? 'low' : (v.volatility_bracket === 'High' ? 'high' : 'moderate');
                return '<tr><td><strong>' + v.symbol + '</strong></td><td>' + (v.annualized_volatility != null ? v.annualized_volatility.toFixed(1) + '%' : '-') + '</td><td>' + (v.atr_14 != null ? v.atr_14.toFixed(2) : '-') + '</td><td><span class=' + Q + 'vol-badge ' + cls + Q + '>' + v.volatility_bracket + '</span></td></tr>';
            }).join('');
        }
    }
    var divData = await apiFetch('/api/quant/dividends');
    if (divData && divData.dividends) {
        var paying = divData.dividends.filter(function(d) { return d.dividend_yield != null; });
        var df = document.getElementById('divForecast');
        var dp = document.getElementById('divPayingCount');
        var da = document.getElementById('divAvgYield');
        var dt = document.getElementById('divTableBody');
        if (df) df.textContent = formatCurrency(divData.total_annual_forecast);
        if (dp) dp.textContent = paying.length + ' / ' + divData.dividends.length;
        var avg = paying.length > 0 ? paying.reduce(function(s, d) { return s + (d.dividend_yield || 0); }, 0) / paying.length : 0;
        if (da) da.textContent = avg.toFixed(2) + '%';
        if (dt) {
            dt.innerHTML = divData.dividends.map(function(d) {
                return '<tr><td><strong>' + d.symbol + '</strong></td><td>' + (d.dividend_yield != null ? d.dividend_yield.toFixed(2) + '%' : '-') + '</td><td>' + (d.annual_div_per_share != null ? formatCurrency(d.annual_div_per_share) : '-') + '</td><td>' + d.quantity + '</td><td class=' + Q + pnlClass(d.annual_income) + Q + '>' + (d.annual_income > 0 ? formatCurrency(d.annual_income) : '-') + '</td></tr>';
            }).join('');
        }
    }
}

// HOLDINGS CRUD
async function loadHoldingsPage() {
    var all = await apiFetch('/api/holdings');
    var tbody = document.getElementById('holdingsPageBody');
    if (!tbody) return;
    if (!all || all.length === 0) { tbody.innerHTML = '<tr><td colspan=' + Q + '10' + Q + ' class=' + Q + 'empty-state' + Q + '>No holdings yet.</td></tr>'; return; }
    tbody.innerHTML = all.map(function(h) {
        return '<tr><td><strong>' + esc(h.symbol) + '</strong></td><td>' + esc(h.name || '-') + '</td><td>' + h.quantity + '</td><td>' + formatCurrency(h.avg_cost) + '</td><td>' + formatCurrency(h.current_price) + '</td><td>' + formatCurrency(h.current_value) + '</td><td class=' + Q + pnlClass(h.pnl) + Q + '>' + formatPnl(h.pnl) + '</td><td class=' + Q + pnlClass(h.pnl_percent) + Q + '>' + formatPct(h.pnl_percent) + '</td><td><canvas id=' + Q + 'pageMiniChart-' + h.id + Q + '></canvas></td><td><button class=' + Q + 'btn-sm btn-primary' + Q + ' onclick=' + Q + 'viewStockDetail(' + jsArg(h.symbol) + ')' + Q + '><i class=' + Q + 'fas fa-eye' + Q + '></i></button><button class=' + Q + 'btn-sm btn-secondary' + Q + ' onclick=' + Q + 'editHolding(' + jsArg(h.id) + ')' + Q + '><i class=' + Q + 'fas fa-edit' + Q + '></i></button><button class=' + Q + 'btn-sm btn-danger' + Q + ' onclick=' + Q + 'deleteHolding(' + jsArg(h.id) + ')' + Q + '><i class=' + Q + 'fas fa-trash' + Q + '></i></button></td></tr>';
    }).join('');
    all.forEach(function(h) { loadMiniChart(h.symbol, 'pageMiniChart-' + h.id); });
}

async function submitHolding(event) {
    event.preventDefault();
    var form = event.target;
    var fd = new FormData(form);
    var data = { symbol: fd.get('symbol').toUpperCase(), name: fd.get('name') || null, quantity: parseFloat(fd.get('quantity')), avg_cost: parseFloat(fd.get('avg_cost')), currency: fd.get('currency') || 'USD' };
    var result = await apiFetch('/api/holdings', { method: 'POST', body: JSON.stringify(data) });
    if (result) { showNotification(data.symbol + ' added successfully!', 'success'); closeModal('addHoldingModal'); form.reset(); refreshCurrentPage(); }
}
async function deleteHolding(id) {
    if (!confirm('Delete this holding?')) return;
    await apiFetch('/api/holdings_by_id/' + id, { method: 'DELETE' });
    showNotification('Holding deleted.', 'info');
    refreshCurrentPage();
}
async function editHolding(id) {
    var all = await apiFetch('/api/holdings');
    if (!all) return;
    var result = all.find(function(h) { return h.id == id; });
    if (!result) return;
    var modal = document.getElementById('addHoldingModal');
    var form = modal.querySelector('form');
    form.querySelector('[name=' + Q + 'symbol' + Q + ']').value = result.symbol;
    form.querySelector('[name=' + Q + 'symbol' + Q + ']').readOnly = true;
    form.querySelector('[name=' + Q + 'name' + Q + ']').value = result.name || '';
    form.querySelector('[name=' + Q + 'quantity' + Q + ']').value = result.quantity;
    form.querySelector('[name=' + Q + 'avg_cost' + Q + ']').value = result.avg_cost;
    openModal('addHoldingModal');
    form.onsubmit = async function(e) {
        e.preventDefault();
        var fd = new FormData(form);
        var data = { name: fd.get('name') || null, quantity: parseFloat(fd.get('quantity')), avg_cost: parseFloat(fd.get('avg_cost')) };
        var updated = await apiFetch('/api/holdings/' + result.symbol, { method: 'PUT', body: JSON.stringify(data) });
        if (updated) {
            showNotification(result.symbol + ' updated!', 'success');
            closeModal('addHoldingModal');
            form.reset();
            form.querySelector('[name=' + Q + 'symbol' + Q + ']').readOnly = false;
            form.onsubmit = submitHolding;
            refreshCurrentPage();
        }
    };
}

// WATCHLIST
async function loadWatchlist() {
    var items = await apiFetch('/api/watchlist');
    var tbody = document.getElementById('watchlistBody');
    if (!tbody) return;
    if (!items || items.length === 0) { tbody.innerHTML = '<tr><td colspan=' + Q + '6' + Q + ' class=' + Q + 'empty-state' + Q + '>Your watchlist is empty.</td></tr>'; return; }
    tbody.innerHTML = items.map(function(w) {
        return '<tr><td><strong>' + esc(w.symbol) + '</strong></td><td>' + esc(w.name || '-') + '</td><td>' + (w.current_price ? formatCurrency(w.current_price) : '-') + '</td><td class=' + Q + pnlClass(w.price_change) + Q + '>' + (w.price_change != null ? formatPnl(w.price_change) : '-') + '</td><td class=' + Q + pnlClass(w.price_change_percent) + Q + '>' + (w.price_change_percent != null ? formatPct(w.price_change_percent) : '-') + '</td><td><button class=' + Q + 'btn-sm btn-primary' + Q + ' onclick=' + Q + 'viewStockDetail(' + jsArg(w.symbol) + ')' + Q + '><i class=' + Q + 'fas fa-eye' + Q + '></i></button><button class=' + Q + 'btn-sm btn-danger' + Q + ' onclick=' + Q + 'removeFromWatchlist(' + jsArg(w.symbol) + ')' + Q + '><i class=' + Q + 'fas fa-trash' + Q + '></i></button></td></tr>';
    }).join('');
}
async function submitWatchlist(event) {
    event.preventDefault();
    var form = event.target;
    var fd = new FormData(form);
    var data = { symbol: fd.get('symbol').toUpperCase(), name: fd.get('name') || '' };
    var result = await apiFetch('/api/watchlist', { method: 'POST', body: JSON.stringify(data) });
    if (result) { showNotification(data.symbol + ' added to watchlist!', 'success'); closeModal('addWatchlistModal'); form.reset(); loadWatchlist(); }
}
async function removeFromWatchlist(symbol) {
    await apiFetch('/api/watchlist/' + symbol, { method: 'DELETE' });
    showNotification(symbol + ' removed from watchlist.', 'info');
    loadWatchlist();
}

// ALERTS
async function loadAlerts() {
    var alerts = await apiFetch('/api/alerts');
    var container = document.getElementById('alertsList');
    if (!container) return;
    if (!alerts || alerts.length === 0) { container.innerHTML = '<p class=' + Q + 'empty-state' + Q + '>No alerts set.</p>'; return; }
    container.innerHTML = alerts.map(function(a) {
        return '<div class=' + Q + 'alert-item' + Q + '><span class=' + Q + 'alert-symbol' + Q + '>' + esc(a.symbol) + '</span><span class=' + Q + 'alert-condition' + Q + '>' + a.condition.toUpperCase() + '</span><span class=' + Q + 'alert-target' + Q + '>' + formatCurrency(a.target_price) + '</span><span class=' + Q + 'alert-current' + Q + '>Current: ' + (a.current_price != null ? formatCurrency(a.current_price) : '-') + '</span><span class=' + Q + 'alert-status ' + (a.triggered ? 'triggered' : '') + Q + '>' + (a.triggered ? 'Triggered' : (a.active ? 'Active' : 'Inactive')) + '</span><div class=' + Q + 'alert-actions' + Q + '><button class=' + Q + 'btn-sm' + Q + ' onclick=' + Q + 'toggleAlert(' + a.id + ')' + Q + '><i class=' + Q + 'fas fa-' + (a.active ? 'pause' : 'play') + Q + '></i></button><button class=' + Q + 'btn-sm btn-danger' + Q + ' onclick=' + Q + 'deleteAlert(' + a.id + ')' + Q + '><i class=' + Q + 'fas fa-trash' + Q + '></i></button></div></div>';
    }).join('');
    await populateAlertSymbolSelect();
}
async function submitAlert(event) {
    event.preventDefault();
    var form = event.target;
    var fd = new FormData(form);
    var data = { symbol: fd.get('symbol').toUpperCase(), condition: fd.get('condition'), target_price: parseFloat(fd.get('target_price')) };
    var result = await apiFetch('/api/alerts', { method: 'POST', body: JSON.stringify(data) });
    if (result) { showNotification('Alert created for ' + data.symbol + '!', 'success'); closeModal('addAlertModal'); form.reset(); loadAlerts(); }
}
async function toggleAlert(id) {
    await apiFetch('/api/alerts/' + id + '/toggle', { method: 'PUT' });
    showNotification('Alert toggled.', 'info');
    loadAlerts();
}
async function deleteAlert(id) {
    if (!confirm('Delete this alert?')) return;
    await apiFetch('/api/alerts/' + id, { method: 'DELETE' });
    showNotification('Alert deleted.', 'info');
    loadAlerts();
}
async function populateAlertSymbolSelect() {
    var select = document.querySelector('#addAlertModal select[name=' + Q + 'symbol' + Q + ']');
    if (!select) return;
    var defaultOpt = select.querySelector('option[value=' + Q + Q + ']');
    select.innerHTML = '';
    if (defaultOpt) select.appendChild(defaultOpt);
    try {
        var symbols = await apiFetch('/api/alert-symbols');
        if (symbols) {
            symbols.forEach(function(item) {
                var opt = document.createElement('option');
                opt.value = item.symbol; opt.textContent = item.symbol + (item.name ? ' (' + item.name + ')' : ''); opt.dataset.type = 'holding';
                select.appendChild(opt);
            });
        }
    } catch(e) { console.error('Failed to load alert symbols:', e); }
}

// NEWS
async function loadNews() {
    var stockFilter = document.getElementById('newsFilterStock');
    var statusFilter = document.getElementById('newsFilterStatus');
    var symbol = stockFilter ? stockFilter.value : '';
    var isRead = null;
    if (statusFilter) { isRead = statusFilter.value === '' ? null : (statusFilter.value === '1'); }
    var params = [];
    if (symbol) params.push('symbol=' + encodeURIComponent(symbol));
    if (isRead !== null) params.push('is_read=' + (isRead ? 1 : 0));
    var url = '/api/news' + (params.length ? '?' + params.join('&') : '');
    var news = await apiFetch(url);
    var container = document.getElementById('newsList');
    if (!container) return;
    if (!news || news.length === 0) { container.innerHTML = '<p class=' + Q + 'empty-state' + Q + '>No news found.</p>'; return; }
    container.innerHTML = news.map(function(n) {
        var html = '<div class=' + Q + 'news-item' + Q + ' ' + Q + (n.is_read ? 'read' : '') + Q + '><div class=' + Q + 'news-header' + Q + '><span class=' + Q + 'news-symbol' + Q + '>' + esc(n.symbol || '') + '</span><span class=' + Q + 'news-time' + Q + '>' + timeAgo(n.published_at) + '</span></div>';
        html += '<h3 class=' + Q + 'news-title' + Q + '><a href=' + Q + safeUrl(n.url) + Q + ' target=' + Q + '_blank' + Q + '>' + esc(n.title) + '</a></h3>';
        if (n.summary) html += '<p class=' + Q + 'news-summary' + Q + '>' + esc(n.summary) + '</p>';
        html += '<div class=' + Q + 'news-footer' + Q + '><span class=' + Q + 'news-source' + Q + '>' + esc(n.source || '') + '</span><button class=' + Q + 'btn-sm' + Q + ' onclick=' + Q + 'markNewsRead(' + n.id + ', this)' + Q + '>' + (n.is_read ? '<i class=' + Q + 'fas fa-check' + Q + '></i> Read' : '<i class=' + Q + 'far fa-circle' + Q + '></i> Unread') + '</button></div></div>';
        return html;
    }).join('');
}
async function markNewsRead(id) {
    await apiFetch('/api/news/' + id + '/read', { method: 'PUT' });
    showNotification('Marked as read.', 'info');
    loadNews();
}
async function fetchNews() {
    var result = await apiFetch('/api/news/fetch', { method: 'POST' });
    if (result) { showNotification('Fetched ' + result.new_count + ' new news items.', 'success'); loadNews(); }
}
async function populateNewsFilter() {
    var select = document.getElementById('newsFilterStock');
    if (!select) return;
    var holdings = await apiFetch('/api/holdings');
    var watchlist = await apiFetch('/api/watchlist');
    var allSymbols = [];
    (holdings || []).forEach(function(h) { if (allSymbols.indexOf(h.symbol) === -1) allSymbols.push(h.symbol); });
    (watchlist || []).forEach(function(w) { if (allSymbols.indexOf(w.symbol) === -1) allSymbols.push(w.symbol); });
    allSymbols.forEach(function(sym) {
        var opt = document.createElement('option');
        opt.value = sym; opt.textContent = sym;
        select.appendChild(opt);
    });
}

// ANALYSIS
async function loadAnalysis() {
    var performance = await apiFetch('/api/portfolio/performance');
    var allocCanvas = document.getElementById('allocationChart');
    var pnlCanvas = document.getElementById('pnlChart');
    var tbody = document.getElementById('analysisBody');
    if (!performance || performance.length === 0) {
        if (allocCanvas) allocCanvas.parentElement.innerHTML = '<p class=' + Q + 'empty-state' + Q + '>Add holdings to see analysis.</p>';
        if (pnlCanvas) { if (state.charts.pnl) { state.charts.pnl.destroy(); state.charts.pnl = null; } }
        if (tbody) tbody.innerHTML = '<tr><td colspan=' + Q + '6' + Q + ' class=' + Q + 'empty-state' + Q + '>No data to analyze.</td></tr>';
        return;
    }
    if (allocCanvas) {
        if (state.charts.allocation) state.charts.allocation.destroy();
        state.charts.allocation = new Chart(allocCanvas, {
            type: 'doughnut',
            data: { labels: performance.map(function(p) { return p.symbol; }), datasets: [{ data: performance.map(function(p) { return p.allocation || 0; }), backgroundColor: ['#3b82f6','#ef4444','#22c55e','#f59e0b','#8b5cf6','#ec4899','#06b6d4','#f97316'], borderWidth: 2, borderColor: getComputedStyle(document.body).backgroundColor }] },
            options: { responsive: true, plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: function(ctx) { return ctx.label + ': ' + ctx.parsed + '%'; } } } } },
        });
    }
    if (pnlCanvas) {
        if (state.charts.pnl) state.charts.pnl.destroy();
        var pnlData = performance.map(function(p) { return p.pnl_percent || 0; });
        var pnlColors = pnlData.map(function(v) { return v >= 0 ? '#22c55e' : '#ef4444'; });
        state.charts.pnl = new Chart(pnlCanvas, {
            type: 'bar',
            data: { labels: performance.map(function(p) { return p.symbol; }), datasets: [{ label: 'P&L %', data: pnlData, backgroundColor: pnlColors, borderRadius: 4 }] },
            options: { responsive: true, plugins: { legend: { display: false }, tooltip: { callbacks: { label: function(ctx) { return ctx.parsed.y + '%'; } } } }, scales: { y: { ticks: { callback: function(v) { return v + '%'; } } } } },
        });
    }
    loadSectorAllocation();
    loadCorrelationMatrix();
    loadRiskMetrics();
    loadBetaData();
    loadVolatilityScanner();
    loadDividendTracker();
    if (tbody) {
        tbody.innerHTML = performance.map(function(p) {
            var mv = (p.current_price || 0) * (p.quantity || 0);
            var cb = (p.avg_cost || 0) * (p.quantity || 0);
            return '<tr><td>' + p.symbol + '</td><td>' + (p.quantity || 0) + '</td><td>' + formatCurrency(p.avg_cost) + '</td><td>' + formatCurrency(p.current_price) + '</td><td class=' + Q + pnlClass(p.pnl_percent) + Q + '>' + formatPct(p.pnl_percent) + '</td><td>' + (p.allocation || 0).toFixed(1) + '%</td></tr>';
        }).join('');
    }
}

// SETTINGS
async function loadSettings() {
    var settings = await apiFetch('/api/settings');
    if (!settings) return;
    var usernameInput = document.getElementById('accountUsername');
    if (usernameInput && state.user) usernameInput.value = state.user.username || '';
    var input = document.getElementById('finnhubApiKey');
    if (input) input.value = settings.finnhub_api_key || '';
}
async function saveUsername(event) {
    event.preventDefault();
    var fd = new FormData(event.target);
    var result = await apiFetch('/api/auth/username', {
        method: 'PUT',
        body: JSON.stringify({ username: fd.get('username') || '' })
    });
    if (result) {
        state.user = result;
        updateUserLabel();
        showNotification('Username updated.', 'success');
    }
}
async function savePassword(event) {
    event.preventDefault();
    var fd = new FormData(event.target);
    var result = await apiFetch('/api/auth/password', {
        method: 'PUT',
        body: JSON.stringify({
            current_password: fd.get('current_password') || '',
            new_password: fd.get('new_password') || ''
        })
    });
    if (result) {
        event.target.reset();
        showNotification('Password changed.', 'success');
    } else {
        showNotification('Password change failed. Check current password.', 'error');
    }
}
async function saveSettings(event) {
    event.preventDefault();
    var fd = new FormData(event.target);
    var result = await apiFetch('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ finnhub_api_key: fd.get('finnhub_api_key') || '' })
    });
    if (result) showNotification('Settings saved for this account.', 'success');
}
function toggleApiKeyVisibility() {
    var input = document.getElementById('finnhubApiKey');
    if (!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
}

// MODAL HELPERS
function showAddHoldingModal() { openModal('addHoldingModal'); }
function showAddWatchlistModal() { openModal('addWatchlistModal'); }
function showAddAlertModal() { openModal('addAlertModal'); }
function openModal(modalId) { var m = document.getElementById(modalId); if (m) m.classList.add('show'); }
function closeModal(modalId) { var m = document.getElementById(modalId); if (m) m.classList.remove('show'); }

// REFRESH COUNTDOWN
var refreshCountdown = null;
var refreshCountdownValue = 0;

function startRefreshCountdown() {
    refreshCountdownValue = AUTO_REFRESH_INTERVAL / 1000;
    if (refreshCountdown) clearInterval(refreshCountdown);
    refreshCountdown = setInterval(function() {
        refreshCountdownValue--;
        var timerEl = document.getElementById('refreshTimer');
        if (timerEl) {
            var mins = Math.floor(refreshCountdownValue / 60);
            var secs = refreshCountdownValue % 60;
            timerEl.textContent = mins + ':' + (secs < 10 ? '0' : '') + secs;
        }
        if (refreshCountdownValue <= 0) {
            refreshCountdownValue = AUTO_REFRESH_INTERVAL / 1000;
        }
    }, 1000);
}

// FLASH ANIMATION
function flashElement(el, className) {
    if (!el) return;
    el.classList.remove(className);
    void el.offsetWidth; // force reflow
    el.classList.add(className);
    setTimeout(function() { el.classList.remove(className); }, 1500);
}

// REFRESH
async function flashPriceUpdate() {
    var cards = document.querySelectorAll('.card-value');
    cards.forEach(function(c) { flashElement(c, 'price-flash'); });
}

async function refreshCurrentPage() { await switchPage(state.currentPage); }
async function refreshAll() {
    showNotification('Refreshing all data...', 'info');
    await Promise.all([loadDashboard(), loadIndices()]);
    showNotification('Data refreshed.', 'success');
}

// SEARCH
async function handleSearch(event) {
    if (event.key !== 'Enter') return;
    var query = document.getElementById('globalSearch').value.trim();
    if (!query) return;
    var results = await apiFetch('/api/stocks/search?query=' + encodeURIComponent(query));
    var container = document.getElementById('searchResults');
    if (!container) return;
    if (!results || results.length === 0) { container.innerHTML = ''; container.classList.remove('show'); return; }
    container.innerHTML = results.slice(0, 8).map(function(r) {
        return '<div class=' + Q + 'search-result-item' + Q + ' onclick=' + Q + 'viewStockDetail(' + jsArg(r.symbol) + '); document.getElementById(' + Q + 'searchResults' + Q + ').classList.remove(' + Q + 'show' + Q + '); document.getElementById(' + Q + 'globalSearch' + Q + ').value = ' + Q + Q + ';' + Q + '>' + '<span class=' + Q + 'search-result-symbol' + Q + '>' + esc(r.symbol) + '</span><span class=' + Q + 'search-result-name' + Q + '>' + esc(r.name || '') + '</span></div>';
    }).join('');
    container.classList.add('show');
}

// STOCK DETAIL
async function viewStockDetail(symbol) {
    var data = await apiFetch('/api/stocks/details/' + symbol);
    if (!data) { showNotification('Failed to load stock data.', 'error'); return; }
    document.getElementById('stockDetailTitle').textContent = data.symbol + ' - ' + data.name;
    var pc = data.priceChange || 0;
    var pcp = data.priceChangePercent || 0;
    var cc = pc >= 0 ? 'positive' : 'negative';
    document.getElementById('stockDetailContent').innerHTML = '<div style=' + Q + 'text-align:center; padding: 20px; border-bottom: 1px solid var(--border); margin-bottom: 20px;' + Q + '><div style=' + Q + 'font-size: 32px; font-weight: 700;' + Q + '>' + formatCurrency(data.currentPrice) + '</div><div class=' + Q + cc + Q + ' style=' + Q + 'font-size: 16px; margin-top: 4px;' + Q + '>' + (pc >= 0 ? '+' : '') + formatNum(pc, 2) + ' (' + (pcp >= 0 ? '+' : '') + formatNum(pcp, 2) + '%)</div></div><div class=' + Q + 'stock-detail-grid' + Q + '><div class=' + Q + 'detail-item' + Q + '><div class=' + Q + 'detail-label' + Q + '>Open</div><div class=' + Q + 'detail-value' + Q + '>' + (data.open != null ? formatCurrency(data.open) : '-') + '</div></div><div class=' + Q + 'detail-item' + Q + '><div class=' + Q + 'detail-label' + Q + '>Day High</div><div class=' + Q + 'detail-value' + Q + '>' + (data.dayHigh != null ? formatCurrency(data.dayHigh) : '-') + '</div></div><div class=' + Q + 'detail-item' + Q + '><div class=' + Q + 'detail-label' + Q + '>Day Low</div><div class=' + Q + 'detail-value' + Q + '>' + (data.dayLow != null ? formatCurrency(data.dayLow) : '-') + '</div></div><div class=' + Q + 'detail-item' + Q + '><div class=' + Q + 'detail-label' + Q + '>Prev Close</div><div class=' + Q + 'detail-value' + Q + '>' + (data.previousClose != null ? formatCurrency(data.previousClose) : '-') + '</div></div><div class=' + Q + 'detail-item' + Q + '><div class=' + Q + 'detail-label' + Q + '>Volume</div><div class=' + Q + 'detail-value' + Q + '>' + (data.volume != null ? data.volume.toLocaleString() : '-') + '</div></div><div class=' + Q + 'detail-item' + Q + '><div class=' + Q + 'detail-label' + Q + '>Market Cap</div><div class=' + Q + 'detail-value' + Q + '>' + (data.marketCap != null ? data.marketCap.toLocaleString() : '-') + '</div></div><div class=' + Q + 'detail-item' + Q + '><div class=' + Q + 'detail-label' + Q + '>P/E Ratio</div><div class=' + Q + 'detail-value' + Q + '>' + (data.peRatio != null ? formatNum(data.peRatio, 2) : '-') + '</div></div><div class=' + Q + 'detail-item' + Q + '><div class=' + Q + 'detail-label' + Q + '>52W Range</div><div class=' + Q + 'detail-value' + Q + ' style=' + Q + 'font-size: 14px;' + Q + '>' + (data.fiftyTwoWeekLow != null ? formatCurrency(data.fiftyTwoWeekLow) : '-') + ' - ' + (data.fiftyTwoWeekHigh != null ? formatCurrency(data.fiftyTwoWeekHigh) : '-') + '</div></div></div>';
    openModal('stockDetailModal');
    loadIndicators(symbol, data);
    renderIndicatorsChart(symbol, data);
}

document.addEventListener('click', function(e) {
    var searchBox = document.querySelector('.search-box');
    var results = document.getElementById('searchResults');
    if (searchBox && results && !searchBox.contains(e.target)) { results.classList.remove('show'); }
});


// MARKET STATUS
function loadMarketStatus() {
    var now = new Date();
    var utc = now.getUTCHours() * 60 + now.getUTCMinutes();
    var isUsOpen = utc >= 570 && utc <= 960;
    var isWeekday = now.getUTCDay() > 0 && now.getUTCDay() < 6;
    var isOpen = isWeekday && isUsOpen;
    var dot = document.getElementById('statusDot');
    var status = document.getElementById('marketStatus');
    if (dot && status) {
        if (isOpen) {
            dot.className = 'status-dot';
            status.textContent = 'Market Open';
            status.style.color = '#22c55e';
        } else if (!isWeekday) {
            dot.className = 'status-dot closed';
            status.textContent = 'Market Closed (Weekend)';
            status.style.color = '#ef4444';
        } else {
            dot.className = 'status-dot closed';
            status.textContent = 'Market Closed';
            status.style.color = '#ef4444';
        }
    }
}

// INITIALIZE
document.addEventListener('DOMContentLoaded', async function() {
    applyTheme();
    loadMarketStatus();
    var authed = await checkAuth();
    if (!authed) return;
    await startAuthedApp();
});

// TECHNICAL INDICATORS
async function loadIndicators(symbol) {
    var container = document.getElementById('stockDetailIndicators');
    var summaryEl = document.getElementById('indicatorsSummary');
    var toggleBtn = document.getElementById('toggleIndicatorsBtn');
    if (!container || !summaryEl || !toggleBtn) return;
    var data = await apiFetch('/api/quant/indicators/' + symbol);
    if (!data) { showNotification('Indicators unavailable for ' + symbol, 'warning'); container.style.display = 'none'; return; }
    summaryEl.innerHTML = '';
    var s = data.summary || data;
    var indicators = [
        { label: 'RSI (14)', value: s.rsi != null ? s.rsi.toFixed(1) : '-', pos: s.rsi != null },
        { label: 'SMA 20', value: s.sma_20 != null ? formatNum(s.sma_20, 2) : '-' },
        { label: 'SMA 50', value: s.sma_50 != null ? formatNum(s.sma_50, 2) : '-' },
        { label: 'MACD', value: s.macd != null ? formatNum(s.macd, 3) : '-' },
        { label: 'BB Upper', value: s.bb_upper != null ? formatNum(s.bb_upper, 2) : '-' },
        { label: 'BB Lower', value: s.bb_lower != null ? formatNum(s.bb_lower, 2) : '-' },
    ];
    summaryEl.innerHTML = '<div class=' + Q + 'indicator-grid' + Q + '>' + indicators.map(function(ind) {
        var cls = ind.pos != null ? (ind.pos ? 'positive' : 'negative') : '';
        return '<div class=' + Q + 'indicator-card' + Q + '><div class=' + Q + 'label' + Q + '>' + ind.label + '</div><div class=' + Q + 'value ' + cls + Q + '>' + ind.value + '</div></div>';
    }).join('') + '</div>';
    toggleBtn.textContent = 'Hide Indicators';
    container.style.display = 'block';
}

function toggleIndicators() {
    var container = document.getElementById('stockDetailIndicators');
    var btn = document.getElementById('toggleIndicatorsBtn');
    if (container.style.display === 'none') { container.style.display = 'block'; btn.textContent = 'Hide Indicators'; }
    else { container.style.display = 'none'; btn.textContent = 'Show Indicators'; }
}

async function renderIndicatorsChart(symbol) {
    var canvas = document.getElementById('indicatorsChart');
    if (!canvas) return;
    // Destroy existing chart to prevent memory leaks
    var existingKey = 'chart_' + canvas.id;
    if (state.charts[existingKey]) { state.charts[existingKey].destroy(); }
    var data = await apiFetch('/api/quant/indicators/' + symbol);
    if (!data) return;
    if (state.charts.indicators) state.charts.indicators.destroy();
    var recent = 60;
    var dates = data.dates.slice(-recent);
    var closes = (data.closes || data.close || []).slice(-recent);
    var indicators = data.indicators || data;
    function makeDS(label, key, color, dash, fill) {
        var values = (indicators[key] || []).slice(-recent);
        return { label: label, data: values.map(function(v, i) { return { x: dates[i], y: v }; }), type: 'line', borderColor: color, borderWidth: 1, pointRadius: 0, borderDash: dash || [], fill: fill || false, yAxisID: 'y' };
    }
    var ds = [
        { label: 'Price', data: closes.map(function(c, i) { return { x: dates[i], y: c }; }), type: 'line', borderColor: '#3b82f6', borderWidth: 1.5, pointRadius: 0, fill: false, yAxisID: 'y' },
        makeDS('SMA 20', 'sma_20', '#f59e0b', [5,5], false),
        makeDS('SMA 50', 'sma_50', '#8b5cf6', [3,3], false),
        makeDS('BB Upper', 'bb_upper', 'rgba(59,130,246,0.3)', [], false),
        { label: 'BB Lower', data: (indicators.bb_lower || []).slice(-recent).map(function(v, i) { return { x: dates[i], y: v }; }), type: 'line', borderColor: 'rgba(59,130,246,0.3)', borderWidth: 1, pointRadius: 0, fill: '-1', backgroundColor: 'rgba(59,130,246,0.05)', yAxisID: 'y' },
    ];
    state.charts.indicators = state.charts['chart_' + canvas.id] = new Chart(canvas, { type: 'line', data: { datasets: ds }, options: { responsive: true, interaction: { mode: 'index', intersect: false }, plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } }, tooltip: { callbacks: { label: function(ctx) { return ctx.dataset.label + ': ' + formatNum(ctx.parsed.y, 2); } } } }, scales: { x: { type: 'category', ticks: { maxTicksLimit: 10, font: { size: 10 } } }, y: { position: 'right', ticks: { callback: function(v) { return formatNum(v, 2); } } } } } });
}

// CORRELATION MATRIX
function correlationColor(value) {
    if (value == null) return 'rgba(100, 116, 139, 0.20)';
    var strength = Math.min(Math.abs(value), 1);
    if (value >= 0.65) return 'rgba(34, 197, 94, ' + (0.25 + strength * 0.55) + ')';
    if (value <= -0.35) return 'rgba(239, 68, 68, ' + (0.25 + strength * 0.55) + ')';
    return 'rgba(148, 163, 184, ' + (0.18 + strength * 0.20) + ')';
}

function correlationLabel(value) {
    if (value == null) return 'No data';
    if (value >= 0.8) return 'Very similar';
    if (value >= 0.5) return 'Similar';
    if (value > -0.3) return 'Weak/neutral';
    if (value > -0.6) return 'Opposite';
    return 'Strong opposite';
}

async function loadCorrelationMatrix() {
    var canvas = document.getElementById('correlationChart');
    if (!canvas) return;
    // Destroy existing chart to prevent memory leaks
    var existingKey = 'chart_' + canvas.id;
    if (state.charts[existingKey]) { state.charts[existingKey].destroy(); }
    var data = await apiFetch('/api/quant/correlation');
    if (!data || !data.symbols || data.symbols.length < 2) {
        canvas.style.display = 'none';
        var emptyWrap = document.getElementById('correlationReadable');
        if (!emptyWrap) {
            emptyWrap = document.createElement('div');
            emptyWrap.id = 'correlationReadable';
            canvas.parentElement.appendChild(emptyWrap);
        }
        emptyWrap.className = 'correlation-readable empty-state';
        emptyWrap.textContent = 'Add 2+ holdings to see correlations.';
        return;
    }
    canvas.style.display = 'none';
    var symbols = data.symbols;
    var matrix = data.matrix;

    var strongest = null;
    for (var i = 0; i < symbols.length; i++) {
        for (var j = i + 1; j < symbols.length; j++) {
            var corr = matrix[i][j];
            if (corr == null) continue;
            if (!strongest || Math.abs(corr) > Math.abs(strongest.value)) {
                strongest = { left: symbols[i], right: symbols[j], value: corr };
            }
        }
    }

    var wrap = document.getElementById('correlationReadable');
    if (!wrap) {
        wrap = document.createElement('div');
        wrap.id = 'correlationReadable';
        canvas.parentElement.appendChild(wrap);
    }
    var summary = strongest
        ? esc(strongest.left) + ' and ' + esc(strongest.right) + ' move most alike/opposite: ' + formatNum(strongest.value, 2) + ' (' + correlationLabel(strongest.value) + ').'
        : 'No reliable pairwise correlation yet.';
    var html = '<div class=' + Q + 'corr-help' + Q + '><strong>How to read:</strong> +1 moves together, 0 means little relationship, -1 moves opposite. ' + summary + '</div>';
    html += '<div class=' + Q + 'corr-legend' + Q + '><span><i class=' + Q + 'corr-dot red' + Q + '></i>Opposite</span><span><i class=' + Q + 'corr-dot gray' + Q + '></i>Weak</span><span><i class=' + Q + 'corr-dot green' + Q + '></i>Similar</span></div>';
    html += '<div class=' + Q + 'corr-table-wrap' + Q + '><table class=' + Q + 'corr-table' + Q + '><thead><tr><th></th>';
    symbols.forEach(function(sym) { html += '<th>' + esc(sym) + '</th>'; });
    html += '</tr></thead><tbody>';
    symbols.forEach(function(rowSym, rowIdx) {
        html += '<tr><th>' + esc(rowSym) + '</th>';
        symbols.forEach(function(colSym, colIdx) {
            var value = matrix[rowIdx][colIdx];
            var display = value == null ? '-' : formatNum(value, 2);
            html += '<td title=' + Q + esc(rowSym + ' vs ' + colSym + ': ' + correlationLabel(value)) + Q + ' style=' + Q + 'background:' + correlationColor(value) + Q + '><span>' + display + '</span></td>';
        });
        html += '</tr>';
    });
    html += '</tbody></table></div>';
    wrap.className = 'correlation-readable';
    wrap.innerHTML = html;
}

// SECTOR ALLOCATION
async function loadSectorAllocation() {
    var canvas = document.getElementById('sectorChart');
    if (!canvas) return;
    // Destroy existing chart to prevent memory leaks
    var existingKey = 'chart_' + canvas.id;
    if (state.charts[existingKey]) { state.charts[existingKey].destroy(); }
    var data = await apiFetch('/api/quant/sectors');
    var allocation = Array.isArray(data) ? data : (data ? data.allocation : null);
    if (!allocation || allocation.length === 0) {
        if (state.charts.sector) { state.charts.sector.destroy(); state.charts.sector = null; }
        var emptyEl = canvas.parentElement.querySelector('.empty-state');
        if (!emptyEl) {
            var p = document.createElement('p');
            p.className = 'empty-state';
            p.textContent = 'No holdings with sector data.';
            canvas.parentElement.appendChild(p);
        }
        return;
    }
    if (state.charts.sector) state.charts.sector.destroy();
    var colors = ['#3b82f6', '#ef4444', '#22c55e', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#f97316', '#14b8a6', '#a855f7', '#6366f1', '#e11d48', '#0ea5e9'];
    state.charts.sector = new Chart(canvas, {
        type: 'doughnut',
        data: { labels: allocation.map(function(a) { return a.sector; }), datasets: [{ data: allocation.map(function(a) { return a.value; }), backgroundColor: colors.slice(0, allocation.length), borderWidth: 2, borderColor: getComputedStyle(document.body).backgroundColor }] },
        options: { responsive: true, plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } }, tooltip: { callbacks: { label: function(ctx) { var a = allocation[ctx.dataIndex]; return ctx.label + ': ' + formatCurrency(ctx.parsed) + ' (' + (a.percentage || a.allocation || 0) + '%)'; } } } } },
    });
}



// RISK METRICS
async function loadRiskMetrics() {
    var data = await apiFetch('/api/quant/risk/portfolio');
    if (!data || !data.portfolio_risk) return;
    var pr = data.portfolio_risk;
    document.getElementById('riskSharpe').textContent = pr.weighted_sharpe != null ? pr.weighted_sharpe.toFixed(2) : '-';
    document.getElementById('riskSortino').textContent = pr.weighted_sortino != null ? pr.weighted_sortino.toFixed(2) : '-';
    document.getElementById('riskDrawdown').textContent = pr.weighted_max_drawdown != null ? pr.weighted_max_drawdown.toFixed(1) + '%' : '-';
    document.getElementById('riskVol').textContent = pr.weighted_annualized_volatility != null ? pr.weighted_annualized_volatility.toFixed(1) + '%' : '-';
    document.getElementById('riskVaR').textContent = '-';
    var tbody = document.getElementById('riskTableBody');
    if (!tbody || !data.holdings || data.holdings.length === 0) return;
    tbody.innerHTML = data.holdings.map(function(h) {
        return '<tr>'
            + '<td><strong>' + h.symbol + '</strong></td>'
            + '<td class=' + Q + pnlClass(h.sharpe) + Q + '>' + (h.sharpe != null ? h.sharpe.toFixed(2) : '-') + '</td>'
            + '<td class=' + Q + pnlClass(h.sortino) + Q + '>' + (h.sortino != null ? h.sortino.toFixed(2) : '-') + '</td>'
            + '<td class=' + Q + 'negative' + Q + '>' + (h.max_drawdown != null ? h.max_drawdown.toFixed(1) + '%' : '-') + '</td>'
            + '<td>' + (h.annualized_volatility != null ? h.annualized_volatility.toFixed(1) + '%' : '-') + '</td>'
            + '<td class=' + Q + 'negative' + Q + '>' + (h.daily_var_95 != null ? (h.daily_var_95 * 100).toFixed(2) + '%' : '-') + '</td>'
            + '<td>' + formatCurrency(h.value) + '</td>'
            + '</tr>';
    }).join('');
}

// BETA
async function loadBetaData() {
    var data = await apiFetch('/api/quant/beta');
    if (!data || !data.betas || data.betas.length === 0) return;
    var tbody = document.getElementById('betaTableBody');
    if (tbody) {
        tbody.innerHTML = data.betas.map(function(b) {
            var cls = b.beta < 0.8 ? 'defensive' : (b.beta > 1.2 ? 'aggressive' : 'moderate');
            var label = b.classification || cls;
            return '<tr>'
                + '<td><strong>' + b.symbol + '</strong></td>'
                + '<td class=' + Q + pnlClass(b.beta - 1) + Q + '>' + b.beta.toFixed(2) + '</td>'
                + '<td><span class=' + Q + 'beta-badge ' + cls + Q + '>' + label + '</span></td>'
                + '<td>' + (b.allocation != null ? b.allocation.toFixed(1) + '%' : '-') + '</td>'
                + '</tr>';
        }).join('');
    }
    var canvas = document.getElementById('betaChart');
    if (!canvas) return;
    if (state.charts.beta) state.charts.beta.destroy();
    state.charts.beta = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: data.betas.map(function(b) { return b.symbol; }),
            datasets: [{
                label: 'Beta',
                data: data.betas.map(function(b) { return b.beta; }),
                backgroundColor: data.betas.map(function(b) {
                    if (b.beta < 0.8) return '#3b82f6';
                    if (b.beta > 1.2) return '#ef4444';
                    return '#f59e0b';
                }),
                borderWidth: 0,
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: function(ctx) { return 'Beta: ' + ctx.parsed.y.toFixed(2); } } }
            },
            scales: {
                y: { min: 0, max: 2, ticks: { callback: function(v) { return v.toFixed(1); } } },
                x: { ticks: { font: { size: 10 } } }
            }
        }
    });
}

// VOLATILITY
async function loadVolatilityScanner() {
    var data = await apiFetch('/api/quant/volatility');
    if (!data || !data.volatility || data.volatility.length === 0) return;
    var tbody = document.getElementById('volTableBody');
    if (!tbody) return;
    tbody.innerHTML = data.volatility.map(function(v) {
        var cls = v.volatility_bracket === 'Low' ? 'low' : (v.volatility_bracket === 'High' ? 'high' : 'moderate');
        return '<tr>'
            + '<td><strong>' + v.symbol + '</strong></td>'
            + '<td>' + (v.annualized_volatility != null ? v.annualized_volatility.toFixed(1) + '%' : '-') + '</td>'
            + '<td>' + (v.atr_14 != null ? v.atr_14.toFixed(2) : '-') + '</td>'
            + '<td><span class=' + Q + 'vol-badge ' + cls + Q + '>' + v.volatility_bracket + '</span></td>'
            + '</tr>';
    }).join('');
}

// DIVIDENDS
async function loadDividendTracker() {
    var data = await apiFetch('/api/quant/dividends');
    if (!data || !data.dividends) return;
    var paying = data.dividends.filter(function(d) { return d.dividend_yield != null; });
    document.getElementById('divForecast').textContent = formatCurrency(data.total_annual_forecast);
    document.getElementById('divPayingCount').textContent = paying.length + ' / ' + data.dividends.length;
    var avgYield = paying.length > 0 ? paying.reduce(function(s, d) { return s + (d.dividend_yield || 0); }, 0) / paying.length : 0;
    document.getElementById('divAvgYield').textContent = avgYield.toFixed(2) + '%';
    var tbody = document.getElementById('divTableBody');
    if (!tbody) return;
    tbody.innerHTML = data.dividends.map(function(d) {
        return '<tr>'
            + '<td><strong>' + d.symbol + '</strong></td>'
            + '<td>' + (d.dividend_yield != null ? d.dividend_yield.toFixed(2) + '%' : '-') + '</td>'
            + '<td>' + (d.annual_div_per_share != null ? formatCurrency(d.annual_div_per_share) : '-') + '</td>'
            + '<td>' + d.quantity + '</td>'
            + '<td class=' + Q + pnlClass(d.annual_income) + Q + '>' + (d.annual_income > 0 ? formatCurrency(d.annual_income) : '-') + '</td>'
            + '</tr>';
    }).join('');
}

// EXPORT CSV
async function exportPortfolioCSV() {
    try {
        var resp = await fetch(API_BASE + '/api/export/portfolio-csv');
        if (!resp.ok) { showNotification('Export failed', 'error'); return; }
        var blob = await resp.blob();
        var url = window.URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'portfolio_export.csv';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        showNotification('Portfolio exported!', 'success');
    } catch (e) { showNotification('Export failed: ' + e.message, 'error'); }
}
