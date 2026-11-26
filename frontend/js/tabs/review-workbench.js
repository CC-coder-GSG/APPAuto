// 审查工作台：所选大版本全部需求（不按负责人过滤），全员对用例进行审查。
// 大部分展示逻辑与需求工作台同构（复用 mine-req-card 样式与流光动效），
// 但不含用例完成/测试完成勾选与 Bug 列表。2026-11-24 排版 v2：共用 mrc-* 结构（weave.css R6/R8/R9）。
import { api } from '../api.js';
import { escapeHtml, renderCaseLink, renderPreviewBtn, renderTaskStoryChangeBadge } from '../utils.js?v=20261126-hc-1';
import { renderCaseReviewControls } from '../components/case-review.js?v=20260714-1';

let reviewData = [];
let reviewSseBound = false;
// 默认全部展开；记住用户手动收起的需求，刷新/审查后重新渲染时保持收起
const collapsedReviewIds = new Set();
window.reviewRememberFold = (reqId, open) => {
  if (open) collapsedReviewIds.delete(Number(reqId));
  else collapsedReviewIds.add(Number(reqId));
};
document.addEventListener('zentao-story-change-confirmed', () => {
  if (window.isWorkbenchSubtabActive?.('review')) loadReviewWorkbench().catch(() => {});
});

const TASK_STATUS_ZH = { wait: '未开始', doing: '进行中', changed: '进行中', done: '已完成', pause: '已暂停', cancel: '已取消', closed: '已关闭' };

export async function loadReviewWorkbench() {
  const majorId = Number(document.getElementById('reviewMajorSelect')?.value || 0);
  const hintEl = document.getElementById('reviewCards');
  if (!majorId) {
    reviewData = [];
    renderReviewStats();
    if (hintEl) hintEl.innerHTML = '<div class="tw-empty">请先选择大版本。</div>';
    return;
  }
  let url = `/workbench/review?major_version_id=${majorId}`;
  const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  if (softwareId) url += `&software_id=${softwareId}`;
  reviewData = await (await api(url)).json();
  renderReviewStats();
  renderReviewCards();
}

function collectAllCases() {
  const seen = new Map(); // case_key → case（跨需求去重）
  (reviewData || []).forEach((req) => {
    (req.test_cases || []).forEach((c) => {
      const key = c.case_key || String(c.zentao_case_id || '').replace(/\D/g, '');
      if (key && !seen.has(key)) seen.set(key, c);
    });
  });
  return [...seen.values()];
}

// ── 顶部审查统计：总数/已审/未审 + 按人计数（有一人审查即算已审查） ──────
// 2026-11-24 排版 v2：进度条 + 状态计数 + 审查人，样式见 weave.css R9

function caseReviewState(c) {
  const active = c.reviews || [];
  if (active.some((r) => r.status === 'failed')) return 'failed';
  if (active.length) return 'passed';
  return 'pending';
}

function renderReviewStats() {
  const bar = document.getElementById('reviewStatsBar');
  if (!bar) return;
  const cases = collectAllCases();
  if (!cases.length) {
    bar.innerHTML = `<span class="rv-stats-empty">${reviewData.length ? '当前大版本暂无用例。' : '选择大版本后显示审查进度。'}</span>`;
    return;
  }
  const reviewed = cases.filter((c) => (c.reviews || []).length > 0);
  const failed = cases.filter((c) => caseReviewState(c) === 'failed');
  const pct = Math.round((reviewed.length / cases.length) * 100);
  const perReviewer = new Map();
  cases.forEach((c) => {
    (c.reviews || []).forEach((r) => {
      perReviewer.set(r.reviewer_name, (perReviewer.get(r.reviewer_name) || 0) + 1);
    });
  });
  const reviewers = [...perReviewer.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([name, n]) => `<span class="rv-reviewer"><i>${escapeHtml(Array.from(String(name || '?'))[0].toUpperCase())}</i>${escapeHtml(name)}<b>${n}</b></span>`)
    .join('');
  bar.innerHTML = `
    <div class="rv-progress">
      <div class="rv-progress-head">
        <strong>审查进度</strong>
        <span class="rv-progress-pct">${pct}%</span>
        <span class="rv-progress-sub">已审查 ${reviewed.length} / 共 ${cases.length} 个用例</span>
      </div>
      <div class="rv-progress-bar" aria-hidden="true">
        <i class="is-pass" style="width:${((reviewed.length - failed.length) / cases.length) * 100}%"></i><i class="is-fail" style="width:${(failed.length / cases.length) * 100}%"></i>
      </div>
    </div>
    <div class="rv-counts">
      <span class="rv-count rv-count--ok"><b>${reviewed.length - failed.length}</b>通过</span>
      <span class="rv-count rv-count--bad"><b>${failed.length}</b>不通过</span>
      <span class="rv-count rv-count--warn"><b>${cases.length - reviewed.length}</b>未审查</span>
    </div>
    ${reviewers ? `<div class="rv-reviewers"><span class="rv-reviewers-label">审查人</span>${reviewers}</div>` : ''}`;
}

