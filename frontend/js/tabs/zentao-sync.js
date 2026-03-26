import { api } from '../api.js';
import { state } from '../state.js';
import { escapeHtml } from '../utils.js';

const PAGE_SIZE = 20;

const STATUS_ZH = {
  received: '已接收',
  duplicate: '重复事件',
  auto_matched: '自动匹配',
  pending_mapping: '待人工映射',
  ready_to_apply: '待应用',
  applied: '已应用',
  ignored: '已忽略',
  failed: '应用失败',
};

const ENTITY_ZH = {
  bug: '缺陷',
  testcase: '用例',
};

const SOURCE_TYPE_ZH = {
  requirement: '需求来源',
  case: '用例来源',
  manual: '人工录入',
  legacy_bug: '历史缺陷',
  retest: '复测来源',
  field_test: '外业测试',
};

const BUCKET_ZH = {
  requirement: '需求池',
  overall: '总览池',
};

const syncState = {
  page: 1,
  total: 0,
  items: [],
  currentEvent: null,
  formBound: false,
  mapRequirements: [],
  resolvedMajorVersionId: null,
  resolvedMajorVersionNo: '',
  resolvedMajorSource: '未识别',
  manualMajorVersionId: null,
  useManualMajor: false,
};

function zhStatus(v) {
  return STATUS_ZH[String(v || '').toLowerCase()] || String(v || '-');
}

function zhEntity(v) {
  return ENTITY_ZH[String(v || '').toLowerCase()] || String(v || '-');
}

function zhSourceType(v) {
  return SOURCE_TYPE_ZH[String(v || '').toLowerCase()] || String(v || '-');
}

function zhBucket(v) {
  return BUCKET_ZH[String(v || '').toLowerCase()] || String(v || '-');
}

function getCurrentSoftwareId() {
  return Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
}

function getVersions() {
  return Array.isArray(window.versions) ? window.versions : [];
}

function getRequirements() {
  return Array.isArray(window.dataOverviewCache?.requirements) ? window.dataOverviewCache.requirements : [];
}

function localizeFreeText(text) {
  const raw = String(text || '');
  if (!raw) return '-';
  return raw
    .replace(/major\/minor/gi, '主版本/小版本')
    .replace(/\bmajor\b/gi, '主版本')
    .replace(/\bminor\b/gi, '小版本')
    .replace(/linked_case_id/gi, '关联用例ID')
    .replace(/source_ref/gi, '来源引用');
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
    displayBucket: document.getElementById('zentaoSyncDisplayBucket')?.value || '',
    sourceType: document.getElementById('zentaoSyncSourceType')?.value || '',
    keyword: (document.getElementById('zentaoSyncKeyword')?.value || '').trim(),
    dateFrom: document.getElementById('zentaoSyncDateFrom')?.value || '',
    dateTo: document.getElementById('zentaoSyncDateTo')?.value || '',
    onlyUnapplied: !!document.getElementById('zentaoSyncOnlyUnapplied')?.checked,
  };
}

function statusBadge(status) {
  const s = String(status || '').toLowerCase();
  let color = '#475569';
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
    bg = '#eff6ff';
  } else if (s === 'duplicate') {
    color = '#7c3aed';
    bg = '#ede9fe';
  }
  return `<span class="badge badge--status" style="background:${bg}; color:${color};">${escapeHtml(zhStatus(status))}</span>`;
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text ?? '';
}

function setMapFormEnabled(enabled) {
  const ids = [
    'zentaoMapRequirementSelect',
    'zentaoMapMinorSelect',
    'zentaoMapSourceType',
    'zentaoMapDisplayBucket',
    'zentaoMapManualMajorSelect',
    'zentaoMapLinkedCaseId',
    'zentaoMapSourceRef',
    'zentaoMapNote',
  ];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = !enabled;
  });

  const saveBtn = document.querySelector("#tab-zentao-sync button[onclick='saveZentaoSyncMapping()']");
  const applyBtn = document.querySelector("#tab-zentao-sync button[onclick='applyZentaoSyncEvent()']");
  const deleteBtn = document.querySelector("#tab-zentao-sync button[onclick='deleteZentaoSyncEvent()']");
  const toggleManualBtn = document.querySelector("#tab-zentao-sync button[onclick='toggleManualMajorSelector()']");
  const resetManualBtn = document.querySelector("#tab-zentao-sync button[onclick='resetZentaoManualMajor()']");
  if (saveBtn) saveBtn.disabled = !enabled;
  if (applyBtn) applyBtn.disabled = !enabled;
  if (deleteBtn) deleteBtn.disabled = !enabled;
  if (toggleManualBtn) toggleManualBtn.disabled = !enabled;
  if (resetManualBtn) resetManualBtn.disabled = !enabled;
}

