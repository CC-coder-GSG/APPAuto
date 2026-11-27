import './realtime/sse.js?v=20261127-rpt-1';
// 必须最先加载，确保 window.OmniQASSE 在各 tab 模块执行 bindXxxSSE() 前已就绪
import './api.js?v=20260417-3';
import './auth.js?v=20261126-zai-1';
import './zentao-binding.js?v=20261126-zai-1';
import './zentao-hydrator.js?v=20261127-rpt-1';
import './zentao-status-map.js?v=20260422-2';
import './state.js?v=20261127-rpt-1';
import './utils.js?v=20261126-hc-1';
import './components/common.js?v=20260630-2';
import './components/modal.js?v=20260417-3';
import './components/dialog.js?v=20261126-zai-1';
import './components/hover-card.js?v=20261126-hc-1';
import './components/ai-result-modal.js?v=20261126-zai-1';
import './components/ai-config-modal.js?v=20260422-3';
import './components/entity-preview-modal.js?v=20261126-hc-1';
import './components/effort-modal.js?v=20261126-zai-1';
import './ai-tasks.js?v=20261126-zai-1';
import { resizeAllCharts } from './components/charts.js?v=20261127-rpt-1';
import { runGuardrails } from './guardrails.js?v=20261127-rpt-1';

const OPTIONAL_TAB_MODULES = [
  { name: 'mine', path: './tabs/mine.js?v=20261126-hc-1' },
  { name: 'review-workbench', path: './tabs/review-workbench.js?v=20261126-hc-1' },
  { name: 'task-workbench', path: './tabs/task-workbench.js?v=20261126-hc-1' },
  { name: 'task-board', path: './tabs/task-board.js?v=20261127-tbd-1' },
  { name: 'feedback', path: './tabs/feedback.js?v=20261126-zai-1' },
  { name: 'report', path: './tabs/report.js?v=20261127-rpt-1' },
  { name: 'retest', path: './tabs/retest.js?v=20261126-hc-1' },
  { name: 'overall-test', path: './tabs/overall-test.js?v=20261126-zai-1' },
  { name: 'field-test', path: './tabs/field-test.js?v=20261126-zai-1' },
  { name: 'build-records', path: './tabs/build-records.js?v=20261126-zai-1' },
  { name: 'testcase-center', path: './tabs/testcase-center.js?v=20261126-zai-1' },
  { name: 'activity', path: './tabs/activity.js?v=20261127-rpt-1' },
  { name: 'assign', path: './tabs/assign.js?v=20261126-hc-1' },
  { name: 'mine-assigned-bugs', path: './tabs/mine-assigned-bugs.js?v=20261127-rpt-1' },
  { name: 'data', path: './tabs/data.js?v=20261127-rpt-1' },
  { name: 'zentao-ai', path: './tabs/zentao-ai.js?v=20261126-zai-1' },
  { name: 'jenkins', path: './tabs/jenkins.js?v=20261126-zai-1' },
  { name: 'cad-test', path: './tabs/cad-test.js?v=20261127-rpt-1' },
  { name: 'tools', path: './tabs/tools.js?v=20261126-zai-1' },
  { name: 'terminal', path: './tabs/terminal.js?v=20261126-zai-1' },
  { name: 'learning', path: './tabs/learning.js?v=20261126-zai-1' },
  { name: 'feature-tree', path: './tabs/feature-tree.js?v=20261127-rpt-1' },
  { name: 'bugly', path: './tabs/bugly.js?v=20261126-bgl-1' },
  { name: 'competitor-analysis', path: './tabs/competitor-analysis.js?v=20261126-bgl-1' },
];

window.__omniqaOptionalModuleFailures = {};

async function loadOptionalTabModules() {
  for (const mod of OPTIONAL_TAB_MODULES) {
    try {
      await import(mod.path);
    } catch (err) {
      window.__omniqaOptionalModuleFailures[mod.name] = err;
      console.error(`[OmniQA] tab module load failed: ${mod.name}`, err);
    }
  }
}

window.OmniQABootReady = loadOptionalTabModules();

window.addEventListener('resize', () => {
  resizeAllCharts();
});

window.addEventListener('load', () => {
  // 页面和内联脚本加载完成后执行一次回归防线自检
  Promise.resolve(window.OmniQABootReady).finally(() => {
    runGuardrails();
  });
});
