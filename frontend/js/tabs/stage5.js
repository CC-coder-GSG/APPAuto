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
  // 保持当前滚动位置稳定；分页切换不再强制滚动页面。
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
  if (summary) summary.innerText = `共 ${total} 条 Bug，当前显示 ${start}-${end}`;
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
    window.showMessage && window.showMessage('请先选择产品', 'error');
    return;
  }

  state.stage5PageSize = clampS5PageSize(state.stage5PageSize || getStoredS5PageSize());
  setStoredS5PageSize(state.stage5PageSize);

  const datasetKey = majorId === 0 ? `all:${softwareId}` : `major:${majorId}`;
  if (datasetKey !== stage5LastDatasetKey) {
    state.stage5Page = 1;
    stage5LastDatasetKey = datasetKey;
  }

  if (majorId !== 0 && syncBeforeLoad) {
    await maybeSyncStage5BeforeLoad({ majorId, silentSync });
  }

  const url = majorId === 0
    ? `/stage5/overview?major_version_id=0&software_id=${softwareId}`
    : `/stage5/overview?major_version_id=${majorId}`;
  const data = await (await api(url)).json();
  if (majorId !== 0) await loadS5OptionsData(majorId);
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
    window.showMessage && window.showMessage('全景大盘加载成功', 'success');
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

// ─── Row builder ────────────────────────────────────────────────────────────

