/**
 * Personal AI (DeepSeek) API Key settings modal.
 *
 * Modal ID: aiConfigModal
 * Exposes: window.OmniQAAIConfig = { open, close }
 *
 * The plaintext API Key is sent once on save. The GET endpoint never returns
 * it — only a masked form like "sk-abcd********wxyz". The local form field is
 * cleared on open and after save so it does not linger in the DOM.
 */

import { api } from '../api.js';

let _cfg = null;          // { provider_name, configured, is_enabled, api_key_masked, updated_at }
let _busy = false;

function el(id) { return document.getElementById(id); }
function showMsg(msg, type = 'success') {
  if (window.showMessage) window.showMessage(msg, type);
}
function escHtml(str) {
  return String(str ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function providerName() {
  return el('aiCfgProvider')?.value || 'deepseek';
}

async function loadConfig() {
  try {
    const res = await api(`/user/ai-config?provider_name=${encodeURIComponent(providerName())}`);
    _cfg = await res.json();
  } catch (e) {
    _cfg = null;
  }
}

async function saveConfig(apiKey) {
  const res = await api('/user/ai-config', {
    method: 'POST',
    body: {
      provider_name: providerName(),
      api_key: apiKey,
      is_enabled: true,
    },
  });
  const data = await res.json();
  _cfg = data;
  return data;
}

async function validateConfig(apiKey) {
  const payload = { provider_name: providerName() };
  const trimmed = (apiKey || '').trim();
  if (trimmed) payload.api_key = trimmed;
  const res = await api('/user/ai-config/validate', { method: 'POST', body: payload });
  return res.json(); // { ok, message, provider_name }
}

function renderStatus() {
  const statusEl = el('aiCfgStatus');
  if (!statusEl) return;

  if (!_cfg || !_cfg.configured) {
    statusEl.innerHTML = `
      <div style="display:flex; align-items:center; gap:8px; padding:10px 14px;
                  background:#f1f5f9; border-radius:8px; color:#64748b; font-size:13px;">
        <span style="font-size:18px;">🔑</span>
        <span>尚未配置 DeepSeek API Key，AI 处理需求前请先保存。</span>
      </div>`;
    el('aiCfgKeyHint').style.display = 'none';
    return;
  }

  const updated = _cfg.updated_at ? new Date(_cfg.updated_at).toLocaleString() : '—';
  const enabled = _cfg.is_enabled !== false;
  const badge = enabled
    ? `<span style="color:#16a34a; font-weight:600;">✅ 已启用</span>`
    : `<span style="color:#dc2626; font-weight:600;">⏸ 已禁用</span>`;
  statusEl.innerHTML = `
    <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:14px 16px; font-size:13px;">
      <div style="display:grid; grid-template-columns: 80px 1fr; gap:6px 12px; color:#334155;">
        <span style="color:#64748b; font-weight:600;">Provider</span>
        <span>${escHtml(_cfg.provider_name || 'deepseek')}</span>
        <span style="color:#64748b; font-weight:600;">API Key</span>
        <span style="font-family: Menlo, Consolas, monospace;">${escHtml(_cfg.api_key_masked || '—')}</span>
        <span style="color:#64748b; font-weight:600;">状态</span>
        <span>${badge}</span>
        <span style="color:#64748b; font-weight:600;">上次更新</span>
        <span>${escHtml(updated)}</span>
      </div>
    </div>`;
  el('aiCfgKeyHint').style.display = 'block';
}

function renderForm() {
  const keyEl = el('aiCfgApiKey');
  if (keyEl) keyEl.value = '';
  const result = el('aiCfgValidateResult');
  if (result) { result.textContent = ''; result.style.color = ''; }
  const provEl = el('aiCfgProvider');
  if (provEl && _cfg?.provider_name) provEl.value = _cfg.provider_name;
}

function render() {
  renderStatus();
  renderForm();
}

async function handleSave() {
  if (_busy) return;
  const key = (el('aiCfgApiKey')?.value || '').trim();
  if (!key) {
    showMsg('请输入 API Key 后再保存', 'error');
    return;
  }
  _busy = true;
  const btn = el('aiCfgSaveBtn');
  if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
  try {
    await saveConfig(key);
    render();
    showMsg('已保存，后续 AI 处理将使用你自己的额度', 'success');
  } catch (e) {
    showMsg(e.message || '保存失败', 'error');
  } finally {
    _busy = false;
    if (btn) { btn.disabled = false; btn.textContent = '保存'; }
  }
}

async function handleValidate() {
  if (_busy) return;
  const key = el('aiCfgApiKey')?.value || '';
  const result = el('aiCfgValidateResult');
  _busy = true;
  const btn = el('aiCfgValidateBtn');
  if (btn) { btn.disabled = true; btn.textContent = '校验中…'; }
  if (result) { result.textContent = ''; result.style.color = ''; }
  try {
    const data = await validateConfig(key);
    if (data.ok) {
      showMsg('API Key 有效', 'success');
      if (result) {
        result.textContent = '✅ ' + (data.message || '有效');
        result.style.color = '#16a34a';
      }
    } else {
      showMsg('校验失败：' + (data.message || ''), 'error');
      if (result) {
        result.textContent = '❌ ' + (data.message || '校验失败');
        result.style.color = '#dc2626';
      }
    }
  } catch (e) {
    showMsg('校验请求异常：' + e.message, 'error');
    if (result) {
      result.textContent = '❌ 请求异常：' + e.message;
      result.style.color = '#dc2626';
    }
  } finally {
    _busy = false;
    if (btn) { btn.disabled = false; btn.textContent = '校验'; }
  }
}

async function open() {
  const modal = el('aiConfigModal');
  if (!modal) return;
  modal.style.display = 'flex';
  modal.classList.remove('hidden');
  const statusEl = el('aiCfgStatus');
  if (statusEl) statusEl.innerHTML = '<div style="color:#64748b; font-size:13px; padding:10px;">加载中…</div>';
  await loadConfig();
  render();
}

function close() {
  const modal = el('aiConfigModal');
  if (!modal) return;
  modal.style.display = 'none';
  modal.classList.add('hidden');
  // clear the plaintext field so it doesn't linger in the DOM
  const keyEl = el('aiCfgApiKey');
  if (keyEl) keyEl.value = '';
}

function init() {
  const modal = el('aiConfigModal');
  const saveBtn = el('aiCfgSaveBtn');
  const validateBtn = el('aiCfgValidateBtn');
  const closeBtn = el('aiCfgCloseBtn');
  const providerEl = el('aiCfgProvider');
  if (saveBtn) saveBtn.addEventListener('click', handleSave);
  if (validateBtn) validateBtn.addEventListener('click', handleValidate);
  if (closeBtn) closeBtn.addEventListener('click', close);
  if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
  if (providerEl) providerEl.addEventListener('change', async () => {
    await loadConfig();
    render();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

window.OmniQAAIConfig = { open, close };
