import { requestActivity } from './components/workbench-activity.js';

export async function api(url, options = {}) {
  const token = localStorage.getItem('token');
  const headers = { ...(options.headers || {}) };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let body = options.body;
  const isJsonBodyObject =
    body &&
    typeof body === 'object' &&
    !(body instanceof FormData) &&
    !(body instanceof URLSearchParams) &&
    !(body instanceof Blob);

  // 统一规范：调用方传对象，api 层统一序列化为 JSON。
  if (isJsonBodyObject) {
    if (!headers['Content-Type'] && !headers['content-type']) {
      headers['Content-Type'] = 'application/json';
    }
    body = JSON.stringify(body);
  }

  const activity = requestActivity(url, options);
  let response;
  try { response = await fetch(url, { ...options, headers, body }); }
  finally { activity?.end(); }
  if (response.status === 401) {
    localStorage.removeItem('token');
    window.location.href = '/login';
    throw Object.assign(new Error('401'), { status: 401 });
  }

  if (response.status === 403) {
    throw Object.assign(new Error('无权限执行此操作'), { status: 403 });
  }

  if (!response.ok) {
    let message = `请求失败 (HTTP ${response.status})`;
    try {
      const data = await response.json();
      message = data.detail || data.message || message;
    } catch {
      try {
        const raw = await response.text();
        if (raw && raw.trim()) message = `${message}: ${raw.slice(0, 160)}`;
      } catch {}
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }

  return response;
}

window.OmniQAApi = { api };
