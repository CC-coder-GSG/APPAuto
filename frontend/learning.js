// 学习中心（2026-11-27 改版）：课程 / 资料 / 答题活动；答题活动可以挂在课程或资料下面（随课答题），也可以单独存在。
// 结构 index.html #tab-learning；样式 weave.css R25（.lrn-*）+ learning.css（题目编辑器）。
// 页面内各视图互斥显示：主页 learningHome / 详情 learningContentDetail / 表单与答题面板。
// 旧版「学习主题与附件考核」仍由 js/tabs/learning.js 渲染在主页底部的折叠区里。

function renderLearningOriginal(q, index, marks = null) {
  const chosen = new Set((marks?.chosen || []).map(String));
  const correct = new Set((marks?.correct || []).map(String));
  const li = o => {
    const cls = [chosen.has(String(o)) ? 'is-chosen' : '', correct.has(String(o)) ? 'is-correct' : ''].filter(Boolean).join(' ');
    return `<li${cls ? ` class="${cls}"` : ''}>${learningEsc(o)}</li>`;
  };
  return `<div class="learning-original"><div class="muted lrn-orig-meta">${learningEsc(learningType(q.question_type || ''))} · ${q.max_score ?? q.score ?? 0} 分</div><strong class="learning-prompt">${q.position ?? index}. ${learningEsc(q.prompt)}</strong>${(q.options || []).length ? `<ol class="learning-original-options" type="A">${q.options.map(li).join('')}</ol>` : ''}</div>`;
}

// 确认框：正式页面走全站对话框（components/dialog.js）；只有独立测试页没加载对话框模块时才退回浏览器原生框
function learningConfirm(opts) {
  if (window.OmniQADialog) return window.OmniQADialog.confirm(opts);
  return Promise.resolve(window.confirm(opts.message || opts.title || ''));
}

let learningContentsCache = [];
let learningQuizzesCache = [];
let activeQuiz = null;
let activeQuizPreview = false;
let learningPreviewObjectUrl = null;
// 从详情页进入表单 / 答题后，「返回」回到这门课程而不是主页
let learningReturnContentId = null;
let learningLoadSeq = 0;
const learningView = { filter: 'all', query: '', mine: false };

const LRN_PANELS = ['learningHome', 'learningContentDetail', 'learningContentForm', 'quizEditor', 'quizTakePanel', 'quizResultPanel', 'quizReviewPanel'];
const LRN_KIND = { course: '课程', material: '资料' };

function learningEsc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

function lrnIc(name, cls = '') {
  return `<svg class="ui-ic${cls ? ` ${cls}` : ''}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

function lrnMe() {
  try { return typeof currentUser !== 'undefined' && currentUser ? currentUser : (window.currentUser || null); } catch { return window.currentUser || null; }
}

function lrnIsMine(creatorId) {
  const me = lrnMe();
  return !!me && Number(me.id) === Number(creatorId);
}

// 后端返回的是不带时区的 UTC 时间
function lrnDate(value, withTime = true) {
  if (!value) return '';
  const raw = String(value);
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(raw) ? raw : `${raw}Z`);
  if (Number.isNaN(d.getTime())) return '';
  const pad = n => String(n).padStart(2, '0');
  const day = `${d.getFullYear() === new Date().getFullYear() ? '' : `${d.getFullYear()}-`}${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return withTime ? `${day} ${pad(d.getHours())}:${pad(d.getMinutes())}` : day;
}

function learningStatus(status) {
  return {draft:'草稿', open:'答题中', closed:'已结束'}[status] || status;
}

function learningType(type) {
  return {single_choice:'单选题', multiple_choice:'多选题', fill_blank:'填空题', short_answer:'简答题'}[type] || type;
}

function learningFileSize(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function lrnFileKind(file) {
  const ext = String(file?.file_ext || file?.original_name?.match(/\.[^.]+$/)?.[0] || '').toLowerCase();
  if (['.ppt', '.pptx'].includes(ext)) return { label: 'PPT', tone: 'ppt' };
  if (ext === '.pdf') return { label: 'PDF', tone: 'pdf' };
  if (['.md', '.markdown'].includes(ext)) return { label: 'MD', tone: 'md' };
  if (['.doc', '.docx'].includes(ext)) return { label: 'DOC', tone: 'doc' };
  if (['.xls', '.xlsx', '.csv'].includes(ext)) return { label: ext === '.csv' ? 'CSV' : 'XLS', tone: 'xls' };
  if (['.png', '.jpg', '.jpeg', '.gif', '.webp'].includes(ext)) return { label: 'IMG', tone: 'img' };
  if (ext === '.txt') return { label: 'TXT', tone: 'txt' };
  return { label: (ext.replace('.', '') || 'FILE').slice(0, 4).toUpperCase(), tone: 'txt' };
}

function lrnPreviewNote(file) {
  if (!file?.previewable) return '仅支持下载';
  const kind = lrnFileKind(file);
  if (kind.tone === 'ppt') return 'PPT 已转换为 PDF 预览';
  if (kind.tone === 'md') return 'Markdown 文档';
  return 'PDF 文档';
}

function findLearningContentFile(fileId) {
  for (const item of learningContentsCache) {
    const file = (item.files || []).find(candidate => candidate.id === Number(fileId));
    if (file) return file;
  }
  return null;
}

function lrnContentOfFile(fileId) {
  return learningContentsCache.find(item => (item.files || []).some(f => f.id === Number(fileId))) || null;
}

function lrnQuizzesFor(contentId) {
  // 随课答题按创建先后排列（第 1 课在前）
  return learningQuizzesCache.filter(q => Number(q.content_id) === Number(contentId)).sort((a, b) => a.id - b.id);
}

function renderLearningFileList(files, canDelete = false, contentId = null) {
  if (!files || !files.length) return '<p class="muted lrn-none">暂无附件</p>';
  return files.map(file => {
    const kind = lrnFileKind(file);
    return `<div class="learning-file-row lrn-file">
    <span class="lrn-ficon lrn-ficon--${kind.tone}" aria-hidden="true">${kind.label}</span>
    <span class="learning-file-name"><b>${learningEsc(file.original_name)}</b><small>${learningFileSize(file.file_size)} · ${lrnPreviewNote(file)}</small></span>
    <span class="learning-file-actions">
      ${file.previewable ? `<button class="secondary" onclick="previewLearningContentFile(${file.id})">${lrnIc('book')}预览</button>` : ''}
      <button class="secondary" onclick="downloadLearningContentFile(${file.id})" aria-label="下载 ${learningEsc(file.original_name)}">${lrnIc('download')}下载</button>
      ${canDelete ? `<button class="danger" onclick="deleteLearningContentFile(${file.id},${contentId})" aria-label="删除 ${learningEsc(file.original_name)}">${lrnIc('trash')}删除</button>` : ''}
    </span>
  </div>`;
  }).join('');
}

function showLearningPanel(id) {
  LRN_PANELS.forEach(pid => document.getElementById(pid)?.classList.toggle('hidden', pid !== id));
  // 切到子视图时回到页签顶部，避免停在主页的滚动位置
  const tab = document.getElementById('tab-learning');
  if (tab && id !== 'learningHome' && !tab.classList.contains('hidden')) {
    const top = tab.getBoundingClientRect().top + window.scrollY - 72;
    if (window.scrollY > top) window.scrollTo({ top: Math.max(0, top) });
  }
}

function closeLearningPanels() {
  const back = learningReturnContentId;
  if (back && learningContentsCache.some(x => x.id === back)) {
    openLearningContent(back);
  } else {
    learningReturnContentId = null;
    showLearningPanel('learningHome');
  }
  loadLearningCenter();
}

function backToLearningHome() {
  learningReturnContentId = null;
  showLearningPanel('learningHome');
  renderLearningHome();
}

async function loadLearningCenter() {
  bindLearningCenter();
  const seq = ++learningLoadSeq;
  const home = document.getElementById('learningHome');
  if (!learningContentsCache.length && !learningQuizzesCache.length) renderLearningSkeleton();
  home?.classList.add('is-loading');
  try {
    const [contents, quizzes] = await Promise.all([
      api('/learning/contents').then(r => r.json()),
      api('/learning/quizzes').then(r => r.json())
    ]);
    if (seq !== learningLoadSeq) return;
    learningContentsCache = Array.isArray(contents) ? contents : [];
    learningQuizzesCache = Array.isArray(quizzes) ? quizzes : [];
    renderLearningHome();
    const detail = document.getElementById('learningContentDetail');
    if (detail && !detail.classList.contains('hidden') && learningReturnContentId) {
      const item = learningContentsCache.find(x => x.id === learningReturnContentId);
      if (item) renderLearningContentDetail(item); else backToLearningHome();
    }
  } catch (e) {
    if (seq === learningLoadSeq) showMessage(e.message || '学习中心加载失败', 'error');
  } finally {
    if (seq === learningLoadSeq) home?.classList.remove('is-loading');
  }
}

function renderLearningSkeleton() {
  const card = '<div class="lrn-card lrn-skel" aria-hidden="true"><i style="width:30%"></i><i style="width:78%;height:16px"></i><i style="width:92%"></i><i style="width:56%"></i></div>';
  const host = document.getElementById('learningContents');
  if (host) host.innerHTML = card.repeat(3);
  const quizHost = document.getElementById('learningQuizzes');
  if (quizHost) quizHost.innerHTML = card.repeat(2);
}

// ============================ 主页 ============================

function bindLearningCenter() {
  const seg = document.getElementById('learningFilterSeg');
  if (!seg || seg.dataset.bound) return;
  seg.dataset.bound = '1';
  seg.addEventListener('click', e => {
    const btn = e.target.closest('[data-lrn-filter]');
    if (!btn) return;
    learningView.filter = btn.dataset.lrnFilter;
    renderLearningHome();
  });
  const search = document.getElementById('learningSearch');
  let timer = 0;
  search?.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { learningView.query = search.value.trim().toLowerCase(); renderLearningHome(); }, 120);
  });
  document.getElementById('learningMineOnly')?.addEventListener('change', e => {
    learningView.mine = e.target.checked;
    renderLearningHome();
  });
}

function lrnMatches(fields) {
  const q = learningView.query;
  if (!q) return true;
  return fields.some(v => String(v || '').toLowerCase().includes(q));
}

function lrnVisibleContents(kind = null) {
  return learningContentsCache.filter(item => {
    if (kind && item.kind !== kind) return false;
    if (learningView.mine && !lrnIsMine(item.creator_id)) return false;
    const quizTitles = lrnQuizzesFor(item.id).map(q => q.title);
    return lrnMatches([item.title, item.description, item.creator_name, ...quizTitles]);
  });
}

