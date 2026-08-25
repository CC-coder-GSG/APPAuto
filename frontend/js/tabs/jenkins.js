import { api } from '../api.js';

// Jenkins 标签：尽量复刻原生 Jenkins 的体验。
// - 视图切换 + Job 列表（状态色、最近构建）
// - Job 详情：健康度、构建历史、产物下载（后端流式代理）
// - 参数化构建：内嵌原生 Jenkins 构建页（后端反向代理，支持 Active Choices 级联参数）
// - 账号绑定收进顶部「⚙ 账号设置」折叠面板

let jenkinsDefaults = { base_url: '', view: '自动化测试' };
let jenkinsBound = false;
let jenkinsJobsCache = [];
let jenkinsCurrentView = '';

function $(id) { return document.getElementById(id); }
function authToken() { return localStorage.getItem('token') || ''; }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// 把 Job 全名转成代理路径片段：a/b → job/a/job/b（逐段编码，兼容中文）
function jobProxyPath(name) {
  return String(name || '').split('/').filter(Boolean)
    .map((seg) => 'job/' + encodeURIComponent(seg)).join('/');
}

function colorToStatus(color) {
  const c = String(color || '');
  if (c.includes('anime')) return { text: '运行中', bg: '#fef9c3', fg: '#854d0e' };
  if (c.startsWith('blue') || c.startsWith('green')) return { text: '成功', bg: '#dcfce7', fg: '#166534' };
  if (c.startsWith('red')) return { text: '失败', bg: '#fee2e2', fg: '#b91c1c' };
  if (c.startsWith('yellow')) return { text: '不稳定', bg: '#fef3c7', fg: '#92400e' };
  if (c.startsWith('aborted')) return { text: '已中止', bg: '#f1f5f9', fg: '#475569' };
  if (c.includes('disabled') || c.includes('notbuilt')) return { text: '未构建/禁用', bg: '#f1f5f9', fg: '#94a3b8' };
  return { text: '未知', bg: '#f1f5f9', fg: '#64748b' };
}

function resultBadge(result, building) {
  if (building) return '<span class="badge" style="background:#fef9c3; color:#854d0e;">● 运行中</span>';
  const map = {
    SUCCESS: ['成功', '#dcfce7', '#166534'],
    FAILURE: ['失败', '#fee2e2', '#b91c1c'],
    UNSTABLE: ['不稳定', '#fef3c7', '#92400e'],
    ABORTED: ['已中止', '#f1f5f9', '#475569'],
  };
  const m = map[String(result || '').toUpperCase()] || [result || '—', '#f1f5f9', '#64748b'];
  return `<span class="badge" style="background:${m[1]}; color:${m[2]};">${esc(m[0])}</span>`;
}

