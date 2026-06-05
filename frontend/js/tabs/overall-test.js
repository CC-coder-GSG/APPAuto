import { api } from '../api.js';
import { state } from '../state.js';
import { withPrefix, sourceTypeZh, escapeHtml } from '../utils.js';

let overallTestSseBound = false;
let overallTestUnreadClearTimer = null;
let overallTestLastDatasetKey = '';

// Track which Esc handler is bound so we can detach on close without collisions.
const _modalEscHandlers = new Map();

function bindModalEsc(modalId, closeFn) {
  if (_modalEscHandlers.has(modalId)) return;
  const handler = (ev) => {
    if (ev.key === 'Escape' || ev.key === 'Esc') {
      ev.preventDefault();
      closeFn();
    }
  };
  _modalEscHandlers.set(modalId, handler);
  document.addEventListener('keydown', handler);
}

function unbindModalEsc(modalId) {
  const handler = _modalEscHandlers.get(modalId);
  if (!handler) return;
  document.removeEventListener('keydown', handler);
  _modalEscHandlers.delete(modalId);
}

// Keep the legacy storage key so existing users do not lose their page-size preference.
const S5_PAGE_SIZE_KEY = 'omniqa_stage5_page_size_v1';
const S5_DEFAULT_PAGE_SIZE = 20;
const S5_MAX_PAGE_SIZE = 500;

function clampS5PageSize(value) {
  const num = Number(value || 0);
  if (!Number.isFinite(num) || num <= 0) return S5_DEFAULT_PAGE_SIZE;
  return Math.max(1, Math.min(S5_MAX_PAGE_SIZE, Math.round(num)));
}

function getStoredS5PageSize() {
  try {
    return clampS5PageSize(localStorage.getItem(S5_PAGE_SIZE_KEY));
  } catch {
    return S5_DEFAULT_PAGE_SIZE;
  }
}

function setStoredS5PageSize(value) {
  try {
    localStorage.setItem(S5_PAGE_SIZE_KEY, String(clampS5PageSize(value)));
  } catch {}
}

function getS5PageCount() {
  const total = state.overallTestRows.length || 0;
  return Math.max(1, Math.ceil(total / clampS5PageSize(state.overallTestPageSize)));
}

function clampS5CurrentPage() {
  state.overallTestPageSize = clampS5PageSize(state.overallTestPageSize || getStoredS5PageSize());
  state.overallTestPage = Math.max(1, Math.min(Number(state.overallTestPage || 1), getS5PageCount()));
}

function getCurrentS5PageRows() {
  clampS5CurrentPage();
  const start = (state.overallTestPage - 1) * state.overallTestPageSize;
  return state.overallTestRows.slice(start, start + state.overallTestPageSize);
}

function scrollS5ToolbarIntoView() {
  // 保持当前滚动位置稳定；分页切换不再强制滚动页面。
}

function updateS5PagerBar() {
  const total = state.overallTestRows.length || 0;
  const bar = document.getElementById('s5PagerBar');
  const summary = document.getElementById('s5PagerSummary');
  const text = document.getElementById('s5PageText');
  const sizeInput = document.getElementById('s5PageSizeInput');
  const prevBtn = document.getElementById('s5PrevPageBtn');
  const nextBtn = document.getElementById('s5NextPageBtn');

  clampS5CurrentPage();
  const pageCount = getS5PageCount();
  const start = total === 0 ? 0 : ((state.overallTestPage - 1) * state.overallTestPageSize + 1);
  const end = total === 0 ? 0 : Math.min(total, state.overallTestPage * state.overallTestPageSize);

  if (bar) bar.classList.toggle('hidden', false);
  if (summary) summary.innerText = `共 ${total} 条 Bug，当前显示 ${start}-${end}`;
  if (text) text.innerText = `第 ${state.overallTestPage} / ${pageCount} 页`;
  if (sizeInput && String(sizeInput.value || '') !== String(state.overallTestPageSize)) {
    sizeInput.value = String(state.overallTestPageSize);
  }
  if (prevBtn) prevBtn.disabled = state.overallTestPage <= 1;
  if (nextBtn) nextBtn.disabled = state.overallTestPage >= pageCount;
}

async function maybeSyncOverallTestBeforeLoad({ majorId, silentSync = true }) {
  if (!majorId) return null;
  try {
    return await syncOverallTestFromZentao({ silent: silentSync, reloadAfter: false, force: false });
  } catch (err) {
    if (!silentSync) throw err;
    return null;
  }
}

function collectS5FilterParams() {
  const statuses = [];
  if (document.getElementById('s5FilterStatusActive')?.checked) statuses.push('active');
  if (document.getElementById('s5FilterStatusResolved')?.checked) statuses.push('resolved');
  if (document.getElementById('s5FilterStatusClosed')?.checked) statuses.push('closed');
  if (document.getElementById('s5FilterStatusLocal')?.checked) statuses.push('local');
  const keyword = (document.getElementById('s5FilterKeyword')?.value || '').trim();
  const qs = [];
  if (statuses.length) qs.push(`statuses=${encodeURIComponent(statuses.join(','))}`);
  if (keyword) qs.push(`keyword=${encodeURIComponent(keyword)}`);
  return { qs: qs.length ? '&' + qs.join('&') : '', hasFilters: statuses.length > 0 || !!keyword };
}

export function applyS5Filters() {
  state.overallTestPage = 1;
  return loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
}

export function resetS5Filters() {
  ['s5FilterStatusActive', 's5FilterStatusResolved', 's5FilterStatusClosed', 's5FilterStatusLocal'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.checked = false;
  });
  const kw = document.getElementById('s5FilterKeyword');
  if (kw) kw.value = '';
  state.overallTestPage = 1;
  return loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
}

export async function loadOverallTest(options = {}) {
  const {
    syncBeforeLoad = true,
    silentSync = true,
    showSuccess = true,
  } = options;
  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  const softwareId = Number(window.currentSoftwareId || 0);
  if (majorId === 0 && !softwareId) {
    window.showMessage && window.showMessage('请先选择产品', 'error');
    return;
  }

  state.overallTestPageSize = clampS5PageSize(state.overallTestPageSize || getStoredS5PageSize());
  setStoredS5PageSize(state.overallTestPageSize);

  const datasetKey = majorId === 0 ? `all:${softwareId}` : `major:${majorId}`;
  if (datasetKey !== overallTestLastDatasetKey) {
    state.overallTestPage = 1;
    overallTestLastDatasetKey = datasetKey;
  }

  const filters = collectS5FilterParams();

  // Load from local DB immediately; trigger background sync after render (Task 10/11)
  const url = majorId === 0
    ? `/overall-test/overview?major_version_id=0&software_id=${softwareId}${filters.qs}`
    : `/overall-test/overview?major_version_id=${majorId}${filters.qs}`;
  const data = await (await api(url)).json();
  if (majorId !== 0 && document.getElementById('s5BugSource')) await loadS5OptionsData(majorId);
  refreshS5EntryMode();
  toggleS5BugInputs();
  state.overallTestRows = data.bug_pool || [];
  state.overallTestAllVersionsMode = !!data.all_versions_mode;
  clampS5CurrentPage();

  const stats = data.stats || {};
  const total = Number(stats.total ?? state.overallTestRows.length);
  const closed = Number(stats.closed ?? state.overallTestRows.filter((b) => b.closed).length);
  const pending = Number(stats.pending ?? Math.max(0, total - closed));
  const rate = Number(stats.ready_rate ?? (total === 0 ? 100 : Math.round((closed / total) * 100)));

  document.getElementById('overallTestPanorama')?.classList.remove('hidden');
  document.getElementById('s5TableContainer')?.classList.remove('hidden');
  document.getElementById('s5FilterBar')?.classList.remove('hidden');
  document.getElementById('s5TotalBugs').innerText = total;
  document.getElementById('s5ClosedBugs').innerText = closed;
  document.getElementById('s5PendingBugs').innerText = pending;
  document.getElementById('s5ReadyRate').innerText = rate + '%';
  renderS5();
  window.scheduleWorkbenchViewportResize?.();
  deferClearOverallUnread();
  if (showSuccess) {
    window.showMessage && window.showMessage('全景大盘加载成功', 'success');
  }

  // Background sync: after rendering local data, trigger incremental sync silently
  if (majorId !== 0 && syncBeforeLoad) {
    maybeSyncOverallTestBeforeLoad({ majorId, silentSync: true }).then((result) => {
      // If sync brought actual changes (not cached), reload silently
      if (result && !result.cached && (result.created || result.updated)) {
        loadOverallTest({ syncBeforeLoad: false, silentSync: true, showSuccess: false });
      }
    }).catch(() => {});
  }
}

function deferClearOverallUnread(ms = 1200) {
  if (overallTestUnreadClearTimer) clearTimeout(overallTestUnreadClearTimer);
  overallTestUnreadClearTimer = setTimeout(() => {
    overallTestUnreadClearTimer = null;
    if (!window.isWorkbenchSubtabActive?.('overall-test')) return;
    if (!window.OmniQASSE || typeof window.OmniQASSE.clearScopeUnread !== 'function') return;
    window.OmniQASSE.clearScopeUnread('overall_bug');
  }, ms);
}

// Row builder

function isS5BugEffectivelyClosed(bug) {
  if (!bug?.zentao_bug_id) return false;
  const zentaoStatus = String(bug.zentao_live_status || '').toLowerCase();
  return (
    zentaoStatus === 'closed'
    || !!bug.zentao_close_date
    || !!bug.zentao_closed_by_name
    || String(bug.zentao_assigned_to_name || '').toLowerCase() === 'closed'
  );
}

function isS5BugReactivatable(bug) {
  if (!bug?.zentao_bug_id) return false;
  const zentaoStatus = String(bug.zentao_live_status || '').toLowerCase();
  return isS5BugEffectivelyClosed(bug) || zentaoStatus === 'resolved';
}

