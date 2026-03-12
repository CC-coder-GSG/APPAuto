import { api } from '../api.js';
import { state } from '../state.js';
import { withPrefix } from '../utils.js';

function isMajorExpanded(majorId) {
  return state.dataTreeExpandedMajors[String(majorId)] === true;
}

function setMajorExpanded(majorId, expanded) {
  state.dataTreeExpandedMajors[String(majorId)] = !!expanded;
}

export function toggleMajorBody(majorId) {
  const body = document.getElementById('major_body_' + majorId);
  if (!body) return;
  const willExpand = body.classList.contains('hidden');
  body.classList.toggle('hidden');
  setMajorExpanded(majorId, willExpand);
}

export async function createVersion() {
  try {
    const version_no = (window.createVersionNo?.value || '').trim();
    const version_type = window.createVersionType?.value;
    const parentRaw = window.createVersionParent?.value;
    const parent_id = version_type === 'minor' ? Number(parentRaw) : null;
    const software_id = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
    if (!version_no) {
      window.showMessage && window.showMessage('请填写版本号', 'error');
      return;
    }
    if (version_type === 'minor' && !parentRaw) {
      window.showMessage && window.showMessage('minor 版本必须选择父大版本', 'error');
      return;
    }
    if (version_type === 'major' && !software_id) {
      window.showMessage && window.showMessage('请先在顶部切换或创建软件', 'error');
      return;
    }
    await api('/versions', { method: 'POST', headers: window.H, body: { version_no, version_type, parent_id, software_id: version_type === 'major' ? software_id : null } });
    window.createVersionNo.value = '';
    window.createVersionType.value = 'major';
    window.toggleCreateVersionParent && window.toggleCreateVersionParent();
    await window.loadVersions();
    await loadDataOverview();
    window.showMessage && window.showMessage('创建版本成功', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '创建版本失败', 'error');
  }
}

export async function createUser() {
  try {
    const username = (window.createUsername?.value || '').trim();
    const password = (window.createPassword?.value || '').trim();
    const role = window.createUserRole?.value;
    if (!username || !password) {
      window.showMessage && window.showMessage('请填写用户名和密码', 'error');
      return;
    }
    await api('/users', { method: 'POST', headers: window.H, body: { username, password, role } });
    window.createUsername.value = '';
    window.createPassword.value = '';
    window.createUserRole.value = 'user';
    await window.loadUsers();
    await loadDataOverview();
    window.showMessage && window.showMessage('创建用户成功', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '创建用户失败', 'error');
  }
}

export async function createReq() {
  try {
    const title = (window.createReqTitle?.value || '').trim();
    const reqNo = withPrefix('r#', window.createReqNo?.value || '');
    const major_version_id = Number(window.createReqMajorSelect?.value || 0);
    if (!major_version_id) {
      window.showMessage && window.showMessage('请先去【数据管理台】创建一个大版本！', 'error');
      return;
    }
    if (!title) {
      window.showMessage && window.showMessage('请填写需求标题', 'error');
      return;
    }
    if (!reqNo) {
      window.showMessage && window.showMessage('请填写需求编号数字部分', 'error');
      return;
    }
    await api('/requirements', { method: 'POST', headers: window.H, body: { zentao_req_id: reqNo, title, major_version_id } });
    window.createReqTitle.value = '';
    window.createReqNo.value = '';
    window.showMessage && window.showMessage('需求已创建成功，请联系管理员分配人员或刷新列表', 'success');
    await loadDataOverview();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '创建需求失败', 'error');
  }
}

export function handleReqFileSelect(e) {
  const file = e.target.files[0];
  if (!file) return;
  if (typeof XLSX === 'undefined') {
    window.showMessage && window.showMessage('Excel 解析组件未加载，请检查 /static/vendor/xlsx.full.min.js', 'error');
    e.target.value = '';
    return;
  }
  const reader = new FileReader();
  reader.onload = function(evt) {
    try {
      const data = new Uint8Array(evt.target.result);
      const workbook = XLSX.read(data, { type: 'array' });
      const worksheet = workbook.Sheets[workbook.SheetNames[0]];
      const jsonArr = XLSX.utils.sheet_to_json(worksheet, { header: 1 });
      parseExcelArray(jsonArr);
    } catch (err) {
      window.showMessage && window.showMessage('Excel 解析失败，请检查文件格式是否正确', 'error');
    }
    e.target.value = '';
  };
  reader.readAsArrayBuffer(file);
}

