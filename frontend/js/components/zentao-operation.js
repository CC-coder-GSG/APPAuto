import { beginActivity } from './workbench-activity.js';

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
export const operationPending = key => [...operations.values()].some(op => (op.key === key || op.locks?.includes(key)) && ['running','checking'].includes(op.state));

function dock() {
  let element = document.getElementById('wb-operation-dock');
  if (!element) { element = document.createElement('div'); element.id = 'wb-operation-dock'; document.body.appendChild(element); }
  element.hidden = operations.size === 0;
  element.innerHTML = [...operations.values()].map((op, i) => `<div class="wb-activity-row"><div>${op.state === 'running' ? '<span class="wb-spin"></span> ' : '⚠ '}${esc(op.label)}</div><div>${op.state === 'running' ? '正在同步禅道，可收起弹窗后继续查看' : esc(op.error)}</div><button class="wb-text-button" data-open="${i}">查看</button>${op.state === 'uncertain' ? `<button class="wb-text-button" data-check="${i}">核对结果</button>` : ''}${op.state === 'failed' ? `<button class="wb-text-button" data-dismiss="${i}">忽略</button>` : ''}</div>`).join('');
  const rows = [...operations.values()];
  element.querySelectorAll('[data-open]').forEach(btn => btn.onclick = () => rows[btn.dataset.open].open());
  element.querySelectorAll('[data-check]').forEach(btn => btn.onclick = () => check(rows[btn.dataset.check]));
  element.querySelectorAll('[data-dismiss]').forEach(btn => btn.onclick = () => { const op = rows[btn.dataset.dismiss]; op.activity.end(); if (op.ownedModal) op.modal.remove(); operations.delete(op.key); dock(); });
}

function notice(op) {
  if (!op.modal || !op.isCurrent()) return;
  let node = op.modal.querySelector('.wb-operation-status');
  if (!node) {
    node = document.createElement('div'); node.className = 'wb-operation-status';
    (op.modal.querySelector('.effort-dialog') || op.modal.firstElementChild || op.modal).prepend(node);
  }
  node.setAttribute('role', op.state === 'running' ? 'status' : 'alert');
  node.toggleAttribute('data-error', op.state !== 'running');
  node.innerHTML = `${op.state === 'running' ? '<span class="wb-spin"></span>' : '⚠'}<span>${esc(op.state === 'running' ? `${op.label}：正在同步禅道…` : op.error)}</span>`;
  if (op.state === 'running') {
    const hide = document.createElement('button'); hide.type = 'button'; hide.className = 'wb-text-button'; hide.textContent = '收起';
    hide.onclick = () => { op.modal.style.display = 'none'; }; node.appendChild(hide);
  }
  if (op.state === 'failed' && op.ownedModal) {
    const retry = document.createElement('button'); retry.textContent = '重试'; retry.className = 'wb-text-button';
    retry.onclick = () => runZentaoOperation(op.config); node.appendChild(retry);
  }
}

function anotherModal(op) {
  return [...document.querySelectorAll('[role="dialog"], [id$="Modal"]')].some(el => el !== op.modal && visible(el) && !el.contains(op.modal));
}

async function complete(op, result) {
  op.state = 'done';
  op.activity.end(); operations.delete(op.key); dock();
  if (op.ownedModal) op.modal.remove();
  if (op.isCurrent()) op.modal?.querySelector('.wb-operation-status')?.remove();
  try { await op.onSuccess?.(result); }
  catch { window.showMessage?.('操作已完成，页面更新失败，请刷新对应区域', 'error'); }
  window.showMessage?.(`${op.label}已完成`, 'success');
}

async function check(op) {
  if (op.state === 'checking') return;
  op.state = 'checking'; dock();
  let result = null;
  try { result = await op.verify?.(); } catch { /* keep uncertain */ }
  if (result === true || result?.confirmed) { await complete(op, result?.data); return; }
  op.state = result === false ? 'failed' : 'uncertain';
  op.error = (op.originalError ? `${op.originalError}。` : '') + (result === false ? '已核对：操作未完成，可查看原输入后重试。' : '结果尚未确认，请先核对禅道。不会自动重复提交。');
  notice(op); dock();
}

export async function runZentaoOperation(config) {
  const keys = [config.key, ...(config.locks || [])];
  const previous = [...operations.values()].find(op => [op.key, ...(op.locks || [])].some(key => keys.includes(key)) && (op.key === config.key || op.state !== 'failed'));
  if (previous && previous.state !== 'failed') {
    previous.open();
    window.showMessage?.(previous.state === 'uncertain' ? '请先核对上一次操作结果' : '该对象的操作正在处理中，请勿重复提交', 'info');
    return;
  }
  previous?.activity.end();
  if (previous?.ownedModal) previous.modal.remove();
  const op = { ...config, state:'running', isCurrent:config.isCurrent || (() => true) };
  op.sequence = String(++sequence);
  op.config = config;
  op.modal = typeof config.modal === 'string' ? document.getElementById(config.modal) : config.modal;
  if (!op.modal) {
    const modal = document.createElement('div');
    modal.setAttribute('role', 'dialog');
    modal.style.cssText = 'position:fixed;inset:0;z-index:10020;background:#0f172a66;display:flex;align-items:center;justify-content:center;padding:16px';
    modal.innerHTML = `<div style="background:white;padding:20px;border-radius:12px;max-width:480px;width:100%"><h3>${esc(config.label)}</h3><div class="wb-operation-content"></div><button type="button" class="wb-text-button">收起</button></div>`;
    modal.querySelector('button').onclick = () => { modal.style.display = 'none'; };
    modal.onclick = event => { if (event.target === modal) modal.style.display = 'none'; };
    document.body.appendChild(modal);
    op.modal = modal; op.ownedModal = true;
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
    if (!op.modal) return;
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
    op.state = error.status && error.status < 500 ? 'failed' : 'uncertain';
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
  }
}

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const op = [...operations.values()].find(row => row.state === 'running' && row.isCurrent() && visible(row.modal));
  if (op) { event.preventDefault(); event.stopImmediatePropagation(); op.modal.style.display = 'none'; }
}, true);
