// Prodpai Cloud dashboard. Sections: helpers, data shaping, loading, rendering, diagram, interactions.

const CATEGORY_ORDER = ['Compute', 'Network', 'Storage', 'Security', 'Database', 'Other'];
const HEALTH = ['healthy', 'warning', 'critical', 'inactive', 'unknown'];
const RUNNING = ['running', 'in-service', 'inservice'];
const EVENT_LIMIT = 6;
const EVENT_LINES = 4;
const TAGS_SHOWN = 1;
const SPARK_POINTS = 20;

// How each server state is presented. Unknown states fall back to "waiting".
const STATE_UI = {
    live: { icon: 'ph-info' },
    waiting: { icon: 'ph-hourglass' },
    writing: { icon: 'ph-arrows-clockwise', spin: true },
    invalid: { icon: 'ph-warning', warn: true },
    schema: { icon: 'ph-warning', warn: true }
};
const SOURCE_LABEL = { live: 'Live file', stale: 'Previous data', mock: 'Sample data', none: 'No data yet' };

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const isDark = () => cssVar('--scheme') === 'dark';
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

const state = {
    data: null, version: null, busy: false, again: false,
    q: '', category: 'All', status: 'all', tag: null,
    sortKey: 'category', sortDir: 1, chart: null, diagramRun: 0
};

// ---------- data shaping ----------
// The JSON shape can change between Terraform versions or edits to outputs.tf.
// Everything the renderers touch is coerced here, so a missing or mistyped field cannot break the page.
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const list = (v) => (Array.isArray(v) ? v : []);
const text = (v, fallback = '') => (v === null || v === undefined || typeof v === 'object' ? fallback : String(v));
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);

function sanitize(input) {
    const d = obj(input);
    const resources = list(d.resources)
        .filter((r) => r && typeof r === 'object' && !Array.isArray(r))
        .map((r) => ({
            id: text(r.id, 'unknown'),
            name: text(r.name, text(r.id, 'unnamed')),
            type: text(r.type, 'unknown'),
            category: text(r.category, 'Other'),
            provider: text(r.provider, 'aws'),
            status: text(r.status, 'unknown'),
            health: HEALTH.includes(r.health) ? r.health : 'unknown',
            region: text(r.region), az: text(r.az), endpoint: text(r.endpoint), spec: text(r.spec),
            ips: list(r.ips).map((ip) => text(ip)).filter(Boolean),
            tags: Object.fromEntries(Object.entries(obj(r.tags)).map(([k, v]) => [k, text(v)])),
            monthly_cost: num(r.monthly_cost)
        }));
    const count = (key) => resources.reduce((acc, r) => { acc[r[key]] = (acc[r[key]] || 0) + 1; return acc; }, {});
    const health = Object.fromEntries(HEALTH.map((h) => [h, 0]));
    resources.forEach((r) => { health[r.health] += 1; });
    const fallbackStatus = !resources.length ? 'No data' : health.critical ? 'Critical' : health.warning ? 'Warning' : 'Healthy';
    const meta = obj(d.meta);
    return {
        state: STATE_UI[d.state] ? d.state : 'waiting',
        source: SOURCE_LABEL[d.source] ? d.source : 'none',
        version: text(d.version, String(Date.now())),
        message: text(d.message), detail: text(d.detail),
        file: text(d.file),
        update_time: text(d.update_time),
        meta: {
            project: text(meta.project), environment: text(meta.environment),
            region: text(meta.region), workspace: text(meta.workspace)
        },
        // Counts are derived from the rows themselves, so the summary always agrees with the table.
        summary: {
            total: resources.length, by_category: count('category'), by_type: count('type'), health,
            attention: health.warning + health.critical,
            status: text(obj(d.summary).status, fallbackStatus),
            monthly_cost: resources.reduce((sum, r) => sum + r.monthly_cost, 0)
        },
        resources,
        history: list(d.history).filter((e) => e && typeof e === 'object').map((e) => ({
            time: text(e.time), total: num(e.total), monthly_cost: num(e.monthly_cost), attention: num(e.attention),
            initial: Boolean(e.initial),
            added: list(e.added).map((n) => text(n)), removed: list(e.removed).map((n) => text(n)),
            changed: list(e.changed).map((c) => ({ name: text(obj(c).name), from: text(obj(c).from), to: text(obj(c).to) }))
        })),
        raw: d.raw === undefined ? null : d.raw
    };
}