function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(Number(ts));
  if (isNaN(d.getTime())) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtDuration(ms) {
  ms = Number(ms || 0);
  if (!ms) return '—';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m${s % 60}s`;
}

// ---------------------------------------------------------------------------
// Tab lifecycle + binding
// ---------------------------------------------------------------------------

export async function loadJenkinsTab() {
  try {
    const data = await (await api('/jenkins/binding/me')).json();
    jenkinsDefaults = data.defaults || jenkinsDefaults;
    renderBinding(data.binding);
    jenkinsBound = !!data.binding;
    if (jenkinsBound) {
      if ($('jenkinsSettingsPanel')) $('jenkinsSettingsPanel').classList.add('hidden');
      await loadJenkinsViews();
      await loadJenkinsJobs();
    } else {
      if ($('jenkinsSettingsPanel')) $('jenkinsSettingsPanel').classList.remove('hidden');
      const jobsArea = $('jenkinsJobsArea');
      if (jobsArea) jobsArea.innerHTML = '<div class="muted" style="padding:16px;">请先在「⚙ 账号设置」中绑定 Jenkins 账号。</div>';
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
  const stateText = ok ? '✅ 已连接' : (binding.last_check_status === 'error' ? '⚠️ 校验失败' : '已保存');
  statusEl.innerHTML = `<span class="badge" style="${tone}">${esc(binding.jenkins_account)} · ${stateText}</span>`;
}

export function toggleJenkinsSettings() {
  const p = $('jenkinsSettingsPanel');
  if (p) p.classList.toggle('hidden');
}

function readBindingForm() {
  return {
    base_url: ($('jenkinsBaseUrl')?.value || '').trim(),
    jenkins_account: ($('jenkinsAccount')?.value || '').trim(),
    jenkins_token: ($('jenkinsToken')?.value || '').trim(),
  };
}

export async function testJenkinsBinding() {
  const body = readBindingForm();
  if (!body.base_url || !body.jenkins_account || !body.jenkins_token) {
    window.showMessage && window.showMessage('请填写 Jenkins 地址、账号和 Token/密码', 'error');
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
    window.showMessage && window.showMessage('请填写 Jenkins 地址、账号和 Token/密码', 'error');
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

// ---------------------------------------------------------------------------
// Views + job list
// ---------------------------------------------------------------------------

async function loadJenkinsViews() {
  const sel = $('jenkinsViewSelect');
  if (!sel) return;
  try {
    const data = await (await api('/jenkins/views')).json();
    const views = data.views || [];
    const want = jenkinsCurrentView || jenkinsDefaults.view || data.default_view || '自动化测试';
    sel.innerHTML = views.map((v) => `<option value="${esc(v.name)}">${esc(v.name)}</option>`).join('');
    const names = views.map((v) => v.name);
    jenkinsCurrentView = names.includes(want) ? want : (names[0] || want);
    sel.value = jenkinsCurrentView;
  } catch (err) {
    sel.innerHTML = `<option value="${esc(jenkinsDefaults.view || '自动化测试')}">${esc(jenkinsDefaults.view || '自动化测试')}</option>`;
    jenkinsCurrentView = jenkinsDefaults.view || '自动化测试';
  }
}

export function onJenkinsViewChange() {
  jenkinsCurrentView = $('jenkinsViewSelect')?.value || jenkinsCurrentView;
  loadJenkinsJobs();
}

export async function loadJenkinsJobs() {
  const jobsArea = $('jenkinsJobsArea');
  if (!jobsArea) return;
  const view = $('jenkinsViewSelect')?.value || jenkinsCurrentView || jenkinsDefaults.view || '自动化测试';
  jenkinsCurrentView = view;
  jobsArea.innerHTML = '<div class="muted" style="padding:16px;">正在加载 Job 列表...</div>';
  try {
    const data = await (await api(`/jenkins/jobs?view=${encodeURIComponent(view)}`)).json();
    jenkinsJobsCache = data.jobs || [];
    renderJobList();
  } catch (err) {
    jobsArea.innerHTML = `<div class="muted" style="padding:16px; color:#b91c1c;">加载 Job 列表失败：${esc(err.message || '')}</div>`;
  }
}

export function onJenkinsJobFilter() { renderJobList(); }

function renderJobList() {
  const jobsArea = $('jenkinsJobsArea');
  if (!jobsArea) return;
  const kw = ($('jenkinsJobFilter')?.value || '').trim().toLowerCase();
  let jobs = jenkinsJobsCache;
  if (kw) jobs = jobs.filter((j) => String(j.name || '').toLowerCase().includes(kw));
  if (!jobs.length) {
    jobsArea.innerHTML = `<div class="muted" style="padding:16px;">视图「${esc(jenkinsCurrentView)}」下没有匹配的 Job</div>`;
    return;
  }
  const rows = jobs.map((j) => {
    const st = colorToStatus(j.color);
    const lb = j.lastBuild || {};
    const last = lb.number ? `#${lb.number} · ${fmtTime(lb.timestamp)}` : '从未构建';
    const safe = encodeURIComponent(j.name);
    return `<tr style="cursor:pointer;" onclick="viewJenkinsJob('${safe}')">
      <td style="padding:8px 10px;">
        <div style="font-weight:600; color:#1d4ed8;">${esc(j.name)}</div>
        <div class="muted" style="font-size:11px;">${esc(last)}</div>
      </td>
      <td style="padding:8px 10px; white-space:nowrap;"><span class="badge" style="background:${st.bg}; color:${st.fg};">${st.text}</span></td>
    </tr>`;
  }).join('');
  jobsArea.innerHTML = `<table style="width:100%; background:#fff; border-radius:8px; overflow:hidden; border:1px solid #e2e8f0;">
    <thead><tr style="background:#f8fafc;"><th style="text-align:left; padding:6px 10px;">Job（${jobs.length}）</th><th style="text-align:left; padding:6px 10px; width:90px;">状态</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

// ---------------------------------------------------------------------------
// Job detail
// ---------------------------------------------------------------------------

export async function viewJenkinsJob(safeName) {
  const name = decodeURIComponent(safeName);
  const area = $('jenkinsDetailArea');
  if (!area) return;
  area.innerHTML = '<div class="muted" style="padding:16px;">正在加载 Job 详情...</div>';
  try {
    const job = await (await api(`/jenkins/jobs/${encodeURIComponent(name)}`)).json();
    renderJobDetail(name, job);
  } catch (err) {
    area.innerHTML = `<div class="muted" style="padding:16px; color:#b91c1c;">加载详情失败：${esc(err.message || '')}</div>`;
  }
}

function paramDefs(job) {
  const props = job.property || [];
  for (const p of props) {
    if (Array.isArray(p.parameterDefinitions) && p.parameterDefinitions.length) return p.parameterDefinitions;
  }
  return [];
}

function renderJobDetail(name, job) {
  const area = $('jenkinsDetailArea');
  if (!area) return;
  const safe = encodeURIComponent(name);
  const health = (job.healthReport && job.healthReport[0]) || null;
  const params = paramDefs(job);
  const st = colorToStatus(job.color);
  const proxyConsoleBase = `/jenkins/proxy/${jobProxyPath(name)}`;
  const tok = encodeURIComponent(authToken());

  const buildBtn = job.buildable !== false
    ? `<button onclick="buildJenkinsJob('${safe}')">▶ ${params.length ? '参数化构建' : '立即构建'}</button>`
    : '<span class="muted" style="font-size:12px;">不可构建</span>';

  const paramSummary = params.length
    ? `<div style="margin-top:10px;"><div style="font-size:12px; color:#475569; margin-bottom:4px;">参数（${params.length}）</div>
        <div style="display:flex; gap:6px; flex-wrap:wrap;">${params.map((p) => `<span class="badge" style="background:#eef2ff; color:#4338ca;">${esc(p.name)}</span>`).join('')}</div></div>`
    : '';

  const builds = (job.builds || []).slice(0, 20);
  const buildRows = builds.length ? builds.map((b) => {
    const consoleUrl = `${proxyConsoleBase}/${b.number}/console?__jp_auth=${tok}`;
    return `<tr>
      <td style="padding:6px 8px; white-space:nowrap;"><a href="${consoleUrl}" target="_blank" rel="noopener" style="color:#1d4ed8;">#${b.number}</a></td>
      <td style="padding:6px 8px;">${resultBadge(b.result, b.building)}</td>
      <td style="padding:6px 8px; white-space:nowrap; font-size:12px; color:#475569;">${fmtTime(b.timestamp)}</td>
      <td style="padding:6px 8px; white-space:nowrap; font-size:12px; color:#475569;">${fmtDuration(b.duration)}</td>
      <td style="padding:6px 8px;"><button class="secondary" style="font-size:12px; padding:2px 8px;" onclick="loadJenkinsArtifacts('${safe}', ${b.number}, this)">产物</button></td>
    </tr>
    <tr id="art_${b.number}" class="hidden"><td colspan="5" style="padding:0 8px 8px 8px;"></td></tr>`;
  }).join('') : '<tr><td colspan="5" class="muted" style="padding:10px;">暂无构建记录</td></tr>';

  area.innerHTML = `
    <div style="border:1px solid #e2e8f0; border-radius:10px; background:#fff; padding:14px 16px;">
      <div class="row" style="justify-content:space-between; align-items:flex-start; gap:10px; flex-wrap:wrap;">
        <div>
          <div style="font-size:16px; font-weight:700; color:#0f172a;">${esc(name)} <span class="badge" style="background:${st.bg}; color:${st.fg};">${st.text}</span></div>
          ${health ? `<div class="muted" style="font-size:12px; margin-top:4px;">健康度 ${health.score}% · ${esc(health.description || '')}</div>` : ''}
          ${job.description ? `<div style="font-size:12px; color:#475569; margin-top:6px; max-width:640px;">${esc(job.description)}</div>` : ''}
        </div>
        <div class="row" style="gap:8px;">
          ${buildBtn}
          <a class="qa-ext-link" href="${proxyConsoleBase}/?__jp_auth=${tok}" target="_blank" rel="noopener" style="font-size:12px;">打开 Job 页</a>
        </div>
      </div>
      ${paramSummary}
      <div style="margin-top:14px;">
        <div style="font-size:13px; font-weight:600; color:#0f172a; margin-bottom:6px;">构建历史</div>
        <table style="width:100%; background:#fff; border:1px solid #e2e8f0; border-radius:8px; overflow:hidden;">
          <thead><tr style="background:#f8fafc; font-size:12px;">
            <th style="text-align:left; padding:6px 8px;">#</th><th style="text-align:left; padding:6px 8px;">结果</th>
            <th style="text-align:left; padding:6px 8px;">时间</th><th style="text-align:left; padding:6px 8px;">耗时</th>
            <th style="text-align:left; padding:6px 8px;">产物</th>
          </tr></thead>
          <tbody>${buildRows}</tbody>
        </table>
      </div>
    </div>`;
}

export async function loadJenkinsArtifacts(safeName, number, btnEl) {
  const name = decodeURIComponent(safeName);
  const row = $(`art_${number}`);
  if (!row) return;
  const cell = row.querySelector('td');
  if (!row.classList.contains('hidden')) { row.classList.add('hidden'); return; }
  row.classList.remove('hidden');
  cell.innerHTML = '<div class="muted" style="padding:6px;">加载产物...</div>';
  try {
    const b = await (await api(`/jenkins/build?job=${encodeURIComponent(name)}&number=${number}`)).json();
    const arts = b.artifacts || [];
    const tok = encodeURIComponent(authToken());
    if (!arts.length) { cell.innerHTML = '<div class="muted" style="padding:6px;">该构建无归档产物</div>'; return; }
    const links = arts.map((a) => {
      const url = `/jenkins/download?job=${encodeURIComponent(name)}&number=${number}&path=${encodeURIComponent(a.relativePath)}&__jp_auth=${tok}`;
      return `<a href="${url}" style="display:inline-flex; align-items:center; gap:4px; font-size:12px; padding:3px 8px; margin:2px; border:1px solid #cbd5e1; border-radius:6px; color:#1d4ed8; text-decoration:none;">⬇ ${esc(a.fileName)}</a>`;
    }).join('');
    const zipUrl = `/jenkins/download?job=${encodeURIComponent(name)}&number=${number}&path=${encodeURIComponent('*zip*/archive.zip')}&__jp_auth=${tok}`;
    cell.innerHTML = `<div style="display:flex; flex-wrap:wrap; align-items:center; padding:4px 0;">${links}
      <a href="${zipUrl}" style="font-size:12px; padding:3px 8px; margin:2px; border:1px solid #94a3b8; border-radius:6px; color:#334155; text-decoration:none;">📦 全部(zip)</a></div>`;
  } catch (err) {
    cell.innerHTML = `<div class="muted" style="padding:6px; color:#b91c1c;">加载产物失败：${esc(err.message || '')}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Build trigger