export function parseExcelArray(rows) {
  state.pendingImportReqs = [];
  let isDataStarted = false;
  let idIndex = -1;
  let titleIndex = -1;
  for (const row of rows) {
    if (!row || row.length === 0) continue;
    if (!isDataStarted) {
      idIndex = row.indexOf('编号');
      titleIndex = row.indexOf('需求名称');
      if (idIndex !== -1 && titleIndex !== -1) isDataStarted = true;
      continue;
    }
    if (idIndex !== -1 && titleIndex !== -1) {
      const rawId = String(row[idIndex] || '').trim();
      const rawTitle = String(row[titleIndex] || '').trim();
      const reqNo = rawId.replace(/\D/g, '');
      if (reqNo && rawTitle) {
        state.pendingImportReqs.push({ zentao_req_id: 'r#' + reqNo, title: rawTitle });
      }
    }
  }
  if (state.pendingImportReqs.length === 0) {
    window.showMessage && window.showMessage('未解析到数据，请确保表格中包含“编号”和“需求名称”列头！', 'error');
    return;
  }
  document.getElementById('importPreviewArea').classList.remove('hidden');
  document.getElementById('previewCount').innerText = state.pendingImportReqs.length;
  document.getElementById('previewList').innerHTML = state.pendingImportReqs.map((r) => `<div style="padding: 4px 0; border-bottom: 1px solid #f1f5f9;"><span class="badge" style="margin-right:8px">${r.zentao_req_id}</span> ${r.title}</div>`).join('');
  document.getElementById('importMajorSelect').innerHTML = document.getElementById('createReqMajorSelect').innerHTML;
}

export function cancelImport() {
  state.pendingImportReqs = [];
  document.getElementById('importPreviewArea').classList.add('hidden');
}

export async function confirmImport() {
  const majorId = Number(document.getElementById('importMajorSelect')?.value || 0);
  if (!majorId) {
    window.showMessage && window.showMessage('请选择要导入的大版本！', 'error');
    return;
  }
  try {
    const res = await api('/requirements/batch', {
      method: 'POST',
      headers: window.H,
      body: { major_version_id: majorId, items: state.pendingImportReqs },
    });
    const data = await res.json();
    window.showMessage && window.showMessage(`成功导入 ${data.count} 条新需求（已自动忽略系统中存在的重复项）！`, 'success');
    cancelImport();
    await loadDataOverview();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '导入失败', 'error');
  }
}

export async function loadDataOverview() {
  state.dataOverviewCache = await (await api('/admin/data-overview')).json();
  window.dataOverviewCache = state.dataOverviewCache;
  if (!state.requirementLinkLogsLoaded) {
    state.requirementLinkLogsCache = [];
  }
  renderDataOverview();
}

export async function loadRequirementLinkLogs() {
  const block = document.getElementById('dataLinkLogsBlock');
  const logsBody = document.getElementById('dataLinkLogs');
  if (logsBody) {
    logsBody.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;">正在加载日志...</td></tr>';
  }
  try {
    const res = await api('/admin/requirement-link-logs');
    const rows = await res.json();
    state.requirementLinkLogsCache = Array.isArray(rows) ? rows : [];
    state.requirementLinkLogsLoaded = true;
    if (block) block.open = true;
    renderDataOverview();
    window.showMessage && window.showMessage('关联日志加载完成', 'success');
  } catch (err) {
    if (logsBody) logsBody.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;color:#dc2626;">日志加载失败</td></tr>';
    window.showMessage && window.showMessage(err.message || '加载日志失败', 'error');
  }
}

export async function setDataView(key, checked) {
  if (key === 'users') state.dataViewState.showUsers = checked;
  if (key === 'versions') state.dataViewState.showVersions = checked;
  window.dataViewState = state.dataViewState;
  if (!state.dataOverviewCache) {
    await loadDataOverview();
    return;
  }
  renderDataOverview();
  if (key === 'versions' && checked) expandAllMajorBodies();
}

