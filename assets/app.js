(() => {
  'use strict';

  const PAGE_SIZE = 50;
  const STALE_DAYS = 7;

  const state = {
    jobs: [],
    filtered: [],
    visible: PAGE_SIZE,
    query: '',
    sort: 'recent',
  };

  const els = {
    statJobs: document.getElementById('statJobs'),
    statCompanies: document.getElementById('statCompanies'),
    statLocations: document.getElementById('statLocations'),
    statUpdated: document.getElementById('statUpdated'),
    staleBanner: document.getElementById('staleBanner'),
    search: document.getElementById('search'),
    sort: document.getElementById('sort'),
    count: document.getElementById('count'),
    jobs: document.getElementById('jobs'),
    empty: document.getElementById('empty'),
    loadmoreWrap: document.getElementById('loadmoreWrap'),
    loadmore: document.getElementById('loadmore'),
  };

  function fmtNum(n) {
    return new Intl.NumberFormat('en-US').format(n);
  }

  function parseDate(s) {
    if (!s) return null;
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  function fmtRelative(date) {
    if (!date) return '—';
    const now = new Date();
    const diffMs = now - date;
    const sec = Math.floor(diffMs / 1000);
    const min = Math.floor(sec / 60);
    const hr = Math.floor(min / 60);
    const day = Math.floor(hr / 24);
    if (day < 1) return hr < 1 ? `${min}m ago` : `${hr}h ago`;
    if (day < 30) return `${day}d ago`;
    const mo = Math.floor(day / 30);
    if (mo < 12) return `${mo}mo ago`;
    return `${Math.floor(day / 365)}y ago`;
  }

  function fmtAbs(date) {
    if (!date) return '';
    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function isSafeUrl(u) {
    if (!u || typeof u !== 'string') return false;
    try {
      const parsed = new URL(u, window.location.href);
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch (_) {
      return false;
    }
  }

  function applyFilter() {
    const q = state.query.trim().toLowerCase();
    let list = state.jobs;

    if (q) {
      const tokens = q.split(/\s+/).filter(Boolean);
      list = list.filter((j) => {
        const hay = [
          j.title || '',
          j.company || '',
          j.location || '',
          j.platform || '',
        ].join(' ').toLowerCase();
        return tokens.every((t) => hay.includes(t));
      });
    }

    const cmp = {
      recent: (a, b) => (b._postedTs || 0) - (a._postedTs || 0),
      oldest: (a, b) => (a._postedTs || 0) - (b._postedTs || 0),
      company: (a, b) => (a.company || '').localeCompare(b.company || ''),
    }[state.sort];

    list = list.slice().sort(cmp);
    state.filtered = list;
    state.visible = PAGE_SIZE;
    render();
  }

  function render() {
    const total = state.filtered.length;
    const slice = state.filtered.slice(0, state.visible);

    if (total === 0) {
      els.jobs.innerHTML = '';
      els.empty.hidden = false;
      els.loadmoreWrap.hidden = true;
      els.count.textContent = `0 of ${fmtNum(state.jobs.length)}`;
      return;
    }

    els.empty.hidden = true;
    els.count.textContent = `${fmtNum(slice.length)} of ${fmtNum(total)}`;

    els.jobs.innerHTML = slice.map((j) => {
      const url = isSafeUrl(j.url) ? j.url : null;
      const titleHtml = url
        ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(j.title || 'Untitled role')}</a>`
        : escapeHtml(j.title || 'Untitled role');

      const platform = j.platform ? `<span class="job__tag">${escapeHtml(j.platform)}</span>` : '';
      const sep = '<span class="job__sep" aria-hidden="true">•</span>';

      const lineParts = [
        `<span class="job__company">${escapeHtml(j.company || 'Unknown company')}</span>`,
        j.location ? sep + `<span class="job__loc">${escapeHtml(j.location)}</span>` : '',
        platform,
      ].filter(Boolean).join(' ');

      const date = parseDate(j.posted_date) || parseDate(j.found_at);
      const dateAttr = date ? ` title="${escapeHtml(fmtAbs(date))}"` : '';
      const dateText = date ? fmtRelative(date) : '—';

      const openLink = url
        ? `<a class="job__open" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Open</a>`
        : '';

      return `
        <li class="job">
          <div class="job__main">
            <h3 class="job__title">${titleHtml}</h3>
            <div class="job__line">${lineParts}</div>
          </div>
          <div class="job__side">
            <span class="job__date"${dateAttr}>${escapeHtml(dateText)}</span>
            ${openLink}
          </div>
        </li>`;
    }).join('');

    els.loadmoreWrap.hidden = state.visible >= total;
  }

  function renderStats(jobs, statsBlock) {
    els.statJobs.textContent = fmtNum(jobs.length);

    const companies = new Set();
    const locations = new Set();
    for (const j of jobs) {
      if (j.company) companies.add(j.company.trim().toLowerCase());
      if (j.location) {
        const city = j.location.split(',')[0].trim();
        if (city) locations.add(city.toLowerCase());
      }
    }
    els.statCompanies.textContent = fmtNum(companies.size);
    els.statLocations.textContent = fmtNum(locations.size);

    const lastScan = parseDate(statsBlock && statsBlock.last_scan);
    els.statUpdated.textContent = lastScan ? fmtRelative(lastScan) : '—';

    if (lastScan) {
      const ageDays = (Date.now() - lastScan.getTime()) / (1000 * 60 * 60 * 24);
      if (ageDays > STALE_DAYS) {
        const ageText = ageDays > 30
          ? `${Math.floor(ageDays / 30)} month${Math.floor(ageDays / 30) > 1 ? 's' : ''}`
          : `${Math.floor(ageDays)} days`;
        els.staleBanner.textContent = `Snapshot is ${ageText} old. Run the scanner locally and push to refresh.`;
        els.staleBanner.hidden = false;
      }
    }
  }

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
    const statsBlock = (!Array.isArray(payload) && payload.stats) || null;

    state.jobs = rawJobs.map((j) => {
      const ts = parseDate(j.posted_date) || parseDate(j.found_at);
      return { ...j, _postedTs: ts ? ts.getTime() : 0 };
    });

    renderStats(state.jobs, statsBlock);
    applyFilter();
  }

  // Events
  let searchTimer;
  els.search.addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.query = e.target.value;
      applyFilter();
    }, 120);
  });
  els.sort.addEventListener('change', (e) => {
    state.sort = e.target.value;
    applyFilter();
  });
  els.loadmore.addEventListener('click', () => {
    state.visible += PAGE_SIZE;
    render();
  });

  load();
})();
