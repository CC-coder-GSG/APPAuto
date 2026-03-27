import { state } from '../state.js';

const listeners = new Map();
let abortController = null;
let reconnectTimer = null;
let reconnectAttempts = 0;
let started = false;
const refreshTimers = new Map();
const unreadByScope = new Map();
const attentionObserved = new WeakMap();
let attentionObserver = null;
const SSE_DEBUG = !!window.__OMNIQA_SSE_DEBUG__;

const RUNTIME = {
  lastEventId: 0, // 每次页面加载从 0 开始，避免服务重启后 cursor 陈旧导致事件被全部过滤
  connectionState: 'disconnected',
  counters: {
    zentaoNew: 0,
    minePrimary: 0,
    mineSecondaryBugDispatch: 0,
    feedback: 0,
    retest: 0,
    overallBug: 0,
  },
};

// ─── Connection indicator ────────────────────────────────────────────────────

function updateConnectionIndicator() {
  const el = document.getElementById('sseConnectionState');
  if (!el) return;
  const map = {
    connected:    '实时已连接',
    reconnecting: '实时重连中',
    connecting:   '实时连接中',
    disconnected: '实时未连接',
  };
  const cur = String(RUNTIME.connectionState || 'disconnected');
  el.textContent = map[cur] || map.disconnected;
  el.classList.remove('hidden', 'connected', 'reconnecting', 'disconnected');
  el.classList.remove('hidden');
  if (cur === 'connected') el.classList.add('connected');
  else if (cur === 'reconnecting' || cur === 'connecting') el.classList.add('reconnecting');
  else el.classList.add('disconnected');
}

function setConnectionState(next) {
  const v = String(next || 'disconnected');
  if (RUNTIME.connectionState === v) return;
  RUNTIME.connectionState = v;
  updateConnectionIndicator();
}

function debugLog(...args) { if (SSE_DEBUG) console.debug('[SSE]', ...args); }
function debugError(...args) { if (SSE_DEBUG) console.error('[SSE]', ...args); }

// ─── Counter & badge management ──────────────────────────────────────────────

const SCOPE_COUNTER_MAP = {
  zentao_sync:          'zentaoNew',
  mine_requirement:     'minePrimary',
  mine_bug_dispatch:    'mineSecondaryBugDispatch',
  feedback_task:        'feedback',
  retest_requirement:   'retest',
  overall_bug:          'overallBug',
};

function getUnreadSet(scope) {
  if (!unreadByScope.has(scope)) unreadByScope.set(scope, new Set());
  return unreadByScope.get(scope);
}

function adjustCounterByScope(scope, delta) {
  const key = SCOPE_COUNTER_MAP[scope];
  if (!key) return;
  RUNTIME.counters[key] = Math.max(0, (RUNTIME.counters[key] || 0) + delta);
}

function markUnread(scope, key) {
  if (!scope || !key) return;
  const set = getUnreadSet(scope);
  if (set.has(String(key))) return;
  set.add(String(key));
  adjustCounterByScope(scope, +1);
}

export function markRead(scope, key) {
  if (!scope || !key) return;
  const set = getUnreadSet(scope);
  if (!set.has(String(key))) return;
  set.delete(String(key));
  adjustCounterByScope(scope, -1);
  updateNavBadges();
}

export function clearScopeUnread(scope) {
  if (!scope) return;
  const set = getUnreadSet(scope);
  if (!set.size) return;
  const size = set.size;
  set.clear();
  adjustCounterByScope(scope, -size);
  updateNavBadges();
}

function queueUnreadByEvent(message) {
  const type = String(message?.type || '');
  const payload = message?.payload || {};
  const item = payload?.item || {};
  if (type === 'zentao_sync_created'          && item?.id)      markUnread('zentao_sync', item.id);
  if (type === 'workbench_requirement_created' && payload?.id)   markUnread('mine_requirement', payload.id);
  if (type === 'bug_dispatch_created'          && payload?.id)   markUnread('mine_bug_dispatch', payload.id);
  if (type === 'feedback_task_created'         && payload?.id)   markUnread('feedback_task', payload.id);
  if (type === 'retest_requirement_created'    && payload?.id)   markUnread('retest_requirement', payload.id);
  if (type === 'overall_bug_created'           && payload?.id)   markUnread('overall_bug', payload.id);
  if (type === 'zentao_sync_deleted'           && payload?.id)   markRead('zentao_sync', payload.id);
}

