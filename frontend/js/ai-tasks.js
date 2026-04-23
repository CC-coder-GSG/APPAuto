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
  // 注意：很多 button.* 用 !important 是为了对抗全局 style.css 里的
  // `button { background: #0071e3 !important; color:#fff !important; ... }`，
  // 否则 inline 样式会被覆盖，"立即查询"看起来跟另一个按钮一模一样。
  style.textContent = `
    .ai-result-chip {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-left: 6px;
      font-size: 11px;
      font-weight: 600;
      line-height: 1;
      padding: 3px 9px;
      border-radius: 999px;
      cursor: pointer;
      user-select: none;
      vertical-align: middle;
      letter-spacing: 0.3px;
      transition: transform .12s ease, box-shadow .12s ease, filter .12s ease;
    }
    .ai-result-chip:hover { transform: translateY(-1px); filter: brightness(1.04); box-shadow: 0 3px 8px rgba(15,23,42,0.14); }
    .ai-result-chip--success { background: linear-gradient(135deg, #ecfdf5, #bbf7d0); color: #047857; border: 1px solid #86efac; }
    .ai-result-chip--failed  { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
    .ai-result-chip--pending { background: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0; cursor: default; }
    .ai-result-chip--pending:hover { transform: none; filter: none; box-shadow: none; }
    .ai-result-chip__dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; opacity: .85; box-shadow: 0 0 0 2px rgba(255,255,255,0.45); }

    button.aiqa-btn {
      font-size: 12px !important;
      font-weight: 600 !important;
      padding: 4px 12px !important;
      border-radius: 6px !important;
      min-height: 0 !important;
      box-shadow: none !important;
      letter-spacing: 0 !important;
      cursor: pointer;
    }
    button.aiqa-btn--primary {
      background: #2563eb !important;
      color: #ffffff !important;
      border: 1px solid #1d4ed8 !important;
    }
    button.aiqa-btn--primary:hover { background: #1d4ed8 !important; }
    button.aiqa-btn--secondary {
      background: #ffffff !important;
      color: #334155 !important;
      border: 1px solid #cbd5e1 !important;
    }
    button.aiqa-btn--secondary:hover { background: #f1f5f9 !important; color: #1e293b !important; }

    /* Android-style top toast */
    #aiTopToastHost {
      position: fixed;
      top: 28px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 2000;
      display: flex;
      flex-direction: column;
      gap: 10px;
      align-items: center;
      pointer-events: none;
      max-width: calc(100vw - 40px);
    }
    .aiqa-toast {
      pointer-events: auto;
      min-width: 280px;
      max-width: 460px;
      padding: 13px 18px;
      border-radius: 999px;
      box-shadow: 0 12px 32px rgba(15, 23, 42, 0.22);
      font-size: 14px;
      font-weight: 600;
      color: #ffffff;
      display: flex;
      align-items: center;
      gap: 10px;
      animation: aiqaToastIn 320ms cubic-bezier(.2,.9,.3,1.2);
    }
    .aiqa-toast.aiqa-toast--exit { animation: aiqaToastOut 240ms ease forwards; }
    .aiqa-toast--success { background: linear-gradient(135deg, #16a34a, #15803d); }
    .aiqa-toast--error   { background: linear-gradient(135deg, #dc2626, #b91c1c); }
    .aiqa-toast--info    { background: linear-gradient(135deg, #2563eb, #1d4ed8); }
    .aiqa-toast__icon {
      width: 22px; height: 22px;
      border-radius: 50%;
      background: rgba(255,255,255,0.22);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      flex-shrink: 0;
    }
    .aiqa-toast__action {
      margin-left: 6px;
      font-size: 13px;
      font-weight: 700;
      color: #ffffff;
      background: rgba(255,255,255,0.18);
      border: none !important;
      padding: 4px 12px !important;
      border-radius: 999px !important;
      cursor: pointer;
      min-height: 0 !important;
      box-shadow: none !important;
    }
    .aiqa-toast__action:hover { background: rgba(255,255,255,0.32) !important; }
    @keyframes aiqaToastIn  { from { opacity:0; transform: translateY(-14px); } to { opacity:1; transform:none; } }
    @keyframes aiqaToastOut { from { opacity:1; transform:none; } to { opacity:0; transform: translateY(-14px); } }
  `;
  document.head.appendChild(style);
}

const state = {
  batches: new Map(),       // batch_id → {story_ids, started_at, expected_seconds, last_poll_ts}
  tickHandle: null,
  hostEl: null,
  toastHostEl: null,
  latestCache: new Map(),   // story_id → {ai_status, id, updated_at}
  lookupsInFlight: new Set(),
  pollInFlight: new Set(),  // batch_ids currently being polled — avoid stacking
};