function categories() {
    return Object.keys(state.data.summary.by_category).sort((a, b) => {
        const ia = CATEGORY_ORDER.indexOf(a), ib = CATEGORY_ORDER.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
    });
}
function categoryToken(name) {
    const i = CATEGORY_ORDER.indexOf(name);
    const slot = i >= 0 ? i : categories().indexOf(name);
    return `--c${(slot % 6) + 1}`;
}

// ---------- loading ----------
async function load(manual = false) {
    if (state.busy) { state.again = true; return; }
    state.busy = true;
    $('refresh-icon').classList.add('spin');
    try {
        const headers = state.version && !manual ? { 'If-None-Match': state.version } : {};
        const res = await fetch('/api/data', { headers, cache: 'no-store' });
        if (res.status !== 304) {
            if (!res.ok) throw new Error(`The server responded with status ${res.status}.`);
            const data = sanitize(await res.json());
            const changed = state.version !== null && data.version !== state.version;
            state.data = data;
            state.version = data.version;
            render();
            if (changed) {
                $('live').textContent = data.state === 'live'
                    ? 'Infrastructure data changed. The dashboard was updated.'
                    : (data.message || 'Waiting for Terraform state.');
            }
        }
        $('error').hidden = true;
        $('checked').textContent = `Data loaded at ${new Date().toLocaleTimeString()}. It reloads only when the server reports a new terraform apply.`;
    } catch (err) {
        const detail = err instanceof TypeError ? 'The dashboard server is not reachable.'
            : err instanceof SyntaxError ? 'The server sent a response that is not valid JSON.' : err.message;
        $('error-text').textContent = `Could not load infrastructure data. ${detail}${state.data ? ' Showing the last data received.' : ''}`;
        $('error').hidden = false;
    } finally {
        state.busy = false;
        $('refresh-icon').classList.remove('spin');
        if (state.again) { state.again = false; load(false); }
    }
}

// The server pushes a "change" event once the Terraform output file has been completely rewritten,
// so the page does not poll. Browsers without EventSource fall back to a slow conditional poll.
function setConnection(live) {
    $('conn-dot').className = `dot ${live ? 'dot--healthy dot--pulse' : 'dot--warning'}`;
    $('conn-text').textContent = live ? 'Watching for apply' : 'Reconnecting';
}
function connect() {
    if (!window.EventSource) {
        $('conn-text').textContent = 'Checking every 5s';
        setInterval(() => load(false), 5000);
        return;
    }
    const source = new EventSource('/api/events');
    source.onopen = () => { setConnection(true); load(false); };
    source.addEventListener('change', () => load(false));
    source.onerror = () => setConnection(false);
}

// ---------- rendering ----------
// Each section renders on its own, so a failure in one cannot blank the rest of the page.
function render() {
    const sections = {
        header: renderHeader, metrics: renderMetrics, chart: renderChart, types: renderTypeBars,
        events: renderEvents, filters: renderFilters, table: renderTable, diagram: renderDiagram, raw: renderRaw
    };
    for (const [name, draw] of Object.entries(sections)) {
        try { draw(); } catch (err) { console.error(`Section "${name}" failed to render:`, err); }
    }
}

function renderHeader() {
    const d = state.data;
    $('env-tag').textContent = d.meta.project || d.meta.workspace || 'prodpai_cloud';
    $('update-time').textContent = d.source === 'mock' ? 'waiting for first apply' : (d.update_time || 'not applied yet');
    $('source-badge').textContent = SOURCE_LABEL[d.source];
    $('source-badge').hidden = d.source === 'live';

    const ui = STATE_UI[d.state];
    $('notice').classList.toggle('banner--warn', Boolean(ui.warn));
    $('notice-icon').className = `ph ${ui.icon}${ui.spin && !reducedMotion() ? ' spin' : ''}`;
    $('notice-title').textContent = d.message;
    $('notice-text').textContent = d.detail;
    $('notice').hidden = !d.message && !d.detail;
}

function sparkline(values) {
    if (values.length < 2) return '';
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const points = values.map((v, i) =>
        `${((i / (values.length - 1)) * 100).toFixed(1)},${(22 - ((v - min) / span) * 20).toFixed(1)}`).join(' ');
    return `<svg class="spark" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">
        <polyline points="${points}" fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>
    </svg>`;
}

