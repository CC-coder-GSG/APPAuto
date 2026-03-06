import { api } from './api.js';

export function logout() {
  localStorage.removeItem('token');
  window.location.href = '/login';
}

export async function changeMyPassword() {
  const oldP = prompt('请输入【原密码】：');
  if (!oldP) return;
  const newP = prompt('请输入【新密码】（长度大于等于 3 位）：');
  if (!newP) return;
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
  const newP = prompt(`请输入用户【${username}】的新密码：`);
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
