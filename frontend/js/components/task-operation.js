import { api } from '../api.js';
import { runZentaoOperation } from './zentao-operation.js';

const names = { start:'开始任务', pause:'暂停任务', finish:'完成任务', close:'关闭任务', cancel:'取消任务', reactivate:'重新激活任务', set_time:'修改预计工时', assign:'指派任务' };
export function operateTask(taskId, payload, options = {}) {
  const snapshot = { ...payload };
  return runZentaoOperation({
    ...options, key:`task:${taskId}`, label:`任务 #${taskId} · ${names[snapshot.action] || '更新'}`,
    run: async () => (await api(`/workbench/tasks/${taskId}/operate`, { method:'POST', body:snapshot })).json(),
    verify: async () => {
      if (snapshot.action === 'assign') {
        const meta = await (await api(`/workbench/tasks/${taskId}/assignable`)).json();
        return meta.current === snapshot.assigned_to ? true : null;
      }
      const task = await (await api(`/zentao/task/${taskId}/detail`)).json();
      const expected = { start:'doing', pause:'pause', finish:'done', close:'closed', cancel:'cancel', reactivate:'doing' }[snapshot.action];
      const applied = expected ? task.status === expected
        : snapshot.action === 'set_time' ? Number(task.estimate) === Number(snapshot.hours)
        : snapshot.action === 'assign' ? (task.assigned_to_account || task.assigned_to) === snapshot.assigned_to : false;
      return applied ? { confirmed:true, data:{ok:true,task} } : null;
    },
  });
}
