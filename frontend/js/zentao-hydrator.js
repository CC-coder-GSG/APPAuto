/**
 * Zentao Live Data Hydrator
 *
 * After each page renders its cards / table rows, call:
 *
 *   window.OmniQAZentao?.hydrateContainer(containerEl)
 *
 * The hydrator scans the container for:
 *   <span class="zt-bug-slot"   data-zt-bug-id="29875"></span>
 *   <span class="zt-story-slot" data-zt-story-id="5604"></span>
 *
 * Life-cycle of a slot:
 *   (empty) → [data-zt-pending] "···" loading → [data-zt-loaded] content
 *                                             → [data-zt-miss]   "—"  (not in Zentao)
 *                                             → (no attr)        "!"  (transient error — will retry)
 *
 * Link upgrade:
 *   When a bug has no local zentao_bug_url, pages render the bug ID as:
 *     <span class="qa-bug-id-nohref" data-zt-bug-id="12345">b#12345</span>
 *   After hydration returns the URL, the hydrator replaces that span with
 *   a proper <a class="qa-ext-link"> so the bug ID itself becomes a link.
 *
 * Title suppression:
 *   Add data-zt-no-title="1" to a zt-bug-slot to suppress the ⓘ title tip
 *   (use this on rows that already display the title in a dedicated column).
 *
 * Page-level indicator: <span id="ztSyncDot"> in the header shows sync state.
 *
 * Exposes: window.OmniQAZentao = { hydrateContainer, invalidate }
 */

import { loadHydration, hydrationEpoch, invalidateHydration } from './components/hydration-queue.js?v=20261105-hydration-1';
import { updateBugVersion } from './components/bug-version.js?v=20261022-found-version-1';

// ─── One-time style injection ─────────────────────────────────────────────────

(function injectStyles() {
  if (document.getElementById('zt-hydrator-styles')) return;
  const style = document.createElement('style');
  style.id = 'zt-hydrator-styles';
  style.textContent = `
    @keyframes zt-pulse { 0%,100%{opacity:.3} 50%{opacity:1} }
    @keyframes zt-spin   { to{transform:rotate(360deg)} }
    .zt-loading-dots {
      display: inline-block;
      color: #94a3b8;
      font-size: 10px;
      letter-spacing: 1px;
      animation: zt-pulse 1.2s ease-in-out infinite;
      vertical-align: middle;
    }
    .zt-miss  { color: #cbd5e1; font-size: 10px; vertical-align: middle; }
    .zt-err   { color: #f59e0b; font-size: 10px; vertical-align: middle; cursor: help; }
    #ztSyncDot {
      display: inline-block;
      width: 8px; height: 8px;
      border-radius: 50%;
      vertical-align: middle;
      margin-left: 4px;
      transition: background 0.4s;
    }
    #ztSyncDot[data-state="syncing"] {
      background: #3b82f6;
      animation: zt-spin 1s linear infinite;
    }
    #ztSyncDot[data-state="ok"]    { background: #16a34a; }
    #ztSyncDot[data-state="error"] { background: #dc2626; }
    #ztSyncDot[data-state="idle"]  { background: #cbd5e1; }
    #ztSyncDot[data-state="none"]  { display: none; }
  `;
  document.head.appendChild(style);
})();

// ─── Page-level sync indicator ────────────────────────────────────────────────

const _STATE_TITLES = {
  syncing: '正在从禅道同步数据…',
  ok:      '禅道数据已同步 ✓',
  error:   '禅道同步失败，请检查绑定',
  idle:    '禅道已绑定，等待页面触发同步',
  none:    '',
};

function _setIndicator(state) {
  const dot = document.getElementById('ztSyncDot');
  if (!dot) return;
  dot.dataset.state = state;
  dot.title = _STATE_TITLES[state] || '';
}

// ─── In-memory cache ─────────────────────────────────────────────────────────
const CACHE_TTL_MS = 60_000; // 60 seconds
const STALE_TTL_MS = 24 * 60 * 60_000;
const STORAGE_KEY = 'omniqa.zentao-labels.v1';

const _bugCache   = new Map(); // id → { data | null, expiry }
const _storyCache = new Map();
let _saveTimer;

// Session storage survives page reloads. Scope it to the authenticated user;
// never store the JWT, password, or Zentao token with the label snapshot.
function _cacheOwner() {
  try {
    const payload = (localStorage.getItem('token') || '').split('.')[1];
    return JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/'))).sub || null;
  } catch { return null; }
}

