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
