import { api } from '../api.js';

// 自动化测试（Jenkins）页：每个用户用自己的 Jenkins 账号 + API Token 绑定，
// 由系统代为列「自动化测试」视图下的 Job 并一键触发构建。

let jenkinsDefaults = { base_url: '', view: '自动化测试' };
let jenkinsBound = false;

function $(id) {
  return document.getElementById(id);
}

function colorToStatus(color) {
  const c = String(color || '');
  if (c.includes('anime')) return { text: '运行中', bg: '#fef9c3', fg: '#854d0e' };
  if (c.startsWith('blue') || c.startsWith('green')) return { text: '上次成功', bg: '#dcfce7', fg: '#166534' };
  if (c.startsWith('red')) return { text: '上次失败', bg: '#fee2e2', fg: '#b91c1c' };
  if (c.startsWith('yellow')) return { text: '不稳定', bg: '#fef3c7', fg: '#92400e' };
  if (c.startsWith('aborted')) return { text: '已中止', bg: '#f1f5f9', fg: '#475569' };
  if (c.includes('disabled') || c.includes('notbuilt')) return { text: '未构建/禁用', bg: '#f1f5f9', fg: '#94a3b8' };
  return { text: '未知', bg: '#f1f5f9', fg: '#64748b' };
}

export async function loadJenkinsTab() {
  try {
    const data = await (await api('/jenkins/binding/me')).json();
    jenkinsDefaults = data.defaults || jenkinsDefaults;
    renderBinding(data.binding);
    if (data.binding) {
      jenkinsBound = true;
      await loadJenkinsJobs();
    } else {
      jenkinsBound = false;
      const jobsArea = $('jenkinsJobsArea');
      if (jobsArea) jobsArea.innerHTML = '<div class="muted" style="padding:16px;">请先在上方绑定你的 Jenkins 账号，绑定成功后这里会列出可触发的自动化测试 Job。</div>';
    }
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载 Jenkins 配置失败', 'error');
  }
}

function renderBinding(binding) {
  const baseInput = $('jenkinsBaseUrl');
  const acctInput = $('jenkinsAccount');
  const tokenInput = $('jenkinsToken');
  const statusEl = $('jenkinsBindingStatus');
  if (baseInput) baseInput.value = (binding && binding.base_url) || jenkinsDefaults.base_url || '';
  if (acctInput) acctInput.value = (binding && binding.jenkins_account) || '';
  if (tokenInput) tokenInput.value = '';

  if (!statusEl) return;
  if (!binding) {
    statusEl.innerHTML = '<span class="badge" style="background:#f1f5f9; color:#64748b;">未绑定</span>';
    return;
  }
  const ok = binding.last_check_status === 'ok';
  const tone = ok
    ? 'background:#dcfce7; color:#166534; border:1px solid #bbf7d0;'
    : (binding.last_check_status === 'error'
      ? 'background:#fee2e2; color:#b91c1c; border:1px solid #fca5a5;'
      : 'background:#f1f5f9; color:#64748b;');
  const stateText = ok ? '✅ 连接正常' : (binding.last_check_status === 'error' ? '⚠️ 上次校验失败' : '已保存（未校验）');
  const err = binding.last_error_message ? ` <span class="muted" style="font-size:12px;">(${binding.last_error_message})</span>` : '';
  statusEl.innerHTML = `<span class="badge" style="${tone}">已绑定：${binding.jenkins_account}</span> <span style="margin-left:8px;">${stateText}</span>${err}`;
}

function readBindingForm() {
  const base_url = ($('jenkinsBaseUrl')?.value || '').trim();
  const jenkins_account = ($('jenkinsAccount')?.value || '').trim();
  const jenkins_token = ($('jenkinsToken')?.value || '').trim();
  return { base_url, jenkins_account, jenkins_token };
}

export async function testJenkinsBinding() {
  const body = readBindingForm();
  if (!body.base_url || !body.jenkins_account || !body.jenkins_token) {
    window.showMessage && window.showMessage('请填写 Jenkins 地址、账号和 API Token', 'error');
    return;
  }
  try {
    const res = await (await api('/jenkins/binding/me/test', { method: 'POST', headers: window.H, body })).json();
    window.showMessage && window.showMessage(res.message || (res.ok ? '连接成功' : '连接失败'), res.ok ? 'success' : 'error');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '测试连接失败', 'error');
  }
}

export async function saveJenkinsBinding() {
  const body = readBindingForm();
  if (!body.base_url || !body.jenkins_account || !body.jenkins_token) {
    window.showMessage && window.showMessage('请填写 Jenkins 地址、账号和 API Token', 'error');
    return;
  }
  try {
    const res = await (await api('/jenkins/binding/me', { method: 'PUT', headers: window.H, body })).json();
    window.showMessage && window.showMessage(res.verified ? 'Jenkins 绑定已保存并校验成功' : `已保存，但校验未通过：${res.message || ''}`, res.verified ? 'success' : 'error');
    await loadJenkinsTab();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '保存绑定失败', 'error');
  }
}

