import { api } from '../api.js';
import { escapeHtml as h } from '../utils.js';

const roundLabel = r => r.status === 'completed' ? '重测完成' : r.status === 'active' ? '待重测' : r.status === 'rejected' ? '派发失败，可重试' : '派发待核实';

export function renderRequirementChange(req) {
  if (!req.final_test) return '';
  const changed = req.task_story_change_pending || [...(req.test_tasks || []), ...(req.story_tasks || [])]
    .some(t => t.type === 'test' && t.task_story_change_pending);
  const rounds = req.change_retests || [];
  if (!changed && !rounds.length) return '';
  const button = `<button type="button" class="secondary" data-handle-change="${Number(req.id)}" style="margin-left:8px;color:#9a3412;border-color:#fdba74">处理变更</button>`;
  const tags = rounds.map(r => `<span style="margin-left:8px;font-size:12px;color:${r.status === 'completed' ? '#15803d' : '#b45309'}">${h(r.owner_name)} · v${Number(r.story_version)} ${roundLabel(r)}${r.previous_test_completed ? '（上一轮已完成）' : ''}</span>`).join('');
  return button + tags;
}

let loading = false;
let submitting = false;

async function showChange(requirementId) {
  if (loading || submitting) return;
  loading = true;
  let dialog = document.getElementById('requirementChangeModal');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.id = 'requirementChangeModal';
    dialog.setAttribute('aria-label', '处理需求变更');
    dialog.style.cssText = 'width:min(960px,94vw);max-height:88vh;overflow:auto;border:1px solid #e2e8f0;border-radius:12px;padding:24px;box-sizing:border-box';
    dialog.addEventListener('cancel', event => { if (submitting) event.preventDefault(); });
    document.body.append(dialog);
  }
  dialog.innerHTML = '<p role="status">正在读取变更任务…</p>';
  if (!dialog.open) dialog.showModal();
  try {
    const data = await (await api(`/requirements/${requirementId}/change-retest`)).json();
    const owners = selected => '<option value="">请选择负责人</option>' + data.owners.map(u => `<option value="${Number(u.id)}" ${Number(u.id) === Number(selected) ? 'selected' : ''}>${h(u.name)}</option>`).join('');
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    dialog.innerHTML = `<h3 style="margin-top:0">处理变更 · ${h(data.title)}</h3>
      <p>当前大版本：${h(data.major_version_name)}。请选择需要重测的任务和负责人。</p>
      <p class="muted">默认重新激活并指派原任务，跨版本也保留原任务编号及所属版本。需要新建时可另选处理方式。需求变更由接手人本人阅读并确认。</p>
      ${!data.can_manage ? '<p role="status">你可以查看变更；派发重测需要需求分配权限。</p>' : ''}
      ${data.can_manage && data.tasks.some(t => t.can_create || t.mode === 'create') ? `<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px"><label>重测预计开始日期 <input type="date" data-change-start value="${today}"></label><label>重测预计结束日期 <input type="date" data-change-deadline></label><span class="muted">仅选择新建重测任务时需填写完整日期。</span></div>` : ''}
      <div style="overflow:auto"><table class="table"><thead><tr><th>选择</th><th>任务 / 需求版本</th><th>所属执行 / 处理方式</th><th>负责人</th><th>结果</th></tr></thead><tbody>
      ${data.tasks.map(t => `<tr data-change-task="${Number(t.task_id)}"><td><input aria-label="选择任务 ${Number(t.task_id)}" type="checkbox" ${!data.can_manage || !t.latest_story_version ? 'disabled' : ''}></td>
        <td><button type="button" class="secondary" data-preview-change="${Number(t.task_id)}">#${Number(t.task_id)} ${h(t.name)}</button><div>v${Number(t.story_version)} → v${Number(t.latest_story_version)}</div></td>
        <td>${h(t.execution_name)}<div>${t.can_create ? `<select data-change-mode aria-label="任务 ${Number(t.task_id)} 的处理方式" ${!data.can_manage ? 'disabled' : ''}><option value="reactivate">重新激活并指派原任务</option><option value="create">在当前大版本新建重测任务</option></select>` : t.mode === 'create' ? '在当前大版本新建重测任务' : '重新激活并指派原任务'}</div>${t.existing_task_id && t.existing_task_id !== t.task_id ? `<div class="muted">此前已新建 #${Number(t.existing_task_id)}；可切回原任务，此前新任务会保留。</div>` : ''}</td>
        <td><select data-change-owner aria-label="任务 ${Number(t.task_id)} 的负责人" ${!data.can_manage ? 'disabled' : ''}>${owners(t.suggested_owner_id)}</select><div class="muted">${h(t.owner_reason)}</div></td><td><span data-change-result>${h(t.dispatch_error || '')}</span>${(t.can_create || t.mode === 'create') && data.can_manage ? `<label data-change-confirm ${t.needs_create_confirmation ? '' : 'hidden'} style="display:${t.needs_create_confirmation ? 'block' : 'none'};margin-top:8px"><input type="checkbox" data-retry-unconfirmed aria-label="任务 ${Number(t.task_id)} 确认未创建，允许重新派发">确认未创建，允许重新派发</label>` : ''}</td></tr>`).join('') || '<tr><td colspan="5">暂无可派发的已关闭变更任务。</td></tr>'}
      </tbody></table></div>
      <div style="margin-top:12px">${data.rounds.map(r => `<p>源任务 #${Number(r.source_task_id)} → ${r.task_id ? `任务 #${Number(r.task_id)}` : '任务待核实'} · ${h(r.owner_name)} · v${Number(r.story_version)} · ${roundLabel(r)}${r.previous_test_completed ? `；上一轮已完成${r.previous_test_completed_at ? `（${h(r.previous_test_completed_at)}）` : ''}` : ''}${r.replaced_task_id ? `????? #${Number(r.replaced_task_id)} ???????????` : ''}${r.dispatch_error ? `<br><span>${h(r.dispatch_error)}</span>` : ''}</p>`).join('')}</div>
      <p id="changeRetestStatus" role="status" aria-live="polite"></p>
      <div style="display:flex;gap:10px;justify-content:flex-end"><button type="button" data-change-close>关闭</button>${data.can_manage && data.tasks.length ? '<button type="button" data-change-submit>派发选中的重测任务</button>' : ''}</div>`;
    dialog.querySelector('[data-change-close]').onclick = () => dialog.close();
    dialog.querySelectorAll('[data-preview-change]').forEach(button => {
      button.onclick = () => {
        dialog.close();
        window.OmniQAPreview?.openTaskStoryChange(Number(button.dataset.previewChange));
      };
    });
    const submit = dialog.querySelector('[data-change-submit]');
    const selectedMode = row => row.querySelector('[data-change-mode]')?.value || data.tasks.find(t => t.task_id === Number(row.dataset.changeTask)).mode;
    if (submit) submit.onclick = async () => {
      if (submitting) return;
      const selected = [...dialog.querySelectorAll('[data-change-task]')].filter(row => row.querySelector('input').checked && !row.querySelector('input').disabled);
      const status = dialog.querySelector('#changeRetestStatus');
      if (!selected.length) { status.textContent = '请先选择需要重测的任务。'; return; }
      if (selected.some(row => !row.querySelector('[data-change-owner]').value)) { status.textContent = '请为每个选中的任务选择负责人。'; return; }
      const needsDates = selected.some(row => selectedMode(row) === 'create');
      const start = dialog.querySelector('[data-change-start]')?.value;
      const deadline = dialog.querySelector('[data-change-deadline]')?.value;
      if (needsDates && (!start || !deadline || start > deadline)) {
        status.textContent = '请填写完整的重测预计开始、结束日期，结束日期不能早于开始日期。'; return;
      }
      if (selected.some(row => selectedMode(row) === 'create' && data.tasks.find(t => t.task_id === Number(row.dataset.changeTask)).needs_create_confirmation && !row.querySelector('[data-retry-unconfirmed]')?.checked)) {
        status.textContent = '上次创建结果不明确，请先核对禅道没有对应任务，再勾选“确认未创建，允许重新派发”。'; return;
      }
      submitting = true;
      dialog.querySelectorAll('button,input,select').forEach(el => { el.dataset.changeWasDisabled = String(el.disabled); el.disabled = true; });
      let succeeded = 0;
      try {
        for (const row of selected) {
          const task = data.tasks.find(t => t.task_id === Number(row.dataset.changeTask));
          const mode = selectedMode(row);
          const result = row.querySelector('[data-change-result]');
          result.textContent = '正在同步禅道…';
          try {
            const response = await (await api(`/requirements/${requirementId}/change-retest`, { method: 'POST', body: {
              source_task_id: task.task_id, owner_id: Number(row.querySelector('[data-change-owner]').value), expected_story_version: Number(task.latest_story_version), mode,
              ...(mode === 'create' ? { task_start_date: start, task_deadline: deadline } : {}),
              ...(row.querySelector('[data-retry-unconfirmed]')?.checked ? { retry_unconfirmed: true } : {}),
            }})).json();
            result.textContent = `已派发 #${response.task_id}`;
            row.dataset.dispatched = '1';
            succeeded++;
          } catch (error) {
            result.textContent = error.message;
            if (mode === 'create' && /创建结果待核实/.test(error.message)) {
              task.needs_create_confirmation = true;
              const label = row.querySelector('[data-change-confirm]');
              if (label) { label.hidden = false; label.style.display = 'block'; }
              const checkbox = row.querySelector('[data-retry-unconfirmed]');
              if (checkbox) checkbox.checked = false;
            }
          }
        }
        status.textContent = `已成功派发 ${succeeded} / ${selected.length} 个任务。失败项可核对后重试。`;
        document.dispatchEvent(new CustomEvent('requirement-change-dispatched'));
      } finally {
        submitting = false;
        dialog.querySelectorAll('button,input,select').forEach(el => { el.disabled = el.dataset.changeWasDisabled === 'true'; });
        dialog.querySelectorAll('[data-dispatched="1"]').forEach(row => row.querySelectorAll('input,select').forEach(el => { el.disabled = true; }));
      }
    };
  } catch (error) {
    dialog.innerHTML = `<p role="alert">${h(error.message)}</p><button type="button">关闭</button>`;
    dialog.querySelector('button').onclick = () => dialog.close();
  } finally { loading = false; }
}

document.addEventListener('click', event => {
  const button = event.target.closest('[data-handle-change]');
  if (!button) return;
  event.preventDefault();
  event.stopPropagation();
  showChange(Number(button.dataset.handleChange));
});
