import { beginActivity } from './workbench-activity.js';

// 禅道写操作的统一壳：提交 → 失败/不确定时回读核对 → 完成后刷新。
// 2026-11-24 v2：
//  - 自建等待弹窗重排（状态徽标 / 标题 / 说明 / 进度条 / 已等待时长 / 操作按钮），失败与待核实各有配色；
//  - 右下角操作坞改为卡片列表；
//  - 触发按钮锁定：页面上声明了 data-op-key="task:123" 的按钮，在该对象的操作「进行中 / 核对中 /
//    待核实」期间统一禁用并提示，被点击的那颗显示转圈；列表重渲染后自动重新套用（MutationObserver），
//    捕获阶段再拦一道，避免在确认框 / 工时输入框弹出之后才被拒绝。

const operations = new Map();
let sequence = 0;
export function prepareOperationModal(modal) {
  modal?.querySelector('.wb-operation-status')?.remove();
  modal?.querySelectorAll('[data-operation-disabled]').forEach(control => {
    control.disabled = control.dataset.operationWasDisabled === 'true';
    delete control.dataset.operationDisabled;
    delete control.dataset.operationWasDisabled;
  });
}
const visible = el => !!el && !el.hidden && el.getClientRects().length > 0;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const BUSY = ['running', 'checking'];
// 锁定触发按钮的状态：待核实时同样不允许再次提交（与 runZentaoOperation 的去重规则一致）
const LOCKING = ['running', 'checking', 'uncertain'];
export const operationPending = key => [...operations.values()].some(op => (op.key === key || op.locks?.includes(key)) && BUSY.includes(op.state));
const blockingOperation = key => [...operations.values()].find(op => (op.key === key || op.locks?.includes(key)) && LOCKING.includes(op.state));

const ICON = {
  spin: '<span class="wb-spin" aria-hidden="true"></span>',
  warn: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="m21.7 18-8-14a2 2 0 0 0-3.5 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3Z"/></svg>',
  fail: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>',
};
const STATE_TEXT = {
  running: { title: '正在同步禅道', sub: '正在把操作提交到禅道，禅道响应较慢时可能需要十几秒。' },
  checking: { title: '正在核对结果', sub: '正在读取禅道最新状态，确认操作是否已生效。' },
  uncertain: { title: '结果待核实', sub: '暂时无法确认禅道是否已执行该操作，为避免重复提交，请先核对结果。' },
  failed: { title: '操作未完成', sub: '禅道没有执行这次操作，可以检查后重试。' },
};

