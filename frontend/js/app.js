import './realtime/sse.js?v=20260417-2'; // 必须最先加载，确保 window.OmniQASSE 在各 tab 模块执行 bindXxxSSE() 前已就绪
import './api.js?v=20260417-2';
import './auth.js?v=20260417-2';
import './zentao-binding.js?v=20260417-2';
import './zentao-hydrator.js?v=20260417-2';
import './state.js?v=20260417-2';
import './utils.js?v=20260417-2';
import './components/common.js?v=20260417-2';
import './components/modal.js?v=20260417-2';
import { resizeAllCharts } from './components/charts.js?v=20260417-2';
import './tabs/mine.js?v=20260417-2';
import './tabs/feedback.js?v=20260417-2';
import './tabs/report.js?v=20260417-2';
import './tabs/retest.js?v=20260417-2';
import './tabs/stage5.js?v=20260417-2';
import './tabs/field-test.js?v=20260417-2';
import './tabs/build-records.js?v=20260417-2';
import './tabs/zentao-sync.js?v=20260417-2';
import './tabs/activity.js?v=20260417-2';
import './tabs/assign.js?v=20260417-2';
import './tabs/dispatch.js?v=20260417-2';
import './tabs/data.js?v=20260417-2';
import { runGuardrails } from './guardrails.js?v=20260417-2';

window.addEventListener('resize', () => {
  resizeAllCharts();
});

window.addEventListener('load', () => {
  // 页面和内联脚本加载完成后执行一次回归防线自检
  runGuardrails();
});
