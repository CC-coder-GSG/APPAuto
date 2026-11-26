import { escapeHtml } from '../utils.js?v=20261126-hc-1';

// 全站悬浮卡（2026-11-26）：替代浏览器原生 title 小黄框。样式见 weave.css R22（.hc-*）。
//
// 三种来源，按优先级：
//   data-hc='{"k":"禅道任务","id":"#12","t":"标题","s":"进行中","tone":"blue","rows":[["当前指派","张三"]],"hint":"点击预览"}'
//       → 结构化信息卡（用 hoverCardAttr(obj) 生成属性值）
//   data-hc-bug="12345"（可带 data-hc-bug-title）
//       → Bug 信息卡：显示时从禅道水合缓存取状态 / 严重程度 / 指派等，没有缓存就只显示标题
//   普通 title="…"
//       → 统一样式的文字提示；显示期间暂时移走 title 防止原生框叠出来，隐藏时还原
// 不想被接管的元素加 data-hc-off。卡片不可交互（pointer-events:none），点击 / 滚动 / Esc 立即收起。

const SHOW_DELAY = 280;
const SWITCH_DELAY = 40;
const HIDE_DELAY = 80;
const GAP = 8;
const MARGIN = 8;

const TONES = new Set(['blue', 'green', 'amber', 'red', 'gray', 'orange', 'purple']);
const BUG_TONE = { active: 'red', resolved: 'green', closed: 'gray', notrepro: 'amber' };
const SEV_TEXT = { 1: '致命', 2: '严重', 3: '一般', 4: '轻微' };

let card = null;
let anchor = null;      // 当前显示中的元素
let pending = null;     // 等待显示的元素
let showTimer = 0;
let hideTimer = 0;
let observer = null;
let aliveTimer = 0;     // 显示期间轮询：锚点被重绘掉（不在文档里）就收起

export function hoverCardAttr(obj) {
  return escapeHtml(JSON.stringify(obj || {}));
}

function ensureCard() {
  if (card && card.isConnected) return card;
  card = document.createElement('div');
  card.id = 'omniqaHoverCard';
  card.className = 'hc';
  card.setAttribute('role', 'tooltip');
  card.hidden = true;
  document.body.appendChild(card);
  return card;
}

function findTarget(node) {
  const el = node && node.nodeType === 1 ? node : node?.parentElement;
  const hit = el?.closest?.('[data-hc], [data-hc-bug], [title], [data-hc-title]');
  if (!hit || hit.closest('[data-hc-off]')) return null;
  if (hit === document.documentElement || hit === document.body) return null;
  if (hit.hasAttribute('data-hc') || hit.hasAttribute('data-hc-bug')) return hit;
  const text = hit.getAttribute('title') ?? hit.dataset.hcTitle;
  return text && text.trim() ? hit : null;
}

// ── 内容 ─────────────────────────────────────────────

function tone(t) {
  return TONES.has(t) ? t : 'gray';
}

function richHtml(o) {
  const rows = (Array.isArray(o.rows) ? o.rows : []).filter((r) => r && r[1] != null && String(r[1]).trim() !== '');
  const head = (o.k || o.id || o.s)
    ? `<div class="hc-head">
        ${o.k ? `<span class="hc-kind">${escapeHtml(o.k)}</span>` : ''}
        ${o.id ? `<b class="hc-id">${escapeHtml(o.id)}</b>` : ''}
        ${o.s ? `<span class="hc-status hc-tone--${tone(o.tone)}">${escapeHtml(o.s)}</span>` : ''}
      </div>`
    : '';
  return `${head}
    ${o.t ? `<div class="hc-title">${escapeHtml(o.t)}</div>` : ''}
    ${o.note ? `<div class="hc-note">${escapeHtml(o.note)}</div>` : ''}
    ${rows.length ? `<dl class="hc-rows">${rows.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join('')}</dl>` : ''}
    ${o.hint ? `<div class="hc-hint"><svg class="ui-ic" aria-hidden="true"><use href="#i-external"/></svg>${escapeHtml(o.hint)}</div>` : ''}`;
}

function bugSpec(el) {
  const id = String(el.getAttribute('data-hc-bug') || '').replace(/\D/g, '');
  const fallbackTitle = el.getAttribute('data-hc-bug-title') || '';
  const d = id ? window.OmniQAZentao?.peekBug?.(id) : undefined;
  const base = { k: 'Bug', id: id ? `#${id}` : '', t: fallbackTitle };
  if (d === null) return { ...base, note: '禅道中未找到此 Bug' };
  if (!d) return { ...base, note: fallbackTitle ? '' : '禅道信息加载中…' };
  if (d.deleted) return { ...base, t: d.title || fallbackTitle, s: '已删除', tone: 'red', note: '该 Bug 已在禅道删除' };
  const status = d.status_zh || d.status || '';
  const res = d.resolution && d.resolution !== 'none' ? (d.resolution_zh || d.resolution) : '';
  const sev = Number(d.severity) || 0;
  return {
    ...base,
    t: d.title || fallbackTitle,
    s: status,
    tone: BUG_TONE[d.status] || 'gray',
    rows: [
      ['严重程度', sev ? `S${sev}${SEV_TEXT[sev] ? ` · ${SEV_TEXT[sev]}` : ''}` : ''],
      ['优先级', d.pri ? `P${d.pri}` : ''],
      ['解决方案', res],
      ['指派给', d.assigned_to || ''],
      ['创建人', d.opened_by || ''],
    ],
  };
}

