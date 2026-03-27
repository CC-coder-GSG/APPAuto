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
  lastEventId: Number(localStorage.getItem('sse_last_event_id') || 0) || 0,
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

function updateConnectionIndicator() {
  const el = document.getElementById('sseConnectionState');
  if (!el) return;
  const stateTextMap = {
    connected: '实时已连接',
    reconnecting: '实时重连中',
    connecting: '实时连接中',
    disconnected: '实时未连接',
  };
  const cur = String(RUNTIME.connectionState || 'disconnected');
  el.textContent = stateTextMap[cur] || stateTextMap.disconnected;
  el.classList.remove('hidden', 'connected', 'reconnecting', 'disconnected');
  if (cur === 'connected') el.classList.add('connected');
  else if (cur === 'reconnecting' || cur === 'connecting') el.classList.add('reconnecting');
  else el.classList.add('disconnected');
}

function setConnectionState(next) {
  const stateValue = String(next || 'disconnected');
  if (RUNTIME.connectionState === stateValue) return;
  RUNTIME.connectionState = stateValue;
  updateConnectionIndicator();
}

function debugLog(...args) {
  if (!SSE_DEBUG) return;
  console.debug('[SSE]', ...args);
}

function debugError(...args) {
  if (!SSE_DEBUG) return;
  console.error('[SSE]', ...args);
}

const SCOPE_COUNTER_MAP = {
  zentao_sync: 'zentaoNew',
  mine_requirement: 'minePrimary',
  mine_bug_dispatch: 'mineSecondaryBugDispatch',
  feedback_task: 'feedback',
  retest_requirement: 'retest',
  overall_bug: 'overallBug',
};

function emit(type, message) {
  const direct = listeners.get(type) || [];
  const wildcard = listeners.get('*') || [];
  [...direct, ...wildcard].forEach((cb) => {
    try {
      cb(message);
    } catch (err) {
      console.error('SSE listener error', err);
    }
  });
}

function getUnreadSet(scope) {
  if (!unreadByScope.has(scope)) unreadByScope.set(scope, new Set());
  return unreadByScope.get(scope);
}

function adjustCounterByScope(scope, delta) {
  const counterKey = SCOPE_COUNTER_MAP[scope];
  if (!counterKey) return;
  const next = Math.max(0, Number(RUNTIME.counters[counterKey] || 0) + delta);
  RUNTIME.counters[counterKey] = next;
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
  if (type === 'zentao_sync_created' && item?.id) markUnread('zentao_sync', item.id);
  if (type === 'workbench_requirement_created' && payload?.id) markUnread('mine_requirement', payload.id);
  if (type === 'bug_dispatch_created' && payload?.id) markUnread('mine_bug_dispatch', payload.id);
  if (type === 'feedback_task_created' && payload?.id) markUnread('feedback_task', payload.id);
  if (type === 'retest_requirement_created' && payload?.id) markUnread('retest_requirement', payload.id);
  if (type === 'overall_bug_created' && payload?.id) markUnread('overall_bug', payload.id);
  if (type === 'zentao_sync_deleted' && payload?.id) markRead('zentao_sync', payload.id);
}

function visibleTabName() {
  const names = ['assign', 'mine', 'feedback', 'retest', 'stage5', 'field-test', 'build-records', 'zentao-sync', 'report', 'activity', 'data', 'dispatch'];
  return names.find((n) => {
    const el = document.getElementById(`tab-${n}`);
    return el && !el.classList.contains('hidden');
  }) || '';
}

function debounceRefresh(key, fn, wait = 700) {
  const old = refreshTimers.get(key);
  if (old) clearTimeout(old);
  const timer = setTimeout(() => {
    refreshTimers.delete(key);
    try {
      fn();
    } catch {
      // no-op
    }
  }, wait);
  refreshTimers.set(key, timer);
}

// Visibility-bound auto-refresh mapping: keep tab handlers lightweight and avoid full-page refresh storms.
function handleVisibleRefreshByEvent(message) {
  const type = String(message?.type || '');
  const visible = visibleTabName();

  if ((type === 'workbench_requirement_created' || type === 'workbench_testcase_created') && visible === 'mine' && typeof window.loadMyWorkbench === 'function') {
    debounceRefresh('mine', () => window.loadMyWorkbench());
  }

  if (type === 'retest_requirement_status_changed' && visible === 'retest' && typeof window.loadRetest === 'function') {
    debounceRefresh('retest', () => window.loadRetest());
  }

  if ((type === 'overall_bug_created' || type === 'overall_bug_closed') && visible === 'stage5' && typeof window.loadStage5 === 'function') {
    debounceRefresh('stage5', () => window.loadStage5());
  }

  if ((type === 'feedback_task_created' || type === 'feedback_task_updated') && visible === 'feedback' && typeof window.loadFeedbackBoard === 'function') {
    debounceRefresh('feedback', () => window.loadFeedbackBoard());
  }

  if ((type === 'bug_dispatch_created' || type === 'bug_dispatch_updated') && visible === 'dispatch' && typeof window.loadDispatchedAll === 'function') {
    debounceRefresh('dispatch', () => window.loadDispatchedAll());
  }

  if ((type === 'field_test_record_created' || type === 'field_test_record_updated') && visible === 'field-test' && typeof window.loadFieldTestBoard === 'function') {
    debounceRefresh('field-test', () => window.loadFieldTestBoard());
  }
}

