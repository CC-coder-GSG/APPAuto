// 终端远程查看/操控标签页
// 设备网格 + 状态徽标 + 获取/释放操作权 + 内嵌 ws-scrcpy 画面。
// 锁与状态由后端 /api/terminals 控制面权威维护；画面经 ws-scrcpy 播放器 iframe 呈现。
import { api } from '../api.js';

const STATUS_META = {
  offline: { text: '离线', color: '#94a3b8', bg: '#f1f5f9' },
  idle: { text: '空闲', color: '#16a34a', bg: '#dcfce7' },
  automation: { text: '自动化中', color: '#b45309', bg: '#fef3c7' },
  manual: { text: '操作中', color: '#2563eb', bg: '#dbeafe' },
};

let playerBaseUrl = '';
let playerName = '';         // ws-scrcpy 解码器：broadway / mse / tinyh264（后端可配）
let heldDeviceId = null;     // 当前持有操作权的设备
let heartbeatTimer = null;

function el(id) { return document.getElementById(id); }

export async function loadTerminalTab() {
  const grid = el('terminalGrid');
  if (grid) grid.innerHTML = '<div class="muted" style="padding:20px;">加载中...</div>';
  try {
    const res = await api('/api/terminals');
    const data = await res.json();
    playerBaseUrl = data.player_base_url || '';
    playerName = data.player_name || '';
    renderDevices(data.devices || []);
  } catch (err) {
    if (grid) grid.innerHTML = `<div class="muted" style="padding:20px; color:#dc2626;">加载失败：${err.message || '未知错误'}</div>`;
  }
}

export async function discoverTerminals() {
  const btn = el('terminalDiscoverBtn');
  if (btn) { btn.disabled = true; btn.textContent = '扫描中...'; }
  try {
    const res = await api('/api/terminals/discover', { method: 'POST', headers: window.H });
    const data = await res.json();
    playerBaseUrl = data.player_base_url || '';
    playerName = data.player_name || '';
    renderDevices(data.devices || []);
    window.showMessage && window.showMessage(`扫描完成：在线 ${data.online || 0} 台，新登记 ${data.created || 0} 台`, 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '扫描失败', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔄 扫描设备'; }
  }
}

function renderDevices(devices) {
  const grid = el('terminalGrid');
  if (!grid) return;
  reconcileHeldLock(devices);
  if (!devices.length) {
    grid.innerHTML = '<div class="muted" style="padding:20px;">暂无终端。点「扫描设备」从服务器 adb 拉取，或确认已启用终端控制并连接设备。</div>';
    return;
  }
  grid.innerHTML = devices.map(renderCard).join('');
}

// 根据服务端锁归属对齐本地心跳：刷新页面后若我仍持有某设备的锁，恢复心跳续约；
// 若我已不再持有任何设备，停掉心跳。避免"刷新后丢了释放按钮 + 锁还挂着"的卡死。
function reconcileHeldLock(devices) {
  const myId = Number((window.currentUser && window.currentUser.id) || 0);
  const mine = (devices || []).find((d) => d.status === 'manual' && d.lock
    && d.lock.holder_kind === 'user' && Number(d.lock.holder_user_id) === myId && myId > 0);
  if (mine) {
    if (heldDeviceId !== mine.id) { heldDeviceId = mine.id; startHeartbeat(mine.id); }
  } else if (heldDeviceId) {
    heldDeviceId = null;
    stopHeartbeat();
  }
}

function renderCard(d) {
  const meta = STATUS_META[d.status] || STATUS_META.offline;
  const holder = d.lock && (d.lock.holder_name || d.lock.jenkins_build);
  const holderLine = d.lock
    ? `<div class="muted" style="font-size:12px;">占用：${d.lock.type === 'automation' ? '自动化' : '人工'}${holder ? ' · ' + holder : ''}${d.lock.jenkins_build ? ' (build ' + d.lock.jenkins_build + ')' : ''}</div>`
    : '';
  // "我是否持有操作权"以服务端锁归属为准（而非前端内存），这样刷新页面后仍能
  // 认出自己的锁、继续显示「释放」，不会把自己的锁当成别人的导致卡死。
  const myId = Number((window.currentUser && window.currentUser.id) || 0);
  const isHeldByMe = d.status === 'manual' && d.lock && d.lock.holder_kind === 'user'
    && Number(d.lock.holder_user_id) === myId && myId > 0;
  const isAdmin = !!(window.currentUser && window.currentUser.role === 'admin');
  const viewBtn = `<button class="secondary" onclick="window.OmniQATerminalTab.viewDevice(${d.id})">👁 只读观看</button>`;

  let actions = '';
  if (d.status === 'offline') {
    actions = '<button disabled>离线</button>';
  } else if (d.status === 'idle') {
    actions = `${viewBtn}
               <button onclick="window.OmniQATerminalTab.controlDevice(${d.id})">🖐 操作</button>`;
  } else if (d.status === 'automation') {
    actions = `${viewBtn}
               <button class="secondary" onclick="window.OmniQATerminalTab.preemptDevice(${d.id})" title="中断自动化并接管（管理员）">⚡ 抢占</button>`;
  } else if (d.status === 'manual') {
    if (isHeldByMe) {
      actions = `<button onclick="window.OmniQATerminalTab.viewDevice(${d.id}, true)">🖥 进入操作</button>
                 ${viewBtn}
                 <button class="danger-btn" onclick="window.OmniQATerminalTab.releaseDevice(${d.id})">释放</button>`;
    } else {
      // 被他人占用：可只读观看；管理员可强制释放（清理卡死/残留的锁）
      actions = viewBtn + (isAdmin
        ? ` <button class="secondary" onclick="window.OmniQATerminalTab.forceReleaseDevice(${d.id})" title="强制释放他人占用（管理员）">🔓 强制释放</button>`
        : '');
    }
  }

  return `
    <div class="card" style="min-width:240px; flex:1 1 240px;">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
        <strong style="font-size:15px;">${escapeHtml(d.name)}</strong>
        <span style="font-size:12px; padding:2px 10px; border-radius:10px; color:${meta.color}; background:${meta.bg};">${meta.text}</span>
      </div>
      <div class="muted" style="font-size:12px; margin:4px 0;">${escapeHtml(d.serial)}${d.model ? ' · ' + escapeHtml(d.model) : ''}</div>
      ${holderLine}
      <div class="row" style="margin-top:10px; gap:6px; flex-wrap:wrap;">${actions}</div>
    </div>`;
}

export async function controlDevice(deviceId) {
  try {
    await api(`/api/terminals/${deviceId}/control/acquire`, { method: 'POST', headers: window.H });
    heldDeviceId = deviceId;
    startHeartbeat(deviceId);
    window.showMessage && window.showMessage('已获取操作权', 'success');
    await loadTerminalTab();
    await viewDevice(deviceId, true);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '获取操作权失败', 'error');
  }
}

