import { wireImageViewer, closeImageViewer } from './image-viewer.js';
import { api } from '../api.js';
import { renderTaskStoryChangeBadge } from '../utils.js?v=20261014-2';

// Unified preview modal for Zentao stories / bugs / testcases / tasks.
// Exposes:
//   window.OmniQAPreview = { openStory(id), openBug(id), openTestcase(id), openTask(id),
//                            openTaskStoryChange(id), prefetch(kind, id), close() }
//   window.OmniQAStoryPreview = { open(id) }  (backwards-compat alias)
//
// 2026-11-24 v2：
//  - 外观改为 weave.css R10（.epv-*）：吸顶标题栏、信息格、分区卡片、附件画廊、流转时间线；
//  - 所有图片（正文内联图 + 附件缩略图）都可点击放大（image-viewer.js）；
//  - 网络：同一实体的并发请求合并；60 秒内直接用缓存，10 分钟内先显示缓存再后台刷新；
//    鼠标在预览按钮上停留 120ms 即预取，点开时多半已就绪。
//  - 测试工作台原有的独立 Bug 预览弹窗（overall-test.js）已并入这里。

const MODAL_ID = 'entityPreviewModal';
const FRESH_MS = 60_000;
const KEEP_MS = 10 * 60_000;
const PREFETCH_DELAY_MS = 120;
const _cache = { story: new Map(), bug: new Map(), testcase: new Map(), task: new Map() };
const _inflight = new Map();
let _previewGeneration = 0;
let _current = null; // { kind, id }
let _lastFocus = null;
const _previewImageUrls = new Set();
function releasePreviewImages() {
  closeImageViewer();
  _previewImageUrls.forEach(url => URL.revokeObjectURL(url));
  _previewImageUrls.clear();
}
const _storyChangeSubmitting = new Set();

const _ENDPOINT = {
  story: (id) => `/zentao/story/${id}/detail`,
  // /zentao/bugs/{id}/preview 返回 normalize_bug_detail + 改写后的附件地址 + 流转记录
  bug: (id) => `/zentao/bugs/${id}/preview`,
  testcase: (id) => `/zentao/testcase/${id}/detail`,
  task: (id) => `/zentao/task/${id}/detail`,
};

function _cacheGet(type, id) {
  const entry = _cache[type]?.get(id);
  if (!entry) return undefined;
  const age = Date.now() - entry.at;
  if (age > KEEP_MS) { _cache[type].delete(id); return undefined; }
  return { data: entry.data, fresh: age <= FRESH_MS };
}
function _cacheSet(type, id, data) {
  _cache[type]?.set(id, { data, at: Date.now() });
}

