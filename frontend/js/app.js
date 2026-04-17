import './realtime/sse.js?v=20260417-3'; // 蹇呴』鏈€鍏堝姞杞斤紝纭繚 window.OmniQASSE 鍦ㄥ悇 tab 妯″潡鎵ц bindXxxSSE() 鍓嶅凡灏辩华
import './api.js?v=20260417-3';
import './auth.js?v=20260417-3';
import './zentao-binding.js?v=20260417-3';
import './zentao-hydrator.js?v=20260417-3';
import './state.js?v=20260417-3';
import './utils.js?v=20260417-3';
import './components/common.js?v=20260417-3';
import './components/modal.js?v=20260417-3';
import { resizeAllCharts } from './components/charts.js?v=20260417-3';
import { runGuardrails } from './guardrails.js?v=20260417-3';

const OPTIONAL_TAB_MODULES = [
  { name: 'mine', path: './tabs/mine.js?v=20260417-3' },
  { name: 'feedback', path: './tabs/feedback.js?v=20260417-3' },
  { name: 'report', path: './tabs/report.js?v=20260417-3' },
  { name: 'retest', path: './tabs/retest.js?v=20260417-3' },
  { name: 'stage5', path: './tabs/stage5.js?v=20260417-4' },
  { name: 'field-test', path: './tabs/field-test.js?v=20260417-3' },
  { name: 'build-records', path: './tabs/build-records.js?v=20260417-3' },
  { name: 'zentao-sync', path: './tabs/zentao-sync.js?v=20260417-3' },
  { name: 'activity', path: './tabs/activity.js?v=20260417-3' },
  { name: 'assign', path: './tabs/assign.js?v=20260417-3' },
  { name: 'dispatch', path: './tabs/dispatch.js?v=20260417-3' },
  { name: 'data', path: './tabs/data.js?v=20260417-3' },
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
  // 椤甸潰鍜屽唴鑱旇剼鏈姞杞藉畬鎴愬悗鎵ц涓€娆″洖褰掗槻绾胯嚜妫€
  Promise.resolve(window.OmniQABootReady).finally(() => {
    runGuardrails();
  });
});

