import { api } from '../api.js';
import { state } from '../state.js';
import { escapeHtml, renderBugLink, renderCaseLink, renderPreviewBtn } from '../utils.js';
let retestSseBound = false;
let retestPreflightPromise = null;
let retestPreflightKey = '';
let retestPreflightAt = 0;
let retestPreflightHandled = null;
let retestLoadSeq = 0;

function getRetestMode() {
  return document.getElementById('retestDisplayMode')?.value || 'version';
}

function renderAutoLinkedBadge(label = '自动归集') {
  return `<span class="badge" style="background:#ecfeff; color:#0f766e; border:1px solid #99f6e4; margin-left:6px; padding:1px 6px;">${label}</span>`;
}

// 禅道校对徽章：本地关联的用例/Bug 与禅道镜像比对不一致时提示（与 mine.js 一致）
function renderZentaoCheckBadge(item) {
  if (item.zentao_deleted) {
    return '<span title="禅道侧该记录已删除，本地关联可能已失效" style="background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; padding:1px 5px; border-radius:4px; font-size:10px; font-weight:600; margin-left:4px;">⚠禅道已删除</span>';
  }
  if (item.story_mismatch) {
    const sid = item.mirror_story_id || item.zentao_story_id || '?';
    return `<span title="禅道上该记录归属另一需求（story #${sid}），与本需求不一致，请核实关联" style="background:#fffbeb; color:#b45309; border:1px solid #fde68a; padding:1px 5px; border-radius:4px; font-size:10px; font-weight:600; margin-left:4px;">⚠归属不一致</span>`;
  }
  return '';
}

// story 关联禅道任务标签（与 mine.js 一致）：最多平铺 3 个，其余折叠 +N
const TASK_CHIP_TONE = {
  doing: { zh: '进行中', bg: '#eff6ff', fg: '#1d4ed8', bd: '#bfdbfe' },
  wait: { zh: '未开始', bg: '#f8fafc', fg: '#475569', bd: '#e2e8f0' },
  pause: { zh: '已暂停', bg: '#fffbeb', fg: '#b45309', bd: '#fde68a' },
  done: { zh: '已完成', bg: '#f0fdf4', fg: '#15803d', bd: '#bbf7d0' },
  cancel: { zh: '已取消', bg: '#f8fafc', fg: '#94a3b8', bd: '#e2e8f0' },
  closed: { zh: '已关闭', bg: '#f8fafc', fg: '#94a3b8', bd: '#e2e8f0' },
};
function renderStoryTaskChips(tasks, domScope, reqId) {
  if (!tasks || !tasks.length) return '';
  const MAX_INLINE = 3;
  const chip = (t) => {
    const tone = TASK_CHIP_TONE[t.status] || TASK_CHIP_TONE.wait;
    const struck = t.status === 'cancel' || t.status === 'closed' ? ' qa-task-chip--closed' : '';
    const assignee = t.assigned_to_name ? `<span class="qa-task-chip-assignee">👤${escapeHtml(t.assigned_to_name)}</span>` : '';
    // 完成者单独展示：任务完成后 assignedTo 常已流转给下一环节的人
    const finisher = t.finished_by_name ? `<span class="qa-task-chip-assignee">✔${escapeHtml(t.finished_by_name)}完成</span>` : '';
    const tip = `任务 #${t.task_id}【${tone.zh}】${t.name || ''}`
      + (t.assigned_to_name ? ` · 当前指派：${t.assigned_to_name}` : '')
      + (t.finished_by_name ? ` · 由 ${t.finished_by_name} 完成` : '')
      + '（点击预览）';
    return `<span class="qa-task-chip${struck}" style="background:${tone.bg}; color:${tone.fg}; border-color:${tone.bd};" title="${escapeHtml(tip)}"
      onclick="event.preventDefault(); event.stopPropagation(); window.OmniQAPreview && window.OmniQAPreview.openTask(${t.task_id})">
      ⚙#${t.task_id}<span class="qa-task-chip-title">${escapeHtml(t.name || '')}</span>${assignee}${finisher}<span class="qa-task-chip-status">${tone.zh}</span></span>`;
  };
  const head = tasks.slice(0, MAX_INLINE).map(chip).join('');
  const rest = tasks.slice(MAX_INLINE);
  let restHtml = '';
  if (rest.length) {
    const moreId = `qaTaskMore_${domScope}_${reqId}`;
    restHtml = `<span id="${moreId}" class="qa-task-chips-rest" style="display:none;">${rest.map(chip).join('')}</span>`
      + `<span class="qa-task-chip qa-task-chip-more" title="展开/收起其余 ${rest.length} 个关联任务"
          onclick="event.preventDefault(); event.stopPropagation(); const el=document.getElementById('${moreId}'); const show=el.style.display==='none'; el.style.display=show?'contents':'none'; this.firstChild.textContent=show?'收起':'+${rest.length}';"><span>+${rest.length}</span></span>`;
  }
  return `<div class="qa-task-chips">${head}${restHtml}</div>`;
}