function updateNavBadges() {
  const badge = (id, counter) => {
    const el = document.getElementById(id);
    if (!el) return;
    const n = RUNTIME.counters[counter] || 0;
    el.textContent = n > 0 ? String(n) : '';
    el.classList.toggle('hidden', n <= 0);
  };
  badge('tabZentaoSyncBadge',  'zentaoNew');
  badge('tabMinePrimaryBadge', 'minePrimary');
  badge('tabMineSecondaryBadge', 'mineSecondaryBugDispatch');
  badge('tabFeedbackBadge',    'feedback');
  badge('tabRetestBadge',      'retest');
  badge('tabStage5Badge',      'overallBug');
}

function bumpCounterByEvent(message) {
  // assignee-only filter for personal events
  const payload = message?.payload || {};
  const myId = Number(state.currentUser?.id || 0);
  if (payload?.assignee_id && myId && Number(payload.assignee_id) !== myId) return;
  updateNavBadges();
}

// ─── Active tab detection ────────────────────────────────────────────────────

function visibleTabName() {
  const names = ['assign','mine','feedback','retest','stage5','field-test','build-records','zentao-sync','report','activity','data','dispatch'];
  return names.find((n) => {
    const el = document.getElementById(`tab-${n}`);
    return el && !el.classList.contains('hidden');
  }) || '';
}

// ─── Debounce refresh (fallback for pages without incremental logic) ─────────

function debounceRefresh(key, fn, wait = 700) {
  const old = refreshTimers.get(key);
  if (old) clearTimeout(old);
  const timer = setTimeout(() => {
    refreshTimers.delete(key);
    try { fn(); } catch { /* no-op */ }
  }, wait);
  refreshTimers.set(key, timer);
}

// Only used for pages that still use full reloads (report, field-test)
function handleVisibleRefreshByEvent(message) {
  const type = String(message?.type || '');
  const visible = visibleTabName();

  if ((type === 'field_test_record_created' || type === 'field_test_record_updated') && visible === 'field-test' && typeof window.loadFieldTestBoard === 'function') {
    debounceRefresh('field-test', () => window.loadFieldTestBoard());
  }
  if (type === 'report_data_changed' && visible === 'report' && typeof window.OmniQAReportTab?.queryReport === 'function') {
    debounceRefresh('report', () => window.OmniQAReportTab.queryReport(), 1200);
  }
}

// ─── Event ID persistence ────────────────────────────────────────────────────

function saveLastEventId(id) {
  if (!id) return;
  RUNTIME.lastEventId = Number(id) || RUNTIME.lastEventId;
  localStorage.setItem('sse_last_event_id', String(RUNTIME.lastEventId));
}

// ─── SSE stream parsing ──────────────────────────────────────────────────────

function parseSSEChunk(buffer, onMessage) {
  const parts = buffer.split('\n\n');
  const rest = parts.pop() || '';
  parts.forEach((part) => {
    const lines = part.split('\n');
    let id = null;
    let event = 'message';
    const dataLines = [];
    lines.forEach((line) => {
      if (line.startsWith('id:'))    id = line.slice(3).trim();
      else if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:'))  dataLines.push(line.slice(5).trim());
    });
    const raw = dataLines.join('\n');
    if (!raw) return;
    let payload = {};
    try { payload = JSON.parse(raw); } catch { payload = { raw }; }
    onMessage({ id: Number(id || payload.id || 0) || null, type: event || payload.type || 'message', ...payload });
  });
  return rest;
}

// ─── Publish-subscribe ───────────────────────────────────────────────────────

function emit(type, message) {
  const direct = listeners.get(type) || [];
  const wildcard = listeners.get('*') || [];
  [...direct, ...wildcard].forEach((cb) => {
    try { cb(message); } catch (err) { console.error('[SSE] listener error', err); }
  });
}

export function subscribeSSE(type, callback) {
  const arr = listeners.get(type) || [];
  arr.push(callback);
  listeners.set(type, arr);
  return () => {
    const list = listeners.get(type) || [];
    listeners.set(type, list.filter((fn) => fn !== callback));
  };
}

// ─── Connect / reconnect ─────────────────────────────────────────────────────