function buildS5RowHtml(b, allVersionsMode) {
  const isMyClosed = b.my_test_done;
  const isZentaoDeleted = !!b.zentao_deleted;
  const rowStyle = isZentaoDeleted
    ? 'background:#fef2f2; box-shadow: inset 4px 0 0 #dc2626;'
    : (isMyClosed ? 'background: #f8fafc; color: #94a3b8;' : '');
  const isZentaoBug = !!b.zentao_bug_id;
  const isEffectivelyClosed = isS5BugEffectivelyClosed(b);
  const canReactivate = isS5BugReactivatable(b);
  const shouldStrikeClosed = !!(isMyClosed || isEffectivelyClosed);
  const strikeDecoration = shouldStrikeClosed
    ? 'text-decoration:line-through; text-decoration-thickness:1px; text-decoration-color:#94a3b8;'
    : '';

  const failBadge = b.is_retest_failed ? '<span class="badge" style="background:#fee2e2; color:#b91c1c; border:1px solid #f87171; margin-left:4px;">🚨复测打回</span>' : '';
  const dispatchBadge = b.dispatched_to_name ? `<span class="badge" style="background:#ffedd5; color:#ea580c; border:1px solid #fdba74; margin-left:4px;">🪂特派:${b.dispatched_to_name}</span>` : '';

  const bugIdText = escapeHtml(b.bug_id || '-');
  const ztBugId = (b.zentao_bug_id) ? String(b.zentao_bug_id) : (b.bug_id || '').replace(/\D/g, '');
  const bugHref = b.zentao_bug_url
    ? `<a class="qa-ext-link" href="${b.zentao_bug_url}" target="_blank" rel="noopener noreferrer">${bugIdText}</a>`
    : ztBugId
      ? `<span class="qa-bug-id-nohref" data-zt-bug-id="${ztBugId}">${bugIdText}</span>`
      : `<span>${bugIdText}</span>`;
  const bugTitle = escapeHtml(b.zentao_bug_title || b.bug_title || '');
  const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" data-zt-no-title="1" style="margin-left:4px;"></span>` : '';
  // 禅道已删除的 Bug：用红色醒目标记，且不展示状态 / 指派等会误导的信息。
  const deletedBadge = isZentaoDeleted
    ? `<span class="badge" style="background:#dc2626; color:#fff; font-weight:bold;">🗑️ 禅道已删除</span>`
    : '';
  const syncMeta = b.last_zentao_synced_at ? `<span class="badge" style="background:#f8fafc; color:#64748b;">同步 ${escapeHtml(b.last_zentao_synced_at)}</span>` : '';
  const assignedMeta = (!isZentaoDeleted && b.zentao_assigned_to_name) ? `<span class="badge" style="background:#faf5ff; color:#7c3aed;">当前指派 ${escapeHtml(getS5AssignedDisplayName(b.zentao_assigned_to_name))}</span>` : '';
  const closedByMeta = (!isZentaoDeleted && b.zentao_closed_by_name) ? `<span class="badge" style="background:#ecfdf5; color:#047857;">禅道关闭 ${escapeHtml(b.zentao_closed_by_name)}</span>` : '';
  const closeDateMeta = (!isZentaoDeleted && b.zentao_close_date) ? `<span class="badge" style="background:#f1f5f9; color:#475569;">${escapeHtml(b.zentao_close_date)}</span>` : '';
  const closeCommentPreview = (b.zentao_close_comment || '').trim();
  const detailMeta = [
    (b.other_records && b.other_records.length > 0)
      ? `<span class="badge" style="background:#eff6ff; color:#1d4ed8;">他人闭环 ${b.other_records.length}</span>`
      : '',
    closeCommentPreview
      ? `<span class="badge" style="background:#f8fafc; color:#475569;">有关闭环备注</span>`
      : '',
  ].filter(Boolean).join('');

  const versionCell = allVersionsMode
    ? `<td style="vertical-align:middle; padding: 6px 10px; white-space:nowrap; font-size:12px; color:#475569;"><span class="badge" style="background:#eff6ff; color:#2563eb;">${escapeHtml(b.major_version_no || '')}</span></td>`
    : '';

  // 操作列：固定两行布局，位置始终稳定
  // 使用 visibility:hidden 代替 display:none，确保按钮槽位不因状态变化而位移
  const linkStyle = 'font-size:12px; text-decoration:none; white-space:nowrap;';
  const editLink = `<a href="javascript:void(0)" onclick="editS5BugById(${b.id})" style="${linkStyle} color:#3b82f6;">编辑</a>`;
  const deleteLink = `<a href="javascript:void(0)" onclick="removeS5BugById(${b.id})" style="${linkStyle} color:#ef4444;">删除</a>`;
  const sep = `<span style="color:#e2e8f0; margin:0 2px;">|</span>`;

  let actionsHtml;
  if (isZentaoBug) {
    const assignLink = `<a href="javascript:void(0)" onclick="openS5AssignById(${b.id})" style="${linkStyle} color:#8b5cf6;">指派</a>`;
    // 重新激活占位始终存在，visibility 控制可见性，保持布局稳定
    const reactivateVis = canReactivate ? 'visible' : 'hidden';
    const reactivateLink = `<a href="javascript:void(0)" onclick="openS5ReactivateById(${b.id})" style="${linkStyle} color:#059669; font-weight:bold; visibility:${reactivateVis};">重新激活</a>`;
    actionsHtml = `
      <div style="display:flex; flex-direction:column; gap:3px;">
        <div style="display:flex; align-items:center; gap:2px;">${editLink}${sep}${deleteLink}</div>
        <div style="display:flex; align-items:center; gap:2px;">${assignLink}${sep}${reactivateLink}</div>
      </div>`;
  } else {
    // 本地 Bug：两行固定，第二行留空，使高度与禅道行一致
    actionsHtml = `
      <div style="display:flex; flex-direction:column; gap:3px;">
        <div style="display:flex; align-items:center; gap:2px;">${editLink}${sep}${deleteLink}</div>
        <div style="height:18px;"></div>
      </div>`;
  }

  // 验证列
  const closeOnChange = isZentaoBug ? `onchange="toggleS5CloseComment(${b.id}, this.checked)"` : '';
  const closeLabel = `<label style="color:#0f172a; font-weight:bold; display:flex; align-items:center; gap:4px; margin:0; ${strikeDecoration}"><input id='done_${b.id}' type='checkbox' ${isMyClosed ? 'checked' : ''} ${closeOnChange}> 我的闭环确认</label>`;

  // 关闭备注默认收起，用户勾选后展开
  const closeCommentRow = isZentaoBug
    ? `<div id='closeComment_${b.id}' style="display:none; margin-top:4px; width:100%;">
        <textarea id='closeCommentText_${b.id}' placeholder='闭环说明（将同步写入禅道备注）' style='width:100%; font-size:12px; padding:4px 6px; border:1px solid #cbd5e1; border-radius:4px; resize:vertical; min-height:40px;'>${escapeHtml(b.my_comment || '')}</textarea>
       </div>`
    : '';

  return `<tr class="overall-test-row-card" data-bug-id="${b.id}" style="${rowStyle}">
    ${versionCell}
    <td style="vertical-align:middle; padding:6px 10px;">
      <div${isZentaoDeleted ? ' style="color:#dc2626; font-weight:bold;"' : ''}>${bugHref}${ztSlot} <span style="font-size:12px;color:#64748b">(${sourceTypeZh(b.source_type)})</span>${failBadge}${dispatchBadge}</div>
      ${(deletedBadge || detailMeta) ? `<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:6px;">${deletedBadge}${detailMeta}</div>` : ''}
    </td>
    <td style="vertical-align:middle; padding:6px 10px;">
      ${bugTitle ? `<div style="max-height:60px; overflow-y:auto; font-size:13px; color:#475569; line-height:1.65; word-break:break-word; ${strikeDecoration}">${bugTitle}</div>` : '<span style="color:#94a3b8;">-</span>'}
      ${(syncMeta || assignedMeta || closedByMeta || closeDateMeta) ? `<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:6px;">${syncMeta}${assignedMeta}${closedByMeta}${closeDateMeta}</div>` : ''}
    </td>
    <td style="vertical-align:middle; padding:6px 8px; width:120px;">${actionsHtml}</td>
    <td style="text-decoration:none; vertical-align:middle; padding:6px 10px;">
      <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
        <select id='res_${b.id}' style="padding:2px; font-size:13px; border:1px solid #cbd5e1; border-radius:4px; color:#475569; ${strikeDecoration}" ${isMyClosed ? 'disabled' : ''}>
          <option value="fixed" ${b.my_resolution === 'fixed' ? 'selected' : ''}>✅修复通过</option>
          <option value="false_alarm" ${b.my_resolution === 'false_alarm' ? 'selected' : ''}>⚠️误报</option>
          <option value="rejected" ${b.my_resolution === 'rejected' ? 'selected' : ''}>拒绝修复</option>
        </select>
        ${closeLabel}
        ${closeCommentRow}
        <button class="${isMyClosed ? 'secondary' : ''}" style="${strikeDecoration}" onclick='saveS5(${b.id})'>保存记录</button>
      </div>
    </td>
  </tr>`;
}

function getS5ResolutionLabel(value) {
  return {
    fixed: '修复通过',
    false_alarm: '误报',
    rejected: '拒绝修复',
  }[String(value || '')] || '-';
}

function getS5ZentaoStatusLabel(bugOrStatus) {
  const raw = typeof bugOrStatus === 'string' ? bugOrStatus : (bugOrStatus?.zentao_live_status || '');
  const status = String(raw || '').trim().toLowerCase();
  if (!status) return '-';
  const isClosedLike = typeof bugOrStatus === 'object' && bugOrStatus && isS5BugEffectivelyClosed(bugOrStatus);
  if (isClosedLike) return '已关闭';
  return {
    active: '激活',
    resolved: '已解决',
    closed: '已关闭',
    delay: '延期',
  }[status] || status;
}

function getS5AssignedDisplayName(value) {
  const text = String(value || '').trim();
  if (!text) return '-';
  return text.toLowerCase() === 'closed' ? '已关闭' : text;
}

function buildS5OtherRecordsHtml(records) {
  if (!(records && records.length > 0)) {
    return '<div style="font-size:12px; color:#94a3b8;">暂无其他测试人员闭环记录</div>';
  }
  return `<div style="display:flex; flex-direction:column; gap:8px;">${records.map((record) => {
    const status = record.test_done
      ? `<span style="color:#16a34a; font-weight:600;">已闭环</span>`
      : `<span style="color:#dc2626; font-weight:600;">未闭环</span>`;
    const resolution = record.test_done
      ? `<span class="badge" style="background:#ecfdf5; color:#166534;">${getS5ResolutionLabel(record.resolution)}</span>`
      : '';
    return `<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:8px 10px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;">
      <span class="badge" style="background:#e2e8f0; color:#334155;">${escapeHtml(record.username || '-')}</span>
      ${status}
      ${resolution}
      <span style="font-size:12px; color:#64748b;">发包：${escapeHtml(record.minor_version_no || '-')}</span>
    </div>`;
  }).join('')}</div>`;
}

function buildS5DetailRowHtml(b, colSpan) {
  const statusBadges = [
    b.zentao_live_status
      ? `<span class="badge" style="background:#f8fafc; color:#475569;">禅道状态 ${escapeHtml(getS5ZentaoStatusLabel(b))}</span>`
      : '',
    b.zentao_assigned_to_name
      ? `<span class="badge" style="background:#faf5ff; color:#7c3aed;">当前指派 ${escapeHtml(getS5AssignedDisplayName(b.zentao_assigned_to_name))}</span>`
      : '',
    b.zentao_closed_by_name
      ? `<span class="badge" style="background:#ecfdf5; color:#047857;">关闭人 ${escapeHtml(b.zentao_closed_by_name)}</span>`
      : '',
    b.zentao_close_date
      ? `<span class="badge" style="background:#f1f5f9; color:#475569;">关闭时间 ${escapeHtml(b.zentao_close_date)}</span>`
      : '',
    b.last_zentao_synced_at
      ? `<span class="badge" style="background:#eff6ff; color:#2563eb;">同步 ${escapeHtml(b.last_zentao_synced_at)}</span>`
      : '',
  ].filter(Boolean).join('');
  const previewAction = b.zentao_bug_id
    ? `<a href="javascript:void(0)" onclick="openS5PreviewById(${b.id})" style="font-size:12px; text-decoration:none; color:#0f766e;">打开禅道预览</a>`
    : '<span style="font-size:12px; color:#94a3b8;">本地 Bug 无禅道预览</span>';
  const closeCommentBlock = (b.zentao_close_comment || '').trim()
    ? `<div style="margin-top:12px;">
        <div style="font-size:12px; font-weight:600; color:#334155; margin-bottom:6px;">禅道关闭备注</div>
        <div style="font-size:12px; color:#475569; line-height:1.7; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:8px; padding:10px 12px; white-space:pre-wrap; word-break:break-word;">${escapeHtml(b.zentao_close_comment)}</div>
      </div>`
    : '';
  return `<tr class="overall-test-detail-row hidden" data-bug-id="${b.id}">
    <td colspan="${colSpan}" style="padding:0 10px 10px 10px; background:#fcfcfd;">
      <div style="border:1px solid #e2e8f0; border-top:none; border-radius:0 0 10px 10px; background:#ffffff; padding:12px 14px;">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">
          <div style="font-size:13px; font-weight:700; color:#0f172a;">Bug 详情</div>
          <div>${previewAction}</div>
        </div>
        ${statusBadges ? `<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:10px;">${statusBadges}</div>` : ''}
        ${closeCommentBlock}
        <div style="margin-top:12px;">
          <div style="font-size:12px; font-weight:600; color:#334155; margin-bottom:8px;">其他人闭环记录</div>
          ${buildS5OtherRecordsHtml(b.other_records)}
        </div>
      </div>
    </td>
  </tr>`;
}

function mountS5DetailRows(table, pageRows, colSpan) {
  pageRows.forEach((bug) => {
    const rowEl = table.querySelector(`.overall-test-row-card[data-bug-id="${bug.id}"]`);
    if (!rowEl || rowEl.nextElementSibling?.matches(`.overall-test-detail-row[data-bug-id="${bug.id}"]`)) return;
    rowEl.insertAdjacentHTML('afterend', buildS5DetailRowHtml(bug, colSpan));
  });
}

// 等两帧让浏览器先完成展开后的 reflow，再判断是否需要滚动
function ensureS5ElementVisible(el) {
  if (!el) return;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const rect = el.getBoundingClientRect();
      const viewportH = window.innerHeight || document.documentElement.clientHeight;
      if (rect.bottom > viewportH || rect.top < 0) {
        el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    });
  });
}

export function toggleS5DetailRow(id, forceExpand = null) {
  const detailRow = document.querySelector(`.overall-test-detail-row[data-bug-id="${id}"]`);
  if (!detailRow) return;
  const shouldExpand = typeof forceExpand === 'boolean'
    ? forceExpand
    : detailRow.classList.contains('hidden');
  detailRow.classList.toggle('hidden', !shouldExpand);
  const toggleLink = document.querySelector(`[data-s5-toggle-link][data-bug-id="${id}"]`);
  if (toggleLink) toggleLink.textContent = shouldExpand ? '收起' : '展开';
  if (shouldExpand) ensureS5ElementVisible(detailRow);
}

export function renderS5() {
  const table = document.getElementById('s5Table');
  if (!table) return;
  clampS5CurrentPage();
  const pageRows = getCurrentS5PageRows();
  const allVersionsMode = !!state.overallTestAllVersionsMode;
  const thead = document.getElementById('s5TableHead');
  if (thead) {
    thead.innerHTML = allVersionsMode
      ? '<tr><th style="width:90px">所属版本</th><th style="width:240px">Bug 编号 (来源)</th><th>Bug 标题</th><th style="width:120px">操作</th><th style="width:380px">验证操作</th></tr>'
      : '<tr><th style="width:240px">Bug 编号 (来源)</th><th>Bug 标题</th><th style="width:120px">操作</th><th style="width:380px">验证操作</th></tr>';
  }
  const colSpan = allVersionsMode ? 5 : 4;
  table.innerHTML = pageRows.length
    ? pageRows.map((b) => buildS5RowHtml(b, allVersionsMode)).join('')
    : `<tr><td colspan="${colSpan}" style="text-align:center; color:#94a3b8; padding:24px 12px;">暂无 Bug 数据</td></tr>`;

  mountS5DetailRows(table, pageRows, colSpan);

  pageRows.forEach((bug) => {
    const rowEl = table.querySelector(`.overall-test-row-card[data-bug-id="${bug.id}"]`);
    const actionCell = rowEl?.children?.[allVersionsMode ? 3 : 2];
    const firstActionRow = actionCell?.querySelector('div > div');
    const actionRows = actionCell?.querySelectorAll(':scope > div > div');
    const secondActionRow = actionRows?.[1] || null;
    if (secondActionRow && isS5BugEffectivelyClosed(bug)) {
      const assignAnchor = secondActionRow.querySelector(`a[onclick="openS5AssignById(${bug.id})"]`);
      if (assignAnchor) {
        assignAnchor.removeAttribute('onclick');
        assignAnchor.removeAttribute('href');
        assignAnchor.style.color = '#94a3b8';
        assignAnchor.style.cursor = 'not-allowed';
        assignAnchor.title = '已关闭的禅道 Bug 不能再指派';
      }
    }
    if (!firstActionRow) return;
    const inserted = [];
    if (bug?.zentao_bug_id && !firstActionRow.querySelector('[data-s5-preview-link]')) {
      inserted.push(`<a data-s5-preview-link="1" href="javascript:void(0)" onclick="openS5PreviewById(${bug.id})" style="font-size:12px; text-decoration:none; white-space:nowrap; color:#0f766e;">预览</a>`);
    }
    if (!firstActionRow.querySelector('[data-s5-toggle-link]')) {
      inserted.push(`<a data-s5-toggle-link="1" data-bug-id="${bug.id}" href="javascript:void(0)" onclick="toggleS5DetailRow(${bug.id})" style="font-size:12px; text-decoration:none; white-space:nowrap; color:#2563eb;">展开</a>`);
    }
    if (!inserted.length) return;
    firstActionRow.insertAdjacentHTML(
      'afterbegin',
      `${inserted.join('<span style="color:#e2e8f0; margin:0 2px;">|</span>')}<span style="color:#e2e8f0; margin:0 2px;">|</span>`,
    );
  });

  updateS5PagerBar();
  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    window.OmniQASSE.releaseAttention?.();
    document.querySelectorAll('.overall-test-row-card[data-bug-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'overall_bug', key: el.getAttribute('data-bug-id'), tone: 'blue', hoverDelayMs: 420 });
    });
  }
  window.OmniQAZentao?.hydrateContainer(table);
}

function setS5Page(nextPage) {
  state.overallTestPage = Math.max(1, Math.min(Number(nextPage || 1), getS5PageCount()));
  renderS5();
}

export function prevS5Page() {
  setS5Page(state.overallTestPage - 1);
}

export function nextS5Page() {
  setS5Page(state.overallTestPage + 1);
}

export function applyS5PageSize() {
  const input = document.getElementById('s5PageSizeInput');
  const pageSize = clampS5PageSize(input?.value || state.overallTestPageSize);
  state.overallTestPageSize = pageSize;
  state.overallTestPage = 1;
  setStoredS5PageSize(pageSize);
  if (input) input.value = String(pageSize);
  renderS5();
}

export async function syncOverallTestFromZentao(options = {}) {
  const { silent = false, reloadAfter = true, force = true } = options;
  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);

  // 全部版本模式：调用新的按产品全量同步接口
  if (majorId === 0) {
    if (!softwareId) {
      if (!silent) {
        window.showMessage && window.showMessage('请先选择产品后再同步禅道全部版本', 'error');
      }
      return null;
    }
    let data;
    try {
      data = await (await api('/overall-test/sync-zentao-all', {
        method: 'POST',
        headers: window.H,
        body: { software_id: softwareId, force },
      })).json();
    } catch (err) {
      if (!silent) {
        window.showMessage && window.showMessage(err.message || '禅道全量同步失败', 'error');
      }
      return null;
    }
    if (!silent) {
      if (data.cached) {
        window.showMessage && window.showMessage('15分钟内已全量同步过，已使用缓存，如需强制请重试', 'info');
      } else {
        const prodCnt = Array.isArray(data.zentao_products) ? data.zentao_products.length : 0;
        window.showMessage && window.showMessage(
          `禅道全量同步完成：${prodCnt} 个产品，远端 ${data.remote_total || 0} 条，新增 ${data.created || 0}，更新 ${data.updated || 0}，未归类 ${data.unclassified || 0}（耗时 ${data.elapsed_seconds || 0}s）`,
          'success',
        );
      }
    }
    if (reloadAfter) {
      await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
    }
    return data;
  }

  const data = await (await api('/overall-test/sync-zentao-bugs', {
    method: 'POST',
    headers: window.H,
    body: { major_version_id: majorId, force },
  })).json();

  if (!silent) {
    if (data.cached) {
      window.showMessage && window.showMessage('5分钟内已同步过，已跳过远端拉取（使用本地缓存）', 'info');
    } else {
      window.showMessage && window.showMessage(
        `禅道同步完成：远端 ${data.remote_total || 0} 条，新增 ${data.created || 0} 条，更新 ${data.updated || 0} 条`,
        'success',
      );
    }
  }
  if (reloadAfter) {
    await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
  }
  return data;
}

export async function syncOverallTestRecentFromZentao(options = {}) {
  const { silent = false, reloadAfter = true } = options;
  const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  if (!softwareId) {
    if (!silent) {
      window.showMessage && window.showMessage('请先选择产品后再增量同步', 'error');
    }
    return null;
  }
  let data;
  try {
    data = await (await api('/overall-test/sync-zentao-recent', {
      method: 'POST',
      headers: window.H,
      body: { software_id: softwareId, force: true },
    })).json();
  } catch (err) {
    if (!silent) {
      window.showMessage && window.showMessage(err.message || '禅道增量同步失败', 'error');
    }
    return null;
  }
  if (!silent) {
    const prodCnt = Array.isArray(data.zentao_products) ? data.zentao_products.length : 0;
    window.showMessage && window.showMessage(
      `禅道增量同步完成：${prodCnt} 个产品，扫描 ${data.remote_total || 0} 条，新增 ${data.created || 0}，更新 ${data.updated || 0}（耗时 ${data.elapsed_seconds || 0}s）`,
      'success',
    );
  }
  if (reloadAfter) {
    await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
  }
  return data;
}

// Toggle the close comment textarea when the close checkbox is ticked
export function toggleS5CloseComment(bugId, checked) {
  const wrap = document.getElementById('closeComment_' + bugId);
  if (wrap) wrap.style.display = checked ? '' : 'none';
  if (checked) {
    // 展开后若行底跑到视口外，自动把整行滚回可见区域
    const rowEl = document.querySelector(`.overall-test-row-card[data-bug-id="${bugId}"]`);
    ensureS5ElementVisible(rowEl || wrap);
  }
}

export async function saveS5(id) {
  const bug = state.overallTestRows.find((b) => b.id === id);
  const isZentaoBug = !!(bug?.zentao_bug_id);
  const zentaoBugId = bug?.zentao_bug_id || '';
  const doneEl = document.getElementById('done_' + id);
  const done = doneEl?.checked;
  const res = document.getElementById('res_' + id)?.value;
  const closeComment = isZentaoBug ? (document.getElementById('closeCommentText_' + id)?.value || '') : '';

  if (isZentaoBug && done && zentaoBugId) {
    const ztId = Number(zentaoBugId);
    if (ztId) {
      try {
        await api(`/zentao/bugs/${ztId}/close`, {
          method: 'POST',
          headers: window.H,
          body: { comment: closeComment },
        });
      } catch (err) {
        window.showMessage && window.showMessage(err.message || '禅道关闭失败，本地未保存闭环结果', 'error');
        return;
      }
    }
  }

  // 取消闭环：本地之前记了 closed=true，现在用户要撤销勾选。此时禅道
  // 通常是 closed 状态，必须先重新激活才允许让本地变回未闭环。
  if (isZentaoBug && !done && bug?.closed && zentaoBugId) {
    const ztId = Number(zentaoBugId);
    if (ztId) {
      window.showMessage && window.showMessage('取消闭环需要先在禅道重新激活该 Bug', 'info');
      const activated = await openS5ReactivateModalAsync(id, ztId);
      if (!activated) {
        // 用户放弃激活 → 回滚勾选，不保存
        if (doneEl) doneEl.checked = true;
        window.showMessage && window.showMessage('未完成重新激活，已取消"取消闭环"操作', 'error');
        return;
      }
    }
  }

  await api(`/overall-test/bugs/${id}/result`, {
    method: 'PUT',
    headers: window.H,
    body: {
      minor_version_id: Number(document.getElementById('s5MinorSelect')?.value || 0),
      test_done: done,
      newly_found_bug_id: null,
      resolution: res,
    },
  });

  window.showMessage && window.showMessage('整体测试项已保存');
  await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
}

export async function editS5Bug(id, oldBugId, zentaoBugId, currentTitle) {
  if (zentaoBugId) {
    // Zentao bug: edit title via Zentao API
    const title = prompt('请输入 Bug 标题：', currentTitle ? String(currentTitle).replace(/&amp;/g, '&') : '');
    if (title === null) return;
    if (!title.trim()) {
      window.showMessage && window.showMessage('标题不能为空', 'error');
      return;
    }
    try {
      await api(`/zentao/bugs/${Number(zentaoBugId)}`, {
        method: 'PUT',
        headers: window.H,
        body: { title: title.trim() },
      });
      window.showMessage && window.showMessage('禅道 Bug 标题已更新', 'success');
      await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '标题更新失败', 'error');
    }
  } else {
    // Local bug: change bug_id (original behaviour)
    const num = prompt('请输入正确的 Bug 数字部分：', String(oldBugId || '').replace('b#', ''));
    if (!num) return;
    try {
      await api('/bugs/' + id + '?new_bug_id=' + encodeURIComponent(withPrefix('b#', num)), { method: 'PUT' });
      window.showMessage && window.showMessage('Bug 编号已纠正', 'success');
      await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '更新失败', 'error');
    }
  }
}

export async function removeS5Bug(id, zentaoBugId = '') {
  if (zentaoBugId) {
    // Zentao bug: soft-delete via Zentao API with strong warning
    const confirmed = window.confirm(
      `⚠️ 删除禅道 Bug\n\n` +
      `将对禅道执行删除操作（软删除，deleted=true）。\n` +
      `当前实例删除后暂无稳定可用的 REST 恢复接口，请谨慎操作！\n\n` +
      `本地记录将标记为“已删除”并从大盘隐藏，不会彻底清除（保留审计记录）。\n\n` +
      `确认删除禅道 Bug #${zentaoBugId} 吗？`
    );
    if (!confirmed) return;
    try {
      await api(`/zentao/bugs/${Number(zentaoBugId)}`, { method: 'DELETE' });
      window.showMessage && window.showMessage('禅道 Bug 已删除并从大盘移除', 'success');
      await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '删除失败', 'error');
    }
  } else {
    // Local bug
    if (!confirm('确定要在该大版本下移除这个 Bug 吗？')) return;
    try {
      await api('/bugs/' + id, { method: 'DELETE' });
      window.showMessage && window.showMessage('Bug 已彻底移除', 'success');
      await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '删除失败', 'error');
    }
  }
}