export async function setDataViewBatch(showAll) {
  state.dataViewState.showUsers = showAll;
  state.dataViewState.showVersions = showAll;
  window.dataViewState = state.dataViewState;
  const usersToggle = document.getElementById('toggleDataUsers');
  const versionsToggle = document.getElementById('toggleDataVersions');
  if (usersToggle) usersToggle.checked = showAll;
  if (versionsToggle) versionsToggle.checked = showAll;
  if (!state.dataOverviewCache) {
    await loadDataOverview();
    if (showAll) expandAllMajorBodies();
    return;
  }
  renderDataOverview();
  if (showAll) expandAllMajorBodies();
}

export function expandAllMajorBodies() {
  document.querySelectorAll('[id^="major_body_"]').forEach((el) => {
    el.classList.remove('hidden');
    const idText = String(el.id || '').replace('major_body_', '');
    if (idText) setMajorExpanded(idText, true);
  });
}

export function renderDataOverview() {
  const data = state.dataOverviewCache || { users: [], versions: [], requirements: [], bugs: [] };
  const activeMajorIds = new Set((window.versions || []).filter((v) => v.version_type === 'major').map((v) => Number(v.id)));
  const usersCard = document.getElementById('dataUsersCard');
  if (usersCard) usersCard.classList.toggle('hidden', !state.dataViewState.showUsers);
  const dataUsers = document.getElementById('dataUsers');
  if (state.dataViewState.showUsers && dataUsers) {
    dataUsers.innerHTML = (data.users || []).length === 0
      ? '<div class="muted" style="padding: 12px 8px;">暂无用户数据</div>'
      : (data.users || []).map((u) => {
        const safeUsername = String(u.username || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        const safeDisplayName = String(u.display_name || u.username || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        return `<div class="card" style="margin-bottom:12px; padding:14px 16px; border:1px solid #e2e8f0; box-shadow:none;">
          <div style="display:grid; grid-template-columns:minmax(220px, 1fr) auto; gap:12px; align-items:center;">
            <div style="display:flex; align-items:center; gap:10px; min-width:0;">
              <span style="font-weight:700; color:#0f172a; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${u.display_name || u.username}</span>
              <span class="badge" style="background:#f8fafc; color:#64748b;">账号: ${u.username}</span>
              <span class="badge" style="background:${u.role === 'admin' ? '#dbeafe' : '#f1f5f9'}; color:${u.role === 'admin' ? '#1d4ed8' : '#475569'}">${u.role === 'admin' ? '管理员' : '普通用户'}</span>
              <span class="badge" style="background:${u.is_team_member ? '#dcfce7' : '#fee2e2'}; color:${u.is_team_member ? '#166534' : '#991b1b'}">${u.is_team_member ? '组员' : '编外'}</span>
            </div>
            <div class="row" style="justify-content:flex-end; gap:8px; margin:0; flex-wrap:wrap;">
              <button class="secondary" onclick="renameUserDisplayName(${u.id}, '${safeDisplayName}', '${safeUsername}')">重命名</button>
              <button onclick="toggleRole(${u.id},'${u.role}')">设为${u.role === 'admin' ? '普通用户' : '管理员'}</button>
              <button class="secondary" onclick="toggleTeamMember(${u.id}, ${u.is_team_member ? false : true})">${u.is_team_member ? '设为编外人员' : '设为组员'}</button>
              <button class="secondary" onclick="resetUserPassword(${u.id}, '${safeUsername}')">重置密码</button>
              <button class="danger" onclick="removeUser(${u.id}, '${safeUsername}')">删除</button>
            </div>
          </div>
        </div>`;
      }).join('');
  } else if (dataUsers) {
    dataUsers.innerHTML = '';
  }

  const logsBody = document.getElementById('dataLinkLogs');
  if (logsBody) {
    if (!state.requirementLinkLogsLoaded) {
      logsBody.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;">点击“加载日志”后显示内容</td></tr>';
    } else {
      const logs = (state.requirementLinkLogsCache || [])
      .filter((x) => activeMajorIds.size === 0 || activeMajorIds.has(Number(x.target_major_id || 0)) || activeMajorIds.has(Number(x.source_major_id || 0)));
      logsBody.innerHTML = logs.length === 0
        ? '<tr><td colspan="6" class="muted" style="text-align:center;">暂无关联记录</td></tr>'
        : logs.map((l) => {
          const timeText = l.created_at ? new Date(l.created_at).toLocaleString() : '-';
          const srcReq = l.source_zentao_req_id ? `${l.source_zentao_req_id} ${l.source_title || ''}` : '-';
          const dstReq = l.target_zentao_req_id ? `${l.target_zentao_req_id} ${l.target_title || ''}` : '-';
          return `<tr>
            <td>${timeText}</td>
            <td>${l.actor_name || '未知'}</td>
            <td>${l.source_major_name || '未知'}</td>
            <td>${l.target_major_name || '未知'}</td>
            <td>${srcReq}</td>
            <td>${dstReq}</td>
          </tr>`;
        }).join('');
    }
  }

  const majors = (data.versions || []).filter((v) => v.version_type === 'major' && activeMajorIds.has(Number(v.id)));
  const minors = (data.versions || []).filter((v) => v.version_type === 'minor' && activeMajorIds.has(Number(v.parent_id)));
  const minorMap = {};
  minors.forEach((m) => { minorMap[m.id] = m.version_no; });
  let treeHtml = '';
  majors.forEach((major) => {
    const majorMinors = minors.filter((m) => m.parent_id === major.id);
    const reqs = (data.requirements || []).filter((r) => r.major_version_id === major.id && activeMajorIds.has(Number(r.major_version_id)));
    const majorOpen = isMajorExpanded(major.id);
    let minorHtml = majorMinors.map((m) => `<span class="badge" style="background:#e0f2fe;color:#0369a1;margin-right:8px;padding-right:2px;">🏷️ ${m.version_no} <button class="text-btn" title="编辑" onclick="editVersion(${m.id},'${m.version_no}','minor',${major.id})">✎</button><button class="text-btn" title="删除" onclick="removeVersion(${m.id})">×</button></span>`).join('');
    if (!minorHtml) minorHtml = '<span class="muted" style="font-size:13px;">暂无发包记录</span>';
    let reqHtml = reqs.map((req) => {
      const reqBugs = (data.bugs || []).filter((b) => b.requirement_id === req.id);
      const freeBugs = reqBugs.filter((b) => b.source_type === 'manual');
      const caseBugs = reqBugs.filter((b) => b.source_type === 'case');
      const casesListHtml = (req.case_ids || []).map((cId) => {
        const relatedBugs = caseBugs.filter((b) => b.source_ref === cId);
        let bHtml = relatedBugs.map((b) => `<div style="margin-left:24px; color:#475569; font-size:13px; margin-top:4px;">↳ 🐛 关联 Bug: <b>${b.bug_id}</b> <span style="color:#94a3b8">[发包: 🏷️ ${minorMap[b.found_minor_version_id] || '未知'}]</span> <button class="text-btn" onclick="editBug(${b.id},'${b.bug_id}')">✎</button><button class="text-btn" onclick="removeBug(${b.id})">×</button></div>`).join('');
        if (!bHtml) bHtml = '<div style="margin-left:24px; color:#10b981; font-size:13px; margin-top:4px;">↳ ✓ 完美通过，无关联Bug</div>';
        return `<div style="margin-top:12px;">🧪 <b>用例 [${cId}]</b> ${bHtml}</div>`;
      }).join('');
      const freeBugsHtml = freeBugs.map((b) => `<div style="margin-left:24px; color:#475569; font-size:13px; margin-top:4px;">↳ 🐛 自由 Bug: <b>${b.bug_id}</b> <span style="color:#94a3b8">[发包: 🏷️ ${minorMap[b.found_minor_version_id] || '未知'}]</span> <button class="text-btn" onclick="editBug(${b.id},'${b.bug_id}')">✎</button><button class="text-btn" onclick="removeBug(${b.id})">×</button></div>`).join('');
      return `
      <div style="border:1px solid #e2e8f0; border-radius:6px; margin-bottom:12px; background:#fff;">
        <div style="padding:10px 12px; cursor:pointer; background:#f8fafc; border-bottom:1px solid #e2e8f0; display:flex; justify-content:space-between; align-items:center;" onclick="document.getElementById('req_body_${req.id}').classList.toggle('hidden')">
          <span style="font-size:14px;">📄 <b>${req.zentao_req_id}</b> ${req.title}</span>
          <span>
            <button class="secondary" style="padding:4px 8px; font-size:12px;" onclick="event.stopPropagation(); editReq(${req.id},'${req.zentao_req_id}','${req.title}',${major.id})">编辑</button>
            <button class="danger" style="padding:4px 8px; font-size:12px;" onclick="event.stopPropagation(); removeReq(${req.id})">删除</button>
          </span>
        </div>
        <div id="req_body_${req.id}" class="hidden" style="padding:12px; background:#fff;">
          <div style="margin-bottom:16px;">
            <div style="font-weight:bold; color:#334155; border-bottom:1px solid #f1f5f9; padding-bottom:4px;">[区块 A：测试用例与关联 Bug]</div>
            ${casesListHtml || '<div class="muted" style="margin-left:24px; margin-top:8px;">暂无用例</div>'}
          </div>
          <div>
            <div style="font-weight:bold; color:#334155; border-bottom:1px solid #f1f5f9; padding-bottom:4px;">[区块 B：自由 Bug 池]</div>
            ${freeBugsHtml || '<div class="muted" style="margin-left:24px; margin-top:8px;">暂无自由Bug</div>'}
          </div>
        </div>
      </div>`;
    }).join('');
    if (!reqHtml) reqHtml = '<div class="muted" style="font-size:13px;">暂无下辖需求</div>';
    treeHtml += `
    <div style="border:2px solid #cbd5e1; border-radius:8px; margin-bottom:16px; background:#fff; overflow:hidden;">
      <div style="padding:12px 16px; background:#f1f5f9; border-bottom:1px solid #cbd5e1; cursor:pointer; display:flex; justify-content:space-between; align-items:center;" onclick="toggleMajorBody(${major.id})">
        <span style="font-size:16px; font-weight:bold; color:#0f172a;">📦 大版本：${major.version_no}</span>
        <span>
          <button class="secondary" onclick="event.stopPropagation(); editVersion(${major.id},'${major.version_no}','major',null)">编辑版本</button>
          <button class="danger" onclick="event.stopPropagation(); removeVersion(${major.id})">删除整体</button>
        </span>
      </div>
      <div id="major_body_${major.id}" class="${majorOpen ? '' : 'hidden'}" style="padding:16px; background:#f8fafc;">
        ${state.dataViewState.showVersions ? `<div style="margin-bottom:20px; padding-bottom:12px; border-bottom:1px dashed #cbd5e1;">
          <div style="font-weight:bold; margin-bottom:8px; color:#334155;">【版本发包履历】</div>
          <div>${minorHtml}</div>
        </div>` : '<div class="muted" style="margin-bottom:20px; padding-bottom:12px; border-bottom:1px dashed #cbd5e1;">版本信息已隐藏（可在上方视图控制中开启）</div>'}
        <div>
          <div style="font-weight:bold; margin-bottom:12px; color:#334155;">【下辖需求清单】</div>
          ${reqHtml}
        </div>
      </div>
    </div>`;
  });
  document.getElementById('dataTreeArea').innerHTML = treeHtml || '<div class="muted" style="padding: 20px; text-align: center;">系统中暂无数据，请先创建大版本</div>';
}


export async function toggleRole(userId, currentRole) {
  const nextRole = currentRole === 'admin' ? 'user' : 'admin';
  if (!confirm(`确定将该用户设为${nextRole === 'admin' ? '管理员' : '普通用户'}吗？`)) return;
  try {
    await api(`/users/${userId}/role`, { method: 'PUT', headers: window.H, body: { role: nextRole } });
    window.showMessage && window.showMessage('角色切换成功', 'success');
    await loadDataOverview();
    await window.loadUsers();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '角色切换失败', 'error');
  }
}

export async function createSoftware() {
  try {
    const name = (window.createSoftwareName?.value || '').trim();
    if (!name) {
      window.showMessage && window.showMessage('请输入软件名称', 'error');
      return;
    }
    await api('/softwares', { method: 'POST', headers: window.H, body: { name } });
    window.createSoftwareName.value = '';
    if (typeof window.loadSoftwares === 'function') {
      await window.loadSoftwares();
      await window.onSoftwareChange();
    }
    window.showMessage && window.showMessage('软件创建成功', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '创建软件失败', 'error');
  }
}

export async function toggleTeamMember(userId, targetStatus) {
  const text = targetStatus ? '组员' : '编外人员';
  if (!confirm(`确定将该用户设为${text}吗？`)) return;
  try {
    await api(`/users/${userId}/team-status`, { method: 'PUT', headers: window.H, body: { is_team_member: targetStatus } });
    window.showMessage && window.showMessage(`已更新为${text}`, 'success');
    await loadDataOverview();
    await window.loadUsers();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '组员状态更新失败', 'error');
  }
}

