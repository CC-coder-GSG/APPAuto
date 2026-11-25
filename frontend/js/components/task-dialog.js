import { escapeHtml } from '../utils.js';

// 任务操作对话框（2026-11-25，weave.css R18 .tod-*，外壳复用 R16 .atl-*）。
// 替代任务工作台 / 任务看板里的 window.prompt / window.confirm：
//   askTaskHours(task, { mode })        → 设置预计工时 / 补填实际工时，返回小时数或 null
//   confirmTaskAction(action, task, …)  → 完成 / 关闭 / 取消 / 重新激活确认；完成且无计时记录时
//                                          在同一个对话框里补填实际工时，返回 { consumed } 或 null
//   confirmTaskDialog({...})            → 通用确认（批量关闭等）
// 对话框挂 body（跨 tab 复用），z-index 高于任务看板弹窗；Esc 只关自己，不波及下层抽屉/弹窗。

const DIALOG_ID = 'taskOpDialog';
const ACTIONS = {
  start: { icon: 'i-play', tone: 'green', title: '开始任务', ok: '开始' },
  finish: { icon: 'i-check', tone: 'green', title: '完成任务', ok: '确认完成', message: '任务将标记为「已完成」并同步到禅道。' },
  close: { icon: 'i-ban', tone: 'red', title: '关闭任务', ok: '确认关闭', danger: true, message: '关闭后任务不再出现在待办中，如需继续可「重新激活」。' },
  cancel: { icon: 'i-x-circle', tone: 'red', title: '取消任务', ok: '确认取消', danger: true, message: '与禅道联动取消该任务，取消后可重新激活。' },
  reactivate: { icon: 'i-rotate', tone: 'blue', title: '重新激活任务', ok: '重新激活', message: '已登记的工时会保留，并从当前时刻开始新的计时段。' },
};
const STATUS = {
  wait: '未开始', doing: '进行中', pause: '已暂停', done: '已完成', closed: '已关闭', cancel: '已取消', changed: '需求变更',
};

let active = null; // { resolve, el, prevFocus }

const fmtH = (v) => `${Math.round(Number(v) * 100) / 100}h`;
const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? null : Number(v));

function taskSub(task, taskId) {
  const id = Number(task?.task_id || taskId || 0);
  const name = task?.name ? escapeHtml(task.name) : '';
  return `<span class="atl-target">${id ? `#${id}` : ''}${name ? ` · ${name}` : ''}</span>`;
}

// 任务现状：状态 + 预计 / 已消耗 / 剩余（有就显示）
function taskFacts(task) {
  if (!task) return '';
  const items = [];
  if (task.status && STATUS[task.status]) items.push(['状态', STATUS[task.status]]);
  if (num(task.estimate) !== null) items.push(['预计', fmtH(task.estimate)]);
  if (num(task.consumed) !== null) items.push(['已消耗', fmtH(task.consumed)]);
  if (num(task.left) !== null) items.push(['剩余', fmtH(task.left)]);
  const who = task.assigned_to_realname || task.assigned_to;
  if (who) items.push(['指派', escapeHtml(who)]);
  if (!items.length) return '';
  return `<dl class="tod-facts">${items.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>`;
}

// 工时输入区（大号数字 + −/+ 步进 + 快捷工时 + 行内错误），任务对话框与需求工作台「提交测试结果」弹窗共用。
// 渲染进 container 并绑定交互，返回 { input, read() }：read() 校验通过返回小时数，否则行内报错并返回 null。
export function mountHoursField(container, { id = 'taskOpHours', label, value, presets = [], hint = '', suggestion = null }) {
  const chips = presets.map((p) => `<button type="button" class="tod-chip" data-hours="${p}">${p}h</button>`).join('');
  const sug = suggestion !== null
    ? `<button type="button" class="tod-chip tod-chip--suggest" data-hours="${suggestion}" title="按计时/剩余工时估算">建议 ${fmtH(suggestion)}</button>`
    : '';
  container.innerHTML = `
    <div class="tod-hours">
      <label class="tod-label" for="${id}">${label}<small>0～999 小时，可填小数</small></label>
      <div class="tod-stepper">
        <button type="button" class="tod-step" data-step="-0.5" aria-label="减少 0.5 小时">−</button>
        <input id="${id}" type="number" inputmode="decimal" min="0.1" max="999" step="0.5" value="${value ?? ''}" autocomplete="off">
        <span class="tod-unit">小时</span>
        <button type="button" class="tod-step" data-step="0.5" aria-label="增加 0.5 小时">+</button>
      </div>
      ${chips || sug ? `<div class="tod-chips">${sug}${chips}</div>` : ''}
      ${hint ? `<p class="tod-hint">${hint}</p>` : ''}
      <p class="tod-err" id="${id}Err" role="alert" hidden></p>
    </div>`;
  const input = container.querySelector('input');
  const err = container.querySelector('.tod-err');
  const showErr = (text) => {
    err.textContent = text;
    err.hidden = !text;
    input.closest('.tod-stepper')?.classList.toggle('is-invalid', !!text);
  };
  const syncChips = () => {
    const v = num(input.value);
    container.querySelectorAll('.tod-chip').forEach((c) => c.classList.toggle('is-on', v !== null && Number(c.dataset.hours) === v));
  };
  container.onclick = (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    if (btn.dataset.hours) {
      input.value = btn.dataset.hours;
      input.focus();
    } else if (btn.dataset.step) {
      const next = Math.min(999, Math.max(0.5, (num(input.value) || 0) + Number(btn.dataset.step)));
      input.value = String(Math.round(next * 100) / 100);
    } else return;
    showErr('');
    syncChips();
  };
  input.oninput = () => { showErr(''); syncChips(); };
  syncChips();
  return {
    input,
    read() {
      const v = num(input.value);
      if (v === null || v <= 0 || v > 999) {
        showErr('请输入 0～999 之间的小时数（大于 0）');
        input.focus();
        input.select?.();
        return null;
      }
      return Math.round(v * 100) / 100;
    },
  };
}

