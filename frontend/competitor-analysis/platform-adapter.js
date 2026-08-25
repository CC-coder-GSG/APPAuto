/* Survey Master 竞品分析工作台 - OmniQA 平台适配器 */
(function () {
  'use strict';

  const REPORT_URL = '/competitor-analysis/report';

  function redirectToLogin() {
    localStorage.removeItem('token');
    window.location.replace('/login');
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
