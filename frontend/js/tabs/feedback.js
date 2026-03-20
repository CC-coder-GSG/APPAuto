import { api } from '../api.js';
import { state } from '../state.js';
import { renderBugLink } from '../utils.js';

let currentPage = 1;
let currentPageSize = 10;

function feedbackStatusZh(v) {
  const m = { pending: '待处理', processing: '处理中', resolved: '已处理', closed: '已关闭' };
  return m[v] || v;
}

function buildStatusBadge(status) {
  const conf = {
    pending: { bg: '#fef9c3', color: '#854d0e' },
    processing: { bg: '#dbeafe', color: '#1d4ed8' },
    resolved: { bg: '#dcfce7', color: '#166534' },
    closed: { bg: '#e2e8f0', color: '#334155' },
  };
  const c = conf[status] || conf.pending;
  return `<span class="badge" style="display:inline-flex; align-items:center; border-radius:99px; padding:4px 10px; background:${c.bg}; color:${c.color}; font-weight:600; font-size:12px; line-height:1;">${feedbackStatusZh(status)}</span>`;
}

function selectedMajorId() {
  return Number(document.getElementById('feedbackMajorSelect')?.value || 0);
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let val = bytes;
  let idx = 0;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx += 1;
  }
  return `${val.toFixed(val >= 10 ? 0 : 1)} ${units[idx]}`;
}

function fileIconByExt(ext = '') {
  const e = ext.toLowerCase();
  if (['.doc', '.docx'].includes(e)) return '📄';
  if (['.xls', '.xlsx', '.csv'].includes(e)) return '📊';
  if (['.pdf'].includes(e)) return '📕';
  if (['.zip', '.rar', '.7z'].includes(e)) return '🗜️';
  if (['.txt', '.md', '.log'].includes(e)) return '📝';
  return '📎';
}

export async function loadFeedbackMinorOptions() {
  const majorId = selectedMajorId();
  const sel = document.getElementById('feedbackMinorSelect');
  const all = (window.versions || []).filter((v) => v.version_type === 'minor');
  if (!sel) return;
  if (majorId === 0) {
    if (all.length === 0) {
      sel.innerHTML = "<option value=''>暂无子版本</option>";
      return;
    }
    sel.innerHTML = "<option value='0'>全部小版本</option>" + all.map((v) => `<option value='${v.id}'>${v.version_no}</option>`).join('');
    return;
  }
  const rows = all.filter((v) => Number(v.parent_id) === majorId);
  if (rows.length === 0) {
    sel.innerHTML = "<option value=''>暂无子版本</option>";
    return;
  }
  sel.innerHTML = rows.map((v) => `<option value='${v.id}'>${v.version_no}</option>`).join('');
}

function getListFilters() {
  const keyword = (document.getElementById('feedbackKeyword')?.value || '').trim();
  const status = document.getElementById('feedbackStatusSelect')?.value || '';
  const assigneeId = Number(document.getElementById('feedbackAssigneeSelect')?.value || 0);
  const majorId = selectedMajorId();
  const minorId = Number(document.getElementById('feedbackMinorSelect')?.value || 0);
  const sortBy = document.getElementById('feedbackSortBy')?.value || 'created_at';
  const sortOrder = document.getElementById('feedbackSortOrder')?.value || 'desc';
  const pageSize = Number(document.getElementById('feedbackPageSize')?.value || currentPageSize || 10);
  return { keyword, status, assigneeId, majorId, minorId, sortBy, sortOrder, pageSize };
}

async function listFeedbacksPaged(page = 1) {
  const { keyword, status, assigneeId, majorId, minorId, sortBy, sortOrder, pageSize } = getListFilters();
  const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  currentPageSize = pageSize;
  const params = new URLSearchParams();
  if (keyword) params.set('keyword', keyword);
  if (status) params.set('status', status);
  if (assigneeId) params.set('assignee_id', String(assigneeId));
  if (softwareId) params.set('software_id', String(softwareId));
  if (majorId) params.set('major_version_id', String(majorId));
  if (minorId) params.set('minor_version_id', String(minorId));
  params.set('page', String(page));
  params.set('page_size', String(pageSize));
  params.set('sort_by', sortBy);
  params.set('sort_order', sortOrder);
  const data = await (await api(`/feedbacks/paged?${params.toString()}`)).json();
  return data || { items: [], total: 0, page, page_size: pageSize };
}