function lrnVisibleQuizzes(standaloneOnly) {
  return learningQuizzesCache.filter(q => {
    if (standaloneOnly && q.content_id) return false;
    if (learningView.mine && !q.is_creator) return false;
    return lrnMatches([q.title, q.description, q.creator_name, q.content?.title]);
  });
}

function renderLearningHome() {
  const seg = document.getElementById('learningFilterSeg');
  if (!seg) { renderLearningContents(); renderLearningQuizzes(); return; }
  const counts = {
    course: learningContentsCache.filter(x => x.kind === 'course').length,
    material: learningContentsCache.filter(x => x.kind === 'material').length,
    quiz: learningQuizzesCache.length,
  };
  counts.all = counts.course + counts.material + learningQuizzesCache.filter(q => !q.content_id).length;
  seg.querySelectorAll('[data-lrn-count]').forEach(el => { el.textContent = counts[el.dataset.lrnCount] ?? 0; });
  seg.querySelectorAll('[data-lrn-filter]').forEach(btn => {
    const on = btn.dataset.lrnFilter === learningView.filter;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  renderLearningTodo();
  renderLearningContents();
  renderLearningQuizzes();
}

function renderLearningTodo() {
  const host = document.getElementById('learningTodo');
  if (!host) return;
  const todo = learningQuizzesCache.filter(q => q.status === 'open' && !q.is_creator && !q.my_submission);
  host.hidden = !todo.length;
  if (!todo.length) { host.innerHTML = ''; return; }
  host.innerHTML = `<span class="lrn-todo-ic">${lrnIc('clock')}</span>
    <div class="lrn-todo-main"><b>有 ${todo.length} 个答题活动等你完成</b><span>正式答卷只能提交一次，提交前可以先看一遍课程内容</span></div>
    <div class="lrn-todo-list">${todo.slice(0, 3).map(q => `<button type="button" onclick="openTakeQuiz(${q.id},false)">${lrnIc('play')}<span>${learningEsc(q.title)}</span></button>`).join('')}${todo.length > 3 ? `<span class="lrn-todo-more">等 ${todo.length} 个</span>` : ''}</div>`;
}

function lrnEmpty(icon, title, hint, action = '') {
  return `<div class="lrn-empty">${lrnIc(icon)}<b>${title}</b><span>${hint}</span>${action}</div>`;
}

function renderLearningContents() {
  const host = document.getElementById('learningContents');
  if (!host) return;
  const section = document.getElementById('learningContentsSection');
  const filter = learningView.filter;
  if (section) section.hidden = filter === 'quiz';
  if (filter === 'quiz') return;
  const kind = filter === 'course' || filter === 'material' ? filter : null;
  const heading = document.getElementById('learningContentsHeading');
  if (heading) heading.textContent = kind ? (kind === 'course' ? '课程' : '学习资料') : '课程与资料';
  const items = lrnVisibleContents(kind);
  const note = document.getElementById('learningContentsNote');
  if (note) {
    const linked = items.reduce((n, item) => n + lrnQuizzesFor(item.id).length, 0);
    note.textContent = items.length ? `${items.length} 项${linked ? ` · 含随课答题 ${linked} 个` : ''}` : '';
  }
  if (!items.length) {
    host.innerHTML = learningContentsCache.length
      ? lrnEmpty('search', '没有符合条件的内容', '换个关键词，或取消「只看我创建的」')
      : lrnEmpty('book', '还没有课程或学习资料', '新建一门课程并上传课件，课程还可以附带随课答题', '<button type="button" onclick="showLearningContentForm()">新建课程 / 资料</button>');
    return;
  }
  host.innerHTML = items.map(renderContentCard).join('');
}

function renderContentCard(item) {
  const quizzes = lrnQuizzesFor(item.id);
  const files = item.files || [];
  const kind = LRN_KIND[item.kind] || '资料';
  return `<article class="lrn-card lrn-card--${item.kind}" data-lrn-open="${item.id}">
    <div class="lrn-card-top">
      <span class="lrn-kind lrn-kind--${item.kind}">${lrnIc(item.kind === 'course' ? 'book' : 'file')}${kind}</span>
      ${item.published ? '' : '<span class="lrn-chip lrn-chip--draft">未发布</span>'}
      ${quizzes.length ? `<span class="lrn-chip lrn-chip--quiz">${lrnIc('check')}随课答题 ${quizzes.length}</span>` : ''}
    </div>
    <h4 class="lrn-card-title"><button type="button" class="lrn-card-link" data-lrn-open="${item.id}">${learningEsc(item.title)}</button></h4>
    ${item.description ? `<p class="lrn-card-desc">${learningEsc(item.description)}</p>` : ''}
    <div class="lrn-card-meta">
      <span>${lrnIc('user')}${learningEsc(item.creator_name || '—')}</span>
      <span>${lrnIc('clock')}${lrnDate(item.updated_at || item.created_at, false)}</span>
      ${files.length ? `<span>${lrnIc('file')}${files.length} 个课件</span>` : ''}
      ${item.resource_url ? `<span>${lrnIc('external')}外部链接</span>` : ''}
    </div>
    ${quizzes.length ? `<div class="lrn-card-quizzes">${quizzes.map(q => renderQuizRow(q, true)).join('')}</div>` : ''}
  </article>`;
}

function lrnMyState(q) {
  if (q.is_creator) {
    if (q.status === 'draft') return '草稿仅自己可见';
    if (q.status === 'open') return '进行中';
    return q.results_released ? '成绩已发布' : '待批阅 / 发布';
  }
  if (q.my_submission) return q.results_released ? `我的成绩 ${q.my_submission.total_score ?? '-'} / ${q.total_points}` : '已提交，等待发布成绩';
  if (q.status === 'open') return '待作答';
  return '未参加';
}

function lrnQuizActions(q, compact = false) {
  const b = (label, call, cls = '', icon = '') => `<button type="button" class="${cls}" onclick="${call}">${icon ? lrnIc(icon) : ''}${label}</button>`;
  const list = [];
  if (q.is_creator) {
    if (q.status === 'draft') {
      list.push(b('编辑题目', `openQuizEditor(${q.id})`, compact ? 'secondary' : '', 'edit'));
      list.push(b('预览答题', `previewQuiz(${q.id})`, 'secondary', 'book'));
      list.push(b('开始答题', `startLearningQuiz(${q.id})`, '', 'play'));
      list.push(b('删除', `deleteLearningQuiz(${q.id})`, 'danger', 'trash'));
    } else if (q.status === 'open') {
      list.push(b('答题情况', `reviewLearningQuiz(${q.id})`, compact ? 'secondary' : '', 'report'));
      list.push(b('预览答题', `previewQuiz(${q.id})`, 'secondary', 'book'));
      list.push(b('结束答题', `closeLearningQuiz(${q.id})`, 'danger', 'ban'));
    } else {
      list.push(b('批阅与统计', `reviewLearningQuiz(${q.id})`, compact ? 'secondary' : '', 'report'));
    }
  } else if (q.status === 'open' && !q.my_submission) {
    list.push(b('开始答题', `openTakeQuiz(${q.id},false)`, '', 'play'));
  } else if (q.my_submission) {
    list.push(b(q.results_released ? '成绩与原题' : '查看作答', `viewMyQuizResult(${q.id})`, 'secondary', 'check'));
  }
  return compact ? list.slice(0, 1) : list;
}

function renderQuizRow(q, compact = false, { unlink = false } = {}) {
  const actions = lrnQuizActions(q, compact);
  return `<div class="lrn-qrow">
    <span class="lrn-qrow-ic">${lrnIc('check')}</span>
    <div class="lrn-qrow-main">
      <b>${learningEsc(q.title)}</b>
      <span><i class="lrn-status lrn-status--${q.status}">${learningStatus(q.status)}</i>${q.question_count} 题 · ${q.total_points} 分 · ${learningEsc(lrnMyState(q))}</span>
    </div>
    <div class="lrn-qrow-act">${actions.join('')}${unlink && q.is_creator ? `<button type="button" class="secondary lrn-icon-btn" onclick="linkLearningQuiz(${q.id},null)" title="解除关联（变为独立答题）" aria-label="解除关联 ${learningEsc(q.title)}">${lrnIc('unlink')}</button>` : ''}</div>
  </div>`;
}

function renderLearningQuizzes() {
  const host = document.getElementById('learningQuizzes');
  if (!host) return;
  const filter = learningView.filter;
  const section = document.getElementById('learningQuizzesSection');
  if (section) section.hidden = filter === 'course' || filter === 'material';
  if (filter === 'course' || filter === 'material') return;
  const all = filter === 'quiz';
  const heading = document.getElementById('learningQuizzesHeading');
  if (heading) heading.textContent = all ? '答题活动' : '独立答题活动';
  const quizzes = lrnVisibleQuizzes(!all);
  const note = document.getElementById('learningQuizzesNote');
  if (note) {
    const linked = quizzes.filter(q => q.content_id).length;
    note.textContent = all ? `${quizzes.length} 个${linked ? ` · 其中随课答题 ${linked} 个` : ''}` : '不属于任何课程的答题';
  }
  if (!quizzes.length) {
    host.innerHTML = learningQuizzesCache.length && (learningView.query || learningView.mine || !all)
      ? lrnEmpty('check', all ? '没有符合条件的答题活动' : '没有独立答题活动', all ? '换个关键词试试' : '随课答题显示在对应课程的卡片里')
      : lrnEmpty('check', '还没有答题活动', '可以单独出一份答题，也可以在课程里添加随课答题', '<button type="button" class="secondary" onclick="openQuizEditor()">创建答题活动</button>');
    return;
  }
  host.innerHTML = quizzes.map(renderQuizCard).join('');
}

function renderQuizCard(q) {
  const mine = lrnMyState(q);
  const scored = !q.is_creator && q.my_submission && q.results_released;
  return `<article class="lrn-card lrn-card--quiz">
    <div class="lrn-card-top">
      <span class="lrn-status lrn-status--${q.status}">${learningStatus(q.status)}</span>
      ${q.content ? `<button type="button" class="lrn-chip lrn-chip--link" data-lrn-open="${q.content.id}" title="查看所属${LRN_KIND[q.content.kind] || '内容'}">${lrnIc(q.content.kind === 'course' ? 'book' : 'file')}<span>随课 · ${learningEsc(q.content.title)}</span></button>` : ''}
    </div>
    <h4 class="lrn-card-title">${learningEsc(q.title)}</h4>
    ${q.description ? `<p class="lrn-card-desc">${learningEsc(q.description)}</p>` : ''}
    <div class="lrn-card-meta">
      <span>${lrnIc('user')}${learningEsc(q.creator_name || '—')}</span>
      <span>${lrnIc('layers')}${q.question_count} 题 · ${q.total_points} 分</span>
      ${q.started_at ? `<span>${lrnIc('clock')}${lrnDate(q.started_at, false)} 开始</span>` : ''}
    </div>
    <div class="lrn-quiz-state${scored ? ' is-score' : ''}${!q.is_creator && q.status === 'open' && !q.my_submission ? ' is-todo' : ''}">${learningEsc(mine)}</div>
    <div class="learning-item-actions lrn-card-actions">${lrnQuizActions(q).join('') || '<span class="muted">当前无可用操作</span>'}</div>
  </article>`;
}

// ============================ 课程 / 资料详情 ============================

function openLearningContent(id) {
  const item = learningContentsCache.find(x => x.id === Number(id));
  if (!item) { showMessage('这项内容不存在或已被删除', 'error'); return; }
  learningReturnContentId = item.id;
  showLearningPanel('learningContentDetail');
  renderLearningContentDetail(item);
}

function renderLearningContentDetail(item) {
  const host = document.getElementById('learningContentDetail');
  if (!host) return;
  const mine = lrnIsMine(item.creator_id);
  const files = item.files || [];
  const previewable = files.filter(f => f.previewable);
  const quizzes = lrnQuizzesFor(item.id);
  const kind = LRN_KIND[item.kind] || '资料';
  const linkable = learningQuizzesCache.filter(q => q.is_creator && !q.content_id);
  const noun = item.kind === 'course' ? '随课答题' : '配套答题';
  host.innerHTML = `
    <div class="lrn-back-row"><button type="button" class="secondary lrn-back" onclick="backToLearningHome()">${lrnIc('arrow-left')}返回学习中心</button></div>
    <div class="lrn-dhead">
      <span class="lrn-dhead-ic lrn-dhead-ic--${item.kind}">${lrnIc(item.kind === 'course' ? 'book' : 'file')}</span>
      <div class="lrn-dhead-main">
        <div class="lrn-dhead-chips"><span class="lrn-kind lrn-kind--${item.kind}">${kind}</span>${item.published ? '<span class="lrn-chip lrn-chip--ok">已发布</span>' : '<span class="lrn-chip lrn-chip--draft">未发布 · 仅自己可见</span>'}</div>
        <h3 class="lrn-dtitle">${learningEsc(item.title)}</h3>
        <div class="lrn-dmeta"><span>${lrnIc('user')}${learningEsc(item.creator_name || '—')}</span><span>创建于 ${lrnDate(item.created_at)}</span>${item.updated_at && item.updated_at !== item.created_at ? `<span>更新于 ${lrnDate(item.updated_at)}</span>` : ''}</div>
      </div>
      <div class="lrn-dhead-actions">
        ${item.resource_url ? `<a class="lrn-btn-link" href="${learningEsc(item.resource_url)}" target="_blank" rel="noopener">${lrnIc('external')}打开外部资源</a>` : ''}
        ${mine ? `<button type="button" class="secondary" onclick="showLearningContentForm(${item.id})">${lrnIc('edit')}编辑</button><button type="button" class="danger" onclick="deleteLearningContent(${item.id})">${lrnIc('trash')}删除</button>` : ''}
      </div>
    </div>
    <div class="lrn-dgrid">
      <div class="lrn-dmain">
        ${item.description ? `<section class="lrn-block"><h4>简介</h4><p class="lrn-prose">${learningEsc(item.description)}</p></section>` : ''}
        ${item.body ? `<section class="lrn-block"><h4>${item.kind === 'course' ? '学习说明' : '正文'}</h4><div class="lrn-prose">${learningEsc(item.body)}</div></section>` : ''}
        <section class="lrn-block">
          <div class="lrn-block-head"><h4>课件与附件</h4><span class="lrn-count">${files.length}</span><span class="lrn-grow"></span>${previewable.length ? `<button type="button" class="secondary" onclick="previewLearningContentFile(${previewable[0].id})">${lrnIc('book')}从头预览</button>` : ''}</div>
          <div class="learning-file-list">${files.length ? renderLearningFileList(files) : `<p class="muted lrn-none">${mine ? '还没有上传课件，点击「编辑」添加。' : '这项内容没有附件。'}</p>`}</div>
        </section>
        ${!item.description && !item.body && !files.length && !item.resource_url ? `<p class="muted lrn-none">${mine ? '内容还是空的，点击「编辑」补充简介、学习说明或课件。' : '暂无更多内容。'}</p>` : ''}
      </div>
      <aside class="lrn-dside">
        <section class="lrn-block lrn-block--quiz">
          <div class="lrn-block-head"><h4>${noun}</h4><span class="lrn-count">${quizzes.length}</span></div>
          ${quizzes.length ? `<div class="lrn-qlist">${quizzes.map(q => renderQuizRow(q, false, { unlink: true })).join('')}</div>` : `<p class="muted lrn-none">${item.kind === 'course' ? '这门课程还没有随课答题。' : '这份资料还没有配套答题。'}</p>`}
          ${mine || linkable.length ? `<div class="lrn-qtools">
            ${mine ? `<button type="button" onclick="openQuizEditor(null,{contentId:${item.id}})">${lrnIc('plus')}新建${noun}</button>` : ''}
            ${linkable.length ? `<div class="lrn-qlink"><select id="learningLinkQuizSelect" aria-label="选择要关联的答题活动"><option value="">关联我已有的独立答题…</option>${linkable.map(q => `<option value="${q.id}">${learningEsc(q.title)}（${learningStatus(q.status)}）</option>`).join('')}</select><button type="button" class="secondary" onclick="linkSelectedLearningQuiz(${item.id})">关联</button></div>` : ''}
          </div>` : ''}
          ${!item.published && quizzes.some(q => q.status !== 'draft') ? '<p class="lrn-warn">内容尚未发布：成员能看到并参加答题，但看不到这里的课件。</p>' : ''}
        </section>
      </aside>
    </div>`;
}

async function linkLearningQuiz(quizId, contentId) {
  const quiz = learningQuizzesCache.find(q => q.id === Number(quizId));
  if (contentId === null && !(await learningConfirm({ title: '解除关联', message: `「${quiz?.title || '这个答题活动'}」会变成独立答题活动，题目和答卷都不受影响。`, okText: '解除关联' }))) return;
  try {
    await api(`/learning/quizzes/${quizId}/content`, { method: 'PUT', headers: H, body: JSON.stringify({ content_id: contentId }) });
    showMessage(contentId === null ? '已解除关联，变为独立答题活动' : '已关联到这项内容');
    await loadLearningCenter();
  } catch (e) { showMessage(e.message || '操作失败', 'error'); }
}

function linkSelectedLearningQuiz(contentId) {
  const select = document.getElementById('learningLinkQuizSelect');
  if (!select?.value) { showMessage('请先选择要关联的答题活动', 'error'); return; }
  linkLearningQuiz(Number(select.value), contentId);
}

// 卡片 / 芯片点击：打开课程或资料详情（卡片内按钮自己处理，不触发）
document.addEventListener('click', e => {
  const opener = e.target.closest('[data-lrn-open]');
  if (!opener || !opener.closest('#tab-learning')) return;
  if (opener.tagName !== 'BUTTON' && e.target.closest('button, a, input, select, textarea, label')) return;
  if (opener.closest('#quizTakePanel')) return;
  e.preventDefault();
  openLearningContent(Number(opener.dataset.lrnOpen));
});

// ============================ 课程 / 资料表单 ============================

function setLearningContentKind(kind) {
  const input = document.getElementById('learningContentKind');
  if (input) input.value = kind;
  document.querySelectorAll('#learningContentForm [data-lrn-kind]').forEach(btn => {
    const on = btn.dataset.lrnKind === kind;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-checked', on ? 'true' : 'false');
  });
  const wrap = document.getElementById('learningContentWithQuizWrap');
  if (wrap) wrap.lastChild.textContent = kind === 'course' ? '保存后为这门课程创建随课答题' : '保存后为这份资料创建配套答题';
}

function bindLearningContentForm() {
  const form = document.getElementById('learningContentForm');
  if (!form || form.dataset.bound) return;
  form.dataset.bound = '1';
  form.addEventListener('click', e => {
    const btn = e.target.closest('[data-lrn-kind]');
    if (btn) setLearningContentKind(btn.dataset.lrnKind);
  });
  const drop = document.getElementById('learningContentDrop');
  const input = document.getElementById('learningContentFiles');
  if (drop && input) {
    ['dragenter', 'dragover'].forEach(type => drop.addEventListener(type, e => { e.preventDefault(); drop.classList.add('is-over'); }));
    ['dragleave', 'drop'].forEach(type => drop.addEventListener(type, () => drop.classList.remove('is-over')));
    drop.addEventListener('drop', e => {
      e.preventDefault();
      if (input.disabled || !e.dataTransfer?.files?.length) return;
      const transfer = new DataTransfer();
      [...input.files, ...e.dataTransfer.files].forEach(file => transfer.items.add(file));
      input.files = transfer.files;
      showLearningSelectedFiles();
    });
  }
}

function showLearningContentForm(id = null) {
  bindLearningContentForm();
  showLearningPanel('learningContentForm');
  const item = id ? learningContentsCache.find(x => x.id === id) : null;
  learningContentFormTitle.textContent = item ? `编辑${LRN_KIND[item.kind] || '内容'}` : '新建课程 / 资料';
  learningContentId.value = item ? item.id : '';
  setLearningContentKind(item ? item.kind : 'course');
  learningContentTitle.value = item ? item.title : '';
  learningContentDescription.value = item ? item.description || '' : '';
  learningContentBody.value = item ? item.body || '' : '';
  learningContentUrl.value = item ? item.resource_url || '' : '';
  learningContentPublished.checked = item ? item.published : false;
  const withQuiz = document.getElementById('learningContentWithQuiz');
  if (withQuiz) withQuiz.checked = false;
  learningContentFiles.value = '';
  learningContentUploadStatus.textContent = '';
  learningContentExistingFiles.innerHTML = item && (item.files || []).length
    ? `<div class="lrn-field-sub">已上传 ${item.files.length} 个附件</div>${renderLearningFileList(item.files || [], true, item.id)}`
    : '';
  setTimeout(() => learningContentTitle.focus({ preventScroll: true }), 30);
}

async function saveLearningContent() {
  if (learningContentSaveBtn.disabled) return;
  const id = learningContentId.value;
  const payload = {
    kind: learningContentKind.value || 'course',
    title: learningContentTitle.value.trim(),
    description: learningContentDescription.value.trim() || null,
    body: learningContentBody.value.trim() || null,
    resource_url: learningContentUrl.value.trim() || null,
    published: learningContentPublished.checked
  };
  if (!payload.title) { learningContentTitle.focus(); return showMessage('请输入标题', 'error'); }
  const pendingFiles = [...learningContentFiles.files];
  if (pendingFiles.some(file => file.size > 100 * 1024 * 1024)) return showMessage('单个附件不能超过 100 MB，请重新选择', 'error');
  const withQuiz = !!document.getElementById('learningContentWithQuiz')?.checked;
  learningContentSaveBtn.disabled = true;
  learningContentFiles.disabled = true;
  try {
    const saved = await api(id ? `/learning/contents/${id}` : '/learning/contents', {method:id ? 'PUT' : 'POST', headers:H, body:JSON.stringify(payload)}).then(r => r.json());
    learningContentId.value = saved.id;
    const failed = await uploadLearningFiles(`/learning/contents/${saved.id}/files`, pendingFiles, learningContentUploadStatus);
    retainLearningFiles(learningContentFiles, failed);
    if (failed.length) {
      showMessage(`${failed.length} 个附件上传失败，点击保存可重试；已成功的附件不会重复上传`, 'error');
      return;
    }
    showMessage(pendingFiles.length ? '内容及附件已保存' : '内容已保存');
    learningReturnContentId = saved.id || null;
    if (withQuiz && saved.id) {
      await loadLearningCenter();
      openQuizEditor(null, { contentId: saved.id });
      return;
    }
    closeLearningPanels();
  } catch(e) {
    showMessage(`${e.message || '保存失败'}${learningContentId.value ? '；已保存的内容可重新编辑并继续上传' : ''}`, 'error');
  } finally {
    learningContentSaveBtn.disabled = false;
    learningContentFiles.disabled = false;
  }
}

function retainLearningFiles(input, files) {
  const transfer = new DataTransfer();
  files.forEach(file => transfer.items.add(file));
  input.files = transfer.files;
}

function showLearningSelectedFiles() {
  const files = [...learningContentFiles.files];
  learningContentUploadStatus.innerHTML = files.length ? `<div class="learning-upload-summary">已选择 ${files.length} 个附件 · 点击保存开始上传</div><div class="learning-upload-list">${files.map(file => { const k = lrnFileKind({ original_name: file.name }); return `<div class="learning-file-row lrn-file"><span class="lrn-ficon lrn-ficon--${k.tone}" aria-hidden="true">${k.label}</span><strong class="learning-file-name">${learningEsc(file.name)}</strong><span class="muted">${learningFileSize(file.size)}</span></div>`; }).join('')}</div>` : '';
}

async function uploadLearningFiles(url, files, host) {
  if (!files.length) return [];
  host.innerHTML = `<div class="learning-upload-summary" role="status"></div><div class="learning-upload-list">${files.map(file => `<div class="learning-upload-file"><strong>${learningEsc(file.name)}</strong><progress max="100" value="0" aria-label="${learningEsc(file.name)} 上传进度"></progress><span>等待上传 · ${learningFileSize(file.size)}</span></div>`).join('')}</div>`;
  const summary = host.querySelector('[role="status"]');
  const rows = host.querySelectorAll('.learning-upload-file');
  const failed = [];
  for (const [index, file] of files.entries()) {
    summary.textContent = `正在上传 ${index + 1} / ${files.length} 个附件`;
    const progress = rows[index].querySelector('progress');
    const label = rows[index].querySelector('span');
    try {
      const form = new FormData();
      form.append('file', file);
      await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url);
        const token = localStorage.getItem('token');
        if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
        label.textContent = '准备上传…';
        xhr.upload.onprogress = event => {
          if (!event.lengthComputable) { progress.removeAttribute('value'); label.textContent = '正在上传…'; return; }
          const percent = Math.round(event.loaded / event.total * 100);
          progress.value = percent;
          label.textContent = percent === 100 ? '上传完成，服务器处理中…' : `上传中 ${percent}%`;
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) return resolve();
          let message = xhr.status === 401 ? '登录已过期，请重新登录后重试' : `上传失败（HTTP ${xhr.status}）`;
          try { message = JSON.parse(xhr.responseText).detail || message; } catch {}
          reject(new Error(message));
        };
        xhr.onerror = () => reject(new Error('网络异常，请重试'));
        xhr.onabort = () => reject(new Error('上传已取消'));
        xhr.send(form);
      });
      progress.value = 100;
      label.textContent = '上传成功';
      rows[index].classList.add('is-success');
    } catch (error) {
      failed.push(file);
      label.textContent = error.message;
      rows[index].classList.add('is-error');
    }
  }
  summary.textContent = `上传结束：成功 ${files.length - failed.length} / ${files.length}${failed.length ? `，失败 ${failed.length} 个，可重试` : ''}`;
  return failed;
}