// State-driven action wrappers (no inline HTML attribute injection)

function editS5BugById(id) {
  const bug = state.overallTestRows.find((b) => b.id === id);
  if (!bug) return;
  editS5Bug(id, bug.bug_id, bug.zentao_bug_id || '', bug.zentao_bug_title || '');
}

function removeS5BugById(id) {
  const bug = state.overallTestRows.find((b) => b.id === id);
  removeS5Bug(id, bug ? (bug.zentao_bug_id || '') : '');
}

function openS5AssignById(id) {
  const bug = state.overallTestRows.find((b) => b.id === id);
  if (!bug?.zentao_bug_id) return;
  if (isS5BugEffectivelyClosed(bug)) {
    window.showMessage && window.showMessage('已关闭的禅道 Bug 不能再指派，请先重新激活', 'error');
    return;
  }
  openS5AssignModal(id, bug.zentao_bug_id);
}

function openS5ReactivateById(id) {
  const bug = state.overallTestRows.find((b) => b.id === id);
  if (!bug?.zentao_bug_id) return;
  openS5ReactivateModal(id, bug.zentao_bug_id);
}

function openS5PreviewById(id) {
  const bug = state.overallTestRows.find((item) => item.id === id);
  if (!bug?.zentao_bug_id) return;
  openZentaoBugPreview(Number(bug.zentao_bug_id), bug);
}