function versionByIdMap() {
  const m = new Map();
  getVersions().forEach((v) => m.set(Number(v.id), v));
  return m;
}

function majorVersionNoById(id) {
  if (!id) return '';
  const row = getVersions().find((v) => Number(v.id) === Number(id));
  return row?.version_no || '';
}

function getSoftwareMajorIds() {
  const currentSoftwareId = getCurrentSoftwareId();
  return new Set(
    getVersions()
      .filter((v) => v.version_type === 'major' && (!currentSoftwareId || Number(v.software_id || 0) === currentSoftwareId))
      .map((v) => Number(v.id))
  );
}

function resolveCurrentMajorContext(detail) {
  const majorId = Number(
    detail?.recommended_major_version_id ||
    detail?.mapped_major_version_id ||
    0
  ) || null;

  const majorNo = detail?.recommended_major_version_no || majorVersionNoById(majorId) || '';
  let source = detail?.recommended_major_source || '';
  if (!source) {
    if (majorId && detail?.mapped_requirement_id) source = '需求反推';
    else if (majorId) source = '自动识别';
    else source = '未识别';
  }

  syncState.resolvedMajorVersionId = majorId;
  syncState.resolvedMajorVersionNo = majorNo || '未识别';
  syncState.resolvedMajorSource = source;
}

function getEffectiveMajorVersionId() {
  if (syncState.useManualMajor && syncState.manualMajorVersionId) {
    return Number(syncState.manualMajorVersionId);
  }
  return Number(syncState.resolvedMajorVersionId || 0) || null;
}

function getEffectiveMajorSource() {
  if (syncState.useManualMajor && syncState.manualMajorVersionId) return '手动指定';
  return syncState.resolvedMajorSource || '未识别';
}

function renderMajorContext(detail) {
  const majorText = document.getElementById('zentaoMapCurrentMajorText');
  const sourceText = document.getElementById('zentaoMapCurrentMajorSource');
  const hint = document.getElementById('zentaoMapMajorHint');
  const manualWrap = document.getElementById('zentaoMapManualMajorWrap');
  const manualSelect = document.getElementById('zentaoMapManualMajorSelect');
  if (!majorText || !sourceText || !hint || !manualWrap || !manualSelect) return;

  const majors = getVersions().filter((v) => v.version_type === 'major' && (!getCurrentSoftwareId() || Number(v.software_id || 0) === getCurrentSoftwareId()));
  manualSelect.innerHTML = "<option value=''>请选择大版本</option>" + majors.map((v) => `<option value='${v.id}'>${escapeHtml(v.version_no || '')}</option>`).join('');
  if (syncState.manualMajorVersionId) manualSelect.value = String(syncState.manualMajorVersionId);

  const effMajorId = getEffectiveMajorVersionId();
  const effMajorNo = syncState.useManualMajor
    ? majorVersionNoById(effMajorId) || '未识别'
    : (syncState.resolvedMajorVersionNo || '未识别');
  majorText.textContent = effMajorNo;
  sourceText.textContent = getEffectiveMajorSource();

  if (!detail) {
    hint.textContent = '当前大版本：未识别。请先从左侧选择事件，或使用手动指定大版本。';
    manualWrap.classList.add('hidden');
    return;
  }
  if (!effMajorId) {
    hint.textContent = '当前大版本：未识别。你可以手动指定大版本后再选择需求与小版本。';
  } else if (syncState.useManualMajor) {
    hint.textContent = '当前使用手动指定大版本作为筛选上下文。';
  } else {
    hint.textContent = '当前使用自动识别大版本作为筛选上下文。';
  }
}

function resolveEventMajorHint(detail) {
  return getEffectiveMajorVersionId() || Number(detail?.recommended_major_version_id || detail?.mapped_major_version_id || 0) || null;
}