function saveLastEventId(id) {
  if (!id) return;
  RUNTIME.lastEventId = Number(id) || RUNTIME.lastEventId;
  localStorage.setItem('sse_last_event_id', String(RUNTIME.lastEventId));
}

function updateNavBadges() {
  const zentao = document.getElementById('tabZentaoSyncBadge');
  if (zentao) {
    zentao.textContent = RUNTIME.counters.zentaoNew > 0 ? String(RUNTIME.counters.zentaoNew) : '';
    zentao.classList.toggle('hidden', RUNTIME.counters.zentaoNew <= 0);
  }

  const minePrimary = document.getElementById('tabMinePrimaryBadge');
  if (minePrimary) {
    minePrimary.textContent = RUNTIME.counters.minePrimary > 0 ? String(RUNTIME.counters.minePrimary) : '';
    minePrimary.classList.toggle('hidden', RUNTIME.counters.minePrimary <= 0);
  }

  const mineSecondary = document.getElementById('tabMineSecondaryBadge');
  if (mineSecondary) {
    mineSecondary.textContent = RUNTIME.counters.mineSecondaryBugDispatch > 0 ? String(RUNTIME.counters.mineSecondaryBugDispatch) : '';
    mineSecondary.classList.toggle('hidden', RUNTIME.counters.mineSecondaryBugDispatch <= 0);
  }

  const feedback = document.getElementById('tabFeedbackBadge');
  if (feedback) {
    feedback.textContent = RUNTIME.counters.feedback > 0 ? String(RUNTIME.counters.feedback) : '';
    feedback.classList.toggle('hidden', RUNTIME.counters.feedback <= 0);
  }

  const retest = document.getElementById('tabRetestBadge');
  if (retest) {
    retest.textContent = RUNTIME.counters.retest > 0 ? String(RUNTIME.counters.retest) : '';
    retest.classList.toggle('hidden', RUNTIME.counters.retest <= 0);
  }

  const stage5 = document.getElementById('tabStage5Badge');
  if (stage5) {
    stage5.textContent = RUNTIME.counters.overallBug > 0 ? String(RUNTIME.counters.overallBug) : '';
    stage5.classList.toggle('hidden', RUNTIME.counters.overallBug <= 0);
  }
}

function bumpCounterByEvent(message) {
  const type = String(message?.type || '');
  const payload = message?.payload || {};
  if (payload?.assignee_id && Number(payload.assignee_id) !== Number(state.currentUser?.id || 0)) return;
  if (type === '__noop__') {
    // no-op placeholder
  }
  updateNavBadges();
}

function parseSSEChunk(buffer, onMessage) {
  const parts = buffer.split('\n\n');
  const rest = parts.pop() || '';
  parts.forEach((part) => {
    const lines = part.split('\n');
    let id = null;
    let event = 'message';
    const dataLines = [];
    lines.forEach((line) => {
      if (line.startsWith('id:')) id = line.slice(3).trim();
      else if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    });
    const raw = dataLines.join('\n');
    if (!raw) return;
    let payload = {};
    try {
      payload = JSON.parse(raw);
    } catch {
      payload = { raw };
    }
    onMessage({ id: Number(id || payload.id || 0) || null, type: event || payload.type || 'message', ...payload });
  });
  return rest;
}

async function connect() {
  if (abortController) abortController.abort();
  abortController = new AbortController();
  setConnectionState(reconnectAttempts > 0 ? 'reconnecting' : 'connecting');

  const cursor = Number(RUNTIME.lastEventId || 0);
  const token = String(localStorage.getItem('token') || '').trim();
  const headers = { Accept: 'text/event-stream' };
  if (token) headers.Authorization = `Bearer ${token}`;

  debugLog('connect start', { cursor, reconnectAttempts, hasToken: !!token });

  const resp = await fetch(`/api/sse/stream?last_event_id=${cursor}`, {
    method: 'GET',
    headers,
    signal: abortController.signal,
    credentials: 'same-origin',
  });

  if (resp.status === 401) {
    debugError('connect unauthorized', { status: resp.status });
    localStorage.removeItem('token');
    setConnectionState('disconnected');
    started = false;
    if (window.location.pathname !== '/login') {
      window.location.href = '/login';
    }
    throw new Error('SSE unauthorized (401)');
  }

  if (!resp.ok || !resp.body) {
    debugError('connect bad response', { status: resp.status, ok: resp.ok, hasBody: !!resp.body });
    throw new Error(`SSE connect failed: ${resp.status}`);
  }

  reconnectAttempts = 0;
  setConnectionState('connected');
  debugLog('connect success', { cursor });

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
      debugLog('event', { id: msg.id || null, type: msg.type || 'message' });
      queueUnreadByEvent(msg);
      bumpCounterByEvent(msg);
      emit(msg.type, msg);
      handleVisibleRefreshByEvent(msg);
    });
  }

  debugLog('stream disconnected');
  if (started) setConnectionState('reconnecting');
}

