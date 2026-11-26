import { api } from '../api.js';
import { withPrefix, renderBugLink, escapeHtml } from '../utils.js';
import { confirmDialog } from '../components/dialog.js?v=20261126-zai-1';
import { openDrawer, getDrawer, closeDrawer } from '../components/side-drawer.js?v=20261126-zai-1';

const PURPOSE_ZH = { requirement: '需求测试', feature: '功能测试' };
const RESULT_ZH = { passed: '测试通过', failed: '测试未通过' };

const state = {
  editingId: null,
  bugDrafts: [],
  inited: false,
  page: 1,
  pageSize: 20,
  unseenTopNewCount: 0,
};
let fieldTestSseBound = false;
let fieldTestSseRefreshTimer = null;

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
  return `<span class="st-pill st-pill--${passed ? 'green' : 'red'}">${RESULT_ZH[status] || status}</span>`;
}

function purposeBadge(status) {
  const isReq = status === 'requirement';
  return `<span class="st-pill st-pill--soft st-pill--${isReq ? 'blue' : 'amber'}">${PURPOSE_ZH[status] || status}</span>`;
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

function formatTimeHM(v) {
  if (!v) return '-';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return '-';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function formatTimeRange(startTime, endTime) {
  if (!startTime && !endTime) return '-';
  if (!startTime) return `- ~ ${formatDateTime(endTime)}`;
  if (!endTime) return `${formatDateTime(startTime)} ~ -`;
  const startDate = formatDateOnly(startTime);
  const endDate = formatDateOnly(endTime);
  if (startDate === endDate) return `${startDate} ${formatTimeHM(startTime)} ~ ${formatTimeHM(endTime)}`;
  return `${formatDateTime(startTime)} ~ ${formatDateTime(endTime)}`;
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

function openDetailPanel() { /* 详情改为右侧抽屉（side-drawer），保留函数名兼容旧调用 */ }
export function closeFieldTestDetail() { closeDrawer('field-test-detail'); }

const esc = (v) => escapeHtml(v == null ? '' : String(v));
const ic = (name, cls = 'ui-ic') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function durationText(min) {
  const m = Number(min || 0);
  if (m < 60) return `${m} 分钟`;
  return `${Math.floor(m / 60)} 小时${m % 60 ? ` ${m % 60} 分` : ''}`;
}

function personChip(name) {
  const n = String(name || '').trim();
  return `<span class="fbk-avatar${n ? '' : ' fbk-avatar--empty'}" aria-hidden="true">${esc(n ? n.slice(0, 1).toUpperCase() : '?')}</span><span>${esc(n || '-')}</span>`;
}

function markFieldTestActive(id) {
  document.querySelectorAll('#fieldTestTable .field-test-row-card').forEach((el) => {
    el.classList.toggle('is-active', Number(el.dataset.fieldTestId) === Number(id));
  });
}

export async function openFieldTestDetail(recordId) {
  try {
    const row = await (await api(`/field-tests/${recordId}`)).json();
    const canEdit = canEditRow(row);
    const bugs = row.bugs || [];
    const bugHtml = bugs.map((b) => {
      const ztBugId = (b.bug_id || '').replace(/\D/g, '');
      const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}"></span>` : '';
      return `<span class="fbk-bug">${renderBugLink(b)}${ztSlot}
        ${canEdit ? `<button type="button" class="fbk-x" onclick="unlinkFieldTestBug(${row.id}, ${b.id})" aria-label="解除关联 ${esc(b.bug_id)}" title="解除关联">×</button>` : ''}</span>`;
    }).join('');
    const sameDay = formatDateOnly(row.start_time) === formatDateOnly(row.end_time);
    const prev = getDrawer('field-test-detail');
    const d = openDrawer({
      key: 'field-test-detail',
      size: 'lg',
      keepScroll: !!prev && Number(prev.root.dataset.recordId) === Number(row.id),
      head: `
        <div class="sd-eyebrow"><span class="fbk-no">#${row.id}</span>${purposeBadge(row.purpose_type)}${resultBadge(row.result_status)}</div>
        <h3 class="sd-title sd-title--clamp" title="${esc(purposeText(row))}">${esc(purposeText(row))}</h3>
        <div class="sd-sub">${esc(row.tester_name || '-')} 记录于 ${esc(formatDateTime(row.created_at))}</div>`,
      tools: `<button type="button" class="secondary sd-tool" onclick="openAuditTimelineModal('field_test', ${row.id}, '外业测试时间线')">${ic('clock')}时间线</button>
        ${canEdit ? `<button type="button" class="sd-tool" onclick="closeFieldTestDetail(); editFieldTestRecord(${row.id});">编辑记录</button>` : ''}`,
      body: `
        <div class="ftd-time">
          <div class="ftd-time-main">
            <span class="ftd-time-label">测试时段</span>
            <strong>${esc(formatDateOnly(row.start_time))} ${esc(formatTimeHM(row.start_time))}<span class="ftd-arrow">→</span>${sameDay ? '' : esc(formatDateOnly(row.end_time)) + ' '}${esc(formatTimeHM(row.end_time))}</strong>
          </div>
          <span class="ftd-dur">${ic('timer')}${esc(durationText(row.duration_minutes))}</span>
        </div>

        <dl class="sd-facts">
          <div><dt>大版本</dt><dd>${esc(row.major_version_no || '-')}</dd></div>
          <div><dt>小版本</dt><dd>${esc(row.minor_version_no || '-')}</dd></div>
          <div><dt>测试目的</dt><dd>${esc(PURPOSE_ZH[row.purpose_type] || row.purpose_type)}</dd></div>
          <div><dt>测试人</dt><dd>${personChip(row.tester_name)}</dd></div>
          <div><dt>记录时间</dt><dd>${esc(formatDateTime(row.created_at))}</dd></div>
          <div><dt>最近更新</dt><dd>${esc(formatDateTime(row.updated_at))}</dd></div>
        </dl>

        <section class="sd-sec">
          <h4 class="sd-sec-title">${ic('field')}${row.purpose_type === 'requirement' ? '关联需求' : '测试内容'}</h4>
          <div class="fbk-content">${esc(purposeText(row))}</div>
        </section>

        <section class="sd-sec">
          <h4 class="sd-sec-title">${ic('feedback')}备注</h4>
          ${row.notes ? `<div class="fbk-content">${esc(row.notes)}</div>` : '<span class="sd-empty">未填写备注</span>'}
        </section>

        <section class="sd-sec">
          <h4 class="sd-sec-title">${ic('bug')}关联 Bug <span class="sd-count">${bugs.length}</span></h4>
          <div class="fbk-bugs">${bugHtml || '<span class="sd-empty">暂无关联 Bug</span>'}</div>
          ${canEdit ? `<div class="sd-subgrid">
            <div class="sd-field"><span class="sd-label">关联已有 Bug</span>
              <div class="sd-inline sd-inline--tight">
                <span class="sd-search">${ic('search')}<input id="fieldTestSearchBugInput" placeholder="搜索已有 Bug（支持模糊）" oninput="searchFieldTestBugOptions(${row.id}, this.value)"></span>
                <select id="fieldTestLinkBugSelect"><option value="">选择已有 Bug</option></select>
                <button type="button" class="secondary" onclick="linkFieldTestDetailBug(${row.id})">关联</button>
              </div>
            </div>
            <div class="sd-field"><span class="sd-label">新增并关联 Bug</span>
              <div class="sd-inline sd-inline--tight">
                <span class="sd-prefix"><span>b#</span><input id="fieldTestDetailBugInput" inputmode="numeric" oninput="digitsOnly(this)" placeholder="Bug 数字编号"></span>
                <button type="button" class="secondary" onclick="addFieldTestDetailBug(${row.id})">新增并关联</button>
              </div>
            </div>
          </div>` : ''}
        </section>`,
      onClose: () => { markFieldTestActive(0); },
    });
    d.root.dataset.recordId = String(row.id);
    markFieldTestActive(row.id);
    window.OmniQAZentao?.hydrateContainer?.(d.body);
    if (canEdit) {
      await searchFieldTestBugOptions(row.id, '');
    }
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载详情失败', 'error');
  }
}

export async function unlinkFieldTestBug(recordId, bugTrackingId) {
  if (!(await confirmDialog({ icon: 'i-unlink', title: '解除 Bug 关联', message: '只解除外业记录与该 Bug 的关联，不会删除禅道里的 Bug。', okText: '解除关联' }))) return;
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
  const list = document.getElementById('fieldTestTable');
  if (!list) return;
  if (!rows.length) {
    list.innerHTML = `<div class="fbk-empty field-test-empty">${ic('field', 'fbk-empty-ic')}<strong>暂无外业测试记录</strong><span>调整上方筛选条件，或点击「新增测试记录」。</span></div>`;
    return;
  }
  const activeId = Number(getDrawer('field-test-detail')?.root.dataset.recordId || 0);
  list.innerHTML = rows.map((r) => {
    const d = new Date(r.start_time);
    const valid = !Number.isNaN(d.getTime());
    const bugs = Number(r.bug_count || 0);
    return `
    <div class="ftl-row field-test-row-card${activeId === Number(r.id) ? ' is-active' : ''}" role="listitem" data-field-test-id="${r.id}">
      <button type="button" class="fbk-row-hit" onclick="openFieldTestDetail(${r.id})" aria-label="查看外业测试记录 #${r.id} 详情"></button>
      <div class="ftl-date">${valid ? `<b>${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}</b><span>${d.getFullYear()} · ${WEEK[d.getDay()]}</span>` : '<b>-</b>'}</div>
      <div class="ftl-main">
        <div class="ftl-title">${purposeBadge(r.purpose_type)}<span title="${esc(purposeText(r))}">${esc(purposeText(r))}</span></div>
        <div class="fbk-meta">
          <span class="fbk-ver">${esc(r.major_version_no || '-')} / ${esc(r.minor_version_no || '-')}</span>
          ${r.notes ? `<span class="ftl-note" title="${esc(r.notes)}">${esc(r.notes)}</span>` : ''}
        </div>
      </div>
      <div class="ftl-time"><b>${esc(formatTimeHM(r.start_time))} – ${esc(formatTimeHM(r.end_time))}</b><span>${esc(durationText(r.duration_minutes))}</span></div>
      <div class="ftl-result">${resultBadge(r.result_status)}<span class="ftl-bugs${bugs ? ' has-bugs' : ''}" title="关联 Bug 数">${ic('bug')}${bugs}</span></div>
      <div class="fbk-assignee">${personChip(r.tester_name)}</div>
      <div class="ftl-actions">${canEditRow(r) ? `<button type="button" class="secondary ftl-edit" onclick="editFieldTestRecord(${r.id})" title="编辑记录">编辑</button>` : ''}</div>
      <svg class="fbk-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
    </div>`;
  }).join('');
}

function isFieldTestNearTop() {
  const first = document.querySelector('#fieldTestTable .field-test-row-card[data-field-test-id]');
  if (!first) return true;
  const rect = first.getBoundingClientRect();
  return rect.top >= 0 && rect.top <= Math.max(240, Math.round(window.innerHeight * 0.45));
}

function updateFieldTestTopNotice() {
  const el = document.getElementById('fieldTestTopNotice');
  if (!el) return;
  if (state.unseenTopNewCount > 0) {
    el.innerText = `有 ${state.unseenTopNewCount} 条新增外业测试记录`;
    el.classList.remove('hidden');
  } else {
    el.innerText = '有 0 条新增外业测试记录';
    el.classList.add('hidden');
  }
}

function clearFieldTestTopNotice() {
  state.unseenTopNewCount = 0;
  updateFieldTestTopNotice();
}

function pulseFieldTestRow(id, tone = 'green') {
  if (!window.OmniQASSE || typeof window.OmniQASSE.pulseBoundaryGlow !== 'function') return;
  const el = document.querySelector(`.field-test-row-card[data-field-test-id='${Number(id)}']`);
  if (!el) return;
  window.OmniQASSE.pulseBoundaryGlow(el, tone);
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
    // 回填记忆的筛选大版本，再据此填充并回填小版本
    window.SelectMemory && window.SelectMemory.apply('fieldTestFilterMajor');
    fillMinorByMajor('fieldTestFilterMajor', 'fieldTestFilterMinor', true);
    window.SelectMemory && window.SelectMemory.apply('fieldTestFilterMinor');
  }

  if (isAdmin()) {
    testerWrap?.classList.remove('hidden');
    const users = window.users || [];
    if (testerSel) {
      testerSel.innerHTML = `<option value="">全部</option>${users.map((u) => `<option value="${u.id}">${u.display_name || u.username}</option>`).join('')}`;
      if (prevTester) testerSel.value = prevTester;
      window.SelectMemory && window.SelectMemory.apply('fieldTestFilterTester');
    }
  } else {
    testerWrap?.classList.add('hidden');
  }
  // 静态筛选项（测试目的、每页条数）的记忆回填
  window.SelectMemory && window.SelectMemory.applyMany(['fieldTestFilterPurpose', 'fieldTestPageSize']);

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
  clearFieldTestTopNotice();
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

// ── 表单内搜索已有 Bug（新增/编辑时使用，复用 feedbacks/bug-options 接口）──────
export async function searchFieldTestFormBugOptions(keyword) {
  try {
    const sid = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
    const params = new URLSearchParams();
    if (keyword) params.set('keyword', keyword);
    if (sid) params.set('software_id', String(sid));
    params.set('limit', '30');
    const rows = await (await api(`/feedbacks/bug-options?${params.toString()}`)).json();
    const sel = document.getElementById('fieldTestFormBugSearchSelect');
    if (!sel) return;
    sel.innerHTML = '<option value="">选择已有 Bug</option>' + (rows || []).map((b) => {
      const title = b.zentao_bug_title ? ` - ${b.zentao_bug_title.slice(0, 28)}` : '';
      return `<option value="${b.bug_id}">${b.bug_id}${title}</option>`;
    }).join('');
  } catch (_) {
    // 搜索失败不打断表单操作
  }
}

export function addFieldTestSearchedBug() {
  const sel = document.getElementById('fieldTestFormBugSearchSelect');
  const bugId = sel?.value?.trim();
  if (!bugId) { window.showMessage && window.showMessage('请先从下拉列表选择一个 Bug', 'error'); return; }
  if (state.bugDrafts.includes(bugId)) { window.showMessage && window.showMessage('该 Bug 已添加', 'error'); return; }
  state.bugDrafts.push(bugId);
  if (sel) sel.value = '';
  renderBugDrafts();
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
  searchFieldTestFormBugOptions,
  addFieldTestSearchedBug,
  nextFieldTestPage,
  prevFieldTestPage,
  openFieldTestCreate,
  revealFieldTestRealtimeNew,
};

export async function revealFieldTestRealtimeNew() {
  clearFieldTestTopNotice();
  state.page = 1;
  await loadFieldTestBoard(1);
  const first = document.querySelector('#fieldTestTable .field-test-row-card[data-field-test-id]');
  if (first && window.OmniQASSE && typeof window.OmniQASSE.pulseBoundaryGlow === 'function') {
    window.OmniQASSE.pulseBoundaryGlow(first, 'purple');
  }
}

function bindFieldTestSSE() {
  if (fieldTestSseBound) return;
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;

  const scheduleReload = (targetId, tone) => {
    if (fieldTestSseRefreshTimer) clearTimeout(fieldTestSseRefreshTimer);
    fieldTestSseRefreshTimer = setTimeout(() => {
      const t = document.getElementById('tab-field-test');
      if (!t || t.classList.contains('hidden')) return;
      loadFieldTestBoard().then(() => {
        if (targetId > 0) pulseFieldTestRow(targetId, tone);
      }).catch(() => {});
    }, 380);
  };

  window.OmniQASSE.subscribe('activity_created', ({ payload }) => {
    const action = String(payload?.action || '');
    const targetType = String(payload?.target_type || '');
    if (targetType !== 'field_test' && !action.startsWith('field_test.')) return;
    const tab = document.getElementById('tab-field-test');
    if (!tab || tab.classList.contains('hidden')) return;
    const targetId = Number(payload?.target_id || 0);
    if (targetId > 0) {
      const existed = document.querySelector(`.field-test-row-card[data-field-test-id='${targetId}']`);
      if (existed) {
        pulseFieldTestRow(targetId, action === 'field_test.create' ? 'purple' : 'green');
        if (action !== 'field_test.create') return;
      }
    }
    if (!isFieldTestNearTop() && action === 'field_test.create') {
      state.unseenTopNewCount += 1;
      updateFieldTestTopNotice();
      return;
    }
    scheduleReload(targetId, action === 'field_test.create' ? 'purple' : 'green');
  });
  fieldTestSseBound = true;
}

bindFieldTestSSE();
