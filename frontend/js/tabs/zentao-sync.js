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

const ROUTE_SOURCE_ZH = {
  case: '用例来源',
  requirement: '需求来源',
  test: '测试来源',
};

const ROUTE_TARGET_ZH = {
  overall: '整体测试落位',
  requirement_free_bug: '需求自由 Bug',
  requirement_case_bug: '需求用例 Bug',
  retest_requirement_free_bug: '复测需求自由 Bug',
  retest_requirement_case_bug: '复测需求用例 Bug',
  pending_decision: '待决策',
  pending_mapping: '待补全映射',
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
  unseenNewCount: 0,
  sseBound: false,
  selectedProductId: null,
};
let zentaoBatchTimer = null;
const zentaoBatch = {
  created: [],
  updated: new Map(),
};
let zentaoUnreadClearTimer = null;

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

function zhRouteSource(v) {
  return ROUTE_SOURCE_ZH[String(v || '').toLowerCase()] || String(v || '-');
}

function zhRouteTarget(v) {
  return ROUTE_TARGET_ZH[String(v || '').toLowerCase()] || String(v || '-');
}

function getCurrentSoftwareId() {
  return Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
}

function getVersions() {
  return Array.isArray(window.versions) ? window.versions : [];
}

function getRequirements() {
  return Array.isArray(window.zentaoRequirementsCache) ? window.zentaoRequirementsCache : [];
}

