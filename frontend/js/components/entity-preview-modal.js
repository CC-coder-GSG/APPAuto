import { api } from '../api.js';

// Unified preview modal for Zentao stories / bugs / testcases.
// Exposes:
//   window.OmniQAPreview = { openStory(id), openBug(id), openTestcase(id), close() }
//   window.OmniQAStoryPreview = { open(id) }  (backwards-compat alias)

const MODAL_ID = 'entityPreviewModal';
const CACHE_TTL_MS = 60_000;
const _cache = { story: new Map(), bug: new Map(), testcase: new Map(), task: new Map() };

function _cacheGet(type, id) {
  const entry = _cache[type].get(id);
  if (!entry) return undefined;
  if (Date.now() > entry.expiry) { _cache[type].delete(id); return undefined; }
  return entry.data;
}
function _cacheSet(type, id, data) {
  _cache[type].set(id, { data, expiry: Date.now() + CACHE_TTL_MS });
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
async function _loadProxyImages(containerEl) {
  if (!containerEl) return;
  const imgs = Array.from(containerEl.querySelectorAll('img[src^="/zentao/files/"]'));
  await Promise.all(imgs.map(async (img) => {
    const src = img.getAttribute('src');
    try {
      const resp = await api(src);
      const blob = await resp.blob();
      const objUrl = URL.createObjectURL(blob);
      img.src = objUrl;
      const anchor = img.closest('a[href^="/zentao/files/"]');
      if (anchor) {
        anchor.href = objUrl;
        anchor.target = '_blank';
        anchor.rel = 'noopener noreferrer';
      }
    } catch {
      img.alt = '图片加载失败';
      img.style.opacity = '0.5';
    }
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
      const original = a.style.opacity;
      a.style.opacity = '0.5';
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
        alert('文件下载失败：' + (err?.message || '未知错误'));
      } finally {
        a.style.opacity = original;
      }
    });
  });
}

function _fmtDate(s) {
  if (!s) return '';
  return String(s).slice(0, 19).replace('T', ' ');
}

function ensureModalRoot() {
  let root = document.getElementById(MODAL_ID);
  if (root) return root;
  root = document.createElement('div');
  root.id = MODAL_ID;
  root.className = 'hidden';
  root.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,0.6); z-index:2000; display:none; padding:32px 16px; overflow:auto;';
  root.innerHTML = `
    <div style="max-width:1000px; margin:0 auto; background:#fff; border-radius:12px; box-shadow:0 16px 50px rgba(0,0,0,0.35); padding:22px 26px;">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; border-bottom:1px solid #e2e8f0; padding-bottom:14px;">
        <div style="flex:1; min-width:0;">
          <div id="entityPreviewKindBadge" style="display:inline-block; font-size:11px; font-weight:600; padding:2px 8px; border-radius:10px; margin-bottom:6px;"></div>
          <div id="entityPreviewTitle" style="font-size:18px; font-weight:700; color:#0f172a; line-height:1.35; word-break:break-word;"></div>
          <div id="entityPreviewMeta" style="font-size:12px; color:#64748b; margin-top:4px;"></div>
        </div>
        <div style="display:flex; gap:8px; align-items:center; flex-shrink:0;">
          <a id="entityPreviewExtLink" target="_blank" rel="noopener noreferrer"
             style="display:none; align-items:center; gap:4px; border:1px solid #2563eb; background:#2563eb; color:#fff; border-radius:6px; padding:6px 12px; text-decoration:none; font-size:12px; font-weight:600;">
            在禅道打开 ↗
          </a>
          <button type="button" id="entityPreviewClose" aria-label="关闭"
            style="display:flex; align-items:center; justify-content:center; width:32px; height:32px; border:none; background:#ef4444; color:#fff; border-radius:50%; cursor:pointer; font-size:18px; font-weight:700; line-height:1; box-shadow:0 2px 6px rgba(239,68,68,0.4); transition:background 0.15s;">
            ✕
          </button>
        </div>
      </div>
      <div id="entityPreviewBody" style="margin-top:16px;"></div>
    </div>
  `;
  document.body.appendChild(root);
  root.addEventListener('click', (e) => { if (e.target === root) close(); });
  root.querySelector('#entityPreviewClose').addEventListener('click', close);
  root.querySelector('#entityPreviewClose').addEventListener('mouseenter', (e) => {
    e.currentTarget.style.background = '#dc2626';
  });
  root.querySelector('#entityPreviewClose').addEventListener('mouseleave', (e) => {
    e.currentTarget.style.background = '#ef4444';
  });
  return root;
}