// 跨大版本归集：Bug 挂在其他大版本下时标注来源（与工作台 mine.js 一致）
function renderCrossMajorBadge(req, b) {
  const cross = b.major_version_id && req.major_version_id
    && Number(b.major_version_id) !== Number(req.major_version_id) && b.major_version_no;
  if (!cross) return '';
  return `<span title="该Bug记录在其他大版本下" style="background:#ede9fe; color:#6d28d9; padding:1px 5px; border-radius:4px; font-size:10px; font-weight:600; margin-left:4px;">🔀来自 ${escapeHtml(b.major_version_no)}</span>`;
}

// Return the shared Promise so one sync only schedules one silent reload.
function preflightRetestData(softwareId) {
  if (!softwareId) return null;
  const key = String(softwareId);
  const now = Date.now();
  if (retestPreflightPromise && retestPreflightKey === key) {
    return retestPreflightPromise;
  }
  if (retestPreflightKey === key && now - retestPreflightAt < 30000) {
    return null;
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
  }).then(async (resp) => {
    retestPreflightAt = Date.now();
    try { return await resp.json(); } catch { return null; }
  }).catch((err) => {
    console.warn('retest preflight refresh failed', err);
    return null;
  }).finally(() => {
    retestPreflightPromise = null;
  });
  return retestPreflightPromise;
}

