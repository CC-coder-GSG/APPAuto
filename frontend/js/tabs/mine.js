import { api } from '../api.js';
import { state } from '../state.js';
import { closeModal, openModal } from '../components/modal.js';
import { escapeHtml, renderBugLink, renderCaseLink, renderPreviewBtn } from '../utils.js';

const RESULT_OPTIONS = [
  { value: 'passed', label: '通过' },
  { value: 'failed', label: '失败' },
  { value: 'blocked', label: '阻塞' },
  { value: 'partial', label: '部分完成' },
  { value: 'untested', label: '未测试' },
];

const modalState = {
  reqId: null,
  checkboxEl: null,
};

const notesModalState = {
  reqId: null,
};

const bugResultModalState = {
  bugId: null,
  zentaoBugId: '',
  wasClosed: false,
};
let mineSseBound = false;
let minePreflightPromise = null;
let minePreflightKey = '';
let minePreflightAt = 0;

function getFoldStorageKey() {
  const uid = state.currentUser?.id || window.currentUser?.id || 'anonymous';
  return `omniqa_mine_fold_state_v1_${uid}`;
}

function getFoldStateMap() {
  try {
    const raw = localStorage.getItem(getFoldStorageKey());
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function setFoldStateMap(map) {
  localStorage.setItem(getFoldStorageKey(), JSON.stringify(map || {}));
}

function getMode() {
  return document.getElementById('mineDisplayMode')?.value || 'version';
}

function getMinorText(minorId) {
  const sel = document.getElementById('mineMinorSelect');
  if (!sel) return String(minorId || '未选择');
  const opt = Array.from(sel.options || []).find((o) => Number(o.value || 0) === Number(minorId || 0));
  return opt?.text || String(minorId || '未选择');
}

function renderAutoLinkedBadge(label = '自动归集') {
  return `<span class="badge" style="background:#ecfeff; color:#0f766e; border:1px solid #99f6e4; margin-left:8px; padding:2px 6px;">${label}</span>`;
}

async function preflightWorkbenchData(softwareId) {
  if (!softwareId) return;
  const key = String(softwareId);
  const now = Date.now();
  if (minePreflightPromise && minePreflightKey === key) {
    return minePreflightPromise;
  }
  if (minePreflightKey === key && now - minePreflightAt < 30000) {
    return;
  }
  minePreflightKey = key;
  minePreflightPromise = api('/workbench/preflight-refresh', {
    method: 'POST',
    headers: window.H,
    body: {
      software_id: softwareId,
      include_bugs: true,
      include_testcases: true,
      force: false,
    },
  }).then(() => {
    minePreflightAt = Date.now();
  }).catch((err) => {
    console.warn('workbench preflight refresh failed', err);
  }).finally(() => {
    minePreflightPromise = null;
  });
  return minePreflightPromise;
}

// DB 缓存的禅道状态/指派人，用于预填 zt-bug-slot（先显示，hydrator 再覆盖更新）。
// 视觉与 zentao-hydrator 的徽章保持一致。
const ZT_BUG_STATUS = {
  active: { zh: '激活', bg: '#fee2e2', color: '#b91c1c' },
  resolved: { zh: '已解决', bg: '#dcfce7', color: '#166534' },
  closed: { zh: '已关闭', bg: '#f1f5f9', color: '#475569' },
};
function renderCachedBugSlot(bug) {
  const status = (bug.zentao_live_status || '').toLowerCase();
  const meta = ZT_BUG_STATUS[status];
  const statusBadge = meta
    ? `<span style="background:${meta.bg};color:${meta.color};padding:1px 5px;border-radius:4px;font-size:10px;font-weight:600;">${meta.zh}</span>`
    : '';
  const assignee = bug.zentao_assigned_to_name
    ? `<span style="color:#0ea5e9;font-size:10px;margin-left:3px;">→${escapeHtml(bug.zentao_assigned_to_name)}</span>`
    : '';
  return `${statusBadge}${assignee}`;
}

function renderBugChip(req, bug) {
  const immutable = req.test_completed || bug.auto_linked;
  const dBadge = bug.dispatched_to_name
    ? `<span style="color:#ea580c; background:#ffedd5; padding:1px 4px; border-radius:4px; font-size:11px; margin-left:6px;">🪂已特派给:${bug.dispatched_to_name}</span>`
    : '';
  const verText = bug.fixed_minor_version_no
    ? `<span style="color:#16a34a; font-size:11px; margin-left:4px;">(✅解决于: 🏷️${bug.fixed_minor_version_no})</span>`
    : `<span style="color:#94a3b8; font-size:11px; margin-left:4px;">(发现于: 🏷️${bug.found_minor_version_no || '未知'})</span>`;
  const ztBugId = (bug.bug_id || '').replace(/\D/g, '');
  // 用 DB 缓存的状态/指派人预填，先显示出来；hydrator 拉到最新后再覆盖。
  const cachedSlot = renderCachedBugSlot(bug);
  const ztSlot = ztBugId
    ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}"${cachedSlot ? ' data-zt-prefilled="1"' : ''} style="margin-left:4px;">${cachedSlot}</span>`
    : '';
  const autoBadge = bug.auto_linked ? renderAutoLinkedBadge('自动归集Bug') : '';
  const previewBugBtn = renderPreviewBtn('bug', ztBugId);
  // 修复结果/闭环确认入口：点击弹窗完成「修复通过 + 确认闭环 + 保存记录」，不占用标签空间
  const closedHint = bug.closed
    ? '<span title="已闭环" style="color:#16a34a; margin-left:4px;">✅</span>'
    : '';
  const resultBtn = `<a href="javascript:void(0)" title="修复结果 / 闭环确认" onclick="openBugResultModal(${bug.id}, '${bug.bug_id}', ${bug.closed ? 'true' : 'false'}, '${bug.zentao_bug_id || ''}')" style="color:#16a34a; margin-left:6px; text-decoration:none;">🛠️</a>`;

  return `<span class="badge" style="background:#f1f5f9; border:1px solid #cbd5e1; padding:2px 6px; margin-right:6px; border-radius:4px; display:inline-block; margin-bottom:4px;">
      ${renderBugLink(bug)} ${ztSlot} ${previewBugBtn} ${verText} ${dBadge} ${autoBadge}${closedHint}
      ${resultBtn}
      <a href="javascript:void(0)" title="编辑" onclick="${immutable ? 'return false;' : `editWorkbenchBug(${bug.id}, '${bug.bug_id}')`}" style="color:${immutable ? '#94a3b8' : '#3b82f6'}; margin-left:4px; text-decoration:none;">✎</a>
      <a href="javascript:void(0)" title="删除" onclick="${immutable ? 'return false;' : `removeWorkbenchBug(${bug.id})`}" style="color:${immutable ? '#94a3b8' : '#ef4444'}; margin-left:2px; text-decoration:none;">×</a>
  </span>`;
}

function initTestExecutionModal() {
  const resultSel = document.getElementById('mineTestExecResult');
  if (resultSel && !resultSel.dataset.initialized) {
    resultSel.innerHTML = RESULT_OPTIONS.map((r) => `<option value="${r.value}">${r.label}</option>`).join('');
    resultSel.value = 'passed';
    resultSel.dataset.initialized = '1';
  }
}

function getRequirementById(reqId) {
  return (state.currentMineData || []).find((r) => Number(r.id) === Number(reqId)) || null;
}

function getMinorOptionsByMajor(majorId) {
  const allVersions = window.versions || [];
  return allVersions.filter((v) => v.version_type === 'minor' && Number(v.parent_id) === Number(majorId));
}

function syncModalMinorSelect(reqId) {
  const modalMinorSel = document.getElementById('mineTestExecMinorSelect');
  if (!modalMinorSel) return;

  const req = getRequirementById(reqId);
  const reqMajorId = Number(req?.major_version_id || 0);
  const linkedMinors = reqMajorId ? getMinorOptionsByMajor(reqMajorId) : [];
  if (linkedMinors.length === 0) {
    modalMinorSel.innerHTML = "<option value=''>暂无子版本</option>";
    return;
  }

  modalMinorSel.innerHTML = linkedMinors
    .map((o) => `<option value="${o.id}">${o.version_no}</option>`)
    .join('');

  const pageMinorSel = document.getElementById('mineMinorSelect');
  if (pageMinorSel.value) {
    modalMinorSel.value = pageMinorSel.value;
  }

  if (!modalMinorSel.value) {
    const firstValid = Array.from(modalMinorSel.options || []).find((o) => Number(o.value || 0) > 0);
    if (firstValid) modalMinorSel.value = firstValid.value;
  }
}

function openTestExecutionModal(reqId, checkboxEl) {
  initTestExecutionModal();
  syncModalMinorSelect(reqId);
  const minorId = Number(document.getElementById('mineTestExecMinorSelect')?.value || 0);
  const modal = document.getElementById('mineTestExecModal');
  const minorSel = document.getElementById('mineTestExecMinorSelect');
  const noteInput = document.getElementById('mineTestExecNotes');
  const resultSel = document.getElementById('mineTestExecResult');
  if (!modal || !minorSel || !noteInput || !resultSel) return;
  if (!minorId) {
    if (checkboxEl) checkboxEl.checked = false;
    window.showMessage && window.showMessage('请先在页面顶部选择当前大版本对应的小版本', 'error');
    return;
  }

  modalState.reqId = reqId;
  modalState.checkboxEl = checkboxEl || null;

  noteInput.value = '';
  resultSel.value = 'passed';
  openModal(modal);
}

export function closeMineTestExecutionModal() {
  const modal = document.getElementById('mineTestExecModal');
  if (modal) closeModal(modal);
  if (modalState.checkboxEl) modalState.checkboxEl.checked = false;
  modalState.reqId = null;
  modalState.checkboxEl = null;
}

export function closeReqTestNotesModal() {
  const modal = document.getElementById('mineReqNotesModal');
  if (modal) closeModal(modal);
  notesModalState.reqId = null;
}

export function openReqTestNotesModal(reqId) {
  const req = getRequirementById(reqId);
  if (!req) {
    window.showMessage && window.showMessage('需求不存在', 'error');
    return;
  }
  const modal = document.getElementById('mineReqNotesModal');
  const titleEl = document.getElementById('mineReqNotesTitle');
  const textarea = document.getElementById('mineReqNotesText');
  const metaEl = document.getElementById('mineReqNotesMeta');
  if (!modal || !titleEl || !textarea || !metaEl) return;

  notesModalState.reqId = reqId;
  titleEl.innerText = `需求测试要点 - ${req.zentao_req_id} ${req.title}`;
  textarea.value = req.test_notes || '';

  if (req.test_notes_updated_at || req.test_notes_updated_by_name) {
    const t = req.test_notes_updated_at ? new Date(req.test_notes_updated_at).toLocaleString() : '未知时间';
    const u = req.test_notes_updated_by_name || '未知';
    metaEl.innerText = `最后更新：${u} ${t}`;
  } else {
    metaEl.innerText = '尚未填写测试要点';
  }

  openModal(modal);
}

export async function saveReqTestNotes() {
  const reqId = Number(notesModalState.reqId || 0);
  if (!reqId) {
    closeReqTestNotesModal();
    return;
  }
  const textarea = document.getElementById('mineReqNotesText');
  const text = (textarea?.value || '').trim();
  try {
    await api(`/requirements/${reqId}/test-notes`, {
      method: 'PUT',
      headers: window.H,
      body: { test_notes: text || null },
    });
    window.showMessage && window.showMessage('测试要点保存成功', 'success');
    closeReqTestNotesModal();
    await loadMyWorkbench();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '保存测试要点失败', 'error');
  }
}

