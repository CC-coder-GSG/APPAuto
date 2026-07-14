// 审查工作台：所选大版本全部需求（不按负责人过滤），全员对用例进行审查。
// 大部分展示逻辑与需求工作台同构（复用 mine-req-card 样式与流光动效），
// 但不含用例完成/测试完成勾选与 Bug 列表。
import { api } from '../api.js';
import { escapeHtml, renderCaseLink, renderPreviewBtn } from '../utils.js';
import { renderCaseReviewControls } from '../components/case-review.js?v=20260714-1';

let reviewData = [];
let reviewSseBound = false;

const TASK_STATUS_ZH = { wait: '未开始', doing: '进行中', done: '已完成', pause: '已暂停', cancel: '已取消', closed: '已关闭' };

export async function loadReviewWorkbench() {
  const majorId = Number(document.getElementById('reviewMajorSelect')?.value || 0);
  const hintEl = document.getElementById('reviewCards');
  if (!majorId) {
    reviewData = [];
    renderReviewStats();
    if (hintEl) hintEl.innerHTML = '<div class="muted" style="padding:16px;">请先选择大版本。</div>';
    return;
  }
  let url = `/workbench/review?major_version_id=${majorId}`;
  const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  if (softwareId) url += `&software_id=${softwareId}`;
  reviewData = await (await api(url)).json();
  renderReviewStats();
  renderReviewCards();
}

// ── 顶部审查统计：总数/已审/未审 + 按人计数（有一人审查即算已审查） ──────

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

function renderReviewStats() {
  const bar = document.getElementById('reviewStatsBar');
  if (!bar) return;
  const cases = collectAllCases();
  if (!cases.length) {
    bar.innerHTML = '<span class="muted" style="font-size:13px;">当前大版本暂无用例。</span>';
    return;
  }
  const reviewed = cases.filter((c) => (c.reviews || []).length > 0);
  const failed = cases.filter((c) => (c.reviews || []).some((r) => r.status === 'failed'));
  const perReviewer = new Map();
  cases.forEach((c) => {
    (c.reviews || []).forEach((r) => {
      perReviewer.set(r.reviewer_name, (perReviewer.get(r.reviewer_name) || 0) + 1);
    });
  });
  const reviewerChips = [...perReviewer.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([name, n]) => `<span class="case-review-tag review-pass" style="cursor:default;">${escapeHtml(name)} 审查 ${n} 例</span>`)
    .join('');
  bar.innerHTML = `
    <div style="display:flex; align-items:center; gap:14px; flex-wrap:wrap;">
      <b style="font-size:14px; color:#334155;">📋 审查进度</b>
      <span style="font-size:13px; color:#475569;">用例共 <b>${cases.length}</b> 个</span>
      <span style="font-size:13px; color:#166534;">已审查 <b>${reviewed.length}</b></span>
      <span style="font-size:13px; color:#b45309;">未审查 <b>${cases.length - reviewed.length}</b></span>
      ${failed.length ? `<span style="font-size:13px; color:#b91c1c;">含不通过 <b>${failed.length}</b></span>` : ''}
      ${reviewerChips ? `<span style="display:inline-flex; gap:6px; flex-wrap:wrap;">${reviewerChips}</span>` : ''}
    </div>`;
}

// ── 卡片渲染（同构需求工作台，去勾选/去Bug） ────────────────────────────

