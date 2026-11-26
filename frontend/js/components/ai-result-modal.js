import { api } from '../api.js';
import { mapZentaoStatus } from '../zentao-status-map.js';
import { openCaseEditor } from './ai-case-editor.js?v=20261126-zai-1';

// AI 用例结果弹窗 v2（2026-11-26，weave.css R20 .aiv-*；外壳复用 R16 .atl-*）
// 结构：头部（需求标题 + 状态）→ 概览条（用例 / 风险 / 待确认 + 归属字段）→ 概要 → 用例卡片（可折叠，
// 步骤表）→ 风险点 / 待确认问题 → 底栏（重新生成 · 复制全部 · 整理并提交禅道）。
// 对外接口不变：window.OmniQAStoryAI.openForStory / closeModal；#aiResultRegenerate 由 ai-tasks.js 统一管理。

const MODAL_ID = 'aiResultModal';
let latestRequest = 0;

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const icon = (id) => `<svg class="ui-ic" aria-hidden="true"><use href="#${id}"/></svg>`;
const textOf = (x) => (typeof x === 'string' ? x : JSON.stringify(x));

function ensureModalRoot() {
  let root = document.getElementById(MODAL_ID);
  if (root) return root;
  root = document.createElement('div');
  root.id = MODAL_ID;
  root.className = 'hidden atl-root aiv-root';
  root.style.display = 'none';
  root.innerHTML = `
    <div class="atl-card aiv-card" role="dialog" aria-modal="true" aria-labelledby="aiResultModalTitle">
      <header class="atl-head">
        <span class="atl-icon" aria-hidden="true"><svg><use href="#i-sparkles"/></svg></span>
        <div class="atl-head-text">
          <h3 id="aiResultModalTitle"></h3>
          <div id="aiResultModalMeta" class="atl-sub aiv-meta"></div>
        </div>
        <button type="button" class="atl-close" data-aiv-close aria-label="关闭" title="关闭（Esc）"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
      </header>
      <div id="aiResultModalBody" class="aiv-body"></div>
      <footer class="atl-foot aiv-foot">
        <button type="button" id="aiResultRegenerate" class="secondary aiv-regen" data-ai-generate-story-id="" data-ai-execution-id="" data-ai-regenerate disabled>重新生成</button>
        <div id="aiResultActions" class="aiv-actions"></div>
      </footer>
    </div>
  `;
  document.body.appendChild(root);
  root.addEventListener('mousedown', (e) => { root._downOnBackdrop = e.target === root; });
  root.addEventListener('click', (e) => {
    if ((e.target === root && root._downOnBackdrop) || e.target.closest('[data-aiv-close]')) closeModal();
  });
  return root;
}

// Esc 关闭；整理弹窗打开时（它在上层）不处理，通用对话框在捕获阶段已先拦截
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const root = document.getElementById(MODAL_ID);
  if (!root || root.classList.contains('hidden')) return;
  const editor = document.getElementById('aiCaseEditor');
  if (editor && editor.style.display !== 'none') return;
  e.preventDefault();
  closeModal();
});

function renderList(arr, emptyText) {
  if (!Array.isArray(arr) || !arr.length) return `<p class="aiv-none">${emptyText}</p>`;
  return `<ul class="aiv-list">${arr.map((x) => `<li>${escapeHtml(textOf(x))}</li>`).join('')}</ul>`;
}

function renderParagraphs(text) {
  if (!text) return '<p class="aiv-none">无</p>';
  return escapeHtml(text).split(/\n\s*\n/).map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
}