// ── 卡片渲染（与需求卡共用 mrc-* 结构，去勾选/去Bug） ─────────────────────

function renderReviewCaseItem(c, reqId) {
  const caseZtId = String(c.zentao_case_id || '').replace(/\D/g, '');
  const metaBits = [
    c.belongs ? `产品/模块：${escapeHtml(c.belongs)}` : '',
    c.creator ? `创建人：${escapeHtml(c.creator)}` : '',
  ].filter(Boolean).join('<span class="tw-dot">·</span>');
  return `
    <div class="case-item rv-case rv-case--${caseReviewState(c)}">
      <div class="rv-case-main">
        <div class="rv-case-title">
          ${renderCaseLink(c)}${renderPreviewBtn('testcase', caseZtId)}
          ${c.title ? `<span class="rv-case-name" title="${escapeHtml(String(c.title))}">${escapeHtml(String(c.title))}</span>` : ''}
        </div>
        ${metaBits ? `<div class="rv-case-meta">${metaBits}</div>` : ''}
      </div>
      <div class="rv-case-review">${renderCaseReviewControls(c, reqId)}</div>
    </div>`;
}

function renderReviewCard(req) {
  const ztStoryId = (req.zentao_req_id || '').replace(/\D/g, '');
  const cases = req.test_cases || [];
  const reviewedN = cases.filter((c) => (c.reviews || []).length > 0).length;
  const failedN = cases.filter((c) => caseReviewState(c) === 'failed').length;
  const progress = cases.length
    ? `<span class="mrc-chip ${reviewedN === cases.length && !failedN ? 'mrc-chip--ok' : 'mrc-chip--outline'}" title="已审查用例 / 用例总数">审查 ${reviewedN}/${cases.length}</span>${failedN ? `<span class="mrc-chip mrc-chip--bad">不通过 ${failedN}</span>` : ''}`
    : '<span class="mrc-chip mrc-chip--muted">暂无用例</span>';

  const taskZh = TASK_STATUS_ZH[req.zentao_task_status] || req.zentao_task_status || '';
  const facts = [];
  if (req.zentao_task_id) {
    const changeBadge = renderTaskStoryChangeBadge(req);
    facts.push(changeBadge
      ? `<span class="tw-fact"><em>禅道子任务</em>${changeBadge}</span>`
      : `<span class="tw-fact"><em>禅道子任务</em>#${Number(req.zentao_task_id)}${taskZh ? `<span class="mrc-chip mrc-chip--info">${escapeHtml(taskZh)}</span>` : ''}${renderPreviewBtn('task', req.zentao_task_id)}</span>`);
    if (req.zentao_task_assigned_to) facts.push(`<span class="tw-fact"><em>任务指派</em>${escapeHtml(req.zentao_task_assigned_to)}</span>`);
  } else {
    facts.push('<span class="tw-fact"><em>禅道子任务</em><span class="tw-hint">未关联</span></span>');
  }
  facts.push(`<span class="tw-fact"><em>测试要点</em><span class="mrc-chip ${req.test_notes ? 'mrc-chip--ok' : 'mrc-chip--muted'}">${req.test_notes ? '已填写' : '未填写'}</span></span>`);
  const notesBtn = `<button type="button" class="mrc-btn tw-btn tw-btn--ghost secondary" onclick="event.stopPropagation(); reviewEditTestNotes(${Number(req.id)})">
    <svg class="ui-ic tw-ic" aria-hidden="true"><use href="#i-book"/></svg><span>${req.test_notes ? '查看 / 编辑测试要点' : '填写测试要点'}</span></button>`;

  const caseHtml = cases.length
    ? cases.map((c) => renderReviewCaseItem(c, req.id)).join('')
    : '<span class="mrc-empty">该需求暂无关联用例。</span>';
  const open = !collapsedReviewIds.has(Number(req.id));

  return `
    <details class="mine-req-card review-req-card" data-req-id="${req.id}" ${open ? 'open' : ''} ontoggle="reviewRememberFold(${Number(req.id)}, this.open)">
      <summary class="mrc-head">
        <svg class="mrc-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
        <div class="mrc-title">
          <span class="mrc-id"><span class="qa-story-id-nohref" data-zt-story-id="${ztStoryId}">${escapeHtml(req.zentao_req_id || '')}</span></span>
          <span class="mrc-name">${escapeHtml(req.title || '')}</span>
          ${renderPreviewBtn('story', ztStoryId)}
          ${renderTaskStoryChangeBadge(req)}
        </div>
        <div class="mrc-status">
          ${progress}
          <span class="rv-owner" title="需求负责人"><svg class="ui-ic tw-ic" aria-hidden="true"><use href="#i-user"/></svg>${escapeHtml(req.owner_name || '未分配')}</span>
        </div>
      </summary>
      <div class="mrc-body">
        <div class="rv-info">
          <div class="tw-facts">${facts.join('')}</div>
          ${notesBtn}
        </div>
        <div class="mrc-section rv-cases">
          <div class="mrc-section-head">用例 <span class="n">${cases.length}</span></div>
          <div class="mrc-section-list">${caseHtml}</div>
        </div>
      </div>
    </details>`;
}

