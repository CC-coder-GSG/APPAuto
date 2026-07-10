// 用例审查共享组件：审查标签 + 通过/不通过/修改完成操作 + 意见/历史弹窗。
// 需求工作台(mine.js)与审查工作台(review-workbench.js)共用，保证两边标签联动一致。
import { api } from '../api.js';
import { escapeHtml } from '../utils.js';

// 渲染期登记：case_key → { caseObj, reqId }，按钮点击时取回上下文
const caseRegistry = new Map();

const STATUS_ZH = { passed: '审查通过', failed: '审查不通过', fixed: '修改完成' };

function fmtTime(iso) {
  if (!iso) return '';
  return String(iso).replace('T', ' ').slice(0, 16);
}

function currentUserId() {
  return Number(window.OmniQAState?.state?.currentUser?.id || window.currentUser?.id || 0);
}

// ── 标签 + 操作按钮（嵌入用例条目） ──────────────────────────────────────

export function renderCaseReviewControls(c, reqId) {
  const key = String(c.case_key || String(c.zentao_case_id || '').replace(/\D/g, ''));
  if (!key) return '';
  caseRegistry.set(key, { caseObj: c, reqId: reqId ?? null });

  const active = c.reviews || [];
  const history = c.review_history || [];
  const myId = currentUserId();
  const hasFailed = active.some((r) => r.status === 'failed');

  const tags = active.map((r) => {
    const mine = Number(r.reviewer_id) === myId ? ' review-tag-mine' : '';
    if (r.status === 'failed') {
      return `<span class="case-review-tag review-fail${mine}" title="点击查看审查意见"
        onclick="caseReviewShowOpinion('${key}', ${r.id})">✗ ${escapeHtml(r.reviewer_name)} 审查不通过</span>`;
    }
    return `<span class="case-review-tag review-pass${mine}" title="${escapeHtml(r.reviewer_name)} 审查通过 ${fmtTime(r.created_at)}">✓ ${escapeHtml(r.reviewer_name)} 审查通过</span>`;
  }).join('');

  // 无 active 但有历史 → 未审查(待复审)标签，可查看历史（含谁审查过/意见/修改记录）
  const pendingTag = (!active.length && history.length)
    ? `<span class="case-review-tag review-pending" title="修改完成后回到未审查状态，点击查看审查历史"
        onclick="caseReviewShowHistory('${key}')">◌ 未审查 · 历史 ${history.length}</span>`
    : '';

  const fixBtn = hasFailed
    ? `<button class="case-review-btn review-btn-fix" onclick="caseReviewFixOpen('${key}')" title="已按审查意见修改，填写修改内容后该用例回到未审查状态">修改完成</button>`
    : '';
  const historyBtn = (active.length && history.length > active.length)
    ? `<a href="javascript:void(0)" class="case-review-history-link" onclick="caseReviewShowHistory('${key}')" title="查看全部审查/修改历史">历史</a>`
    : '';

  return `<span class="case-review-box" data-case-key="${key}">
    ${tags}${pendingTag}
    <button class="case-review-btn review-btn-pass" onclick="caseReviewSubmit('${key}', 'passed')" title="标记我对该用例审查通过">✓ 通过</button>
    <button class="case-review-btn review-btn-fail" onclick="caseReviewFailOpen('${key}')" title="审查不通过，需填写审查意见并企微播报">✗ 不通过</button>
    ${fixBtn}${historyBtn}
  </span>`;
}

// ── 提交 ──────────────────────────────────────────────────────────────────

async function refreshWorkbenches() {
  const tasks = [];
  if (window.isWorkbenchSubtabActive?.('demand') && typeof window.loadMyWorkbench === 'function') {
    tasks.push(window.loadMyWorkbench());
  }
  if (window.isWorkbenchSubtabActive?.('review') && window.OmniQAReviewTab?.loadReviewWorkbench) {
    tasks.push(window.OmniQAReviewTab.loadReviewWorkbench());
  }
  try { await Promise.all(tasks); } catch { /* 刷新失败不阻断提示 */ }
}

