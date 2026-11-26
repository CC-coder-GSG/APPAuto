import { api } from './api.js';
import { formDialog, promptDialog } from './components/dialog.js?v=20261126-zai-1';

export function logout() {
  window.OmniQASSE?.stop();
  localStorage.removeItem('token');
  sessionStorage.removeItem('sse_last_event_id');
  window.location.href = '/login';
}

export async function changeMyPassword() {
  const values = await formDialog({
    icon: 'i-key',
    title: '修改密码',
    message: '修改成功后需要用新密码重新登录。',
    okText: '修改密码',
    fields: [
      { name: 'oldP', label: '原密码', type: 'password' },
      { name: 'newP', label: '新密码', type: 'password', hint: '至少 3 位', validate: (v) => (v.length < 3 ? '新密码至少 3 位' : '') },
      { name: 'again', label: '确认新密码', type: 'password', validate: (v, all) => (v !== all.newP ? '两次输入的新密码不一致' : '') },
    ],
  });
  if (!values) return;
  const { oldP, newP } = values;
  try {
    await api('/auth/password', {
      method: 'PUT',
      headers: window.H,
      body: { old_password: oldP, new_password: newP },
    });
    if (window.showMessage) window.showMessage('密码修改成功，3 秒后跳转到登录页', 'success');
    setTimeout(logout, 3000);
  } catch (e) {
    if (window.showMessage) window.showMessage(e.message || '密码修改失败', 'error');
  }
}

export async function resetUserPassword(id, username) {
  const newP = await promptDialog({
    icon: 'i-key',
    title: '重置密码',
    sub: `用户 ${username}`,
    message: '重置后该用户会被强制下线，需要用新密码重新登录。',
    label: '新密码',
    type: 'password',
    okText: '重置密码',
  });
  if (!newP) return;
  try {
    await api(`/users/${id}/password`, {
      method: 'PUT',
      headers: window.H,
      body: { new_password: newP },
    });
    if (window.showMessage) window.showMessage(`密码已重置，用户 ${username} 已被强制下线。`, 'success');
  } catch (e) {
    if (window.showMessage) window.showMessage(e.message || '重置密码失败', 'error');
  }
}

window.OmniQAAuth = { logout, changeMyPassword, resetUserPassword };