// ---------------------------------------------------------------------------

export async function buildJenkinsJob(safeName) {
  const name = decodeURIComponent(safeName);
  let job;
  try {
    job = await (await api(`/jenkins/jobs/${encodeURIComponent(name)}`)).json();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '读取 Job 失败', 'error');
    return;
  }
  const params = paramDefs(job);
  if (params.length) {
    openJenkinsBuildModal(name);
    return;
  }
  if (!confirm(`确认触发 Job「${name}」的构建吗？`)) return;
  try {
    const res = await (await api(`/jenkins/jobs/${encodeURIComponent(name)}/build`, { method: 'POST', headers: window.H, body: {} })).json();
    window.showMessage && window.showMessage(res.message || '已触发构建', 'success');
    if (res.queue_item_url) await pollQueueItem(res.queue_item_url, name);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '触发构建失败', 'error');
  }
}

function openJenkinsBuildModal(name) {
  const modal = $('jenkinsBuildModal');
  const frame = $('jenkinsBuildFrame');
  const title = $('jenkinsBuildModalTitle');
  const openLink = $('jenkinsBuildModalOpen');
  if (!modal || !frame) return;
  const tok = encodeURIComponent(authToken());
  const src = `/jenkins/proxy/${jobProxyPath(name)}/build?delay=0sec&__jp_auth=${tok}`;
  if (title) title.textContent = `参数化构建 · ${name}`;
  if (openLink) openLink.href = `/jenkins/proxy/${jobProxyPath(name)}/?__jp_auth=${tok}`;
  frame.src = src;
  modal.classList.remove('hidden');
}