function injectStyles() {
  if (document.getElementById('zentaoOperationStyle')) return;
  const style = document.createElement('style');
  style.id = 'zentaoOperationStyle';
  style.textContent = `
    .zop-overlay{position:fixed;inset:0;z-index:10020;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(28,25,23,.38);-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px);animation:zopFade .16s ease-out}
    @keyframes zopFade{from{opacity:0}} @keyframes zopRise{from{opacity:0;transform:translateY(8px) scale(.985)}}
    .zop-dialog{width:100%;max-width:440px;padding:22px 22px 18px;border-radius:16px;background:var(--w-raised,#fff);color:var(--w-ink,#1c1917);box-shadow:0 24px 60px rgba(28,25,23,.28),0 0 0 1px rgba(28,25,23,.06);animation:zopRise .24s cubic-bezier(.22,1,.36,1)}
    .zop-head{display:flex;align-items:flex-start;gap:14px}
    .zop-badge{flex:0 0 auto;display:grid;place-items:center;width:40px;height:40px;border-radius:12px;background:rgba(var(--w-accent-rgb,234,88,12),.12);color:var(--w-accent,#cc4e0b)}
    .zop-badge .wb-spin{width:18px;height:18px;border-width:2.5px;border-color:rgba(var(--w-accent-rgb,234,88,12),.25);border-top-color:var(--w-accent,#cc4e0b)}
    .zop-badge svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
    .zop-dialog[data-state=uncertain] .zop-badge{background:rgba(245,158,11,.14);color:#b45309}
    .zop-dialog[data-state=failed] .zop-badge{background:rgba(220,38,38,.10);color:#b91c1c}
    .zop-titles{flex:1;min-width:0}
    .zop-kicker{margin:0 0 2px;font-size:12px;color:var(--w-ink-faint,#a8a29e)}
    .zop-titles h3{margin:0!important;font-size:16px!important;font-weight:650!important;line-height:1.4!important;color:var(--w-ink,#1c1917)!important;word-break:break-word}
    .zop-sub{margin:4px 0 0;font-size:13px;line-height:1.6;color:var(--w-ink-soft,#57534e)}
    .zop-bar{position:relative;height:4px;margin:18px 0 0;overflow:hidden;border-radius:999px;background:rgba(28,25,23,.07)}
    .zop-bar::after{content:"";position:absolute;top:0;bottom:0;left:-40%;width:40%;border-radius:inherit;background:var(--w-accent,#cc4e0b);animation:zopSlide 1.3s cubic-bezier(.4,0,.2,1) infinite}
    @keyframes zopSlide{to{left:100%}}
    .zop-dialog:not([data-state=running]):not([data-state=checking]) .zop-bar{display:none}
    .zop-message{margin:16px 0 0;padding:10px 12px;border-radius:10px;font-size:13px;line-height:1.6;word-break:break-word}
    .zop-message:empty{display:none}
    .zop-dialog[data-state=uncertain] .zop-message{background:rgba(245,158,11,.10);color:#92400e}
    .zop-dialog[data-state=failed] .zop-message{background:rgba(220,38,38,.07);color:#991b1b}
    .zop-hint{margin:14px 0 0;font-size:12px;line-height:1.6;color:var(--w-ink-faint,#a8a29e)}
    .zop-foot{display:flex;align-items:center;gap:10px;margin-top:18px}
    .zop-elapsed{font-size:12px;color:var(--w-ink-faint,#a8a29e);font-variant-numeric:tabular-nums}
    .zop-actions{display:flex;flex-wrap:wrap;gap:8px;margin-left:auto}
    .zop-actions button{height:34px!important;min-height:0!important;margin:0!important;padding:0 16px!important;font-size:13px!important;border-radius:8px!important}
    .zop-actions button.zop-ghost{background:var(--w-raised,#fff)!important;color:var(--w-ink,#1c1917)!important;border:1px solid var(--w-hairline-strong,rgba(28,25,23,.16))!important;box-shadow:none!important}
    .zop-actions button.zop-ghost:hover{background:var(--w-surface,#fbfaf8)!important}
    div.wb-operation-status{padding:10px 12px;margin:0 0 12px;border-radius:10px;background:rgba(var(--w-accent-rgb,234,88,12),.08);color:var(--w-accent-ink,#b8430e);font-size:13px;line-height:1.5;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    div.wb-operation-status[data-error]{background:rgba(245,158,11,.10);color:#92400e}
    div.wb-operation-status .wb-spin{border-color:rgba(var(--w-accent-rgb,234,88,12),.25);border-top-color:var(--w-accent,#cc4e0b)}
    div.wb-operation-status>span:not(.wb-spin){flex:1;min-width:0}
    #wb-operation-dock{position:fixed;right:18px;bottom:18px;z-index:12000;width:min(340px,calc(100vw - 32px));max-width:none;padding:0;overflow:hidden;background:var(--w-raised,#fff);border:1px solid var(--w-hairline,rgba(28,25,23,.09));border-radius:14px;box-shadow:0 16px 40px rgba(28,25,23,.18);font-size:13px;animation:zopRise .24s cubic-bezier(.22,1,.36,1)}
    #wb-operation-dock[hidden]{display:none}
    #wb-operation-dock .zop-dock-head{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--w-hairline,rgba(28,25,23,.09));font-size:12px;font-weight:600;color:var(--w-ink-soft,#57534e)}
    #wb-operation-dock .wb-activity-row{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;padding:10px 14px;border-bottom:1px solid var(--w-hairline,rgba(28,25,23,.09))}
    #wb-operation-dock .wb-activity-row:last-child{border-bottom:0}
    #wb-operation-dock .zop-dot{grid-row:span 3;display:grid;place-items:center;width:20px;height:20px;margin-top:1px}
    #wb-operation-dock .zop-dot svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
    #wb-operation-dock .zop-dot.is-warn{color:#b45309} #wb-operation-dock .zop-dot.is-fail{color:#b91c1c}
    #wb-operation-dock .zop-dock-title{font-weight:600;color:var(--w-ink,#1c1917);word-break:break-word}
    #wb-operation-dock .zop-dock-text{font-size:12px;line-height:1.5;color:var(--w-ink-soft,#57534e);word-break:break-word}
    #wb-operation-dock .zop-dock-actions{display:flex;gap:4px;margin-top:4px}
    .wb-spin{border-color:rgba(var(--w-accent-rgb,234,88,12),.22);border-top-color:var(--w-accent,#cc4e0b)}
    #wb-operation-dock button.wb-text-button,#workbenchActivity button.wb-text-button{color:var(--w-accent-ink,#b8430e)!important}
    #wb-operation-dock button.wb-text-button:hover,#workbenchActivity button.wb-text-button:hover{background:rgba(var(--w-accent-rgb,234,88,12),.08)!important}
    [data-op-locked]{cursor:not-allowed!important;opacity:.55}
    [data-op-locked].is-op-spinning{opacity:.85}
    [data-op-locked].is-op-spinning::before{content:"";display:inline-block;width:11px;height:11px;margin-right:5px;vertical-align:-1px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:wb-spin .8s linear infinite}
    @media (prefers-reduced-motion:reduce){.zop-overlay,.zop-dialog,#wb-operation-dock{animation:none}.zop-bar::after{animation:none;left:0;width:100%;opacity:.4}}
  `;
  document.head.appendChild(style);
}