function _restoreCache() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY));
    if (!saved || !saved.owner || saved.owner !== _cacheOwner()) return;
    for (const [kind, map] of [['bugs', _bugCache], ['stories', _storyCache]]) {
      for (const [id, entry] of (saved[kind] || []).slice(-200)) {
        if (/^\d+$/.test(id) && entry?.data && Number.isFinite(entry.saved)
            && Date.now() - entry.saved < STALE_TTL_MS) map.set(id, entry);
      }
    }
  } catch { /* Storage may be disabled or full; memory caching still works. */ }
}

function _saveCache() {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    try {
      const owner = _cacheOwner();
      if (!owner) return;
      const recent = map => [...map].filter(([, e]) => e.data && Date.now() - e.saved < STALE_TTL_MS).slice(-200);
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ owner, bugs: recent(_bugCache), stories: recent(_storyCache) }));
    } catch { /* Best effort only. */ }
  }, 0);
}
_restoreCache();

// null stored in cache = "confirmed not found in Zentao"
// undefined from _cacheGet = "not in cache (or expired)" — fetch or error-retry
function _cacheGet(map, id) {
  const entry = map.get(id);
  if (!entry) return undefined;
  if (Date.now() > entry.expiry) return undefined;
  return entry.data;   // may be null (confirmed not found)
}

function _cacheSet(map, id, data) {
  map.set(id, { data, saved: Date.now(), expiry: Date.now() + CACHE_TTL_MS });
  _saveCache();
}

/** Clear all cached data (call after user changes Zentao binding). */
function invalidate() {
  invalidateHydration();
  clearTimeout(_saveTimer);
  try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Best effort. */ }
  _bugCache.clear();
  _storyCache.clear();
  document.querySelectorAll('.zt-story-slot, .zt-bug-slot').forEach(el => {
    el.replaceChildren();
    delete el.dataset.ztLoaded;
    delete el.dataset.ztPending;
    delete el.dataset.ztStale;
    el.removeAttribute('title');
  });
  _setIndicator('idle');
}

// ─── Badge renderers ──────────────────────────────────────────────────────────

const _BUG_STATUS_COLOR = {
  active:   { bg: '#fee2e2', color: '#b91c1c' },
  resolved: { bg: '#dcfce7', color: '#166534' },
  closed:   { bg: '#f1f5f9', color: '#475569' },
  notrepro: { bg: '#fef3c7', color: '#92400e' },
};

