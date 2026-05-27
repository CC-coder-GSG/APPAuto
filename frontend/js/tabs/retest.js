import { api } from '../api.js';
import { state } from '../state.js';
import { withPrefix, renderBugLink, renderCaseLink, renderPreviewBtn } from '../utils.js';
let retestSseBound = false;
let retestPreflightPromise = null;
let retestPreflightKey = '';
let retestPreflightAt = 0;

function getRetestMode() {
  return document.getElementById('retestDisplayMode')?.value || 'version';
}

function renderAutoLinkedBadge(label = '自动归集') {
  return `<span class="badge" style="background:#ecfeff; color:#0f766e; border:1px solid #99f6e4; margin-left:6px; padding:1px 6px;">${label}</span>`;
}

async function preflightRetestData(softwareId) {
  if (!softwareId) return;
  const key = String(softwareId);
  const now = Date.now();
  if (retestPreflightPromise && retestPreflightKey === key) {
    return retestPreflightPromise;
  }
  if (retestPreflightKey === key && now - retestPreflightAt < 30000) {
    return;
  }
  retestPreflightKey = key;
  retestPreflightPromise = api('/workbench/preflight-refresh', {
    method: 'POST',
    headers: window.H,
    body: {
      software_id: softwareId,
      include_bugs: true,
      include_testcases: true,
      force: false,
    },
  }).then(() => {
    retestPreflightAt = Date.now();
  }).catch((err) => {
    console.warn('retest preflight refresh failed', err);
  }).finally(() => {
    retestPreflightPromise = null;
  });
  return retestPreflightPromise;
}

function syncRetestMinorOptionsByMode() {
  const mode = getRetestMode();
  const minorSel = document.getElementById('retestMinorSelect');
  const majorWrap = document.getElementById('retestVersionWrap');
  if (!minorSel) return;

  if (majorWrap) majorWrap.style.display = mode === 'version' ? 'flex' : 'none';

  if (mode === 'all_pending') {
    const allMinors = (window.versions || []).filter((v) => v.version_type === 'minor');
    if (allMinors.length === 0) {
      minorSel.innerHTML = "<option value=''>暂无子版本</option>";
      return;
    }
    const prev = minorSel.value;
    minorSel.innerHTML = allMinors.map((v) => `<option value='${v.id}'>${v.version_no}</option>`).join('');
    if (prev) minorSel.value = prev;
    if (!minorSel.value && allMinors[0]) minorSel.value = String(allMinors[0].id);
    return;
  }

  if (typeof window.fillMinorSelectByMajor === 'function') {
    window.fillMinorSelectByMajor('retestMajorSelect', 'retestMinorSelect');
  }
}

export function toggleRetestMode() {
  syncRetestMinorOptionsByMode();
  return loadRetest();
}