async function deleteLearningContent(id) {
  const item = learningContentsCache.find(x => x.id === Number(id));
  const linked = lrnQuizzesFor(id).length;
  const message = `删除「${item?.title || '这项内容'}」及其全部附件，删除后无法恢复。${linked ? `\n关联的 ${linked} 个答题活动会保留，变为独立答题活动。` : ''}`;
  if (!(await learningConfirm({ danger: true, title: `删除${LRN_KIND[item?.kind] || '学习内容'}`, message }))) return;
  try {
    await api(`/learning/contents/${id}`, {method:'DELETE'});
    showMessage('已删除');
    learningReturnContentId = null;
    showLearningPanel('learningHome');
    loadLearningCenter();
  } catch(e) { showMessage(e.message, 'error'); }
}

async function downloadLearningContentFile(fileId) {
  const file = findLearningContentFile(fileId);
  try {
    const response = await api(`/learning/content-files/${fileId}/download`);
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = file?.original_name || 'download';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 60000);
  } catch(e) { showMessage(e.message || '附件下载失败', 'error'); }
}

async function deleteLearningContentFile(fileId, contentId) {
  const file = findLearningContentFile(fileId);
  if (!(await learningConfirm({ danger: true, title: '删除附件', message: `删除「${file?.original_name || '附件'}」，删除后无法恢复。` }))) return;
  try {
    await api(`/learning/content-files/${fileId}`, {method:'DELETE'});
    await loadLearningCenter();
    showLearningContentForm(contentId);
    showMessage('附件已删除');
  } catch(e) { showMessage(e.message || '附件删除失败', 'error'); }
}

