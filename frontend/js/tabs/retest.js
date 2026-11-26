import { api } from '../api.js';
import { state } from '../state.js';
import { escapeHtml, renderBugLink, renderCaseLink, renderPreviewBtn, renderTaskStoryChangeBadge } from '../utils.js?v=20261126-hc-1';
let retestSseBound = false;
let retestPreflightPromise = null;
let retestPreflightKey = '';
let retestPreflightAt = 0;
let retestPreflightHandled = null;
let retestLoadSeq = 0;
let retestLoadController = null;
let retestRenderedKey = null;

function getRetestSelection() {
  const mode = getRetestMode();
  const majorId = Number(document.getElementById('retestMajorSelect')?.value || 0);
  const sid = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  return { mode, majorId, sid, key: `${sid}:${mode}:${mode === 'version' ? majorId : 0}` };
}

function getRetestMode() {
  return document.getElementById('retestDisplayMode')?.value || 'version';
}

function renderAutoLinkedBadge(label = '自动归集') {
  return `<span class="mrc-chip mrc-chip--info">${label}</span>`;
}

// 禅道校对徽章：本地关联的用例/Bug 与禅道镜像比对不一致时提示（与 mine.js 一致）
function renderZentaoCheckBadge(item) {
  if (item.zentao_deleted) {
    return '<span class="mrc-chip mrc-chip--bad" title="禅道侧该记录已删除，本地关联可能已失效">禅道已删除</span>';
  }
  if (item.story_mismatch) {
    const sid = escapeHtml(item.mirror_story_id || item.zentao_story_id || '?');
    return `<span class="mrc-chip mrc-chip--warn" title="禅道上该记录归属另一需求（story #${sid}），与本需求不一致，请核实关联">归属不一致</span>`;
  }
  return '';
}

