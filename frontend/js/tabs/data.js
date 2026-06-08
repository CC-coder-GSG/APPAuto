import { api } from '../api.js';
import { state } from '../state.js';
import { withPrefix, renderBugLink, renderCaseLink } from '../utils.js';
const TAB_PERMISSION_OPTIONS = [
  { key: 'assign', label: '任务分配台' },
  { key: 'mine', label: '我的工作台' },
  { key: 'task-board', label: '任务看板' },
  { key: 'feedback', label: '反馈记录与处理' },
  { key: 'retest', label: '复测工作台' },
  { key: 'overall-test', label: '整体测试' },
  { key: 'field-test', label: '外业测试' },
  { key: 'build-records', label: '构建记录' },
  { key: 'testcase-center', label: '用例中心' },
  { key: 'zentao-sync', label: '禅道同步中心' },
  { key: 'report', label: '报表中心' },
  { key: 'activity', label: '活动中心' },
  { key: 'data', label: '数据管理台' },
  { key: 'dispatch', label: 'BUG特派' },
  { key: 'zentao-ai', label: '禅道AI用例生成' },
  { key: 'cad-test', label: 'CAD测试统计' },
];

function bugZentaoMeta(b) {
  const parts = [];
  const statusColors = { active: '#dc2626', resolved: '#d97706', closed: '#16a34a', rejected: '#6b7280' };
  if (b.zentao_live_status) {
    const color = statusColors[b.zentao_live_status.toLowerCase()] || '#475569';
    parts.push(`<span class="badge" style="background:#f8fafc; color:${color}; border:1px solid ${color}40;">${b.zentao_live_status}</span>`);
  }
  if (b.zentao_deleted) {
    parts.push(`<span class="badge" style="background:#fee2e2; color:#b91c1c;">已删除</span>`);
  }
  if (b.zentao_closed_by_name) {
    parts.push(`<span class="badge" style="background:#ecfdf5; color:#047857;">关闭: ${b.zentao_closed_by_name}</span>`);
  }
  if (b.last_zentao_synced_at) {
    parts.push(`<span class="badge" style="background:#f8fafc; color:#94a3b8; font-size:11px;">同步 ${b.last_zentao_synced_at}</span>`);
  }
  return parts.length ? ` &nbsp;${parts.join('')}` : '';
}

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
  const isAdmin = String(window.currentUser?.role || '') === 'admin';
  const adminControls = document.getElementById('dataAdminControls');
  const readOnlyHint = document.getElementById('dataReadonlyHint');
  const dangerHint = document.getElementById('dataDangerHint');
  if (adminControls) adminControls.classList.toggle('hidden', !isAdmin);
  if (readOnlyHint) readOnlyHint.classList.toggle('hidden', isAdmin);
  if (dangerHint) dangerHint.classList.toggle('hidden', !isAdmin);
  const usersCard = document.getElementById('dataUsersCard');
  if (usersCard) usersCard.classList.toggle('hidden', !state.dataViewState.showUsers);
  const dataUsers = document.getElementById('dataUsers');
  if (state.dataViewState.showUsers && dataUsers) {
    dataUsers.innerHTML = (data.users || []).length === 0
      ? '<div class="muted" style="padding: 12px 8px;">暂无用户数据</div>'
      : (data.users || []).map((u) => {
        const safeUsername = String(u.username || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        const safeDisplayName = String(u.display_name || u.username || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        const safeAllowedTabs = encodeURIComponent(JSON.stringify(u.allowed_tabs || []));
        return `<div class="card" style="margin-bottom:12px; padding:14px 16px; border:1px solid #e2e8f0; box-shadow:none;">
          <div style="display:grid; grid-template-columns:minmax(220px, 1fr) auto; gap:12px; align-items:center;">
            <div style="display:flex; align-items:center; gap:10px; min-width:0;">
              <span style="font-weight:700; color:#0f172a; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${u.display_name || u.username}</span>
              <span class="badge" style="background:#f8fafc; color:#64748b;">账号: ${u.username}</span>
              <span class="badge" style="background:${u.role === 'admin' ? '#dbeafe' : '#f1f5f9'}; color:${u.role === 'admin' ? '#1d4ed8' : '#475569'}">${u.role === 'admin' ? '管理员' : '普通用户'}</span>
              <span class="badge" style="background:${u.is_team_member ? '#dcfce7' : '#fee2e2'}; color:${u.is_team_member ? '#166534' : '#991b1b'}">${u.is_team_member ? '组员' : '编外'}</span>
              ${Array.isArray(u.allowed_tabs) && u.allowed_tabs.length ? `<span class="badge" style="background:#eef2ff; color:#4338ca;">页面权限 ${u.allowed_tabs.length} 项</span>` : ''}
            </div>
            <div class="row" style="justify-content:flex-end; gap:8px; margin:0; flex-wrap:wrap; ${isAdmin ? '' : 'display:none;'}">
              <button class="secondary" onclick="openUserTabPermissionModal(${u.id}, '${safeUsername}', decodeURIComponent('${safeAllowedTabs}'))">页面权限</button>
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
    let minorHtml = majorMinors.map((m) => {
      const actionHtml = isAdmin
        ? ` <a href="javascript:void(0)" title="编辑" onclick="editVersion(${m.id},'${m.version_no}','minor',${major.id})" style="color:#3b82f6;margin-left:4px;text-decoration:none;">✎</a><a href="javascript:void(0)" title="删除" onclick="removeVersion(${m.id})" style="color:#ef4444;margin-left:2px;text-decoration:none;">×</a>`
        : '';
      return `<span class="badge" style="background:#e0f2fe;color:#0369a1;margin-right:8px;">🏷️ ${m.version_no}${actionHtml}</span>`;
    }).join('');
    if (!minorHtml) minorHtml = '<span class="muted" style="font-size:13px;">暂无发包记录</span>';
    let reqHtml = reqs.map((req) => {
      const safeReqNo = String(req.zentao_req_id || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      const safeReqTitle = String(req.title || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      const reqBugs = (data.bugs || []).filter((b) => b.requirement_id === req.id);
      const freeBugs = reqBugs.filter((b) => b.source_type === 'manual');
      const caseBugs = reqBugs.filter((b) => b.source_type === 'case');
      const notesSummary = (req.test_notes || '').trim();
      const notesMeta = req.test_notes_updated_at
        ? `最后更新：${req.test_notes_updated_by_name || '未知'} ${new Date(req.test_notes_updated_at).toLocaleString()}`
        : '';
      const storyBadge = req.zentao_story_id
        ? `<span class="badge" style="background:#ecfeff; color:#0f766e; margin-left:8px;">Story ${req.zentao_story_id}</span>`
        : `<span class="badge" style="background:#f8fafc; color:#94a3b8; margin-left:8px;">未绑定 Story</span>`;
      const casesListHtml = (req.case_ids || []).map((cId) => {
        const relatedBugs = caseBugs.filter((b) => b.source_ref === cId);
        let bHtml = relatedBugs.map((b) => {
          const actions = isAdmin
            ? `<button class="text-btn" onclick="editBug(${b.id},'${b.bug_id}')">✎</button><button class="text-btn" onclick="removeBug(${b.id})">×</button>`
            : '';
          return `<div style="margin-left:24px; color:#475569; font-size:13px; margin-top:4px;">↳ 🐛 关联 Bug: ${renderBugLink(b)}${bugZentaoMeta(b)} <span style="color:#94a3b8">[发包: 🏷️ ${minorMap[b.found_minor_version_id] || '未知'}]</span> <button class="text-btn" onclick="openAuditTimelineModal('bug', ${b.id}, 'Bug 时间线')">🕓</button>${actions}</div>`;
        }).join('');
        if (!bHtml) bHtml = '<div style="margin-left:24px; color:#10b981; font-size:13px; margin-top:4px;">↳ ✓ 完美通过，无关联Bug</div>';
        const caseObj = (req.test_cases || []).find((x) => String(x.zentao_case_id) === String(cId));
        return `<div style="margin-top:12px;">🧪 <b>用例 [${caseObj ? renderCaseLink(caseObj) : cId}]</b> ${bHtml}</div>`;
      }).join('');
      const freeBugsHtml = freeBugs.map((b) => {
        const actions = isAdmin
          ? `<button class="text-btn" onclick="editBug(${b.id},'${b.bug_id}')">✎</button><button class="text-btn" onclick="removeBug(${b.id})">×</button>`
          : '';
        return `<div style="margin-left:24px; color:#475569; font-size:13px; margin-top:4px;">↳ 🐛 自由 Bug: ${renderBugLink(b)}${bugZentaoMeta(b)} <span style="color:#94a3b8">[发包: 🏷️ ${minorMap[b.found_minor_version_id] || '未知'}]</span> <button class="text-btn" onclick="openAuditTimelineModal('bug', ${b.id}, 'Bug 时间线')">🕓</button>${actions}</div>`;
      }).join('');
      const reqActionButtons = isAdmin
        ? `
            <button class="secondary" style="padding:4px 8px; font-size:12px;" onclick="event.stopPropagation(); openRequirementStoryBindingModal(${req.id}, '${safeReqNo}', '${safeReqTitle}', ${req.zentao_story_id || 'null'})">修正自动归集</button>
            <button class="secondary" style="padding:4px 8px; font-size:12px;" onclick="event.stopPropagation(); editReq(${req.id},'${req.zentao_req_id}','${req.title}',${major.id})">编辑</button>
            <button class="danger" style="padding:4px 8px; font-size:12px;" onclick="event.stopPropagation(); removeReq(${req.id})">删除</button>
          `
        : '';
      return `
      <div style="border:1px solid #e2e8f0; border-radius:6px; margin-bottom:12px; background:#fff;">
        <div style="padding:10px 12px; cursor:pointer; background:#f8fafc; border-bottom:1px solid #e2e8f0; display:flex; justify-content:space-between; align-items:center;" onclick="document.getElementById('req_body_${req.id}').classList.toggle('hidden')">
          <span style="font-size:14px;">📄 <b>${req.zentao_req_id}</b> ${req.title}${storyBadge}</span>
          <span>
            <button class="secondary" style="padding:4px 8px; font-size:12px;" onclick="event.stopPropagation(); openAuditTimelineModal('requirement', ${req.id}, '需求时间线')">时间线</button>
            ${reqActionButtons}
          </span>
        </div>
        <div id="req_body_${req.id}" class="hidden" style="padding:12px; background:#fff;">
          <div style="margin-bottom:12px; padding:10px; border:1px dashed #cbd5e1; border-radius:6px; background:#f8fafc;">
            <div style="font-weight:bold; color:#334155; margin-bottom:6px;">[测试要点]</div>
            <div style="color:${notesSummary ? '#334155' : '#94a3b8'}; font-size:13px; line-height:1.5;">
              ${notesSummary ? (notesSummary.length > 60 ? `${notesSummary.slice(0, 60)}...` : notesSummary) : '无'}
            </div>
            ${notesMeta ? `<div class="muted" style="margin-top:6px; font-size:12px;">${notesMeta}</div>` : ''}
          </div>
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
    const majorActionButtons = isAdmin
      ? `
          <button class="secondary" onclick="event.stopPropagation(); editVersion(${major.id},'${major.version_no}','major',null)">编辑版本</button>
          <button class="danger" onclick="event.stopPropagation(); removeVersion(${major.id})">删除整体</button>
        `
      : '';
    treeHtml += `
    <div style="border:2px solid #cbd5e1; border-radius:8px; margin-bottom:16px; background:#fff; overflow:hidden;">
      <div style="padding:12px 16px; background:#f1f5f9; border-bottom:1px solid #cbd5e1; cursor:pointer; display:flex; justify-content:space-between; align-items:center;" onclick="toggleMajorBody(${major.id})">
        <span style="font-size:16px; font-weight:bold; color:#0f172a;">📦 大版本：${major.version_no}</span>
        <span>${majorActionButtons}</span>
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

function ensureUserTabPermissionModal() {
  let modal = document.getElementById('userTabPermissionModal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'userTabPermissionModal';
  modal.className = 'hidden';
  modal.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,.42); z-index:10000; display:none; align-items:center; justify-content:center;';
  modal.innerHTML = `
    <div style="width:min(640px, 94vw); max-height:90vh; overflow:auto; background:#fff; border-radius:14px; box-shadow:0 16px 40px rgba(0,0,0,.22); padding:22px;">
      <div class="row" style="justify-content:space-between; align-items:center; margin:0 0 14px;">
        <h3 id="userTabPermissionTitle" style="margin:0; color:#0f172a;">页面权限</h3>
        <button class="secondary" onclick="closeUserTabPermissionModal()">关闭</button>
      </div>
      <div id="userTabPermissionOptions" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:10px;"></div>
      <div class="row" style="justify-content:flex-end; margin:18px 0 0;">
        <button class="secondary" onclick="closeUserTabPermissionModal()">取消</button>
        <button onclick="saveUserTabPermissions()">保存权限</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  return modal;
}

export function openUserTabPermissionModal(userId, username, allowedTabs = []) {
  const modal = ensureUserTabPermissionModal();
  modal.dataset.userId = String(userId);
  const title = document.getElementById('userTabPermissionTitle');
  if (title) title.innerText = `页面权限：${username}`;
  const options = document.getElementById('userTabPermissionOptions');
  let normalizedTabs = allowedTabs;
  if (typeof normalizedTabs === 'string') {
    try {
      normalizedTabs = JSON.parse(normalizedTabs);
    } catch {
      normalizedTabs = [];
    }
  }
  const current = new Set(Array.isArray(normalizedTabs) ? normalizedTabs : []);
  if (options) {
    options.innerHTML = TAB_PERMISSION_OPTIONS.map((item) => `
      <label style="display:flex; align-items:center; gap:8px; padding:8px 10px; border:1px solid #e2e8f0; border-radius:10px; background:#f8fafc;">
        <input type="checkbox" value="${item.key}" ${current.has(item.key) ? 'checked' : ''}>
        <span>${item.label}</span>
      </label>`).join('');
  }
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
}

export function closeUserTabPermissionModal() {
  const modal = document.getElementById('userTabPermissionModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.style.display = 'none';
}

function ensureRequirementStoryBindingModal() {
  let modal = document.getElementById('requirementStoryBindingModal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'requirementStoryBindingModal';
  modal.className = 'hidden';
  modal.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,.42); z-index:10000; display:none; align-items:center; justify-content:center;';
  modal.innerHTML = `
    <div style="width:min(720px, 94vw); max-height:90vh; overflow:auto; background:#fff; border-radius:14px; box-shadow:0 16px 40px rgba(0,0,0,.22); padding:22px;">
      <div class="row" style="justify-content:space-between; align-items:center; margin:0 0 12px;">
        <h3 id="requirementStoryBindingTitle" style="margin:0; color:#0f172a;">修正自动归集</h3>
        <button class="secondary" onclick="closeRequirementStoryBindingModal()">关闭</button>
      </div>
      <div class="muted" style="margin-bottom:12px;">修正的是需求的禅道 story 绑定。保存后，工作台中的自动归集用例和 story 级 Bug 都会随之变化。</div>
      <div class="row" style="align-items:flex-end; gap:10px; flex-wrap:wrap;">
        <div>
          <label style="font-size:12px; color:#64748b; display:block; margin-bottom:4px;">禅道 Story ID</label>
          <input id="requirementStoryBindingInput" inputmode="numeric" placeholder="留空表示清除绑定" oninput="digitsOnly(this)" style="min-width:220px;">
        </div>
        <button class="secondary" onclick="previewRequirementStoryBinding()">预览自动归集结果</button>
      </div>
      <div id="requirementStoryBindingPreview" style="margin-top:14px; padding:12px; border:1px solid #e2e8f0; border-radius:10px; background:#f8fafc; min-height:120px;"></div>
      <div class="row" style="justify-content:flex-end; gap:8px; margin:18px 0 0;">
        <button class="secondary" onclick="clearRequirementStoryBinding()">清除绑定</button>
        <button class="secondary" onclick="closeRequirementStoryBindingModal()">取消</button>
        <button onclick="saveRequirementStoryBinding()">保存绑定</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  return modal;
}

export function openRequirementStoryBindingModal(requirementId, reqNo, title, currentStoryId) {
  const modal = ensureRequirementStoryBindingModal();
  modal.dataset.requirementId = String(requirementId);
  const titleEl = document.getElementById('requirementStoryBindingTitle');
  const input = document.getElementById('requirementStoryBindingInput');
  const preview = document.getElementById('requirementStoryBindingPreview');
  if (titleEl) titleEl.innerText = `修正自动归集：${reqNo} ${title}`;
  if (input) input.value = currentStoryId ? String(currentStoryId) : '';
  if (preview) {
    preview.innerHTML = currentStoryId
      ? `<div class="muted">当前已绑定 Story ${currentStoryId}。点击“预览自动归集结果”可查看会归集到多少用例和 Bug。</div>`
      : '<div class="muted">当前未绑定 Story。输入 Story ID 后可预览自动归集结果。</div>';
  }
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
}

export function closeRequirementStoryBindingModal() {
  const modal = document.getElementById('requirementStoryBindingModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.style.display = 'none';
}

function renderRequirementStoryBindingPreview(data) {
  const preview = document.getElementById('requirementStoryBindingPreview');
  if (!preview) return;
  const storyId = data?.preview_story_id;
  if (!storyId) {
    preview.innerHTML = '<div class="muted">当前预览为空绑定。保存后该需求将不再按禅道 story 自动归集用例和 story 级 Bug。</div>';
    return;
  }
  const testcaseLines = (data.sample_testcases || []).map((row) => `<div>• ${row.zentao_case_id || '-'} ${row.title || ''}</div>`).join('');
  const bugLines = (data.sample_bugs || []).map((row) => `<div>• ${row.bug_id || '-'} ${row.title || ''}</div>`).join('');
  const duplicateLines = (data.duplicate_requirements || []).map((row) => `<div>• ${row.zentao_req_id || '-'} ${row.title || ''} <span class="muted">(${row.major_version_name || '-'})</span></div>`).join('');
  preview.innerHTML = `
    <div class="row" style="gap:12px; flex-wrap:wrap; margin-bottom:10px;">
      <span class="badge" style="background:#eef6ff; color:#1d4ed8;">Story ${storyId}</span>
      <span class="badge" style="background:#ecfeff; color:#0f766e;">自动归集用例 ${Number(data?.testcase_total || 0)}</span>
      <span class="badge" style="background:#fff7ed; color:#c2410c;">自动归集Bug ${Number(data?.bug_total || 0)}</span>
    </div>
    <div style="margin-bottom:10px;">
      <div style="font-weight:600; color:#334155; margin-bottom:4px;">用例样本</div>
      ${testcaseLines || '<div class="muted">没有匹配到 testcase 镜像</div>'}
    </div>
    <div style="margin-bottom:10px;">
      <div style="font-weight:600; color:#334155; margin-bottom:4px;">Bug 样本</div>
      ${bugLines || '<div class="muted">当前大版本下没有匹配到 story 级 Bug</div>'}
    </div>
    <div>
      <div style="font-weight:600; color:#334155; margin-bottom:4px;">其他已绑定到同一 Story 的需求</div>
      ${duplicateLines || '<div class="muted">没有其他需求绑定到这个 Story</div>'}
    </div>`;
}

export async function previewRequirementStoryBinding() {
  const modal = document.getElementById('requirementStoryBindingModal');
  const input = document.getElementById('requirementStoryBindingInput');
  if (!modal || !input) return;
  const requirementId = Number(modal.dataset.requirementId || 0);
  if (!requirementId) return;
  const storyIdText = String(input.value || '').trim();
  const url = storyIdText
    ? `/requirements/${requirementId}/story-binding-preview?story_id=${encodeURIComponent(storyIdText)}`
    : `/requirements/${requirementId}/story-binding-preview`;
  try {
    const data = await (await api(url)).json();
    renderRequirementStoryBindingPreview(data);
  } catch (err) {
    const preview = document.getElementById('requirementStoryBindingPreview');
    if (preview) preview.innerHTML = `<div style="color:#dc2626;">预览失败：${err.message || '未知错误'}</div>`;
    window.showMessage && window.showMessage(err.message || '预览自动归集结果失败', 'error');
  }
}

export function clearRequirementStoryBinding() {
  const input = document.getElementById('requirementStoryBindingInput');
  if (input) input.value = '';
  const preview = document.getElementById('requirementStoryBindingPreview');
  if (preview) {
    preview.innerHTML = '<div class="muted">当前将保存为空绑定。保存后该需求不再自动归集 story 级 testcase 和 Bug。</div>';
  }
}

export async function saveRequirementStoryBinding() {
  const modal = document.getElementById('requirementStoryBindingModal');
  const input = document.getElementById('requirementStoryBindingInput');
  if (!modal || !input) return;
  const requirementId = Number(modal.dataset.requirementId || 0);
  if (!requirementId) return;
  const storyIdText = String(input.value || '').trim();
  const payload = { zentao_story_id: storyIdText ? Number(storyIdText) : null };
  try {
    await api(`/requirements/${requirementId}/story-binding`, {
      method: 'PUT',
      headers: window.H,
      body: payload,
    });
    window.showMessage && window.showMessage('Story 绑定已更新', 'success');
    closeRequirementStoryBindingModal();
    await loadDataOverview();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || 'Story 绑定更新失败', 'error');
  }
}

export async function saveUserTabPermissions() {
  const modal = document.getElementById('userTabPermissionModal');
  if (!modal) return;
  const userId = Number(modal.dataset.userId || 0);
  if (!userId) return;
  const allowedTabs = Array.from(modal.querySelectorAll('input[type="checkbox"]:checked')).map((el) => el.value);
  try {
    await api(`/users/${userId}/tab-permissions`, {
      method: 'PUT',
      headers: window.H,
      body: { allowed_tabs: allowedTabs },
    });
    window.showMessage && window.showMessage('页面权限已更新', 'success');
    closeUserTabPermissionModal();
    await loadDataOverview();
    if (typeof window.loadUsers === 'function') await window.loadUsers();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '页面权限更新失败', 'error');
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

export async function loadZtProjects() {
  const sel = document.getElementById('ztProjectSelect');
  if (!sel) return;
  sel.innerHTML = '<option value="">-- 加载中... --</option>';
  try {
    const res = await api('/zentao/projects');
    const projects = await res.json();
    if (!Array.isArray(projects) || projects.length === 0) {
      sel.innerHTML = '<option value="">-- 暂无可用项目（请先绑定禅道账号）--</option>';
      return;
    }
    sel.innerHTML = '<option value="">请选择禅道项目</option>' + projects.map((p) => `<option value="${p.id}">${p.name}</option>`).join('');
  } catch (err) {
    sel.innerHTML = '<option value="">-- 加载失败 --</option>';
    window.showMessage && window.showMessage(err.message || '加载禅道项目失败', 'error');
  }
}

export async function syncZtVersions() {
  const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  if (!softwareId) {
    window.showMessage && window.showMessage('请先在顶部选择软件产品', 'error');
    return;
  }
  const projectId = Number(document.getElementById('ztProjectSelect')?.value || 0);
  if (!projectId) {
    window.showMessage && window.showMessage('请选择禅道项目', 'error');
    return;
  }
  const syncMinor = document.getElementById('ztSyncMinorSelect')?.value !== '0';
  const btn = document.getElementById('ztSyncVersionsBtn');
  const resultEl = document.getElementById('ztSyncResult');
  if (btn) { btn.disabled = true; btn.textContent = '同步中...'; }
  if (resultEl) { resultEl.style.display = 'none'; resultEl.textContent = ''; }
  try {
    const res = await api('/zentao/sync-versions', {
      method: 'POST',
      headers: window.H,
      body: { software_id: softwareId, zentao_project_id: projectId, sync_minor: syncMinor },
    });
    const data = await res.json();
    if (resultEl) {
      resultEl.style.display = 'block';
      resultEl.innerHTML = `<span style="color:#16a34a; font-weight:bold;">✅ 同步完成</span>　${data.summary || ''}`;
    }
    window.showMessage && window.showMessage('版本同步完成：' + (data.summary || ''), 'success');
    await window.loadVersions();
    await loadDataOverview();
  } catch (err) {
    if (resultEl) {
      resultEl.style.display = 'block';
      resultEl.innerHTML = `<span style="color:#dc2626;">❌ 同步失败：${err.message || '未知错误'}</span>`;
    }
    window.showMessage && window.showMessage(err.message || '同步失败', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '开始同步'; }
  }
}

function renderZentaoBackgroundSyncResult(data) {
  const resultEl = document.getElementById('ztBackgroundSyncResult');
  const detailEl = document.getElementById('ztBackgroundSyncDetail');
  if (!resultEl || !detailEl) return;

  const total = Number(data?.total || 0);
  const success = Number(data?.success || 0);
  const failed = Number(data?.failed || 0);
  const modeText = data?.mode === 'nightly_full' ? '夜间全量对账' : '最近同步';
  resultEl.style.display = 'block';
  resultEl.innerHTML = failed > 0
    ? `<span style="color:#d97706; font-weight:bold;">⚠ ${modeText}已完成</span>　共 ${total} 个软件，成功 ${success}，失败 ${failed}`
    : `<span style="color:#16a34a; font-weight:bold;">✅ ${modeText}已完成</span>　共 ${total} 个软件，成功 ${success}，失败 ${failed}`;

  const items = Array.isArray(data?.items) ? data.items : [];
  const lines = items.map((item) => {
    if (!item?.ok) {
      return `- ${item.software_name || item.software_id}: 失败 - ${item.error || '未知错误'}`;
    }
    if (data?.mode === 'nightly_full') {
      const bugs = item.bugs || {};
      const cases = item.testcases || {};
      return `- ${item.software_name || item.software_id}: Bug远端 ${bugs.remote_total || 0} / 新增 ${bugs.created || 0} / 更新 ${bugs.updated || 0}；用例远端 ${cases.remote_total || 0} / 新增 ${cases.created || 0} / 更新 ${cases.updated || 0}`;
    }
    const bugs = item.result?.bugs || {};
    const cases = item.result?.testcases || {};
    return `- ${item.software_name || item.software_id}: 最近Bug ${bugs.remote_total || 0} / 新增 ${bugs.created || 0} / 更新 ${bugs.updated || 0}；最近用例 ${cases.remote_total || 0} / 新增 ${cases.created || 0} / 更新 ${cases.updated || 0}`;
  });

  detailEl.style.display = 'block';
  detailEl.textContent = lines.join('\n') || '本次没有返回明细。';
}

async function runZentaoBackgroundSync(url, buttonId) {
  const btn = document.getElementById(buttonId);
  const resultEl = document.getElementById('ztBackgroundSyncResult');
  const detailEl = document.getElementById('ztBackgroundSyncDetail');
  const originalText = btn?.textContent || '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '执行中...';
  }
  if (resultEl) {
    resultEl.style.display = 'block';
    resultEl.innerHTML = '<span style="color:#2563eb;">正在执行后台同步任务...</span>';
  }
  if (detailEl) {
    detailEl.style.display = 'none';
    detailEl.textContent = '';
  }
  try {
    const response = await api(url, { method: 'POST', headers: window.H });
    const data = await response.json();
    renderZentaoBackgroundSyncResult(data);
    window.showMessage && window.showMessage('后台同步任务执行完成', 'success');
  } catch (err) {
    if (resultEl) {
      resultEl.style.display = 'block';
      resultEl.innerHTML = `<span style="color:#dc2626;">❌ 执行失败：${err.message || '未知错误'}</span>`;
    }
    if (detailEl) {
      detailEl.style.display = 'none';
      detailEl.textContent = '';
    }
    window.showMessage && window.showMessage(err.message || '后台同步任务执行失败', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }
}

export async function runZentaoRecentSync() {
  return runZentaoBackgroundSync('/workbench/admin/run-recent-sync', 'ztRunRecentSyncBtn');
}

export async function runZentaoNightlyFullSync() {
  return runZentaoBackgroundSync('/workbench/admin/run-nightly-full-sync', 'ztRunNightlyFullSyncBtn');
}

function escapeHtmlData(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const versionDiffState = {
  majorId: 0,
  data: null,
  selectedActions: new Map(), // key → action object
};

function fillVersionDiffMajorSelect() {
  const sel = document.getElementById('ztVersionDiffMajorSelect');
  if (!sel) return;
  const prev = sel.value || '';
  const majors = (window.versions || []).filter((v) => v.version_type === 'major');
  if (!majors.length) {
    sel.innerHTML = '<option value="">暂无大版本</option>';
    return;
  }
  sel.innerHTML = '<option value="">请选择</option>' + majors.map((m) => `<option value="${m.id}">${escapeHtmlData(m.version_no)}</option>`).join('');
  if (prev && majors.some((m) => String(m.id) === prev)) sel.value = prev;
}

export async function openVersionDiffModal() {
  fillVersionDiffMajorSelect();
  const sel = document.getElementById('ztVersionDiffMajorSelect');
  const majorId = Number(sel?.value || 0);
  if (!majorId) {
    window.showMessage && window.showMessage('请先选择本地大版本', 'error');
    return;
  }
  versionDiffState.majorId = majorId;
  versionDiffState.data = null;
  versionDiffState.selectedActions = new Map();
  const area = document.getElementById('ztVersionDiffArea');
  if (area) area.innerHTML = '<div class="muted">对账中...</div>';
  try {
    const res = await api(`/api/admin/versions/compare?major_version_id=${majorId}`);
    const data = await res.json();
    versionDiffState.data = data;
    renderVersionDiff();
  } catch (err) {
    if (area) area.innerHTML = `<div style="color:#dc2626;">对账失败：${escapeHtmlData(err.message || '未知错误')}</div>`;
  }
}

function renderVersionDiff() {
  const area = document.getElementById('ztVersionDiffArea');
  if (!area) return;
  const data = versionDiffState.data;
  if (!data) { area.innerHTML = ''; return; }

  const headerBits = [];
  if (data.major) headerBits.push(`大版本 <b>${escapeHtmlData(data.major.version_no)}</b>`);
  if (data.execution) headerBits.push(`禅道执行 <b>${escapeHtmlData(data.execution.name || data.execution.id)}</b>`);
  const errors = Array.isArray(data.errors) ? data.errors : [];
  const errorHtml = errors.length ? `<div style="color:#b45309; margin-bottom:8px;">⚠ ${errors.map(escapeHtmlData).join('；')}</div>` : '';

  const diff = data.diff || {};
  const onlyLocal = diff.only_local || [];
  const onlyRemote = diff.only_remote || [];
  const matched = diff.matched || [];
  const placeholders = diff.remote_placeholders || [];

  const localList = onlyLocal.map((row) => {
    const key = `del:${row.id}`;
    const checked = versionDiffState.selectedActions.has(key) ? 'checked' : '';
    return `
      <tr>
        <td><input type="checkbox" data-action-key="${key}" data-action-kind="delete_local" data-version-id="${row.id}" ${checked}></td>
        <td>${escapeHtmlData(row.version_no)}</td>
        <td class="muted">${row.zentao_build_id || '-'}</td>
      </tr>`;
  }).join('');

  const remoteList = onlyRemote.map((row) => {
    const key = `imp:${row.id}`;
    const checked = versionDiffState.selectedActions.has(key) ? 'checked' : '';
    return `
      <tr>
        <td><input type="checkbox" data-action-key="${key}" data-action-kind="import_from_zentao" data-build-id="${row.id}" ${checked}></td>
        <td>${escapeHtmlData(row.normalized_name)}</td>
        <td class="muted">${row.id}</td>
      </tr>`;
  }).join('');

  const matchedList = matched.map((p) => {
    const tone = p.name_mismatch ? '#d97706' : '#16a34a';
    const label = p.name_mismatch ? '名字差异' : '一致';
    return `<tr>
      <td>${escapeHtmlData(p.local.version_no)}</td>
      <td>${escapeHtmlData(p.remote.normalized_name)}</td>
      <td style="color:${tone};">${label}</td>
    </tr>`;
  }).join('');

  area.innerHTML = `
    ${errorHtml}
    <div style="font-size:13px; color:#475569; margin-bottom:10px;">${headerBits.join(' · ')}</div>
    <div class="row" style="gap:16px; flex-wrap:wrap;">
      <div style="flex:1; min-width:280px;">
        <div style="font-weight:600; margin-bottom:6px;">本地有 / 禅道无（建议删除本地）</div>
        ${onlyLocal.length ? `<table style="width:100%; font-size:13px;"><thead><tr><th></th><th>本地版本号</th><th>build_id</th></tr></thead><tbody>${localList}</tbody></table>` : '<div class="muted">无差异</div>'}
      </div>
      <div style="flex:1; min-width:280px;">
        <div style="font-weight:600; margin-bottom:6px;">禅道有 / 本地无（建议补到本地）</div>
        ${onlyRemote.length ? `<table style="width:100%; font-size:13px;"><thead><tr><th></th><th>禅道 build 名</th><th>build_id</th></tr></thead><tbody>${remoteList}</tbody></table>` : '<div class="muted">无差异</div>'}
      </div>
    </div>
    <div style="margin-top:14px;">
      <details>
        <summary style="cursor:pointer; color:#475569;">已对齐（${matched.length}）${placeholders.length ? ` · 占位 build ${placeholders.length}` : ''}</summary>
        ${matched.length ? `<table style="width:100%; font-size:12px; margin-top:6px;"><thead><tr><th>本地</th><th>禅道</th><th>状态</th></tr></thead><tbody>${matchedList}</tbody></table>` : '<div class="muted" style="margin-top:6px;">暂无</div>'}
      </details>
    </div>
    <div class="row" style="justify-content:flex-end; gap:8px; margin-top:14px;">
      <button class="secondary" onclick="openVersionDiffModal()">刷新对账</button>
      <button onclick="applyVersionDiff()">应用选中差异</button>
    </div>
  `;

  area.querySelectorAll('input[type=checkbox][data-action-key]').forEach((el) => {
    el.addEventListener('change', () => {
      const key = el.getAttribute('data-action-key');
      const kind = el.getAttribute('data-action-kind');
      if (el.checked) {
        const action = kind === 'delete_local'
          ? { action: 'delete_local', version_id: Number(el.getAttribute('data-version-id')) }
          : { action: 'import_from_zentao', zentao_build_id: Number(el.getAttribute('data-build-id')) };
        versionDiffState.selectedActions.set(key, action);
      } else {
        versionDiffState.selectedActions.delete(key);
      }
    });
  });
}

export async function applyVersionDiff() {
  const actions = Array.from(versionDiffState.selectedActions.values());
  if (!actions.length) {
    window.showMessage && window.showMessage('请先勾选要应用的差异', 'error');
    return;
  }
  if (!confirm(`将应用 ${actions.length} 条修复，确认？`)) return;
  try {
    const res = await api(`/api/admin/versions/apply-diff?major_version_id=${versionDiffState.majorId}`, {
      method: 'POST',
      headers: window.H,
      body: { actions },
    });
    const data = await res.json();
    const bits = [
      `删除本地 ${data.deleted_local?.length || 0}`,
      `补录本地 ${data.imported_local?.length || 0}`,
    ];
    if (data.errors?.length) bits.push(`告警 ${data.errors.length}`);
    window.showMessage && window.showMessage('对账完成：' + bits.join('，'), data.errors?.length ? 'info' : 'success');
    versionDiffState.selectedActions = new Map();
    await window.loadVersions();
    await loadDataOverview();
    await openVersionDiffModal();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '应用差异失败', 'error');
  }
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
  loadZtProjects,
  syncZtVersions,
  runZentaoRecentSync,
  runZentaoNightlyFullSync,
  openRequirementStoryBindingModal,
  closeRequirementStoryBindingModal,
  previewRequirementStoryBinding,
  clearRequirementStoryBinding,
  saveRequirementStoryBinding,
  openUserTabPermissionModal,
  closeUserTabPermissionModal,
  saveUserTabPermissions,
  openVersionDiffModal,
  applyVersionDiff,
  fillVersionDiffMajorSelect,
};

window.toggleMajorBody = toggleMajorBody;
window.loadZtProjects = loadZtProjects;
window.syncZtVersions = syncZtVersions;
window.runZentaoRecentSync = runZentaoRecentSync;
window.runZentaoNightlyFullSync = runZentaoNightlyFullSync;
window.openRequirementStoryBindingModal = openRequirementStoryBindingModal;
window.closeRequirementStoryBindingModal = closeRequirementStoryBindingModal;
window.previewRequirementStoryBinding = previewRequirementStoryBinding;
window.clearRequirementStoryBinding = clearRequirementStoryBinding;
window.saveRequirementStoryBinding = saveRequirementStoryBinding;
window.openUserTabPermissionModal = openUserTabPermissionModal;
window.closeUserTabPermissionModal = closeUserTabPermissionModal;
window.saveUserTabPermissions = saveUserTabPermissions;
window.openVersionDiffModal = openVersionDiffModal;
window.applyVersionDiff = applyVersionDiff;
