import { api } from '../api.js';

const ID = 'aiCaseEditor';
const labels = { draft: '待提交', queued: '等待提交', submitting: '正在提交', succeeded: '已创建', failed: '提交失败', unknown: '结果待核实' };
let state = null;
let pollTimer = null;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const editable = (draft) => ['draft', 'failed'].includes(draft.submit_status);
const message = (text, level = 'error') => window.showMessage?.(text, level);
const requestId = () => crypto.randomUUID ? crypto.randomUUID() : '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (c) => (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));

async function json(path, method = 'GET', body) {
  return (await api(`/zentao/ai/${path}`, { method, ...(body ? { body } : {}) })).json();
}

function ensureRoot() {
  let root = document.getElementById(ID);
  if (root) return root;
  root = document.createElement('div');
  root.id = ID;
  root.style.cssText = 'position:fixed;inset:0;z-index:2100;background:rgba(15,23,42,.6);overflow:auto;padding:20px;display:none';
  const style = document.createElement('style');
  style.textContent = `#${ID} *{box-sizing:border-box} #${ID} .ace-panel{max-width:1380px;margin:auto;background:#fff;border-radius:12px;padding:22px;color:#0f172a} #${ID} .ace-grid{display:grid;grid-template-columns:300px minmax(0,1fr);gap:20px} #${ID} .ace-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0} #${ID} button{border:1px solid #cbd5e1;border-radius:6px;padding:6px 10px;background:#fff;cursor:pointer;color:#334155} #${ID} button.primary{background:#2563eb;color:white;border-color:#2563eb} #${ID} button:disabled{opacity:.45;cursor:default} #${ID} input:not([type=checkbox]),#${ID} select,#${ID} textarea{width:100%;border:1px solid #cbd5e1;border-radius:6px;padding:8px;color:#0f172a;background:#fff;font:inherit} #${ID} textarea{resize:vertical;min-height:74px} #${ID} .ace-card{border:1px solid #dbe3ef;border-radius:8px;padding:12px;margin:8px 0;overflow-wrap:anywhere} #${ID} .ace-muted{font-size:12px;color:#64748b} #${ID} .ace-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px} #${ID} label.ace-field{display:grid;gap:5px;font-size:13px} #${ID} table{width:100%;border-collapse:collapse} #${ID} td,#${ID} th{padding:6px;text-align:left;border-bottom:1px solid #e2e8f0;vertical-align:top} #${ID} .ace-notice{padding:10px 12px;background:#eff6ff;border-radius:6px;line-height:1.6;font-size:13px} #${ID} .ace-warning{color:#b45309;font-size:13px;white-space:pre-wrap;margin-top:6px} #${ID} .ace-error{color:#b91c1c;font-size:13px;white-space:pre-wrap;margin-top:6px} #${ID} a{color:#2563eb} @media(max-width:850px){#${ID}{padding:8px!important}#${ID} .ace-grid{grid-template-columns:1fr}#${ID} .ace-fields{grid-template-columns:1fr}}`;
  document.head.appendChild(style);
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

function sourceList() {
  const { workspace: w } = state;
  const used = new Set(w.drafts.flatMap((d) => d.sources));
  const filtered = w.sources.filter((s) => s.content.title.toLowerCase().includes(state.search.toLowerCase()));
  const pages = Math.max(1, Math.ceil(filtered.length / 20));
  state.page = Math.min(state.page, pages - 1);
  const visible = filtered.slice(state.page * 20, state.page * 20 + 20);
  return `<h3 style="margin:0">AI 原始小用例 <span class="ace-muted">${w.sources.length} 条</span></h3>
    <div class="ace-toolbar"><input aria-label="搜索原始用例" data-source-search value="${esc(state.search)}" placeholder="搜索小用例标题"></div>
    <div class="ace-toolbar"><button data-action="source-all">选择筛选结果</button><button data-action="source-clear">清空</button></div>
    <div data-source-scroll style="max-height:540px;overflow:auto">${visible.map((s) => `<label class="ace-card" style="display:block;background:${used.has(s.index) ? '#f8fafc' : '#fff'}"><input type="checkbox" data-source="${s.index}" ${state.sources.has(s.index) ? 'checked' : ''} ${used.has(s.index) ? 'disabled' : ''}> <b>${s.index + 1}. ${esc(s.content.title || '未命名用例')}</b><div class="ace-muted" style="margin-top:5px">${esc(w.context.types[s.content.case_type] || '类型待选择')} · ${s.content.steps.length} 步${used.has(s.index) ? ' · 已加入草稿' : ''}</div></label>`).join('') || '<p class="ace-muted">没有匹配的小用例</p>'}</div>
    <div class="ace-toolbar"><button data-action="prev" ${state.page === 0 ? 'disabled' : ''}>上一页</button><span class="ace-muted">${state.page + 1} / ${pages}</span><button data-action="next" ${state.page >= pages - 1 ? 'disabled' : ''}>下一页</button></div>
    <button class="primary" data-action="add" ${state.sources.size && !state.busy && !state.editing ? '' : 'disabled'}>加入 ${state.sources.size} 条到草稿</button>`;
}

function draftList() {
  const w = state.workspace;
  const selected = selectedDrafts();
  const pending = w.drafts.some((d) => ['queued', 'submitting'].includes(d.submit_status));
  return `<h3 style="margin:0">待提交草稿 <span class="ace-muted">${w.drafts.length} 条</span></h3>
    <div class="ace-toolbar"><button data-action="draft-all">选择全部待提交</button><button data-action="draft-clear">清空</button><button data-action="merge" ${selected.length >= 2 && !state.busy && !state.editing ? '' : 'disabled'}>合并所选 ${selected.length} 条</button><button data-action="refresh" ${state.busy || state.editing ? 'disabled' : ''}>刷新</button>${pending ? `<button data-action="resume" ${state.busy || state.editing ? 'disabled' : ''}>继续等待中的提交</button>` : ''}</div>
    ${w.drafts.map((d) => `<div class="ace-card"><div style="display:flex;align-items:center;gap:8px"><input type="checkbox" data-draft="${esc(d.id)}" ${state.drafts.has(d.id) ? 'checked' : ''} ${editable(d) && !state.editing ? '' : 'disabled'}><b style="flex:1">${esc(d.content.title || '未命名用例')}</b><span class="ace-muted">${labels[d.submit_status] || esc(d.submit_status)}</span></div>
      <div class="ace-muted" style="margin-top:6px">来源 ${d.sources.map((i) => i + 1).join('、')} · ${d.content.steps.length} 步 · ${esc(w.context.types[d.content.case_type] || '类型待选择')} · ${d.content.priority ? `优先级 ${d.content.priority}` : '优先级待选择'}</div>
      ${d.error ? `<div class="ace-error">${esc(d.error)}</div>` : ''}${d.warning ? `<div class="ace-warning">${esc(d.warning)}</div>` : ''}
      <div class="ace-toolbar" style="margin-bottom:0"><button data-action="edit" data-id="${esc(d.id)}" ${state.busy || state.editing ? 'disabled' : ''}>${editable(d) ? '编辑' : '查看'}</button>${editable(d) ? `<button data-action="remove" data-id="${esc(d.id)}" ${state.busy || state.editing ? 'disabled' : ''}>移出</button>${d.parents.length ? `<button data-action="unmerge" data-id="${esc(d.id)}" ${state.busy || state.editing ? 'disabled' : ''}>撤销合并</button>` : ''}` : ''}${d.remote_url ? `<a target="_blank" rel="noopener noreferrer" href="${esc(d.remote_url)}">禅道 #${d.remote_case_id}</a><button data-action="sync" data-id="${esc(d.id)}" ${state.busy || state.editing ? 'disabled' : ''}>重新同步</button>` : ''}${d.submit_status === 'unknown' ? `<button data-action="reconcile" data-id="${esc(d.id)}" ${state.busy || state.editing ? 'disabled' : ''}>关联已核实的禅道用例</button><button data-action="confirm-not-created" data-id="${esc(d.id)}" ${state.busy || state.editing ? 'disabled' : ''}>确认未创建，恢复草稿</button>` : ''}</div></div>`).join('') || '<div class="ace-notice">从左侧选择需要的小用例，加入后可单独编辑，也可勾选几条合并。</div>'}
    ${state.editing ? renderEditor() : ''}
    <div class="ace-toolbar" style="justify-content:flex-end;border-top:1px solid #e2e8f0;padding-top:12px"><span class="ace-muted">最终选中 ${selected.length} 条</span><button data-action="preview" class="primary" ${selected.length && !state.busy && !state.editing ? '' : 'disabled'}>预览并提交禅道</button></div>`;
}

function renderEditor() {
  const e = state.editing;
  const c = e.content;
  const ctx = state.workspace.context;
  const d = state.workspace.drafts.find((x) => x.id === e.id);
  const readOnly = !editable(d);
  const fields = `<div class="ace-fields">
    <label class="ace-field" style="grid-column:1/-1">标题<input data-field="title" maxlength="255" value="${esc(c.title)}"></label>
    <label class="ace-field">类型<select data-field="case_type">${options(ctx.types, c.case_type)}</select></label>
    <label class="ace-field">优先级<select data-field="priority">${options({ 1: '1 · 最高', 2: '2 · 高', 3: '3 · 中', 4: '4 · 低' }, c.priority)}</select></label>
    <label class="ace-field">模块<select data-field="module_id">${options(Object.fromEntries(ctx.modules.map((m) => [m.id, m.name])), c.module_id)}</select></label>
    <label class="ace-field">测试阶段<select data-field="stage">${options(ctx.stages, c.stage, '不指定')}</select></label>
    <label class="ace-field" style="grid-column:1/-1">前置条件<textarea data-field="precondition" maxlength="50000">${esc(c.precondition)}</textarea></label>
    <label class="ace-field" style="grid-column:1/-1">关键词（分号分隔）<input data-field="keywords" maxlength="500" value="${esc(c.keywords)}"></label>
  </div><p class="ace-muted">优先级映射：高→2，中→3，低→4；无法识别的值需手动选择。合并后请检查每个场景的准备条件和状态重置。</p>
  <table><thead><tr><th style="width:42px">#</th><th>操作步骤</th><th>预期结果</th><th style="width:90px">顺序</th></tr></thead><tbody>${c.steps.map((s, i) => `<tr><td>${i + 1}</td><td><textarea data-step="${i}" data-step-field="step" maxlength="20000">${esc(s.step)}</textarea></td><td><textarea data-step="${i}" data-step-field="expected" maxlength="20000">${esc(s.expected)}</textarea></td><td><button data-action="step-up" data-index="${i}" ${i === 0 ? 'disabled' : ''}>↑</button><button data-action="step-down" data-index="${i}" ${i === c.steps.length - 1 ? 'disabled' : ''}>↓</button><button data-action="step-remove" data-index="${i}">删除</button></td></tr>`).join('')}</tbody></table>`;
  return `<div id="aceEditing" class="ace-card" style="border-color:#2563eb;background:#f8fafc"><h3 style="margin-top:0">${readOnly ? '查看用例' : '编辑用例'}</h3><fieldset style="border:0;padding:0;margin:0;min-width:0" ${readOnly || state.busy ? 'disabled' : ''}>${fields}${readOnly ? '' : '<div class="ace-toolbar"><button data-action="step-add">增加步骤</button></div>'}</fieldset><div class="ace-toolbar"><button data-action="copy">复制当前用例</button>${readOnly ? '' : `<button class="primary" data-action="save" ${state.busy ? 'disabled' : ''}>保存草稿</button>`}<button data-action="cancel-edit" ${state.busy ? 'disabled' : ''}>${readOnly ? '关闭查看' : '放弃本次修改'}</button></div></div>`;
}

function renderPreview() {
  const w = state.workspace;
  const drafts = selectedDrafts();
  return `<div class="ace-card" style="border-color:#2563eb"><h3>提交预览 · ${drafts.length} 条</h3><div class="ace-notice">目标：${esc(w.base_url)}<br>产品：${esc(w.context.product_name)}（#${w.context.product_id}）<br>需求：${esc(w.context.story_title)}（#${w.context.story_id}）</div>
    ${drafts.map((d) => `<details class="ace-card" open><summary><b>${esc(d.content.title)}</b></summary><div style="white-space:pre-wrap;margin-top:8px">${esc(template(d.content))}</div></details>`).join('')}
    <p class="ace-muted">将创建 ${drafts.length} 条新用例并关联上述需求。成功项保留禅道编号，失败项可修改后重新提交，超时项需先核实。</p><div class="ace-toolbar"><button data-action="send" class="primary" ${state.busy ? 'disabled' : ''}>确认创建 ${drafts.length} 条</button><button data-action="cancel-preview" ${state.busy ? 'disabled' : ''}>返回整理</button></div></div>`;
}

function render({ resetSourceScroll = false } = {}) {
  const root = ensureRoot();
  if (!state?.workspace) return;
  const sourceScroller = root.querySelector('[data-source-scroll]');
  if (sourceScroller) state.sourceScrollTop = sourceScroller.scrollTop;
  if (resetSourceScroll) state.sourceScrollTop = 0;
  const scrollTop = root.scrollTop;
  const w = state.workspace;
  root.innerHTML = `<div class="ace-panel"><div style="display:flex;align-items:center;gap:12px"><div style="flex:1"><h2 style="font-size:20px;margin:0">整理 AI 用例并提交禅道</h2><div class="ace-muted" style="margin-top:6px">${esc(w.context.product_name)} · 需求 #${w.context.story_id} ${esc(w.context.story_title)} · 保存后可关闭或刷新继续整理</div></div><button data-action="close" ${state.busy ? 'disabled' : ''}>关闭</button></div>
    <div class="ace-notice" style="margin:14px 0">先加入需要的小用例，再选择单独提交或合并，编辑保存后预览提交。${!w.context.module_options_complete ? '<br>当前仅取得根模块和需求模块；其他模块暂不可选。' : ''}</div>
    ${state.notice ? `<div role="alert" class="ace-error" style="margin-bottom:12px">${esc(state.notice)}</div>` : ''}
    ${state.preview ? renderPreview() : `<div class="ace-grid"><aside>${sourceList()}</aside><main>${draftList()}</main></div>`}</div>`;
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
  } else if (action === 'cancel-edit') { state.editing = null; state.dirty = false; }
  else if (action === 'copy') {
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
    if (invalid) { state.notice = `请先编辑并补齐用例「${invalid.content.title || '未命名'}」的必填字段。`; }
    else { state.preview = true; state.pushRequest = null; }
  } else if (action === 'cancel-preview') { state.preview = false; state.pushRequest = null; }
  else {
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
      if (action === 'remove' || action === 'unmerge') {
        if (action === 'unmerge' && !window.confirm('恢复合并前的草稿。合并后做的修改会留在合并记录中，不会带回原草稿。继续？')) return;
        await mutate(`drafts/${id}/action`, { action });
      }
      if (action === 'confirm-not-created') {
        if (!window.confirm('请确认已经在目标禅道的对应产品和需求下核实：本条用例没有创建。恢复后允许再次提交；如果实际已创建，会产生重复用例。确认未创建？')) return;
        await mutate(`drafts/${id}/action`, { action });
      }
      if (action === 'save') { await saveEditing(); message('草稿已保存', 'success'); }
      if (action === 'refresh') await refresh();
      if (action === 'resume') state.workspace = await json(`workspaces/${state.workspace.id}/resume`, 'POST');
      if (action === 'sync') state.workspace = await json(`workspaces/${state.workspace.id}/drafts/${id}/sync`, 'POST');
      if (action === 'reconcile') {
        const input = window.prompt('请先在禅道确认用例内容和归属，再填写已创建的用例编号。系统会回读校验。');
        if (input === null) return;
        if (!/^\d+$/.test(input.trim()) || Number(input) <= 0) throw new Error('请输入有效的用例编号');
        state.workspace = await json(`workspaces/${state.workspace.id}/drafts/${id}/reconcile`, 'POST', { case_id: Number(input) });
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
  root.innerHTML = '<div class="ace-panel">正在加载草稿和禅道归属…</div>';
  try {
    state.workspace = await json(`results/${resultId}/workspace`, 'POST');
    state.busy = false;
    render();
    schedulePoll();
  } catch (error) {
    root.innerHTML = `<div class="ace-panel"><p class="ace-error">${esc(error.message || '加载失败')}</p><button data-action="close">关闭</button></div>`;
    state.busy = false;
  }
}
