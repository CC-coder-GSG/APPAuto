import { api } from '../api.js';
import { confirmDialog, promptDialog } from './dialog.js?v=20261126-zai-1';

// 整理 AI 用例并提交禅道 v2（2026-11-26，weave.css R20 .ace-*）
// 布局：头部（上下文 + 三步进度）→ 左栏「AI 原始小用例」（搜索 / 勾选 / 分页，独立滚动）
//      → 右栏「待提交草稿」（卡片 + 状态徽标；编辑时右栏切换为编辑器）→ 底栏汇总；预览为整页视图。
// 数据流程不变：加入草稿 → 单独编辑或合并 → 预览 → 提交（push 请求带幂等 request_id）。

const ID = 'aiCaseEditor';
const labels = { draft: '待提交', queued: '等待提交', submitting: '正在提交', succeeded: '已创建', failed: '提交失败', unknown: '结果待核实' };
const tones = { draft: 'muted', queued: 'run', submitting: 'run', succeeded: 'ok', failed: 'err', unknown: 'warn' };
let state = null;
let pollTimer = null;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const editable = (draft) => ['draft', 'failed'].includes(draft.submit_status);
const message = (text, level = 'error') => window.showMessage?.(text, level);
const requestId = () => crypto.randomUUID ? crypto.randomUUID() : '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (c) => (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));
const icon = (id) => `<svg class="ui-ic" aria-hidden="true"><use href="#${id}"/></svg>`;
const dis = (off) => (off ? 'disabled' : '');

async function json(path, method = 'GET', body) {
  return (await api(`/zentao/ai/${path}`, { method, ...(body ? { body } : {}) })).json();
}

function ensureRoot() {
  let root = document.getElementById(ID);
  if (root) return root;
  root = document.createElement('div');
  root.id = ID;
  root.className = 'ace-root';
  root.style.display = 'none';
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-label', '整理 AI 用例并提交禅道');
  document.body.appendChild(root);
  root.addEventListener('click', onClick);
  root.addEventListener('change', onChange);
  root.addEventListener('input', onInput);
  return root;
}

function selectedDrafts() {
  const byId = new Map(state.workspace.drafts.map((d) => [d.id, d]));
  return [...state.drafts].map((id) => byId.get(id)).filter((d) => d && editable(d));
}

function options(values, selected, placeholder = '请选择') {
  return `<option value="">${placeholder}</option>${Object.entries(values).map(([key, name]) => `<option value="${esc(key)}" ${String(key) === String(selected) ? 'selected' : ''}>${esc(name)}</option>`).join('')}`;
}

// 头部三步：挑选 → 整理 → 预览提交
function stepper() {
  const w = state.workspace;
  const current = state.preview ? 3 : (w.drafts.length ? 2 : 1);
  const steps = ['挑选小用例', '整理草稿', '预览并提交'];
  return `<ol class="ace-steps" aria-label="整理进度">${steps.map((name, i) => {
    const n = i + 1;
    const cls = n < current ? 'is-done' : n === current ? 'is-on' : '';
    return `<li class="${cls}" ${n === current ? 'aria-current="step"' : ''}><span class="ace-step-no">${n < current ? icon('i-check') : n}</span>${name}</li>`;
  }).join('')}</ol>`;
}

