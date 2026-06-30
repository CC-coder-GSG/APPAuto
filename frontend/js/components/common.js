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
  // 顶部非阻塞加载条：靠上居中、pointer-events:none，加载时仍可操作/切换页面。
  const el = document.createElement('div');
  el.id = 'omniqaLoadingOverlay';
  el.style.cssText = 'position:fixed; top:18px; left:50%; transform:translateX(-50%); z-index:4000; display:none; align-items:center; gap:10px; background:#fff; border-radius:12px; box-shadow:0 8px 28px rgba(2,6,23,.22); padding:11px 18px; pointer-events:none; max-width:90vw;';
  el.innerHTML = `
    <div style="width:20px; height:20px; border:2.5px solid #e2e8f0; border-top-color:#6366f1; border-radius:50%; animation:omniqaSpin .8s linear infinite; flex:none;"></div>
    <div id="omniqaLoadingMsg" style="font-size:13px; color:#334155; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">处理中，请稍候…</div>`;
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
