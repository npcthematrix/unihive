/* UniHive Console — 前端逻辑
 *
 * 与 console.html 配套使用; 通过 <script src="/static/console/console.js" defer>
 * 加载。CSP 已移除 'unsafe-inline', 所以所有事件必须用 addEventListener /
 * event delegation, 不能用 inline onclick="...".
 *
 * 修改历史:
 *   - 2026-09-08 拆自 console.html 1912 行单文件结构, 改为外部 JS.
 *   - 同步: fetchJSON 错误带上响应体并弹 toast; showToast 增加 variant.
 *   - 同步: 移除 emoji 字面量 (CSP + 设计语言统一); 改用 text/SVG.
 *   - 同步: 把模板字符串里的 inline onclick 改为 event delegation.
 */
(function () {
    'use strict';

    /* ===== Constants ===== */
    const STALE_THRESHOLD_SEC = 30;
    const CFG_TABLE_COLSPANS = { ups: 5, upstreams: 7, mapping: 2, tools: 5, cache: 3 };
    const STATUS_TEXT = { online: '在线', degraded: '降级', offline: '离线', configured: '就绪', disabled: '已禁用', unknown: '未知' };
    const STATUS_BUCKET = { online: 'good', degraded: 'warn', offline: 'bad', configured: 'idle', disabled: 'idle', unknown: 'bad' };
    const STATUS_ORDER = { online: 0, degraded: 1, configured: 2, offline: 3, disabled: 4, unknown: 5 };
    const TYPE_TEXT = { http: 'HTTP', stdio: 'Stdio', npx: 'NPX' };
    const GROUP_ORDER = ['tdx_quant', 'mootdx2', 'fuyao_ashare', 'fuyao_index', 'fuyao_meta', 'fuyao_fund'];
    const GROUP_META = {
        'tdx_quant':          { title: '通达信-量化终端', desc: '进程内直调 tqcenter.py：行情/板块/交易日/财务/公式/交易/预警 (54 工具)' },
        'mootdx2':            { title: 'MooTDX2', desc: '基于 mootdx2 的行情/K线/财务/板块/日历' },
        'fuyao_ashare':       { title: '同花顺-a-share', desc: 'A 股行情/财报/估值/特殊数据, 端点 /mcp/a-share' },
        'fuyao_index':        { title: '同花顺-a-share-index', desc: '指数/板块目录/成分股/历史K线, 端点 /mcp/a-share-index' },
        'fuyao_meta':         { title: '同花顺-meta', desc: '跨市场 ticker 搜索/列表, 端点 /mcp/meta' },
        'fuyao_fund':         { title: '同花顺-fund', desc: '公募基金持仓/业绩/经理/财务/历史, 端点 /mcp/fund' },
    };
    const UPSTREAM_DISPLAY_NAME = {
        'tdx_quant':          '通达信-量化终端',
        'mootdx2':           'MooTDX2',
        'fuyao_ashare':       '同花顺-a-share',
        'fuyao_index':        '同花顺-a-share-index',
        'fuyao_meta':         '同花顺-meta',
        'fuyao_fund':         '同花顺-fund',
    };
    const ALL_KEY = '__all__';

    /* ===== Module state ===== */
    let statusUpstreams = [];
    let statusFilter = 'all';
    let statusSort = { key: null, dir: 'asc' };
    let lastStatusFetch = 0;
    let autoRefreshTimer = null;
    let updateClockTimer = null;
    let allTools = [];
    let activeGroup = ALL_KEY;
    let searchQuery = '';
    let dynamicGroupOrder = [];
    let toggles = { dangerous: false, cache: false, noparam: false };
    let dataSourceFilter = '';
    let upstreamDotStatus = {};
    let _cfgUpstreamsSearch = '';
    let _cfgCache = null;
    let searchDebounceTimer = null;
    let omniStatsLoaded = false;
    let _expandedTools = new Set();
    let activePanel = 'interfaces';
    let _omniSyncInFlight = false;
    let _onboardingObserver = null;

    /* ===== Helpers ===== */
    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function showToast(msg, variant) {
        let t = document.getElementById('toast');
        if (!t) {
            t = document.createElement('div');
            t.id = 'toast';
            t.className = 'toast';
            document.body.appendChild(t);
        }
        t.textContent = msg;
        t.classList.remove('toast-error', 'toast-success');
        if (variant === 'error') t.classList.add('toast-error');
        else if (variant === 'success') t.classList.add('toast-success');
        t.classList.add('show');
        clearTimeout(t._hideTimer);
        t._hideTimer = setTimeout(() => t.classList.remove('show'), variant === 'error' ? 2800 : 1400);
    }

    async function copyText(text, btn) {
        try {
            await navigator.clipboard.writeText(text);
            if (btn) {
                const orig = btn.textContent;
                btn.textContent = '已复制';
                btn.classList.add('copied');
                setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 1200);
            }
        } catch {
            showToast('复制失败', 'error');
        }
    }

    /* ===== fetchJSON — 含响应体的错误 + 错误 toast =====
     *
     * 旧版只 throw new Error(resp.statusText), 完全丢失后端返回的 JSON 体
     * (比如 {"detail": "..."} 或 {"error": "..."}), 排查问题时只能看 network.
     * 现在解析响应体 (无论 ok 与否), 把后端 detail/error 拼进 Error.message,
     * 同时弹一个 toast 提示用户. silent=true 时只 console.error 不弹 toast
     * (用于后台轮询/refreshAll 这种允许个别失败的整体刷新场景).
     */
    async function fetchJSON(url, opts) {
        const silent = !!(opts && opts.silent);
        const fetchOpts = { method: (opts && opts.method) || 'GET' };
        if (opts && opts.headers) fetchOpts.headers = opts.headers;
        if (opts && opts.body !== undefined) fetchOpts.body = opts.body;
        const resp = await fetch(url, fetchOpts);
        if (!resp.ok) {
            let detail = '';
            try {
                const body = await resp.json();
                detail = body.detail || body.error || body.message || JSON.stringify(body);
            } catch {
                try { detail = await resp.text(); } catch { /* ignore */ }
            }
            const msg = `${resp.status} ${resp.statusText}${detail ? ` — ${detail}` : ''}`;
            if (!silent) showToast(msg, 'error');
            const err = new Error(msg);
            err.status = resp.status;
            throw err;
        }
        return resp.json();
    }

    /* ===== Skeleton helpers (item 4: 不再显示裸 "加载中..." 文字) =====
     *
     * 调用方: loadInterfaces() 开始时 renderInterfacesSkeleton();
     *          loadConfig() 内部每个 cfg-panel 渲染时也可单独 skeleton.
     * 数据返回后, render*() 直接 innerHTML 覆盖, 自动替换.
     */
    function skeletonLines(count, widths) {
        widths = widths || [60, 80, 40];
        const out = [];
        for (let i = 0; i < count; i++) {
            const w = widths[i % widths.length];
            out.push(`<div class="skeleton skeleton-line w-${w}"></div>`);
        }
        return out.join('');
    }
    function renderInterfacesSkeleton() {
        const root = document.getElementById('ifs-content');
        if (!root) return;
        const rows = [];
        for (let i = 0; i < 6; i++) {
            rows.push(`<div class="skeleton-card"><div class="skeleton-row"><div class="skeleton skeleton-line w-60"></div><div class="skeleton skeleton-line w-80"></div><div class="skeleton skeleton-line w-40"></div></div></div>`);
        }
        root.innerHTML = `<div class="ifs-content-header"><h3>加载中</h3><div class="ifs-content-meta">正在从网关拉取 tool 列表…</div></div>${rows.join('')}`;
        const metaEl = document.getElementById('ifs-total-meta');
        if (metaEl) metaEl.textContent = '加载中…';
    }
    function renderStatusSkeleton() {
        const tbody = document.getElementById('upstreams-body');
        if (!tbody) return;
        const rows = [];
        for (let i = 0; i < 5; i++) {
            rows.push(`<tr><td colspan="5"><div class="skeleton skeleton-line w-60"></div></td></tr>`);
        }
        tbody.innerHTML = rows.join('');
    }
    function renderConfigSkeleton(panelId) {
        const root = document.getElementById(panelId);
        if (!root) return;
        root.innerHTML = `<div class="skeleton-card">${skeletonLines(8)}</div>`;
    }

    /* ===== Status panel ===== */
    function isHealthy(u) { return u.status === 'online' || u.status === 'configured'; }
    function isUnhealthy(u) { return !isHealthy(u); }

    function applyStatusFilter(list) {
        if (statusFilter === 'healthy') return list.filter(isHealthy);
        if (statusFilter === 'unhealthy') return list.filter(isUnhealthy);
        return list;
    }

    function applyStatusSort(list) {
        if (!statusSort.key) return list;
        const k = statusSort.key, dir = statusSort.dir === 'asc' ? 1 : -1;
        return [...list].sort((a, b) => {
            let va, vb;
            if (k === 'name') { va = a._name; vb = b._name; }
            else if (k === 'status') { va = STATUS_ORDER[a.status] ?? 99; vb = STATUS_ORDER[b.status] ?? 99; }
            else if (k === 'latency') { va = a.latency_ms == null ? -1 : a.latency_ms; vb = b.latency_ms == null ? -1 : b.latency_ms; }
            if (va < vb) return -1 * dir;
            if (va > vb) return  1 * dir;
            return 0;
        });
    }

    function renderUpstreamsTable() {
        const tbody = document.getElementById('upstreams-body');
        const filtered = applyStatusFilter(statusUpstreams);
        const sorted = applyStatusSort(filtered);
        document.querySelectorAll('.ups-table th.sortable').forEach(th => {
            th.classList.remove('sort-asc', 'sort-desc');
            if (th.dataset.sort === statusSort.key) th.classList.add(statusSort.dir === 'asc' ? 'sort-asc' : 'sort-desc');
        });
        if (statusUpstreams.length === 0) {
            tbody.innerHTML = `<tr><td colspan="${CFG_TABLE_COLSPANS.ups}"><div class="empty-state"><div class="empty-state-icon"><span class="status-icon status-icon-empty">○</span></div><div class="empty-state-title">暂无上游配置</div><div class="empty-state-hint">编辑 <code>config/upstreams.yaml</code> 后重启网关</div></div></td></tr>`;
            return;
        }
        if (sorted.length === 0) {
            const msg = statusFilter === 'healthy' ? '当前没有正常的上游' : statusFilter === 'unhealthy' ? '当前没有异常的上游' : '没有匹配的上游';
            tbody.innerHTML = `<tr><td colspan="5"><div class="empty-state"><div class="empty-state-icon"><span class="status-icon status-icon-empty">○</span></div><div class="empty-state-title">${msg}</div><div class="empty-state-hint">点击上方"全部"查看所有上游</div></div></td></tr>`;
            return;
        }
        tbody.innerHTML = sorted.map(u => {
            const st = STATUS_TEXT[u.status] || '未知';
            const bcls = `b-${u.status || 'unknown'}`;
            const tp = TYPE_TEXT[u.type] || u.type || '-';
            let latHtml;
            if (u.latency_ms == null) {
                latHtml = '<span class="latency-cell dim" title="未探测 - 上游未在线或未发起请求">未探测</span>';
            } else if (u.latency_ms === 0) {
                latHtml = '<span class="latency-cell dim" title="可能为缓存命中或本地解析">0<span style="opacity:.6">ms</span></span>';
            } else {
                let cls = 'good';
                if (u.latency_ms > 1000) cls = 'slow';
                if (u.latency_ms > 3000) cls = 'bad';
                latHtml = `<span class="latency-cell ${cls}">${u.latency_ms}<span style="opacity:.6">ms</span></span>`;
            }
            const errHtml = u.last_error ? `<button type="button" class="err-icon" data-error="${escapeHtml(u.last_error)}" title="${escapeHtml(u.last_error)} (点击复制)" aria-label="复制错误信息">!</button>` : '<span style="color:var(--text-secondary);opacity:.3">—</span>';
            return `<tr>
                <td><span class="ups-name">${UPSTREAM_DISPLAY_NAME[u._name] || escapeHtml(u._name)}</span></td>
                <td><span class="type-pill">${escapeHtml(tp)}</span></td>
                <td><span class="status-badge ${bcls}"><span class="status-dot ${u.status === 'online' ? 'status-online' : u.status === 'degraded' ? 'status-degraded' : u.status === 'offline' ? 'status-offline' : u.status === 'disabled' ? 'status-disabled' : 'status-offline'}"></span>${st}</span></td>
                <td class="num">${latHtml}</td>
                <td>${errHtml}</td>
            </tr>`;
        }).join('');
    }

    function renderCache(cache) {
        const enabledEl = document.getElementById('cache-enabled');
        const liveEl = document.getElementById('cache-live');
        const entriesEl = document.getElementById('cache-entries');
        const sizeEl = document.getElementById('cache-size');
        const hitrateEl = document.getElementById('cache-hitrate');
        const hitrateBar = document.getElementById('cache-hitrate-bar');
        const hitsEl = document.getElementById('cache-hits');
        const missesEl = document.getElementById('cache-misses');
        const hintEl = document.getElementById('cache-hitrate-hint');
        document.getElementById('cache-timestamp').textContent = cache && cache.timestamp ? formatTime(cache.timestamp) : '-';
        if (!cache || !cache.enabled) {
            enabledEl.className = 'mini-value off';
            enabledEl.querySelector('.cache-enabled-text').textContent = '—';
            liveEl.textContent = '-';
            entriesEl.textContent = '-';
            sizeEl.textContent = '-';
            hitrateEl.textContent = '-';
            hitrateBar.innerHTML = '';
            hitsEl.textContent = '0';
            missesEl.textContent = '0';
            hintEl.textContent = cache && cache.error ? cache.error : '缓存数据库未创建';
            return;
        }
        enabledEl.className = 'mini-value on';
        enabledEl.querySelector('.cache-enabled-text').textContent = '—';
        liveEl.textContent = cache.live_entries != null ? cache.live_entries : '-';
        entriesEl.textContent = cache.entries != null ? cache.entries : '-';
        sizeEl.textContent = (cache.db_size_mb || 0).toFixed(2) + ' MB';
        const hits = cache.hits || 0;
        const misses = cache.misses || 0;
        const total = hits + misses;
        hitsEl.textContent = hits;
        missesEl.textContent = misses;
        if (total > 0 && cache.hit_rate != null) {
            const pct = cache.hit_rate * 100;
            hitrateEl.textContent = pct.toFixed(1) + '%';
            const tier = pct > 70 ? 'high' : pct > 40 ? 'mid' : 'low';
            hitrateBar.innerHTML = `<div class="cache-hitrate-fill cache-hitrate-fill-${tier}" style="width:${pct}%"></div>`;
            hintEl.textContent = `共 ${total} 次请求`;
        } else {
            hitrateEl.textContent = '—';
            hitrateBar.innerHTML = '';
            hintEl.textContent = '网关启动后累计 (尚未请求)';
        }
    }

    function renderChipCounts() {
        const all = statusUpstreams.length;
        const healthy = statusUpstreams.filter(isHealthy).length;
        const unhealthy = statusUpstreams.filter(isUnhealthy).length;
        document.getElementById('chip-count-all').textContent = all;
        document.getElementById('chip-count-healthy').textContent = healthy;
        document.getElementById('chip-count-unhealthy').textContent = unhealthy;
    }

    function updateLastRefresh() {
        const el = document.getElementById('last-refresh');
        if (!lastStatusFetch) { el.textContent = '从未刷新'; return; }
        const sec = Math.max(0, Math.floor((Date.now() / 1000) - lastStatusFetch));
        if (sec < 5) el.textContent = '刚刚刷新';
        else if (sec < 60) el.textContent = `${sec} 秒前刷新`;
        else if (sec < 3600) el.textContent = `${Math.floor(sec / 60)} 分钟前刷新`;
        else el.textContent = `${Math.floor(sec / 3600)} 小时前刷新`;
    }

    function startUpdateClock() {
        if (updateClockTimer) return;
        updateClockTimer = setInterval(updateLastRefresh, 1000);
    }
    function stopUpdateClock() {
        if (updateClockTimer) { clearInterval(updateClockTimer); updateClockTimer = null; }
    }

    async function loadStatus(showLoading) {
        const refreshEl = document.getElementById('last-refresh');
        if (showLoading && refreshEl) refreshEl.classList.add('refreshing');
        if (showLoading) renderStatusSkeleton();
        try {
            const data = await fetchJSON('/api/status');
            statusUpstreams = Object.entries(data.upstreams || {}).map(([name, info]) => ({ _name: name, ...info }));
            Object.entries(data.upstreams || {}).forEach(([n, info]) => { upstreamDotStatus[n] = info.status; });
            renderStatusSummary(data.summary);
            renderSidebar();
            renderChipCounts();
            renderUpstreamsTable();
            renderCache(data.cache);
            lastStatusFetch = Math.floor(Date.now() / 1000);
            updateLastRefresh();
        } catch (e) {
            console.error('Load status error:', e);
            if (refreshEl) { refreshEl.textContent = '刷新失败'; refreshEl.classList.remove('refreshing'); }
        } finally {
            if (refreshEl) refreshEl.classList.remove('refreshing');
        }
    }

    function renderStatusSummary(s) {
        if (!s) return;
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v ?? '—'; };
        set('sum-total', s.total);
        set('sum-online', s.online);
        set('sum-degraded', s.degraded);
        set('sum-offline', s.offline);
        set('sum-configured', s.configured);
        set('sum-disabled', s.disabled);
    }

    function setStatusFilter(f) {
        statusFilter = f;
        document.querySelectorAll('.filter-chips .chip').forEach(c => c.classList.toggle('active', c.dataset.filter === f));
        renderUpstreamsTable();
    }

    function setStatusSort(key) {
        if (statusSort.key === key) statusSort.dir = statusSort.dir === 'asc' ? 'desc' : 'asc';
        else { statusSort.key = key; statusSort.dir = 'asc'; }
        renderUpstreamsTable();
    }

    function startAutoRefresh() {
        stopAutoRefresh();
        autoRefreshTimer = setInterval(() => loadStatus(false), 10000);
    }
    function stopAutoRefresh() {
        if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
    }

    function formatTime(ts) {
        if (!ts) return '-';
        const d = new Date(ts * 1000);
        return d.toLocaleTimeString('zh-CN');
    }

    /* ===== Tab activation ===== */
    function activatePanel(panelId) {
        document.querySelectorAll('.tab').forEach(t => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        const tab = document.querySelector(`.tab[data-panel="${panelId}"]`);
        if (!tab) return;
        tab.classList.add('active');
        tab.setAttribute('aria-selected', 'true');
        document.getElementById(panelId).classList.add('active');
        // 离开 onboarding 时 disconnect observer
        if (activePanel === 'onboarding' && _onboardingObserver) {
            _onboardingObserver.disconnect();
            _onboardingObserver = null;
        }
        activePanel = panelId;
        if (panelId === 'status') {
            const age = Math.floor(Date.now() / 1000) - (lastStatusFetch || 0);
            if (age > STALE_THRESHOLD_SEC) loadStatus(false);
            startUpdateClock();
        } else {
            stopUpdateClock();
        }
        if (panelId === 'onboarding') {
            const list = document.getElementById('interfaces-summary');
            if (list && (list.children.length === 0 || list.querySelector('.loading-state'))) {
                loadOnboarding();
            }
            // 初始化 IntersectionObserver
            initOnboardingObserver();
        }
        if (panelId === 'omni' && !omniStatsLoaded) {
            omniStatsLoaded = true;
            omniLoadStats();
        }
    }

    /* ===== Interfaces panel ===== */
    function getGroupOrder() {
        return dynamicGroupOrder.length > 0 ? dynamicGroupOrder : GROUP_ORDER;
    }

    function hasActiveFilters() {
        return searchQuery || Object.values(toggles).some(Boolean) || dataSourceFilter;
    }

    function primarySource(src) {
        if (!src) return '';
        if (getGroupOrder().includes(src)) return src;
        return src;
    }

    function highlight(text, q) {
        if (!q || !text) return escapeHtml(text || '');
        const safe = escapeHtml(text);
        const pattern = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return safe.replace(new RegExp(pattern, 'gi'), m => `<mark class="ifs-hl">${m}</mark>`);
    }

    function bucketByGroup(tools) {
        const buckets = new Map();
        for (const t of tools) {
            const key = primarySource(t.source);
            if (!buckets.has(key)) buckets.set(key, []);
            buckets.get(key).push(t);
        }
        return buckets;
    }

    function applyFilter(tools, q) {
        let out = tools;
        if (q) {
            out = out.filter(t =>
                (t.name || '').toLowerCase().includes(q) ||
                (t.description || '').toLowerCase().includes(q) ||
                (t.source || '').toLowerCase().includes(q)
            );
        }
        if (toggles.dangerous) out = out.filter(t => t.dangerous);
        if (toggles.cache) out = out.filter(t => t.cache_ttl_key && t.cache_ttl_key !== 'realtime_quote');
        if (toggles.noparam) out = out.filter(t => !t.params || t.params.length === 0 || (t.params.length === 1 && t.params[0] === '?'));
        if (dataSourceFilter) out = out.filter(t => t.data_source_type === dataSourceFilter);
        return out;
    }

    function filteredAll() {
        return applyFilter(allTools, searchQuery);
    }

    function renderSidebar() {
        const root = document.getElementById('ifs-nav');
        if (!root) return;
        const filtered = filteredAll();
        const buckets = bucketByGroup(filtered);
        const allBuckets = bucketByGroup(allTools);
        const allCount = filtered.length;
        const groups = [ALL_KEY, ...getGroupOrder()];
        root.innerHTML = groups.map(key => {
            const total = key === ALL_KEY ? allTools.length : (allBuckets.get(key) || []).length;
            const matched = key === ALL_KEY ? allCount : (buckets.get(key) || []).length;
            const meta = key === ALL_KEY ? { title: '全部' } : (GROUP_META[key] || { title: key });
            let countCls = '';
            if (searchQuery || hasActiveFilters()) {
                if (matched === 0) countCls = 'zero';
                else if (matched < total) countCls = 'has-match';
            }
            const countText = (searchQuery || hasActiveFilters()) && total ? `${matched}/${total}` : `${total}`;
            const isActive = key === activeGroup;
            const isDisabled = (searchQuery || hasActiveFilters()) && matched === 0;
            let dotCls = 'dot';
            if (key !== ALL_KEY) {
                const st = upstreamDotStatus[key];
                if (st === 'online') dotCls += ' dot-online';
                else if (st === 'degraded') dotCls += ' dot-degraded';
                else if (st === 'offline') dotCls += ' dot-offline';
                else if (st === 'configured' || st === 'disabled') dotCls += ' dot-configured';
            }
            return `<button type="button" class="ifs-nav-item${isActive ? ' active' : ''}" data-group="${escapeHtml(key)}"${isActive ? ' aria-current="true"' : ''}${isDisabled ? ' disabled' : ''}><span class="${dotCls}"></span><span class="label" title="${escapeHtml(meta.title)}">${escapeHtml(meta.title)}</span><span class="count ${countCls}">${countText}</span></button>`;
        }).join('');
    }

    function renderToggleCounts() {
        document.getElementById('tgl-count-dangerous').textContent = allTools.filter(t => t.dangerous).length;
        document.getElementById('tgl-count-cache').textContent = allTools.filter(t => t.cache_ttl_key && t.cache_ttl_key !== 'realtime_quote').length;
        document.getElementById('tgl-count-noparam').textContent = allTools.filter(t => !t.params || t.params.length === 0 || (t.params.length === 1 && t.params[0] === '?')).length;
        document.getElementById('chip-dst-all').textContent = allTools.length;
        document.getElementById('chip-dst-online').textContent = allTools.filter(t => t.data_source_type === 'online').length;
        document.getElementById('chip-dst-offline').textContent = allTools.filter(t => t.data_source_type === 'offline').length;
        document.getElementById('chip-dst-mixed').textContent = allTools.filter(t => t.data_source_type === 'mixed').length;
    }

    function renderEmpty() {
        const root = document.getElementById('ifs-content');
        // 用 text label 替代原来的 emoji (CSP + 设计语言统一)
        root.innerHTML = `<div class="ifs-empty"><div class="ifs-empty-icon" aria-hidden="true">无结果</div><div class="ifs-empty-text">此分组无匹配工具</div><button type="button" class="ifs-empty-action" data-action="clear-search">清空搜索</button></div>`;
    }

    function renderContent() {
        const root = document.getElementById('ifs-content');
        if (!root) return;
        const filtered = filteredAll();
        const buckets = bucketByGroup(filtered);
        const tools = activeGroup === ALL_KEY ? filtered : (buckets.get(activeGroup) || []);
        if ((searchQuery || hasActiveFilters()) && tools.length === 0) {
            renderEmpty();
            return;
        }
        const meta = activeGroup === ALL_KEY ? { title: '全部', key: '全部' } : { title: (GROUP_META[activeGroup] || { title: activeGroup }).title, key: activeGroup };
        const metaEl = document.getElementById('ifs-total-meta');
        if (metaEl) metaEl.textContent = `${meta.title} · ${tools.length} 个工具`;
        window._currentFilteredToolNames = tools.map(t => t.name);
        const headerHtml = activeGroup === ALL_KEY
            ? '<th style="width:28%">接口</th><th>描述</th><th style="width:90px">操作</th>'
            : `<th style="width:28%">接口</th><th>描述</th><th style="width:14%" class="col-source">数据源</th><th style="width:90px">操作</th>`;
        const rows = tools.map((t, i) => {
            const badges = [];
            if (t.dangerous) badges.push('<span class="badge badge-dangerous">dangerous</span>');
            if (t.data_source_type) {
                const dst = t.data_source_type;
                const cls = dst === 'online' ? 'badge-online' : dst === 'offline' ? 'badge-offline' : 'badge-mixed';
                badges.push(`<span class="badge ${cls}" title="数据源类型">${dst}</span>`);
            }
            if (t.cache_ttl_key) badges.push(`<span class="badge badge-cache" title="cache_ttl_key=${escapeHtml(t.cache_ttl_key)}">${escapeHtml(t.cache_ttl_key)}</span>`);
            const params = (t.params || []).filter(p => p && p !== '?');
            const paramNames = params.map(p => typeof p === 'string' ? p : (p.name || '')).filter(Boolean);
            const moreCount = paramNames.length > 3 ? paramNames.length - 3 : 0;
            const paramsDisplay = moreCount > 0 ? highlight(paramNames.slice(0, 3).join(', '), searchQuery) + ` <span class="params-more">+${moreCount} more</span>` : (paramNames.length > 0 ? highlight(paramNames.join(', '), searchQuery) : '');
            const sourceCell = activeGroup === ALL_KEY ? '' : `<td><button type="button" class="chain chain-link" data-source="${escapeHtml(t.source || '')}" title="跳转到该上游分组">${escapeHtml(t.source || '-')} →</button></td>`;
            const detailBody = buildDetailBody(t);
            return `<tr data-tool="${escapeHtml(t.name)}"><td><span class="tool-name">${highlight(t.name, searchQuery)}</span>${badges.length ? '<span class="tool-meta-cell">' + badges.join('') + '</span>' : ''}</td><td><div class="tool-desc">${highlight(t.description || '-', searchQuery)}</div>${paramsDisplay ? `<div class="tool-params">${paramsDisplay}</div>` : ''}</td>${sourceCell}<td><div class="row-actions"><button class="row-action-btn copy-name" data-name="${escapeHtml(t.name)}">复制</button><button class="row-action-btn toggle-detail" data-idx="${i}" data-tool="${escapeHtml(t.name)}" ${!detailBody ? 'disabled' : ''}>详情</button></div></td></tr>${detailBody ? `<tr class="ifs-detail-row${_expandedTools.has(t.name) ? '' : ' ifs-detail-row-hidden'}" data-detail-for="${escapeHtml(t.name)}"><td colspan="${activeGroup === ALL_KEY ? 3 : 4}">${detailBody}</td></tr>` : ''}`;
        }).join('');
        root.innerHTML = `<div class="ifs-content-header"><h3>${escapeHtml(meta.title)}</h3><div class="ifs-content-meta">${tools.length} 个工具</div></div><div class="ifs-table-wrap"><table class="ifs-table"><caption class="visually-hidden">${escapeHtml(meta.title)} MCP APIs</caption><thead><tr>${headerHtml}</tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function buildDetailBody(t) {
        const parts = [];
        // 高风险提示: 用纯文字 + CSS 类替代 emoji
        if (t.dangerous) parts.push(`<div class="ifs-danger-banner"><span class="ifs-danger-icon" aria-hidden="true">!</span>高风险工具 (${escapeHtml(t.source || '未指定上游')}): 可透传任意路径, 需谨慎授权</div>`);

        if (t.cache_ttl_key) {
            const isRealtime = t.cache_ttl_key === 'realtime_quote';
            const ttlVal = isRealtime ? '<span style="color:var(--text-secondary)">实时（不缓存）</span>' : (t.cache_ttl_seconds != null ? `${t.cache_ttl_seconds}秒` : 'no TTL');
            parts.push(`<div class="ifs-section-title">缓存</div>`);
            parts.push(`<div class="ifs-detail-grid"><div class="ifs-detail-key">TTL Key</div><div class="ifs-detail-val">${escapeHtml(t.cache_ttl_key)}</div></div>`);
            parts.push(`<div class="ifs-detail-grid"><div class="ifs-detail-key">TTL</div><div class="ifs-detail-val">${ttlVal}</div></div>`);
        } else {
            parts.push(`<div class="ifs-section-title">缓存</div>`);
            parts.push(`<div class="ifs-detail-grid"><div class="ifs-detail-key">缓存</div><div class="ifs-detail-val">无</div></div>`);
        }

        if (t.routing || (t.chain && t.chain.length)) {
            parts.push(`<div class="ifs-section-title">路由</div>`);
            if (t.routing) {
                parts.push(`<div class="ifs-detail-grid"><div class="ifs-detail-key">Routing Key</div><div class="ifs-detail-val"><code>${escapeHtml(t.routing)}</code></div></div>`);
            }
            if (t.chain && t.chain.length) {
                const chainHtml = t.chain.map((u, i) => {
                    const isPrimary = i === 0;
                    return `<span class="ifs-chain-item${isPrimary ? ' primary' : ''}">${escapeHtml(u)}</span>`;
                }).join(' → ');
                parts.push(`<div class="ifs-detail-grid"><div class="ifs-detail-key">Chain</div><div class="ifs-detail-val">${chainHtml}</div></div>`);
            }
        }

        if (t.params && t.params.length) {
            parts.push(`<div class="ifs-section-title">参数</div>`);
            const paramsHtml = t.params.map(p => {
                if (typeof p === 'string') {
                    return `<div class="ifs-param-item"><code>${escapeHtml(p)}</code></div>`;
                } else {
                    const required = p.required ? 'required' : 'optional';
                    const type = p.type || 'any';
                    return `<div class="ifs-param-item"><code>${escapeHtml(p.name)}</code><span class="ifs-param-type">${type}</span><span class="ifs-param-req">${required}</span>${p.description ? `<div class="ifs-param-desc">${escapeHtml(p.description)}</div>` : ''}</div>`;
                }
            }).join('');
            parts.push(`<div class="ifs-params-list">${paramsHtml}</div>`);
        }

        if (t.source) {
            parts.push(`<div class="ifs-section-title">数据源</div>`);
            parts.push(`<div class="ifs-detail-grid"><div class="ifs-detail-key">Source</div><div class="ifs-detail-val">${escapeHtml(t.source)}</div></div>`);
        }

        return parts.join('');
    }

    function renderAll() {
        renderSidebar();
        renderContent();
    }

    function clearSearch() {
        searchQuery = '';
        const search = document.getElementById('ifs-search');
        if (search) search.value = '';
        renderAll();
    }

    function setActiveGroup(key) {
        activeGroup = key;
        const hash = key === ALL_KEY ? '' : `#tools=${key}`;
        const newUrl = location.pathname + location.search + hash;
        const oldHash = location.hash || '';
        if (oldHash !== hash) {
            history.replaceState(null, '', newUrl);
        }
        renderAll();
    }

    function maybeAutoSwitch() {
        if (!searchQuery) return;
        if (activeGroup !== ALL_KEY) {
            const buckets = bucketByGroup(filteredAll());
            if ((buckets.get(activeGroup) || []).length > 0) return;
        }
        const buckets = bucketByGroup(filteredAll());
        const firstWithMatch = getGroupOrder().find(k => (buckets.get(k) || []).length > 0);
        activeGroup = firstWithMatch || ALL_KEY;
    }

    function readHashGroup() {
        const m = window.location.href.match(/#tools=([^&#]+)/);
        if (m && (m[1] === ALL_KEY || getGroupOrder().includes(m[1]))) return m[1];
        return ALL_KEY;
    }

    async function loadInterfaces() {
        renderInterfacesSkeleton();
        try {
            const data = await fetchJSON('/api/interfaces');
            document.getElementById('interfaces-timestamp').textContent = formatTime(data.timestamp);
            allTools = data.tools || [];
            const sources = [...new Set(allTools.map(t => t.source).filter(s => s))];
            const knownOrder = GROUP_ORDER;
            dynamicGroupOrder = [
                ...knownOrder.filter(k => sources.includes(k)),
                ...sources.filter(s => !knownOrder.includes(s))
            ];
            activeGroup = readHashGroup();
            renderToggleCounts();
            renderAll();
        } catch (e) {
            console.error('Load interfaces error:', e);
            const root = document.getElementById('ifs-content');
            if (root) root.innerHTML = `<div class="loading">加载失败：${escapeHtml(e.message)}<br><button type="button" class="btn-secondary" data-action="retry-interfaces">重试</button></div>`;
        }
    }

    /* ===== Config panel ===== */
    function humanizeDuration(seconds) {
        if (seconds == null) return '永不过期';
        const s = Number(seconds);
        if (!Number.isFinite(s)) return String(seconds);
        if (s < 60) return `${s} 秒`;
        if (s < 3600) { const m = s / 60; return `${Number.isInteger(m) ? m : m.toFixed(1)} 分钟`; }
        if (s < 86400) { const h = s / 3600; return `${Number.isInteger(h) ? h : h.toFixed(1)} 小时`; }
        const d = s / 86400;
        return `${Number.isInteger(d) ? d : d.toFixed(1)} 天`;
    }

    function renderCfgUpstreams(data) {
        const root = document.getElementById('cfg-panel-upstreams');
        const ups = data.upstreams || {};
        let entries = Object.entries(ups);

        if (_cfgUpstreamsSearch) {
            const q = _cfgUpstreamsSearch.toLowerCase();
            entries = entries.filter(([name, cfg]) =>
                name.toLowerCase().includes(q) ||
                (cfg.type || '').toLowerCase().includes(q) ||
                (cfg.base_url || '').toLowerCase().includes(q) ||
                (cfg.description || '').toLowerCase().includes(q)
            );
        }

        const total = Object.keys(ups).length;
        document.getElementById('cfg-count-upstreams').textContent = entries.length + (entries.length !== total ? `/${total}` : '');

        if (entries.length === 0) { root.innerHTML = '<div class="loading">暂无上游配置</div>'; return; }

        const rows = entries.map(([name, cfg]) => {
            const enabled = cfg.enabled ? '<span class="cfg-switch on">启用</span>' : '<span class="cfg-switch off">停用</span>';
            const apiKeyHtml = cfg.api_key
                ? `<span class="cfg-mask" title="服务端已脱敏 (前 2 + *** + 后 2 字符) — 原始值在 .env 中">${escapeHtml(cfg.api_key)}</span>`
                : '<span style="color:var(--text-secondary)">-</span>';
            const envEntries = cfg.env ? Object.entries(cfg.env) : [];
            const envRowsHtml = envEntries.length
                ? envEntries.map(([k, v]) => {
                    const strVal = String(v);
                    const looksMasked = strVal.includes('***');
                    const tt = looksMasked ? '服务端已脱敏 (前 2 + *** + 后 2 字符)' : '';
                    return `<tr><td class="tool-name">${escapeHtml(k)}</td><td>${tt ? `<span class="cfg-mask" title="${tt}">${escapeHtml(strVal)}</span>` : escapeHtml(strVal)}</td></tr>`;
                  }).join('')
                : '';
            const hasDetail = envEntries.length > 0 || cfg.retry;
            const envRowHtml = hasDetail ? `
                <tr class="cfg-env-row" data-env-for="${escapeHtml(name)}">
                    <td colspan="${CFG_TABLE_COLSPANS.upstreams}">
                        ${envEntries.length ? `
                        <div style="font-size:0.75rem;color:var(--text-secondary);margin-bottom:6px">环境变量 (env)</div>
                        <table class="cfg-table" style="background:var(--card-bg);border:1px solid var(--border);border-radius:4px">
                            <thead><tr><th scope="col" style="width:30%">变量</th><th scope="col">值</th></tr></thead>
                            <tbody>${envRowsHtml}</tbody>
                        </table>` : ''}
                        ${cfg.retry ? `<div style="font-size:0.75rem;color:var(--text-secondary);margin-top:8px">retry: ${escapeHtml(JSON.stringify(cfg.retry))}</div>` : ''}
                    </td>
                </tr>` : '';
            const detailId = `cfg-up-${name.replace(/\W/g, '_')}`;
            // 移除 inline onclick, 改用 event delegation (CSP)
            return `
                <tr class="cfg-expandable" data-detail="${detailId}">
                    <td><span class="tool-name">${escapeHtml(name)}</span>${hasDetail ? '<span class="cfg-expand-icon">▸</span>' : ''}</td>
                    <td><span class="cfg-cap">${escapeHtml(cfg.type || '-')}</span></td>
                    <td>${enabled}</td>
                    <td style="font-family:monospace;font-size:0.75rem;word-break:break-all">${escapeHtml(cfg.base_url || '-')}</td>
                    <td class="num">${cfg.timeout_seconds != null ? cfg.timeout_seconds : '-'}</td>
                    <td style="font-family:monospace;font-size:0.75rem">${apiKeyHtml}</td>
                </tr>${envRowHtml}`;
        }).join('');

        let searchBox = root.querySelector('.cfg-search-box');
        if (!searchBox) {
            // Clear any skeleton placeholder
            root.innerHTML = '';
            searchBox = document.createElement('div');
            searchBox.className = 'cfg-search-box';
            searchBox.innerHTML = `
                <input type="text" class="cfg-search" placeholder="搜索上游... (名称/类型/URL)" value="${escapeHtml(_cfgUpstreamsSearch)}" />
                ${_cfgUpstreamsSearch ? '<button class="cfg-search-clear" title="清空">×</button>' : ''}`;
            root.appendChild(searchBox);
            searchBox.querySelector('input')?.addEventListener('input', (e) => {
                _cfgUpstreamsSearch = e.target.value;
                renderCfgUpstreams(data);
            });
            searchBox.querySelector('.cfg-search-clear')?.addEventListener('click', () => {
                _cfgUpstreamsSearch = '';
                renderCfgUpstreams(data);
            });
        }

        root.innerHTML += `
            <div class="cfg-section">
                <div class="cfg-section-body" style="padding:0">
                    <table class="cfg-table">
                        <thead>
                            <tr><th scope="col" style="width:15%">名称</th><th scope="col" style="width:8%">类型</th><th scope="col" style="width:8%">状态</th><th scope="col">Endpoint</th><th scope="col" class="num" style="width:6%">超时</th><th scope="col" style="width:12%">API Key</th></tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            </div>`;
        const logging = data.logging || {};
        const dd = data.disable_dangerous;
        const sr = data.strict_registry;
        const sw = (label, val, hint) => val
            ? `<span class="cfg-switch on" title="${escapeHtml(hint)} (只读 — 修改 YAML 后重启生效)">${escapeHtml(label)}</span>`
            : `<span class="cfg-switch off" title="${escapeHtml(hint)} (只读 — 修改 YAML 后重启生效)">${escapeHtml(label)}</span>`;
        const loggingKv = Object.entries(logging).map(([k, v]) => {
            const display = typeof v === 'object' && v
                ? Object.entries(v).map(([k2, v2]) => `<span class="cfg-kv-item"><span class="cfg-kv-key">${escapeHtml(k2)}</span>=<span class="cfg-kv-val">${escapeHtml(String(v2))}</span></span>`).join('')
                : escapeHtml(String(v));
            return `<div class="cfg-kv-row"><div class="cfg-kv-key">${escapeHtml(k)}</div><div class="cfg-kv-val">${display}</div></div>`;
        }).join('') || '<div class="cfg-kv-row"><div class="cfg-kv-key">(无)</div><div class="cfg-kv-val">-</div></div>';

        root.innerHTML += `
            <div class="cfg-section">
                <div class="cfg-section-header" data-toggle-section>
                    <span class="cfg-section-title light"><span class="cfg-section-toggle">▾</span> 全局开关 <span style="font-weight:400;color:var(--text-secondary);font-size:0.8rem">— disable_dangerous / strict_registry</span></span>
                </div>
                <div class="cfg-section-body">
                    <div class="cfg-toggles-row">
                        ${sw('disable_dangerous', dd, '启用后跳过所有 dangerous: true 的 tool')}
                        ${sw('strict_registry', sr, '启用后启动校验 YAML 工具名都在上游 tools/list 中实际出现')}
                    </div>
                </div>
            </div>
            <div class="cfg-section">
                <div class="cfg-section-header" data-toggle-section>
                    <span class="cfg-section-title light"><span class="cfg-section-toggle">▾</span> 日志配置</span>
                </div>
                <div class="cfg-section-body">
                    <div class="cfg-kv-grid">${loggingKv}</div>
                </div>
            </div>`;
        root.querySelectorAll('[data-toggle-section]').forEach(hdr => {
            hdr.addEventListener('click', () => hdr.closest('.cfg-section').classList.toggle('collapsed'));
        });
        // event delegation for cfg-expandable rows (替代 inline onclick)
        root.addEventListener('click', onCfgPanelClick);
    }

    function onCfgPanelClick(e) {
        const row = e.target.closest('tr.cfg-expandable');
        if (!row) return;
        const detailId = row.dataset.detail;
        if (!detailId) return;
        const detail = document.getElementById(detailId);
        if (detail) detail.classList.toggle('hidden');
        row.classList.toggle('expanded');
    }

    function renderCfgMapping(data) {
        const root = document.getElementById('cfg-panel-mapping');
        const map = data.upstream_tool_mapping || {};
        const entries = Object.entries(map).sort((a, b) => a[0].localeCompare(b[0]));
        document.getElementById('cfg-count-mapping').textContent = entries.length;
        if (entries.length === 0) { root.innerHTML = '<div class="loading">暂无工具映射</div>'; return; }
        const allRowsHtml = entries.map(([toolName, chain]) => {
            const items = Object.entries(chain || {});
            if (items.length === 0) return `<tr data-search="${escapeHtml(toolName.toLowerCase())}"><td><span class="tool-name">${escapeHtml(toolName)}</span></td><td colspan="${CFG_TABLE_COLSPANS.mapping}" style="color:var(--text-secondary)">无上游映射</td></tr>`;
            const pills = items.map(([upstream, toolCall], idx) =>
                `<span class="chain-pill${idx === 0 ? ' primary' : ''}" title="${escapeHtml(upstream)} :: ${escapeHtml(toolCall)}">${escapeHtml(upstream)} <span style="opacity:0.6">::</span> ${escapeHtml(toolCall)}</span>`
            ).join('<span style="color:var(--text-secondary);margin:0 2px">→</span>');
            const searchKey = (toolName + ' ' + items.map(([u, t]) => `${u} ${t}`).join(' ')).toLowerCase();
            return `<tr data-search="${escapeHtml(searchKey)}"><td><span class="tool-name">${escapeHtml(toolName)}</span></td><td><div class="chain-list">${pills}</div></td></tr>`;
        }).join('');
        root.innerHTML = `
            <div class="cfg-section">
                <div class="cfg-section-header" data-toggle-section>
                    <span class="cfg-section-title light"><span class="cfg-section-toggle">▾</span> ${entries.length} 条工具映射 <span style="font-weight:400;color:var(--text-secondary);font-size:0.8rem">— 路由键 → 上游 + 上游 tool 名</span></span>
                </div>
                <div class="cfg-section-body">
                    <div class="cfg-search-wrap" style="margin-bottom:10px">
                        <label class="visually-hidden" for="cfg-search-mapping">筛选工具映射</label>
                        <input type="text" class="cfg-search" id="cfg-search-mapping" placeholder="按路由键 / 上游 / 上游 tool 筛选…" />
                        <button type="button" class="cfg-search-clear" id="cfg-search-mapping-clear" title="清空">×</button>
                    </div>
                    <table class="cfg-table">
                        <thead><tr><th scope="col" style="width:30%">路由键</th><th scope="col">调用链 (上游 → 上游 tool)</th></tr></thead>
                        <tbody id="cfg-mapping-tbody">${allRowsHtml}</tbody>
                    </table>
                    <div style="font-size:0.75rem;color:var(--text-secondary);margin-top:6px" id="cfg-mapping-count"></div>
                </div>
            </div>`;
        const search = document.getElementById('cfg-search-mapping');
        const clear = document.getElementById('cfg-search-mapping-clear');
        const tbody = document.getElementById('cfg-mapping-tbody');
        const countEl = document.getElementById('cfg-mapping-count');
        const applyFilter = () => {
            const q = (search.value || '').trim().toLowerCase();
            const rows = tbody.querySelectorAll('tr[data-search]');
            let shown = 0;
            rows.forEach(r => {
                const match = !q || (r.dataset.search || '').includes(q);
                r.style.display = match ? '' : 'none';
                if (match) shown++;
            });
            countEl.textContent = q ? `显示 ${shown} / ${rows.length}` : '';
            search.parentElement.classList.toggle('has-value', !!q);
        };
        search.addEventListener('input', applyFilter);
        clear.addEventListener('click', () => { search.value = ''; applyFilter(); });
        root.querySelectorAll('[data-toggle-section]').forEach(hdr => {
            hdr.addEventListener('click', () => hdr.closest('.cfg-section').classList.toggle('collapsed'));
        });
    }

    function renderCfgTools(data) {
        const root = document.getElementById('cfg-panel-tools');
        const tools = data.tools || [];
        document.getElementById('cfg-count-tools').textContent = tools.length;
        if (tools.length === 0) { root.innerHTML = '<div class="loading">暂无工具规格</div>'; return; }
        const sorted = [...tools].sort((a, b) => String(a.name).localeCompare(String(b.name)));
        const allRowsHtml = sorted.map(t => {
            // 危险标签: 用纯文字 "危险" 替代原 ⚠ emoji
            const danger = t.dangerous ? '<span class="cfg-switch off" title="需要调用方显式启用">危险</span>' : '<span style="color:var(--text-secondary)">-</span>';
            const ttl = t.cache_ttl_key ? `<span class="cfg-cap">${escapeHtml(t.cache_ttl_key)}</span>` : '<span style="color:var(--text-secondary)">-</span>';
            const paramsDisplay = (t.params || []).map(p => {
                    if (typeof p === 'object' && p) {
                        const tag = p.required ? '<span style="color:#b91c1c">*</span>' : '<span style="color:var(--text-secondary)">·</span>';
                        return `${tag}${escapeHtml(p.name || '?')}`;
                    }
                    return escapeHtml(String(p));
                }).join(' ') || '-';
            const desc = t.description || '(无描述)';
            const trunc = desc.length > 80 ? desc.slice(0, 80) + '…' : desc;
            const expandable = desc.length > 80 ? ' cfg-expandable' : '';
            const searchKey = `${t.name} ${desc} ${(t.params||[]).map(p => p && p.name || p).join(' ')} ${t.cache_ttl_key || ''}`.toLowerCase();
            const detailHtml = desc.length > 80 ? `
                <tr class="cfg-detail-row" data-detail-for="${escapeHtml(t.name)}" style="display:none">
                    <td colspan="${CFG_TABLE_COLSPANS.tools}">${escapeHtml(desc)}</td>
                </tr>` : '';
            return `
                <tr class="${expandable.trim()}" data-tool="${escapeHtml(t.name)}" data-search="${escapeHtml(searchKey)}">
                    <td><span class="tool-name">${escapeHtml(t.name)}</span></td>
                    <td>${escapeHtml(trunc)}</td>
                    <td>${ttl}</td>
                    <td style="font-family:monospace;font-size:0.75rem">${paramsDisplay}</td>
                    <td>${danger}</td>
                </tr>${detailHtml}`;
        }).join('');
        root.innerHTML = `
            <div class="cfg-section">
                <div class="cfg-section-header" data-toggle-section>
                    <span class="cfg-section-title mono"><span class="cfg-section-toggle">▾</span> ${tools.length} 个工具规格 <span style="font-weight:400;color:var(--text-secondary);font-size:0.8rem">— 名称 / 描述 / 缓存键 / 参数 / 危险标记 · 点行展开完整描述</span></span>
                </div>
                <div class="cfg-section-body">
                    <div class="cfg-search-wrap" style="margin-bottom:10px">
                        <label class="visually-hidden" for="cfg-search-tools">筛选工具规格</label>
                        <input type="text" class="cfg-search" id="cfg-search-tools" placeholder="按名称 / 描述 / 参数筛选…" />
                        <button type="button" class="cfg-search-clear" id="cfg-search-tools-clear" title="清空">×</button>
                    </div>
                    <table class="cfg-table">
                        <thead><tr><th scope="col" style="width:20%">名称</th><th scope="col">描述</th><th scope="col" style="width:11%">缓存键</th><th scope="col" style="width:24%">参数 <span style="text-transform:none; color:var(--text-secondary); font-weight:400"><span style="color:#b91c1c">*</span>必填 · <span style="color:var(--text-secondary)">·</span>可选</span></th><th scope="col" style="width:8%">危险</th></tr></thead>
                        <tbody id="cfg-tools-tbody">${allRowsHtml}</tbody>
                    </table>
                    <div style="font-size:0.75rem;color:var(--text-secondary);margin-top:6px" id="cfg-tools-count"></div>
                </div>
            </div>`;
        const search = document.getElementById('cfg-search-tools');
        const clear = document.getElementById('cfg-search-tools-clear');
        const tbody = document.getElementById('cfg-tools-tbody');
        const countEl = document.getElementById('cfg-tools-count');
        const applyFilter = () => {
            const q = (search.value || '').trim().toLowerCase();
            const rows = tbody.querySelectorAll('tr[data-search]');
            let shown = 0;
            rows.forEach(r => {
                const match = !q || (r.dataset.search || '').includes(q);
                r.style.display = match ? '' : 'none';
                const detail = tbody.querySelector(`tr.cfg-detail-row[data-detail-for="${r.dataset.tool}"]`);
                if (detail) detail.style.display = 'none';
                if (match) shown++;
            });
            countEl.textContent = q ? `显示 ${shown} / ${rows.length}` : '';
            search.parentElement.classList.toggle('has-value', !!q);
        };
        search.addEventListener('input', applyFilter);
        clear.addEventListener('click', () => { search.value = ''; applyFilter(); });
        tbody.addEventListener('click', (e) => {
            const tr = e.target.closest('tr.cfg-expandable');
            if (!tr) return;
            const detail = tbody.querySelector(`tr.cfg-detail-row[data-detail-for="${tr.dataset.tool}"]`);
            if (detail) {
                const show = detail.style.display === 'none';
                detail.style.display = show ? '' : 'none';
            }
        });
        root.querySelectorAll('[data-toggle-section]').forEach(hdr => {
            hdr.addEventListener('click', () => hdr.closest('.cfg-section').classList.toggle('collapsed'));
        });
    }

    function renderCfgCache(data) {
        const root = document.getElementById('cfg-panel-cache');
        const cache = data.cache || {};
        const enabled = cache.enabled ? '<span class="cfg-switch on">启用</span>' : '<span class="cfg-switch off">停用</span>';
        const ttl = cache.ttl || {};
        const ttlEntries = Object.entries(ttl).sort((a, b) => Number(a[1]) - Number(b[1]));
        const ttlRows = ttlEntries.map(([k, v]) =>
            `<tr><td><span class="tool-name">${escapeHtml(k)}</span></td><td class="num">${v}</td><td>${humanizeDuration(v)}</td></tr>`
        ).join('') || `<tr><td colspan="${CFG_TABLE_COLSPANS.cache}" style="color:var(--text-secondary)">无 TTL 配置</td></tr>`;
        const kvHtml = `
            <div class="cfg-kv-grid">
                <div class="k">启用</div><div class="v">${enabled}</div>
                <div class="k">DB 路径</div><div class="v">${escapeHtml(cache.db_path || '-')}</div>
                <div class="k">TTL 配置</div><div class="v"><span class="num">${ttlEntries.length}</span> 条 · 最短 <strong>${ttlEntries.length ? humanizeDuration(ttlEntries[0][1]) : '-'}</strong> · 最长 <strong>${ttlEntries.length ? humanizeDuration(ttlEntries[ttlEntries.length-1][1]) : '-'}</strong></div>
            </div>`;
        // 提示横幅: 用纯文字 + CSS 类替代 ℹ️ emoji
        root.innerHTML = '<div class="cfg-cache-banner"><span class="cfg-info-icon" aria-hidden="true">i</span>此处显示的是 <code>config/config.yaml</code> 里的 <code>cache</code> 配置（与"上游状态"标签页的运行时缓存是不同概念）。<strong>修改后需重启网关</strong>生效。</div>'
            + `<div class="cfg-section">
                <div class="cfg-section-header" data-toggle-section>
                    <span class="cfg-section-title light"><span class="cfg-section-toggle">▾</span> 缓存基本配置</span>
                </div>
                <div class="cfg-section-body">${kvHtml}</div>
            </div>
            <div class="cfg-section">
                <div class="cfg-section-header" data-toggle-section>
                    <span class="cfg-section-title light"><span class="cfg-section-toggle">▾</span> ${ttlEntries.length} 条 TTL 配置 <span style="font-weight:400;color:var(--text-secondary);font-size:0.8rem">— 按 TTL 升序排列</span></span>
                </div>
                <div class="cfg-section-body">
                    <table class="cfg-table">
                        <thead><tr><th scope="col">键</th><th scope="col" class="num">秒</th><th scope="col">人类可读</th></tr></thead>
                        <tbody>${ttlRows}</tbody>
                    </table>
                </div>
            </div>`;
        root.querySelectorAll('[data-toggle-section]').forEach(hdr => {
            hdr.addEventListener('click', () => hdr.closest('.cfg-section').classList.toggle('collapsed'));
        });
    }

    async function loadConfig(silent) {
        if (!silent) {
            renderConfigSkeleton('cfg-panel-upstreams');
            renderConfigSkeleton('cfg-panel-mapping');
            renderConfigSkeleton('cfg-panel-tools');
            renderConfigSkeleton('cfg-panel-cache');
        }
        try {
            const data = await fetchJSON('/api/config');
            _cfgCache = data;
            renderCfgUpstreams(data);
            renderCfgMapping(data);
            renderCfgTools(data);
            renderCfgCache(data);
            if (!silent) {
                const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
                const el = document.getElementById('config-last-refresh');
                if (el) el.textContent = `已刷新 ${ts}`;
                const meta = document.getElementById('config-meta');
                if (meta) {
                    const upCount = Object.keys(data.upstreams || {}).length;
                    const toolCount = (data.tools || []).length;
                    meta.textContent = `·  ${upCount} 上游 · ${toolCount} 工具`;
                }
            }
        } catch (e) {
            console.error('Load config error:', e);
            _cfgCache = null;
            const failMsg = `<div class="loading">加载失败：${escapeHtml(e.message)}<br><button type="button" class="btn-secondary" data-action="retry-config">重试</button></div>`;
            ['cfg-panel-upstreams', 'cfg-panel-mapping', 'cfg-panel-tools', 'cfg-panel-cache']
                .forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = failMsg; });
            const meta = document.getElementById('config-meta');
            if (meta) meta.textContent = '加载失败';
        }
    }

    function setCfgTab(name) {
        document.querySelectorAll('.cfg-subtab').forEach(b => b.classList.toggle('active', b.dataset.cfg === name));
        document.querySelectorAll('.cfg-panel').forEach(p => p.classList.toggle('active', p.dataset.cfgPanel === name));
    }

    function refreshAll() {
        loadStatus();
        loadInterfaces();
        loadConfig();
    }

    /* ===== Onboarding ===== */
    async function loadOnboarding() {
        const container = document.getElementById('interfaces-summary');
        const totalEl = document.getElementById('ifs-total-count');
        try {
            const [ifsRes, upsRes] = await Promise.allSettled([
                fetchJSON('/api/interfaces', { silent: true }),
                fetchJSON('/api/status', { silent: true }),
            ]);
            if (ifsRes.status !== 'fulfilled') {
                throw ifsRes.reason;
            }
            const ifsData = ifsRes.value;
            const upsData = upsRes.status === 'fulfilled' ? upsRes.value : { upstreams: {} };
            const tools = ifsData.tools || [];
            const descByName = {};
            for (const [name, info] of Object.entries(upsData.upstreams || {})) {
                descByName[name] = info.description || '';
            }
            if (totalEl) totalEl.textContent = tools.length;
            if (tools.length === 0) {
                container.innerHTML = '<div class="loading-state" style="grid-column:1/-1">暂无接口配置</div>';
                return;
            }
            const counts = new Map();
            for (const t of tools) {
                const k = primarySource(t.source) || '其他';
                counts.set(k, (counts.get(k) || 0) + 1);
            }
            const groups = [ALL_KEY, ...getGroupOrder()].filter(k => k === ALL_KEY || counts.has(k));
            container.innerHTML = groups.map(key => {
                const meta = key === ALL_KEY ? { title: '全部接口', desc: '一次性查看所有上游的 tool' } : (GROUP_META[key] || { title: key, desc: '' });
                const count = key === ALL_KEY ? tools.length : (counts.get(key) || 0);
                const desc = (key !== ALL_KEY && descByName[key]) || meta.desc || '';
                return `<a href="#" data-jump-panel="interfaces" data-jump-group="${escapeHtml(key)}"><span><strong>${escapeHtml(meta.title)}</strong><br><small style="color:var(--text-secondary)">${escapeHtml(desc)}</small></span><span><span class="count">${count}</span> <span class="arrow">→</span></span></a>`;
            }).join('');
        } catch (e) {
            console.error('Load interfaces error:', e);
            if (totalEl) totalEl.textContent = '加载失败';
            // 移除 inline onclick, 改用 event delegation (CSP)
            container.innerHTML = `<div class="loading-state" style="grid-column:1/-1">加载失败 <span class="retry-link" data-action="retry-onboarding">点击重试</span></div>`;
        }
    }

    function jumpToPanel(panelId, groupKey) {
        const tab = document.querySelector(`.tab[data-panel="${panelId}"]`);
        if (!tab) return;
        tab.click();
        if (!groupKey) return;
        const tryApply = (tries) => {
            if (tries == null) tries = 60;
            if (document.querySelector(`.ifs-nav-item[data-group="${CSS.escape(groupKey)}"]`)) {
                setActiveGroup(groupKey);
            } else if (tries > 0) {
                setTimeout(() => tryApply(tries - 1), 50);
            }
        };
        tryApply();
    }

    /* ===== Wiring ===== */
    function wireTabs() {
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => activatePanel(tab.dataset.panel));
        });
        window.addEventListener('hashchange', () => {
            const g = readHashGroup();
            if (g !== activeGroup && document.getElementById('interfaces').classList.contains('active')) {
                setActiveGroup(g);
            }
        });
    }

    function wireInterfaces() {
        document.getElementById('ifs-search').addEventListener('input', (e) => {
            searchQuery = (e.target.value || '').trim().toLowerCase();
            document.getElementById('ifs-search-wrap').classList.toggle('has-value', !!searchQuery);
            clearTimeout(searchDebounceTimer);
            searchDebounceTimer = setTimeout(() => {
                maybeAutoSwitch();
                renderAll();
            }, 300);
        });
        document.getElementById('ifs-search-clear').addEventListener('click', () => clearSearch());
        document.getElementById('ifs-toggles').addEventListener('change', (e) => {
            const lbl = e.target.closest('.ifs-toggle');
            if (!lbl) return;
            const key = lbl.dataset.toggle;
            toggles[key] = e.target.checked;
            lbl.classList.toggle('active', e.target.checked);
            renderAll();
        });
        document.getElementById('datasource-chips').addEventListener('click', (e) => {
            const chip = e.target.closest('.chip');
            if (!chip) return;
            document.querySelectorAll('#datasource-chips .chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            dataSourceFilter = chip.dataset.dst || '';
            renderAll();
        });
        document.getElementById('ifs-content').addEventListener('click', (e) => {
            const copyBtn = e.target.closest('.copy-name');
            if (copyBtn) { copyText(copyBtn.dataset.name, copyBtn); return; }
            const detailBtn = e.target.closest('.toggle-detail');
            if (detailBtn) {
                const toolName = detailBtn.closest('tr')?.dataset?.tool;
                if (!toolName) return;
                const tr = document.querySelector(`.ifs-detail-row[data-detail-for="${CSS.escape(toolName)}"]`);
                if (tr) {
                    const isHidden = tr.classList.toggle('ifs-detail-row-hidden');
                    if (isHidden) _expandedTools.delete(toolName);
                    else _expandedTools.add(toolName);
                    detailBtn.textContent = isHidden ? '详情' : '收起';
                }
                return;
            }
            const srcBtn = e.target.closest('.chain-link');
            if (srcBtn && srcBtn.dataset.source) {
                setActiveGroup(srcBtn.dataset.source);
            }
            // 替代 renderEmpty() 模板里的 onclick="clearSearch()"
            const clearBtn = e.target.closest('[data-action="clear-search"]');
            if (clearBtn) { clearSearch(); return; }
            // loadInterfaces 失败时的重试按钮
            const retryBtn = e.target.closest('[data-action="retry-interfaces"]');
            if (retryBtn) { loadInterfaces(); return; }
        });
        document.getElementById('ifs-copy-all').addEventListener('click', (e) => {
            const names = window._currentFilteredToolNames || [];
            if (!names.length) { showToast('暂无可复制的工具', 'error'); return; }
            copyText(names.join('\n'), e.currentTarget);
        });
        document.getElementById('ifs-nav').addEventListener('click', (e) => {
            const btn = e.target.closest('.ifs-nav-item');
            if (!btn || btn.hasAttribute('disabled')) return;
            setActiveGroup(btn.dataset.group);
        });
        document.addEventListener('keydown', (e) => {
            // / 和 ArrowUp/Down 是 interfaces 面板的快捷键, 离开该面板不再生效
            if (activePanel !== 'interfaces') return;
            const tag = (e.target.tagName || '').toLowerCase();
            const inField = tag === 'input' || tag === 'textarea';
            if (e.key === '/' && !inField) {
                e.preventDefault();
                document.getElementById('ifs-search').focus();
                return;
            }
            if (e.key === 'Escape') {
                const search = document.getElementById('ifs-search');
                if (document.activeElement === search || (search && search.value)) {
                    e.preventDefault();
                    clearSearch();
                    if (search) search.blur();
                }
                return;
            }
            if (inField) return;
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                const items = Array.from(document.querySelectorAll('.ifs-nav-item:not([disabled])'));
                if (items.length === 0) return;
                const idx = items.findIndex(b => b.classList.contains('active'));
                const next = e.key === 'ArrowDown'
                    ? items[Math.min(items.length - 1, idx + 1)]
                    : items[Math.max(0, idx - 1)];
                if (next && next !== items[idx]) setActiveGroup(next.dataset.group);
            }
        });
    }

    function wireStatus() {
        document.getElementById('refresh-now').addEventListener('click', () => loadStatus(true));
        document.getElementById('cache-reload').addEventListener('click', () => loadStatus(true));
        document.getElementById('auto-refresh').addEventListener('change', (e) => {
            if (e.target.checked) startAutoRefresh(); else stopAutoRefresh();
        });
        document.querySelector('.filter-chips').addEventListener('click', (e) => {
            const chip = e.target.closest('.chip');
            if (chip) setStatusFilter(chip.dataset.filter);
        });
        document.querySelector('.ups-table').addEventListener('click', (e) => {
            const th = e.target.closest('th.sortable');
            if (th) { setStatusSort(th.dataset.sort); return; }
            const errBtn = e.target.closest('.err-icon');
            if (errBtn) copyText(errBtn.dataset.error, errBtn);
        });
    }

    function wireConfig() {
        document.getElementById('cfg-subtabs').addEventListener('click', (e) => {
            const btn = e.target.closest('.cfg-subtab');
            if (!btn) return;
            setCfgTab(btn.dataset.cfg);
        });
        document.getElementById('config-reload').addEventListener('click', () => loadConfig(false));
        document.getElementById('config-copy-all').addEventListener('click', async (e) => {
            if (!_cfgCache) { showToast('尚未加载配置', 'error'); return; }
            await copyText(JSON.stringify(_cfgCache, null, 2), e.currentTarget);
        });
    }

    function wireOnboarding() {
        document.getElementById('onboarding').addEventListener('click', (e) => {
            const jumpBtn = e.target.closest('[data-jump-panel]');
            if (jumpBtn) {
                e.preventDefault();
                jumpToPanel(jumpBtn.dataset.jumpPanel, jumpBtn.dataset.jumpGroup);
                return;
            }
            const cmdBtn = e.target.closest('[data-copy-cmd]');
            if (cmdBtn) {
                copyText(cmdBtn.dataset.copyCmd, cmdBtn);
                return;
            }
            const copyBtn = e.target.closest('.copy-btn');
            if (copyBtn) {
                const pre = copyBtn.nextElementSibling;
                if (pre && pre.tagName === 'PRE') copyText(pre.textContent, copyBtn);
            }
            // 替代 loadOnboarding 失败模板里的 onclick="loadOnboarding()"
            const retryLink = e.target.closest('[data-action="retry-onboarding"]');
            if (retryLink) { loadOnboarding(); return; }
        });

        initOnboardingObserver();
    }

    function initOnboardingObserver() {
        const navLinks = document.querySelectorAll('#onboarding-nav a');
        const sections = Array.from(navLinks).map(a => document.getElementById(a.dataset.target)).filter(Boolean);
        if (sections.length === 0) return;
        _onboardingObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    navLinks.forEach(l => l.classList.remove('active'));
                    const link = document.querySelector(`#onboarding-nav a[data-target="${entry.target.id}"]`);
                    if (link) link.classList.add('active');
                }
            });
        }, { rootMargin: '-20% 0px -70% 0px' });
        sections.forEach(s => _onboardingObserver.observe(s));
    }

    function wireGlobalActions() {
        // loadConfig 失败模板里的 [data-action="retry-config"]
        document.addEventListener('click', (e) => {
            const retry = e.target.closest('[data-action="retry-config"]');
            if (retry) { loadConfig(false); return; }
        });
        window.addEventListener('beforeunload', () => {
            stopAutoRefresh();
            stopUpdateClock();
        });
    }

    /* ===== OMNI panel (板块数据) ===== */
    const OMNI_STATUS_COLOR = { success: 'var(--success)', failed: 'var(--danger)', running: 'var(--warning)' };

    function omniFmtTime(s) {
        if (!s) return '-';
        return String(s).replace('T', ' ').substring(0, 19);
    }

    function omniFmtStatus(s) {
        const cls = OMNI_STATUS_COLOR[s] ? `omni-status-${s}` : 'omni-status-default';
        return `<span class="${cls}">${escapeHtml(s)}</span>`;
    }

    function omniRenderStats(stats) {
        if (!stats || stats.length === 0) return '<p class="omni-hint">暂无数据，请先同步</p>';
        return stats.map(s => `
            <div class="stat-card">
                <div class="stat-label">${escapeHtml(s.source)} / ${escapeHtml(s.board_type)}</div>
                <div class="stat-value">${Number(s.sector_count) || 0}</div>
                <div class="stat-unit">板块</div>
                <div class="stat-value">${Number(s.total_stocks) || 0}</div>
                <div class="stat-unit">股票</div>
            </div>
        `).join('');
    }

    function omniRenderLogs(logs) {
        if (!logs || logs.length === 0) return '<p class="omni-hint">暂无同步记录</p>';
        return logs.map(l => `
            <div class="log-item">
                <span class="log-time">${escapeHtml(omniFmtTime(l.start_time))}</span>
                <span class="log-source">${escapeHtml(l.source)}</span>
                <span class="log-type">${escapeHtml(l.board_type)}</span>
                <span class="log-status">${omniFmtStatus(l.status)}</span>
                <span class="log-count">${Number(l.record_count) || 0}条</span>
            </div>
        `).join('');
    }

    async function omniLoadStats() {
        const statsEl = document.getElementById('omni-stats');
        const logsEl = document.getElementById('omni-log-list');
        const refreshEl = document.getElementById('omni-last-refresh');
        try {
            const d = await fetchJSON('/api/omni', { silent: true });
            if (statsEl) statsEl.innerHTML = omniRenderStats(d.stats);
            if (logsEl) logsEl.innerHTML = omniRenderLogs(d.recent_logs);
            if (refreshEl) refreshEl.textContent = new Date().toLocaleTimeString();
        } catch (e) {
            // fetchJSON 已把后端错误体塞到 message 里; 网络失败是 TypeError, 后端 4xx/5xx 走 e.status
            const hint = e.status === 404 || e.status === 503
                ? '数据库未初始化，请先同步'
                : `加载失败: ${e.message}`;
            if (statsEl) statsEl.innerHTML = '<p class="omni-hint">' + escapeHtml(hint) + '</p>';
        }
    }

    /* ===== OMNI pagination state ===== */
    let _omniQueryState = {
        source: '',
        boardType: '',
        keyword: '',
        page: 1,
        pageSize: 50,
        totalCount: 0
    };

    function omniRenderPagination(page, pageSize, totalCount) {
        const totalPages = Math.ceil(totalCount / pageSize);
        if (totalPages <= 1) return '';
        const pages = [];
        const maxVisible = 5;
        let start = Math.max(1, page - Math.floor(maxVisible / 2));
        let end = Math.min(totalPages, start + maxVisible - 1);
        if (end - start + 1 < maxVisible) start = Math.max(1, end - maxVisible + 1);

        if (start > 1) pages.push('<button class="omni-page-btn" data-page="1">1</button>');
        if (start > 2) pages.push('<span class="omni-page-ellipsis">...</span>');
        for (let i = start; i <= end; i++) {
            pages.push(`<button class="omni-page-btn${i === page ? ' active' : ''}" data-page="${i}">${i}</button>`);
        }
        if (end < totalPages - 1) pages.push('<span class="omni-page-ellipsis">...</span>');
        if (end < totalPages) pages.push(`<button class="omni-page-btn" data-page="${totalPages}">${totalPages}</button>`);

        const prevDisabled = page <= 1 ? ' disabled' : '';
        const nextDisabled = page >= totalPages ? ' disabled' : '';

        return `
            <div class="omni-pagination">
                <button class="omni-page-btn" data-page="${page - 1}"${prevDisabled}>上一页</button>
                <div class="omni-page-numbers">${pages.join('')}</div>
                <button class="omni-page-btn" data-page="${page + 1}"${nextDisabled}>下一页</button>
                <span class="omni-page-info">第 ${page} / ${totalPages} 页 (共 ${totalCount} 条)</span>
            </div>`;
    }

    async function omniQuerySectors(page = 1) {
        const source = document.getElementById('omni-query-source').value;
        const boardType = document.getElementById('omni-query-type').value;
        const keyword = document.getElementById('omni-keyword').value.trim();
        const resultsDiv = document.getElementById('omni-results');

        // 保存查询状态
        _omniQueryState = { source, boardType, keyword, page, pageSize: 50, totalCount: 0 };

        resultsDiv.innerHTML = '<div class="omni-feedback omni-feedback-info"><span class="status-icon status-icon-loading">⏳</span> 查询中…</div>';

        try {
            const params = new URLSearchParams();
            if (source) params.append('source', source);
            if (boardType) params.append('board_type', boardType);
            if (keyword) params.append('keyword', keyword);
            params.append('page', page);
            params.append('page_size', 50);

            const d = await fetchJSON('/api/omni/query?' + params.toString(), { silent: true });

            const queryDesc = [source && `数据源=${source}`, boardType && `类型=${boardType}`, keyword && `关键词="${keyword}"`].filter(Boolean).join(', ') || '(无筛选)';

            if (!d.sectors || d.sectors.length === 0) {
                resultsDiv.innerHTML = `<div class="omni-feedback omni-feedback-empty"><span class="status-icon status-icon-empty">○</span> 未找到匹配的板块 <span class="omni-feedback-meta">[${escapeHtml(queryDesc)}]</span></div>`;
                return;
            }

            _omniQueryState.totalCount = d.total_count || d.sectors.length;
            const pagination = omniRenderPagination(d.page || page, d.page_size || 50, _omniQueryState.totalCount);

            const header = `<div class="omni-results-header">找到 <strong>${_omniQueryState.totalCount}</strong> 个板块 <span class="omni-feedback-meta">[${escapeHtml(queryDesc)}]</span></div>`;
            const list = `
                <table class="omni-sector-table">
                    <thead>
                        <tr>
                            <th>板块名称</th>
                            <th>代码</th>
                            <th>类型</th>
                            <th>成分股数量</th>
                            <th>数据源</th>
                            <th>更新时间</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${d.sectors.map(s => `
                            <tr class="sector-item" data-id="${escapeHtml(String(s.id))}">
                                <td class="sector-name">${escapeHtml(s.name)}</td>
                                <td class="sector-code">${escapeHtml(s.code)}</td>
                                <td><span class="sector-type-badge">${escapeHtml(s.board_type)}</span></td>
                                <td class="sector-count">${Number(s.stock_count) || 0}</td>
                                <td class="sector-source">${escapeHtml(s.source)}</td>
                                <td class="sector-update">${s.update_time ? escapeHtml(s.update_time.slice(0, 10)) : '-'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>`;
            resultsDiv.innerHTML = header + list + pagination;
            // 分页与板块行的点击由 wireOmni 里的事件委托统一处理 (避免每次重渲 forEach addEventListener)
        } catch (e) {
            resultsDiv.innerHTML = '<div class="omni-feedback omni-feedback-error"><span class="status-icon status-icon-error">✕</span> 查询异常：' + escapeHtml(e.message) + '</div>';
        }
    }

    async function omniSync() {
        if (_omniSyncInFlight) {
            showToast('同步已在进行中，请等待完成', 'warn');
            return;
        }
        const btn = document.getElementById('omni-sync-btn');
        const historyEl = document.getElementById('omni-sync-history-list');
        const source = document.getElementById('omni-sync-source').value;
        const boardType = document.getElementById('omni-sync-type').value;
        if (!btn) return;
        _omniSyncInFlight = true;
        btn.disabled = true;
        btn.textContent = '同步中...';
        if (historyEl) historyEl.innerHTML = '<div class="omni-feedback omni-feedback-info"><span class="status-icon status-icon-loading">⏳</span> 同步中… <span class="omni-feedback-meta">(同步可能持续数十秒, 请勿关闭页面)</span></div>';
        try {
            const d = await fetchJSON('/api/omni/sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source, board_type: boardType }),
                silent: true,
            });
            if (d.status === 'success') {
                showToast('同步成功: ' + (d.message || ''), 'success');
            } else {
                const msg = (d.message || '未知错误').substring(0, 200);
                showToast('同步失败: ' + msg, 'error');
            }
        } catch (e) {
            showToast('同步失败: ' + e.message, 'error');
        } finally {
            _omniSyncInFlight = false;
            btn.disabled = false;
            btn.textContent = '立即同步';
        }
        // 完成后刷新概览 + 历史 (这两块在 sync tab 内自包含, 不依赖 stats tab)
        omniLoadSyncTab();
    }

    /* ===== OMNI 同步管理 tab 自渲染 (上次同步概览 + 最近历史) ===== */
    async function omniLoadSyncTab() {
        const lastEl = document.getElementById('omni-last-sync');
        const historyEl = document.getElementById('omni-sync-history-list');
        if (!lastEl && !historyEl) return;
        try {
            const d = await fetchJSON('/api/omni', { silent: true });
            if (lastEl) lastEl.innerHTML = omniRenderLastSync(d.recent_logs);
            if (historyEl) historyEl.innerHTML = omniRenderRecentHistory(d.recent_logs, 3);
        } catch (e) {
            if (lastEl) lastEl.innerHTML = '<div class="omni-hint">加载失败</div>';
        }
    }

    function omniRenderLastSync(logs) {
        if (!logs || logs.length === 0) return '<div class="omni-hint">暂无同步记录 — 下方点击"立即同步"开始</div>';
        const last = logs[0];
        const statusClass = last.status === 'success' ? 'omni-last-sync-success'
            : last.status === 'running' ? 'omni-last-sync-running'
            : 'omni-last-sync-failed';
        return `
            <div class="omni-last-sync-card ${statusClass}">
                <div class="omni-last-sync-label">上次同步</div>
                <div class="omni-last-sync-main">
                    <span class="omni-last-sync-source">${escapeHtml(last.source)}</span>
                    <span class="omni-last-sync-type">${escapeHtml(last.board_type || '-')}</span>
                    <span class="omni-last-sync-status">${omniFmtStatus(last.status)}</span>
                </div>
                <div class="omni-last-sync-meta">
                    <span>${escapeHtml(omniFmtTime(last.start_time))}</span>
                    <span class="omni-last-sync-count">写入 <strong>${Number(last.record_count) || 0}</strong> 条</span>
                </div>
            </div>`;
    }

    function omniRenderRecentHistory(logs, limit) {
        const items = (logs || []).slice(0, limit);
        if (items.length === 0) return '<p class="omni-hint">暂无同步记录</p>';
        return items.map(l => `
            <div class="log-item">
                <span class="log-time">${escapeHtml(omniFmtTime(l.start_time))}</span>
                <span class="log-source">${escapeHtml(l.source)}</span>
                <span class="log-type">${escapeHtml(l.board_type || '-')}</span>
                <span class="log-status">${omniFmtStatus(l.status)}</span>
                <span class="log-count">${Number(l.record_count) || 0}条</span>
            </div>
        `).join('');
    }

    function wireOmni() {
        // 子标签页切换 (同步 aria-selected + tabindex, 让屏幕阅读器 + 键盘 Tab 顺序正确)
        document.querySelectorAll('.omni-subtab').forEach(btn => {
            btn.addEventListener('click', () => {
                const tab = btn.dataset.omni;
                document.querySelectorAll('.omni-subtab').forEach(t => {
                    t.classList.remove('active');
                    t.setAttribute('aria-selected', 'false');
                    t.setAttribute('tabindex', '-1');
                });
                btn.classList.add('active');
                btn.setAttribute('aria-selected', 'true');
                btn.setAttribute('tabindex', '0');
                document.querySelectorAll('.omni-panel').forEach(p => p.classList.remove('active'));
                const panel = document.querySelector('[data-omni-panel="' + tab + '"]');
                if (panel) panel.classList.add('active');
                // 切换到 sync tab 时加载概览 + 历史
                if (tab === 'sync') omniLoadSyncTab();
            });
        });

        // 按钮绑定
        const queryBtn = document.getElementById('omni-query-btn');
        if (queryBtn) queryBtn.addEventListener('click', () => omniQuerySectors(1));
        const keywordInput = document.getElementById('omni-keyword');
        if (keywordInput) {
            keywordInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    omniQuerySectors(1);
                }
            });
        }
        const refreshBtn = document.getElementById('omni-refresh-btn');
        if (refreshBtn) refreshBtn.addEventListener('click', () => { omniLoadStats(); omniLoadSyncTab(); });
        const syncBtn = document.getElementById('omni-sync-btn');
        if (syncBtn) syncBtn.addEventListener('click', omniSync);

        // OMNI 结果区事件委托: 分页按钮 + 板块行点击 (避免 omniQuerySectors 每次重渲 forEach addEventListener)
        const resultsDiv = document.getElementById('omni-results');
        if (resultsDiv) {
            resultsDiv.addEventListener('click', (e) => {
                const pageBtn = e.target.closest('.omni-page-btn:not([disabled])');
                if (pageBtn) {
                    const newPage = parseInt(pageBtn.dataset.page, 10);
                    if (newPage && newPage !== _omniQueryState.page) {
                        omniQuerySectors(newPage);
                    }
                    return;
                }
                const sectorRow = e.target.closest('tr.sector-item');
                if (sectorRow) {
                    omniOpenSectorModal(sectorRow);
                }
            });
        }
        // 首屏不再预加载, 改为首次切到 OMNI 标签页时触发 (activatePanel 内)
    }

    /* ===== OMNI 板块成分股弹窗 ===== */
    let _omniModalLastFocus = null;

    function omniOpenSectorModal(rowEl) {
        const modal = document.getElementById('omni-modal');
        const bodyEl = document.getElementById('omni-modal-body');
        const titleEl = document.getElementById('omni-modal-title');
        const metaEl = document.getElementById('omni-modal-meta');
        if (!modal || !bodyEl) return;

        // 从行 dataset + cell 抽取需要的信息 (避免重复请求元数据)
        const sectorId = rowEl.dataset.id;
        const name = rowEl.querySelector('.sector-name')?.textContent || '板块';
        const code = rowEl.querySelector('.sector-code')?.textContent || '';
        const source = rowEl.querySelector('.sector-source')?.textContent || '';
        const boardType = rowEl.querySelector('.sector-type-badge')?.textContent || '';

        // 立即打开 modal, 内容由 fetch 填充
        _omniModalLastFocus = document.activeElement;
        titleEl.textContent = name;
        metaEl.textContent = `${code} · ${boardType} · ${source}`;
        bodyEl.innerHTML = '<div class="omni-feedback omni-feedback-info"><span class="status-icon status-icon-loading">⏳</span> 加载中…</div>';
        modal.hidden = false;

        // 焦点移到关闭按钮 (基本可访问性)
        const closeBtn = modal.querySelector('.omni-modal-close');
        if (closeBtn) setTimeout(() => closeBtn.focus(), 0);

        // 请求成分股
        const params = new URLSearchParams();
        if (sectorId) params.set('sector_id', sectorId);
        else { params.set('code', code); params.set('source', source); }

        fetchJSON('/api/omni/sector-stocks?' + params.toString(), { silent: true })
            .then(d => omniRenderStocks(bodyEl, d))
            .catch(err => {
                bodyEl.innerHTML = '<div class="omni-feedback omni-feedback-error"><span class="status-icon status-icon-error">✕</span> 加载失败：' + escapeHtml(err.message || '未知错误') + '</div>';
            });
    }

    function omniRenderStocks(bodyEl, data) {
        const stocks = (data && data.stocks) || [];
        const sector = (data && data.sector) || {};
        if (!stocks.length) {
            bodyEl.innerHTML = '<div class="omni-feedback omni-feedback-empty"><span class="status-icon status-icon-empty">○</span> 暂无成分股数据</div>';
            return;
        }
        const rows = stocks.map(s => `
            <tr>
                <td class="stock-rank">${Number(s.rank) || '-'}</td>
                <td class="stock-code">${escapeHtml(s.stock_code)}</td>
                <td class="stock-name">${escapeHtml(s.stock_name || '')}</td>
            </tr>
        `).join('');
        bodyEl.innerHTML = `
            <div class="omni-results-header">共 <strong>${stocks.length}</strong> 只成分股 ${sector.stock_count ? `(板块宣称 ${sector.stock_count} 只)` : ''}</div>
            <table class="omni-stocks-table">
                <thead><tr><th>#</th><th>代码</th><th>名称</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    function omniCloseModal() {
        const modal = document.getElementById('omni-modal');
        if (!modal || modal.hidden) return;
        modal.hidden = true;
        if (_omniModalLastFocus && typeof _omniModalLastFocus.focus === 'function') {
            _omniModalLastFocus.focus();
        }
        _omniModalLastFocus = null;
    }

    // 弹窗事件一次性绑定 (DOMContentLoaded 后只跑一次)
    function wireOmniModal() {
        const modal = document.getElementById('omni-modal');
        if (!modal) return;
        // backdrop + 关闭按钮
        modal.addEventListener('click', (e) => {
            if (e.target.closest('[data-modal-close]')) omniCloseModal();
        });
        // Escape 关闭
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.hidden) {
                e.preventDefault();
                omniCloseModal();
            }
        });
    }

    function init() {
        wireTabs();
        wireStatus();
        wireInterfaces();
        wireConfig();
        wireOnboarding();
        wireGlobalActions();
        wireOmni();
        wireOmniModal();

        if (document.getElementById('auto-refresh').checked) startAutoRefresh();
        const initialActivePanel = document.querySelector('.panel.active')?.id;
        if (initialActivePanel === 'status') startUpdateClock();
        refreshAll();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
