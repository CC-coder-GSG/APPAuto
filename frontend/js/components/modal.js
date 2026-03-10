const modalHooks = new Map();

function resolveEl(target) {
  if (!target) return null;
  if (typeof target === 'string') return document.getElementById(target);
  return target;
}

export function openModal(target, opts = {}) {
  const el = resolveEl(target);
  if (!el) return false;
  el.classList.remove('hidden');
  el.dataset.open = '1';
  if (opts.onClose && typeof opts.onClose === 'function') {
    modalHooks.set(el.id || String(Math.random()), opts.onClose);
  }
  if (opts.onOpen && typeof opts.onOpen === 'function') opts.onOpen(el);
  return true;
}

export function closeModal(target, opts = {}) {
  const el = resolveEl(target);
  if (!el) return false;
  el.classList.add('hidden');
  el.dataset.open = '0';
  if (opts.onClose && typeof opts.onClose === 'function') opts.onClose(el);
  return true;
}

window.OmniQAModal = { openModal, closeModal };