function getFilteredRequirements(detail) {
  const reqs = getRequirements();
  const majorIds = getSoftwareMajorIds();
  const majorHint = resolveEventMajorHint(detail);
  const result = reqs.filter((r) => {
    const majorId = Number(r.major_version_id || 0);
    if (!majorId) return false;
    if (majorIds.size > 0 && !majorIds.has(majorId)) return false;
    if (majorHint && majorId !== majorHint) return false;
    return true;
  });
  result.sort((a, b) => Number(b.id || 0) - Number(a.id || 0));
  return result;
}

function getRequirementById(requirements, requirementId) {
  return requirements.find((r) => Number(r.id) === Number(requirementId)) || null;
}

function getMinorOptionsForContext(detail, selectedRequirementId) {
  const allMinors = getVersions().filter((v) => v.version_type === 'minor');
  const currentSoftwareId = getCurrentSoftwareId();
  const vMap = versionByIdMap();
  const majorIds = getSoftwareMajorIds();

  let targetMajorId = null;
  if (selectedRequirementId) {
    const req = getRequirementById(syncState.mapRequirements, selectedRequirementId);
    targetMajorId = req ? Number(req.major_version_id || 0) : null;
  }
  if (!targetMajorId) {
    targetMajorId = resolveEventMajorHint(detail);
  }

  const filtered = allMinors.filter((m) => {
    const parent = Number(m.parent_id || 0);
    if (!parent) return false;
    if (targetMajorId && parent !== targetMajorId) return false;
    if (!targetMajorId) {
      if (majorIds.size > 0 && !majorIds.has(parent)) return false;
      if (currentSoftwareId) {
        const major = vMap.get(parent);
        if (!major || Number(major.software_id || 0) !== currentSoftwareId) return false;
      }
    }
    return true;
  });

  filtered.sort((a, b) => Number(b.id || 0) - Number(a.id || 0));
  return filtered;
}

function renderMinorOptions(detail, selectedMinorId = null) {
  const minorSelect = document.getElementById('zentaoMapMinorSelect');
  const reqSelect = document.getElementById('zentaoMapRequirementSelect');
  if (!minorSelect || !reqSelect) return;

  const reqId = Number(reqSelect.value || 0);
  const minors = getMinorOptionsForContext(detail, reqId || null);
  const opts = ["<option value=''>请选择小版本</option>"];
  minors.forEach((v) => opts.push(`<option value='${v.id}'>${escapeHtml(v.version_no || '')}</option>`));
  minorSelect.innerHTML = opts.join('');

  const expectId = Number(selectedMinorId || 0);
  if (expectId && minors.some((v) => Number(v.id) === expectId)) {
    minorSelect.value = String(expectId);
  }
}

function updateBucketHint() {
  const displayBucket = document.getElementById('zentaoMapDisplayBucket')?.value || '';
  const hint = document.getElementById('zentaoMapBucketHint');
  const reqSelect = document.getElementById('zentaoMapRequirementSelect');
  if (!hint) return;

  if (displayBucket === 'overall') {
    hint.innerText = '总览池：仅归属到版本整体，不挂具体需求；建议同时选择小版本。';
    if (reqSelect) reqSelect.required = false;
  } else if (displayBucket === 'requirement') {
    hint.innerText = '需求池：挂到具体需求下，参与需求维度统计；该模式下需求为必选。';
    if (reqSelect) reqSelect.required = true;
  } else {
    hint.innerText = '请选择归属范围：需求池（挂需求）或总览池（仅挂版本总览）。';
    if (reqSelect) reqSelect.required = false;
  }
}

function updateRuleHint(detail) {
  const el = document.getElementById('zentaoMapRuleHint');
  if (!el) return;
  if (!detail) {
    el.innerText = '当前映射要求：请先从左侧选择一个同步事件。';
    return;
  }
  const bucket = document.getElementById('zentaoMapDisplayBucket')?.value || detail.display_bucket || detail.recommended_display_bucket || '';
  if (bucket === 'overall') {
    el.innerText = '当前映射要求：总览池模式可不选需求，但需选择小版本，用于版本总览归档。';
  } else {
    el.innerText = '当前映射要求：需求池模式必须选择需求；建议选择该需求所属的小版本。';
  }
}