function renderPagination(total, page, pageSize) {
  const el = document.getElementById('feedbackPaginationText');
  if (!el) return;
  const pages = Math.max(1, Math.ceil((total || 0) / (pageSize || 10)));
  el.innerText = `第 ${page} / ${pages} 页，共 ${total || 0} 条`;
  const prev = document.getElementById('feedbackPrevBtn');
  const next = document.getElementById('feedbackNextBtn');
  if (prev) prev.disabled = page <= 1;
  if (next) next.disabled = page >= pages;
}

function renderFeedbackList(rows) {
  const tbody = document.getElementById('feedbackTable');
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#94a3b8; padding:20px;">暂无反馈数据</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r) => `
    <tr>
      <td>${r.feedback_no || '-'}</td>
      <td title="${(r.summary || '').replace(/"/g, '&quot;')}">${(r.summary || '').slice(0, 50)}${(r.summary || '').length > 50 ? '...' : ''}</td>
      <td>${r.major_version_no || '-'} / ${r.minor_version_no || '-'}</td>
      <td>${buildStatusBadge(r.status)}</td>
      <td>${r.assignee_name || '<span class="muted">未指派</span>'}</td>
      <td>${r.creator_name || '-'}</td>
      <td>${r.created_at ? new Date(r.created_at).toLocaleString() : '-'}</td>
      <td><button class="secondary" onclick="openFeedbackDetail(${r.id})">查看详情</button></td>
    </tr>
  `).join('');
}

export async function loadFeedbackBoard(page = 1) {
  await loadFeedbackMinorOptions();
  const data = await listFeedbacksPaged(page);
  currentPage = data.page || page;
  renderFeedbackList(data.items || []);
  renderPagination(data.total || 0, currentPage, data.page_size || currentPageSize);
}

export async function nextFeedbackPage() {
  await loadFeedbackBoard(currentPage + 1);
}

export async function prevFeedbackPage() {
  if (currentPage <= 1) return;
  await loadFeedbackBoard(currentPage - 1);
}

export async function createFeedback() {
  const noNum = (document.getElementById('newFeedbackNoNum')?.value || '').trim();
  const majorId = selectedMajorId();
  const minorId = Number(document.getElementById('feedbackMinorSelect')?.value || 0);
  const summary = (document.getElementById('newFeedbackSummary')?.value || '').trim();
  if (!majorId || !minorId) {
    window.showMessage && window.showMessage('请选择反馈版本（大版本/小版本）', 'error');
    return;
  }
  if (!summary) {
    window.showMessage && window.showMessage('反馈概览不能为空', 'error');
    return;
  }

  const created = await (await api('/feedbacks', {
    method: 'POST',
    headers: window.H,
    body: { feedback_no_num: noNum || null, major_version_id: majorId, minor_version_id: minorId, summary },
  })).json();

  const createFilesInput = document.getElementById('newFeedbackFiles');
  const createFiles = createFilesInput?.files ? Array.from(createFilesInput.files) : [];
  if (createFiles.length > 0) {
    for (const file of createFiles) {
      const form = new FormData();
      form.append('file', file);
      await api(`/feedbacks/${created.id}/attachments`, { method: 'POST', body: form });
    }
  }
  window.showMessage && window.showMessage('反馈已创建', 'success');
  document.getElementById('newFeedbackNoNum').value = '';
  document.getElementById('newFeedbackSummary').value = '';
  if (createFilesInput) createFilesInput.value = '';
  await loadFeedbackBoard(1);
}

export async function uploadFeedbackFiles(feedbackId) {
  const input = document.getElementById('feedbackUploadInput');
  const files = input?.files ? Array.from(input.files) : [];
  if (!files.length) {
    window.showMessage && window.showMessage('请先选择附件', 'error');
    return;
  }
  for (const file of files) {
    const form = new FormData();
    form.append('file', file);
    await api(`/feedbacks/${feedbackId}/attachments`, { method: 'POST', body: form });
  }
  window.showMessage && window.showMessage('附件上传完成', 'success');
  await openFeedbackDetail(feedbackId);
}

export async function deleteFeedbackAttachment(attachmentId, feedbackId) {
  if (!confirm('确定删除该附件吗？')) return;
  await api(`/feedbacks/attachments/${attachmentId}`, { method: 'DELETE' });
  window.showMessage && window.showMessage('附件已删除', 'success');
  await openFeedbackDetail(feedbackId);
}

async function loadBugOptions(keyword = '') {
  const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  const params = new URLSearchParams();
  if (keyword) params.set('keyword', keyword);
  if (softwareId) params.set('software_id', String(softwareId));
  const rows = await (await api(`/feedbacks/bug-options?${params.toString()}`)).json();
  const sel = document.getElementById('feedbackLinkBugId');
  if (!sel) return;
  sel.innerHTML = '<option value="">选择已有 Bug</option>' + rows.map((b) => `<option value="${b.id}">${b.bug_id} (ID:${b.id})</option>`).join('');
}

function renderStatusOps(feedbackId, currentStatus) {
  const isAdmin = window.currentUser && window.currentUser.role === 'admin';
  const statuses = isAdmin ? ['pending', 'processing', 'resolved', 'closed'] : ['pending', 'processing', 'resolved'];
  return statuses.map((s) => {
    const active = s === currentStatus;
    return `<button class="${active ? 'secondary' : ''}" style="padding:6px 12px; font-size:12px; border-radius:10px; transition:all .2s ease;" onclick="setFeedbackStatus(${feedbackId}, '${s}')">${feedbackStatusZh(s)}</button>`;
  }).join('');
}

export async function openFeedbackDetail(feedbackId) {
  const detail = await (await api(`/feedbacks/${feedbackId}`)).json();
  const timeline = await (await api(`/feedbacks/${feedbackId}/timeline`)).json();
  const panel = document.getElementById('feedbackDetailPanel');
  const body = document.getElementById('feedbackDetailBody');
  if (!panel || !body) return;
  const sectionCardStyle = 'flex:1; min-width:320px; min-height:320px; border-radius:12px; background:#ffffff; box-shadow:0 4px 6px -1px rgba(0,0,0,0.05); padding:22px; display:flex; flex-direction:column;';

  const attachmentsHtml = (detail.attachments || []).map((a) => {
    const dl = `/feedbacks/attachments/${a.id}/download`;
    if (a.is_image) {
      return `<div style="display:inline-block; margin-right:10px; margin-bottom:10px;">
        <a href="${dl}" target="_blank">
          <img src="${dl}" alt="${a.original_name}" style="width:136px; height:102px; object-fit:cover; border-radius:10px; box-shadow:0 2px 8px rgba(15,23,42,0.08);">
        </a>
        <div style="font-size:12px; margin-top:6px; color:#64748b;">
          <a href="javascript:void(0)" style="color:#ef4444; text-decoration:none;" onclick="deleteFeedbackAttachment(${a.id}, ${detail.id})">删除</a>
        </div>
      </div>`;
    }
    return `<div class="row" style="margin-bottom:8px; justify-content:space-between; background:#f8fafc; border-radius:10px; padding:10px 12px;">
      <span style="color:#0f172a;">${fileIconByExt(a.file_ext)} ${a.original_name} <span class="muted">(${formatBytes(a.file_size)})</span></span>
      <span>
        <a href="${dl}" target="_blank" style="text-decoration:none;">下载</a>
        <a href="javascript:void(0)" style="color:#ef4444; margin-left:8px; text-decoration:none;" onclick="deleteFeedbackAttachment(${a.id}, ${detail.id})">删除</a>
      </span>
    </div>`;
  }).join('') || '<span class="muted">暂无附件</span>';

  const bugsHtml = (detail.bugs || []).map((b) => `<span class="badge" style="display:inline-flex; align-items:center; margin-right:8px; margin-bottom:8px; border-radius:99px; padding:6px 10px; background:#f8fafc; color:#334155;">${renderBugLink(b)} <a href="javascript:void(0)" style="color:#ef4444; margin-left:6px; text-decoration:none;" onclick="unlinkFeedbackBug(${detail.id}, ${b.id})">×</a></span>`).join('') || '<span class="muted">暂无关联 Bug</span>';
  const users = window.users || [];
  const isAdmin = window.currentUser && window.currentUser.role === 'admin';
  const assigneeOpts = users.map((u) => `<option value="${u.id}" ${Number(detail.assignee_id) === Number(u.id) ? 'selected' : ''}>${u.display_name || u.username}</option>`).join('');
  const actionZhMap = {
    'feedback.create': '创建反馈',
    'feedback.assign': '指派处理人',
    'feedback.handle': '提交处理结果',
    'feedback.status': '更新反馈状态',
    'feedback.upload_attachment': '上传附件',
    'feedback.delete_attachment': '删除附件',
    'feedback.link_bug': '关联已有Bug',
    'feedback.create_bug_link': '新建并关联Bug',
    'feedback.unlink_bug': '解除Bug关联',
  };
  const timelineHtml = (timeline || []).map((t) => {
    const actionZh = actionZhMap[t.action] || t.action || '未知操作';
    const actorName = t.actor_name || '系统';
    const timeText = t.created_at ? new Date(t.created_at).toLocaleString() : '-';
    return `<div style="padding:10px 0; border-bottom:1px dashed #e2e8f0; line-height:1.65;">
      <b>${actionZh}</b> · ${actorName} · ${timeText}
      <div class="muted">${t.detail || ''}</div>
    </div>`;
  }).join('') || '<span class="muted">暂无操作记录</span>';

  body.innerHTML = `
    <div class="card" style="border-radius:12px; background:#f8fafc; box-shadow:0 4px 6px -1px rgba(0,0,0,0.05); padding:24px;">
      <div class="row" style="justify-content:space-between; align-items:flex-start; margin-bottom:8px;">
        <div>
          <h3 style="margin:0; color:#0f172a; font-size:22px; line-height:1.35;">反馈详情 ${detail.feedback_no || '未编号'}</h3>
          <div style="margin-top:6px; color:#64748b; font-size:13px;">创建于 ${detail.created_at ? new Date(detail.created_at).toLocaleString() : '-'}</div>
        </div>
        <div class="row" style="gap:8px; margin:0;">
          <button class="secondary" onclick="openAuditTimelineModal('feedback', ${detail.id}, '反馈时间线')">时间线</button>
          ${buildStatusBadge(detail.status)}
        </div>
      </div>
      <div class="row" style="margin-top:12px; margin-bottom:4px; gap:8px;">${renderStatusOps(detail.id, detail.status)}</div>

      <div class="row" style="align-items:stretch; gap:20px; margin-top:16px; flex-wrap:wrap;">
        <div class="card" style="${sectionCardStyle}">
          <div style="font-weight:700; margin-bottom:12px; color:#0f172a;">概览信息</div>
          <div style="display:grid; row-gap:8px; color:#334155; line-height:1.7;">
            <div><span style="color:#64748b;">反馈版本：</span>${detail.major_version_no || '-'} / ${detail.minor_version_no || '-'}</div>
            <div><span style="color:#64748b;">创建人：</span>${detail.creator_name || '-'}</div>
            <div><span style="color:#64748b;">指派处理人：</span>${detail.assignee_name || '未指派'}</div>
          </div>
        </div>

        <div class="card" style="${sectionCardStyle}">
          <div style="font-weight:700; margin-bottom:12px; color:#0f172a;">反馈内容</div>
          <div style="white-space:pre-wrap; margin-bottom:12px; color:#334155; line-height:1.75;">${detail.summary || ''}</div>
          <div>${attachmentsHtml}</div>
          <div class="row" style="margin-top:auto; padding-top:10px;">
            <input type="file" id="feedbackUploadInput" multiple>
            <button onclick="uploadFeedbackFiles(${detail.id})" style="border-radius:10px;">上传附件</button>
          </div>
        </div>
      </div>

      <div class="row" style="align-items:stretch; gap:20px; margin-top:16px; flex-wrap:wrap;">
        <div class="card" style="${sectionCardStyle}">
          <div style="font-weight:700; margin-bottom:12px; color:#0f172a;">处理信息</div>
          <div class="row" style="${isAdmin ? '' : 'display:none;'}">
            <select id="feedbackAssigneeSelectInDetail">${assigneeOpts}</select>
            <button class="secondary" onclick="assignFeedback(${detail.id})" style="border-radius:10px; background:#eef2ff; color:#4338ca; border:none;">管理员指派</button>
          </div>
          <textarea id="feedbackHandleResult" rows="4" placeholder="处理结果" style="width:100%; margin-top:10px; border-radius:10px; border:none; box-shadow:inset 0 0 0 1px #e2e8f0;">${detail.handling_result || ''}</textarea>
          <div class="row" style="margin-top:10px;">
            <label>处理大版本</label><select id="feedbackHandleMajor"></select>
            <label>处理小版本</label><select id="feedbackHandleMinor"></select>
          </div>
          <div class="row" style="margin-top:12px;">
            <select id="feedbackHandleStatus">
              <option value="processing">处理中</option>
              <option value="resolved">已处理</option>
              <option value="closed">已关闭</option>
            </select>
            <button onclick="saveFeedbackHandle(${detail.id})" style="border-radius:10px;">保存处理结果</button>
          </div>
        </div>

        <div class="card" style="${sectionCardStyle}">
          <div style="font-weight:700; margin-bottom:12px; color:#0f172a;">关联 Bug</div>
          <div style="margin-bottom:10px; min-height:38px;">${bugsHtml}</div>
          <div class="row">
            <div class="prefix-input" style="min-width:220px; border-radius:10px;">
              <span>🔎</span>
              <input id="feedbackBugKeyword" placeholder="按 b# 搜索已有 Bug" oninput="searchFeedbackBugOptions(this.value)">
            </div>
          </div>
          <div class="row" style="margin-top:10px;">
            <select id="feedbackLinkBugId" style="min-width:220px;"><option value="">选择已有 Bug</option></select>
            <button class="secondary" onclick="linkFeedbackBug(${detail.id})" style="border-radius:10px; background:#eef2ff; color:#4338ca; border:none;">关联已有 Bug</button>
          </div>
          <div class="row" style="margin-top:10px;">
            <div class="prefix-input" style="border-radius:10px;"><span>b#</span><input id="feedbackNewBugNo" inputmode="numeric" oninput="digitsOnly(this)" placeholder="新增 Bug 数字"></div>
            <button onclick="createFeedbackBug(${detail.id})" style="border-radius:10px;">新增 Bug 并关联</button>
          </div>
        </div>
      </div>

      <div class="card" style="margin-top:16px; border-radius:12px; box-shadow:0 4px 6px -1px rgba(0,0,0,0.05); padding:22px;">
        <div style="font-weight:700; margin-bottom:10px; color:#0f172a;">操作时间线</div>
        <div>${timelineHtml}</div>
      </div>
    </div>
  `;

  const majors = (window.versions || []).filter((v) => v.version_type === 'major');
  const mSel = document.getElementById('feedbackHandleMajor');
  const mmSel = document.getElementById('feedbackHandleMinor');
  mSel.innerHTML = majors.map((v) => `<option value="${v.id}" ${Number(detail.handled_major_version_id || detail.major_version_id) === Number(v.id) ? 'selected' : ''}>${v.version_no}</option>`).join('');
  const bindMinor = () => {
    const majorId = Number(mSel.value || 0);
    const minors = (window.versions || []).filter((v) => v.version_type === 'minor' && Number(v.parent_id) === majorId);
    mmSel.innerHTML = minors.map((v) => `<option value="${v.id}" ${Number(detail.handled_minor_version_id || detail.minor_version_id) === Number(v.id) ? 'selected' : ''}>${v.version_no}</option>`).join('');
  };
  mSel.onchange = bindMinor;
  bindMinor();
  document.getElementById('feedbackHandleStatus').value = detail.status === 'pending' ? 'processing' : detail.status;
  if (!isAdmin) {
    const statusSel = document.getElementById('feedbackHandleStatus');
    if (statusSel) {
      Array.from(statusSel.options).forEach((opt) => {
        if (opt.value === 'closed') opt.disabled = true;
      });
    }
  }
  await loadBugOptions();
  panel.classList.remove('hidden');
}

export async function searchFeedbackBugOptions(keyword) {
  await loadBugOptions(keyword || '');
}

export async function setFeedbackStatus(feedbackId, status) {
  await api(`/feedbacks/${feedbackId}/status`, { method: 'PATCH', headers: window.H, body: { status } });
  window.showMessage && window.showMessage(`状态已更新为：${feedbackStatusZh(status)}`, 'success');
  await openFeedbackDetail(feedbackId);
  await loadFeedbackBoard(currentPage);
}

export async function assignFeedback(feedbackId) {
  const assigneeId = Number(document.getElementById('feedbackAssigneeSelectInDetail')?.value || 0);
  if (!assigneeId) {
    window.showMessage && window.showMessage('请选择处理人', 'error');
    return;
  }
  await api(`/feedbacks/${feedbackId}/assign`, { method: 'POST', headers: window.H, body: { assignee_id: assigneeId, status: 'processing' } });
  window.showMessage && window.showMessage('反馈已指派并触发企微通知', 'success');
  await openFeedbackDetail(feedbackId);
  await loadFeedbackBoard(currentPage);
}

export async function saveFeedbackHandle(feedbackId) {
  const handlingResult = (document.getElementById('feedbackHandleResult')?.value || '').trim();
  const handledMajor = Number(document.getElementById('feedbackHandleMajor')?.value || 0);
  const handledMinor = Number(document.getElementById('feedbackHandleMinor')?.value || 0);
  const status = document.getElementById('feedbackHandleStatus')?.value || 'resolved';
  if (!handlingResult) {
    window.showMessage && window.showMessage('请填写处理结果', 'error');
    return;
  }
  await api(`/feedbacks/${feedbackId}/handle`, {
    method: 'POST',
    headers: window.H,
    body: { handling_result: handlingResult, handled_major_version_id: handledMajor, handled_minor_version_id: handledMinor, status },
  });
  window.showMessage && window.showMessage('处理结果已保存', 'success');
  await openFeedbackDetail(feedbackId);
  await loadFeedbackBoard(currentPage);
}

export async function linkFeedbackBug(feedbackId) {
  const bid = Number(document.getElementById('feedbackLinkBugId')?.value || 0);
  if (!bid) {
    window.showMessage && window.showMessage('请选择要关联的 Bug', 'error');
    return;
  }
  await api(`/feedbacks/${feedbackId}/bugs/link`, { method: 'POST', headers: window.H, body: { bug_id: bid } });
  window.showMessage && window.showMessage('已关联 Bug', 'success');
  await openFeedbackDetail(feedbackId);
}

export async function unlinkFeedbackBug(feedbackId, bugId) {
  if (!confirm('确定解除该 Bug 关联吗？')) return;
  await api(`/feedbacks/${feedbackId}/bugs/${bugId}`, { method: 'DELETE' });
  window.showMessage && window.showMessage('已解除 Bug 关联', 'success');
  await openFeedbackDetail(feedbackId);
}

export async function createFeedbackBug(feedbackId) {
  const num = (document.getElementById('feedbackNewBugNo')?.value || '').trim();
  const bugId = window.withPrefix('b#', num);
  if (!bugId) {
    window.showMessage && window.showMessage('请输入 Bug 数字编号', 'error');
    return;
  }
  await api(`/feedbacks/${feedbackId}/bugs/create-and-link`, { method: 'POST', headers: window.H, body: { bug_id: bugId } });
  window.showMessage && window.showMessage('已新建并关联 Bug', 'success');
  await openFeedbackDetail(feedbackId);
}

export async function loadFeedbackTodoOnMine(majorVersionId) {
  const majorPart = majorVersionId ? `?major_version_id=${majorVersionId}` : '';
  const rows = await (await api(`/feedbacks/my-todo${majorPart}`)).json();
  if (!rows.length) {
    state.currentFeedbackTodoHtml = '';
    return;
  }
  state.currentFeedbackTodoHtml = `
    <div class="card" style="border:2px solid #16a34a; background:#f0fdf4; margin-bottom:24px;">
      <h3 style="color:#166534; margin-top:0; border-bottom:1px dashed #86efac; padding-bottom:8px;">📚 反馈待处理</h3>
      <table style="background:#fff; border-radius:6px; overflow:hidden;">
        <thead><tr><th>反馈编号</th><th>反馈概览</th><th>反馈版本</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td>${r.feedback_no || '-'}</td>
              <td>${(r.summary || '').slice(0, 36)}${(r.summary || '').length > 36 ? '...' : ''}</td>
              <td>${r.major_version_no || '-'} / ${r.minor_version_no || '-'}</td>
              <td>${buildStatusBadge(r.status)}</td>
              <td><button class="secondary" onclick="showTab('feedback'); openFeedbackDetail(${r.id});">处理</button></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

window.OmniQAFeedbackTab = {
  loadFeedbackBoard,
  loadFeedbackMinorOptions,
  nextFeedbackPage,
  prevFeedbackPage,
  createFeedback,
  openFeedbackDetail,
  uploadFeedbackFiles,
  deleteFeedbackAttachment,
  assignFeedback,
  saveFeedbackHandle,
  linkFeedbackBug,
  unlinkFeedbackBug,
  createFeedbackBug,
  loadFeedbackTodoOnMine,
  searchFeedbackBugOptions,
  setFeedbackStatus,
};
