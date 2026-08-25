import { api } from '../api.js';
import { state } from '../state.js';
import { withPrefix, sourceTypeZh, escapeHtml } from '../utils.js';

let stage5SseBound = false;
let stage5UnreadClearTimer = null;
let stage5LastDatasetKey = '';

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
  const total = state.stage5Rows.length || 0;
  return Math.max(1, Math.ceil(total / clampS5PageSize(state.stage5PageSize)));
}

function clampS5CurrentPage() {
  state.stage5PageSize = clampS5PageSize(state.stage5PageSize || getStoredS5PageSize());
  state.stage5Page = Math.max(1, Math.min(Number(state.stage5Page || 1), getS5PageCount()));
}

function getCurrentS5PageRows() {
  clampS5CurrentPage();
  const start = (state.stage5Page - 1) * state.stage5PageSize;
  return state.stage5Rows.slice(start, start + state.stage5PageSize);
}

function scrollS5ToolbarIntoView() {
  // 淇濇寔褰撳墠婊氬姩浣嶇疆绋冲畾锛涘垎椤靛垏鎹笉鍐嶅己鍒舵粴鍔ㄩ〉闈€?
}

function updateS5PagerBar() {
  const total = state.stage5Rows.length || 0;
  const bar = document.getElementById('s5PagerBar');
  const summary = document.getElementById('s5PagerSummary');
  const text = document.getElementById('s5PageText');
  const sizeInput = document.getElementById('s5PageSizeInput');
  const prevBtn = document.getElementById('s5PrevPageBtn');
  const nextBtn = document.getElementById('s5NextPageBtn');

  clampS5CurrentPage();
  const pageCount = getS5PageCount();
  const start = total === 0 ? 0 : ((state.stage5Page - 1) * state.stage5PageSize + 1);
  const end = total === 0 ? 0 : Math.min(total, state.stage5Page * state.stage5PageSize);

  if (bar) bar.classList.toggle('hidden', false);
  if (summary) summary.innerText = `鍏?${total} 鏉?Bug锛屽綋鍓嶆樉绀?${start}-${end}`;
  if (text) text.innerText = `第 ${state.stage5Page} / ${pageCount} 页`;
  if (sizeInput && String(sizeInput.value || '') !== String(state.stage5PageSize)) {
    sizeInput.value = String(state.stage5PageSize);
  }
  if (prevBtn) prevBtn.disabled = state.stage5Page <= 1;
  if (nextBtn) nextBtn.disabled = state.stage5Page >= pageCount;
}

async function maybeSyncStage5BeforeLoad({ majorId, silentSync = true }) {
  if (!majorId) return null;
  try {
    return await syncStage5FromZentao({ silent: silentSync, reloadAfter: false, force: false });
  } catch (err) {
    if (!silentSync) throw err;
    return null;
  }
}

export async function loadStage5(options = {}) {
  const {
    syncBeforeLoad = true,
    silentSync = true,
    showSuccess = true,
  } = options;
  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  const softwareId = Number(window.currentSoftwareId || 0);
  if (majorId === 0 && !softwareId) {
    window.showMessage && window.showMessage('璇峰厛閫夋嫨浜у搧', 'error');
    return;
  }

  state.stage5PageSize = clampS5PageSize(state.stage5PageSize || getStoredS5PageSize());
  setStoredS5PageSize(state.stage5PageSize);

  const datasetKey = majorId === 0 ? `all:${softwareId}` : `major:${majorId}`;
  if (datasetKey !== stage5LastDatasetKey) {
    state.stage5Page = 1;
    stage5LastDatasetKey = datasetKey;
  }

  // Load from local DB immediately; trigger background sync after render (Task 10/11)
  const url = majorId === 0
    ? `/stage5/overview?major_version_id=0&software_id=${softwareId}`
    : `/stage5/overview?major_version_id=${majorId}`;
  const data = await (await api(url)).json();
  if (majorId !== 0) await loadS5OptionsData(majorId);
  refreshS5EntryMode();
  toggleS5BugInputs();
  state.stage5Rows = data.bug_pool || [];
  state.stage5AllVersionsMode = !!data.all_versions_mode;
  clampS5CurrentPage();

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
  if (showSuccess) {
    window.showMessage && window.showMessage('鍏ㄦ櫙澶х洏鍔犺浇鎴愬姛', 'success');
  }

  // Background sync: after rendering local data, trigger incremental sync silently
  if (majorId !== 0 && syncBeforeLoad) {
    maybeSyncStage5BeforeLoad({ majorId, silentSync: true }).then((result) => {
      // If sync brought actual changes (not cached), reload silently
      if (result && !result.cached && (result.created || result.updated)) {
        loadStage5({ syncBeforeLoad: false, silentSync: true, showSuccess: false });
      }
    }).catch(() => {});
  }
}

function deferClearOverallUnread(ms = 1200) {
  if (stage5UnreadClearTimer) clearTimeout(stage5UnreadClearTimer);
  stage5UnreadClearTimer = setTimeout(() => {
    stage5UnreadClearTimer = null;
    const tab = document.getElementById('tab-stage5');
    if (!tab || tab.classList.contains('hidden')) return;
    if (!window.OmniQASSE || typeof window.OmniQASSE.clearScopeUnread !== 'function') return;
    window.OmniQASSE.clearScopeUnread('overall_bug');
  }, ms);
}

// 鈹€鈹€鈹€ Row builder 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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

