import { api } from '../api.js';
import { state } from '../state.js';
import { escapeHtml } from '../utils.js?v=20261014-2';

const h = value => escapeHtml(String(value ?? ''));
const statusNames = { wait: '未开始', doing: '进行中', changed: '进行中', pause: '已暂停', done: '已完成', closed: '已关闭' };
let sequence = 0;
const context = () => `${window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0}:${document.getElementById('assignMajorSelect')?.value || 0}`;

export async function openAssignLinkedTasks(reqId) {
  const req = (state.assignReqs || []).find(r => Number(r.id) === Number(reqId));
  if (!req) return;
  const captured = context(), request = ++sequence;
  document.getElementById('assignLinkedTasksDialog')?.remove();
  const dialog = document.createElement('dialog');
  dialog.id = 'assignLinkedTasksDialog';
  dialog.className = 'assign-result-modal qa-linked-task-dialog';
  dialog.setAttribute('aria-label', '选择关联任务并修改');
  dialog.innerHTML = `<div class="assign-result-heading"><h3>选择关联任务并修改</h3><button type="button" class="secondary" data-close>关闭</button></div><p>${h(req.zentao_req_id)} ${h(req.title)}</p><div data-content role="status">正在同步关联测试任务…</div>`;
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { if (request === sequence) sequence++; });
  document.body.appendChild(dialog);
  dialog.showModal();
  const valid = () => request === sequence && captured === context() && dialog.open;
  try {
    const data = await (await api(`/requirements/${Number(reqId)}/linked-tasks?refresh=true`)).json();
    if (!valid()) { dialog.close(); return; }
    const tasks = data.tasks || [];
    const content = dialog.querySelector('[data-content]');
    if (!tasks.length) { content.textContent = '暂无可修改的关联测试任务。请确认任务已关联当前需求与当前版本。'; return; }
    const users = window.users || state.users || [];
    content.innerHTML = `${data.refreshed?.ok === false ? `<p role="status">同步失败，当前展示已缓存任务：${h(data.refreshed.error || '请稍后重试')}</p>` : ''}
      <form class="qa-linked-task-form">
        <label>关联测试任务<select data-task>${tasks.map(t => `<option value="${Number(t.task_id)}" ${t.task_id === req.zentao_task_id ? 'selected' : ''}>#${Number(t.task_id)} ${h(t.name)} · ${h(statusNames[t.status] || t.status || '待同步')} · ${h(t.assigned_to_realname || t.assigned_to || '未指派')}</option>`).join('')}</select></label>
        <label>任务指派人<select data-owner><option value="">保留当前指派人</option>${users.filter(u => u.is_active !== false && u.zentao_account).map(u => `<option value="${Number(u.id)}">${h(u.display_name || u.username)}</option>`).join('')}</select></label>
        <div class="row"><label>预计开始<input type="date" data-start></label><label>预计结束<input type="date" data-deadline></label></div>
        <p class="muted">仅修改当前所选任务的指派人和日期。需求负责人请在分配表中设置。</p>
        <div role="status" data-result></div><button type="submit">保存所选任务</button>
      </form>`;
    const form = content.querySelector('form');
    const taskSelect = form.querySelector('[data-task]');
    const ownerSelect = form.querySelector('[data-owner]');
    const start = form.querySelector('[data-start]'), deadline = form.querySelector('[data-deadline]');
    const result = form.querySelector('[data-result]');
    const selected = () => tasks.find(t => t.task_id === Number(taskSelect.value));
    const fill = () => {
      const task = selected();
      const owner = users.find(u => u.zentao_account && u.zentao_account.toLowerCase() === (task.assigned_to || '').toLowerCase());
      ownerSelect.value = owner ? String(owner.id) : '';
      start.value = task.est_started || '';
      deadline.value = task.deadline || '';
      result.textContent = '';
    };
    taskSelect.onchange = fill;
    fill();
    form.onsubmit = async event => {
      event.preventDefault();
      if (!valid()) { dialog.close(); return; }
      if (!!start.value !== !!deadline.value || (start.value && start.value > deadline.value)) {
        result.textContent = '请填写完整、有效的预计起止日期，开始日期不能晚于结束日期'; return;
      }
      const task = selected();
      const body = {};
      const owner = users.find(u => Number(u.id) === Number(ownerSelect.value));
      if (owner && (owner.zentao_account || '').toLowerCase() !== (task.assigned_to || '').toLowerCase()) body.owner_id = owner.id;
      if (start.value) { body.task_start_date = start.value; body.task_deadline = deadline.value; }
      form.querySelectorAll('input,select,button').forEach(el => { el.disabled = true; });
      result.textContent = `正在保存任务 #${task.task_id}…`;
      try {
        const saved = await (await api(`/requirements/${Number(reqId)}/linked-tasks/${task.task_id}`, { method: 'PUT', body })).json();
        if (!valid()) return;
        if (!saved.ok) {
          result.textContent = `${saved.results?.assignment?.ok ? '任务改派已生效；' : ''}${saved.results?.dates?.reason || '部分修改未完成，请刷新后核实'}`;
          return;
        }
        window.showMessage?.(`任务 #${task.task_id} 已更新`, 'success');
        dialog.close();
        await window.OmniQAAssignTab?.loadAssignBoard({ skipSync: true });
      } catch (err) {
        if (valid()) result.textContent = err.message || '保存失败，请重试';
      } finally {
        form.querySelectorAll('input,select,button').forEach(el => { el.disabled = false; });
      }
    };
  } catch (err) {
    if (valid()) dialog.querySelector('[data-content]').textContent = err.message || '关联任务加载失败';
  }
}

window.openAssignLinkedTasks = openAssignLinkedTasks;