function getSoftwareProducts() {
  return Array.isArray(window.zentaoSoftwareProductsCache) ? window.zentaoSoftwareProductsCache : [];
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

function ensureLoggedIn() {
  if (!state.currentUser) {
    throw new Error('请先登录后访问禅道同步中心');
  }
}

function isAdminUser() {
  return !!state.currentUser && state.currentUser.role === 'admin';
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
    productId: Number(document.getElementById('zentaoSyncProductFilter')?.value || 0) || null,
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
    'zentaoMapProductSelect',
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

// 用例只需要「需求」字段，其余字段仅 Bug 使用，置灰不可操作
const BUG_ONLY_FIELD_IDS = [
  'zentaoMapMinorSelect',
  'zentaoMapSourceType',
  'zentaoMapDisplayBucket',
  'zentaoMapLinkedCaseId',
  'zentaoMapSourceRef',
  'zentaoMapNote',
];

function applyEntityConstraints(detail) {
  const isTestcase = detail?.entity_type === 'testcase';
  BUG_ONLY_FIELD_IDS.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (isTestcase) el.disabled = true;
    const wrapper = el.closest('.zentao-map-field');
    if (wrapper) {
      wrapper.style.opacity = isTestcase ? '0.38' : '';
      wrapper.style.pointerEvents = isTestcase ? 'none' : '';
      wrapper.title = isTestcase ? '用例同步不使用此字段' : '';
    }
  });
  // 归属范围提示行单独处理（不在 .zentao-map-field 内）
  const bucketHint = document.getElementById('zentaoMapBucketHint');
  if (bucketHint) {
    bucketHint.style.opacity = isTestcase ? '0.38' : '';
  }
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
  const productId = syncState.selectedProductId;
  return new Set(
    getVersions()
      .filter((v) => v.version_type === 'major' && (!productId || Number(v.software_id || 0) === productId))
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

  const majors = getVersions().filter((v) => v.version_type === 'major' && (!syncState.selectedProductId || Number(v.software_id || 0) === syncState.selectedProductId));
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
  const productId = syncState.selectedProductId;
  const majorIds = getSoftwareMajorIds();
  const majorHint = resolveEventMajorHint(detail);
  const result = reqs.filter((r) => {
    const majorId = Number(r.major_version_id || 0);
    if (!majorId) return false;
    // If product is selected but has no versions, show no requirements for that product
    if (productId && majorIds.size === 0) return false;
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
  const productId = syncState.selectedProductId;
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
      if (productId) {
        const major = vMap.get(parent);
        if (!major || Number(major.software_id || 0) !== productId) return false;
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

export function onZentaoProductChange() {
  const select = document.getElementById('zentaoMapProductSelect');
  if (!select) return;
  syncState.selectedProductId = Number(select.value || 0) || null;
  syncState.manualMajorVersionId = null;
  syncState.useManualMajor = false;
  renderMajorContext(syncState.currentEvent);
  refillRequirementAndMinorOptions(false);
}

function renderProductSelectOptions(detail) {
  const productSelect = document.getElementById('zentaoMapProductSelect');
  if (!productSelect) return;
  const products = getSoftwareProducts();
  productSelect.innerHTML =
    "<option value=''>全部产品（不限制）</option>" +
    products.map((p) => `<option value='${p.id}'>${escapeHtml(p.name)}</option>`).join('');
  if (detail && detail.recommended_software_id) {
    syncState.selectedProductId = detail.recommended_software_id;
  }
  productSelect.value = syncState.selectedProductId ? String(syncState.selectedProductId) : '';
}

function renderMapSelectors(detail) {
  const reqSelect = document.getElementById('zentaoMapRequirementSelect');
  const minorSelect = document.getElementById('zentaoMapMinorSelect');
  const sourceType = document.getElementById('zentaoMapSourceType');
  const sourceRef = document.getElementById('zentaoMapSourceRef');
  const linkedCaseId = document.getElementById('zentaoMapLinkedCaseId');
  const displayBucket = document.getElementById('zentaoMapDisplayBucket');
  if (!reqSelect || !minorSelect || !sourceType || !displayBucket) return;

  renderProductSelectOptions(detail);
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
    const productSelect = document.getElementById('zentaoMapProductSelect');
    if (productSelect) productSelect.value = '';
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
  applyEntityConstraints(detail);
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

    if (!Array.isArray(window.zentaoRequirementsCache)) {
      const data = await (await api('/api/integrations/zentao/requirements')).json();
      window.zentaoRequirementsCache = data.requirements || [];
    }

    if (!Array.isArray(window.zentaoSoftwareProductsCache)) {
      const data = await (await api('/api/integrations/zentao/software-products')).json();
      window.zentaoSoftwareProductsCache = data.software_products || [];
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
  syncState.selectedProductId = null;
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
      const productName = it.zentao_product_name || '';
      const selected = syncState.currentEvent?.id === it.id;
      const deleteBtn = `<button class='secondary' style='color:#b91c1c; border-color:#fecaca; background:#fef2f2;' onclick='deleteZentaoSyncEvent(${it.id})'>删除</button>`;
      return `<tr class='zentao-row-card' data-event-id='${it.id}' style="${selected ? 'background:#eff6ff;' : ''}">
        <td>${fmt(it.created_at)}</td>
        <td>${escapeHtml(zhEntity(it.entity_type))}</td>
        <td>${escapeHtml(no)}</td>
        <td class='col-text col-title' title='${escapeHtml(title)}'><span class='cell-ellipsis'>${escapeHtml(title)}</span>${productName ? `<br><small style='color:#94a3b8; font-size:0.78em;'>${escapeHtml(productName)}</small>` : ''}</td>
        <td>${escapeHtml(it.creator_name || '-')}</td>
        <td class='col-status'>${statusBadge(it.status)}</td>
        <td class='col-actions'>
          <div class='zentao-row-actions'>
            <button class='secondary' onclick='openZentaoSyncEventDetail(${it.id})'>查看</button>
            ${deleteBtn}
          </div>
        </td>
      </tr>`;
    }).join('');
  }

  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    window.OmniQASSE.releaseAttention?.();
    document.querySelectorAll('tr.zentao-row-card[data-event-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'zentao_sync', key: el.getAttribute('data-event-id'), tone: 'purple', hoverDelayMs: 420 });
    });
  }

  const maxPage = Math.max(1, Math.ceil((syncState.total || 0) / PAGE_SIZE));
  pageText.innerText = `第 ${syncState.page} / ${maxPage} 页，共 ${syncState.total} 条`;
}

function zentaoAtTop() {
  const wrap = document.querySelector('#tab-zentao-sync .zentao-sync-table-wrap');
  if (!wrap) return true;
  return wrap.scrollTop <= 12;
}

const _zentaoTopBannerCount = { value: 0 };

function updateTopNotice() {
  // Legacy DOM notice (keep for backward compat if element exists)
  const notice = document.getElementById('zentaoSyncTopNotice');
  if (notice) {
    if (syncState.unseenNewCount <= 0) {
      notice.classList.add('hidden');
    } else {
      notice.classList.remove('hidden');
      notice.textContent = `有 ${syncState.unseenNewCount} 条新同步记录`;
    }
  }
  // New position-aware banner
  if (!window.OmniQASSE?.showPositionBanner) return;
  _zentaoTopBannerCount.value = syncState.unseenNewCount;
  if (syncState.unseenNewCount <= 0) return;
  const tableWrap = document.querySelector('#tab-zentao-sync .zentao-sync-table-wrap');
  if (!tableWrap) return;
  window.OmniQASSE.showPositionBanner({
    scrollContainer: tableWrap,
    anchorEl: tableWrap,
    position: 'top',
    countRef: _zentaoTopBannerCount,
    labelFn: (n) => `⬆ 有 ${n} 条新同步记录，点击滚到顶部`,
    onClickScroll: () => {
      tableWrap.scrollTop = 0;
      syncState.unseenNewCount = 0;
      window.OmniQASSE?.clearScopeUnread?.('zentao_sync');
    },
    bannerId: 'zentao-new',
  });
}

function glowRowByEventId(eventId, tone = 'blue') {
  const el = document.querySelector(`.zentao-row-card[data-event-id='${eventId}']`);
  if (!el || !window.OmniQASSE || typeof window.OmniQASSE.pulseBoundaryGlow !== 'function') return;
  window.OmniQASSE.pulseBoundaryGlow(el, tone);
}

function upsertItemKeepingOrder(item) {
  if (!item || !item.id) return false;
  const idx = syncState.items.findIndex((x) => Number(x.id) === Number(item.id));
  if (idx >= 0) {
    syncState.items[idx] = { ...syncState.items[idx], ...item };
    return false;
  }
  syncState.items.unshift(item);
  if (syncState.items.length > PAGE_SIZE) syncState.items = syncState.items.slice(0, PAGE_SIZE);
  syncState.total = Number(syncState.total || 0) + 1;
  return true;
}

function removeItemWithFade(eventId) {
  const rowCard = document.querySelector(`.zentao-row-card[data-event-id='${eventId}']`);
  if (rowCard) {
    const tr = rowCard.closest('tr');
    if (tr) {
      tr.style.transition = 'opacity .22s ease, transform .22s ease';
      tr.style.opacity = '0';
      tr.style.transform = 'translateY(-3px)';
      setTimeout(() => {
        syncState.items = syncState.items.filter((x) => Number(x.id) !== Number(eventId));
        syncState.total = Math.max(0, Number(syncState.total || 0) - 1);
        renderList();
      }, 220);
      return;
    }
  }
  syncState.items = syncState.items.filter((x) => Number(x.id) !== Number(eventId));
  syncState.total = Math.max(0, Number(syncState.total || 0) - 1);
  renderList();
}

function bindSSE() {
  if (syncState.sseBound) return;
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;

  const flushZentaoBatch = () => {
    zentaoBatchTimer = null;
    const createdItems = zentaoBatch.created.splice(0);
    const updatedItems = Array.from(zentaoBatch.updated.values());
    zentaoBatch.updated.clear();
    if (!createdItems.length && !updatedItems.length) return;

    const insertedIds = [];
    const updatedIds = [];
    createdItems.forEach((item) => {
      if (upsertItemKeepingOrder(item)) insertedIds.push(Number(item.id));
    });
    updatedItems.forEach((item) => {
      const idx = syncState.items.findIndex((x) => Number(x.id) === Number(item.id));
      if (idx < 0) return;
      syncState.items[idx] = { ...syncState.items[idx], ...item };
      updatedIds.push(Number(item.id));
      if (syncState.currentEvent?.id === Number(item.id)) {
        syncState.currentEvent = { ...syncState.currentEvent, ...item };
      }
    });

    if (insertedIds.length || updatedIds.length) {
      renderList();
      if (syncState.currentEvent && updatedIds.includes(Number(syncState.currentEvent.id))) {
        renderDetail(syncState.currentEvent);
      }
    }

    if (insertedIds.length) {
      if (zentaoAtTop()) {
        // 新记录：深邃蓝紫
        insertedIds.forEach((id) => glowRowByEventId(id, 'purple'));
      } else {
        syncState.unseenNewCount += insertedIds.length;
        updateTopNotice();
      }
    }
    // 状态变化：青绿色
    updatedIds.forEach((id) => glowRowByEventId(id, 'teal'));
  };

  const enqueueZentaoBatch = (kind, item) => {
    if (!item?.id) return;
    if (kind === 'created') zentaoBatch.created.push(item);
    else zentaoBatch.updated.set(Number(item.id), item);
    if (zentaoBatchTimer) clearTimeout(zentaoBatchTimer);
    zentaoBatchTimer = setTimeout(flushZentaoBatch, 260);
  };

  const onCreated = ({ payload }) => {
    enqueueZentaoBatch('created', payload?.item);
  };
  const onUpdated = ({ payload }) => {
    enqueueZentaoBatch('updated', payload?.item);
  };
  const onDeleted = ({ payload }) => {
    const eventId = Number(payload?.id || 0);
    if (!eventId) return;
    if (syncState.currentEvent?.id === eventId) {
      syncState.currentEvent = null;
      renderDetail(null);
      window.showMessage?.('当前详情记录已删除', 'success');
    }
    removeItemWithFade(eventId);
  };
  window.OmniQASSE.subscribe('zentao_sync_created', onCreated);
  window.OmniQASSE.subscribe('zentao_sync_updated', onUpdated);
  window.OmniQASSE.subscribe('zentao_sync_auto_apply_failed', onUpdated);
  window.OmniQASSE.subscribe('zentao_sync_deleted', onDeleted);
  syncState.sseBound = true;
}

function bindTopScrollReset() {
  const wrap = document.querySelector('#tab-zentao-sync .zentao-sync-table-wrap');
  if (!wrap || wrap.dataset.sseBound === '1') return;
  wrap.dataset.sseBound = '1';
  wrap.addEventListener('scroll', () => {
    if (zentaoAtTop() && syncState.unseenNewCount > 0) {
      syncState.unseenNewCount = 0;
      updateTopNotice();
      if (window.OmniQASSE && typeof window.OmniQASSE.clearScopeUnread === 'function') {
        window.OmniQASSE.clearScopeUnread('zentao_sync');
      }
    }
  });
}

function deferClearZentaoUnread(ms = 1200) {
  if (zentaoUnreadClearTimer) clearTimeout(zentaoUnreadClearTimer);
  zentaoUnreadClearTimer = setTimeout(() => {
    zentaoUnreadClearTimer = null;
    const tab = document.getElementById('tab-zentao-sync');
    if (!tab || tab.classList.contains('hidden')) return;
    if (!window.OmniQASSE || typeof window.OmniQASSE.clearScopeUnread !== 'function') return;
    // Delay clearing unread until list rows are rendered and first attention pulse had time to play.
    window.OmniQASSE.clearScopeUnread('zentao_sync');
  }, ms);
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
  const bugTitle = detail.zentao_bug_title || detail.title || draft.bugTitle || '-';
  const executionName = detail.zentao_execution_name || draft.executionName || '-';
  const affectedVersion = detail.zentao_affected_version || draft.affectedVersion || '-';
  const linkedCaseId = detail.linked_case_id || draft.linkedCaseId || '-';
  const linkedCaseLabel = detail.linked_case_label || draft.linkedCaseLabel || '';

  const originLines = [
    `禅道 Bug：${detail.zentao_bug_id || '-'}`,
    `禅道用例：${detail.zentao_case_id || '-'}`,
    `产品/应用：${detail.zentao_product_name || '-'}`,
    `标题：${bugTitle}`,
    `需求编号：${requirementId}`,
    `需求名称：${requirementName}`,
    `创建者：${creatorName}`,
    `执行版本：${executionName}`,
    `影响版本：${affectedVersion}`,
    `来源用例：${`${linkedCaseId} ${linkedCaseLabel}`.trim()}`,
    `原始页面：${detail.top_href || '-'}`,
  ];
  setText('zentaoSyncOriginInfo', originLines.join('\n'));

  const recMajorId = detail.recommended_major_version_id || detail.mapped_major_version_id;
  const recMajorNo = majorVersionNoById(recMajorId) || recMajorId || '-';
  const recMinorNo = majorVersionNoById(detail.recommended_minor_version_id) || detail.recommended_minor_version_id || '-';
  const recReqId = Number(detail.recommended_requirement_id || 0);
  const recReq = recReqId ? getRequirementById(syncState.mapRequirements, recReqId) : null;
  const recReqLabel = recReq ? `${recReq.zentao_req_id || ''} ${recReq.title || ''}`.trim() : (recReqId ? String(recReqId) : '-');

  const recLines = [
    `推荐需求：${recReqLabel}`,
    `推荐主版本：${recMajorNo}`,
    `推荐小版本：${recMinorNo}`,
    `推荐来源类型：${zhSourceType(detail.recommended_source_type)}`,
    `推荐归属范围：${zhBucket(detail.recommended_display_bucket || detail.display_bucket)}`,
    `推荐来源用例关联：${detail.recommended_test_case_id || '-'}`,
    `自动路由来源：${zhRouteSource(detail.recommended_route_source)}`,
    `自动路由目标：${zhRouteTarget(detail.recommended_route_target)}`,
    `待确认原因：${localizeFreeText(detail.recommended_decision_reason)}`,
  ];
  setText('zentaoSyncRecommendInfo', recLines.join('\n'));

  setText('zentaoSyncFailureReason', localizeFreeText(detail.failure_reason));
  setText('zentaoSyncDetailRaw', JSON.stringify(detail.raw_payload || {}, null, 2));
  renderMapSelectors(detail);
}

function renderProductFilterOptions() {
  const sel = document.getElementById('zentaoSyncProductFilter');
  if (!sel) return;
  const products = getSoftwareProducts();
  const val = sel.value;
  sel.innerHTML =
    "<option value=''>全部产品</option>" +
    products.map((p) => `<option value='${p.id}'>${escapeHtml(p.name)}</option>`).join('');
  if (val) sel.value = val;
}

export async function loadZentaoSyncBoard(page = 1) {
  ensureLoggedIn();
  await ensureZentaoMapDataReady();
  renderProductFilterOptions();
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
  if (q.productId) params.set('software_id', String(q.productId));

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
  ensureLoggedIn();
  bindSSE();
  bindTopScrollReset();
  await ensureZentaoMapDataReady();
  await resetZentaoMapForm();
  await loadZentaoSyncBoard(1);
  deferClearZentaoUnread(1300);
}

export function scrollZentaoSyncToTop() {
  const wrap = document.querySelector('#tab-zentao-sync .zentao-sync-table-wrap');
  if (wrap) wrap.scrollTop = 0;
  syncState.unseenNewCount = 0;
  updateTopNotice();
  if (window.OmniQASSE && typeof window.OmniQASSE.clearScopeUnread === 'function') {
    window.OmniQASSE.clearScopeUnread('zentao_sync');
  }
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

  const isTestcase = syncState.currentEvent?.entity_type === 'testcase';

  if (!isTestcase) {
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
  } else if (!requirementId) {
    window.showMessage?.('用例同步必须选择需求', 'error');
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
  try {
    const data = await (await api(`/api/integrations/zentao/browser-events/${syncState.currentEvent.id}/apply`, {
      method: 'POST',
      headers: window.H,
    })).json();
    window.showMessage?.(data.message || '应用完成', 'success');
    await openZentaoSyncEventDetail(syncState.currentEvent.id);
    await loadZentaoSyncBoard(syncState.page);
  } catch (err) {
    window.showMessage?.(err?.message || '应用失败', 'error');
  }
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
  onZentaoProductChange,
  loadZentaoSyncBoard,
  nextZentaoSyncPage,
  prevZentaoSyncPage,
  openZentaoSyncEventDetail,
  saveZentaoSyncMapping,
  applyZentaoSyncEvent,
  deleteZentaoSyncEvent,
  applyZentaoSyncBatch,
  closeZentaoSyncDetail,
  scrollZentaoSyncToTop,
};