function setDelta(id, diff, label, badWhenUp = false) {
    const el = $(id);
    el.className = 'metric__note';
    if (diff === null) { el.textContent = 'No earlier apply to compare'; return; }
    if (diff === 0) { el.textContent = 'No change since last apply'; return; }
    const tone = !badWhenUp ? 'delta' : diff > 0 ? 'delta--up-bad' : 'delta--down-good';
    el.innerHTML = `<span class="mono ${tone}">${esc(label)}</span> since last apply`;
}

function renderMetrics() {
    const s = state.data.summary, h = state.data.history;
    const prev = h.length >= 2 ? h[h.length - 2] : null;
    const recent = h.slice(-SPARK_POINTS);
    const signed = (n) => `${n > 0 ? '+' : ''}${n}`;

    $('sum-total').textContent = s.total;
    $('spark-total').innerHTML = sparkline(recent.map((e) => e.total));
    const dTotal = prev ? s.total - prev.total : null;
    setDelta('delta-total', dTotal, dTotal === null ? '' : signed(dTotal));

    $('sum-attention').textContent = s.attention;
    $('sum-attention').className = `metric__value mono ${s.health.critical ? 'tone-critical' : s.attention ? 'tone-warning' : ''}`;
    const flagged = state.data.resources.filter((r) => r.health === 'warning' || r.health === 'critical');
    if (flagged.length) {
        $('delta-attention').className = 'metric__note mono';
        $('delta-attention').textContent = flagged.map((r) => `${r.name} ${r.status}`).join(', ');
        $('delta-attention').title = $('delta-attention').textContent;
    } else {
        const dAttention = prev ? s.attention - prev.attention : null;
        $('delta-attention').title = '';
        if (dAttention) setDelta('delta-attention', dAttention, signed(dAttention), true);
        else { $('delta-attention').className = 'metric__note'; $('delta-attention').textContent = 'No warning or critical resources'; }
    }

    $('sum-cost').textContent = money.format(s.monthly_cost);
    $('spark-cost').innerHTML = sparkline(recent.map((e) => e.monthly_cost));
    if (prev && prev.monthly_cost > 0) {
        const pct = ((s.monthly_cost - prev.monthly_cost) / prev.monthly_cost) * 100;
        const rounded = Math.round(pct * 10) / 10;
        setDelta('delta-cost', rounded, `${rounded > 0 ? '+' : ''}${rounded}%`);
    } else {
        $('delta-cost').className = 'metric__note';
        $('delta-cost').textContent = 'Rough on-demand estimate, USD';
    }

    const tone = { Healthy: 'healthy', Warning: 'warning', Critical: 'critical' }[s.status] || 'idle';
    const pulse = tone === 'healthy' && state.data.source === 'live' ? ' dot--pulse' : '';
    $('sum-status').innerHTML = `<span class="dot dot--${tone}${pulse}"></span><span class="tone-${tone}">${esc(s.status)}</span>`;
    const parts = [`${s.health.healthy} healthy`];
    if (s.health.warning) parts.push(`${s.health.warning} warning`);
    if (s.health.critical) parts.push(`${s.health.critical} critical`);
    if (s.health.inactive) parts.push(`${s.health.inactive} inactive`);
    if (s.health.unknown) parts.push(`${s.health.unknown} unknown`);
    $('sum-health').textContent = parts.join(', ');
}

function renderChart() {
    const s = state.data.summary, cats = categories();
    $('chart-total').textContent = s.total;
    $('chart-legend').innerHTML = cats.length
        ? cats.map((c) => `
            <li><button type="button" class="legend__item" data-category="${esc(c)}" aria-pressed="${c === state.category}">
                <span class="legend__swatch" style="background:var(${categoryToken(c)})"></span>
                <span>${esc(c)}</span>
                <span class="mono">${s.by_category[c]}</span>
                <span class="legend__pct mono">${Math.round((s.by_category[c] / s.total) * 100)}%</span>
            </button></li>`).join('')
        : '<li class="muted">No resources reported.</li>';

    if (!window.Chart) { $('chart-fallback').hidden = false; return; }
    if (state.chart) state.chart.destroy();
    state.chart = new Chart($('category-chart'), {
        type: 'doughnut',
        data: {
            labels: cats,
            datasets: [{
                data: cats.map((c) => s.by_category[c]),
                backgroundColor: cats.map((c) => cssVar(categoryToken(c))),
                borderColor: cssVar('--surface'), borderWidth: 2, hoverOffset: 0
            }]
        },
        options: {
            cutout: '72%', animation: reducedMotion() ? false : { duration: 300 },
            plugins: { legend: { display: false }, tooltip: { displayColors: false } },
            onClick: (_evt, items) => { if (items.length) setCategory(cats[items[0].index]); }
        }
    });
}

