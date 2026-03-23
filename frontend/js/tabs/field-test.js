import { api } from '../api.js';
import { withPrefix, renderBugLink } from '../utils.js';

const PURPOSE_ZH = { requirement: '需求测试', feature: '功能测试' };
const RESULT_ZH = { passed: '测试通过', failed: '测试未通过' };

const state = {
  editingId: null,
  bugDrafts: [],
  inited: false,
  page: 1,
  pageSize: 20,
};

function getVersions() { return window.versions || []; }
function currentUser() { return window.currentUser || null; }
function isAdmin() { return currentUser()?.role === 'admin'; }

function majorOptionsHtml(withAll = false) {
  const majors = getVersions().filter((v) => v.version_type === 'major');
  const opts = majors.map((v) => `<option value="${v.id}">${v.version_no}</option>`).join('');
  return withAll ? `<option value="0">全部版本</option>${opts}` : (opts || `<option value="">暂无大版本</option>`);
}

function fillMinorByMajor(majorSelectId, minorSelectId, allowAll = false) {
  const majorEl = document.getElementById(majorSelectId);
  const minorEl = document.getElementById(minorSelectId);
  if (!majorEl || !minorEl) return;
  const majorId = Number(majorEl.value || 0);
  const allMinors = getVersions().filter((v) => v.version_type === 'minor');

  if (allowAll && majorId === 0) {
    minorEl.innerHTML = `<option value="0">全部小版本</option>${allMinors.map((v) => `<option value="${v.id}">${v.version_no}</option>`).join('')}`;
    return;
  }

  const minors = allMinors.filter((v) => Number(v.parent_id) === majorId);
  minorEl.innerHTML = allowAll
    ? `<option value="0">全部小版本</option>${minors.map((v) => `<option value="${v.id}">${v.version_no}</option>`).join('')}`
    : (minors.length ? minors.map((v) => `<option value="${v.id}">${v.version_no}</option>`).join('') : `<option value="">暂无小版本</option>`);
}

async function loadRequirementOptions() {
  const majorId = Number(document.getElementById('fieldTestMajorSelect')?.value || 0);
  const reqSel = document.getElementById('fieldTestRequirementSelect');
  if (!reqSel) return;
  if (!majorId) { reqSel.innerHTML = `<option value="">请先选择大版本</option>`; return; }
  try {
    const data = await (await api(`/field-tests/options?major_version_id=${majorId}`)).json();
    const reqs = data.requirements || [];
    reqSel.innerHTML = `<option value="">请选择需求</option>${reqs.map((r) => `<option value="${r.id}">${r.label}</option>`).join('')}`;
  } catch (err) {
    reqSel.innerHTML = `<option value="">需求加载失败</option>`;
    window.showMessage && window.showMessage(err.message || '需求加载失败', 'error');
  }
}

function renderBugDrafts() {
  const box = document.getElementById('fieldTestBugList');
  if (!box) return;
  if (!state.bugDrafts.length) { box.innerHTML = '<span class="muted">暂无已添加 Bug</span>'; return; }
  box.innerHTML = state.bugDrafts.map((b, idx) => `
    <span class="badge" style="margin-right:6px; margin-bottom:6px; display:inline-flex; align-items:center;">
      ${b}
      <a href="javascript:void(0)" onclick="removeFieldTestBug(${idx})" style="margin-left:6px; color:#dc2626; text-decoration:none;">×</a>
    </span>
  `).join('');
}

export function addFieldTestBug() {
  const input = document.getElementById('fieldTestBugInput');
  const bug = withPrefix('b#', input?.value || '');
  if (!bug) { window.showMessage && window.showMessage('请输入 Bug 编号数字部分', 'error'); return; }
  if (state.bugDrafts.includes(bug)) { window.showMessage && window.showMessage('该 Bug 编号已添加', 'error'); return; }
  state.bugDrafts.push(bug);
  if (input) input.value = '';
  renderBugDrafts();
}

export function removeFieldTestBug(index) {
  state.bugDrafts = state.bugDrafts.filter((_, i) => i !== Number(index));
  renderBugDrafts();
}

