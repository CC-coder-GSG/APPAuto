import { api } from '../api.js';
import { state } from '../state.js';
import { withPrefix, sourceTypeZh, renderBugLink } from '../utils.js';
let stage5SseBound = false;
let stage5UnreadClearTimer = null;

export async function loadStage5() {
  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  if (!majorId) {
    window.showMessage && window.showMessage('请选择大版本', 'error');
    return;
  }
  const data = await (await api('/stage5/overview?major_version_id=' + majorId)).json();
  await loadS5OptionsData(majorId);
  toggleS5BugInputs();
  state.stage5Rows = data.bug_pool || [];

  const total = state.stage5Rows.length;
  const closed = state.stage5Rows.filter((b) => b.closed).length;
  const pending = total - closed;
  const rate = total === 0 ? 100 : Math.round((closed / total) * 100);

  document.getElementById('stage5Panorama')?.classList.remove('hidden');
  document.getElementById('s5TableContainer')?.classList.remove('hidden');
  document.getElementById('s5TotalBugs').innerText = total;
  document.getElementById('s5ClosedBugs').innerText = closed;
  document.getElementById('s5PendingBugs').innerText = pending;
  document.getElementById('s5ReadyRate').innerText = rate + '%';
  renderS5();
  deferClearOverallUnread();
  window.showMessage && window.showMessage('全景大盘加载成功', 'success');
}

function deferClearOverallUnread(ms = 1200) {
  if (stage5UnreadClearTimer) clearTimeout(stage5UnreadClearTimer);
  stage5UnreadClearTimer = setTimeout(() => {
    stage5UnreadClearTimer = null;
    const tab = document.getElementById('tab-stage5');
    if (!tab || tab.classList.contains('hidden')) return;
    if (!window.OmniQASSE || typeof window.OmniQASSE.clearScopeUnread !== 'function') return;
    // Do not clear unread on tab switch immediately; let new rows glow once after render.
    window.OmniQASSE.clearScopeUnread('overall_bug');
  }, ms);
}

export function renderS5() {
  const table = document.getElementById('s5Table');
  if (!table) return;
  table.innerHTML = state.stage5Rows.map((b) => {
    const isMyClosed = b.my_test_done;
    const rowStyle = isMyClosed ? 'background: #f8fafc; color: #94a3b8; text-decoration: line-through;' : '';
    let othersHtml = '';
    const resZh = { fixed: '修复通过', false_alarm: '误报', rejected: '拒绝修复' };
    if (b.other_records && b.other_records.length > 0) {
      othersHtml = `<div style="margin-top: 6px; font-size: 12px; text-decoration: none;">` + b.other_records.map((r) => {
        const status = r.test_done ? `<span style="color:#16a34a; font-weight:bold;">✅闭环(${resZh[r.resolution] || '修复'})</span>` : '<span style="color:#dc2626;">⏳未闭环</span>';
        return `<span class="badge" style="background:#f1f5f9; color:#475569; margin-right:4px; padding: 2px 6px;">🙋‍♂️ ${r.username}: ${status} (发包: 🏷️${r.minor_version_no})</span>`;
      }).join('') + `</div>`;
    }
    const failBadge = b.is_retest_failed ? '<span class="badge" style="background:#fee2e2; color:#b91c1c; border:1px solid #f87171; margin-left:4px;">🚨复测打回</span>' : '';
    const dispatchBadge = b.dispatched_to_name ? `<span class="badge" style="background:#ffedd5; color:#ea580c; border:1px solid #fdba74; margin-left:4px;">🪂特派:${b.dispatched_to_name}</span>` : '';
    return `<tr class="stage5-row-card" data-bug-id="${b.id}" style="${rowStyle}">
      <td>
        ${renderBugLink(b)} <span style="font-size:12px;color:#64748b">(${sourceTypeZh(b.source_type)})</span> ${failBadge} ${dispatchBadge}
        <a href="javascript:void(0)" onclick="editS5Bug(${b.id}, '${b.bug_id}')" style="margin-left:8px; font-size:12px; color:#3b82f6; text-decoration:none;">编辑</a>
        <a href="javascript:void(0)" onclick="removeS5Bug(${b.id})" style="margin-left:4px; font-size:12px; color:#ef4444; text-decoration:none;">删除</a>
        ${othersHtml}
      </td>
      <td style="text-decoration: none;">
        <select id='res_${b.id}' style="margin-right: 8px; padding: 2px; font-size: 13px; border: 1px solid #cbd5e1; border-radius: 4px; color: #475569;" ${isMyClosed ? 'disabled' : ''}>
          <option value="fixed" ${b.my_resolution === 'fixed' ? 'selected' : ''}>🚀修复通过</option>
          <option value="false_alarm" ${b.my_resolution === 'false_alarm' ? 'selected' : ''}>⚠️误报</option>
          <option value="rejected" ${b.my_resolution === 'rejected' ? 'selected' : ''}>⛔拒绝修复</option>
        </select>
        <label style="color: #0f172a; font-weight: bold;"><input id='done_${b.id}' type='checkbox' ${isMyClosed ? 'checked' : ''} onchange="this.nextSibling.disabled=this.checked"> 我的闭环确认</label>
        <button class="${isMyClosed ? 'secondary' : ''}" onclick='saveS5(${b.id})' style="margin-left:8px;">保存记录</button>
      </td>
    </tr>`;
  }).join('');
  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    document.querySelectorAll('.stage5-row-card[data-bug-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'overall_bug', key: el.getAttribute('data-bug-id'), tone: 'blue', hoverDelayMs: 420 });
    });
  }
}

