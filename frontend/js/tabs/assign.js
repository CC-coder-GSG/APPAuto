import { loadAssignProgress, toggleAssignProgressPendingOnly, resetAssignProgressContext } from './assign-progress.js?v=20261021-2';
export { loadAssignProgress, toggleAssignProgressPendingOnly };

import { api } from '../api.js';
import { state } from '../state.js';
import { renderPreviewBtn, escapeHtml } from '../utils.js?v=20261014-2';
import { showLoading, hideLoading } from '../components/common.js';

function getUsers() {
  return window.users || state.users || [];
}

let selectedRequirements = new Set();
let boardContext = '';
let loadedContext = '';
let loadSequence = 0;
let boardLoading = false;
let assignBusy = false;
let lastBatchResult = null;

function currentAssignContext() {
  return `${Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0)}:${Number(document.getElementById('assignMajorSelect')?.value || 0)}`;
}

function updateAssignSelectionUI() {
  const rows = Array.isArray(state.assignReqs) ? state.assignReqs : [];
  const ready = !assignBusy && !boardLoading && loadedContext === currentAssignContext()
    && !!Number(document.getElementById('assignMajorSelect')?.value || 0);
  const all = document.getElementById('assignSelectAll');
  if (all) {
    all.disabled = !ready || !rows.length;
    all.checked = rows.length > 0 && selectedRequirements.size === rows.length;
    all.indeterminate = selectedRequirements.size > 0 && selectedRequirements.size < rows.length;
  }
  const count = document.getElementById('assignSelectionCount');
  if (count) count.textContent = boardLoading ? '正在加载需求…' : `已选 ${selectedRequirements.size} / ${rows.length} 个需求${ready ? '' : '（当前不可操作）'}`;
  document.querySelectorAll('[data-assign-action]').forEach(el => { el.disabled = !ready || !selectedRequirements.size; });
  document.querySelectorAll('#assignTable input[type="checkbox"], #assignTable select').forEach(el => { el.disabled = !ready; });
  document.querySelectorAll('[data-assign-lock], #assignMajorSelect, #assignTaskStartDate, #assignTaskDeadline').forEach(el => { el.disabled = assignBusy; });
}

export function toggleAssignSelectAll(checked) {
  if (assignBusy || boardLoading || loadedContext !== currentAssignContext()) return;
  selectedRequirements = new Set(checked ? (state.assignReqs || []).map(r => Number(r.id)) : []);
  document.querySelectorAll('#assignTable [data-assign-id]').forEach(el => { el.checked = checked; });
  updateAssignSelectionUI();
}

export function toggleAssignRequirement(id, checked) {
  if (assignBusy || boardLoading || loadedContext !== currentAssignContext()) return;
  if (checked) selectedRequirements.add(Number(id)); else selectedRequirements.delete(Number(id));
  updateAssignSelectionUI();
}

function syncLinkSourceMajorOptions() {
  const assignMajorEl = document.getElementById('assignMajorSelect');
  const sourceEl = document.getElementById('linkSourceMajorSelect');
  if (!assignMajorEl || !sourceEl) return;
  const currentTarget = Number(assignMajorEl.value || 0);
  const allMajors = (window.versions || state.versions || []).filter((v) => v.version_type === 'major');
  sourceEl.innerHTML = allMajors
    .filter((v) => Number(v.id) !== currentTarget)
    .map((v) => `<option value="${v.id}">${v.version_no}</option>`)
    .join('');
}

export async function syncAssignRequirementsFromZentao(options = {}) {
  const { silent = false } = options;
  const majorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  if (!majorId) {
    if (!silent) window.showMessage && window.showMessage('请先选择一个具体大版本再同步需求', 'error');
    return null;
  }
  try {
    const res = await api(`/requirements/admin/sync-zentao?major_version_id=${majorId}`, { method: 'POST' });
    const data = await res.json();
    if (!silent) {
      window.showMessage && window.showMessage(`禅道需求同步完成：远端 ${data.remote_total || 0} 条，新增 ${data.created || 0} 条，更新 ${data.updated || 0} 条`, 'success');
    }
    return data;
  } catch (err) {
    if (!silent) window.showMessage && window.showMessage(err.message || '同步禅道需求失败', 'error');
    return null;
  }
}