function getCurrentS5MajorVersion() {
  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  if (!majorId) return null;
  return (state.versions || []).find((item) => Number(item.id) === majorId && item.version_type === 'major') || null;
}

export function refreshS5EntryMode() {
  const panel = document.getElementById('s5LocalBugPanel');
  const hint = document.getElementById('s5LocalBugHint');
  const title = document.getElementById('s5LocalBugTitle');
  if (!panel || !hint || !title) return;

  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  const major = getCurrentS5MajorVersion();
  let visible = true;
  let hintText = '当前大版本未配置禅道执行版本，可临时录入本地 Bug。';

  if (!majorId) {
    visible = false;
    hintText = '全部版本视图下不允许直接新增本地 Bug，请先切换到具体大版本。';
  } else if (major?.zentao_execution_id) {
    visible = false;
    hintText = '当前大版本已配置禅道执行版本，请优先使用上方“新建禅道Bug”。';
  }

  panel.classList.toggle('hidden', !visible);
  title.innerText = visible ? '➕ 新增问题' : '➕ 本地新增已隐藏';
  hint.innerText = hintText;
}

function _sanitizePreviewHtml(rawHtml) {
  const template = document.createElement('template');
  template.innerHTML = String(rawHtml || '');

  const blockedTags = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META']);
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_ELEMENT);
  const toRemove = [];
  while (walker.nextNode()) {
    const el = walker.currentNode;
    if (blockedTags.has(el.tagName)) {
      toRemove.push(el);
      continue;
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const value = String(attr.value || '');
      if (name.startsWith('on')) {
        el.removeAttribute(attr.name);
        continue;
      }
      if ((name === 'href' || name === 'src') && /^\s*javascript:/i.test(value)) {
        el.removeAttribute(attr.name);
      }
    }
  }
  toRemove.forEach((node) => node.remove());
  return template.innerHTML || '<span style="color:#94a3b8;">暂无内容</span>';
}

let _zentaoPreviewImageGallery = [];
let _zentaoPreviewImageIndex = -1;

function _isPreviewImageFile(item) {
  if (!item) return false;
  if (item.is_image) return true;
  const text = String(item.extension || item.title || '').trim().toLowerCase();
  const ext = text.includes('.') ? text.split('.').pop() : text;
  return ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'].includes(ext);
}

function _renderPreviewFiles(preview) {
  const files = Array.isArray(preview.files) ? preview.files : [];
  if (!files.length) {
    return '<span style="color:#94a3b8; font-size:13px;">暂无附件</span>';
  }

  const imageFiles = files.filter((item) => _isPreviewImageFile(item) && item?.url);
  const otherFiles = files.filter((item) => !_isPreviewImageFile(item) || !item?.url);

  const imageBlock = imageFiles.length ? `
    <div style="margin-bottom:${otherFiles.length ? '14px' : '0'};">
      <div style="font-size:13px; font-weight:600; color:#334155; margin-bottom:8px;">图片附件</div>
      <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px;">
        ${imageFiles.map((item, index) => `
          <a href="javascript:void(0)" onclick="openZentaoBugImageLightboxByIndex(${index})" style="display:block; text-decoration:none; border:1px solid #dbeafe; border-radius:12px; overflow:hidden; background:#eff6ff;">
            <div style="aspect-ratio:4/3; background:#dbeafe; display:flex; align-items:center; justify-content:center;">
              <img src="${escapeHtml(item.url || '')}" alt="${escapeHtml(item.title || '图片附件')}" style="width:100%; height:100%; object-fit:cover; display:block;">
            </div>
            <div style="padding:10px 12px; font-size:12px; color:#1e3a8a; line-height:1.6; word-break:break-word;">${escapeHtml(item.title || '图片附件')}</div>
          </a>
        `).join('')}
      </div>
    </div>
  ` : '';

  const fileBlock = otherFiles.length ? `
    <div>
      <div style="font-size:13px; font-weight:600; color:#334155; margin-bottom:8px;">文件附件</div>
      <div style="display:flex; flex-wrap:wrap; gap:8px;">
        ${otherFiles.map((item) => item?.url ? `
          <a href="${escapeHtml(item.url || '#')}" target="_blank" rel="noopener noreferrer" class="badge" style="background:#eff6ff; color:#2563eb; text-decoration:none;">
            ${escapeHtml(item.title || '附件')}
          </a>
        ` : `
          <span class="badge" style="background:#f8fafc; color:#64748b;">
            ${escapeHtml(item?.title || '附件')}
          </span>
        `).join('')}
      </div>
    </div>
  ` : '';

  return `${imageBlock}${fileBlock}`;
}

function _setZentaoPreviewImageGallery(files) {
  _zentaoPreviewImageGallery = (Array.isArray(files) ? files : [])
    .filter((item) => _isPreviewImageFile(item) && item?.url)
    .map((item) => ({
      url: String(item.url || ''),
      title: String(item.title || '图片附件'),
    }));
  _zentaoPreviewImageIndex = -1;
}

function _updateZentaoBugImageLightbox() {
  const modal = document.getElementById('zentaoBugImageLightbox');
  const img = document.getElementById('zentaoBugImageLightboxImg');
  const titleEl = document.getElementById('zentaoBugImageLightboxTitle');
  const indexEl = document.getElementById('zentaoBugImageLightboxIndex');
  const prevBtn = document.getElementById('zentaoBugImageLightboxPrev');
  const nextBtn = document.getElementById('zentaoBugImageLightboxNext');
  const current = _zentaoPreviewImageGallery[_zentaoPreviewImageIndex];
  if (!modal || !img || !titleEl || !current) return;
  img.src = current.url;
  img.alt = current.title || '图片预览';
  titleEl.innerText = current.title || '图片预览';
  if (indexEl) indexEl.innerText = `${_zentaoPreviewImageIndex + 1} / ${_zentaoPreviewImageGallery.length}`;
  if (prevBtn) prevBtn.disabled = _zentaoPreviewImageIndex <= 0;
  if (nextBtn) nextBtn.disabled = _zentaoPreviewImageIndex >= _zentaoPreviewImageGallery.length - 1;
}

export function openZentaoBugImageLightboxByIndex(index) {
  const modal = document.getElementById('zentaoBugImageLightbox');
  if (!modal || !_zentaoPreviewImageGallery.length) return;
  const nextIndex = Number(index);
  if (!Number.isInteger(nextIndex) || nextIndex < 0 || nextIndex >= _zentaoPreviewImageGallery.length) return;
  _zentaoPreviewImageIndex = nextIndex;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _updateZentaoBugImageLightbox();
}

export function openZentaoBugImageLightbox(url, title = '图片预览') {
  _zentaoPreviewImageGallery = [{ url: String(url || ''), title: String(title || '图片预览') }];
  _zentaoPreviewImageIndex = 0;
  openZentaoBugImageLightboxByIndex(0);
}

export function prevZentaoBugImageLightbox() {
  if (_zentaoPreviewImageIndex <= 0) return;
  _zentaoPreviewImageIndex -= 1;
  _updateZentaoBugImageLightbox();
}

export function nextZentaoBugImageLightbox() {
  if (_zentaoPreviewImageIndex >= _zentaoPreviewImageGallery.length - 1) return;
  _zentaoPreviewImageIndex += 1;
  _updateZentaoBugImageLightbox();
}

export function closeZentaoBugImageLightbox(event = null) {
  if (event?.target && event.target.id !== 'zentaoBugImageLightbox') return;
  const modal = document.getElementById('zentaoBugImageLightbox');
  const img = document.getElementById('zentaoBugImageLightboxImg');
  const titleEl = document.getElementById('zentaoBugImageLightboxTitle');
  const indexEl = document.getElementById('zentaoBugImageLightboxIndex');
  if (!modal || !img || !titleEl) return;
  modal.classList.add('hidden');
  modal.style.display = 'none';
  img.removeAttribute('src');
  img.alt = '图片预览';
  titleEl.innerText = '图片预览';
  if (indexEl) indexEl.innerText = '';
  _zentaoPreviewImageGallery = [];
  _zentaoPreviewImageIndex = -1;
}

function _renderPreviewMetaGrid(preview) {
  const fields = [
    ['状态', preview.status_zh || preview.status || '-'],
    ['解决方案', preview.resolution_zh || preview.resolution || '-'],
    ['Bug 类型', preview.type_zh || preview.type || '-'],
    ['严重程度', preview.severity ? `S${preview.severity}` : '-'],
    ['优先级', preview.pri ? `P${preview.pri}` : '-'],
    ['指派给', preview.assigned_to || '-'],
    ['创建人', preview.opened_by || '-'],
    ['创建时间', preview.opened_date || '-'],
    ['模块', preview.module || '-'],
    ['影响版本', preview.opened_build || '-'],
    ['执行版本', preview.execution || '-'],
    ['关联故事', preview.story_title || '-'],
    ['项目', preview.project || '-'],
    ['关闭人', preview.closed_by || '-'],
    ['关闭时间', preview.closed_date || '-'],
    ['截止日期', preview.deadline || '-'],
  ];
  return fields.map(([label, value]) => `
    <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:10px 12px;">
      <div style="font-size:12px; color:#64748b; margin-bottom:4px;">${escapeHtml(label)}</div>
      <div style="font-size:13px; color:#0f172a; line-height:1.6; word-break:break-word;">${escapeHtml(value)}</div>
    </div>
  `).join('');
}