/** 拉取详情（同一实体同时只有一个请求在途） */
function _fetchDetail(kind, id) {
  const key = `${kind}:${id}`;
  if (_inflight.has(key)) return _inflight.get(key);
  const promise = (async () => {
    const res = await api(_ENDPOINT[kind](id));
    const data = await res.json();
    if (!data || data.error) throw new Error(data?.message || '加载失败');
    _cacheSet(kind, id, data);
    return data;
  })();
  _inflight.set(key, promise);
  promise.then(() => _inflight.delete(key), () => _inflight.delete(key));
  return promise;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const _ALLOWED_TAGS = new Set([
  'A','B','BLOCKQUOTE','BR','CODE','DIV','EM','H1','H2','H3','H4','H5','H6',
  'HR','I','IMG','LI','OL','P','PRE','SPAN','STRONG','SUB','SUP','TABLE',
  'TBODY','TD','TH','THEAD','TR','U','UL','FONT',
]);
const _ALLOWED_ATTRS = new Set([
  'href','target','rel','src','alt','title','width','height','colspan','rowspan',
  'align','valign','style','color','size','face',
]);
const _ALLOWED_STYLE_PROPS = new Set([
  'font-size','font-family','font-weight','font-style','color',
  'background-color','text-align','text-decoration','line-height',
  'padding','padding-left','padding-right','padding-top','padding-bottom',
  'margin','margin-left','margin-right','margin-top','margin-bottom',
  'width','height','border','border-color','border-style','border-width',
  'list-style','vertical-align','white-space',
]);

function _sanitizeStyle(style) {
  if (!style) return '';
  const safe = [];
  style.split(';').forEach((decl) => {
    const idx = decl.indexOf(':');
    if (idx < 0) return;
    const prop = decl.slice(0, idx).trim().toLowerCase();
    const val = decl.slice(idx + 1).trim();
    if (!prop || !val) return;
    if (/(expression|javascript:|url\s*\()/i.test(val)) return;
    if (!_ALLOWED_STYLE_PROPS.has(prop)) return;
    safe.push(`${prop}:${val}`);
  });
  return safe.join(';');
}

function _sanitize(html) {
  if (!html) return '';
  const doc = new DOMParser().parseFromString(`<div id="__root">${html}</div>`, 'text/html');
  const root = doc.getElementById('__root');
  const walk = (node) => {
    const children = Array.from(node.childNodes);
    for (const child of children) {
      if (child.nodeType === 1) {
        const tag = child.tagName.toUpperCase();
        if (!_ALLOWED_TAGS.has(tag)) {
          while (child.firstChild) node.insertBefore(child.firstChild, child);
          node.removeChild(child);
          continue;
        }
        for (const attr of Array.from(child.attributes)) {
          const name = attr.name.toLowerCase();
          if (!_ALLOWED_ATTRS.has(name) && !name.startsWith('data-')) {
            child.removeAttribute(attr.name);
            continue;
          }
          if (name === 'href' || name === 'src') {
            const v = (attr.value || '').trim();
            if (/^\s*(javascript|data|vbscript):/i.test(v)) {
              child.removeAttribute(attr.name);
              continue;
            }
          }
          if (name === 'style') {
            const cleaned = _sanitizeStyle(attr.value);
            if (cleaned) child.setAttribute('style', cleaned);
            else child.removeAttribute('style');
          }
        }
        if (tag === 'A') {
          child.setAttribute('target', '_blank');
          child.setAttribute('rel', 'noopener noreferrer');
        }
        walk(child);
      } else if (child.nodeType === 8) {
        node.removeChild(child);
      }
    }
  };
  walk(root);
  return root.innerHTML;
}

// Replace <img src="/zentao/files/..."> with blob URLs fetched through the
// authenticated api() wrapper, so the proxy endpoint sees our Bearer token.
// Also rewrite any wrapping <a href="/zentao/files/..."> to the blob URL so
// clicking the thumbnail to view the full image doesn't hit the proxy
// un-authenticated (which would render {"detail":"Not authenticated"}).
// 后端对 /zentao/files/{id} 返回 Cache-Control: private, max-age=…，重复打开时走浏览器缓存。
async function _loadProxyImages(containerEl) {
  if (!containerEl) return;
  wireImageViewer(containerEl);
  const generation = _previewGeneration;
  const imgs = Array.from(containerEl.querySelectorAll('img[src^="/zentao/files/"]'));
  await Promise.all(imgs.map(img => {
    img.classList.add('is-loading');
    img._previewImagePromise = (async () => {
      const src = img.getAttribute('src');
      try {
        const resp = await api(src);
        const blob = await resp.blob();
        if (generation !== _previewGeneration || !img.isConnected) return;
        const objUrl = URL.createObjectURL(blob);
        _previewImageUrls.add(objUrl);
        img.src = objUrl;
        const anchor = img.closest('a[href^="/zentao/files/"]');
        if (anchor) {
          anchor.href = objUrl;
          anchor.target = '_blank';
          anchor.rel = 'noopener noreferrer';
        }
      } catch {
        img.dataset.previewImageError = '1';
        img.alt = '图片加载失败';
        img.classList.add('is-broken');
      } finally {
        img.classList.remove('is-loading');
      }
    })();
    return img._previewImagePromise;
  }));
}

function _filenameFromUrl(url) {
  try {
    const path = String(url).split('?')[0].split('#')[0];
    const tail = path.substring(path.lastIndexOf('/') + 1);
    return decodeURIComponent(tail) || 'download';
  } catch {
    return 'download';
  }
}

// Non-image attachment links point at the authed /zentao/files/ proxy. A raw
// <a href> navigation carries no Bearer token, so the proxy answers with
// {"detail":"Not authenticated"}. Intercept the click, fetch the file through
// the api() wrapper, and trigger a real download from the resulting blob.
function _wireProxyDownloads(containerEl) {
  if (!containerEl) return;
  const links = Array.from(containerEl.querySelectorAll('a[href^="/zentao/files/"]'));
  links.forEach((a) => {
    if (a.dataset.proxyWired || a.querySelector('img')) return; // images handled by _loadProxyImages
    a.dataset.proxyWired = '1';
    const url = a.getAttribute('href');
    const filename = a.getAttribute('data-filename') || _filenameFromUrl(url);
    a.addEventListener('click', async (e) => {
      e.preventDefault();
      a.classList.add('is-busy');
      try {
        const resp = await api(url);
        const blob = await resp.blob();
        const objUrl = URL.createObjectURL(blob);
        const tmp = document.createElement('a');
        tmp.href = objUrl;
        tmp.download = filename;
        document.body.appendChild(tmp);
        tmp.click();
        tmp.remove();
        setTimeout(() => URL.revokeObjectURL(objUrl), 10000);
      } catch (err) {
        const msg = '文件下载失败：' + (err?.message || '未知错误');
        if (window.showMessage) window.showMessage(msg, 'error'); else alert(msg);
      } finally {
        a.classList.remove('is-busy');
      }
    });
  });
}

function _fmtDate(s) {
  if (!s) return '';
  return String(s).slice(0, 19).replace('T', ' ');
}
const _shortDate = (s) => _fmtDate(s).slice(0, 16);

// ─── 外壳 ────────────────────────────────────────────────────────────────────
const _ICON = {
  x: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',
  external: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>',
  doc: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h4"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
  bug: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7.13v-1a3 3 0 1 1 6 0v1"/><path d="M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6"/><path d="M12 20v-9"/><path d="M6 13H2"/><path d="M22 13h-4"/></svg>',
  clip: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m21.4 11.1-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/></svg>',
  history: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/></svg>',
  steps: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/></svg>',
  gear: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  file: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
  alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m21.7 18-8-14a2 2 0 0 0-3.5 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
};

function ensureModalRoot() {
  let root = document.getElementById(MODAL_ID);
  if (root) return root;
  root = document.createElement('div');
  root.id = MODAL_ID;
  root.className = 'epv-overlay';
  root.hidden = true;
  root.innerHTML = `
    <div class="epv-panel" role="dialog" aria-modal="true" aria-labelledby="entityPreviewTitle" tabindex="-1">
      <header class="epv-head">
        <div class="epv-head-main">
          <div class="epv-kicker">
            <span id="entityPreviewKindBadge" class="epv-kind"></span>
            <span id="entityPreviewId" class="epv-id"></span>
          </div>
          <h2 id="entityPreviewTitle" class="epv-title"></h2>
          <div id="entityPreviewMeta" class="epv-meta"></div>
        </div>
        <div class="epv-actions">
          <a id="entityPreviewExtLink" class="epv-open" target="_blank" rel="noopener noreferrer" hidden>${_ICON.external}<span>在禅道打开</span></a>
          <button type="button" id="entityPreviewClose" class="epv-close" aria-label="关闭预览" title="关闭（Esc）">${_ICON.x}</button>
        </div>
      </header>
      <div id="entityPreviewBody" class="epv-body"></div>
      <footer class="epv-foot"><span id="entityPreviewFoot"></span></footer>
    </div>
  `;
  document.body.appendChild(root);
  root.addEventListener('mousedown', (e) => { root._downOnBackdrop = e.target === root; });
  root.addEventListener('click', (e) => { if (e.target === root && root._downOnBackdrop) close(); });
  root.querySelector('#entityPreviewClose').addEventListener('click', close);
  return root;
}

function openModal() {
  const root = ensureModalRoot();
  if (root.hidden) {
    _lastFocus = document.activeElement;
    root.hidden = false;
    // 页面原本有滚动条时保留滚动条槽位，否则锁滚动后滚动条消失，背景会横向跳动
    const html = document.documentElement;
    html.classList.toggle('epv-gutter', window.innerWidth > html.clientWidth);
    html.classList.add('epv-locked');
    root.querySelector('.epv-panel').scrollTop = 0;
    root.scrollTop = 0;
    root.querySelector('.epv-panel').focus({ preventScroll: true });
  }
}

function close() {
  releasePreviewImages();
  _previewGeneration++;
  _current = null;
  const root = document.getElementById(MODAL_ID);
  if (!root || root.hidden) return;
  root.hidden = true;
  document.documentElement.classList.remove('epv-locked', 'epv-gutter');
  if (_lastFocus?.isConnected) _lastFocus.focus({ preventScroll: true });
  _lastFocus = null;
}

// 图片查看器在捕获阶段先处理 Esc（并阻止继续传播），所以这里只会在查看器关闭时收到
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const root = document.getElementById(MODAL_ID);
  if (!root || root.hidden) return;
  e.preventDefault();
  close();
});