function durationMinutes() {
  const startVal = document.getElementById('fieldTestStartTime')?.value;
  const endVal = document.getElementById('fieldTestEndTime')?.value;
  const textEl = document.getElementById('fieldTestDurationText');
  if (!startVal || !endVal) { if (textEl) textEl.value = ''; return 0; }
  const diff = Math.floor((new Date(endVal).getTime() - new Date(startVal).getTime()) / 60000);
  if (textEl) {
    if (diff > 0) {
      const h = Math.floor(diff / 60); const m = diff % 60;
      textEl.value = `${h}小时${m}分钟`;
    } else textEl.value = '时间无效';
  }
  return diff;
}

function collectPayload() {
  const majorId = Number(document.getElementById('fieldTestMajorSelect')?.value || 0);
  const minorId = Number(document.getElementById('fieldTestMinorSelect')?.value || 0);
  const purpose = document.getElementById('fieldTestPurpose')?.value || 'requirement';
  const requirementId = Number(document.getElementById('fieldTestRequirementSelect')?.value || 0);
  const testContent = (document.getElementById('fieldTestContentInput')?.value || '').trim();
  const startTime = document.getElementById('fieldTestStartTime')?.value;
  const endTime = document.getElementById('fieldTestEndTime')?.value;
  const result = document.getElementById('fieldTestResult')?.value || 'passed';
  const notes = (document.getElementById('fieldTestNotes')?.value || '').trim();

  if (!majorId) throw new Error('请选择大版本');
  if (!minorId) throw new Error('请选择小版本');
  if (purpose === 'requirement' && !requirementId) throw new Error('需求测试必须选择关联需求');
  if (purpose === 'feature' && !testContent) throw new Error('功能测试必须填写测试内容');
  if (!startTime || !endTime) throw new Error('请填写开始时间和结束时间');

  const minutes = durationMinutes();
  if (minutes <= 0) throw new Error('开始时间必须早于结束时间');
  if (minutes > 24 * 60) throw new Error('测试时长超过 24 小时，请检查时间输入');
  if (result === 'failed' && state.bugDrafts.length === 0) throw new Error('测试未通过时至少需要添加 1 个 Bug');

  return {
    major_version_id: majorId,
    minor_version_id: minorId,
    purpose_type: purpose,
    requirement_id: purpose === 'requirement' ? requirementId : null,
    test_content: purpose === 'feature' ? testContent : null,
    start_time: startTime,
    end_time: endTime,
    result_status: result,
    bug_ids: state.bugDrafts,
    notes: notes || null,
  };
}

function resultBadge(status) {
  const passed = status === 'passed';
  return `<span class="badge badge--status" style="background:${passed ? '#dcfce7' : '#fee2e2'}; color:${passed ? '#166534' : '#991b1b'};">${RESULT_ZH[status] || status}</span>`;
}

function purposeBadge(status) {
  const isReq = status === 'requirement';
  return `<span class="badge badge--status" style="background:${isReq ? '#dbeafe' : '#fef3c7'}; color:${isReq ? '#1d4ed8' : '#92400e'};">${PURPOSE_ZH[status] || status}</span>`;
}

function openFormModal() {
  document.getElementById('fieldTestFormModal')?.classList.remove('hidden');
}

function closeFormModal() {
  document.getElementById('fieldTestFormModal')?.classList.add('hidden');
}

function purposeText(row) { return row.purpose_type === 'requirement' ? (row.requirement_label || '未选择需求') : (row.test_content || '-'); }
function canEditRow(row) { const me = currentUser(); return !!me && (me.role === 'admin' || Number(row.tester_id) === Number(me.id)); }

