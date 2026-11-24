// 图片查看器（预览弹窗等共用）：点击容器内任意 <img> 打开，支持多图切换、
// 滚轮/双指/按钮缩放（以光标为中心）、拖动平移、双击放大/还原、原图新标签打开。
// 键盘：←/→ 切换，+/- 缩放，0 适应窗口，Esc 关闭（只关查看器，不影响下层弹窗）。
let root, stage, picture, caption, zoomLabel, images = [], index = 0, generation = 0;
let scale = 1, x = 0, y = 0, previousFocus, moved = 0;
const pointers = new Map();
const ICON = {
  prev: '<path d="m15 18-6-6 6-6"/>',
  next: '<path d="m9 18 6-6-6-6"/>',
  out: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6"/>',
  in: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6"/><path d="M11 8v6"/>',
  fit: '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
  open: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
};
const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

const transform = () => {
  picture.style.transform = `translate(${x}px,${y}px) scale(${scale})`;
  if (zoomLabel) zoomLabel.textContent = `${Math.round(scale * 100)}%`;
};
function reset() { scale = 1; x = y = 0; pointers.clear(); transform(); }
function zoom(factor, cx, cy) {
  const next = Math.min(8, Math.max(.25, scale * factor));
  if (cx !== undefined && stage) {
    // 以 (cx, cy) 为中心缩放：保持光标下的点不动
    const rect = stage.getBoundingClientRect();
    const px = cx - (rect.left + rect.width / 2);
    const py = cy - (rect.top + rect.height / 2);
    const ratio = next / scale;
    x = px - ratio * (px - x);
    y = py - ratio * (py - y);
  }
  scale = next;
  transform();
}