function renderTypeBars() {
    const entries = Object.entries(state.data.summary.by_type).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    const max = entries.length ? entries[0][1] : 1;
    $('type-bars').innerHTML = entries.length
        ? entries.map(([type, n]) => `
            <div class="bar">
                <span class="bar__name mono" title="${esc(type)}">${esc(type)}</span>
                <span><span class="bar__fill" style="display:block;width:${Math.max((n / max) * 100, 2)}%"></span></span>
                <span class="bar__count mono">${n}</span>
            </div>`).join('')
        : '<p class="muted">No resources reported.</p>';
}

function renderEvents() {
    const entries = state.data.history.slice(-EVENT_LIMIT).reverse();
    $('events-count').textContent = state.data.history.length ? `${state.data.history.length} recorded` : '';
    if (!entries.length) {
        $('events').innerHTML = `<li class="event muted">${state.data.source === 'mock'
            ? 'Change history starts with the first real terraform apply.'
            : 'No changes recorded yet.'}</li>`;
        return;
    }
    $('events').innerHTML = entries.map((e) => {
        const lines = [
            ...e.added.map((n) => `<span><span class="sign-add">+</span> ${esc(n)}</span>`),
            ...e.removed.map((n) => `<span><span class="sign-remove">-</span> ${esc(n)}</span>`),
            ...e.changed.map((c) => `<span><span class="sign-change">~</span> ${esc(c.name)} ${esc(c.from)} &rarr; ${esc(c.to)}</span>`)
        ];
        const summary = e.initial
            ? `Baseline, ${plural(e.total, 'resource')}`
            : [e.added.length && `${e.added.length} added`, e.removed.length && `${e.removed.length} removed`,
                e.changed.length && `${e.changed.length} status changed`].filter(Boolean).join(', ') || 'Configuration changed';
        const shown = e.initial ? [] : lines.slice(0, EVENT_LINES);
        const more = e.initial ? 0 : lines.length - shown.length;
        return `<li class="event">
            <div class="event__head"><span>${esc(summary)}</span><span class="event__time mono">${esc(e.time)}</span></div>
            ${shown.length ? `<div class="event__lines mono">${shown.join('')}${more > 0 ? `<span class="muted">and ${more} more</span>` : ''}</div>` : ''}
        </li>`;
    }).join('');
}

function renderFilters() {
    const cats = ['All', ...categories()];
    if (!cats.includes(state.category)) state.category = 'All';
    $('category-chips').innerHTML = cats.map((c) =>
        `<button type="button" class="segment" data-category="${esc(c)}" aria-pressed="${c === state.category}">${esc(c)}</button>`).join('');
    document.querySelectorAll('.legend__item').forEach((el) => el.setAttribute('aria-pressed', String(el.dataset.category === state.category)));
    $('tag-filter').innerHTML = state.tag
        ? `<button type="button" id="tag-clear" class="tag tag--active mono">${esc(state.tag)}<i class="ph ph-x" aria-hidden="true"></i><span class="sr-only">Remove tag filter</span></button>`
        : '';
}

const tagList = (r) => Object.entries(r.tags).map(([k, v]) => `${k}=${v}`);

function haystack(r) {
    return [r.name, r.id, r.type, r.category, r.provider, r.status, r.region, r.az, r.endpoint, r.spec,
        ...r.ips, ...tagList(r)].join(' ').toLowerCase();
}

function visibleRows() {
    const q = state.q.trim().toLowerCase();
    const rows = state.data.resources.filter((r) =>
        (state.category === 'All' || r.category === state.category) &&
        (state.status === 'all' || r.health === state.status) &&
        (!state.tag || tagList(r).includes(state.tag)) &&
        (!q || haystack(r).includes(q)));
    const key = state.sortKey;
    return rows.sort((a, b) => {
        const av = a[key], bv = b[key];
        const cmp = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv));
        return (cmp || a.name.localeCompare(b.name)) * state.sortDir;
    });
}