function formatDateOnly(v) {
  if (!v) return '-';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return '-';
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function formatDateTime(v) {
  if (!v) return '-';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return '-';
  return `${formatDateOnly(v)} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function fillFormFromRecord(row) {
  document.getElementById('fieldTestMajorSelect').value = String(row.major_version_id || '');
  fillMinorByMajor('fieldTestMajorSelect', 'fieldTestMinorSelect', false);
  document.getElementById('fieldTestMinorSelect').value = String(row.minor_version_id || '');
  document.getElementById('fieldTestPurpose').value = row.purpose_type;
  onFieldTestPurposeChange();

  if (row.purpose_type === 'requirement') {
    loadRequirementOptions().then(() => { document.getElementById('fieldTestRequirementSelect').value = String(row.requirement_id || ''); });
  } else {
    document.getElementById('fieldTestContentInput').value = row.test_content || '';
  }

  document.getElementById('fieldTestStartTime').value = (row.start_time || '').slice(0, 16);
  document.getElementById('fieldTestEndTime').value = (row.end_time || '').slice(0, 16);
  document.getElementById('fieldTestResult').value = row.result_status || 'passed';
  document.getElementById('fieldTestNotes').value = row.notes || '';

  state.bugDrafts = (row.bugs || []).map((b) => b.bug_id);
  onFieldTestResultChange();
  renderBugDrafts();
  durationMinutes();

  state.editingId = row.id;
  document.getElementById('fieldTestFormTitle').innerText = `编辑外业测试记录 #${row.id}`;
  document.getElementById('fieldTestSubmitBtn').innerText = '保存修改';
  document.getElementById('fieldTestCancelEditBtn').innerText = '取消编辑';
  openFormModal();
}

export function cancelFieldTestEdit() {
  state.editingId = null;
  state.bugDrafts = [];
  document.getElementById('fieldTestFormTitle').innerText = '新增外业测试记录';
  document.getElementById('fieldTestSubmitBtn').innerText = '提交外业测试记录';
  document.getElementById('fieldTestCancelEditBtn').innerText = '取消';
  document.getElementById('fieldTestRequirementSelect').value = '';
  document.getElementById('fieldTestContentInput').value = '';
  document.getElementById('fieldTestStartTime').value = '';
  document.getElementById('fieldTestEndTime').value = '';
  document.getElementById('fieldTestDurationText').value = '';
  document.getElementById('fieldTestResult').value = 'passed';
  document.getElementById('fieldTestNotes').value = '';
  onFieldTestResultChange();
  renderBugDrafts();
  closeFormModal();
}

export function openFieldTestCreate() {
  cancelFieldTestEdit();
  openFormModal();
}

export async function editFieldTestRecord(recordId) {
  try {
    const row = await (await api(`/field-tests/${recordId}`)).json();
    fillFormFromRecord(row);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载记录详情失败', 'error');
  }
}

export function onFieldTestPurposeChange() {
  const purpose = document.getElementById('fieldTestPurpose')?.value || 'requirement';
  document.getElementById('fieldTestRequirementWrap')?.classList.toggle('hidden', purpose !== 'requirement');
  document.getElementById('fieldTestContentWrap')?.classList.toggle('hidden', purpose !== 'feature');
}

export function onFieldTestResultChange() {
  const result = document.getElementById('fieldTestResult')?.value || 'passed';
  document.getElementById('fieldTestBugWrap')?.classList.toggle('hidden', result !== 'failed');
}

export async function onFieldTestMajorChange() {
  fillMinorByMajor('fieldTestMajorSelect', 'fieldTestMinorSelect', false);
  await loadRequirementOptions();
}

export function onFieldTestFilterMajorChange() {
  fillMinorByMajor('fieldTestFilterMajor', 'fieldTestFilterMinor', true);
  state.page = 1;
  return loadFieldTestBoard();
}

export async function submitFieldTestRecord() {
  try {
    const payload = collectPayload();
    if (state.editingId) {
      await api(`/field-tests/${state.editingId}`, { method: 'PUT', headers: window.H, body: payload });
      window.showMessage && window.showMessage('外业测试记录已更新', 'success');
    } else {
      await api('/field-tests', { method: 'POST', headers: window.H, body: payload });
      window.showMessage && window.showMessage('外业测试记录已创建', 'success');
    }
    cancelFieldTestEdit();
    state.page = 1;
    await loadFieldTestBoard();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '提交失败', 'error');
  }
}

function openDetailPanel() { document.getElementById('fieldTestDetailPanel')?.classList.remove('hidden'); }
export function closeFieldTestDetail() { document.getElementById('fieldTestDetailPanel')?.classList.add('hidden'); }