function renderReviewCaseItem(c, reqId) {
  const caseZtId = String(c.zentao_case_id || '').replace(/\D/g, '');
  const metaBits = [
    c.belongs ? `所属：${escapeHtml(c.belongs)}` : '',
    c.creator ? `创建人：${escapeHtml(c.creator)}` : '',
  ].filter(Boolean).join('　');
  return `
    <div class="case-item">
      <div class="row" style="gap:6px; flex-wrap:wrap; align-items:center;">
        ${renderCaseLink(c)}${renderPreviewBtn('testcase', caseZtId)}
        ${c.title ? `<span style="font-size:13px; color:#475569;">${escapeHtml(String(c.title).slice(0, 60))}</span>` : ''}
      </div>
      ${metaBits ? `<div class="muted" style="font-size:12px; margin-top:4px;">${metaBits}</div>` : ''}
      <div style="margin-top:6px;">${renderCaseReviewControls(c, reqId)}</div>
    </div>`;
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
    wrap.innerHTML = '<div class="muted" style="padding:16px;">没有匹配的需求。</div>';
    window.scheduleWorkbenchViewportResize?.();
    return;
  }

  wrap.innerHTML = rows.map((req) => {
    const ztStoryId = (req.zentao_req_id || '').replace(/\D/g, '');
    const previewBtn = renderPreviewBtn('story', ztStoryId);
    const taskZh = TASK_STATUS_ZH[req.zentao_task_status] || req.zentao_task_status || '';
    const taskTag = req.zentao_task_id
      ? `<span class="badge" style="background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe;">禅道子任务 #${req.zentao_task_id}${taskZh ? '·' + taskZh : ''}</span>${renderPreviewBtn('task', req.zentao_task_id)}${req.zentao_task_assigned_to ? `<span class="badge" style="background:#f1f5f9; color:#475569;">任务指派：${escapeHtml(req.zentao_task_assigned_to)}</span>` : ''}`
      : '<span class="badge" style="background:#f1f5f9; color:#94a3b8;">未关联禅道任务</span>';
    const notesTag = `<span class="badge" style="background:${req.test_notes ? '#dcfce7' : '#f1f5f9'}; color:${req.test_notes ? '#166534' : '#64748b'}; border:1px solid ${req.test_notes ? '#bbf7d0' : '#e2e8f0'};">测试要点：${req.test_notes ? '已填写' : '未填写'}</span>`;
    const notesBtn = req.test_notes
      ? `<button class="secondary" style="padding:2px 8px; font-size:12px;" onclick="reviewShowTestNotes(${req.id})">查看测试要点</button>`
      : '';
    const caseHtml = (req.test_cases || []).map((c) => renderReviewCaseItem(c, req.id)).join('')
      || '<div class="muted" style="font-size:13px;">该需求暂无关联用例。</div>';

    return `
      <details class="mine-req-card review-req-card" data-req-id="${req.id}" open style="background:#ffffff; transition:all .3s;">
        <summary style="outline:none; cursor:pointer; font-size:16px; font-weight:bold; color:#0f172a; border-bottom:1px solid #e2e8f0; padding-bottom:12px; display:flex; justify-content:space-between; align-items:center; list-style:none;">
          <div><span class="qa-story-id-nohref" data-zt-story-id="${ztStoryId}">${escapeHtml(req.zentao_req_id || '')}</span> ${escapeHtml(req.title || '')}${previewBtn}</div>
          <span class="badge" style="background:#eef2ff; color:#4338ca; border:1px solid #c7d2fe; font-weight:normal;">负责人：${escapeHtml(req.owner_name || '未分配')}</span>
        </summary>
        <div style="margin-top:12px;">
          <div class="row" style="margin-bottom:8px; gap:6px; flex-wrap:wrap;">
            ${taskTag}${notesTag}${notesBtn}
          </div>
          <div>${caseHtml}</div>
        </div>
      </details>`;
  }).join('');

  window.OmniQAZentao?.hydrateContainer(wrap);
  window.scheduleWorkbenchViewportResize?.();
  if (window.OmniQASSE?.mountAttention) {
    wrap.querySelectorAll('.review-req-card[data-req-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'review_requirement', key: el.getAttribute('data-req-id'), tone: 'purple', hoverDelayMs: 420 });
    });
  }
}

// 测试要点只读弹窗（审查台不编辑要点，编辑仍在需求工作台）
window.reviewShowTestNotes = (reqId) => {
  const req = (reviewData || []).find((r) => r.id === reqId);
  if (!req) return;
  const meta = [req.test_notes_updated_by_name, req.test_notes_updated_at ? String(req.test_notes_updated_at).replace('T', ' ').slice(0, 16) : '']
    .filter(Boolean).join(' · ');
  const modal = document.getElementById('caseReviewModal');
  if (window.caseReviewCloseModal && modal) window.caseReviewCloseModal();
  // 复用审查弹窗容器展示只读要点
  const body = `
    <div style="font-size:13px; color:#475569; white-space:pre-wrap;">${escapeHtml(req.test_notes || '')}</div>
    ${meta ? `<div class="muted" style="font-size:12px; margin-top:8px;">${escapeHtml(meta)}</div>` : ''}`;
  if (window.OmniQACaseReviewOpenModal) {
    window.OmniQACaseReviewOpenModal(`📝 测试要点 · ${escapeHtml(req.zentao_req_id || '')}`, body);
  } else {
    alert(req.test_notes || '');
  }
};

function bindReviewWorkbenchSSE() {
  if (reviewSseBound) return;
  if (!window.OmniQASSE?.subscribe) return;
  // 新需求/新用例同步进来时，若审查台在前台则静默刷新
  ['workbench_requirement_created', 'workbench_testcase_created'].forEach((ev) => {
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