// Start Zentao synchronization only after cached retest data has rendered.
// Reload once in the background when the synchronization actually ran.
function scheduleRetestPreflightBackgroundRefresh(softwareId) {
  const p = preflightRetestData(softwareId);
  if (!p || typeof p.then !== 'function' || p === retestPreflightHandled) return;
  retestPreflightHandled = p;
  p.then((res) => {
    if (!res) return;
    const synced = (part) => part && part.cached !== true && !part.error;
    if (!synced(res.bugs) && !synced(res.testcases)) return;
    if (window.isWorkbenchSubtabActive && !window.isWorkbenchSubtabActive('retest')) return;
    loadRetest().catch((err) => console.warn('retest reload after preflight failed', err));
  });
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

  // 该大版本若已进入最终测试阶段：复测流程暂停，不再统计待复测需求，
  // 改为提示前往工作台处理。取消最终测试后自动恢复正常复测。
  if (mode === 'version' && majorId) {
    let finalTestEnabled = false;
    try {
      const st = await (await api(`/final-test/status?major_version_id=${majorId}`)).json();
      finalTestEnabled = !!st.enabled;
    } catch {
      finalTestEnabled = false;
    }
    if (finalTestEnabled) {
      state.currentRetestData = [];
      const container = document.getElementById('retestCardsArea');
      if (container) {
        container.innerHTML = `<div class="card" style="padding:24px; text-align:center; border:2px solid #fcd34d; background:#fffbeb;">
          <div style="font-size:18px; font-weight:700; color:#92400e;">🏁 当前版本已进入最终测试阶段</div>
          <div class="muted" style="margin-top:8px; color:#92400e;">复测流程已暂停，不再统计待复测需求。请前往「我的工作台」对全部需求进行处理。</div>
          <div class="muted" style="margin-top:4px; font-size:12px;">在「任务分配台」取消最终测试状态后，复测将自动恢复正常。</div>
        </div>`;
      }
      return;
    }
  }

  const sid = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  let url = '/workbench/retest?mode=' + mode;
  if (mode === 'version' && majorId) url += '&major_version_id=' + majorId;
  if (sid) url += '&software_id=' + sid;

  // Cache first: render local data before starting the background preflight.
  // The sequence guard prevents an older request from replacing newer filters.
  const seq = ++retestLoadSeq;
  const data = await (await api(url)).json();
  if (seq !== retestLoadSeq) return;
  state.currentRetestData = data;

  const container = document.getElementById('retestCardsArea');
  if (!container) return;
  if (!data || data.length === 0) {
    container.innerHTML = '<div class="muted" style="padding: 20px; text-align: center; background: #f8fafc; border-radius: 8px;">🎉 当前筛选条件下没有需要您复测的需求</div>';
    if (sid) scheduleRetestPreflightBackgroundRefresh(sid);
    return;
  }

  container.innerHTML = data.map((req) => {
    // 复测结论（后端统一判定）：pending 未处理 / failed 复测出现问题 / passed 复测通过
    const conclusion = req.retest_conclusion || 'pending';
    const activeProblems = Number(req.retest_active_problem_count || 0);

    // 复测问题相关的行内控件：问题徽章（着重色）+「取消复测结论」误报勾选 +
    // 旧 Bug 的「激活」入口（走禅道真实激活弹窗，成功后记录复测激活留痕）
    const problemBits = (b) => {
      const bits = [];
      const ztNumeric = (b.bug_id || '').replace(/\D/g, '') || String(b.zentao_bug_id || '').replace(/\D/g, '');
      if (b.retest_problem_kind) {
        if (b.retest_problem) {
          bits.push('<span style="background:#dc2626; color:#fff; padding:1px 6px; border-radius:4px; font-size:11px; font-weight:700;">🚨复测出现的bug</span>');
          if (b.retest_problem_kind === 'activated') {
            bits.push(`<span style="background:#fee2e2; color:#b91c1c; border:1px solid #ef4444; padding:1px 6px; border-radius:4px; font-size:11px; font-weight:700;">🔄复测激活bug${b.retest_activated_by_name ? '·' + escapeHtml(b.retest_activated_by_name) : ''}</span>`);
          }
        } else {
          bits.push('<span style="background:#f1f5f9; color:#94a3b8; border:1px solid #e2e8f0; padding:1px 6px; border-radius:4px; font-size:11px;">已标记误报</span>');
        }
        bits.push(`<label style="font-size:12px; color:#64748b; display:flex; align-items:center; gap:4px; margin:0;" title="勾选表示该问题属误发现，不计入复测结论"><input type="checkbox" ${b.retest_dismissed ? 'checked' : ''} onchange="window.OmniQARetestTab.retestDismissBug(${b.id}, ${req.id}, this.checked)"> 取消复测结论</label>`);
      } else if (!b.retest_activated && ztNumeric) {
        bits.push(`<button class="secondary" style="padding:1px 8px; font-size:12px; color:#b91c1c; border-color:#fecaca;" title="在禅道中重新激活该 Bug，并记录为复测发现未修好" onclick="window.OmniQARetestTab.retestActivateBug(${b.id}, ${ztNumeric}, ${req.id})">🔄激活</button>`);
      }
      return bits.join('');
    };
    // 问题 Bug 整条着重显示
    const problemStyle = (b, base) => (b.retest_problem
      ? 'background:#fef2f2; color:#b91c1c; border:1.5px solid #ef4444; font-weight:700; padding: 2px 6px;'
      : base);

    const caseHtml = (req.test_cases || []).map((c) => {
      const bugs = (c.bugs || []).map((b) => {
        const ztBugId = (b.bug_id || '').replace(/\D/g, '');
        const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" style="margin-left:3px;"></span>` : '';
        const linkedBadge = b.auto_linked ? renderAutoLinkedBadge('自动归集Bug') : '';
        return `<div style="margin-top:4px; display:flex; align-items:center; flex-wrap:wrap; gap:6px;">
          <span class="badge" style="${problemStyle(b, 'background:#fef2f2; color:#dc2626; padding: 2px 6px;')}">🐛 ${renderBugLink(b)}${ztSlot}${renderPreviewBtn('bug', ztBugId)} <span style="color:#94a3b8;font-size:11px;">(发现于: 🏷️${b.found_minor_version_no || '未知'})</span></span>${renderCrossMajorBadge(req, b)}${linkedBadge}${renderZentaoCheckBadge(b)}
          ${problemBits(b)}
        </div>`;
      }).join('');
      const caseZtId = String(c.zentao_case_id || '').replace(/\D/g, '');
      return `<div style="margin-bottom: 10px; padding-left: 12px; border-left: 3px solid #cbd5e1;">
        <div style="font-weight: bold; color: #475569;">🧪 用例 [${renderCaseLink(c)}]${renderPreviewBtn('testcase', caseZtId)}${c.auto_linked ? renderAutoLinkedBadge('自动归集用例') : ''}${renderZentaoCheckBadge(c)}</div>
        <div style="margin-top: 4px;">${bugs || '<span class="muted" style="font-size:12px;">✓ 完美通过，无关联Bug</span>'}</div>
      </div>`;
    }).join('');

    const freeBugHtml = (req.free_bugs || []).map((b) => {
      const ztBugId = (b.bug_id || '').replace(/\D/g, '');
      const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" style="margin-left:3px;"></span>` : '';
      const linkedBadge = b.auto_linked ? renderAutoLinkedBadge('自动归集Bug') : '';
      return `<div style="margin-bottom:6px; display:flex; align-items:center; flex-wrap:wrap; gap:6px;">
      <span class="badge" style="${problemStyle(b, 'background:#fff7ed; color:#ea580c; padding: 2px 6px;')}">🐛 ${renderBugLink(b)}${ztSlot}${renderPreviewBtn('bug', ztBugId)} <span style="color:#94a3b8;font-size:11px;">(发现于: 🏷️${b.found_minor_version_no || '未知'})</span></span>${renderCrossMajorBadge(req, b)}${linkedBadge}${renderZentaoCheckBadge(b)}
      ${problemBits(b)}
    </div>`;
    }).join('');

    const evidenceBugs = req.retest_evidence_bugs || [];
    const evidenceBugHtml = evidenceBugs.map((b) => {
      const ztBugId = (b.bug_id || '').replace(/\D/g, '');
      const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" style="margin-left:3px;"></span>` : '';
      const closedTag = b.closed
        ? '<span class="badge" style="background:#dcfce7; color:#166534; margin-left:6px; padding:1px 6px;">✅已闭环</span>'
        : '<span class="badge" style="background:#fef9c3; color:#854d0e; margin-left:6px; padding:1px 6px;">⏳未闭环</span>';
      return `<div style="margin-bottom:6px; display:flex; align-items:center; flex-wrap:wrap; gap:6px;">
      <span class="badge" style="${problemStyle(b, 'background:#fff7ed; color:#c2410c; padding: 2px 6px;')}">🐛 ${renderBugLink(b)}${ztSlot} <span style="color:#94a3b8;font-size:11px;">(测后自动归集${b.zentao_opened_by_name ? '·' + escapeHtml(b.zentao_opened_by_name) + '提出' : ''})</span></span>${renderCrossMajorBadge(req, b)}${closedTag}${renderAutoLinkedBadge('自动归集Bug')}
      ${problemBits(b)}
    </div>`;
    }).join('');

    // 需求级结论标签（全员可见）
    const conclusionTag = conclusion === 'failed'
      ? '<span class="badge" style="background:#dc2626; color:#fff; border:1.5px solid #b91c1c; font-weight:700;">🚨复测出现问题</span>'
      : conclusion === 'passed'
        ? '<span class="badge" style="background:#dcfce7; color:#166534; border:1px solid #bbf7d0;">✅复测通过</span>'
        : '<span class="badge" style="background:#f1f5f9; color:#64748b; border:1px solid #e2e8f0;">未处理</span>';
    // 我的显式结论（点过通过才有；历史打回记录保留展示）
    const isCompleted = req.my_retest_completed;
    const myTag = isCompleted
      ? (req.my_retest_passed ? '<span class="badge" style="background:#dcfce7;color:#166534;">✅我已通过</span>' : '<span class="badge" style="background:#fee2e2;color:#b91c1c;">❌我已打回(历史)</span>')
      : '';
    // 所有复测人的结论：显式记录 + 问题归属人（某人激活/提出 Bug → 某人复测未通过）
    const retestRecordsHtml = (req.retest_records || []).map((rec) => {
      const tone = rec.passed
        ? 'background:#dcfce7;color:#166534;border:1px solid #bbf7d0;'
        : 'background:#fee2e2;color:#b91c1c;border:1px solid #fecaca;';
      return `<span class="badge" style="${tone} margin-left:4px;">${rec.passed ? '✅' : '❌'} ${rec.user_name}${rec.is_me ? '（我）' : ''}</span>`;
    }).join('');
    const failActorHtml = (req.retest_fail_actors || []).map((n) =>
      `<span class="badge" style="background:#fee2e2; color:#b91c1c; border:1px solid #ef4444; font-weight:600; margin-left:4px;">❌ ${escapeHtml(n)} 复测未通过</span>`).join('');
    const retestRecordsBar = (retestRecordsHtml || failActorHtml)
      ? `<span style="margin-left:8px; font-size:12px; color:#64748b;">复测结论:</span>${retestRecordsHtml}${failActorHtml}`
      : '';

    // 需求标题：禅道需求号渲染为可跳转的蓝色链接（由 hydrator 升级），并附预览按钮（对齐需求/测试工作台）
    const ztStoryId = (req.zentao_req_id || '').replace(/\D/g, '');
    const ztStorySlot = ztStoryId ? `<span class="zt-story-slot" data-zt-story-id="${ztStoryId}" style="margin-left:6px; vertical-align:middle;"></span>` : '';
    const reqIdHtml = ztStoryId
      ? `<span class="qa-story-id-nohref" data-zt-story-id="${ztStoryId}">${req.zentao_req_id}</span>`
      : (req.zentao_req_id || '');
    const previewBtn = renderPreviewBtn('story', ztStoryId);

    // 原测试人填写的测试要点（只读展示，编辑仍在需求工作台）：优先富文本版
    const notesInner = req.test_notes_html
      || (req.test_notes ? escapeHtml(req.test_notes).replace(/\n/g, '<br>') : '');
    const notesMeta = [req.test_notes_updated_by_name, req.test_notes_updated_at ? new Date(req.test_notes_updated_at).toLocaleString() : '']
      .filter(Boolean).join(' · ');
    const notesBlock = notesInner
      ? `<div style="margin-bottom:12px; padding:10px 12px; background:#f0f9ff; border:1px solid #bae6fd; border-radius:6px;">
          <div style="font-weight:bold; color:#0369a1; margin-bottom:6px; font-size:13px;">📝 原测试人填写的测试要点${notesMeta ? `<span style="font-weight:normal; font-size:12px; color:#64748b; margin-left:8px;">${escapeHtml(notesMeta)}</span>` : ''}</div>
          <div class="qa-rich-view">${notesInner}</div>
        </div>`
      : '<div class="muted" style="margin-bottom:12px; font-size:12px;">📝 原测试人未填写测试要点</div>';

    // 出现问题的卡片保持展开并红框醒目；通过的折叠置灰
    const cardOpen = conclusion === 'failed' || !isCompleted;
    const cardBorder = conclusion === 'failed'
      ? 'border: 2px solid #ef4444; box-shadow: 0 0 0 3px rgba(239,68,68,.12);'
      : 'border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05);';
    return `
      <details class="card retest-req-card" data-req-id="${req.id}" ${cardOpen ? 'open' : ''} ontoggle="window.scheduleWorkbenchViewportResize?.()" style="${cardBorder} border-radius: 8px; margin-bottom: 16px; background: ${conclusion === 'passed' ? '#f8fafc' : '#fff'}; transition: all 0.3s;">
        <summary style="outline:none; cursor:pointer; list-style:none; display: flex; justify-content: space-between; align-items: center; border-bottom: ${cardOpen ? '1px dashed #cbd5e1' : 'none'}; padding-bottom: ${cardOpen ? '12px' : '0'}; margin-bottom: ${cardOpen ? '12px' : '0'};">
          <div style="flex:1; min-width:0;">
            <span style="font-size: 16px; font-weight: bold; color: ${conclusion === 'passed' ? '#94a3b8; text-decoration:line-through;' : '#0f172a'};">📄 ${reqIdHtml} ${req.title}</span>${ztStorySlot}${previewBtn}
            <span style="margin-left:8px;">${conclusionTag}</span>
            ${mode === 'all_pending' ? `<span class="badge" style="margin-left:8px; background:#e0f2fe; color:#0369a1; border:1px solid #bae6fd;">🏷️${req.major_version_name || '未知版本'}</span>` : ''}
            <span class="badge" style="margin-left: 12px; background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0;">👤 原测试人: ${req.owner || '未知'}</span>
            ${req.auto_linked_case_count > 0 ? renderAutoLinkedBadge(`自动归集用例 ${req.auto_linked_case_count}`) : ''}
            ${myTag ? `<span style="margin-left:8px;">${myTag}</span>` : ''}
            ${retestRecordsBar}
            ${renderStoryTaskChips(req.story_tasks, 'retest', req.id)}
          </div>
          <div onclick="event.stopPropagation()">
            <button style="background:${activeProblems > 0 ? '#94a3b8' : '#16a34a'};" title="${activeProblems > 0 ? '存在复测问题 Bug，无法通过；若属误报请先勾选「取消复测结论」' : '确认该需求复测通过'}" onclick='setRetest(${req.id}, true, ${activeProblems})'>✅通过</button>
          </div>
        </summary>

        <div style="margin-top: 12px;">
          ${notesBlock}
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
        </div>
      </details>`;
  }).join('');
  // 复测台仅展示原测试人的要点；禁用勾选项，避免产生“已修改但未保存”的错觉。
  container.querySelectorAll('.qa-rich-view input.qa-notes-check[type="checkbox"]').forEach((checkbox) => {
    checkbox.disabled = true;
  });
  window.scheduleWorkbenchViewportResize?.();
  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    window.OmniQASSE.releaseAttention?.();
    document.querySelectorAll('.retest-req-card[data-req-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'retest_requirement', key: el.getAttribute('data-req-id'), tone: 'purple', hoverDelayMs: 420 });
    });
  }
  window.OmniQAZentao?.hydrateContainer(container);
  if (sid) scheduleRetestPreflightBackgroundRefresh(sid);
}