function _renderPreviewActions(preview) {
  const actions = Array.isArray(preview.actions) ? preview.actions : [];
  if (!actions.length) {
    return '<div style="color:#94a3b8; font-size:13px;">暂无流转记录</div>';
  }
  return actions.map((item) => `
    <div style="padding:10px 12px; border:1px solid #e2e8f0; border-radius:10px; background:#fff;">
      <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:6px;">
        <span style="font-size:12px; color:#475569;">${escapeHtml(item.date || '-')}</span>
        <span class="badge" style="background:#eff6ff; color:#2563eb;">${escapeHtml(item.actor || '系统')}</span>
        <span class="badge" style="background:#f8fafc; color:#475569;">${escapeHtml(item.action_zh || item.action || '操作')}</span>
      </div>
      <div style="font-size:13px; color:#334155; line-height:1.7; word-break:break-word;">${_sanitizePreviewHtml(item.comment || '无备注')}</div>
    </div>
  `).join('');
}

async function _loadProxyImages(containerEl) {
  if (!containerEl) return;
  const imgs = Array.from(containerEl.querySelectorAll('img[src^="/zentao/files/"]'));
  await Promise.all(imgs.map(async (img) => {
    const src = img.getAttribute('src');
    try {
      const resp = await api(src);
      const blob = await resp.blob();
      img.src = URL.createObjectURL(blob);
    } catch (_e) {
      // 保持破图状态，不干扰其他内容
    }
  }));
}

export async function openZentaoBugPreview(ztId, bugRow = null) {
  const modal = document.getElementById('zentaoBugPreviewModal');
  const titleEl = document.getElementById('zentaoBugPreviewTitle');
  const metaEl = document.getElementById('zentaoBugPreviewMeta');
  const bodyEl = document.getElementById('zentaoBugPreviewBody');
  const errorEl = document.getElementById('zentaoBugPreviewError');
  const linkEl = document.getElementById('zentaoBugPreviewLink');
  if (!modal || !titleEl || !metaEl || !bodyEl || !errorEl || !linkEl) return;

  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  titleEl.innerText = bugRow?.zentao_bug_title || `Bug #${ztId}`;
  metaEl.innerText = `禅道 Bug #${ztId}`;
  bodyEl.innerHTML = '<div style="color:#64748b; font-size:13px;">正在加载禅道 Bug 详情...</div>';
  errorEl.style.display = 'none';
  linkEl.classList.add('hidden');
  linkEl.removeAttribute('href');

  try {
    const preview = await (await api(`/zentao/bugs/${Number(ztId)}/preview`)).json();
    _setZentaoPreviewImageGallery(preview.files);
    titleEl.innerText = preview.title || bugRow?.zentao_bug_title || `Bug #${ztId}`;
    metaEl.innerText = `禅道 Bug #${ztId} · ${preview.status_zh || preview.status || '未知状态'}`;
    if (preview.zentao_url) {
      linkEl.href = preview.zentao_url;
      linkEl.classList.remove('hidden');
    }
    bodyEl.innerHTML = `
      <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:10px; margin-bottom:16px;">
        ${_renderPreviewMetaGrid(preview)}
      </div>
      <div style="margin-bottom:16px;">
        <div style="font-size:14px; font-weight:700; color:#0f172a; margin-bottom:8px;">复现步骤</div>
        <div style="border:1px solid #e2e8f0; border-radius:12px; padding:14px; background:#f8fafc; color:#334155; line-height:1.8;">
          ${_sanitizePreviewHtml(preview.steps)}
        </div>
      </div>
      <div style="margin-bottom:16px;">
        <div style="font-size:14px; font-weight:700; color:#0f172a; margin-bottom:8px;">附件</div>
        <div>${_renderPreviewFiles(preview)}</div>
      </div>
      <div>
        <div style="font-size:14px; font-weight:700; color:#0f172a; margin-bottom:8px;">流转记录</div>
        <div style="display:flex; flex-direction:column; gap:10px;">
          ${_renderPreviewActions(preview)}
        </div>
      </div>
    `;
    await _loadProxyImages(bodyEl);
  } catch (err) {
    _setZentaoPreviewImageGallery([]);
    errorEl.innerText = err.message || '加载禅道 Bug 详情失败';
    errorEl.style.display = 'block';
    bodyEl.innerHTML = '<div style="color:#94a3b8; font-size:13px;">无法获取禅道详情，请检查绑定或稍后重试。</div>';
  }
}

export function closeZentaoBugPreview() {
  const modal = document.getElementById('zentaoBugPreviewModal');
  if (modal) {
    modal.classList.add('hidden');
    modal.style.display = 'none';
  }
  closeZentaoBugImageLightbox();
}

export async function pushOverallTest() {
  if (!(window.confirmPush && window.confirmPush())) return;
  const res = await (await api(`/overall-test/push-status?major_version_id=${Number(document.getElementById('s5MajorSelect')?.value || 0)}&minor_version_id=${Number(document.getElementById('s5MinorSelect')?.value || 0)}`, { method: 'POST' })).json();
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
    state.overallTestOptions = await (await api('/overall-test/search-options?major_version_id=' + majorId)).json();
    const reqSelect = document.getElementById('s5ReqSelect');
    const caseSelect = document.getElementById('s5CaseSelect');
    const legacySelect = document.getElementById('s5LegacySelect');

    if (reqSelect) {
      reqSelect.innerHTML = '<option value="">请选择关联需求</option>' + state.overallTestOptions.reqs.map((r) => `<option value="${r.id}">${r.label}</option>`).join('');
    }
    if (caseSelect) {
      caseSelect.innerHTML = '<option value="">请选择关联用例</option>' + state.overallTestOptions.cases.map((c) => `<option value="${c.id}">${c.label}</option>`).join('');
    }
    if (legacySelect) {
      legacySelect.innerHTML = '<option value="">请选择关联历史Bug</option>' + state.overallTestOptions.bugs.map((b) => `<option value="${b.id}">${b.label}</option>`).join('');
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
    const target = state.overallTestOptions.reqs.find((r) => r.id === selectedReqId);
    if (!target) {
      window.showMessage && window.showMessage('请选择关联需求！', 'error');
      return;
    }
    reqId = target.id;
  } else if (type === 'case') {
    const selectedCaseId = Number(document.getElementById('s5CaseSelect').value || 0);
    const target = state.overallTestOptions.cases.find((c) => c.id === selectedCaseId);
    if (!target) {
      window.showMessage && window.showMessage('请选择关联用例！', 'error');
      return;
    }
    reqId = target.req_id;
    sourceRef = target.label;
  } else if (type === 'legacy_bug') {
    const selectedLegacyBugId = Number(document.getElementById('s5LegacySelect').value || 0);
    const target = state.overallTestOptions.bugs.find((b) => b.id === selectedLegacyBugId);
    if (!target) {
      window.showMessage && window.showMessage('请选择关联历史Bug！', 'error');
      return;
    }
    reqId = target.req_id;
    sourceRef = target.label;
  }
  try {
    await api('/overall-test/issues', {
      method: 'POST',
      headers: window.H,
      body: { major_version_id: Number(document.getElementById('s5MajorSelect')?.value || 0), requirement_id: reqId, source_type: type, source_ref: sourceRef, bug_id: bugId, minor_version_id: Number(document.getElementById('s5MinorSelect')?.value || 0) },
    });
    window.showMessage && window.showMessage('新问题已成功添加到大盘！', 'success');
    document.getElementById('s5BugId').value = '';
    await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '添加失败', 'error');
  }
}

// Reactivate Modal

// In-memory state for the reactivate / assign modals
let _s5ModalZtId = '';
let _s5ModalBugDbId = 0;
let _s5ActionMeta = { users: {}, builds: {} };

async function _loadActionMeta(ztId, action = 'activate') {
  try {
    const data = await (await api(`/zentao/bugs/${ztId}/action-meta?action=${action}`)).json();
    _s5ActionMeta = data || { users: {}, builds: {} };
  } catch {
    _s5ActionMeta = { users: {}, builds: {} };
  }
  return _s5ActionMeta;
}

function _buildSearchableUserSelect(selectId, users, currentAssigned = '') {
  const container = document.getElementById(selectId + 'Container');
  if (!container) return;

  const entries = Object.entries(users || {}).sort((a, b) => a[1].localeCompare(b[1], 'zh-CN'));
  if (!entries.length) {
    container.innerHTML = '<div style="color:#94a3b8; font-size:13px;">未获取到候选人列表</div>';
    return;
  }

  const hiddenId = selectId;
  const inputId = `${selectId}Input`;
  const dropdownId = `${selectId}Dropdown`;
  const clearId = `${selectId}ClearBtn`;

  container.innerHTML = `
    <div style="position:relative;" data-s5-combobox>
      <input type="hidden" id="${hiddenId}" value="">
      <div style="display:flex; gap:6px;">
        <input id="${inputId}" autocomplete="off"
          placeholder="点击选择指派人（可按姓名/账号过滤）"
          style="flex:1; padding:8px; border:1px solid #cbd5e1; border-radius:6px; font-size:13px;">
        <button type="button" class="secondary" id="${clearId}"
          style="padding:6px 10px; font-size:12px;">清除</button>
      </div>
      <div id="${dropdownId}" style="display:none; position:absolute; left:0; right:0; top:100%; margin-top:4px; max-height:220px; overflow-y:auto; background:#ffffff; border:1px solid #cbd5e1; border-radius:6px; box-shadow:0 4px 12px rgba(15,23,42,0.08); z-index:20;"></div>
    </div>`;

  const input = document.getElementById(inputId);
  const hidden = document.getElementById(hiddenId);
  const dropdown = document.getElementById(dropdownId);
  const clearBtn = document.getElementById(clearId);
  if (!input || !hidden || !dropdown || !clearBtn) return;

  function renderOptions(filter = '') {
    const kw = String(filter || '').toLowerCase();
    const items = entries.filter(([acc, name]) => !kw
      || String(acc).toLowerCase().includes(kw)
      || String(name).toLowerCase().includes(kw));
    if (!items.length) {
      dropdown.innerHTML = '<div style="padding:8px 10px; color:#94a3b8; font-size:13px;">无匹配候选人</div>';
      return;
    }
    dropdown.innerHTML = items.map(([acc, name]) => `
      <div class="s5-assign-option" data-account="${escapeHtml(acc)}" data-name="${escapeHtml(name)}"
        style="padding:8px 10px; font-size:13px; color:#0f172a; cursor:pointer; border-bottom:1px solid #f1f5f9;">
        <span style="font-weight:600;">${escapeHtml(name)}</span>
        <span style="color:#94a3b8; margin-left:6px; font-size:12px;">${escapeHtml(acc)}</span>
      </div>
    `).join('');
    Array.from(dropdown.querySelectorAll('.s5-assign-option')).forEach((opt) => {
      opt.addEventListener('mousedown', (ev) => {
        ev.preventDefault();
        hidden.value = opt.getAttribute('data-account') || '';
        input.value = opt.getAttribute('data-name') || '';
        dropdown.style.display = 'none';
      });
      opt.addEventListener('mouseenter', () => { opt.style.background = '#f1f5f9'; });
      opt.addEventListener('mouseleave', () => { opt.style.background = ''; });
    });
  }

  function openDropdown() {
    renderOptions(input.value);
    dropdown.style.display = 'block';
  }

  input.addEventListener('focus', openDropdown);
  input.addEventListener('click', openDropdown);
  input.addEventListener('input', () => {
    hidden.value = '';
    renderOptions(input.value);
  });
  input.addEventListener('blur', () => setTimeout(() => { dropdown.style.display = 'none'; }, 120));
  clearBtn.addEventListener('click', () => {
    hidden.value = '';
    input.value = '';
    renderOptions('');
    input.focus();
  });

  if (currentAssigned && entries.some(([acc]) => acc === currentAssigned)) {
    const found = entries.find(([acc]) => acc === currentAssigned);
    hidden.value = currentAssigned;
    input.value = found ? String(found[1]) : currentAssigned;
  }
}