function refillRequirementAndMinorOptions(preserveSelections = true) {
  const reqSelect = document.getElementById('zentaoMapRequirementSelect');
  const minorSelect = document.getElementById('zentaoMapMinorSelect');
  if (!reqSelect || !minorSelect) return;
  const prevReq = preserveSelections ? Number(reqSelect.value || 0) : 0;
  const prevMinor = preserveSelections ? Number(minorSelect.value || 0) : 0;
  renderMapSelectors(syncState.currentEvent);
  if (prevReq) {
    const ok = Array.from(reqSelect.options).some((opt) => Number(opt.value || 0) === prevReq);
    if (ok) reqSelect.value = String(prevReq);
  }
  renderMinorOptions(syncState.currentEvent, prevMinor || null);
}

export function toggleManualMajorSelector() {
  const wrap = document.getElementById('zentaoMapManualMajorWrap');
  if (!wrap) return;
  wrap.classList.toggle('hidden');
}

export function onZentaoManualMajorChange() {
  const select = document.getElementById('zentaoMapManualMajorSelect');
  if (!select) return;
  const majorId = Number(select.value || 0);
  if (majorId) {
    syncState.manualMajorVersionId = majorId;
    syncState.useManualMajor = true;
  } else {
    syncState.manualMajorVersionId = null;
    syncState.useManualMajor = false;
  }
  renderMajorContext(syncState.currentEvent);
  refillRequirementAndMinorOptions(false);
}

export function resetZentaoManualMajor() {
  syncState.manualMajorVersionId = null;
  syncState.useManualMajor = false;
  const select = document.getElementById('zentaoMapManualMajorSelect');
  if (select) select.value = '';
  const wrap = document.getElementById('zentaoMapManualMajorWrap');
  if (wrap) wrap.classList.add('hidden');
  renderMajorContext(syncState.currentEvent);
  refillRequirementAndMinorOptions(false);
}

function renderMapSelectors(detail) {
  const reqSelect = document.getElementById('zentaoMapRequirementSelect');
  const minorSelect = document.getElementById('zentaoMapMinorSelect');
  const sourceType = document.getElementById('zentaoMapSourceType');
  const sourceRef = document.getElementById('zentaoMapSourceRef');
  const linkedCaseId = document.getElementById('zentaoMapLinkedCaseId');
  const displayBucket = document.getElementById('zentaoMapDisplayBucket');
  if (!reqSelect || !minorSelect || !sourceType || !displayBucket) return;

  syncState.mapRequirements = getFilteredRequirements(detail);

  const reqOptions = ["<option value=''>请选择需求</option>"];
  syncState.mapRequirements.forEach((r) => {
    const label = `${r.zentao_req_id || '-'} ${r.title || ''}`.trim();
    reqOptions.push(`<option value='${r.id}'>${escapeHtml(label)}</option>`);
  });
  reqSelect.innerHTML = reqOptions.join('');
  minorSelect.innerHTML = "<option value=''>请选择小版本</option>";

  const enabled = !!detail;
  setMapFormEnabled(enabled);

  if (!enabled) {
    reqSelect.value = '';
    minorSelect.value = '';
    sourceType.value = 'manual';
    displayBucket.value = '';
    if (sourceRef) sourceRef.value = '';
    if (linkedCaseId) linkedCaseId.value = '';
    const note = document.getElementById('zentaoMapNote');
    if (note) note.value = '';
    renderMajorContext(null);
    updateBucketHint();
    updateRuleHint(null);
    return;
  }

  const mappedReq = Number(detail.mapped_requirement_id || 0);
  const recommendedReq = Number(detail.recommended_requirement_id || 0);
  const selectedReq = mappedReq || recommendedReq || 0;
  if (selectedReq && syncState.mapRequirements.some((r) => Number(r.id) === selectedReq)) {
    reqSelect.value = String(selectedReq);
  }

  renderMinorOptions(detail, Number(detail.mapped_minor_version_id || detail.recommended_minor_version_id || 0));

  sourceType.value = detail.mapped_source_type || detail.recommended_source_type || 'manual';
  const bucketValue = detail.display_bucket || detail.recommended_display_bucket || (selectedReq ? 'requirement' : 'overall');
  displayBucket.value = bucketValue;
  if (sourceRef) sourceRef.value = detail.mapped_source_ref || '';
  if (linkedCaseId) linkedCaseId.value = detail.linked_case_id || '';

  renderMajorContext(detail);
  updateBucketHint();
  updateRuleHint(detail);
}