function sourceList() {
  const { workspace: w } = state;
  const used = new Set(w.drafts.flatMap((d) => d.sources));
  const filtered = w.sources.filter((s) => s.content.title.toLowerCase().includes(state.search.toLowerCase()));
  const pages = Math.max(1, Math.ceil(filtered.length / 20));
  state.page = Math.min(state.page, pages - 1);
  const visible = filtered.slice(state.page * 20, state.page * 20 + 20);
  const locked = state.busy || state.editing;
  return `<div class="ace-col-head">
      <h3>AI 原始小用例</h3><span class="ace-count">${w.sources.length}</span>
      <span class="ace-col-sub">${used.size ? `已加入草稿 ${used.size} 条` : '勾选后加入草稿'}</span>
    </div>
    <label class="ace-search">${icon('i-search')}<input aria-label="搜索原始用例" data-source-search value="${esc(state.search)}" placeholder="搜索小用例标题"></label>
    <div class="ace-tools">
      <button type="button" class="ace-btn" data-action="source-all">选择筛选结果</button>
      <button type="button" class="ace-btn" data-action="source-clear" ${dis(!state.sources.size)}>清空</button>
      <span class="ace-tools-meta">已选 <b>${state.sources.size}</b></span>
    </div>
    <div data-source-scroll class="ace-src-list">${visible.map((s) => {
      const isUsed = used.has(s.index);
      const checked = state.sources.has(s.index);
      return `<label class="ace-src${isUsed ? ' is-used' : ''}${checked ? ' is-checked' : ''}">
        <input type="checkbox" data-source="${s.index}" ${checked ? 'checked' : ''} ${isUsed ? 'disabled' : ''}>
        <span class="ace-src-no">${s.index + 1}</span>
        <span class="ace-src-main">
          <span class="ace-src-title">${esc(s.content.title || '未命名用例')}</span>
          <span class="ace-src-meta">${esc(w.context.types[s.content.case_type] || '类型待选择')} · ${s.content.steps.length} 步${isUsed ? '<span class="ace-tag">已加入草稿</span>' : ''}</span>
        </span>
      </label>`;
    }).join('') || `<div class="ace-empty-sm">${state.search ? '没有匹配的小用例' : 'AI 没有生成小用例'}</div>`}</div>
    <div class="ace-pager">
      <button type="button" class="ace-btn ace-btn--icon" data-action="prev" aria-label="上一页" ${dis(state.page === 0)}>${icon('i-chevron-left')}</button>
      <span>${state.page + 1} / ${pages}</span>
      <button type="button" class="ace-btn ace-btn--icon" data-action="next" aria-label="下一页" ${dis(state.page >= pages - 1)}>${icon('i-chevron-right')}</button>
    </div>
    <button type="button" class="ace-add" data-action="add" ${dis(!(state.sources.size && !locked))}>${icon('i-plus')}加入 ${state.sources.size} 条到草稿</button>`;
}

function draftCard(d) {
  const w = state.workspace;
  const locked = state.busy || state.editing;
  const tone = tones[d.submit_status] || 'muted';
  const chips = [
    `<span class="ace-chip">来源 ${d.sources.map((i) => i + 1).join('、')}</span>`,
    `<span class="ace-chip">${d.content.steps.length} 步</span>`,
    `<span class="ace-chip${w.context.types[d.content.case_type] ? '' : ' is-missing'}">${esc(w.context.types[d.content.case_type] || '类型待选择')}</span>`,
    `<span class="ace-chip${d.content.priority ? '' : ' is-missing'}">${d.content.priority ? `优先级 ${d.content.priority}` : '优先级待选择'}</span>`,
  ].join('');
  const actions = [
    `<button type="button" class="ace-btn" data-action="edit" data-id="${esc(d.id)}" ${dis(locked)}>${icon(editable(d) ? 'i-edit' : 'i-file')}${editable(d) ? '编辑' : '查看'}</button>`,
    editable(d) ? `<button type="button" class="ace-btn" data-action="remove" data-id="${esc(d.id)}" ${dis(locked)}>移出</button>` : '',
    editable(d) && d.parents.length ? `<button type="button" class="ace-btn" data-action="unmerge" data-id="${esc(d.id)}" ${dis(locked)}>撤销合并</button>` : '',
    d.remote_url ? `<a class="ace-link" target="_blank" rel="noopener noreferrer" href="${esc(d.remote_url)}">${icon('i-external')}禅道 #${esc(d.remote_case_id)}</a><button type="button" class="ace-btn" data-action="sync" data-id="${esc(d.id)}" ${dis(locked)}>重新同步</button>` : '',
    d.submit_status === 'unknown' ? `<button type="button" class="ace-btn" data-action="reconcile" data-id="${esc(d.id)}" ${dis(locked)}>关联已核实的禅道用例</button><button type="button" class="ace-btn" data-action="confirm-not-created" data-id="${esc(d.id)}" ${dis(locked)}>确认未创建，恢复草稿</button>` : '',
  ].join('');
  return `<article class="ace-draft${state.drafts.has(d.id) ? ' is-checked' : ''}" data-status="${esc(d.submit_status)}">
    <div class="ace-draft-head">
      <input type="checkbox" aria-label="选择草稿" data-draft="${esc(d.id)}" ${state.drafts.has(d.id) ? 'checked' : ''} ${dis(!(editable(d) && !state.editing))}>
      <span class="ace-draft-title">${esc(d.content.title || '未命名用例')}</span>
      <span class="ace-status ace-status--${tone}">${labels[d.submit_status] || esc(d.submit_status)}</span>
    </div>
    <div class="ace-chips">${chips}</div>
    ${d.error ? `<div class="ace-msg ace-msg--err">${esc(d.error)}</div>` : ''}${d.warning ? `<div class="ace-msg ace-msg--warn">${esc(d.warning)}</div>` : ''}
    <div class="ace-draft-actions">${actions}</div>
  </article>`;
}