function openModal() {
  const root = ensureModalRoot();
  root.classList.remove('hidden');
  root.style.display = 'block';
}

function close() {
  const root = document.getElementById(MODAL_ID);
  if (!root) return;
  root.classList.add('hidden');
  root.style.display = 'none';
}

const _KIND_BADGE = {
  story:    { label: '需求', bg: '#dbeafe', color: '#1d4ed8' },
  bug:      { label: 'Bug',  bg: '#fee2e2', color: '#b91c1c' },
  testcase: { label: '用例', bg: '#dcfce7', color: '#166534' },
  task:     { label: '任务', bg: '#fef3c7', color: '#b45309' },
};

const _KIND_PREFIX = { story: 's#', bug: 'b#', testcase: 'case#', task: 'task#' };

function _setHeader(kind, data) {
  const root = ensureModalRoot();
  const kindBadge = root.querySelector('#entityPreviewKindBadge');
  const title = root.querySelector('#entityPreviewTitle');
  const meta = root.querySelector('#entityPreviewMeta');
  const ext = root.querySelector('#entityPreviewExtLink');

  const kinfo = _KIND_BADGE[kind] || _KIND_BADGE.story;
  kindBadge.textContent = kinfo.label;
  kindBadge.style.background = kinfo.bg;
  kindBadge.style.color = kinfo.color;

  const prefix = _KIND_PREFIX[kind] || 's#';
  title.innerHTML = `${prefix}${escapeHtml(data.id)} ${escapeHtml(data.title || data.name || '')}`;

  const extUrl = data.zentao_url || data.url;
  if (extUrl) {
    ext.href = extUrl;
    ext.style.display = 'inline-flex';
  } else {
    ext.style.display = 'none';
  }
  return { meta };
}

function _renderError(messageHtml) {
  const root = ensureModalRoot();
  root.querySelector('#entityPreviewBody').innerHTML = `
    <div style="padding:16px; border:1px solid #fecaca; border-radius:8px; background:#fef2f2; color:#991b1b;">
      ${messageHtml}
    </div>`;
}

function _renderLoading(kind, id) {
  const root = ensureModalRoot();
  const kindBadge = root.querySelector('#entityPreviewKindBadge');
  const title = root.querySelector('#entityPreviewTitle');
  const meta = root.querySelector('#entityPreviewMeta');
  const ext = root.querySelector('#entityPreviewExtLink');
  const body = root.querySelector('#entityPreviewBody');

  const kinfo = _KIND_BADGE[kind] || _KIND_BADGE.story;
  kindBadge.textContent = kinfo.label;
  kindBadge.style.background = kinfo.bg;
  kindBadge.style.color = kinfo.color;

  const prefix = _KIND_PREFIX[kind] || 's#';
  title.textContent = `${prefix}${id}`;
  meta.textContent = '';
  ext.style.display = 'none';
  body.innerHTML = '<div style="padding:32px; text-align:center; color:#64748b;">加载禅道详情中...</div>';
}

// ─── Story renderer ─────────────────────────────────────────────────────────
function _renderStory(data) {
  const { meta } = _setHeader('story', data);
  const body = document.getElementById('entityPreviewBody');

  const bits = [];
  if (data.status_zh) bits.push(`状态：<b>${escapeHtml(data.status_zh)}</b>`);
  if (data.stage_zh) bits.push(`阶段：<b style="${data.stage === 'developed' ? 'color:#16a34a;' : ''}">${escapeHtml(data.stage_zh)}</b>`);
  if (data.assigned_to) bits.push(`指派给：${escapeHtml(data.assigned_to)}`);
  if (data.opened_by) bits.push(`创建：${escapeHtml(data.opened_by)} ${escapeHtml(_fmtDate(data.opened_date))}`);
  if (data.last_edited_by) bits.push(`最后修改：${escapeHtml(data.last_edited_by)} ${escapeHtml(_fmtDate(data.last_edited_date))}`);
  if (data.module) bits.push(`模块：${escapeHtml(data.module)}`);
  if (data.pri) bits.push(`P${escapeHtml(data.pri)}`);
  meta.innerHTML = bits.join(' · ');

  body.innerHTML = `
    <div style="border:1px solid #e2e8f0; border-radius:10px; padding:14px 16px; background:#f8fafc;">
      <div style="font-weight:600; color:#0f172a; margin-bottom:8px;">📝 需求描述</div>
      <div class="entity-preview-rich">${_sanitize(data.spec) || '<span style="color:#94a3b8;">该需求暂无描述</span>'}</div>
    </div>
    <div style="border:1px solid #bbf7d0; border-radius:10px; padding:14px 16px; background:#f0fdf4; margin-top:12px;">
      <div style="font-weight:600; color:#166534; margin-bottom:8px;">✅ 验收标准</div>
      <div class="entity-preview-rich">${_sanitize(data.verify) || '<span style="color:#94a3b8;">该需求暂无验收标准</span>'}</div>
    </div>
    <div style="margin-top:10px; font-size:11px; color:#94a3b8; text-align:right;">数据实时来自禅道，浏览器侧缓存 60 秒</div>
  `;
  _loadProxyImages(body);
  _wireProxyDownloads(body);
}