export async function setRetest(id, passed, activeProblemCount) {
  if (Number(activeProblemCount || 0) > 0) {
    window.showMessage && window.showMessage('无法通过：该需求存在复测激活/新归集的问题 Bug。若确认属误报，请先勾选对应 Bug 的「取消复测结论」。', 'error');
    return;
  }
  const minorId = Number(document.getElementById('retestMinorSelect')?.value || 0);
  if (!minorId) {
    window.showMessage && window.showMessage('请选择当前复测发包(小版本)', 'error');
    return;
  }
  try {
    await api(`/requirements/${id}/retest`, { method: 'PUT', headers: window.H, body: { retest_completed: true, retest_passed: true, retest_minor_version_id: minorId } });
    window.showMessage && window.showMessage('🎉 复测结果已标记为通过', 'success');
    await loadRetest();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '操作失败', 'error');
  }
}

// 复测激活：先走禅道真实激活弹窗（选指派人/版本/备注，带回读确认），
// 成功后记录「复测激活」留痕 —— 该需求结论自动转为复测未通过。
export async function retestActivateBug(bugId, ztBugId, reqId) {
  const reactivate = window.OmniQAOverallTestTab?.openS5ReactivateModalAsync;
  if (typeof reactivate !== 'function' || !ztBugId) {
    window.showMessage && window.showMessage('该 Bug 未关联禅道或激活组件未加载，无法激活', 'error');
    return;
  }
  const activated = await reactivate(bugId, ztBugId);
  if (!activated) return; // 用户取消或禅道激活失败（弹窗内已提示）
  try {
    await api(`/bugs/${bugId}/retest-activated`, { method: 'PATCH', headers: window.H, body: { requirement_id: reqId } });
    window.showMessage && window.showMessage('已激活并记录为复测问题，该需求转为复测未通过', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '复测激活留痕失败', 'error');
  }
  await loadRetest();
}

// 取消复测结论（误报标记）：全部问题被标记误报后结论自动回到复测通过
export async function retestDismissBug(bugId, reqId, checked) {
  try {
    const resp = await api(`/bugs/${bugId}/retest-dismiss`, { method: 'PATCH', headers: window.H, body: { requirement_id: reqId, dismissed: !!checked } });
    const data = await resp.json();
    window.showMessage && window.showMessage(data.message || '已更新', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '操作失败', 'error');
  }
  await loadRetest();
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

window.OmniQARetestTab = { loadRetest, toggleRetestMode, setRetest, retestActivateBug, retestDismissBug, pushRetest };

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

  // 测试要点与需求/审查工作台共用；其他用户修改后刷新只读参考内容。
  window.OmniQASSE.subscribe('requirement_test_notes_updated', () => {
    if (!window.isWorkbenchSubtabActive?.('retest')) return;
    if (window._retestSSETestNotesTimer) clearTimeout(window._retestSSETestNotesTimer);
    window._retestSSETestNotesTimer = setTimeout(() => {
      window._retestSSETestNotesTimer = null;
      loadRetest().catch(() => {});
    }, 500);
  });

  retestSseBound = true;
}
bindRetestSSE();
