// Global coordinator for zentao-ai batch tasks.
// - Countdown toast stays visible regardless of which tab the user is on.
// - State persists in localStorage so page reloads can resume awareness.
// - Listens to SSE `zentao_ai_batch_completed` to clear pending tasks.
// - Provides a slot injector: any `<span class="ai-result-slot" data-story-id="123">` gets an "AI结果" button.
// - Exposes window.OmniQAStoryAI.startBatchWatch(batchInfo) for the zentao-ai tab to register a new pending batch.

import { api } from './api.js';

const STORAGE_KEY = 'omniqa_pending_ai_batches_v1';
const CACHE_LATEST_KEY = 'omniqa_ai_latest_cache_v1';
const CHIP_CLASS = 'ai-result-chip';

function injectChipStyles() {
  if (document.getElementById('aiResultChipStyles')) return;
  const style = document.createElement('style');
  style.id = 'aiResultChipStyles';
  style.textContent = `
    .ai-result-chip {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      margin-left: 6px;
      font-size: 10px;
      font-weight: 600;
      line-height: 1;
      padding: 3px 8px;
      border-radius: 999px;
      cursor: pointer;
      user-select: none;
      vertical-align: middle;
      letter-spacing: 0.2px;
      transition: transform .12s ease, box-shadow .12s ease, filter .12s ease;
    }
    .ai-result-chip:hover { transform: translateY(-1px); filter: brightness(1.03); box-shadow: 0 2px 6px rgba(15,23,42,0.12); }
    .ai-result-chip--success { background: linear-gradient(135deg, #ecfdf5, #d1fae5); color: #047857; }
    .ai-result-chip--failed  { background: #fef2f2; color: #b91c1c; }
    .ai-result-chip--pending { background: #f1f5f9; color: #64748b; cursor: default; }
    .ai-result-chip--pending:hover { transform: none; filter: none; box-shadow: none; }
    .ai-result-chip__dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; opacity: .8; }
  `;
  document.head.appendChild(style);
}

const state = {
  batches: new Map(),       // batch_id → {story_ids, started_at, expected_seconds}
  tickHandle: null,
  hostEl: null,
  latestCache: new Map(),   // story_id → {ai_status, id, updated_at}
  lookupsInFlight: new Set(),
};

function now() { return Date.now(); }

function loadPersisted() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return;
    for (const [k, v] of Object.entries(parsed)) {
      if (v && Array.isArray(v.story_ids)) state.batches.set(k, v);
    }
  } catch {}
  try {
    const raw = localStorage.getItem(CACHE_LATEST_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        for (const [k, v] of Object.entries(parsed)) state.latestCache.set(Number(k), v);
      }
    }
  } catch {}
}

function persist() {
  const obj = {};
  for (const [k, v] of state.batches.entries()) obj[k] = v;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
}

function persistLatestCache() {
  const obj = {};
  for (const [k, v] of state.latestCache.entries()) obj[k] = v;
  localStorage.setItem(CACHE_LATEST_KEY, JSON.stringify(obj));
}

function ensureHost() {
  if (state.hostEl && document.body.contains(state.hostEl)) return state.hostEl;
  const el = document.createElement('div');
  el.id = 'aiTaskBanner';
  el.style.cssText = 'position:fixed; right:20px; bottom:20px; z-index:1800; display:flex; flex-direction:column; gap:8px; max-width:360px;';
  document.body.appendChild(el);
  state.hostEl = el;
  return el;
}