export async function openFieldTestDetail(recordId) {
  try {
    const row = await (await api(`/field-tests/${recordId}`)).json();
    const canEdit = canEditRow(row);
    const bugHtml = (row.bugs || []).map((b) => `
      <span class="badge" style="margin-right:6px; margin-bottom:6px; display:inline-flex; align-items:center;">
        ${renderBugLink(b)}
        ${canEdit ? `<a href="javascript:void(0)" onclick="unlinkFieldTestBug(${row.id}, ${b.id})" style="margin-left:6px; color:#dc2626; text-decoration:none;">×</a>` : ''}
      </span>
    `).join('') || '<span class="muted">暂无关联 Bug</span>';

    const body = document.getElementById('fieldTestDetailBody');
    const title = document.getElementById('fieldTestDetailTitle');
    if (title) title.innerText = `外业测试详情 #${row.id}`;
    if (body) {
      body.innerHTML = `
        <div class="card" style="box-shadow:none; border:1px solid #e2e8f0;">
          <div><b>大版本：</b>${row.major_version_no || '-'}</div>
          <div><b>小版本：</b>${row.minor_version_no || '-'}</div>
          <div><b>测试目的：</b>${PURPOSE_ZH[row.purpose_type] || row.purpose_type}</div>
          <div><b>关联内容：</b>${purposeText(row)}</div>
          <div><b>开始时间：</b>${row.start_time ? new Date(row.start_time).toLocaleString() : '-'}</div>
          <div><b>结束时间：</b>${row.end_time ? new Date(row.end_time).toLocaleString() : '-'}</div>
          <div><b>本次时长：</b>${row.duration_minutes || 0} 分钟</div>
          <div><b>测试结果：</b>${RESULT_ZH[row.result_status] || row.result_status}</div>
          <div><b>测试人：</b>${row.tester_name || '-'}</div>
          <div><b>备注：</b>${row.notes || '-'}</div>
          <div style="margin-top:8px;"><b>关联 Bug：</b><div style="margin-top:6px;">${bugHtml}</div></div>
          ${canEdit ? `
          <div class="row" style="margin-top:10px;">
            <div class="prefix-input" style="min-width:220px;">
              <span>🔎</span>
              <input id="fieldTestSearchBugInput" placeholder="搜索已有 Bug（支持模糊）" oninput="searchFieldTestBugOptions(${row.id}, this.value)">
            </div>
            <select id="fieldTestLinkBugSelect" style="min-width:200px;"><option value="">选择已有 Bug</option></select>
            <button class="secondary" onclick="linkFieldTestDetailBug(${row.id})">关联已有 Bug</button>
          </div>
          <div class="row" style="margin-top:10px;">
            <div class="prefix-input"><span>b#</span><input id="fieldTestDetailBugInput" inputmode="numeric" oninput="digitsOnly(this)" placeholder="输入 Bug 数字部分"></div>
            <button class="secondary" onclick="addFieldTestDetailBug(${row.id})">新增并关联 Bug</button>
          </div>` : ''}
          <div class="row" style="margin-top:12px;">
            <button class="secondary" onclick="openAuditTimelineModal('field_test', ${row.id}, '外业测试时间线')">时间线</button>
            ${canEdit ? `<button class="secondary" onclick="editFieldTestRecord(${row.id}); closeFieldTestDetail();">编辑记录</button>` : ''}
          </div>
        </div>
      `;
    }
    openDetailPanel();
    if (canEdit) {
      await searchFieldTestBugOptions(row.id, '');
    }
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载详情失败', 'error');
  }
}

export async function unlinkFieldTestBug(recordId, bugTrackingId) {
  if (!confirm('确定解除这个 Bug 关联吗？')) return;
  try {
    await api(`/field-tests/${recordId}/bugs/${bugTrackingId}`, { method: 'DELETE' });
    window.showMessage && window.showMessage('已解除该 Bug 关联', 'success');
    await openFieldTestDetail(recordId);
    await loadFieldTestBoard();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '解除关联失败', 'error');
  }
}

export async function addFieldTestDetailBug(recordId) {
  const input = document.getElementById('fieldTestDetailBugInput');
  const bug = withPrefix('b#', input?.value || '');
  if (!bug) {
    window.showMessage && window.showMessage('请输入 Bug 编号数字部分', 'error');
    return;
  }
  try {
    const res = await (await api(`/field-tests/${recordId}/bugs/add`, { method: 'POST', headers: window.H, body: { bug_id: bug } })).json();
    if (input) input.value = '';
    window.showMessage && window.showMessage(res.message || '操作成功', 'success');
    await openFieldTestDetail(recordId);
    await loadFieldTestBoard();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '新增并关联失败', 'error');
  }
}