export async function loadAssignBoard(options = {}) {
  if (assignBusy && !options.afterAction) return;
  const sequence = ++loadSequence;
  const majorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  const sid = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  const context = currentAssignContext();
  resetAssignProgressContext();
  const sameContext = boardContext === context;
  const ownerDrafts = new Map(sameContext ? (state.assignReqs || [])
    .map(r => [Number(r.id), document.getElementById('o_' + r.id)?.value, String(r.owner_id || '')])
    .filter(([, value, original]) => value !== undefined && value !== original)
    .map(([id, value]) => [id, value]) : []);
  if (!sameContext) {
    selectedRequirements.clear();
    boardContext = context;
    const result = document.getElementById('assignBatchResult');
    if (result) result.hidden = true;
  }
  boardLoading = true;
  updateAssignSelectionUI();
  try {
    syncLinkSourceMajorOptions();

    if (majorId && !options.skipSync) {
      await syncAssignRequirementsFromZentao({ silent: true });
    }

    if (getUsers().length === 0 && typeof window.loadUsers === 'function') {
      await window.loadUsers();
    }

    let reqUrl = '/requirements/admin/list';
    const reqParams = [];
    if (majorId) reqParams.push('major_version_id=' + majorId);
    if (sid) reqParams.push('software_id=' + sid);
    if (reqParams.length) reqUrl += '?' + reqParams.join('&');

    const reqs = await (await api(reqUrl)).json();
    if (sequence !== loadSequence || currentAssignContext() !== context) return;
    if (!Array.isArray(reqs)) throw new Error('需求列表返回格式错误，请重新加载');
    state.assignReqs = reqs;
    loadedContext = context;
    selectedRequirements = new Set(reqs.filter(r => selectedRequirements.has(Number(r.id))).map(r => Number(r.id)));
    window.assignReqs = state.assignReqs;

    const countEl = document.getElementById('assignReqCount');
    if (countEl) {
      const total = Array.isArray(state.assignReqs) ? state.assignReqs.length : 0;
      countEl.textContent = majorId ? `共 ${total} 个需求` : `全部版本共 ${total} 个需求`;
    }

    const assignTable = document.getElementById('assignTable');
    if (!assignTable) return;
    const users = getUsers();
    assignTable.innerHTML = state.assignReqs.map((r) => {
      const sid = String(r.zentao_req_id || '').replace(/\D/g, '');
      const idHtml = sid
        ? `<span class="qa-story-id-nohref" data-zt-story-id="${sid}">${escapeHtml(r.zentao_req_id)}</span>`
        : escapeHtml(r.zentao_req_id || '');
      const ztSlot = sid ? ` <span class="zt-story-slot" data-zt-story-id="${sid}" style="margin-left:4px;"></span>` : '';
      const aiSlot = sid ? ` <span class="ai-result-slot" data-story-id="${sid}"></span>` : '';
      return `
      <tr>
        <td><input type="checkbox" data-assign-id="${r.id}" aria-label="选择需求 ${escapeHtml(r.zentao_req_id || r.id)}" ${selectedRequirements.has(Number(r.id)) ? 'checked' : ''} onchange="toggleAssignRequirement(${r.id}, this.checked)"></td>
        <td>${idHtml} ${escapeHtml(r.title || '')}${ztSlot}${renderPreviewBtn('story', sid)}${aiSlot}</td>
        <td>
          <select id='o_${r.id}'>
            <option value=''>未分配</option>
            ${(r.owner_id && !users.some((u) => Number(u.id) === Number(r.owner_id))) ? `<option value='${r.owner_id}' selected>原负责人(账号不存在 #${r.owner_id})</option>` : ''}
            ${users.map((u) => `<option value='${u.id}' ${u.id === r.owner_id ? 'selected' : ''}>${escapeHtml(u.display_name || u.username)}</option>`).join('')}
          </select>
        </td>
        <td class="assign-task-dates">${r.zentao_task_id ? `<span>任务 #${Number(r.zentao_task_id)}</span><small>${r.task_dates_synced === false ? '日期待同步' : `${escapeHtml(r.task_start_date || '未设置')} → ${escapeHtml(r.task_deadline || '未设置')}`}</small>` : '<span class="muted">未创建子任务</span>'}</td>
      </tr>`;
    }).join('');
    ownerDrafts.forEach((value, id) => {
      const select = document.getElementById('o_' + id);
      if (select && value !== undefined && Array.from(select.options).some(o => o.value === value)) select.value = value;
    });
    window.OmniQAStoryAI?.refreshSlots?.(assignTable);
    window.OmniQAZentao?.hydrateContainer?.(assignTable);

    await loadAssignProgress();
    await loadLinkCandidates();
    await refreshFinalTestState();
  } catch (err) {
    if (sequence === loadSequence) {
      loadedContext = '';
      window.showMessage?.(err.message || '需求加载失败，请重试', 'error');
    }
  } finally {
    if (sequence === loadSequence) {
      boardLoading = false;
      updateAssignSelectionUI();
    }
  }
}

export async function refreshFinalTestState() {
  const majorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  const btn = document.getElementById('finalTestToggleBtn');
  const card = document.getElementById('finalTestProgressCard');
  const area = document.getElementById('finalTestProgressArea');
  if (!btn) return;

  if (!majorId) {
    btn.style.display = 'none';
    if (card) card.style.display = 'none';
    return;
  }
  btn.style.display = '';

  let enabled = false;
  try {
    const status = await (await api(`/final-test/status?major_version_id=${majorId}`)).json();
    enabled = !!status.enabled;
  } catch {
    enabled = false;
  }

  if (enabled) {
    btn.textContent = '关闭最终测试';
    btn.className = '';
    btn.style.background = '#d97706';
    btn.style.color = '#fff';
  } else {
    btn.textContent = '进入最终测试';
    btn.className = 'secondary';
    btn.style.background = '';
    btn.style.color = '';
  }

  if (!enabled) {
    if (card) card.style.display = 'none';
    return;
  }

  // 已开启：拉取每人进度并渲染
  try {
    const data = await (await api(`/final-test/progress?major_version_id=${majorId}`)).json();
    if (card) card.style.display = '';
    if (area) area.innerHTML = renderFinalTestProgress(data);
  } catch {
    if (card) card.style.display = 'none';
  }
}

function renderFinalTestProgress(data) {
  const total = Number(data.total_requirements || 0);
  const reqs = data.requirements || [];
  const people = data.people || [];
  if (people.length === 0) {
    return `<div class="muted">该版本共 ${total} 个需求。暂无人员开始最终测试勾选。</div>`;
  }
  const summary = `<div class="row" style="gap:10px; flex-wrap:wrap; margin-bottom:10px;">
      <span class="badge">📄 需求总数：${total}</span>
      <span class="badge">👥 参与人数：${people.length}</span>
    </div>`;

  const peopleCards = people.map((p) => {
    const rows = reqs.map((r) => {
      const st = (p.req_status || {})[r.id] || {};
      return `<tr>
        <td>${r.zentao_req_id || ''} ${r.title || ''}</td>
        <td style="text-align:center;">${st.case_completed ? '✅' : '⏳'}</td>
        <td style="text-align:center;">${st.test_completed ? '✅' : '⏳'}</td>
      </tr>`;
    }).join('');
    return `<details class="card" style="margin-top:10px;">
      <summary style="cursor:pointer; font-weight:700; color:#92400e;">👤 ${p.user_name}（用例 ${p.case_done}/${total}，测试 ${p.test_done}/${total}）</summary>
      <table style="margin-top:8px;">
        <thead><tr><th>需求</th><th>用例完成</th><th>测试完成</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="3" class="muted">暂无需求</td></tr>'}</tbody>
      </table>
    </details>`;
  }).join('');

  return summary + peopleCards;
}

export async function toggleFinalTest() {
  const majorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  if (!majorId) {
    window.showMessage && window.showMessage('请先选择一个具体大版本', 'error');
    return;
  }
  let currentlyEnabled = false;
  try {
    const status = await (await api(`/final-test/status?major_version_id=${majorId}`)).json();
    currentlyEnabled = !!status.enabled;
  } catch {
    currentlyEnabled = false;
  }
  const nextEnabled = !currentlyEnabled;
  const msg = nextEnabled
    ? '确认让该版本进入最终测试阶段？\n\n进入后：所有人在「我的工作台」选择该版本时都能看到全部需求，并各自独立勾选（不影响原有分配与勾选状态）。'
    : '确认关闭该版本的最终测试阶段？\n\n关闭后恢复原有分配视图，最终测试数据会被保留，再次开启将延续。';
  if (!confirm(msg)) return;

  try {
    await api('/final-test/toggle', {
      method: 'POST',
      headers: window.H,
      body: { major_version_id: majorId, enabled: nextEnabled },
    });
    window.showMessage && window.showMessage(nextEnabled ? '已进入最终测试阶段' : '已关闭最终测试阶段', 'success');
    await refreshFinalTestState();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '操作失败', 'error');
  }
}

export async function publishAssign() {
  await runAssignAction('publish');
}

// 仅在禅道创建/改派任务，不发企微、不改本地负责人。用于「发布」后创建失败时单独重试。
export async function createZentaoTasks() {
  await runAssignAction('create');
}

export async function updateZentaoTaskDates() {
  await runAssignAction('dates');
}

function selectedAssignPayload() {
  if (assignBusy || boardLoading || loadedContext !== currentAssignContext()) {
    throw new Error('需求仍在加载或操作中，请稍候');
  }
  const majorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  if (!majorId) throw new Error('“全部版本”仅用于查看；请先选择一个具体大版本');
  const selected = (state.assignReqs || []).filter(r => selectedRequirements.has(Number(r.id)));
  if (!selected.length) throw new Error('请至少勾选一个需求');
  const taskStart = document.getElementById('assignTaskStartDate')?.value || '';
  const taskDeadline = document.getElementById('assignTaskDeadline')?.value || '';
  const validDate = value => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const date = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value && !value.startsWith('0000');
  };
  if (!taskStart || !taskDeadline) throw new Error('请设置完整的禅道任务预计开始和预计结束日期');
  if (!validDate(taskStart) || !validDate(taskDeadline)) throw new Error('禅道任务日期非法，请重新设置');
  if (taskStart > taskDeadline) throw new Error('预计开始日期不能晚于预计结束日期');
  return {
    major_version_id: majorId,
    assignments: selected.map(r => ({
    requirement_id: r.id,
    owner_id: Number(document.getElementById('o_' + r.id)?.value || 0) || null,
    })),
    task_start_date: taskStart,
    task_deadline: taskDeadline,
  };
}

async function runAssignAction(kind) {
  let payload;
  try { payload = selectedAssignPayload(); }
  catch (err) { window.showMessage?.(err.message, 'error'); return; }
  if (kind === 'publish' && !(window.confirmPush && window.confirmPush())) return;
  const selected = (state.assignReqs || []).filter(r => selectedRequirements.has(Number(r.id)));
  const context = currentAssignContext();
  const majorName = document.getElementById('assignMajorSelect')?.selectedOptions[0]?.textContent || '';
  const urls = { publish: '/requirements/assign-and-publish', create: '/requirements/create-zentao-tasks', dates: '/requirements/update-zentao-task-dates' };
  const body = kind === 'dates' ? {
    major_version_id: payload.major_version_id,
    requirement_ids: payload.assignments.map(a => a.requirement_id),
    task_start_date: payload.task_start_date,
    task_deadline: payload.task_deadline,
  } : payload;
  assignBusy = true;
  updateAssignSelectionUI();
  showLoading(kind === 'dates' ? '正在更新所选子任务的预计起止日期…' : '正在处理所选需求的分配及禅道任务…');
  try {
    const data = await (await api(urls[kind], { method: 'POST', headers: window.H, body })).json();
    if (kind === 'dates') showDatesResult(data, majorName, context);
    else showPublishResult(data.zentao, { taskOnly: kind === 'create', selected, payload, majorName, context });
    await loadAssignBoard({ skipSync: true, afterAction: true });
  } catch (err) {
    window.showMessage?.(err.message || '操作失败，请检查后重试', 'error');
  } finally {
    assignBusy = false;
    updateAssignSelectionUI();
    hideLoading();
  }
}

function showBatchResult(title, summary, details, context) {
  lastBatchResult = { title, summary, details };
  const area = document.getElementById('assignBatchResult');
  if (area && currentAssignContext() === context) {
    area.hidden = false;
    area.innerHTML = `<span>${escapeHtml(summary)}</span> <button class="secondary" onclick="openAssignBatchResult()">查看处理结果</button>`;
  }
  openAssignBatchResult();
}

export function openAssignBatchResult() {
  if (!lastBatchResult) return;
  let dialog = document.getElementById('assignResultDialog');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.id = 'assignResultDialog';
    dialog.className = 'assign-result-modal';
    dialog.setAttribute('aria-labelledby', 'assignResultTitle');
    document.body.appendChild(dialog);
  }
  const { title, summary, details } = lastBatchResult;
  dialog.innerHTML = `<div class="assign-result-heading"><h3 id="assignResultTitle">${escapeHtml(title)}</h3><button class="secondary" aria-label="关闭处理结果" onclick="document.getElementById('assignResultDialog').close()">关闭</button></div><p>${escapeHtml(summary)}</p><div class="assign-result-content">${details}</div>`;
  if (!dialog.open) dialog.showModal();
}

function showDatesResult(data, majorName, context) {
  const counts = data.counts || {};
  const labels = { updated: '已更新', unchanged: '无需修改', skipped: '已跳过', failed: '失败', unverified: '待核实' };
  const summary = `所选 ${data.total || 0} 个需求：更新 ${counts.updated || 0}，日期一致 ${counts.unchanged || 0}，跳过 ${counts.skipped || 0}，失败 ${counts.failed || 0}，待核实 ${counts.unverified || 0}`;
  const details = `<p class="muted">${escapeHtml(majorName)} · ${escapeHtml(data.task_start_date)} 至 ${escapeHtml(data.task_deadline)}</p><table><thead><tr><th>需求 / 子任务</th><th>结果</th><th>说明</th></tr></thead><tbody>${(data.results || []).map(row => `<tr><td>${escapeHtml(row.zentao_req_id || '#' + row.requirement_id)} ${escapeHtml(row.title)}<small>${row.task_id ? '任务 #' + Number(row.task_id) : '未创建子任务'}</small></td><td><span class="assign-result-status ${escapeHtml(row.status)}">${labels[row.status] || '未知'}</span></td><td>${escapeHtml(row.reason)}${row.warning ? `<small class="assign-result-warning">${escapeHtml(row.warning)}</small>` : ''}</td></tr>`).join('')}</tbody></table>`;
  showBatchResult('预计起止时间更新结果', summary, details, context);
}

function showPublishResult(zentao, opts = {}) {
  const lead = opts.taskOnly ? '禅道任务处理完成' : '分配发布完成';
  if (!zentao) {
    showBatchResult(lead, '本地分配已更新，未执行禅道任务联动', '', opts.context);
    return;
  }
  const adoptedIds = new Set(zentao.adopted_tasks || []);
  const created = (zentao.created_tasks || []).filter(id => !adoptedIds.has(id)).length;
  const reassigned = (zentao.reassigned_tasks || []).length;
  const retainedClosed = (zentao.retained_closed_tasks || []).length;
  const unassigned = zentao.unassigned || [];
  const errors = zentao.errors || [];
  const parts = [`所选 ${opts.selected.length} 个需求`];
  if (created) parts.push(`禅道新建子任务 ${created} 个`);
  if (adoptedIds.size) parts.push(`认领已有子任务 ${adoptedIds.size} 个（未重复创建）`);
  if (reassigned) parts.push(`改派 ${reassigned} 个`);
  if (retainedClosed) parts.push(`已存在关闭任务 ${retainedClosed} 个（未重复创建）`);
  if (unassigned.length) {
    const names = unassigned.map((u) => u.owner_name).filter(Boolean).join('、');
    parts.push(`未能指派（缺禅道账号）：${names}`);
  }
  const noOwner = opts.payload.assignments.filter(a => !a.owner_id).map(a => opts.selected.find(r => r.id === a.requirement_id));
  if (noOwner.length) parts.push(`未设置负责人 ${noOwner.length} 个，已跳过禅道建任务`);
  if (errors.length) parts.push(`禅道异常 ${errors.length} 条`);
  const details = `<p class="muted">${escapeHtml(opts.majorName)} · ${escapeHtml(opts.payload.task_start_date)} 至 ${escapeHtml(opts.payload.task_deadline)}</p>${!opts.taskOnly ? '<p>本地分配已更新并发布通知；禅道处理结果如下。</p>' : ''}<ul>${[
    ...errors.map(e => `<li class="assign-result-warning">${escapeHtml(e)}</li>`),
    ...unassigned.map(u => `<li>需求 #${Number(u.requirement_id)}：${escapeHtml(u.owner_name)} 缺少可用禅道账号，无法指派</li>`),
    ...noOwner.map(r => `<li>${escapeHtml(r.zentao_req_id)} ${escapeHtml(r.title)}：未设置负责人，跳过禅道任务创建</li>`),
  ].join('')}</ul>`;
  showBatchResult(lead, parts.join('；'), details, opts.context);
}

export async function loadLinkCandidates() {
  const targetMajorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  const sourceMajorId = Number(document.getElementById('linkSourceMajorSelect')?.value || 0);
  const area = document.getElementById('linkCandidatesArea');
  if (!area) return;

  const allEl = document.getElementById('linkSelectAll');
  if (allEl) allEl.checked = false;

  if (!targetMajorId) {
    area.innerHTML = '<div class="muted">当前处于“全部版本”模式，请先选择一个目标大版本再做关联</div>';
    return;
  }
  if (!sourceMajorId) {
    area.innerHTML = '<div class="muted">请选择来源大版本</div>';
    return;
  }
  if (targetMajorId === sourceMajorId) {
    area.innerHTML = '<div class="muted">来源大版本不能与目标大版本相同</div>';
    return;
  }
  area.innerHTML = '<div class="muted">正在加载来源需求...</div>';
  try {
    const data = await (await api(`/requirements/admin/link-options?source_major_version_id=${sourceMajorId}&target_major_version_id=${targetMajorId}`)).json();
    if (!data || data.length === 0) {
      area.innerHTML = '<div class="muted">来源版本暂无可关联需求</div>';
      return;
    }
    const canLinkCount = data.filter((x) => !x.already_linked).length;
    if (canLinkCount === 0) {
      area.innerHTML = '<div class="muted">来源版本需求均已存在于当前目标版本，无需重复关联</div>';
      return;
    }

    area.innerHTML = data.map((r) => {
      const sid = String(r.zentao_req_id || '').replace(/\D/g, '');
      const aiSlot = sid ? ` <span class="ai-result-slot" data-story-id="${sid}"></span>` : '';
      return `
      <label class="row" style="display:flex; justify-content:space-between; align-items:flex-start; border-bottom:1px dashed #e2e8f0; padding:8px 0;">
        <span style="display:flex; align-items:flex-start; gap:8px;">
          <input type="checkbox" class="link-req-check" value="${r.id}" ${r.already_linked ? 'disabled' : ''}>
          <span>
            <b>${r.zentao_req_id}</b> ${r.title || ''}${aiSlot}
            <span class="muted" style="margin-left:8px;">负责人：${r.owner_name || '未分配'} ｜ 用例：${r.case_count || 0}</span>
          </span>
        </span>
        ${r.already_linked ? '<span class="badge" style="background:#ecfeff;color:#0369a1;">已在目标版本</span>' : ''}
      </label>`;
    }).join('');
    window.OmniQAStoryAI?.refreshSlots?.(area);
  } catch (err) {
    area.innerHTML = '<div class="muted" style="color:#dc2626;">来源需求加载失败</div>';
    window.showMessage && window.showMessage(err.message || '来源需求加载失败', 'error');
  }
}

export function toggleLinkSelectAll(checked) {
  const items = document.querySelectorAll('.link-req-check');
  items.forEach((el) => {
    if (!el.disabled) el.checked = !!checked;
  });
}

export async function confirmLinkRequirements() {
  const targetMajorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  const sourceMajorId = Number(document.getElementById('linkSourceMajorSelect')?.value || 0);
  const copyStatus = !!document.getElementById('linkCopyStatus')?.checked;
  if (!targetMajorId) {
    window.showMessage && window.showMessage('请先选择目标大版本', 'error');
    return;
  }
  if (!sourceMajorId) {
    window.showMessage && window.showMessage('请先选择来源大版本', 'error');
    return;
  }

  const ids = Array.from(document.querySelectorAll('.link-req-check'))
    .filter((el) => el.checked && !el.disabled)
    .map((el) => Number(el.value));
  if (!ids.length) {
    window.showMessage && window.showMessage('请至少勾选一条需求', 'error');
    return;
  }

  try {
    const res = await api('/requirements/admin/link-major', {
      method: 'POST',
      headers: window.H,
      body: {
        target_major_version_id: targetMajorId,
        source_major_version_id: sourceMajorId,
        source_requirement_ids: ids,
        copy_status: copyStatus,
      },
    });
    const data = await res.json();
    const created = Number(data?.created_count || 0);
    const skipped = Number(data?.skipped_count || 0);
    const backendMsg = data?.message || '';
    if (created > 0) {
      window.showMessage && window.showMessage(backendMsg || `关联成功：新增 ${created} 条，跳过 ${skipped} 条重复需求`, 'success');
    } else {
      window.showMessage && window.showMessage(backendMsg || `未新增需求：所选需求均已存在（跳过 ${skipped} 条）`, 'error');
    }
    await loadAssignBoard();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '关联失败', 'error');
  }
}

window.OmniQAAssignTab = {
  loadAssignBoard,
  loadAssignProgress,
  toggleAssignProgressPendingOnly,
  publishAssign,
  createZentaoTasks,
  updateZentaoTaskDates,
  toggleAssignSelectAll,
  toggleAssignRequirement,
  openAssignBatchResult,
  syncAssignRequirementsFromZentao,
  loadLinkCandidates,
  toggleLinkSelectAll,
  confirmLinkRequirements,
  toggleFinalTest,
  refreshFinalTestState,
};
window.toggleAssignProgressPendingOnly = toggleAssignProgressPendingOnly;
window.syncAssignRequirementsFromZentao = syncAssignRequirementsFromZentao;
window.toggleFinalTest = toggleFinalTest;
window.updateZentaoTaskDates = updateZentaoTaskDates;
window.toggleAssignSelectAll = toggleAssignSelectAll;
window.toggleAssignRequirement = toggleAssignRequirement;
window.openAssignBatchResult = openAssignBatchResult;