export async function saveS5(id) {
  const done = document.getElementById('done_' + id).checked;
  const res = document.getElementById('res_' + id).value;
  await api(`/stage5/bugs/${id}/result`, { method: 'PUT', headers: window.H, body: { minor_version_id: Number(document.getElementById('s5MinorSelect')?.value || 0), test_done: done, newly_found_bug_id: null, resolution: res } });
  window.showMessage && window.showMessage('整体测试项已保存');
  await loadStage5();
}

export async function editS5Bug(id, oldBugId) {
  const num = prompt('请输入正确的 Bug 数字部分：', String(oldBugId || '').replace('b#', ''));
  if (!num) return;
  try {
    await api('/bugs/' + id + '?new_bug_id=' + encodeURIComponent(withPrefix('b#', num)), { method: 'PUT' });
    window.showMessage && window.showMessage('Bug 编号已纠正', 'success');
    await loadStage5();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '更新失败', 'error');
  }
}

export async function removeS5Bug(id) {
  if (!confirm('确定要在该大版本下移除这个 Bug 吗？')) return;
  try {
    await api('/bugs/' + id, { method: 'DELETE' });
    window.showMessage && window.showMessage('Bug 已彻底移除', 'success');
    await loadStage5();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '删除失败', 'error');
  }
}

export async function pushStage5() {
  if (!(window.confirmPush && window.confirmPush())) return;
  const res = await (await api(`/stage5/push-status?major_version_id=${Number(document.getElementById('s5MajorSelect')?.value || 0)}&minor_version_id=${Number(document.getElementById('s5MinorSelect')?.value || 0)}`, { method: 'POST' })).json();
  window.showMessage && window.showMessage(`整体状态已推送，剩余未闭环 ${res.remaining}`);
}

export function toggleS5BugInputs() {
  const sourceEl = document.getElementById('s5BugSource');
  if (!sourceEl) return;
  const type = sourceEl.value;

  const reqWrap = document.getElementById('s5WrapReq');
  const caseWrap = document.getElementById('s5WrapCase');
  const legacyWrap = document.getElementById('s5WrapLegacy');
  if (reqWrap) reqWrap.classList.toggle('hidden', type !== 'requirement');
  if (caseWrap) caseWrap.classList.toggle('hidden', type !== 'case');
  if (legacyWrap) legacyWrap.classList.toggle('hidden', type !== 'legacy_bug');

  const reqSelect = document.getElementById('s5ReqSelect');
  const caseSelect = document.getElementById('s5CaseSelect');
  const legacySelect = document.getElementById('s5LegacySelect');
  if (reqSelect) reqSelect.value = '';
  if (caseSelect) caseSelect.value = '';
  if (legacySelect) legacySelect.value = '';
}

export async function loadS5OptionsData(majorId) {
  try {
    state.globalS5Options = await (await api('/stage5/search-options?major_version_id=' + majorId)).json();
    const reqSelect = document.getElementById('s5ReqSelect');
    const caseSelect = document.getElementById('s5CaseSelect');
    const legacySelect = document.getElementById('s5LegacySelect');

    if (reqSelect) {
      reqSelect.innerHTML = '<option value="">请选择关联需求</option>' + state.globalS5Options.reqs.map((r) => `<option value="${r.id}">${r.label}</option>`).join('');
    }
    if (caseSelect) {
      caseSelect.innerHTML = '<option value="">请选择关联用例</option>' + state.globalS5Options.cases.map((c) => `<option value="${c.id}">${c.label}</option>`).join('');
    }
    if (legacySelect) {
      legacySelect.innerHTML = '<option value="">请选择关联历史Bug</option>' + state.globalS5Options.bugs.map((b) => `<option value="${b.id}">${b.label}</option>`).join('');
    }
  } catch (e) {
    console.error('搜索数据加载失败', e);
  }
}

