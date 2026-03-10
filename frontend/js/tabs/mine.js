import { api } from '../api.js';
import { state } from '../state.js';
import { closeModal, openModal } from '../components/modal.js';

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

function renderBugChip(req, bug) {
  const dBadge = bug.dispatched_to_name
    ? `<span style="color:#ea580c; background:#ffedd5; padding:1px 4px; border-radius:4px; font-size:11px; margin-left:6px;">🪂已特派给:${bug.dispatched_to_name}</span>`
    : '';
  const verText = bug.fixed_minor_version_no
    ? `<span style="color:#16a34a; font-size:11px; margin-left:4px;">(✅解决于: 🏷️${bug.fixed_minor_version_no})</span>`
    : `<span style="color:#94a3b8; font-size:11px; margin-left:4px;">(发现于: 🏷️${bug.found_minor_version_no || '未知'})</span>`;

  return `<span class="badge" style="background:#f1f5f9; border:1px solid #cbd5e1; padding:2px 6px; margin-right:6px; border-radius:4px; display:inline-block; margin-bottom:4px;">
      ${bug.bug_id} ${verText} ${dBadge}
      <a href="javascript:void(0)" title="编辑" onclick="${req.test_completed ? 'return false;' : `editWorkbenchBug(${bug.id}, '${bug.bug_id}')`}" style="color:${req.test_completed ? '#94a3b8' : '#3b82f6'}; margin-left:4px; text-decoration:none;">✎</a>
      <a href="javascript:void(0)" title="删除" onclick="${req.test_completed ? 'return false;' : `removeWorkbenchBug(${bug.id})`}" style="color:${req.test_completed ? '#94a3b8' : '#ef4444'}; margin-left:2px; text-decoration:none;">×</a>
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
  let url = '/requirements/my-workbench?mode=' + mode;
  if (mode === 'version' && majorId) url += '&major_version_id=' + majorId;
  const currentSoftwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  if (currentSoftwareId) url += `&software_id=${currentSoftwareId}`;

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
        <h3 style="color:#1d4ed8; margin-top:0; border-bottom:1px dashed #93c5fd; padding-bottom:8px;">🪂 管理员特派给我的专项 Bug</h3>
        <table style="background:#fff; border-radius:6px; overflow:hidden;">
          <thead><tr><th>Bug 编号 / 归属需求</th><th>引出的新Bug</th><th>专项处理操作</th></tr></thead>
          <tbody>
            ${ddata.map((b) => `
              <tr style="${b.test_done ? 'background:#f8fafc; color:#94a3b8; text-decoration:line-through;' : ''}">
                <td><b>${b.bug_id}</b> <span style="font-size:12px;color:#64748b;">(${b.req_title})</span></td>
                <td>
                  <input type="hidden" id="dnb_hidden_${b.id}" value="${b.newly_found_bug_id || ''}">
                  <div style="margin-bottom:6px;">
                    ${(b.newly_found_bug_id ? b.newly_found_bug_id.split(',') : []).map((dbug) => `
                      <span class="badge" style="background:#fef2f2; color:#b91c1c; border:1px solid #fca5a5; margin-right:4px; margin-bottom:4px; display:inline-block;">
                        ${dbug} <a href="javascript:void(0)" onclick="${b.test_done ? 'return false;' : `removeDerivedBug(${b.id}, '${dbug}')`}" style="color:#7f1d1d; text-decoration:none; margin-left:4px; font-weight:bold;">×</a>
                      </span>
                    `).join('') || '<span class="muted" style="font-size:12px;">暂无引出Bug</span>'}
                  </div>
                  <button class="secondary" style="padding:2px 8px; font-size:12px;" ${b.test_done ? 'disabled' : ''} onclick="addDerivedBug(${b.id})">➕ 添加引出Bug</button>
                </td>
                <td style="text-decoration:none;">
                  <select id='dres_${b.id}' style="margin-right:8px; padding:2px; font-size:13px;" ${b.test_done ? 'disabled' : ''}>
                    <option value="fixed" ${b.resolution === 'fixed' ? 'selected' : ''}>🚀修复通过</option>
                    <option value="false_alarm" ${b.resolution === 'false_alarm' ? 'selected' : ''}>⚠️误报</option>
                    <option value="rejected" ${b.resolution === 'rejected' ? 'selected' : ''}>⛔拒绝修复</option>
                  </select>
                  <label style="color:#0f172a;"><input id='ddone_${b.id}' type='checkbox' ${b.test_done ? 'checked' : ''} onchange="document.getElementById('dnb_hidden_'+${b.id}).disabled=this.checked"> 确认闭环</label>
                  <button class="${b.test_done ? 'secondary' : ''}" onclick="saveDispatchedBug(${b.id})" style="margin-left:8px;">保存记录</button>
                </td>
              </tr>
            `).join('')}
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

export function renderMineCards() {
  const searchKw = (document.getElementById('mineSearchInput')?.value || '').trim().toLowerCase();
  const filteredData = state.currentMineData.filter((req) => !searchKw || (req.zentao_req_id && req.zentao_req_id.toLowerCase().includes(searchKw)) || (req.title && req.title.toLowerCase().includes(searchKw)));
  const mode = getMode();
  const foldStateMap = getFoldStateMap();

  const reqsHtml = filteredData.map((req) => {
    const caseDisabled = req.case_completed ? 'disabled' : '';
    const testDisabled = req.test_completed ? 'disabled' : '';
    const casePrefixColor = req.case_completed ? 'color:#94a3b8;' : '';
    const testPrefixColor = req.test_completed ? 'color:#94a3b8;' : '';
    const isFullyCompleted = req.test_completed && req.case_completed;
    const isOpen = Object.prototype.hasOwnProperty.call(foldStateMap, String(req.id)) ? !!foldStateMap[String(req.id)] : !isFullyCompleted;
    const vTag = mode === 'all_pending' && req.major_version_name
      ? `<span class="badge" style="background:#e0f2fe; color:#0369a1; border:1px solid #bae6fd; margin-right:8px; padding:2px 6px;">🏷️${req.major_version_name}</span>`
      : '';

    const caseHtml = (req.test_cases || []).map((c) => `
      <div class="case-item">
        <div class="row">
          <b>${c.zentao_case_id}</b>
          <button class="secondary" style="padding:2px 8px; font-size:12px; margin-left:8px;" ${caseDisabled} onclick="editWorkbenchCase(${c.id}, '${c.zentao_case_id}')">编辑编号</button>
          <button ${testDisabled} onclick="promptCaseBug(${req.id},${c.id})">添加关联Bug</button>
          <button class="danger" ${caseDisabled} onclick="deleteCase(${c.id})">删除用例</button>
        </div>
        <div class="case-bugs" style="margin-top:6px;">${(c.bugs || []).map((b) => renderBugChip(req, b)).join('') || '<span class="muted">暂无关联Bug</span>'}</div>
      </div>`).join('');

    const freeBugHtml = (req.free_bugs || []).map((b) => renderBugChip(req, b)).join('') || '<span class="muted">暂无自由Bug</span>';

    return `
      <details class="mine-req-card" ${isOpen ? 'open' : ''} ontoggle="rememberMineReqFold(${req.id}, this.open)" style="background: ${isFullyCompleted ? '#f8fafc' : '#ffffff'}; transition: all 0.3s;">
        <summary style="outline:none; cursor:pointer; font-size:16px; font-weight:bold; color:#0f172a; border-bottom: ${isFullyCompleted ? 'none' : '1px solid #e2e8f0'}; padding-bottom: ${isFullyCompleted ? '0' : '12px'}; display: flex; justify-content: space-between; align-items: center; list-style: none;">
          <div>${vTag}<span style="${isFullyCompleted ? 'text-decoration:line-through; color:#94a3b8;' : ''}">${req.zentao_req_id} ${req.title}</span></div>
          ${isFullyCompleted ? '<span style="color:#16a34a; font-size:14px; background:#f0fdf4; padding:4px 8px; border-radius:4px; border:1px solid #bbf7d0;">✅ 测试已完成</span>' : '<span style="font-size:12px; color:#94a3b8; font-weight:normal;">(点击标题可收起/展开卡片)</span>'}
        </summary>
        <div style="margin-top: 12px;">
          <div class="row" style="margin-bottom:8px">
            <label><input type="checkbox" ${req.case_completed ? 'checked' : ''} onchange="setReqStatus(${req.id}, 'case_completed', this.checked).then(()=>loadMyWorkbench())">✅用例完成</label>
            <label><input type="checkbox" ${req.test_completed ? 'checked' : ''} onchange="handleTestCompletedToggle(${req.id}, this.checked, this)">✅测试完成</label>
          </div>
          <div>${caseHtml}</div>
          <div class="free-bug-box"><div><b>自由Bug</b></div><div style="margin-top:6px;">${freeBugHtml}</div></div>
          <div class="row" style="margin-top:8px">
            <div class="prefix-input"><span style="${casePrefixColor}">u#</span><input id="new_case_${req.id}" inputmode="numeric" oninput="digitsOnly(this)" placeholder="新增用例编号" ${caseDisabled}></div>
            <button ${caseDisabled} onclick="addCase(${req.id})">逐个添加用例</button>
            <div class="prefix-input"><span style="${testPrefixColor}">b#</span><input id="new_free_bug_${req.id}" inputmode="numeric" oninput="digitsOnly(this)" placeholder="新增自由Bug" ${testDisabled}></div>
            <button ${testDisabled} onclick="addFreeBug(${req.id})">添加自由Bug</button>
          </div>
        </div>
      </details>`;
  }).join('');

  const mineCards = document.getElementById('mineCards');
  if (mineCards) mineCards.innerHTML = (state.currentFeedbackTodoHtml || '') + state.currentDispatchHtml + reqsHtml;
}

export function rememberMineReqFold(reqId, isOpen) {
  const map = getFoldStateMap();
  map[String(reqId)] = !!isOpen;
  setFoldStateMap(map);
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
window.OmniQAMineTab = {
  toggleMineMode,
  loadMyWorkbench,
  renderMineCards,
  rememberMineReqFold,
  editWorkbenchCase,
  editWorkbenchBug,
  removeWorkbenchBug,
  setReqStatus,
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
};


