import './api.js';
import './auth.js';
import './state.js';
import './utils.js';
import './components/common.js';
import { resizeAllCharts } from './components/charts.js';
import './tabs/mine.js';
import './tabs/feedback.js';
import './tabs/report.js';
import './tabs/retest.js';
import './tabs/stage5.js';
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
