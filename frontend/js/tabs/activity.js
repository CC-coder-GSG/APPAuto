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
let activityUnseenTopCount = 0;
let activityBatchTimer = null;
let activityPendingBatchCount = 0;

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
    requirement: 'Requirement',
    feedback: 'Feedback',
    field_test: 'Field Test',
    retest: 'Retest',
    admin: 'Admin',
  };
  return map[value] || value || 'Activity';
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
  return date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
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

function getRangeDays() { return Number(document.getElementById('activityRangeDays')?.value || 1); }

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
  select.innerHTML = '<option value="0">All Users</option>' + users.map((user) => `<option value="${user.id}">${user.display_name || user.username}</option>`).join('');
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
    highlightWrap.innerHTML = '<div class="muted" style="padding:10px 0;">No key activity in current range.</div>';
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
  text.innerText = `Page ${state.page}/${pages}, Total ${state.total}`;
  prev.disabled = state.page <= 1;
  next.disabled = state.page >= pages;
}

function renderFeed(feed) {
  const wrap = document.getElementById('activityFeedList');
  const totalEl = document.getElementById('activityFeedMeta');
  if (!wrap || !totalEl) return;

  const items = feed?.items || [];
  state.total = Number(feed?.total || 0);
  totalEl.innerText = `Total ${state.total}`;

  if (!items.length) {
    wrap.innerHTML = '<div class="muted" style="padding:18px 0; text-align:center;">No activity under current filter.</div>';
    renderPagination();
    return;
  }

  wrap.innerHTML = items.map((item) => {
    const level = levelStyle(item.level);
    const module = moduleBadgeStyle(item.module);
    const targetLine = [item.target_no, item.target_title].filter(Boolean).join(' / ');
    return `
      <div class="activity-feed-item" data-activity-id="${item.id}" onclick="openActivityItem(${item.id}, '${item.module}', ${item.target_id || 0})">
        <div class="row" style="justify-content:space-between; align-items:flex-start; gap:12px; margin:0;">
          <div style="min-width:0; flex:1;">
            <div class="row" style="gap:8px; align-items:center; margin:0 0 6px 0; flex-wrap:wrap;">
              <span class="badge" style="border-radius:999px; background:${module.bg}; color:${module.color}; border:1px solid ${module.border};">${moduleZh(item.module)}</span>
              <span class="badge" style="border-radius:999px; background:${level.bg}; color:${level.color}; border:none;">${item.action_text || item.action || '-'}</span>
              <span class="muted" style="font-size:12px;">${formatShanghaiTime(item.created_at)}</span>
              <span class="muted" style="font-size:12px;">${item.actor_name || 'System'}</span>
            </div>
            <div style="font-weight:700; color:#0f172a; line-height:1.55;">${item.summary || '-'}</div>
            ${targetLine ? `<div class="muted" style="margin-top:4px; line-height:1.55;">${targetLine}</div>` : ''}
            ${item.detail ? `<div class="muted" style="margin-top:4px; line-height:1.55;">${item.detail}</div>` : ''}
          </div>
          <button class="secondary" onclick="event.stopPropagation(); openActivityItem(${item.id}, '${item.module}', ${item.target_id || 0})">View</button>
        </div>
      </div>
    `;
  }).join('');

  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    wrap.querySelectorAll('.activity-feed-item[data-activity-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'activity_event', key: el.getAttribute('data-activity-id'), tone: 'blue', hoverDelayMs: 420 });
    });
  }

  renderPagination();
}

function isActivityNearTop() {
  const first = document.querySelector('#activityFeedList .activity-feed-item[data-activity-id]');
  if (!first) return true;
  const rect = first.getBoundingClientRect();
  return rect.top >= 0 && rect.top <= Math.max(260, Math.round(window.innerHeight * 0.45));
}

const _activityTopBannerCount = { value: 0 };

function updateActivityTopNotice() {
  // Legacy DOM notice
  const el = document.getElementById('activityTopNotice');
  if (el) {
    if (activityUnseenTopCount > 0) {
      el.innerText = `有 ${activityUnseenTopCount} 条新活动`;
      el.classList.remove('hidden');
    } else {
      el.classList.add('hidden');
    }
  }
  // New position-aware banner
  if (!window.OmniQASSE?.showPositionBanner) return;
  _activityTopBannerCount.value = activityUnseenTopCount;
  if (activityUnseenTopCount <= 0) return;
  const feedList = document.getElementById('activityFeedList');
  if (!feedList) return;
  window.OmniQASSE.showPositionBanner({
    scrollContainer: feedList.closest('[style*="overflow"], .card') || feedList.parentElement,
    anchorEl: feedList,
    position: 'top',
    countRef: _activityTopBannerCount,
    labelFn: (n) => `⬆ 有 ${n} 条新活动，点击查看`,
    onClickScroll: () => {
      activityUnseenTopCount = 0;
      _activityTopBannerCount.value = 0;
      revealActivityRealtimeNew();
    },
    bannerId: 'activity-new',
  });
}

function clearActivityTopNotice() {
  activityUnseenTopCount = 0;
  _activityTopBannerCount.value = 0;
  updateActivityTopNotice();
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
  clearActivityTopNotice();
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
  window.showMessage && window.showMessage('Summary pushed.', 'success');
}

