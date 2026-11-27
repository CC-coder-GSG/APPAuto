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
    requirement: '需求',
    feedback: '反馈',
    field_test: '外业测试',
    retest: '复测',
    admin: '管理',
  };
  return map[value] || value || '动态';
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
  select.innerHTML = '<option value="0">全部人员</option>' + users.map((user) => `<option value="${user.id}">${user.display_name || user.username}</option>`).join('');
  select.value = Array.from(select.options).some((opt) => opt.value === String(savedActorId)) ? String(savedActorId) : '0';
}

// 模块 → 圆点色（与动态流左侧标记一致）
const MODULE_DOT = { bug: '#e34948', requirement: '#2a78d6', feedback: '#1baf7a', field_test: '#eda100', retest: '#6250d6', admin: '#a8a29e' };

function renderSummary(summary) {
  const wrap = document.getElementById('activitySummaryCards');
  const highlightWrap = document.getElementById('activityHighlights');
  if (!wrap || !highlightWrap) return;

  const rangeEl = document.getElementById('activitySummaryRange');
  if (rangeEl) rangeEl.textContent = `近 ${getRangeDays()} 天`;

  const cards = summary?.cards || [];
  const max = Math.max(1, ...cards.map((c) => Number(c.count) || 0));
  wrap.innerHTML = cards.length
    ? `<ul class="act-stats">${cards.map((card) => `
        <li><span class="act-stat-k">${esc(card.label)}</span>
          <span class="act-stat-bar"><i style="width:${Math.max(4, Math.round((Number(card.count) || 0) / max * 100))}%"></i></span>
          <b>${Number(card.count) || 0}</b></li>`).join('')}</ul>`
    : '<div class="act-empty-s">当前范围内暂无可统计的动作</div>';

  const highlights = summary?.highlights || [];
  if (!highlights.length) {
    highlightWrap.innerHTML = '<div class="act-empty-s">当前时间范围内暂无关键动态</div>';
    return;
  }
  highlightWrap.innerHTML = `<ul class="act-hl">${highlights.map((item) => {
    const p = shanghaiParts(item.created_at);
    const clickable = item.target_id ? ` role="button" tabindex="0" onclick="openActivityItem(${Number(item.id) || 0}, '${esc(item.module)}', ${Number(item.target_id) || 0})" onkeydown="if(event.key==='Enter'){this.click()}"` : '';
    return `<li class="activity-highlight-item${item.target_id ? ' is-link' : ''}"${clickable}>
      <span class="act-dot" style="--dot:${MODULE_DOT[item.module] || MODULE_DOT.admin}"></span>
      <span class="act-hl-text">${esc(item.summary || item.action_text || '-')}</span>
      <time>${esc(p.day ? `${p.day.slice(5)} ${p.time}` : p.time)}</time>
    </li>`;
  }).join('')}</ul>`;
}

function syncRangeSeg() {
  const days = String(getRangeDays());
  document.querySelectorAll('#activityRangeSeg [data-act-days]').forEach((btn) => {
    const on = btn.getAttribute('data-act-days') === days;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function bindActivityUi() {
  const seg = document.getElementById('activityRangeSeg');
  if (!seg || seg.dataset.bound) return;
  seg.dataset.bound = '1';
  seg.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-act-days]');
    if (!btn) return;
    setActivityRange(Number(btn.getAttribute('data-act-days'))).catch((err) => window.showMessage && window.showMessage(err.message || '加载动态失败', 'error'));
  });
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
  totalEl.innerText = `共 ${state.total} 条 · 点击一条查看对象时间线`;

  if (!items.length) {
    wrap.innerHTML = `<div class="atl-empty act-empty"><svg class="atl-empty-ic" aria-hidden="true"><use href="#i-activity"/></svg>
      <strong>暂无动态</strong><span>换个时间范围或清空筛选条件试试。</span></div>`;
    renderPagination();
    return;
  }

  const groups = [];
  items.forEach((item) => {
    const p = shanghaiParts(item.created_at);
    let g = groups[groups.length - 1];
    if (!g || g.day !== p.day) { g = { day: p.day, rows: [] }; groups.push(g); }
    g.rows.push({ item, time: p.time });
  });
  wrap.innerHTML = groups.map((g) => `
    <section class="act-day">
      <h4 class="act-day-label">${esc(dayLabel(g.day))}<span>${g.rows.length} 条</span></h4>
      <ol class="act-list">${g.rows.map(({ item, time }) => {
        const lv = item.level === 'critical' ? 'critical' : (isImportantItem(item) ? 'important' : 'info');
        const targetLine = [item.target_no, item.target_title].filter(Boolean).join(' · ');
        const actor = item.actor_name || '系统';
        const open = `openActivityItem(${Number(item.id) || 0}, '${esc(item.module)}', ${Number(item.target_id) || 0})`;
        return `
        <li class="activity-feed-item act-row act-row--${lv}" data-activity-id="${item.id}" role="button" tabindex="0"
            onclick="${open}" onkeydown="if(event.key==='Enter'){${open}}">
          <time class="act-time">${esc(time)}</time>
          <span class="act-dot" style="--dot:${MODULE_DOT[item.module] || MODULE_DOT.admin}" aria-hidden="true"></span>
          <div class="act-body">
            <div class="act-line1">
              <span class="act-mod">${esc(moduleZh(item.module))}</span>
              <span class="act-action">${esc(item.action_text || item.action || '-')}</span>
              ${lv === 'critical' ? '<span class="st-pill st-pill--red">关键</span>' : lv === 'important' ? '<span class="st-pill st-pill--soft st-pill--amber">重要</span>' : ''}
            </div>
            <div class="act-summary">${esc(item.summary || '-')}</div>
            ${(targetLine || item.detail) ? `<div class="act-sub">${targetLine ? `<span>${esc(targetLine)}</span>` : ''}${item.detail ? `<span>${esc(item.detail)}</span>` : ''}</div>` : ''}
          </div>
          <span class="act-actor" title="操作人"><span class="fbk-avatar" aria-hidden="true">${esc(actor.slice(0, 1).toUpperCase())}</span>${esc(actor)}</span>
          <svg class="ui-ic act-go" aria-hidden="true"><use href="#i-chevron-right"/></svg>
        </li>`;
      }).join('')}</ol>
    </section>`).join('');

  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    window.OmniQASSE.releaseAttention?.();
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
  bindActivityUi();
  syncRangeSeg();
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
  window.showMessage && window.showMessage('动态摘要已推送', 'success');
}