// ─── Bug renderer ───────────────────────────────────────────────────────────
function _renderBug(data) {
  const { meta } = _setHeader('bug', data);
  const body = document.getElementById('entityPreviewBody');

  const bits = [];
  if (data.status_zh) bits.push(`状态：<b>${escapeHtml(data.status_zh)}</b>`);
  if (data.resolution_zh) bits.push(`解决方案：<b>${escapeHtml(data.resolution_zh)}</b>`);
  if (data.type_zh) bits.push(`类型：${escapeHtml(data.type_zh)}`);
  if (data.assigned_to) bits.push(`指派给：${escapeHtml(data.assigned_to)}`);
  if (data.opened_by) bits.push(`提出：${escapeHtml(data.opened_by)} ${escapeHtml(_fmtDate(data.opened_date))}`);
  if (data.resolved_by) bits.push(`解决：${escapeHtml(data.resolved_by)} ${escapeHtml(_fmtDate(data.resolved_date))}`);
  if (data.opened_build) bits.push(`发现版本：${escapeHtml(data.opened_build)}`);
  if (data.resolved_build) bits.push(`解决版本：${escapeHtml(data.resolved_build)}`);
  if (data.module) bits.push(`模块：${escapeHtml(data.module)}`);
  meta.innerHTML = bits.join(' · ');

  const filesHtml = (data.files || []).length
    ? `<div style="display:flex; flex-wrap:wrap; gap:8px;">${data.files.map((f) => f.is_image
        ? `<a href="${escapeHtml(f.url)}" target="_blank"><img src="${escapeHtml(f.url)}" alt="${escapeHtml(f.title)}" style="max-width:160px; max-height:120px; border:1px solid #e2e8f0; border-radius:6px;"></a>`
        : `<a href="${escapeHtml(f.url)}" data-filename="${escapeHtml(f.title || '')}" target="_blank" style="display:inline-flex; align-items:center; gap:4px; border:1px solid #cbd5e1; border-radius:6px; padding:4px 10px; color:#0f172a; text-decoration:none; font-size:12px; background:#f8fafc; cursor:pointer;">📎 ${escapeHtml(f.title || f.url)}</a>`
      ).join('')}</div>`
    : '<span style="color:#94a3b8; font-size:12px;">无附件</span>';

  body.innerHTML = `
    <div style="border:1px solid #fecaca; border-radius:10px; padding:14px 16px; background:#fef2f2;">
      <div style="font-weight:600; color:#991b1b; margin-bottom:8px;">🐞 复现步骤</div>
      <div class="entity-preview-rich">${_sanitize(data.steps) || '<span style="color:#94a3b8;">无步骤描述</span>'}</div>
    </div>
    <div style="border:1px solid #e2e8f0; border-radius:10px; padding:14px 16px; background:#f8fafc; margin-top:12px;">
      <div style="font-weight:600; color:#0f172a; margin-bottom:8px;">📎 附件</div>
      ${filesHtml}
    </div>
    ${(data.actions || []).length ? `
      <div style="border:1px solid #e2e8f0; border-radius:10px; padding:14px 16px; background:#fff; margin-top:12px;">
        <div style="font-weight:600; color:#0f172a; margin-bottom:8px;">🕘 流转记录</div>
        <div style="display:flex; flex-direction:column; gap:8px;">
          ${data.actions.map((a) => `
            <div style="border-left:3px solid #cbd5e1; padding:6px 10px;">
              <div style="font-size:11px; color:#64748b;">${escapeHtml(_fmtDate(a.date))} · <b>${escapeHtml(a.actor || '')}</b> · ${escapeHtml(a.action_zh || a.action || '')}</div>
              ${a.comment ? `<div class="entity-preview-rich" style="margin-top:4px; font-size:13px;">${_sanitize(a.comment)}</div>` : ''}
            </div>
          `).join('')}
        </div>
      </div>` : ''}
    <div style="margin-top:10px; font-size:11px; color:#94a3b8; text-align:right;">数据实时来自禅道，浏览器侧缓存 60 秒</div>
  `;
  _loadProxyImages(body);
  _wireProxyDownloads(body);
}

