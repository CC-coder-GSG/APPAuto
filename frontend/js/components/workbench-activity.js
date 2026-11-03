// Shared by reads and user operations. A completion only ends its own entry.
const entries = new Map();
let sequence = 0;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function mount() {
  let host = document.getElementById('workbenchActivity');
  if (!host) {
    const title = document.querySelector('.workbench-shell-header h2');
    host = document.createElement('details');
    host.id = 'workbenchActivity';
    (title || document.body).appendChild(host);
  }
  if (!document.getElementById('workbenchActivityStyle')) {
    const style = document.createElement('style');
    style.id = 'workbenchActivityStyle';
    style.textContent = `
      #workbenchActivity{display:inline-block;position:relative;margin-left:12px;vertical-align:middle;font-size:12px;font-weight:400;line-height:1.5}
      #workbenchActivity[hidden]{display:none} #workbenchActivity>summary{list-style:none;cursor:pointer;color:#475569;display:flex;align-items:center;gap:6px;padding:3px 8px;border:1px solid #e2e8f0;border-radius:20px;background:#f8fafc}
      #workbenchActivity .wb-activity-list{position:absolute;top:100%;left:0;width:min(330px,80vw);max-height:320px;overflow:auto;background:white;border:1px solid #e2e8f0;border-radius:10px;padding:8px;box-shadow:0 8px 24px #0f172a20;z-index:80}
      .wb-activity-row{padding:8px;border-bottom:1px solid #f1f5f9;word-break:break-word}.wb-activity-row:last-child{border:0}
      .wb-spin{display:inline-block;width:12px;height:12px;border:2px solid #dbeafe;border-top-color:#2563eb;border-radius:50%;animation:wb-spin .8s linear infinite;flex-shrink:0}@keyframes wb-spin{to{transform:rotate(360deg)}}
      .wb-error{display:flex;align-items:flex-start;gap:10px;padding:12px 14px;margin:8px 0;background:#fffaf5;border:1px solid #fed7aa;border-radius:9px;color:#9a3412;font-size:13px;line-height:1.6}.wb-error-content{flex:1;min-width:0}.wb-error details{color:#64748b;font-size:12px}.wb-error summary{cursor:pointer}.wb-error pre{white-space:pre-wrap;word-break:break-word;margin:4px 0}
      button.wb-text-button{background:transparent!important;color:#2563eb!important;border:0!important;border-radius:4px!important;padding:2px 6px!important;min-height:24px!important;box-shadow:none!important;font-size:12px!important;white-space:nowrap}
      .wb-operation-status{padding:10px 12px;margin:10px 0;border-radius:8px;background:#eff6ff;color:#1d4ed8;font-size:13px;display:flex;gap:8px;align-items:center}.wb-operation-status[data-error]{background:#fff7ed;color:#9a3412}
      #wb-operation-dock{position:fixed;right:18px;bottom:18px;z-index:12000;max-width:min(360px,90vw);background:white;border:1px solid #cbd5e1;border-radius:10px;box-shadow:0 4px 20px #0f172a22;padding:8px;font-size:13px}
      @media(prefers-reduced-motion:reduce){.wb-spin{animation:none}}
    `;
    document.head.appendChild(style);
  }
  return host;
}

function render() {
  const host = mount();
  if (!host) return;
  const rows = [...entries.values()];
  host.hidden = !rows.length;
  const running = rows.filter(row => row.state === 'running').length;
  const label = running ? (running === 1 ? rows.find(row => row.state === 'running').label : `${running} 项正在处理`) : `${rows.length} 项需要查看`;
  host.innerHTML = `<summary aria-live="polite">${running ? '<span class="wb-spin" aria-hidden="true"></span>' : '⚠'} ${esc(label)}</summary><div class="wb-activity-list">${rows.map(row => `<div class="wb-activity-row"><span>${esc(row.label)}</span>${row.error ? `<div>${esc(row.error)}</div>` : ''}${row.open ? `<button class="wb-text-button" data-activity="${row.id}">查看</button>` : ''}${row.state !== 'running' ? `<button class="wb-text-button" data-dismiss="${row.id}">忽略</button>` : ''}</div>`).join('')}</div>`;
  host.querySelectorAll('[data-activity]').forEach(btn => btn.onclick = () => entries.get(Number(btn.dataset.activity))?.open?.());
  host.querySelectorAll('[data-dismiss]').forEach(btn => btn.onclick = () => { entries.delete(Number(btn.dataset.dismiss)); render(); });
}

export function beginActivity(label, { open } = {}) {
  const id = ++sequence;
  const row = { id, label, state: 'running', open };
  entries.set(id, row); render();
  return {
    end() { entries.delete(id); render(); },
    fail(error) { row.state = 'failed'; row.error = String(error); render(); },
  };
}

export function requestActivity(url, options = {}) {
  if ((options.method || 'GET').toUpperCase() !== 'GET') {
    return String(url).startsWith('/workbench/preflight-refresh') || String(url).startsWith('/overall-test/sync-')
      ? beginActivity('禅道数据同步中') : null;
  }
  const labels = [['/workbench/mine', '需求更新中'], ['/workbench/retest', '复测更新中'], ['/workbench/review', '审查更新中'], ['/workbench/tasks', '任务更新中'], ['/overall-test/', '测试更新中'], ['/feedbacks/my-todo', '反馈待办更新中'], ['/bugs/dispatched-to-me', '指派 Bug 更新中']];
  const label = labels.find(([prefix]) => String(url).startsWith(prefix))?.[1];
  return label ? beginActivity(label) : null;
}

export function errorPanel(title, error, action) {
  mount();
  return `<div class="wb-error" role="alert"><span aria-hidden="true">⚠</span><div class="wb-error-content"><strong>${esc(title)}</strong><div>暂时无法更新，请稍后重试。</div><details><summary>查看原因</summary><pre>${esc(error?.message || error || '请求未完成')}</pre></details></div><button type="button" class="wb-text-button" onclick="${action}">重试</button></div>`;
}

document.addEventListener('DOMContentLoaded', render);
window.OmniQAWorkbenchActivity = { begin: beginActivity };