function toggleBugResultCloseComment() {
  const wrap = document.getElementById('mineBugResultCommentWrap');
  if (!wrap) return;
  const done = !!document.getElementById('mineBugResultDone')?.checked;
  const isZentao = !!bugResultModalState.zentaoBugId;
  // 仅在「禅道 Bug + 勾选闭环」时需要填写闭环说明（会同步写入禅道备注）
  wrap.style.display = done && isZentao ? '' : 'none';
}

export function openBugResultModal(bugId, bugIdText, closed, zentaoBugId) {
  const modal = document.getElementById('mineBugResultModal');
  if (!modal) return;
  const minorId = Number(document.getElementById('mineMinorSelect')?.value || 0);
  if (!minorId) {
    window.showMessage && window.showMessage('请先在页面顶部选择【当前复测发包(小版本)】环境！', 'error');
    return;
  }
  bugResultModalState.bugId = bugId;
  bugResultModalState.zentaoBugId = String(zentaoBugId || '');
  bugResultModalState.wasClosed = !!closed;
  const titleEl = document.getElementById('mineBugResultTitle');
  const minorEl = document.getElementById('mineBugResultMinor');
  const resSel = document.getElementById('mineBugResultResolution');
  const doneChk = document.getElementById('mineBugResultDone');
  const commentEl = document.getElementById('mineBugResultComment');
  const hintEl = document.getElementById('mineBugResultZentaoHint');
  if (titleEl) titleEl.innerText = `Bug 修复结果 / 闭环确认 - ${bugIdText}`;
  if (minorEl) minorEl.innerText = getMinorText(minorId);
  if (resSel) resSel.value = 'fixed';
  if (doneChk) doneChk.checked = !!closed;
  if (commentEl) commentEl.value = '';
  if (hintEl) {
    hintEl.innerText = bugResultModalState.zentaoBugId
      ? '该 Bug 关联禅道，勾选「确认闭环」保存时会同步关闭禅道 Bug（需禅道中已是“已解决”状态）。'
      : '该 Bug 为本地 Bug，保存仅记录闭环结果。';
  }
  toggleBugResultCloseComment();
  openModal(modal);
}