function renderSteps(steps) {
  if (!Array.isArray(steps) || !steps.length) return '<p class="aiv-none">无步骤</p>';
  return `
    <div class="aiv-steps" role="table">
      <div class="aiv-steps-head" role="row"><span role="columnheader">#</span><span role="columnheader">操作步骤</span><span role="columnheader">预期结果</span></div>
      ${steps.map((s, i) => `
        <div class="aiv-step" role="row">
          <span class="aiv-step-no" role="cell">${i + 1}</span>
          <div class="aiv-step-do" role="cell">${escapeHtml(typeof s === 'string' ? s : (s?.step ?? ''))}</div>
          <div class="aiv-step-exp" role="cell">${escapeHtml(typeof s === 'string' ? '' : (s?.expected ?? '')) || '<span class="aiv-none">—</span>'}</div>
        </div>`).join('')}
    </div>`;
}

function getTestCases(data) {
  if (Array.isArray(data?.test_cases)) return data.test_cases.filter((item) => item && typeof item === 'object');
  const rawCases = data?.raw_ai_result?.test_cases;
  return Array.isArray(rawCases) ? rawCases.filter((item) => item && typeof item === 'object') : [];
}

function buildCaseTemplate(testCase, index = 0) {
  if (testCase?.testcase_template) return String(testCase.testcase_template);
  const steps = Array.isArray(testCase?.steps) ? testCase.steps : [];
  const stepLines = steps.map((item, stepIndex) => {
    const step = typeof item === 'string' ? item : (item?.step ?? '');
    const expected = typeof item === 'string' ? '' : (item?.expected ?? '');
    return `${stepIndex + 1}. ${step}${expected ? `\n   预期：${expected}` : ''}`;
  }).join('\n');
  return [
    `用例标题：${testCase?.title || `测试用例 ${index + 1}`}`,
    testCase?.case_type ? `用例类型：${testCase.case_type}` : '',
    testCase?.priority ? `优先级：${testCase.priority}` : '',
    testCase?.precondition ? `前置条件：${testCase.precondition}` : '',
    stepLines ? `测试步骤：\n${stepLines}` : '',
    testCase?.keywords ? `关键词：${testCase.keywords}` : '',
  ].filter(Boolean).join('\n');
}

function caseChips(testCase) {
  const chips = [];
  if (testCase.case_type) chips.push(`<span class="aiv-chip">${escapeHtml(testCase.case_type)}</span>`);
  if (testCase.priority) chips.push(`<span class="aiv-chip aiv-chip--pri">P${escapeHtml(String(testCase.priority).replace(/^p/i, ''))}</span>`);
  const steps = Array.isArray(testCase.steps) ? testCase.steps.length : 0;
  chips.push(`<span class="aiv-chip aiv-chip--muted">${steps} 步</span>`);
  return chips.join('');
}

function renderTestCases(testCases) {
  if (!testCases.length) return '';
  return `
    <section class="aiv-section">
      <div class="aiv-section-head">
        <h4>${icon('i-cases')}测试用例 <span class="aiv-count">${testCases.length}</span></h4>
        <button type="button" class="aiv-text-btn" data-aiv-toggle-all>全部展开</button>
      </div>
      <div class="aiv-cases">
        ${testCases.map((testCase, index) => `
          <details class="aiv-case" ${index === 0 ? 'open' : ''}>
            <summary>
              <span class="aiv-case-no">${index + 1}</span>
              <span class="aiv-case-title">${escapeHtml(testCase.title || `测试用例 ${index + 1}`)}</span>
              <span class="aiv-case-chips">${caseChips(testCase)}</span>
              <svg class="ui-ic aiv-case-caret" aria-hidden="true"><use href="#i-chevron-down"/></svg>
            </summary>
            <div class="aiv-case-body">
              ${testCase.precondition ? `<div class="aiv-pre"><span class="aiv-k">前置条件</span><div>${renderParagraphs(testCase.precondition)}</div></div>` : ''}
              ${renderSteps(testCase.steps)}
              <div class="aiv-case-foot">
                ${testCase.keywords ? `<span class="aiv-kw"><span class="aiv-k">关键词</span>${escapeHtml(testCase.keywords)}</span>` : '<span></span>'}
                <button type="button" class="aiv-text-btn" data-ai-copy-case="${index}">${icon('i-copy')}复制本条</button>
              </div>
            </div>
          </details>`).join('')}
      </div>
    </section>`;
}

