const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const statuses = {pending:'待分析',processing:'处理中',verifying:'待验证',closed:'已关闭',ignored:'忽略'};
const actions = {created:'新增',updated:'更新',skipped:'跳过'};
const state = {user:null,external:location.pathname.endsWith('/external'),apps:[],users:[],page:1,size:20,total:0,issue:null,reports:[],reportPage:1,reportTotal:0,reportQuery:'',listSequence:0,reportSequence:0,reportListSequence:0,detailSequence:0};
const date = value => value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '未采集';
const option = (value, label) => `<option value="${escape(value)}">${escape(label)}</option>`;
const appOptions = writable => state.apps.filter(a => !writable || a.can_edit).map(a => option(a.id, `${a.name}${a.package_name ? ' · '+a.package_name : ''}`)).join('');
const userOptions = () => state.users.map(u => option(u.id, `${u.display_name || u.username}${u.account_type === 'external' ? ' · 外部只读' : ''}`)).join('');

async function request(path, options = {}) {
  const headers = {...options.headers, Authorization:`Bearer ${localStorage.getItem('token') || ''}`};
  let body = options.body;
  if (body && !(body instanceof FormData) && typeof body === 'object') {
    body = JSON.stringify(body); headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, {...options,headers,body,cache:'no-store'});
  if (response.status === 401) {
    localStorage.removeItem('token');
    location.replace('/login?next='+encodeURIComponent(location.pathname+location.search));
    throw new Error('请重新登录');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(i => `${i.loc?.slice(1).join('.')}: ${i.msg}`).join('；') : data.detail;
    throw new Error(detail || `请求失败 (${response.status})`);
  }
  return data;
}
const api = (path, options) => request('/api/bugly'+path, options);
function notify(message, error = false) { const el=$('#notice');el.textContent=message;el.classList.toggle('error',error);el.hidden=false;if($('#modal').open){$('#modalNotice').textContent=message;$('#modalNotice').classList.toggle('error',error);$('#modalNotice').hidden=false;} }
function safe(fn) { return async (...args) => { try { await fn(...args); } catch (error) { notify(error.message,true); } }; }
function modal(title, html) { $('#modalNotice').hidden=true;$('#modalTitle').textContent=title;$('#modalBody').onclick=null;$('#modalBody').innerHTML=html;if(!$('#modal').open)$('#modal').showModal(); }
function closeModal() { $('#modal').close(); }
function formData(form) { return Object.fromEntries(new FormData(form).entries()); }
function bindForm(selector, callback) {
  $(selector).addEventListener('submit', safe(async event => {
    event.preventDefault();const form=event.currentTarget, button=$('[type=submit]',form);button.disabled=true;
    try { await callback(formData(form),form); } finally { button.disabled=false; }
  }));
}
function selectedApp() { return $('#applicationFilter').value || state.apps.find(a=>a.can_edit)?.id || state.apps[0]?.id || ''; }
function restoreFilters() {
  const params=new URLSearchParams(location.search);
  for(const el of $$('#filterForm [name]')) { if(params.has(el.name)){ if(el.type==='checkbox')el.checked=params.get(el.name)==='true';else el.value=params.get(el.name); } }
  state.page=Number(params.get('page')) || 1;
  state.size=[20,50,100].includes(Number(params.get('page_size')))?Number(params.get('page_size')):20;
  $('#pageSize').value=state.size;
}
function filterParams() {
  const params=new URLSearchParams();
  for(const el of $$('#filterForm [name]')) {
    if(state.external&&el.closest('.internal-filter'))continue;
    let value=el.type==='checkbox'?(el.checked?'true':''):el.value.trim();
    if(value&&el.type==='datetime-local')value=new Date(value).toISOString();
    if(value)params.set(el.name,value);
  }
  params.set('page',state.page);params.set('page_size',state.size);return params;
}
function syncUrl(issueId=null, push=false) {
  const params=new URLSearchParams();
  for(const el of $$('#filterForm [name]')) { const value=el.type==='checkbox'?(el.checked?'true':''):el.value;if(value)params.set(el.name,value); }
  params.set('page',state.page);params.set('page_size',state.size);if(issueId)params.set('issue',issueId);
  history[push?'pushState':'replaceState']({},'',`${location.pathname}?${params}`);
}
async function loadList(sync=true) {
  const sequence=++state.listSequence;
  const reportView=!state.external&&$('#filterForm [name=view]').value==='reports';
  const data=await api(`${state.external?'/external/issues':reportView?'/reports':'/issues'}?${filterParams()}`);
  if(sequence!==state.listSequence)return;
  if(reportView)data.items.forEach(item=>{item.versions=[item.app_version];item.last_occurred_at=item.occurred_at;});
  state.total=data.total;
  $('#issueTotalLabel').textContent=reportView?'符合筛选的上报':'符合筛选的问题';
  $('#reportTotalLabel').textContent=state.external?'当前页已发布上报':'当前页已导入上报';
  $('#thirdMetricLabel').textContent=state.external?'当前访问权限':reportView?'查看方式':'当前页已发布问题';
  $('#issueTotal').textContent=data.total;
  $('#reportTotal').textContent=data.items.reduce((sum,item)=>sum+item.report_count,0);
  $('#publishedTotal').textContent=state.external?'只读':reportView?'逐条上报':data.items.filter(i=>i.published).length;
  $('#issueHead').innerHTML=`<tr><th>问题 / 标题</th><th>所属应用</th>${state.external?'':'<th>涉及版本</th>'}<th>${state.external?'已发布':'已导入'}上报</th><th>${state.external?'发布时间':'最近发生时间'}</th><th>${state.external?'可见范围':'处理 / 发布状态'}</th></tr>`;
  $('#issueRows').innerHTML=data.items.length?data.items.map(item=>`<tr><td><button class="issue-link" data-open-issue="${item.id}">${escape(item.title)}</button><div class="subline">${item.source_issue_id?'Bugly #'+escape(item.source_issue_id):'手动问题'}${item.archived?' · 已归档':''}</div></td><td>${escape(item.application_name)}</td>${state.external?'':`<td>${escape(item.versions.join('、') || '未采集')}</td>`}<td>${item.report_count}</td><td>${date(state.external?item.published_at:item.last_occurred_at)}</td><td>${state.external?'<span class="badge closed">已授权 · 只读</span>':`<span class="badge ${escape(item.status)}">${statuses[item.status] || escape(item.status)}</span><div class="subline">${item.published?'已发布':'内部可见'}${item.unpublished_changes?' · 有未发布变更':''}</div>`}</td></tr>`).join(''):`<tr><td colspan="6"><div class="empty">暂无符合条件的问题<br>${state.external?'有新的资料发布并授权后，会显示在这里。':'可新增问题，或导入插件采集的 JSON / CSV 文件。'}</div></td></tr>`;
  if(reportView){
    $('#issueHead').innerHTML='<tr><th>上报编号</th><th>所属问题 / 应用</th><th>版本</th><th>设备</th><th>发生时间</th><th>堆栈质量</th></tr>';
    $('#issueRows').innerHTML=data.items.length?data.items.map(r=>`<tr><td><button class="issue-link" data-open-global-report="${r.id}" data-issue-id="${r.issue_id}">${r.source_report_id?'#'+escape(r.source_report_id):'手动上报'}</button></td><td><button class="issue-link" data-open-issue="${r.issue_id}">${escape(r.title)}</button><div class="subline">${escape(r.application_name)}</div></td><td>${escape(r.app_version||'未采集')}</td><td>${escape(r.device||'未采集')}</td><td>${date(r.occurred_at)}</td><td><span class="badge">${r.stack_mode==='parsed'?'解析堆栈':r.stack_mode==='manual'?'手动录入':'兜底 · 待检查'}</span></td></tr>`).join(''):'<tr><td colspan="6"><div class="empty">暂无符合条件的上报</div></td></tr>';
  }
  $('#pageLabel').textContent=`共 ${data.total} ${reportView?'条上报':'个问题'} · 第 ${state.page} / ${Math.max(1,Math.ceil(data.total/state.size))} 页`;
  $('#previousPage').disabled=state.page<=1;$('#nextPage').disabled=state.page*state.size>=data.total;
  if(sync)syncUrl();
}
async function refreshApplications() {
  state.apps=await api(state.external?'/external/applications':'/applications');
  const current=$('#applicationFilter').value;
  $('#applicationFilter').innerHTML=option('','全部授权应用')+appOptions(false);
  $('#applicationFilter').value=current;
  if(!state.external){const canEdit=state.apps.some(a=>a.can_edit);$('#newIssueBtn').disabled=!canEdit;$('#importBtn').disabled=!canEdit;}
}
async function openIssue(id,push=true) {
  const sequence=++state.detailSequence;
  const data=await api(`${state.external?'/external':''}/issues/${id}`);
  if(sequence!==state.detailSequence)return;
  state.issue=data;state.reportPage=1;state.reportQuery='';++state.reportSequence;
  $('#listView').hidden=true;$('#detailView').hidden=false;
  if(push)syncUrl(id,true);
  const count=state.external?data.reports.length:data.report_count;
  $('#detailView').innerHTML=`<div class="detail-heading"><div><button class="quiet" data-action="back">← 返回问题列表</button><h2>${escape(data.title)}</h2><span class="badge">${data.source_issue_id?'Bugly #'+escape(data.source_issue_id):'手动问题'}</span> <span class="muted">${escape(data.application_name)}</span></div><div class="actions">${state.external?'<span class="badge closed">已授权 · 只读</span>':`${data.can_edit?'<button class="secondary" data-action="edit">编辑问题</button><button class="secondary" data-action="new-report">＋ 新增上报</button>':''}${data.can_publish?'<button data-action="publish">对外预览与发布</button>':''}${data.published&&data.can_publish?'<button class="danger" data-action="withdraw">撤回发布</button>':''}`}</div></div>
    <div class="panel detail-overview"><div><span>异常类型</span><strong>${escape(data.exception_type)}</strong></div><div><span>${state.external?'已发布上报数':'已导入上报数'}</span><strong>${count}</strong></div><div><span>${state.external?'发布时间':'处理状态'}</span><strong>${state.external?date(data.published_at):statuses[data.status]}</strong></div><div><span>资料范围</span><strong>${state.external?'审核后的发布版本':`${data.published?'已对外发布':'内部可见'}${data.unpublished_changes?' · 有未发布变更':''}`}</strong></div></div>
    <div class="panel notes">${!state.external?`<div><h3>内部分析结论</h3><p>${escape(data.analysis||'暂无分析结论')}</p></div>`:''}<div><h3>对外说明</h3><p>${escape(data.external_note||'暂无对外说明')}</p></div></div>
    <div class="report-layout"><aside class="panel report-sidebar"><h3>上报记录</h3><form class="report-search" id="reportSearch"><input id="reportQuery" placeholder="版本 / 设备 / 上报编号"><button class="secondary">查找</button></form><div id="reportList" class="report-list"></div><div class="pagination"><span id="reportPageLabel"></span><div><button class="secondary" data-action="report-prev">‹</button><button class="secondary" data-action="report-next">›</button></div></div></aside><article class="panel report-content" id="reportContent"><div class="empty">选择一条上报记录查看详情</div></article></div>`;
  $('#reportSearch').addEventListener('submit',safe(async event=>{event.preventDefault();state.reportQuery=$('#reportQuery').value;state.reportPage=1;await loadReports();}));
  await loadReports();
}
async function loadReports() {
  const sequence=++state.reportListSequence;
  const id=state.issue.id;
  let data;
  if(state.external){const q=state.reportQuery.toLowerCase();const all=state.issue.reports.filter(r=>!q||[r.app_version,r.device,r.source_report_id].join(' ').toLowerCase().includes(q));data={items:all.slice((state.reportPage-1)*20,state.reportPage*20),total:all.length};}
  else data=await api(`/issues/${id}/reports?${new URLSearchParams({page:state.reportPage,page_size:20,q:state.reportQuery})}`);
  if(state.issue?.id!==id||sequence!==state.reportListSequence)return;
  state.reports=data.items;state.reportTotal=data.total;
  $('#reportList').innerHTML=data.items.length?data.items.map(r=>`<button class="report-item" data-open-report="${r.id}">${r.source_report_id?'#'+escape(r.source_report_id):'手动上报'}<span>${escape(r.app_version||'版本未采集')} · ${escape(r.device||'设备未采集')}</span><span>${date(r.occurred_at)}${r.stack_mode==='fallback'?' · 兜底堆栈':''}</span></button>`).join(''):'<div class="empty">暂无上报记录</div>';
  $('#reportPageLabel').textContent=`${data.total} 条 · 第 ${state.reportPage} 页`;
  $('[data-action=report-prev]').disabled=state.reportPage<=1;$('[data-action=report-next]').disabled=state.reportPage*20>=data.total;
  if(data.items.length)await openReport(data.items[0].id);
  else $('#reportContent').innerHTML='<div class="empty">暂无匹配记录</div>';
}
async function openReport(id) {
  const sequence=++state.reportSequence;
  const row=state.external?state.issue.reports.find(r=>r.id===id):await api(`/reports/${id}`);
  if(sequence!==state.reportSequence)return;
  $$('[data-open-report]').forEach(el=>el.classList.toggle('active',Number(el.dataset.openReport)===id));
  const fields=[['应用版本',row.app_version],['设备机型',row.device],['系统版本',row.os_version],['发生时间',row.occurred_at?date(row.occurred_at):row.occurred_at_raw],...(!state.external?[['采集时间',date(row.collected_at)],['堆栈帧数',row.frame_count]]:[])];
  $('#reportContent').innerHTML=`<div class="bar"><h3>${row.source_report_id?'上报 #'+escape(row.source_report_id):'手动上报'}</h3><span class="badge">${row.stack_mode==='parsed'?'解析堆栈':row.stack_mode==='manual'?'手动录入':'兜底结果 · 待检查'}</span></div><div class="fields">${fields.map(([key,value])=>`<div>${escape(key)}<strong>${escape(value||'未采集')}</strong></div>`).join('')}</div>
    <div class="bar"><input id="stackQuery" placeholder="在堆栈中定位关键词"><div class="actions"><button class="quiet" data-action="stack-wrap">换行</button><button class="quiet" data-action="stack-full">展开</button><button class="secondary" data-action="stack-copy">复制堆栈</button></div></div><pre class="stack" id="stackText"></pre>
    ${!state.external?`<details><summary>现场字段</summary><pre>${escape(JSON.stringify({列表:row.payload.listFields||{},详情:row.payload.detailFields||{}},null,2))}</pre></details>${state.issue.can_sensitive?`<details><summary>原始文本与采集诊断 · 内部资料</summary><pre>${escape(row.payload.rawDetailText||'未采集')}</pre><pre>${escape(JSON.stringify(row.payload.captureDiagnostics||{},null,2))}</pre></details>`:''}`:''}`;
  if(!state.external&&state.issue.can_sensitive){
    const details=document.createElement('details');details.innerHTML='<summary>上报修订记录（最近 50 次）</summary><div class="revision-content"></div>';
    $('#reportContent').append(details);
    details.addEventListener('toggle',safe(async()=>{if(!details.open||details.dataset.loaded)return;const revisions=await api(`/reports/${id}/revisions`);$('.revision-content',details).innerHTML=revisions.map(r=>`<details><summary>第 ${r.revision} 版 · ${date(r.created_at)}</summary><pre>${escape(JSON.stringify(r.payload,null,2))}</pre></details>`).join('');details.dataset.loaded='true';}));
  }
  state.stack=row.stack_text||'未采集到堆栈';
  const renderStack=()=>{const q=$('#stackQuery').value;$('#stackText').innerHTML=state.stack.split('\n').map(line=>`<span class="stack-line">${q?line.split(q).map(escape).join(`<mark>${escape(q)}</mark>`):escape(line)}</span>`).join('\n');};
  renderStack();$('#stackQuery').addEventListener('input',renderStack);
}
async function back() {++state.detailSequence;++state.reportSequence;++state.reportListSequence;state.issue=null;$('#detailView').hidden=true;$('#listView').hidden=false;syncUrl(null,true);await loadList(false);}

