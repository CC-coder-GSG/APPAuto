import { api } from '../api.js';

const MODAL_ID = 'storyPreviewModal';
const CACHE_TTL_MS = 60_000;
const _cache = new Map();

function _cacheGet(id) {
  const entry = _cache.get(id);
  if (!entry) return undefined;
  if (Date.now() > entry.expiry) { _cache.delete(id); return undefined; }
  return entry.data;
}

function _cacheSet(id, data) {
  _cache.set(id, { data, expiry: Date.now() + CACHE_TTL_MS });
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Whitelist of tags + attributes we keep when rendering 禅道 spec/verify.
// Anything else is stripped. This is a defense against XSS in the rich-text
// payload returned by 禅道 (which we cannot trust to be clean).
const _ALLOWED_TAGS = new Set([
  'A','B','BLOCKQUOTE','BR','CODE','DIV','EM','H1','H2','H3','H4','H5','H6',
  'HR','I','IMG','LI','OL','P','PRE','SPAN','STRONG','SUB','SUP','TABLE',
  'TBODY','TD','TH','THEAD','TR','U','UL','FONT',
]);

const _ALLOWED_ATTRS = new Set([
  'href','target','rel','src','alt','title','width','height','colspan','rowspan',
  'align','valign','style','color','size','face',
]);

function _sanitizeStyle(style) {
  // Allow only a tiny safe subset of inline CSS — font, color, text-align,
  // background-color, padding/margin numbers. Drop anything with url(),
  // expression(), or other risky values.
  if (!style) return '';
  const safe = [];
  style.split(';').forEach((decl) => {
    const idx = decl.indexOf(':');
    if (idx < 0) return;
    const prop = decl.slice(0, idx).trim().toLowerCase();
    const val = decl.slice(idx + 1).trim();
    if (!prop || !val) return;
    if (/(expression|javascript:|url\s*\()/i.test(val)) return;
    if (!/^[\w-]+$/.test(prop)) return;
    if (![
      'font-size','font-family','font-weight','font-style','color',
      'background-color','text-align','text-decoration','line-height',
      'padding','padding-left','padding-right','padding-top','padding-bottom',
      'margin','margin-left','margin-right','margin-top','margin-bottom',
      'width','height','border','border-color','border-style','border-width',
      'list-style','vertical-align','white-space',
    ].includes(prop)) return;
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
          // Unwrap (keep children) for unknown tags instead of dropping content.
          while (child.firstChild) node.insertBefore(child.firstChild, child);
          node.removeChild(child);
          continue;
        }
        // Filter attributes
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
        node.removeChild(child); // strip comments
      }
    }
  };
  walk(root);
  return root.innerHTML;
}

function ensureModalRoot() {
  let root = document.getElementById(MODAL_ID);
  if (root) return root;
  root = document.createElement('div');
  root.id = MODAL_ID;
  root.className = 'hidden';
  root.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,0.55); z-index:2000; display:none; padding:32px 16px; overflow:auto;';
  root.innerHTML = `
    <div style="max-width:1000px; margin:0 auto; background:#fff; border-radius:10px; box-shadow:0 12px 40px rgba(0,0,0,0.3); padding:24px;">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px;">
        <div style="flex:1;">
          <div id="storyPreviewTitle" style="font-size:18px; font-weight:700; color:#0f172a; margin-bottom:4px;"></div>
          <div id="storyPreviewMeta" style="font-size:12px; color:#64748b;"></div>
        </div>
        <div style="display:flex; gap:8px; align-items:center;">
          <a id="storyPreviewExtLink" target="_blank" rel="noopener noreferrer" style="display:none; border:1px solid #cbd5e1; background:#fff; border-radius:6px; padding:4px 12px; color:#0f172a; text-decoration:none; font-size:12px;">在禅道打开 ↗</a>
          <button type="button" id="storyPreviewClose" style="border:1px solid #cbd5e1; background:#f8fafc; border-radius:6px; padding:4px 12px; cursor:pointer;">关闭</button>
        </div>
      </div>
      <div id="storyPreviewBody" style="margin-top:16px;"></div>
    </div>
  `;
  document.body.appendChild(root);
  root.addEventListener('click', (e) => {
    if (e.target === root) closeModal();
  });
  root.querySelector('#storyPreviewClose').addEventListener('click', closeModal);
  return root;
}

function openModal() {
  const root = ensureModalRoot();
  root.classList.remove('hidden');
  root.style.display = 'block';
}

function closeModal() {
  const root = document.getElementById(MODAL_ID);
  if (!root) return;
  root.classList.add('hidden');
  root.style.display = 'none';
}

function renderError(messageHtml) {
  const root = ensureModalRoot();
  root.querySelector('#storyPreviewBody').innerHTML = `
    <div style="padding:16px; border:1px solid #fecaca; border-radius:8px; background:#fef2f2; color:#991b1b;">
      ${messageHtml}
    </div>`;
}