function copyToClipboard(text) {
  if (!text) return;
  try {
    navigator.clipboard.writeText(text).then(() => {
      window.showMessage && window.showMessage('已复制到剪贴板', 'success');
    }).catch(() => fallbackCopy(text));
  } catch {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); window.showMessage && window.showMessage('已复制到剪贴板', 'success'); }
  finally { document.body.removeChild(ta); }
}

function buildStoryTemplate(data) {
  const testCases = getTestCases(data);
  const steps = Array.isArray(data?.steps) ? data.steps : [];
  const stepLines = steps.map((s, i) => {
    const step = typeof s === 'string' ? s : (s?.step ?? '');
    const expected = typeof s === 'string' ? '' : (s?.expected ?? '');
    return `  ${i + 1}. ${step}${expected ? `\n     预期：${expected}` : ''}`;
  }).join('\n');
  const risks = Array.isArray(data?.risk_points) ? data.risk_points : [];
  const questions = Array.isArray(data?.questions_to_confirm) ? data.questions_to_confirm : [];
  return [
    `【需求】s#${data?.story_id ?? ''} ${data?.title ?? ''}`,
    data?.briefing ? `\n【简述】\n${data.briefing}` : '',
    data?.module_name ? `\n【模块】${data.module_name}` : '',
    data?.scene_name ? `【场景】${data.scene_name}` : '',
    data?.stage_name ? `【阶段】${data.stage_name}` : '',
    data?.case_type ? `【用例类型】${data.case_type}` : '',
    data?.priority ? `【优先级】${data.priority}` : '',
    data?.precondition ? `\n【前置条件】\n${data.precondition}` : '',
    steps.length ? `\n【步骤】\n${stepLines}` : '',
    risks.length ? `\n【风险点】\n${risks.map((x) => `  - ${textOf(x)}`).join('\n')}` : '',
    questions.length ? `\n【待确认问题】\n${questions.map((x) => `  - ${textOf(x)}`).join('\n')}` : '',
    testCases.length ? `\n【测试用例（${testCases.length} 条）】\n\n${testCases.map((item, index) => buildCaseTemplate(item, index)).join('\n\n--------------------\n\n')}` : '',
    data?.testcase_template ? `\n【用例模板】\n${data.testcase_template}` : '',
  ].filter(Boolean).join('\n');
}

const STATUS_TONE = { success: 'ok', failed: 'err', pending: 'run' };
// AI 处理状态不是禅道状态，mapZentaoStatus 不认识（会原样显示 success / failed），这里单独翻译
const STATUS_TEXT = { success: '生成成功', failed: '生成失败', pending: '生成中' };

function renderHeader(data, testCases) {
  const root = ensureModalRoot();
  root.querySelector('#aiResultModalTitle').textContent = data.title || `需求 #${data.story_id}`;
  const tone = STATUS_TONE[data.ai_status] || 'muted';
  root.querySelector('#aiResultModalMeta').innerHTML = [
    `<span class="aiv-sid">#${escapeHtml(data.story_id)}</span>`,
    `<span class="aiv-status aiv-status--${tone}">${escapeHtml(STATUS_TEXT[data.ai_status] || mapZentaoStatus(data.ai_status))}</span>`,
    testCases.length ? `<span>${testCases.length} 条用例</span>` : '',
    data.updated_at ? `<span title="批次 ${escapeHtml(data.batch_id || '')}">更新于 ${escapeHtml(String(data.updated_at).replace('T', ' ').slice(0, 16))}</span>` : '',
  ].filter(Boolean).join('');
}