export function closeBugResultModal() {
  const modal = document.getElementById('mineBugResultModal');
  if (modal) closeModal(modal);
  bugResultModalState.bugId = null;
  bugResultModalState.zentaoBugId = '';
  bugResultModalState.wasClosed = false;
}

export async function confirmBugResultModal() {
  const bugId = Number(bugResultModalState.bugId || 0);
  if (!bugId) {
    closeBugResultModal();
    return;
  }
  const minorId = Number(document.getElementById('mineMinorSelect')?.value || 0);
  if (!minorId) {
    window.showMessage && window.showMessage('请先在页面顶部选择【当前复测发包(小版本)】环境！', 'error');
    return;
  }
  const resolution = document.getElementById('mineBugResultResolution')?.value || 'fixed';
  const done = !!document.getElementById('mineBugResultDone')?.checked;
  const comment = (document.getElementById('mineBugResultComment')?.value || '').trim();
  const zentaoBugId = bugResultModalState.zentaoBugId;
  const isZentaoBug = !!zentaoBugId;

  try {
    // 勾选闭环且是禅道 Bug：先同步关闭禅道（与测试工作台一致）
    if (isZentaoBug && done) {
      const ztId = Number(zentaoBugId);
      if (ztId) {
        try {
          await api(`/zentao/bugs/${ztId}/close`, {
            method: 'POST',
            headers: window.H,
            body: { comment },
          });
        } catch (err) {
          window.showMessage && window.showMessage(err.message || '禅道关闭失败，本地未保存闭环结果', 'error');
          return;
        }
      }
    }

    // 取消闭环且禅道侧已关闭：必须先在禅道重新激活，再落本地未闭环
    if (isZentaoBug && !done && bugResultModalState.wasClosed) {
      const ztId = Number(zentaoBugId);
      if (ztId) {
        const reactivate = window.OmniQAOverallTestTab?.openS5ReactivateModalAsync;
        if (typeof reactivate === 'function') {
          window.showMessage && window.showMessage('取消闭环需要先在禅道重新激活该 Bug', 'info');
          const activated = await reactivate(bugId, ztId);
          if (!activated) {
            window.showMessage && window.showMessage('未完成重新激活，已取消「取消闭环」操作', 'error');
            return;
          }
        }
      }
    }

    await api(`/overall-test/bugs/${bugId}/result`, {
      method: 'PUT',
      headers: window.H,
      body: { minor_version_id: minorId, test_done: done, newly_found_bug_id: null, resolution },
    });
    window.showMessage && window.showMessage('Bug 修复结果已保存并同步至总盘', 'success');
    closeBugResultModal();
    await loadMyWorkbench();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '保存失败，请稍后重试', 'error');
  }
}

export async function submitTestExecution(reqId, payload) {
  return api(`/requirements/${reqId}/test-execution`, {
    method: 'PUT',
    headers: window.H,
    body: payload,
  });
}

export async function confirmMineTestExecutionModal() {
  const reqId = Number(modalState.reqId || 0);
  if (!reqId) {
    closeMineTestExecutionModal();
    return;
  }
  const minorId = Number(document.getElementById('mineTestExecMinorSelect')?.value || 0);
  const resultStatus = document.getElementById('mineTestExecResult')?.value || 'passed';
  const notes = (document.getElementById('mineTestExecNotes')?.value || '').trim();
  if (!minorId) {
    window.showMessage && window.showMessage('请先选择当前复测发包（小版本）', 'error');
    return;
  }

  const pageMinorSel = document.getElementById('mineMinorSelect');
  if (pageMinorSel) pageMinorSel.value = String(minorId);

  try {
    await submitTestExecution(reqId, {
      minor_version_id: minorId,
      result_status: resultStatus,
      test_completed: true,
      notes,
    });
    window.showMessage && window.showMessage('测试执行记录已提交，并同步标记需求测试完成', 'success');
    const modal = document.getElementById('mineTestExecModal');
    if (modal) closeModal(modal);
    await loadMyWorkbench();
  } catch (err) {
    if (modalState.checkboxEl) modalState.checkboxEl.checked = false;
    window.showMessage && window.showMessage(err.message || '提交失败，请稍后重试', 'error');
  } finally {
    modalState.reqId = null;
    modalState.checkboxEl = null;
  }
}