function buildS5RowHtml(b, allVersionsMode) {
  const isMyClosed = b.my_test_done;
  const rowStyle = isMyClosed ? 'background: #f8fafc; color: #94a3b8; text-decoration: line-through;' : '';
  const isZentaoBug = !!b.zentao_bug_id;
  const zentaoStatus = (b.zentao_live_status || '').toLowerCase();

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

  const bugIdText = escapeHtml(b.bug_id || '-');
  const ztBugId = (b.zentao_bug_id) ? String(b.zentao_bug_id) : (b.bug_id || '').replace(/\D/g, '');
  const bugHref = b.zentao_bug_url
    ? `<a class="qa-ext-link" href="${b.zentao_bug_url}" target="_blank" rel="noopener noreferrer">${bugIdText}</a>`
    : ztBugId
      ? `<span class="qa-bug-id-nohref" data-zt-bug-id="${ztBugId}">${bugIdText}</span>`
      : `<span>${bugIdText}</span>`;
  const bugTitle = escapeHtml(b.zentao_bug_title || b.bug_title || '');
  const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" data-zt-no-title="1" style="margin-left:4px;"></span>` : '';
  const syncMeta = b.last_zentao_synced_at ? `<span class="badge" style="background:#f8fafc; color:#64748b;">同步 ${escapeHtml(b.last_zentao_synced_at)}</span>` : '';
  const assignedMeta = b.zentao_assigned_to_name ? `<span class="badge" style="background:#faf5ff; color:#7c3aed;">当前指派 ${escapeHtml(b.zentao_assigned_to_name)}</span>` : '';
  const closedByMeta = b.zentao_closed_by_name ? `<span class="badge" style="background:#ecfdf5; color:#047857;">禅道关闭 ${escapeHtml(b.zentao_closed_by_name)}</span>` : '';
  const closeDateMeta = b.zentao_close_date ? `<span class="badge" style="background:#f1f5f9; color:#475569;">${escapeHtml(b.zentao_close_date)}</span>` : '';
  const closeCommentPreview = (b.zentao_close_comment || '').trim();
  const closeCommentHtml = closeCommentPreview
    ? `<div style="margin-top:6px; font-size:12px; color:#475569; line-height:1.6; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:8px; padding:8px 10px;">禅道备注：${escapeHtml(closeCommentPreview)}</div>`
    : '';

  const versionCell = allVersionsMode
    ? `<td style="vertical-align:middle; padding: 6px 10px; white-space:nowrap; font-size:12px; color:#475569;"><span class="badge" style="background:#eff6ff; color:#2563eb;">${escapeHtml(b.major_version_no || '')}</span></td>`
    : '';

  // ── 操作列：固定两行布局，位置始终稳定 ──────────────────────────────────────
  // 使用 visibility:hidden 代替 display:none，确保按钮槽位不因状态变化而移位
  const linkStyle = 'font-size:12px; text-decoration:none; white-space:nowrap;';
  const editLink = `<a href="javascript:void(0)" onclick="editS5BugById(${b.id})" style="${linkStyle} color:#3b82f6;">编辑</a>`;
  const deleteLink = `<a href="javascript:void(0)" onclick="removeS5BugById(${b.id})" style="${linkStyle} color:#ef4444;">删除</a>`;
  const sep = `<span style="color:#e2e8f0; margin:0 2px;">|</span>`;

  let actionsHtml;
  if (isZentaoBug) {
    const assignLink = `<a href="javascript:void(0)" onclick="openS5AssignById(${b.id})" style="${linkStyle} color:#8b5cf6;">指派</a>`;
    // 重新激活占位始终存在，visibility 控制可见性，保持布局稳定
    const reactivateVis = zentaoStatus === 'closed' ? 'visible' : 'hidden';
    const reactivateLink = `<a href="javascript:void(0)" onclick="openS5ReactivateById(${b.id})" style="${linkStyle} color:#059669; font-weight:bold; visibility:${reactivateVis};">重新激活</a>`;
    actionsHtml = `
      <div style="display:flex; flex-direction:column; gap:3px;">
        <div style="display:flex; align-items:center; gap:2px;">${editLink}${sep}${deleteLink}</div>
        <div style="display:flex; align-items:center; gap:2px;">${assignLink}${sep}${reactivateLink}</div>
      </div>`;
  } else {
    // 本地 Bug：两行固定，第二行留空使高度与禅道行一致
    actionsHtml = `
      <div style="display:flex; flex-direction:column; gap:3px;">
        <div style="display:flex; align-items:center; gap:2px;">${editLink}${sep}${deleteLink}</div>
        <div style="height:18px;"></div>
      </div>`;
  }

  // ── 验证列 ──────────────────────────────────────────────────────────────────
  const closeOnChange = isZentaoBug ? `onchange="toggleS5CloseComment(${b.id}, this.checked)"` : '';
  const closeLabel = `<label style="color:#0f172a; font-weight:bold; display:flex; align-items:center; gap:4px; margin:0;"><input id='done_${b.id}' type='checkbox' ${isMyClosed ? 'checked' : ''} ${closeOnChange}> 我的闭环确认</label>`;

  // 关闭备注默认收起（修复"已闭环不可收起"问题），用户勾选时展开
  const closeCommentRow = isZentaoBug
    ? `<div id='closeComment_${b.id}' style="display:none; margin-top:4px; width:100%;">
        <textarea id='closeCommentText_${b.id}' placeholder='闭环说明（将同步写入禅道备注）' style='width:100%; font-size:12px; padding:4px 6px; border:1px solid #cbd5e1; border-radius:4px; resize:vertical; min-height:40px;'>${escapeHtml(b.my_comment || '')}</textarea>
       </div>`
    : '';

  return `<tr class="stage5-row-card" data-bug-id="${b.id}" style="${rowStyle}">
    ${versionCell}
    <td style="vertical-align:middle; padding:6px 10px;">
      <div>${bugHref}${ztSlot} <span style="font-size:12px;color:#64748b">(${sourceTypeZh(b.source_type)})</span>${failBadge}${dispatchBadge}</div>
      ${othersHtml}
    </td>
    <td style="vertical-align:middle; padding:6px 10px;">
      ${bugTitle ? `<div style="max-height:60px; overflow-y:auto; font-size:13px; color:#475569; line-height:1.65; word-break:break-word;">${bugTitle}</div>` : '<span style="color:#94a3b8;">-</span>'}
      ${(syncMeta || assignedMeta || closedByMeta || closeDateMeta) ? `<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:6px;">${syncMeta}${assignedMeta}${closedByMeta}${closeDateMeta}</div>` : ''}
      ${closeCommentHtml}
    </td>
    <td style="vertical-align:middle; padding:6px 8px; width:120px;">${actionsHtml}</td>
    <td style="text-decoration:none; vertical-align:middle; padding:6px 10px;">
      <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
        <select id='res_${b.id}' style="padding:2px; font-size:13px; border:1px solid #cbd5e1; border-radius:4px; color:#475569;" ${isMyClosed ? 'disabled' : ''}>
          <option value="fixed" ${b.my_resolution === 'fixed' ? 'selected' : ''}>🚀修复通过</option>
          <option value="false_alarm" ${b.my_resolution === 'false_alarm' ? 'selected' : ''}>⚠️误报</option>
          <option value="rejected" ${b.my_resolution === 'rejected' ? 'selected' : ''}>⛔拒绝修复</option>
        </select>
        ${closeLabel}
        ${closeCommentRow}
        <button class="${isMyClosed ? 'secondary' : ''}" onclick='saveS5(${b.id})'>保存记录</button>
      </div>
    </td>
  </tr>`;
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
      ? '<tr><th style="width:90px">所属版本</th><th style="width:240px">Bug 编号 (来源)</th><th>Bug 标题</th><th style="width:120px">操作</th><th style="width:380px">验证操作</th></tr>'
      : '<tr><th style="width:240px">Bug 编号 (来源)</th><th>Bug 标题</th><th style="width:120px">操作</th><th style="width:380px">验证操作</th></tr>';
  }
  const colSpan = allVersionsMode ? 5 : 4;
  table.innerHTML = pageRows.length
    ? pageRows.map((b) => buildS5RowHtml(b, allVersionsMode)).join('')
    : `<tr><td colspan="${colSpan}" style="text-align:center; color:#94a3b8; padding:24px 12px;">暂无 Bug 数据</td></tr>`;

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
      window.showMessage && window.showMessage('请选择一个具体大版本后再从禅道同步', 'error');
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
      window.showMessage && window.showMessage('已在 5 分钟内同步过，跳过远端拉取（使用本地缓存）', 'info');
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

  // If Zentao bug and closing — call Zentao close API
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
          const detail = err.detail || '禅道关闭请求失败';
          window.showMessage && window.showMessage(`本地记录已保存，但禅道关闭失败：${detail}`, 'info');
        }
      }
    } catch (err) {
      window.showMessage && window.showMessage(`本地记录已保存，但禅道关闭异常：${err.message}`, 'info');
    }
  }

  window.showMessage && window.showMessage('整体测试项已保存');
  await loadStage5({ syncBeforeLoad: false, showSuccess: false });
}

export async function editS5Bug(id, oldBugId, zentaoBugId, currentTitle) {
  if (zentaoBugId) {
    // Zentao bug — edit title via Zentao API
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
      await loadStage5({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '标题更新失败', 'error');
    }
  } else {
    // Local bug — change bug_id (original behaviour)
    const num = prompt('请输入正确的 Bug 数字部分：', String(oldBugId || '').replace('b#', ''));
    if (!num) return;
    try {
      await api('/bugs/' + id + '?new_bug_id=' + encodeURIComponent(withPrefix('b#', num)), { method: 'PUT' });
      window.showMessage && window.showMessage('Bug 编号已纠正', 'success');
      await loadStage5({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '更新失败', 'error');
    }
  }
}

export async function removeS5Bug(id, zentaoBugId = '') {
  if (zentaoBugId) {
    // Zentao bug — soft-delete via Zentao API with strong warning
    const confirmed = window.confirm(
      `⚠️ 删除禅道 Bug\n\n` +
      `将对禅道执行删除操作（软删除，deleted=true）。\n` +
      `当前实例删除后暂无稳定可用的 REST 恢复接口，谨慎操作！\n\n` +
      `本地记录将标记为"已删除"并从大盘隐藏，不会彻底清除（保留审计记录）。\n\n` +
      `确认删除禅道 Bug #${zentaoBugId} 吗？`
    );
    if (!confirmed) return;
    try {
      await api(`/zentao/bugs/${Number(zentaoBugId)}`, { method: 'DELETE' });
      window.showMessage && window.showMessage('禅道 Bug 已删除并从大盘移除', 'success');
      await loadStage5({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '删除失败', 'error');
    }
  } else {
    // Local bug
    if (!confirm('确定要在该大版本下移除这个 Bug 吗？')) return;
    try {
      await api('/bugs/' + id, { method: 'DELETE' });
      window.showMessage && window.showMessage('Bug 已彻底移除', 'success');
      await loadStage5({ syncBeforeLoad: false, showSuccess: false });
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '删除失败', 'error');
    }
  }
}

