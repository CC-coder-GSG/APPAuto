import { syncLegacyGlobalsToState } from './state.js';

const REQUIRED_NAMESPACES = {
  OmniQAApi: ['api'],
  OmniQAAuth: ['logout', 'changeMyPassword', 'resetUserPassword'],
  OmniQAAssignTab: ['loadAssignBoard', 'publishAssign'],
  OmniQAMineTab: ['loadMyWorkbench', 'renderMineCards', 'pushCase', 'pushTest'],
  OmniQAFeedbackTab: ['loadFeedbackBoard', 'createFeedback', 'openFeedbackDetail'],
  OmniQARetestTab: ['loadRetest', 'setRetest', 'addRetestBug'],
  OmniQAStage5Tab: ['loadStage5', 'renderS5', 'submitS5Bug'],
  OmniQAFieldTestTab: ['loadFieldTestBoard', 'submitFieldTestRecord', 'editFieldTestRecord', 'openFieldTestDetail', 'addFieldTestDetailBug', 'searchFieldTestBugOptions', 'linkFieldTestDetailBug'],
  OmniQABuildRecordsTab: ['loadBuildRecordsBoard', 'openBuildRecordLogModal', 'openMajorBuildLogModal'],
  OmniQAReportTab: ['queryReport', 'exportReportPdf'],
  OmniQAActivityTab: ['loadActivityBoard', 'pushActivitySummary', 'openAuditTimelineModal'],
  OmniQADataTab: ['loadDataOverview', 'createUser', 'createVersion'],
  OmniQADispatchTab: ['loadDispatchedAll', 'searchDispatchBug', 'confirmDispatchBug'],
};

const REQUIRED_DOM_IDS = [
  'tab-assign',
  'tab-mine',
  'tab-feedback',
  'tab-retest',
  'tab-stage5',
  'tab-field-test',
  'tab-build-records',
  'buildRecordsCardList',
  'fieldTestMajorSelect',
  'fieldTestMinorSelect',
  'fieldTestTable',
  'tab-report',
  'tab-activity',
  'tab-data',
  'tab-dispatch',
  'assignMajorSelect',
  'mineMajorSelect',
  'mineMinorSelect',
  'retestMajorSelect',
  'retestMinorSelect',
  's5MajorSelect',
  's5MinorSelect',
  'reportStartDate',
  'reportEndDate',
  'feedbackMajorSelect',
  'feedbackMinorSelect',
  'feedbackTable',
];

function collectNamespaceIssues() {
  const issues = [];
  for (const [name, methods] of Object.entries(REQUIRED_NAMESPACES)) {
    const ns = window[name];
    if (!ns) {
      issues.push(`缺少命名空间: ${name}`);
      continue;
    }
    for (const method of methods) {
      if (typeof ns[method] !== 'function') {
        issues.push(`${name}.${method} 不存在或不是函数`);
      }
    }
  }
  return issues;
}

function collectDomIssues() {
  const issues = [];
  for (const id of REQUIRED_DOM_IDS) {
    if (!document.getElementById(id)) {
      issues.push(`缺少关键 DOM: #${id}`);
    }
  }
  return issues;
}

function installGlobalErrorProbe() {
  window.addEventListener('unhandledrejection', (event) => {
    console.error('[Guardrails] 未处理 Promise 异常:', event.reason);
  });
}

export function runGuardrails() {
  syncLegacyGlobalsToState();
  const nsIssues = collectNamespaceIssues();
  if (nsIssues.length > 0) {
    console.error('[Guardrails] 模块检查失败:', nsIssues);
  }
  installGlobalErrorProbe();
}

export function runFrontendSelfCheck() {
  syncLegacyGlobalsToState();
  const issues = [...collectNamespaceIssues(), ...collectDomIssues()];
  const ok = issues.length === 0;
  if (!ok) {
    console.error('[Guardrails] 前端自检未通过:', issues);
  } else {
    console.info('[Guardrails] 前端自检通过');
  }
  return { ok, issues };
}

const API_PROBES = [
  { name: '当前用户', url: '/auth/me' },
  { name: '版本列表', url: '/versions' },
  { name: '我的工作台', url: '/requirements/my-workbench?mode=all_pending' },
  { name: '反馈列表', url: '/feedbacks/paged?page=1&page_size=1' },
];

export async function runOnlineApiCheck() {
  const checker = window.OmniQAApi && typeof window.OmniQAApi.api === 'function' ? window.OmniQAApi.api : null;
  if (!checker) {
    return {
      ok: false,
      items: [],
      issues: ['API 模块未挂载，无法执行在线探测'],
    };
  }

  const items = [];
  const issues = [];
  for (const probe of API_PROBES) {
    try {
      const resp = await checker(probe.url, { method: 'GET' });
      items.push({ name: probe.name, url: probe.url, ok: true, status: resp.status });
    } catch (err) {
      const message = err?.message || '未知错误';
      items.push({ name: probe.name, url: probe.url, ok: false, status: null, error: message });
      issues.push(`${probe.name} 探测失败: ${message}`);
    }
  }
  return { ok: issues.length === 0, items, issues };
}

export async function runFullHealthCheck() {
  const frontend = runFrontendSelfCheck();
  const api = await runOnlineApiCheck();
  return {
    ok: frontend.ok && api.ok,
    frontend,
    api,
    checked_at: new Date().toISOString(),
  };
}

window.OmniQAGuardrails = { runGuardrails, runFrontendSelfCheck, runOnlineApiCheck, runFullHealthCheck };