function draftList() {
  const w = state.workspace;
  const selected = selectedDrafts();
  const pending = w.drafts.some((d) => ['queued', 'submitting'].includes(d.submit_status));
  const locked = state.busy || state.editing;
  const done = w.drafts.filter((d) => d.submit_status === 'succeeded').length;
  return `<div class="ace-col-head">
      <h3>待提交草稿</h3><span class="ace-count">${w.drafts.length}</span>
      ${done ? `<span class="ace-col-sub">已创建 ${done} 条</span>` : ''}
    </div>
    <div class="ace-tools">
      <button type="button" class="ace-btn" data-action="draft-all">选择全部待提交</button>
      <button type="button" class="ace-btn" data-action="draft-clear" ${dis(!state.drafts.size)}>清空</button>
      <button type="button" class="ace-btn" data-action="merge" ${dis(!(selected.length >= 2 && !locked))}>${icon('i-layers')}合并所选 ${selected.length} 条</button>
      <span class="ace-tools-gap"></span>
      ${pending ? `<button type="button" class="ace-btn" data-action="resume" ${dis(locked)}>继续等待中的提交</button>` : ''}
      <button type="button" class="ace-btn" data-action="refresh" ${dis(locked)}>${icon('i-refresh')}刷新</button>
    </div>
    <div class="ace-draft-list">
      ${w.drafts.map(draftCard).join('') || `<div class="ace-empty">
        <span class="ace-empty-icon">${icon('i-layers')}</span>
        <p>还没有草稿</p>
        <span>从左侧勾选需要的小用例，加入后可以单独编辑，也可以勾选几条合并成一条。</span>
      </div>`}
    </div>
    <div class="ace-foot">
      <span class="ace-foot-meta">已选 <b>${selected.length}</b> 条待提交${pending ? ' · 有提交正在进行，结果会自动刷新' : ''}</span>
      <button type="button" data-action="preview" ${dis(!(selected.length && !locked))}>预览并提交禅道${icon('i-chevron-right')}</button>
    </div>`;
}