export async function searchFieldTestBugOptions(recordId, keyword) {
  try {
    const rows = await (await api(`/field-tests/${recordId}/bug-options?keyword=${encodeURIComponent(keyword || '')}`)).json();
    const sel = document.getElementById('fieldTestLinkBugSelect');
    if (!sel) return;
    sel.innerHTML = '<option value="">选择已有 Bug</option>' + (rows || []).map((b) => `<option value="${b.id}">${b.bug_id} (ID:${b.id})</option>`).join('');
  } catch (err) {
    // 搜索失败不打断详情页交互
  }
}

export async function linkFieldTestDetailBug(recordId) {
  const sel = document.getElementById('fieldTestLinkBugSelect');
  const bugTrackingId = Number(sel?.value || 0);
  if (!bugTrackingId) {
    window.showMessage && window.showMessage('请先选择一个已有 Bug', 'error');
    return;
  }
  try {
    const res = await (await api(`/field-tests/${recordId}/bugs/link`, { method: 'POST', headers: window.H, body: { bug_tracking_id: bugTrackingId } })).json();
    window.showMessage && window.showMessage(res.message || '关联成功', 'success');
    await openFieldTestDetail(recordId);
    await loadFieldTestBoard();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '关联失败', 'error');
  }
}

function updatePagination(total, page, pageSize) {
  const pages = Math.max(1, Math.ceil((total || 0) / (pageSize || 20)));
  const txt = document.getElementById('fieldTestPaginationText');
  if (txt) txt.innerText = `第 ${page} / ${pages} 页，共 ${total || 0} 条`;
  const prev = document.getElementById('fieldTestPrevBtn');
  const next = document.getElementById('fieldTestNextBtn');
  if (prev) prev.disabled = page <= 1;
  if (next) next.disabled = page >= pages;
}

function renderList(rows) {
  const tbody = document.getElementById('fieldTestTable');
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="field-test-empty">暂无外业测试记录</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r) => `
    <tr>
      <td class="field-test-col-date">${formatDateOnly(r.start_time)}</td>
      <td class="field-test-col-version">
        <div class="field-test-cell-main">${r.major_version_no || '-'}</div>
        <div class="field-test-cell-sub" title="${(r.minor_version_no || '-').replace(/"/g, '&quot;')}">${r.minor_version_no || '-'}</div>
      </td>
      <td class="col-status">${purposeBadge(r.purpose_type)}</td>
      <td class="field-test-col-content" title="${purposeText(r).replace(/"/g, '&quot;')}">
        <div class="field-test-cell-main field-test-text-clamp-2">${purposeText(r)}</div>
      </td>
      <td class="field-test-col-time">
        <div class="field-test-cell-main">${formatDateTime(r.start_time)} ~ ${formatDateTime(r.end_time)}</div>
        <div class="field-test-cell-sub">${r.duration_minutes || 0} 分钟</div>
      </td>
      <td class="col-status">${resultBadge(r.result_status)}</td>
      <td class="field-test-col-bug">${r.bug_count || 0}</td>
      <td class="field-test-col-tester">${r.tester_name || '-'}</td>
      <td class="field-test-col-notes" title="${(r.notes || '').replace(/"/g, '&quot;')}">
        <div class="field-test-text-clamp-2">${r.notes || '-'}</div>
      </td>
      <td class="col-actions">
        <button class="secondary" onclick="openFieldTestDetail(${r.id})">详情</button>
        ${canEditRow(r) ? `<button class="secondary" onclick="editFieldTestRecord(${r.id})" style="margin-left:6px;">编辑</button>` : ''}
      </td>
    </tr>
  `).join('');
}