export async function handleTestCompletedToggle(reqId, checked, checkboxEl) {
  if (checked) {
    const req = getRequirementById(reqId);
    const linkedMinors = getMinorOptionsByMajor(Number(req?.major_version_id || 0));
    if (linkedMinors.length === 0) {
      if (checkboxEl) checkboxEl.checked = false;
      window.showMessage && window.showMessage('当前需求所属大版本下暂无可用小版本，请先补充小版本', 'error');
      return;
    }
    openTestExecutionModal(reqId, checkboxEl);
    return;
  }

  const ok = confirm('确认取消该需求的测试完成状态吗？');
  if (!ok) {
    if (checkboxEl) checkboxEl.checked = true;
    return;
  }
  try {
    await api(`/requirements/${reqId}/status`, {
      method: 'PATCH',
      headers: window.H,
      body: { test_completed: false },
    });
    window.showMessage && window.showMessage('已取消测试完成状态', 'success');
  } catch (err) {
    if (checkboxEl) checkboxEl.checked = true;
    window.showMessage && window.showMessage(err.message || '状态更新失败', 'error');
  } finally {
    await loadMyWorkbench();
  }
}

export function toggleMineMode() {
  const mode = getMode();
  const wrap = document.getElementById('mineVersionWrap');
  if (wrap) wrap.style.display = mode === 'version' ? 'flex' : 'none';
  refreshMineMinorSelectByMode();
  return loadMyWorkbench();
}

function refreshMineMinorSelectByMode() {
  const minorSel = document.getElementById('mineMinorSelect');
  if (!minorSel) return;
  const mode = getMode();

  if (mode === 'all_pending') {
    const allMinors = (window.versions || []).filter((v) => v.version_type === 'minor');
    if (allMinors.length === 0) {
      minorSel.innerHTML = "<option value=''>暂无子版本</option>";
      return;
    }
    const prev = minorSel.value;
    minorSel.innerHTML = allMinors.map((v) => `<option value="${v.id}">${v.version_no}</option>`).join('');
    if (prev) minorSel.value = prev;
    if (!minorSel.value && allMinors[0]) minorSel.value = String(allMinors[0].id);
    return;
  }

  if (typeof window.fillMinorSelectByMajor === 'function') {
    window.fillMinorSelectByMajor('mineMajorSelect', 'mineMinorSelect');
  }
}