function rowHtml(r) {
    const addr = [...r.ips, r.endpoint].filter(Boolean);
    const tags = tagList(r);
    const chips = tags.slice(0, TAGS_SHOWN).map((t) => `<button type="button" class="tag mono" data-tag="${esc(t)}">${esc(t)}</button>`).join('');
    const more = tags.length > TAGS_SHOWN ? `<span class="muted mono" title="${esc(tags.slice(TAGS_SHOWN).join(', '))}">+${tags.length - TAGS_SHOWN}</span>` : '';
    const zone = r.az || (r.region && r.region !== 'unknown' ? r.region : '');
    const pulse = r.health === 'healthy' && RUNNING.includes(r.status) ? ' dot--pulse' : '';
    return `
        <tr>
            <td>
                <div class="cell-main">${esc(r.name)}</div>
                <div class="cell-sub mono" title="${esc(r.id)}">${esc(r.id)}</div>
            </td>
            <td>
                <div class="cell-mono mono">${esc(r.type)}</div>
                <div class="cell-sub" title="${esc(r.spec)}">${esc(r.spec || r.category)}</div>
            </td>
            <td><span class="status"><span class="dot dot--${r.health}${pulse}"></span>${esc(r.status)}</span></td>
            <td class="col-md cell-mono cell-addr mono">
                <div>${zone ? esc(zone) : '<span class="muted">n/a</span>'}</div>
                ${addr.map((a) => `<div class="muted" title="${esc(a)}">${esc(a)}</div>`).join('')}
            </td>
            <td class="col-lg"><div class="tags">${chips}${more}${tags.length ? '' : '<span class="muted">none</span>'}</div></td>
            <td class="cell-mono mono">${r.monthly_cost ? money.format(r.monthly_cost) : '<span class="muted">$0.00</span>'}</td>
        </tr>`;
}

function renderTable() {
    const all = state.data.resources, rows = visibleRows();
    $('rows').innerHTML = rows.map(rowHtml).join('');
    $('result-count').textContent = `${rows.length} of ${all.length}`;
    const empty = rows.length === 0;
    $('empty').hidden = !empty;
    if (empty) {
        const noData = all.length === 0;
        $('empty-title').textContent = noData ? (state.data.message || 'Waiting for Terraform state') : 'No resources match these filters';
        $('empty-hint').textContent = noData
            ? 'Resources appear here by themselves after the next terraform apply writes its output file.'
            : 'Try a different search term, category, status or tag.';
        $('clear-btn').hidden = noData;
    }
    document.querySelectorAll('th').forEach((th) => {
        const btn = th.querySelector('[data-sort]');
        if (!btn) return;
        th.setAttribute('aria-sort', btn.dataset.sort === state.sortKey ? (state.sortDir > 0 ? 'ascending' : 'descending') : 'none');
    });
}

function renderRaw() {
    const raw = state.data.raw;
    $('raw-file').textContent = state.data.source === 'mock' ? 'sample' : state.data.file;
    $('json-log').textContent = raw === null ? 'Waiting for Terraform state' : JSON.stringify(raw, null, 2);
}