function renderEditor() {
  const e = state.editing;
  const c = e.content;
  const ctx = state.workspace.context;
  const d = state.workspace.drafts.find((x) => x.id === e.id);
  const readOnly = !editable(d);
  const fields = `<div class="ace-fields">
    <label class="ace-field ace-field--full"><span>标题</span><input data-field="title" maxlength="255" value="${esc(c.title)}"></label>
    <label class="ace-field"><span>类型</span><select data-field="case_type">${options(ctx.types, c.case_type)}</select></label>
    <label class="ace-field"><span>优先级</span><select data-field="priority">${options({ 1: '1 · 最高', 2: '2 · 高', 3: '3 · 中', 4: '4 · 低' }, c.priority)}</select></label>
    <label class="ace-field"><span>模块</span><select data-field="module_id">${options(Object.fromEntries(ctx.modules.map((m) => [m.id, m.name])), c.module_id)}</select></label>
    <label class="ace-field"><span>测试阶段</span><select data-field="stage">${options(ctx.stages, c.stage, '不指定')}</select></label>
    <label class="ace-field ace-field--full"><span>前置条件</span><textarea data-field="precondition" maxlength="50000" rows="3">${esc(c.precondition)}</textarea></label>
    <label class="ace-field ace-field--full"><span>关键词<small>多个用分号分隔</small></span><input data-field="keywords" maxlength="500" value="${esc(c.keywords)}"></label>
  </div>
  <p class="ace-hint">优先级映射：高→2，中→3，低→4，无法识别的值需要手动选择。合并后请检查每个场景的准备条件和状态重置。</p>
  <div class="ace-steps-edit">
    <div class="ace-steps-head"><h4>测试步骤</h4><span class="ace-count">${c.steps.length}</span></div>
    ${c.steps.map((s, i) => `<div class="ace-step-row">
      <span class="ace-step-badge">${i + 1}</span>
      <label class="ace-step-cell"><span>操作步骤</span><textarea data-step="${i}" data-step-field="step" maxlength="20000" rows="2">${esc(s.step)}</textarea></label>
      <label class="ace-step-cell"><span>预期结果</span><textarea data-step="${i}" data-step-field="expected" maxlength="20000" rows="2">${esc(s.expected)}</textarea></label>
      <div class="ace-step-ops">
        <button type="button" class="ace-btn ace-btn--icon" data-action="step-up" data-index="${i}" aria-label="上移" title="上移" ${dis(i === 0)}>↑</button>
        <button type="button" class="ace-btn ace-btn--icon" data-action="step-down" data-index="${i}" aria-label="下移" title="下移" ${dis(i === c.steps.length - 1)}>↓</button>
        <button type="button" class="ace-btn ace-btn--icon ace-btn--danger" data-action="step-remove" data-index="${i}" aria-label="删除步骤" title="删除步骤">${icon('i-trash')}</button>
      </div>
    </div>`).join('') || '<div class="ace-empty-sm">还没有步骤</div>'}
    ${readOnly ? '' : `<button type="button" class="ace-step-add" data-action="step-add">${icon('i-plus')}增加步骤</button>`}
  </div>`;
  return `<div id="aceEditing" class="ace-editor">
    <div class="ace-col-head ace-editor-head">
      <h3>${readOnly ? '查看用例' : '编辑用例'}</h3>
      ${d ? `<span class="ace-status ace-status--${tones[d.submit_status] || 'muted'}">${labels[d.submit_status] || esc(d.submit_status)}</span>` : ''}
    </div>
    <div class="ace-editor-body"><fieldset class="ace-fieldset" ${dis(readOnly || state.busy)}>${fields}</fieldset></div>
    <div class="ace-foot">
      <button type="button" class="secondary ace-foot-btn" data-action="copy">${icon('i-copy')}复制当前用例</button>
      <span class="ace-tools-gap"></span>
      <button type="button" class="secondary ace-foot-btn" data-action="cancel-edit" ${dis(state.busy)}>${readOnly ? '关闭查看' : '放弃本次修改'}</button>
      ${readOnly ? '' : `<button type="button" data-action="save" ${dis(state.busy)}>${icon('i-check')}保存草稿</button>`}
    </div>
  </div>`;
}

function previewCase(d, index) {
  const ctx = state.workspace.context;
  const c = d.content;
  const module = ctx.modules.find((m) => m.id === c.module_id)?.name || '待选择';
  return `<article class="ace-pv-case">
    <header><span class="ace-src-no">${index + 1}</span><h4>${esc(c.title)}</h4></header>
    <div class="ace-chips">
      <span class="ace-chip">${esc(ctx.types[c.case_type] || '类型待选择')}</span>
      <span class="ace-chip">优先级 ${esc(c.priority || '待选择')}</span>
      <span class="ace-chip">模块 ${esc(module)}</span>
      <span class="ace-chip">${esc(ctx.stages[c.stage] || '阶段不指定')}</span>
      ${c.keywords ? `<span class="ace-chip">关键词 ${esc(c.keywords)}</span>` : ''}
    </div>
    ${c.precondition ? `<div class="ace-pv-pre"><span>前置条件</span><p>${esc(c.precondition)}</p></div>` : ''}
    <div class="ace-pv-steps">
      <div class="ace-pv-steps-head"><span>#</span><span>操作步骤</span><span>预期结果</span></div>
      ${c.steps.map((s, i) => `<div class="ace-pv-step"><span>${i + 1}</span><p>${esc(s.step)}</p><p>${esc(s.expected)}</p></div>`).join('')}
    </div>
  </article>`;
}