export async function loadMyWorkbench() {
  initTestExecutionModal();
  refreshMineMinorSelectByMode();
  const mode = getMode();
  const majorId = Number(document.getElementById('mineMajorSelect')?.value || 0);
  let url = '/workbench/mine?mode=' + mode;
  if (mode === 'version' && majorId) url += '&major_version_id=' + majorId;
  const currentSoftwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  if (currentSoftwareId) url += `&software_id=${currentSoftwareId}`;
  if (currentSoftwareId) {
    await preflightWorkbenchData(currentSoftwareId);
  }

  state.currentFeedbackTodoHtml = '';
  if (window.OmniQAFeedbackTab && typeof window.OmniQAFeedbackTab.loadFeedbackTodoOnMine === 'function') {
    try {
      await window.OmniQAFeedbackTab.loadFeedbackTodoOnMine(mode === 'version' ? majorId : null);
    } catch {
      state.currentFeedbackTodoHtml = '';
    }
  }

  state.currentDispatchHtml = '';
  if (mode === 'version' && majorId) {
    const ddata = await (await api('/bugs/dispatched-to-me?major_version_id=' + majorId)).json();
    if (ddata.length > 0) {
      state.currentDispatchHtml = `<div class="card" style="border:2px solid #3b82f6; background:#eff6ff; margin-bottom:24px;">
        <h3 style="color:#1d4ed8; margin-top:0; border-bottom:1px dashed #93c5fd; padding-bottom:8px;">🪂 指派给我的Bug</h3>
        <table style="background:#fff; border-radius:6px; overflow:hidden;">
          <thead><tr><th>Bug 编号 / 归属需求</th><th>引出的新Bug</th><th>专项处理操作</th></tr></thead>
          <tbody>
            ${ddata.map((b) => {
              const ztBugId = (b.bug_id || '').replace(/\D/g, '');
              const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" style="margin-left:4px;"></span>` : '';
              const previewBugBtn = renderPreviewBtn('bug', ztBugId);
              return `
              <tr style="${b.test_done ? 'background:#f8fafc; color:#94a3b8; text-decoration:line-through;' : ''}">
                <td>${renderBugLink(b)} ${ztSlot} ${previewBugBtn} <span style="font-size:12px;color:#64748b;">(${b.req_title})</span></td>
                <td>
                  <input type="hidden" id="dnb_hidden_${b.id}" value="${b.newly_found_bug_id || ''}">
                  <div style="margin-bottom:6px;">
                    ${(b.newly_found_bug_id ? b.newly_found_bug_id.split(',') : []).map((dbug) => `
                      <span class="badge" style="background:#fef2f2; color:#b91c1c; border:1px solid #fca5a5; margin-right:4px; margin-bottom:4px; display:inline-block;">
                        ${dbug} <a href="javascript:void(0)" onclick="${b.test_done ? 'return false;' : `removeDerivedBug(${b.id}, '${dbug}')`}" style="color:#7f1d1d; text-decoration:none; margin-left:4px; font-weight:bold;">×</a>
                      </span>
                    `).join('') || '<span class="muted" style="font-size:12px;">暂无引出Bug</span>'}
                  </div>
                </td>
                <td style="text-decoration:none;">
                  <input type="hidden" id="dzt_${b.id}" value="${b.zentao_bug_id || ''}">
                  <input type="hidden" id="dwasclosed_${b.id}" value="${b.closed ? '1' : ''}">
                  <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                    <select id='dres_${b.id}' style="padding:2px; font-size:13px;" ${b.test_done ? 'disabled' : ''}>
                      <option value="fixed" ${b.resolution === 'fixed' ? 'selected' : ''}>🚀修复通过</option>
                      <option value="false_alarm" ${b.resolution === 'false_alarm' ? 'selected' : ''}>⚠️误报</option>
                      <option value="rejected" ${b.resolution === 'rejected' ? 'selected' : ''}>⛔拒绝修复</option>
                    </select>
                    <label style="color:#0f172a; display:flex; align-items:center; gap:4px; margin:0;"><input id='ddone_${b.id}' type='checkbox' ${b.test_done ? 'checked' : ''} onchange="toggleDispatchedClose(${b.id}, this.checked)"> 确认闭环</label>
                    <button class="${b.test_done ? 'secondary' : ''}" onclick="saveDispatchedBug(${b.id})">保存记录</button>
                  </div>
                  <div id="dcomment_wrap_${b.id}" style="margin-top:6px; display:none;">
                    <textarea id="dcomment_${b.id}" rows="2" placeholder="闭环说明（将同步写入禅道备注）" style="width:100%; font-size:12px; padding:4px 6px; border:1px solid #cbd5e1; border-radius:4px;"></textarea>
                    ${b.zentao_bug_id ? '<div class="muted" style="font-size:11px; margin-top:2px;">勾选「确认闭环」保存时会同步关闭禅道 Bug（需禅道中已是“已解决”）。</div>' : ''}
                  </div>
                </td>
              </tr>
            `;
            }).join('')}
          </tbody>
        </table>
      </div>`;
    }
  }

  state.currentMineData = await (await api(url)).json();
  const summaryEl = document.getElementById('mineSummaryText');
  const pendingCount = state.currentMineData.filter((r) => !r.test_completed || !r.case_completed).length;
  if (summaryEl) {
    if (mode === 'version') {
      const select = document.getElementById('mineMajorSelect');
      const vName = select?.options?.[select.selectedIndex]?.text || '当前大版本';
      summaryEl.innerHTML = `📢 <b>${vName}</b> 剩余 <b style="color:#dc2626; font-size:18px;">${pendingCount}</b> 个需求未完成测试！`;
    } else {
      summaryEl.innerHTML = `📢 跨版本汇总：您共有 <b style="color:#dc2626; font-size:18px;">${pendingCount}</b> 个待办测试需求！`;
    }
    summaryEl.style.display = 'block';
  }
  renderMineCards();
}

const TASK_STATUS_ZH = { wait: '未开始', doing: '进行中', done: '已完成', pause: '已暂停', cancel: '已取消', closed: '已关闭' };

// 「开始」按钮（放在用例/测试勾选框之前）。仅在关联了禅道任务时显示，并按任务状态切换：
//   wait（已关联未开始）→ 可点「开始」
//   doing（已开始 / 重新激活后）→「任务已开始」禁用
//   done（测试完成）→「任务已完成」禁用
function renderTaskStartControl(req) {
  if (!req.zentao_task_id) return '';
  if (req.zentao_task_status === 'done' || req.test_completed) {
    return `<button class="secondary" disabled style="padding:2px 10px; font-size:12px; opacity:.7;">✅ 任务已完成</button>`;
  }
  if (req.zentao_task_status === 'doing' || req.task_started_at) {
    return `<button class="secondary" disabled style="padding:2px 10px; font-size:12px; opacity:.7;">⏱ 任务已开始</button>`;
  }
  return `<button style="padding:2px 10px; font-size:12px; background:#16a34a;" onclick="startReqTask(${req.id})" title="开始测试，禅道子任务同步开始">▶ 开始</button>`;
}

// 子任务标签 + 预览 + 预计用时输入。预计用时仅在关联了禅道任务时显示。
function renderTaskMeta(req) {
  if (req.zentao_task_id) {
    const est = (req.estimated_test_hours != null ? req.estimated_test_hours : 4);
    const estInput = `<label class="badge" style="background:#f8fafc; color:#475569; border:1px solid #e2e8f0; display:inline-flex; align-items:center; gap:4px;">预计用时
      <input type="number" min="0.5" step="0.5" value="${est}" style="width:54px; padding:1px 4px; border:1px solid #cbd5e1; border-radius:4px;" onchange="setReqEstimatedHours(${req.id}, this.value)" onclick="event.stopPropagation()">h</label>`;
    const zh = TASK_STATUS_ZH[req.zentao_task_status] || req.zentao_task_status || '';
    const done = req.zentao_task_status === 'done';
    const taskTag = `<span class="badge" style="background:${done ? '#dcfce7' : '#eff6ff'}; color:${done ? '#166534' : '#1d4ed8'}; border:1px solid ${done ? '#bbf7d0' : '#bfdbfe'};">禅道子任务 #${req.zentao_task_id}${zh ? '·' + zh : ''}</span>${renderPreviewBtn('task', req.zentao_task_id)}`;
    return `${estInput}${taskTag}`;
  }
  return `<span class="badge" style="background:#f1f5f9; color:#94a3b8; border:1px solid #e2e8f0;">未关联禅道任务</span>`;
}

// 收集一个需求卡片下的全部 bug（用例关联 bug + 自由 bug）。
function collectReqBugs(req) {
  const caseBugs = (req.test_cases || []).flatMap((c) => c.bugs || []);
  return [...caseBugs, ...(req.free_bugs || [])];
}