// ============================ 课件预览窗 ============================
// 同一门课程的课件可以在窗内左侧切换，←/→ 翻到上一个 / 下一个可预览课件；Markdown 渲染为排版后的文档，可切回源文本。

const learningPreview = { content: null, files: [], fileId: null, raw: false, max: false, seq: 0, opener: null, text: '' };

function lrnPreviewModal() {
  const modal = document.getElementById('learningFilePreviewModal');
  // 页签 section 带切换动效（transform），fixed 定位会相对它偏移，首次打开时挂到 body
  if (modal && modal.parentElement !== document.body) {
    document.body.appendChild(modal);
    modal.addEventListener('click', e => {
      if (e.target.closest('[data-lrn-pv-close]')) { closeLearningFilePreview(); return; }
      const nav = e.target.closest('[data-lrn-pv-file]');
      if (nav) { lrnShowPreviewFile(Number(nav.dataset.lrnPvFile)); return; }
      const act = e.target.closest('[data-lrn-pv]');
      if (!act) return;
      const kind = act.dataset.lrnPv;
      if (kind === 'prev') lrnStepPreview(-1);
      else if (kind === 'next') lrnStepPreview(1);
      else if (kind === 'download') downloadLearningContentFile(learningPreview.fileId);
      else if (kind === 'open' && learningPreviewObjectUrl) window.open(learningPreviewObjectUrl, '_blank', 'noopener');
      else if (kind === 'raw') { learningPreview.raw = !learningPreview.raw; lrnRenderMarkdown(); lrnRenderPreviewTools(); }
      else if (kind === 'max') { learningPreview.max = !learningPreview.max; modal.classList.toggle('is-max', learningPreview.max); lrnRenderPreviewTools(); }
      else if (kind === 'dl-file') downloadLearningContentFile(Number(act.dataset.id));
    });
    document.addEventListener('keydown', e => {
      if (modal.classList.contains('hidden')) return;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeLearningFilePreview(); return; }
      if (e.target.closest?.('input, textarea, select')) return;
      if (e.key === 'ArrowLeft') { e.preventDefault(); lrnStepPreview(-1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); lrnStepPreview(1); }
    }, true);
  }
  return modal;
}

function lrnPreviewable() {
  return learningPreview.files.filter(f => f.previewable);
}

function lrnStepPreview(delta) {
  const list = lrnPreviewable();
  if (list.length < 2) return;
  const index = list.findIndex(f => f.id === learningPreview.fileId);
  const next = list[(index + delta + list.length) % list.length];
  lrnShowPreviewFile(next.id);
}

async function previewLearningContentFile(fileId) {
  const file = findLearningContentFile(fileId);
  if (!file?.previewable) return showMessage('该附件不支持在线预览，请下载查看', 'error');
  const modal = lrnPreviewModal();
  if (!modal) return;
  const content = lrnContentOfFile(fileId);
  learningPreview.content = content;
  learningPreview.files = content?.files || [file];
  if (modal.classList.contains('hidden')) {
    learningPreview.opener = document.activeElement;
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    const html = document.documentElement;
    if (!html.classList.contains('sd-locked')) html.classList.toggle('sd-gutter', window.innerWidth > html.clientWidth);
    html.classList.add('sd-locked', 'lrn-pv-lock');
    requestAnimationFrame(() => requestAnimationFrame(() => modal.classList.add('is-open')));
    modal.querySelector('.learning-preview-dialog')?.focus({ preventScroll: true });
  }
  await lrnShowPreviewFile(file.id);
}

async function lrnShowPreviewFile(fileId) {
  const file = learningPreview.files.find(f => f.id === Number(fileId)) || findLearningContentFile(fileId);
  if (!file) return;
  if (!file.previewable) { downloadLearningContentFile(file.id); return; }
  const seq = ++learningPreview.seq;
  learningPreview.fileId = file.id;
  learningPreview.raw = false;
  learningPreview.text = '';
  const body = document.getElementById('learningFilePreviewBody');
  const kind = lrnFileKind(file);
  const icon = document.getElementById('learningFilePreviewIcon');
  if (icon) { icon.textContent = kind.label; icon.className = `lrn-ficon lrn-ficon--${kind.tone}`; }
  learningFilePreviewTitle.textContent = file.original_name;
  const meta = document.getElementById('learningFilePreviewMeta');
  if (meta) meta.textContent = [learningPreview.content?.title, learningFileSize(file.file_size), lrnPreviewNote(file)].filter(Boolean).join(' · ');
  lrnRenderPreviewNav();
  if (learningPreviewObjectUrl) { URL.revokeObjectURL(learningPreviewObjectUrl); learningPreviewObjectUrl = null; }
  lrnRenderPreviewTools();
  body.innerHTML = `<div class="lrn-pv-state"><span class="lrn-spin" aria-hidden="true"></span><b>正在加载预览…</b><span>${kind.tone === 'ppt' ? '演示文稿已转换为 PDF，翻页更流畅' : '大文件可能需要几秒'}</span></div>`;
  try {
    const response = await api(`/learning/content-files/${file.id}/preview`);
    if (seq !== learningPreview.seq) return;
    if (file.preview_kind === 'markdown') {
      learningPreview.text = await response.text();
      if (seq !== learningPreview.seq) return;
      lrnRenderMarkdown();
      lrnRenderPreviewTools();
      return;
    }
    const blob = await response.blob();
    if (seq !== learningPreview.seq) return;
    learningPreviewObjectUrl = URL.createObjectURL(blob);
    body.innerHTML = `<iframe src="${learningPreviewObjectUrl}" aria-label="${learningEsc(file.original_name)}"></iframe>`;
    lrnRenderPreviewTools();
  } catch(e) {
    if (seq !== learningPreview.seq) return;
    body.innerHTML = `<div class="lrn-pv-state is-error">${lrnIc('alert')}<b>${learningEsc(e.message || '预览失败')}</b><span>可以先下载到本地查看</span><button type="button" data-lrn-pv="download">${lrnIc('download')}下载附件</button></div>`;
  }
}

