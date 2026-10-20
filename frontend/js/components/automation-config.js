import { escapeHtml as esc } from '../utils.js';

export function mountAutomationConfiguration(host, { request, onCreated, notify }) {
  host.innerHTML = `<form id="automationProjectForm" style="padding:12px;border:1px solid #cbd5e1;border-radius:8px">
    <p>创建可重复使用的自动化配置。项目名称可自定义；禅道和 Jenkins 使用本人已绑定的账号。</p>
    <p id="automationBindingSummary" role="status"></p>
    <div class="row" style="gap:10px;flex-wrap:wrap;align-items:flex-start">
      <label>项目名称<input name="name" required maxlength="160" placeholder="例如：SurveyMaster 自动化" style="display:block"></label>
      <label>Git 仓库地址（HTTPS / SSH）<input name="git_url" type="text" required maxlength="500" placeholder="git@github.com:组织/仓库.git" style="display:block;min-width:300px"></label>
      <label>脚本分支<input name="branch" required value="main" style="display:block"></label>
      <label>Jenkins Job<select id="automationConfigJob" name="jenkins_job" required style="display:block"></select></label>
      <label>禅道产品<select id="automationConfigProduct" name="product_id" required style="display:block"></select></label>
      <label>禅道项目（筛选研发执行，可选）<select id="automationConfigProject" style="display:block"></select></label>
      <label>研发执行（可选）<select id="automationConfigExecution" name="execution_id" style="display:block"></select></label>
      <label>禅道构建（可选）<select id="automationConfigBuild" name="target_build_id" style="display:block"></select></label>
      <label>固定测试单（可选）<select id="automationConfigTesttask" name="default_testtask_id" style="display:block"></select></label>
    </div>
    <p class="muted">支持 git@github.com:组织/仓库.git 和 ssh://git@github.com/组织/仓库.git。脚本由 Jenkins 拉取，SSH 密钥在 Jenkins 执行节点或凭据中配置；Job 使用的仓库应与这里一致。</p>
    <p class="muted">测试单是禅道中的一次测试批次，包含本轮要执行的用例，正式结果归入该测试单。创建配置可暂不选择，执行前必须选择（脚本演练也需要）；启用禅道回写需配置固定测试单。已配置时，本项目的执行固定归入它。没有可用测试单时，请先在禅道创建并关联用例。</p>
    <p class="muted">先选择禅道项目以列出研发执行，再选择执行以列出构建；测试单按产品、执行和构建筛选。只展示当前账号可访问且未完成的测试单。</p>
    <p id="automationConfigErrors" role="status" style="white-space:pre-wrap"></p>
    <button id="automationConfigReload" type="button" class="secondary">刷新远程选项</button>
    <button id="automationConfigSave" type="submit" disabled>保存新项目</button>
  </form><div id="automationKey"></div>`;

  const el = id => host.querySelector('#' + id);
  const form = el('automationProjectForm');
  const fields = Object.fromEntries(['Job', 'Product', 'Project', 'Execution', 'Build', 'Testtask']
    .map(key => [key, el('automationConfig' + key)]));
  const versions = new Map();
  const errors = new Map();
  let bindingsReady = false;
  let pending = 0;
  let initialized = false;
  let refreshVersion = 0;
  let saving = false;
  const update = () => {
    el('automationConfigSave').disabled = !bindingsReady || pending > 0 || saving;
    el('automationConfigErrors').textContent = [...errors.values()].join('\n');
  };
  const reset = (key, placeholder, disabled = true) => {
    versions.set(key, (versions.get(key) || 0) + 1);
    fields[key].innerHTML = `<option value="">${esc(placeholder)}</option>`;
    fields[key].disabled = disabled;
    errors.delete(key);
    update();
  };
  const load = async (key, path, placeholder) => {
    reset(key, '正在获取…');
    const version = versions.get(key);
    pending++; update();
    try {
      const result = await request(path);
      if (versions.get(key) !== version) return;
      const rows = result.items || (result.jobs || []).filter(row => row.buildable !== false)
        .map(row => ({ id: row.fullName || row.name, name: row.fullName || row.name }));
      fields[key].innerHTML = `<option value="">${esc(rows.length ? placeholder : '没有可用选项')}</option>`
        + rows.map(row => `<option value="${esc(row.id)}">${esc(row.name)}${key === 'Job' ? '' : ' · #' + esc(row.id)}</option>`).join('');
      fields[key].disabled = rows.length === 0;
    } catch (error) {
      if (versions.get(key) !== version) return;
      fields[key].innerHTML = '<option value="">获取失败，请刷新重试</option>';
      errors.set(key, `${fields[key].parentElement.firstChild.textContent.trim()}：${error.message || error}`);
    } finally { pending--; update(); }
  };
  const options = (kind, params = {}) => '/automation/configuration/zentao/' + kind + '?' + new URLSearchParams(
    Object.entries(params).filter(([, value]) => value));
  const scope = () => ({ product_id: fields.Product.value, execution_id: fields.Execution.value, build_id: fields.Build.value });
  const testtasks = () => fields.Product.value
    ? load('Testtask', options('testtasks', scope()), '暂不固定，运行时选择')
    : reset('Testtask', '先选择产品');
  const clearExecution = () => {
    reset('Execution', '先选择禅道项目');
    reset('Build', '先选择研发执行');
    reset('Testtask', '先选择产品');
  };
  fields.Product.onchange = async () => { fields.Project.value = ''; clearExecution(); await testtasks(); };
  fields.Project.onchange = async () => {
    clearExecution();
    await Promise.all([
      fields.Project.value ? load('Execution', options('executions', { project_id: fields.Project.value }), '不限研发执行') : null,
      testtasks(),
    ]);
  };
  fields.Execution.onchange = async () => {
    reset('Build', '先选择研发执行');
    reset('Testtask', '先选择产品');
    await Promise.all([
      fields.Product.value && fields.Execution.value
        ? load('Build', options('builds', scope()), '不限定构建') : null,
      testtasks(),
    ]);
  };
  fields.Build.onchange = testtasks;

  async function refresh() {
    const version = ++refreshVersion;
    initialized = true;
    bindingsReady = false;
    reset('Job', '先绑定 Jenkins'); reset('Product', '先绑定禅道'); reset('Project', '先绑定禅道');
    clearExecution();
    errors.clear();
    el('automationBindingSummary').textContent = '正在读取账号绑定…';
    pending++; update();
    try {
      const bindings = await request('/automation/configuration/bindings');
      if (version !== refreshVersion) return;
      bindingsReady = !!(bindings.zentao_base_url && bindings.jenkins_base_url);
      el('automationBindingSummary').textContent = `禅道：${bindings.zentao_base_url || '未绑定，请先在账号设置中绑定'}；Jenkins：${bindings.jenkins_base_url || '未绑定，请先绑定 Jenkins 账号'}`;
      await Promise.all([
        bindings.jenkins_base_url ? load('Job', '/jenkins/jobs', '请选择 Jenkins Job') : null,
        bindings.zentao_base_url ? load('Product', options('products'), '请选择禅道产品') : null,
        bindings.zentao_base_url ? load('Project', options('projects'), '不限研发执行') : null,
      ]);
    } catch (error) {
      if (version === refreshVersion) {
        errors.set('bindings', error.message || String(error));
        el('automationBindingSummary').textContent = '读取账号绑定失败，请刷新重试';
      }
    } finally { pending--; update(); }
  }
  el('automationConfigReload').onclick = refresh;
  form.onsubmit = async event => {
    event.preventDefault();
    if (!bindingsReady || pending || saving || !form.reportValidity()) return;
    if (!fields.Product.value || !fields.Job.value) { notify(new Error('请选择禅道产品和 Jenkins Job')); return; }
    const data = new FormData(form);
    const payload = Object.fromEntries(['name', 'git_url', 'branch', 'jenkins_job'].map(key => [key, data.get(key)]));
    for (const key of ['product_id', 'execution_id', 'target_build_id', 'default_testtask_id']) payload[key] = data.get(key) ? Number(data.get(key)) : null;
    saving = true; update();
    try { await onCreated(await request('/automation/repositories', 'POST', payload)); }
    catch (error) { notify(error); }
    finally { saving = false; update(); }
  };
  return { load: async () => { if (!initialized) await refresh(); } };
}