const _KIND = {
  story:    { label: '需求', prefix: 's#' },
  bug:      { label: 'Bug',  prefix: 'b#' },
  testcase: { label: '用例', prefix: 'case#' },
  task:     { label: '任务', prefix: 'task#' },
};

const chip = (text, tone = '') => (text || text === 0)
  ? `<span class="epv-chip${tone ? ` epv-chip--${tone}` : ''}">${escapeHtml(text)}</span>` : '';

function _setFoot(text) {
  const foot = document.getElementById('entityPreviewFoot');
  if (foot) foot.textContent = text || '';
}

function _setKicker(kind, id) {
  const root = ensureModalRoot();
  const info = _KIND[kind] || _KIND.story;
  const badge = root.querySelector('#entityPreviewKindBadge');
  badge.textContent = info.label;
  badge.className = `epv-kind epv-kind--${kind}`;
  root.querySelector('#entityPreviewId').textContent = `${info.prefix}${id}`;
}

function _setHeader(kind, data, chips = []) {
  const root = ensureModalRoot();
  _setKicker(kind, data.id);
  root.querySelector('#entityPreviewTitle').textContent = data.title || data.name || '';
  const meta = root.querySelector('#entityPreviewMeta');
  meta.innerHTML = chips.filter(Boolean).join('');
  const ext = root.querySelector('#entityPreviewExtLink');
  const extUrl = data.zentao_url || data.url;
  if (extUrl) { ext.href = extUrl; ext.hidden = false; } else { ext.removeAttribute('href'); ext.hidden = true; }
  return { meta };
}