async function submitReview(key, status, opinion) {
  const ctx = caseRegistry.get(key);
  if (!ctx) return;
  try {
    await api('/workbench/case-reviews', {
      method: 'POST',
      headers: window.H,
      body: {
        zentao_case_id: ctx.caseObj.zentao_case_id,
        requirement_id: ctx.reqId,
        status,
        opinion: opinion || null,
      },
    });
    closeReviewModal();
    await refreshWorkbenches();
    pulseCaseCard(key);
    window.showMessage && window.showMessage(status === 'passed' ? '已标记审查通过' : '已标记审查不通过，企微已播报', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '提交审查失败', 'error');
  }
}

async function submitFix(key, content) {
  const ctx = caseRegistry.get(key);
  if (!ctx) return;
  try {
    await api('/workbench/case-reviews/fix', {
      method: 'POST',
      headers: window.H,
      body: {
        zentao_case_id: ctx.caseObj.zentao_case_id,
        requirement_id: ctx.reqId,
        content,
      },
    });
    closeReviewModal();
    await refreshWorkbenches();
    pulseCaseCard(key);
    window.showMessage && window.showMessage('已记录修改完成，用例回到未审查状态', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '提交修改完成失败', 'error');
  }
}

function pulseCaseCard(key) {
  const box = document.querySelector(`.case-review-box[data-case-key="${key}"]`);
  const card = box?.closest('.mine-req-card');
  if (card && window.OmniQASSE?.pulseBoundaryGlow) window.OmniQASSE.pulseBoundaryGlow(card, 'purple');
}

// ── 弹窗（动态挂载，避免污染 index.html） ────────────────────────────────

function ensureModal() {
  let modal = document.getElementById('caseReviewModal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'caseReviewModal';
  modal.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,.35); z-index:9999; display:none; align-items:center; justify-content:center;';
  modal.innerHTML = `
    <div style="width:min(520px,92vw); max-height:80vh; overflow:auto; background:#fff; border-radius:10px; box-shadow:0 12px 28px rgba(0,0,0,.18); padding:18px;">
      <h3 id="caseReviewModalTitle" style="margin:0 0 10px 0;"></h3>
      <div id="caseReviewModalBody"></div>
      <div class="row" style="justify-content:flex-end; gap:8px; margin-top:14px;">
        <button class="secondary" onclick="caseReviewCloseModal()">取消</button>
        <button id="caseReviewModalOk" style="display:none;">确认</button>
      </div>
    </div>`;
  modal.addEventListener('click', (e) => { if (e.target === modal) closeReviewModal(); });
  document.body.appendChild(modal);
  return modal;
}

function openModal(title, bodyHtml, okHandler, okText) {
  const modal = ensureModal();
  modal.querySelector('#caseReviewModalTitle').innerHTML = title;
  modal.querySelector('#caseReviewModalBody').innerHTML = bodyHtml;
  const ok = modal.querySelector('#caseReviewModalOk');
  if (okHandler) {
    ok.style.display = '';
    ok.textContent = okText || '确认';
    ok.onclick = okHandler;
  } else {
    ok.style.display = 'none';
    ok.onclick = null;
  }
  modal.style.display = 'flex';
}

function closeReviewModal() {
  const modal = document.getElementById('caseReviewModal');
  if (modal) modal.style.display = 'none';
}

function historyItemHtml(r) {
  const cls = r.status === 'passed' ? 'review-pass' : r.status === 'failed' ? 'review-fail' : 'review-pending';
  return `
    <div style="padding:8px 10px; border:1px solid #e2e8f0; border-radius:6px; margin-bottom:8px; background:#fafbfc;">
      <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
        <span class="case-review-tag ${cls}">${escapeHtml(STATUS_ZH[r.status] || r.status)}</span>
        <b style="font-size:13px; color:#334155;">${escapeHtml(r.reviewer_name)}</b>
        <span class="muted" style="font-size:12px;">${fmtTime(r.created_at)}</span>
      </div>
      ${r.opinion ? `<div style="margin-top:6px; font-size:13px; color:#475569; white-space:pre-wrap;">${escapeHtml(r.opinion)}</div>` : ''}
    </div>`;
}

