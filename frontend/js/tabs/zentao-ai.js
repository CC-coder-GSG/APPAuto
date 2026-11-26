import { api } from '../api.js';
import { mapZentaoStatus } from '../zentao-status-map.js';

// 禅道AI用例生成（批量）v2（2026-11-26，weave.css R21 .zai-*）
// 左侧：执行下的需求表（可按 ID / 标题筛选，整行点选）；右侧：已选数量 + 补充说明 + 提交。
// 提交后交给 ai-tasks.js 的等待浮窗跟踪；完成后需求标题旁出现 AI 标记。
// 对外接口不变（ai_case_generation.spec 直接调用 onStoryToggle / submit）。

const state = {
  initialized: false,
  loadingExecutions: false,
  loadingStories: false,
  storiesError: '',
  loaded: false,
  submitting: false,
  executions: [],
  stories: [],
  search: '',
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

// 需求阶段（禅道 story.stage）：共用的 mapZentaoStatus 不全（developed 原样显示），这里按禅道自己的叫法翻译
const STAGE_TEXT = {
  wait: '未开始', planned: '已计划', projected: '已立项', developing: '研发中', developed: '研发完毕',
  testing: '测试中', tested: '测试完毕', verified: '已验收', released: '已发布', closed: '已关闭',
};

const icon = (id) => `<svg class="ui-ic" aria-hidden="true"><use href="#${id}"/></svg>`;

function renderExecutions() {
  const sel = $('zentaoAiExecutionSelect');
  if (!sel) return;
  const prev = sel.value;
  if (!state.executions.length) {
    sel.innerHTML = '<option value="">（当前账号在禅道下没有可见的执行）</option>';
    return;
  }
  sel.innerHTML = state.executions.map((e) => {
    const status = mapZentaoStatus(e.status);
    const label = `${e.name || '(未命名)'}${e.project_name ? ` · ${e.project_name}` : ''}${status ? ` · ${status}` : ''}`;
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
  if (exec.status) parts.push(`状态 ${mapZentaoStatus(exec.status)}`);
  if (exec.project_name) parts.push(`项目 ${exec.project_name}`);
  meta.textContent = parts.join(' · ');
}

function stateRow(kind, title, text) {
  const ic = kind === 'error' ? 'i-alert' : kind === 'empty' ? 'i-search' : 'i-sparkles';
  return `<tr class="zai-state-row"><td colspan="8"><div class="zai-state zai-state--${kind}">${icon(ic)}<b>${escapeHtml(title)}</b>${text ? `<span>${escapeHtml(text)}</span>` : ''}</div></td></tr>`;
}

function visibleStories() {
  const q = state.search.trim().toLowerCase().replace(/^#/, '');
  if (!q) return state.stories;
  return state.stories.filter((s) => String(s.id).includes(q) || String(s.title || '').toLowerCase().includes(q));
}

function priBadge(pri) {
  const n = Number(pri);
  if (!n) return '<span class="zai-muted">—</span>';
  return `<span class="zai-pri zai-pri--${Math.min(Math.max(n, 1), 4)}">P${n}</span>`;
}

function renderStories() {
  const tbody = $('zentaoAiStoryTbody');
  const count = $('zentaoAiStoryCount');
  if (count) count.textContent = String(state.stories.length);
  if (!tbody) return;
  if (state.loadingStories) {
    tbody.innerHTML = Array.from({ length: 6 }, () => '<tr class="zai-skel"><td colspan="8"><i></i></td></tr>').join('');
    return;
  }
  if (state.storiesError) {
    tbody.innerHTML = stateRow('error', '加载需求失败', state.storiesError);
    updateSelectedCount();
    return;
  }
  if (!state.loaded) {
    tbody.innerHTML = stateRow('idle', '先选择一个禅道执行', '选好执行后点击「加载需求」，再勾选要交给 AI 的需求');
    updateSelectedCount();
    return;
  }
  if (!state.stories.length) {
    tbody.innerHTML = stateRow('empty', '该执行下暂无需求', '换一个执行试试');
    updateSelectedCount();
    return;
  }
  const rows = visibleStories();
  const all = !!$('zentaoAiSelectAll')?.checked;
  tbody.innerHTML = rows.map((s) => {
    const checked = all || state.selectedStoryIds.has(s.id);
    return `<tr class="zai-row${checked ? ' is-checked' : ''}" data-story-row="${s.id}">
      <td class="zai-col-check"><input type="checkbox" aria-label="选择需求 #${s.id}" data-story-id="${s.id}" ${checked ? 'checked' : ''} ${all ? 'disabled' : ''} onchange="window.OmniQAZentaoAiTab && window.OmniQAZentaoAiTab.onStoryToggle(${s.id}, this.checked)"></td>
      <td class="zai-col-id">#${s.id}</td>
      <td class="zai-col-title"><span class="zai-title">${escapeHtml(s.title || '')}</span><span class="ai-result-slot" data-story-id="${s.id}"></span></td>
      <td class="zai-col-pri">${priBadge(s.pri)}</td>
      <td class="zai-col-st"><span class="zai-chip">${escapeHtml(mapZentaoStatus(s.status) || '—')}</span></td>
      <td class="zai-col-st"><span class="zai-chip zai-chip--soft">${escapeHtml(STAGE_TEXT[s.stage] || mapZentaoStatus(s.stage) || '—')}</span></td>
      <td class="zai-col-who">${escapeHtml(s.assigned_to || '—')}</td>
      <td class="zai-col-prod" title="${escapeHtml(s.product_name || '')}">${escapeHtml(s.product_name || '—')}</td>
    </tr>`;
  }).join('') || stateRow('empty', '没有匹配的需求', `没有 ID 或标题包含「${state.search.trim()}」的需求`);
  updateSelectedCount();
  if (window.OmniQAStoryAI?.refreshSlots) {
    window.OmniQAStoryAI.refreshSlots(tbody);
  }
}

function selectedTotal() {
  return $('zentaoAiSelectAll')?.checked ? state.stories.length : state.selectedStoryIds.size;
}

function updateSelectedCount() {
  const selectAll = !!$('zentaoAiSelectAll')?.checked;
  const total = selectedTotal();
  const num = $('zentaoAiSelectedNum');
  if (num) num.textContent = selectAll ? '全部' : String(total);
  const el = $('zentaoAiSelectedCount');
  if (el) {
    el.textContent = selectAll ? `本执行下 ${state.stories.length} 条需求` : (num ? '条需求已选' : `已选：${total} 条`);
  }
  const btn = $('zentaoAiSubmitBtn');
  if (btn) {
    btn.disabled = state.submitting || !(selectAll ? state.loaded : total);
    btn.innerHTML = `${icon('i-sparkles')}${state.submitting ? '正在提交…' : `开始生成用例${!selectAll && total ? `（${total} 条）` : ''}`}`;
  }
}

function setLoadButton(loading) {
  const btn = $('zentaoAiLoadBtn');
  if (!btn) return;
  btn.disabled = loading;
  btn.classList.toggle('is-busy', loading);
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
  state.storiesError = '';
  state.selectedStoryIds = new Set();
  const selectAllEl = $('zentaoAiSelectAll');
  if (selectAllEl) selectAllEl.checked = false;
  setLoadButton(true);
  renderStories();
  try {
    const res = await api(`/zentao/ai/executions/${id}/stories`);
    state.stories = await res.json();
    state.loaded = true;
  } catch (err) {
    state.storiesError = err.message || '加载需求失败';
    state.stories = [];
  } finally {
    state.loadingStories = false;
    setLoadButton(false);
    renderStories();
  }
}

function setRowChecked(storyId, checked) {
  const row = document.querySelector(`#zentaoAiStoryTbody [data-story-row="${storyId}"]`);
  if (!row) return;
  row.classList.toggle('is-checked', checked);
  const box = row.querySelector('input[data-story-id]');
  if (box) box.checked = checked;
}

function onStoryToggle(storyId, checked) {
  const id = Number(storyId);
  if (checked) state.selectedStoryIds.add(id);
  else state.selectedStoryIds.delete(id);
  const selectAllEl = $('zentaoAiSelectAll');
  if (selectAllEl) selectAllEl.checked = false;
  setRowChecked(id, checked);
  updateSelectedCount();
}

function toggleSelectAll(checked) {
  if (checked) {
    state.selectedStoryIds = new Set(state.stories.map((s) => s.id));
  } else {
    state.selectedStoryIds = new Set();
  }
  renderStories();
}

function onSearch(value) {
  state.search = String(value || '');
  renderStories();
}

function onExecutionChange() {
  state.stories = [];
  state.loaded = false;
  state.storiesError = '';
  state.selectedStoryIds = new Set();
  const selectAllEl = $('zentaoAiSelectAll');
  if (selectAllEl) selectAllEl.checked = false;
  renderStories();
  renderExecMeta();
}

// 整行点选：点链接 / AI 标记 / 复选框本身时不重复处理
function bindRowClicks() {
  const tbody = $('zentaoAiStoryTbody');
  if (!tbody || tbody.dataset.rowClickBound) return;
  tbody.dataset.rowClickBound = '1';
  tbody.addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-story-row]');
    if (!row || e.target.closest('input, a, button, .ai-result-chip, .ai-result-slot')) return;
    if ($('zentaoAiSelectAll')?.checked) return;
    const id = Number(row.dataset.storyRow);
    onStoryToggle(id, !state.selectedStoryIds.has(id));
  });
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
  updateSelectedCount();
  const submitState = $('zentaoAiSubmitState');
  if (submitState) submitState.textContent = '正在提交后台任务...';
  const resultWrap = $('zentaoAiResult');
  if (resultWrap) resultWrap.classList.add('hidden');

  try {
    const res = await api('/zentao/ai/generate-and-save', {
      method: 'POST',
      body: {
        execution_id: id,
        story_ids: storyIds,
        select_all: selectAll,
        user_note: userNote,
      },
    });
    const data = await res.json();
    toast('用例正在生成，请稍后', 'success');
    if (window.OmniQAStoryAI?.startBatchWatch) {
      window.OmniQAStoryAI.startBatchWatch({
        batch_id: data.batch_id,
        story_ids: data.story_ids,
        expected_duration_seconds: data.expected_duration_seconds || 300,
        story_titles: Object.fromEntries(state.stories.filter((s) => (data.story_ids || []).includes(s.id)).map((s) => [s.id, s.title || ''])),
        started_at: Date.now(),
      });
    }
    // 提交成功后清空勾选，避免重复提交同一批
    state.selectedStoryIds = new Set();
    const selectAllEl = $('zentaoAiSelectAll');
    if (selectAllEl) selectAllEl.checked = false;
    if ($('zentaoAiStoryTbody')) renderStories();
  } catch (err) {
    toast(err.message || '提交失败', 'error');
  } finally {
    state.submitting = false;
    if (submitState) submitState.textContent = '';
    updateSelectedCount();
  }
}

async function activate() {
  bindRowClicks();
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
  onSearch,
  onExecutionChange,
  submit,
};
