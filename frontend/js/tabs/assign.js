import { api } from '../api.js';
import { state } from '../state.js';

function getUsers() {
  return window.users || state.users || [];
}

function isPendingReq(req) {
  return !(req.case_completed && req.test_completed);
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

window.OmniQAAssignTab = { loadAssignBoard, loadAssignProgress, toggleAssignProgressPendingOnly, publishAssign };
window.toggleAssignProgressPendingOnly = toggleAssignProgressPendingOnly;

