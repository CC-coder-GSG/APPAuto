import { state } from '../state.js';

const listeners = new Map();
let abortController = null;
let reconnectTimer = null;
let reconnectAttempts = 0;
let started = false;
const refreshTimers = new Map();

const RUNTIME = {
  lastEventId: Number(localStorage.getItem('sse_last_event_id') || 0) || 0,
  counters: {
    zentaoNew: 0,
    minePrimary: 0,
    mineSecondaryBugDispatch: 0,
    feedback: 0,
    retest: 0,
    overallBug: 0,
  },
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
    } catch {}
  }, wait);
  refreshTimers.set(key, timer);
}

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
  if (type === 'zentao_sync_created') RUNTIME.counters.zentaoNew += 1;
  if (type === 'workbench_requirement_created') RUNTIME.counters.minePrimary += 1;
  if (type === 'bug_dispatch_created') RUNTIME.counters.mineSecondaryBugDispatch += 1;
  if (type === 'feedback_task_created') RUNTIME.counters.feedback += 1;
  if (type === 'retest_requirement_created') RUNTIME.counters.retest += 1;
  if (type === 'overall_bug_created') RUNTIME.counters.overallBug += 1;
  if (type === 'zentao_sync_deleted' && RUNTIME.counters.zentaoNew > 0) RUNTIME.counters.zentaoNew -= 1;
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
  const cursor = Number(RUNTIME.lastEventId || 0);
  const resp = await fetch(`/api/sse/stream?last_event_id=${cursor}`, {
    method: 'GET',
    headers: { Accept: 'text/event-stream' },
    signal: abortController.signal,
    credentials: 'same-origin',
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`SSE connect failed: ${resp.status}`);
  }

  reconnectAttempts = 0;
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
      bumpCounterByEvent(msg);
      emit(msg.type, msg);
      handleVisibleRefreshByEvent(msg);
    });
  }
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectAttempts += 1;
  const ms = Math.min(15000, 1000 * 2 ** Math.min(5, reconnectAttempts));
  reconnectTimer = setTimeout(() => {
    connect().catch(() => scheduleReconnect());
  }, ms);
}

export function startSSE() {
  if (started) return;
  started = true;
  // Always normalize badges on boot: no number => hidden.
  updateNavBadges();
  connect().catch(() => scheduleReconnect());
}

export function stopSSE() {
  started = false;
  if (abortController) abortController.abort();
  abortController = null;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
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
  // burst-throttle: keep hold phase if events are too dense.
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

window.OmniQASSE = {
  start: startSSE,
  stop: stopSSE,
  subscribe: subscribeSSE,
  pulseBoundaryGlow,
  runtime: RUNTIME,
};