function _escHtml(str) {
  return String(str ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * Render the Zentao status badge(s) for a bug slot.
 * Link injection (↗) has been removed — link upgrade is handled post-fill
 * by _upgradeBugIdLinks() so the bug ID itself becomes the clickable link.
 *
 * @param {object|null} d          Normalized bug data (null = not found in Zentao)
 * @param {boolean} showTitle      Inject ⓘ tip when true and bug has a title
 */
function _renderBugSlot(d, { showTitle = false } = {}) {
  if (!d) return `<span class="zt-miss" title="禅道中未找到此Bug">—</span>`;

  // 禅道已删除：只显示红色「已删除」，不再展示状态/指派等会误导的信息。
  if (d.deleted) {
    return `<span class="badge" style="background:#dc2626;color:#fff;font-weight:bold;font-size:10px;padding:1px 5px;border-radius:4px;" title="该Bug已在禅道删除">🗑️ 已删除</span>`;
  }

  const sc = _BUG_STATUS_COLOR[d.status] || { bg: '#f1f5f9', color: '#475569' };
  const statusBadge = `<span style="background:${sc.bg};color:${sc.color};padding:1px 5px;border-radius:4px;font-size:10px;font-weight:600;">${_escHtml(d.status_zh || d.status)}</span>`;

  const resBadge = (d.resolution && d.resolution !== 'none')
    ? `<span style="color:#64748b;font-size:10px;margin-left:3px;">(${_escHtml(d.resolution_zh || d.resolution)})</span>`
    : '';

  const assignee = d.assigned_to
    ? `<span style="color:#0ea5e9;font-size:10px;margin-left:3px;">→${_escHtml(d.assigned_to)}</span>`
    : '';

  const titleHtml = (showTitle && d.title)
    ? `<span class="qa-title-tip" title="${_escHtml(d.title)}"
         style="color:#64748b;font-size:11px;margin-left:2px;cursor:help;">ⓘ</span>`
    : '';

  return `${statusBadge}${resBadge}${assignee}${titleHtml}`;
}

const _STORY_STATUS_COLOR = {
  draft:    { bg: '#f1f5f9', color: '#475569' },
  active:   { bg: '#dbeafe', color: '#1d4ed8' },
  closed:   { bg: '#f0fdf4', color: '#166534' },
  changing: { bg: '#fef3c7', color: '#92400e' },
};

// Eye-catching colors per stage. "developed" (开发完成) gets a bright green
// pill with a sparkle so testers can spot stories that just became testable.
const _STORY_STAGE_STYLE = {
  developed: {
    style: 'background:#16a34a;color:#fff;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:700;margin-left:3px;box-shadow:0 0 0 1px #bbf7d0;',
    icon: '✨ ',
  },
  testing: {
    style: 'background:#f59e0b;color:#fff;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;margin-left:3px;',
    icon: '',
  },
  released: {
    style: 'background:#0ea5e9;color:#fff;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;margin-left:3px;',
    icon: '',
  },
};

function _renderStorySlot(d) {
  if (!d) return '';

  const sc = _STORY_STATUS_COLOR[d.status] || { bg: '#f1f5f9', color: '#475569' };
  const statusBadge = `<span style="background:${sc.bg};color:${sc.color};padding:1px 5px;border-radius:4px;font-size:10px;font-weight:600;">${_escHtml(d.status_zh || d.status)}</span>`;

  let stageBadge = '';
  if (d.stage_zh) {
    const sty = _STORY_STAGE_STYLE[d.stage];
    if (sty) {
      stageBadge = `<span style="${sty.style}">${sty.icon}${_escHtml(d.stage_zh)}</span>`;
    } else {
      stageBadge = `<span style="color:#64748b;font-size:10px;margin-left:3px;">[${_escHtml(d.stage_zh)}]</span>`;
    }
  }

  const assignee = d.assigned_to
    ? `<span style="color:#0ea5e9;font-size:10px;margin-left:3px;">→${_escHtml(d.assigned_to)}</span>`
    : '';

  return `${statusBadge}${stageBadge}${assignee}`;
}

// ─── Fetch helpers ────────────────────────────────────────────────────────────

/**
 * Fetch bug data from the backend.
 * Returns { data, errors, ok } where:
 *   data   — map of id → normalized bug object (successfully fetched)
 *   errors — Set of id strings that had transient fetch errors (do NOT cache as null)
 *   ok     — false if the entire HTTP request failed
 */
const _containerRequestSeq = new WeakMap();

function _upgradeBugIdLinks(bugSlots) {
  for (const el of bugSlots) {
    const id = el.dataset.ztBugId;
    if (!id) continue;
    const data = _cacheGet(_bugCache, id);
    if (!data?.zentao_url) continue;

    const parent = el.parentElement;
    if (!parent) continue;
    const noHrefSpan = parent.querySelector(`.qa-bug-id-nohref[data-zt-bug-id="${id}"]`);
    if (!noHrefSpan) continue;

    const link = document.createElement('a');
    link.className = 'qa-ext-link';
    link.href = data.zentao_url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.innerHTML = noHrefSpan.innerHTML;
    noHrefSpan.replaceWith(link);
  }
}

/**
 * Same as _upgradeBugIdLinks but for story slots. Pages render a
 * non-link wrapper like:
 *   <span class="qa-story-id-nohref" data-zt-story-id="6453">s#6453</span>
 * After hydration we replace it with a real anchor pointing to the
 * Zentao story view URL.
 *
 * Searches the whole document for matching ids (not just the slot's
 * sibling) so callers can place the wrapper anywhere in the row.
 */
function _upgradeStoryIdLinks(storySlots) {
  for (const el of storySlots) {
    const id = el.dataset.ztStoryId;
    if (!id) continue;
    const data = _cacheGet(_storyCache, id);
    if (!data?.zentao_url) continue;

    const noHrefSpans = document.querySelectorAll(
      `.qa-story-id-nohref[data-zt-story-id="${id}"]`
    );
    for (const span of noHrefSpans) {
      const link = document.createElement('a');
      link.className = 'qa-ext-link';
      link.href = data.zentao_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.innerHTML = span.innerHTML;
      span.replaceWith(link);
    }
  }
}

// ─── Main hydration entry point ───────────────────────────────────────────────

/**
 * Scan containerEl for Zentao slots, fetch missing data, and fill slots.
 * Safe to call multiple times — already-loaded slots are skipped.
 * Slots with transient errors are NOT marked loaded, so they retry next call.
 * @param {Element} containerEl
 */
function _fillSlot(el, kind, data) {
  delete el.dataset.ztPending;
  delete el.dataset.ztStale;
  el.removeAttribute('title');
  if (kind === 'bugs') {
    const parent = el.parentElement;
    el.innerHTML = _renderBugSlot(data, { showTitle: !parent?.querySelector('.qa-title-tip') && el.dataset.ztNoTitle !== '1' });
    updateBugVersion(parent, data);
    _upgradeBugIdLinks([el]);
  } else {
    el.innerHTML = _renderStorySlot(data);
    _upgradeStoryIdLinks([el]);
  }
  el.dataset.ztLoaded = '1';
}

let _identity = localStorage.getItem('token') || '';
async function hydrateContainer(containerEl) {
  if (!containerEl) return;
  const identity = localStorage.getItem('token') || '';
  if (_identity !== identity) { _identity = identity; invalidate(); }
  const seq = (_containerRequestSeq.get(containerEl) || 0) + 1;
  _containerRequestSeq.set(containerEl, seq);
  const epoch = hydrationEpoch();
  const valid = el => el.isConnected && epoch === hydrationEpoch() && _containerRequestSeq.get(containerEl) === seq;
  const tasks = [];
  let errors = false;
  for (const [kind, attr, cache] of [['stories', 'ztStoryId', _storyCache], ['bugs', 'ztBugId', _bugCache]]) {
    const selector = kind === 'stories' ? '.zt-story-slot:not([data-zt-loaded])' : '.zt-bug-slot:not([data-zt-loaded])';
    for (const el of containerEl.querySelectorAll(selector)) {
      const id = el.dataset[attr];
      if (!/^\d+$/.test(id || '')) continue;
      const cached = _cacheGet(cache, id);
      if (cached !== undefined) { _fillSlot(el, kind, cached); continue; }
      const stale = cache.get(id);
      if (stale?.data && Date.now() - stale.saved < STALE_TTL_MS) {
        _fillSlot(el, kind, stale.data);
        delete el.dataset.ztLoaded;
        el.dataset.ztStale = '1';
        el.title = `上次同步：${new Date(stale.saved).toLocaleString()}，正在更新`;
      }
      if (!el.textContent.trim()) el.innerHTML = '<span class="zt-loading-dots">···</span>';
      el.dataset.ztPending = '1';
      const rect = el.getBoundingClientRect();
      const priority = rect.bottom >= 0 && rect.top < innerHeight ? 0 : 1;
      tasks.push(loadHydration(kind, id, priority).then(result => {
        if (!valid(el) || result.cancelled) return;
        delete el.dataset.ztPending;
        if (!result.error) {
          _cacheSet(cache, id, result.data);
          _fillSlot(el, kind, result.data);
          return;
        }
        errors = true;
        if (el.dataset.ztStale) el.title = `更新失败，显示上次成功同步的信息（${new Date(stale.saved).toLocaleString()}）`;
        el.querySelector('.zt-loading-dots')?.remove();
        el.querySelector('.zt-err')?.remove();
        const retry = document.createElement('button');
        retry.type = 'button'; retry.className = 'zt-err wb-text-button';
        retry.textContent = '↻'; retry.title = '信息更新失败，点击重试；鉴权失败时请检查禅道绑定';
        retry.onclick = event => { event.preventDefault(); event.stopPropagation(); hydrateContainer(el.parentElement); };
        el.appendChild(retry);
      }));
    }
  }
  if (tasks.length) _setIndicator('syncing');
  await Promise.all(tasks);
  if (epoch === hydrationEpoch()) _setIndicator(errors ? 'error' : 'ok');
}


function refreshVisible() {
  let container = null;
  const minePanel = document.getElementById('tab-mine');
  if (minePanel && !minePanel.classList.contains('hidden')) {
    const activeWorkbench = window.getWorkbenchVisibleSubtab?.() || 'demand';
    if (activeWorkbench === 'retest') container = document.getElementById('retestCardsArea') || document.getElementById('tab-retest');
    else if (activeWorkbench === 'overall-test') container = document.getElementById('s5TableContainer') || document.getElementById('tab-overall-test');
    else container = document.getElementById('mineCards') || document.querySelector('[data-workbench-panel="demand"]');
  }
  if (!container) {
    const tabNames = ['assign','feedback','field-test','build-records','report','activity','data','dispatch'];
    for (const name of tabNames) {
      const panel = document.getElementById('tab-' + name);
      if (panel && !panel.classList.contains('hidden')) { container = panel; break; }
    }
  }
  if (!container) return;

  // Keep the last successful label visible while explicitly refreshing it.
  invalidateHydration();
  for (const el of container.querySelectorAll('.zt-bug-slot[data-zt-bug-id]')) {
    const entry = _bugCache.get(el.dataset.ztBugId);
    if (entry) entry.expiry = 0;
    delete el.dataset.ztLoaded;
    delete el.dataset.ztPending;
  }
  for (const el of container.querySelectorAll('.zt-story-slot[data-zt-story-id]')) {
    const entry = _storyCache.get(el.dataset.ztStoryId);
    if (entry) entry.expiry = 0;
    delete el.dataset.ztLoaded;
    delete el.dataset.ztPending;
  }

  hydrateContainer(container);
}

window.OmniQAZentao = { hydrateContainer, invalidate, refreshVisible };
