import { api } from '../api.js';
import { state } from '../state.js';
import { renderBugLink, escapeHtml } from '../utils.js';
import { openDrawer, getDrawer, closeDrawer } from '../components/side-drawer.js';
import { wireImageViewer } from '../components/image-viewer.js';

// 2026-11-25 排版 v2：列表改为可点击的行卡片；新建反馈 / 反馈详情都在右侧抽屉里完成，
// 不再把详情渲染到页面底部。附件经 api() 取 blob（下载接口需要 Authorization 头，
// 直接 <img src> / <a href> 会 401）。
let currentPage = 1;
let currentPageSize = 10;
let feedbackSseBound = false;
let activeFeedbackId = 0;
let attachmentUrls = [];
let createFiles = [];
const _feedbackTopBannerCount = { value: 0 };

const esc = (v) => escapeHtml(v == null ? '' : String(v));
const STATUS = {
  pending: { zh: '待处理', tone: 'amber' },
  processing: { zh: '处理中', tone: 'blue' },
  resolved: { zh: '已处理', tone: 'green' },
  closed: { zh: '已关闭', tone: 'gray' },
};
const STATUS_ORDER = ['pending', 'processing', 'resolved', 'closed'];
const ic = (name, cls = 'ui-ic') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

function feedbackStatusZh(v) {
  return STATUS[v]?.zh || v;
}

function buildStatusBadge(status) {
  const s = STATUS[status] || STATUS.pending;
  return `<span class="st-pill st-pill--${s.tone}">${s.zh}</span>`;
}

function isAdmin() {
  return !!(window.currentUser && window.currentUser.role === 'admin');
}

function selectedMajorId() {
  return Number(document.getElementById('feedbackMajorSelect')?.value || 0);
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let val = bytes;
  let idx = 0;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx += 1;
  }
  return `${val.toFixed(val >= 10 ? 0 : 1)} ${units[idx]}`;
}

function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function relTime(iso) {
  if (!iso) return '';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(diff)) return '';
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return fmtTime(iso).slice(0, 10);
}

function avatar(name) {
  const n = String(name || '').trim();
  if (!n) return '<span class="fbk-avatar fbk-avatar--empty" aria-hidden="true">?</span>';
  return `<span class="fbk-avatar" aria-hidden="true">${esc(n.slice(0, 1).toUpperCase())}</span>`;
}

function fileKind(ext = '') {
  const e = String(ext).toLowerCase();
  if (['.doc', '.docx'].includes(e)) return 'DOC';
  if (['.xls', '.xlsx', '.csv'].includes(e)) return 'XLS';
  if (e === '.pdf') return 'PDF';
  if (['.zip', '.rar', '.7z'].includes(e)) return 'ZIP';
  if (['.txt', '.md', '.log'].includes(e)) return 'TXT';
  return (e.replace('.', '').slice(0, 4) || 'FILE').toUpperCase();
}

function versionOptions(type, parentId, selected) {
  const rows = (window.versions || []).filter((v) => v.version_type === type && (type !== 'minor' || Number(v.parent_id) === Number(parentId)));
  return rows.map((v) => `<option value="${v.id}" ${Number(selected) === Number(v.id) ? 'selected' : ''}>${esc(v.version_no)}</option>`).join('');
}

export async function loadFeedbackMinorOptions() {
  const majorId = selectedMajorId();
  const sel = document.getElementById('feedbackMinorSelect');
  const all = (window.versions || []).filter((v) => v.version_type === 'minor');
  if (!sel) return;
  if (majorId === 0) {
    if (all.length === 0) {
      sel.innerHTML = "<option value=''>暂无子版本</option>";
      return;
    }
    sel.innerHTML = "<option value='0'>全部小版本</option>" + all.map((v) => `<option value='${v.id}'>${v.version_no}</option>`).join('');
    return;
  }
  const rows = all.filter((v) => Number(v.parent_id) === majorId);
  if (rows.length === 0) {
    sel.innerHTML = "<option value=''>暂无子版本</option>";
    return;
  }
  sel.innerHTML = rows.map((v) => `<option value='${v.id}'>${v.version_no}</option>`).join('');
}

function getListFilters() {
  const keyword = (document.getElementById('feedbackKeyword')?.value || '').trim();
  const status = document.getElementById('feedbackStatusSelect')?.value || '';
  const assigneeId = Number(document.getElementById('feedbackAssigneeSelect')?.value || 0);
  const majorId = selectedMajorId();
  const minorId = Number(document.getElementById('feedbackMinorSelect')?.value || 0);
  const sortBy = document.getElementById('feedbackSortBy')?.value || 'created_at';
  const sortOrder = document.getElementById('feedbackSortOrder')?.value || 'desc';
  const pageSize = Number(document.getElementById('feedbackPageSize')?.value || currentPageSize || 10);
  return { keyword, status, assigneeId, majorId, minorId, sortBy, sortOrder, pageSize };
}