// ─── Testcase renderer ──────────────────────────────────────────────────────
function _renderTestcase(data) {
  const { meta } = _setHeader('testcase', data);
  const body = document.getElementById('entityPreviewBody');

  const bits = [];
  if (data.type_zh) bits.push(`类型：${escapeHtml(data.type_zh)}`);
  if (data.status_zh) bits.push(`状态：<b>${escapeHtml(data.status_zh)}</b>`);
  if (data.pri) bits.push(`P${escapeHtml(data.pri)}`);
  if (data.story) bits.push(`关联需求：${escapeHtml(data.story)}`);
  if (data.module) bits.push(`模块：${escapeHtml(data.module)}`);
  if (data.opened_by) bits.push(`创建：${escapeHtml(data.opened_by)} ${escapeHtml(_fmtDate(data.opened_date))}`);
  if (data.last_run_result) bits.push(`上次结果：<b>${escapeHtml(data.last_run_result)}</b>`);
  meta.innerHTML = bits.join(' · ');

  const stepsHtml = (data.steps || []).length
    ? `<table style="width:100%; border-collapse:collapse;">
        <thead><tr style="background:#f1f5f9;">
          <th style="border:1px solid #cbd5e1; padding:6px; text-align:left; width:50px;">#</th>
          <th style="border:1px solid #cbd5e1; padding:6px; text-align:left;">步骤</th>
          <th style="border:1px solid #cbd5e1; padding:6px; text-align:left;">预期结果</th>
        </tr></thead>
        <tbody>${data.steps.map((s) => `
          <tr>
            <td style="border:1px solid #cbd5e1; padding:6px; vertical-align:top; color:#64748b;">${escapeHtml(s.name || '')}</td>
            <td style="border:1px solid #cbd5e1; padding:6px; vertical-align:top;"><div class="entity-preview-rich">${_sanitize(s.step)}</div></td>
            <td style="border:1px solid #cbd5e1; padding:6px; vertical-align:top;"><div class="entity-preview-rich">${_sanitize(s.expect)}</div></td>
          </tr>`).join('')}</tbody>
       </table>`
    : '<span style="color:#94a3b8;">该用例暂无步骤</span>';

  body.innerHTML = `
    ${data.precondition ? `
      <div style="border:1px solid #e2e8f0; border-radius:10px; padding:14px 16px; background:#fffbeb;">
        <div style="font-weight:600; color:#92400e; margin-bottom:8px;">⚙️ 前置条件</div>
        <div class="entity-preview-rich">${_sanitize(data.precondition)}</div>
      </div>` : ''}
    <div style="border:1px solid #e2e8f0; border-radius:10px; padding:14px 16px; background:#f8fafc; margin-top:${data.precondition ? '12px' : '0'};">
      <div style="font-weight:600; color:#0f172a; margin-bottom:8px;">🧪 用例步骤</div>
      ${stepsHtml}
    </div>
    <div style="margin-top:10px; font-size:11px; color:#94a3b8; text-align:right;">数据实时来自禅道，浏览器侧缓存 60 秒</div>
  `;
  _loadProxyImages(body);
  _wireProxyDownloads(body);
}

// ─── Public open functions ──────────────────────────────────────────────────
async function _genericOpen(kind, id, url, renderFn) {
  const numericId = Number(id);
  if (!numericId) return;
  _renderLoading(kind, numericId);
  openModal();

  const cached = _cacheGet(kind, numericId);
  if (cached) { renderFn(cached); return; }

  try {
    const res = await api(url);
    const data = await res.json();
    if (!data || data.error) {
      _renderError(escapeHtml(data?.message || '加载失败'));
      return;
    }
    _cacheSet(kind, numericId, data);
    renderFn(data);
  } catch (err) {
    _renderError(escapeHtml(err?.message || '加载失败'));
  }
}

function openStory(id) {
  return _genericOpen('story', id, `/zentao/story/${Number(id)}/detail`, _renderStory);
}

function openBug(id) {
  // Reuse the existing /zentao/bugs/{id}/preview which already returns
  // normalize_bug_detail + rewritten file URLs + actions.
  return _genericOpen('bug', id, `/zentao/bugs/${Number(id)}/preview`, _renderBug);
}

function openTestcase(id) {
  return _genericOpen('testcase', id, `/zentao/testcase/${Number(id)}/detail`, _renderTestcase);
}

// ─── Task renderer ──────────────────────────────────────────────────────────
const _TASK_STATUS_ZH = { wait: '未开始', doing: '进行中', changed: '进行中', done: '已完成', pause: '已暂停', cancel: '已取消', closed: '已关闭' };