function renderPreview() {
  const w = state.workspace;
  const drafts = selectedDrafts();
  return `<div class="ace-preview">
    <div class="ace-col-head">
      <h3>提交预览</h3><span class="ace-count">${drafts.length}</span>
    </div>
    <div class="ace-preview-body">
      <dl class="ace-target">
        <div><dt>目标禅道</dt><dd title="${esc(w.base_url)}">${esc(w.base_url)}</dd></div>
        <div><dt>产品</dt><dd>${esc(w.context.product_name)} <small>#${esc(w.context.product_id)}</small></dd></div>
        <div><dt>关联需求</dt><dd>${esc(w.context.story_title)} <small>#${esc(w.context.story_id)}</small></dd></div>
      </dl>
      <p class="ace-hint">将创建 ${drafts.length} 条新用例并关联上述需求。成功的用例会保留禅道编号；失败的可以修改后重新提交；超时的需要先在禅道里核实。</p>
      ${drafts.map(previewCase).join('')}
    </div>
    <div class="ace-foot">
      <button type="button" class="secondary ace-foot-btn" data-action="cancel-preview" ${dis(state.busy)}>${icon('i-arrow-left')}返回整理</button>
      <span class="ace-tools-gap"></span>
      <button type="button" data-action="send" ${dis(state.busy)}>${icon('i-send')}${state.busy ? '正在提交…' : `确认创建 ${drafts.length} 条`}</button>
    </div>
  </div>`;
}

function render({ resetSourceScroll = false } = {}) {
  const root = ensureRoot();
  if (!state?.workspace) return;
  const sourceScroller = root.querySelector('[data-source-scroll]');
  if (sourceScroller) state.sourceScrollTop = sourceScroller.scrollTop;
  if (resetSourceScroll) state.sourceScrollTop = 0;
  const scrollTop = root.scrollTop;
  const w = state.workspace;
  // 入场动画只在打开后的第一次渲染播放；之后每次勾选 / 操作重绘都不能让整块面板再弹一次
  const enter = !state.entered;
  state.entered = true;
  root.innerHTML = `<div class="ace-panel${enter ? ' ace-panel--enter' : ''}${state.busy ? ' is-busy' : ''}">
    <header class="ace-head">
      <span class="atl-icon" aria-hidden="true"><svg><use href="#i-sparkles"/></svg></span>
      <div class="ace-head-text">
        <h2>整理 AI 用例并提交禅道</h2>
        <div class="ace-ctx"><span>${esc(w.context.product_name)}</span><span>需求 #${esc(w.context.story_id)} ${esc(w.context.story_title)}</span><span class="ace-ctx-tip">保存后可以关闭或刷新，下次继续整理</span></div>
      </div>
      ${stepper()}
      <button type="button" class="ace-close" data-action="close" aria-label="关闭" title="关闭" ${dis(state.busy)}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
    </header>
    ${!w.context.module_options_complete ? `<div class="ace-banner ace-banner--info">${icon('i-info')}当前只取得了根模块和需求所在模块，其他模块暂不可选。</div>` : ''}
    ${state.notice ? `<div role="alert" class="ace-banner ace-banner--err">${icon('i-alert')}<span>${esc(state.notice)}</span></div>` : ''}
    ${state.preview ? renderPreview() : `<div class="ace-grid">
      <aside class="ace-col ace-col--src">${sourceList()}</aside>
      <main class="ace-col ace-col--drafts">${state.editing ? renderEditor() : draftList()}</main>
    </div>`}
  </div>`;
  const replacement = root.querySelector('[data-source-scroll]');
  if (replacement) replacement.scrollTop = state.sourceScrollTop || 0;
  root.scrollTop = scrollTop;
}