export function renderMineCards() {
  const searchKw = (document.getElementById('mineSearchInput')?.value || '').trim().toLowerCase();
  const onlyAssignedToMe = !!document.getElementById('mineAssignedToMe')?.checked;
  const filteredData = state.currentMineData.filter((req) => {
    // 搜索：匹配需求编号/名称，或卡片内任一 bug 的编号/标题（卡片级过滤）
    if (searchKw) {
      const bugs = collectReqBugs(req);
      const hit = (req.zentao_req_id && req.zentao_req_id.toLowerCase().includes(searchKw))
        || (req.title && req.title.toLowerCase().includes(searchKw))
        || bugs.some((b) => (b.bug_id && b.bug_id.toLowerCase().includes(searchKw))
          || (b.zentao_bug_title && b.zentao_bug_title.toLowerCase().includes(searchKw)));
      if (!hit) return false;
    }
    // 「指派给我的」：只保留含有 ≥1 个指派给当前用户 bug 的卡片
    if (onlyAssignedToMe && !collectReqBugs(req).some((b) => b.assigned_to_me)) return false;
    return true;
  });
  const mode = getMode();
  const foldStateMap = getFoldStateMap();

  const reqsHtml = filteredData.map((req) => {
    const caseDisabled = req.case_completed ? 'disabled' : '';
    const testDisabled = req.test_completed ? 'disabled' : '';
    const isFullyCompleted = req.test_completed && req.case_completed;
    const isOpen = Object.prototype.hasOwnProperty.call(foldStateMap, String(req.id)) ? !!foldStateMap[String(req.id)] : !isFullyCompleted;
    const vTag = mode === 'all_pending' && req.major_version_name
      ? `<span class="badge" style="background:#e0f2fe; color:#0369a1; border:1px solid #bae6fd; margin-right:8px; padding:2px 6px;">🏷️${req.major_version_name}</span>`
      : '';
    const syncSummary = [
      req.auto_linked_case_count > 0 ? renderAutoLinkedBadge(`自动归集用例 ${req.auto_linked_case_count}`) : '',
      req.auto_linked_bug_count > 0 ? renderAutoLinkedBadge(`自动归集Bug ${req.auto_linked_bug_count}`) : '',
    ].join('');

    const caseHtml = (req.test_cases || []).map((c) => {
      const caseZtId = String(c.zentao_case_id || '').replace(/\D/g, '');
      return `
      <div class="case-item">
        <div class="row">
          ${renderCaseLink(c)}${renderPreviewBtn('testcase', caseZtId)}${c.auto_linked ? renderAutoLinkedBadge() : ''}
        </div>
        <div class="case-bugs" style="margin-top:6px;">${(c.bugs || []).map((b) => renderBugChip(req, b)).join('') || '<span class="muted">暂无关联Bug</span>'}</div>
      </div>`;
    }).join('');

    const freeBugHtml = (req.free_bugs || []).map((b) => renderBugChip(req, b)).join('') || '<span class="muted">暂无自由Bug</span>';

    const ztStoryId = (req.zentao_req_id || '').replace(/\D/g, '');
    const ztStorySlot = ztStoryId ? `<span class="zt-story-slot" data-zt-story-id="${ztStoryId}" style="margin-left:6px; vertical-align:middle;"></span>` : '';
    const aiResultSlot = ztStoryId ? `<span class="ai-result-slot" data-story-id="${ztStoryId}" style="margin-left:6px; vertical-align:middle;"></span>` : '';
    const reqIdHtml = ztStoryId
      ? `<span class="qa-story-id-nohref" data-zt-story-id="${ztStoryId}">${req.zentao_req_id}</span>`
      : (req.zentao_req_id || '');
    const previewBtn = renderPreviewBtn('story', ztStoryId);

    return `
      <details class="mine-req-card" data-req-id="${req.id}" ${isOpen ? 'open' : ''} ontoggle="rememberMineReqFold(${req.id}, this.open)" style="background: ${isFullyCompleted ? '#f8fafc' : '#ffffff'}; transition: all 0.3s;">
        <summary style="outline:none; cursor:pointer; font-size:16px; font-weight:bold; color:#0f172a; border-bottom: ${isFullyCompleted ? 'none' : '1px solid #e2e8f0'}; padding-bottom: ${isFullyCompleted ? '0' : '12px'}; display: flex; justify-content: space-between; align-items: center; list-style: none;">
          <div>${vTag}<span style="${isFullyCompleted ? 'text-decoration:line-through; color:#94a3b8;' : ''}">${reqIdHtml} ${req.title}</span>${ztStorySlot}${previewBtn}${aiResultSlot}</div>
          ${isFullyCompleted ? '<span style="color:#16a34a; font-size:14px; background:#f0fdf4; padding:4px 8px; border-radius:4px; border:1px solid #bbf7d0;">✅ 测试已完成</span>' : '<span style="font-size:12px; color:#94a3b8; font-weight:normal;">(点击标题可收起/展开卡片)</span>'}
        </summary>
        <div style="margin-top: 12px;">
          <div class="row" style="margin-bottom:8px">
            ${renderTaskStartControl(req)}
            ${req.final_test
              ? `<label><input type="checkbox" ${req.case_completed ? 'checked' : ''} onchange="setFinalTestStatus(${req.id}, 'case_completed', this.checked, this)">✅用例完成</label>
            <label><input type="checkbox" ${req.test_completed ? 'checked' : ''} onchange="setFinalTestStatus(${req.id}, 'test_completed', this.checked, this)">✅测试完成</label>`
              : `<label><input type="checkbox" ${req.case_completed ? 'checked' : ''} onchange="setReqStatus(${req.id}, 'case_completed', this.checked).then(()=>loadMyWorkbench())">✅用例完成</label>
            <label><input type="checkbox" ${req.test_completed ? 'checked' : ''} onchange="handleTestCompletedToggle(${req.id}, this.checked, this)">✅测试完成</label>`}
            ${renderTaskMeta(req)}
            <span class="badge" style="background:${req.test_notes ? '#dcfce7' : '#f1f5f9'}; color:${req.test_notes ? '#166534' : '#64748b'}; border:1px solid ${req.test_notes ? '#bbf7d0' : '#e2e8f0'};">
              测试要点：${req.test_notes ? '已填写' : '未填写'}
            </span>
            <button class="secondary" style="padding:2px 8px; font-size:12px;" onclick="openReqTestNotesModal(${req.id})">${req.test_notes ? '查看/编辑测试要点' : '填写测试要点'}</button>
            ${syncSummary}
          </div>
          <div>${caseHtml}</div>
          <div class="free-bug-box"><div><b>自由Bug</b></div><div style="margin-top:6px;">${freeBugHtml}</div></div>
          <div class="muted" style="margin-top:10px; font-size:12px; padding:6px 10px; background:#f8fafc; border-left:3px solid #94a3b8; border-radius:4px;">
            用例与 Bug 已改为按禅道 story / testcase 自动归集，不再需要手工录入。如需补救异常归属，请到“同步中心”由管理员处理。
          </div>
        </div>
      </details>`;
  }).join('');

  const finalTestBanner = (state.currentMineData || []).some((r) => r.final_test)
    ? `<div class="card" style="border:2px solid #f59e0b; background:#fffbeb; margin-bottom:16px;">
         <b style="color:#92400e;">🏁 该版本已进入「最终测试」阶段</b>
         <div class="muted" style="margin-top:4px;">以下为该版本全部需求（无视任务分配）。您的勾选独立记录，与原有测试状态互不影响。</div>
       </div>`
    : '';

  const mineCards = document.getElementById('mineCards');
  if (mineCards) {
    mineCards.innerHTML = finalTestBanner + (state.currentFeedbackTodoHtml || '') + state.currentDispatchHtml + reqsHtml;
    window.OmniQAZentao?.hydrateContainer(mineCards);
    window.OmniQAStoryAI?.refreshSlots?.(mineCards);
  }
  window.scheduleWorkbenchViewportResize?.();
  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    window.OmniQASSE.releaseAttention?.();
    document.querySelectorAll('.mine-req-card[data-req-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'mine_requirement', key: el.getAttribute('data-req-id'), tone: 'blue', hoverDelayMs: 420 });
    });
  }
}