// ── window 级句柄（inline onclick 需要） ─────────────────────────────────

window.caseReviewCloseModal = closeReviewModal;
window.OmniQACaseReviewOpenModal = openModal; // 审查台复用弹窗容器（测试要点只读等）

window.caseReviewSubmit = (key, status) => submitReview(key, status, null);

window.caseReviewFailOpen = (key) => {
  const ctx = caseRegistry.get(key);
  if (!ctx) return;
  openModal(
    `✗ 审查不通过 · ${escapeHtml(ctx.caseObj.zentao_case_id || '')}`,
    `<div class="muted" style="font-size:12px; margin-bottom:8px;">审查意见将企微播报给需求负责人，请描述问题点。</div>
     <textarea id="caseReviewOpinionInput" style="width:100%; min-height:110px; padding:8px; border:1px solid #cbd5e1; border-radius:6px; resize:vertical;" placeholder="审查意见（必填）"></textarea>`,
    () => {
      const opinion = (document.getElementById('caseReviewOpinionInput')?.value || '').trim();
      if (!opinion) {
        window.showMessage && window.showMessage('请填写审查意见', 'info');
        return;
      }
      submitReview(key, 'failed', opinion);
    },
    '提交不通过',
  );
};

window.caseReviewFixOpen = (key) => {
  const ctx = caseRegistry.get(key);
  if (!ctx) return;
  const failed = (ctx.caseObj.reviews || []).filter((r) => r.status === 'failed');
  const failedHtml = failed.map(historyItemHtml).join('');
  openModal(
    `🛠 修改完成 · ${escapeHtml(ctx.caseObj.zentao_case_id || '')}`,
    `${failedHtml}
     <div class="muted" style="font-size:12px; margin:8px 0;">确认后该用例回到未审查状态（历史保留可查），请填写修改内容。</div>
     <textarea id="caseReviewFixInput" style="width:100%; min-height:100px; padding:8px; border:1px solid #cbd5e1; border-radius:6px; resize:vertical;" placeholder="修改内容（必填）"></textarea>`,
    () => {
      const content = (document.getElementById('caseReviewFixInput')?.value || '').trim();
      if (!content) {
        window.showMessage && window.showMessage('请填写修改内容', 'info');
        return;
      }
      submitFix(key, content);
    },
    '确认修改完成',
  );
};

window.caseReviewShowOpinion = (key, reviewId) => {
  const ctx = caseRegistry.get(key);
  if (!ctx) return;
  const rec = (ctx.caseObj.review_history || []).find((r) => r.id === reviewId)
    || (ctx.caseObj.reviews || []).find((r) => r.id === reviewId);
  if (!rec) return;
  openModal(`审查意见 · ${escapeHtml(ctx.caseObj.zentao_case_id || '')}`, historyItemHtml(rec), null);
};

window.caseReviewShowHistory = (key) => {
  const ctx = caseRegistry.get(key);
  if (!ctx) return;
  const history = ctx.caseObj.review_history || [];
  openModal(
    `审查历史 · ${escapeHtml(ctx.caseObj.zentao_case_id || '')}`,
    history.map(historyItemHtml).join('') || '<span class="muted">暂无审查记录</span>',
    null,
  );
};

// ── SSE：他人审查变更 → 当前打开的工作台静默刷新 + 流光 ──────────────────

let reviewSseBound = false;
let reviewSseTimer = null;
function bindReviewSSE() {
  if (reviewSseBound) return;
  if (!window.OmniQASSE?.subscribe) return;
  window.OmniQASSE.subscribe('case_review_changed', ({ payload }) => {
    const key = String(payload?.case_key || '');
    if (reviewSseTimer) clearTimeout(reviewSseTimer);
    reviewSseTimer = setTimeout(async () => {
      reviewSseTimer = null;
      await refreshWorkbenches();
      if (key) pulseCaseCard(key);
    }, 600);
  });
  reviewSseBound = true;
}
bindReviewSSE();
setTimeout(bindReviewSSE, 3000); // SSE 模块晚于本模块初始化时兜底重绑

window.OmniQACaseReview = { renderCaseReviewControls };