export function closeJenkinsBuildModal() {
  const modal = $('jenkinsBuildModal');
  const frame = $('jenkinsBuildFrame');
  if (frame) frame.src = 'about:blank';
  if (modal) modal.classList.add('hidden');
  // 构建可能已提交：刷新当前 Job 详情（如已打开）
}

async function pollQueueItem(queueUrl, jobName) {
  for (let i = 0; i < 8; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      const q = await (await api(`/jenkins/queue?url=${encodeURIComponent(queueUrl)}`)).json();
      if (q.cancelled) { window.showMessage && window.showMessage('构建已被取消', 'error'); return; }
      if (q.started && q.build_number) {
        window.showMessage && window.showMessage(`已启动构建 #${q.build_number}`, 'success');
        if (jobName) viewJenkinsJob(encodeURIComponent(jobName));
        return;
      }
    } catch { /* ignore single poll error */ }
  }
}

// Expose handlers used by inline onclick (generated HTML + index.html)
window.OmniQAJenkinsTab = {
  loadJenkinsTab, testJenkinsBinding, saveJenkinsBinding, deleteJenkinsBinding,
  loadJenkinsJobs, toggleJenkinsSettings, onJenkinsViewChange, onJenkinsJobFilter,
  viewJenkinsJob, buildJenkinsJob, loadJenkinsArtifacts, closeJenkinsBuildModal,
};
Object.assign(window, {
  loadJenkinsJobs, toggleJenkinsSettings, onJenkinsViewChange, onJenkinsJobFilter,
  viewJenkinsJob, buildJenkinsJob, loadJenkinsArtifacts, closeJenkinsBuildModal,
});