function ensureInited() {
  const majorSel = document.getElementById('fieldTestMajorSelect');
  const filterMajorSel = document.getElementById('fieldTestFilterMajor');
  const testerWrap = document.getElementById('fieldTestFilterTesterWrap');
  const testerSel = document.getElementById('fieldTestFilterTester');
  const prevMajor = majorSel?.value || '';
  const prevFilterMajor = filterMajorSel?.value || '';
  const prevTester = testerSel?.value || '';

  if (majorSel) {
    majorSel.innerHTML = majorOptionsHtml(false);
    if (prevMajor) majorSel.value = prevMajor;
    if (!majorSel.value && majorSel.options.length > 0) majorSel.value = majorSel.options[0].value;
    fillMinorByMajor('fieldTestMajorSelect', 'fieldTestMinorSelect', false);
  }
  if (filterMajorSel) {
    filterMajorSel.innerHTML = majorOptionsHtml(true);
    if (prevFilterMajor) filterMajorSel.value = prevFilterMajor;
    fillMinorByMajor('fieldTestFilterMajor', 'fieldTestFilterMinor', true);
  }

  if (isAdmin()) {
    testerWrap?.classList.remove('hidden');
    const users = window.users || [];
    if (testerSel) {
      testerSel.innerHTML = `<option value="">全部</option>${users.map((u) => `<option value="${u.id}">${u.display_name || u.username}</option>`).join('')}`;
      if (prevTester) testerSel.value = prevTester;
    }
  } else {
    testerWrap?.classList.add('hidden');
  }

  if (state.inited) return;

  ['fieldTestStartTime', 'fieldTestEndTime'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => durationMinutes());
  });
  const filterIds = ['fieldTestFilterMinor', 'fieldTestFilterPurpose', 'fieldTestFilterTester', 'fieldTestFilterKeyword', 'fieldTestPageSize'];
  filterIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => { state.page = 1; loadFieldTestBoard(); });
    if (id === 'fieldTestFilterKeyword' && el) el.addEventListener('input', () => { state.page = 1; loadFieldTestBoard(); });
  });

  onFieldTestPurposeChange();
  onFieldTestResultChange();
  renderBugDrafts();
  loadRequirementOptions();
  state.inited = true;
}

export async function loadFieldTestBoard(page = null) {
  ensureInited();
  if (page) state.page = Math.max(1, Number(page || 1));

  const majorId = Number(document.getElementById('fieldTestFilterMajor')?.value || 0);
  const minorId = Number(document.getElementById('fieldTestFilterMinor')?.value || 0);
  const purpose = document.getElementById('fieldTestFilterPurpose')?.value || '';
  const testerId = Number(document.getElementById('fieldTestFilterTester')?.value || 0);
  const keyword = (document.getElementById('fieldTestFilterKeyword')?.value || '').trim();
  const pageSize = Number(document.getElementById('fieldTestPageSize')?.value || state.pageSize || 20);
  state.pageSize = pageSize;

  const sid = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  const params = new URLSearchParams();
  if (majorId) params.set('major_version_id', String(majorId));
  if (minorId) params.set('minor_version_id', String(minorId));
  if (purpose) params.set('purpose_type', purpose);
  if (testerId) params.set('tester_id', String(testerId));
  if (keyword) params.set('keyword', keyword);
  if (sid) params.set('software_id', String(sid));
  params.set('page', String(state.page));
  params.set('page_size', String(pageSize));
  params.set('sort_by', 'start_time');
  params.set('sort_order', 'desc');

  const data = await (await api(`/field-tests/paged?${params.toString()}`)).json();
  renderList(data.items || []);
  updatePagination(data.total || 0, data.page || state.page, data.page_size || pageSize);
}

export async function nextFieldTestPage() {
  state.page += 1;
  await loadFieldTestBoard(state.page);
}

export async function prevFieldTestPage() {
  if (state.page <= 1) return;
  state.page -= 1;
  await loadFieldTestBoard(state.page);
}

window.OmniQAFieldTestTab = {
  loadFieldTestBoard,
  onFieldTestMajorChange,
  onFieldTestFilterMajorChange,
  onFieldTestPurposeChange,
  onFieldTestResultChange,
  addFieldTestBug,
  removeFieldTestBug,
  submitFieldTestRecord,
  editFieldTestRecord,
  cancelFieldTestEdit,
  openFieldTestDetail,
  closeFieldTestDetail,
  unlinkFieldTestBug,
  addFieldTestDetailBug,
  searchFieldTestBugOptions,
  linkFieldTestDetailBug,
  nextFieldTestPage,
  prevFieldTestPage,
  openFieldTestCreate,
};