async function listFeedbacksPaged(page = 1) {
  const { keyword, status, assigneeId, majorId, minorId, sortBy, sortOrder, pageSize } = getListFilters();
  const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  currentPageSize = pageSize;
  const params = new URLSearchParams();
  if (keyword) params.set('keyword', keyword);
  if (status) params.set('status', status);
  if (assigneeId) params.set('assignee_id', String(assigneeId));
  if (softwareId) params.set('software_id', String(softwareId));
  if (majorId) params.set('major_version_id', String(majorId));
  if (minorId) params.set('minor_version_id', String(minorId));
  params.set('page', String(page));
  params.set('page_size', String(pageSize));
  params.set('sort_by', sortBy);
  params.set('sort_order', sortOrder);
  const data = await (await api(`/feedbacks/paged?${params.toString()}`)).json();
  return data || { items: [], total: 0, page, page_size: pageSize };
}

function renderPagination(total, page, pageSize) {
  const el = document.getElementById('feedbackPaginationText');
  if (!el) return;
  const pages = Math.max(1, Math.ceil((total || 0) / (pageSize || 10)));
  el.innerText = `第 ${page} / ${pages} 页，共 ${total || 0} 条`;
  const prev = document.getElementById('feedbackPrevBtn');
  const next = document.getElementById('feedbackNextBtn');
  if (prev) prev.disabled = page <= 1;
  if (next) next.disabled = page >= pages;
}

function markActiveRow() {
  document.querySelectorAll('#feedbackTable .feedback-row-card').forEach((el) => {
    el.classList.toggle('is-active', Number(el.dataset.feedbackId) === activeFeedbackId && !!getDrawer('feedback-detail'));
  });
}

function renderFeedbackList(rows) {
  const list = document.getElementById('feedbackTable');
  if (!list) return;
  if (!rows.length) {
    list.innerHTML = `<div class="fbk-empty">${ic('feedback', 'fbk-empty-ic')}<strong>暂无反馈数据</strong><span>调整上方筛选条件，或点击右上角「新建反馈」。</span></div>`;
    return;
  }
  list.innerHTML = rows.map((r) => `
    <div class="fbk-row feedback-row-card" role="listitem" data-feedback-id="${r.id}">
      <button type="button" class="fbk-row-hit" onclick="openFeedbackDetail(${r.id})" aria-label="查看反馈 ${esc(r.feedback_no || '#' + r.id)} 详情"></button>
      <span class="fbk-no${r.feedback_no ? '' : ' is-empty'}">${esc(r.feedback_no || '未编号')}</span>
      <div class="fbk-main">
        <div class="fbk-summary" title="${esc(r.summary)}">${esc(r.summary)}</div>
        <div class="fbk-meta">
          <span class="fbk-ver">${esc(r.major_version_no || '-')} / ${esc(r.minor_version_no || '-')}</span>
          <span>${esc(r.creator_name || '-')} 创建</span>
          <span title="${esc(fmtTime(r.created_at))}">${esc(relTime(r.created_at))}</span>
        </div>
      </div>
      <div class="fbk-assignee">${avatar(r.assignee_name)}<span class="${r.assignee_name ? '' : 'is-empty'}">${esc(r.assignee_name || '未指派')}</span></div>
      <div class="fbk-status">${buildStatusBadge(r.status)}</div>
      <svg class="fbk-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
    </div>
  `).join('');
  markActiveRow();
  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    window.OmniQASSE.releaseAttention?.();
    document.querySelectorAll('.feedback-row-card[data-feedback-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'feedback_task', key: el.getAttribute('data-feedback-id'), tone: 'blue', hoverDelayMs: 420 });
    });
  }
}

export async function loadFeedbackBoard(page = 1) {
  await loadFeedbackMinorOptions();
  const data = await listFeedbacksPaged(page);
  currentPage = data.page || page;
  renderFeedbackList(data.items || []);
  renderPagination(data.total || 0, currentPage, data.page_size || currentPageSize);
}

export async function nextFeedbackPage() {
  await loadFeedbackBoard(currentPage + 1);
}

export async function prevFeedbackPage() {
  if (currentPage <= 1) return;
  await loadFeedbackBoard(currentPage - 1);
}

/* ── 新建反馈（右侧抽屉） ─────────────────────────────── */

function renderCreateFiles() {
  const box = document.getElementById('newFeedbackFileList');
  if (!box) return;
  box.innerHTML = createFiles.map((f, i) => `
    <span class="fbk-file-chip"><span class="fbk-kind">${esc(fileKind((f.name.match(/\.[^.]+$/) || [''])[0]))}</span>
      <span class="fbk-file-name" title="${esc(f.name)}">${esc(f.name)}</span><span class="fbk-file-size">${formatBytes(f.size)}</span>
      <button type="button" class="fbk-x" onclick="window.OmniQAFeedbackTab._removeCreateFile(${i})" aria-label="移除 ${esc(f.name)}">×</button>
    </span>`).join('');
}