function buildS5RowHtml(b, allVersionsMode) {
  const isMyClosed = b.my_test_done;
  const rowStyle = isMyClosed ? 'background: #f8fafc; color: #94a3b8;' : '';
  const isZentaoBug = !!b.zentao_bug_id;
  const isEffectivelyClosed = isS5BugEffectivelyClosed(b);

  const failBadge = b.is_retest_failed ? '<span class="badge" style="background:#fee2e2; color:#b91c1c; border:1px solid #f87171; margin-left:4px;">馃毃澶嶆祴鎵撳洖</span>' : '';
  const dispatchBadge = b.dispatched_to_name ? `<span class="badge" style="background:#ffedd5; color:#ea580c; border:1px solid #fdba74; margin-left:4px;">馃獋鐗规淳:${b.dispatched_to_name}</span>` : '';

  const bugIdText = escapeHtml(b.bug_id || '-');
  const ztBugId = (b.zentao_bug_id) ? String(b.zentao_bug_id) : (b.bug_id || '').replace(/\D/g, '');
  const bugHref = b.zentao_bug_url
    ? `<a class="qa-ext-link" href="${b.zentao_bug_url}" target="_blank" rel="noopener noreferrer">${bugIdText}</a>`
    : ztBugId
      ? `<span class="qa-bug-id-nohref" data-zt-bug-id="${ztBugId}">${bugIdText}</span>`
      : `<span>${bugIdText}</span>`;
  const bugTitle = escapeHtml(b.zentao_bug_title || b.bug_title || '');
  const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" data-zt-no-title="1" style="margin-left:4px;"></span>` : '';
  const syncMeta = b.last_zentao_synced_at ? `<span class="badge" style="background:#f8fafc; color:#64748b;">鍚屾 ${escapeHtml(b.last_zentao_synced_at)}</span>` : '';
  const assignedMeta = b.zentao_assigned_to_name ? `<span class="badge" style="background:#faf5ff; color:#7c3aed;">褰撳墠鎸囨淳 ${escapeHtml(getS5AssignedDisplayName(b.zentao_assigned_to_name))}</span>` : '';
  const closedByMeta = b.zentao_closed_by_name ? `<span class="badge" style="background:#ecfdf5; color:#047857;">绂呴亾鍏抽棴 ${escapeHtml(b.zentao_closed_by_name)}</span>` : '';
  const closeDateMeta = b.zentao_close_date ? `<span class="badge" style="background:#f1f5f9; color:#475569;">${escapeHtml(b.zentao_close_date)}</span>` : '';
  const closeCommentPreview = (b.zentao_close_comment || '').trim();
  const detailMeta = [
    (b.other_records && b.other_records.length > 0)
      ? `<span class="badge" style="background:#eff6ff; color:#1d4ed8;">浠栦汉闂幆 ${b.other_records.length}</span>`
      : '',
    closeCommentPreview
      ? `<span class="badge" style="background:#f8fafc; color:#475569;">鏈夊叧闂娉?/span>`
      : '',
  ].filter(Boolean).join('');

  const versionCell = allVersionsMode
    ? `<td style="vertical-align:middle; padding: 6px 10px; white-space:nowrap; font-size:12px; color:#475569;"><span class="badge" style="background:#eff6ff; color:#2563eb;">${escapeHtml(b.major_version_no || '')}</span></td>`
    : '';

  // 鈹€鈹€ 鎿嶄綔鍒楋細鍥哄畾涓よ甯冨眬锛屼綅缃缁堢ǔ瀹?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
  // 浣跨敤 visibility:hidden 浠ｆ浛 display:none锛岀‘淇濇寜閽Ы浣嶄笉鍥犵姸鎬佸彉鍖栬€岀Щ浣?
  const linkStyle = 'font-size:12px; text-decoration:none; white-space:nowrap;';
  const editLink = `<a href="javascript:void(0)" onclick="editS5BugById(${b.id})" style="${linkStyle} color:#3b82f6;">缂栬緫</a>`;
  const deleteLink = `<a href="javascript:void(0)" onclick="removeS5BugById(${b.id})" style="${linkStyle} color:#ef4444;">鍒犻櫎</a>`;
  const sep = `<span style="color:#e2e8f0; margin:0 2px;">|</span>`;

  let actionsHtml;
  if (isZentaoBug) {
    const assignLink = `<a href="javascript:void(0)" onclick="openS5AssignById(${b.id})" style="${linkStyle} color:#8b5cf6;">鎸囨淳</a>`;
    // 閲嶆柊婵€娲诲崰浣嶅缁堝瓨鍦紝visibility 鎺у埗鍙鎬э紝淇濇寔甯冨眬绋冲畾
    const reactivateVis = isEffectivelyClosed ? 'visible' : 'hidden';
    const reactivateLink = `<a href="javascript:void(0)" onclick="openS5ReactivateById(${b.id})" style="${linkStyle} color:#059669; font-weight:bold; visibility:${reactivateVis};">閲嶆柊婵€娲?/a>`;
    actionsHtml = `
      <div style="display:flex; flex-direction:column; gap:3px;">
        <div style="display:flex; align-items:center; gap:2px;">${editLink}${sep}${deleteLink}</div>
        <div style="display:flex; align-items:center; gap:2px;">${assignLink}${sep}${reactivateLink}</div>
      </div>`;
  } else {
    // 鏈湴 Bug锛氫袱琛屽浐瀹氾紝绗簩琛岀暀绌轰娇楂樺害涓庣閬撹涓€鑷?
    actionsHtml = `
      <div style="display:flex; flex-direction:column; gap:3px;">
        <div style="display:flex; align-items:center; gap:2px;">${editLink}${sep}${deleteLink}</div>
        <div style="height:18px;"></div>
      </div>`;
  }

  // 鈹€鈹€ 楠岃瘉鍒?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
  const closeOnChange = isZentaoBug ? `onchange="toggleS5CloseComment(${b.id}, this.checked)"` : '';
  const closeLabel = `<label style="color:#0f172a; font-weight:bold; display:flex; align-items:center; gap:4px; margin:0;"><input id='done_${b.id}' type='checkbox' ${isMyClosed ? 'checked' : ''} ${closeOnChange}> 鎴戠殑闂幆纭</label>`;

  // 鍏抽棴澶囨敞榛樿鏀惰捣锛堜慨澶?宸查棴鐜笉鍙敹璧?闂锛夛紝鐢ㄦ埛鍕鹃€夋椂灞曞紑
  const closeCommentRow = isZentaoBug
    ? `<div id='closeComment_${b.id}' style="display:none; margin-top:4px; width:100%;">
        <textarea id='closeCommentText_${b.id}' placeholder='闂幆璇存槑锛堝皢鍚屾鍐欏叆绂呴亾澶囨敞锛? style='width:100%; font-size:12px; padding:4px 6px; border:1px solid #cbd5e1; border-radius:4px; resize:vertical; min-height:40px;'>${escapeHtml(b.my_comment || '')}</textarea>
       </div>`
    : '';

  return `<tr class="stage5-row-card" data-bug-id="${b.id}" style="${rowStyle}">
    ${versionCell}
    <td style="vertical-align:middle; padding:6px 10px;">
      <div>${bugHref}${ztSlot} <span style="font-size:12px;color:#64748b">(${sourceTypeZh(b.source_type)})</span>${failBadge}${dispatchBadge}</div>
      ${detailMeta ? `<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:6px;">${detailMeta}</div>` : ''}
    </td>
    <td style="vertical-align:middle; padding:6px 10px;">
      ${bugTitle ? `<div style="max-height:60px; overflow-y:auto; font-size:13px; color:#475569; line-height:1.65; word-break:break-word;">${bugTitle}</div>` : '<span style="color:#94a3b8;">-</span>'}
      ${(syncMeta || assignedMeta || closedByMeta || closeDateMeta) ? `<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:6px;">${syncMeta}${assignedMeta}${closedByMeta}${closeDateMeta}</div>` : ''}
    </td>
    <td style="vertical-align:middle; padding:6px 8px; width:120px;">${actionsHtml}</td>
    <td style="text-decoration:none; vertical-align:middle; padding:6px 10px;">
      <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
        <select id='res_${b.id}' style="padding:2px; font-size:13px; border:1px solid #cbd5e1; border-radius:4px; color:#475569;" ${isMyClosed ? 'disabled' : ''}>
          <option value="fixed" ${b.my_resolution === 'fixed' ? 'selected' : ''}>馃殌淇閫氳繃</option>
          <option value="false_alarm" ${b.my_resolution === 'false_alarm' ? 'selected' : ''}>鈿狅笍璇姤</option>
          <option value="rejected" ${b.my_resolution === 'rejected' ? 'selected' : ''}>鉀旀嫆缁濅慨澶?/option>
        </select>
        ${closeLabel}
        ${closeCommentRow}
        <button class="${isMyClosed ? 'secondary' : ''}" onclick='saveS5(${b.id})'>淇濆瓨璁板綍</button>
      </div>
    </td>
  </tr>`;
}

function getS5ResolutionLabel(value) {
  return {
    fixed: '淇閫氳繃',
    false_alarm: '璇姤',
    rejected: '鎷掔粷淇',
  }[String(value || '')] || '-';
}

function getS5ZentaoStatusLabel(bugOrStatus) {
  const raw = typeof bugOrStatus === 'string' ? bugOrStatus : (bugOrStatus?.zentao_live_status || '');
  const status = String(raw || '').trim().toLowerCase();
  if (!status) return '-';
  const isClosedLike = typeof bugOrStatus === 'object' && bugOrStatus && isS5BugEffectivelyClosed(bugOrStatus);
  if (isClosedLike) return '宸插叧闂?;
  return {
    active: '婵€娲?,
    resolved: '宸蹭慨澶?,
    closed: '宸插叧闂?,
    delay: '寤舵湡',
  }[status] || status;
}

function getS5AssignedDisplayName(value) {
  const text = String(value || '').trim();
  if (!text) return '-';
  return text.toLowerCase() === 'closed' ? '宸插叧闂? : text;
}

function buildS5OtherRecordsHtml(records) {
  if (!(records && records.length > 0)) {
    return '<div style="font-size:12px; color:#94a3b8;">鏆傛棤鍏朵粬娴嬭瘯浜哄憳闂幆璁板綍</div>';
  }
  return `<div style="display:flex; flex-direction:column; gap:8px;">${records.map((record) => {
    const status = record.test_done
      ? `<span style="color:#16a34a; font-weight:600;">宸查棴鐜?/span>`
      : `<span style="color:#dc2626; font-weight:600;">鏈棴鐜?/span>`;
    const resolution = record.test_done
      ? `<span class="badge" style="background:#ecfdf5; color:#166534;">${getS5ResolutionLabel(record.resolution)}</span>`
      : '';
    return `<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:8px 10px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;">
      <span class="badge" style="background:#e2e8f0; color:#334155;">${escapeHtml(record.username || '-')}</span>
      ${status}
      ${resolution}
      <span style="font-size:12px; color:#64748b;">鍙戝寘锛?{escapeHtml(record.minor_version_no || '-')}</span>
    </div>`;
  }).join('')}</div>`;
}

function buildS5DetailRowHtml(b, colSpan) {
  const statusBadges = [
    b.zentao_live_status
      ? `<span class="badge" style="background:#f8fafc; color:#475569;">绂呴亾鐘舵€?${escapeHtml(getS5ZentaoStatusLabel(b))}</span>`
      : '',
    b.zentao_assigned_to_name
      ? `<span class="badge" style="background:#faf5ff; color:#7c3aed;">褰撳墠鎸囨淳 ${escapeHtml(getS5AssignedDisplayName(b.zentao_assigned_to_name))}</span>`
      : '',
    b.zentao_closed_by_name
      ? `<span class="badge" style="background:#ecfdf5; color:#047857;">鍏抽棴浜?${escapeHtml(b.zentao_closed_by_name)}</span>`
      : '',
    b.zentao_close_date
      ? `<span class="badge" style="background:#f1f5f9; color:#475569;">鍏抽棴鏃堕棿 ${escapeHtml(b.zentao_close_date)}</span>`
      : '',
    b.last_zentao_synced_at
      ? `<span class="badge" style="background:#eff6ff; color:#2563eb;">鍚屾 ${escapeHtml(b.last_zentao_synced_at)}</span>`
      : '',
  ].filter(Boolean).join('');
  const previewAction = b.zentao_bug_id
    ? `<a href="javascript:void(0)" onclick="openS5PreviewById(${b.id})" style="font-size:12px; text-decoration:none; color:#0f766e;">鎵撳紑绂呴亾棰勮</a>`
    : '<span style="font-size:12px; color:#94a3b8;">鏈湴 Bug 鏃犵閬撻瑙?/span>';
  const closeCommentBlock = (b.zentao_close_comment || '').trim()
    ? `<div style="margin-top:12px;">
        <div style="font-size:12px; font-weight:600; color:#334155; margin-bottom:6px;">绂呴亾鍏抽棴澶囨敞</div>
        <div style="font-size:12px; color:#475569; line-height:1.7; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:8px; padding:10px 12px; white-space:pre-wrap; word-break:break-word;">${escapeHtml(b.zentao_close_comment)}</div>
      </div>`
    : '';
  return `<tr class="stage5-detail-row hidden" data-bug-id="${b.id}">
    <td colspan="${colSpan}" style="padding:0 10px 10px 10px; background:#fcfcfd;">
      <div style="border:1px solid #e2e8f0; border-top:none; border-radius:0 0 10px 10px; background:#ffffff; padding:12px 14px;">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">
          <div style="font-size:13px; font-weight:700; color:#0f172a;">Bug 璇︽儏</div>
          <div>${previewAction}</div>
        </div>
        ${statusBadges ? `<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:10px;">${statusBadges}</div>` : ''}
        ${closeCommentBlock}
        <div style="margin-top:12px;">
          <div style="font-size:12px; font-weight:600; color:#334155; margin-bottom:8px;">鍏朵粬浜洪棴鐜褰?/div>
          ${buildS5OtherRecordsHtml(b.other_records)}
        </div>
      </div>
    </td>
  </tr>`;
}

function mountS5DetailRows(table, pageRows, colSpan) {
  pageRows.forEach((bug) => {
    const rowEl = table.querySelector(`.stage5-row-card[data-bug-id="${bug.id}"]`);
    if (!rowEl || rowEl.nextElementSibling?.matches(`.stage5-detail-row[data-bug-id="${bug.id}"]`)) return;
    rowEl.insertAdjacentHTML('afterend', buildS5DetailRowHtml(bug, colSpan));
  });
}

export function toggleS5DetailRow(id, forceExpand = null) {
  const detailRow = document.querySelector(`.stage5-detail-row[data-bug-id="${id}"]`);
  if (!detailRow) return;
  const shouldExpand = typeof forceExpand === 'boolean'
    ? forceExpand
    : detailRow.classList.contains('hidden');
  detailRow.classList.toggle('hidden', !shouldExpand);
  const toggleLink = document.querySelector(`[data-s5-toggle-link][data-bug-id="${id}"]`);
  if (toggleLink) toggleLink.textContent = shouldExpand ? '鏀惰捣' : '灞曞紑';
}

export function renderS5() {
  const table = document.getElementById('s5Table');
  if (!table) return;
  clampS5CurrentPage();
  const pageRows = getCurrentS5PageRows();
  const allVersionsMode = !!state.stage5AllVersionsMode;
  const thead = document.getElementById('s5TableHead');
  if (thead) {
    thead.innerHTML = allVersionsMode
      ? '<tr><th style="width:90px">鎵€灞炵増鏈?/th><th style="width:240px">Bug 缂栧彿 (鏉ユ簮)</th><th>Bug 鏍囬</th><th style="width:120px">鎿嶄綔</th><th style="width:380px">楠岃瘉鎿嶄綔</th></tr>'
      : '<tr><th style="width:240px">Bug 缂栧彿 (鏉ユ簮)</th><th>Bug 鏍囬</th><th style="width:120px">鎿嶄綔</th><th style="width:380px">楠岃瘉鎿嶄綔</th></tr>';
  }
  const colSpan = allVersionsMode ? 5 : 4;
  table.innerHTML = pageRows.length
    ? pageRows.map((b) => buildS5RowHtml(b, allVersionsMode)).join('')
    : `<tr><td colspan="${colSpan}" style="text-align:center; color:#94a3b8; padding:24px 12px;">鏆傛棤 Bug 鏁版嵁</td></tr>`;

  mountS5DetailRows(table, pageRows, colSpan);

  pageRows.forEach((bug) => {
    const rowEl = table.querySelector(`.stage5-row-card[data-bug-id="${bug.id}"]`);
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
        assignAnchor.title = '宸插叧闂殑绂呴亾 Bug 涓嶈兘鍐嶆寚娲?;
      }
    }
    if (!firstActionRow) return;
    const inserted = [];
    if (bug?.zentao_bug_id && !firstActionRow.querySelector('[data-s5-preview-link]')) {
      inserted.push(`<a data-s5-preview-link="1" href="javascript:void(0)" onclick="openS5PreviewById(${bug.id})" style="font-size:12px; text-decoration:none; white-space:nowrap; color:#0f766e;">棰勮</a>`);
    }
    if (!firstActionRow.querySelector('[data-s5-toggle-link]')) {
      inserted.push(`<a data-s5-toggle-link="1" data-bug-id="${bug.id}" href="javascript:void(0)" onclick="toggleS5DetailRow(${bug.id})" style="font-size:12px; text-decoration:none; white-space:nowrap; color:#2563eb;">灞曞紑</a>`);
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
    document.querySelectorAll('.stage5-row-card[data-bug-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'overall_bug', key: el.getAttribute('data-bug-id'), tone: 'blue', hoverDelayMs: 420 });
    });
  }
  window.OmniQAZentao?.hydrateContainer(table);
}

function setS5Page(nextPage) {
  state.stage5Page = Math.max(1, Math.min(Number(nextPage || 1), getS5PageCount()));
  renderS5();
}

export function prevS5Page() {
  setS5Page(state.stage5Page - 1);
}

export function nextS5Page() {
  setS5Page(state.stage5Page + 1);
}

export function applyS5PageSize() {
  const input = document.getElementById('s5PageSizeInput');
  const pageSize = clampS5PageSize(input?.value || state.stage5PageSize);
  state.stage5PageSize = pageSize;
  state.stage5Page = 1;
  setStoredS5PageSize(pageSize);
  if (input) input.value = String(pageSize);
  renderS5();
}

export async function syncStage5FromZentao(options = {}) {
  const { silent = false, reloadAfter = true, force = true } = options;
  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  if (!majorId) {
    if (!silent) {
      window.showMessage && window.showMessage('璇烽€夋嫨涓€涓叿浣撳ぇ鐗堟湰鍚庡啀浠庣閬撳悓姝?, 'error');
    }
    return null;
  }

  const data = await (await api('/stage5/sync-zentao-bugs', {
    method: 'POST',
    headers: window.H,
    body: { major_version_id: majorId, force },
  })).json();

  if (!silent) {
    if (data.cached) {
      window.showMessage && window.showMessage('宸插湪 5 鍒嗛挓鍐呭悓姝ヨ繃锛岃烦杩囪繙绔媺鍙栵紙浣跨敤鏈湴缂撳瓨锛?, 'info');
    } else {
      window.showMessage && window.showMessage(
        `禅道同步完成：远端 ${data.remote_total || 0} 条，新增 ${data.created || 0} 条，更新 ${data.updated || 0} 条`,
        'success',
      );
    }
  }
  if (reloadAfter) {
    await loadStage5({ syncBeforeLoad: false, showSuccess: false });
  }
  return data;
}

// Toggle the close comment textarea when the close checkbox is ticked
export function toggleS5CloseComment(bugId, checked) {
  const wrap = document.getElementById('closeComment_' + bugId);
  if (wrap) wrap.style.display = checked ? '' : 'none';
}

export async function saveS5(id) {
  const bug = state.stage5Rows.find((b) => b.id === id);
  const isZentaoBug = !!(bug?.zentao_bug_id);
  const zentaoBugId = bug?.zentao_bug_id || '';
  const done = document.getElementById('done_' + id)?.checked;
  const res = document.getElementById('res_' + id)?.value;
  const closeComment = isZentaoBug ? (document.getElementById('closeCommentText_' + id)?.value || '') : '';

  await api(`/stage5/bugs/${id}/result`, {
    method: 'PUT',
    headers: window.H,
    body: {
      minor_version_id: Number(document.getElementById('s5MinorSelect')?.value || 0),
      test_done: done,
      newly_found_bug_id: null,
      resolution: res,
    },
  });

  // If Zentao bug and closing 鈥?call Zentao close API
  if (isZentaoBug && done && zentaoBugId) {
    try {
      const ztId = Number(zentaoBugId);
      if (ztId) {
        const closeResp = await api(`/zentao/bugs/${ztId}/close`, {
          method: 'POST',
          headers: window.H,
          body: { comment: closeComment },
        });
        if (!closeResp.ok) {
          const err = await closeResp.json().catch(() => ({}));
          const detail = err.detail || '绂呴亾鍏抽棴璇锋眰澶辫触';
          window.showMessage && window.showMessage(`鏈湴璁板綍宸蹭繚瀛橈紝浣嗙閬撳叧闂け璐ワ細${detail}`, 'info');
        }
      }
    } catch (err) {
      window.showMessage && window.showMessage(`鏈湴璁板綍宸蹭繚瀛橈紝浣嗙閬撳叧闂紓甯革細${err.message}`, 'info');
    }
  }

  window.showMessage && window.showMessage('鏁翠綋娴嬭瘯椤瑰凡淇濆瓨');
  await loadStage5({ syncBeforeLoad: false, showSuccess: false });
}

export async function editS5Bug(id, oldBugId, zentaoBugId, currentTitle) {
  if (zentaoBugId) {
    // Zentao bug 鈥?edit title via Zentao API
    const title = prompt('璇疯緭鍏?Bug 鏍囬锛?, currentTitle ? String(currentTitle).replace(/&amp;/g, '&') : '');
    if (title === null) return;
    if (!title.trim()) {
      window.showMessage && window.showMessage('鏍囬涓嶈兘涓虹┖', 'error');
      return;
    }
    try {
      await api(`/zentao/bugs/${Number(zentaoBugId)}`, {
        method: 'PUT',
        headers: window.H,
        body: { title: title.trim() },
      });
      window.showMessage && window.showMessage('绂呴亾 Bug 鏍囬宸叉洿鏂?, 'success');
      await loadStage5({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '鏍囬鏇存柊澶辫触', 'error');
    }
  } else {
    // Local bug 鈥?change bug_id (original behaviour)
    const num = prompt('璇疯緭鍏ユ纭殑 Bug 鏁板瓧閮ㄥ垎锛?, String(oldBugId || '').replace('b#', ''));
    if (!num) return;
    try {
      await api('/bugs/' + id + '?new_bug_id=' + encodeURIComponent(withPrefix('b#', num)), { method: 'PUT' });
      window.showMessage && window.showMessage('Bug 缂栧彿宸茬籂姝?, 'success');
      await loadStage5({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '鏇存柊澶辫触', 'error');
    }
  }
}

export async function removeS5Bug(id, zentaoBugId = '') {
  if (zentaoBugId) {
    // Zentao bug 鈥?soft-delete via Zentao API with strong warning
    const confirmed = window.confirm(
      `鈿狅笍 鍒犻櫎绂呴亾 Bug\n\n` +
      `灏嗗绂呴亾鎵ц鍒犻櫎鎿嶄綔锛堣蒋鍒犻櫎锛宒eleted=true锛夈€俓n` +
      `褰撳墠瀹炰緥鍒犻櫎鍚庢殏鏃犵ǔ瀹氬彲鐢ㄧ殑 REST 鎭㈠鎺ュ彛锛岃皑鎱庢搷浣滐紒\n\n` +
      `鏈湴璁板綍灏嗘爣璁颁负"宸插垹闄?骞朵粠澶х洏闅愯棌锛屼笉浼氬交搴曟竻闄わ紙淇濈暀瀹¤璁板綍锛夈€俓n\n` +
      `纭鍒犻櫎绂呴亾 Bug #${zentaoBugId} 鍚楋紵`
    );
    if (!confirmed) return;
    try {
      await api(`/zentao/bugs/${Number(zentaoBugId)}`, { method: 'DELETE' });
      window.showMessage && window.showMessage('绂呴亾 Bug 宸插垹闄ゅ苟浠庡ぇ鐩樼Щ闄?, 'success');
      await loadStage5({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '鍒犻櫎澶辫触', 'error');
    }
  } else {
    // Local bug
    if (!confirm('纭畾瑕佸湪璇ュぇ鐗堟湰涓嬬Щ闄よ繖涓?Bug 鍚楋紵')) return;
    try {
      await api('/bugs/' + id, { method: 'DELETE' });
      window.showMessage && window.showMessage('Bug 宸插交搴曠Щ闄?, 'success');
      await loadStage5({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '鍒犻櫎澶辫触', 'error');
    }
  }
}

// 鈹€鈹€鈹€ State-driven action wrappers (no inline HTML attribute injection) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€

function editS5BugById(id) {
  const bug = state.stage5Rows.find((b) => b.id === id);
  if (!bug) return;
  editS5Bug(id, bug.bug_id, bug.zentao_bug_id || '', bug.zentao_bug_title || '');
}

function removeS5BugById(id) {
  const bug = state.stage5Rows.find((b) => b.id === id);
  removeS5Bug(id, bug ? (bug.zentao_bug_id || '') : '');
}

function openS5AssignById(id) {
  const bug = state.stage5Rows.find((b) => b.id === id);
  if (!bug?.zentao_bug_id) return;
  if (isS5BugEffectivelyClosed(bug)) {
    window.showMessage && window.showMessage('宸插叧闂殑绂呴亾 Bug 涓嶈兘鍐嶆寚娲撅紝璇峰厛閲嶆柊婵€娲?, 'error');
    return;
  }
  openS5AssignModal(id, bug.zentao_bug_id);
}

function openS5ReactivateById(id) {
  const bug = state.stage5Rows.find((b) => b.id === id);
  if (!bug?.zentao_bug_id) return;
  openS5ReactivateModal(id, bug.zentao_bug_id);
}

function openS5PreviewById(id) {
  const bug = state.stage5Rows.find((item) => item.id === id);
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
  let hintText = '褰撳墠澶х増鏈湭閰嶇疆绂呴亾鎵ц鐗堟湰锛屽彲涓存椂褰曞叆鏈湴 Bug銆?;

  if (!majorId) {
    visible = false;
    hintText = '鍏ㄩ儴鐗堟湰瑙嗗浘涓嬩笉鍏佽鐩存帴鏂板鏈湴 Bug锛岃鍏堝垏鎹㈠埌鍏蜂綋澶х増鏈€?;
  } else if (major?.zentao_execution_id) {
    visible = false;
    hintText = '褰撳墠澶х増鏈凡閰嶇疆绂呴亾鎵ц鐗堟湰锛岃浼樺厛浣跨敤涓婃柟鈥滄柊寤虹閬揃ug鈥濄€?;
  }

  panel.classList.toggle('hidden', !visible);
  title.innerText = visible ? '鉃?鏂板闂' : '鉃?鏈湴鏂板宸查殣钘?;
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
  return template.innerHTML || '<span style="color:#94a3b8;">鏆傛棤鍐呭</span>';
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
    return '<span style="color:#94a3b8; font-size:13px;">鏆傛棤闄勪欢</span>';
  }

  const imageFiles = files.filter((item) => _isPreviewImageFile(item) && item?.url);
  const otherFiles = files.filter((item) => !_isPreviewImageFile(item) || !item?.url);

  const imageBlock = imageFiles.length ? `
    <div style="margin-bottom:${otherFiles.length ? '14px' : '0'};">
      <div style="font-size:13px; font-weight:600; color:#334155; margin-bottom:8px;">鍥剧墖闄勪欢</div>
      <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px;">
        ${imageFiles.map((item, index) => `
          <a href="javascript:void(0)" onclick="openZentaoBugImageLightboxByIndex(${index})" style="display:block; text-decoration:none; border:1px solid #dbeafe; border-radius:12px; overflow:hidden; background:#eff6ff;">
            <div style="aspect-ratio:4/3; background:#dbeafe; display:flex; align-items:center; justify-content:center;">
              <img src="${escapeHtml(item.url || '')}" alt="${escapeHtml(item.title || '鍥剧墖闄勪欢')}" style="width:100%; height:100%; object-fit:cover; display:block;">
            </div>
            <div style="padding:10px 12px; font-size:12px; color:#1e3a8a; line-height:1.6; word-break:break-word;">${escapeHtml(item.title || '鍥剧墖闄勪欢')}</div>
          </a>
        `).join('')}
      </div>
    </div>
  ` : '';

  const fileBlock = otherFiles.length ? `
    <div>
      <div style="font-size:13px; font-weight:600; color:#334155; margin-bottom:8px;">鏂囦欢闄勪欢</div>
      <div style="display:flex; flex-wrap:wrap; gap:8px;">
        ${otherFiles.map((item) => item?.url ? `
          <a href="${escapeHtml(item.url || '#')}" target="_blank" rel="noopener noreferrer" class="badge" style="background:#eff6ff; color:#2563eb; text-decoration:none;">
            ${escapeHtml(item.title || '闄勪欢')}
          </a>
        ` : `
          <span class="badge" style="background:#f8fafc; color:#64748b;">
            ${escapeHtml(item?.title || '闄勪欢')}
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
      title: String(item.title || '鍥剧墖闄勪欢'),
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
  img.alt = current.title || '鍥剧墖棰勮';
  titleEl.innerText = current.title || '鍥剧墖棰勮';
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

export function openZentaoBugImageLightbox(url, title = '鍥剧墖棰勮') {
  _zentaoPreviewImageGallery = [{ url: String(url || ''), title: String(title || '鍥剧墖棰勮') }];
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
  img.alt = '鍥剧墖棰勮';
  titleEl.innerText = '鍥剧墖棰勮';
  if (indexEl) indexEl.innerText = '';
  _zentaoPreviewImageGallery = [];
  _zentaoPreviewImageIndex = -1;
}

function _renderPreviewMetaGrid(preview) {
  const fields = [
    ['鐘舵€?, preview.status_zh || preview.status || '-'],
    ['瑙ｅ喅鏂规', preview.resolution_zh || preview.resolution || '-'],
    ['Bug 绫诲瀷', preview.type_zh || preview.type || '-'],
    ['涓ラ噸绋嬪害', preview.severity ? `S${preview.severity}` : '-'],
    ['浼樺厛绾?, preview.pri ? `P${preview.pri}` : '-'],
    ['鎸囨淳缁?, preview.assigned_to || '-'],
    ['鍒涘缓浜?, preview.opened_by || '-'],
    ['鍒涘缓鏃堕棿', preview.opened_date || '-'],
    ['妯″潡', preview.module || '-'],
    ['褰卞搷鐗堟湰', preview.opened_build || '-'],
    ['鎵ц鐗堟湰', preview.execution || '-'],
    ['鍏宠仈鏁呬簨', preview.story_title || '-'],
    ['椤圭洰', preview.project || '-'],
    ['鍏抽棴浜?, preview.closed_by || '-'],
    ['鍏抽棴鏃堕棿', preview.closed_date || '-'],
    ['鎴鏃ユ湡', preview.deadline || '-'],
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
    return '<div style="color:#94a3b8; font-size:13px;">鏆傛棤娴佽浆璁板綍</div>';
  }
  return actions.map((item) => `
    <div style="padding:10px 12px; border:1px solid #e2e8f0; border-radius:10px; background:#fff;">
      <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:6px;">
        <span style="font-size:12px; color:#475569;">${escapeHtml(item.date || '-')}</span>
        <span class="badge" style="background:#eff6ff; color:#2563eb;">${escapeHtml(item.actor || '绯荤粺')}</span>
        <span class="badge" style="background:#f8fafc; color:#475569;">${escapeHtml(item.action_zh || item.action || '鎿嶄綔')}</span>
      </div>
      <div style="font-size:13px; color:#334155; line-height:1.7; word-break:break-word;">${_sanitizePreviewHtml(item.comment || '鏃犲娉?)}</div>
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
      // 淇濇寔鐮村浘鐘舵€侊紝涓嶅共鎵板叾浠栧唴瀹?
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
  metaEl.innerText = `绂呴亾 Bug #${ztId}`;
  bodyEl.innerHTML = '<div style="color:#64748b; font-size:13px;">姝ｅ湪鍔犺浇绂呴亾 Bug 璇︽儏...</div>';
  errorEl.style.display = 'none';
  linkEl.classList.add('hidden');
  linkEl.removeAttribute('href');

  try {
    const preview = await (await api(`/zentao/bugs/${Number(ztId)}/preview`)).json();
    _setZentaoPreviewImageGallery(preview.files);
    titleEl.innerText = preview.title || bugRow?.zentao_bug_title || `Bug #${ztId}`;
    metaEl.innerText = `绂呴亾 Bug #${ztId} 路 ${preview.status_zh || preview.status || '鏈煡鐘舵€?}`;
    if (preview.zentao_url) {
      linkEl.href = preview.zentao_url;
      linkEl.classList.remove('hidden');
    }
    bodyEl.innerHTML = `
      <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:10px; margin-bottom:16px;">
        ${_renderPreviewMetaGrid(preview)}
      </div>
      <div style="margin-bottom:16px;">
        <div style="font-size:14px; font-weight:700; color:#0f172a; margin-bottom:8px;">澶嶇幇姝ラ</div>
        <div style="border:1px solid #e2e8f0; border-radius:12px; padding:14px; background:#f8fafc; color:#334155; line-height:1.8;">
          ${_sanitizePreviewHtml(preview.steps)}
        </div>
      </div>
      <div style="margin-bottom:16px;">
        <div style="font-size:14px; font-weight:700; color:#0f172a; margin-bottom:8px;">闄勪欢</div>
        <div>${_renderPreviewFiles(preview)}</div>
      </div>
      <div>
        <div style="font-size:14px; font-weight:700; color:#0f172a; margin-bottom:8px;">娴佽浆璁板綍</div>
        <div style="display:flex; flex-direction:column; gap:10px;">
          ${_renderPreviewActions(preview)}
        </div>
      </div>
    `;
    await _loadProxyImages(bodyEl);
  } catch (err) {
    _setZentaoPreviewImageGallery([]);
    errorEl.innerText = err.message || '鍔犺浇绂呴亾 Bug 璇︽儏澶辫触';
    errorEl.style.display = 'block';
    bodyEl.innerHTML = '<div style="color:#94a3b8; font-size:13px;">鏃犳硶鑾峰彇绂呴亾璇︽儏锛岃妫€鏌ョ粦瀹氭垨绋嶅悗閲嶈瘯銆?/div>';
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

export async function pushStage5() {
  if (!(window.confirmPush && window.confirmPush())) return;
  const res = await (await api(`/stage5/push-status?major_version_id=${Number(document.getElementById('s5MajorSelect')?.value || 0)}&minor_version_id=${Number(document.getElementById('s5MinorSelect')?.value || 0)}`, { method: 'POST' })).json();
  window.showMessage && window.showMessage(`鏁翠綋鐘舵€佸凡鎺ㄩ€侊紝鍓╀綑鏈棴鐜?${res.remaining}`);
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
      reqSelect.innerHTML = '<option value="">璇烽€夋嫨鍏宠仈闇€姹?/option>' + state.globalS5Options.reqs.map((r) => `<option value="${r.id}">${r.label}</option>`).join('');
    }
    if (caseSelect) {
      caseSelect.innerHTML = '<option value="">璇烽€夋嫨鍏宠仈鐢ㄤ緥</option>' + state.globalS5Options.cases.map((c) => `<option value="${c.id}">${c.label}</option>`).join('');
    }
    if (legacySelect) {
      legacySelect.innerHTML = '<option value="">璇烽€夋嫨鍏宠仈鍘嗗彶Bug</option>' + state.globalS5Options.bugs.map((b) => `<option value="${b.id}">${b.label}</option>`).join('');
    }
  } catch (e) {
    console.error('鎼滅储鏁版嵁鍔犺浇澶辫触', e);
  }
}

export async function submitS5Bug() {
  const type = document.getElementById('s5BugSource').value;
  const bugInput = document.getElementById('s5BugId').value;
  if (!bugInput) {
    window.showMessage && window.showMessage('璇疯緭鍏ユ柊Bug缂栧彿', 'error');
    return;
  }
  const bugId = withPrefix('b#', bugInput);
  let reqId = null;
  let sourceRef = null;
  if (type === 'requirement') {
    const selectedReqId = Number(document.getElementById('s5ReqSelect').value || 0);
    const target = state.globalS5Options.reqs.find((r) => r.id === selectedReqId);
    if (!target) {
      window.showMessage && window.showMessage('璇烽€夋嫨鍏宠仈闇€姹傦紒', 'error');
      return;
    }
    reqId = target.id;
  } else if (type === 'case') {
    const selectedCaseId = Number(document.getElementById('s5CaseSelect').value || 0);
    const target = state.globalS5Options.cases.find((c) => c.id === selectedCaseId);
    if (!target) {
      window.showMessage && window.showMessage('璇烽€夋嫨鍏宠仈鐢ㄤ緥锛?, 'error');
      return;
    }
    reqId = target.req_id;
    sourceRef = target.label;
  } else if (type === 'legacy_bug') {
    const selectedLegacyBugId = Number(document.getElementById('s5LegacySelect').value || 0);
    const target = state.globalS5Options.bugs.find((b) => b.id === selectedLegacyBugId);
    if (!target) {
      window.showMessage && window.showMessage('璇烽€夋嫨鍏宠仈鍘嗗彶Bug锛?, 'error');
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
    window.showMessage && window.showMessage('鏂伴棶棰樺凡鎴愬姛娣诲姞鍒板ぇ鐩橈紒', 'success');
    document.getElementById('s5BugId').value = '';
    await loadStage5({ syncBeforeLoad: false, showSuccess: false });
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '娣诲姞澶辫触', 'error');
  }
}

// 鈹€鈹€鈹€ Reactivate Modal 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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
  let currentLabel = '';
  const opts = entries.map(([account, display]) => {
    const label = `${display}（${account}）`;
    if (account === currentAssigned) currentLabel = label;
    return `<option value="${escapeHtml(account)}">${escapeHtml(label)}</option>`;
  }).join('');

  container.innerHTML = `
    <input type="text" id="${selectId}Search" placeholder="鎼滅储濮撳悕/璐﹀彿" style="width:100%; padding:6px 8px; border:1px solid #cbd5e1; border-radius:4px; font-size:13px; margin-bottom:4px;" oninput="_filterS5UserSelect('${selectId}', this.value)">
    <select id="${selectId}" size="5" style="width:100%; border:1px solid #cbd5e1; border-radius:4px; font-size:13px;">
      <option value="">-- 璇烽€夋嫨 --</option>
      ${opts}
    </select>
  `;
  // Pre-select current assigned
  if (currentAssigned) {
    const sel = document.getElementById(selectId);
    if (sel) sel.value = currentAssigned;
  }
}

window._filterS5UserSelect = function(selectId, keyword) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  const kw = (keyword || '').toLowerCase();
  Array.from(sel.options).forEach((opt) => {
    if (!opt.value) { opt.style.display = ''; return; }
    opt.style.display = (opt.text.toLowerCase().includes(kw) || opt.value.toLowerCase().includes(kw)) ? '' : 'none';
  });
};

export function openS5ReactivateModal(bugDbId, zentaoBugId) {
  _s5ModalBugDbId = bugDbId;
  _s5ModalZtId = zentaoBugId;
  const modal = document.getElementById('s5ReactivateModal');
  if (!modal) return;

  // Reset form
  const commentEl = document.getElementById('s5ReactivateComment');
  if (commentEl) commentEl.value = '';
  const userContainer = document.getElementById('s5ReactivateUserSelectContainer');
  if (userContainer) userContainer.innerHTML = '<div style="color:#94a3b8; font-size:13px;">鍔犺浇鍊欓€変汉涓?..</div>';

  modal.classList.remove('hidden');
  modal.style.display = 'flex';

  // Load action meta for user list and build list
  _loadActionMeta(zentaoBugId, 'activate').then((meta) => {
    _buildSearchableUserSelect('s5ReactivateUserSelect', meta.users, meta.current_assigned || '');
    // Populate build select
    const buildSel = document.getElementById('s5ReactivateBuildSelect');
    if (buildSel) {
      const builds = meta.builds || {};
      buildSel.innerHTML = '<option value="">-- 閫夋嫨褰卞搷鐗堟湰锛堝彲閫夛級--</option>' +
        Object.entries(builds).map(([bid, bname]) => `<option value="${escapeHtml(bid)}">${escapeHtml(String(bname))}</option>`).join('');
    }
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
    window.showMessage && window.showMessage('绂呴亾 Bug 宸查噸鏂版縺娲?, 'success');
    closeS5ReactivateModal();
    await loadStage5({ syncBeforeLoad: false, showSuccess: false });
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '婵€娲诲け璐?, 'error');
  }
}

export function closeS5ReactivateModal() {
  const modal = document.getElementById('s5ReactivateModal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
}

// 鈹€鈹€鈹€ Assign Modal 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

export function openS5AssignModal(bugDbId, zentaoBugId) {
  _s5ModalBugDbId = bugDbId;
  _s5ModalZtId = zentaoBugId;
  const modal = document.getElementById('s5AssignModal');
  if (!modal) return;

  const commentEl = document.getElementById('s5AssignComment');
  if (commentEl) commentEl.value = '';
  const userContainer = document.getElementById('s5AssignUserSelectContainer');
  if (userContainer) userContainer.innerHTML = '<div style="color:#94a3b8; font-size:13px;">鍔犺浇鍊欓€変汉涓?..</div>';

  modal.classList.remove('hidden');
  modal.style.display = 'flex';

  _loadActionMeta(zentaoBugId, 'activate').then((meta) => {
    _buildSearchableUserSelect('s5AssignUserSelect', meta.users, meta.current_assigned || '');
  });
}

export async function submitS5Assign() {
  const assignedTo = document.getElementById('s5AssignUserSelect')?.value || '';
  const comment = document.getElementById('s5AssignComment')?.value || '';
  if (!assignedTo) {
    window.showMessage && window.showMessage('璇烽€夋嫨鎸囨淳浜?, 'error');
    return;
  }
  if (!_s5ModalZtId) return;
  try {
    await api(`/zentao/bugs/${Number(_s5ModalZtId)}/assign`, {
      method: 'POST',
      headers: window.H,
      body: { assigned_to: assignedTo, comment },
    });
    window.showMessage && window.showMessage(`绂呴亾 Bug 宸叉寚娲剧粰 ${assignedTo}`, 'success');
    closeS5AssignModal();
    await loadStage5({ syncBeforeLoad: false, showSuccess: false });
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '鎸囨淳澶辫触', 'error');
  }
}

export function closeS5AssignModal() {
  const modal = document.getElementById('s5AssignModal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
}

// 鈹€鈹€鈹€ Create Zentao Bug 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

let _s5CreateBugMeta = null;

function _resetS5CreateBugForm() {
  ['s5CreateBugTitle', 's5CreateBugSteps'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  ['s5CreateBugSeverity', 's5CreateBugPri'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '3';
  });
  ['s5CreateBugExecution', 's5CreateBugModule', 's5CreateBugBuild', 's5CreateBugStory'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  const typeEl = document.getElementById('s5CreateBugType');
  if (typeEl) typeEl.value = 'codeerror';
}

function _fillS5CreateBugSelect(selectId, options, placeholder, defaultValue = '') {
  const select = document.getElementById(selectId);
  if (!select) return;
  const entries = Object.entries(options || {});
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
  const userEntries = Object.entries(users || {});
  if (userEntries.length === 0) {
    userContainer.innerHTML = '<div style="color:#94a3b8; font-size:13px;">未获取到候选人列表</div>';
    return;
  }

  let search = '';
  const renderId = 'createBugUserSearch_' + Date.now();
  userContainer.innerHTML = `
    <input id="${renderId}" placeholder="搜索候选人（姓名/账号）..." style="width:100%; padding:6px 8px; border:1px solid #cbd5e1; border-radius:4px; font-size:13px; box-sizing:border-box; margin-bottom:4px;">
    <select id="s5CreateBugAssignTo" size="4" style="width:100%; border:1px solid #cbd5e1; border-radius:4px; font-size:13px; padding:2px;">
      <option value="">-- 不指派 --</option>
      ${userEntries.map(([acc, name]) => `<option value="${acc}">${name} (${acc})</option>`).join('')}
    </select>`;

  const searchEl = document.getElementById(renderId);
  const selectEl = document.getElementById('s5CreateBugAssignTo');
  if (!searchEl || !selectEl) return;

  if (selectedValue && userEntries.some(([acc]) => acc === selectedValue)) {
    selectEl.value = selectedValue;
  }
  searchEl.addEventListener('input', () => {
    search = searchEl.value.toLowerCase();
    selectEl.innerHTML = '<option value="">-- 不指派 --</option>' + userEntries
      .filter(([acc, name]) => !search || acc.toLowerCase().includes(search) || name.toLowerCase().includes(search))
      .map(([acc, name]) => `<option value="${acc}">${name} (${acc})</option>`)
      .join('');
    if (selectedValue && userEntries.some(([acc]) => acc === selectedValue)) {
      selectEl.value = selectedValue;
    }
  });
}

function _applyS5CreateBugMeta(meta, previous = {}) {
  const hintEl = document.getElementById('s5CreateBugMetaHint');
  const productId = (meta.product_ids || [])[0];
  const selectedExecutionId = String(meta.selected_execution_id || meta.execution_id || '');
  const executionOptions = meta.executions || {};

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
    hintEl.innerText = productId
      ? `产品ID: ${productId}${selectedExecutionId ? `  执行ID: ${selectedExecutionId}` : ''}`
      : '⚠️ 未找到产品信息，请确认该大版本已配置禅道执行版本映射';
  }
}

async function _loadS5CreateBugMeta(majorId, executionId = '', previous = {}) {
  const hintEl = document.getElementById('s5CreateBugMetaHint');
  const userContainer = document.getElementById('s5CreateBugUserContainer');
  const errEl = document.getElementById('s5CreateBugErrorMsg');
  if (hintEl) hintEl.innerText = '正在加载产品/用户信息...';
  if (userContainer) userContainer.innerHTML = '<div style="color:#94a3b8; font-size:13px;">加载中...</div>';
  if (errEl) errEl.style.display = 'none';

  const qs = executionId ? `&execution_id=${encodeURIComponent(executionId)}` : '';
  const resp = await api(`/zentao/bugs/create-meta?major_version_id=${majorId}${qs}`);
  _s5CreateBugMeta = await resp.json();
  _applyS5CreateBugMeta(_s5CreateBugMeta, previous);
}

export async function openS5CreateZentaoBugModal() {
  const modal = document.getElementById('s5CreateZentaoBugModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';

  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  const hintEl = document.getElementById('s5CreateBugMetaHint');
  const userContainer = document.getElementById('s5CreateBugUserContainer');
  const errEl = document.getElementById('s5CreateBugErrorMsg');
  if (errEl) errEl.style.display = 'none';

  _resetS5CreateBugForm();
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

export async function onS5CreateBugExecutionChange() {
  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  if (!majorId) return;
  const executionId = document.getElementById('s5CreateBugExecution')?.value || '';
  const previous = _collectS5CreateBugSelections();
  try {
    await _loadS5CreateBugMeta(majorId, executionId, previous);
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
}

export async function submitS5CreateZentaoBug() {
  const title = (document.getElementById('s5CreateBugTitle')?.value || '').trim();
  const steps = (document.getElementById('s5CreateBugSteps')?.value || '').trim();
  const severity = Number(document.getElementById('s5CreateBugSeverity')?.value || 3);
  const pri = Number(document.getElementById('s5CreateBugPri')?.value || 3);
  const assignedTo = document.getElementById('s5CreateBugAssignTo')?.value || '';
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
  if (Object.keys(_s5CreateBugMeta.executions || {}).length > 0 && !executionId) {
    if (errEl) { errEl.innerText = '请选择执行'; errEl.style.display = 'block'; }
    return;
  }
  if (errEl) errEl.style.display = 'none';

  const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
  const minorId = Number(document.getElementById('s5MinorSelect')?.value || 0);
  const submitBtn = document.getElementById('s5CreateBugSubmitBtn');
  if (submitBtn) { submitBtn.disabled = true; submitBtn.innerText = '提交中...'; }

  try {
    const body = {
      product_id: _s5CreateBugMeta.product_ids[0],
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
      loadStage5({ syncBeforeLoad: false, silentSync: true, showSuccess: false });
    }
  } catch (err) {
    if (errEl) { errEl.innerText = err.message || '创建失败，请稍后重试'; errEl.style.display = 'block'; }
  } finally {
    if (submitBtn) { submitBtn.disabled = false; submitBtn.innerText = '🚀 提交到禅道'; }
  }
}

window.OmniQAStage5Tab = {
  loadStage5,
  renderS5,
  saveS5,
  editS5Bug,
  removeS5Bug,
  pushStage5,
  refreshS5EntryMode,
  toggleS5BugInputs,
  loadS5OptionsData,
  submitS5Bug,
  syncStage5FromZentao,
  prevS5Page,
  nextS5Page,
  applyS5PageSize,
  openS5ReactivateModal,
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
  onS5CreateBugExecutionChange,
  toggleS5DetailRow,
};
window.editS5Bug = editS5Bug;
window.editS5BugById = editS5BugById;
window.removeS5Bug = removeS5Bug;
window.removeS5BugById = removeS5BugById;
window.openS5AssignById = openS5AssignById;
window.openS5ReactivateById = openS5ReactivateById;
window.syncStage5FromZentao = syncStage5FromZentao;
window.prevS5Page = prevS5Page;
window.nextS5Page = nextS5Page;
window.applyS5PageSize = applyS5PageSize;
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
window.onS5CreateBugExecutionChange = onS5CreateBugExecutionChange;

// 鈹€鈹€鈹€ Stage5 SSE: 鏂?bug 搴曢儴鎻愮ず + 鍗＄墖娴佸厜 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

const s5NewBugCount = { value: 0 };
let s5ReloadTimer = null;

function deferReloadStage5(ms = 600) {
  if (s5ReloadTimer) clearTimeout(s5ReloadTimer);
  s5ReloadTimer = setTimeout(() => {
    s5ReloadTimer = null;
    const tab = document.getElementById('tab-stage5');
    if (!tab || tab.classList.contains('hidden')) return;
    const majorId = Number(document.getElementById('s5MajorSelect')?.value || 0);
    const softwareId = Number(window.currentSoftwareId || 0);
    if (majorId === 0 && !softwareId) return;
    loadStage5({ syncBeforeLoad: false, showSuccess: false }).catch(() => {});
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
      loadStage5({ syncBeforeLoad: false, showSuccess: false });
    },
    bannerId: 'stage5-new-bug',
  });
}

function bindStage5SSE() {
  if (stage5SseBound) return;
  if (!window.OmniQASSE?.subscribe) return;

  window.OmniQASSE.subscribe('overall_bug_created', ({ payload }) => {
    const bugId = Number(payload?.id || payload?.bug_id || 0);
    if (bugId) {
      const el = document.querySelector(`.stage5-row-card[data-bug-id='${bugId}']`);
      if (el) window.OmniQASSE.pulseBoundaryGlow(el, 'blue');
    }
    const tableWrap = document.getElementById('s5TableContainer');
    const atBottom = !tableWrap || (tableWrap.scrollHeight - tableWrap.scrollTop - tableWrap.clientHeight <= 80);
    if (!atBottom) {
      s5NewBugCount.value += 1;
      showS5BottomBanner();
    } else {
      s5NewBugCount.value = 0;
      deferReloadStage5(600);
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