function lrnRenderPreviewNav() {
  const nav = document.getElementById('learningFilePreviewNav');
  const modal = document.getElementById('learningFilePreviewModal');
  const files = learningPreview.files;
  modal?.classList.toggle('is-single', files.length < 2);
  if (!nav) return;
  if (files.length < 2) { nav.innerHTML = ''; return; }
  nav.innerHTML = `<div class="lrn-pv-files-head">课件 <b>${files.length}</b></div>` + files.map((f, i) => {
    const k = lrnFileKind(f);
    const on = f.id === learningPreview.fileId;
    return f.previewable
      ? `<button type="button" class="lrn-pv-file${on ? ' is-active' : ''}" data-lrn-pv-file="${f.id}" aria-current="${on ? 'true' : 'false'}"><span class="lrn-ficon lrn-ficon--${k.tone}">${k.label}</span><span class="lrn-pv-file-name"><b>${i + 1}. ${learningEsc(f.original_name)}</b><small>${learningFileSize(f.file_size)}</small></span></button>`
      : `<div class="lrn-pv-file is-disabled"><span class="lrn-ficon lrn-ficon--${k.tone}">${k.label}</span><span class="lrn-pv-file-name"><b>${i + 1}. ${learningEsc(f.original_name)}</b><small>不支持预览</small></span><button type="button" class="lrn-pv-dl" data-lrn-pv="dl-file" data-id="${f.id}" title="下载" aria-label="下载 ${learningEsc(f.original_name)}">${lrnIc('download')}</button></div>`;
  }).join('');
  nav.querySelector('.is-active')?.scrollIntoView({ block: 'nearest' });
}

function lrnRenderPreviewTools() {
  const tools = document.getElementById('learningFilePreviewTools');
  if (!tools) return;
  const file = learningPreview.files.find(f => f.id === learningPreview.fileId);
  const list = lrnPreviewable();
  const index = list.findIndex(f => f.id === learningPreview.fileId);
  const md = file?.preview_kind === 'markdown';
  tools.innerHTML = `
    ${list.length > 1 ? `<div class="lrn-pv-step"><button type="button" data-lrn-pv="prev" title="上一个课件（←）" aria-label="上一个课件">${lrnIc('chevron-left')}</button><span>${index + 1} / ${list.length}</span><button type="button" data-lrn-pv="next" title="下一个课件（→）" aria-label="下一个课件">${lrnIc('chevron-right')}</button></div>` : ''}
    ${md ? `<button type="button" class="lrn-pv-tool${learningPreview.raw ? ' is-on' : ''}" data-lrn-pv="raw" aria-pressed="${learningPreview.raw}">${learningPreview.raw ? '排版视图' : '源文本'}</button>` : ''}
    ${!md && learningPreviewObjectUrl ? `<button type="button" class="lrn-pv-tool" data-lrn-pv="open" title="在新窗口中打开">${lrnIc('external')}新窗口</button>` : ''}
    <button type="button" class="lrn-pv-tool" data-lrn-pv="max" aria-pressed="${learningPreview.max}" title="${learningPreview.max ? '还原窗口' : '铺满屏幕'}">${lrnIc(learningPreview.max ? 'minimize' : 'layers')}${learningPreview.max ? '还原' : '铺满'}</button>
    <button type="button" class="lrn-pv-tool" data-lrn-pv="download">${lrnIc('download')}下载</button>`;
}

function lrnRenderMarkdown() {
  const body = document.getElementById('learningFilePreviewBody');
  if (!body) return;
  body.innerHTML = learningPreview.raw
    ? `<pre class="learning-markdown-preview">${learningEsc(learningPreview.text)}</pre>`
    : `<article class="lrn-md">${lrnMarkdown(learningPreview.text) || '<p class="muted">文档为空</p>'}</article>`;
}

// 轻量 Markdown 渲染：先整体转义再做排版，链接只放行 http(s)
function lrnMarkdown(source) {
  const inline = text => {
    const codes = [];
    let s = learningEsc(text).replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g, (_, alt) => `<span class="lrn-md-img">[图片${alt ? `：${alt}` : ''}]</span>`)
      .replace(/\[([^\]]+)\]\(([^)\s]+)[^)]*\)/g, (m, label, url) => /^https?:\/\//i.test(url.replace(/&amp;/g, '&')) ? `<a href="${url}" target="_blank" rel="noopener">${label}</a>` : label)
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/__([^_]+)__/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
      .replace(/~~([^~]+)~~/g, '<del>$1</del>');
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
  };
  const lines = String(source || '').replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let i = 0;
  const isTableSep = l => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(l);
  const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(`<pre><code>${learningEsc(buf.join('\n'))}</code></pre>`);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2].replace(/\s+#+\s*$/, ''))}</h${h[1].length}>`); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push('<hr>'); i++; continue; }
    if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push(`<div class="lrn-md-table"><table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
      out.push(`<blockquote>${buf.map(inline).join('<br>')}</blockquote>`);
      continue;
    }
    const listRe = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
    if (listRe.test(line)) {
      const ordered = /\d/.test(line.match(listRe)[2]);
      const items = [];
      while (i < lines.length && listRe.test(lines[i])) {
        const m = lines[i].match(listRe);
        const task = m[3].match(/^\[( |x|X)\]\s+(.*)$/);
        const depth = Math.min(3, Math.floor(m[1].replace(/\t/g, '  ').length / 2));
        items.push(`<li${depth ? ` class="lrn-md-d${depth}"` : ''}>${task ? `<span class="lrn-md-task${task[1].trim() ? ' is-done' : ''}" aria-hidden="true"></span>${inline(task[2])}` : inline(m[3])}</li>`);
        i++;
      }
      out.push(`<${ordered ? 'ol' : 'ul'}>${items.join('')}</${ordered ? 'ol' : 'ul'}>`);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*```|\s*>|(\s*)([-*+]|\d+[.)])\s+)/.test(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    out.push(`<p>${para.map(inline).join('<br>')}</p>`);
  }
  return out.join('');
}

function closeLearningFilePreview() {
  const modal = document.getElementById('learningFilePreviewModal');
  if (!modal || modal.classList.contains('hidden')) return;
  learningPreview.seq++;
  modal.classList.remove('is-open');
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  learningFilePreviewBody.innerHTML = '';
  const html = document.documentElement;
  html.classList.remove('lrn-pv-lock');
  if (!document.querySelector('.sd-root:not([hidden])')) html.classList.remove('sd-locked', 'sd-gutter');
  if (learningPreviewObjectUrl) {
    URL.revokeObjectURL(learningPreviewObjectUrl);
    learningPreviewObjectUrl = null;
  }
  if (learningPreview.opener?.isConnected) learningPreview.opener.focus({ preventScroll: true });
}

// ============================ 答题活动：编辑器 ============================

async function openQuizEditor(id = null, opts = {}) {
  showLearningPanel('quizEditor');
  quizEditId.value = id || '';
  quizEditorTitle.textContent = id ? '编辑答题活动' : (opts.contentId ? '新建随课答题' : '创建答题活动');
  quizTitle.value = '';
  quizDescription.value = '';
  quizQuestionEditors.innerHTML = '';
  lrnFillQuizContentSelect(opts.contentId || '');
  if (!id) {
    const owner = opts.contentId ? learningContentsCache.find(x => x.id === Number(opts.contentId)) : null;
    if (owner) quizTitle.value = `${owner.title} · 随堂测验`;
    addQuestionEditor('single_choice');
    return;
  }
  try {
    const quiz = await api(`/learning/quizzes/${id}`).then(r => r.json());
    quizTitle.value = quiz.title;
    quizDescription.value = quiz.description || '';
    lrnFillQuizContentSelect(quiz.content_id || '');
    quiz.questions.forEach(addQuestionEditorFromData);
  } catch(e) { showMessage(e.message, 'error'); closeLearningPanels(); }
}

function lrnFillQuizContentSelect(selected) {
  const select = document.getElementById('quizContentId');
  if (!select) return;
  const sorted = [...learningContentsCache].sort((a, b) => (a.kind === b.kind ? 0 : a.kind === 'course' ? -1 : 1));
  select.innerHTML = '<option value="">不关联（独立答题活动）</option>' + sorted.map(item =>
    `<option value="${item.id}">${LRN_KIND[item.kind] || '资料'} · ${learningEsc(item.title)}${item.published ? '' : '（未发布）'}</option>`).join('');
  select.value = selected ? String(selected) : '';
}

function addQuestionEditor(type) {
  addQuestionEditorFromData({question_type:type, prompt:'', score:1, options:['','','',''], correct_answers:[], fill_grading_mode:'exact', match_count:1});
  const card = document.getElementById('quizQuestionEditors').lastElementChild;
  card.scrollIntoView({block:'center', behavior:'smooth'});
  card.querySelector('.qe-prompt').focus({preventScroll:true});
}

let questionEditorSequence = 0;

