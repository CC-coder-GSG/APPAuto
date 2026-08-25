/**
 * Zentao Account Binding UI
 *
 * Allows the current user to bind their Zentao account so the system can
 * fetch live data from Zentao on their behalf.
 *
 * Modal ID: zentaoBindingModal
 * Exposes: window.OmniQAZentaoBinding = { open }
 */

import { api } from './api.js';

// ─── State ───────────────────────────────────────────────────────────────────
let _binding = null;   // current binding from server, or null
let _saving = false;
let _testing = false;

// ─── DOM helpers ─────────────────────────────────────────────────────────────
function el(id) { return document.getElementById(id); }
function showMsg(msg, type = 'success') {
  if (window.showMessage) window.showMessage(msg, type);
}

// ─── API ─────────────────────────────────────────────────────────────────────
async function loadBinding() {
  try {
    const res = await api('/zentao-binding/me');
    const data = await res.json();
    _binding = data.binding || null;
  } catch {
    _binding = null;
  }
}

async function saveBinding(base_url, zentao_account, zentao_password) {
  const res = await api('/zentao-binding/me', {
    method: 'PUT',
    body: { base_url, zentao_account, zentao_password },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const data = await res.json();
  _binding = data.binding;
}

async function testConnection(base_url, zentao_account, zentao_password) {
  const res = await api('/zentao-binding/me/test', {
    method: 'POST',
    body: { base_url, zentao_account, zentao_password },
  });
  const data = await res.json();
  return data; // { ok, message }
}

async function refreshToken() {
  const res = await api('/zentao-binding/me/refresh', { method: 'POST' });
  const data = await res.json();
  if (data.binding) _binding = data.binding;
  return data; // { ok, message }
}

async function deleteBinding() {
  const res = await api('/zentao-binding/me', { method: 'DELETE' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  _binding = null;
}

// ─── Rendering ───────────────────────────────────────────────────────────────
function renderStatus() {
  const statusEl = el('ztBindingStatus');
  if (!statusEl) return;

  if (!_binding) {
    statusEl.innerHTML = `
      <div style="display:flex; align-items:center; gap:8px; padding:10px 14px;
                  background:#f1f5f9; border-radius:8px; color:#64748b; font-size:13px;">
        <span style="font-size:18px;">🔗</span>
        <span>尚未绑定禅道账号，请在下方填写并保存。</span>
      </div>`;
    return;
  }

  const b = _binding;
  const refreshStatus = b.last_refresh_status;
  const statusColor = refreshStatus === 'ok' ? '#16a34a' : refreshStatus === 'error' ? '#dc2626' : '#64748b';
  const statusText = refreshStatus === 'ok' ? '✅ 正常' : refreshStatus === 'error' ? '❌ 刷新失败' : '—';

  let expiresText = '—';
  if (b.token_expires_at) {
    const expDate = new Date(b.token_expires_at);
    const diffMs = expDate - Date.now();
    if (diffMs > 0) {
      const diffMin = Math.round(diffMs / 60000);
      expiresText = diffMin > 60
        ? `${Math.floor(diffMin / 60)}h${diffMin % 60}m 后过期`
        : `${diffMin} 分钟后过期`;
    } else {
      expiresText = '已过期';
    }
  }

  statusEl.innerHTML = `
    <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:14px 16px; font-size:13px;">
      <div style="display:grid; grid-template-columns: 100px 1fr; gap:6px 12px; color:#334155;">
        <span style="color:#64748b; font-weight:600;">禅道地址</span>
        <span>${escHtml(b.base_url)}</span>
        <span style="color:#64748b; font-weight:600;">账号</span>
        <span>${escHtml(b.zentao_account)}</span>
        <span style="color:#64748b; font-weight:600;">密码</span>
        <span>${b.has_password ? '●●●●●●●●' : '未设置'}</span>
        <span style="color:#64748b; font-weight:600;">Token</span>
        <span>${b.has_token ? `<span style="color:${statusColor};">${statusText}</span>，${escHtml(expiresText)}` : '尚未获取'}</span>
        ${b.last_error_message ? `
        <span style="color:#64748b; font-weight:600;">错误</span>
        <span style="color:#dc2626;">${escHtml(b.last_error_message)}</span>` : ''}
      </div>
    </div>`;
}

function renderForm() {
  const b = _binding;
  const baseUrlEl = el('ztBaseUrl');
  const accountEl = el('ztAccount');
  if (!baseUrlEl || !accountEl) return;
  baseUrlEl.value = b?.base_url || 'http://192.168.2.148:81/zentao';
  accountEl.value = b?.zentao_account || '';
  el('ztPassword').value = '';          // never pre-fill password
  el('ztPasswordHint').style.display = b?.has_password ? 'block' : 'none';
}

function renderActionButtons() {
  const delBtn = el('ztDeleteBtn');
  const refreshBtn = el('ztRefreshBtn');
  if (delBtn) delBtn.style.display = _binding ? 'inline-flex' : 'none';
  if (refreshBtn) refreshBtn.style.display = _binding?.has_password ? 'inline-flex' : 'none';
}

function render() {
  renderStatus();
  renderForm();
  renderActionButtons();
}

function escHtml(str) {
  return String(str ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ─── Event handlers ───────────────────────────────────────────────────────────
async function handleSave() {
  if (_saving) return;
  const base_url = (el('ztBaseUrl')?.value || '').trim();
  const account = (el('ztAccount')?.value || '').trim();
  const password = el('ztPassword')?.value || '';

  if (!base_url) { showMsg('请填写禅道地址', 'error'); return; }
  if (!account) { showMsg('请填写禅道账号', 'error'); return; }
  if (!password && !_binding?.has_password) { showMsg('请填写禅道密码', 'error'); return; }

  // If password is empty but binding already exists, user skipped re-entering — keep old password
  // We must send a password always; prompt user to confirm
  if (!password && _binding?.has_password) {
    const confirm = window.confirm('密码留空时将保持原有密码不变，确认保存其他修改吗？');
    if (!confirm) return;
    // Re-fetch and re-save only account/url fields by re-entering password via prompt
    const reenter = window.prompt('请重新输入禅道密码以确认修改（安全要求）：');
    if (!reenter) return;
    _saving = true;
    setSavingState(true);
    try {
      await saveBinding(base_url, account, reenter);
      render();
      showMsg('绑定已更新', 'success');
    } catch (e) {
      showMsg(e.message || '保存失败', 'error');
    } finally {
      _saving = false;
      setSavingState(false);
    }
    return;
  }

  _saving = true;
  setSavingState(true);
  try {
    await saveBinding(base_url, account, password);
    render();
    window.OmniQAZentao?.invalidate();
    showMsg('禅道账号绑定已保存', 'success');
  } catch (e) {
    showMsg(e.message || '保存失败', 'error');
  } finally {
    _saving = false;
    setSavingState(false);
  }
}

async function handleTest() {
  if (_testing) return;
  const base_url = (el('ztBaseUrl')?.value || '').trim();
  const account = (el('ztAccount')?.value || '').trim();
  const password = el('ztPassword')?.value || '';

  if (!base_url || !account || !password) {
    showMsg('请先填写禅道地址、账号和密码再测试', 'error');
    return;
  }

  _testing = true;
  const testBtn = el('ztTestBtn');
  if (testBtn) { testBtn.disabled = true; testBtn.textContent = '测试中…'; }
  const resultEl = el('ztTestResult');
  if (resultEl) resultEl.textContent = '';

  try {
    const result = await testConnection(base_url, account, password);
    if (result.ok) {
      showMsg('连接测试成功！', 'success');
      if (resultEl) {
        resultEl.textContent = '✅ ' + (result.message || '连接成功');
        resultEl.style.color = '#16a34a';
      }
    } else {
      showMsg('连接失败：' + (result.message || '未知错误'), 'error');
      if (resultEl) {
        resultEl.textContent = '❌ ' + (result.message || '连接失败');
        resultEl.style.color = '#dc2626';
      }
    }
  } catch (e) {
    showMsg('测试请求异常：' + e.message, 'error');
    if (resultEl) {
      resultEl.textContent = '❌ 请求异常：' + e.message;
      resultEl.style.color = '#dc2626';
    }
  } finally {
    _testing = false;
    if (testBtn) { testBtn.disabled = false; testBtn.textContent = '测试连接'; }
  }
}

async function handleRefresh() {
  const refreshBtn = el('ztRefreshBtn');
  if (refreshBtn) { refreshBtn.disabled = true; refreshBtn.textContent = '刷新中…'; }
  try {
    const result = await refreshToken();
    render();
    if (result.ok) {
      showMsg('Token 刷新成功', 'success');
    } else {
      showMsg('Token 刷新失败：' + (result.message || ''), 'error');
    }
  } catch (e) {
    showMsg('刷新请求异常：' + e.message, 'error');
  } finally {
    if (refreshBtn) { refreshBtn.disabled = false; refreshBtn.textContent = '刷新 Token'; }
  }
}

async function handleDelete() {
  if (!_binding) return;
  if (!window.confirm('确认删除禅道账号绑定？删除后系统将无法代您读取禅道数据。')) return;
  try {
    await deleteBinding();
    render();
    showMsg('禅道绑定已删除', 'success');
  } catch (e) {
    showMsg(e.message || '删除失败', 'error');
  }
}

function setSavingState(saving) {
  const saveBtn = el('ztSaveBtn');
  if (!saveBtn) return;
  saveBtn.disabled = saving;
  saveBtn.textContent = saving ? '保存中…' : '保存绑定';
}

// ─── Modal open ───────────────────────────────────────────────────────────────
async function open() {
  const modal = el('zentaoBindingModal');
  if (!modal) return;

  // Show modal immediately with loading state
  modal.style.display = 'flex';
  modal.classList.remove('hidden');

  const statusEl = el('ztBindingStatus');
  if (statusEl) statusEl.innerHTML = '<div style="color:#64748b; font-size:13px; padding:10px;">加载中…</div>';

  await loadBinding();
  render();
}

function close() {
  const modal = el('zentaoBindingModal');
  if (!modal) return;
  modal.style.display = 'none';
  modal.classList.add('hidden');
}

// ─── Bootstrap ────────────────────────────────────────────────────────────────
function init() {
  // Wire up buttons once DOM is ready
  const saveBtn = el('ztSaveBtn');
  const testBtn = el('ztTestBtn');
  const refreshBtn = el('ztRefreshBtn');
  const deleteBtn = el('ztDeleteBtn');
  const closeBtn = el('ztCloseBtn');
  const modal = el('zentaoBindingModal');

  if (saveBtn) saveBtn.addEventListener('click', handleSave);
  if (testBtn) testBtn.addEventListener('click', handleTest);
  if (refreshBtn) refreshBtn.addEventListener('click', handleRefresh);
  if (deleteBtn) deleteBtn.addEventListener('click', handleDelete);
  if (closeBtn) closeBtn.addEventListener('click', close);
  if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

window.OmniQAZentaoBinding = { open, close };
