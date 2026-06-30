export function showMessage(message, type = 'success') {
  if (typeof window.showMessage === 'function' && window.showMessage !== showMessage) {
    return window.showMessage(message, type);
  }
  const wrap = document.getElementById('toastWrap');
  if (!wrap) return;
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.innerText = message;
  wrap.appendChild(node);
  setTimeout(() => {
    node.classList.add('hide');
    setTimeout(() => node.remove(), 220);
  }, 3000);
}

window.OmniQACommon = { showMessage };

// ── 全局加载遮罩：耗时网络操作时给个明确提示，避免用户干等 ──
let _loadingCount = 0;
let _loadingEl = null;
let _loadingMsgEl = null;

function _ensureLoadingEl() {
  if (_loadingEl) return _loadingEl;
  if (!document.getElementById('omniqaLoadingStyle')) {
    const st = document.createElement('style');
    st.id = 'omniqaLoadingStyle';
    st.textContent = '@keyframes omniqaSpin{to{transform:rotate(360deg)}}';
    document.head.appendChild(st);
  }
  const el = document.createElement('div');
  el.id = 'omniqaLoadingOverlay';
  el.style.cssText = 'position:fixed; inset:0; z-index:4000; display:none; align-items:center; justify-content:center; background:rgba(15,23,42,.32); backdrop-filter:blur(2px);';
  el.innerHTML = `
    <div style="min-width:240px; max-width:80vw; background:#fff; border-radius:16px; box-shadow:0 12px 40px rgba(2,6,23,.28); padding:26px 32px; display:flex; flex-direction:column; align-items:center; gap:14px;">
      <div style="width:38px; height:38px; border:3px solid #e2e8f0; border-top-color:#6366f1; border-radius:50%; animation:omniqaSpin .8s linear infinite;"></div>
      <div id="omniqaLoadingMsg" style="font-size:14px; color:#334155; font-weight:600; text-align:center; line-height:1.5;">处理中，请稍候…</div>
    </div>`;
  document.body.appendChild(el);
  _loadingEl = el;
  _loadingMsgEl = el.querySelector('#omniqaLoadingMsg');
  return el;
}

export function showLoading(message = '处理中，请稍候…') {
  const el = _ensureLoadingEl();
  _loadingCount += 1;
  if (_loadingMsgEl) _loadingMsgEl.textContent = message;
  el.style.display = 'flex';
}

export function hideLoading(force = false) {
  _loadingCount = force ? 0 : Math.max(0, _loadingCount - 1);
  if (_loadingCount === 0 && _loadingEl) _loadingEl.style.display = 'none';
}

// 包裹一个异步操作：自动显示/隐藏加载遮罩（无论成功失败都收起）。
export async function withLoading(message, fn) {
  showLoading(message);
  try {
    return await fn();
  } finally {
    hideLoading();
  }
}

window.OmniQALoading = { show: showLoading, hide: hideLoading, withLoading };
window.showLoading = showLoading;
window.hideLoading = hideLoading;
window.withLoading = withLoading;
