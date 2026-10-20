import { api } from '../api.js';
import { escapeHtml as esc } from '../utils.js';

const state = { repositories: [], selected: null, run: null };
let pendingRequest = null;
let deepLinkApplied = false;
const labels = {
  created: '已登记', dispatching: '正在提交', queued: '排队中', running: '运行中', completed: '已结束',
  dispatch_unknown: '提交待核实', environment_error: '环境异常', cancelled: '已取消',
  PASS: '通过', FAIL: '失败', SKIP: '跳过', ERROR: '环境异常', pending: '待回写', syncing: '回写中',
  synced: '已回写', error: '回写失败', unknown: '回写待核实', skipped: '未回写（跳过）',
};
const $ = (id) => document.getElementById(id);
const call = async (path, method = 'GET', body) => (await api('/automation' + path, { method, ...(body === undefined ? {} : { body }) })).json();
const notice = (error) => window.showMessage?.(error.message || String(error), 'error');
const action = (fn) => async (event) => { event?.preventDefault(); try { await fn(); } catch (error) { notice(error); } };

function safeLink(url, text) {
  try {
    const parsed = new URL(url);
    if (!['http:', 'https:'].includes(parsed.protocol)) return esc(text);
    return `<a href="${esc(parsed.href)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>`;
  } catch { return esc(text); }
}

function createShell(host) {
  host.innerHTML = `<div id="automationDetails"><h3 style="margin:0 0 12px">用例关联与执行历史</h3>
    <div class="row" style="gap:8px;flex-wrap:wrap;margin:12px 0">
      <select id="automationRepository" aria-label="自动化项目"></select>
      <button id="automationReload" class="secondary">刷新</button>
      <button id="automationConfigure" class="secondary">配置项目</button>
    </div>
    <div id="automationSetup" class="hidden"></div>
    <div id="automationCases"></div>
    <form id="automationRunForm" class="row" style="gap:10px;flex-wrap:wrap;margin:12px 0">
      <label>测试单 <input id="automationTesttask" type="number" min="1" required style="width:110px"></label>
      <label>设备 <input id="automationDevice" required placeholder="设备序列号" style="width:170px"></label>
      <label>APK <input id="automationApk" required placeholder="Jenkins 可访问的 APK 路径" style="min-width:250px"></label>
      <label><input id="automationDryRun" type="checkbox">仅校验脚本（不操作设备、不回写禅道）</label>
      <details><summary>其他构建选项</summary><label>构建参数（JSON）<textarea id="automationParameters" rows="3" placeholder='{"PACKAGE":"…"}'>{}</textarea></label></details>
      <button type="submit" id="automationStart">执行所选用例</button>
    </form>
    <div id="automationHistory"></div><div id="automationRunDetail"></div>
  </div>`;
  $('automationRepository').onchange = action(async () => { state.selected = Number($('automationRepository').value); await renderProject(); });
  $('automationReload').onclick = action(() => loadAutomationPanel(true));
  $('automationConfigure').onclick = () => $('automationSetup').classList.toggle('hidden');
  $('automationRunForm').onsubmit = action(startRun);
  renderSetup();
}

function renderSetup() {
  $('automationSetup').innerHTML = `<form id="automationProjectForm" style="padding:12px;border:1px solid #cbd5e1;border-radius:8px">
    <p>使用 Git 中的脚本关联禅道用例。先绑定 Jenkins 和禅道账号；项目创建后，将集成密钥保存到 Jenkins Credentials。</p>
    <div class="row" style="gap:10px;flex-wrap:wrap">
      ${[['name','项目名称','text'],['git_url','Git 仓库地址','text'],['branch','脚本分支','text'],['jenkins_job','Jenkins Job','text'],['zentao_base_url','禅道地址','url'],['product_id','禅道产品 ID','number'],['execution_id','研发执行 ID（可选）','number'],['default_testtask_id','专用测试单 ID','number'],['target_build_id','禅道构建 ID（可选）','number']].map(([key,label,type]) => `<label>${label}<input name="${key}" type="${type}" ${label.includes('可选') ? '' : 'required'} value="${key === 'branch' ? 'main' : key === 'jenkins_job' ? 'SurveyMaster-RF-Test' : ''}" style="display:block;min-width:170px"></label>`).join('')}
      <button type="submit">保存新项目</button>
    </div><div id="automationKey"></div>
  </form>`;
  $('automationProjectForm').onsubmit = action(async () => {
    const fields = Object.fromEntries(new FormData($('automationProjectForm')));
    for (const key of ['product_id','execution_id','default_testtask_id','target_build_id']) fields[key] = fields[key] ? Number(fields[key]) : null;
    const response = await call('/repositories', 'POST', fields);
    state.selected = response.repository.id;
    $('automationKey').textContent = `项目 ID：${response.repository.id}。集成密钥仅显示一次：${response.integration_key}`;
    await loadAutomationPanel(true);
  });
}

