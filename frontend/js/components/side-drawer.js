// 右侧抽屉（反馈详情 / 新建反馈 / 外业测试详情共用）。
// 挂在 <body> 下：避免页面 section 上的 transform / 「压平内联背景」规则影响 fixed 定位与底色。
// 同一个 key 只有一个实例；重复 open 只替换内容（保留滚动位置由调用方决定）。
// Esc / 点遮罩 / 关闭按钮关闭；图片查看器打开时 Esc 只交给查看器。
const drawers = new Map();
let keyBound = false;

const CLOSE_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';

function topmostOpen() {
  let last = null;
  drawers.forEach((d) => { if (!d.root.hidden) last = d; });
  return last;
}

function bindKeys() {
  if (keyBound) return;
  keyBound = true;
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const viewer = document.getElementById('entityImageViewer');
    if (viewer && !viewer.hidden) return;
    if (document.querySelector('#omniqaSelectMenu:not([hidden]), #omniqaCalendar:not([hidden])')) return;
    const d = topmostOpen();
    if (!d) return;
    event.preventDefault();
    d.close();
  });
}

function build(key) {
  const root = document.createElement('div');
  root.className = 'sd-root';
  root.dataset.sdKey = key;
  root.hidden = true;
  root.innerHTML = `
    <div class="sd-backdrop" data-sd-close></div>
    <aside class="sd-panel" role="dialog" aria-modal="true" aria-labelledby="sd-title-${key}" tabindex="-1">
      <header class="sd-head">
        <div class="sd-head-main" id="sd-title-${key}"></div>
        <div class="sd-head-tools"></div>
        <button type="button" class="sd-close" data-sd-close aria-label="关闭" title="关闭（Esc）">${CLOSE_ICON}</button>
      </header>
      <div class="sd-body"></div>
      <footer class="sd-foot" hidden></footer>
    </aside>`;
  document.body.appendChild(root);
  const d = {
    key,
    root,
    panel: root.querySelector('.sd-panel'),
    head: root.querySelector('.sd-head-main'),
    tools: root.querySelector('.sd-head-tools'),
    body: root.querySelector('.sd-body'),
    foot: root.querySelector('.sd-foot'),
    onClose: null,
    previousFocus: null,
    close() {
      if (root.hidden) return;
      root.classList.remove('is-open');
      const done = () => { root.hidden = true; };
      if (matchMedia('(prefers-reduced-motion: reduce)').matches) done();
      else setTimeout(done, 220);
      document.documentElement.classList.toggle('sd-locked', [...drawers.values()].some((x) => x !== d && !x.root.hidden));
      const cb = d.onClose; d.onClose = null;
      try { cb && cb(); } catch (_) { /* 回调失败不影响关闭 */ }
      if (d.previousFocus?.isConnected) d.previousFocus.focus({ preventScroll: true });
    },
  };
  root.addEventListener('click', (event) => {
    if (event.target.closest('[data-sd-close]')) d.close();
  });
  drawers.set(key, d);
  return d;
}

/**
 * 打开（或刷新）抽屉。
 * @param {object} opts
 * @param {string} opts.key       抽屉标识
 * @param {string} [opts.size]    'md'（560px）| 'lg'（760px，默认）
 * @param {string} opts.head      头部 HTML（标题区）
 * @param {string} [opts.tools]   头部右侧工具按钮 HTML
 * @param {string} opts.body      主体 HTML
 * @param {string} [opts.foot]    底部操作区 HTML（空则隐藏）
 * @param {boolean} [opts.keepScroll] 刷新内容时保留滚动位置
 * @param {Function} [opts.onClose]
 */
export function openDrawer({ key, size = 'lg', head = '', tools = '', body = '', foot = '', keepScroll = false, onClose = null }) {
  bindKeys();
  const d = drawers.get(key) || build(key);
  const wasOpen = !d.root.hidden;
  const scroll = keepScroll && wasOpen ? d.body.scrollTop : 0;
  d.panel.dataset.size = size;
  d.head.innerHTML = head;
  d.tools.innerHTML = tools;
  d.body.innerHTML = body;
  d.foot.innerHTML = foot;
  d.foot.hidden = !foot;
  d.onClose = onClose;
  if (!wasOpen) {
    d.previousFocus = document.activeElement;
    d.root.hidden = false;
    document.documentElement.classList.add('sd-locked');
    // 先渲染初始位置再加 is-open，过渡才会生效
    requestAnimationFrame(() => requestAnimationFrame(() => d.root.classList.add('is-open')));
    d.panel.focus({ preventScroll: true });
  }
  d.body.scrollTop = scroll;
  return d;
}

export function getDrawer(key) {
  const d = drawers.get(key);
  return d && !d.root.hidden ? d : null;
}

export function closeDrawer(key) {
  drawers.get(key)?.close();
}
