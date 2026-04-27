import { api } from '../api.js';
import { escapeHtml, renderCaseLink } from '../utils.js';

const testcaseCenterState = {
  page: 1,
  pageSize: 20,
  total: 0,
  items: [],
  modules: [],
  modulesLoadedFor: 0,
};

function currentSoftwareId() {
  return Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
}

function currentQuery() {
  const moduleSel = document.getElementById('testcaseCenterModule');
  const moduleId = Number(moduleSel?.value || 0);
  return {
    software_id: currentSoftwareId(),
    status: document.getElementById('testcaseCenterStatus')?.value || '',
    module_id: moduleId || 0,
    keyword: (document.getElementById('testcaseCenterKeyword')?.value || '').trim(),
    page: testcaseCenterState.page,
    page_size: testcaseCenterState.pageSize,
  };
}

async function refreshModuleOptions() {
  const softwareId = currentSoftwareId();
  if (testcaseCenterState.modulesLoadedFor === softwareId) return;
  const moduleSel = document.getElementById('testcaseCenterModule');
  if (!moduleSel) return;
  try {
    const url = softwareId ? `/zentao/testcases/modules?software_id=${softwareId}` : '/zentao/testcases/modules';
    const data = await (await api(url)).json();
    testcaseCenterState.modules = Array.isArray(data) ? data : [];
    testcaseCenterState.modulesLoadedFor = softwareId;
    const prev = moduleSel.value;
    moduleSel.innerHTML = ['<option value="">全部模块</option>']
      .concat(testcaseCenterState.modules.map((m) => `<option value="${m.id}">${escapeHtml(m.name || `模块 ${m.id}`)}</option>`))
      .join('');
    if (prev) moduleSel.value = prev;
  } catch (err) {
    console.warn('load testcase modules failed', err);
  }
}

function linkedReqHtml(row) {
  const items = Array.isArray(row.linked_requirements) ? row.linked_requirements : [];
  if (items.length === 0) return '<span class="muted">未自动归属</span>';
  return items.map((req) => {
    const major = req.major_version_name ? `<span style="color:#94a3b8;">(${escapeHtml(req.major_version_name)})</span>` : '';
    return `<div><span class="badge" style="background:#eff6ff; color:#1d4ed8; margin-right:6px;">${escapeHtml(req.zentao_req_id || '-')}</span>${escapeHtml(req.title || '')} ${major}</div>`;
  }).join('');
}

const STATUS_ZH = {
  normal: '正常',
  wait: '待评审',
  blocked: '已阻塞',
  done: '已完成',
  closed: '已关闭',
  draft: '草稿',
};

const STAGE_ZH = {
  unittest: '单元测试',
  feature: '功能测试',
  integration: '集成测试',
  system: '系统测试',
  smoke: '冒烟测试',
  bvt: 'BVT',
  design: '设计阶段',
};

const RUN_RESULT_ZH = {
  pass: '通过',
  fail: '失败',
  blocked: '阻塞',
  n_a: '未涉及',
  skipped: '跳过',
};

function localizeStatus(value) {
  const key = String(value || '').toLowerCase();
  return STATUS_ZH[key] || value || '-';
}

function localizeStage(value) {
  const key = String(value || '').toLowerCase();
  return STAGE_ZH[key] || value || '-';
}

function localizeRunResult(value) {
  const key = String(value || '').toLowerCase();
  return RUN_RESULT_ZH[key] || value || '-';
}

function syncText(row) {
  const source = escapeHtml(row.sync_source || '-');
  const time = row.last_zentao_synced_at ? new Date(row.last_zentao_synced_at).toLocaleString() : '从未同步';
  return `<div>${source}</div><div class="muted" style="font-size:12px;">${time}</div>`;
}

function renderRows() {
  const tbody = document.getElementById('testcaseCenterTableBody');
  const meta = document.getElementById('testcaseCenterMeta');
  const pager = document.getElementById('testcaseCenterPagerText');
  if (!tbody) return;

  if (!testcaseCenterState.items.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="muted" style="text-align:center; padding: 24px;">当前条件下暂无用例数据</td></tr>';
  } else {
    tbody.innerHTML = testcaseCenterState.items.map((row) => {
      const caseLink = renderCaseLink(row);
      const title = escapeHtml(row.title || '-');
      const status = escapeHtml(localizeStatus(row.status));
      const stage = escapeHtml(localizeStage(row.stage));
      const lastRunDate = row.last_run_date ? new Date(row.last_run_date).toLocaleString() : '未执行';
      const lastRunResult = escapeHtml(localizeRunResult(row.last_run_result));
      const deletedBadge = row.deleted ? '<span class="badge" style="background:#fee2e2; color:#b91c1c; margin-left:6px;">已删除</span>' : '';
      return `<tr>
        <td>${caseLink}${deletedBadge}</td>
        <td>
          <div style="font-weight:600; color:#0f172a;">${title}</div>
          <div class="muted" style="font-size:12px;">产品：${escapeHtml(row.zentao_product_name || '-')} / 模块：${escapeHtml(row.zentao_module_name || '-')}</div>
        </td>
        <td>${linkedReqHtml(row)}</td>
        <td>
          <span class="badge" style="background:#f1f5f9; color:#475569;">${status}</span>
          <span class="badge" style="background:#eef2ff; color:#4338ca; margin-left:6px;">${stage}</span>
        </td>
        <td>
          <div>${escapeHtml(row.last_runner_name || '无人')}</div>
          <div class="muted" style="font-size:12px;">${lastRunResult} / ${lastRunDate}</div>
        </td>
        <td>${syncText(row)}</td>
        <td><button class="secondary" onclick="openTestcaseCenterDetail(${row.zentao_case_numeric_id})">详情</button></td>
      </tr>`;
    }).join('');
  }

  if (meta) {
    meta.innerHTML = `当前软件 <b>${escapeHtml(String(currentSoftwareId() || '-'))}</b>，共 <b>${testcaseCenterState.total}</b> 条用例`;
  }
  if (pager) {
    const totalPages = Math.max(1, Math.ceil((testcaseCenterState.total || 0) / testcaseCenterState.pageSize));
    pager.innerText = `第 ${testcaseCenterState.page} / ${totalPages} 页`;
  }
}