function fmtSeconds(sec) {
  const s = Math.max(0, Math.round(sec));
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  if (mm <= 0) return `${ss}s`;
  return `${mm}m${String(ss).padStart(2, '0')}s`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function render() {
  const host = ensureHost();
  host.innerHTML = '';
  const tNow = now();
  for (const [batch_id, info] of state.batches.entries()) {
    const elapsed = (tNow - info.started_at) / 1000;
    const expected = Math.max(60, Number(info.expected_seconds) || 300);
    const remaining = Math.max(0, expected - elapsed);
    const overdue = elapsed > expected + 60;
    const card = document.createElement('div');
    card.style.cssText = `padding:12px 14px; border-radius:10px; box-shadow:0 6px 18px rgba(15,23,42,0.18); background:${overdue ? '#fff7ed' : '#ffffff'}; border:1px solid ${overdue ? '#fdba74' : '#e2e8f0'};`;
    card.innerHTML = `
      <div style="font-size:12px; color:#64748b; margin-bottom:2px;">禅道 AI 处理中</div>
      <div style="font-weight:600; color:#0f172a;">${info.story_ids.length} 条需求</div>
      <div style="margin-top:4px; font-size:12px; color:${overdue ? '#b45309' : '#475569'};">
        ${overdue ? '已超过预估时长，AI 可能仍在处理…' : `剩余约 ${fmtSeconds(remaining)}`}
      </div>
      <div style="margin-top:6px; display:flex; gap:6px; justify-content:flex-end;">
        <button type="button" data-ai-batch-poll="${escapeHtml(batch_id)}" style="font-size:12px; border:1px solid #cbd5e1; background:#f8fafc; border-radius:6px; padding:3px 10px; cursor:pointer;">立即查询</button>
        <button type="button" data-ai-batch-dismiss="${escapeHtml(batch_id)}" style="font-size:12px; border:1px solid #cbd5e1; background:#ffffff; border-radius:6px; padding:3px 10px; cursor:pointer;">稍后再说</button>
      </div>
    `;
    host.appendChild(card);
  }
  host.querySelectorAll('[data-ai-batch-poll]').forEach((btn) => {
    btn.addEventListener('click', () => pollBatch(btn.getAttribute('data-ai-batch-poll')));
  });
  host.querySelectorAll('[data-ai-batch-dismiss]').forEach((btn) => {
    btn.addEventListener('click', () => { state.batches.delete(btn.getAttribute('data-ai-batch-dismiss')); persist(); render(); });
  });
}

function startTicking() {
  if (state.tickHandle) return;
  state.tickHandle = setInterval(() => {
    if (state.batches.size === 0) { clearInterval(state.tickHandle); state.tickHandle = null; render(); return; }
    render();
  }, 2000);
}

function showCompletionToast({ batch_id, success, failed, error }) {
  const host = ensureHost();
  const card = document.createElement('div');
  const ok = !error && failed === 0;
  card.style.cssText = `padding:12px 14px; border-radius:10px; box-shadow:0 6px 18px rgba(15,23,42,0.18); background:${ok ? '#f0fdf4' : failed > 0 ? '#fef2f2' : '#ffffff'}; border:1px solid ${ok ? '#86efac' : failed > 0 ? '#fecaca' : '#e2e8f0'};`;
  card.innerHTML = `
    <div style="font-size:12px; color:#64748b; margin-bottom:2px;">AI 处理完成</div>
    <div style="font-weight:600; color:#0f172a;">成功 ${Number(success) || 0} · 失败 ${Number(failed) || 0}</div>
    ${error ? `<div style="margin-top:4px; font-size:12px; color:#b91c1c;">${escapeHtml(error)}</div>` : ''}
    <div style="margin-top:6px; display:flex; gap:6px; justify-content:flex-end;">
      <button type="button" data-ai-view-batch="${escapeHtml(batch_id)}" style="font-size:12px; border:1px solid #2563eb; background:#2563eb; color:#fff; border-radius:6px; padding:3px 10px; cursor:pointer;">查看结果</button>
      <button type="button" data-ai-dismiss-toast style="font-size:12px; border:1px solid #cbd5e1; background:#ffffff; border-radius:6px; padding:3px 10px; cursor:pointer;">关闭</button>
    </div>
  `;
  host.appendChild(card);
  card.querySelector('[data-ai-view-batch]').addEventListener('click', async () => {
    await viewBatchResults(batch_id);
    card.remove();
  });
  card.querySelector('[data-ai-dismiss-toast]').addEventListener('click', () => card.remove());
  setTimeout(() => { if (card.isConnected) card.remove(); }, 60000);
}

async function viewBatchResults(batchId) {
  try {
    const res = await api(`/zentao/ai/batch/${batchId}`);
    const data = await res.json();
    const firstSuccess = (data.results || []).find((r) => r.ai_status === 'success');
    const target = firstSuccess || (data.results || [])[0];
    if (target && window.OmniQAStoryAI && typeof window.OmniQAStoryAI.openForStory === 'function') {
      window.OmniQAStoryAI.openForStory(target.story_id);
    } else {
      window.showMessage && window.showMessage('批次无可查看的结果', 'info');
    }
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '查询批次失败', 'error');
  }
}

async function pollBatch(batchId) {
  try {
    const res = await api(`/zentao/ai/batch/${batchId}`);
    const data = await res.json();
    if (!data) return;
    if (data.pending === 0) {
      finishBatch(batchId, { success: data.success, failed: data.failed });
    } else {
      window.showMessage && window.showMessage(`批次仍在处理：pending ${data.pending}`, 'info');
    }
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '查询失败', 'error');
  }
}

function finishBatch(batchId, payload) {
  const info = state.batches.get(batchId);
  state.batches.delete(batchId);
  persist();
  render();
  showCompletionToast({ batch_id: batchId, success: payload.success, failed: payload.failed, error: payload.error });
  if (info && Array.isArray(info.story_ids)) {
    invalidateLatestCache(info.story_ids);
    refreshSlots();
  }
}

function startBatchWatch(info) {
  if (!info || !info.batch_id) return;
  state.batches.set(info.batch_id, {
    story_ids: Array.isArray(info.story_ids) ? info.story_ids : [],
    started_at: info.started_at || now(),
    expected_seconds: Number(info.expected_duration_seconds) || 300,
  });
  persist();
  render();
  startTicking();
}

function subscribeSSE() {
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') {
    setTimeout(subscribeSSE, 500);
    return;
  }
  window.OmniQASSE.subscribe('zentao_ai_batch_completed', ({ payload }) => {
    if (!payload || !payload.batch_id) return;
    finishBatch(payload.batch_id, {
      success: payload.success,
      failed: payload.failed,
      error: payload.error,
    });
  });
}