export function renderReviewCards() {
  const wrap = document.getElementById('reviewCards');
  if (!wrap) return;
  const kw = (document.getElementById('reviewSearchInput')?.value || '').trim().toLowerCase();
  const rows = (reviewData || []).filter((req) => {
    if (!kw) return true;
    return (req.zentao_req_id && req.zentao_req_id.toLowerCase().includes(kw))
      || (req.title && req.title.toLowerCase().includes(kw))
      || (req.owner_name && req.owner_name.toLowerCase().includes(kw))
      || (req.test_cases || []).some((c) => (c.zentao_case_id && String(c.zentao_case_id).toLowerCase().includes(kw))
        || (c.title && String(c.title).toLowerCase().includes(kw)));
  });

  if (!rows.length) {
    wrap.innerHTML = `<div class="tw-empty">${kw ? '没有匹配的需求，换个关键字试试。' : '当前大版本暂无需求。'}</div>`;
    window.scheduleWorkbenchViewportResize?.();
    return;
  }

  wrap.innerHTML = rows.map(renderReviewCard).join('');

  window.OmniQAZentao?.hydrateContainer(wrap);
  window.scheduleWorkbenchViewportResize?.();
  if (window.OmniQASSE?.mountAttention) {
    wrap.querySelectorAll('.review-req-card[data-req-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'review_requirement', key: el.getAttribute('data-req-id'), tone: 'purple', hoverDelayMs: 420 });
    });
  }
}

// 审查工作台复用需求工作台的富文本编辑器；两边保存到同一需求字段。
window.reviewEditTestNotes = (reqId) => {
  const req = (reviewData || []).find((r) => Number(r.id) === Number(reqId));
  if (!req) {
    window.showMessage && window.showMessage('需求不存在', 'error');
    return;
  }
  const openEditor = window.OmniQAMineTab?.openReqTestNotesEditor;
  if (typeof openEditor !== 'function') {
    window.showMessage && window.showMessage('测试要点编辑器加载失败，请刷新页面后重试', 'error');
    return;
  }
  openEditor(req, loadReviewWorkbench);
};

function bindReviewWorkbenchSSE() {
  if (reviewSseBound) return;
  if (!window.OmniQASSE?.subscribe) return;
  // 新需求/新用例同步进来时，若审查台在前台则静默刷新
  ['workbench_requirement_created', 'workbench_testcase_created', 'requirement_test_notes_updated'].forEach((ev) => {
    window.OmniQASSE.subscribe(ev, () => {
      if (!window.isWorkbenchSubtabActive?.('review')) return;
      if (window._reviewSSETimer) clearTimeout(window._reviewSSETimer);
      window._reviewSSETimer = setTimeout(() => {
        window._reviewSSETimer = null;
        loadReviewWorkbench().catch(() => {});
      }, 800);
    });
  });
  reviewSseBound = true;
}
bindReviewWorkbenchSSE();
setTimeout(bindReviewWorkbenchSSE, 3000);

window.OmniQAReviewTab = {
  loadReviewWorkbench,
  renderReviewCards,
};