function render(data) {
  const root = ensureModalRoot();
  const body = root.querySelector('#aiResultModalBody');
  const actions = root.querySelector('#aiResultActions');

  const regenerateButton = root.querySelector('#aiResultRegenerate');
  regenerateButton.dataset.aiGenerateStoryId = String(data.story_id);
  regenerateButton.dataset.aiExecutionId = String(data.execution_id || '');
  regenerateButton.dataset.aiStoryTitle = data.title || '';
  regenerateButton.disabled = !data.execution_id;
  regenerateButton.title = data.execution_id ? '重新生成该需求的 AI 测试用例' : '该结果缺少禅道执行信息，无法重新生成';
  window.OmniQAStoryAI?.refreshGenerationButtons?.(root);

  const testCases = getTestCases(data);
  renderHeader(data, testCases);

  if (data.ai_status !== 'success') {
    body.innerHTML = `
      <div class="aiv-fail" role="alert">
        <span class="aiv-fail-icon">${icon('i-alert')}</span>
        <div>
          <div class="aiv-fail-title">AI 处理未成功</div>
          <div class="aiv-fail-msg">${escapeHtml(data.ai_error_message || '无错误信息')}</div>
          <p class="aiv-fail-tip">可以点击左下角「重新生成」再试一次；如果反复失败，请检查需求描述是否完整。</p>
        </div>
      </div>`;
    actions.innerHTML = '';
    openModal();
    return;
  }

  const metaFields = [
    ['模块', data.module_name], ['场景', data.scene_name], ['阶段', data.stage_name],
    ['用例类型', data.case_type], ['优先级', data.priority], ['关键词', data.keywords],
  ].filter((r) => r[1]);
  const risks = Array.isArray(data.risk_points) ? data.risk_points : [];
  const questions = Array.isArray(data.questions_to_confirm) ? data.questions_to_confirm : [];
  const template = buildStoryTemplate(data);

  body.innerHTML = `
    <div class="aiv-overview">
      <div class="aiv-stats">
        <div class="aiv-stat"><b>${testCases.length || (Array.isArray(data.steps) && data.steps.length ? 1 : 0)}</b><span>测试用例</span></div>
        <div class="aiv-stat aiv-stat--warn"><b>${risks.length}</b><span>风险点</span></div>
        <div class="aiv-stat aiv-stat--info"><b>${questions.length}</b><span>待确认问题</span></div>
      </div>
      ${metaFields.length ? `<dl class="aiv-fields">${metaFields.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd title="${escapeHtml(v)}">${escapeHtml(v)}</dd></div>`).join('')}</dl>` : ''}
    </div>

    ${data.briefing ? `<section class="aiv-section">
      <div class="aiv-section-head"><h4>${icon('i-file')}概要说明</h4></div>
      <div class="aiv-prose">${renderParagraphs(data.briefing)}</div>
    </section>` : ''}

    ${!testCases.length ? `<section class="aiv-section">
      <div class="aiv-section-head"><h4>${icon('i-cases')}步骤 / 预期</h4></div>
      ${data.precondition ? `<div class="aiv-pre"><span class="aiv-k">前置条件</span><div>${renderParagraphs(data.precondition)}</div></div>` : ''}
      ${renderSteps(data.steps)}
    </section>` : ''}

    <div class="aiv-notes">
      <section class="aiv-note aiv-note--warn">
        <h4>${icon('i-alert')}风险点</h4>
        ${renderList(risks, '未识别到风险点')}
      </section>
      <section class="aiv-note aiv-note--info">
        <h4>${icon('i-info')}待确认问题</h4>
        ${renderList(questions, '没有需要确认的问题')}
      </section>
    </div>

    ${renderTestCases(testCases)}

    ${!testCases.length && data.testcase_template ? `<section class="aiv-section">
      <div class="aiv-section-head"><h4>${icon('i-file')}用例模板</h4><button type="button" class="aiv-text-btn" data-ai-copy="template">${icon('i-copy')}复制</button></div>
      <pre class="aiv-template">${escapeHtml(data.testcase_template)}</pre>
    </section>` : ''}
  `;

  actions.innerHTML = `
    <button type="button" class="secondary" data-ai-copy="zentao">${icon('i-copy')}${testCases.length ? '复制全部用例' : '复制禅道模板'}</button>
    <button type="button" data-ai-organize>${icon('i-send')}整理并提交禅道</button>`;

  actions.querySelector('[data-ai-copy="zentao"]').addEventListener('click', () => copyToClipboard(template));
  actions.querySelector('[data-ai-organize]').addEventListener('click', () => openCaseEditor(data.id));
  body.querySelector('[data-ai-copy="template"]')?.addEventListener('click', () => copyToClipboard(data.testcase_template || ''));
  body.querySelectorAll('[data-ai-copy-case]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const index = Number(btn.getAttribute('data-ai-copy-case'));
      if (Number.isInteger(index) && testCases[index]) copyToClipboard(buildCaseTemplate(testCases[index], index));
    });
  });
  const toggleAll = body.querySelector('[data-aiv-toggle-all]');
  if (toggleAll) {
    const cards = () => [...body.querySelectorAll('.aiv-case')];
    const sync = () => { toggleAll.textContent = cards().every((d) => d.open) ? '全部收起' : '全部展开'; };
    toggleAll.addEventListener('click', () => {
      const open = !cards().every((d) => d.open);
      cards().forEach((d) => { d.open = open; });
      sync();
    });
    body.querySelectorAll('.aiv-case').forEach((d) => d.addEventListener('toggle', sync));
    sync();
  }

  openModal();
}

function openModal() {
  const root = ensureModalRoot();
  root.classList.remove('hidden');
  root.style.display = 'flex';
}

function closeModal() {
  latestRequest++;
  const root = document.getElementById(MODAL_ID);
  if (!root) return;
  root.classList.add('hidden');
  root.style.display = 'none';
}

function renderPlaceholder(html) {
  const root = ensureModalRoot();
  root.querySelector('#aiResultModalBody').innerHTML = html;
  root.querySelector('#aiResultActions').innerHTML = '';
}

async function openForStory(storyId) {
  const id = Number(storyId);
  if (!id) return;
  const root = ensureModalRoot();
  const request = ++latestRequest;
  root.dataset.storyId = String(id);
  const regenerateButton = root.querySelector('#aiResultRegenerate');
  regenerateButton.dataset.aiGenerateStoryId = String(id);
  regenerateButton.dataset.aiExecutionId = '';
  regenerateButton.disabled = true;
  regenerateButton.textContent = '重新生成';
  root.querySelector('#aiResultModalTitle').textContent = `需求 #${id}`;
  root.querySelector('#aiResultModalMeta').innerHTML = '<span class="aiv-status aiv-status--run">加载中</span>';
  renderPlaceholder(`
    <div class="aiv-skeleton" aria-busy="true" aria-label="加载中">
      <i class="aiv-sk aiv-sk--bar"></i><i class="aiv-sk"></i><i class="aiv-sk"></i><i class="aiv-sk aiv-sk--short"></i>
    </div>`);
  openModal();
  try {
    const res = await api(`/zentao/ai/story/${id}/latest`);
    const data = await res.json();
    if (request !== latestRequest) return;
    if (!data || !data.id) {
      root.querySelector('#aiResultModalMeta').innerHTML = `<span class="aiv-sid">#${id}</span>`;
      renderPlaceholder(`<div class="aiv-empty">${icon('i-sparkles')}<p>该需求还没有 AI 处理结果</p></div>`);
      return;
    }
    render(data);
  } catch (err) {
    if (request !== latestRequest) return;
    root.querySelector('#aiResultModalMeta').innerHTML = `<span class="aiv-sid">#${id}</span>`;
    renderPlaceholder(`<div class="aiv-fail" role="alert"><span class="aiv-fail-icon">${icon('i-alert')}</span><div><div class="aiv-fail-title">加载失败</div><div class="aiv-fail-msg">${escapeHtml(err.message || '加载失败')}</div></div></div>`);
  }
}

window.OmniQAStoryAI = Object.assign(window.OmniQAStoryAI || {}, {
  openForStory,
  closeModal,
});
