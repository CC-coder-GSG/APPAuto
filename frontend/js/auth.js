import { api } from './api.js';

export function logout() {
  localStorage.removeItem("token");
  window.location.href = "/login";
}

export async function changeMyPassword() {
  const oldP = prompt('?????????');
  if (!oldP) return;
  const newP = prompt('??????????????? 3 ???');
  if (!newP) return;
  try {
    await api('/auth/password', { method: 'PUT', headers: window.H, body: JSON.stringify({ old_password: oldP, new_password: newP }) });
    window.showMessage && window.showMessage('???????3 ????????', 'success');
    setTimeout(logout, 3000);
  } catch (e) {
    window.showMessage && window.showMessage(e.message, 'error');
  }
}

export async function resetUserPassword(id, username) {
  const newP = prompt(`??????${username}??????`);
  if (!newP) return;
  try {
    await api(`/users/${id}/password`, { method: 'PUT', headers: window.H, body: JSON.stringify({ new_password: newP }) });
    window.showMessage && window.showMessage(`???????? ${username} ???????`, 'success');
  } catch (e) {
    window.showMessage && window.showMessage(e.message, 'error');
  }
}

window.OmniQAAuth = { logout, changeMyPassword, resetUserPassword };