export async function removeUser(userId, username) {
  if (!confirm(`确认删除用户【${username}】吗？此操作不可恢复。`)) return;
  try {
    await api(`/users/${userId}`, { method: 'DELETE' });
    window.showMessage && window.showMessage('用户删除成功', 'success');
    await loadDataOverview();
    await window.loadUsers();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '删除用户失败', 'error');
  }
}

export async function renameUserDisplayName(userId, currentDisplayName, username) {
  const next = prompt(`请输入用户【${username}】的新显示名称：`, currentDisplayName || username);
  if (!next) return;
  const clean = String(next).trim();
  if (!clean) return;
  try {
    await api(`/users/${userId}/display-name`, { method: 'PUT', headers: window.H, body: { display_name: clean } });
    window.showMessage && window.showMessage('用户显示名称已更新', 'success');
    await loadDataOverview();
    await window.loadUsers();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '重命名失败', 'error');
  }
}

export async function removeVersion(id) {
  if (!confirm('高危操作：删除版本将级联删除所有下挂需求、用例和 Bug，确定继续吗？')) return;
  await api('/versions/' + id, { method: 'DELETE' });
  window.showMessage && window.showMessage('版本已删除');
  await window.loadVersions();
  await loadDataOverview();
}

export async function removeReq(id) {
  if (!confirm('确定删除该需求及其用例、执行记录吗？')) return;
  await api('/requirements/' + id, { method: 'DELETE' });
  window.showMessage && window.showMessage('需求已删除');
  await loadDataOverview();
}