export async function loadRetest() {
  syncRetestMinorOptionsByMode();
  const mode = getRetestMode();
  const majorId = Number(document.getElementById('retestMajorSelect')?.value || 0);
  if (mode === 'version' && !majorId) {
    window.showMessage && window.showMessage('请选择大版本', 'error');
    return;
  }
  const sid = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  if (sid) {
    await preflightRetestData(sid);
  }
  let url = '/workbench/retest?mode=' + mode;
  if (mode === 'version' && majorId) url += '&major_version_id=' + majorId;
  if (sid) url += '&software_id=' + sid;

  const data = await (await api(url)).json();
  state.currentRetestData = data;

  const container = document.getElementById('retestCardsArea');
  if (!container) return;
  if (!data || data.length === 0) {
    container.innerHTML = '<div class="muted" style="padding: 20px; text-align: center; background: #f8fafc; border-radius: 8px;">🎉 当前筛选条件下没有需要您复测的需求</div>';
    return;
  }

  container.innerHTML = data.map((req) => {
    let evidenceCount = 0;
    const caseHtml = (req.test_cases || []).map((c) => {
      const bugs = (c.bugs || []).map((b) => {
        if (b.is_retest_failed) evidenceCount++;
        const ztBugId = (b.bug_id || '').replace(/\D/g, '');
        const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" style="margin-left:3px;"></span>` : '';
        const linkedBadge = b.auto_linked ? renderAutoLinkedBadge('自动归集Bug') : '';
        return `<div style="margin-top:4px;">
          <span class="badge" style="background:#fef2f2; color:#dc2626; margin-right:4px; padding: 2px 6px;">🐛 ${renderBugLink(b)}${ztSlot}${renderPreviewBtn('bug', ztBugId)} <span style="color:#94a3b8;font-size:11px;">(发现于: 🏷️${b.found_minor_version_no || '未知'})</span></span>${linkedBadge}
          <label style="font-size:12px; color:#b91c1c;"><input type="checkbox" ${b.is_retest_failed ? 'checked' : ''} onchange="toggleBugFail(${b.id}, this.checked)"> 标记未修好</label>
        </div>`;
      }).join('');
      const caseZtId = String(c.zentao_case_id || '').replace(/\D/g, '');
      return `<div style="margin-bottom: 10px; padding-left: 12px; border-left: 3px solid #cbd5e1;">
        <div style="font-weight: bold; color: #475569;">🧪 用例 [${renderCaseLink(c)}]${renderPreviewBtn('testcase', caseZtId)}${c.auto_linked ? renderAutoLinkedBadge('自动归集用例') : ''}</div>
        <div style="margin-top: 4px;">${bugs || '<span class="muted" style="font-size:12px;">✓ 完美通过，无关联Bug</span>'}</div>
      </div>`;
    }).join('');

    const freeBugHtml = (req.free_bugs || []).map((b) => {
      if (b.is_retest_failed) evidenceCount++;
      const ztBugId = (b.bug_id || '').replace(/\D/g, '');
      const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" style="margin-left:3px;"></span>` : '';
      const linkedBadge = b.auto_linked ? renderAutoLinkedBadge('自动归集Bug') : '';
      return `<div style="margin-bottom:6px;">
      <span class="badge" style="background:#fff7ed; color:#ea580c; margin-right:4px; padding: 2px 6px;">🐛 ${renderBugLink(b)}${ztSlot}${renderPreviewBtn('bug', ztBugId)} <span style="color:#94a3b8;font-size:11px;">(发现于: 🏷️${b.found_minor_version_no || '未知'})</span></span>${linkedBadge}
      <label style="font-size:12px; color:#b91c1c;"><input type="checkbox" ${b.is_retest_failed ? 'checked' : ''} onchange="toggleBugFail(${b.id}, this.checked)"> 标记未修好</label>
    </div>`;
    }).join('');

    const retestBugHtml = (req.retest_bugs || []).map((b) => {
      if (!b.closed) evidenceCount++;
      const ztBugId = (b.bug_id || '').replace(/\D/g, '');
      const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" style="margin-left:3px;"></span>` : '';
      const closedTag = b.closed
        ? '<span class="badge" style="background:#dcfce7; color:#166534; margin-left:6px; padding:1px 6px;">✅已闭环</span>'
        : '<span class="badge" style="background:#fee2e2; color:#b91c1c; margin-left:6px; padding:1px 6px;">⏳未闭环</span>';
      return `<div style="margin-bottom:6px;">
      <span class="badge" style="background:#fee2e2; color:#b91c1c; margin-right:4px; padding: 2px 6px;">🐛 ${renderBugLink(b)}${ztSlot} <span style="color:#94a3b8;font-size:11px;">(复测新增)</span></span>${closedTag}
    </div>`;
    }).join('');

    const evidenceBugs = req.retest_evidence_bugs || [];
    if (evidenceBugs.length > 0) evidenceCount += evidenceBugs.length;
    const evidenceBugHtml = evidenceBugs.map((b) => {
      const ztBugId = (b.bug_id || '').replace(/\D/g, '');
      const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" style="margin-left:3px;"></span>` : '';
      const closedTag = b.closed
        ? '<span class="badge" style="background:#dcfce7; color:#166534; margin-left:6px; padding:1px 6px;">✅已闭环</span>'
        : '<span class="badge" style="background:#fef9c3; color:#854d0e; margin-left:6px; padding:1px 6px;">⏳未闭环</span>';
      return `<div style="margin-bottom:6px;">
      <span class="badge" style="background:#fff7ed; color:#c2410c; margin-right:4px; padding: 2px 6px;">🐛 ${renderBugLink(b)}${ztSlot} <span style="color:#94a3b8;font-size:11px;">(测后自动归集)</span></span>${closedTag}${renderAutoLinkedBadge('自动归集Bug')}
    </div>`;
    }).join('');

    const hasEvidence = evidenceCount > 0;
    const isCompleted = req.retest_completed;
    const statusTag = isCompleted
      ? (req.retest_passed ? '<span class="badge" style="background:#dcfce7;color:#166534;">✅已通过</span>' : '<span class="badge" style="background:#fee2e2;color:#b91c1c;">❌已打回</span>')
      : '<span class="badge">未提交复测结论</span>';

    return `
      <details class="card retest-req-card" data-req-id="${req.id}" ${isCompleted ? '' : 'open'} style="border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 16px; background: ${isCompleted ? '#f8fafc' : '#fff'}; box-shadow: 0 1px 3px rgba(0,0,0,0.05); transition: all 0.3s;">
        <summary style="outline:none; cursor:pointer; list-style:none; display: flex; justify-content: space-between; align-items: center; border-bottom: ${isCompleted ? 'none' : '1px dashed #cbd5e1'}; padding-bottom: ${isCompleted ? '0' : '12px'}; margin-bottom: ${isCompleted ? '0' : '12px'};">
          <div>
            <span style="font-size: 16px; font-weight: bold; color: ${isCompleted ? '#94a3b8; text-decoration:line-through;' : '#0f172a'};">📄 ${req.zentao_req_id} ${req.title}</span>
            ${mode === 'all_pending' ? `<span class="badge" style="margin-left:8px; background:#e0f2fe; color:#0369a1; border:1px solid #bae6fd;">🏷️${req.major_version_name || '未知版本'}</span>` : ''}
            <span class="badge" style="margin-left: 12px; background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0;">👤 原测试人: ${req.owner || '未知'}</span>
            ${req.auto_linked_case_count > 0 ? renderAutoLinkedBadge(`自动归集用例 ${req.auto_linked_case_count}`) : ''}
            <span style="margin-left:8px;">${statusTag}</span>
          </div>
          <div onclick="event.stopPropagation()"><button style="background:#16a34a;" onclick='setRetest(${req.id}, true, ${hasEvidence})'>✅通过</button>
            <button class="danger" onclick='setRetest(${req.id}, false, ${hasEvidence})'>❌打回</button>
          </div>
        </summary>

        <div style="margin-top: 12px;">
          <div class="row" style="align-items: flex-start; margin: 0;">
            <div style="flex: 1; padding-right: 16px; border-right: 1px dashed #e2e8f0;">
              <div style="font-weight: bold; margin-bottom: 12px; color: #334155; font-size: 13px;">【原测试用例 & 关联Bug】</div>
              ${caseHtml || '<div class="muted">原测试人员未建立用例</div>'}
            </div>
            <div style="flex: 1; padding-left: 16px;">
              <div style="font-weight: bold; margin-bottom: 12px; color: #334155; font-size: 13px;">【原测试发现的自由Bug】</div>
              <div>${freeBugHtml || '<div class="muted">暂无自由Bug</div>'}</div>
            </div>
          </div>
          ${evidenceBugs.length > 0 ? `
          <div style="margin-top:12px; border-top:1px dashed #e2e8f0; padding-top:12px; background:#fffbeb; padding:10px; border-radius:6px;">
            <div style="font-weight:bold; color:#c2410c; margin-bottom:8px;">【系统归集：测试完成后新增 Bug 候选】</div>
            <div class="muted" style="font-size:12px; margin-bottom:6px;">${req.test_completed_at ? `测试完成时间：${new Date(req.test_completed_at).toLocaleString()}` : '该需求暂无测试完成时间，归集结果可能不完整'}</div>
            <div>${evidenceBugHtml}</div>
          </div>` : ''}
          <div style="margin-top:12px; border-top:1px dashed #e2e8f0; padding-top:12px;">
            <div style="font-weight:bold; color:#b91c1c; margin-bottom:8px;">【复测新增漏测 Bug】（兜底入口）</div>
            <div>${retestBugHtml || '<div class="muted">暂无复测新增Bug</div>'}</div>
            <div class="row" style="margin-top:8px;">
              <div class="prefix-input"><span>b#</span><input id="rb_${req.id}" inputmode="numeric" oninput="digitsOnly(this)" placeholder="新增漏测Bug编号（系统未自动归集到时使用）"></div>
              <button onclick="addRetestBug(${req.id})">➕新增漏测Bug</button>
            </div>
          </div>
        </div>
      </details>`;
  }).join('');
  window.scheduleWorkbenchViewportResize?.();
  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    window.OmniQASSE.releaseAttention?.();
    document.querySelectorAll('.retest-req-card[data-req-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'retest_requirement', key: el.getAttribute('data-req-id'), tone: 'purple', hoverDelayMs: 420 });
    });
  }
  window.OmniQAZentao?.hydrateContainer(container);
}

