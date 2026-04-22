import { api } from '../api.js';
import { mapZentaoStatus } from '../zentao-status-map.js';

const MODAL_ID = 'aiResultModal';

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function ensureModalRoot() {
  let root = document.getElementById(MODAL_ID);
  if (root) return root;
  root = document.createElement('div');
  root.id = MODAL_ID;
  root.className = 'hidden';
  root.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,0.55); z-index:2000; display:none; padding:32px 16px; overflow:auto;';
  root.innerHTML = `
    <div style="max-width:980px; margin:0 auto; background:#fff; border-radius:10px; box-shadow:0 12px 40px rgba(0,0,0,0.3); padding:24px;">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px;">
        <div style="flex:1;">
          <div id="aiResultModalTitle" style="font-size:18px; font-weight:700; color:#0f172a; margin-bottom:4px;"></div>
          <div id="aiResultModalMeta" style="font-size:12px; color:#64748b;"></div>
        </div>
        <button type="button" onclick="window.OmniQAStoryAI && window.OmniQAStoryAI.closeModal()" style="border:1px solid #cbd5e1; background:#f8fafc; border-radius:6px; padding:4px 12px; cursor:pointer;">关闭</button>
      </div>
      <div id="aiResultModalBody" style="margin-top:16px;"></div>
    </div>
  `;
  document.body.appendChild(root);
  root.addEventListener('click', (e) => {
    if (e.target === root) closeModal();
  });
  return root;
}

function renderList(arr) {
  if (!Array.isArray(arr) || !arr.length) return '<span class="muted">无</span>';
  return `<ul style="margin:4px 0 0 18px; padding:0;">${arr.map((x) => `<li style="margin:2px 0;">${escapeHtml(typeof x === 'string' ? x : JSON.stringify(x))}</li>`).join('')}</ul>`;
}

function renderParagraphs(text) {
  if (!text) return '<span class="muted">无</span>';
  const safe = escapeHtml(text);
  return safe.split(/\n\s*\n/).map((p) => `<p style="margin:0 0 8px; line-height:1.7; color:#1f2937;">${p.replace(/\n/g, '<br>')}</p>`).join('');
}

function renderSteps(steps) {
  if (!Array.isArray(steps) || !steps.length) return '<span class="muted">无</span>';
  return `
    <table class="qa-table" style="margin:6px 0 0; width:100%; border-collapse:collapse;">
      <thead><tr><th style="width:56px;">序号</th><th>步骤</th><th>预期</th></tr></thead>
      <tbody>
        ${steps.map((s, i) => `
          <tr>
            <td>${i + 1}</td>
            <td style="white-space:pre-wrap;">${escapeHtml(typeof s === 'string' ? s : (s?.step ?? ''))}</td>
            <td style="white-space:pre-wrap;">${escapeHtml(typeof s === 'string' ? '' : (s?.expected ?? ''))}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
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
    risks.length ? `\n【风险点】\n${risks.map((x) => `  - ${typeof x === 'string' ? x : JSON.stringify(x)}`).join('\n')}` : '',
    questions.length ? `\n【待确认问题】\n${questions.map((x) => `  - ${typeof x === 'string' ? x : JSON.stringify(x)}`).join('\n')}` : '',
    data?.testcase_template ? `\n【用例模板】\n${data.testcase_template}` : '',
  ].filter(Boolean).join('\n');
}