// ─── State-driven action wrappers (no inline HTML attribute injection) ───────

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
  openS5AssignModal(id, bug.zentao_bug_id);
}

function openS5ReactivateById(id) {
  const bug = state.stage5Rows.find((b) => b.id === id);
  if (!bug?.zentao_bug_id) return;
  openS5ReactivateModal(id, bug.zentao_bug_id);
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
    await loadStage5({ syncBeforeLoad: false, showSuccess: false });
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '添加失败', 'error');
  }
}

// ─── Reactivate Modal ────────────────────────────────────────────────────────

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
    <input type="text" id="${selectId}Search" placeholder="搜索姓名/账号" style="width:100%; padding:6px 8px; border:1px solid #cbd5e1; border-radius:4px; font-size:13px; margin-bottom:4px;" oninput="_filterS5UserSelect('${selectId}', this.value)">
    <select id="${selectId}" size="5" style="width:100%; border:1px solid #cbd5e1; border-radius:4px; font-size:13px;">
      <option value="">-- 请选择 --</option>
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
  if (userContainer) userContainer.innerHTML = '<div style="color:#94a3b8; font-size:13px;">加载候选人中...</div>';

  modal.classList.remove('hidden');
  modal.style.display = 'flex';

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
    closeS5ReactivateModal();
    await loadStage5({ syncBeforeLoad: false, showSuccess: false });
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '激活失败', 'error');
  }
}

export function closeS5ReactivateModal() {
  const modal = document.getElementById('s5ReactivateModal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
}

// ─── Assign Modal ─────────────────────────────────────────────────────────────

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
    await loadStage5({ syncBeforeLoad: false, showSuccess: false });
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '指派失败', 'error');
  }
}

export function closeS5AssignModal() {
  const modal = document.getElementById('s5AssignModal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
}

window.OmniQAStage5Tab = {
  loadStage5,
  renderS5,
  saveS5,
  editS5Bug,
  removeS5Bug,
  pushStage5,
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
window.toggleS5CloseComment = toggleS5CloseComment;
window.openS5ReactivateModal = openS5ReactivateModal;
window.submitS5Reactivate = submitS5Reactivate;
window.closeS5ReactivateModal = closeS5ReactivateModal;
window.openS5AssignModal = openS5AssignModal;
window.submitS5Assign = submitS5Assign;
window.closeS5AssignModal = closeS5AssignModal;

// ─── Stage5 SSE: 新 bug 底部提示 + 卡片流光 ──────────────────────────────────

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
    labelFn: (n) => `⬇ 下面有 ${n} 条新增 Bug，点击刷新`,
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
