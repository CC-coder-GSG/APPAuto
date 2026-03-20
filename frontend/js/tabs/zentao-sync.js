import { api } from '../api.js';
import { state } from '../state.js';
import { openModal, closeModal } from '../components/modal.js';

const PAGE_SIZE = 20;

const syncState = {
  page: 1,
  total: 0,
  items: [],
  currentEvent: null,
};

function esc(v) {
  return String(v ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmt(v) {
  if (!v) return '-';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}

function ensureAdmin() {
  if (!state.currentUser || state.currentUser.role !== 'admin') {
    throw new Error('仅管理员可访问禅道同步中心');
  }
}

function currentQuery() {
  return {
    entityType: document.getElementById('zentaoSyncEntityType')?.value || '',
    status: document.getElementById('zentaoSyncStatus')?.value || '',
    keyword: (document.getElementById('zentaoSyncKeyword')?.value || '').trim(),
    dateFrom: document.getElementById('zentaoSyncDateFrom')?.value || '',
    dateTo: document.getElementById('zentaoSyncDateTo')?.value || '',
    onlyUnapplied: !!document.getElementById('zentaoSyncOnlyUnapplied')?.checked,
  };
}

function renderMapSelectors(detail) {
  const reqSelect = document.getElementById('zentaoMapRequirementSelect');
  const minorSelect = document.getElementById('zentaoMapMinorSelect');
  const sourceType = document.getElementById('zentaoMapSourceType');
  const sourceRef = document.getElementById('zentaoMapSourceRef');

  if (!reqSelect || !minorSelect || !sourceType) return;

  const reqs = (window.dataOverviewCache?.requirements || []).slice(0, 1500);
  reqSelect.innerHTML = "<option value=''>请选择需求</option>" + reqs
    .map((r) => `<option value='${r.id}'>${esc(r.zentao_req_id || '')} ${esc(r.title || '')}</option>`)
    .join('');

  const minors = (window.versions || []).filter((v) => v.version_type === 'minor');
  minorSelect.innerHTML = "<option value=''>请选择小版本</option>" + minors
    .map((v) => `<option value='${v.id}'>${esc(v.version_no || '')}</option>`)
    .join('');

  reqSelect.value = detail?.mapped_requirement_id ? String(detail.mapped_requirement_id) : '';
  minorSelect.value = detail?.mapped_minor_version_id ? String(detail.mapped_minor_version_id) : '';
  sourceType.value = detail?.mapped_source_type || detail?.recommended_source_type || 'manual';
  if (sourceRef) {
    sourceRef.value = detail?.mapped_source_ref || '';
  }
}

function statusBadge(status) {
  const s = String(status || '').toLowerCase();
  let color = '#64748b';
  let bg = '#f1f5f9';
  if (s === 'applied') {
    color = '#166534';
    bg = '#dcfce7';
  } else if (s === 'failed') {
    color = '#991b1b';
    bg = '#fee2e2';
  } else if (s === 'pending_mapping') {
    color = '#9a3412';
    bg = '#ffedd5';
  } else if (s === 'ready_to_apply') {
    color = '#1d4ed8';
    bg = '#dbeafe';
  } else if (s === 'duplicate') {
    color = '#7c3aed';
    bg = '#ede9fe';
  }
  return `<span class='badge' style='background:${bg}; color:${color};'>${esc(status || '-')}</span>`;
}

function renderList() {
  const tb = document.getElementById('zentaoSyncTableBody');
  const pageText = document.getElementById('zentaoSyncPageText');
  if (!tb || !pageText) return;

  if (!syncState.items.length) {
    tb.innerHTML = "<tr><td colspan='10' style='text-align:center; padding:16px; color:#94a3b8;'>暂无同步事件</td></tr>";
  } else {
    tb.innerHTML = syncState.items
      .map((it) => {
        const no = it.entity_type === 'bug' ? it.zentao_bug_id || '-' : it.zentao_case_id || '-';
        const mapped = it.mapped_requirement_id
          ? `需求:${it.mapped_requirement_id}${it.mapped_minor_version_id ? ` / 小版本:${it.mapped_minor_version_id}` : ''}`
          : '-';
        const applied = it.applied_case_id
          ? `用例:${it.applied_case_id}`
          : it.applied_bug_tracking_id
            ? `Bug:${it.applied_bug_tracking_id}`
            : '-';
        return `<tr>
          <td>${fmt(it.created_at)}</td>
          <td>${it.entity_type === 'bug' ? 'Bug' : '用例'}</td>
          <td>${esc(no)}</td>
          <td title='${esc(it.title || '')}'>${esc(it.title || '-')}</td>
          <td>${esc(it.creator_name || '-')}</td>
          <td title='${esc(it.requirement_name || '')}'>${esc(it.requirement_name || '-')}</td>
          <td>${statusBadge(it.status)}</td>
          <td>${mapped}</td>
          <td>${applied}</td>
          <td><button class='secondary' onclick='openZentaoSyncEventDetail(${it.id})'>查看</button></td>
        </tr>`;
      })
      .join('');
  }

  const maxPage = Math.max(1, Math.ceil((syncState.total || 0) / PAGE_SIZE));
  pageText.innerText = `第 ${syncState.page} / ${maxPage} 页，共 ${syncState.total} 条`;
}

export async function loadZentaoSyncBoard(page = 1) {
  ensureAdmin();
  syncState.page = page;

  const q = currentQuery();
  const params = new URLSearchParams({
    page: String(syncState.page),
    page_size: String(PAGE_SIZE),
  });

  if (q.entityType) params.set('entity_type', q.entityType);
  if (q.status) params.set('status', q.status);
  if (q.keyword) params.set('keyword', q.keyword);
  if (q.dateFrom) params.set('date_from', q.dateFrom);
  if (q.dateTo) params.set('date_to', q.dateTo);
  if (q.onlyUnapplied) params.set('only_unapplied', 'true');

  const data = await (await api(`/api/integrations/zentao/browser-events?${params.toString()}`)).json();
  syncState.items = data.items || [];
  syncState.total = Number(data.total || 0);
  renderList();
}

export async function nextZentaoSyncPage() {
  const maxPage = Math.max(1, Math.ceil((syncState.total || 0) / PAGE_SIZE));
  if (syncState.page >= maxPage) return;
  await loadZentaoSyncBoard(syncState.page + 1);
}

export async function prevZentaoSyncPage() {
  if (syncState.page <= 1) return;
  await loadZentaoSyncBoard(syncState.page - 1);
}

export async function openZentaoSyncEventDetail(eventId) {
  const detail = await (await api(`/api/integrations/zentao/browser-events/${eventId}`)).json();
  syncState.currentEvent = detail;

  const title = document.getElementById('zentaoSyncDetailTitle');
  const meta = document.getElementById('zentaoSyncDetailMeta');
  const draftBox = document.getElementById('zentaoSyncDetailDraft');
  const resultBox = document.getElementById('zentaoSyncDetailResult');
  const rawBox = document.getElementById('zentaoSyncDetailRaw');
  const reason = document.getElementById('zentaoSyncFailureReason');

  if (title) title.innerText = `同步事件 #${detail.id}`;
  if (meta) meta.innerText = `状态：${detail.status} | 类型：${detail.entity_type} | 创建时间：${fmt(detail.created_at)}`;
  if (draftBox) draftBox.innerText = JSON.stringify(detail.draft || {}, null, 2);
  if (resultBox) resultBox.innerText = JSON.stringify(detail.result || {}, null, 2);
  if (rawBox) rawBox.innerText = JSON.stringify(detail.raw_payload || {}, null, 2);
  if (reason) reason.innerText = detail.failure_reason || '-';

  renderMapSelectors(detail);
  openModal('zentaoSyncDetailModal');
}

export async function saveZentaoSyncMapping() {
  if (!syncState.currentEvent) return;

  const requirementId = Number(document.getElementById('zentaoMapRequirementSelect')?.value || 0);
  const minorVersionId = Number(document.getElementById('zentaoMapMinorSelect')?.value || 0);
  const sourceType = document.getElementById('zentaoMapSourceType')?.value || '';
  const sourceRef = (document.getElementById('zentaoMapSourceRef')?.value || '').trim();
  const note = (document.getElementById('zentaoMapNote')?.value || '').trim();

  await api(`/api/integrations/zentao/browser-events/${syncState.currentEvent.id}/map`, {
    method: 'POST',
    headers: window.H,
    body: {
      requirement_id: requirementId || null,
      minor_version_id: minorVersionId || null,
      source_type: sourceType || null,
      source_ref: sourceRef || null,
      note: note || null,
    },
  });

  if (window.showMessage) window.showMessage('映射保存成功', 'success');
  await openZentaoSyncEventDetail(syncState.currentEvent.id);
  await loadZentaoSyncBoard(syncState.page);
}

export async function applyZentaoSyncEvent() {
  if (!syncState.currentEvent) return;

  const data = await (await api(`/api/integrations/zentao/browser-events/${syncState.currentEvent.id}/apply`, {
    method: 'POST',
    headers: window.H,
  })).json();

  if (window.showMessage) window.showMessage(data.message || '应用完成', 'success');
  await openZentaoSyncEventDetail(syncState.currentEvent.id);
  await loadZentaoSyncBoard(syncState.page);
}

export async function applyZentaoSyncBatch() {
  const data = await (await api('/api/integrations/zentao/browser-events/apply-batch', {
    method: 'POST',
    headers: window.H,
    body: { limit: 100 },
  })).json();

  const ok = Number(data.failed || 0) === 0;
  if (window.showMessage) {
    window.showMessage(`批量应用完成：成功 ${data.success || 0}，失败 ${data.failed || 0}`, ok ? 'success' : 'error');
  }
  await loadZentaoSyncBoard(syncState.page);
}

export function closeZentaoSyncDetail() {
  closeModal('zentaoSyncDetailModal');
}

window.OmniQAZentaoSyncTab = {
  loadZentaoSyncBoard,
  nextZentaoSyncPage,
  prevZentaoSyncPage,
  openZentaoSyncEventDetail,
  saveZentaoSyncMapping,
  applyZentaoSyncEvent,
  applyZentaoSyncBatch,
  closeZentaoSyncDetail,
};