function render(data) {
  const root = ensureModalRoot();
  const title = root.querySelector('#aiResultModalTitle');
  const meta = root.querySelector('#aiResultModalMeta');
  const body = root.querySelector('#aiResultModalBody');

  const statusLabel = mapZentaoStatus(data.ai_status);
  const statusColor = data.ai_status === 'success' ? '#166534' : data.ai_status === 'failed' ? '#b91c1c' : '#475569';
  title.innerHTML = `s#${data.story_id} ${escapeHtml(data.title || '')}`;
  meta.innerHTML = `状态：<span style="color:${statusColor}; font-weight:600;">${escapeHtml(statusLabel)}</span> · 批次 ${escapeHtml(data.batch_id)} · 更新 ${escapeHtml(data.updated_at || '')}`;

  if (data.ai_status !== 'success') {
    body.innerHTML = `
      <div style="padding:16px; border:1px solid #fecaca; border-radius:8px; background:#fef2f2; color:#991b1b;">
        <div style="font-weight:600; margin-bottom:6px;">AI 处理未成功</div>
        <div style="white-space:pre-wrap;">${escapeHtml(data.ai_error_message || '无错误信息')}</div>
      </div>`;
    openModal();
    return;
  }

  const metaFields = [
    ['模块', data.module_name], ['场景', data.scene_name], ['阶段', data.stage_name],
    ['用例类型', data.case_type], ['优先级', data.priority], ['关键词', data.keywords],
  ].filter((r) => r[1]);

  const template = buildStoryTemplate(data);

  body.innerHTML = `
    ${metaFields.length ? `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:6px 12px; padding:10px 12px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:14px;">
      ${metaFields.map(([k, v]) => `<div><span style="color:#64748b;">${escapeHtml(k)}：</span><span style="color:#0f172a;">${escapeHtml(v)}</span></div>`).join('')}
    </div>` : ''}

    <div style="margin-bottom:14px;">
      <div style="font-weight:600; color:#0f172a; margin-bottom:4px;">概要说明</div>
      ${renderParagraphs(data.briefing)}
    </div>

    ${data.precondition ? `<div style="margin-bottom:14px;">
      <div style="font-weight:600; color:#0f172a; margin-bottom:4px;">前置条件</div>
      ${renderParagraphs(data.precondition)}
    </div>` : ''}

    <div style="margin-bottom:14px;">
      <div style="font-weight:600; color:#0f172a; margin-bottom:4px;">步骤 / 预期</div>
      ${renderSteps(data.steps)}
    </div>

    <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:14px;">
      <div>
        <div style="font-weight:600; color:#0f172a; margin-bottom:4px;">风险点</div>
        ${renderList(data.risk_points)}
      </div>
      <div>
        <div style="font-weight:600; color:#0f172a; margin-bottom:4px;">待确认问题</div>
        ${renderList(data.questions_to_confirm)}
      </div>
    </div>

    ${data.testcase_template ? `<div style="margin-bottom:14px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
        <div style="font-weight:600; color:#0f172a;">用例模板</div>
        <button type="button" data-ai-copy="template" style="border:1px solid #cbd5e1; background:#fff; border-radius:6px; padding:2px 10px; cursor:pointer; font-size:12px;">复制</button>
      </div>
      <textarea readonly style="width:100%; min-height:180px; font-family:Consolas,Menlo,monospace; padding:10px; border:1px solid #cbd5e1; border-radius:6px; box-sizing:border-box;">${escapeHtml(data.testcase_template)}</textarea>
    </div>` : ''}

    <div style="display:flex; gap:8px; justify-content:flex-end; border-top:1px solid #e2e8f0; padding-top:12px;">
      <button type="button" data-ai-copy="zentao" style="border:1px solid #2563eb; background:#2563eb; color:#fff; border-radius:6px; padding:6px 14px; cursor:pointer;">复制禅道模板</button>
    </div>
  `;

  body.querySelectorAll('[data-ai-copy]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const kind = btn.getAttribute('data-ai-copy');
      if (kind === 'template') copyToClipboard(data.testcase_template || '');
      else if (kind === 'zentao') copyToClipboard(template);
    });
  });

  openModal();
}

function openModal() {
  const root = ensureModalRoot();
  root.classList.remove('hidden');
  root.style.display = 'block';
}

function closeModal() {
  const root = document.getElementById(MODAL_ID);
  if (!root) return;
  root.classList.add('hidden');
  root.style.display = 'none';
}

async function openForStory(storyId) {
  const id = Number(storyId);
  if (!id) return;
  const root = ensureModalRoot();
  const body = root.querySelector('#aiResultModalBody');
  const title = root.querySelector('#aiResultModalTitle');
  const meta = root.querySelector('#aiResultModalMeta');
  title.textContent = `s#${id}`;
  meta.textContent = '';
  body.innerHTML = '<div class="muted" style="padding:16px; text-align:center;">加载中...</div>';
  openModal();
  try {
    const res = await api(`/zentao/ai/story/${id}/latest`);
    const data = await res.json();
    if (!data || !data.id) {
      body.innerHTML = '<div class="muted" style="padding:24px; text-align:center;">暂无 AI 处理结果</div>';
      return;
    }
    render(data);
  } catch (err) {
    body.innerHTML = `<div style="padding:16px; color:#b91c1c;">${escapeHtml(err.message || '加载失败')}</div>`;
  }
}

window.OmniQAStoryAI = Object.assign(window.OmniQAStoryAI || {}, {
  openForStory,
  closeModal,
});