const DETAIL_DRAWER = { feedback: 'feedback-detail', field_test: 'field-test-detail' };
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const WEEK_ZH = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function isImportantItem(item) {
  return !!(item.important || item.level === 'important' || item.level === 'critical');
}

// 上海时区下的 { day: 'YYYY-MM-DD', time: 'HH:mm' }
function shanghaiParts(value) {
  if (!value) return { day: '', time: '-' };
  const text = String(value).trim();
  // 后端用 local_now()（服务器上海本地时间）存无时区时间戳，按 +08:00 解析；旧代码补 Z 当 UTC 会晚 8 小时
  const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(text) ? text : `${text.replace(' ', 'T')}+08:00`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return { day: '', time: text };
  const parts = Object.fromEntries(new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(date).map((p) => [p.type, p.value]));
  return { day: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` };
}

function dayLabel(day) {
  if (!day) return '时间未知';
  const today = shanghaiParts(new Date().toISOString()).day;
  const yesterday = shanghaiParts(new Date(Date.now() - 86400000).toISOString()).day;
  const [y, m, d] = day.split('-').map(Number);
  const week = WEEK_ZH[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  if (day === today) return `今天 · ${m}月${d}日`;
  if (day === yesterday) return `昨天 · ${m}月${d}日`;
  return `${day} · ${week}`;
}

function detailAlreadyOpen() {
  const key = DETAIL_DRAWER[timelineState.targetType];
  if (!key) return false;
  const root = document.querySelector(`.sd-root[data-sd-key="${key}"]`);
  return !!root && !root.hidden && Number(root.dataset.recordId) === Number(timelineState.targetId);
}

function buildTimelineActionBar() {
  const all = timelineState.items || [];
  const importantCount = all.filter(isImportantItem).length;
  const seg = (on, label, count, value) => `<button type="button" class="atl-seg${on ? ' is-on' : ''}" aria-pressed="${on}"
      onclick="window.OmniQAActivityTab.toggleAuditTimelineImportantOnly(${value})">${label}<span class="atl-seg-n">${count}</span></button>`;
  let detailButton = '';
  if (timelineState.targetId && !detailAlreadyOpen()) {
    if (timelineState.targetType === 'feedback') {
      detailButton = `<button type="button" class="secondary atl-detail" onclick="closeAuditTimelineModal(); showTab('feedback'); openFeedbackDetail(${timelineState.targetId});">打开详情</button>`;
    } else if (timelineState.targetType === 'field_test') {
      detailButton = `<button type="button" class="secondary atl-detail" onclick="closeAuditTimelineModal(); showTab('field-test'); openFieldTestDetail(${timelineState.targetId});">打开详情</button>`;
    }
  }
  return `
    <div class="atl-segs" role="group" aria-label="筛选记录">
      ${seg(!timelineState.importantOnly, '全部', all.length, false)}
      ${seg(timelineState.importantOnly, '仅看重要', importantCount, true)}
    </div>
    ${detailButton}
  `;
}

function renderTimelineItems() {
  const body = document.getElementById('auditTimelineBody');
  if (!body) return;
  const items = timelineState.importantOnly
    ? (timelineState.items || []).filter(isImportantItem)
    : (timelineState.items || []);

  if (!items.length) {
    body.innerHTML = `<div class="atl-empty"><svg class="atl-empty-ic" aria-hidden="true"><use href="#i-clock"/></svg>
      <strong>${timelineState.importantOnly ? '没有重要记录' : '暂无操作记录'}</strong>
      <span>${timelineState.importantOnly ? '切换到「全部」可查看所有操作。' : '该对象还没有被记录的操作。'}</span></div>`;
    return;
  }

  const groups = [];
  items.forEach((item) => {
    const p = shanghaiParts(item.created_at);
    let g = groups[groups.length - 1];
    if (!g || g.day !== p.day) { g = { day: p.day, rows: [] }; groups.push(g); }
    g.rows.push({ item, time: p.time });
  });

  body.innerHTML = groups.map((g) => `
    <section class="atl-day">
      <h4 class="atl-day-label">${esc(dayLabel(g.day))}</h4>
      <ol class="atl-list">
        ${g.rows.map(({ item, time }) => {
          const lv = item.level === 'critical' ? 'critical' : (isImportantItem(item) ? 'important' : 'info');
          const pill = lv === 'critical' ? '<span class="st-pill st-pill--red">关键</span>' : lv === 'important' ? '<span class="st-pill st-pill--soft st-pill--amber">重要</span>' : '';
          const actor = item.actor_name || '系统';
          return `
          <li class="atl-item atl-item--${lv}">
            <time class="atl-time">${esc(time)}</time>
            <span class="atl-dot" aria-hidden="true"></span>
            <div class="atl-content">
              <div class="atl-row1">
                <b class="atl-action">${esc(item.action_text || item.action || '-')}</b>${pill}
                <span class="atl-actor"><span class="fbk-avatar" aria-hidden="true">${esc(actor.slice(0, 1).toUpperCase())}</span>${esc(actor)}</span>
              </div>
              ${item.summary ? `<div class="atl-summary">${esc(item.summary)}</div>` : ''}
              ${item.detail ? `<div class="atl-detail-text">${esc(item.detail)}</div>` : ''}
            </div>
          </li>`;
        }).join('')}
      </ol>
    </section>`).join('');
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
  const sub = document.getElementById('auditTimelineSub');
  if (sub) {
    const first = timelineState.items[0] || {};
    const target = [first.target_no, first.target_title].filter(Boolean).join(' · ');
    sub.innerHTML = `${target ? `<span class="atl-target" title="${esc(target)}">${esc(target)}</span>` : ''}<span class="atl-total">共 ${timelineState.items.length} 条记录</span>`;
  }
  actionBar.innerHTML = buildTimelineActionBar();
  renderTimelineItems();
  openModal(modal);
  const body = document.getElementById('auditTimelineBody');
  if (body) body.scrollTop = 0;
  modal.querySelector('.atl-close')?.focus({ preventScroll: true });
  bindTimelineKeys();
}

let timelineKeysBound = false;
function bindTimelineKeys() {
  if (timelineKeysBound) return;
  timelineKeysBound = true;
  // 捕获阶段处理 Esc：只关时间线，不连带关闭下层的详情抽屉
  window.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const modal = document.getElementById('auditTimelineModal');
    if (!modal || modal.dataset.open !== '1' || modal.classList.contains('hidden')) return;
    const viewer = document.getElementById('entityImageViewer');
    if (viewer && !viewer.hidden) return;
    event.preventDefault();
    event.stopPropagation();
    closeAuditTimelineModal();
  }, true);
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

const TIMELINE_TITLE = {
  bug: 'Bug 时间线',
  requirement: '需求时间线',
  feedback: '反馈时间线',
  field_test: '外业测试时间线',
};

export async function openAuditTimelineModal(targetType, targetId, customTitle = '') {
  if (!targetId) {
    window.showMessage && window.showMessage('缺少要查看的对象编号', 'error');
    return;
  }
  const urls = {
    bug: `/bugs/${targetId}/timeline`,
    requirement: `/requirements/${targetId}/timeline`,
    feedback: `/feedbacks/${targetId}/timeline`,
    field_test: `/field-tests/${targetId}/timeline`,
  };
  const url = urls[targetType];
  if (!url) {
    window.showMessage && window.showMessage('该类型暂不支持查看时间线', 'error');
    return;
  }
  const title = customTitle || TIMELINE_TITLE[targetType];
  try {
    const items = await (await api(url)).json();
    renderTimelineModal(title, items || [], targetType, targetId);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '时间线加载失败', 'error');
  }
}

export async function openActivityItem(_id, module, targetId) {
  if (!targetId) {
    window.showMessage && window.showMessage('这条动态没有可查看的对象', 'error');
    return;
  }
  if (module === 'bug') return openAuditTimelineModal('bug', targetId);
  if (module === 'requirement' || module === 'retest') return openAuditTimelineModal('requirement', targetId);
  if (module === 'feedback') return openAuditTimelineModal('feedback', targetId);
  if (module === 'field_test') return openAuditTimelineModal('field_test', targetId);
  window.showMessage && window.showMessage('该模块暂不支持查看时间线', 'error');
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