function addQuestionEditorFromData(q) {
  const host = document.getElementById('quizQuestionEditors');
  const el = document.createElement('div');
  el.className = 'question-editor';
  el.dataset.type = q.question_type;
  el.dataset.editorId = String(++questionEditorSequence);
  const choice = ['single_choice','multiple_choice'].includes(q.question_type);
  const fill = q.question_type === 'fill_blank';
  const modeOptions = [
    ['exact','必须一字不差'],['contains','填写内容连续包含答案'],['normalized','去除标点/空格/符号后匹配'],['match_any','与任一参考答案匹配'],['match_count','匹配指定数量的参考答案']
  ].map(([v,t]) => `<option value="${v}" ${q.fill_grading_mode===v?'selected':''}>${t}</option>`).join('');
  el.innerHTML = `<div class="question-editor-head"><strong>${learningType(q.question_type)}</strong><button type="button" class="secondary qe-remove-question">删除题目</button></div>
    <div class="question-editor-grid">
      <label class="wide">题干<textarea class="qe-prompt" rows="3" placeholder="在这里输入题目内容…">${learningEsc(q.prompt)}</textarea></label>
      ${fill ? '<div class="wide qe-blank-tools"><button type="button" class="secondary qe-insert-blank">＋ 插入填空位</button><span class="muted">在光标处插入；选中文字后点击，可将文字转为参考答案。</span></div>' : ''}
      <label class="qe-score-field">分值<input class="qe-score" type="number" min="0.5" max="10000" step="0.5" value="${learningEsc(q.score ?? 1)}"></label>
      ${choice ? `<div class="wide qe-section"><div class="qe-section-head"><strong>选项与正确答案</strong><span class="muted">${q.question_type === 'single_choice' ? '点击圆圈设为正确答案（单选）' : '勾选所有正确答案（多选）'}</span></div><div class="qe-option-list"></div><button type="button" class="secondary qe-add-option">＋ 添加选项</button></div>` : ''}
      ${fill ? `<div class="wide qe-section"><div class="qe-section-head"><strong>参考答案</strong><span class="muted">每个输入框填写一个答案，无需手动分隔</span></div><div class="qe-reference-list"></div><button type="button" class="secondary qe-add-reference">＋ 添加参考答案</button></div><label>自动批改方式<select class="qe-mode">${modeOptions}</select></label><label class="qe-count-field">至少匹配答案数<input class="qe-count" type="number" min="1" step="1" value="${learningEsc(q.match_count || 1)}"></label><p class="wide muted qe-mode-help"></p><p class="wide muted">多个空位的答案由答题人统一填写在答案框中，按本题的参考答案和批改方式评分，不按空位逐一计分。</p>` : ''}
      ${q.question_type === 'short_answer' ? '<p class="muted wide">简答题提交后由出题人手工评分。</p>' : ''}
    </div><details class="qe-preview"><summary>查看题目预览</summary><div class="qe-preview-body"></div></details><p class="qe-error" role="alert" hidden></p>`;
  host.appendChild(el);
  el.querySelector('.qe-remove-question').onclick = async () => {
    if (!(await learningConfirm({ danger: true, title: '删除题目', message: '这道题目里未保存的内容会被移除。', okText: '删除题目' }))) return;
    el.remove(); renumberQuestionEditors();
  };
  if (choice) {
    (q.options?.length ? q.options : ['','']).forEach(text => addQuizOption(el, text, (q.correct_answers || []).includes(text)));
    el.querySelector('.qe-add-option').onclick = () => addQuizOption(el, '', false, true);
  }
  if (fill) {
    (q.correct_answers?.length ? q.correct_answers : ['']).forEach(text => addQuizReference(el, text));
    el.querySelector('.qe-add-reference').onclick = () => addQuizReference(el, '', true);
    el.querySelector('.qe-insert-blank').onclick = () => insertQuizBlank(el);
    el.querySelector('.qe-mode').onchange = () => updateQuizFillMode(el);
    updateQuizFillMode(el);
  }
  el.addEventListener('input', () => { clearQuizQuestionError(el); updateQuizQuestionPreview(el); });
  el.addEventListener('change', () => updateQuizQuestionPreview(el));
  updateQuizQuestionPreview(el);
  renumberQuestionEditors();
}

function quizOptionLetter(index) {
  let label = '';
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) label = String.fromCharCode(65 + (n - 1) % 26) + label;
  return label;
}

function addQuizOption(el, text = '', checked = false, focus = false) {
  const row = document.createElement('div');
  row.className = 'qe-option-row';
  row.innerHTML = `<label class="qe-option-check"><input class="qe-option-correct" type="${el.dataset.type === 'single_choice' ? 'radio' : 'checkbox'}" name="qe-answer-${el.dataset.editorId}" ${checked ? 'checked' : ''}><span class="qe-option-letter"></span></label><textarea class="qe-option-text" rows="2" placeholder="输入选项内容，无需填写 A、B 编号">${learningEsc(text)}</textarea><button type="button" class="secondary qe-remove-option">删除</button>`;
  el.querySelector('.qe-option-list').appendChild(row);
  row.querySelector('.qe-remove-option').onclick = () => {
    row.remove(); refreshQuizOptionRows(el); clearQuizQuestionError(el); updateQuizQuestionPreview(el);
  };
  refreshQuizOptionRows(el);
  clearQuizQuestionError(el);
  updateQuizQuestionPreview(el);
  if (focus) row.querySelector('textarea').focus();
}

function refreshQuizOptionRows(el) {
  const rows = [...el.querySelectorAll('.qe-option-row')];
  rows.forEach((row, i) => {
    const letter = quizOptionLetter(i);
    row.querySelector('.qe-option-letter').textContent = letter;
    row.querySelector('input').setAttribute('aria-label', `将选项 ${letter} 设为正确答案`);
    row.querySelector('textarea').setAttribute('aria-label', `选项 ${letter} 内容`);
    const remove = row.querySelector('button');
    remove.disabled = rows.length <= 2;
    remove.setAttribute('aria-label', `删除选项 ${letter}`);
    remove.title = rows.length <= 2 ? '选择题至少保留两个选项' : `删除选项 ${letter}`;
  });
}

function addQuizReference(el, text = '', focus = false) {
  const row = document.createElement('div');
  row.className = 'qe-reference-row';
  row.innerHTML = `<span class="qe-reference-number"></span><textarea class="qe-reference-text" rows="2" placeholder="输入可接受的答案">${learningEsc(text)}</textarea><button type="button" class="secondary">删除</button>`;
  el.querySelector('.qe-reference-list').appendChild(row);
  row.querySelector('button').onclick = () => {
    row.remove(); refreshQuizReferences(el); clearQuizQuestionError(el); updateQuizQuestionPreview(el);
  };
  refreshQuizReferences(el);
  clearQuizQuestionError(el);
  if (focus) row.querySelector('textarea').focus();
}

function refreshQuizReferences(el) {
  const rows = [...el.querySelectorAll('.qe-reference-row')];
  rows.forEach((row, i) => {
    row.querySelector('span').textContent = i + 1;
    row.querySelector('textarea').setAttribute('aria-label', `参考答案 ${i + 1}`);
    row.querySelector('button').disabled = rows.length <= 1;
    row.querySelector('button').setAttribute('aria-label', `删除参考答案 ${i + 1}`);
  });
}

function insertQuizBlank(el) {
  const prompt = el.querySelector('.qe-prompt');
  const selected = prompt.value.slice(prompt.selectionStart, prompt.selectionEnd).trim();
  prompt.setRangeText(' ______ ', prompt.selectionStart, prompt.selectionEnd, 'end');
  prompt.focus();
  if (selected) {
    const refs = [...el.querySelectorAll('.qe-reference-text')];
    if (!refs.some(input => input.value.trim() === selected)) {
      const empty = refs.find(input => !input.value.trim());
      if (empty) empty.value = selected;
      else addQuizReference(el, selected);
    }
  }
  clearQuizQuestionError(el);
  updateQuizQuestionPreview(el);
}

function updateQuizFillMode(el) {
  const mode = el.querySelector('.qe-mode').value;
  el.querySelector('.qe-count-field').hidden = mode !== 'match_count';
  el.querySelector('.qe-mode-help').textContent = {
    exact:'填写内容与任一参考答案完全一致即可得分。',
    contains:'填写内容连续包含任一参考答案即可得分，不区分英文大小写。',
    normalized:'去除空白、标点和符号，统一全半角及英文大小写后，与任一参考答案一致即可得分。',
    match_any:'填写内容可用换行、逗号或分号分隔，规范化后命中任一参考答案即可得分。',
    match_count:'填写内容可用换行、逗号或分号分隔，至少命中指定数量的不同参考答案即可得分。'
  }[mode];
  clearQuizQuestionError(el);
}

function updateQuizQuestionPreview(el) {
  const body = el.querySelector('.qe-preview-body');
  if (!body) return;
  const rows = [...el.querySelectorAll('.qe-option-row')];
  rows.forEach(row => row.classList.toggle('is-correct', row.querySelector('input').checked));
  body.innerHTML = `<div class="qe-preview-prompt">${learningEsc(el.querySelector('.qe-prompt').value || '尚未填写题干')}</div>` +
    rows.map((row, i) => `<div class="qe-preview-option ${row.querySelector('input').checked ? 'is-correct' : ''}"><b>${quizOptionLetter(i)}.</b> ${learningEsc(row.querySelector('textarea').value || '（未填写选项）')}${row.querySelector('input').checked ? '<span class="qe-correct-tag">正确答案</span>' : ''}</div>`).join('') +
    (rows.length ? '' : '<div class="qe-preview-answer">答题区域：请输入答案</div>');
}

function clearQuizQuestionError(el) {
  el.classList.remove('has-error');
  const error = el.querySelector('.qe-error');
  if (error) { error.hidden = true; error.textContent = ''; }
}

function renumberQuestionEditors() {
  [...document.querySelectorAll('#quizQuestionEditors .question-editor')].forEach((el,i) => {
    const title = el.querySelector('.question-editor-head strong');
    title.textContent = `${i+1}. ${learningType(el.dataset.type)}`;
  });
  const summary = document.getElementById('quizEditorSummary');
  if (summary) summary.textContent = `共 ${document.querySelectorAll('#quizQuestionEditors .question-editor').length} 道题 · 保存草稿后可试答预览`;
}

