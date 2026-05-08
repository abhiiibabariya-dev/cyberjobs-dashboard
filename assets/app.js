(() => {
  'use strict';

  // ─── Constants ─────────────────────────────────────────────────────
  const PAGE_SIZE = 50;
  const STALE_DAYS = 7;
  const NS = 'cyberjobs:v1:';
  const STAGES = ['applied', 'interviewing', 'offer', 'rejected'];
  const KANBAN_COLS = ['saved', 'applied', 'interviewing', 'offer', 'rejected'];

  // ─── Storage ───────────────────────────────────────────────────────
  const Storage = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(NS + key);
        return raw == null ? fallback : JSON.parse(raw);
      } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(NS + key, JSON.stringify(value)); }
      catch { /* quota or disabled */ }
    },
    remove(key) {
      try { localStorage.removeItem(NS + key); } catch {}
    },
  };

  // ─── In-memory state ───────────────────────────────────────────────
  const state = {
    jobs: [],
    filtered: [],
    visible: PAGE_SIZE,
    statsBlock: null,
    query: '',
    sort: 'recent',
    onlyNew: false,
    filters: { date: 'all', level: 'all', locType: 'all', platform: 'all' },
    saved: Storage.get('saved', {}),
    pipeline: Storage.get('pipeline', {}),       // { jobId: { stage, ts, notes } }
    hidden: Storage.get('hidden', {}),
    resume: Storage.get('resume', ''),
    resumeScores: Storage.get('resumeScores', {}),
    lastVisit: Storage.get('lastVisit', null),
    density: Storage.get('density', 'comfortable'),
    view: 'list',
    focusIndex: -1,
  };

  // ─── DOM ───────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const els = {
    statJobs: $('statJobs'),
    statCompanies: $('statCompanies'),
    statSaved: $('statSaved'),
    statApplied: $('statApplied'),
    statUpdated: $('statUpdated'),
    staleBanner: $('staleBanner'),
    diffBanner: $('diffBanner'),
    search: $('search'),
    sort: $('sort'),
    count: $('count'),
    jobs: $('jobs'),
    empty: $('empty'),
    loadmoreWrap: $('loadmoreWrap'),
    loadmore: $('loadmore'),
    toggleNew: $('toggleNew'),
    chipBar: $('chipBar'),
    platformMenu: $('platformMenu'),
    clearFilters: $('clearFilters'),
    density: $('density'),
    exportCsv: $('exportCsv'),
    navList: $('navList'),
    navPipeline: $('navPipeline'),
    pipelineCount: $('pipelineCount'),
    viewList: $('viewList'),
    viewPipeline: $('viewPipeline'),
    kanban: $('kanban'),
    helpDialog: $('helpDialog'),
    openHelp: $('openHelp'),
    resumeText: $('resumeText'),
    scoreResume: $('scoreResume'),
    clearResume: $('clearResume'),
    resumeStatus: $('resumeStatus'),
  };

  // ─── Helpers ───────────────────────────────────────────────────────
  const fmtNum = (n) => new Intl.NumberFormat('en-US').format(n);

  function parseDate(s) {
    if (!s) return null;
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  function fmtRelative(date) {
    if (!date) return '—';
    const sec = Math.floor((Date.now() - date.getTime()) / 1000);
    if (sec < 0) return 'just now';
    const min = Math.floor(sec / 60), hr = Math.floor(min / 60), day = Math.floor(hr / 24);
    if (day < 1) return hr < 1 ? `${min}m ago` : `${hr}h ago`;
    if (day < 30) return `${day}d ago`;
    const mo = Math.floor(day / 30);
    return mo < 12 ? `${mo}mo ago` : `${Math.floor(day / 365)}y ago`;
  }

  function fmtAbs(date) {
    if (!date) return '';
    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function isSafeUrl(u) {
    if (!u || typeof u !== 'string') return false;
    try {
      const p = new URL(u, window.location.href);
      return p.protocol === 'http:' || p.protocol === 'https:';
    } catch { return false; }
  }

  function jobId(j) {
    return [(j.platform || '?'), (j.company || '?'), (j.title || '?')]
      .map((s) => String(s).toLowerCase().trim())
      .join('::');
  }

  // ─── Job enrichment (derived fields) ───────────────────────────────
  const LEVEL_PATTERNS = [
    { level: 'lead',   re: /\b(lead|principal|head|director|manager|chief|architect)\b/i },
    { level: 'senior', re: /\b(senior|sr\.?|staff|l3|level\s*3|iii)\b/i },
    { level: 'entry',  re: /\b(intern|trainee|fresher|graduate|entry|junior|jr\.?|l1|level\s*1|i\b)\b/i },
    { level: 'mid',    re: /\b(l2|level\s*2|associate|ii\b|mid)\b/i },
  ];

  function detectLevel(title) {
    const t = String(title || '');
    for (const p of LEVEL_PATTERNS) if (p.re.test(t)) return p.level;
    return 'mid'; // sensible default for unspecified titles
  }

  function detectRemote(j) {
    const blob = `${j.location || ''} ${j.title || ''}`.toLowerCase();
    return /\b(remote|work from home|wfh|anywhere)\b/.test(blob);
  }

  function tokenize(text) {
    return String(text || '')
      .toLowerCase()
      .replace(/[^a-z0-9+#./-]+/g, ' ')
      .split(/\s+/)
      .filter((t) => t.length > 1);
  }

  function enrichJob(j) {
    const posted = parseDate(j.posted_date);
    const found = parseDate(j.found_at);
    return {
      ...j,
      _id: jobId(j),
      _ts: (posted || found || new Date(0)).getTime(),
      _postedDate: posted,
      _foundDate: found,
      _level: detectLevel(j.title),
      _isRemote: detectRemote(j),
      _searchHay: [
        j.title || '', j.company || '', j.location || '', j.platform || ''
      ].join(' ').toLowerCase(),
      _tokens: new Set(tokenize(`${j.title} ${j.company}`)),
    };
  }

  // ─── Boolean search parser ─────────────────────────────────────────
  function buildMatcher(q) {
    q = String(q || '').trim().toLowerCase();
    if (!q) return () => true;

    const tokens = [];
    const re = /"([^"]+)"|(\S+)/g;
    let m;
    while ((m = re.exec(q)) !== null) tokens.push(m[1] || m[2]);

    // Split by OR into AND-groups
    const orGroups = [[]];
    for (const t of tokens) {
      if (t === 'or') orGroups.push([]);
      else orGroups[orGroups.length - 1].push(t);
    }

    return (hay) => orGroups.some((group) => group.every((t) => {
      if (t.length > 1 && t.startsWith('-')) return !hay.includes(t.slice(1));
      return hay.includes(t);
    }));
  }

  // ─── Filtering & sorting ───────────────────────────────────────────
  function passesFilters(j) {
    if (state.hidden[j._id]) return false;
    if (state.onlyNew && !j.is_new) return false;

    const f = state.filters;
    if (f.date !== 'all') {
      const days = parseInt(f.date, 10);
      const ageMs = Date.now() - j._ts;
      if (j._ts === 0 || ageMs > days * 86400000) return false;
    }
    if (f.level !== 'all' && j._level !== f.level) return false;
    if (f.locType === 'remote' && !j._isRemote) return false;
    if (f.locType === 'onsite' && j._isRemote) return false;
    if (f.platform !== 'all' && (j.platform || '?') !== f.platform) return false;

    return true;
  }

  function applyFilter() {
    const matcher = buildMatcher(state.query);
    const filtered = state.jobs.filter((j) => passesFilters(j) && matcher(j._searchHay));

    const cmp = {
      recent: (a, b) => b._ts - a._ts,
      oldest: (a, b) => a._ts - b._ts,
      company: (a, b) => (a.company || '').localeCompare(b.company || ''),
      match: (a, b) => (state.resumeScores[b._id] || 0) - (state.resumeScores[a._id] || 0),
    }[state.sort] || ((a, b) => b._ts - a._ts);

    state.filtered = filtered.slice().sort(cmp);
    state.visible = PAGE_SIZE;
    state.focusIndex = -1;
    writeUrlState();
    renderList();
    updateClearButton();
  }

  // ─── URL hash state ────────────────────────────────────────────────
  function readUrlState() {
    const params = new URLSearchParams(window.location.hash.slice(1));
    if (params.has('q')) state.query = params.get('q');
    if (params.has('sort')) state.sort = params.get('sort');
    if (params.has('new')) state.onlyNew = params.get('new') === '1';
    if (params.has('view')) state.view = params.get('view');
    for (const k of ['date', 'level', 'locType', 'platform']) {
      if (params.has(k)) state.filters[k] = params.get(k);
    }
  }

  function writeUrlState() {
    const params = new URLSearchParams();
    if (state.query) params.set('q', state.query);
    if (state.sort && state.sort !== 'recent') params.set('sort', state.sort);
    if (state.onlyNew) params.set('new', '1');
    if (state.view !== 'list') params.set('view', state.view);
    for (const k of ['date', 'level', 'locType', 'platform']) {
      if (state.filters[k] !== 'all') params.set(k, state.filters[k]);
    }
    const h = params.toString();
    const newHash = h ? '#' + h : '';
    if (window.location.hash !== newHash) {
      history.replaceState(null, '', window.location.pathname + window.location.search + newHash);
    }
  }

  // ─── Job state (save / pipeline / hide) ────────────────────────────
  function isSaved(id) { return !!state.saved[id]; }
  function pipelineStage(id) { return state.pipeline[id]?.stage || null; }
  function isHidden(id) { return !!state.hidden[id]; }

  function toggleSaved(id) {
    if (state.saved[id]) delete state.saved[id];
    else state.saved[id] = { ts: Date.now() };
    Storage.set('saved', state.saved);
    updateStatBar();
    renderJobChrome(id);
    renderKanbanIfActive();
  }

  function setPipeline(id, stage) {
    if (!stage || !STAGES.includes(stage)) {
      delete state.pipeline[id];
    } else {
      state.pipeline[id] = { stage, ts: Date.now(), ...state.pipeline[id], stage };
    }
    Storage.set('pipeline', state.pipeline);
    updateStatBar();
    renderJobChrome(id);
    renderKanbanIfActive();
  }

  function cyclePipeline(id) {
    const cur = pipelineStage(id);
    const order = ['applied', 'interviewing', 'offer'];
    const idx = order.indexOf(cur);
    const next = idx === -1 ? 'applied' : (idx >= order.length - 1 ? null : order[idx + 1]);
    setPipeline(id, next);
  }

  function toggleHidden(id) {
    if (state.hidden[id]) delete state.hidden[id];
    else state.hidden[id] = { ts: Date.now() };
    Storage.set('hidden', state.hidden);
    applyFilter();
  }

  function updateStatBar() {
    els.statSaved.textContent = fmtNum(Object.keys(state.saved).length);
    els.statApplied.textContent = fmtNum(Object.keys(state.pipeline).length);
    els.pipelineCount.textContent = fmtNum(Object.keys(state.pipeline).length + Object.keys(state.saved).length);
  }

  // ─── Resume scoring ────────────────────────────────────────────────
  const STOPWORDS = new Set(('a,an,the,and,or,of,in,to,for,with,on,at,by,from,as,is,are,be,been,being,was,were,it,this,that,these,those,we,you,i,he,she,they,them,his,her,their,my,our,your,but,if,then,so,not,no,do,does,did,have,has,had,will,would,can,could,should,may,might,about,into,through,during,before,after,above,below,up,down,out,over,under,again,further,here,there').split(','));

  function resumeKeywords(text) {
    const tokens = tokenize(text).filter((t) => !STOPWORDS.has(t));
    const counts = new Map();
    for (const t of tokens) counts.set(t, (counts.get(t) || 0) + 1);
    return counts;
  }

  function scoreAgainstResume(resumeMap, jobTokens) {
    if (resumeMap.size === 0 || jobTokens.size === 0) return 0;
    let hit = 0, total = 0;
    for (const [word, count] of resumeMap) {
      total += count;
      if (jobTokens.has(word)) hit += count;
    }
    return total === 0 ? 0 : Math.min(100, Math.round((hit / total) * 200));
  }

  function rescoreResume() {
    const text = state.resume;
    if (!text || text.trim().length < 20) {
      state.resumeScores = {};
      Storage.remove('resumeScores');
      els.resumeStatus.textContent = '';
      return;
    }
    const map = resumeKeywords(text);
    const scores = {};
    for (const j of state.jobs) {
      scores[j._id] = scoreAgainstResume(map, j._tokens);
    }
    state.resumeScores = scores;
    Storage.set('resumeScores', scores);
    const max = Math.max(...Object.values(scores), 0);
    const matched = Object.values(scores).filter((s) => s > 0).length;
    els.resumeStatus.textContent = `Scored ${fmtNum(state.jobs.length)} roles · ${fmtNum(matched)} with overlap · top score ${max}%`;
  }

  // ─── List rendering ────────────────────────────────────────────────
  function renderList() {
    const total = state.filtered.length;
    if (total === 0) {
      els.jobs.innerHTML = '';
      els.empty.hidden = false;
      els.loadmoreWrap.hidden = true;
      els.count.textContent = `0 of ${fmtNum(state.jobs.length)}`;
      return;
    }
    els.empty.hidden = true;
    const slice = state.filtered.slice(0, state.visible);
    els.count.textContent = `${fmtNum(slice.length)} of ${fmtNum(total)}`;

    els.jobs.innerHTML = slice.map((j, idx) => renderJobItem(j, idx)).join('');
    els.loadmoreWrap.hidden = state.visible >= total;
  }

  function renderJobItem(j, idx) {
    const url = isSafeUrl(j.url) ? j.url : null;
    const id = j._id;
    const saved = isSaved(id);
    const stage = pipelineStage(id);
    const newBadge = j.is_new ? '<span class="badge badge--new" title="Found in the latest scan">NEW</span>' : '';
    const sinceLast = state.lastVisit && j._foundDate && j._foundDate.getTime() > new Date(state.lastVisit).getTime()
      ? '<span class="badge badge--diff" title="Added since your last visit">↑</span>' : '';
    const score = state.resumeScores[id];
    const scoreBadge = (score && score > 0) ? `<span class="badge badge--score" title="Resume match">${score}%</span>` : '';

    const titleText = escapeHtml(j.title || 'Untitled role');
    const titleInner = url
      ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${titleText}</a>`
      : titleText;

    const tags = [];
    if (j.platform) tags.push(`<span class="job__tag">${escapeHtml(j.platform)}</span>`);
    if (j._level && j._level !== 'mid') tags.push(`<span class="job__tag job__tag--level" data-level="${j._level}">${j._level}</span>`);
    if (j._isRemote) tags.push('<span class="job__tag job__tag--remote">remote</span>');

    const sep = '<span class="job__sep" aria-hidden="true">•</span>';
    const lineParts = [
      `<span class="job__company">${escapeHtml(j.company || 'Unknown company')}</span>`,
      j.location ? sep + `<span class="job__loc">${escapeHtml(j.location)}</span>` : '',
      tags.length ? '<span class="job__tags">' + tags.join('') + '</span>' : '',
    ].filter(Boolean).join(' ');

    const date = j._postedDate || j._foundDate;
    const datePrefix = j._postedDate ? '' : (j._foundDate ? 'found ' : '');
    const dateAttr = date ? ` title="${escapeHtml(datePrefix + fmtAbs(date))}"` : '';
    const dateText = date ? (datePrefix + fmtRelative(date)) : '—';

    return `
      <li class="job${saved ? ' is-saved' : ''}${stage ? ' is-' + stage : ''}${j.is_new ? ' is-new' : ''}"
          data-id="${escapeHtml(id)}" data-idx="${idx}">
        <div class="job__main">
          <h3 class="job__title">${newBadge}${sinceLast}${scoreBadge}${titleInner}</h3>
          <div class="job__line">${lineParts}</div>
          ${stage ? `<div class="job__stage">stage: <strong>${stage}</strong></div>` : ''}
        </div>
        <div class="job__side">
          <span class="job__date"${dateAttr}>${escapeHtml(dateText)}</span>
          <div class="job__actions">
            <button class="iconbtn ${saved ? 'is-active' : ''}" data-action="save" data-id="${escapeHtml(id)}" title="Save (b)">${saved ? '★' : '☆'}</button>
            <button class="iconbtn ${stage ? 'is-active' : ''}" data-action="apply" data-id="${escapeHtml(id)}" title="Application stage (a)">${stage ? stage[0].toUpperCase() : '+'}</button>
            <button class="iconbtn" data-action="hide" data-id="${escapeHtml(id)}" title="Hide (h)">✕</button>
            ${url ? `<a class="job__open" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Open</a>` : ''}
          </div>
        </div>
      </li>`;
  }

  function cssEscape(s) {
    if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => '\\' + c);
  }

  function renderJobChrome(id) {
    // Re-render only the affected list items
    const nodes = els.jobs.querySelectorAll(`.job[data-id="${cssEscape(id)}"]`);
    nodes.forEach((node) => {
      const idx = parseInt(node.dataset.idx, 10);
      const j = state.filtered[idx];
      if (!j) return;
      node.outerHTML = renderJobItem(j, idx);
    });
  }

  // ─── Pipeline / kanban ─────────────────────────────────────────────
  function renderKanbanIfActive() {
    if (state.view === 'pipeline') renderKanban();
  }

  function renderKanban() {
    const cols = KANBAN_COLS.map((stage) => ({ stage, jobs: [] }));
    const colByStage = {};
    cols.forEach((c) => (colByStage[c.stage] = c));

    for (const j of state.jobs) {
      const id = j._id;
      const stage = pipelineStage(id);
      if (stage && colByStage[stage]) colByStage[stage].jobs.push(j);
      else if (isSaved(id) && colByStage.saved) colByStage.saved.jobs.push(j);
    }

    // Sort each column by recency of action
    for (const c of cols) {
      c.jobs.sort((a, b) => {
        const at = (state.pipeline[a._id]?.ts || state.saved[a._id]?.ts || 0);
        const bt = (state.pipeline[b._id]?.ts || state.saved[b._id]?.ts || 0);
        return bt - at;
      });
    }

    els.kanban.innerHTML = cols.map((c) => `
      <section class="kanban-col" data-stage="${c.stage}">
        <header class="kanban-col__head">
          <span class="kanban-col__name">${c.stage}</span>
          <span class="kanban-col__count">${c.jobs.length}</span>
        </header>
        <div class="kanban-col__body">
          ${c.jobs.length === 0
            ? `<p class="kanban-col__empty">No items</p>`
            : c.jobs.map((j) => renderKanbanCard(j, c.stage)).join('')}
        </div>
      </section>
    `).join('');
  }

  function renderKanbanCard(j, currentStage) {
    const url = isSafeUrl(j.url) ? j.url : null;
    const id = j._id;
    const date = j._postedDate || j._foundDate;
    const dateText = date ? fmtRelative(date) : '';

    // Move targets: pipeline stages other than current
    const moveButtons = STAGES
      .filter((s) => s !== currentStage)
      .map((s) => `<button class="kanban-move" data-id="${escapeHtml(id)}" data-stage="${s}" title="Move to ${s}">${s[0].toUpperCase()}</button>`)
      .join('');

    return `
      <article class="kanban-card" data-id="${escapeHtml(id)}">
        <h4 class="kanban-card__title">${url
          ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(j.title)}</a>`
          : escapeHtml(j.title)}</h4>
        <div class="kanban-card__sub">
          <span>${escapeHtml(j.company || '?')}</span>
          ${j.location ? `<span class="muted">· ${escapeHtml(j.location)}</span>` : ''}
        </div>
        <div class="kanban-card__foot">
          <span class="muted">${escapeHtml(dateText)}</span>
          <div class="kanban-card__actions">
            ${moveButtons}
            <button class="kanban-move kanban-move--remove" data-id="${escapeHtml(id)}" data-stage="" title="Remove from pipeline">✕</button>
          </div>
        </div>
      </article>
    `;
  }

  // ─── Filter chips ──────────────────────────────────────────────────
  function buildPlatformChips() {
    const platforms = new Map();
    for (const j of state.jobs) {
      const p = j.platform || '?';
      platforms.set(p, (platforms.get(p) || 0) + 1);
    }
    const entries = Array.from(platforms.entries()).sort((a, b) => b[1] - a[1]);
    els.platformMenu.innerHTML = `
      <button class="chip-opt" data-filter="platform" data-value="all">All platforms</button>
      ${entries.map(([p, n]) => `<button class="chip-opt" data-filter="platform" data-value="${escapeHtml(p)}">${escapeHtml(p)} <span class="chip-opt__count">${n}</span></button>`).join('')}
    `;
  }

  function refreshChipLabels() {
    document.querySelectorAll('.chip-group').forEach((g) => {
      const summary = g.querySelector('.chip');
      const filterKey = g.querySelector('[data-filter]')?.dataset.filter;
      if (!filterKey || !summary) return;
      const cur = state.filters[filterKey];
      const baseLabel = { date: 'Date', level: 'Experience', locType: 'Location', platform: 'Platform' }[filterKey] || filterKey;
      if (cur === 'all') {
        summary.firstChild && (summary.firstChild.textContent = baseLabel + ' ');
        summary.classList.remove('is-active');
      } else {
        summary.firstChild && (summary.firstChild.textContent = `${baseLabel}: ${cur} `);
        summary.classList.add('is-active');
      }
    });
  }

  function updateClearButton() {
    const any = state.query
      || state.onlyNew
      || Object.values(state.filters).some((v) => v !== 'all')
      || state.sort !== 'recent';
    els.clearFilters.hidden = !any;
  }

  // ─── Stats / banners ───────────────────────────────────────────────
  function renderStats() {
    els.statJobs.textContent = fmtNum(state.jobs.length);
    const companies = new Set();
    let newCount = 0;
    for (const j of state.jobs) {
      if (j.company) companies.add(j.company.trim().toLowerCase());
      if (j.is_new) newCount++;
    }
    els.statCompanies.textContent = fmtNum(companies.size);
    updateStatBar();

    const lastScan = parseDate(state.statsBlock?.last_scan);
    els.statUpdated.textContent = lastScan ? fmtRelative(lastScan) : '—';

    if (newCount > 0) {
      els.toggleNew.hidden = false;
      els.toggleNew.querySelector('.toggle-new__count').textContent = fmtNum(newCount);
    }

    if (lastScan) {
      const ageDays = (Date.now() - lastScan.getTime()) / 86400000;
      if (ageDays > STALE_DAYS) {
        const ageText = ageDays > 30
          ? `${Math.floor(ageDays / 30)} month${Math.floor(ageDays / 30) > 1 ? 's' : ''}`
          : `${Math.floor(ageDays)} days`;
        els.staleBanner.textContent = `Snapshot is ${ageText} old. Run the scanner locally and push to refresh.`;
        els.staleBanner.hidden = false;
      }
    }

    if (state.lastVisit) {
      const since = new Date(state.lastVisit);
      const fresh = state.jobs.filter((j) => j._foundDate && j._foundDate > since).length;
      if (fresh > 0) {
        els.diffBanner.innerHTML = `<strong>${fmtNum(fresh)}</strong> roles added since your last visit (${fmtAbs(since)}). <button class="link-btn" id="filterDiff">Show only those</button>`;
        els.diffBanner.hidden = false;
      }
    }
  }

  // ─── CSV export ────────────────────────────────────────────────────
  function exportCsv() {
    const rows = [['Title', 'Company', 'Location', 'Platform', 'Posted', 'Found', 'URL', 'Saved', 'Stage', 'Match%']];
    for (const j of state.filtered) {
      rows.push([
        j.title || '',
        j.company || '',
        j.location || '',
        j.platform || '',
        j.posted_date || '',
        j.found_at || '',
        j.url || '',
        isSaved(j._id) ? 'yes' : '',
        pipelineStage(j._id) || '',
        state.resumeScores[j._id] || '',
      ]);
    }
    const csv = rows.map((r) => r.map((cell) => {
      const s = String(cell ?? '');
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    }).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `cyberjobs-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ─── View routing ──────────────────────────────────────────────────
  function showView(name) {
    state.view = name === 'pipeline' ? 'pipeline' : 'list';
    els.viewList.hidden = state.view !== 'list';
    els.viewPipeline.hidden = state.view !== 'pipeline';
    els.navList.classList.toggle('is-active', state.view === 'list');
    els.navPipeline.classList.toggle('is-active', state.view === 'pipeline');
    if (state.view === 'pipeline') renderKanban();
    writeUrlState();
  }

  // ─── Density ───────────────────────────────────────────────────────
  function applyDensity() {
    document.body.classList.toggle('compact', state.density === 'compact');
    els.density.textContent = state.density === 'compact' ? '▥▥' : '▤▤';
    els.density.title = state.density === 'compact' ? 'Switch to comfortable density' : 'Switch to compact density';
  }

  // ─── Keyboard navigation ───────────────────────────────────────────
  let comboKey = null;
  let comboTimer = null;

  function setFocusIndex(i) {
    state.focusIndex = i;
    document.querySelectorAll('.job.is-focused').forEach((n) => n.classList.remove('is-focused'));
    if (i < 0 || i >= state.filtered.length) return;
    if (i >= state.visible) {
      state.visible = Math.min(state.filtered.length, Math.ceil((i + 1) / PAGE_SIZE) * PAGE_SIZE);
      renderList();
    }
    const node = els.jobs.querySelector(`.job[data-idx="${i}"]`);
    if (node) {
      node.classList.add('is-focused');
      node.scrollIntoView({ block: 'nearest' });
    }
  }

  function focusedJob() {
    return state.focusIndex >= 0 ? state.filtered[state.focusIndex] : null;
  }

  function handleKey(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    const inField = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
    if (e.key === 'Escape') {
      if (els.helpDialog.open) return; // dialog handles close
      if (inField) { t.blur(); return; }
      if (state.query) { state.query = ''; els.search.value = ''; applyFilter(); }
      return;
    }
    if (inField) return;

    if (e.key === '/') { e.preventDefault(); els.search.focus(); els.search.select(); return; }
    if (e.key === '?') { e.preventDefault(); els.helpDialog.showModal(); return; }

    if (e.key === 'g') {
      comboKey = 'g';
      clearTimeout(comboTimer);
      comboTimer = setTimeout(() => (comboKey = null), 800);
      return;
    }
    if (comboKey === 'g') {
      comboKey = null;
      if (e.key === 'l') { e.preventDefault(); showView('list'); return; }
      if (e.key === 'p') { e.preventDefault(); showView('pipeline'); return; }
    }

    if (state.view !== 'list') return;
    if (e.key === 'j') { e.preventDefault(); setFocusIndex(Math.min(state.filtered.length - 1, state.focusIndex + 1)); return; }
    if (e.key === 'k') { e.preventDefault(); setFocusIndex(Math.max(0, state.focusIndex - 1)); return; }

    const j = focusedJob();
    if (!j) return;
    if (e.key === 'Enter' && j.url) { e.preventDefault(); window.open(j.url, '_blank', 'noopener'); return; }
    if (e.key === 'b') { e.preventDefault(); toggleSaved(j._id); return; }
    if (e.key === 'a') { e.preventDefault(); cyclePipeline(j._id); return; }
    if (e.key === 'h') { e.preventDefault(); toggleHidden(j._id); return; }
  }

  // ─── Event wiring ──────────────────────────────────────────────────
  function bindEvents() {
    let searchTimer;
    els.search.addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { state.query = e.target.value; applyFilter(); }, 120);
    });
    els.sort.addEventListener('change', (e) => { state.sort = e.target.value; applyFilter(); });
    els.loadmore.addEventListener('click', () => { state.visible += PAGE_SIZE; renderList(); });

    els.toggleNew.addEventListener('click', () => {
      state.onlyNew = !state.onlyNew;
      els.toggleNew.classList.toggle('is-active', state.onlyNew);
      els.toggleNew.setAttribute('aria-pressed', String(state.onlyNew));
      applyFilter();
    });

    els.chipBar.addEventListener('click', (e) => {
      const opt = e.target.closest('.chip-opt');
      if (!opt) return;
      const f = opt.dataset.filter;
      const v = opt.dataset.value;
      if (!f) return;
      state.filters[f] = v;
      const details = opt.closest('details');
      if (details) details.open = false;
      refreshChipLabels();
      applyFilter();
    });

    els.clearFilters.addEventListener('click', () => {
      state.query = '';
      els.search.value = '';
      state.onlyNew = false;
      els.toggleNew.classList.remove('is-active');
      els.toggleNew.setAttribute('aria-pressed', 'false');
      state.filters = { date: 'all', level: 'all', locType: 'all', platform: 'all' };
      state.sort = 'recent';
      els.sort.value = 'recent';
      refreshChipLabels();
      applyFilter();
    });

    els.density.addEventListener('click', () => {
      state.density = state.density === 'compact' ? 'comfortable' : 'compact';
      Storage.set('density', state.density);
      applyDensity();
    });

    els.exportCsv.addEventListener('click', exportCsv);

    els.jobs.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      if (btn) {
        e.preventDefault();
        const action = btn.dataset.action;
        const id = btn.dataset.id;
        if (action === 'save') toggleSaved(id);
        else if (action === 'apply') cyclePipeline(id);
        else if (action === 'hide') toggleHidden(id);
        return;
      }
      const item = e.target.closest('.job');
      if (item && !e.target.closest('a')) {
        state.focusIndex = parseInt(item.dataset.idx, 10);
        document.querySelectorAll('.job.is-focused').forEach((n) => n.classList.remove('is-focused'));
        item.classList.add('is-focused');
      }
    });

    els.kanban.addEventListener('click', (e) => {
      const btn = e.target.closest('.kanban-move');
      if (!btn) return;
      const id = btn.dataset.id;
      const stage = btn.dataset.stage;
      setPipeline(id, stage || null);
    });

    els.navList.addEventListener('click', () => showView('list'));
    els.navPipeline.addEventListener('click', () => showView('pipeline'));

    els.openHelp.addEventListener('click', () => els.helpDialog.showModal());

    els.scoreResume.addEventListener('click', () => {
      state.resume = els.resumeText.value;
      Storage.set('resume', state.resume);
      rescoreResume();
      if (Object.keys(state.resumeScores).length) {
        state.sort = 'match';
        els.sort.value = 'match';
      }
      applyFilter();
    });
    els.clearResume.addEventListener('click', () => {
      els.resumeText.value = '';
      state.resume = '';
      Storage.remove('resume');
      Storage.remove('resumeScores');
      state.resumeScores = {};
      els.resumeStatus.textContent = '';
      applyFilter();
    });

    els.diffBanner.addEventListener('click', (e) => {
      if (e.target.id === 'filterDiff') {
        state.filters.date = '14';
        refreshChipLabels();
        applyFilter();
      }
    });

    document.addEventListener('keydown', handleKey);
  }

  // ─── Boot ──────────────────────────────────────────────────────────
  async function load() {
    let payload;
    try {
      const res = await fetch('dashboard_jobs.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      payload = await res.json();
    } catch (err) {
      els.jobs.innerHTML = `<li class="job"><div class="job__main"><h3 class="job__title">Failed to load job data</h3><div class="job__line muted">${escapeHtml(err.message)}</div></div></li>`;
      return;
    }
    const rawJobs = Array.isArray(payload) ? payload : (payload.jobs || []);
    state.statsBlock = (!Array.isArray(payload) && payload.stats) || null;
    state.jobs = rawJobs.map(enrichJob);

    buildPlatformChips();
    renderStats();
    refreshChipLabels();
    if (state.resume) {
      els.resumeText.value = state.resume;
      // re-score on boot only if we don't have cached scores
      if (Object.keys(state.resumeScores).length === 0) rescoreResume();
      else {
        const max = Math.max(...Object.values(state.resumeScores), 0);
        const matched = Object.values(state.resumeScores).filter((s) => s > 0).length;
        els.resumeStatus.textContent = `Cached: ${fmtNum(matched)} matches · top ${max}%`;
      }
    }
    applyFilter();

    // Update last visit AFTER computing diff
    Storage.set('lastVisit', new Date().toISOString());
  }

  // Init UI state
  readUrlState();
  if (els.search) els.search.value = state.query;
  if (els.sort) els.sort.value = state.sort;
  if (els.toggleNew && state.onlyNew) {
    els.toggleNew.classList.add('is-active');
    els.toggleNew.setAttribute('aria-pressed', 'true');
  }
  applyDensity();
  showView(state.view);
  bindEvents();
  load();
})();