function buildTimelineActionBar() {
  const detailButton = (() => {
    if (!timelineState.targetId) return '';
    if (timelineState.targetType === 'feedback') {
      return `<button class="secondary" onclick="showTab('feedback'); openFeedbackDetail(${timelineState.targetId}); closeAuditTimelineModal();">Open Detail</button>`;
    }
    if (timelineState.targetType === 'field_test') {
      return `<button class="secondary" onclick="showTab('field-test'); openFieldTestDetail(${timelineState.targetId}); closeAuditTimelineModal();">Open Detail</button>`;
    }
    return '';
  })();

  return `
    <label style="display:flex; align-items:center; gap:6px; color:#475569; font-size:13px; cursor:pointer; margin-right:8px;">
      <input type="checkbox" ${timelineState.importantOnly ? 'checked' : ''} onchange="window.OmniQAActivityTab.toggleAuditTimelineImportantOnly(this.checked)">
      Important only
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
    body.innerHTML = '<div class="muted" style="padding:12px 0;">No timeline records.</div>';
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
            <span class="badge" style="border-radius:999px; background:${level.bg}; color:${level.color}; border:none;">${item.level === 'critical' ? 'Critical' : item.level === 'important' ? 'Important' : 'Normal'}</span>
          </div>
          <div class="muted" style="font-size:12px; margin-top:4px;">${item.actor_name || 'System'} / ${formatShanghaiTime(item.created_at)}</div>
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
    window.showMessage && window.showMessage('Missing target id.', 'error');
    return;
  }
  let url = '';
  let title = customTitle;
  if (targetType === 'bug') {
    url = `/bugs/${targetId}/timeline`;
    title = title || 'Bug Timeline';
  } else if (targetType === 'requirement') {
    url = `/requirements/${targetId}/timeline`;
    title = title || 'Requirement Timeline';
  } else if (targetType === 'feedback') {
    url = `/feedbacks/${targetId}/timeline`;
    title = title || 'Feedback Timeline';
  } else if (targetType === 'field_test') {
    url = `/field-tests/${targetId}/timeline`;
    title = title || 'Field Test Timeline';
  } else {
    window.showMessage && window.showMessage('Timeline not supported for this type.', 'error');
    return;
  }
  try {
    const items = await (await api(url)).json();
    renderTimelineModal(title, items || [], targetType, targetId);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || 'Load timeline failed.', 'error');
  }
}

export async function openActivityItem(_id, module, targetId) {
  if (!targetId) {
    window.showMessage && window.showMessage('No timeline target for this item.', 'error');
    return;
  }
  if (module === 'bug') return openAuditTimelineModal('bug', targetId, 'Bug Timeline');
  if (module === 'requirement' || module === 'retest') return openAuditTimelineModal('requirement', targetId, 'Requirement Timeline');
  if (module === 'feedback') return openAuditTimelineModal('feedback', targetId, 'Feedback Timeline');
  if (module === 'field_test') return openAuditTimelineModal('field_test', targetId, 'Field Test Timeline');
  window.showMessage && window.showMessage('Open timeline not supported.', 'error');
}

window.OmniQAActivityTab = {
  loadActivityBoard,
  setActivityRange,
  nextActivityPage,
  prevActivityPage,
  pushActivitySummary,
  revealActivityRealtimeNew,
  openAuditTimelineModal,
  closeAuditTimelineModal,
  openActivityItem,
  toggleAuditTimelineImportantOnly,
};

export async function revealActivityRealtimeNew() {
  clearActivityTopNotice();
  await loadActivityBoard();
  const first = document.querySelector('#activityFeedList .activity-feed-item[data-activity-id]');
  if (first && window.OmniQASSE && typeof window.OmniQASSE.pulseBoundaryGlow === 'function') {
    window.OmniQASSE.pulseBoundaryGlow(first, 'purple');
  }
}

function bindActivitySSE() {
  if (activitySseBound) return;
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;

  const flushActivityBatch = () => {
    activityBatchTimer = null;
    const tab = document.getElementById('tab-activity');
    if (!tab || tab.classList.contains('hidden')) {
      activityPendingBatchCount = 0;
      return;
    }

    const batchCount = activityPendingBatchCount;
    activityPendingBatchCount = 0;
    if (batchCount <= 0) return;

    if (!isActivityNearTop()) {
      activityUnseenTopCount += batchCount;
      updateActivityTopNotice();
      return;
    }

    loadActivityBoard().then(() => {
      const first = document.querySelector('#activityFeedList .activity-feed-item[data-activity-id]');
      if (first && window.OmniQASSE && typeof window.OmniQASSE.pulseBoundaryGlow === 'function') {
        window.OmniQASSE.pulseBoundaryGlow(first, 'purple');
      }
    }).catch(() => {});
  };

  window.OmniQASSE.subscribe('activity_created', () => {
    activityPendingBatchCount += 1;
    if (activityBatchTimer) clearTimeout(activityBatchTimer);
    activityBatchTimer = setTimeout(flushActivityBatch, 320);
  });

  activitySseBound = true;
}

bindActivitySSE();