export function openS5ReactivateModal(bugDbId, zentaoBugId) {
  _s5ModalBugDbId = bugDbId;
  _s5ModalZtId = zentaoBugId;
  const modal = document.getElementById('s5ReactivateModal');
  if (!modal) return;

  // Reset form
  const commentEl = document.getElementById('s5ReactivateComment');
  if (commentEl) commentEl.value = '';
  const userContainer = document.getElementById('s5ReactivateUserSelectContainer');
  if (userContainer) userContainer.innerHTML = '<div style="color:#94a3b8; font-size:13px;">加载候选人中...</div>';

  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  bindModalEsc('s5ReactivateModal', closeS5ReactivateModal);

  // Load action meta for user list and build list
  _loadActionMeta(zentaoBugId, 'activate').then((meta) => {
    _buildSearchableUserSelect('s5ReactivateUserSelect', meta.users, meta.current_assigned || '');
    // Populate build select
    const buildSel = document.getElementById('s5ReactivateBuildSelect');
    if (buildSel) {
      const builds = meta.builds || {};
      buildSel.innerHTML = '<option value="">-- 选择影响版本（可选）--</option>' +
        Object.entries(builds).map(([bid, bname]) => `<option value="${escapeHtml(bid)}">${escapeHtml(String(bname))}</option>`).join('');
    }
  });
}

// Promise-aware opener used by saveS5's "uncheck a closed bug" flow.
// Resolves to `true` when the user successfully reactivates, `false`
// when they cancel or the reactivation fails. Co-exists with the plain
// openS5ReactivateModal for the existing "重新激活" button entry.
let _s5ReactivateResolver = null;

export function openS5ReactivateModalAsync(bugDbId, zentaoBugId) {
  openS5ReactivateModal(bugDbId, zentaoBugId);
  return new Promise((resolve) => {
    _s5ReactivateResolver = resolve;
  });
}

export async function submitS5Reactivate() {
  const assignedTo = document.getElementById('s5ReactivateUserSelect')?.value || '';
  const openedBuild = document.getElementById('s5ReactivateBuildSelect')?.value || '';
  const comment = document.getElementById('s5ReactivateComment')?.value || '';

  if (!_s5ModalZtId) return;
  try {
    await api(`/zentao/bugs/${Number(_s5ModalZtId)}/active`, {
      method: 'POST',
      headers: window.H,
      body: {
        assigned_to: assignedTo,
        opened_build: openedBuild ? [openedBuild] : [],
        comment,
      },
    });
    window.showMessage && window.showMessage('禅道 Bug 已重新激活', 'success');
    if (_s5ReactivateResolver) {
      const resolver = _s5ReactivateResolver;
      _s5ReactivateResolver = null;
      resolver(true);
      closeS5ReactivateModal();
      return;
    }
    closeS5ReactivateModal();
    await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '激活失败', 'error');
  }
}

export function closeS5ReactivateModal() {
  const modal = document.getElementById('s5ReactivateModal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  unbindModalEsc('s5ReactivateModal');
  if (_s5ReactivateResolver) {
    const resolver = _s5ReactivateResolver;
    _s5ReactivateResolver = null;
    resolver(false);
  }
}

// Assign Modal

export function openS5AssignModal(bugDbId, zentaoBugId) {
  _s5ModalBugDbId = bugDbId;
  _s5ModalZtId = zentaoBugId;
  const modal = document.getElementById('s5AssignModal');
  if (!modal) return;

  const commentEl = document.getElementById('s5AssignComment');
  if (commentEl) commentEl.value = '';
  const userContainer = document.getElementById('s5AssignUserSelectContainer');
  if (userContainer) userContainer.innerHTML = '<div style="color:#94a3b8; font-size:13px;">加载候选人中...</div>';

  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  bindModalEsc('s5AssignModal', closeS5AssignModal);

  _loadActionMeta(zentaoBugId, 'activate').then((meta) => {
    _buildSearchableUserSelect('s5AssignUserSelect', meta.users, meta.current_assigned || '');
  });
}

export async function submitS5Assign() {
  const assignedTo = document.getElementById('s5AssignUserSelect')?.value || '';
  const comment = document.getElementById('s5AssignComment')?.value || '';
  if (!assignedTo) {
    window.showMessage && window.showMessage('请选择指派人', 'error');
    return;
  }
  if (!_s5ModalZtId) return;
  try {
    await api(`/zentao/bugs/${Number(_s5ModalZtId)}/assign`, {
      method: 'POST',
      headers: window.H,
      body: { assigned_to: assignedTo, comment },
    });
    window.showMessage && window.showMessage(`禅道 Bug 已指派给 ${assignedTo}`, 'success');
    closeS5AssignModal();
    await loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '指派失败', 'error');
  }
}

export function closeS5AssignModal() {
  const modal = document.getElementById('s5AssignModal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  unbindModalEsc('s5AssignModal');
}

// Create Zentao Bug

let _s5CreateBugMeta = null;

// Pre-fill template matches what Zentao's native create-bug page shows:
// three editable sections so the reporter can fill in each part directly.
const S5_STEPS_TEMPLATE = '[步骤]\n1. \n2. \n3. \n\n[结果]\n\n\n[期望]\n\n';
// HTML version used for the contenteditable reproduce-steps editor. We
// convert newlines into <br> so the template renders the same way it
// would in a plain textarea, but still allows inline <img> nodes.
const S5_STEPS_TEMPLATE_HTML = S5_STEPS_TEMPLATE
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/\r\n|\n/g, '<br>');

// Track whether the paste handler has been bound so we don't attach it twice.
let _s5StepsPasteHandlerBound = false;

function _insertHtmlAtCaret(html) {
  const el = document.getElementById('s5CreateBugSteps');
  if (!el) return;
  el.focus();
  const sel = window.getSelection();
  if (sel && sel.rangeCount > 0 && el.contains(sel.anchorNode)) {
    const range = sel.getRangeAt(0);
    range.deleteContents();
    const frag = range.createContextualFragment(html);
    const lastNode = frag.lastChild;
    range.insertNode(frag);
    if (lastNode) {
      const newRange = document.createRange();
      newRange.setStartAfter(lastNode);
      newRange.collapse(true);
      sel.removeAllRanges();
      sel.addRange(newRange);
    }
  } else {
    el.insertAdjacentHTML('beforeend', html);
  }
}

async function _uploadInlineStepsImage(file) {
  const form = new FormData();
  form.append('file', file, file.name || `paste-${Date.now()}.png`);
  const resp = await api('/zentao/bugs/upload-inline-image', { method: 'POST', body: form });
  return await resp.json();
}