function _renderTask(data) {
  const { meta } = _setHeader('task', data);
  const body = document.getElementById('entityPreviewBody');
  const statusZh = _TASK_STATUS_ZH[data.status] || data.status || '';
  const isCompleted = data.status === 'done' || data.status === 'closed';
  const bits = [];
  // 父/子任务标识
  if (data.is_parent) {
    bits.push('<span style="background:#ecfeff; color:#0e7490; padding:1px 6px; border-radius:4px;">父任务</span>');
  } else if (data.parent) {
    const pn = data.parent_name ? `：${escapeHtml(data.parent_name)}` : ` #${escapeHtml(data.parent)}`;
    bits.push(`<span style="background:#f5f3ff; color:#6d28d9; padding:1px 6px; border-radius:4px;">子任务</span> <span style="color:#64748b;">父任务${pn}</span>`);
  }
  if (statusZh) bits.push(`状态：<b>${escapeHtml(statusZh)}</b>`);
  if (data.assigned_to) bits.push(`指派：${escapeHtml(data.assigned_to)}`);
  if (data.story) bits.push(`关联需求：s#${escapeHtml(data.story)}`);
  meta.innerHTML = bits.join(' · ');

  const row = (label, val) => (val || val === 0)
    ? `<tr><td style="padding:6px 10px; color:#64748b; white-space:nowrap;">${label}</td><td style="padding:6px 10px; color:#0f172a;">${escapeHtml(val)}</td></tr>`
    : '';
  const ztBtn = data.url
    ? `<div style="margin-bottom:10px;"><a href="${escapeHtml(data.url)}" target="_blank" rel="noopener noreferrer" style="display:inline-flex; align-items:center; gap:6px; background:#6366f1; color:#fff; padding:7px 14px; border-radius:8px; font-size:13px; font-weight:600; text-decoration:none;">🔗 在禅道中打开任务</a></div>`
    : '';
  body.innerHTML = `
    ${ztBtn}
    <div style="border:1px solid #e2e8f0; border-radius:10px; padding:8px 12px; background:#f8fafc;">
      <table style="width:100%; border-collapse:collapse; font-size:14px;">
        ${row('预计工时', data.estimate != null ? data.estimate + ' h' : '')}
        ${row('已消耗', data.consumed != null ? data.consumed + ' h' : '')}
        ${row('剩余', data.left != null ? data.left + ' h' : '')}
        ${row('预计开始', data.est_started)}
        ${row('截止日期', data.deadline)}
        ${row('实际开始', _fmtDate(data.real_started))}
        ${row('完成者', isCompleted ? (data.finished_by || '禅道未记录') : '')}
        ${row('完成时间', _fmtDate(data.finished_date))}
      </table>
    </div>
    ${data.desc ? `<div style="border:1px solid #e2e8f0; border-radius:10px; padding:14px 16px; background:#fff; margin-top:12px;">
        <div style="font-weight:600; color:#0f172a; margin-bottom:8px;">📝 任务描述</div>
        <div class="entity-preview-rich">${_sanitize(data.desc)}</div>
      </div>` : ''}
    <div style="margin-top:10px; font-size:11px; color:#94a3b8; text-align:right;">数据实时来自禅道，浏览器侧缓存 60 秒</div>
  `;
  _loadProxyImages(body);
  _wireProxyDownloads(body);
}

function openTask(id) {
  return _genericOpen('task', id, `/zentao/task/${Number(id)}/detail`, _renderTask);
}

(function injectStyles() {
  if (document.getElementById('entity-preview-styles')) return;
  const style = document.createElement('style');
  style.id = 'entity-preview-styles';
  style.textContent = `
    .entity-preview-rich { color:#1f2937; font-size:14px; line-height:1.75; word-break:break-word; }
    .entity-preview-rich p { margin:0 0 8px; }
    .entity-preview-rich img { max-width:100%; height:auto; border-radius:4px; margin:4px 0; }
    .entity-preview-rich table { border-collapse:collapse; margin:6px 0; }
    .entity-preview-rich th, .entity-preview-rich td { border:1px solid #cbd5e1; padding:4px 8px; }
    .entity-preview-rich a { color:#2563eb; text-decoration:underline; }
    .entity-preview-rich ul, .entity-preview-rich ol { padding-left:22px; margin:6px 0; }
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

window.OmniQAPreview = { openStory, openBug, openTestcase, openTask, close };
// Backwards-compat: original story-only API still works.
window.OmniQAStoryPreview = { open: openStory, closeModal: close };