function addCreateFiles(list) {
  Array.from(list || []).forEach((f) => {
    if (!createFiles.some((x) => x.name === f.name && x.size === f.size)) createFiles.push(f);
  });
  renderCreateFiles();
}

function bindDropzone(zone, input, onFiles) {
  if (!zone || !input) return;
  input.addEventListener('change', () => { onFiles(input.files); input.value = ''; });
  ['dragenter', 'dragover'].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add('is-over'); }));
  ['dragleave', 'drop'].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove('is-over'); }));
  zone.addEventListener('drop', (e) => onFiles(e.dataTransfer?.files));
}

export function openFeedbackCreate() {
  createFiles = [];
  const filterMajor = selectedMajorId();
  const majors = (window.versions || []).filter((v) => v.version_type === 'major');
  const hasMinor = (id) => (window.versions || []).some((v) => v.version_type === 'minor' && Number(v.parent_id) === Number(id));
  const majorId = filterMajor || Number((majors.find((m) => hasMinor(m.id)) || majors[0])?.id || 0);
  const filterMinor = Number(document.getElementById('feedbackMinorSelect')?.value || 0);
  const d = openDrawer({
    key: 'feedback-create',
    size: 'md',
    head: `<div class="sd-eyebrow">反馈记录</div><h3 class="sd-title">新建反馈</h3>`,
    body: `
      <div class="sd-form">
        <div class="sd-grid2">
          <label class="sd-field"><span class="sd-label">大版本<em>*</em></span><select id="newFeedbackMajor">${versionOptions('major', 0, majorId)}</select></label>
          <label class="sd-field"><span class="sd-label">小版本<em>*</em></span><select id="newFeedbackMinor"></select></label>
        </div>
        <label class="sd-field"><span class="sd-label">反馈编号<small>选填，只填数字</small></span>
          <span class="sd-prefix"><span>f#</span><input id="newFeedbackNoNum" inputmode="numeric" oninput="digitsOnly(this)" placeholder="例如 12"></span></label>
        <label class="sd-field"><span class="sd-label">反馈概览<em>*</em><small id="newFeedbackCount">0 / 500</small></span>
          <textarea id="newFeedbackSummary" rows="6" maxlength="500" placeholder="描述用户遇到的问题：现象、操作步骤、设备与环境……"></textarea></label>
        <div class="sd-field"><span class="sd-label">附件<small>截图、日志等，可多选或拖入</small></span>
          <label class="sd-drop" id="newFeedbackDrop">
            <input id="newFeedbackFiles" type="file" multiple hidden>
            ${ic('upload', 'sd-drop-ic')}<span><b>点击选择文件</b>，或拖到这里</span>
          </label>
          <div id="newFeedbackFileList" class="fbk-file-chips"></div>
        </div>
      </div>`,
    foot: `<button type="button" class="secondary" data-sd-close>取消</button>
      <button type="button" id="newFeedbackSubmit" onclick="createFeedback()">${ic('check')}提交反馈</button>`,
  });
  const mSel = d.body.querySelector('#newFeedbackMajor');
  const nSel = d.body.querySelector('#newFeedbackMinor');
  const fillMinor = () => {
    const opts = versionOptions('minor', mSel.value, Number(mSel.value) === filterMajor ? filterMinor : 0);
    nSel.innerHTML = opts || '<option value="">暂无小版本</option>';
  };
  mSel.addEventListener('change', fillMinor);
  fillMinor();
  const summary = d.body.querySelector('#newFeedbackSummary');
  const count = d.body.querySelector('#newFeedbackCount');
  summary.addEventListener('input', () => { count.textContent = `${summary.value.length} / 500`; });
  bindDropzone(d.body.querySelector('#newFeedbackDrop'), d.body.querySelector('#newFeedbackFiles'), addCreateFiles);
  setTimeout(() => summary.focus({ preventScroll: true }), 60);
}