function template(c) {
  const ctx = state.workspace.context;
  const module = ctx.modules.find((m) => m.id === c.module_id)?.name || '待选择';
  return `用例标题：${c.title}\n类型：${ctx.types[c.case_type] || '待选择'}\n优先级：${c.priority || '待选择'}\n模块：${module}\n测试阶段：${ctx.stages[c.stage] || '不指定'}\n前置条件：\n${c.precondition}\n关键词：${c.keywords}\n测试步骤：\n${c.steps.map((s, i) => `${i + 1}. ${s.step}\n   预期：${s.expected}`).join('\n')}`;
}

function onInput(event) {
  if (!state) return;
  const target = event.target;
  if (target.hasAttribute('data-source-search')) {
    state.search = target.value;
    state.page = 0;
    const start = target.selectionStart;
    render({ resetSourceScroll: true });
    const replacement = ensureRoot().querySelector('[data-source-search]');
    replacement.focus();
    replacement.setSelectionRange(start, start);
  } else if (state.editing && target.hasAttribute('data-field')) {
    const field = target.dataset.field;
    state.editing.content[field] = ['priority', 'module_id'].includes(field) ? (target.value === '' ? null : Number(target.value)) : target.value;
    state.dirty = true;
  } else if (state.editing && target.hasAttribute('data-step')) {
    state.editing.content.steps[Number(target.dataset.step)][target.dataset.stepField] = target.value;
    state.dirty = true;
  }
}

function onChange(event) {
  if (!state || state.busy) return;
  const target = event.target;
  // Search is already handled by input; handling its blur-time change would
  // redraw the list again while the focused input is being replaced.
  if (target.hasAttribute('data-source-search')) return;
  const set = target.hasAttribute('data-source') ? state.sources : target.hasAttribute('data-draft') ? state.drafts : null;
  if (set) {
    const key = set === state.sources ? Number(target.dataset.source) : target.dataset.draft;
    target.checked ? set.add(key) : set.delete(key);
    state.pushRequest = null;
    render();
  } else onInput(event);
}

async function mutate(path, body, method = 'POST') {
  state.workspace = await json(`workspaces/${state.workspace.id}/${path}`, method, { revision: state.workspace.revision, ...body });
  state.pushRequest = null;
  state.drafts = new Set([...state.drafts].filter((id) => state.workspace.drafts.some((d) => d.id === id && editable(d))));
}

async function saveEditing() {
  if (!state.editing) return;
  const c = state.editing.content;
  if (c.module_id === null) throw new Error('请选择模块');
  if (c.title.length > 255) throw new Error('用例标题最多 255 个字符，请精简合并后的标题');
  if (c.keywords.length > 500) throw new Error('关键词最多 500 个字符，请精简后保存');
  if (c.precondition.length > 50000) throw new Error('前置条件最多 50000 个字符');
  if (c.steps.length > 1000) throw new Error('单条用例最多 1000 步，请减少步骤或撤销合并后分组提交');
  if (c.steps.some((s) => s.step.length > 20000 || s.expected.length > 20000)) throw new Error('每步操作和预期最多 20000 个字符');
  await mutate(`drafts/${state.editing.id}`, { content: c }, 'PATCH');
  state.editing = null;
  state.dirty = false;
}

async function refresh() {
  const current = state;
  const workspace = await json(`workspaces/${current.workspace.id}`);
  if (state !== current) return;
  current.workspace = workspace;
  current.drafts = new Set([...current.drafts].filter((id) => workspace.drafts.some((d) => d.id === id && editable(d))));
}