export async function loadAutomationPanel(force = false) {
  const host = $('automationPanel');
  if (!host) return;
  if (!host.childElementCount) createShell(host);
  if (!force && state.repositories.length) return;
  try {
    state.repositories = (await call('/repositories')).items || [];
    const linkedProject = Number(new URLSearchParams(location.search).get('automation_repository'));
    if (!deepLinkApplied && linkedProject) state.selected = linkedProject;
    if (!state.repositories.some(x => x.id === state.selected)) state.selected = state.repositories[0]?.id || null;
    $('automationRepository').innerHTML = state.repositories.length
      ? state.repositories.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('') : '<option value="">尚未配置自动化项目</option>';
    $('automationRepository').value = state.selected || '';
    await renderProject();
    if (!deepLinkApplied) {
      deepLinkApplied = true;
      const runId = new URLSearchParams(location.search).get('automation_run');
      if (linkedProject && runId) {
        await showRun(runId);
      }
    }
  } catch (error) { notice(error); }
}

async function renderProject() {
  const project = state.repositories.find(x => x.id === state.selected);
  $('automationStart').disabled = !project;
  if (!project) {
    $('automationCases').textContent = '配置自动化项目后，可同步脚本关联并执行测试。';
    $('automationHistory').textContent = '';
    return;
  }
  $('automationTesttask').value = project.default_testtask_id || '';
  $('automationTesttask').readOnly = !!project.default_testtask_id;
  $('automationCases').innerHTML = `<p>脚本版本：<code>${esc(project.catalog_revision || '尚未同步')}</code> · 禅道回写：${project.writeback_enabled ? '已启用' : '未启用'}
    <button id="automationWriteback" class="secondary">${project.writeback_enabled ? '停用' : '启用'}回写</button>
    <label style="margin-left:8px">导入脚本目录 <input id="automationCatalogFile" type="file" accept=".json,application/json"></label></p>
    <div style="max-height:280px;overflow:auto"><table class="table"><thead><tr><th>选择</th><th>禅道用例</th><th>自动化测试</th><th>脚本</th></tr></thead><tbody>
      ${project.cases.map(x => `<tr><td><input type="checkbox" name="automationCase" value="${x.case_id || ''}" ${x.case_id ? '' : 'disabled'}></td><td>${x.case_id ? safeLink(`${project.zentao_base_url}/testcase-view-${x.case_id}.html`, '#' + x.case_id) : '未关联'}</td><td>${esc(x.name)}</td><td>${esc(x.path)}</td></tr>`).join('') || '<tr><td colspan="4">尚未同步脚本目录</td></tr>'}
    </tbody></table></div>`;
  $('automationWriteback').onclick = action(async () => { await call(`/repositories/${project.id}/writeback`, 'PUT', { enabled: !project.writeback_enabled }); await loadAutomationPanel(true); });
  $('automationCatalogFile').onchange = action(async () => {
    const file = $('automationCatalogFile').files[0];
    if (!file || file.size > 4 * 1024 * 1024) throw new Error('请选择小于 4MB 的脚本目录 JSON');
    await call(`/repositories/${project.id}/catalog`, 'POST', JSON.parse(await file.text()));
    await loadAutomationPanel(true);
  });
  await loadHistory();
}