export async function removeBug(id) {
  if (!confirm('确定删除该 Bug 吗？')) return;
  await api('/bugs/' + id, { method: 'DELETE' });
  window.showMessage && window.showMessage('Bug 已删除');
  await loadDataOverview();
}

export async function editVersion(id, no, type, parent) {
  const newNo = prompt('版本号', no);
  if (!newNo) return;
  const software_id = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  await api('/versions/' + id, {
    method: 'PUT',
    headers: window.H,
    body: { version_no: newNo, version_type: type, parent_id: parent, software_id: type === 'major' ? software_id : null },
  });
  window.showMessage && window.showMessage('版本已更新');
  await window.loadVersions();
  await loadDataOverview();
}

export async function editReq(id, z, title, major) {
  const zNum = prompt('需求号数字部分', z.replace('r#', ''));
  if (!zNum) return;
  const t = prompt('需求标题', title);
  if (!t) return;
  await api('/requirements/' + id, { method: 'PUT', headers: window.H, body: { zentao_req_id: withPrefix('r#', zNum), title: t, major_version_id: major } });
  window.showMessage && window.showMessage('需求已更新');
  await loadDataOverview();
}

export async function editBug(id, b) {
  const num = prompt('Bug数字部分', b.replace('b#', ''));
  if (!num) return;
  await api('/bugs/' + id + '?new_bug_id=' + encodeURIComponent(withPrefix('b#', num)), { method: 'PUT' });
  window.showMessage && window.showMessage('Bug已更新');
  await loadDataOverview();
}

window.OmniQADataTab = {
  createSoftware,
  createVersion,
  createUser,
  createReq,
  handleReqFileSelect,
  parseExcelArray,
  cancelImport,
  confirmImport,
  loadDataOverview,
  loadRequirementLinkLogs,
  setDataView,
  setDataViewBatch,
  expandAllMajorBodies,
  renderDataOverview,
  toggleRole,
  toggleTeamMember,
  removeUser,
  renameUserDisplayName,
  removeVersion,
  removeReq,
  removeBug,
  editVersion,
  editReq,
  editBug,
  toggleMajorBody,
};

window.toggleMajorBody = toggleMajorBody;