async function onClick(event) {
  const button = event.target.closest('[data-action]');
  if (!button || button.disabled || !state || state.busy) return;
  const action = button.dataset.action;
  const id = button.dataset.id;
  state.notice = '';
  if (action === 'source-all') {
    const used = new Set(state.workspace.drafts.flatMap((d) => d.sources));
    state.workspace.sources.filter((s) => !used.has(s.index) && s.content.title.toLowerCase().includes(state.search.toLowerCase())).slice(0, 1000).forEach((s) => state.sources.add(s.index));
  } else if (action === 'source-clear') state.sources.clear();
  else if (action === 'draft-all') state.workspace.drafts.filter(editable).slice(0, 1000).forEach((d) => state.drafts.add(d.id));
  else if (action === 'draft-clear') state.drafts.clear();
  else if (action === 'prev') state.page--;
  else if (action === 'next') state.page++;
  else if (action === 'edit') {
    state.editing = { id, content: structuredClone(state.workspace.drafts.find((d) => d.id === id).content) };
    state.dirty = false;
  } else if (action === 'cancel-edit') {
    if (state.dirty && !(await confirmDialog({ icon: 'i-alert', tone: 'red', title: '放弃本次修改', message: '编辑过的内容还没有保存，放弃后会恢复为上次保存的草稿。', okText: '放弃修改', cancelText: '继续编辑' }))) return;
    if (!state) return;
    state.editing = null; state.dirty = false;
  } else if (action === 'copy') {
    try { await navigator.clipboard.writeText(template(state.editing.content)); message('已复制当前用例', 'success'); }
    catch { message('复制失败，请检查浏览器剪贴板权限'); }
  } else if (action.startsWith('step-')) {
    const index = Number(button.dataset.index);
    const steps = state.editing.content.steps;
    if (action === 'step-add') steps.push({ step: '', expected: '' });
    if (action === 'step-remove') steps.splice(index, 1);
    if (action === 'step-up') [steps[index - 1], steps[index]] = [steps[index], steps[index - 1]];
    if (action === 'step-down') [steps[index + 1], steps[index]] = [steps[index], steps[index + 1]];
    state.dirty = true;
  } else if (action === 'preview') {
    const invalid = selectedDrafts().find((d) => !d.content.title.trim() || !d.content.case_type || !d.content.priority || d.content.module_id === null || !d.content.steps.length || d.content.steps.some((s) => !s.step.trim() || !s.expected.trim()));
    if (invalid) { state.notice = `请先编辑并补齐用例「${invalid.content.title || '未命名'}」的必填字段（类型、优先级、模块、每一步的操作与预期）。`; }
    else { state.preview = true; state.pushRequest = null; }
  } else if (action === 'cancel-preview') { state.preview = false; state.pushRequest = null; }
  else {
    // 需要用户确认 / 输入的操作：先问，再进入忙碌态
    let reconcileId = null;
    if (action === 'unmerge' && !(await confirmDialog({
      icon: 'i-rotate', title: '撤销合并',
      message: '恢复合并前的几条草稿。合并后做的修改会留在合并记录里，不会带回原草稿。', okText: '撤销合并',
    }))) return;
    if (action === 'confirm-not-created' && !(await confirmDialog({
      icon: 'i-alert', tone: 'red', title: '确认用例未创建',
      message: '请先在目标禅道的对应产品和需求下核实：这条用例确实没有创建。\n恢复后允许再次提交；如果实际已经创建，会产生重复用例。', okText: '确认未创建，恢复草稿',
    }))) return;
    if (action === 'reconcile') {
      const input = await promptDialog({
        icon: 'i-plug', title: '关联已核实的禅道用例',
        message: '请先在禅道里确认用例内容和归属，再填写已创建的用例编号。系统会回读校验。',
        label: '禅道用例编号', prefix: '#',
        validate: (v) => (/^\d+$/.test(v) && Number(v) > 0 ? '' : '请输入有效的用例编号'), okText: '关联并校验',
      });
      if (input === null) return;
      reconcileId = Number(input);
    }
    if (!state) return;
    state.busy = true;
    render();
    try {
      if (action === 'close') {
        if (state.editing && state.dirty) await saveEditing();
        clearTimeout(pollTimer);
        ensureRoot().style.display = 'none';
        state = null;
        return;
      }
      if (action === 'add') { if (state.sources.size > 1000) throw new Error('一次最多加入 1000 条用例，请分批整理'); await mutate('sources', { source_indexes: [...state.sources] }); state.sources.clear(); }
      if (action === 'merge') {
        if (selectedDrafts().length > 1000) throw new Error('一次最多合并 1000 条用例');
        const previous = new Set(state.workspace.drafts.map((d) => d.id));
        await mutate('merge', { draft_ids: selectedDrafts().map((d) => d.id) });
        const merged = state.workspace.drafts.find((d) => !previous.has(d.id));
        state.drafts.clear();
        if (merged) { state.drafts.add(merged.id); state.editing = { id: merged.id, content: structuredClone(merged.content) }; }
      }
      if (action === 'remove' || action === 'unmerge') await mutate(`drafts/${id}/action`, { action });
      if (action === 'confirm-not-created') await mutate(`drafts/${id}/action`, { action });
      if (action === 'save') { await saveEditing(); message('草稿已保存', 'success'); }
      if (action === 'refresh') await refresh();
      if (action === 'resume') state.workspace = await json(`workspaces/${state.workspace.id}/resume`, 'POST');
      if (action === 'sync') state.workspace = await json(`workspaces/${state.workspace.id}/drafts/${id}/sync`, 'POST');
      if (action === 'reconcile') {
        state.workspace = await json(`workspaces/${state.workspace.id}/drafts/${id}/reconcile`, 'POST', { case_id: reconcileId });
      }
      if (action === 'send') {
        const draftIds = selectedDrafts().map((d) => d.id);
        if (draftIds.length > 1000) throw new Error('一次最多提交 1000 条用例，请分批提交');
        state.pushRequest ||= { request_id: requestId(), draft_ids: draftIds, revision: state.workspace.revision };
        const batch = await json(`workspaces/${state.workspace.id}/push`, 'POST', state.pushRequest);
        state.preview = false;
        state.pushRequest = null;
        state.drafts.clear();
        await refresh();
        message(`已提交 ${batch.total} 条，结果将逐条更新`, 'success');
      }
    } catch (error) {
      state.notice = error.message || '操作失败';
    } finally {
      if (state) state.busy = false;
    }
  }
  if (state) {
    render({ resetSourceScroll: action === 'prev' || action === 'next' });
    if (state.editing) ensureRoot().querySelector('#aceEditing')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    schedulePoll();
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (!state || !state.workspace.drafts.some((d) => ['queued', 'submitting'].includes(d.submit_status))) return;
  pollTimer = setTimeout(async () => {
    if (!state) return;
    const current = state;
    if (!state.busy) {
      try { await refresh(); if (state === current && !state.editing && !state.preview) render(); }
      catch (error) { if (state === current) { state.notice = error.message; if (!state.editing) render(); } }
    }
    schedulePoll();
  }, 3000);
}

export async function openCaseEditor(resultId) {
  const root = ensureRoot();
  if (state?.busy) { message('当前操作正在处理，请稍候'); return; }
  if (state?.editing && state.dirty) { message('请先保存或放弃当前修改'); return; }
  clearTimeout(pollTimer);
  state = { workspace: null, sources: new Set(), drafts: new Set(), search: '', page: 0, busy: true, notice: '', editing: null, dirty: false, preview: false, pushRequest: null };
  root.style.display = 'block';
  root.innerHTML = `<div class="ace-panel ace-panel--loading ace-panel--enter"><div class="ace-loading"><span class="aig-orb" aria-hidden="true"><svg><use href="#i-sparkles"/></svg></span>正在加载草稿和禅道归属…</div></div>`;
  try {
    state.workspace = await json(`results/${resultId}/workspace`, 'POST');
    state.busy = false;
    render();
    schedulePoll();
  } catch (error) {
    root.innerHTML = `<div class="ace-panel ace-panel--loading"><div class="ace-loading ace-loading--err">${icon('i-alert')}<p>${esc(error.message || '加载失败')}</p><button type="button" class="secondary" data-action="close">关闭</button></div></div>`;
    state.busy = false;
  }
}