async function saveQuizDraft() {
  const editors = [...document.querySelectorAll('#quizQuestionEditors .question-editor')];
  const contentValue = document.getElementById('quizContentId')?.value || '';
  const payload = {title:quizTitle.value.trim(), description:quizDescription.value.trim() || null, content_id: contentValue ? Number(contentValue) : null, questions:editors.map(el => ({
    question_type:el.dataset.type,
    prompt:el.querySelector('.qe-prompt').value.trim(),
    score:Number(el.querySelector('.qe-score').value),
    options:[...el.querySelectorAll('.qe-option-text')].map(input => input.value.trim()),
    correct_answers:el.querySelector('.qe-option-list')
      ? [...el.querySelectorAll('.qe-option-row')].filter(row => row.querySelector('.qe-option-correct').checked).map(row => row.querySelector('.qe-option-text').value.trim())
      : [...el.querySelectorAll('.qe-reference-text')].map(input => input.value.trim()),
    fill_grading_mode:el.querySelector('.qe-mode') ? el.querySelector('.qe-mode').value : null,
    match_count:el.querySelector('.qe-mode')?.value === 'match_count' ? Number(el.querySelector('.qe-count').value) : 1
  }))};
  if (!payload.title) { quizTitle.focus(); return showMessage('请填写答题活动标题', 'error'); }
  if (!payload.questions.length) return showMessage('请至少添加一道题目', 'error');
  for (let i = 0; i < payload.questions.length; i++) {
    const q = payload.questions[i];
    const el = editors[i];
    clearQuizQuestionError(el);
    let message = '', selector = '.qe-prompt';
    if (!q.prompt) message = '请填写题干';
    else if (!Number.isFinite(q.score) || q.score <= 0 || q.score > 10000) { message = '分值必须大于 0 且不超过 10000'; selector = '.qe-score'; }
    else if (q.options.length) {
      selector = '.qe-option-text';
      if (q.options.length < 2 || q.options.some(x => !x)) message = '请填写所有选项内容，不需要的选项可以删除';
      else if (new Set(q.options).size !== q.options.length) message = '选项内容不能重复';
      else if (!q.correct_answers.length || (q.question_type === 'single_choice' && q.correct_answers.length !== 1)) { message = '请选择正确答案'; selector = '.qe-option-correct'; }
    } else if (q.question_type === 'fill_blank') {
      selector = '.qe-reference-text';
      if (!q.correct_answers.length || q.correct_answers.some(x => !x)) message = '请填写参考答案，或删除多余的空答案框';
      else if (new Set(q.correct_answers).size !== q.correct_answers.length) message = '参考答案不能重复';
      else if (!Number.isInteger(q.match_count) || q.match_count < 1 || q.match_count > q.correct_answers.length) { message = '匹配数量必须为正整数，且不能超过参考答案数量'; selector = '.qe-count'; }
    }
    if (message) {
      el.classList.add('has-error');
      el.querySelector('.qe-error').textContent = message;
      el.querySelector('.qe-error').hidden = false;
      el.querySelector(selector)?.focus();
      el.scrollIntoView({behavior:'smooth', block:'center'});
      return showMessage(`第 ${i + 1} 题：${message}`, 'error');
    }
  }
  const id = quizEditId.value;
  try {
    await api(id ? `/learning/quizzes/${id}` : '/learning/quizzes', {method:id ? 'PUT' : 'POST', headers:H, body:JSON.stringify(payload)});
    // 随课答题保存后回到所属课程，能直接看到它挂在课程下面
    if (payload.content_id) learningReturnContentId = payload.content_id;
    showMessage('题目草稿已保存，开始前其他人不可见'); closeLearningPanels();
  } catch(e) { showMessage(e.message, 'error'); }
}

async function startLearningQuiz(id) {
  const quiz = learningQuizzesCache.find(q => q.id === Number(id));
  const hidden = quiz?.content && !quiz.content.published
    ? `\n关联的「${quiz.content.title}」尚未发布：成员能答题，但看不到课程内容。` : '';
  if (!(await learningConfirm({ icon: 'i-play', tone: 'green', title: '开始答题', message: `开始后题目对团队成员可见，且不能再编辑。${hidden}`, okText: '开始答题' }))) return;
  try { await api(`/learning/quizzes/${id}/start`, {method:'POST'}); showMessage('答题已开始'); loadLearningCenter(); }
  catch(e) { showMessage(e.message, 'error'); }
}

async function closeLearningQuiz(id) {
  if (!(await learningConfirm({ icon: 'i-ban', tone: 'red', title: '结束答题', message: '结束后未提交的成员将无法再提交。', okText: '结束答题' }))) return;
  try { await api(`/learning/quizzes/${id}/close`, {method:'POST'}); showMessage('答题已结束'); loadLearningCenter(); }
  catch(e) { showMessage(e.message, 'error'); }
}

async function deleteLearningQuiz(id) {
  if (!(await learningConfirm({ danger: true, title: '删除草稿', message: '删除这个答题草稿，删除后无法恢复。' }))) return;
  try { await api(`/learning/quizzes/${id}`, {method:'DELETE'}); showMessage('已删除'); loadLearningCenter(); }
  catch(e) { showMessage(e.message, 'error'); }
}

async function previewQuiz(id) {
  try {
    const existing = await api(`/learning/quizzes/${id}?preview=true`).then(r => r.json());
    if (existing.preview_submission_id) await api(`/learning/quizzes/${id}/preview`, {method:'DELETE'});
    await openTakeQuiz(id, true);
  } catch(e) { showMessage(e.message, 'error'); }
}

// ============================ 答题 ============================

async function openTakeQuiz(id, preview) {
  try {
    activeQuiz = await api(`/learning/quizzes/${id}${preview ? '?preview=true' : ''}`).then(r => r.json());
    activeQuizPreview = preview;
    showLearningPanel('quizTakePanel');
    takeQuizTitle.textContent = (preview ? '预览：' : '') + activeQuiz.title;
    takeQuizDescription.textContent = activeQuiz.description || '';
    takeQuizDescription.hidden = !activeQuiz.description;
    const meta = document.getElementById('takeQuizMeta');
    if (meta) {
      const total = activeQuiz.total_points ?? activeQuiz.questions.reduce((n, q) => n + Number(q.score || 0), 0);
      meta.innerHTML = `${preview ? '<span class="lrn-chip lrn-chip--draft">出题人预览，不计入成绩</span>' : '<span class="lrn-chip lrn-chip--warn">正式答卷只能提交一次</span>'}<span>${lrnIc('layers')}${activeQuiz.questions.length} 题 · 共 ${total} 分</span>${activeQuiz.content ? `<button type="button" class="lrn-chip lrn-chip--link" onclick="lrnLeaveTakeToContent(${activeQuiz.content.id})">${lrnIc(activeQuiz.content.kind === 'course' ? 'book' : 'file')}<span>随课 · ${learningEsc(activeQuiz.content.title)}</span></button>` : ''}`;
    }
    submitQuizButton.innerHTML = `${lrnIc('send')}${preview ? '提交预览答卷' : '确认提交（仅一次）'}`;
    takeQuizQuestions.innerHTML = activeQuiz.questions.map(q => renderTakeQuestion(q)).join('');
    takeQuizQuestions.oninput = updateTakeQuizProgress;
    takeQuizQuestions.onchange = updateTakeQuizProgress;
    updateTakeQuizProgress();
  } catch(e) { showMessage(e.message, 'error'); }
}

async function lrnLeaveTakeToContent(contentId) {
  if (lrnAnsweredCount() > 0 && !(await learningConfirm({ title: '离开答题', message: '已填写的答案不会保存，确定先去看课程内容吗？', okText: '去看课程' }))) return;
  if (!learningContentsCache.some(x => x.id === Number(contentId))) await loadLearningCenter();
  openLearningContent(contentId);
}

function lrnQuestionAnswered(q) {
  if (q.question_type === 'single_choice' || q.question_type === 'multiple_choice') return !!document.querySelector(`input[name="q_${q.id}"]:checked`);
  return !!document.getElementById(`answer_${q.id}`)?.value.trim();
}

function lrnAnsweredCount() {
  return activeQuiz ? activeQuiz.questions.filter(lrnQuestionAnswered).length : 0;
}

function updateTakeQuizProgress() {
  if (!activeQuiz) return;
  const total = activeQuiz.questions.length;
  const done = lrnAnsweredCount();
  activeQuiz.questions.forEach(q => document.querySelector(`.take-question[data-id="${q.id}"]`)?.classList.toggle('is-answered', lrnQuestionAnswered(q)));
  const text = document.getElementById('takeQuizProgressText');
  if (text) text.textContent = `已作答 ${done} / ${total}`;
  const bar = document.getElementById('takeQuizProgressBar');
  if (bar) bar.style.width = `${total ? Math.round(done * 100 / total) : 0}%`;
}

function renderTakeQuestion(q) {
  let input = '';
  if (q.question_type === 'single_choice' || q.question_type === 'multiple_choice') {
    const inputType = q.question_type === 'single_choice' ? 'radio' : 'checkbox';
    input = q.options.map((option, i) => `<label class="option-answer"><input type="${inputType}" name="q_${q.id}" value="${learningEsc(option)}"><span class="lrn-opt-letter">${quizOptionLetter(i)}</span><span class="lrn-opt-text">${learningEsc(option)}</span></label>`).join('');
  } else {
    input = `<textarea id="answer_${q.id}" rows="${q.question_type === 'short_answer' ? 5 : 2}" placeholder="${q.question_type === 'fill_blank' ? '请输入答案；有多个空位时可用换行、逗号或分号分隔' : '请输入答案'}"></textarea>`;
  }
  return `<div class="take-question lrn-tq" data-id="${q.id}" data-type="${q.question_type}">
    <div class="lrn-tq-head"><span class="lrn-tq-no">${q.position}</span><span class="lrn-tq-type">${learningType(q.question_type)}${q.question_type === 'multiple_choice' ? '（可多选）' : ''}</span><span class="lrn-tq-score">${q.score} 分</span></div>
    <strong class="learning-prompt">${learningEsc(q.prompt)}</strong>
    <div class="lrn-tq-body">${input}</div>
  </div>`;
}

async function submitCurrentQuiz() {
  if (!activeQuiz) return;
  const missing = activeQuiz.questions.length - lrnAnsweredCount();
  const warn = missing > 0 ? `还有 ${missing} 道题未作答。` : '';
  if (!(await learningConfirm(activeQuizPreview
    ? { icon: 'i-send', title: '提交预览答卷', message: `${warn}这是预览答卷，提交后可查看判分效果。`, okText: '提交' }
    : { icon: 'i-send', title: '提交答卷', message: `${warn}正式答卷只能提交一次，提交后不可修改。`, okText: '提交答卷' }))) return;
  const answers = activeQuiz.questions.map(q => {
    let answer;
    if (q.question_type === 'single_choice') answer = document.querySelector(`input[name="q_${q.id}"]:checked`)?.value ?? null;
    else if (q.question_type === 'multiple_choice') answer = [...document.querySelectorAll(`input[name="q_${q.id}"]:checked`)].map(x => x.value);
    else answer = document.getElementById(`answer_${q.id}`).value;
    return {question_id:q.id, answer};
  });
  submitQuizButton.disabled = true;
  try {
    await api(`/learning/quizzes/${activeQuiz.id}/submissions`, {method:'POST', headers:H, body:JSON.stringify({answers,is_preview:activeQuizPreview})});
    showMessage(activeQuizPreview ? '预览答卷已提交，可在批阅页检查' : '提交成功'); closeLearningPanels();
  } catch(e) { showMessage(e.message, 'error'); }
  finally { submitQuizButton.disabled = false; }
}

// ============================ 我的成绩 ============================