export async function loadTestcaseCenter(page = 1) {
  testcaseCenterState.page = page;
  await refreshModuleOptions();
  const query = currentQuery();
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== '' && value !== null && value !== undefined && value !== 0) {
      params.set(key, String(value));
    }
  });
  const data = await (await api(`/zentao/testcases?${params.toString()}`)).json();
  testcaseCenterState.items = Array.isArray(data.items) ? data.items : [];
  testcaseCenterState.total = Number(data.total || 0);
  testcaseCenterState.page = Number(data.page || page);
  testcaseCenterState.pageSize = Number(data.page_size || testcaseCenterState.pageSize);
  renderRows();
}

export async function syncTestcaseCenter() {
  const softwareId = currentSoftwareId();
  if (!softwareId) {
    window.showMessage && window.showMessage('请先选择软件', 'error');
    return;
  }
  const res = await api('/zentao/testcases/sync', {
    method: 'POST',
    headers: window.H,
    body: { software_id: softwareId, force: true },
  });
  const data = await res.json();
  window.showMessage && window.showMessage(`同步完成：新增 ${data.created || 0}，更新 ${data.updated || 0}，详情补拉 ${data.detail_refreshed || 0}`, 'success');
  await loadTestcaseCenter(1);
}

export async function openTestcaseCenterDetail(caseNumericId) {
  const softwareId = currentSoftwareId();
  const detail = await (await api(`/zentao/testcases/${caseNumericId}?software_id=${softwareId || ''}`)).json();
  const modal = document.getElementById('testcaseCenterDetailModal');
  const titleEl = document.getElementById('testcaseCenterDetailTitle');
  const metaEl = document.getElementById('testcaseCenterDetailMeta');
  const preEl = document.getElementById('testcaseCenterPrecondition');
  const stepsEl = document.getElementById('testcaseCenterSteps');
  if (!modal || !titleEl || !metaEl || !preEl || !stepsEl) return;

  titleEl.innerText = `${detail.zentao_case_id || '-'} ${detail.title || ''}`;
  metaEl.innerText = `产品：${detail.zentao_product_name || '-'} | 模块：${detail.zentao_module_name || '-'} | 状态：${localizeStatus(detail.status)} | 阶段：${localizeStage(detail.stage)} | 最近同步：${detail.last_zentao_synced_at ? new Date(detail.last_zentao_synced_at).toLocaleString() : '从未同步'}`;
  preEl.innerText = detail.precondition || '无';
  stepsEl.innerText = detail.steps_digest || '无';
  modal.style.display = 'flex';
  modal.classList.remove('hidden');
}

export function closeTestcaseCenterDetail() {
  const modal = document.getElementById('testcaseCenterDetailModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.style.display = 'none';
}

export async function nextTestcaseCenterPage() {
  const totalPages = Math.max(1, Math.ceil((testcaseCenterState.total || 0) / testcaseCenterState.pageSize));
  if (testcaseCenterState.page >= totalPages) return;
  await loadTestcaseCenter(testcaseCenterState.page + 1);
}

export async function prevTestcaseCenterPage() {
  if (testcaseCenterState.page <= 1) return;
  await loadTestcaseCenter(testcaseCenterState.page - 1);
}

window.OmniQATestcaseCenterTab = {
  loadTestcaseCenter,
  syncTestcaseCenter,
  openTestcaseCenterDetail,
  closeTestcaseCenterDetail,
  nextTestcaseCenterPage,
  prevTestcaseCenterPage,
};

window.loadTestcaseCenter = loadTestcaseCenter;
window.syncTestcaseCenter = syncTestcaseCenter;
window.openTestcaseCenterDetail = openTestcaseCenterDetail;
window.closeTestcaseCenterDetail = closeTestcaseCenterDetail;
window.nextTestcaseCenterPage = nextTestcaseCenterPage;
window.prevTestcaseCenterPage = prevTestcaseCenterPage;
