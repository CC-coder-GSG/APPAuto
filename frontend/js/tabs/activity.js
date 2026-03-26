import { api } from '../api.js';
import { closeModal, openModal } from '../components/modal.js';

const STORAGE_KEY = 'activityBoardState';

const state = {
  page: 1,
  pageSize: 20,
  total: 0,
};

let restoredOnce = false;
let activitySseBound = false;
let activityRefreshTimer = null;

const timelineState = {
  title: '',
  targetType: '',
  targetId: 0,
  items: [],
  importantOnly: false,
};

function moduleZh(value) {
  const map = {
    bug: 'Bug',
    requirement: '需求',
    feedback: '反馈',
    field_test: '外业测试',
    retest: '复测',
    admin: '管理',
  };
  return map[value] || value || '动态';
}

function moduleBadgeStyle(module) {
  const map = {
    bug: { bg: '#fff7ed', color: '#c2410c', border: '#fdba74' },
    requirement: { bg: '#eff6ff', color: '#1d4ed8', border: '#93c5fd' },
    feedback: { bg: '#ecfeff', color: '#0f766e', border: '#99f6e4' },
    field_test: { bg: '#fefce8', color: '#a16207', border: '#fde68a' },
    retest: { bg: '#eef2ff', color: '#4338ca', border: '#c7d2fe' },
    admin: { bg: '#f8fafc', color: '#475569', border: '#cbd5e1' },
  };
  return map[module] || map.admin;
}

function levelStyle(level) {
  const map = {
    info: { bg: '#f8fafc', color: '#475569' },
    important: { bg: '#dbeafe', color: '#1d4ed8' },
    critical: { bg: '#fee2e2', color: '#b91c1c' },
  };
  return map[level] || map.info;
}

