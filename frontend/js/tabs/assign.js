import { api } from '../api.js';
import { state } from '../state.js';

function getUsers() {
  return window.users || state.users || [];
}

export async function loadAssignBoard() {
  const majorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  if (!majorId) {
    window.showMessage && window.showMessage('请选择大版本', 'error');
    return;
  }
  if (getUsers().length === 0 && typeof window.loadUsers === 'function') {
    await window.loadUsers();
  }
  state.assignReqs = await (await api('/requirements?major_version_id=' + majorId)).json();
  window.assignReqs = state.assignReqs;
  const assignTable = document.getElementById('assignTable');
  if (!assignTable) return;
  const users = getUsers();
  assignTable.innerHTML = state.assignReqs.map((r) => `
    <tr>
      <td>${r.zentao_req_id} ${r.title}</td>
      <td>
        <select id='o_${r.id}'>
          <option value=''>未分配</option>
          ${users.map((u) => `<option value='${u.id}' ${u.id === r.owner_id ? 'selected' : ''}>${u.username}</option>`).join('')}
        </select>
      </td>
    </tr>`).join('');
}

export async function publishAssign() {
  if (!(window.confirmPush && window.confirmPush())) return;
  const majorId = Number(document.getElementById('assignMajorSelect')?.value || 0);
  const assignments = state.assignReqs.map((r) => ({
    requirement_id: r.id,
    owner_id: Number(document.getElementById('o_' + r.id)?.value || 0) || null,
  }));
  await api('/requirements/assign-and-publish', {
    method: 'POST',
    headers: window.H,
    body: ({ major_version_id: majorId, assignments }),
  });
  window.showMessage && window.showMessage('分配发布成功');
}

window.OmniQAAssignTab = { loadAssignBoard, publishAssign };

