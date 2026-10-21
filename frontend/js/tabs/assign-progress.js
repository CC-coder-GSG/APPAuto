import { api } from '../api.js';
import { state } from '../state.js';
import { escapeHtml, renderPreviewBtn } from '../utils.js?v=20261014-2';

const h = value => escapeHtml(String(value ?? ''));
const opened = new Set();
const details = new Map();
let context = '';
let sequence = 0;
let resizeObserver;
let filters = { search: '', owner: '', status: '' };
const statuses = { wait: '未开始', doing: '进行中', changed: '需求变更', pause: '已暂停', done: '已完成', closed: '已关闭', cancel: '已取消', todo: '待处理', in_progress: '进行中', blocked: '受阻', deferred: '已延期' };
const conclusions = { pending: '未处理', passed: '复测通过', failed: '复测出现问题' };
const bugStatuses = { active: '激活中', resolved: '已解决', closed: '已关闭' };
const time = value => value ? h(String(value).replace('T', ' ').slice(0, 19)) : '待同步';
const badge = (label, tone = '') => `<span class="assign-progress-badge ${tone}">${h(label)}</span>`;
const entityLink = (label, kind, id) => Number(id) > 0 ? `<button type="button" class="assign-progress-link" data-progress-action="preview" data-entity-kind="${kind}" data-entity-id="${Number(id)}">${h(label)}</button>` : h(label);

function currentContext() {
  return `${Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0)}:${Number(document.getElementById('assignMajorSelect')?.value || 0)}`;
}

function params() {
  const [software, major] = currentContext().split(':').map(Number);
  const result = new URLSearchParams();
  if (software) result.set('software_id', software);
  if (major) result.set('major_version_id', major);
  return result;
}

export function resetAssignProgressContext() {
  const next = currentContext();
  if (next === context) return;
  context = next;
  sequence++;
  opened.clear();
  details.clear();
  resizeObserver?.disconnect();
  filters = { search: '', owner: '', status: '' };
  state.assignProgressData = null;
  const area = document.getElementById('assignProgressArea');
  if (area) area.innerHTML = '<div class="muted" role="status">正在加载需求进度…</div>';
}

function matches(req) {
  const query = filters.search.trim().toLocaleLowerCase();
  if (query && !`${req.zentao_req_id} ${req.title}`.toLocaleLowerCase().includes(query)) return false;
  if (filters.owner && String(req.owner_id ?? -1) !== filters.owner) return false;
  if (filters.status === 'unfinished') return !(req.case_completed && req.test_completed);
  if (filters.status === 'retest') return req.test_completed && req.retest_conclusion === 'pending';
  if (filters.status === 'failed') return req.retest_conclusion === 'failed';
  if (filters.status === 'open-bugs') return req.open_bug_count > 0;
  return true;
}

function summaryHtml(data) {
  const s = data.summary || {};
  return [badge(`人员 ${s.owners || 0}`), badge(`需求 ${s.requirements || 0}`),
    badge(`用例待完成 ${s.case_pending || 0}`), badge(`测试待完成 ${s.test_pending || 0}`),
    badge(`待复测 ${s.retest_pending_total || 0}`, 'warning'), badge(`复测出现问题 ${s.retest_failed_total || 0}`, s.retest_failed_total ? 'danger' : ''),
    badge(`未分配 ${s.unassigned_total || 0}`)].join('');
}