function formatShanghaiTime(value) {
  if (!value) return '-';
  const text = String(value).trim();
  const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(text) ? text : `${text}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
  });
}

function saveLocalState() {
  const payload = {
    page: state.page,
    pageSize: state.pageSize,
    days: document.getElementById('activityRangeDays')?.value || '1',
    targetType: document.getElementById('activityTargetType')?.value || '',
    actorId: document.getElementById('activityActorSelect')?.value || '0',
    keyword: document.getElementById('activityKeyword')?.value || '',
    onlyImportant: !!document.getElementById('activityOnlyImportant')?.checked,
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

function readSavedState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
  } catch {
    return null;
  }
}

function applySavedState(saved) {
  if (!saved) return;
  const setValue = (id, value) => {
    const el = document.getElementById(id);
    if (el && value !== undefined && value !== null) el.value = String(value);
  };
  setValue('activityRangeDays', saved.days || '1');
  setValue('activityTargetType', saved.targetType || '');
  setValue('activityKeyword', saved.keyword || '');
  const importantEl = document.getElementById('activityOnlyImportant');
  if (importantEl) importantEl.checked = !!saved.onlyImportant;
  state.page = Math.max(1, Number(saved.page || 1));
  state.pageSize = Math.max(1, Number(saved.pageSize || 20));
}

function getRangeDays() {
  return Number(document.getElementById('activityRangeDays')?.value || 1);
}

function getFeedFilters() {
  return {
    targetType: document.getElementById('activityTargetType')?.value || '',
    actorId: Number(document.getElementById('activityActorSelect')?.value || 0),
    keyword: (document.getElementById('activityKeyword')?.value || '').trim(),
    onlyImportant: !!document.getElementById('activityOnlyImportant')?.checked,
    days: getRangeDays(),
  };
}

function getCurrentSoftwareId() {
  return Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
}

function fillActorOptions(savedActorId = '0') {
  const select = document.getElementById('activityActorSelect');
  if (!select || !window.currentUser || window.currentUser.role !== 'admin') return;
  const users = window.users || [];
  select.innerHTML = '<option value="0">全部人员</option>' + users.map((user) => `<option value="${user.id}">${user.display_name || user.username}</option>`).join('');
  select.value = Array.from(select.options).some((opt) => opt.value === String(savedActorId)) ? String(savedActorId) : '0';
}

function renderSummary(summary) {
  const wrap = document.getElementById('activitySummaryCards');
  const highlightWrap = document.getElementById('activityHighlights');
  if (!wrap || !highlightWrap) return;
  const cards = summary?.cards || [];
  wrap.innerHTML = cards.map((card) => `
    <div class="metric-card" style="min-width:150px;">
      <div class="metric-title">${card.label}</div>
      <div class="metric-value" style="font-size:26px; color:#0f172a;">${card.count}</div>
    </div>
  `).join('');

  const highlights = summary?.highlights || [];
  if (!highlights.length) {
    highlightWrap.innerHTML = '<div class="muted" style="padding:10px 0;">最近没有关键动态</div>';
    return;
  }
  highlightWrap.innerHTML = highlights.map((item) => `<div class="activity-highlight-item">• ${item.summary}</div>`).join('');
}

function renderPagination() {
  const text = document.getElementById('activityPaginationText');
  const prev = document.getElementById('activityPrevBtn');
  const next = document.getElementById('activityNextBtn');
  if (!text || !prev || !next) return;
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  text.innerText = `第 ${state.page} / ${pages} 页，共 ${state.total} 条`;
  prev.disabled = state.page <= 1;
  next.disabled = state.page >= pages;
}

function renderFeed(feed) {
  const wrap = document.getElementById('activityFeedList');
  const totalEl = document.getElementById('activityFeedMeta');
  if (!wrap || !totalEl) return;
  const items = feed?.items || [];
  state.total = Number(feed?.total || 0);
  totalEl.innerText = `共 ${state.total} 条动态`;

  if (!items.length) {
    wrap.innerHTML = '<div class="muted" style="padding:18px 0; text-align:center;">当前筛选条件下暂无动态</div>';
    renderPagination();
    return;
  }

  wrap.innerHTML = items.map((item) => {
    const level = levelStyle(item.level);
    const module = moduleBadgeStyle(item.module);
    const targetLine = [item.target_no, item.target_title].filter(Boolean).join(' / ');
    return `
      <div class="activity-feed-item" onclick="openActivityItem(${item.id}, '${item.module}', ${item.target_id || 0})">
        <div class="row" style="justify-content:space-between; align-items:flex-start; gap:12px; margin:0;">
          <div style="min-width:0; flex:1;">
            <div class="row" style="gap:8px; align-items:center; margin:0 0 6px 0; flex-wrap:wrap;">
              <span class="badge" style="border-radius:999px; background:${module.bg}; color:${module.color}; border:1px solid ${module.border};">${moduleZh(item.module)}</span>
              <span class="badge" style="border-radius:999px; background:${level.bg}; color:${level.color}; border:none;">${item.action_text || item.action || '-'}</span>
              <span class="muted" style="font-size:12px;">${formatShanghaiTime(item.created_at)}</span>
              <span class="muted" style="font-size:12px;">${item.actor_name || '系统'}</span>
            </div>
            <div style="font-weight:700; color:#0f172a; line-height:1.55;">${item.summary || '-'}</div>
            ${targetLine ? `<div class="muted" style="margin-top:4px; line-height:1.55;">${targetLine}</div>` : ''}
            ${item.detail ? `<div class="muted" style="margin-top:4px; line-height:1.55;">${item.detail}</div>` : ''}
          </div>
          <button class="secondary" onclick="event.stopPropagation(); openActivityItem(${item.id}, '${item.module}', ${item.target_id || 0})">查看</button>
        </div>
      </div>
    `;
  }).join('');
  renderPagination();
}

async function fetchSummary() {
  const { days, onlyImportant, targetType } = getFeedFilters();
  const softwareId = getCurrentSoftwareId();
  const params = new URLSearchParams();
  params.set('days', String(days));
  if (onlyImportant) params.set('only_important', 'true');
  if (targetType) params.append('target_types', targetType);
  if (softwareId) params.set('software_id', String(softwareId));
  return (await (await api(`/admin/activity-summary?${params.toString()}`)).json()) || {};
}

async function fetchFeed() {
  const { targetType, actorId, keyword, onlyImportant, days } = getFeedFilters();
  const softwareId = getCurrentSoftwareId();
  const now = new Date();
  const from = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
  const params = new URLSearchParams();
  params.set('limit', String(state.pageSize));
  params.set('offset', String((state.page - 1) * state.pageSize));
  params.set('date_from', from.toISOString());
  params.set('date_to', now.toISOString());
  if (targetType) params.set('target_type', targetType);
  if (actorId) params.set('actor_id', String(actorId));
  if (keyword) params.set('keyword', keyword);
  if (onlyImportant) params.set('only_important', 'true');
  if (softwareId) params.set('software_id', String(softwareId));
  return (await (await api(`/admin/activity-feed?${params.toString()}`)).json()) || { items: [], total: 0 };
}

export async function loadActivityBoard() {
  const saved = readSavedState();
  if (!restoredOnce) {
    applySavedState(saved);
    restoredOnce = true;
  }
  fillActorOptions((document.getElementById('activityActorSelect')?.value || saved?.actorId || '0'));
  const [summary, feed] = await Promise.all([fetchSummary(), fetchFeed()]);
  renderSummary(summary);
  renderFeed(feed);
  saveLocalState();
}

export function setActivityRange(days) {
  const select = document.getElementById('activityRangeDays');
  if (select) select.value = String(days);
  state.page = 1;
  return loadActivityBoard();
}

export async function nextActivityPage() {
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  if (state.page >= pages) return;
  state.page += 1;
  await loadActivityBoard();
}

export async function prevActivityPage() {
  if (state.page <= 1) return;
  state.page -= 1;
  await loadActivityBoard();
}

export async function pushActivitySummary() {
  const { targetType, onlyImportant, days } = getFeedFilters();
  const softwareId = getCurrentSoftwareId();
  const hours = Math.max(1, days * 24);
  await api('/admin/push-activity-summary', {
    method: 'POST',
    headers: window.H,
    body: {
      hours,
      software_id: softwareId || null,
      target_types: targetType ? [targetType] : [],
      only_important: onlyImportant,
    },
  });
  window.showMessage && window.showMessage('最近动态摘要已推送', 'success');
}

function buildTimelineActionBar() {
  const detailButton = (() => {
    if (!timelineState.targetId) return '';
    if (timelineState.targetType === 'feedback') {
      return `<button class="secondary" onclick="showTab('feedback'); openFeedbackDetail(${timelineState.targetId}); closeAuditTimelineModal();">打开详情</button>`;
    }
    if (timelineState.targetType === 'field_test') {
      return `<button class="secondary" onclick="showTab('field-test'); openFieldTestDetail(${timelineState.targetId}); closeAuditTimelineModal();">打开详情</button>`;
    }
    return '';
  })();

  return `
    <label style="display:flex; align-items:center; gap:6px; color:#475569; font-size:13px; cursor:pointer; margin-right:8px;">
      <input type="checkbox" ${timelineState.importantOnly ? 'checked' : ''} onchange="window.OmniQAActivityTab.toggleAuditTimelineImportantOnly(this.checked)">
      仅看关键节点
    </label>
    ${detailButton}
  `;
}

function renderTimelineItems() {
  const body = document.getElementById('auditTimelineBody');
  if (!body) return;
  const items = timelineState.importantOnly
    ? (timelineState.items || []).filter((item) => item.important || item.level === 'important' || item.level === 'critical')
    : (timelineState.items || []);

  if (!items.length) {
    body.innerHTML = '<div class="muted" style="padding:12px 0;">暂无时间线记录</div>';
    return;
  }

  body.innerHTML = items.map((item) => {
    const level = levelStyle(item.level);
    return `
      <div class="timeline-item">
        <div class="timeline-dot" style="background:${level.color};"></div>
        <div class="timeline-content">
          <div class="row" style="gap:8px; align-items:center; margin:0; flex-wrap:wrap;">
            <div style="font-weight:700; color:#0f172a;">${item.action_text || item.action || '-'}</div>
            <span class="badge" style="border-radius:999px; background:${level.bg}; color:${level.color}; border:none;">${item.level === 'critical' ? '关键' : item.level === 'important' ? '重要' : '普通'}</span>
          </div>
          <div class="muted" style="font-size:12px; margin-top:4px;">${item.actor_name || '系统'} / ${formatShanghaiTime(item.created_at)}</div>
          <div style="margin-top:6px; line-height:1.6; color:#334155;">${item.summary || ''}</div>
          ${item.detail ? `<div class="muted" style="margin-top:4px; line-height:1.6;">${item.detail}</div>` : ''}
        </div>
      </div>
    `;
  }).join('');
}

function renderTimelineModal(title, items, targetType = '', targetId = 0) {
  const modal = document.getElementById('auditTimelineModal');
  const titleEl = document.getElementById('auditTimelineTitle');
  const actionBar = document.getElementById('auditTimelineActionBar');
  if (!modal || !titleEl || !actionBar) return;
  timelineState.title = title;
  timelineState.targetType = targetType;
  timelineState.targetId = targetId;
  timelineState.items = items || [];
  timelineState.importantOnly = false;
  titleEl.innerText = title;
  actionBar.innerHTML = buildTimelineActionBar();
  renderTimelineItems();
  openModal(modal);
}

export function toggleAuditTimelineImportantOnly(checked) {
  timelineState.importantOnly = !!checked;
  const actionBar = document.getElementById('auditTimelineActionBar');
  if (actionBar) actionBar.innerHTML = buildTimelineActionBar();
  renderTimelineItems();
}

export function closeAuditTimelineModal() {
  closeModal('auditTimelineModal');
}

export async function openAuditTimelineModal(targetType, targetId, customTitle = '') {
  if (!targetId) {
    window.showMessage && window.showMessage('缺少目标对象，无法加载时间线', 'error');
    return;
  }
  let url = '';
  let title = customTitle;
  if (targetType === 'bug') {
    url = `/bugs/${targetId}/timeline`;
    title = title || 'Bug 时间线';
  } else if (targetType === 'requirement') {
    url = `/requirements/${targetId}/timeline`;
    title = title || '需求时间线';
  } else if (targetType === 'feedback') {
    url = `/feedbacks/${targetId}/timeline`;
    title = title || '反馈时间线';
  } else if (targetType === 'field_test') {
    url = `/field-tests/${targetId}/timeline`;
    title = title || '外业测试时间线';
  } else {
    window.showMessage && window.showMessage('当前对象暂不支持时间线查看', 'error');
    return;
  }
  try {
    const items = await (await api(url)).json();
    renderTimelineModal(title, items || [], targetType, targetId);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载时间线失败', 'error');
  }
}

export async function openActivityItem(_id, module, targetId) {
  if (!targetId) {
    window.showMessage && window.showMessage('这条动态没有可查看的对象时间线', 'error');
    return;
  }
  if (module === 'bug') return openAuditTimelineModal('bug', targetId, 'Bug 时间线');
  if (module === 'requirement' || module === 'retest') return openAuditTimelineModal('requirement', targetId, '需求时间线');
  if (module === 'feedback') return openAuditTimelineModal('feedback', targetId, '反馈时间线');
  if (module === 'field_test') return openAuditTimelineModal('field_test', targetId, '外业测试时间线');
  window.showMessage && window.showMessage('当前动态暂不支持打开时间线', 'error');
}

window.OmniQAActivityTab = {
  loadActivityBoard,
  setActivityRange,
  nextActivityPage,
  prevActivityPage,
  pushActivitySummary,
  openAuditTimelineModal,
  closeAuditTimelineModal,
  openActivityItem,
  toggleAuditTimelineImportantOnly,
};

function bindActivitySSE() {
  if (activitySseBound) return;
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;
  window.OmniQASSE.subscribe('activity_created', () => {
    const tab = document.getElementById('tab-activity');
    if (!tab || tab.classList.contains('hidden')) return;
    if (activityRefreshTimer) clearTimeout(activityRefreshTimer);
    activityRefreshTimer = setTimeout(() => {
      loadActivityBoard().catch(() => {});
    }, 700);
  });
  activitySseBound = true;
}

bindActivitySSE();