// ── 触发按钮锁定 ─────────────────────────────────────────────────────────
let syncQueued = false;
function syncTriggers() {
  if (syncQueued) return;
  syncQueued = true;
  requestAnimationFrame(() => { syncQueued = false; applyTriggerLocks(); });
}
function triggerSignature(el) {
  return el ? `${el.dataset.opKey || ''}|${el.getAttribute('onclick') || el.textContent.trim()}` : '';
}
function applyTriggerLocks() {
  for (const el of document.querySelectorAll('[data-op-key], [data-op-locked]')) {
    const op = el.dataset.opKey ? blockingOperation(el.dataset.opKey) : null;
    if (op) {
      if (!el.hasAttribute('data-op-locked')) {
        el.setAttribute('data-op-locked', 'disabled' in el && el.disabled ? 'pre' : '1');
        el.dataset.opTitle = el.getAttribute('title') || '';
      }
      if ('disabled' in el) el.disabled = true;
      el.setAttribute('aria-disabled', 'true');
      el.setAttribute('aria-busy', BUSY.includes(op.state) ? 'true' : 'false');
      el.title = op.state === 'uncertain' ? `${op.label}：结果待核实，请先在弹窗中核对` : `${op.label}：正在同步禅道，请稍候`;
      el.classList.toggle('is-op-spinning', BUSY.includes(op.state) && triggerSignature(el) === op.triggerSignature);
    } else if (el.hasAttribute('data-op-locked')) {
      if ('disabled' in el && el.getAttribute('data-op-locked') !== 'pre') el.disabled = false;
      if (el.dataset.opTitle) el.title = el.dataset.opTitle; else el.removeAttribute('title');
      el.removeAttribute('data-op-locked');
      el.removeAttribute('aria-busy');
      el.removeAttribute('aria-disabled');
      delete el.dataset.opTitle;
      el.classList.remove('is-op-spinning');
    }
  }
}
// 列表在操作进行中被重渲染（SSE / 后台刷新）时，新按钮也要立刻锁上
const observer = new MutationObserver(() => { if (operations.size || document.querySelector('[data-op-locked]')) syncTriggers(); });
const observeBody = () => observer.observe(document.body, { childList: true, subtree: true });
if (document.body) observeBody(); else document.addEventListener('DOMContentLoaded', observeBody);
// 捕获阶段拦截：在业务 onclick（确认框、工时输入框）之前就挡住重复操作
document.addEventListener('click', event => {
  const el = event.target.closest?.('[data-op-key]');
  const op = el && blockingOperation(el.dataset.opKey);
  if (!op) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  window.showMessage?.(op.state === 'uncertain' ? '上一次操作结果待核实，请先核对' : '该任务正在同步禅道，请勿重复操作', 'info');
}, true);