export async function releaseDevice(deviceId) {
  try {
    await api(`/api/terminals/${deviceId}/control/release`, { method: 'POST', headers: window.H });
    if (heldDeviceId === deviceId) { heldDeviceId = null; stopHeartbeat(); }
    closeViewer();
    window.showMessage && window.showMessage('已释放操作权', 'success');
    await loadTerminalTab();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '释放失败', 'error');
  }
}

export async function forceReleaseDevice(deviceId) {
  if (!confirm('强制释放将解除他人对该终端的占用，确定继续吗？')) return;
  try {
    await api(`/api/terminals/${deviceId}/control/force-release`, { method: 'POST', headers: window.H });
    if (heldDeviceId === deviceId) { heldDeviceId = null; stopHeartbeat(); }
    window.showMessage && window.showMessage('已强制释放', 'success');
    await loadTerminalTab();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '强制释放失败', 'error');
  }
}

export async function preemptDevice(deviceId) {
  if (!confirm('抢占会中断该终端上正在运行的自动化测试，确定继续吗？')) return;
  try {
    await api(`/api/terminals/${deviceId}/control/preempt`, { method: 'POST', headers: window.H });
    heldDeviceId = deviceId;
    startHeartbeat(deviceId);
    window.showMessage && window.showMessage('已抢占并接管', 'success');
    await loadTerminalTab();
    await viewDevice(deviceId, true);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '抢占失败', 'error');
  }
}

export async function viewDevice(deviceId, control = false) {
  try {
    const res = await api(`/api/terminals/${deviceId}/stream-ticket`, {
      method: 'POST', headers: window.H, body: { mode: control ? 'control' : 'view' },
    });
    const ticket = await res.json();
    openViewer(deviceId, ticket, control);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '打开画面失败', 'error');
  }
}

