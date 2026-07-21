import './realtime/sse.js?v=20260612-1';
// 必须最先加载，确保 window.OmniQASSE 在各 tab 模块执行 bindXxxSSE() 前已就绪
import './api.js?v=20260417-3';
import './auth.js?v=20260417-3';
import './zentao-binding.js?v=20260417-3';
import './zentao-hydrator.js?v=20260630-2';
import './zentao-status-map.js?v=20260422-2';
import './state.js?v=20260417-3';
import './utils.js?v=20260630-1';
import './components/common.js?v=20260630-2';
import './components/modal.js?v=20260417-3';
import './components/ai-result-modal.js?v=20260422-2';
import './components/ai-config-modal.js?v=20260422-3';
import './components/entity-preview-modal.js?v=20260630-2';
import './components/effort-modal.js?v=20260717-1';
import './ai-tasks.js?v=20260423-1';
import { resizeAllCharts } from './components/charts.js?v=20260706-2';
import { runGuardrails } from './guardrails.js?v=20260716-1';

const OPTIONAL_TAB_MODULES = [
  { name: 'mine', path: './tabs/mine.js?v=20260721-2' },
  { name: 'review-workbench', path: './tabs/review-workbench.js?v=20260720-2' },
  { name: 'task-workbench', path: './tabs/task-workbench.js?v=20260717-1' },
  { name: 'task-board', path: './tabs/task-board.js?v=20260717-2' },
  { name: 'feedback', path: './tabs/feedback.js?v=20260417-3' },
  { name: 'report', path: './tabs/report.js?v=20260710-1' },
  { name: 'retest', path: './tabs/retest.js?v=20260721-1' },
  { name: 'overall-test', path: './tabs/overall-test.js?v=20260630-3' },
  { name: 'field-test', path: './tabs/field-test.js?v=20260417-3' },
  { name: 'build-records', path: './tabs/build-records.js?v=20260513-1' },
  { name: 'testcase-center', path: './tabs/testcase-center.js?v=20260714-1' },
  { name: 'activity', path: './tabs/activity.js?v=20260417-3' },
  { name: 'assign', path: './tabs/assign.js?v=20260630-2' },
  { name: 'dispatch', path: './tabs/dispatch.js?v=20260417-3' },
  { name: 'data', path: './tabs/data.js?v=20260714-1' },
  { name: 'zentao-ai', path: './tabs/zentao-ai.js?v=20260422-2' },
  { name: 'jenkins', path: './tabs/jenkins.js?v=20260604-1' },
  { name: 'cad-test', path: './tabs/cad-test.js?v=20260622-1' },
  { name: 'terminal', path: './tabs/terminal.js?v=20260611-1' },
  { name: 'learning', path: './tabs/learning.js?v=20260615-5' },
  { name: 'feature-tree', path: './tabs/feature-tree.js?v=20260716-1' },
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