// ── 右下角操作坞 ─────────────────────────────────────────────────────────
function dock() {
  injectStyles();
  let element = document.getElementById('wb-operation-dock');
  if (!element) { element = document.createElement('div'); element.id = 'wb-operation-dock'; element.setAttribute('aria-live', 'polite'); document.body.appendChild(element); }
  element.hidden = operations.size === 0;
  const rows = [...operations.values()];
  const busy = rows.filter(op => BUSY.includes(op.state)).length;
  element.innerHTML = rows.length ? `<div class="zop-dock-head">${busy ? ICON.spin : ''}<span>禅道操作 · ${busy ? `${busy} 项处理中` : `${rows.length} 项需要处理`}</span></div>` + rows.map((op, i) => {
    const dot = BUSY.includes(op.state) ? `<span class="zop-dot">${ICON.spin}</span>`
      : op.state === 'uncertain' ? `<span class="zop-dot is-warn">${ICON.warn}</span>` : `<span class="zop-dot is-fail">${ICON.fail}</span>`;
    const text = op.state === 'running' ? '正在同步禅道，完成后会提示' : op.state === 'checking' ? '正在读取禅道核对结果…' : esc(op.error);
    return `<div class="wb-activity-row">${dot}<div class="zop-dock-title">${esc(op.label)}</div><div class="zop-dock-text">${text}</div>
      <div class="zop-dock-actions"><button class="wb-text-button" data-open="${i}">查看</button>${op.state === 'uncertain' ? `<button class="wb-text-button" data-check="${i}">核对结果</button>` : ''}${op.state === 'failed' ? `<button class="wb-text-button" data-dismiss="${i}">忽略</button>` : ''}</div></div>`;
  }).join('') : '';
  element.querySelectorAll('[data-open]').forEach(btn => btn.onclick = () => rows[btn.dataset.open].open());
  element.querySelectorAll('[data-check]').forEach(btn => btn.onclick = () => check(rows[btn.dataset.check]));
  element.querySelectorAll('[data-dismiss]').forEach(btn => btn.onclick = () => { const op = rows[btn.dataset.dismiss]; op.activity.end(); stopElapsed(op); if (op.ownedModal) op.modal.remove(); operations.delete(op.key); dock(); });
  syncTriggers();
}

// ── 自建等待弹窗 ─────────────────────────────────────────────────────────
function stopElapsed(op) { clearInterval(op.elapsedTimer); op.elapsedTimer = 0; }
function renderElapsed(op) {
  const node = op.modal?.querySelector('.zop-elapsed');
  if (!node) return;
  const seconds = Math.max(0, Math.round((Date.now() - op.startedAt) / 1000));
  node.textContent = BUSY.includes(op.state) ? `已等待 ${seconds} 秒` : '';
}
function buildOwnedModal(op) {
  const modal = document.createElement('div');
  const titleId = `zopTitle${op.sequence}`;
  modal.className = 'zop-overlay';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', titleId);
  modal.innerHTML = `<div class="zop-dialog" data-state="running">
    <div class="zop-head">
      <span class="zop-badge"></span>
      <div class="zop-titles"><p class="zop-kicker"></p><h3 id="${titleId}">${esc(op.label)}</h3><p class="zop-sub"></p></div>
    </div>
    <div class="zop-bar" aria-hidden="true"></div>
    <div class="zop-message" role="status"></div>
    <p class="zop-hint"></p>
    <div class="zop-foot"><span class="zop-elapsed"></span><div class="zop-actions"></div></div>
  </div>`;
  modal.onclick = event => { if (event.target === modal) modal.style.display = 'none'; };
  document.body.appendChild(modal);
  return modal;
}
function renderOwned(op) {
  const dialog = op.modal?.querySelector('.zop-dialog');
  if (!dialog) return;
  const text = STATE_TEXT[op.state] || STATE_TEXT.running;
  dialog.dataset.state = op.state;
  dialog.querySelector('.zop-badge').innerHTML = BUSY.includes(op.state) ? ICON.spin : op.state === 'uncertain' ? ICON.warn : ICON.fail;
  dialog.querySelector('.zop-kicker').textContent = text.title;
  dialog.querySelector('.zop-sub').textContent = text.sub;
  dialog.querySelector('.zop-message').textContent = BUSY.includes(op.state) ? '' : op.error || '';
  dialog.querySelector('.zop-hint').textContent = BUSY.includes(op.state)
    ? '可以先收起弹窗继续其他工作，完成后会提示；处理期间该对象的操作按钮会暂时锁定，避免重复提交。'
    : op.state === 'uncertain' ? '核对确认生效后会自动完成；不会自动重复提交。' : '';
  const actions = dialog.querySelector('.zop-actions');
  actions.innerHTML = '';
  const add = (label, onClick, ghost) => {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = label;
    if (ghost) button.className = 'zop-ghost';
    button.onclick = onClick; actions.appendChild(button);
  };
  if (op.state === 'uncertain') add('核对结果', () => check(op));
  if (op.state === 'failed') add('重试', () => runZentaoOperation(op.config));
  add('收起', () => { op.modal.style.display = 'none'; }, op.state === 'uncertain' || op.state === 'failed');
  renderElapsed(op);
  if (BUSY.includes(op.state) && !op.elapsedTimer) op.elapsedTimer = setInterval(() => renderElapsed(op), 1000);
  if (!BUSY.includes(op.state)) stopElapsed(op);
}