function bindMineSSE() {
  if (mineSseBound) return;
  if (!window.OmniQASSE?.subscribe) return;

  // 新需求进来：尝试增量刷新工作台（保持筛选/折叠状态）
  window.OmniQASSE.subscribe('workbench_requirement_created', ({ payload }) => {
    const reqId = Number(payload?.id || 0);
    if (!reqId) return;
    const myId = Number(state.currentUser?.id || window.currentUser?.id || 0);
    if (myId && payload?.owner_id && Number(payload.owner_id) !== myId) return;
    // Refresh quietly to get new card data; markUnread is handled by sse.js
    const majorId = Number(document.getElementById('mineMajorSelect')?.value || 0);
    const mode = document.getElementById('mineDisplayMode')?.value || 'version';
    if (!majorId && mode === 'version') return;
    // Debounce so rapid-fire events only trigger one reload
    if (window._mineSSERequirementTimer) clearTimeout(window._mineSSERequirementTimer);
    window._mineSSERequirementTimer = setTimeout(async () => {
      window._mineSSERequirementTimer = null;
      if (!window.isWorkbenchSubtabActive?.('demand')) return;
      if (typeof window.loadMyWorkbench === 'function') await window.loadMyWorkbench();
      // After reload, pulse the new card
      const el = document.querySelector(`.mine-req-card[data-req-id='${reqId}']`);
      if (el) window.OmniQASSE.pulseBoundaryGlow(el, 'blue');
    }, 500);
  });

  // 新 testcase：容器级轻流光，不进主角标
  window.OmniQASSE.subscribe('workbench_testcase_created', ({ payload }) => {
    const reqId = Number(payload?.requirement_id || 0);
    if (!reqId) return;
    const el = document.querySelector(`.mine-req-card[data-req-id='${reqId}']`);
    if (el) window.OmniQASSE.pulseBoundaryGlow(el, 'teal');
  });

  // 新 bug（执行阶段挂载到需求）：在需求卡片上显示琥珀色流光，不进主角标
  window.OmniQASSE.subscribe('bug_created', ({ payload }) => {
    const reqId = Number(payload?.requirement_id || 0);
    if (!reqId) return;
    const el = document.querySelector(`.mine-req-card[data-req-id='${reqId}']`);
    if (el) window.OmniQASSE.pulseBoundaryGlow(el, 'amber');
  });

  mineSseBound = true;
}
bindMineSSE();

export function rememberMineReqFold(reqId, isOpen) {
  const map = getFoldStateMap();
  map[String(reqId)] = !!isOpen;
  setFoldStateMap(map);
  // 展开/收起卡片后容器高度会变化，重新计算工作台视口高度，避免末尾内容被裁切
  window.scheduleWorkbenchViewportResize?.();
}

export async function editWorkbenchCase(id, oldCaseId) {
  const num = prompt('请输入正确的用例数字部分：', oldCaseId.replace('u#', ''));
  if (!num) return;
  try {
    await api('/test-cases/' + id, { method: 'PUT', headers: window.H, body: { zentao_case_id: window.withPrefix('u#', num) } });
    window.showMessage && window.showMessage('用例编号已修改', 'success');
    await loadMyWorkbench();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '修改失败', 'error');
  }
}

export async function editWorkbenchBug(id, oldBugId) {
  const num = prompt('请输入正确的 Bug 数字部分：', oldBugId.replace('b#', ''));
  if (!num) return;
  try {
    await api('/bugs/' + id + '?new_bug_id=' + encodeURIComponent(window.withPrefix('b#', num)), { method: 'PUT' });
    window.showMessage && window.showMessage('Bug 编号已纠正', 'success');
    await loadMyWorkbench();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '修改失败', 'error');
  }
}

export async function removeWorkbenchBug(id) {
  if (!confirm('确定要在工作台中彻底删除这个 Bug 吗？')) return;
  try {
    await api('/bugs/' + id, { method: 'DELETE' });
    window.showMessage && window.showMessage('Bug 已彻底删除', 'success');
    await loadMyWorkbench();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '删除失败', 'error');
  }
}

export async function setReqStatus(reqId, key, checked) {
  try {
    if (!checked) {
      const ok = confirm(key === 'case_completed' ? '确认取消【用例完成】状态吗？' : '确认取消【测试完成】状态吗？');
      if (!ok) {
        await loadMyWorkbench();
        return;
      }
    }
    const payload = {};
    payload[key] = checked;
    await api(`/requirements/${reqId}/status`, { method: 'PATCH', headers: window.H, body: payload });
    window.showMessage && window.showMessage('需求状态已更新', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '状态更新失败', 'error');
  } finally {
    await loadMyWorkbench();
  }
}