async function startRun() {
  const ids = [...document.querySelectorAll('input[name="automationCase"]:checked')].map(x => Number(x.value));
  if (!ids.length) throw new Error('请先选择已关联脚本的用例');
  $('automationStart').disabled = true;
  try {
    const payload = {
      case_ids: ids, testtask_id: Number($('automationTesttask').value), device_serial: $('automationDevice').value.trim(),
      apk_reference: $('automationApk').value.trim(), parameters: JSON.parse($('automationParameters').value || '{}'),
      dry_run: $('automationDryRun').checked,
    };
    const fingerprint = JSON.stringify([state.selected, payload]);
    if (pendingRequest?.fingerprint !== fingerprint) pendingRequest = { fingerprint, key: crypto.randomUUID() };
    const run = await call(`/repositories/${state.selected}/runs`, 'POST', { ...payload, idempotency_key: pendingRequest.key });
    pendingRequest = null;
    await loadHistory();
    await showRun(run.id);
  } finally { $('automationStart').disabled = false; }
}

async function loadHistory() {
  const rows = (await call(`/repositories/${state.selected}/runs`)).items || [];
  $('automationHistory').innerHTML = `<h3>执行历史</h3><table class="table"><thead><tr><th>运行</th><th>状态</th><th>测试结论</th><th>Jenkins</th><th>操作</th></tr></thead><tbody>${rows.map(x => `<tr><td>${esc(x.created_at || '')}<br><small>${esc(x.id)}</small></td><td>${esc(labels[x.status] || x.status)}</td><td>通过 ${x.summary.PASS || 0} / 失败 ${x.summary.FAIL || 0} / 跳过 ${x.summary.SKIP || 0} / 异常 ${x.summary.ERROR || 0}</td><td>${x.build_url ? safeLink(x.build_url, '#' + x.build_number) : '尚未开始'}</td><td><button data-automation-run="${esc(x.id)}" class="secondary">详情</button></td></tr>`).join('') || '<tr><td colspan="5">暂无执行记录</td></tr>'}</tbody></table>`;
  $('automationHistory').querySelectorAll('[data-automation-run]').forEach(button => { button.onclick = action(() => showRun(button.dataset.automationRun)); });
}

async function showRun(id) {
  const run = await call('/runs/' + encodeURIComponent(id));
  state.run = id;
  $('automationRunDetail').innerHTML = `<h3>执行详情 · ${run.dry_run ? '脚本演练 · ' : ''}${esc(labels[run.status] || run.status)}</h3>
    <p>版本：<code>${esc(run.revision)}</code> · 测试单 #${run.testtask_id} · 设备 ${esc(run.device_serial)}</p>
    <p>${esc(run.message || '')}</p><p>${run.build_url ? safeLink(run.build_url + 'artifact/results/report.html', 'Robot 报告') + ' · ' + safeLink(run.build_url + 'artifact/results/log.html', '详细日志与截图') : ''}</p>
    <button id="automationRunRefresh" class="secondary">刷新进度</button> <button id="automationRunSync" class="secondary">重试结果同步</button>
    <table class="table"><thead><tr><th>用例</th><th>结论</th><th>耗时</th><th>原因</th><th>禅道同步</th></tr></thead><tbody>${run.results.map(x => `<tr><td>#${x.case_id} · ${esc(x.test_name)}</td><td>${esc(labels[x.status] || x.status)}</td><td>${esc(x.duration_seconds)} 秒</td><td style="white-space:pre-wrap">${esc(x.message)}</td><td>${esc(labels[x.sync_status] || x.sync_status)}<br>${esc(x.sync_message || '')}</td></tr>`).join('') || '<tr><td colspan="5">等待测试结果</td></tr>'}</tbody></table>`;
  $('automationRunRefresh').onclick = action(async () => { await call(`/runs/${id}/refresh`, 'POST'); await loadHistory(); await showRun(id); });
  $('automationRunSync').disabled = run.dry_run;
  $('automationRunSync').onclick = action(async () => { await call(`/runs/${id}/sync`, 'POST'); window.showMessage?.('已提交同步，请稍后刷新进度'); });
}