function renderBoard(data) {
  const area = document.getElementById('assignProgressArea');
  if (!area) return;
  resizeObserver?.disconnect();
  const ownerOptions = (data.owners || []).map(o => `<option value="${h(o.owner_id ?? 'unassigned')}">${h(o.owner_name)}</option>`).join('');
  area.innerHTML = `<div class="assign-progress-summary">${summaryHtml(data)}</div>
    <div class="assign-progress-toolbar">
      <label>查找需求<input id="assignProgressSearch" type="search" placeholder="需求编号 / 标题" value="${h(filters.search)}"></label>
      <label>负责人<select id="assignProgressOwner"><option value="">全部负责人</option>${ownerOptions}</select></label>
      <label>进度筛选<select id="assignProgressStatus"><option value="">全部需求</option><option value="unfinished">用例或测试未完成</option><option value="retest">待复测</option><option value="failed">复测出现问题</option><option value="open-bugs">存在未闭环 Bug</option></select></label>
      <button type="button" class="secondary" data-progress-action="reload">刷新进度</button>
      <span class="muted" id="assignProgressCount"></span>
    </div>
    <details class="assign-progress-retest-summary"><summary>复测待办汇总（按大版本）</summary><div>${(data.retest_pending_by_major || []).map(r => badge(`${r.major_version_name}：待复测 ${r.pending_retest_count}`, 'warning')).join('') || '<span class="muted">暂无待复测需求</span>'}</div></details>
    <div id="assignProgressNotice" role="status"></div><div id="assignProgressRows"></div>
    <div class="assign-progress-footnote">摘要读取时间：${time(data.loaded_at)}。禅道数据以明细中的同步时间为准，可展开需求后刷新详情。</div>`;
  area.querySelector('#assignProgressOwner').value = filters.owner;
  area.querySelector('#assignProgressStatus').value = filters.status;
  // Keep the expanded content within the viewport even when the summary
  // table scrolls horizontally; each detail list has its own scroll area.
  const resize = () => area.style.setProperty('--assign-progress-detail-width', `${Math.max(0, area.clientWidth - 2)}px`);
  resize();
  resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(area);
  area.oninput = event => {
    if (event.target.id !== 'assignProgressSearch') return;
    filters.search = event.target.value;
    renderRows();
  };
  area.onchange = event => {
    if (event.target.id === 'assignProgressOwner') filters.owner = event.target.value;
    else if (event.target.id === 'assignProgressStatus') filters.status = event.target.value;
    else return;
    renderRows();
  };
  area.onclick = event => {
    const button = event.target.closest('[data-progress-action]');
    if (!button) return;
    const id = Number(button.dataset.reqId);
    switch (button.dataset.progressAction) {
      case 'expand':
        if (opened.has(id)) opened.delete(id); else opened.add(id);
        renderRows();
        break;
      case 'retry': fetchDetail(id); break;
      case 'refresh': fetchDetail(id, { refresh: true }); break;
      case 'reload': loadAssignProgress(); break;
      case 'page': fetchDetail(id, { section: button.dataset.section, page: Number(button.dataset.page) }); break;
      case 'jump': area.querySelector(`#assign-progress-${id}-${button.dataset.section}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); break;
      case 'local-task': openLocalTask(id, Number(button.dataset.taskId)); break;
      case 'preview': {
        const method = { bug: 'openBug', task: 'openTask', story: 'openStory' }[button.dataset.entityKind];
        if (method) window.OmniQAPreview?.[method]?.(Number(button.dataset.entityId));
        break;
      }
    }
  };
  renderRows();
}

function renderRows() {
  const area = document.getElementById('assignProgressRows');
  if (!area) return;
  const data = state.assignProgressData || {};
  let visible = 0;
  const groups = (data.owners || []).map(owner => {
    const reqs = (owner.requirements || []).filter(matches);
    visible += reqs.length;
    if (!reqs.length) return '';
    return `<details class="assign-progress-owner" open><summary>${h(owner.owner_name)} <span class="muted">${reqs.length} 个需求</span></summary>
      <div class="assign-progress-scroll"><table class="assign-progress-table"><thead><tr><th>大版本 / 需求</th><th>用例 / 测试</th><th>复测结论</th><th>关联 Bug</th><th>关联任务</th><th>操作</th></tr></thead><tbody>${reqs.map(r => {
        const sid = Number(String(r.zentao_req_id || '').replace(/\D/g, ''));
        return `<tr class="${r.retest_conclusion === 'failed' ? 'assign-progress-risk' : ''}" data-progress-row="${r.id}">
          <td><small class="muted">${h(r.major_version_name)}</small><div>${entityLink(r.zentao_req_id, 'story', sid)} ${h(r.title)}${renderPreviewBtn('story', sid)}</div></td>
          <td>${badge(r.case_completed ? '用例完成' : '用例未完成', r.case_completed ? 'success' : '')}${badge(r.test_completed ? '测试完成' : '测试未完成', r.test_completed ? 'success' : '')}<small class="muted">用例 ${Number(r.case_count || 0)}</small></td>
          <td>${badge(conclusions[r.retest_conclusion] || '未处理', r.retest_conclusion === 'failed' ? 'danger' : r.retest_conclusion === 'passed' ? 'success' : '')}${r.retest_active_problem_count ? `<small class="assign-progress-danger">有效问题 ${Number(r.retest_active_problem_count)}</small>` : ''}</td>
          <td>共 ${Number(r.bug_count || 0)}<small class="${r.open_bug_count ? 'assign-progress-danger' : 'muted'}">未闭环 ${Number(r.open_bug_count || 0)}</small></td>
          <td>完成 ${Number(r.task_done_count || 0)} / 共 ${Number(r.task_count || 0)}${r.task_overdue_count ? `<small class="assign-progress-warning">逾期 ${Number(r.task_overdue_count)}</small>` : ''}</td>
          <td><button type="button" class="secondary" data-progress-action="expand" data-req-id="${r.id}" aria-expanded="${opened.has(r.id)}" aria-controls="assignProgressDetail-${r.id}">${opened.has(r.id) ? '收起详情' : '展开详情'}</button></td></tr>
          <tr ${opened.has(r.id) ? '' : 'hidden'}><td colspan="6" class="assign-progress-detail-cell"><div id="assignProgressDetail-${r.id}" class="assign-progress-detail" aria-live="polite"></div></td></tr>`;
      }).join('')}</tbody></table></div></details>`;
  }).join('');
  area.innerHTML = groups || '<div class="assign-progress-empty">当前筛选条件下暂无需求</div>';
  document.getElementById('assignProgressCount').textContent = `显示 ${visible} / ${data.summary?.requirements || 0} 个需求`;
  for (const id of opened) {
    if (!document.getElementById(`assignProgressDetail-${id}`)) continue;
    if (details.has(id)) renderDetail(id); else fetchDetail(id);
  }
}

function action(label, actionName, id, attrs = '') {
  return `<button type="button" class="secondary" data-progress-action="${actionName}" data-req-id="${id}" ${attrs}>${label}</button>`;
}

function pager(id, section, list, busy) {
  const pages = Math.max(1, Math.ceil(list.total / list.page_size));
  return `<div class="assign-progress-pager">${action('上一页', 'page', id, `data-section="${section}" data-page="${list.page - 1}" ${busy || list.page <= 1 ? 'disabled' : ''}`)}<span>第 ${list.page} / ${pages} 页 · 共 ${list.total} 条</span>${action('下一页', 'page', id, `data-section="${section}" data-page="${list.page + 1}" ${busy || list.page >= pages ? 'disabled' : ''}`)}</div>`;
}

function bugRow(b, req, isRetest) {
  const remoteId = Number(String(b.zentao_bug_id || b.bug_id || '').replace(/\D/g, ''));
  const problemKind = b.retest_problem_kind === 'activated' ? '原 Bug 再次激活' : b.retest_problem_kind === 'collected' ? '测后新增问题' : '复测来源记录';
  const actor = b.retest_problem_kind === 'activated' ? b.retest_activated_by_name : b.zentao_opened_by_name;
  return `<tr><td>${entityLink(b.bug_id, 'bug', remoteId)}${renderPreviewBtn('bug', remoteId)}<div>${h(b.zentao_bug_title || '暂无标题')}</div>${b.zentao_deleted ? badge('禅道已删除') : ''}${b.story_mismatch ? badge('需求归属不一致', 'warning') : ''}</td>
    <td>${badge(bugStatuses[b.zentao_live_status] || b.zentao_live_status || '待同步')}${badge(b.closed ? '平台已闭环' : '平台未闭环', b.closed ? 'success' : 'warning')}</td>
    ${isRetest ? `<td>${badge(problemKind, b.retest_problem ? 'danger' : '')}${b.retest_dismissed ? badge('已取消复测结论') : ''}<div>${h(actor || '未知人员')}</div><small>${time(b.retest_problem_kind === 'activated' ? b.retest_activated_at : b.opened_at)}</small></td>` : `<td>${h(b.zentao_assigned_to_name || b.dispatched_to_name || '未指派')}</td>`}
    <td>${h(b.found_minor_version_no || '未知')}<small>${h((b.association_sources || []).join('、'))}</small>${b.major_version_id !== req.major_version_id ? badge(`来自 ${b.major_version_no || '其他版本'}`, 'warning') : ''}</td>
    <td><small>同步：${time(b.synced_at)}</small><small>校对：${time(b.checked_at)}</small></td></tr>`;
}

function taskRow(t, id) {
  const preview = t.source === 'zentao' ? renderPreviewBtn('task', t.id) : action('查看', 'local-task', id, `data-task-id="${t.id}"`);
  const label = t.source === 'zentao' ? entityLink(`#${t.id}`, 'task', t.id) : `<button type="button" class="assign-progress-link" data-progress-action="local-task" data-req-id="${id}" data-task-id="${t.id}">#${Number(t.id)}</button>`;
  const type = { test: '测试', devel: '开发', design: '设计', study: '研究', discuss: '讨论', review: '评审', affair: '事务', misc: '其他' }[t.type] || t.type;
  return `<tr><td>${label}${preview}<div>${h(t.name)}</div>${badge(t.source === 'zentao' ? '禅道任务' : '本地看板')}${t.is_test_task ? badge('测试子任务') : ''}${type ? badge(type) : ''}${t.parent_id ? `<small>父任务 #${Number(t.parent_id)} ${h(t.parent_name || '')}</small>` : ''}</td>
    <td>${badge(statuses[t.status] || t.status || '待同步', t.status === 'done' || t.status === 'closed' ? 'success' : '')}${t.overdue ? badge('已逾期', 'warning') : ''}</td>
    <td>${h(t.assigned_to_name || '未指派')}${t.finished_by_name ? `<small>完成者：${h(t.finished_by_name)}</small>` : ''}</td>
    <td><small>开始：${t.start_date ? time(t.start_date) : '未设置'}</small><small>截止：${t.deadline ? time(t.deadline) : '未设置'}</small></td>
    <td><small>${t.source === 'local' ? '本地更新' : '同步'}：${time(t.source === 'local' ? t.updated_at : t.synced_at)}</small></td></tr>`;
}

function renderDetail(id) {
  const entry = details.get(id);
  const area = document.getElementById(`assignProgressDetail-${id}`);
  if (!entry || !area) return;
  let html = `<div class="assign-progress-detail-toolbar"><b>需求进度详情</b>${action(entry.busy ? '加载中…' : '刷新详情', 'refresh', id, entry.busy ? 'disabled' : '')}</div>`;
  if (entry.busy) html += '<div class="muted" role="status">正在加载，已有数据保留显示…</div>';
  if (entry.error) html += `<div class="assign-progress-alert" role="alert">${h(entry.error)} ${action('重试', 'retry', id, entry.busy ? 'disabled' : '')}</div>`;
  const data = entry.data;
  if (!data) { area.innerHTML = html; return; }
  const req = data.requirement;
  html += `<div class="assign-progress-summary">${badge(conclusions[req.retest_conclusion] || '未处理', req.retest_conclusion === 'failed' ? 'danger' : '')}<span>测试负责人：${h(req.owner_name)}</span></div>
    <div class="assign-progress-records">复测记录：${data.retest_records.length ? data.retest_records.map(r => badge(`${r.user_name} · ${r.passed ? '通过' : '未通过（历史）'}${r.minor_version_name ? ` · ${r.minor_version_name}` : ''} · ${String(r.updated_at || '').replace('T', ' ').slice(0, 19)}`, r.passed ? 'success' : 'danger')).join('') : '<span class="muted">暂无人员提交复测结论</span>'}</div>
    ${(data.warnings || []).length ? `<div class="assign-progress-alert" role="alert">同步未全部完成，以下保留最近已知数据：${data.warnings.map(h).join('；')}</div>` : ''}
    <nav class="assign-progress-detail-nav" aria-label="需求详情区块">${[['bugs', '关联 Bug'], ['retest_bugs', '复测问题'], ['tasks', '关联任务']].map(([key, label]) => action(`${label}（${data[key].total}）`, 'jump', id, `data-section="${key}"`)).join('')}</nav>
    <p class="muted">复测问题与关联 Bug 可能重叠，数量不能相加。复测来源记录保留追溯，有效问题按复测工作台规则判定。</p>`;
  for (const [key, label] of [['bugs', '关联 Bug'], ['retest_bugs', '复测问题'], ['tasks', '关联任务']]) {
    const list = data[key];
    const headers = key === 'tasks' ? ['任务 / 来源', '状态', '指派人 / 完成者', '预计起止', '数据时间'] : ['Bug', '禅道 / 平台状态', key === 'retest_bugs' ? '问题类型 / 人员 / 时间' : '当前处理人', '发现版本 / 关联来源', '数据时间'];
    html += `<section class="assign-progress-section" id="assign-progress-${id}-${key}"><h4>${label} <span class="muted">${list.total}</span></h4>${list.items.length ? `<div class="assign-progress-scroll"><table><thead><tr>${headers.map(t => `<th>${t}</th>`).join('')}</tr></thead><tbody>${list.items.map(item => key === 'tasks' ? taskRow(item, id) : bugRow(item, req, key === 'retest_bugs')).join('')}</tbody></table></div>${pager(id, key, list, entry.busy)}` : `<div class="assign-progress-empty">暂无${label}</div>`}</section>`;
  }
  area.innerHTML = html + `<div class="assign-progress-footnote">明细读取时间：${time(data.loaded_at)}。缺少同步时间表示尚无可确认的同步记录。</div>`;
}

async function fetchDetail(id, options = {}) {
  if (context !== currentContext()) return;
  let entry = details.get(id);
  if (entry?.busy) return;
  if (!entry) { entry = {}; details.set(id, entry); }
  const captured = context;
  const query = params();
  const pages = { bugs: 'bug_page', retest_bugs: 'retest_page', tasks: 'task_page' };
  if (!options.refresh) for (const [key, param] of Object.entries(pages)) query.set(param, String(key === options.section ? options.page : entry.data?.[key]?.page || 1));
  entry.busy = true;
  entry.error = '';
  renderDetail(id);
  try {
    const data = await (await api(`/requirements/admin/progress/${id}${options.refresh ? '/refresh' : ''}?${query}`, options.refresh ? { method: 'POST' } : {})).json();
    if (captured !== currentContext() || details.get(id) !== entry) return;
    entry.data = data;
  } catch (err) {
    if (captured !== currentContext() || details.get(id) !== entry) return;
    entry.error = err.message || '详情加载失败，请重试';
  } finally {
    if (captured === currentContext() && details.get(id) === entry) { entry.busy = false; renderDetail(id); }
  }
  if (options.refresh && !entry.error && captured === currentContext() && details.get(id) === entry) await loadAssignProgress({ preserveDetails: true });
}

function openLocalTask(reqId, taskId) {
  const task = details.get(reqId)?.data?.tasks.items.find(t => t.source === 'local' && t.id === taskId);
  if (!task) return;
  document.getElementById('assignProgressTaskDialog')?.remove();
  const dialog = document.createElement('dialog');
  dialog.id = 'assignProgressTaskDialog';
  dialog.className = 'assign-progress-task-dialog';
  dialog.innerHTML = `<h3>本地看板任务 #${task.id}</h3><h4>${h(task.name)}</h4><p>${h(statuses[task.status] || task.status)} · ${h(task.assigned_to_name || '未指派')}</p><div class="assign-progress-task-description">${h(task.description || '暂无描述')}</div><p>开始：${task.start_date ? time(task.start_date) : '未设置'}<br>截止：${task.deadline ? time(task.deadline) : '未设置'}<br>完成：${task.finished_at ? time(task.finished_at) : '未完成'}</p><button type="button">关闭</button>`;
  dialog.querySelector('button').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

export async function loadAssignProgress(options = {}) {
  resetAssignProgressContext();
  const captured = context;
  const request = ++sequence;
  const notice = document.getElementById('assignProgressNotice');
  if (notice) notice.textContent = '正在刷新进度…';
  try {
    const data = await (await api(`/requirements/admin/progress?${params()}`)).json();
    if (request !== sequence || captured !== currentContext()) return;
    state.assignProgressData = data;
    if (!options.preserveDetails) details.clear();
    const validIds = new Set((data.owners || []).flatMap(o => (o.requirements || []).map(r => r.id)));
    for (const id of opened) if (!validIds.has(id)) { opened.delete(id); details.delete(id); }
    renderBoard(data);
  } catch (err) {
    if (request !== sequence || captured !== currentContext()) return;
    if (!state.assignProgressData) renderBoard({ summary: {}, owners: [] });
    const target = document.getElementById('assignProgressNotice');
    if (target) { target.className = 'assign-progress-alert'; target.textContent = `进度加载失败：${err.message || '请重试'}。可点击“刷新进度”重试。`; }
  }
}

// Kept for older index.html bridges; current UI uses the explicit status selector.
export function toggleAssignProgressPendingOnly() {
  filters.status = document.getElementById('assignOnlyPending')?.checked ? 'unfinished' : '';
  renderBoard(state.assignProgressData || { summary: {}, owners: [] });
}