export async function createFeedback() {
  const noNum = (document.getElementById('newFeedbackNoNum')?.value || '').trim();
  const majorId = Number(document.getElementById('newFeedbackMajor')?.value || selectedMajorId() || 0);
  const minorId = Number(document.getElementById('newFeedbackMinor')?.value || document.getElementById('feedbackMinorSelect')?.value || 0);
  const summary = (document.getElementById('newFeedbackSummary')?.value || '').trim();
  if (!majorId || !minorId) {
    window.showMessage && window.showMessage('请选择反馈版本（大版本/小版本）', 'error');
    return;
  }
  if (!summary) {
    window.showMessage && window.showMessage('反馈概览不能为空', 'error');
    document.getElementById('newFeedbackSummary')?.focus();
    return;
  }
  const btn = document.getElementById('newFeedbackSubmit');
  if (btn) btn.disabled = true;
  try {
    const created = await (await api('/feedbacks', {
      method: 'POST',
      headers: window.H,
      body: { feedback_no_num: noNum || null, major_version_id: majorId, minor_version_id: minorId, summary },
    })).json();
    for (const file of createFiles) {
      const form = new FormData();
      form.append('file', file);
      await api(`/feedbacks/${created.id}/attachments`, { method: 'POST', body: form });
    }
    createFiles = [];
    window.showMessage && window.showMessage('反馈已创建', 'success');
    closeDrawer('feedback-create');
    await loadFeedbackBoard(1);
    const row = document.querySelector(`.feedback-row-card[data-feedback-id='${created.id}']`);
    if (row && window.OmniQASSE?.pulseBoundaryGlow) window.OmniQASSE.pulseBoundaryGlow(row, 'blue');
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ── 附件 ─────────────────────────────────────────────── */

function revokeAttachmentUrls() {
  attachmentUrls.forEach((u) => URL.revokeObjectURL(u));
  attachmentUrls = [];
}

async function attachmentBlobUrl(id) {
  const res = await api(`/feedbacks/attachments/${id}/download`);
  const url = URL.createObjectURL(await res.blob());
  attachmentUrls.push(url);
  return url;
}

async function hydrateAttachmentImages(root) {
  const imgs = [...root.querySelectorAll('img[data-att-id]')];
  await Promise.all(imgs.map(async (img) => {
    try {
      img.src = await attachmentBlobUrl(img.dataset.attId);
    } catch (_) {
      img.dataset.previewImageError = '1';
      img.closest('.fbk-thumb')?.classList.add('is-broken');
    }
  }));
}

export async function downloadFeedbackAttachment(attachmentId, name) {
  try {
    const url = await attachmentBlobUrl(attachmentId);
    const a = document.createElement('a');
    a.href = url;
    a.download = name || 'attachment';
    document.body.appendChild(a);
    a.click();
    a.remove();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '下载失败', 'error');
  }
}

async function uploadFiles(feedbackId, files) {
  const list = Array.from(files || []);
  if (!list.length) return;
  for (const file of list) {
    const form = new FormData();
    form.append('file', file);
    await api(`/feedbacks/${feedbackId}/attachments`, { method: 'POST', body: form });
  }
  window.showMessage && window.showMessage(`已上传 ${list.length} 个附件`, 'success');
  await openFeedbackDetail(feedbackId);
}

export async function uploadFeedbackFiles(feedbackId) {
  const input = document.getElementById('feedbackUploadInput');
  const files = input?.files ? Array.from(input.files) : [];
  if (!files.length) {
    window.showMessage && window.showMessage('请先选择附件', 'error');
    return;
  }
  await uploadFiles(feedbackId, files);
}

export async function deleteFeedbackAttachment(attachmentId, feedbackId) {
  if (!confirm('确定删除该附件吗？')) return;
  await api(`/feedbacks/attachments/${attachmentId}`, { method: 'DELETE' });
  window.showMessage && window.showMessage('附件已删除', 'success');
  await openFeedbackDetail(feedbackId);
}

async function loadBugOptions(keyword = '') {
  const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  const params = new URLSearchParams();
  if (keyword) params.set('keyword', keyword);
  if (softwareId) params.set('software_id', String(softwareId));
  const rows = await (await api(`/feedbacks/bug-options?${params.toString()}`)).json();
  const sel = document.getElementById('feedbackLinkBugId');
  if (!sel) return;
  sel.innerHTML = '<option value="">选择已有 Bug</option>' + rows.map((b) => `<option value="${b.id}">${esc(b.bug_id)}${b.zentao_bug_title ? ' · ' + esc(String(b.zentao_bug_title).slice(0, 24)) : ''}</option>`).join('');
}

/* ── 反馈详情（右侧抽屉） ─────────────────────────────── */

// 与后端 FeedbackService._assert_status_transition 保持一致：不允许的流转直接置灰，避免点了才报错
const STATUS_FLOW = {
  admin: {
    pending: ['processing', 'resolved', 'closed'],
    processing: ['pending', 'resolved', 'closed'],
    resolved: ['processing', 'closed'],
    closed: ['processing'],
  },
  member: {
    pending: ['processing'],
    processing: ['resolved'],
    resolved: ['processing'],
    closed: [],
  },
};

function renderStatusStepper(detail) {
  const admin = isAdmin();
  const statuses = admin ? STATUS_ORDER : STATUS_ORDER.filter((s) => s !== 'closed');
  const allowed = (STATUS_FLOW[admin ? 'admin' : 'member'][detail.status]) || [];
  const cur = STATUS_ORDER.indexOf(detail.status);
  return `<div class="fbk-stepper" role="group" aria-label="反馈状态">${statuses.map((s) => {
    const i = STATUS_ORDER.indexOf(s);
    const current = s === detail.status;
    const blocked = !current && !allowed.includes(s);
    const cls = current ? 'is-current' : `${i < cur ? 'is-done' : ''}${blocked ? ' is-blocked' : ''}`;
    const title = current ? '当前状态' : blocked ? `当前状态不能直接切换为「${STATUS[s].zh}」` : `切换为「${STATUS[s].zh}」`;
    return `<button type="button" class="fbk-step fbk-step--${STATUS[s].tone} ${cls}" ${current ? 'aria-current="step" disabled' : ''}${blocked ? ' disabled' : ''}
      onclick="setFeedbackStatus(${detail.id}, '${s}')" title="${title}">
      <span class="fbk-step-dot"></span>${STATUS[s].zh}</button>`;
  }).join('')}</div>`;
}

const ACTION_ZH = {
  'feedback.create': '创建反馈',
  'feedback.assign': '指派处理人',
  'feedback.handle': '提交处理结果',
  'feedback.status': '更新反馈状态',
  'feedback.upload_attachment': '上传附件',
  'feedback.delete_attachment': '删除附件',
  'feedback.link_bug': '关联已有 Bug',
  'feedback.create_bug_link': '新建并关联 Bug',
  'feedback.unlink_bug': '解除 Bug 关联',
};

export async function openFeedbackDetail(feedbackId) {
  const [detail, timeline] = await Promise.all([
    (await api(`/feedbacks/${feedbackId}`)).json(),
    (await api(`/feedbacks/${feedbackId}/timeline`)).json(),
  ]);
  const keepScroll = activeFeedbackId === Number(feedbackId) && !!getDrawer('feedback-detail');
  activeFeedbackId = Number(feedbackId);
  revokeAttachmentUrls();
  const admin = isAdmin();
  const attachments = detail.attachments || [];
  const images = attachments.filter((a) => a.is_image);
  const files = attachments.filter((a) => !a.is_image);

  const imagesHtml = images.length ? `<div class="fbk-thumbs" id="feedbackDetailImages">${images.map((a) => `
      <figure class="fbk-thumb">
        <img data-att-id="${a.id}" alt="${esc(a.original_name)}" title="点击放大查看">
        <figcaption><span title="${esc(a.original_name)}">${esc(a.original_name)}</span>
          <button type="button" class="fbk-x" onclick="deleteFeedbackAttachment(${a.id}, ${detail.id})" aria-label="删除 ${esc(a.original_name)}" title="删除">×</button></figcaption>
      </figure>`).join('')}</div>` : '';
  const filesHtml = files.length ? `<div class="fbk-files">${files.map((a) => `
      <div class="fbk-file">
        <span class="fbk-kind">${esc(fileKind(a.file_ext))}</span>
        <span class="fbk-file-name" title="${esc(a.original_name)}">${esc(a.original_name)}</span>
        <span class="fbk-file-size">${formatBytes(a.file_size)}</span>
        <button type="button" class="fbk-link" onclick="window.OmniQAFeedbackTab.downloadFeedbackAttachment(${a.id}, '${esc(a.original_name).replace(/'/g, '&#39;')}')">下载</button>
        <button type="button" class="fbk-link fbk-link--danger" onclick="deleteFeedbackAttachment(${a.id}, ${detail.id})">删除</button>
      </div>`).join('')}</div>` : '';

  const bugsHtml = (detail.bugs || []).map((b) => `<span class="fbk-bug">${renderBugLink(b)}
      <button type="button" class="fbk-x" onclick="unlinkFeedbackBug(${detail.id}, ${b.id})" aria-label="解除关联 ${esc(b.bug_id)}" title="解除关联">×</button></span>`).join('');
  const users = window.users || [];
  const assigneeOpts = users.map((u) => `<option value="${u.id}" ${Number(detail.assignee_id) === Number(u.id) ? 'selected' : ''}>${esc(u.display_name || u.username)}</option>`).join('');

  const timelineHtml = (timeline || []).map((t) => `
      <li class="fbk-tl-item">
        <div class="fbk-tl-head"><b>${esc(ACTION_ZH[t.action] || t.action || '未知操作')}</b><span>${esc(t.actor_name || '系统')} · ${esc(fmtTime(t.created_at))}</span></div>
        ${t.detail ? `<div class="fbk-tl-detail">${esc(t.detail)}</div>` : ''}
      </li>`).join('');

  const handledInfo = detail.handled_by_name
    ? `<div class="fbk-handled">${ic('check')}<span>${esc(detail.handled_by_name)} 于 ${esc(fmtTime(detail.handled_at))} 处理，修复版本 ${esc(detail.handled_major_version_no || '-')} / ${esc(detail.handled_minor_version_no || '-')}</span></div>`
    : '';

  const d = openDrawer({
    key: 'feedback-detail',
    size: 'lg',
    keepScroll,
    onClose: () => { revokeAttachmentUrls(); activeFeedbackId = 0; markActiveRow(); },
    head: `
      <div class="sd-eyebrow"><span class="fbk-no">${esc(detail.feedback_no || '未编号')}</span>${buildStatusBadge(detail.status)}</div>
      <h3 class="sd-title sd-title--clamp" title="${esc(detail.summary)}">${esc(detail.summary || '（无概览）')}</h3>
      <div class="sd-sub">${esc(detail.creator_name || '-')} 创建于 ${esc(fmtTime(detail.created_at))}</div>`,
    tools: `<button type="button" class="secondary sd-tool" onclick="openAuditTimelineModal('feedback', ${detail.id}, '反馈时间线')">${ic('clock')}审计时间线</button>`,
    body: `
      ${renderStatusStepper(detail)}

      <dl class="sd-facts">
        <div><dt>反馈版本</dt><dd>${esc(detail.major_version_no || '-')} / ${esc(detail.minor_version_no || '-')}</dd></div>
        <div><dt>创建人</dt><dd>${esc(detail.creator_name || '-')}</dd></div>
        <div><dt>指派处理人</dt><dd>${detail.assignee_name ? `${avatar(detail.assignee_name)}${esc(detail.assignee_name)}` : '<span class="is-empty">未指派</span>'}</dd></div>
        <div><dt>创建时间</dt><dd>${esc(fmtTime(detail.created_at))}</dd></div>
      </dl>

      <section class="sd-sec">
        <h4 class="sd-sec-title">${ic('feedback')}反馈内容</h4>
        <div class="fbk-content">${esc(detail.summary || '')}</div>
        ${imagesHtml}${filesHtml}
        <label class="sd-drop sd-drop--sm" id="feedbackUploadDrop">
          <input type="file" id="feedbackUploadInput" multiple hidden>
          ${ic('upload', 'sd-drop-ic')}<span><b>添加附件</b>，或把文件拖到这里（选中后自动上传）</span>
        </label>
      </section>

      <section class="sd-sec">
        <h4 class="sd-sec-title">${ic('check')}处理</h4>
        ${handledInfo}
        ${admin ? `<div class="sd-inline">
          <label class="sd-field sd-field--grow"><span class="sd-label">指派处理人</span><select id="feedbackAssigneeSelectInDetail">${assigneeOpts}</select></label>
          <button type="button" class="secondary" onclick="assignFeedback(${detail.id})">${ic('user')}指派并通知</button>
        </div>` : ''}
        <label class="sd-field"><span class="sd-label">处理结果<em>*</em></span>
          <textarea id="feedbackHandleResult" rows="4" placeholder="说明处理方式、原因或修复情况">${esc(detail.handling_result || '')}</textarea></label>
        <div class="sd-grid3">
          <label class="sd-field"><span class="sd-label">处理大版本</span><select id="feedbackHandleMajor"></select></label>
          <label class="sd-field"><span class="sd-label">处理小版本</span><select id="feedbackHandleMinor"></select></label>
          <label class="sd-field"><span class="sd-label">处理后状态</span><select id="feedbackHandleStatus">
            <option value="processing">处理中</option>
            <option value="resolved">已处理</option>
            <option value="closed">已关闭</option>
          </select></label>
        </div>
        <div class="sd-actions"><button type="button" onclick="saveFeedbackHandle(${detail.id})">${ic('check')}保存处理结果</button></div>
      </section>

      <section class="sd-sec">
        <h4 class="sd-sec-title">${ic('bug')}关联 Bug <span class="sd-count">${(detail.bugs || []).length}</span></h4>
        <div class="fbk-bugs">${bugsHtml || '<span class="sd-empty">暂无关联 Bug</span>'}</div>
        <div class="sd-subgrid">
          <div class="sd-field"><span class="sd-label">关联已有 Bug</span>
            <div class="sd-inline sd-inline--tight">
              <span class="sd-search">${ic('search')}<input id="feedbackBugKeyword" placeholder="按 b# 编号或标题搜索" oninput="searchFeedbackBugOptions(this.value)"></span>
              <select id="feedbackLinkBugId"><option value="">选择已有 Bug</option></select>
              <button type="button" class="secondary" onclick="linkFeedbackBug(${detail.id})">关联</button>
            </div>
          </div>
          <div class="sd-field"><span class="sd-label">新建 Bug 并关联</span>
            <div class="sd-inline sd-inline--tight">
              <span class="sd-prefix"><span>b#</span><input id="feedbackNewBugNo" inputmode="numeric" oninput="digitsOnly(this)" placeholder="Bug 数字编号"></span>
              <button type="button" class="secondary" onclick="createFeedbackBug(${detail.id})">新建并关联</button>
            </div>
          </div>
        </div>
      </section>

      <section class="sd-sec">
        <h4 class="sd-sec-title">${ic('clock')}操作记录</h4>
        ${timelineHtml ? `<ol class="fbk-tl">${timelineHtml}</ol>` : '<span class="sd-empty">暂无操作记录</span>'}
      </section>`,
  });

  d.root.dataset.recordId = String(detail.id);
  const mSel = d.body.querySelector('#feedbackHandleMajor');
  const mmSel = d.body.querySelector('#feedbackHandleMinor');
  mSel.innerHTML = versionOptions('major', 0, detail.handled_major_version_id || detail.major_version_id);
  const bindMinor = () => {
    mmSel.innerHTML = versionOptions('minor', mSel.value, detail.handled_minor_version_id || detail.minor_version_id) || '<option value="">暂无小版本</option>';
  };
  mSel.onchange = bindMinor;
  bindMinor();
  const statusSel = d.body.querySelector('#feedbackHandleStatus');
  statusSel.value = detail.status === 'pending' ? 'processing' : detail.status;
  if (!admin) Array.from(statusSel.options).forEach((opt) => { if (opt.value === 'closed') opt.disabled = true; });

  bindDropzone(d.body.querySelector('#feedbackUploadDrop'), d.body.querySelector('#feedbackUploadInput'), (fl) => uploadFiles(detail.id, fl));
  const thumbs = d.body.querySelector('#feedbackDetailImages');
  if (thumbs) {
    wireImageViewer(thumbs);
    hydrateAttachmentImages(thumbs);
  }
  window.OmniQAZentao?.hydrateContainer?.(d.body);
  markActiveRow();
  await loadBugOptions();
}

export function closeFeedbackDetail() {
  closeDrawer('feedback-detail');
}

export async function searchFeedbackBugOptions(keyword) {
  await loadBugOptions(keyword || '');
}

export async function setFeedbackStatus(feedbackId, status) {
  await api(`/feedbacks/${feedbackId}/status`, { method: 'PATCH', headers: window.H, body: { status } });
  window.showMessage && window.showMessage(`状态已更新为：${feedbackStatusZh(status)}`, 'success');
  await openFeedbackDetail(feedbackId);
  await loadFeedbackBoard(currentPage);
}

export async function assignFeedback(feedbackId) {
  const assigneeId = Number(document.getElementById('feedbackAssigneeSelectInDetail')?.value || 0);
  if (!assigneeId) {
    window.showMessage && window.showMessage('请选择处理人', 'error');
    return;
  }
  await api(`/feedbacks/${feedbackId}/assign`, { method: 'POST', headers: window.H, body: { assignee_id: assigneeId, status: 'processing' } });
  window.showMessage && window.showMessage('反馈已指派并触发企微通知', 'success');
  await openFeedbackDetail(feedbackId);
  await loadFeedbackBoard(currentPage);
}

export async function saveFeedbackHandle(feedbackId) {
  const handlingResult = (document.getElementById('feedbackHandleResult')?.value || '').trim();
  const handledMajor = Number(document.getElementById('feedbackHandleMajor')?.value || 0);
  const handledMinor = Number(document.getElementById('feedbackHandleMinor')?.value || 0);
  const status = document.getElementById('feedbackHandleStatus')?.value || 'resolved';
  if (!handlingResult) {
    window.showMessage && window.showMessage('请填写处理结果', 'error');
    document.getElementById('feedbackHandleResult')?.focus();
    return;
  }
  await api(`/feedbacks/${feedbackId}/handle`, {
    method: 'POST',
    headers: window.H,
    body: { handling_result: handlingResult, handled_major_version_id: handledMajor, handled_minor_version_id: handledMinor, status },
  });
  window.showMessage && window.showMessage('处理结果已保存', 'success');
  await openFeedbackDetail(feedbackId);
  await loadFeedbackBoard(currentPage);
}

export async function linkFeedbackBug(feedbackId) {
  const bid = Number(document.getElementById('feedbackLinkBugId')?.value || 0);
  if (!bid) {
    window.showMessage && window.showMessage('请选择要关联的 Bug', 'error');
    return;
  }
  await api(`/feedbacks/${feedbackId}/bugs/link`, { method: 'POST', headers: window.H, body: { bug_id: bid } });
  window.showMessage && window.showMessage('已关联 Bug', 'success');
  await openFeedbackDetail(feedbackId);
}

export async function unlinkFeedbackBug(feedbackId, bugId) {
  if (!confirm('确定解除该 Bug 关联吗？')) return;
  await api(`/feedbacks/${feedbackId}/bugs/${bugId}`, { method: 'DELETE' });
  window.showMessage && window.showMessage('已解除 Bug 关联', 'success');
  await openFeedbackDetail(feedbackId);
}

export async function createFeedbackBug(feedbackId) {
  const num = (document.getElementById('feedbackNewBugNo')?.value || '').trim();
  const bugId = window.withPrefix('b#', num);
  if (!bugId) {
    window.showMessage && window.showMessage('请输入 Bug 数字编号', 'error');
    return;
  }
  await api(`/feedbacks/${feedbackId}/bugs/create-and-link`, { method: 'POST', headers: window.H, body: { bug_id: bugId } });
  window.showMessage && window.showMessage('已新建并关联 Bug', 'success');
  await openFeedbackDetail(feedbackId);
}

export async function loadFeedbackTodoOnMine(majorVersionId, { signal, commit = true } = {}) {
  const majorPart = majorVersionId ? `?major_version_id=${majorVersionId}` : '';
  const rows = await (await api(`/feedbacks/my-todo${majorPart}`, { signal })).json();
  if (!rows.length) {
    if (commit) state.currentFeedbackTodoHtml = '';
    return '';
  }
  const html = `
    <div class="card" style="border:2px solid #16a34a; background:#f0fdf4; margin-bottom:24px;">
      <h3 style="color:#166534; margin-top:0; border-bottom:1px dashed #86efac; padding-bottom:8px;">📚 反馈待处理</h3>
      <table style="background:#fff; border-radius:6px; overflow:hidden;">
        <thead><tr><th>反馈编号</th><th>反馈概览</th><th>反馈版本</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td>${r.feedback_no || '-'}</td>
              <td>${(r.summary || '').slice(0, 36)}${(r.summary || '').length > 36 ? '...' : ''}</td>
              <td>${r.major_version_no || '-'} / ${r.minor_version_no || '-'}</td>
              <td>${buildStatusBadge(r.status)}</td>
              <td><button class="secondary" onclick="showTab('feedback'); openFeedbackDetail(${r.id});">处理</button></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
  if (commit) state.currentFeedbackTodoHtml = html;
  return html;
}

window.OmniQAFeedbackTab = {
  loadFeedbackBoard,
  loadFeedbackMinorOptions,
  nextFeedbackPage,
  prevFeedbackPage,
  createFeedback,
  openFeedbackCreate,
  openFeedbackDetail,
  closeFeedbackDetail,
  uploadFeedbackFiles,
  deleteFeedbackAttachment,
  downloadFeedbackAttachment,
  assignFeedback,
  saveFeedbackHandle,
  linkFeedbackBug,
  unlinkFeedbackBug,
  createFeedbackBug,
  loadFeedbackTodoOnMine,
  searchFeedbackBugOptions,
  setFeedbackStatus,
  _removeCreateFile(i) { createFiles.splice(Number(i), 1); renderCreateFiles(); },
};

function bindFeedbackSSE() {
  if (feedbackSseBound) return;
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;

  // New feedback created → top banner if scrolled down, otherwise reload & glow
  window.OmniQASSE.subscribe('feedback_task_created', ({ payload }) => {
    const tab = document.getElementById('tab-feedback');
    if (!tab || tab.classList.contains('hidden')) return;
    const feedbackId = Number(payload?.id || 0);
    const listEl = document.getElementById('feedbackTable');
    const rect = listEl?.getBoundingClientRect();
    const atTop = !rect || rect.top >= 0;
    if (!atTop && window.OmniQASSE.showPositionBanner) {
      _feedbackTopBannerCount.value += 1;
      window.OmniQASSE.showPositionBanner({
        scrollContainer: document.scrollingElement || document.documentElement,
        anchorEl: listEl,
        position: 'top',
        countRef: _feedbackTopBannerCount,
        labelFn: (n) => `↑ ${n} 条新反馈任务`,
        onClickScroll: () => listEl?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
        bannerId: 'feedback-top-banner',
      });
      return;
    }
    if (window._feedbackSSETimer) clearTimeout(window._feedbackSSETimer);
    window._feedbackSSETimer = setTimeout(async () => {
      _feedbackTopBannerCount.value = 0;
      await loadFeedbackBoard(1);
      if (feedbackId) {
        const el = document.querySelector(`.feedback-row-card[data-feedback-id='${feedbackId}']`);
        if (el) window.OmniQASSE.pulseBoundaryGlow(el, 'blue');
      }
    }, 400);
  });

  // Feedback status/assignment updated → teal glow on existing row
  window.OmniQASSE.subscribe('feedback_task_updated', ({ payload }) => {
    const id = Number(payload?.id || 0);
    if (!id) return;
    const el = document.querySelector(`.feedback-row-card[data-feedback-id='${id}']`);
    if (el && typeof window.OmniQASSE.pulseBoundaryGlow === 'function') {
      window.OmniQASSE.pulseBoundaryGlow(el, 'teal');
    }
  });

  feedbackSseBound = true;
}
bindFeedbackSSE();