function render(data) {
  const root = ensureModalRoot();
  const title = root.querySelector('#storyPreviewTitle');
  const meta = root.querySelector('#storyPreviewMeta');
  const body = root.querySelector('#storyPreviewBody');
  const ext = root.querySelector('#storyPreviewExtLink');

  title.innerHTML = `s#${escapeHtml(data.id)} ${escapeHtml(data.title || '')}`;

  const metaBits = [];
  if (data.status_zh) metaBits.push(`状态：<b>${escapeHtml(data.status_zh)}</b>`);
  if (data.stage_zh) metaBits.push(`阶段：<b>${escapeHtml(data.stage_zh)}</b>`);
  if (data.assigned_to) metaBits.push(`指派给：${escapeHtml(data.assigned_to)}`);
  if (data.opened_by) metaBits.push(`创建人：${escapeHtml(data.opened_by)}`);
  if (data.opened_date) metaBits.push(`创建于：${escapeHtml(String(data.opened_date).slice(0, 19).replace('T', ' '))}`);
  if (data.last_edited_by) metaBits.push(`最近修改人：${escapeHtml(data.last_edited_by)}`);
  if (data.last_edited_date) metaBits.push(`最近修改：${escapeHtml(String(data.last_edited_date).slice(0, 19).replace('T', ' '))}`);
  if (data.module) metaBits.push(`模块：${escapeHtml(data.module)}`);
  if (data.pri) metaBits.push(`优先级：P${escapeHtml(data.pri)}`);
  meta.innerHTML = metaBits.join(' · ');

  if (data.zentao_url) {
    ext.href = data.zentao_url;
    ext.style.display = 'inline-block';
  } else {
    ext.style.display = 'none';
  }

  const specHtml = _sanitize(data.spec || '');
  const verifyHtml = _sanitize(data.verify || '');

  body.innerHTML = `
    <div style="border:1px solid #e2e8f0; border-radius:8px; padding:14px 16px; background:#f8fafc;">
      <div style="font-weight:600; color:#0f172a; margin-bottom:8px;">📝 需求描述</div>
      <div class="story-preview-rich">${specHtml || '<span style="color:#94a3b8;">该需求暂无描述内容</span>'}</div>
    </div>
    <div style="border:1px solid #e2e8f0; border-radius:8px; padding:14px 16px; background:#f0fdf4; margin-top:12px;">
      <div style="font-weight:600; color:#0f172a; margin-bottom:8px;">✅ 验收标准</div>
      <div class="story-preview-rich">${verifyHtml || '<span style="color:#94a3b8;">该需求暂无验收标准</span>'}</div>
    </div>
    <div style="margin-top:10px; font-size:11px; color:#94a3b8; text-align:right;">数据实时从禅道拉取，缓存 60 秒</div>
  `;
}

async function open(storyId) {
  const id = Number(storyId);
  if (!id) return;
  const root = ensureModalRoot();
  const body = root.querySelector('#storyPreviewBody');
  const title = root.querySelector('#storyPreviewTitle');
  const meta = root.querySelector('#storyPreviewMeta');
  const ext = root.querySelector('#storyPreviewExtLink');

  title.textContent = `s#${id}`;
  meta.textContent = '';
  ext.style.display = 'none';
  body.innerHTML = '<div class="muted" style="padding:32px; text-align:center; color:#64748b;">加载禅道需求详情中...</div>';
  openModal();

  const cached = _cacheGet(id);
  if (cached) {
    render(cached);
    return;
  }

  try {
    const res = await api(`/zentao/story/${id}/detail`);
    const data = await res.json();
    if (!data || data.error) {
      renderError(escapeHtml(data?.message || '加载失败'));
      return;
    }
    _cacheSet(id, data);
    render(data);
  } catch (err) {
    renderError(escapeHtml(err?.message || '加载失败'));
  }
}

(function injectStyles() {
  if (document.getElementById('story-preview-styles')) return;
  const style = document.createElement('style');
  style.id = 'story-preview-styles';
  style.textContent = `
    .story-preview-rich { color:#1f2937; font-size:14px; line-height:1.75; word-break:break-word; }
    .story-preview-rich p { margin:0 0 8px; }
    .story-preview-rich img { max-width:100%; height:auto; border-radius:4px; }
    .story-preview-rich table { border-collapse:collapse; margin:6px 0; }
    .story-preview-rich th, .story-preview-rich td { border:1px solid #cbd5e1; padding:4px 8px; }
    .story-preview-rich a { color:#2563eb; text-decoration:underline; }
    .story-preview-rich ul, .story-preview-rich ol { padding-left:22px; margin:6px 0; }
  `;
  document.head.appendChild(style);
})();

window.OmniQAStoryPreview = { open, closeModal };