async function connect() {
  if (abortController) abortController.abort();
  abortController = new AbortController();
  setConnectionState(reconnectAttempts > 0 ? 'reconnecting' : 'connecting');

  const cursor = Number(RUNTIME.lastEventId || 0);
  const token = String(localStorage.getItem('token') || '').trim();
  const headers = { Accept: 'text/event-stream' };
  if (token) headers.Authorization = `Bearer ${token}`;

  debugLog('connect', { cursor, reconnectAttempts });

  const resp = await fetch(`/api/sse/stream?last_event_id=${cursor}`, {
    method: 'GET', headers,
    signal: abortController.signal,
    credentials: 'same-origin',
  });

  if (resp.status === 401) {
    localStorage.removeItem('token');
    setConnectionState('disconnected');
    started = false;
    if (window.location.pathname !== '/login') window.location.href = '/login';
    throw new Error('SSE unauthorized (401)');
  }

  if (!resp.ok || !resp.body) throw new Error(`SSE connect failed: ${resp.status}`);

  reconnectAttempts = 0;
  setConnectionState('connected');
  debugLog('connected');

  const decoder = new TextDecoder('utf-8');
  const reader = resp.body.getReader();
  let carry = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    carry += decoder.decode(value, { stream: true });
    carry = parseSSEChunk(carry, (msg) => {
      if (msg.type === 'ping') return;
      if (msg.id) saveLastEventId(msg.id);
      debugLog('event', msg.type);
      queueUnreadByEvent(msg);
      bumpCounterByEvent(msg);
      emit(msg.type, msg);
      handleVisibleRefreshByEvent(msg);
    });
  }

  debugLog('stream ended');
  if (started) setConnectionState('reconnecting');
}

function scheduleReconnect() {
  if (!started) return;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectAttempts += 1;
  setConnectionState('reconnecting');
  const ms = Math.min(15000, 1000 * 2 ** Math.min(5, reconnectAttempts));
  debugLog('reconnect in', ms, 'ms');
  reconnectTimer = setTimeout(() => {
    connect().catch((err) => {
      if (String(err?.message || '').includes('401')) return;
      debugError('reconnect failed', err);
      scheduleReconnect();
    });
  }, ms);
}

export function startSSE() {
  if (started) return;
  started = true;
  updateNavBadges();
  setConnectionState('connecting');
  connect().catch((err) => {
    if (String(err?.message || '').includes('401')) return;
    debugError('initial connect failed', err);
    scheduleReconnect();
  });
}

export function stopSSE() {
  started = false;
  if (abortController) abortController.abort();
  abortController = null;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  setConnectionState('disconnected');
}

// ─── Glow animation ──────────────────────────────────────────────────────────

function beginGlowPhase(el, cls) {
  el.classList.remove('sse-glow-enter', 'sse-glow-hold', 'sse-glow-exit');
  el.classList.add('sse-glow', cls);
}

export function pulseBoundaryGlow(el, tone = 'blue') {
  if (!el) return;
  const now = Date.now();
  const last = Number(el.dataset.ssePulseTs || 0);
  // Throttle: if already glowing within 1200ms, just stay in hold
  if (now - last < 1200 && el.classList.contains('sse-glow')) {
    el.dataset.ssePulseTs = String(now);
    beginGlowPhase(el, 'sse-glow-hold');
    return;
  }
  el.dataset.ssePulseTs = String(now);
  el.classList.remove('sse-glow-blue', 'sse-glow-teal', 'sse-glow-green', 'sse-glow-purple', 'sse-glow-amber');
  el.classList.add(`sse-glow-${tone}`);
  beginGlowPhase(el, 'sse-glow-enter');
  // Phase timing: enter 480ms → hold 2200ms → exit 650ms → cleanup
  setTimeout(() => { if (el.classList.contains('sse-glow')) beginGlowPhase(el, 'sse-glow-hold'); }, 480);
  setTimeout(() => { if (el.classList.contains('sse-glow')) beginGlowPhase(el, 'sse-glow-exit'); }, 2680);
  setTimeout(() => el.classList.remove('sse-glow','sse-glow-enter','sse-glow-hold','sse-glow-exit','sse-glow-blue','sse-glow-teal','sse-glow-green','sse-glow-purple','sse-glow-amber'), 3380);
}

// ─── IntersectionObserver — viewport-aware attention ─────────────────────────

function ensureAttentionObserver() {
  if (attentionObserver) return attentionObserver;
  attentionObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      const el = entry.target;
      const meta = attentionObserved.get(el);
      if (!meta) return;
      meta.inView = !!entry.isIntersecting;
      if (meta.inView && !meta.read && getUnreadSet(meta.scope).has(meta.key)) {
        pulseBoundaryGlow(el, meta.tone || 'blue');
      }
    });
  }, { threshold: 0.2 });
  return attentionObserver;
}