export async function setRetest(id, passed, hasEvidence) {
  if (passed && hasEvidence) {
    window.showMessage && window.showMessage('逻辑冲突：该需求存在未修好的Bug或新增漏测Bug，绝对无法标记为【通过】！', 'error');
    return;
  }
  if (!passed && !hasEvidence) {
    window.showMessage && window.showMessage('空口无凭：请至少勾选一个未修好的旧Bug，或新增一个漏测Bug作为打回证据！', 'error');
    return;
  }
  const minorId = Number(document.getElementById('retestMinorSelect')?.value || 0);
  if (!minorId) {
    window.showMessage && window.showMessage('请选择当前复测发包(小版本)', 'error');
    return;
  }
  try {
    await api(`/requirements/${id}/retest`, { method: 'PUT', headers: window.H, body: { retest_completed: true, retest_passed: passed, retest_minor_version_id: minorId } });
    window.showMessage && window.showMessage(passed ? '🎉 复测结果已标记为通过' : '🚨 已打回给原测试人', 'success');
    await loadRetest();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '操作失败', 'error');
  }
}

export async function toggleBugFail(id, checked) {
  await api(`/bugs/${id}/retest-fail`, { method: 'PATCH', headers: window.H, body: { is_retest_failed: checked } });
  window.showMessage && window.showMessage(checked ? '已标记未修好' : '已取消未修好标记', 'success');
  await loadRetest();
}

