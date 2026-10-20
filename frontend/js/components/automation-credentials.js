export function mountAutomationCredentials(host, { request, selectedRepository, notify }) {
  host.innerHTML = `<div style="padding:12px;border:1px solid #cbd5e1;border-radius:8px;margin-bottom:12px">
    <h3 style="margin:0 0 8px">Jenkins 接入凭据</h3>
    <p>同一平台账号、同一个 Jenkins 地址共用一份密钥。只需在 Jenkins 保存一次，后续新增或切换项目会复用；平台发起执行时自动传入项目 ID。</p>
    <p id="automationCredentialStatus" role="status">正在获取…</p>
    <p>Jenkins Secret Text 凭据 ID 使用 <code>omniqa-surveymaster</code>，让相关 Job 可以访问它。手动发布脚本目录时，用 <code>OMNIQA_REPOSITORY_ID</code> 指定目标项目。</p>
    <button id="automationCredentialRefresh" type="button" class="secondary">刷新状态</button>
    <button id="automationCredentialAdopt" type="button" class="secondary" hidden>复用当前项目已有的密钥</button>
    <button id="automationCredentialCreate" type="button" class="secondary" hidden>生成共享密钥</button>
    <button id="automationCredentialRotate" type="button" class="secondary" hidden>更换共享密钥</button>
    <p id="automationCredentialSecret" style="white-space:pre-wrap;overflow-wrap:anywhere" role="status"></p>
  </div>`;
  const el = id => host.querySelector('#' + id);
  const path = '/automation/configuration/integration-key';
  let busy = false;
  let status;
  const buttons = ['Refresh', 'Adopt', 'Create', 'Rotate'].map(name => el('automationCredential' + name));
  const disable = value => buttons.forEach(button => { button.disabled = value; });
  function render() {
    const legacy = status.legacy_repository_ids.includes(selectedRepository());
    el('automationCredentialAdopt').hidden = status.configured || !legacy;
    el('automationCredentialCreate').hidden = status.configured;
    el('automationCredentialRotate').hidden = !status.configured;
    el('automationCredentialStatus').textContent = `Jenkins：${status.jenkins_base_url}。${status.configured
      ? '已配置共享凭据，本人此连接下的项目自动复用。'
      : legacy ? '当前项目已有密钥，可直接将它设为共享凭据，Jenkins 中已保存的值无需修改。' : '尚未配置，可生成一次并保存到 Jenkins。'}`;
  }
  async function refresh() {
    if (busy) return;
    busy = true; disable(true);
    try { status = await request(path); render(); }
    catch (error) { el('automationCredentialStatus').textContent = error.message || String(error); }
    finally { busy = false; disable(false); }
  }
  async function save(payload) {
    if (busy) return;
    busy = true; disable(true);
    try {
      status = await request(path, 'POST', payload);
      render();
      el('automationCredentialSecret').textContent = status.integration_key
        ? `共享密钥仅显示一次，请保存到 Jenkins Secret Text 凭据 omniqa-surveymaster：\n${status.integration_key}`
        : '已复用当前项目原有密钥。Jenkins 中的密钥值无需修改，其他项目也可使用同一份凭据。';
    } catch (error) { notify(error); }
    finally { busy = false; disable(false); }
  }
  el('automationCredentialRefresh').onclick = refresh;
  el('automationCredentialCreate').onclick = () => save({});
  el('automationCredentialAdopt').onclick = () => save({ reuse_repository_id: selectedRepository() });
  el('automationCredentialRotate').onclick = () => {
    if (window.confirm('更换后旧共享密钥会失效，需要在 Jenkins 更新一次凭据。确定更换？')) return save({ rotate: true });
  };
  return { refresh };
}