function _bindS5StepsPasteHandler() {
  const el = document.getElementById('s5CreateBugSteps');
  if (!el || _s5StepsPasteHandlerBound) return;
  el.addEventListener('paste', async (event) => {
    const cd = event.clipboardData;
    if (!cd) return;
    const items = Array.from(cd.items || []);
    const imageItems = items.filter((it) => it.kind === 'file' && (it.type || '').startsWith('image/'));
    if (!imageItems.length) return;
    event.preventDefault();
    for (const item of imageItems) {
      const file = item.getAsFile();
      if (!file) continue;
      const placeholderId = `s5-img-ph-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      _insertHtmlAtCaret(`<span id="${placeholderId}" style="color:#94a3b8; font-size:12px;">[上传中…]</span>`);
      try {
        const info = await _uploadInlineStepsImage(file);
        // Preview URL: a client-side blob URL so the image displays
        // immediately without needing to re-fetch through our auth-
        // protected /zentao/files proxy. Submit URL: the absolute URL we
        // want Zentao to store (either Zentao's own file-read URL or our
        // local inline-images URL when storage=local).
        const previewUrl = URL.createObjectURL(file);
        const submitUrl = info.zentao_url || info.proxy_url || '';
        const imgHtml = `<img src="${previewUrl}" data-submit-url="${escapeHtml(submitUrl)}" data-storage="${escapeHtml(info.storage || '')}" alt="${escapeHtml(info.title || 'image')}" style="max-width:100%; margin:4px 0; border-radius:4px;"/>`;
        const placeholder = document.getElementById(placeholderId);
        if (placeholder) {
          placeholder.outerHTML = imgHtml;
        } else {
          _insertHtmlAtCaret(imgHtml);
        }
      } catch (err) {
        const placeholder = document.getElementById(placeholderId);
        const msg = (err && err.message) ? err.message : '上传失败';
        if (placeholder) {
          placeholder.outerHTML = `<span style="color:#dc2626; font-size:12px;">[图片上传失败: ${escapeHtml(msg)}]</span>`;
        }
        window.showMessage && window.showMessage(`图片上传失败: ${msg}`, 'error');
      }
    }
  });
  _s5StepsPasteHandlerBound = true;
}

function _serializeS5StepsForSubmit() {
  const source = document.getElementById('s5CreateBugSteps');
  if (!source) return '';
  const clone = source.cloneNode(true);
  clone.querySelectorAll('img[data-submit-url]').forEach((img) => {
    const submitUrl = img.getAttribute('data-submit-url');
    if (submitUrl) img.setAttribute('src', submitUrl);
    img.removeAttribute('data-submit-url');
    img.removeAttribute('data-storage');
  });
  return (clone.innerHTML || '').trim();
}

function _resetS5CreateBugForm() {
  const titleEl = document.getElementById('s5CreateBugTitle');
  if (titleEl) titleEl.value = '';
  const stepsEl = document.getElementById('s5CreateBugSteps');
  if (stepsEl) stepsEl.innerHTML = S5_STEPS_TEMPLATE_HTML;
  ['s5CreateBugSeverity', 's5CreateBugPri'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '3';
  });
  ['s5CreateBugProduct', 's5CreateBugProject', 's5CreateBugExecution', 's5CreateBugModule', 's5CreateBugBuild', 's5CreateBugStory'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  const typeEl = document.getElementById('s5CreateBugType');
  if (typeEl) typeEl.value = 'codeerror';
}

function _fillS5CreateBugSelect(selectId, options, placeholder, defaultValue = '') {
  const select = document.getElementById(selectId);
  if (!select) return;
  let entries = Object.entries(options || {});
  // Build select: show newest first. Zentao returns builds in
  // ascending order of creation; reverse so users don't have to scroll
  // to the bottom to pick the latest small version.
  if (selectId === 's5CreateBugBuild') {
    entries = entries.slice().reverse();
  }
  const allowEmpty = selectId !== 's5CreateBugType';
  const html = [];
  if (allowEmpty) {
    html.push(`<option value="">${escapeHtml(placeholder)}</option>`);
  }
  entries.forEach(([value, label]) => {
    html.push(`<option value="${escapeHtml(value)}">${escapeHtml(label || value)}</option>`);
  });
  if (!entries.length && !allowEmpty) {
    html.push('<option value="codeerror">codeerror</option>');
  }
  select.innerHTML = html.join('');
  if (defaultValue && entries.some(([value]) => value === defaultValue)) {
    select.value = defaultValue;
  } else if (allowEmpty) {
    select.value = '';
  } else {
    select.value = entries[0]?.[0] || 'codeerror';
  }
}

function _collectS5CreateBugSelections() {
  return {
    productId: document.getElementById('s5CreateBugProduct')?.value || '',
    projectId: document.getElementById('s5CreateBugProject')?.value || '',
    assignedTo: document.getElementById('s5CreateBugAssignTo')?.value || '',
    executionId: document.getElementById('s5CreateBugExecution')?.value || '',
    moduleId: document.getElementById('s5CreateBugModule')?.value || '',
    buildId: document.getElementById('s5CreateBugBuild')?.value || '',
    storyId: document.getElementById('s5CreateBugStory')?.value || '',
    bugType: document.getElementById('s5CreateBugType')?.value || 'codeerror',
  };
}

function _renderS5CreateBugUserSelect(users, selectedValue = '') {
  const userContainer = document.getElementById('s5CreateBugUserContainer');
  if (!userContainer) return;
  const userEntries = Object.entries(users || {}).sort((a, b) => String(a[1]).localeCompare(String(b[1]), 'zh-CN'));
  if (userEntries.length === 0) {
    userContainer.innerHTML = '<div style="color:#94a3b8; font-size:13px;">未获取到候选人列表</div>';
    return;
  }

  // Combobox: one visible input shows the display name; a hidden field
  // stores the account that is actually submitted to Zentao. Clicking
  // the input opens a filterable dropdown.
  userContainer.innerHTML = `
    <div style="position:relative;" data-s5-combobox>
      <input type="hidden" id="s5CreateBugAssignTo" value="">
      <div style="display:flex; gap:6px;">
        <input id="s5CreateBugAssignToInput" autocomplete="off"
          placeholder="点击选择指派人（可按姓名/账号过滤）"
          style="flex:1; padding:8px; border:1px solid #cbd5e1; border-radius:6px; font-size:13px;">
        <button type="button" class="secondary" id="s5CreateBugAssignClearBtn"
          style="padding:6px 10px; font-size:12px;">清除</button>
      </div>
      <div id="s5CreateBugAssignDropdown" style="display:none; position:absolute; left:0; right:0; top:100%; margin-top:4px; max-height:220px; overflow-y:auto; background:#ffffff; border:1px solid #cbd5e1; border-radius:6px; box-shadow:0 4px 12px rgba(15,23,42,0.08); z-index:20;"></div>
    </div>`;

  const input = document.getElementById('s5CreateBugAssignToInput');
  const hidden = document.getElementById('s5CreateBugAssignTo');
  const dropdown = document.getElementById('s5CreateBugAssignDropdown');
  const clearBtn = document.getElementById('s5CreateBugAssignClearBtn');
  if (!input || !hidden || !dropdown || !clearBtn) return;

  function renderOptions(filter = '') {
    const kw = String(filter || '').toLowerCase();
    const items = userEntries.filter(([acc, name]) => !kw
      || String(acc).toLowerCase().includes(kw)
      || String(name).toLowerCase().includes(kw));
    if (!items.length) {
      dropdown.innerHTML = '<div style="padding:8px 10px; color:#94a3b8; font-size:13px;">无匹配候选人</div>';
      return;
    }
    dropdown.innerHTML = items.map(([acc, name]) => `
      <div class="s5-assign-option" data-account="${escapeHtml(acc)}" data-name="${escapeHtml(name)}"
        style="padding:8px 10px; font-size:13px; color:#0f172a; cursor:pointer; border-bottom:1px solid #f1f5f9;">
        <span style="font-weight:600;">${escapeHtml(name)}</span>
        <span style="color:#94a3b8; margin-left:6px; font-size:12px;">${escapeHtml(acc)}</span>
      </div>
    `).join('');
    Array.from(dropdown.querySelectorAll('.s5-assign-option')).forEach((opt) => {
      opt.addEventListener('mousedown', (ev) => {
        ev.preventDefault();
        const acc = opt.getAttribute('data-account');
        const name = opt.getAttribute('data-name');
        hidden.value = acc;
        input.value = name;
        dropdown.style.display = 'none';
      });
      opt.addEventListener('mouseenter', () => { opt.style.background = '#f1f5f9'; });
      opt.addEventListener('mouseleave', () => { opt.style.background = ''; });
    });
  }

  function openDropdown() {
    renderOptions(input.value);
    dropdown.style.display = 'block';
  }
  input.addEventListener('focus', openDropdown);
  input.addEventListener('click', openDropdown);
  input.addEventListener('input', () => renderOptions(input.value));
  input.addEventListener('blur', () => setTimeout(() => { dropdown.style.display = 'none'; }, 120));
  clearBtn.addEventListener('click', () => {
    hidden.value = '';
    input.value = '';
    renderOptions('');
    input.focus();
  });

  if (selectedValue && userEntries.some(([acc]) => acc === selectedValue)) {
    const found = userEntries.find(([acc]) => acc === selectedValue);
    hidden.value = selectedValue;
    input.value = found ? String(found[1]) : selectedValue;
  }
}

function _applyS5CreateBugMeta(meta, previous = {}) {
  const hintEl = document.getElementById('s5CreateBugMetaHint');
  const productId = String(meta.selected_product_id || (meta.product_ids || [])[0] || '');
  const projectId = String(meta.selected_project_id || '');
  const selectedExecutionId = String(meta.selected_execution_id || meta.execution_id || '');
  const executionOptions = meta.executions || {};
  const createAccess = meta.create_access || {};

  _fillS5CreateBugSelect('s5CreateBugProduct', meta.products || {}, '-- 请选择产品 --', previous.productId || productId);
  _fillS5CreateBugSelect('s5CreateBugProject', meta.projects || {}, '-- 请选择项目 --', previous.projectId || projectId);
  _fillS5CreateBugSelect('s5CreateBugExecution', executionOptions, '-- 请选择执行 --', previous.executionId || selectedExecutionId);
  _fillS5CreateBugSelect('s5CreateBugModule', meta.modules || {}, '-- 请选择模块 --', previous.moduleId);
  _fillS5CreateBugSelect('s5CreateBugBuild', meta.builds || {}, '-- 请选择小版本 --', previous.buildId);
  _fillS5CreateBugSelect('s5CreateBugStory', meta.stories || {}, '-- 不关联故事 --', previous.storyId);
  _fillS5CreateBugSelect(
    's5CreateBugType',
    Object.keys(meta.bug_types || {}).length ? meta.bug_types : { codeerror: '代码错误 (codeerror)' },
    'codeerror',
    previous.bugType || 'codeerror',
  );
  _renderS5CreateBugUserSelect(meta.users || {}, previous.assignedTo || '');

  if (hintEl) {
    hintEl.innerText = (createAccess.ok === false && createAccess.message)
      ? `⚠️ ${createAccess.message}`
      : (productId
        ? `产品ID: ${productId}${projectId ? `  项目ID: ${projectId}` : ''}${selectedExecutionId ? `  执行ID: ${selectedExecutionId}` : ''}`
        : '⚠️ 未找到产品信息，请确认该大版本已配置禅道执行版本映射');
  }
}

async function _loadS5CreateBugMeta(majorId, options = {}, previous = {}) {
  const hintEl = document.getElementById('s5CreateBugMetaHint');
  const userContainer = document.getElementById('s5CreateBugUserContainer');
  const errEl = document.getElementById('s5CreateBugErrorMsg');
  if (hintEl) hintEl.innerText = '正在加载产品/用户信息...';
  if (userContainer) userContainer.innerHTML = '<div style="color:#94a3b8; font-size:13px;">加载中...</div>';
  if (errEl) errEl.style.display = 'none';

  const parts = [];
  if (options.productId) parts.push(`product_id=${encodeURIComponent(options.productId)}`);
  if (options.projectId) parts.push(`project_id=${encodeURIComponent(options.projectId)}`);
  if (options.executionId) parts.push(`execution_id=${encodeURIComponent(options.executionId)}`);
  const qs = parts.length ? `&${parts.join('&')}` : '';
  const resp = await api(`/zentao/bugs/create-meta?major_version_id=${majorId}${qs}`);
  _s5CreateBugMeta = await resp.json();
  _applyS5CreateBugMeta(_s5CreateBugMeta, previous);
}

export async function openS5CreateZentaoBugModal() {
  const modal = document.getElementById('s5CreateZentaoBugModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  bindModalEsc('s5CreateZentaoBugModal', closeS5CreateZentaoBugModal);

  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  const hintEl = document.getElementById('s5CreateBugMetaHint');
  const userContainer = document.getElementById('s5CreateBugUserContainer');
  const errEl = document.getElementById('s5CreateBugErrorMsg');
  if (errEl) errEl.style.display = 'none';

  _resetS5CreateBugForm();
  _bindS5StepsPasteHandler();
  _fillS5CreateBugSelect('s5CreateBugProduct', {}, '-- 请选择产品 --');
  _fillS5CreateBugSelect('s5CreateBugProject', {}, '-- 请选择项目 --');
  _fillS5CreateBugSelect('s5CreateBugExecution', {}, '-- 请选择执行 --');
  _fillS5CreateBugSelect('s5CreateBugModule', {}, '-- 请选择模块 --');
  _fillS5CreateBugSelect('s5CreateBugBuild', {}, '-- 请选择小版本 --');
  _fillS5CreateBugSelect('s5CreateBugStory', {}, '-- 不关联故事 --');
  _fillS5CreateBugSelect('s5CreateBugType', { codeerror: '代码错误 (codeerror)' }, 'codeerror', 'codeerror');

  if (!majorId) {
    if (hintEl) hintEl.innerText = '⚠️ 请先在整体测试页选择一个具体大版本';
    if (userContainer) userContainer.innerHTML = '<div style="color:#94a3b8; font-size:13px;">请先选择大版本</div>';
    return;
  }

  try {
    await _loadS5CreateBugMeta(majorId);
  } catch (err) {
    if (hintEl) hintEl.innerText = `加载失败: ${err.message}`;
    if (userContainer) userContainer.innerHTML = '<div style="color:#dc2626; font-size:13px;">获取元数据失败</div>';
  }
}

export async function onS5CreateBugProductChange() {
  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  if (!majorId) return;
  const previous = _collectS5CreateBugSelections();
  try {
    await _loadS5CreateBugMeta(majorId, {
      productId: previous.productId,
      projectId: previous.projectId,
    }, previous);
  } catch (err) {
    const errEl = document.getElementById('s5CreateBugErrorMsg');
    if (errEl) {
      errEl.innerText = err.message || '加载产品元数据失败';
      errEl.style.display = 'block';
    }
  }
}

export async function onS5CreateBugProjectChange() {
  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  if (!majorId) return;
  const previous = _collectS5CreateBugSelections();
  try {
    await _loadS5CreateBugMeta(majorId, {
      productId: previous.productId,
      projectId: previous.projectId,
    }, previous);
  } catch (err) {
    const errEl = document.getElementById('s5CreateBugErrorMsg');
    if (errEl) {
      errEl.innerText = err.message || '加载项目元数据失败';
      errEl.style.display = 'block';
    }
  }
}

export async function onS5CreateBugExecutionChange() {
  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  if (!majorId) return;
  const previous = _collectS5CreateBugSelections();
  try {
    await _loadS5CreateBugMeta(majorId, {
      productId: previous.productId,
      projectId: previous.projectId,
      executionId: previous.executionId,
    }, previous);
  } catch (err) {
    const errEl = document.getElementById('s5CreateBugErrorMsg');
    if (errEl) {
      errEl.innerText = err.message || '加载执行元数据失败';
      errEl.style.display = 'block';
    }
  }
}

export function closeS5CreateZentaoBugModal() {
  const modal = document.getElementById('s5CreateZentaoBugModal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _s5CreateBugMeta = null;
  unbindModalEsc('s5CreateZentaoBugModal');
  // Reset the steps fullscreen state so the next open starts collapsed.
  if (_s5StepsFullscreen) toggleS5StepsFullscreen();
}

// Track the fullscreen state of the 复现步骤 box. We toggle inline styles
// rather than a CSS class so no stylesheet change is needed.
let _s5StepsFullscreen = false;
let _s5StepsOrigStyle = '';
let _s5StepsWrapOrigStyle = '';

export function toggleS5StepsFullscreen() {
  const wrap = document.getElementById('s5CreateBugStepsWrap');
  const editor = document.getElementById('s5CreateBugSteps');
  const btn = document.getElementById('s5CreateBugStepsExpandBtn');
  if (!wrap || !editor) return;

  if (!_s5StepsFullscreen) {
    _s5StepsWrapOrigStyle = wrap.getAttribute('style') || '';
    _s5StepsOrigStyle = editor.getAttribute('style') || '';
    wrap.setAttribute('style',
      'position:fixed; inset:24px; z-index:10020; background:#fff; border:1px solid #cbd5e1;'
      + ' border-radius:14px; box-shadow:0 16px 40px rgba(0,0,0,.22); padding:18px 20px;'
      + ' display:flex; flex-direction:column; margin-bottom:0;');
    editor.setAttribute('style',
      'flex:1; width:100%; box-sizing:border-box; padding:12px; border:1px solid #cbd5e1;'
      + ' border-radius:6px; font-size:14px; min-height:0; overflow-y:auto;'
      + ' white-space:pre-wrap; outline:none; background:#fff; line-height:1.6;');
    if (btn) btn.innerText = '⤢ 收起';
    _s5StepsFullscreen = true;
  } else {
    wrap.setAttribute('style', _s5StepsWrapOrigStyle);
    editor.setAttribute('style', _s5StepsOrigStyle);
    if (btn) btn.innerText = '⛶ 放大';
    _s5StepsFullscreen = false;
  }
}

export async function submitS5CreateZentaoBug() {
  const title = (document.getElementById('s5CreateBugTitle')?.value || '').trim();
  const steps = _serializeS5StepsForSubmit();
  const severity = Number(document.getElementById('s5CreateBugSeverity')?.value || 3);
  const pri = Number(document.getElementById('s5CreateBugPri')?.value || 3);
  const assignedTo = document.getElementById('s5CreateBugAssignTo')?.value || '';
  const productId = document.getElementById('s5CreateBugProduct')?.value || '';
  const projectId = document.getElementById('s5CreateBugProject')?.value || '';
  const executionId = document.getElementById('s5CreateBugExecution')?.value || '';
  const moduleId = document.getElementById('s5CreateBugModule')?.value || '';
  const openedBuild = document.getElementById('s5CreateBugBuild')?.value || '';
  const storyId = document.getElementById('s5CreateBugStory')?.value || '';
  const bugType = document.getElementById('s5CreateBugType')?.value || 'codeerror';
  const errEl = document.getElementById('s5CreateBugErrorMsg');

  if (!title) {
    if (errEl) { errEl.innerText = 'Bug 标题不能为空'; errEl.style.display = 'block'; }
    return;
  }
  if (!_s5CreateBugMeta || !(_s5CreateBugMeta.product_ids || []).length) {
    if (errEl) { errEl.innerText = '未获取到产品信息，无法创建'; errEl.style.display = 'block'; }
    return;
  }
  if (!productId) {
    if (errEl) { errEl.innerText = '请选择产品'; errEl.style.display = 'block'; }
    return;
  }
  if (Object.keys(_s5CreateBugMeta.projects || {}).length > 0 && !projectId) {
    if (errEl) { errEl.innerText = '请选择项目'; errEl.style.display = 'block'; }
    return;
  }
  if (_s5CreateBugMeta.create_access && _s5CreateBugMeta.create_access.ok === false) {
    if (errEl) {
      errEl.innerText = _s5CreateBugMeta.create_access.message || '当前禅道账号无法在该产品下创建Bug';
      errEl.style.display = 'block';
    }
    return;
  }
  if (Object.keys(_s5CreateBugMeta.executions || {}).length > 0 && !executionId) {
    if (errEl) { errEl.innerText = '请选择执行'; errEl.style.display = 'block'; }
    return;
  }
  if (Object.keys(_s5CreateBugMeta.builds || {}).length > 0 && !openedBuild) {
    if (errEl) { errEl.innerText = '请选择影响版本（禅道强制要求）'; errEl.style.display = 'block'; }
    return;
  }
  if (errEl) errEl.style.display = 'none';

  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  const minorId = Number(document.getElementById('s5MinorSelect')?.value || 0);
  const submitBtn = document.getElementById('s5CreateBugSubmitBtn');
  if (submitBtn) { submitBtn.disabled = true; submitBtn.innerText = '提交中...'; }

  try {
    const body = {
      product_id: Number(productId),
      project_id: projectId ? Number(projectId) : null,
      execution_id: executionId ? Number(executionId) : null,
      title,
      severity,
      pri,
      steps,
      assigned_to: assignedTo,
      opened_build: openedBuild ? [openedBuild] : [],
      bug_type: bugType,
      module_id: moduleId || null,
      story_id: storyId || null,
      major_version_id: majorId || null,
      found_minor_version_id: minorId || null,
    };
    const resp = await api('/zentao/bugs', { method: 'POST', body });
    const result = await resp.json();
    window.showMessage && window.showMessage(`禅道Bug #${result.zentao_bug_id} 创建成功`, 'success');
    closeS5CreateZentaoBugModal();
    if (majorId) {
      loadOverallTest({ syncBeforeLoad: false, silentSync: true, showSuccess: false });
    }
  } catch (err) {
    if (errEl) { errEl.innerText = err.message || '创建失败，请稍后重试'; errEl.style.display = 'block'; }
  } finally {
    if (submitBtn) { submitBtn.disabled = false; submitBtn.innerText = '🚀 提交到禅道'; }
  }
}

const OmniQAOverallTestTab = {
  loadOverallTest,
  renderS5,
  saveS5,
  editS5Bug,
  removeS5Bug,
  pushOverallTest,
  refreshS5EntryMode,
  toggleS5BugInputs,
  loadS5OptionsData,
  submitS5Bug,
  syncOverallTestFromZentao,
  syncOverallTestRecentFromZentao,
  prevS5Page,
  nextS5Page,
  applyS5PageSize,
  applyS5Filters,
  resetS5Filters,
  openS5ReactivateModal,
  openS5ReactivateModalAsync,
  submitS5Reactivate,
  closeS5ReactivateModal,
  openS5AssignModal,
  submitS5Assign,
  closeS5AssignModal,
  toggleS5CloseComment,
  openZentaoBugPreview,
  closeZentaoBugPreview,
  openZentaoBugImageLightbox,
  openZentaoBugImageLightboxByIndex,
  closeZentaoBugImageLightbox,
  prevZentaoBugImageLightbox,
  nextZentaoBugImageLightbox,
  openS5CreateZentaoBugModal,
  closeS5CreateZentaoBugModal,
  submitS5CreateZentaoBug,
  onS5CreateBugProductChange,
  onS5CreateBugProjectChange,
  onS5CreateBugExecutionChange,
  toggleS5StepsFullscreen,
  toggleS5DetailRow,
};
window.OmniQAOverallTestTab = OmniQAOverallTestTab;
window.loadOverallTest = loadOverallTest;
window.pushOverallTest = pushOverallTest;
window.syncOverallTestFromZentao = syncOverallTestFromZentao;
window.syncOverallTestRecentFromZentao = syncOverallTestRecentFromZentao;
window.editS5Bug = editS5Bug;
window.editS5BugById = editS5BugById;
window.removeS5Bug = removeS5Bug;
window.removeS5BugById = removeS5BugById;
window.openS5AssignById = openS5AssignById;
window.openS5ReactivateById = openS5ReactivateById;
window.prevS5Page = prevS5Page;
window.nextS5Page = nextS5Page;
window.applyS5PageSize = applyS5PageSize;
window.applyS5Filters = applyS5Filters;
window.resetS5Filters = resetS5Filters;
window.saveS5 = saveS5;
window.openS5PreviewById = openS5PreviewById;
window.toggleS5DetailRow = toggleS5DetailRow;
window.toggleS5CloseComment = toggleS5CloseComment;
window.openZentaoBugPreview = openZentaoBugPreview;
window.closeZentaoBugPreview = closeZentaoBugPreview;
window.openZentaoBugImageLightbox = openZentaoBugImageLightbox;
window.openZentaoBugImageLightboxByIndex = openZentaoBugImageLightboxByIndex;
window.closeZentaoBugImageLightbox = closeZentaoBugImageLightbox;
window.prevZentaoBugImageLightbox = prevZentaoBugImageLightbox;
window.nextZentaoBugImageLightbox = nextZentaoBugImageLightbox;
window.openS5ReactivateModal = openS5ReactivateModal;
window.submitS5Reactivate = submitS5Reactivate;
window.closeS5ReactivateModal = closeS5ReactivateModal;
window.openS5AssignModal = openS5AssignModal;
window.submitS5Assign = submitS5Assign;
window.closeS5AssignModal = closeS5AssignModal;
window.openS5CreateZentaoBugModal = openS5CreateZentaoBugModal;
window.closeS5CreateZentaoBugModal = closeS5CreateZentaoBugModal;
window.submitS5CreateZentaoBug = submitS5CreateZentaoBug;
window.onS5CreateBugProductChange = onS5CreateBugProductChange;
window.onS5CreateBugProjectChange = onS5CreateBugProjectChange;
window.onS5CreateBugExecutionChange = onS5CreateBugExecutionChange;
window.toggleS5StepsFullscreen = toggleS5StepsFullscreen;

// Overall-Test SSE: 新 bug 底部提示 + 卡片流光

const s5NewBugCount = { value: 0 };
let s5ReloadTimer = null;

function deferReloadOverallTest(ms = 600) {
  if (s5ReloadTimer) clearTimeout(s5ReloadTimer);
  s5ReloadTimer = setTimeout(() => {
    s5ReloadTimer = null;
    if (!window.isWorkbenchSubtabActive?.('overall-test')) return;
    const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
    const softwareId = Number(window.currentSoftwareId || 0);
    if (majorId === 0 && !softwareId) return;
    loadOverallTest({ syncBeforeLoad: false, showSuccess: false }).catch(() => {});
  }, ms);
}

function showS5BottomBanner() {
  if (!window.OmniQASSE?.showPositionBanner) return;
  const tableWrap = document.getElementById('s5TableContainer');
  if (!tableWrap) return;
  window.OmniQASSE.showPositionBanner({
    scrollContainer: tableWrap,
    anchorEl: tableWrap,
    position: 'bottom',
    countRef: s5NewBugCount,
    labelFn: (n) => `下面有 ${n} 条新增 Bug，点击刷新`,
    onClickScroll: () => {
      s5NewBugCount.value = 0;
      loadOverallTest({ syncBeforeLoad: false, showSuccess: false });
    },
    bannerId: 'overall-test-new-bug',
  });
}

function bindOverallTestSSE() {
  if (overallTestSseBound) return;
  if (!window.OmniQASSE?.subscribe) return;

  window.OmniQASSE.subscribe('overall_bug_created', ({ payload }) => {
    const bugId = Number(payload?.id || payload?.bug_id || 0);
    if (bugId) {
      const el = document.querySelector(`.overall-test-row-card[data-bug-id='${bugId}']`);
      if (el) window.OmniQASSE.pulseBoundaryGlow(el, 'blue');
    }
    const tableWrap = document.getElementById('s5TableContainer');
    const atBottom = !tableWrap || (tableWrap.scrollHeight - tableWrap.scrollTop - tableWrap.clientHeight <= 80);
    if (!atBottom) {
      s5NewBugCount.value += 1;
      showS5BottomBanner();
    } else {
      s5NewBugCount.value = 0;
      deferReloadOverallTest(600);
    }
  });

  window.OmniQASSE.subscribe('overall_bug_closed', ({ payload }) => {
    const bugId = Number(payload?.id || payload?.bug_id || 0);
    if (!bugId) return;
    const el = document.querySelector(`.overall-test-row-card[data-bug-id='${bugId}']`);
    if (el) window.OmniQASSE.pulseBoundaryGlow(el, 'teal');
  });

  overallTestSseBound = true;
}
bindOverallTestSSE();