async function viewMyQuizResult(id) {
  try {
    const result = await api(`/learning/quizzes/${id}/my-result`).then(r => r.json());
    showLearningPanel('quizResultPanel');
    const quiz = learningQuizzesCache.find(q => q.id === Number(id));
    const title = document.getElementById('quizResultTitle');
    if (title) title.textContent = quiz ? `我的答卷：${quiz.title}` : '我的答卷与成绩';
    let head;
    if (result.released) {
      const rate = result.total_points ? Math.round(Number(result.score || 0) * 100 / result.total_points) : 0;
      const right = result.answers.filter(a => a.is_correct).length;
      head = `<div class="lrn-score"><div class="lrn-score-ring" style="--p:${rate}"><span>${rate}%</span></div><div><div class="quiz-score">${result.score} / ${result.total_points} 分</div><p>答对 ${right} / ${result.answers.length} 题${result.show_answers ? ' · 出题人已公布参考答案' : ''}</p></div></div>`;
    } else {
      head = '<div class="lrn-notice">' + lrnIc('clock') + '<span>答卷已提交，成绩尚未发布。可查看原题和自己的作答。</span></div>';
    }
    quizResultBody.innerHTML = head + result.answers.map((a, i) => {
      const tone = !result.released ? '' : a.is_correct ? ' is-right' : (a.awarded_score > 0 ? ' is-partial' : ' is-wrong');
      const chosen = Array.isArray(a.answer) ? a.answer : (a.answer == null ? [] : [a.answer]);
      const marks = (a.options || []).length ? { chosen, correct: result.show_answers ? (a.correct_answers || []) : [] } : null;
      return `<div class="result-answer lrn-ra${tone}">${renderLearningOriginal(a, i + 1, marks)}
        <p class="lrn-ra-line"><span>你的答案</span>${learningEsc(formatLearningAnswer(a.answer))}</p>
        ${result.released ? `<p class="lrn-ra-line lrn-ra-score">得分：${a.awarded_score} / ${a.max_score}</p>` : ''}
        ${a.reviewer_comment ? `<p class="lrn-ra-line"><span>批语</span>${learningEsc(a.reviewer_comment)}</p>` : ''}
        ${result.show_answers && (a.correct_answers || []).length ? `<p class="lrn-ra-line lrn-ra-ref">参考答案：${learningEsc(formatLearningAnswer(a.correct_answers))}</p>` : ''}
      </div>`;
    }).join('');
  } catch(e) { showMessage(e.message, 'error'); }
}

function lrnRefAnswer(answer) {
  return answer == null || answer === '' || (Array.isArray(answer) && !answer.length) ? '—（人工评分）' : formatLearningAnswer(answer);
}

function formatLearningAnswer(answer) {
  if (answer === null || answer === undefined || answer === '') return '（未作答）';
  if (Array.isArray(answer) && !answer.length) return '（未作答）';
  return Array.isArray(answer) ? answer.join('；') : String(answer);
}

// ============================ 批阅与统计 ============================

async function reviewLearningQuiz(id) {
  try {
    const [quiz, stats, submissions] = await Promise.all([
      api(`/learning/quizzes/${id}`).then(r => r.json()),
      api(`/learning/quizzes/${id}/stats`).then(r => r.json()),
      api(`/learning/quizzes/${id}/submissions`).then(r => r.json())
    ]);
    activeQuiz = quiz;
    showLearningPanel('quizReviewPanel');
    reviewQuizTitle.textContent = `批阅与统计：${quiz.title}`;
    const judged = stats.questions.filter(q => q.correct_rate !== null);
    const avg = judged.length ? Math.round(judged.reduce((n, q) => n + q.correct_rate, 0) / judged.length) : null;
    const pendingReview = submissions.filter(s => !s.is_preview && s.status !== 'graded').length;
    quizStatsBody.innerHTML = `
      <div class="lrn-review-meta"><span class="lrn-status lrn-status--${quiz.status}">${learningStatus(quiz.status)}</span>${quiz.content ? `<button type="button" class="lrn-chip lrn-chip--link" data-lrn-open="${quiz.content.id}">${lrnIc(quiz.content.kind === 'course' ? 'book' : 'file')}<span>随课 · ${learningEsc(quiz.content.title)}</span></button>` : '<span class="lrn-chip">独立答题活动</span>'}<span>${quiz.question_count} 题 · ${quiz.total_points} 分</span></div>
      <div class="lrn-kpis">
        <div><b>${stats.submitted_count}<small> / ${stats.expected_count}</small></b><span>已提交</span><div class="lrn-bar"><span style="width:${stats.expected_count ? Math.round(stats.submitted_count * 100 / stats.expected_count) : 0}%"></span></div></div>
        <div><b>${stats.pending_count}</b><span>未提交</span></div>
        <div><b>${stats.graded_count}</b><span>已批阅</span></div>
        <div class="${pendingReview ? 'is-warn' : ''}"><b>${pendingReview}</b><span>待人工批阅</span></div>
        <div><b>${avg === null ? '—' : `${avg}%`}</b><span>平均正确率</span></div>
      </div>
      <div class="lrn-section-head"><h3>逐题统计</h3><span class="lrn-section-note">正确率按自动判分与已批阅的答卷计算</span></div>
      <div class="lrn-qstats">${stats.questions.map(q => `<div class="lrn-qstat">${renderLearningOriginal(q, q.position, (q.options || []).length ? { correct: q.correct_answers || [] } : null)}
        ${(q.options || []).length || !(q.correct_answers || []).length ? '' : `<p class="lrn-ra-line lrn-ra-ref">参考答案：${learningEsc(formatLearningAnswer(q.correct_answers))}</p>`}
        <div class="lrn-qstat-rate"><span>作答 ${q.answered_count} 人 · ${q.correct_rate === null ? '待人工评分' : `正确率 ${q.correct_rate}%`}</span><div class="stats-bar lrn-bar${q.correct_rate !== null && q.correct_rate < 60 ? ' is-low' : ''}"><span style="width:${q.correct_rate || 0}%"></span></div></div>
        <details class="lrn-qstat-answers"><summary>查看所有人的答案（${q.answers.length}）</summary>${q.answers.map(a => `<p><b>${learningEsc(a.username)}</b><span>${learningEsc(formatLearningAnswer(a.answer))}</span><i class="lrn-mark ${a.is_correct === null ? 'is-pending' : a.is_correct ? 'is-right' : 'is-wrong'}">${a.is_correct === null ? '待评' : a.is_correct ? '正确' : '错误'}</i></p>`).join('') || '<p class="muted">暂无</p>'}</details>
      </div>`).join('')}</div>`;
    const release = quiz.status === 'closed'
      ? `<div class="lrn-release${quiz.results_released ? ' is-done' : ''}">${lrnIc(quiz.results_released ? 'check' : 'send')}<div><b>${quiz.results_released ? '成绩已发布' : '完成批阅后发布成绩'}</b><span>${quiz.results_released ? (quiz.show_answers ? '成员可以看到得分和参考答案' : '成员可以看到得分，参考答案未公开') : (pendingReview ? `还有 ${pendingReview} 份答卷待批阅` : '所有答卷已批阅，可以发布')}</span></div><div class="learning-item-actions"><button onclick="releaseLearningResults(${quiz.id},true)">发布成绩并展示答案</button><button class="secondary" onclick="releaseLearningResults(${quiz.id},false)">发布成绩但隐藏答案</button></div></div>`
      : (quiz.status === 'open' ? `<div class="lrn-release">${lrnIc('clock')}<div><b>答题进行中</b><span>结束答题后才能发布成绩</span></div></div>` : '');
    quizSubmissionsBody.innerHTML = `<div class="lrn-section-head"><h3>答卷批阅</h3><span class="lrn-section-note">${submissions.length} 份${pendingReview ? ` · 待批阅 ${pendingReview}` : ''}</span></div>` + release + (submissions.map(renderReviewSubmission).join('') || lrnEmpty('check', '暂时没有答卷', '成员提交后会出现在这里'));
  } catch(e) { showMessage(e.message, 'error'); }
}

function renderReviewSubmission(s) {
  return `<div class="review-submission lrn-sub" data-submission="${s.id}"><div class="lrn-sub-head"><h4>${learningEsc(s.username)}</h4>${s.is_preview ? '<span class="lrn-chip lrn-chip--draft">出题人预览</span>' : ''}<span class="lrn-status ${s.status === 'graded' ? 'lrn-status--graded' : 'lrn-status--pending'}">${s.status === 'graded' ? `已批阅 ${s.total_score} 分` : '待批阅'}</span><span class="lrn-sub-time">${lrnDate(s.submitted_at)} 提交</span></div>
    <div class="learning-answer-table"><table><thead><tr><th>题目/答案</th><th>参考答案</th><th>得分</th><th>批语</th></tr></thead><tbody>${s.answers.map(a => `<tr data-question="${a.question_id}" class="${a.is_correct === null ? 'is-pending' : a.is_correct ? 'is-right' : 'is-wrong'}"><td>${renderLearningOriginal(a,a.position)}<p class="lrn-ra-line"><span>作答</span>${learningEsc(formatLearningAnswer(a.answer))}</p></td><td>${learningEsc(lrnRefAnswer(a.correct_answers))}</td><td><input class="review-score" type="number" min="0" max="${a.max_score}" step="0.5" value="${a.awarded_score ?? ''}" placeholder="/${a.max_score}" aria-label="第 ${a.position} 题得分，满分 ${a.max_score}"></td><td><input class="review-comment" value="${learningEsc(a.reviewer_comment || '')}" placeholder="可选" aria-label="第 ${a.position} 题批语"></td></tr>`).join('')}</tbody></table></div><div class="lrn-sub-foot"><button onclick="gradeLearningSubmission(${activeQuiz.id},${s.id})">${lrnIc('check')}保存批阅</button></div></div>`;
}

async function gradeLearningSubmission(quizId, submissionId) {
  const card = document.querySelector(`[data-submission="${submissionId}"]`);
  const grades = [...card.querySelectorAll('tr[data-question]')].map(row => ({question_id:Number(row.dataset.question), awarded_score:row.querySelector('.review-score').value === '' ? NaN : Number(row.querySelector('.review-score').value), reviewer_comment:row.querySelector('.review-comment').value || null}));
  if (grades.some(g => Number.isNaN(g.awarded_score))) return showMessage('请为所有题目填写得分', 'error');
  try { await api(`/learning/quizzes/${quizId}/submissions/${submissionId}/grade`, {method:'PUT', headers:H, body:JSON.stringify({grades})}); showMessage('批阅已保存'); reviewLearningQuiz(quizId); }
  catch(e) { showMessage(e.message, 'error'); }
}

async function releaseLearningResults(id, showAnswers) {
  if (!(await learningConfirm({ icon: 'i-check', tone: 'green', title: '发布成绩', message: `发布成绩，并${showAnswers ? '展示' : '隐藏'}参考答案。`, okText: '发布成绩' }))) return;
  try { await api(`/learning/quizzes/${id}/release`, {method:'POST', headers:H, body:JSON.stringify({show_answers:showAnswers})}); showMessage('成绩已发布'); reviewLearningQuiz(id); }
  catch(e) { showMessage(e.message, 'error'); }
}
