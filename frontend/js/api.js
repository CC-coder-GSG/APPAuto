export async function api(url, options = {}) {
  const token = localStorage.getItem("token");
  const headers = { ...(options.headers || {}) };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let body = options.body;
  const isJsonBodyObject =
    body &&
    typeof body === "object" &&
    !(body instanceof FormData) &&
    !(body instanceof URLSearchParams) &&
    !(body instanceof Blob);

  // 单一规范：调用方传对象，api 层统一序列化 JSON。
  if (isJsonBodyObject) {
    if (!headers["Content-Type"] && !headers["content-type"]) {
      headers["Content-Type"] = "application/json";
    }
    body = JSON.stringify(body);
  }

  const response = await fetch(url, { ...options, headers, body });
  if (response.status === 401) {
    localStorage.removeItem("token");
    window.location.href = "/login";
    throw new Error("401");
  }

  if (!response.ok) {
    let message = "操作失败";
    try {
      const data = await response.json();
      message = data.detail || data.message || message;
    } catch {}
    throw new Error(message);
  }

  return response;
}

window.OmniQAApi = { api };
