import { api } from '../api.js';
import { escapeHtml } from '../utils.js';

// 工时记录弹窗（任务看板 / 任务工作台共用，2026-07-17 工时口径升级配套）。
// 展示某禅道任务已提交的工时记录（暂停/完成时平台按天自动结算 + 手工记录），
// 本人的记录可修改日期/小时/备注（后端用本人网页凭据提交禅道，保持工时归属）。
// 跨 tab 复用 → 弹窗直接挂 body（项目惯例），不依赖任何 tab 的 DOM。

const ctx = { taskId: null, efforts: [], canEditAny: false, editingId: null };

function ensureModal() {
  if (document.getElementById('taskEffortModal')) return;
  const wrap = document.createElement('div');
  wrap.id = 'taskEffortModal';
  wrap.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,.35); z-index:9999; display:none; align-items:center; justify-content:center;';
  wrap.innerHTML = `
    <div style="width:min(640px, 94vw); max-height:82vh; display:flex; flex-direction:column; background:#fff; border-radius:10px; box-shadow:0 12px 28px rgba(0,0,0,.18); padding:18px;">
      <h3 style="margin:0 0 4px 0;">🕒 工时记录 <span id="taskEffortTaskLabel" style="color:#1d4ed8;"></span></h3>
      <div id="taskEffortHint" class="muted" style="font-size:12px; margin-bottom:10px;"></div>
      <div id="taskEffortBody" style="flex:1; min-height:120px; overflow:auto; border:1px solid #e2e8f0; border-radius:8px;"></div>
      <div class="row" style="justify-content:space-between; gap:8px; margin-top:14px;">
        <span id="taskEffortTotal" class="muted" style="font-size:12px; align-self:center;"></span>
        <span class="row" style="gap:8px;">
          <button class="secondary" onclick="window.reloadTaskEffortModal()">🔄 刷新</button>
          <button class="secondary" onclick="window.closeTaskEffortModal()">关闭</button>
        </span>
      </div>
    </div>`;
  wrap.addEventListener('click', (e) => { if (e.target === wrap) window.closeTaskEffortModal(); });
  document.body.appendChild(wrap);
}