const section = (title, icon, content, { tone = '', count = null } = {}) => `
  <section class="epv-section${tone ? ` epv-section--${tone}` : ''}">
    <div class="epv-section-head">${icon || ''}<span>${escapeHtml(title)}</span>${count != null ? `<span class="epv-count">${count}</span>` : ''}</div>
    <div class="epv-section-body">${content}</div>
  </section>`;

const facts = (pairs) => {
  const items = pairs.filter(([, v]) => v !== undefined && v !== null && String(v).trim() !== '' && String(v) !== '0');
  if (!items.length) return '';
  return `<dl class="epv-facts">${items.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join('')}</dl>`;
};
const rich = (html, empty) => `<div class="entity-preview-rich epv-rich">${_sanitize(html) || `<span class="epv-empty">${escapeHtml(empty)}</span>`}</div>`;
const who = (name, date) => [name, _shortDate(date)].filter(Boolean).join(' · ');

function _renderError(message, retry) {
  const root = ensureModalRoot();
  const body = root.querySelector('#entityPreviewBody');
  body.innerHTML = `
    <div class="epv-error" role="alert">
      ${_ICON.alert}
      <div><strong>加载禅道详情失败</strong><div>${escapeHtml(message)}</div></div>
      ${retry ? '<button type="button" class="secondary epv-retry">重试</button>' : ''}
    </div>`;
  if (retry) body.querySelector('.epv-retry').addEventListener('click', retry);
  _setFoot('');
}

function _renderLoading(kind, id) {
  releasePreviewImages();
  const root = ensureModalRoot();
  _setKicker(kind, id);
  root.querySelector('#entityPreviewTitle').innerHTML = '<span class="epv-skel" style="width:46%"></span>';
  root.querySelector('#entityPreviewMeta').innerHTML = '<span class="epv-skel epv-skel--chip"></span><span class="epv-skel epv-skel--chip"></span><span class="epv-skel epv-skel--chip"></span>';
  root.querySelector('#entityPreviewExtLink').hidden = true;
  root.querySelector('#entityPreviewBody').innerHTML = `
    <div class="epv-loading" aria-busy="true" aria-label="加载禅道详情中">
      <div class="epv-skel-grid">${'<span class="epv-skel epv-skel--block"></span>'.repeat(4)}</div>
      <span class="epv-skel" style="width:92%"></span><span class="epv-skel" style="width:78%"></span>
      <span class="epv-skel" style="width:85%"></span><span class="epv-skel" style="width:60%"></span>
    </div>`;
  _setFoot('正在从禅道加载…');
}

const _FOOT_NOTE = '数据实时来自禅道 · 1 分钟内重复打开直接使用缓存';

// ─── Story renderer ─────────────────────────────────────────────────────────
function _renderStory(data) {
  _setHeader('story', data, [
    chip(data.status_zh, data.status === 'closed' ? 'muted' : 'info'),
    chip(data.stage_zh, data.stage === 'developed' ? 'ok' : ''),
    data.pri ? chip(`P${data.pri}`, Number(data.pri) <= 1 ? 'bad' : '') : '',
  ]);
  const body = document.getElementById('entityPreviewBody');
  body.innerHTML = `
    ${facts([
      ['指派给', data.assigned_to],
      ['创建', who(data.opened_by, data.opened_date)],
      ['最后修改', who(data.last_edited_by, data.last_edited_date)],
      ['模块', data.module],
      ['计划', data.plan],
    ])}
    ${section('需求描述', _ICON.doc, rich(data.spec, '该需求暂无描述'))}
    ${section('验收标准', _ICON.check, rich(data.verify, '该需求暂无验收标准'), { tone: 'ok' })}
  `;
  _afterRender(body);
}

// ─── Bug renderer ───────────────────────────────────────────────────────────
const _BUG_TONE = { active: 'bad', resolved: 'info', closed: 'muted' };