// 禅道任务联动：点击「开始」→ 记录开始时刻并让禅道子任务开始
export async function startReqTask(reqId) {
  try {
    const res = await api(`/requirements/${reqId}/task/start`, { method: 'POST', headers: window.H, body: {} });
    let data = null;
    try { data = await res.json(); } catch (_) { /* ignore */ }
    const errs = (data && data.errors) || [];
    if (errs.length) {
      window.showMessage && window.showMessage(`任务已开始（禅道侧部分失败：${errs[0]}）`, 'error');
    } else {
      window.showMessage && window.showMessage('任务已开始', 'success');
    }
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '开始任务失败', 'error');
  } finally {
    await loadMyWorkbench();
  }
}

// 修改某需求的预计测试用时（小时）
export async function setReqEstimatedHours(reqId, value) {
  const hours = Number(value);
  if (!Number.isFinite(hours) || hours <= 0) {
    window.showMessage && window.showMessage('预计用时必须是正数', 'error');
    return;
  }
  try {
    await api(`/requirements/${reqId}/estimated-hours`, { method: 'PUT', headers: window.H, body: { estimated_test_hours: hours } });
    window.showMessage && window.showMessage('预计测试用时已更新', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '更新预计用时失败', 'error');
    await loadMyWorkbench();
  }
}

export async function setFinalTestStatus(reqId, key, checked, checkboxEl) {
  if (key === 'test_completed' && !checked) {
    const ok = confirm('确认取消该需求的【测试完成】状态吗？');
    if (!ok) {
      if (checkboxEl) checkboxEl.checked = true;
      return;
    }
  }
  try {
    const payload = {};
    payload[key] = checked;
    await api(`/final-test/requirements/${reqId}/status`, { method: 'PATCH', headers: window.H, body: payload });
    window.showMessage && window.showMessage('最终测试状态已更新', 'success');
  } catch (err) {
    if (checkboxEl) checkboxEl.checked = !checked;
    window.showMessage && window.showMessage(err.message || '状态更新失败', 'error');
  } finally {
    await loadMyWorkbench();
  }
}

export async function addCase(reqId) {
  try {
    const n = document.getElementById(`new_case_${reqId}`).value;
    const caseId = window.withPrefix('u#', n);
    if (!caseId) {
      window.showMessage && window.showMessage('请输入用例编号数字部分', 'error');
      return;
    }
    await api(`/requirements/${reqId}/cases`, { method: 'POST', headers: window.H, body: { zentao_case_id: caseId } });
    window.showMessage && window.showMessage('用例新增成功', 'success');
    await loadMyWorkbench();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '新增用例失败', 'error');
  }
}

export async function deleteCase(caseId) {
  if (!confirm('确定删除该用例吗？')) return;
  try {
    await api(`/test-cases/${caseId}`, { method: 'DELETE' });
    window.showMessage && window.showMessage('用例已删除', 'success');
    await loadMyWorkbench();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '删除用例失败', 'error');
  }
}

export async function promptCaseBug(reqId, caseId) {
  const minorId = Number(document.getElementById('mineMinorSelect')?.value || 0);
  if (!minorId) {
    window.showMessage && window.showMessage('拦截：请先在页面顶部选择【当前复测发包(小版本)】环境！', 'error');
    return;
  }
  const n = prompt('请输入关联 Bug 的数字部分：');
  if (!n) return;
  const bug = window.withPrefix('b#', n);
  try {
    await api('/bugs/execution', { method: 'POST', headers: window.H, body: { bug_id: bug, minor_version_id: minorId, requirement_id: reqId, source_type: 'case', source_ref: String(caseId) } });
    window.showMessage && window.showMessage('关联Bug新增成功', 'success');
    await loadMyWorkbench();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '新增Bug失败', 'error');
  }
}

export async function addFreeBug(reqId) {
  const minorId = Number(document.getElementById('mineMinorSelect')?.value || 0);
  if (!minorId) {
    window.showMessage && window.showMessage('拦截：请先在页面顶部选择【当前复测发包(小版本)】环境！', 'error');
    return;
  }
  const n = document.getElementById(`new_free_bug_${reqId}`).value;
  const bug = window.withPrefix('b#', n);
  if (!bug) {
    window.showMessage && window.showMessage('请输入自由Bug数字', 'error');
    return;
  }
  try {
    await api('/bugs/execution', { method: 'POST', headers: window.H, body: { bug_id: bug, minor_version_id: minorId, requirement_id: reqId, source_type: 'manual' } });
    window.showMessage && window.showMessage('自由Bug新增成功', 'success');
    await loadMyWorkbench();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '新增自由Bug失败', 'error');
  }
}

export async function pushCase() {
  if (!(window.confirmPush && window.confirmPush())) return;
  await api('/push/case-progress', { method: 'POST', headers: window.H, body: { major_version_id: Number(document.getElementById('mineMajorSelect')?.value || 0) } });
  window.showMessage && window.showMessage('用例进度已推送');
}

export async function pushTest() {
  if (!(window.confirmPush && window.confirmPush())) return;
  await api(`/push/test-progress?minor_version_id=${Number(document.getElementById('mineMinorSelect')?.value || 0)}&major_version_id=${Number(document.getElementById('mineMajorSelect')?.value || 0)}`, { method: 'POST' });
  window.showMessage && window.showMessage('测试进度已推送');
}

window.rememberMineReqFold = rememberMineReqFold;
// 直接挂到 window，避免依赖（可能被缓存的）index.html 内联包装函数
window.setFinalTestStatus = setFinalTestStatus;
window.OmniQAMineTab = {
  toggleMineMode,
  loadMyWorkbench,
  renderMineCards,
  rememberMineReqFold,
  editWorkbenchCase,
  editWorkbenchBug,
  removeWorkbenchBug,
  setReqStatus,
  setFinalTestStatus,
  startReqTask,
  setReqEstimatedHours,
  addCase,
  deleteCase,
  promptCaseBug,
  addFreeBug,
  pushCase,
  pushTest,
  handleTestCompletedToggle,
  submitTestExecution,
  confirmMineTestExecutionModal,
  closeMineTestExecutionModal,
  openReqTestNotesModal,
  closeReqTestNotesModal,
  saveReqTestNotes,
  openBugResultModal,
  closeBugResultModal,
  confirmBugResultModal,
  toggleBugResultCloseComment,
};