// Fallback polling interval (ms). 防止 SSE 事件丢失（断线重连缝隙、handler 异常等）
// 导致倒计时永远停不下来。SSE 仍是主通道，这只是兜底。
const FALLBACK_POLL_MS = 20000;

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

function ensureToastHost() {
  if (state.toastHostEl && document.body.contains(state.toastHostEl)) return state.toastHostEl;
  const el = document.createElement('div');
  el.id = 'aiTopToastHost';
  document.body.appendChild(el);
  state.toastHostEl = el;
  return el;
}

function showTopToast({ tone = 'success', message, actionLabel, onAction, durationMs = 3500 }) {
  const host = ensureToastHost();
  const toast = document.createElement('div');
  toast.className = `aiqa-toast aiqa-toast--${tone}`;
  const icon = tone === 'success' ? '✓' : tone === 'error' ? '!' : 'i';
  toast.innerHTML = `
    <span class="aiqa-toast__icon">${icon}</span>
    <span class="aiqa-toast__msg"></span>
  `;
  toast.querySelector('.aiqa-toast__msg').textContent = String(message || '');
  if (actionLabel && typeof onAction === 'function') {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'aiqa-toast__action';
    btn.textContent = actionLabel;
    btn.addEventListener('click', () => {
      try { onAction(); } catch {}
      dismiss();
    });
    toast.appendChild(btn);
  }
  host.appendChild(toast);

  let dismissed = false;
  const dismiss = () => {
    if (dismissed) return;
    dismissed = true;
    toast.classList.add('aiqa-toast--exit');
    setTimeout(() => toast.remove(), 260);
  };
  setTimeout(dismiss, Math.max(1500, durationMs));
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
      <div style="margin-top:8px; display:flex; gap:8px; justify-content:flex-end;">
        <button type="button" class="aiqa-btn aiqa-btn--secondary" data-ai-batch-dismiss="${escapeHtml(batch_id)}">稍后再说</button>
        <button type="button" class="aiqa-btn aiqa-btn--primary" data-ai-batch-poll="${escapeHtml(batch_id)}">立即查询</button>
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
    fallbackPollIfStale();
  }, 2000);
}

// SSE 兜底：每 FALLBACK_POLL_MS 给每个 pending batch 主动查一次。
// 若 SSE 在不知不觉中漏掉了 zentao_ai_batch_completed 事件，这里会补上。
function fallbackPollIfStale() {
  const t = now();
  for (const [batch_id, info] of state.batches.entries()) {
    if (state.pollInFlight.has(batch_id)) continue;
    const last = Number(info.last_poll_ts || 0);
    if (t - last < FALLBACK_POLL_MS) continue;
    info.last_poll_ts = t;
    state.pollInFlight.add(batch_id);
    api(`/zentao/ai/batch/${batch_id}`)
      .then((res) => res.json())
      .then((data) => {
        if (data && data.pending === 0) {
          finishBatch(batch_id, { success: data.success, failed: data.failed });
        }
      })
      .catch(() => { /* 服务器重启等，下一轮再试 */ })
      .finally(() => { state.pollInFlight.delete(batch_id); });
  }
}

function showCompletionToast({ batch_id, success, failed, error }) {
  const successCount = Number(success) || 0;
  const failedCount = Number(failed) || 0;
  if (error) {
    showTopToast({
      tone: 'error',
      message: `AI 处理失败：${error}`,
      durationMs: 6000,
    });
    return;
  }
  if (successCount > 0 && failedCount === 0) {
    showTopToast({
      tone: 'success',
      message: `AI 用例生成完成，成功 ${successCount} 条`,
      actionLabel: '查看结果',
      onAction: () => viewBatchResults(batch_id),
      durationMs: 4500,
    });
    return;
  }
  if (successCount > 0 && failedCount > 0) {
    showTopToast({
      tone: 'info',
      message: `AI 完成：成功 ${successCount} · 失败 ${failedCount}`,
      actionLabel: '查看结果',
      onAction: () => viewBatchResults(batch_id),
      durationMs: 6000,
    });
    return;
  }
  showTopToast({
    tone: 'error',
    message: `AI 处理失败，全部 ${failedCount} 条未生成`,
    durationMs: 6000,
  });
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
  const startedAt = info.started_at || now();
  state.batches.set(info.batch_id, {
    story_ids: Array.isArray(info.story_ids) ? info.story_ids : [],
    started_at: startedAt,
    expected_seconds: Number(info.expected_duration_seconds) || 300,
    // 把 last_poll_ts 设到启动时间，避免刚提交就立即去 poll；
    // 等 FALLBACK_POLL_MS (20s) 之后兜底轮询才上场。
    last_poll_ts: startedAt,
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