function _renderBugFiles(files) {
  const list = Array.isArray(files) ? files.filter((f) => f && f.url) : [];
  if (!list.length) return '';
  const images = list.filter((f) => f.is_image);
  const others = list.filter((f) => !f.is_image);
  const gallery = images.length ? `<div class="epv-gallery">${images.map((f) => `
      <figure class="epv-thumb">
        <img src="${escapeHtml(f.url)}" alt="${escapeHtml(f.title || '图片附件')}" data-caption="${escapeHtml(f.title || '')}" loading="lazy">
        <figcaption title="${escapeHtml(f.title || '')}">${escapeHtml(f.title || '图片附件')}</figcaption>
      </figure>`).join('')}</div>` : '';
  const fileLinks = others.length ? `<div class="epv-files">${others.map((f) => `
      <a class="epv-file" href="${escapeHtml(f.url)}" data-filename="${escapeHtml(f.title || '')}" target="_blank" rel="noopener noreferrer">${_ICON.file}<span>${escapeHtml(f.title || f.url)}</span></a>`).join('')}</div>` : '';
  return section('附件', _ICON.clip, gallery + fileLinks, { count: list.length });
}

function _renderBug(data) {
  _setHeader('bug', data, [
    chip(data.status_zh || data.status, _BUG_TONE[data.status] || ''),
    data.severity ? chip(`严重 S${data.severity}`, Number(data.severity) <= 2 ? 'bad' : 'warn') : '',
    data.pri ? chip(`P${data.pri}`, Number(data.pri) <= 1 ? 'bad' : '') : '',
    chip(data.type_zh || data.type),
    data.resolution_zh ? chip(data.resolution_zh, 'ok') : '',
  ]);
  const body = document.getElementById('entityPreviewBody');
  const actions = Array.isArray(data.actions) ? data.actions : [];
  const timeline = actions.length ? section('流转记录', _ICON.history, `
      <ol class="epv-timeline">${actions.map((a) => `
        <li>
          <div class="epv-tl-head"><b>${escapeHtml(a.actor || '系统')}</b><span>${escapeHtml(a.action_zh || a.action || '')}</span><time>${escapeHtml(_shortDate(a.date))}</time></div>
          ${a.comment ? `<div class="entity-preview-rich epv-rich epv-tl-body">${_sanitize(a.comment)}</div>` : ''}
        </li>`).join('')}
      </ol>`, { count: actions.length }) : '';
  body.innerHTML = `
    ${facts([
      ['指派给', data.assigned_to],
      ['提出', who(data.opened_by, data.opened_date)],
      ['解决', who(data.resolved_by, data.resolved_date)],
      ['关闭', who(data.closed_by, data.closed_date)],
      ['发现版本', data.opened_build],
      ['解决版本', data.resolved_build],
      ['模块', data.module],
      ['所属项目', data.project],
      ['执行', data.execution],
      ['关联需求', data.story_title],
      ['截止日期', data.deadline],
      ['关键词', data.keywords],
    ])}
    ${section('复现步骤', _ICON.bug, rich(data.steps, '无步骤描述'), { tone: 'bad' })}
    ${_renderBugFiles(data.files)}
    ${timeline}
  `;
  _afterRender(body);
}

// ─── Testcase renderer ──────────────────────────────────────────────────────
function _renderTestcase(data) {
  _setHeader('testcase', data, [
    chip(data.status_zh || data.status, 'info'),
    chip(data.type_zh || data.type),
    data.pri ? chip(`P${data.pri}`, Number(data.pri) <= 1 ? 'bad' : '') : '',
    data.last_run_result ? chip(`上次结果：${data.last_run_result}`, /pass/i.test(data.last_run_result) ? 'ok' : 'warn') : '',
  ]);
  const body = document.getElementById('entityPreviewBody');
  const steps = Array.isArray(data.steps) ? data.steps : [];
  const stepsHtml = steps.length
    ? `<div class="epv-table-wrap"><table class="epv-steps">
        <thead><tr><th>#</th><th>步骤</th><th>预期结果</th></tr></thead>
        <tbody>${steps.map((s) => `
          <tr>
            <td>${escapeHtml(s.name || '')}</td>
            <td><div class="entity-preview-rich epv-rich">${_sanitize(s.step)}</div></td>
            <td><div class="entity-preview-rich epv-rich">${_sanitize(s.expect)}</div></td>
          </tr>`).join('')}</tbody>
      </table></div>`
    : '<span class="epv-empty">该用例暂无步骤</span>';
  body.innerHTML = `
    ${facts([
      ['关联需求', data.story],
      ['模块', data.module],
      ['创建', who(data.opened_by, data.opened_date)],
      ['最后修改', who(data.last_edited_by, data.last_edited_date)],
      ['上次执行', _shortDate(data.last_run_date)],
    ])}
    ${data.precondition ? section('前置条件', _ICON.gear, rich(data.precondition, ''), { tone: 'warn' }) : ''}
    ${section('用例步骤', _ICON.steps, stepsHtml, { count: steps.length || null })}
  `;
  _afterRender(body);
}