// ---------- architecture diagram ----------
function diagramDefinition(resources) {
    const of = (type) => resources.filter((r) => r.type === type);
    const label = (v) => String(v ?? '').replace(/["`<>{}\[\]()|#;]/g, ' ').trim();
    const lines = ['graph TD', 'NET(["Internet"])'];
    let entry = 'NET';
    const link = (id, shape) => { lines.push(shape, `${entry} --> ${id}`); entry = id; };

    const waf = of('aws_wafv2_web_acl')[0], lb = of('aws_lb')[0], asg = of('aws_autoscaling_group')[0];
    if (waf) link('WAF', `WAF["AWS WAF<br/>${label(waf.name)}"]`);
    if (lb) link('ALB', `ALB["Load balancer<br/>${label(lb.name)}"]`);
    if (asg) link('ASG', `ASG[["Auto Scaling group<br/>${label(asg.name)}<br/>${label(asg.spec)}"]]`);

    const instances = of('aws_instance'), vpc = of('aws_vpc')[0];
    if (instances.length) {
        lines.push(`subgraph VPC["VPC ${label(vpc ? vpc.id : '')}"]`);
        instances.forEach((r, i) => lines.push(`EC2_${i}["${label(r.name)}<br/>${label(r.id)}<br/>${label(r.status)}"]`));
        lines.push('end');
        instances.forEach((r, i) => { if (r.health !== 'inactive') lines.push(`${entry} --> EC2_${i}`); });
    }
    const lambdas = of('aws_lambda_function');
    if (lambdas.length) {
        lines.push('CW(("CloudWatch"))');
        lambdas.forEach((r, i) => lines.push(`FN_${i}["Lambda<br/>${label(r.name)}"]`, `CW -.->|Alert| FN_${i}`));
    }
    const buckets = of('aws_s3_bucket');
    if (buckets.length) {
        lines.push('subgraph S3["S3 buckets"]');
        buckets.forEach((r, i) => lines.push(`BKT_${i}[("${label(r.name)}")]`));
        lines.push('end');
    }
    return lines.join('\n');
}

let mermaidModule = null;
async function renderDiagram() {
    const el = $('diagram'), run = ++state.diagramRun;
    if (!state.data.resources.length) { el.textContent = 'Waiting for Terraform state. Nothing to draw yet.'; return; }
    try {
        if (!mermaidModule) {
            mermaidModule = (await import('https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs')).default;
        }
        // Mermaid's "base" theme takes its colors from the same tokens as the rest of the page.
        mermaidModule.initialize({
            startOnLoad: false, securityLevel: 'strict', theme: 'base',
            themeVariables: {
                darkMode: isDark(), fontFamily: cssVar('--font-sans'), fontSize: '12px',
                background: cssVar('--surface'), primaryColor: cssVar('--subtle'), primaryTextColor: cssVar('--text'),
                primaryBorderColor: cssVar('--border-strong'), lineColor: cssVar('--text-3'),
                secondaryColor: cssVar('--subtle'), tertiaryColor: cssVar('--surface'),
                clusterBkg: cssVar('--surface'), clusterBorder: cssVar('--border'), edgeLabelBackground: cssVar('--surface')
            }
        });
        const { svg } = await mermaidModule.render(`diagram-svg-${run}`, diagramDefinition(state.data.resources));
        if (run === state.diagramRun) el.innerHTML = svg;
    } catch (err) {
        console.error('Diagram rendering failed:', err);
        if (run === state.diagramRun) el.textContent = 'The architecture diagram could not be drawn. The resource table is unaffected.';
    }
}

// ---------- interactions ----------
function setCategory(c) {
    state.category = state.category === c && c !== 'All' ? 'All' : c;
    renderFilters();
    renderTable();
}

function start() {
    document.addEventListener('click', (e) => {
        if (!state.data) return;
        const cat = e.target.closest('[data-category]');
        if (cat) return setCategory(cat.dataset.category);
        const tag = e.target.closest('[data-tag]');
        if (tag) { state.tag = tag.dataset.tag; renderFilters(); return renderTable(); }
        if (e.target.closest('#tag-clear')) { state.tag = null; renderFilters(); return renderTable(); }
        const sort = e.target.closest('[data-sort]');
        if (sort) {
            state.sortDir = state.sortKey === sort.dataset.sort ? -state.sortDir : 1;
            state.sortKey = sort.dataset.sort;
            renderTable();
        }
    });
    $('search').addEventListener('input', (e) => { state.q = e.target.value; if (state.data) renderTable(); });
    $('status-filter').addEventListener('change', (e) => { state.status = e.target.value; if (state.data) renderTable(); });
    $('clear-btn').addEventListener('click', () => {
        Object.assign(state, { q: '', category: 'All', status: 'all', tag: null });
        $('search').value = '';
        $('status-filter').value = 'all';
        renderFilters();
        renderTable();
    });
    $('refresh-btn').addEventListener('click', () => load(true));
    $('retry-btn').addEventListener('click', () => load(true));
    $('theme-btn').addEventListener('click', () => {
        const next = isDark() ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem('theme', next); } catch (_) { /* storage unavailable */ }
        if (state.data) { renderChart(); renderDiagram(); }
    });

    load(true);
    connect();
}

start();