function openViewer(deviceId, ticket, control) {
  const panel = el('terminalViewer');
  const frameWrap = el('terminalViewerFrame');
  const titleEl = el('terminalViewerTitle');
  if (!panel || !frameWrap) return;
  panel.classList.remove('hidden');
  if (titleEl) titleEl.textContent = `终端 #${deviceId} · ${control ? '操作模式' : '只读观看'}`;

  // 找到设备 serial：从已渲染数据里取（ticket 不含 serial，这里再查一次轻量接口可省略，
  // 直接用 player_base_url + udid 由 ws-scrcpy 播放器接管渲染）。
  if (!playerBaseUrl) {
    frameWrap.innerHTML = `
      <div class="muted" style="padding:24px; line-height:1.8;">
        画面播放器（ws-scrcpy）未配置。<br>
        请在服务器部署 ws-scrcpy 并设置 <code>APP_TERMINAL_PLAYER_URL</code> 后重试。<br>
        操作权与锁状态已生效；票据：<code>${ticket.token.slice(0, 12)}…</code>（${ticket.mode}）
      </div>`;
    return;
  }
  const serial = ticket.serial || '';
  // ws-scrcpy 的流深链必须同时带 player（解码器）和 ws（设备流 WebSocket 地址）。
  // 缺 player → "Missing required parameter player"；缺 ws → "Missing required parameter ws"。
  // player：broadway=纯 JS 软解，兼容性最好；mse=Chrome 硬解更省 CPU（APP_TERMINAL_PLAYER_NAME 可覆盖）。
  // ws：固定走 proxy-adb，scrcpy 端口 8886 是 ws-scrcpy 的约定值（adb 转发，按 udid 区分）。
  // 注意 remote 里的冒号要先 encode 成 %3A，整段 ws 再 encodeURIComponent 一次（对齐 ws-scrcpy 自身生成的链接）。
  const base = playerBaseUrl.replace(/\/$/, '');
  const wsBase = base.replace(/^http/i, 'ws');
  const player = playerName || 'broadway';
  const innerWs = `${wsBase}/?action=proxy-adb&remote=tcp%3A8886&udid=${encodeURIComponent(serial)}`;
  const src = `${base}/#!action=stream&udid=${encodeURIComponent(serial)}&player=${encodeURIComponent(player)}&ws=${encodeURIComponent(innerWs)}`;

  // 只读模式用 pointer-events:none 让 iframe 不接收任何点击/滑动 → 真正只读，
  // 防止"只读观看"也能操作设备。操作模式才放开交互。
  const interactive = control ? 'auto' : 'none';
  const badge = control
    ? '<span style="position:absolute; top:8px; left:8px; z-index:2; font-size:12px; padding:2px 10px; border-radius:10px; color:#2563eb; background:#dbeafe;">🖐 操作模式</span>'
    : '<span style="position:absolute; top:8px; left:8px; z-index:2; font-size:12px; padding:2px 10px; border-radius:10px; color:#475569; background:#e2e8f0;">👁 只读（不可操作）</span>';
  // 竖屏手机：容器收窄、加高并居中，避免宽容器把画面挤到右侧/高度不够被截断。
  frameWrap.innerHTML = `
    <div style="display:flex; justify-content:center;">
      <div style="position:relative; width:min(460px, 96vw); height:min(82vh, 900px); background:#000; border-radius:8px; overflow:hidden;">
        ${badge}
        <iframe src="${src}" style="width:100%; height:100%; border:0; pointer-events:${interactive};" allow="autoplay; fullscreen"></iframe>
      </div>
    </div>
    <div class="muted" style="text-align:center; margin-top:8px; font-size:12px;">
      画面偏小可 <a href="${src}" target="_blank" rel="noopener">在新窗口放大查看</a>${control ? '' : '（新窗口为 ws-scrcpy 原生页，可操作，请注意）'}
    </div>`;
}

export function closeViewer() {
  const panel = el('terminalViewer');
  const frameWrap = el('terminalViewerFrame');
  if (frameWrap) frameWrap.innerHTML = '';
  if (panel) panel.classList.add('hidden');
}

function startHeartbeat(deviceId) {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    api(`/api/terminals/${deviceId}/control/heartbeat`, { method: 'POST', headers: window.H }).catch(() => {});
  }, 60000);
}

function stopHeartbeat() {
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// SSE：设备状态变化时刷新列表（仅当终端页可见）
function bindTerminalSSE() {
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;
  const refresh = () => {
    const tabEl = document.getElementById('tab-terminal');
    if (tabEl && !tabEl.classList.contains('hidden')) loadTerminalTab();
  };
  window.OmniQASSE.subscribe('device.status_changed', refresh);
  window.OmniQASSE.subscribe('device.lock_changed', refresh);
}
bindTerminalSSE();

window.OmniQATerminalTab = {
  loadTerminalTab,
  discoverTerminals,
  controlDevice,
  forceReleaseDevice,
  releaseDevice,
  preemptDevice,
  viewDevice,
  closeViewer,
};