// 是否需要补填实际工时：本人负责且未检测到计时记录的禅道任务；建议值取后端估算 / 剩余 / 预计
export function consumedSuggestion(task) {
  const v = num(task?.suggested_consumed_hours ?? task?.left ?? task?.estimate);
  return v && v > 0 ? v : null;
}

function close(result) {
  if (!active) return;
  const { resolve, el, prevFocus } = active;
  active = null;
  el.classList.add('hidden');
  el.style.display = 'none';
  el.innerHTML = '';
  try { prevFocus?.focus?.({ preventScroll: true }); } catch { /* 原焦点元素可能已被重渲染 */ }
  resolve(result);
}

function ensureRoot() {
  let el = document.getElementById(DIALOG_ID);
  if (el) return el;
  el = document.createElement('div');
  el.id = DIALOG_ID;
  el.className = 'hidden atl-root tod-root';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-labelledby', 'taskOpDialogTitle');
  el.style.display = 'none';
  el.addEventListener('mousedown', (e) => { if (e.target === el) close(null); });
  el.addEventListener('click', (e) => active?.onClick?.(e));
  document.body.appendChild(el);
  // 捕获阶段处理 Esc / Enter：只作用于本对话框
  window.addEventListener('keydown', (e) => {
    if (!active) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopImmediatePropagation();
      close(null);
    } else if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.target?.tagName !== 'TEXTAREA'
      // 取消 / 关闭按钮上的回车走按钮本身；输入框、快捷工时、步进按钮上的回车 = 确认
      && !(e.target?.tagName === 'BUTTON' && e.target.dataset.act && e.target.dataset.act !== 'ok')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      active.submit();
    }
  }, true);
  return el;
}

// 单行文本输入（编号纠正等）：可带前缀标签（如 b#）与校验函数，校验失败行内报错。
function mountTextField(container, { label, value = '', placeholder = '', prefix = '', hint = '', validate = null }) {
  container.innerHTML = `
    <div class="tod-hours">
      <label class="tod-label" for="taskOpText">${escapeHtml(label || '')}</label>
      <div class="tod-text">
        ${prefix ? `<span class="tod-prefix">${escapeHtml(prefix)}</span>` : ''}
        <input id="taskOpText" type="text" autocomplete="off" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}">
      </div>
      ${hint ? `<p class="tod-hint">${escapeHtml(hint)}</p>` : ''}
      <p class="tod-err" id="taskOpTextErr" role="alert" hidden></p>
    </div>`;
  const input = container.querySelector('input');
  const err = container.querySelector('.tod-err');
  const showErr = (text) => {
    err.textContent = text;
    err.hidden = !text;
    input.closest('.tod-text').classList.toggle('is-invalid', !!text);
  };
  input.oninput = () => showErr('');
  return {
    input,
    read() {
      let v = input.value.trim();
      // 用户顺手带上了前缀（b#123）也接受
      if (prefix && v.toLowerCase().startsWith(prefix.toLowerCase())) v = v.slice(prefix.length).trim();
      const problem = !v ? '请填写内容' : (validate ? validate(v) : '');
      if (problem) {
        showErr(problem);
        input.focus();
        input.select?.();
        return null;
      }
      return v;
    },
  };
}