function newIssue() {
  modal('新增问题',`<p class="form-note">可先记录问题和分析线索，再补充上报。没有 Bugly 编号时可以留空。</p><form id="issueForm"><div class="form-grid"><label>所属应用<select name="application_id" required>${appOptions(true)}</select></label><label>Bugly 问题编号<input name="source_issue_id" maxlength="100" placeholder="例如 12345，选填"></label><label class="wide">问题标题<input name="title" required maxlength="500"></label><label>异常类型<input name="exception_type" value="待确认" maxlength="100"></label></div><div class="form-footer"><button type="submit">创建问题</button></div></form>`);
  $('[name=application_id]',$('#modal')).value=selectedApp();
  bindForm('#issueForm',async data=>{data.application_id=Number(data.application_id);data.source_issue_id=data.source_issue_id||null;const row=await api('/issues',{method:'POST',body:data});closeModal();notify('问题已创建');await openIssue(row.id);});
}
async function editIssue() {
  const data=state.issue;
  if(!state.users.length)state.users=await request('/users');
  modal('编辑问题',`<form id="editForm"><div class="form-grid"><label class="wide">标题<input name="title" value="${escape(data.title)}" required maxlength="500"></label><label>处理状态<select name="status">${Object.entries(statuses).map(([key,value])=>option(key,value)).join('')}</select></label><label>异常类型<input name="exception_type" value="${escape(data.exception_type)}" maxlength="100"></label><label>负责人<select name="owner_id">${option('','暂不指定')+state.users.filter(u=>u.account_type!=='external').map(u=>option(u.id,u.display_name||u.username)).join('')}</select></label><label class="check"><input name="archived" type="checkbox" ${data.archived?'checked':''}>归档问题（会撤回对外发布）</label><label class="wide">内部分析结论<textarea name="analysis" maxlength="50000">${escape(data.analysis)}</textarea></label><label class="wide">对外说明<textarea name="external_note" maxlength="50000">${escape(data.external_note)}</textarea></label></div><div class="form-footer"><button type="submit">保存修改</button></div></form>`);
  $('[name=status]',$('#modal')).value=data.status;$('[name=owner_id]',$('#modal')).value=data.owner_id||'';
  const idLabel=document.createElement('label');idLabel.textContent='Bugly 问题编号';const idInput=document.createElement('input');idInput.name='source_issue_id';idInput.value=data.source_issue_id||'';idInput.maxLength=100;idInput.readOnly=!!data.source_issue_id;idInput.placeholder='尚无编号时可后续补充';idLabel.append(idInput);$('#editForm .form-grid').prepend(idLabel);
  bindForm('#editForm',async values=>{values.expected_version=data.version;values.source_issue_id=values.source_issue_id||null;values.owner_id=values.owner_id?Number(values.owner_id):null;values.archived=values.archived==='on';await api(`/issues/${data.id}`,{method:'PATCH',body:values});closeModal();notify('修改已保存；对外内容需重新审核发布');await openIssue(data.id,false);});
}
function newReport() {
  modal('新增上报记录',`<form id="reportForm"><div class="form-grid"><label>上报编号<input name="reportId" maxlength="100" placeholder="选填"></label><label>应用版本<input name="appVersion" maxlength="255"></label><label>发生时间<input name="occurredAt" type="datetime-local"></label><label>设备机型<input name="device" maxlength="255"></label><label>系统版本<input name="osVersion" maxlength="255"></label><label class="wide">出错堆栈<textarea name="stack" rows="10" maxlength="600000" spellcheck="false"></textarea></label><label class="wide">现场补充文本<textarea name="rawDetailText" maxlength="600000"></textarea></label></div><div class="form-footer"><button type="submit">保存上报</button></div></form>`);
  bindForm('#reportForm',async data=>{const payload={reportId:data.reportId||null,collectedAt:new Date().toISOString(),detailFields:{应用版本:data.appVersion,发生时间:data.occurredAt?new Date(data.occurredAt).toISOString():'',设备机型:data.device,系统版本:data.osVersion},stack:{text:data.stack,mode:'manual',frameCount:0},rawDetailText:data.rawDetailText};await api(`/issues/${state.issue.id}/reports`,{method:'POST',body:payload});closeModal();notify('上报已保存');await openIssue(state.issue.id,false);});
}
function importFiles() {
  modal('导入 Bugly 采集文件',`<p class="form-note">推荐使用插件导出的 JSON，CSV 也可导入。每个文件不超过 20 MB、2,000 条上报。数据先进入内部资料。</p><form id="importForm"><div class="form-grid"><label>所属应用<select name="application_id" required>${appOptions(true)}</select></label><label>缺少问题编号时的归属问题<select name="issue_id">${option('','按文件问题编号识别')}</select></label><label class="wide">采集文件<input name="files" type="file" accept=".json,.csv" multiple required></label></div><div class="form-footer"><button type="submit">校验并预览</button></div></form><div id="importPreview"></div>`);
  $('[name=application_id]',$('#modal')).value=selectedApp();
  const reloadAssigned=safe(async()=>{const app=$('[name=application_id]',$('#modal')).value;if(!app)return;const rows=await api(`/issues?application_id=${app}&page_size=100`);$('[name=issue_id]',$('#modal')).innerHTML=option('','按文件问题编号识别')+rows.items.map(r=>option(r.id,`${r.source_issue_id?'#'+r.source_issue_id:'手动'} · ${r.title}`)).join('');});
  $('[name=application_id]',$('#modal')).addEventListener('change',reloadAssigned);reloadAssigned();
  bindForm('#importForm',async(data,form)=>{
    const files=$('[name=files]',form).files;
    if(files.length>10)throw new Error('单次请选择不超过 10 个文件');
    if([...files].some(f=>f.size>20*1024*1024))throw new Error('单文件不能超过 20 MB');
    const batches=[];let errors=[];
    for(const file of files){const fd=new FormData();fd.set('application_id',data.application_id);if(data.issue_id)fd.set('issue_id',data.issue_id);fd.set('file',file);try{batches.push(await api('/imports/preview',{method:'POST',body:fd}));}catch(error){errors.push(`${file.name}：${error.message}`);}}
    $('#importPreview').innerHTML=errors.map(e=>`<p class="error-text">${escape(e)}</p>`).join('')+batches.map(batch=>{
      const counts={created:0,updated:0,skipped:0,failed:0};batch.items.forEach(i=>counts[i.error?'failed':i.action]++);
      return `<section class="review-report" data-batch="${batch.id}"><h3>${escape(batch.filename)}</h3><div class="preview-summary">${Object.entries(counts).map(([k,v])=>`<span class="badge">${actions[k]||'无效'} ${v} 条</span>`).join('')}</div><p class="form-note">${escape((batch.diagnostics.warnings||[]).join('；'))}${batch.diagnostics.failures?.length?` · 原采集失败 ${batch.diagnostics.failures.length} 条`:''}</p><details><summary>逐条校验结果（${batch.items.length} 条）</summary><div class="table-wrap"><table class="mini-table"><tr><th>行</th><th>问题</th><th>结果</th><th>提示</th></tr>${batch.items.map(i=>`<tr><td>${i.row}</td><td>${escape(i.title||'未识别')}</td><td>${escape(i.error?'无效':actions[i.action])}</td><td>${escape(i.error||(i.warnings||[]).join('；'))}</td></tr>`).join('')}</table></div></details><div class="form-footer"><span class="muted" data-result></span><button data-confirm-import="${batch.id}" ${counts.failed===batch.items.length?'disabled':''}>确认导入有效记录</button></div></section>`;
    }).join('');
    $('#importPreview').onclick=safe(async event=>{
      const button=event.target.closest('[data-confirm-import]');if(!button)return;button.disabled=true;
      try{const batch=await api(`/imports/${button.dataset.confirmImport}/confirm`,{method:'POST'});const r=batch.result;button.textContent='已导入';$('[data-result]',button.closest('[data-batch]')).textContent=`新增 ${r.created} · 更新 ${r.updated} · 跳过 ${r.skipped} · 无效 ${r.failed}`;await loadList(false);}
      catch(error){button.disabled=false;throw error;}
    });
  });
}
async function historyModal() {
  const rows=await api('/imports');
  modal('最近导入记录',rows.length?`<div class="table-wrap"><table class="mini-table"><tr><th>文件</th><th>预览时间</th><th>状态</th><th>实际结果</th></tr>${rows.map(r=>`<tr><td>${escape(r.filename)}<details><summary>查看校验与采集结果</summary><pre>${escape(JSON.stringify({采集信息:r.diagnostics,逐条结果:r.result.items||r.items},null,2))}</pre></details></td><td>${date(r.created_at)}</td><td>${r.status==='confirmed'?'已导入':new Date(r.expires_at)<new Date()?'预览已过期':'待确认'}</td><td>${r.status==='confirmed'?`新增 ${r.result.created} / 更新 ${r.result.updated}<br>跳过 ${r.result.skipped} / 无效 ${r.result.failed}`:'尚未写入上报'}</td></tr>`).join('')}</table></div>`:'<div class="empty">暂无导入记录</div>');
}
async function applicationsModal() {
  modal('应用管理',`<p class="form-note">为每个 Bugly 应用建立独立数据空间。来源站点和包名用于导入校验，编号相同的不同应用不会合并。</p><form id="appForm"><div class="form-grid"><label>应用名称<input name="name" required maxlength="120" placeholder="例如 Android 正式版"></label><label>Bugly App ID<input name="bugly_app_id" maxlength="100" placeholder="选填"></label><label>来源站点<input name="source_host" value="bugly.qq.com" required maxlength="255"></label><label>应用包名<input name="package_name" maxlength="255" placeholder="选填"></label><label>系统平台<select name="platform">${['Android','iOS','Other'].map(p=>option(p,p)).join('')}</select></label><label>来源时区<input name="source_timezone" value="Asia/Shanghai" required></label></div><div class="form-footer"><button type="submit">新增应用</button></div></form><hr class="section-divider"><h3>已建档应用</h3><div>${state.apps.map(a=>`<div class="account-row"><div>${escape(a.name)}<br><span>${escape(a.platform)} · ${escape(a.package_name||'包名未配置')} · ${escape(a.source_host)}</span></div><span>${escape(a.bugly_app_id||'App ID 未配置')}</span></div>`).join('')||'<p class="muted">尚未建档</p>'}</div>`);
  bindForm('#appForm',async data=>{data.bugly_app_id=data.bugly_app_id||null;data.package_name=data.package_name||null;await api('/applications',{method:'POST',body:data});notify('应用已建立');await refreshApplications();await applicationsModal();});
}
async function permissionsModal() {
  const [users,grants]=await Promise.all([request('/users'),api('/grants')]);state.users=users;
  modal('账号与权限管理',`<p class="form-note">外部人员使用独立账号，只能浏览授权范围内已发布的资料。授权可指定整个应用或某个问题，可设置到期时间。</p><h3>新增外部只读账号</h3><form id="accountForm"><div class="form-grid"><label>用户名<input name="username" required minlength="3" maxlength="50" pattern="[A-Za-z0-9_.@-]+" autocomplete="off" placeholder="字母、数字、下划线等"></label><label>显示名称<input name="display_name" maxlength="80"></label><label>初始密码<input name="password" type="password" required minlength="8" maxlength="72" autocomplete="new-password"></label><label>账号到期时间<input name="expires_at" type="datetime-local"></label></div><div class="form-footer"><button type="submit">创建外部账号</button></div></form>
    <hr class="section-divider"><h3>分配数据权限</h3><form id="grantForm"><div class="form-grid"><label>用户<select name="user_id" required>${userOptions()}</select></label><label>应用<select name="application_id" required>${appOptions(false)}</select></label><label>授权范围<select name="issue_id">${option('','整个应用')}</select></label><label>内部操作权限<select name="role"><option value="viewer">只读查看</option><option value="editor">新增 / 导入 / 编辑</option></select></label><label>授权到期时间<input name="expires_at" type="datetime-local"></label><div><label class="check"><input name="can_sensitive" type="checkbox">查看内部原始资料</label><label class="check" style="margin-top:12px"><input name="can_publish" type="checkbox">审核发布 / 撤回</label></div></div><div class="form-footer"><button type="submit">保存授权</button></div></form>
    <hr class="section-divider"><h3>现有授权</h3><div class="table-wrap"><table class="mini-table"><tr><th>用户</th><th>范围</th><th>权限</th><th>到期</th><th></th></tr>${grants.map(g=>`<tr><td>${escape(g.username)}</td><td>${escape(g.application_name)} · ${g.issue_id?'指定问题':'整个应用'}</td><td>${g.role==='editor'?'维护':'只读'}${g.can_publish?' / 发布':''}${g.can_sensitive?' / 原始资料':''}</td><td>${g.expires_at?date(g.expires_at):'长期有效'}</td><td><button class="danger" data-revoke="${g.id}">撤销</button></td></tr>`).join('')}</table></div>
    <hr class="section-divider"><h3>外部账号</h3>${users.filter(u=>u.account_type==='external').map(u=>`<div class="account-row"><div>${escape(u.display_name||u.username)} <span>${escape(u.username)} · ${u.is_active?'启用':'停用'}${u.expires_at?' · 到期 '+date(u.expires_at):''}</span></div><div class="actions"><button class="secondary" data-account-expiry="${u.id}">调整有效期</button><button class="secondary" data-account-reset="${u.id}">重置密码</button><button class="${u.is_active?'danger':'secondary'}" data-account-toggle="${u.id}">${u.is_active?'停用':'启用'}</button></div></div>`).join('')||'<p class="muted">暂无外部账号</p>'}`);
  bindForm('#accountForm',async data=>{data.expires_at=data.expires_at?new Date(data.expires_at).toISOString():null;await api('/accounts',{method:'POST',body:data});notify('外部账号已创建，请继续分配数据权限');await permissionsModal();});
  const grantForm=$('#grantForm');
  const reloadScope=safe(async()=>{const id=$('[name=application_id]',grantForm).value;if(!id)return;const rows=await api(`/issues?application_id=${id}&page_size=100`);$('[name=issue_id]',grantForm).innerHTML=option('','整个应用')+rows.items.map(r=>option(r.id,r.title)).join('');});
  const restrict=()=>{const user=users.find(u=>u.id===Number($('[name=user_id]',grantForm).value)),external=user?.account_type==='external';for(const el of $$('[name=role],[name=can_sensitive],[name=can_publish]',grantForm)){el.disabled=external;if(external){if(el.type==='checkbox')el.checked=false;else el.value='viewer';}}};
  $('[name=user_id]',grantForm).addEventListener('change',restrict);$('[name=application_id]',grantForm).addEventListener('change',reloadScope);restrict();reloadScope();
  bindForm('#grantForm',async data=>{const body={user_id:Number(data.user_id),application_id:Number(data.application_id),issue_id:data.issue_id?Number(data.issue_id):null,role:data.role||'viewer',can_sensitive:data.can_sensitive==='on',can_publish:data.can_publish==='on',expires_at:data.expires_at?new Date(data.expires_at).toISOString():null};await api('/grants',{method:'POST',body});notify('授权已保存');await permissionsModal();});
  $('#modalBody').onclick=safe(async event=>{
    const revoke=event.target.closest('[data-revoke]');if(revoke){await api('/grants/'+revoke.dataset.revoke,{method:'DELETE'});notify('授权已撤销');await permissionsModal();return;}
    const toggle=event.target.closest('[data-account-toggle]');if(toggle){const user=users.find(u=>u.id===Number(toggle.dataset.accountToggle));await api('/accounts/'+user.id,{method:'PATCH',body:{is_active:!user.is_active,expires_at:user.expires_at}});notify('账号状态已更新');await permissionsModal();return;}
    const reset=event.target.closest('[data-account-reset]');if(reset){const password=prompt('请输入新的账号密码（至少 8 位）');if(!password)return;if(password.length<8)throw new Error('密码至少 8 位');await request(`/users/${reset.dataset.accountReset}/password`,{method:'PUT',body:{new_password:password}});notify('密码已重置，原会话已失效');}
    const expiry=event.target.closest('[data-account-expiry]');if(expiry){const user=users.find(u=>u.id===Number(expiry.dataset.accountExpiry));modal('调整账号有效期',`<form id="expiryForm"><p>${escape(user.display_name||user.username)}</p><label>到期时间（留空为长期有效）<input type="datetime-local" name="expires_at"></label><div class="form-footer"><button type="submit">保存</button></div></form>`);if(user.expires_at){const dt=new Date(user.expires_at);dt.setMinutes(dt.getMinutes()-dt.getTimezoneOffset());$('[name=expires_at]').value=dt.toISOString().slice(0,16);}bindForm('#expiryForm',async data=>{await api('/accounts/'+user.id,{method:'PATCH',body:{is_active:user.is_active,expires_at:data.expires_at?new Date(data.expires_at).toISOString():null}});notify('有效期已更新');await permissionsModal();});}
  });
}
async function publicationModal() {
  const preview=await api(`/issues/${state.issue.id}/publication-preview`),snapshot=preview.snapshot;
  modal('外部视角预览与发布',`<p class="form-note">以下为自动脱敏后的候选内容。可继续编辑标题、说明、设备信息与堆栈，取消不需要对外展示的上报。发布后外部人员只会看到此版本，后续内部变更需要重新发布。</p><form id="publishForm"><div class="form-grid"><label class="wide">对外标题<input name="title" value="${escape(snapshot.title)}" required maxlength="500"></label><label>应用名称<input name="application_name" value="${escape(snapshot.application_name)}" required maxlength="120"></label><label>异常类型<input name="exception_type" value="${escape(snapshot.exception_type)}" maxlength="100"></label><label class="wide">对外说明<textarea name="external_note" maxlength="50000">${escape(snapshot.external_note)}</textarea></label></div><h3 style="margin-top:24px">选择对外上报（${snapshot.reports.length} 条）</h3>${snapshot.reports.map((r,index)=>`<section class="review-report" data-review-report="${index}"><label class="check"><input type="checkbox" checked data-selected>发布 ${r.source_report_id?'#'+escape(r.source_report_id):'手动上报'} · ${date(r.occurred_at)}</label><div class="form-grid"><label>应用版本<input data-field="app_version" value="${escape(r.app_version)}" maxlength="255"></label><label>设备机型<input data-field="device" value="${escape(r.device)}" maxlength="255"></label><label>系统版本<input data-field="os_version" value="${escape(r.os_version)}" maxlength="255"></label></div><textarea data-field="stack_text" maxlength="600000" spellcheck="false">${escape(r.stack_text)}</textarea></section>`).join('')}<div class="form-footer"><label class="check"><input type="checkbox" required>已检查对外内容和敏感信息</label><button type="submit">发布此版本</button></div></form>`);
  bindForm('#publishForm',async data=>{const reports=$$('[data-review-report]').filter(el=>$('[data-selected]',el).checked).map(el=>{const row={...snapshot.reports[Number(el.dataset.reviewReport)]};$$('[data-field]',el).forEach(input=>row[input.dataset.field]=input.value);return row;});const body={source_version:preview.source_version,snapshot:{...snapshot,title:data.title,application_name:data.application_name,exception_type:data.exception_type,external_note:data.external_note,reports}};await api(`/issues/${state.issue.id}/publication`,{method:'POST',body});closeModal();notify('审核后的资料已发布，外部授权账号可以查看');await openIssue(state.issue.id,false);});
}
function passwordModal() {
  modal('修改密码',`<form id="passwordForm"><div class="form-grid"><label>原密码<input name="old_password" type="password" required autocomplete="current-password"></label><label>新密码<input name="new_password" type="password" required minlength="8" maxlength="72" autocomplete="new-password"></label></div><div class="form-footer"><button type="submit">保存并重新登录</button></div></form>`);
  bindForm('#passwordForm',async body=>{await request('/auth/password',{method:'PUT',body});localStorage.removeItem('token');location.href='/login';});
}