function notice(op) {
  if (op.ownedModal) { renderOwned(op); return; }
  if (!op.modal || !op.isCurrent()) return;
  let node = op.modal.querySelector('.wb-operation-status');
  if (!node) {
    node = document.createElement('div'); node.className = 'wb-operation-status';
    (op.modal.querySelector('.effort-dialog') || op.modal.firstElementChild || op.modal).prepend(node);
  }
  const busy = BUSY.includes(op.state);
  node.setAttribute('role', busy ? 'status' : 'alert');
  node.toggleAttribute('data-error', !busy);
  node.innerHTML = `${busy ? '<span class="wb-spin"></span>' : '⚠'}<span>${esc(op.state === 'running' ? `${op.label}：正在同步禅道…` : op.state === 'checking' ? '正在读取禅道核对结果…' : op.error)}</span>`;
  if (op.state === 'uncertain') {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'wb-text-button'; button.textContent = '核对结果';
    button.onclick = () => check(op); node.appendChild(button);
  }
  if (op.state === 'running') {
    const hide = document.createElement('button'); hide.type = 'button'; hide.className = 'wb-text-button'; hide.textContent = '收起';
    hide.onclick = () => { op.modal.style.display = 'none'; }; node.appendChild(hide);
  }
}

function anotherModal(op) {
  return [...document.querySelectorAll('[role="dialog"], [id$="Modal"]')].some(el => el !== op.modal && visible(el) && !el.contains(op.modal));
}

async function complete(op, result) {
  op.state = 'done';
  stopElapsed(op);
  op.activity.end(); operations.delete(op.key); dock();
  if (op.ownedModal) op.modal.remove();
  if (op.isCurrent()) op.modal?.querySelector('.wb-operation-status')?.remove();
  try { await op.onSuccess?.(result); }
  catch { window.showMessage?.('操作已完成，页面更新失败，请刷新对应区域', 'error'); }
  window.showMessage?.(`${op.label}已完成`, 'success');
}

async function check(op) {
  if (op.state === 'checking') return;
  op.state = 'checking'; notice(op); dock();
  op.checkCount = (op.checkCount || 0) + 1;
  let result = null;
  let timeout;
  let timedOut = false;
  let verificationError = '';
  const controller = new AbortController();
  try {
    result = await Promise.race([
      Promise.resolve().then(() => op.verify?.({signal:controller.signal})),
      new Promise((_, reject) => { timeout = setTimeout(() => { timedOut = true; controller.abort(); reject(new Error('verification timeout')); }, 20000); }),
    ]);
  } catch (error) { verificationError = error.message || '读取失败'; }
  finally { clearTimeout(timeout); }
  if (result === true || result?.confirmed) { await complete(op, result?.data); return; }
  op.state = result === false ? 'failed' : 'uncertain';
  op.error = (op.originalError ? `上次提交错误：${op.originalError}。` : '')
    + (result === false ? '已核对：操作未完成，可查看原输入后重试。'
      : `${timedOut ? '核对超时' : `第 ${op.checkCount} 次核对仍未确认成功`}${verificationError && !timedOut ? `；本次核对读取失败：${verificationError}` : ''}，请先核对禅道。不会自动重复提交。`);
  notice(op); dock();
}