export function mountAttention(el, { scope, key, tone = 'blue', hoverDelayMs = 420 } = {}) {
  if (!el || !scope || key == null) return;
  const meta = { scope, key: String(key), tone, inView: false, read: false, hoverTimer: null };
  attentionObserved.set(el, meta);
  ensureAttentionObserver().observe(el);

  // If already unread, trigger glow immediately
  if (getUnreadSet(scope).has(meta.key)) pulseBoundaryGlow(el, tone);

  const clearTimer = () => { if (meta.hoverTimer) { clearTimeout(meta.hoverTimer); meta.hoverTimer = null; } };

  el.addEventListener('mouseenter', () => {
    clearTimer();
    meta.hoverTimer = setTimeout(() => {
      if (meta.read) return;
      meta.read = true;
      markRead(scope, meta.key);
      el.classList.remove('sse-glow','sse-glow-enter','sse-glow-hold','sse-glow-exit','sse-glow-blue','sse-glow-teal','sse-glow-green','sse-glow-purple','sse-glow-amber');
    }, hoverDelayMs);
  });

  el.addEventListener('mouseleave', () => clearTimer());
}

// ─── Position-aware banners (顶部/底部新记录提示) ────────────────────────────

/**
 * showPositionBanner — 在容器内显示"有 N 条新记录"提示 banner
 *
 * @param {Object} opts
 *   scrollContainer  — 监听滚动的元素（通常是列表外层容器）
 *   anchorEl         — banner 插入到哪个 DOM 元素之前/之后
 *   position         — 'top' | 'bottom'
 *   countRef         — { value: number } 共享计数引用
 *   labelFn          — (n) => string，生成提示文字
 *   onClickScroll    — banner 被点击时滚动目标元素
 *   bannerId         — banner 的唯一 id，用于去重
 */
export function showPositionBanner({
  scrollContainer,
  anchorEl,
  position = 'top',
  countRef,
  labelFn,
  onClickScroll,
  bannerId,
}) {
  if (!anchorEl || !countRef) return;
  const n = countRef.value;
  if (n <= 0) return;

  // Remove existing banner with same id
  const existingId = `sse-banner-${bannerId}`;
  document.getElementById(existingId)?.remove();

  const banner = document.createElement('div');
  banner.id = existingId;
  banner.className = `sse-position-banner ${position}`;
  banner.setAttribute('role', 'status');
  banner.setAttribute('aria-live', 'polite');

  const label = document.createElement('span');
  label.textContent = labelFn(n);

  const dismiss = document.createElement('button');
  dismiss.className = 'banner-dismiss';
  dismiss.textContent = '×';
  dismiss.title = '关闭';
  dismiss.addEventListener('click', (e) => {
    e.stopPropagation();
    dismissBanner(banner, countRef);
  });

  banner.appendChild(label);
  banner.appendChild(dismiss);

  // Click banner → scroll to new records
  banner.addEventListener('click', () => {
    if (onClickScroll) {
      onClickScroll();
    }
    dismissBanner(banner, countRef);
  });

  // Insert banner
  if (position === 'top') {
    anchorEl.parentNode?.insertBefore(banner, anchorEl);
  } else {
    anchorEl.parentNode?.insertBefore(banner, anchorEl.nextSibling);
  }

  // Auto-dismiss when scrolled to correct position
  if (scrollContainer) {
    const checkScroll = () => {
      const atEdge = position === 'top'
        ? scrollContainer.scrollTop <= 60
        : scrollContainer.scrollHeight - scrollContainer.scrollTop - scrollContainer.clientHeight <= 60;
      if (atEdge) {
        dismissBanner(banner, countRef);
        scrollContainer.removeEventListener('scroll', checkScroll);
      }
    };
    scrollContainer.addEventListener('scroll', checkScroll, { passive: true });
  }
}

function dismissBanner(banner, countRef) {
  if (!banner || !banner.parentNode) return;
  banner.classList.add('dismissing');
  setTimeout(() => banner.remove(), 240);
  if (countRef) countRef.value = 0;
}

export function updateBannerLabel(bannerId, labelFn, countRef) {
  const banner = document.getElementById(`sse-banner-${bannerId}`);
  if (!banner || !countRef) return;
  const label = banner.querySelector('span');
  if (label) label.textContent = labelFn(countRef.value);
}

// ─── Public API ──────────────────────────────────────────────────────────────

window.OmniQASSE = {
  start:             startSSE,
  stop:              stopSSE,
  subscribe:         subscribeSSE,
  pulseBoundaryGlow,
  mountAttention,
  markRead,
  clearScopeUnread,
  showPositionBanner,
  updateBannerLabel,
  runtime:           RUNTIME,
};
