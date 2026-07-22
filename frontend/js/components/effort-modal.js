import { api } from '../api.js';
import { escapeHtml } from '../utils.js';

// 工时记录弹窗（任务看板 / 任务工作台共用，2026-07-17 工时口径升级配套）。
// 展示某禅道任务已提交的工时记录（暂停/完成时平台按天自动结算 + 手工记录），
// 本人的记录可修改或删除（后端用本人网页凭据提交并回读禅道，保持工时归属）。
// 跨 tab 复用 → 弹窗直接挂 body（项目惯例），不依赖任何 tab 的 DOM。

const ctx = { taskId: null, efforts: [], canEditAny: false, editingId: null, deletingId: null };

function ensureModal() {
  if (document.getElementById('taskEffortModal')) return;
  const wrap = document.createElement('div');
  wrap.id = 'taskEffortModal';
  wrap.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,.46); backdrop-filter:blur(2px); z-index:9999; display:none; align-items:center; justify-content:center; padding:18px; box-sizing:border-box;';
  wrap.innerHTML = `
    <style>
      #taskEffortModal .effort-dialog { width:min(820px, 96vw); max-height:86vh; display:flex; flex-direction:column; background:#fff; border:1px solid #dbeafe; border-radius:14px; box-shadow:0 24px 64px rgba(15,23,42,.24); padding:20px; }
      #taskEffortModal .effort-table { width:100%; min-width:720px; border-collapse:separate; border-spacing:0; font-size:13px; }
      #taskEffortModal .effort-table thead th { position:sticky; top:0; z-index:1; background:#f8fafc; color:#475569; font-size:12px; text-align:left; padding:10px 12px; border-bottom:1px solid #e2e8f0; }
      #taskEffortModal .effort-row td { padding:10px 12px; border-bottom:1px solid #f1f5f9; vertical-align:middle; }
      #taskEffortModal .effort-row:last-child td { border-bottom:0; }
      #taskEffortModal .effort-row:hover td { background:#f8fbff; }
      #taskEffortModal .effort-actions { display:flex; align-items:center; justify-content:flex-end; gap:7px; }
      #taskEffortModal .effort-action-btn { padding:4px 10px; min-height:28px; border-radius:6px; font-size:12px; font-weight:600; white-space:nowrap; box-shadow:none; }
      #taskEffortModal .effort-delete-btn { color:#b91c1c; background:#fff7f7; border:1px solid #fecaca; }
      #taskEffortModal .effort-delete-btn:hover:not(:disabled) { color:#991b1b; background:#fee2e2; border-color:#fca5a5; transform:none; box-shadow:none; }
      #taskEffortModal .effort-delete-btn:disabled { color:#94a3b8; background:#f8fafc; border-color:#e2e8f0; cursor:wait; opacity:1; }
    </style>
    <div class="effort-dialog">
      <div style="display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:4px;">
        <div>
          <h3 style="margin:0; color:#0f172a;">🕒 工时记录 <span id="taskEffortTaskLabel" style="color:#1d4ed8;"></span></h3>
          <div id="taskEffortHint" class="muted" style="font-size:12px; margin-top:5px;"></div>
        </div>
        <span style="font-size:11px; color:#64748b; background:#f1f5f9; border:1px solid #e2e8f0; border-radius:999px; padding:4px 9px; white-space:nowrap;">数据来自禅道</span>
      </div>
      <div id="taskEffortBody" style="flex:1; min-height:150px; overflow:auto; border:1px solid #e2e8f0; border-radius:10px; margin-top:12px; background:#fff;"></div>
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
            <div class="effort-actions">
              <button class="effort-action-btn" style="background:#0d9488;" onclick="window.saveTaskEffortEdit(${e.id})">保存</button>
              <button class="secondary effort-action-btn" onclick="window.cancelTaskEffortEdit()">取消</button>
            </div>
          </td>
        </tr>`;
    }
    const canMaintain = !!e.can_edit && ctx.canEditAny;
    const isDeleting = ctx.deletingId === e.id;
    const actionButtons = canMaintain
      ? `<div class="effort-actions">
          <button class="secondary effort-action-btn" ${isDeleting ? 'disabled' : ''} onclick="window.startTaskEffortEdit(${e.id})" title="修改这条工时记录">✏️ 修改</button>
          <button class="effort-action-btn effort-delete-btn" ${isDeleting ? 'disabled' : ''} onclick="window.deleteTaskEffort(${e.id})" title="从禅道删除这条工时记录">${isDeleting ? '正在同步…' : '🗑️ 删除'}</button>
        </div>`
      : '<span class="muted" style="font-size:12px; display:block; text-align:center;">仅本人可操作</span>';
    return `
      <tr class="effort-row" style="${isDeleting ? 'opacity:.7;' : ''}">
        <td style="padding:6px 10px; white-space:nowrap;">${escapeHtml(e.date)}</td>
        <td style="padding:6px 10px; font-weight:600; color:#0f172a;">${e.consumed}h</td>
        <td style="padding:6px 10px;" class="muted">${escapeHtml(e.account || '')}</td>
        <td style="padding:6px 10px; color:#475569; word-break:break-all;">${escapeHtml(e.work || '')}</td>
        <td style="padding:6px 10px; min-width:156px; white-space:nowrap;">${actionButtons}</td>
      </tr>`;
  }).join('');
  body.innerHTML = `
    <table class="effort-table">
      <thead>
        <tr style="background:#f8fafc; color:#475569; font-size:12px; text-align:left;">
          <th style="padding:6px 10px;">日期</th>
          <th style="padding:6px 10px;">消耗</th>
          <th style="padding:6px 10px;">记录人</th>
          <th style="padding:6px 10px;">工作内容</th>
          <th style="padding:6px 10px; text-align:center;">操作</th>
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
        ? '暂停/完成任务时平台按天自动结算并提交禅道；本人的记录可修改或删除。删除以禅道回读结果为准。'
        : '未绑定禅道网页登录凭据，仅可查看（修改或删除工时必须以本人身份同步禅道）。';
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
  ctx.deletingId = null;
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
  ctx.deletingId = null;
};

window.reloadTaskEffortModal = function reloadTaskEffortModal() {
  ctx.editingId = null;
  ctx.deletingId = null;
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

window.deleteTaskEffort = async function deleteTaskEffort(effortId) {
  const effort = ctx.efforts.find((e) => Number(e.id) === Number(effortId));
  if (!effort || !effort.can_edit || !ctx.canEditAny || ctx.deletingId != null) return;
  const work = String(effort.work || '').trim();
  const detail = `${effort.date || '未知日期'} · ${effort.consumed}h${work ? ` · ${work}` : ''}`;
  if (!window.confirm(`确认删除这条工时记录？\n\n${detail}\n\n删除会同步到禅道，成功后不可撤销。`)) return;

  ctx.editingId = null;
  ctx.deletingId = effortId;
  renderRows();
  try {
    const res = await (await api(`/workbench/tasks/${ctx.taskId}/efforts/${effortId}`, {
      method: 'DELETE',
    })).json();
    ctx.efforts = res.efforts || [];
    ctx.canEditAny = !!res.can_edit_any;
    ctx.deletingId = null;
    renderRows();
    window.showMessage && window.showMessage('工时记录已删除，禅道已确认同步', 'success');
  } catch (err) {
    ctx.deletingId = null;
    renderRows();
    window.showMessage && window.showMessage(err.message || '删除失败：禅道未确认删除', 'error');
  }
};
