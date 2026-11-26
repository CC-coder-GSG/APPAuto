/* Survey Master 竞品分析工作台 - OmniQA 平台适配器 */
(function () {
  'use strict';

  const REPORT_URL = '/competitor-analysis/report';

  // 嵌在主应用页签里时（iframe），登录失效 / 无权限要整页跳转，而不是只跳 iframe 里面
  function topLocation() {
    try {
      if (window.top && window.top !== window && window.top.location.origin === window.location.origin) return window.top.location;
    } catch (_) { /* 跨源时退回自身 */ }
    return window.location;
  }

  function redirectToLogin() {
    localStorage.removeItem('token');
    topLocation().replace('/login');
  }

  async function request(options = {}) {
    const token = localStorage.getItem('token');
    if (!token) {
      redirectToLogin();
      const error = new Error('未登录');
      error.status = 401;
      throw error;
    }
    const response = await fetch(REPORT_URL, {
      ...options,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.headers || {}),
      },
    });
    if (response.status === 401) redirectToLogin();
    if (response.status === 403) topLocation().replace('/dashboard');
    if (!response.ok) {
      let message = `请求失败 (HTTP ${response.status})`;
      try {
        const payload = await response.json();
        message = payload.detail || payload.message || message;
      } catch (_) {}
      const error = new Error(message);
      error.status = response.status;
      if (response.status === 409) error.code = 'CONFLICT';
      throw error;
    }
    return response.json();
  }

  window.CompetitorPlatformAdapter = {
    isConfigured() {
      return true;
    },

    async getCurrentUser() {
      const token = localStorage.getItem('token');
      if (!token) {
        redirectToLogin();
        return null;
      }
      const response = await fetch('/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.status === 401) {
        redirectToLogin();
        return null;
      }
      if (!response.ok) throw new Error('无法获取当前登录用户');
      const user = await response.json();
      return {
        id: user.id,
        name: user.display_name || user.username,
        username: user.username,
      };
    },

    async loadReport() {
      return request();
    },

    async saveReport({ data, baseVersion }) {
      return request({
        method: 'PUT',
        body: JSON.stringify({ data, baseVersion }),
      });
    },
  };
})();