// 打开对话框。hours / text 有值时带输入，确认时校验；返回 Promise<{hours?, text?}|null>
function openDialog({ icon, tone = 'accent', title, sub = '', facts = '', message = '', hours = null, text = null, okText = '确定', cancelText = '取消', danger = false }) {
  if (active) close(null);
  const el = ensureRoot();
  el.dataset.tone = tone;
  el.innerHTML = `
    <div class="atl-card atl-card--sm tod-card">
      <header class="atl-head">
        <span class="atl-icon tod-icon" aria-hidden="true"><svg><use href="#${icon}"/></svg></span>
        <div class="atl-head-text">
          <h3 id="taskOpDialogTitle">${escapeHtml(title)}</h3>
          ${sub ? `<div class="atl-sub">${sub}</div>` : ''}
        </div>
        <button type="button" class="atl-close" data-act="cancel" aria-label="关闭" title="关闭（Esc）"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
      </header>
      <div class="atl-body atl-body--form tod-body">
        ${facts}
        ${message ? `<p class="tod-msg">${message}</p>` : ''}
        ${hours || text ? '<div class="tod-hours-slot"></div>' : ''}
      </div>
      <footer class="atl-foot">
        <button type="button" class="secondary" data-act="cancel">${escapeHtml(cancelText)}</button>
        <button type="button" class="tod-ok${danger ? ' tod-ok--danger' : ''}" data-act="ok">${escapeHtml(okText)}</button>
      </footer>
    </div>`;
  const slot = el.querySelector('.tod-hours-slot');
  const field = hours ? mountHoursField(slot, hours) : (text ? mountTextField(slot, text) : null);
  const input = field?.input;
  const submit = () => {
    if (!field) { close({}); return; }
    const v = field.read();
    if (v !== null) close(hours ? { hours: v } : { text: v });
  };
  const onClick = (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    if (btn.dataset.act === 'cancel') close(null);
    else if (btn.dataset.act === 'ok') submit();
  };
  return new Promise((resolve) => {
    active = { resolve, el, submit, onClick, prevFocus: document.activeElement };
    el.classList.remove('hidden');
    el.style.display = 'flex';
    requestAnimationFrame(() => {
      if (input) { input.focus(); input.select?.(); }
      else el.querySelector('[data-act="ok"]')?.focus();
    });
  });
}

// 设置预计工时（mode='estimate'）/ 补填实际工时（mode='consumed'）
// 需求工作台等场景可覆盖标题 / 副标题 / 说明 / 按钮文案（sub 为纯文本）
export async function askTaskHours(task, { mode = 'estimate', taskId, title, sub, hint, okText } = {}) {
  const estimate = mode === 'estimate';
  const suggestion = estimate ? null : consumedSuggestion(task);
  const res = await openDialog({
    icon: estimate ? 'i-timer' : 'i-clock',
    title: title || (estimate ? '设置预计工时' : '填写实际工时'),
    sub: sub ? `<span class="atl-target">${escapeHtml(sub)}</span>` : taskSub(task, taskId),
    facts: taskFacts(task),
    hours: {
      label: estimate ? '预计工时' : '本次实际工时',
      value: estimate ? (num(task?.estimate) || 4) : (suggestion || 1),
      presets: [1, 2, 4, 8, 16],
      suggestion,
      hint: hint || (estimate ? '修改后同步写入禅道任务的「预计」工时。' : '未检测到该任务的计时记录，请补填本次实际消耗的工时。'),
    },
    okText: okText || (estimate ? '保存并同步' : '确定'),
  });
  return res ? res.hours : null;
}

// 任务状态操作确认。finish 且无计时记录时在同一对话框里补填工时。
// 返回 null = 用户取消；{} = 确认；{ consumed } = 确认并带上实际工时。
export async function confirmTaskAction(action, task, { taskId, message } = {}) {
  const meta = ACTIONS[action];
  if (!meta) return {};
  const needConsumed = action === 'finish' && task && !task.has_time_tracking;
  const suggestion = needConsumed ? num(task.suggested_consumed_hours ?? task.left ?? task.estimate) : null;
  const res = await openDialog({
    icon: meta.icon,
    tone: meta.tone,
    title: meta.title,
    sub: taskSub(task, taskId),
    facts: taskFacts(task),
    message: escapeHtml(message || meta.message || ''),
    hours: needConsumed ? {
      label: '本次实际工时',
      value: suggestion && suggestion > 0 ? suggestion : 1,
      presets: [1, 2, 4, 8],
      suggestion: suggestion && suggestion > 0 ? suggestion : null,
      hint: '未检测到该任务的计时记录，完成前请补填实际消耗的工时。',
    } : null,
    okText: meta.ok,
    danger: !!meta.danger,
  });
  if (!res) return null;
  return res.hours !== undefined ? { consumed: res.hours } : {};
}

// 通用确认（批量操作、取消完成、删除等）
export async function confirmTaskDialog({ icon = 'i-alert', tone = 'accent', title, sub = '', message = '', okText = '确定', cancelText = '取消', danger = false }) {
  const res = await openDialog({ icon, tone, title, sub: sub ? `<span class="atl-target">${escapeHtml(sub)}</span>` : '', message: escapeHtml(message), okText, cancelText, danger });
  return !!res;
}

// 通用单行输入（替代 window.prompt）：返回去掉前缀后的文本，取消返回 null
export async function promptTaskDialog({ icon = 'i-wrench', tone = 'accent', title, sub = '', message = '', label, value = '', placeholder = '', prefix = '', hint = '', validate = null, okText = '确定' }) {
  const res = await openDialog({
    icon, tone, title, sub: sub ? `<span class="atl-target">${escapeHtml(sub)}</span>` : '', message: escapeHtml(message),
    text: { label, value, placeholder, prefix, hint, validate }, okText,
  });
  return res ? res.text : null;
}

window.OmniQATaskDialog = { askTaskHours, confirmTaskAction, confirmTaskDialog, promptTaskDialog };