// ─── Slot injector ──────────────────────────────────────────────────────────

function invalidateLatestCache(storyIds) {
  for (const sid of storyIds) state.latestCache.delete(Number(sid));
  persistLatestCache();
}

async function fetchLatestFor(storyIds) {
  const unique = Array.from(new Set(storyIds.map(Number).filter(Boolean)));
  const missing = unique.filter((sid) => !state.latestCache.has(sid) && !state.lookupsInFlight.has(sid));
  if (!missing.length) return;
  missing.forEach((sid) => state.lookupsInFlight.add(sid));
  try {
    const res = await api('/zentao/ai/story/batch-latest', {
      method: 'POST',
      body: { story_ids: missing },
    });
    const data = await res.json();
    const latest = (data && data.latest) || {};
    for (const sid of missing) {
      const entry = latest[sid] || latest[String(sid)] || null;
      state.latestCache.set(sid, entry ? { ai_status: entry.ai_status, id: entry.id, updated_at: entry.updated_at } : null);
    }
    persistLatestCache();
  } catch {
    // don't poison the cache — leave unknown so the button still works
  } finally {
    missing.forEach((sid) => state.lookupsInFlight.delete(sid));
  }
}

function chipHtml(storyId, cacheEntry) {
  if (!cacheEntry || !cacheEntry.ai_status) return '';
  const status = cacheEntry.ai_status;
  if (status === 'success') {
    return `<span class="${CHIP_CLASS} ${CHIP_CLASS}--success" data-ai-story-id="${storyId}" title="点击查看 AI 生成的测试信息"><span class="${CHIP_CLASS}__dot"></span>AI</span>`;
  }
  if (status === 'failed') {
    return `<span class="${CHIP_CLASS} ${CHIP_CLASS}--failed" data-ai-story-id="${storyId}" title="点击查看失败原因"><span class="${CHIP_CLASS}__dot"></span>AI</span>`;
  }
  if (status === 'pending') {
    return `<span class="${CHIP_CLASS} ${CHIP_CLASS}--pending" title="AI 处理中"><span class="${CHIP_CLASS}__dot"></span>AI</span>`;
  }
  return '';
}

function refreshSlots(root) {
  const scope = root || document;
  const slots = scope.querySelectorAll('.ai-result-slot[data-story-id]');
  if (!slots.length) return;
  const ids = [];
  slots.forEach((el) => {
    const sid = Number(el.dataset.storyId);
    if (!sid) return;
    ids.push(sid);
  });
  fetchLatestFor(ids).then(() => {
    slots.forEach((el) => {
      const sid = Number(el.dataset.storyId);
      if (!sid) return;
      const entry = state.latestCache.get(sid);
      el.innerHTML = chipHtml(sid, entry);
    });
  });
}

function bindDelegatedClicks() {
  document.addEventListener('click', (e) => {
    const el = e.target && e.target.closest ? e.target.closest(`.${CHIP_CLASS}[data-ai-story-id]`) : null;
    if (!el) return;
    const sid = Number(el.getAttribute('data-ai-story-id'));
    if (!sid) return;
    if (window.OmniQAStoryAI && typeof window.OmniQAStoryAI.openForStory === 'function') {
      window.OmniQAStoryAI.openForStory(sid);
    }
  });
}

function installMutationObserver() {
  const obs = new MutationObserver((mutations) => {
    let needsRefresh = false;
    for (const m of mutations) {
      if (!m.addedNodes) continue;
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.classList && node.classList.contains('ai-result-slot')) { needsRefresh = true; break; }
        if (node.querySelector && node.querySelector('.ai-result-slot')) { needsRefresh = true; break; }
      }
      if (needsRefresh) break;
    }
    if (needsRefresh) refreshSlots();
  });
  obs.observe(document.body, { childList: true, subtree: true });
}

// ─── Resume pending batches on page load ───────────────────────────────────

async function resumePending() {
  const entries = Array.from(state.batches.entries());
  for (const [batchId, info] of entries) {
    try {
      const res = await api(`/zentao/ai/batch/${batchId}`);
      const data = await res.json();
      if (data && data.pending === 0) {
        finishBatch(batchId, { success: data.success, failed: data.failed });
      }
    } catch {
      // likely 404 (server restarted + batch DB gone) — drop
      state.batches.delete(batchId);
      persist();
    }
  }
  if (state.batches.size) { render(); startTicking(); }
}

// ─── Bootstrap ──────────────────────────────────────────────────────────────

injectChipStyles();
loadPersisted();
subscribeSSE();
bindDelegatedClicks();
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => { installMutationObserver(); refreshSlots(); resumePending(); });
} else {
  installMutationObserver();
  refreshSlots();
  resumePending();
}

window.OmniQAStoryAI = Object.assign(window.OmniQAStoryAI || {}, {
  startBatchWatch,
  refreshSlots,
  invalidateLatestCache,
});