function ensure() {
  if (root) return;
  root = document.createElement('div');
  root.id = 'entityImageViewer'; root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-label', '图片查看器'); root.hidden = true;
  root.innerHTML = `<style>
    #entityImageViewer{position:fixed;inset:0;z-index:20000;display:flex;flex-direction:column;color:#f5f5f4;background:rgba(12,10,9,.88);-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);animation:ivIn .18s ease-out}
    #entityImageViewer[hidden]{display:none}
    @keyframes ivIn{from{opacity:0}}
    #entityImageViewer .image-bar{display:flex;align-items:center;gap:12px;padding:10px 14px;padding-top:calc(10px + env(safe-area-inset-top,0px));background:linear-gradient(rgba(0,0,0,.45),transparent);z-index:1}
    #entityImageViewer .image-caption{flex:1;min-width:0;font-size:13px;line-height:1.4;color:#e7e5e4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #entityImageViewer .image-tools{display:flex;align-items:center;gap:4px;padding:4px;border-radius:12px;background:rgba(41,37,36,.82);box-shadow:0 0 0 1px rgba(255,255,255,.08)}
    #entityImageViewer .image-tools button{display:inline-grid!important;place-items:center;width:34px!important;height:34px!important;min-height:0!important;margin:0!important;padding:0!important;border:none!important;border-radius:8px!important;background:transparent!important;color:#f5f5f4!important;box-shadow:none!important;transform:none!important;cursor:pointer}
    #entityImageViewer .image-tools button:hover:not(:disabled){background:rgba(255,255,255,.12)!important}
    #entityImageViewer .image-tools button:disabled{opacity:.35;cursor:default}
    #entityImageViewer .image-tools button:focus-visible{outline:2px solid #fbbf24;outline-offset:1px}
    #entityImageViewer .image-tools svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
    #entityImageViewer .image-tools .sep{width:1px;height:20px;margin:0 4px;background:rgba(255,255,255,.16)}
    #entityImageViewer .image-zoom{min-width:48px;font-size:12px;font-variant-numeric:tabular-nums;text-align:center;color:#d6d3d1}
    #entityImageViewer .image-stage{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;overflow:hidden;touch-action:none;cursor:zoom-out}
    #entityImageViewer img{max-width:94%;max-height:94%;object-fit:contain;user-select:none;cursor:grab;transform-origin:center;border-radius:4px;box-shadow:0 12px 40px rgba(0,0,0,.45);transition:transform .12s ease-out}
    #entityImageViewer.is-dragging img{cursor:grabbing;transition:none}
    #entityImageViewer .image-hint{padding:8px 14px calc(10px + env(safe-area-inset-bottom,0px));text-align:center;font-size:12px;color:#a8a29e}
    @media (max-width:640px){#entityImageViewer .image-bar{flex-wrap:wrap}#entityImageViewer .image-caption{flex-basis:100%}#entityImageViewer [data-action=open]{display:none}}
    @media (prefers-reduced-motion:reduce){#entityImageViewer,#entityImageViewer img{animation:none;transition:none}}
  </style>
  <div class="image-bar">
    <div class="image-caption" role="status"></div>
    <div class="image-tools">
      <button type="button" data-action="prev" aria-label="上一张" title="上一张（←）">${svg('prev')}</button>
      <button type="button" data-action="next" aria-label="下一张" title="下一张（→）">${svg('next')}</button>
      <span class="sep" aria-hidden="true"></span>
      <button type="button" data-action="out" aria-label="缩小" title="缩小（-）">${svg('out')}</button>
      <span class="image-zoom" aria-live="off">100%</span>
      <button type="button" data-action="in" aria-label="放大" title="放大（+）">${svg('in')}</button>
      <button type="button" data-action="fit" aria-label="适应窗口" title="适应窗口（0）">${svg('fit')}</button>
      <button type="button" data-action="open" aria-label="在新标签页打开原图" title="在新标签页打开原图">${svg('open')}</button>
      <span class="sep" aria-hidden="true"></span>
      <button type="button" data-action="close" aria-label="关闭" title="关闭（Esc）">${svg('close')}</button>
    </div>
  </div>
  <div class="image-stage"><img alt="放大预览" draggable="false"></div>
  <div class="image-hint">滚轮或双指缩放 · 拖动查看 · 双击放大 / 还原 · 点击空白处关闭</div>`;
  document.body.appendChild(root);
  picture = root.querySelector('img'); caption = root.querySelector('.image-caption');
  zoomLabel = root.querySelector('.image-zoom'); stage = root.querySelector('.image-stage');
  const openOriginal = () => { if (picture.src) window.open(picture.src, '_blank', 'noopener'); };
  const actions = { prev: () => show(index - 1), next: () => show(index + 1), out: () => zoom(1/1.3), in: () => zoom(1.3), fit: reset, open: openOriginal, close: closeImageViewer };
  root.querySelectorAll('button[data-action]').forEach(button => button.onclick = actions[button.dataset.action]);
  stage.addEventListener('wheel', event => { event.preventDefault(); zoom(event.deltaY < 0 ? 1.12 : 1/1.12, event.clientX, event.clientY); }, { passive:false });
  stage.addEventListener('pointerdown', event => {
    pointers.set(event.pointerId, {x:event.clientX,y:event.clientY}); moved = 0;
    stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener('pointermove', event => {
    const old = pointers.get(event.pointerId); if (!old) return;
    const other = [...pointers.entries()].find(([id]) => id !== event.pointerId)?.[1];
    if (other) {
      const before = Math.hypot(old.x-other.x,old.y-other.y);
      const after = Math.hypot(event.clientX-other.x,event.clientY-other.y);
      if (before > 0) zoom(after/before);
    } else {
      x += event.clientX-old.x; y += event.clientY-old.y;
      root.classList.add('is-dragging'); transform();
    }
    moved += Math.abs(event.clientX-old.x) + Math.abs(event.clientY-old.y);
    pointers.set(event.pointerId, {x:event.clientX,y:event.clientY});
  });
  for (const name of ['pointerup','pointercancel','lostpointercapture']) stage.addEventListener(name, event => {
    pointers.delete(event.pointerId);
    if (!pointers.size) root.classList.remove('is-dragging');
  });
  stage.addEventListener('dblclick', event => {
    if (scale > 1.01) reset(); else zoom(2 / scale, event.clientX, event.clientY);
  });
  // 点击图片以外的空白处关闭（拖动后松手不算点击）
  stage.addEventListener('click', event => {
    if (moved > 6) return;
    if (event.target === stage) closeImageViewer();
  });
  document.addEventListener('keydown', event => {
    if (root.hidden) return;
    if (['Escape','ArrowLeft','ArrowRight','+','=','-','0','Tab'].includes(event.key)) { event.preventDefault(); event.stopImmediatePropagation(); }
    if (event.key === 'Escape') closeImageViewer();
    else if (event.key === 'ArrowLeft') show(index-1);
    else if (event.key === 'ArrowRight') show(index+1);
    else if (event.key === '+' || event.key === '=') zoom(1.3);
    else if (event.key === '-') zoom(1/1.3);
    else if (event.key === '0') reset();
    else if (event.key === 'Tab') {
      const buttons = [...root.querySelectorAll('button:not(:disabled)')].filter(b => b.offsetParent);
      const current = buttons.indexOf(document.activeElement);
      buttons[(current + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
    }
  }, true);
}

function labelOf(image) {
  return (image?.dataset?.caption || image?.getAttribute?.('alt') || image?.getAttribute?.('title') || '').trim();
}

async function show(next) {
  index = Math.max(0, Math.min(images.length-1,next));
  const token = ++generation;
  const image = images[index];
  reset(); picture.hidden = true;
  const name = labelOf(image);
  const prefix = `${index+1} / ${images.length}`;
  caption.textContent = `${prefix} · 正在加载图片…`;
  root.querySelector('[data-action="prev"]').disabled = index === 0;
  root.querySelector('[data-action="next"]').disabled = index === images.length-1;
  try {
    await image._previewImagePromise;
    if (image.dataset.previewImageError) throw new Error('图片加载失败，请重新打开预览后重试');
    if (token !== generation || root.hidden) return;
    picture.onload = () => { if (token === generation) { picture.hidden = false; caption.textContent = name && name !== '放大预览' ? `${prefix} · ${name}` : prefix; } };
    picture.onerror = () => { if (token === generation) caption.textContent = `${prefix} · 图片加载失败，请重新打开预览后重试`; };
    picture.alt = name || '放大预览';
    picture.src = image.src || image.currentSrc;
  } catch (error) { if (token === generation) caption.textContent = `${prefix} · ${error.message}`; }
}

export function closeImageViewer() {
  generation++;
  if (root) { root.hidden = true; picture.removeAttribute('src'); root.classList.remove('is-dragging'); }
  images = []; pointers.clear();
  if (previousFocus?.isConnected) previousFocus.focus({ preventScroll:true });
}

export function wireImageViewer(container) {
  if (container.dataset.imageViewerWired) return;
  container.dataset.imageViewerWired = '1';
  container.addEventListener('click', event => {
    const image = event.target.closest('img');
    if (!image || !container.contains(image)) return;
    event.preventDefault(); event.stopPropagation();
    ensure(); previousFocus = document.activeElement;
    images = [...container.querySelectorAll('img')].filter(img => !img.dataset.previewImageError || img === image);
    root.hidden = false;
    root.querySelector('[data-action="close"]').focus({ preventScroll:true });
    show(images.indexOf(image));
  });
}