async function init() {
  state.user=await request('/auth/me');
  if(state.user.account_type==='external'&&!state.external){location.replace('/bugly/external'+location.search);return;}
  $('#me').textContent=state.user.display_name||state.user.username;
  $('.brand').href=state.external?'/bugly/external':'/bugly';
  $('#platformLink').hidden=state.user.account_type==='external';
  if(state.external){document.title='Bugly 资料浏览';$('#pageTitle').textContent='问题资料，清晰可查';$('#pageDescription').textContent='浏览已授权、已发布的问题资料，查看异常堆栈与设备信息。';$('#eyebrow').textContent='BUGLY / SHARED INSIGHTS';$('#thirdMetricLabel').textContent='当前访问权限';$('#coverageNote').textContent='仅展示已审核发布的上报资料。后续资料更新由维护者审核后发布。';$$('.internal-filter').forEach(el=>el.hidden=true);}
  else{$('#internalActions').hidden=false;$('#applicationsBtn').hidden=state.user.role!=='admin';$('#permissionsBtn').hidden=state.user.role!=='admin';}
  await refreshApplications();restoreFilters();await loadList(false);
  const issueId=Number(new URLSearchParams(location.search).get('issue'));if(issueId)await openIssue(issueId,false);
}
$('#modalClose').addEventListener('click',closeModal);
$('#modal').addEventListener('click',event=>{if(event.target===$('#modal')){const rect=$('#modal').getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeModal();}});
$('#filterForm').addEventListener('submit',safe(async event=>{event.preventDefault();state.page=1;await loadList();}));
$('#resetBtn').addEventListener('click',safe(async()=>{$('#filterForm').reset();state.page=1;await loadList();}));
$('#previousPage').addEventListener('click',safe(async()=>{state.page--;await loadList();}));
$('#nextPage').addEventListener('click',safe(async()=>{state.page++;await loadList();}));
$('#pageSize').addEventListener('change',safe(async()=>{state.size=Number($('#pageSize').value);state.page=1;await loadList();}));
$('#newIssueBtn').addEventListener('click',safe(newIssue));$('#importBtn').addEventListener('click',safe(importFiles));
$('#applicationsBtn').addEventListener('click',safe(applicationsModal));$('#permissionsBtn').addEventListener('click',safe(permissionsModal));$('#historyBtn').addEventListener('click',safe(historyModal));$('#passwordBtn').addEventListener('click',passwordModal);
$('#logoutBtn').addEventListener('click',safe(async()=>{await request('/auth/logout',{method:'POST'});localStorage.removeItem('token');location.href='/login';}));
document.addEventListener('click',safe(async event=>{
  const globalReport=event.target.closest('[data-open-global-report]');if(globalReport){await openIssue(Number(globalReport.dataset.issueId));await openReport(Number(globalReport.dataset.openGlobalReport));return;}
  const issue=event.target.closest('[data-open-issue]');if(issue){await openIssue(Number(issue.dataset.openIssue));return;}
  const report=event.target.closest('[data-open-report]');if(report){await openReport(Number(report.dataset.openReport));return;}
  const action=event.target.closest('[data-action]')?.dataset.action;
  if(action==='back')await back();if(action==='edit')await editIssue();if(action==='new-report')newReport();if(action==='publish')await publicationModal();
  if(action==='withdraw'){await api(`/issues/${state.issue.id}/publication`,{method:'DELETE'});notify('对外发布已撤回');await openIssue(state.issue.id,false);}
  if(action==='report-prev'){state.reportPage--;await loadReports();}if(action==='report-next'){state.reportPage++;await loadReports();}
  if(action==='stack-full'){$('#stackText').classList.toggle('full');event.target.textContent=$('#stackText').classList.contains('full')?'收起':'展开';}
  if(action==='stack-wrap')$('#stackText').classList.toggle('wrap');
  if(action==='stack-copy'){if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(state.stack);else{const input=document.createElement('textarea');input.value=state.stack;document.body.append(input);input.select();document.execCommand('copy');input.remove();}notify('堆栈已复制');}
}));
window.addEventListener('popstate',safe(async()=>{restoreFilters();const id=Number(new URLSearchParams(location.search).get('issue'));if(id)await openIssue(id,false);else{state.issue=null;$('#detailView').hidden=true;$('#listView').hidden=false;await loadList(false);}}));
safe(init)();