// story 关联禅道任务标签（与 mine.js 一致）：最多平铺 3 个，其余折叠 +N
const TASK_CHIP_TONE = {
  doing: { zh: '进行中', bg: '#eff6ff', fg: '#1d4ed8', bd: '#bfdbfe' },
  changed: { zh: '进行中', bg: '#eff6ff', fg: '#1d4ed8', bd: '#bfdbfe' },
  wait: { zh: '未开始', bg: '#f8fafc', fg: '#475569', bd: '#e2e8f0' },
  pause: { zh: '已暂停', bg: '#fffbeb', fg: '#b45309', bd: '#fde68a' },
  done: { zh: '已完成', bg: '#f0fdf4', fg: '#15803d', bd: '#bbf7d0' },
  cancel: { zh: '已取消', bg: '#f8fafc', fg: '#94a3b8', bd: '#e2e8f0' },
  closed: { zh: '已关闭', bg: '#f8fafc', fg: '#94a3b8', bd: '#e2e8f0' },
};
const TASK_HC_TONE = { doing: 'blue', changed: 'blue', wait: 'gray', pause: 'amber', done: 'green', cancel: 'gray', closed: 'gray' };
function renderStoryTaskChips(tasks, domScope, reqId) {
  if (!tasks || !tasks.length) return '';
  const MAX_INLINE = 3;
  const chip = (t) => {
    if (t.task_story_change_pending) return renderTaskStoryChangeBadge(t);
    const tone = TASK_CHIP_TONE[t.status] || TASK_CHIP_TONE.wait;
    const struck = t.status === 'cancel' || t.status === 'closed' ? ' qa-task-chip--closed' : '';
    const assignee = t.assigned_to_name ? `<span class="qa-task-chip-assignee">👤${escapeHtml(t.assigned_to_name)}</span>` : '';
    // 完成者单独展示：任务完成后 assignedTo 常已流转给下一环节的人
    const finisher = t.finished_by_name ? `<span class="qa-task-chip-assignee">✔${escapeHtml(t.finished_by_name)}完成</span>` : '';
    // 悬浮信息卡（components/hover-card.js）
    const card = {
      k: '禅道任务', id: `#${t.task_id}`, t: t.name || '', s: tone.zh, tone: TASK_HC_TONE[t.status] || 'gray',
      rows: [['当前指派', t.assigned_to_name || ''], ['完成人', t.finished_by_name || '']],
      hint: '点击预览任务详情',
    };
    const label = `任务 #${t.task_id}【${tone.zh}】${t.name || ''}`;
    return `<span class="qa-task-chip${struck}" style="background:${tone.bg}; color:${tone.fg}; border-color:${tone.bd};" data-hc="${escapeHtml(JSON.stringify(card))}" aria-label="${escapeHtml(label)}"
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
  return `<span class="mrc-chip mrc-chip--purple" title="该Bug记录在其他大版本下">来自 ${escapeHtml(b.major_version_no)}</span>`;
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
    // A completed sync must not interrupt a version switch already in progress.
    if (retestLoadController || getRetestSelection().sid !== softwareId) return;
    loadRetest({ background: true }).catch((err) => console.warn('retest reload after preflight failed', err));
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

export async function loadRetest({ background = false } = {}) {
  // Invalidate the whole previous load, including its final-test status request.
  const seq = ++retestLoadSeq;
  retestLoadController?.abort();
  const controller = new AbortController();
  retestLoadController = controller;
  syncRetestMinorOptionsByMode();
  const { mode, majorId, sid, key } = getRetestSelection();
  const isCurrent = () => seq === retestLoadSeq && key === getRetestSelection().key;
  const container = document.getElementById('retestCardsArea');
  const keepCards = background && retestRenderedKey === key;
  if (!keepCards) {
    state.currentRetestData = [];
    retestRenderedKey = null;
    renderRetestStats([]);
    if (container) container.innerHTML = rtState('正在加载复测需求，请稍候…', 'loading');
  }
  container?.setAttribute('aria-busy', 'true');

  try {
    if (mode === 'version' && !majorId) {
      if (container) container.innerHTML = rtState('请选择大版本后加载复测需求');
      window.showMessage && window.showMessage('请选择大版本', 'error');
      return;
    }

    // 该大版本若已进入最终测试阶段：复测流程暂停，不再统计待复测需求，
    // 改为提示前往工作台处理。取消最终测试后自动恢复正常复测。
    if (mode === 'version' && majorId) {
      let finalTestEnabled = false;
      try {
        const st = await (await api(`/final-test/status?major_version_id=${majorId}`, { signal: controller.signal })).json();
        finalTestEnabled = !!st.enabled;
      } catch {
        if (!isCurrent() || controller.signal.aborted) return;
        finalTestEnabled = false;
      }
      if (!isCurrent()) return;
      if (finalTestEnabled) {
        state.currentRetestData = [];
        const container = document.getElementById('retestCardsArea');
        if (container) {
          renderRetestStats([]);
          container.innerHTML = `<div class="rt-final" role="status">
            <strong>当前版本已进入最终测试阶段</strong>
            <div>复测流程已暂停，不再统计待复测需求。请前往「我的工作台」对全部需求进行处理。</div>
            <small>在「任务分配台」取消最终测试状态后，复测将自动恢复正常。</small>
          </div>`;
        }
        return;
      }
    }

    let url = '/workbench/retest?mode=' + mode;
    if (mode === 'version' && majorId) url += '&major_version_id=' + majorId;
    if (sid) url += '&software_id=' + sid;

    const data = await (await api(url, { signal: controller.signal })).json();
    if (!isCurrent()) return;
    state.currentRetestData = data;
    renderRetestCards(data, mode, sid);
    retestRenderedKey = key;
  } catch (err) {
    if (!isCurrent() || controller.signal.aborted) return;
    if (!keepCards && container) {
      state.currentRetestData = [];
      container.innerHTML = `<div class="rt-state rt-state--error" role="alert"><span>加载复测需求失败：${escapeHtml(err.message || '请稍后重试')}</span><button type="button" class="secondary" onclick="window.OmniQARetestTab.loadRetest().catch(() => {})">重试</button></div>`;
    }
    throw err;
  } finally {
    // An obsolete request must not clear the new request's loading indicator.
    if (seq === retestLoadSeq) {
      container?.setAttribute('aria-busy', 'false');
      retestLoadController = null;
    }
  }
}

// ── 卡片渲染（2026-11-24 排版 v2：与需求卡共用 mrc-* 结构，样式见 weave.css R6/R8/R11） ──

const RT_FILTERS = [
  ['all', '全部'],
  ['pending', '待复测'],
  ['failed', '出现问题'],
  ['passed', '已通过'],
];
let retestFilter = 'all';
const rtIcon = (id) => `<svg class="ui-ic tw-ic" aria-hidden="true"><use href="#i-${id}"/></svg>`;
const rtChip = (text, tone = '', title = '') =>
  `<span class="mrc-chip${tone ? ` mrc-chip--${tone}` : ''}"${title ? ` title="${escapeHtml(title)}"` : ''}>${text}</span>`;
const rtConclusion = (req) => req.retest_conclusion || 'pending';

function rtState(html, tone = '') {
  return `<div class="rt-state${tone ? ` rt-state--${tone}` : ''}" role="status">${html}</div>`;
}

// 顶部状态统计 = 筛选（数字为该结论下的需求数）
function renderRetestStats(data) {
  const el = document.getElementById('retestStats');
  if (!el) return;
  if (!data || !data.length) { el.innerHTML = ''; return; }
  const count = (key) => (key === 'all' ? data.length : data.filter((r) => rtConclusion(r) === key).length);
  el.innerHTML = `<div class="tw-stat-group" role="group" aria-label="按复测结论筛选">${RT_FILTERS.map(([key, label]) => `
    <button type="button" class="tw-stat${retestFilter === key ? ' is-on' : ''}${key === 'failed' && count(key) ? ' is-alert' : ''}" aria-pressed="${retestFilter === key}"
      onclick="window.OmniQARetestTab.setRetestFilter('${key}')"><span>${label}</span><b>${count(key)}</b></button>`).join('')}
  </div>`;
}

export function setRetestFilter(key) {
  retestFilter = RT_FILTERS.some(([k]) => k === key) ? key : 'all';
  const { mode, sid } = getRetestSelection();
  renderRetestCards(state.currentRetestData || [], mode, sid, { skipPreflight: true });
}

// 复测问题相关的行内控件：问题标签 +「取消复测结论」误报勾选 + 旧 Bug 的「激活」入口
//（走禅道真实激活弹窗，成功后记录复测激活留痕）
function rtProblemBits(req, b) {
  const bits = [];
  const ztNumeric = (b.bug_id || '').replace(/\D/g, '') || String(b.zentao_bug_id || '').replace(/\D/g, '');
  if (b.retest_problem_kind) {
    if (b.retest_problem) {
      bits.push(rtChip('复测出现的 Bug', 'solid-bad'));
      if (b.retest_problem_kind === 'activated') {
        bits.push(rtChip(`复测激活${b.retest_activated_by_name ? ` · ${escapeHtml(b.retest_activated_by_name)}` : ''}`, 'bad'));
      }
    } else {
      bits.push(rtChip('已标记误报', 'muted'));
    }
    bits.push(`<label class="rt-dismiss" title="勾选表示该问题属误发现，不计入复测结论"><input type="checkbox" ${b.retest_dismissed ? 'checked' : ''} onchange="window.OmniQARetestTab.retestDismissBug(${Number(b.id)}, ${Number(req.id)}, this.checked)">取消复测结论</label>`);
  } else if (!b.retest_activated && ztNumeric) {
    bits.push(`<button type="button" class="mrc-btn tw-btn tw-btn--danger secondary rt-activate" title="在禅道中重新激活该 Bug，并记录为复测发现未修好"
      onclick="window.OmniQARetestTab.retestActivateBug(${Number(b.id)}, ${Number(ztNumeric)}, ${Number(req.id)})">${rtIcon('rotate')}<span>激活</span></button>`);
  }
  return bits.join('');
}

function rtBugRow(req, b, { note = '', extraTags = '' } = {}) {
  const ztBugId = (b.bug_id || '').replace(/\D/g, '');
  const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}"></span>` : '';
  const found = note || `发现于 ${escapeHtml(b.found_minor_version_no || '未知')}`;
  return `<div class="badge mrc-bug rt-bug${b.retest_problem ? ' is-retest-problem' : ''}">
    <span class="mrc-bug-main">${renderBugLink(b)}${ztSlot}${renderPreviewBtn('bug', ztBugId)}<span class="rt-bug-note">${found}</span></span>
    <span class="mrc-bug-tags">${renderCrossMajorBadge(req, b)}${extraTags}${b.auto_linked ? renderAutoLinkedBadge('自动归集') : ''}${renderZentaoCheckBadge(b)}${rtProblemBits(req, b)}</span>
  </div>`;
}

function rtCaseItem(req, c) {
  const caseZtId = String(c.zentao_case_id || '').replace(/\D/g, '');
  const bugs = (c.bugs || []).map((b) => rtBugRow(req, b)).join('');
  return `<div class="case-item rt-case">
    <div class="case-item-head rt-case-head">
      <span class="rt-case-id">${renderCaseLink(c)}</span>${renderPreviewBtn('testcase', caseZtId)}
      ${c.auto_linked ? renderAutoLinkedBadge('自动归集用例') : ''}${renderZentaoCheckBadge(c)}
      ${bugs ? rtChip(`${(c.bugs || []).length} 个 Bug`, 'outline') : rtChip('无关联 Bug', 'ok')}
    </div>
    ${bugs ? `<div class="case-bugs">${bugs}</div>` : ''}
  </div>`;
}

function rtNotes(req) {
  // 原测试人填写的测试要点（只读展示，编辑仍在需求工作台）：优先富文本版
  const inner = req.test_notes_html || (req.test_notes ? escapeHtml(req.test_notes).replace(/\n/g, '<br>') : '');
  if (!inner) return `<div class="rt-notes is-empty">${rtIcon('book')}<span>原测试人未填写测试要点</span></div>`;
  const meta = [req.test_notes_updated_by_name, req.test_notes_updated_at ? new Date(req.test_notes_updated_at).toLocaleString() : '']
    .filter(Boolean).join(' · ');
  return `<div class="rt-notes">
    <div class="rt-notes-head">${rtIcon('book')}<span>原测试人填写的测试要点</span>${meta ? `<em>${escapeHtml(meta)}</em>` : ''}</div>
    <div class="qa-rich-view">${inner}</div>
  </div>`;
}

function renderRetestCard(req, mode) {
  // 复测结论（后端统一判定）：pending 未处理 / failed 复测出现问题 / passed 复测通过
  const conclusion = rtConclusion(req);
  const activeProblems = Number(req.retest_active_problem_count || 0);
  const isCompleted = req.my_retest_completed;

  // 需求标题：禅道需求号由 hydrator 升级为链接，并附预览按钮
  const ztStoryId = (req.zentao_req_id || '').replace(/\D/g, '');
  const ztStorySlot = ztStoryId ? `<span class="zt-story-slot" data-zt-story-id="${ztStoryId}"></span>` : '';
  const reqIdHtml = ztStoryId
    ? `<span class="qa-story-id-nohref" data-zt-story-id="${ztStoryId}">${escapeHtml(req.zentao_req_id)}</span>`
    : escapeHtml(req.zentao_req_id || '');

  const conclusionChip = conclusion === 'failed'
    ? rtChip('复测出现问题', 'solid-bad')
    : conclusion === 'passed' ? rtChip('复测通过', 'ok') : rtChip('待复测', 'outline');
  const passBlocked = activeProblems > 0;
  const passBtn = `<button type="button" class="mrc-btn tw-btn rt-pass${passBlocked ? ' is-blocked' : ' tw-btn--start'}"
      title="${passBlocked ? '存在复测问题 Bug，无法通过；若属误报请先勾选「取消复测结论」' : '确认该需求复测通过'}"
      aria-disabled="${passBlocked}" onclick="event.preventDefault(); event.stopPropagation(); setRetest(${Number(req.id)}, true, ${activeProblems})">${rtIcon('check')}<span>通过</span></button>`;

  // 复测结论：我的显式结论 + 所有复测人记录 + 问题归属人
  const myTag = isCompleted
    ? (req.my_retest_passed ? rtChip('我已通过', 'ok') : rtChip('我已打回（历史）', 'bad'))
    : '';
  const records = (req.retest_records || []).map((rec) =>
    rtChip(`${rec.passed ? '✓' : '✗'} ${escapeHtml(rec.user_name || '')}${rec.is_me ? '（我）' : ''}`, rec.passed ? 'ok' : 'bad')).join('');
  const failActors = (req.retest_fail_actors || []).map((n) => rtChip(`✗ ${escapeHtml(n)} 复测未通过`, 'bad')).join('');
  const verdictRow = (myTag || records || failActors)
    ? `<span class="tw-fact rt-verdicts"><em>复测结论</em>${myTag}${records}${failActors}</span>` : '';
  const taskChips = renderStoryTaskChips(req.story_tasks, 'retest', req.id);

  const cases = req.test_cases || [];
  const freeBugs = req.free_bugs || [];
  const evidenceBugs = req.retest_evidence_bugs || [];
  const caseHtml = cases.length
    ? cases.map((c) => rtCaseItem(req, c)).join('')
    : '<span class="mrc-empty">原测试人员未建立用例</span>';
  const freeHtml = freeBugs.length
    ? freeBugs.map((b) => rtBugRow(req, b)).join('')
    : '<span class="mrc-empty">暂无自由 Bug</span>';
  const evidenceHtml = evidenceBugs.map((b) => rtBugRow(req, b, {
    note: `测后自动归集${b.zentao_opened_by_name ? ` · ${escapeHtml(b.zentao_opened_by_name)} 提出` : ''}`,
    extraTags: b.closed ? rtChip('已闭环', 'ok') : rtChip('未闭环', 'warn'),
  })).join('');

  // 出现问题的卡片保持展开并红框醒目；我已通过的折叠置灰
  const cardOpen = conclusion === 'failed' || !isCompleted;
  const cls = ['retest-req-card', 'rt-card', `is-${conclusion}`];
  if (isCompleted && req.my_retest_passed) cls.push('is-mine-passed');

  return `
    <details class="${cls.join(' ')}" data-req-id="${req.id}" ${cardOpen ? 'open' : ''} ontoggle="window.scheduleWorkbenchViewportResize?.()">
      <summary class="mrc-head">
        <svg class="mrc-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
        <div class="mrc-title">
          <span class="mrc-id">${reqIdHtml}</span>
          <span class="mrc-name">${escapeHtml(req.title || '')}</span>
          ${ztStorySlot}${renderPreviewBtn('story', ztStoryId)}
          ${mode === 'all_pending' ? rtChip(escapeHtml(req.major_version_name || '未知版本'), 'info') : ''}
          ${rtChip(`${rtIcon('user')}原测试 ${escapeHtml(req.owner || '未知')}`, 'outline', '原测试人')}
          ${req.auto_linked_case_count > 0 ? renderAutoLinkedBadge(`自动归集用例 ${req.auto_linked_case_count}`) : ''}
        </div>
        <div class="mrc-status">${conclusionChip}${passBtn}</div>
      </summary>
      <div class="mrc-body">
        ${(verdictRow || taskChips) ? `<div class="tw-facts rt-facts">${verdictRow}${taskChips ? `<span class="tw-fact rt-tasks"><em>关联任务</em>${taskChips}</span>` : ''}</div>` : ''}
        ${rtNotes(req)}
        <div class="rt-columns">
          <div class="mrc-section">
            <div class="mrc-section-head">原测试用例与关联 Bug <span class="n">${cases.length}</span></div>
            <div class="mrc-section-list">${caseHtml}</div>
          </div>
          <div class="mrc-section">
            <div class="mrc-section-head">原测试发现的自由 Bug <span class="n">${freeBugs.length}</span></div>
            <div class="mrc-section-list">${freeHtml}</div>
          </div>
        </div>
        ${evidenceBugs.length ? `
        <div class="mrc-section rt-evidence">
          <div class="mrc-section-head">系统归集：测试完成后新增 Bug 候选 <span class="n">${evidenceBugs.length}</span>
            <em>${req.test_completed_at ? `测试完成于 ${escapeHtml(new Date(req.test_completed_at).toLocaleString())}` : '该需求暂无测试完成时间，归集结果可能不完整'}</em></div>
          <div class="mrc-section-list">${evidenceHtml}</div>
        </div>` : ''}
      </div>
    </details>`;
}

function renderRetestCards(data, mode, sid, { skipPreflight = false } = {}) {
  const container = document.getElementById('retestCardsArea');
  if (!container) return;
  renderRetestStats(data);
  if (!data || data.length === 0) {
    container.innerHTML = rtState('🎉 当前筛选条件下没有需要您复测的需求', 'ok');
    if (sid && !skipPreflight) scheduleRetestPreflightBackgroundRefresh(sid);
    return;
  }
  const visible = retestFilter === 'all' ? data : data.filter((req) => rtConclusion(req) === retestFilter);
  container.innerHTML = visible.length
    ? visible.map((req) => renderRetestCard(req, mode)).join('')
    : rtState('当前筛选下没有需求，可切换上方状态查看其他需求。');
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
  if (sid && !skipPreflight) scheduleRetestPreflightBackgroundRefresh(sid);
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
  if (!(window.confirmPush && await window.confirmPush())) return;
  if (getRetestMode() !== 'version') {
    window.showMessage && window.showMessage('查看所有待复测需求模式下不支持一键推送，请切换到按大版本查看后再推送', 'error');
    return;
  }
  await api(`/push/retest-result?major_version_id=${Number(document.getElementById('retestMajorSelect')?.value || 0)}`, { method: 'POST' });
  window.showMessage && window.showMessage('复测结果已推送');
}

window.OmniQARetestTab = { loadRetest, toggleRetestMode, setRetest, retestActivateBug, retestDismissBug, pushRetest, setRetestFilter };

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
    if (payload?.test_completed === false) {
      if (window._retestSSEReopenTimer) clearTimeout(window._retestSSEReopenTimer);
      window._retestSSEReopenTimer = setTimeout(() => {
        window._retestSSEReopenTimer = null;
        if (!window.isWorkbenchSubtabActive?.('retest')) return;
        loadRetest().catch(() => {});
      }, 400);
      return;
    }
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