export async function addRetestBug(reqId) {
  try {
    const num = (document.getElementById('rb_' + reqId)?.value || '').trim();
    const bug = withPrefix('b#', num);
    const minorId = Number(document.getElementById('retestMinorSelect')?.value || 0);
    if (!bug) {
      window.showMessage && window.showMessage('请输入漏测Bug编号数字部分', 'error');
      return;
    }
    if (!minorId) {
      window.showMessage && window.showMessage('请选择当前复测发包(小版本)', 'error');
      return;
    }
    await api('/bugs/execution', { method: 'POST', headers: window.H, body: { bug_id: bug, minor_version_id: minorId, requirement_id: reqId, source_type: 'retest' } });
    window.showMessage && window.showMessage('复测漏测Bug已新增', 'success');
    await loadRetest();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '新增漏测Bug失败', 'error');
  }
}

export async function pushRetest() {
  if (!(window.confirmPush && window.confirmPush())) return;
  if (getRetestMode() !== 'version') {
    window.showMessage && window.showMessage('查看所有待复测需求模式下不支持一键推送，请切换到按大版本查看后再推送', 'error');
    return;
  }
  await api(`/push/retest-result?major_version_id=${Number(document.getElementById('retestMajorSelect')?.value || 0)}`, { method: 'POST' });
  window.showMessage && window.showMessage('复测结果已推送');
}

window.OmniQARetestTab = { loadRetest, toggleRetestMode, setRetest, toggleBugFail, addRetestBug, pushRetest };

function bindRetestSSE() {
  if (retestSseBound) return;
  if (!window.OmniQASSE?.subscribe) return;

  // 新进入复测需求 → 角标已由 sse.js 处理，此处增量刷新列表
  window.OmniQASSE.subscribe('retest_requirement_created', ({ payload }) => {
    const reqId = Number(payload?.id || 0);
    if (!reqId) return;
    if (window._retestSSERequirementTimer) clearTimeout(window._retestSSERequirementTimer);
    window._retestSSERequirementTimer = setTimeout(async () => {
      window._retestSSERequirementTimer = null;
      if (!window.isWorkbenchSubtabActive?.('retest')) return;
      if (typeof window.loadRetest === 'function') await window.loadRetest();
      const el = document.querySelector(`.retest-req-card[data-req-id='${reqId}']`);
      if (el) window.OmniQASSE.pulseBoundaryGlow(el, 'blue');
    }, 600);
  });

  // 复测通过/不通过 → 卡片级提示，不进角标
  window.OmniQASSE.subscribe('retest_requirement_status_changed', ({ payload }) => {
    const reqId = Number(payload?.requirement_id || 0);
    if (!reqId) return;
    const el = document.querySelector(`.retest-req-card[data-req-id='${reqId}']`);
    if (!el) return;
    // 通过 → 明快绿；不通过 → 深邃紫
    const tone = payload?.retest_passed === true ? 'green' : 'purple';
    window.OmniQASSE.pulseBoundaryGlow(el, tone);
  });

  retestSseBound = true;
}
bindRetestSSE();
