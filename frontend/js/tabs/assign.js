import { api } from '../api.js';
import { state } from '../state.js';

function getUsers() {
  return window.users || state.users || [];
}

function isPendingReq(req) {
  return !(req.case_completed && req.test_completed);
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

function renderAssignProgress(data) {
  const area = document.getElementById('assignProgressArea');
  if (!area) return;
  const onlyPending = !!document.getElementById('assignOnlyPending')?.checked;
  const s = data.summary || {};

  const retestRows = data.retest_pending_by_major || [];
  const retestHtml = retestRows.length
    ? `<table style="margin-top:8px;"><thead><tr><th>大版本</th><th>待复测需求数</th></tr></thead><tbody>${
      retestRows.map((r) => `<tr><td>${r.major_version_name}</td><td><b style="color:#dc2626">${r.pending_retest_count}</b></td></tr>`).join('')
    }</tbody></table>`
    : '<div class="muted">当前筛选条件下，暂无待复测需求</div>';

  const ownerCards = (data.owners || []).map((owner) => {
    const allReqRows = owner.requirements || [];
    const reqRows = onlyPending ? allReqRows.filter(isPendingReq) : allReqRows;
    if (onlyPending && reqRows.length === 0) return '';

    const majorMap = new Map();
    reqRows.forEach((r) => {
      const k = Number(r.major_version_id || 0);
      const bucket = majorMap.get(k) || {
        major_version_name: r.major_version_name || '未知',
        total: 0,
        case_done: 0,
        test_done: 0,
      };
      bucket.total += 1;
      bucket.case_done += r.case_completed ? 1 : 0;
      bucket.test_done += r.test_completed ? 1 : 0;
      majorMap.set(k, bucket);
    });
    const majorBadges = Array.from(majorMap.values()).map((m) =>
      `<span class="badge" style="margin-right:6px; margin-bottom:6px;">
        ${m.major_version_name}：用例待完成 ${Math.max(0, m.total - m.case_done)}，测试待完成 ${Math.max(0, m.total - m.test_done)}
      </span>`
    ).join('');

    const reqTable = reqRows.length
      ? `<table style="margin-top:8px;">
          <thead><tr><th>大版本</th><th>需求</th><th>用例完成</th><th>测试完成</th><th>用例数</th><th>Bug数</th></tr></thead>
          <tbody>${
            reqRows.map((r) => `<tr>
              <td>${r.major_version_name}</td>
              <td>${r.zentao_req_id} ${r.title || ''}</td>
              <td>${r.case_completed ? '✅已勾选' : '⏳未勾选'}</td>
              <td>${r.test_completed ? '✅已勾选' : '⏳未勾选'}</td>
              <td>${r.case_count || 0}</td>
              <td>${r.bug_count || 0}</td>
            </tr>`).join('')
          }</tbody>
        </table>`
      : '<div class="muted">暂无需求</div>';

    return `<details class="card" open style="margin-top:12px;">
      <summary style="cursor:pointer; font-weight:700; color:#0f172a;">👤 ${owner.owner_name}（${reqRows.length} 个需求${onlyPending ? '，仅未完成' : ''}）</summary>
      <div style="margin-top:10px;">
        <div>${majorBadges || '<span class="muted">暂无版本汇总</span>'}</div>
        ${reqTable}
      </div>
    </details>`;
  }).join('');

  area.innerHTML = `
    <div class="row" style="justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap;">
      <div class="row" style="gap:10px; flex-wrap:wrap;">
        <span class="badge">👥 人员：${s.owners || 0}</span>
        <span class="badge">📄 需求：${s.requirements || 0}</span>
        <span class="badge">🧪 用例已完成：${s.case_done || 0}</span>
        <span class="badge">⏳ 用例待完成：${s.case_pending || 0}</span>
        <span class="badge">✅ 测试已完成：${s.test_done || 0}</span>
        <span class="badge">🚧 测试待完成：${s.test_pending || 0}</span>
        <span class="badge" style="background:#fee2e2;color:#991b1b;">🔁 待复测：${s.retest_pending_total || 0}</span>
      </div>
      <label style="cursor:pointer; color:#334155; font-weight:600;">
        <input type="checkbox" id="assignOnlyPending" ${onlyPending ? 'checked' : ''} onchange="toggleAssignProgressPendingOnly()"> 仅看未完成需求
      </label>
    </div>
    <div class="card" style="margin-top:12px; border:1px dashed #e2e8f0;">
      <b>复测待办汇总（按大版本）</b>
      ${retestHtml}
    </div>
    <div style="margin-top:8px;">${ownerCards || '<div class="muted">暂无任务进行状态数据</div>'}</div>
  `;
}

export async function loadAssignBoard() {
  const majorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  const sid = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  syncLinkSourceMajorOptions();

  if (getUsers().length === 0 && typeof window.loadUsers === 'function') {
    await window.loadUsers();
  }

  let reqUrl = '/requirements/admin/list';
  const reqParams = [];
  if (majorId) reqParams.push('major_version_id=' + majorId);
  if (sid) reqParams.push('software_id=' + sid);
  if (reqParams.length) reqUrl += '?' + reqParams.join('&');

  state.assignReqs = await (await api(reqUrl)).json();
  window.assignReqs = state.assignReqs;

  const assignTable = document.getElementById('assignTable');
  if (!assignTable) return;
  const users = getUsers();
  assignTable.innerHTML = state.assignReqs.map((r) => `
    <tr>
      <td>${r.zentao_req_id} ${r.title}</td>
      <td>
        <select id='o_${r.id}'>
          <option value=''>未分配</option>
          ${users.map((u) => `<option value='${u.id}' ${u.id === r.owner_id ? 'selected' : ''}>${u.display_name || u.username}</option>`).join('')}
        </select>
      </td>
    </tr>`).join('');

  await loadAssignProgress();
  await loadLinkCandidates();
}

export async function loadAssignProgress() {
  const majorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  const sid = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  let url = '/requirements/admin/progress';
  const params = [];
  if (majorId) params.push('major_version_id=' + majorId);
  if (sid) params.push('software_id=' + sid);
  if (params.length) url += '?' + params.join('&');

  const data = await (await api(url)).json();
  state.assignProgressData = data;
  renderAssignProgress(data);
}

export function toggleAssignProgressPendingOnly() {
  renderAssignProgress(state.assignProgressData || { summary: {}, owners: [], retest_pending_by_major: [] });
}

export async function publishAssign() {
  if (!(window.confirmPush && window.confirmPush())) return;
  const majorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  if (!majorId) {
    window.showMessage && window.showMessage('“全部版本”仅用于查看；发布分配前请先选择一个具体大版本', 'error');
    return;
  }
  const assignments = state.assignReqs.map((r) => ({
    requirement_id: r.id,
    owner_id: Number(document.getElementById('o_' + r.id)?.value || 0) || null,
  }));
  await api('/requirements/assign-and-publish', {
    method: 'POST',
    headers: window.H,
    body: ({ major_version_id: majorId, assignments }),
  });
  window.showMessage && window.showMessage('分配发布成功');
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

    area.innerHTML = data.map((r) => `
      <label class="row" style="display:flex; justify-content:space-between; align-items:flex-start; border-bottom:1px dashed #e2e8f0; padding:8px 0;">
        <span style="display:flex; align-items:flex-start; gap:8px;">
          <input type="checkbox" class="link-req-check" value="${r.id}" ${r.already_linked ? 'disabled' : ''}>
          <span>
            <b>${r.zentao_req_id}</b> ${r.title || ''}
            <span class="muted" style="margin-left:8px;">负责人：${r.owner_name || '未分配'} ｜ 用例：${r.case_count || 0}</span>
          </span>
        </span>
        ${r.already_linked ? '<span class="badge" style="background:#ecfeff;color:#0369a1;">已在目标版本</span>' : ''}
      </label>
    `).join('');
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
    if (created > 0) {
      window.showMessage && window.showMessage(`关联成功：新增 ${created} 条，跳过 ${skipped} 条重复需求`, 'success');
    } else {
      window.showMessage && window.showMessage(`未新增需求：所选需求均已存在（跳过 ${skipped} 条）`, 'error');
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
  loadLinkCandidates,
  toggleLinkSelectAll,
  confirmLinkRequirements,
};
window.toggleAssignProgressPendingOnly = toggleAssignProgressPendingOnly;
