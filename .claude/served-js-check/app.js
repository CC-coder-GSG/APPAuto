import './realtime/sse.js'; // 必须最先加载，确保 window.OmniQASSE 在各 tab 模块执行 bindXxxSSE() 前已就绪
import './api.js';
import './auth.js';
import './zentao-binding.js';
import './zentao-hydrator.js';
import './state.js';
import './utils.js';
import './components/common.js';
import './components/modal.js';
import { resizeAllCharts } from './components/charts.js';
import './tabs/mine.js';
import './tabs/feedback.js';
import './tabs/report.js';
import './tabs/retest.js';
import './tabs/stage5.js';
import './tabs/field-test.js';
import './tabs/build-records.js';
import './tabs/zentao-sync.js';
import './tabs/activity.js';
import './tabs/assign.js';
import './tabs/dispatch.js';
import './tabs/data.js';
import { runGuardrails } from './guardrails.js';

window.addEventListener('resize', () => {
  resizeAllCharts();
});

window.addEventListener('load', () => {
  // 页面和内联脚本加载完成后执行一次回归防线自检
  runGuardrails();
});