export async function deleteJenkinsBinding() {
  if (!confirm('确定要删除你的 Jenkins 账号绑定吗？')) return;
  try {
    await api('/jenkins/binding/me', { method: 'DELETE' });
    window.showMessage && window.showMessage('Jenkins 绑定已删除', 'success');
    await loadJenkinsTab();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '删除绑定失败', 'error');
  }
}

export async function loadJenkinsJobs() {
  const jobsArea = $('jenkinsJobsArea');
  if (!jobsArea) return;
  jobsArea.innerHTML = '<div class="muted" style="padding:16px;">正在加载 Job 列表...</div>';
  try {
    const data = await (await api('/jenkins/jobs')).json();
    const jobs = data.jobs || [];
    if (jobs.length === 0) {
      jobsArea.innerHTML = `<div class="muted" style="padding:16px;">视图「${data.view}」下暂无可触发的 Job</div>`;
      return;
    }
    const rows = jobs.map((j) => {
      const st = colorToStatus(j.color);
      const safeName = encodeURIComponent(j.name);
      const buildable = j.buildable !== false;
      const btn = buildable
        ? `<button onclick="triggerJenkinsJob('${safeName}', this)">▶ 触发构建</button>`
        : '<span class="muted" style="font-size:12px;">不可构建</span>';
      const link = j.url ? `<a class="qa-ext-link" href="${j.url}" target="_blank" rel="noopener noreferrer" style="font-size:12px;">在 Jenkins 打开</a>` : '';
      return `<tr data-job="${safeName}">
        <td style="font-weight:bold; color:#0f172a;">${j.name}</td>
        <td><span class="badge" style="background:${st.bg}; color:${st.fg};">${st.text}</span></td>
        <td style="display:flex; gap:10px; align-items:center;">${btn}${link}<span class="jenkins-job-feedback" style="font-size:12px; color:#475569;"></span></td>
      </tr>`;
    }).join('');
    jobsArea.innerHTML = `<table style="width:100%; background:#fff; border-radius:8px; overflow:hidden;">
      <thead><tr><th style="text-align:left;">Job 名称</th><th style="text-align:left; width:120px;">状态</th><th style="text-align:left; width:360px;">操作</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  } catch (err) {
    jobsArea.innerHTML = `<div class="muted" style="padding:16px; color:#b91c1c;">加载 Job 列表失败：${err.message || ''}</div>`;
  }
}

export async function triggerJenkinsJob(safeName, btnEl) {
  const jobName = decodeURIComponent(safeName);
  if (!confirm(`确认触发 Job「${jobName}」的自动化测试构建吗？`)) return;
  const row = btnEl?.closest('tr');
  const feedback = row?.querySelector('.jenkins-job-feedback');
  if (btnEl) btnEl.disabled = true;
  if (feedback) feedback.textContent = '正在触发...';
  try {
    const res = await (await api(`/jenkins/jobs/${safeName}/build`, { method: 'POST', headers: window.H, body: {} })).json();
    window.showMessage && window.showMessage(res.message || '已触发构建', 'success');
    if (feedback) feedback.textContent = '已进入队列，等待执行...';
    if (res.queue_item_url) {
      await pollQueueItem(res.queue_item_url, feedback);
    } else if (feedback) {
      feedback.textContent = '已触发（无法跟踪队列）';
    }
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '触发构建失败', 'error');
    if (feedback) feedback.textContent = `触发失败：${err.message || ''}`;
  } finally {
    if (btnEl) btnEl.disabled = false;
  }
}

async function pollQueueItem(queueUrl, feedback) {
  // 触发是异步的：进队列 → executor 空闲后才分到 build number，轮询几次报告启动。
  for (let i = 0; i < 10; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      const q = await (await api(`/jenkins/queue?url=${encodeURIComponent(queueUrl)}`)).json();
      if (q.cancelled) {
        if (feedback) feedback.textContent = '构建已被取消';
        return;
      }
      if (q.started && q.build_number) {
        if (feedback) {
          const link = q.build_url ? `<a class="qa-ext-link" href="${q.build_url}" target="_blank" rel="noopener noreferrer">#${q.build_number}</a>` : `#${q.build_number}`;
          feedback.innerHTML = `已启动构建 ${link}`;
        }
        return;
      }
      if (feedback && q.why) feedback.textContent = `排队中：${q.why}`;
    } catch {
      // 单次轮询失败不终止
    }
  }
  if (feedback) feedback.textContent = '已触发，稍后请在 Jenkins 查看结果';
}

window.OmniQAJenkinsTab = {
  loadJenkinsTab,
  testJenkinsBinding,
  saveJenkinsBinding,
  deleteJenkinsBinding,
  loadJenkinsJobs,
  triggerJenkinsJob,
};