function parseSpec(el) {
  try { return JSON.parse(el.getAttribute('data-hc') || '{}') || {}; } catch { return { t: el.getAttribute('data-hc') }; }
}

function render(el) {
  const c = ensureCard();
  if (el.hasAttribute('data-hc') || el.hasAttribute('data-hc-bug')) {
    c.className = 'hc hc--rich';
    c.innerHTML = richHtml(el.hasAttribute('data-hc') ? parseSpec(el) : bugSpec(el));
  } else {
    c.className = 'hc hc--tip';
    c.innerHTML = `<div class="hc-text">${escapeHtml(el.dataset.hcTitle ?? el.getAttribute('title') ?? '')}</div>`;
  }
}

// ── title 暂存 / 还原 ────────────────────────────────

function stashTitle(el) {
  if (!el.hasAttribute('title')) return;
  el.dataset.hcTitle = el.getAttribute('title');
  el.removeAttribute('title');
}

function restoreTitle(el) {
  if (!el || el.dataset.hcTitle == null) return;
  // 显示期间业务代码若重新设了 title，以新的为准；若代码清掉了（delete dataset.hcTitle），这里就不会还原
  if (!el.hasAttribute('title')) el.setAttribute('title', el.dataset.hcTitle);
  delete el.dataset.hcTitle;
}

function watch(el) {
  observer?.disconnect();
  observer = new MutationObserver(() => {
    if (!anchor) return;
    if (anchor.hasAttribute('title')) stashTitle(anchor);
    render(anchor);
    place(anchor);
  });
  observer.observe(el, { attributes: true, attributeFilter: ['title', 'data-hc', 'data-hc-bug-title'] });
  clearInterval(aliveTimer);
  aliveTimer = setInterval(() => { if (anchor && !anchor.isConnected) hideNow(); }, 400);
}

// ── 定位 ─────────────────────────────────────────────

function place(el) {
  const c = card;
  const r = el.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = window.innerHeight;
  c.style.left = '0px';
  c.style.top = '0px';
  const w = c.offsetWidth;
  const h = c.offsetHeight;
  const rich = c.classList.contains('hc--rich');
  // 信息卡默认在下方，文字提示默认在上方；放不下再换边
  const below = r.bottom + GAP + h <= vh - MARGIN;
  const above = r.top - GAP - h >= MARGIN;
  const side = rich ? (below || !above ? 'bottom' : 'top') : (above || !below ? 'top' : 'bottom');
  const top = side === 'bottom' ? r.bottom + GAP : r.top - GAP - h;
  const cx = r.left + r.width / 2;
  const left = Math.min(Math.max(cx - w / 2, MARGIN), Math.max(MARGIN, vw - MARGIN - w));
  c.dataset.side = side;
  c.style.left = `${Math.round(left)}px`;
  c.style.top = `${Math.round(Math.max(MARGIN, top))}px`;
  c.style.setProperty('--hc-arrow', `${Math.round(Math.min(Math.max(cx - left, 12), w - 12))}px`);
}

// ── 显示 / 隐藏 ──────────────────────────────────────

function show(el) {
  clearTimeout(showTimer);
  clearTimeout(hideTimer);
  pending = null;
  if (!el.isConnected) return;
  if (anchor && anchor !== el) restoreTitle(anchor);
  anchor = el;
  stashTitle(el);
  render(el);
  card.hidden = false;
  card.classList.remove('is-in');
  place(el);
  void card.offsetWidth;
  card.classList.add('is-in');
  watch(el);
}

function hideNow() {
  clearTimeout(showTimer);
  clearTimeout(hideTimer);
  pending = null;
  observer?.disconnect();
  observer = null;
  clearInterval(aliveTimer);
  if (anchor) restoreTitle(anchor);
  anchor = null;
  if (card) { card.hidden = true; card.classList.remove('is-in'); }
}

function scheduleShow(el) {
  if (el === anchor) { clearTimeout(hideTimer); return; }
  if (el === pending) return;
  clearTimeout(showTimer);
  pending = el;
  showTimer = setTimeout(() => show(el), anchor ? SWITCH_DELAY : SHOW_DELAY);
}

function scheduleHide() {
  clearTimeout(showTimer);
  pending = null;
  clearTimeout(hideTimer);
  if (anchor) hideTimer = setTimeout(hideNow, HIDE_DELAY);
}

function init() {
  if (window.__omniqaHoverCard) return;
  window.__omniqaHoverCard = true;
  const doc = document;
  doc.addEventListener('pointerover', (e) => {
    if (e.pointerType && e.pointerType !== 'mouse') return;
    const t = findTarget(e.target);
    if (t) scheduleShow(t); else scheduleHide();
  }, true);
  doc.addEventListener('pointerout', (e) => {
    if (e.pointerType && e.pointerType !== 'mouse') return;
    if (!e.relatedTarget) scheduleHide();   // 移出窗口
  }, true);
  doc.addEventListener('focusin', (e) => {
    const t = findTarget(e.target);
    if (t && e.target.matches?.(':focus-visible')) scheduleShow(t);
  });
  doc.addEventListener('focusout', () => { if (anchor || pending) scheduleHide(); });
  doc.addEventListener('pointerdown', hideNow, true);
  doc.addEventListener('keydown', (e) => { if (e.key === 'Escape' && anchor) hideNow(); }, true);
  window.addEventListener('scroll', () => { if (anchor || pending) hideNow(); }, true);
  window.addEventListener('blur', hideNow);
  window.addEventListener('resize', hideNow);
}

init();

window.OmniQAHoverCard = { hide: hideNow, attr: hoverCardAttr };