function scheduleReconnect() {
  if (!started) return;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectAttempts += 1;
  setConnectionState('reconnecting');
  const ms = Math.min(15000, 1000 * 2 ** Math.min(5, reconnectAttempts));
  debugLog('schedule reconnect', { reconnectAttempts, delayMs: ms });
  reconnectTimer = setTimeout(() => {
    connect().catch((err) => {
      if (String(err?.message || '').includes('401')) {
        debugError('reconnect stopped (unauthorized)', err);
        return;
      }
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
    if (String(err?.message || '').includes('401')) {
      debugError('initial connect unauthorized', err);
      return;
    }
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

export function subscribeSSE(type, callback) {
  const arr = listeners.get(type) || [];
  arr.push(callback);
  listeners.set(type, arr);
  return () => {
    const list = listeners.get(type) || [];
    listeners.set(type, list.filter((fn) => fn !== callback));
  };
}

function beginGlowPhase(el, cls) {
  el.classList.remove('sse-glow-enter', 'sse-glow-hold', 'sse-glow-exit');
  el.classList.add('sse-glow', cls);
}

export function pulseBoundaryGlow(el, tone = 'blue') {
  if (!el) return;
  const now = Date.now();
  const last = Number(el.dataset.ssePulseTs || 0);
  if (now - last < 1200 && el.classList.contains('sse-glow')) {
    el.dataset.ssePulseTs = String(now);
    el.classList.add('sse-glow-hold');
    return;
  }
  el.dataset.ssePulseTs = String(now);
  el.classList.remove('sse-glow-blue', 'sse-glow-green', 'sse-glow-purple');
  el.classList.add(`sse-glow-${tone}`);
  beginGlowPhase(el, 'sse-glow-enter');
  setTimeout(() => beginGlowPhase(el, 'sse-glow-hold'), 520);
  setTimeout(() => beginGlowPhase(el, 'sse-glow-exit'), 2650);
  setTimeout(() => el.classList.remove('sse-glow', 'sse-glow-enter', 'sse-glow-hold', 'sse-glow-exit', `sse-glow-${tone}`), 3450);
}

function ensureAttentionObserver() {
  if (attentionObserver) return attentionObserver;
  attentionObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      const el = entry.target;
      const meta = attentionObserved.get(el);
      if (!meta) return;
      meta.inView = !!entry.isIntersecting;
      if (meta.inView && getUnreadSet(meta.scope).has(meta.key)) {
        pulseBoundaryGlow(el, meta.tone || 'blue');
      }
    });
  }, { threshold: 0.25 });
  return attentionObserver;
}

export function mountAttention(el, { scope, key, tone = 'blue', hoverDelayMs = 420 } = {}) {
  if (!el || !scope || key == null) return;
  const meta = { scope, key: String(key), tone, inView: false, read: false, hoverTimer: null };
  attentionObserved.set(el, meta);
  ensureAttentionObserver().observe(el);
  if (getUnreadSet(scope).has(meta.key)) {
    pulseBoundaryGlow(el, tone);
  }

  const clearTimer = () => {
    if (meta.hoverTimer) {
      clearTimeout(meta.hoverTimer);
      meta.hoverTimer = null;
    }
  };

  el.addEventListener('mouseenter', () => {
    clearTimer();
    meta.hoverTimer = setTimeout(() => {
      if (meta.read) return;
      meta.read = true;
      markRead(scope, meta.key);
      el.classList.remove('sse-glow', 'sse-glow-enter', 'sse-glow-hold', 'sse-glow-exit', 'sse-glow-blue', 'sse-glow-green', 'sse-glow-purple');
    }, hoverDelayMs);
  });

  el.addEventListener('mouseleave', () => clearTimer());
}

window.OmniQASSE = {
  start: startSSE,
  stop: stopSSE,
  subscribe: subscribeSSE,
  pulseBoundaryGlow,
  mountAttention,
  markRead,
  clearScopeUnread,
  runtime: RUNTIME,
};