export async function submitS5Bug() {
  const type = document.getElementById('s5BugSource').value;
  const bugInput = document.getElementById('s5BugId').value;
  if (!bugInput) {
    window.showMessage && window.showMessage('请输入新Bug编号', 'error');
    return;
  }
  const bugId = withPrefix('b#', bugInput);
  let reqId = null;
  let sourceRef = null;
  if (type === 'requirement') {
    const selectedReqId = Number(document.getElementById('s5ReqSelect').value || 0);
    const target = state.globalS5Options.reqs.find((r) => r.id === selectedReqId);
    if (!target) {
      window.showMessage && window.showMessage('请选择关联需求！', 'error');
      return;
    }
    reqId = target.id;
  } else if (type === 'case') {
    const selectedCaseId = Number(document.getElementById('s5CaseSelect').value || 0);
    const target = state.globalS5Options.cases.find((c) => c.id === selectedCaseId);
    if (!target) {
      window.showMessage && window.showMessage('请选择关联用例！', 'error');
      return;
    }
    reqId = target.req_id;
    sourceRef = target.label;
  } else if (type === 'legacy_bug') {
    const selectedLegacyBugId = Number(document.getElementById('s5LegacySelect').value || 0);
    const target = state.globalS5Options.bugs.find((b) => b.id === selectedLegacyBugId);
    if (!target) {
      window.showMessage && window.showMessage('请选择关联历史Bug！', 'error');
      return;
    }
    reqId = target.req_id;
    sourceRef = target.label;
  }
  try {
    await api('/stage5/issues', {
      method: 'POST',
      headers: window.H,
      body: { major_version_id: Number(document.getElementById('s5MajorSelect')?.value || 0), requirement_id: reqId, source_type: type, source_ref: sourceRef, bug_id: bugId, minor_version_id: Number(document.getElementById('s5MinorSelect')?.value || 0) },
    });
    window.showMessage && window.showMessage('新问题已成功添加到大盘！', 'success');
    document.getElementById('s5BugId').value = '';
    await loadStage5();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '添加失败', 'error');
  }
}

window.OmniQAStage5Tab = { loadStage5, renderS5, saveS5, editS5Bug, removeS5Bug, pushStage5, toggleS5BugInputs, loadS5OptionsData, submitS5Bug };
window.editS5Bug = editS5Bug;
window.removeS5Bug = removeS5Bug;

// ─── Stage5 SSE: 新 bug 底部提示 + 卡片流光 ──────────────────────────────────

const s5NewBugCount = { value: 0 };

function showS5BottomBanner() {
  if (!window.OmniQASSE?.showPositionBanner) return;
  const tableWrap = document.getElementById('s5TableContainer');
  if (!tableWrap) return;
  window.OmniQASSE.showPositionBanner({
    scrollContainer: tableWrap,
    anchorEl: tableWrap,
    position: 'bottom',
    countRef: s5NewBugCount,
    labelFn: (n) => `⬇ 下面有 ${n} 条新增 Bug，点击刷新`,
    onClickScroll: () => {
      s5NewBugCount.value = 0;
      loadStage5();
    },
    bannerId: 'stage5-new-bug',
  });
}

function bindStage5SSE() {
  if (stage5SseBound) return;
  if (!window.OmniQASSE?.subscribe) return;

  window.OmniQASSE.subscribe('overall_bug_created', ({ payload }) => {
    const bugId = Number(payload?.id || payload?.bug_id || 0);
    // Pulse if row exists in DOM
    if (bugId) {
      const el = document.querySelector(`.stage5-row-card[data-bug-id='${bugId}']`);
      if (el) window.OmniQASSE.pulseBoundaryGlow(el, 'blue');
    }
    // Check if user is at bottom; if not, show banner
    const tableWrap = document.getElementById('s5TableContainer');
    const atBottom = !tableWrap || (tableWrap.scrollHeight - tableWrap.scrollTop - tableWrap.clientHeight <= 80);
    if (!atBottom) {
      s5NewBugCount.value += 1;
      showS5BottomBanner();
    } else {
      // At bottom: immediately refresh list
      s5NewBugCount.value = 0;
      deferClearOverallUnread(600);
    }
  });

  window.OmniQASSE.subscribe('overall_bug_closed', ({ payload }) => {
    const bugId = Number(payload?.id || payload?.bug_id || 0);
    if (!bugId) return;
    const el = document.querySelector(`.stage5-row-card[data-bug-id='${bugId}']`);
    if (el) window.OmniQASSE.pulseBoundaryGlow(el, 'teal');
  });

  stage5SseBound = true;
}
bindStage5SSE();