function bindMapInteractions() {
  if (syncState.formBound) return;

  const reqSelect = document.getElementById('zentaoMapRequirementSelect');
  const bucketSelect = document.getElementById('zentaoMapDisplayBucket');

  if (reqSelect) {
    reqSelect.addEventListener('change', () => {
      renderMinorOptions(syncState.currentEvent, null);
      updateRuleHint(syncState.currentEvent);
    });
  }

  if (bucketSelect) {
    bucketSelect.addEventListener('change', () => {
      updateBucketHint();
      updateRuleHint(syncState.currentEvent);
    });
  }

  syncState.formBound = true;
}

export async function ensureZentaoMapDataReady() {
  try {
    if (!Array.isArray(window.versions) || window.versions.length === 0) {
      if (typeof window.loadVersions === 'function') {
        await window.loadVersions();
      }
    }

    const overviewReady = !!(window.dataOverviewCache && Array.isArray(window.dataOverviewCache.requirements));
    if (!overviewReady) {
      if (typeof window.loadDataOverview === 'function') {
        await window.loadDataOverview();
      } else {
        const data = await (await api('/admin/data-overview')).json();
        window.dataOverviewCache = data;
      }
    }
  } catch (err) {
    window.showMessage?.(err.message || '禅道同步初始化数据加载失败', 'error');
    return false;
  }
  return true;
}

export async function initZentaoMapForm() {
  await ensureZentaoMapDataReady();
  bindMapInteractions();
  syncState.manualMajorVersionId = null;
  syncState.useManualMajor = false;
  syncState.resolvedMajorVersionId = null;
  syncState.resolvedMajorVersionNo = '未识别';
  syncState.resolvedMajorSource = '未识别';
  renderMapSelectors(null);
}

export async function resetZentaoMapForm() {
  syncState.currentEvent = null;
  renderDetail(null);
  await initZentaoMapForm();
}

function renderList() {
  const tb = document.getElementById('zentaoSyncTableBody');
  const pageText = document.getElementById('zentaoSyncPageText');
  if (!tb || !pageText) return;

  if (!syncState.items.length) {
    tb.innerHTML = "<tr><td colspan='7' style='text-align:center; padding:16px; color:#94a3b8;'>暂无同步事件</td></tr>";
  } else {
    tb.innerHTML = syncState.items.map((it) => {
      const no = it.entity_type === 'bug' ? it.zentao_bug_id || '-' : it.zentao_case_id || '-';
      const title = it.title || '-';
      const selected = syncState.currentEvent?.id === it.id;
      return `<tr style="${selected ? 'background:#eff6ff;' : ''}">
        <td>${fmt(it.created_at)}</td>
        <td>${escapeHtml(zhEntity(it.entity_type))}</td>
        <td>${escapeHtml(no)}</td>
        <td class='col-text col-title' title='${escapeHtml(title)}'><span class='cell-ellipsis'>${escapeHtml(title)}</span></td>
        <td>${escapeHtml(it.creator_name || '-')}</td>
        <td class='col-status'>${statusBadge(it.status)}</td>
        <td class='col-actions'>
          <button class='secondary' onclick='openZentaoSyncEventDetail(${it.id})'>查看</button>
          <button class='secondary' style='margin-left:6px; color:#b91c1c; border-color:#fecaca; background:#fef2f2;' onclick='deleteZentaoSyncEvent(${it.id})'>删除</button>
        </td>
      </tr>`;
    }).join('');
  }

  const maxPage = Math.max(1, Math.ceil((syncState.total || 0) / PAGE_SIZE));
  pageText.innerText = `第 ${syncState.page} / ${maxPage} 页，共 ${syncState.total} 条`;
}

