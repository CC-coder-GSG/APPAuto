let root, picture, caption, images = [], index = 0, generation = 0;
let scale = 1, x = 0, y = 0, previousFocus;
const pointers = new Map();
const transform = () => { picture.style.transform = `translate(${x}px,${y}px) scale(${scale})`; };
function reset() { scale = 1; x = y = 0; pointers.clear(); transform(); }
function zoom(factor) { scale = Math.min(8, Math.max(.25, scale * factor)); transform(); }

function ensure() {
  if (root) return;
  root = document.createElement('div');
  root.id = 'entityImageViewer'; root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-label', '图片查看器'); root.hidden = true;
  root.innerHTML = `<style>
    #entityImageViewer{position:fixed;inset:0;z-index:20000;background:#0f172af5;display:flex;flex-direction:column;color:white}#entityImageViewer[hidden]{display:none}
    #entityImageViewer .image-toolbar{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:8px;padding:12px;background:#0f172a;z-index:1}
    #entityImageViewer button{color:#f8fafc;background:#334155;border:1px solid #64748b;border-radius:6px;padding:6px 10px;font-size:13px;box-shadow:none}
    #entityImageViewer .image-stage{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;overflow:hidden;touch-action:none}
    #entityImageViewer img{max-width:94%;max-height:94%;object-fit:contain;user-select:none;cursor:grab;transform-origin:center}
    #entityImageViewer .image-caption{text-align:center;padding:8px;font-size:12px;min-height:30px}
  </style><div class="image-toolbar"><button data-action="prev" aria-label="上一张">←</button><button data-action="next" aria-label="下一张">→</button><button data-action="out" aria-label="缩小">－</button><button data-action="in" aria-label="放大">＋</button><button data-action="fit">适应窗口</button><button data-action="close">关闭</button></div><div class="image-stage"><img alt="放大预览" draggable="false"></div><div class="image-caption" role="status"></div>`;
  document.body.appendChild(root);
  picture = root.querySelector('img'); caption = root.querySelector('.image-caption');
  const actions = { prev: () => show(index - 1), next: () => show(index + 1), out: () => zoom(1/1.3), in: () => zoom(1.3), fit: reset, close: closeImageViewer };
  root.querySelectorAll('button').forEach(button => button.onclick = actions[button.dataset.action]);
  const stage = root.querySelector('.image-stage');
  stage.addEventListener('wheel', event => { event.preventDefault(); zoom(event.deltaY < 0 ? 1.12 : 1/1.12); }, { passive:false });
  stage.addEventListener('pointerdown', event => { pointers.set(event.pointerId, {x:event.clientX,y:event.clientY}); stage.setPointerCapture(event.pointerId); });
  stage.addEventListener('pointermove', event => {
    const old = pointers.get(event.pointerId); if (!old) return;
    const other = [...pointers.entries()].find(([id]) => id !== event.pointerId)?.[1];
    if (other) {
      const before = Math.hypot(old.x-other.x,old.y-other.y);
      const after = Math.hypot(event.clientX-other.x,event.clientY-other.y);
      if (before > 0) zoom(after/before);
    } else { x += event.clientX-old.x; y += event.clientY-old.y; transform(); }
    pointers.set(event.pointerId, {x:event.clientX,y:event.clientY});
  });
  for (const name of ['pointerup','pointercancel','lostpointercapture']) stage.addEventListener(name,event=>pointers.delete(event.pointerId));
  document.addEventListener('keydown', event => {
    if (root.hidden) return;
    if (['Escape','ArrowLeft','ArrowRight','+','-','Tab'].includes(event.key)) { event.preventDefault(); event.stopImmediatePropagation(); }
    if (event.key === 'Escape') closeImageViewer();
    else if (event.key === 'ArrowLeft') show(index-1);
    else if (event.key === 'ArrowRight') show(index+1);
    else if (event.key === '+') zoom(1.3);
    else if (event.key === '-') zoom(1/1.3);
    else if (event.key === 'Tab') {
      const buttons = [...root.querySelectorAll('button:not(:disabled)')];
      const current = buttons.indexOf(document.activeElement);
      buttons[(current + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
    }
  }, true);
}

async function show(next) {
  index = Math.max(0, Math.min(images.length-1,next));
  const token = ++generation;
  const image = images[index];
  reset(); picture.hidden = true;
  caption.textContent = `正在加载图片 ${index+1} / ${images.length}…`;
  root.querySelector('[data-action="prev"]').disabled = index === 0;
  root.querySelector('[data-action="next"]').disabled = index === images.length-1;
  try {
    await image._previewImagePromise;
    if (image.dataset.previewImageError) throw new Error('图片加载失败，请重新打开预览后重试');
    if (token !== generation || root.hidden) return;
    picture.onload = () => { if (token === generation) { picture.hidden = false; caption.textContent = `${index+1} / ${images.length} · 滚轮或双指缩放，拖动查看`; } };
    picture.onerror = () => { if (token === generation) caption.textContent = '图片加载失败，请重新打开预览后重试'; };
    picture.src = image.src || image.currentSrc;
  } catch (error) { if (token === generation) caption.textContent = error.message; }
}

export function closeImageViewer() {
  generation++;
  if (root) { root.hidden = true; picture.removeAttribute('src'); }
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
    images = [...container.querySelectorAll('img')]; root.hidden = false;
    root.querySelector('[data-action="close"]').focus({ preventScroll:true });
    show(images.indexOf(image));
  });
}