function renderRows() {
  const body = document.getElementById('taskEffortBody');
  const totalEl = document.getElementById('taskEffortTotal');
  if (!body) return;
  if (!ctx.efforts.length) {
    body.innerHTML = '<div class="muted" style="padding:18px; text-align:center; font-size:13px;">该任务暂无工时记录</div>';
    if (totalEl) totalEl.textContent = '';
    return;
  }
  const rows = ctx.efforts.map((e) => {
    if (ctx.editingId === e.id) {
      return `
        <tr style="background:#f0f9ff;">
          <td style="padding:6px 10px;"><input id="effortEditDate" type="date" value="${escapeHtml(e.date)}" style="width:130px;"></td>
          <td style="padding:6px 10px;"><input id="effortEditConsumed" type="number" min="0.1" max="999" step="0.1" value="${e.consumed}" style="width:70px;"></td>
          <td style="padding:6px 10px;" class="muted">${escapeHtml(e.account || '')}</td>
          <td style="padding:6px 10px;"><input id="effortEditWork" value="${escapeHtml(e.work || '')}" placeholder="工作内容" style="width:100%; box-sizing:border-box;"></td>
          <td style="padding:6px 10px; white-space:nowrap;">
            <button style="padding:2px 10px; font-size:12px; background:#0d9488;" onclick="window.saveTaskEffortEdit(${e.id})">保存</button>
            <button class="secondary" style="padding:2px 10px; font-size:12px;" onclick="window.cancelTaskEffortEdit()">取消</button>
          </td>
        </tr>`;
    }
    const editBtn = e.can_edit
      ? `<button class="secondary" style="padding:2px 10px; font-size:12px;" onclick="window.startTaskEffortEdit(${e.id})">✏️ 修改</button>`
      : '<span class="muted" style="font-size:12px;">—</span>';
    return `
      <tr style="border-top:1px solid #f1f5f9;">
        <td style="padding:6px 10px; white-space:nowrap;">${escapeHtml(e.date)}</td>
        <td style="padding:6px 10px; font-weight:600; color:#0f172a;">${e.consumed}h</td>
        <td style="padding:6px 10px;" class="muted">${escapeHtml(e.account || '')}</td>
        <td style="padding:6px 10px; color:#475569; word-break:break-all;">${escapeHtml(e.work || '')}</td>
        <td style="padding:6px 10px; white-space:nowrap;">${editBtn}</td>
      </tr>`;
  }).join('');
  body.innerHTML = `
    <table style="width:100%; border-collapse:collapse; font-size:13px;">
      <thead>
        <tr style="background:#f8fafc; color:#475569; font-size:12px; text-align:left;">
          <th style="padding:6px 10px;">日期</th>
          <th style="padding:6px 10px;">消耗</th>
          <th style="padding:6px 10px;">记录人</th>
          <th style="padding:6px 10px;">工作内容</th>
          <th style="padding:6px 10px;">操作</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
  if (totalEl) {
    const total = ctx.efforts.reduce((s, e) => s + (Number(e.consumed) || 0), 0);
    totalEl.textContent = `共 ${ctx.efforts.length} 条，合计 ${Math.round(total * 100) / 100}h`;
  }
}

async function loadEfforts() {
  const hint = document.getElementById('taskEffortHint');
  const body = document.getElementById('taskEffortBody');
  if (body) body.innerHTML = '<div class="muted" style="padding:18px; text-align:center; font-size:13px;">正在从禅道拉取工时记录…</div>';
  try {
    const res = await (await api(`/workbench/tasks/${ctx.taskId}/efforts`)).json();
    if (!res.ok) {
      if (body) body.innerHTML = `<div style="padding:18px; text-align:center; font-size:13px; color:#b91c1c;">${escapeHtml(res.error || '拉取工时记录失败')}</div>`;
      return;
    }
    ctx.efforts = res.efforts || [];
    ctx.canEditAny = !!res.can_edit_any;
    if (hint) {
      hint.textContent = ctx.canEditAny
        ? '暂停/完成任务时平台按天自动结算并提交禅道；本人的记录可在此修改。'
        : '未绑定禅道网页登录凭据，仅可查看（修改工时须以本人身份提交禅道）。';
    }
    renderRows();
  } catch (err) {
    if (body) body.innerHTML = `<div style="padding:18px; text-align:center; font-size:13px; color:#b91c1c;">${escapeHtml(err.message || '拉取工时记录失败')}</div>`;
  }
}

window.openTaskEffortModal = async function openTaskEffortModal(taskId) {
  ensureModal();
  ctx.taskId = taskId;
  ctx.editingId = null;
  ctx.efforts = [];
  const label = document.getElementById('taskEffortTaskLabel');
  if (label) label.textContent = `#${taskId}`;
  const modal = document.getElementById('taskEffortModal');
  modal.style.display = 'flex';
  await loadEfforts();
};

window.closeTaskEffortModal = function closeTaskEffortModal() {
  const modal = document.getElementById('taskEffortModal');
  if (modal) modal.style.display = 'none';
  ctx.editingId = null;
};

window.reloadTaskEffortModal = function reloadTaskEffortModal() {
  ctx.editingId = null;
  return loadEfforts();
};

window.startTaskEffortEdit = function startTaskEffortEdit(effortId) {
  ctx.editingId = effortId;
  renderRows();
};

window.cancelTaskEffortEdit = function cancelTaskEffortEdit() {
  ctx.editingId = null;
  renderRows();
};

window.saveTaskEffortEdit = async function saveTaskEffortEdit(effortId) {
  const date = (document.getElementById('effortEditDate') || {}).value;
  const consumed = Number((document.getElementById('effortEditConsumed') || {}).value);
  const work = (document.getElementById('effortEditWork') || {}).value;
  if (!date) {
    window.showMessage && window.showMessage('请选择日期', 'error');
    return;
  }
  if (!Number.isFinite(consumed) || consumed <= 0 || consumed > 999) {
    window.showMessage && window.showMessage('工时需在 0~999 小时之间', 'error');
    return;
  }
  try {
    const res = await (await api(`/workbench/tasks/${ctx.taskId}/efforts/${effortId}`, {
      method: 'PUT', body: { date, consumed, work },
    })).json();
    ctx.efforts = res.efforts || [];
    ctx.editingId = null;
    renderRows();
    window.showMessage && window.showMessage('工时记录已更新，已同步禅道', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '修改失败', 'error');
  }
};