function renderDetail(detail) {
  if (!detail) {
    setText('zentaoSyncDetailTitle', '请选择左侧事件查看详情');
    setText('zentaoSyncDetailMeta', '');
    setText('zentaoSyncOriginInfo', '-');
    setText('zentaoSyncRecommendInfo', '-');
    setText('zentaoSyncFailureReason', '-');
    setText('zentaoSyncDetailRaw', '');
    renderMapSelectors(null);
    return;
  }

  resolveCurrentMajorContext(detail);

  setText('zentaoSyncDetailTitle', `同步事件 #${detail.id}`);
  setText('zentaoSyncDetailMeta', `状态：${zhStatus(detail.status)} | 类型：${zhEntity(detail.entity_type)} | 创建时间：${fmt(detail.created_at)}`);

  const draft = detail.draft || detail.raw_payload?.draft || {};
  const requirementId = detail.zentao_req_id || draft.requirementId || '-';
  const requirementName = detail.zentao_requirement_name || draft.requirementName || '-';
  const creatorName = detail.creator_name || draft.creatorName || '-';
  const executionName = detail.zentao_execution_name || draft.executionName || '-';
  const affectedVersion = detail.zentao_affected_version || draft.affectedVersion || '-';
  const linkedCaseId = detail.linked_case_id || draft.linkedCaseId || '-';
  const linkedCaseLabel = detail.linked_case_label || draft.linkedCaseLabel || '';

  const originLines = [
    `禅道 Bug：${detail.zentao_bug_id || '-'}`,
    `禅道用例：${detail.zentao_case_id || '-'}`,
    `需求编号：${requirementId}`,
    `需求名称：${requirementName}`,
    `创建者：${creatorName}`,
    `执行版本：${executionName}`,
    `影响版本：${affectedVersion}`,
    `来源用例：${`${linkedCaseId} ${linkedCaseLabel}`.trim()}`,
    `原始页面：${detail.top_href || '-'}`,
  ];
  setText('zentaoSyncOriginInfo', originLines.join('\n'));

  const recLines = [
    `推荐需求：${detail.recommended_requirement_id || '-'}`,
    `推荐主版本：${detail.recommended_major_version_id || detail.mapped_major_version_id || '-'}`,
    `推荐小版本：${detail.recommended_minor_version_id || '-'}`,
    `推荐来源类型：${zhSourceType(detail.recommended_source_type)}`,
    `推荐归属范围：${zhBucket(detail.recommended_display_bucket || detail.display_bucket)}`,
    `推荐来源用例关联：${detail.recommended_test_case_id || '-'}`,
  ];
  setText('zentaoSyncRecommendInfo', recLines.join('\n'));

  setText('zentaoSyncFailureReason', localizeFreeText(detail.failure_reason));
  setText('zentaoSyncDetailRaw', JSON.stringify(detail.raw_payload || {}, null, 2));
  renderMapSelectors(detail);
}

export async function loadZentaoSyncBoard(page = 1) {
  ensureAdmin();
  await ensureZentaoMapDataReady();
  bindMapInteractions();
  if (!syncState.currentEvent) renderMapSelectors(null);

  syncState.page = page;
  const q = currentQuery();
  const params = new URLSearchParams({ page: String(syncState.page), page_size: String(PAGE_SIZE) });
  if (q.entityType) params.set('entity_type', q.entityType);
  if (q.status) params.set('status', q.status);
  if (q.displayBucket) params.set('display_bucket', q.displayBucket);
  if (q.sourceType) params.set('source_type', q.sourceType);
  if (q.keyword) params.set('keyword', q.keyword);
  if (q.dateFrom) params.set('date_from', q.dateFrom);
  if (q.dateTo) params.set('date_to', q.dateTo);
  if (q.onlyUnapplied) params.set('only_unapplied', 'true');

  const data = await (await api(`/api/integrations/zentao/browser-events?${params.toString()}`)).json();
  syncState.items = data.items || [];
  syncState.total = Number(data.total || 0);
  renderList();

  if (syncState.currentEvent) {
    const found = syncState.items.find((x) => x.id === syncState.currentEvent.id);
    if (!found) {
      syncState.currentEvent = null;
      renderDetail(null);
    }
  }
}

export async function initZentaoSyncBoard() {
  ensureAdmin();
  await ensureZentaoMapDataReady();
  await resetZentaoMapForm();
  await loadZentaoSyncBoard(1);
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
  await ensureZentaoMapDataReady();
  const detail = await (await api(`/api/integrations/zentao/browser-events/${eventId}`)).json();
  syncState.currentEvent = detail;
  renderList();
  renderDetail(detail);
}

