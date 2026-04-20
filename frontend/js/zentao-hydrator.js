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

import { api } from './api.js';

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

const _bugCache   = new Map(); // id → { data | null, expiry }
const _storyCache = new Map();

// null stored in cache = "confirmed not found in Zentao"
// undefined from _cacheGet = "not in cache (or expired)" — fetch or error-retry
function _cacheGet(map, id) {
  const entry = map.get(id);
  if (!entry) return undefined;
  if (Date.now() > entry.expiry) { map.delete(id); return undefined; }
  return entry.data;   // may be null (confirmed not found)
}

function _cacheSet(map, id, data) {
  map.set(id, { data, expiry: Date.now() + CACHE_TTL_MS });
}

/** Clear all cached data (call after user changes Zentao binding). */
function invalidate() {
  _bugCache.clear();
  _storyCache.clear();
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

  const sc = _BUG_STATUS_COLOR[d.status] || { bg: '#f1f5f9', color: '#475569' };
  const statusBadge = `<span style="background:${sc.bg};color:${sc.color};padding:1px 5px;border-radius:4px;font-size:10px;font-weight:600;">${d.status_zh || d.status}</span>`;

  const resBadge = (d.resolution && d.resolution !== 'none')
    ? `<span style="color:#64748b;font-size:10px;margin-left:3px;">(${d.resolution_zh || d.resolution})</span>`
    : '';

  const assignee = d.assigned_to
    ? `<span style="color:#0ea5e9;font-size:10px;margin-left:3px;">→${d.assigned_to}</span>`
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

function _renderStorySlot(d) {
  if (!d) return '';

  const sc = _STORY_STATUS_COLOR[d.status] || { bg: '#f1f5f9', color: '#475569' };
  const statusBadge = `<span style="background:${sc.bg};color:${sc.color};padding:1px 5px;border-radius:4px;font-size:10px;font-weight:600;">${d.status_zh || d.status}</span>`;

  const stageBadge = d.stage_zh
    ? `<span style="color:#64748b;font-size:10px;margin-left:3px;">[${d.stage_zh}]</span>`
    : '';

  const assignee = d.assigned_to
    ? `<span style="color:#0ea5e9;font-size:10px;margin-left:3px;">→${d.assigned_to}</span>`
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
async function _fetchBugs(ids, signal) {
  if (!ids.length) return { data: {}, errors: new Set(), ok: true };
  try {
    const res = await api('/zentao/hydrate/bugs?ids=' + ids.join(','), { signal });
    if (!res.ok) return { data: {}, errors: new Set(ids.map(String)), ok: false };
    const json = await res.json();
    const errorIds = new Set((json.__fetch_errors__ || []).map(String));
    delete json.__fetch_errors__;
    return { data: json, errors: errorIds, ok: true };
  } catch (err) {
    if (err.name === 'AbortError') return { data: {}, errors: new Set(), ok: true };
    return { data: {}, errors: new Set(ids.map(String)), ok: false };
  }
}

/**
 * Fetch story data from the backend.
 * Returns { data, errors, ok } with the same semantics as _fetchBugs.
 */
async function _fetchStories(ids, signal) {
  if (!ids.length) return { data: {}, errors: new Set(), ok: true };
  try {
    const res = await api('/zentao/hydrate/stories?ids=' + ids.join(','), { signal });
    if (!res.ok) return { data: {}, errors: new Set(ids.map(String)), ok: false };
    const json = await res.json();
    const errorIds = new Set((json.__fetch_errors__ || []).map(String));
    delete json.__fetch_errors__;
    return { data: json, errors: errorIds, ok: true };
  } catch (err) {
    if (err.name === 'AbortError') return { data: {}, errors: new Set(), ok: true };
    return { data: {}, errors: new Set(ids.map(String)), ok: false };
  }
}

// ─── Concurrency guard ────────────────────────────────────────────────────────
// Prevent overlapping fetches from the same container
const _containerAbortMap = new WeakMap();
const _containerRequestSeq = new WeakMap();

// Global abort controller — cancelled on each new hydrateContainer call so that
// page/version switches immediately free up Zentao connections for the new data.
function _nextContainerRequestSeq(containerEl) {
  const next = (_containerRequestSeq.get(containerEl) || 0) + 1;
  _containerRequestSeq.set(containerEl, next);
  return next;
}

function _isLatestContainerRequest(containerEl, seq) {
  return (_containerRequestSeq.get(containerEl) || 0) === seq;
}

function _replaceContainerAbort(containerEl) {
  const prev = _containerAbortMap.get(containerEl);
  if (prev) prev.abort();
  const abort = new AbortController();
  _containerAbortMap.set(containerEl, abort);
  return abort;
}

// ─── Link upgrade helper ──────────────────────────────────────────────────────

/**
 * After slots are filled, find any <span class="qa-bug-id-nohref"> elements
 * whose bug ID now has a known URL (from hydration) and upgrade them to
 * proper <a class="qa-ext-link"> links.
 *
 * Pages should render bug IDs without a local URL as:
 *   <span class="qa-bug-id-nohref" data-zt-bug-id="12345">b#12345</span>
 * so the hydrator can find and upgrade them.
 */
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

// ─── Main hydration entry point ───────────────────────────────────────────────

/**
 * Scan containerEl for Zentao slots, fetch missing data, and fill slots.
 * Safe to call multiple times — already-loaded slots are skipped.
 * Slots with transient errors are NOT marked loaded, so they retry next call.
 * @param {Element} containerEl
 */
async function hydrateContainer(containerEl) {
  if (!containerEl) return;
  const requestSeq = _nextContainerRequestSeq(containerEl);
  const abort = _replaceContainerAbort(containerEl);

  // Collect un-loaded slots
  const bugSlots   = Array.from(containerEl.querySelectorAll(
    '.zt-bug-slot[data-zt-bug-id]:not([data-zt-loaded])'));
  const storySlots = Array.from(containerEl.querySelectorAll(
    '.zt-story-slot[data-zt-story-id]:not([data-zt-loaded])'));

  if (!bugSlots.length && !storySlots.length) return;

  // — Step 1: show loading dots immediately —
  const LOADING_HTML = '<span class="zt-loading-dots">···</span>';
  bugSlots.forEach((el) => { el.innerHTML = LOADING_HTML; el.dataset.ztPending = '1'; });
  storySlots.forEach((el) => { el.innerHTML = LOADING_HTML; el.dataset.ztPending = '1'; });

  // Collect unique uncached IDs
  const uncachedBugIds = [...new Set(
    bugSlots.map((el) => el.dataset.ztBugId).filter(Boolean)
  )].filter((id) => _cacheGet(_bugCache, id) === undefined);

  const uncachedStoryIds = [...new Set(
    storySlots.map((el) => el.dataset.ztStoryId).filter(Boolean)
  )].filter((id) => _cacheGet(_storyCache, id) === undefined);

  _setIndicator('syncing');

  // — Step 2: fetch concurrently from backend —
  let bugResult   = { data: {}, errors: new Set(), ok: true };
  let storyResult = { data: {}, errors: new Set(), ok: true };

  try {
    [bugResult, storyResult] = await Promise.all([
      _fetchBugs(uncachedBugIds, abort.signal),
      _fetchStories(uncachedStoryIds, abort.signal),
    ]);

    // If this hydration was superseded by a newer one, stop filling slots
    if (abort.signal.aborted || !_isLatestContainerRequest(containerEl, requestSeq)) return;

    // Populate cache.
    // KEY RULE: only cache null for "confirmed not found" (id absent from data
    // AND no transient error).  Transient errors are NOT cached — they retry.
    for (const id of uncachedBugIds) {
      if (!bugResult.ok || bugResult.errors.has(id)) {
        // Transient error — skip cache, will retry on next hydrateContainer call
      } else if (bugResult.data[id]) {
        _cacheSet(_bugCache, id, bugResult.data[id]);          // found
      } else {
        _cacheSet(_bugCache, id, null);                        // confirmed not in Zentao
      }
    }
    for (const id of uncachedStoryIds) {
      if (!storyResult.ok || storyResult.errors.has(id)) {
        // skip
      } else if (storyResult.data[id]) {
        _cacheSet(_storyCache, id, storyResult.data[id]);
      } else {
        _cacheSet(_storyCache, id, null);
      }
    }
  } catch {
    // Unexpected JS exception — treat all uncached IDs as transient errors
    uncachedBugIds.forEach((id) => bugResult.errors.add(id));
    uncachedStoryIds.forEach((id) => storyResult.errors.add(id));
    bugResult.ok   = false;
    storyResult.ok = false;
  } finally {
    if (_containerAbortMap.get(containerEl) === abort) {
      _containerAbortMap.delete(containerEl);
    }
  }

  if (abort.signal.aborted || !_isLatestContainerRequest(containerEl, requestSeq)) return;

  // — Step 3: fill bug slots —
  let anyFound = false;

  // Determine which IDs had transient errors this round
  const bugErrorIds = new Set();
  for (const id of uncachedBugIds) {
    if (!bugResult.ok || bugResult.errors.has(id)) bugErrorIds.add(id);
  }

  for (const el of bugSlots) {
    const id = el.dataset.ztBugId;
    delete el.dataset.ztPending;

    if (!id) { el.innerHTML = ''; el.dataset.ztLoaded = '1'; continue; }

    if (bugErrorIds.has(id)) {
      // Transient error: show indicator but do NOT set ztLoaded so it retries
      el.innerHTML = `<span class="zt-err" title="禅道连接失败，请检查绑定">!</span>`;
      continue;
    }

    const data = _cacheGet(_bugCache, id);
    if (data) anyFound = true;

    const parent    = el.parentElement;
    const hasTitleTip = !!parent?.querySelector('.qa-title-tip');
    const hasNoTitle  = el.dataset.ztNoTitle === '1';
    el.innerHTML = _renderBugSlot(data, {
      showTitle: !hasTitleTip && !hasNoTitle,
    });
    el.dataset.ztLoaded = '1';
  }

  // — Step 4: fill story slots —
  const storyErrorIds = new Set();
  for (const id of uncachedStoryIds) {
    if (!storyResult.ok || storyResult.errors.has(id)) storyErrorIds.add(id);
  }

  for (const el of storySlots) {
    const id = el.dataset.ztStoryId;
    delete el.dataset.ztPending;

    if (!id) { el.innerHTML = ''; el.dataset.ztLoaded = '1'; continue; }

    if (storyErrorIds.has(id)) {
      el.innerHTML = `<span class="zt-err" title="禅道连接失败">!</span>`;
      continue;
    }

    const data = _cacheGet(_storyCache, id);
    if (data) anyFound = true;
    el.innerHTML = _renderStorySlot(data);
    el.dataset.ztLoaded = '1';
  }

  // — Step 5: upgrade no-href bug ID spans to links —
  _upgradeBugIdLinks(bugSlots);

  // — Step 6: update page indicator —
  const hasErrors = bugErrorIds.size > 0 || storyErrorIds.size > 0;
  _setIndicator(hasErrors ? 'error' : 'ok');
}

// ─── Export ───────────────────────────────────────────────────────────────────

/**
 * Clear cached Zentao data for the currently visible tab and re-hydrate.
 * Callable from a header "刷新禅道" button — no page reload needed.
 */
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
    const tabNames = ['assign','feedback','field-test','build-records','zentao-sync','report','activity','data','dispatch'];
    for (const name of tabNames) {
      const panel = document.getElementById('tab-' + name);
      if (panel && !panel.classList.contains('hidden')) { container = panel; break; }
    }
  }
  if (!container) return;

  // Clear loaded state + cache for all slots in this tab
  for (const el of container.querySelectorAll('.zt-bug-slot[data-zt-bug-id]')) {
    _bugCache.delete(el.dataset.ztBugId);
    delete el.dataset.ztLoaded;
    delete el.dataset.ztPending;
    el.innerHTML = '';
  }
  for (const el of container.querySelectorAll('.zt-story-slot[data-zt-story-id]')) {
    _storyCache.delete(el.dataset.ztStoryId);
    delete el.dataset.ztLoaded;
    delete el.dataset.ztPending;
    el.innerHTML = '';
  }

  hydrateContainer(container);
}

window.OmniQAZentao = { hydrateContainer, invalidate, refreshVisible };
