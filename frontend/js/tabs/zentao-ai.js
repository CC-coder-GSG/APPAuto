import { api } from '../api.js';

const state = {
  initialized: false,
  loadingExecutions: false,
  loadingStories: false,
  submitting: false,
  executions: [],
  stories: [],
  selectedStoryIds: new Set(),
};

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function toast(message, level = 'error') {
  if (window.showMessage) window.showMessage(message, level);
}

function $(id) {
  return document.getElementById(id);
}

function renderExecutions() {
  const sel = $('zentaoAiExecutionSelect');
  if (!sel) return;
  const prev = sel.value;
  if (!state.executions.length) {
    sel.innerHTML = '<option value="">（当前账号在禅道下没有可见的执行）</option>';
    return;
  }
  sel.innerHTML = state.executions.map((e) => {
    const label = `${e.name || '(未命名)'}${e.project_name ? ` · ${e.project_name}` : ''}${e.status ? ` · ${e.status}` : ''}`;
    return `<option value="${e.id}">${escapeHtml(label)}</option>`;
  }).join('');
  if (prev && state.executions.some((e) => String(e.id) === String(prev))) {
    sel.value = prev;
  }
  renderExecMeta();
}

function renderExecMeta() {
  const meta = $('zentaoAiExecMeta');
  if (!meta) return;
  const id = Number($('zentaoAiExecutionSelect')?.value || 0);
  const exec = state.executions.find((e) => Number(e.id) === id);
  if (!exec) { meta.textContent = ''; return; }
  const parts = [];
  if (exec.begin || exec.end) parts.push(`周期 ${exec.begin || '?'} → ${exec.end || '?'}`);
  if (exec.status) parts.push(`状态 ${exec.status}`);
  if (exec.project_name) parts.push(`项目 ${exec.project_name}`);
  meta.textContent = parts.join(' · ');
}

function renderStories() {
  const tbody = $('zentaoAiStoryTbody');
  if (!tbody) return;
  if (state.loadingStories) {
    tbody.innerHTML = '<tr><td colspan="8" class="muted" style="text-align:center; padding:18px;">加载中...</td></tr>';
    return;
  }
  if (!state.stories.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="muted" style="text-align:center; padding:18px;">该执行下暂无需求</td></tr>';
    updateSelectedCount();
    return;
  }
  tbody.innerHTML = state.stories.map((s) => {
    const checked = state.selectedStoryIds.has(s.id) ? 'checked' : '';
    return `<tr>
      <td><input type="checkbox" data-story-id="${s.id}" ${checked} onchange="window.OmniQAZentaoAiTab && window.OmniQAZentaoAiTab.onStoryToggle(${s.id}, this.checked)"></td>
      <td>#${s.id}</td>
      <td>${escapeHtml(s.title || '')}</td>
      <td>${s.pri ?? ''}</td>
      <td>${escapeHtml(s.status || '')}</td>
      <td>${escapeHtml(s.stage || '')}</td>
      <td>${escapeHtml(s.assigned_to || '')}</td>
      <td>${escapeHtml(s.product_name || '')}</td>
    </tr>`;
  }).join('');
  updateSelectedCount();
}

function updateSelectedCount() {
  const el = $('zentaoAiSelectedCount');
  if (!el) return;
  const selectAll = !!$('zentaoAiSelectAll')?.checked;
  if (selectAll) {
    el.textContent = `已选：全部（${state.stories.length} 条可见）`;
  } else {
    el.textContent = `已选：${state.selectedStoryIds.size} 条`;
  }
}

async function loadExecutions() {
  if (state.loadingExecutions) return;
  state.loadingExecutions = true;
  try {
    const res = await api('/zentao/ai/executions');
    state.executions = await res.json();
  } catch (err) {
    toast(err.message || '加载执行列表失败', 'error');
    state.executions = [];
  } finally {
    state.loadingExecutions = false;
    renderExecutions();
  }
}