export async function saveZentaoSyncMapping() {
  if (!syncState.currentEvent) {
    window.showMessage?.('请先选择左侧同步事件', 'error');
    return;
  }

  const requirementId = Number(document.getElementById('zentaoMapRequirementSelect')?.value || 0);
  const minorVersionId = Number(document.getElementById('zentaoMapMinorSelect')?.value || 0);
  const sourceType = document.getElementById('zentaoMapSourceType')?.value || '';
  const sourceRef = (document.getElementById('zentaoMapSourceRef')?.value || '').trim();
  const linkedCaseId = (document.getElementById('zentaoMapLinkedCaseId')?.value || '').trim();
  const displayBucket = document.getElementById('zentaoMapDisplayBucket')?.value || '';
  const note = (document.getElementById('zentaoMapNote')?.value || '').trim();

  if (!displayBucket) {
    window.showMessage?.('请选择归属范围（需求池/总览池）', 'error');
    return;
  }

  if (displayBucket === 'requirement' && !requirementId) {
    window.showMessage?.('归属范围为需求池时，需求为必选项', 'error');
    return;
  }

  if (displayBucket === 'overall' && !minorVersionId) {
    window.showMessage?.('归属范围为总览池时，请至少选择小版本', 'error');
    return;
  }

  await api(`/api/integrations/zentao/browser-events/${syncState.currentEvent.id}/map`, {
    method: 'POST',
    headers: window.H,
    body: {
      requirement_id: requirementId || null,
      minor_version_id: minorVersionId || null,
      source_type: sourceType || null,
      source_ref: sourceRef || null,
      linked_case_id: linkedCaseId || null,
      display_bucket: displayBucket || null,
      note: note || null,
    },
  });

  window.showMessage?.('映射保存成功', 'success');
  await openZentaoSyncEventDetail(syncState.currentEvent.id);
  await loadZentaoSyncBoard(syncState.page);
}

export async function applyZentaoSyncEvent() {
  if (!syncState.currentEvent) {
    window.showMessage?.('请先选择左侧同步事件', 'error');
    return;
  }
  const data = await (await api(`/api/integrations/zentao/browser-events/${syncState.currentEvent.id}/apply`, {
    method: 'POST',
    headers: window.H,
  })).json();
  window.showMessage?.(data.message || '应用完成', 'success');
  await openZentaoSyncEventDetail(syncState.currentEvent.id);
  await loadZentaoSyncBoard(syncState.page);
}

export async function deleteZentaoSyncEvent(eventId = null) {
  const targetId = Number(eventId || syncState.currentEvent?.id || 0);
  if (!targetId) {
    window.showMessage?.('请先选择要删除的同步记录', 'error');
    return;
  }
  const ok = window.confirm('确认删除这条禅道同步记录吗？删除后无法恢复。');
  if (!ok) return;

  try {
    const resp = await api(`/api/integrations/zentao/browser-events/${targetId}`, {
      method: 'DELETE',
      headers: window.H,
    });
    const data = await resp.json();
    window.showMessage?.(data.message || '删除成功', 'success');
  } catch (err) {
    const msg = err?.message || '删除失败';
    window.showMessage?.(
      msg.includes('404') ? '记录不存在或已被删除' : msg,
      'error'
    );
    return;
  }

  if (syncState.currentEvent?.id === targetId) {
    syncState.currentEvent = null;
    renderDetail(null);
  }

  const targetPage = (syncState.items.length <= 1 && syncState.page > 1) ? syncState.page - 1 : syncState.page;
  await loadZentaoSyncBoard(targetPage);
}

export async function applyZentaoSyncBatch() {
  const data = await (await api('/api/integrations/zentao/browser-events/apply-batch', {
    method: 'POST',
    headers: window.H,
    body: { limit: 100 },
  })).json();
  const ok = Number(data.failed || 0) === 0;
  window.showMessage?.(`批量应用完成：成功 ${data.success || 0}，失败 ${data.failed || 0}`, ok ? 'success' : 'error');
  await loadZentaoSyncBoard(syncState.page);
}

export function closeZentaoSyncDetail() {}

window.OmniQAZentaoSyncTab = {
  ensureZentaoMapDataReady,
  initZentaoMapForm,
  resetZentaoMapForm,
  initZentaoSyncBoard,
  toggleManualMajorSelector,
  onZentaoManualMajorChange,
  resetZentaoManualMajor,
  loadZentaoSyncBoard,
  nextZentaoSyncPage,
  prevZentaoSyncPage,
  openZentaoSyncEventDetail,
  saveZentaoSyncMapping,
  applyZentaoSyncEvent,
  deleteZentaoSyncEvent,
  applyZentaoSyncBatch,
  closeZentaoSyncDetail,
};