export async function runZentaoOperation(config) {
  injectStyles();
  const keys = [config.key, ...(config.locks || [])];
  const previous = [...operations.values()].find(op => [op.key, ...(op.locks || [])].some(key => keys.includes(key)) && (op.key === config.key || op.state !== 'failed'));
  if (previous && previous.state !== 'failed') {
    previous.open();
    window.showMessage?.(previous.state === 'uncertain' ? '请先核对上一次操作结果' : '该对象的操作正在处理中，请勿重复提交', 'info');
    return;
  }
  previous?.activity.end();
  if (previous) stopElapsed(previous);
  if (previous?.ownedModal) previous.modal.remove();
  const op = { ...config, state:'running', isCurrent:config.isCurrent || (() => true) };
  op.sequence = String(++sequence);
  op.config = config;
  op.startedAt = Date.now();
  // 触发按钮：显式传入，或点击时获得焦点的 [data-op-key] 元素（用于在它上面显示转圈）
  const active = config.trigger || document.activeElement;
  op.triggerSignature = active?.closest?.('[data-op-key]') ? triggerSignature(active.closest('[data-op-key]')) : '';
  op.modal = typeof config.modal === 'string' ? document.getElementById(config.modal) : config.modal;
  if (!op.modal) {
    op.modal = buildOwnedModal(op); op.ownedModal = true;
  }
  const display = op.modal ? (getComputedStyle(op.modal).display === 'none' ? 'flex' : getComputedStyle(op.modal).display) : '';
  const fields = op.modal ? [...op.modal.querySelectorAll('input,textarea,select')].map(el => ({ id:el.id, value:el.value, checked:el.checked, options:el.tagName === 'SELECT' ? el.innerHTML : null })) : [];
  op.open = () => {
    if (!op.modal) { window.showMessage?.(op.error || `${op.label}正在处理`, 'info'); return; }
    if (!op.isCurrent() && visible(op.modal)) { window.showMessage?.('请先收起当前编辑弹窗，再查看这项操作', 'info'); return; }
    if (!op.isCurrent() || !visible(op.modal)) {
      prepareOperationModal(op.modal);
      config.restore?.();
    }
    for (const field of fields) {
      const input = field.id && document.getElementById(field.id);
      if (input && op.modal.contains(input)) {
        if (field.options !== null) input.innerHTML = field.options;
        input.value = field.value; input.checked = field.checked;
      }
    }
    op.modal.classList.remove('hidden'); op.modal.style.display = display; op.modal.dataset.open = '1';
    notice(op);
    if (op.state === 'running') disableControls();
  };
  operations.set(op.key, op);
  op.activity = beginActivity(op.label, { open:op.open });
  const disabled = [];
  function disableControls() {
    if (!op.modal || op.ownedModal) return;
    for (const control of op.modal.querySelectorAll('button,input,textarea,select')) {
      if (control.closest('.wb-operation-status')) continue;
      if (control.tagName === 'BUTTON' && /关闭|收起|close/i.test((control.textContent || '') + (control.getAttribute('onclick') || ''))) continue;
      if (control.dataset.operationDisabled === op.sequence) continue;
      control.dataset.operationDisabled = op.sequence;
      control.dataset.operationWasDisabled = String(control.disabled);
      disabled.push([control, control.disabled]); control.disabled = true;
    }
  }
  op.modal?.querySelector('.wb-operation-status')?.remove();
  disableControls();
  notice(op); dock();
  try {
    const result = await config.run();
    if (result?.ok === false) { const err = new Error((result.errors || [result.error || '禅道操作未确认完成']).join('；')); err.status = 502; throw err; }
    await complete(op, result);
  } catch (error) {
    op.error = op.originalError = error.message || '操作失败';
    op.state = error.operationOutcome === 'unknown' ? 'uncertain'
      : error.operationOutcome === 'not-started' || (error.status && error.status < 500) ? 'failed' : 'uncertain';
    if (error.operationOutcome === 'not-started') op.error += '。本次未提交修改，可重试。';
    if (op.state === 'uncertain') await check(op);
    if (operations.has(op.key)) {
      op.activity.fail(op.error);
      if (!anotherModal(op) && (!visible(op.modal) || op.isCurrent())) op.open();
      else notice(op);
      dock(); window.showMessage?.(`${op.label}：${op.error}`, 'error');
    }
  } finally {
    for (const [control, wasDisabled] of disabled) if (control.isConnected && control.dataset.operationDisabled === op.sequence) {
      control.disabled = wasDisabled;
      delete control.dataset.operationDisabled;
      delete control.dataset.operationWasDisabled;
    }
    syncTriggers();
  }
}

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const op = [...operations.values()].find(row => row.state === 'running' && row.isCurrent() && visible(row.modal));
  if (op) { event.preventDefault(); event.stopImmediatePropagation(); op.modal.style.display = 'none'; }
}, true);