async function loadStories() {
  const id = Number($('zentaoAiExecutionSelect')?.value || 0);
  if (!id) { toast('请先选择执行', 'error'); return; }
  state.loadingStories = true;
  state.selectedStoryIds = new Set();
  const selectAllEl = $('zentaoAiSelectAll');
  if (selectAllEl) selectAllEl.checked = false;
  renderStories();
  try {
    const res = await api(`/zentao/ai/executions/${id}/stories`);
    state.stories = await res.json();
  } catch (err) {
    toast(err.message || '加载需求失败', 'error');
    state.stories = [];
  } finally {
    state.loadingStories = false;
    renderStories();
  }
}

function onStoryToggle(storyId, checked) {
  const id = Number(storyId);
  if (checked) state.selectedStoryIds.add(id);
  else state.selectedStoryIds.delete(id);
  const selectAllEl = $('zentaoAiSelectAll');
  if (selectAllEl) selectAllEl.checked = false;
  updateSelectedCount();
}

function toggleSelectAll(checked) {
  if (checked) {
    state.selectedStoryIds = new Set(state.stories.map((s) => s.id));
  } else {
    state.selectedStoryIds = new Set();
  }
  document.querySelectorAll('#zentaoAiStoryTbody input[data-story-id]').forEach((el) => { el.checked = checked; });
  updateSelectedCount();
}

function onExecutionChange() {
  state.stories = [];
  state.selectedStoryIds = new Set();
  const selectAllEl = $('zentaoAiSelectAll');
  if (selectAllEl) selectAllEl.checked = false;
  renderStories();
  renderExecMeta();
}

async function submit() {
  if (state.submitting) return;
  const id = Number($('zentaoAiExecutionSelect')?.value || 0);
  if (!id) { toast('请先选择执行', 'error'); return; }
  const selectAll = !!$('zentaoAiSelectAll')?.checked;
  const storyIds = Array.from(state.selectedStoryIds);
  if (!selectAll && !storyIds.length) { toast('请至少选择一条需求或勾选全部', 'error'); return; }
  const userNote = ($('zentaoAiUserNote')?.value || '').trim();

  state.submitting = true;
  const submitState = $('zentaoAiSubmitState');
  if (submitState) submitState.textContent = '正在转发到 n8n...';
  const resultWrap = $('zentaoAiResult');
  if (resultWrap) resultWrap.classList.add('hidden');

  try {
    const res = await api('/zentao/ai/generate', {
      method: 'POST',
      body: {
        execution_id: id,
        story_ids: storyIds,
        select_all: selectAll,
        user_note: userNote,
      },
    });
    const data = await res.json();
    showResult(data);
    toast(data.ok ? `已转发 ${data.forwarded_story_count} 条需求到 n8n` : `n8n 返回非 2xx（${data.n8n_status ?? '未知'}）`, data.ok ? 'success' : 'error');
  } catch (err) {
    toast(err.message || '提交失败', 'error');
  } finally {
    state.submitting = false;
    if (submitState) submitState.textContent = '';
  }
}

function showResult(data) {
  const wrap = $('zentaoAiResult');
  const summary = $('zentaoAiResultSummary');
  const body = $('zentaoAiResultBody');
  if (!wrap || !summary || !body) return;
  wrap.classList.remove('hidden');
  const bits = [
    `状态：${data.ok ? '成功' : '失败'}`,
    `转发需求数：${data.forwarded_story_count}`,
    `n8n HTTP：${data.n8n_status ?? '-'}`,
  ];
  summary.textContent = bits.join(' · ');
  try {
    body.textContent = typeof data.n8n_response === 'string'
      ? data.n8n_response
      : JSON.stringify(data.n8n_response, null, 2);
  } catch {
    body.textContent = String(data.n8n_response ?? '');
  }
}

async function activate() {
  if (!state.initialized) {
    state.initialized = true;
    await loadExecutions();
  }
}

async function reload() {
  await loadExecutions();
}

window.OmniQAZentaoAiTab = {
  activate,
  reload,
  loadStories,
  onStoryToggle,
  toggleSelectAll,
  onExecutionChange,
  submit,
};