// ─── Task renderer ──────────────────────────────────────────────────────────
const _TASK_STATUS_ZH = { wait: '未开始', doing: '进行中', changed: '进行中', done: '已完成', pause: '已暂停', cancel: '已取消', closed: '已关闭' };
const _TASK_TONE = { wait: '', doing: 'info', changed: 'info', done: 'ok', pause: 'warn', cancel: 'muted', closed: 'muted' };

function _renderTask(data) {
  const statusZh = _TASK_STATUS_ZH[data.status] || data.status || '';
  const isCompleted = data.status === 'done' || data.status === 'closed';
  const hierarchy = data.is_parent
    ? chip('父任务', 'info')
    : (data.parent ? chip(`子任务 · 父任务 ${data.parent_name || `#${data.parent}`}`, 'purple') : '');
  _setHeader('task', data, [
    chip(statusZh, _TASK_TONE[data.status] || ''),
    hierarchy,
    data.story ? chip(`关联需求 s#${data.story}`) : '',
  ]);
  const body = document.getElementById('entityPreviewBody');
  const hours = (v) => (v != null && v !== '' ? `${v} h` : '');
  body.innerHTML = `
    ${renderTaskStoryChangeBadge(data, data.id) ? `<div class="epv-banner">${renderTaskStoryChangeBadge(data, data.id)}</div>` : ''}
    ${facts([
      ['指派', data.assigned_to],
      ['预计工时', hours(data.estimate)],
      ['已消耗', hours(data.consumed)],
      ['剩余', hours(data.left)],
      ['预计开始', data.est_started],
      ['截止日期', data.deadline],
      ['实际开始', _shortDate(data.real_started)],
      ['完成者', isCompleted ? (data.finished_by || '禅道未记录') : ''],
      ['完成时间', _shortDate(data.finished_date)],
    ])}
    ${data.desc ? section('任务描述', _ICON.doc, rich(data.desc, '')) : ''}
  `;
  _afterRender(body);
}

function _afterRender(body) {
  _loadProxyImages(body);
  _wireProxyDownloads(body);
}

// ─── Public open functions ──────────────────────────────────────────────────
const _RENDER = { story: _renderStory, bug: _renderBug, testcase: _renderTestcase, task: _renderTask };

function _viewerOpen() {
  const viewer = document.getElementById('entityImageViewer');
  return !!viewer && !viewer.hidden;
}

async function _genericOpen(kind, id, { force = false } = {}) {
  const numericId = Number(String(id ?? '').replace(/\D/g, ''));
  if (!numericId) return;
  const generation = ++_previewGeneration;
  _current = { kind, id: numericId };
  const renderFn = _RENDER[kind];
  openModal();
  const retry = () => _genericOpen(kind, numericId, { force: true });

  const hit = force ? undefined : _cacheGet(kind, numericId);
  if (hit) {
    releasePreviewImages();
    renderFn(hit.data);
    if (hit.fresh) { _setFoot(_FOOT_NOTE); return; }
    // 先显示缓存，后台刷新；内容有变化且用户仍停留在该预览时再替换
    _setFoot('已显示缓存内容，正在后台同步禅道最新数据…');
    try {
      const data = await _fetchDetail(kind, numericId);
      if (generation !== _previewGeneration) return;
      if (JSON.stringify(data) !== JSON.stringify(hit.data) && !_viewerOpen()) {
        const panel = document.querySelector(`#${MODAL_ID}`);
        const top = panel ? panel.scrollTop : 0;
        releasePreviewImages();
        renderFn(data);
        if (panel) panel.scrollTop = top;
        _setFoot('已更新为禅道最新数据');
      } else {
        _setFoot(_FOOT_NOTE);
      }
    } catch {
      if (generation === _previewGeneration) _setFoot('后台同步失败，当前显示的是缓存内容');
    }
    return;
  }

  _renderLoading(kind, numericId);
  try {
    const data = await _fetchDetail(kind, numericId);
    if (generation !== _previewGeneration) return;
    renderFn(data);
    _setFoot(_FOOT_NOTE);
  } catch (err) {
    if (generation !== _previewGeneration) return;
    _renderError(err?.message || '加载失败', retry);
  }
}

function openStory(id) { return _genericOpen('story', id); }
function openBug(id) { return _genericOpen('bug', id); }
function openTestcase(id) { return _genericOpen('testcase', id); }
function openTask(id) { return _genericOpen('task', id); }

/** 预取（悬停预览按钮时调用）：缓存新鲜或已在途则什么都不做 */
function prefetch(kind, id) {
  const numericId = Number(String(id ?? '').replace(/\D/g, ''));
  if (!_ENDPOINT[kind] || !numericId) return;
  const hit = _cacheGet(kind, numericId);
  if (hit?.fresh) return;
  _fetchDetail(kind, numericId).catch(() => {});
}

// 悬停/聚焦预览入口 120ms 即预取。入口可用 data-preview-kind / data-preview-id 声明，
// 或沿用 utils.renderPreviewBtn 生成的 onclick="…OmniQAPreview.openBug(123)"。
const _OPENER_RE = /OmniQAPreview\.(openStory|openBug|openTestcase|openTask)\((\d+)\)/;
const _OPENER_KIND = { openStory: 'story', openBug: 'bug', openTestcase: 'testcase', openTask: 'task' };
function _previewTarget(el) {
  const node = el?.closest?.('[data-preview-kind][data-preview-id], .qa-preview-btn');
  if (!node) return null;
  if (node.dataset.previewKind) return { node, kind: node.dataset.previewKind, id: node.dataset.previewId };
  const m = _OPENER_RE.exec(node.getAttribute('onclick') || '');
  return m ? { node, kind: _OPENER_KIND[m[1]], id: m[2] } : null;
}
let _prefetchTimer = 0;
let _prefetchNode = null;
function _schedulePrefetch(e) {
  const target = _previewTarget(e.target);
  if (!target || target.node === _prefetchNode) return;
  clearTimeout(_prefetchTimer);
  _prefetchNode = target.node;
  _prefetchTimer = setTimeout(() => prefetch(target.kind, target.id), PREFETCH_DELAY_MS);
}
document.addEventListener('pointerover', _schedulePrefetch, { passive: true });
document.addEventListener('focusin', _schedulePrefetch);
document.addEventListener('pointerout', (e) => {
  if (_prefetchNode && !_prefetchNode.contains(e.relatedTarget)) {
    clearTimeout(_prefetchTimer);
    _prefetchNode = null;
  }
}, { passive: true });

// ─── Task story change ──────────────────────────────────────────────────────
function _storyChangeContent(story) {
  if (!story) return '<p class="epv-empty">旧版需求内容暂不可用</p>';
  const files = (story.files || []).map((file) => `<a class="epv-file" href="${escapeHtml(file.url)}" data-filename="${escapeHtml(file.title)}" target="_blank" rel="noopener noreferrer">${_ICON.file}<span>${escapeHtml(file.title)}</span></a>`).join('');
  return `<h4>需求描述</h4><div class="entity-preview-rich epv-rich">${_sanitize(story.spec) || '<span class="epv-empty">暂无需求描述</span>'}</div>
    <h4>验收标准</h4><div class="entity-preview-rich epv-rich">${_sanitize(story.verify) || '<span class="epv-empty">暂无验收标准</span>'}</div>${files ? `<h4>附件</h4><div class="epv-files">${files}</div>` : ''}`;
}

function _renderTaskStoryChange(data, generation) {
  _setHeader('story', { ...data.latest_story, id: data.story_id });
  const body = document.getElementById('entityPreviewBody');
  const meta = document.getElementById('entityPreviewMeta');
  const pending = !!data.task_story_change_pending;
  const version = Number(data.task_latest_story_version);
  const previous = Number(data.task_story_version);
  meta.innerHTML = [
    chip(`禅道任务 #${data.task_id}`),
    chip(data.task_name || ''),
    chip(`任务已记录 v${previous} → 最新 v${version}`, pending ? 'warn' : 'ok'),
  ].join('');
  const story = data.latest_story || {};
  const assignee = typeof data.assigned_to === 'object' ? (data.assigned_to?.realname || data.assigned_to?.account || '') : data.assigned_to;
  const changed = [story.changed_by, _fmtDate(story.changed_date)].filter(Boolean).join(' · ');
  const compare = data.previous_story ? `<details class="epv-compare">
      <summary>与任务已记录版本 v${previous} 对比</summary>
      <div class="epv-compare-grid">
        <section><h3>原需求 v${previous}</h3>${_storyChangeContent(data.previous_story)}</section>
        <section class="is-latest"><h3>最新需求 v${version}</h3>${_storyChangeContent(story)}</section>
      </div></details>` : data.previous_error ? `<p class="epv-warn-text">${escapeHtml(data.previous_error)}</p>` : '';
  const canConfirm = pending && data.can_confirm && !_storyChangeSubmitting.has(data.task_id);
  body.innerHTML = `<div role="status" class="epv-callout ${pending ? 'is-warn' : 'is-ok'}">
      <b>${pending ? '研发需求变更 · 待确认' : `已确认需求版本 v${version}`}</b>
      <div>当前指派：${escapeHtml(assignee || '未指派')}${changed ? ` · 需求变更：${escapeHtml(changed)}` : ''}</div>
    </div>
    ${section(`最新版需求 v${version}`, _ICON.doc, _storyChangeContent(story))}
    ${compare}
    <div id="storyChangeResult" role="status" class="epv-warn-text"></div>
    <div class="epv-sticky-actions">
      <button type="button" id="confirmTaskStoryChange" class="qa-story-change-confirm" ${canConfirm ? '' : 'disabled'}>${pending ? '确认已阅读需求变更' : '已确认'}</button>
      <button type="button" id="refreshTaskStoryChange" class="secondary">刷新内容</button>
      <span>${escapeHtml(pending && !data.can_confirm ? data.confirm_disabled_reason : '确认仅记录已阅读变更；需要重新测试时，请另行重新激活任务。')}</span>
    </div>`;
  _setFoot('需求变更内容每次打开都从禅道实时读取');
  body.querySelector('#refreshTaskStoryChange').addEventListener('click', () => openTaskStoryChange(data.task_id));
  body.querySelector('#confirmTaskStoryChange').addEventListener('click', async (event) => {
    if (!canConfirm || _storyChangeSubmitting.has(data.task_id)) return;
    const button = event.currentTarget;
    const message = body.querySelector('#storyChangeResult');
    const refresh = body.querySelector('#refreshTaskStoryChange');
    _storyChangeSubmitting.add(data.task_id);
    button.disabled = true;
    refresh.disabled = true;
    button.textContent = '正在确认…';
    try {
      const response = await api(`/zentao/tasks/${data.task_id}/confirm-story-change`, { method: 'POST', body: {
        story_id: data.story_id, expected_story_version: previous, expected_latest_story_version: version,
      } });
      const result = await response.json();
      _cache.task.delete(Number(data.task_id));
      _cache.story.delete(Number(data.story_id));
      document.dispatchEvent(new CustomEvent('zentao-story-change-confirmed', { detail: result }));
      if (generation !== _previewGeneration) return;
      if (result.version_changed_during_confirm) {
        message.textContent = result.message;
        button.textContent = '请刷新后查看最新内容';
      } else {
        message.classList.add('is-ok');
        message.textContent = result.message;
        button.textContent = '已确认';
      }
    } catch (err) {
      if (generation !== _previewGeneration) return;
      message.textContent = `${err.message || '确认失败'}。请刷新内容后核对确认状态。`;
      button.textContent = '请刷新后核对状态';
    } finally {
      _storyChangeSubmitting.delete(data.task_id);
      refresh.disabled = false;
    }
  });
  _afterRender(body);
}

async function openTaskStoryChange(id) {
  const taskId = Number(id);
  if (!taskId) return;
  const generation = ++_previewGeneration;
  _current = { kind: 'task-story-change', id: taskId };
  openModal();
  _renderLoading('task', taskId);
  try {
    // This workflow always fetches fresh content rather than using preview TTL.
    const response = await api(`/zentao/tasks/${taskId}/story-change`, { cache: 'no-store' });
    const data = await response.json();
    if (generation !== _previewGeneration) return;
    _renderTaskStoryChange(data, generation);
  } catch (err) {
    if (generation !== _previewGeneration) return;
    _renderError(err.message || '无法读取需求变更内容', () => openTaskStoryChange(taskId));
  }
}

(function injectStyles() {
  // 预览按钮在未加载 weave.css 的页面（如独立测试页）也要保持紧凑；弹窗本体样式在 weave.css R10
  if (document.getElementById('entity-preview-styles')) return;
  const style = document.createElement('style');
  style.id = 'entity-preview-styles';
  style.textContent = `
    .entity-preview-rich { color:#1f2937; font-size:14px; line-height:1.75; word-break:break-word; }
    .entity-preview-rich p { margin:0 0 8px; }
    .entity-preview-rich img { max-width:100%; height:auto; border-radius:4px; margin:4px 0; cursor:zoom-in; }
    .entity-preview-rich table { border-collapse:collapse; margin:6px 0; }
    .entity-preview-rich th, .entity-preview-rich td { border:1px solid #cbd5e1; padding:4px 8px; }
    .entity-preview-rich a { color:#2563eb; text-decoration:underline; }
    .entity-preview-rich ul, .entity-preview-rich ol { padding-left:22px; margin:6px 0; }
    #entityPreviewModal[hidden] { display:none !important; }
    .qa-preview-btn {
      display:inline-flex; align-items:center; justify-content:center;
      width:22px; height:22px; border:1px solid #cbd5e1; border-radius:5px;
      background:#fff; color:#0ea5e9; text-decoration:none;
      vertical-align:middle; margin-left:6px; cursor:pointer;
      transition:all 0.15s;
    }
    .qa-preview-btn:hover {
      background:#0ea5e9; color:#fff; border-color:#0ea5e9;
      box-shadow:0 2px 6px rgba(14,165,233,0.35);
    }
    .qa-preview-btn svg { width:13px; height:13px; }
  `;
  document.head.appendChild(style);
})();

window.OmniQAPreview = { openStory, openBug, openTestcase, openTask, openTaskStoryChange, prefetch, close };
// Backwards-compat: original story-only API still works.
window.OmniQAStoryPreview = { open: openStory, closeModal: close };
