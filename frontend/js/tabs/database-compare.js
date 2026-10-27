import { api } from '../api.js';

const BASE = '/api/database-compare';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const terminal = new Set(['succeeded', 'partial', 'failed', 'cancelled', 'interrupted']);
const statusNames = {queued:'排队中', running:'比较中', succeeded:'已完成', partial:'部分完成', failed:'失败', cancelled:'已取消', interrupted:'已中断'};
const conclusions = {equal:'比较范围内一致', different:'发现差异', different_in_covered_scope:'已覆盖范围内发现差异', inconclusive:'无法判定完整一致性', unknown:'尚未完成'};
const kindNames = {table_added:'新增表', table_removed:'删除表', column_added:'新增字段', column_removed:'删除字段', column_changed:'字段属性变化', definition_changed:'定义变化', object_added:'新增对象', object_removed:'删除对象', object_changed:'对象变化', row_added:'增加记录', row_removed:'减少记录', value_changed:'值修改', value_added:'新增字段值', value_removed:'删除字段值', metadata_changed:'元数据变化', database_added:'新增数据库', database_removed:'删除数据库', side_row:'单侧完整数据', file_error:'文件读取失败', table_error:'表比较失败'};
let initialized = false, oldInput = null, newInput = null, previewData = null;
let selected = null, timer = null, taskPage = 1, diffPage = 1, tableCache = [], resultKey = '', busy = false, pairsDirty = false, diffRequest = 0;
let historyItems = [], historyRequest = 0, finishedTotal = 0;
const checkedTasks = new Set();
const fileSets = {old:[], new:[]};
const filePrefix = side => side === 'old' ? 'dcOld' : 'dcNew';
const databaseFileIcon = `<svg viewBox="0 0 40 48" fill="none" aria-hidden="true"><path d="M8 2h17l9 9v31a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4V6a4 4 0 0 1 4-4Z" fill="currentColor" fill-opacity=".08" stroke="currentColor" stroke-width="1.5"/><path d="M25 2v9h9" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><ellipse cx="19" cy="22" rx="8" ry="3.5" stroke="currentColor" stroke-width="1.5"/><path d="M11 22v10c0 2 3.6 3.5 8 3.5s8-1.5 8-3.5V22M11 27c0 2 3.6 3.5 8 3.5s8-1.5 8-3.5" stroke="currentColor" stroke-width="1.5"/></svg>`;
const uploadIcon = `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 16V4m-4 4 4-4 4 4M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function fileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const unit = bytes < 1024 ** 2 ? 'KB' : bytes < 1024 ** 3 ? 'MB' : 'GB';
  const amount = bytes / (unit === 'KB' ? 1024 : unit === 'MB' ? 1024 ** 2 : 1024 ** 3);
  return `${new Intl.NumberFormat('zh-CN', {maximumFractionDigits:1}).format(amount)} ${unit}`;
}

function uploadPanel(side) {
  const prefix = filePrefix(side), old = side === 'old';
  return `<div class="dc-upload" data-side="${side}">
    <div class="dc-upload-heading"><span class="dc-side-marker">${old?'A':'B'}</span><div><span class="dc-eyebrow">${old?'旧版本':'新版本'}</span><span class="dc-side-subtitle">${old?'比较基准':'比较目标'}</span></div><span class="dc-upload-format">SQLite</span></div>
    <label for="${prefix}Label">版本名称</label><input id="${prefix}Label" maxlength="120" placeholder="例如 ${old?'4.04.00':'4.04.01'}">
    <div class="dc-files-heading"><span>数据库快照</span><span id="${prefix}FileCount" class="dc-file-count">支持多文件</span></div>
    <input id="${prefix}Files" type="file" multiple accept=".db,.sqlite,.sqlite3" aria-label="${old?'旧版本':'新版本'}数据库文件" hidden>
    <div id="${prefix}FileList" class="dc-file-list" aria-live="polite" aria-label="${old?'旧版本':'新版本'}已选文件"></div>
    <div id="${prefix}Dropzone" class="dc-dropzone" role="group" aria-label="${old?'旧版本':'新版本'}数据库拖放区域">
      <span class="dc-drop-icon">${uploadIcon}</span><div class="dc-drop-copy"><strong>将数据库快照拖到这里</strong><span>.db / .sqlite / .sqlite3</span></div>
      <button id="${prefix}Choose" type="button" class="secondary dc-file-choose dc-file-control" aria-controls="${prefix}Files">选择文件<span aria-hidden="true"> ↗</span></button>
    </div>
    <div id="${prefix}FileSummary" class="dc-file-summary">选择停止写入后导出的完整快照</div>
  </div>`;
}

function renderFiles(side) {
  const prefix = filePrefix(side), files = fileSets[side];
  const uploaded = !!(side === 'old' ? oldInput : newInput);
  $(prefix+'FileList').innerHTML = files.map((file, index) => {
    const dot = file.name.lastIndexOf('.'), stem = dot > 0 ? file.name.slice(0, dot) : file.name;
    const extension = dot > 0 ? file.name.slice(dot+1).toUpperCase() : 'FILE';
    return `<div class="dc-file-card"><span class="dc-file-icon">${databaseFileIcon}</span><div class="dc-file-info"><div class="dc-file-title" title="${esc(file.name)}"><span class="dc-file-name">${esc(stem)}</span><span class="dc-file-extension">.${esc(file.name.slice(dot+1))}</span></div><div class="dc-file-meta"><span class="dc-file-type">${esc(extension)}</span><span>${fileSize(file.size)}</span><span class="dc-file-state" data-uploaded="${uploaded}">${uploaded?'已上传':'待上传'}</span></div></div><button type="button" class="secondary dc-file-remove dc-file-control" data-remove-file="${index}" aria-label="移除 ${esc(file.name)}" title="移除文件"><svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m5 5 6 6m0-6-6 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button></div>`;
  }).join('');
  $(prefix+'Dropzone').classList.toggle('dc-has-files', !!files.length);
  $(prefix+'Choose').innerHTML = `${files.length?'添加文件':'选择文件'}<span aria-hidden="true"> ${files.length?'+':'↗'}</span>`;
  $(prefix+'FileCount').textContent = files.length ? `${String(files.length).padStart(2,'0')} 个文件` : '支持多文件';
  $(prefix+'FileSummary').textContent = files.length ? `共 ${fileSize(files.reduce((sum,file) => sum+file.size, 0))} · ${uploaded?'快照已上传，可继续预览或比较':'文件已选择，点击“上传并预览”继续'}` : '选择停止写入后导出的完整快照';
  document.querySelectorAll(`.dc-upload[data-side="${side}"] .dc-file-control`).forEach(button => {button.disabled = busy;});
}

function setFiles(side, files) {
  if (busy) return;
  if (files.some(file => !/\.(db|sqlite|sqlite3)$/i.test(file.name))) { message('请选择 .db、.sqlite 或 .sqlite3 数据库文件', true); return; }
  if (files.length > 16) { message('每侧最多选择 16 个数据库文件', true); return; }
  const transfer = new DataTransfer();
  files.forEach(file => transfer.items.add(file));
  $(filePrefix(side)+'Files').files = transfer.files;
  fileSets[side] = files;
  if (side === 'old') oldInput = null; else newInput = null;
  previewData = null; pairsDirty = false;
  $('dcStart').disabled = true; $('dcPreviewArea').innerHTML = '';
  renderFiles(side);
  message('');
}

function addFiles(side, incoming) {
  const files = new Map(fileSets[side].map(file => [file.name.toLowerCase(), file]));
  incoming.forEach(file => files.set(file.name.toLowerCase(), file));
  setFiles(side, [...files.values()]);
}

function bindFilePicker(side) {
  const prefix = filePrefix(side), input = $(prefix+'Files'), zone = $(prefix+'Dropzone');
  let dragDepth = 0;
  input.onchange = () => {
    const incoming = [...input.files];
    if (incoming.length) addFiles(side, incoming); else setFiles(side, []);
    // Restore the accepted selection if an invalid addition was rejected.
    const transfer = new DataTransfer();
    fileSets[side].forEach(file => transfer.items.add(file));
    input.files = transfer.files;
  };
  $(prefix+'Choose').onclick = () => {if (!busy) input.click();};
  $(prefix+'FileList').onclick = event => {
    const button = event.target.closest('[data-remove-file]');
    if (button) setFiles(side, fileSets[side].filter((_,index) => index !== Number(button.dataset.removeFile)));
  };
  zone.ondragenter = event => {event.preventDefault(); if (!busy) {dragDepth++; zone.classList.add('dc-drag-over');}};
  zone.ondragover = event => {event.preventDefault(); event.dataTransfer.dropEffect = busy ? 'none' : 'copy';};
  zone.ondragleave = event => {event.preventDefault(); if (--dragDepth <= 0) {dragDepth = 0; zone.classList.remove('dc-drag-over');}};
  zone.ondrop = event => {event.preventDefault(); dragDepth = 0; zone.classList.remove('dc-drag-over'); if (!busy) addFiles(side, [...event.dataTransfer.files]);};
}

async function json(url, options) { return (await api(BASE + url, options)).json(); }
function message(text, error = false) { $('dcMessage').textContent = text; $('dcMessage').style.color = error ? 'var(--danger, #b91c1c)' : 'var(--text-muted, #57534e)'; }
function reportError(error) { message(error.message || String(error), true); }

const detailLabels = {key:'记录定位',match_mode:'匹配方式',count:'变化条数',old_count:'旧版出现次数',new_count:'新版出现次数',columns:'参与比较字段',field:'字段',property:'属性',object_type:'对象类型',object_name:'对象名称',review_required:'需人工复核',error:'错误',side:'数据来源',reason:'说明',sql:'建表语句',type:'类型',notnull:'非空约束',dflt_value:'默认值',pk:'主键',cid:'字段序号',name:'名称',internal:'内部表',indexes:'索引',foreign_keys:'外键'};
const typeLabels = {null:'空值',text:'文本',integer:'整数',real:'实数',blob:'二进制'};
const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const isTyped = value => value && typeof value === 'object' && typeof value.type === 'string' && has(value,'value') && has(typeLabels,value.type);
function valueHtml(value, present = true) {
  if (!present) return '<span class="dc-empty">不存在</span>';
  if (value === null) return '<span class="dc-type">NULL · 空值</span>';
  if (isTyped(value)) {
    const text = value.type === 'null' ? 'NULL' : value.value === '' ? '（空字符串）' : String(value.value);
    return `<span class="dc-type">${esc(typeLabels[value.type])}${value.type==='blob'?` · ${esc(value.length)} 字节 · Base64`:''}</span><div class="dc-value">${esc(text)}</div>${value.hex?`<small class="dc-note">精确值：${esc(value.hex)}</small>`:''}`;
  }
  if (Array.isArray(value)) return value.length ? `<ol class="dc-list">${value.map(v=>`<li>${valueHtml(v)}</li>`).join('')}</ol>` : '<span class="dc-empty">空列表</span>';
  if (typeof value === 'object') return Object.keys(value).length ? `<dl class="dc-properties">${Object.entries(value).map(([k,v])=>`<div><dt>${esc(detailLabels[k]||k)}</dt><dd>${valueHtml(v)}</dd></div>`).join('')}</dl>` : '<span class="dc-empty">无属性</span>';
  return `<div class="dc-value">${esc(value === '' ? '（空字符串）' : typeof value === 'boolean' ? (value?'是':'否') : value)}</div>`;
}
function differenceHtml(d) {
  const info = d.details;
  let locator = '';
  if (info.key) {
    const table = tableCache.find(t=>t.database===d.database && t.table===d.table);
    const keys = Array.isArray(info.key) ? Object.fromEntries(info.key.map((v,i)=>[table?.key?.[i]||`键 ${i+1}`,v])) : info.key;
    locator = `<div class="dc-location"><strong>记录定位</strong>${valueHtml(keys)}</div>`;
  } else if (info.match_mode === 'row_multiset' || info.row) {
    locator = '<p class="dc-note">按整行内容及出现次数定位；没有可靠主键，不推断记录的修改关系。</p>';
  }
  let comparison = '';
  if (has(info,'old') || has(info,'new')) {
    const a = info.old, b = info.new;
    const record = v => v && typeof v === 'object' && !Array.isArray(v) && !isTyped(v);
    const fields = !info.field && (record(a) || record(b)) ? [...new Set([...Object.keys(a||{}),...Object.keys(b||{})])] : null;
    const rows = fields ? fields.map(k=>({label:d.scope==='data'?k:detailLabels[k]||k,a:a?.[k],b:b?.[k],ap:!!a&&has(a,k),bp:!!b&&has(b,k)})) : [{label:info.field||detailLabels[info.property]||info.property||'内容',a,b,ap:has(info,'old')&&!['value_added','column_added','table_added','object_added'].includes(d.kind),bp:has(info,'new')&&!['value_removed','column_removed','table_removed','object_removed'].includes(d.kind)}];
    comparison = `<div class="dc-compare-scroll"><table class="dc-values"><thead><tr><th>字段 / 属性</th><th>旧值 · ${esc(selected?.old_label||'旧版本')}</th><th>新值 · ${esc(selected?.new_label||'新版本')}</th></tr></thead><tbody>${rows.map(r=>`<tr class="${r.ap!==r.bp||JSON.stringify(r.a)!==JSON.stringify(r.b)?'dc-changed':''}"><th scope="row">${esc(r.label)}</th><td>${valueHtml(r.a,r.ap)}</td><td>${valueHtml(r.b,r.bp)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  if (info.row) comparison += `<h4>${info.side==='old'?'旧版本':info.side==='new'?'新版本':'记录'}字段内容</h4>${valueHtml(info.row)}`;
  const extra = Object.fromEntries(Object.entries(info).filter(([k])=>!['old','new','key','row','field','property'].includes(k)));
  if (extra.match_mode) extra.match_mode = extra.match_mode==='key'?'按主键 / 业务键':'整行及次数';
  if (extra.side) extra.side = extra.side==='old'?'旧版本':'新版本';
  return `${locator}${comparison}${Object.keys(extra).length?`<div class="dc-detail-meta">${valueHtml(extra)}</div>`:''}${d.preview_only?'<p class="dc-note">长内容已节选，完整值请下载完整 JSON 报告查看。</p>':''}`;
}

function initialize() {
  if (initialized) return;
  initialized = true;
  $('databaseCompareRoot').innerHTML = `
    <link rel="stylesheet" href="/static/css/database-compare.css?v=20261027-files-1">
    <div class="dc-flex" style="justify-content:space-between"><h2 style="margin:0">数据库对比</h2><button id="dcRefresh" class="secondary">刷新任务</button></div>
    <p class="dc-note">比较旧版本到新版本的结构和全部数据。请上传停止写入后导出的完整 SQLite 快照，同组文件应来自同一业务时点。</p>
    <div class="dc-panel">
      <div class="dc-section-title"><span class="dc-step">01</span><h3>新建对比</h3><span class="dc-note">选择版本与数据库快照</span></div>
      <div class="dc-grid">
        ${uploadPanel('old')}${uploadPanel('new')}
      </div>
      <div class="dc-flex dc-upload-actions"><button id="dcPreview">上传并预览</button><button id="dcStart" disabled>开始比较</button><span id="dcMessage" role="status" aria-live="polite"></span></div>
      <div id="dcPreviewArea"></div>
    </div>
    <div class="dc-panel"><div class="dc-section-title"><span class="dc-step">02</span><h3>历史任务</h3><span class="dc-note">查看进度与比较报告</span></div>
      <div class="dc-flex dc-history-actions"><button id="dcDeleteSelected" class="secondary dc-danger" disabled>删除所选</button><button id="dcCleanup" class="secondary dc-danger" disabled>清理全部已结束历史</button><span class="dc-note">删除时一并清理结果、导出报告及不再使用的快照，排队中和比较中的任务会保留。</span></div>
      <div id="dcHistory" class="dc-scroll"></div><div class="dc-flex" style="margin-top:10px"><button id="dcHistoryPrev" class="secondary">上一页</button><span id="dcHistoryPage"></span><button id="dcHistoryNext" class="secondary">下一页</button></div></div>
    <div id="dcTaskPanel" class="dc-panel" hidden>
      <div class="dc-section-title"><span class="dc-step">03</span><h3>对比结果</h3></div><div id="dcTaskSummary" aria-live="polite"></div>
      <div class="dc-flex"><button id="dcCancel" class="secondary">取消任务</button><button id="dcRerun" class="secondary">按相同输入重跑</button><button id="dcExcel" class="secondary">导出 Excel 报告包</button><button id="dcJson" class="secondary">导出完整 JSON</button></div>
      <div id="dcTableSummary"></div>
      <div id="dcDiffControls" class="dc-flex" style="margin-top:16px" hidden>
        <label>表 <select id="dcFilterTable"><option value="">全部数据库和表</option></select></label>
        <label>范围 <select id="dcFilterScope"><option value="">全部</option><option value="structure">结构</option><option value="data">数据</option><option value="metadata">数据库元数据</option><option value="file">文件</option><option value="coverage">失败项</option><option value="evidence">单侧数据证据</option></select></label>
        <label>类型 <select id="dcFilterKind"><option value="">全部</option>${Object.entries(kindNames).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>
      </div>
      <div id="dcDifferences" style="margin-top:12px"></div>
      <div id="dcDiffPager" class="dc-flex" style="margin-top:12px" hidden><button id="dcDiffPrev" class="secondary">上一页</button><span id="dcDiffPage"></span><button id="dcDiffNext" class="secondary">下一页</button></div>
    </div>`;
  $('dcPreview').onclick = () => withBusy(makePreview);
  $('dcStart').onclick = () => withBusy(startTask);
  $('dcRefresh').onclick = () => loadHistory().catch(reportError);
  for (const side of ['old','new']) bindFilePicker(side);
  $('dcHistoryPrev').onclick = () => { taskPage = Math.max(1, taskPage - 1); loadHistory().catch(reportError); };
  $('dcHistoryNext').onclick = () => { taskPage++; loadHistory().catch(reportError); };
  $('dcHistory').onclick = (event) => {
    const remove = event.target.closest('[data-delete-task]');
    if (remove) { withBusy(() => deleteHistory([remove.dataset.deleteTask])); return; }
    const button = event.target.closest('[data-task]');
    if (button) selectTask(button.dataset.task).catch(reportError);
  };
  $('dcHistory').onchange = (event) => {
    if (event.target.id === 'dcSelectPage') {
      historyItems.filter(t => terminal.has(t.status)).forEach(t => event.target.checked ? checkedTasks.add(t.id) : checkedTasks.delete(t.id));
    } else if (event.target.matches('[data-check-task]')) {
      const id = event.target.dataset.checkTask;
      if (event.target.checked) checkedTasks.add(id); else checkedTasks.delete(id);
    }
    updateHistoryActions();
  };
  $('dcDeleteSelected').onclick = () => withBusy(() => deleteHistory([...checkedTasks]));
  $('dcCleanup').onclick = () => withBusy(() => deleteHistory(null));
  $('dcCancel').onclick = async () => { try { await json(`/tasks/${selected.id}/cancel`, {method:'POST'}); await pollTask(); } catch(e) { reportError(e); } };
  $('dcRerun').onclick = () => withBusy(async () => { const t = selected; const created = await json('/tasks', {method:'POST',body:{old_input_id:t.old_input_id,new_input_id:t.new_input_id,old_label:t.old_label,new_label:t.new_label,pairs:t.pairs,rules:t.rules}}); await selectTask(created.id); await loadHistory(); });
  $('dcExcel').onclick = () => withBusy(() => download('excel'));
  $('dcJson').onclick = () => withBusy(() => download('json'));
  for (const id of ['dcFilterTable','dcFilterScope','dcFilterKind']) $(id).onchange = () => {diffPage = 1; loadDifferences().catch(reportError);};
  $('dcDiffPrev').onclick = () => {diffPage = Math.max(1,diffPage-1);loadDifferences().catch(reportError);};
  $('dcDiffNext').onclick = () => {diffPage++;loadDifferences().catch(reportError);};
}

async function withBusy(action) {
  if (busy) return;
  busy = true;
  const ids = ['dcPreview','dcStart','dcRerun','dcExcel','dcJson','dcOldFiles','dcNewFiles'];
  const previous = ids.map(id => $(id).disabled);
  ids.forEach(id => {$(id).disabled = true;});
  for (const side of ['old','new']) renderFiles(side);
  updateHistoryActions();
  try { await action(); } catch (error) { reportError(error); }
  finally { busy = false; ids.forEach((id,i)=>{$(id).disabled = previous[i];}); $('dcStart').disabled = !previewData || pairsDirty; if (selected) updateActions(); updateHistoryActions(); for (const side of ['old','new']) renderFiles(side); }
}

async function upload(id) {
  const files = [...$(id).files];
  if (!files.length) throw new Error('请分别选择旧版本和新版本数据库文件');
  const form = new FormData();
  files.forEach(file => form.append('files',file));
  return json('/input-sets',{method:'POST',body:form});
}

function collectPairs() {
  return [...document.querySelectorAll('#dcPreviewArea [data-pair]')].map(row => ({name:row.querySelector('[data-name]').value.trim(),old:row.querySelector('[data-old]').value || null,new:row.querySelector('[data-new]').value || null}));
}

function collectRules() {
  const tables = {};
  for (const row of document.querySelectorAll('#dcPreviewArea [data-rule]')) {
    const mode = row.querySelector('select').value;
    if (mode === 'row_multiset') tables[row.dataset.rule] = {match_mode:mode};
    if (mode === 'key') tables[row.dataset.rule] = {match_mode:mode,key:row.querySelector('input').value.split(',').map(v=>v.trim()).filter(Boolean)};
  }
  return {schema_version:1,strict_comparison:true,tables};
}

async function makePreview() {
  message('正在上传和检查数据库…');
  if (!oldInput) oldInput = await upload('dcOldFiles');
  if (!newInput) newInput = await upload('dcNewFiles');
  const pairs = previewData ? collectPairs() : [];
  previewData = null;
  const data = await json('/previews',{method:'POST',body:{old_input_id:oldInput.id,new_input_id:newInput.id,pairs}});
  previewData = data;
  pairsDirty = false;
  const options = (input,value) => '<option value="">无（单侧新增或删除）</option>' + input.files.map(f=>`<option value="${esc(f.file.name)}" ${f.file.name===value?'selected':''}>${esc(f.file.name)}</option>`).join('');
  const pairRow = (p) => `<tr data-pair><td><input data-name value="${esc(p.name)}"></td><td><select data-old>${options(oldInput,p.old)}</select></td><td><select data-new>${options(newInput,p.new)}</select></td><td><button type="button" class="secondary" data-remove-pair>移除此对应关系</button></td></tr>`;
  $('dcPreviewArea').innerHTML = `<h3>数据库对应关系</h3><p class="dc-note">修改对应关系后请重新预览，确保两侧全部文件各使用一次。文件更名时可合并对应关系，并移除多余行。</p><div class="dc-scroll"><table><thead><tr><th>报告名称</th><th>旧版本文件</th><th>新版本文件</th><th>操作</th></tr></thead><tbody id="dcPairsBody">${data.pairs.map(pairRow).join('')}</tbody></table></div><button type="button" id="dcAddPair" class="secondary">添加对应关系</button>
    <h3>表覆盖与记录匹配（${data.tables.length} 张表，含内部表）</h3><p class="dc-note">自动模式使用可验证的声明主键；无可靠键时保留重复次数，不推断修改关系。若版本间 ID 已重建，请选择整行比较或指定稳定业务键。</p>
    <div class="dc-scroll"><table><thead><tr><th>数据库 / 表</th><th>旧 / 新记录数</th><th>匹配策略</th><th>业务键（逗号分隔）</th></tr></thead><tbody>${data.tables.map(t=>`<tr data-rule="${esc(t.database+'.'+t.table)}"><td>${esc(t.database)} / ${esc(t.table)} ${t.internal?'（内部）':''}</td><td>${t.old_rows ?? '—'} / ${t.new_rows ?? '—'}</td><td><select aria-label="${esc(t.table)} 匹配策略"><option value="auto">自动${t.key.length?'：'+esc(t.key.join(', ')):'：整行及次数'}</option><option value="row_multiset">整行及次数</option><option value="key">指定业务键</option></select></td><td><input aria-label="${esc(t.table)} 业务键" placeholder="例如 CODE 或 ID,TYPE"></td></tr>`).join('')}</tbody></table></div>`;
  const dirty = () => { pairsDirty = true; $('dcStart').disabled = true; message('对应关系已修改，请点击“上传并预览”重新确认'); };
  $('dcPairsBody').onchange = dirty;
  $('dcPairsBody').onclick = (event) => { if (event.target.closest('[data-remove-pair]')) {event.target.closest('[data-pair]').remove(); dirty();} };
  $('dcAddPair').onclick = () => { $('dcPairsBody').insertAdjacentHTML('beforeend',pairRow({name:'',old:null,new:null})); dirty(); };
  message(`预检查完成：${data.pairs.length} 组数据库，${data.tables.length} 张表。`);
}

async function startTask() {
  if (!previewData) return;
  message('正在创建比较任务…');
  const item = await json('/tasks',{method:'POST',body:{old_input_id:oldInput.id,new_input_id:newInput.id,old_label:$('dcOldLabel').value.trim()||'旧版本',new_label:$('dcNewLabel').value.trim()||'新版本',pairs:collectPairs(),rules:collectRules()}});
  message('任务已创建，正在后台比较。');
  await selectTask(item.id);
  await loadHistory();
}

async function loadHistory() {
  const request = ++historyRequest;
  const page = taskPage;
  const data = await json(`/tasks?page=${taskPage}&page_size=10`);
  if (page !== taskPage || request !== historyRequest) return;
  const lastPage = Math.max(1, Math.ceil(data.total / 10));
  if (taskPage > lastPage) { taskPage = lastPage; return loadHistory(); }
  historyItems = data.items;
  finishedTotal = data.finished_total;
  for (const id of checkedTasks) if (!historyItems.some(t => t.id === id && terminal.has(t.status))) checkedTasks.delete(id);
  $('dcHistory').innerHTML = data.items.length ? `<table><thead><tr><th><input id="dcSelectPage" type="checkbox" aria-label="选择本页已结束任务"></th><th>版本</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${data.items.map(t=>`<tr><td><input type="checkbox" data-check-task="${esc(t.id)}" aria-label="选择 ${esc(t.old_label)} 到 ${esc(t.new_label)}" ${terminal.has(t.status)?'':'disabled'}></td><td>${esc(t.old_label)} → ${esc(t.new_label)}</td><td><span class="dc-badge" data-status="${esc(t.status)}">${esc(statusNames[t.status]||t.status)}</span></td><td>${esc(t.created_at)}</td><td><div class="dc-flex dc-row-actions"><button class="secondary" data-task="${esc(t.id)}">查看</button><button class="secondary dc-danger" data-delete-task="${esc(t.id)}" ${terminal.has(t.status)?'':'disabled title="请先取消并等待任务结束"'}>删除</button></div></td></tr>`).join('')}</tbody></table>` : '<p class="dc-note">暂无比较任务。</p>';
  $('dcHistoryPage').textContent = `第 ${taskPage} 页，共 ${data.total} 个任务`;
  $('dcHistoryPrev').disabled = taskPage <= 1;
  $('dcHistoryNext').disabled = taskPage * 10 >= data.total;
  updateHistoryActions();
}

function updateHistoryActions() {
  $('dcDeleteSelected').disabled = busy || !checkedTasks.size;
  $('dcDeleteSelected').textContent = checkedTasks.size ? `删除所选（${checkedTasks.size}）` : '删除所选';
  $('dcCleanup').disabled = busy || !finishedTotal;
  const eligible = historyItems.filter(t => terminal.has(t.status));
  const selectPage = $('dcSelectPage');
  if (selectPage) {
    selectPage.disabled = busy || !eligible.length;
    selectPage.checked = !!eligible.length && eligible.every(t => checkedTasks.has(t.id));
    selectPage.indeterminate = eligible.some(t => checkedTasks.has(t.id)) && !selectPage.checked;
  }
  document.querySelectorAll('#dcHistory [data-check-task], #dcHistory [data-delete-task]').forEach(el => {
    const id = el.dataset.checkTask || el.dataset.deleteTask;
    el.disabled = busy || !eligible.some(t => t.id === id);
    if (el.matches('[data-check-task]')) el.checked = checkedTasks.has(id);
  });
}

async function deleteHistory(ids) {
  if (ids && !ids.length) return;
  const scope = ids ? `所选 ${ids.length} 条` : '当前可见范围内全部已结束的';
  if (!window.confirm(`确定删除${scope}对比历史吗？\n结果、导出报告和不再被其他任务使用的上传快照将被永久清理，删除后无法恢复。`)) return;
  message('正在清理对比历史和文件…');
  const data = ids?.length === 1
    ? await json(`/tasks/${ids[0]}`, {method:'DELETE'})
    : await json('/tasks/cleanup', {method:'POST', body:ids ? {task_ids:ids} : {}});
  if (data.deleted_task_ids.includes(selected?.id)) {
    clearTimeout(timer);
    selected = null; resultKey = ''; tableCache = []; diffRequest++;
    $('dcTaskPanel').hidden = true;
    $('dcTaskSummary').innerHTML = ''; $('dcTableSummary').innerHTML = ''; $('dcDifferences').innerHTML = '';
  }
  if ([oldInput?.id, newInput?.id].some(id => data.deleted_input_ids.includes(id))) {
    oldInput = null; newInput = null; previewData = null; pairsDirty = false;
    $('dcPreviewArea').innerHTML = ''; $('dcStart').disabled = true;
  }
  checkedTasks.clear();
  await loadHistory();
  message(`已清理 ${data.deleted_count} 条对比历史及相关文件。`);
}

function updateActions() {
  const finished = terminal.has(selected.status);
  $('dcCancel').disabled = finished || selected.cancel_requested;
  $('dcRerun').disabled = !finished || busy;
  $('dcExcel').disabled = !finished || busy || selected.status === 'cancelled' && !selected.started_at;
  $('dcJson').disabled = $('dcExcel').disabled;
}

async function selectTask(id) {
  clearTimeout(timer);
  selected = {id};
  resultKey = ''; diffPage = 1; tableCache = [];
  $('dcTaskPanel').hidden = false;
  $('dcTaskSummary').textContent = '正在加载任务…';
  for (const button of ['dcCancel','dcRerun','dcExcel','dcJson']) $(button).disabled = true;
  $('dcTableSummary').innerHTML = '';
  $('dcDifferences').innerHTML = '';
  $('dcDiffControls').hidden = true;
  $('dcDiffPager').hidden = true;
  $('dcFilterScope').value = ''; $('dcFilterKind').value = '';
  await pollTask();
}

async function pollTask() {
  clearTimeout(timer);
  const id = selected?.id;
  if (!id) return;
  const item = await json(`/tasks/${id}`);
  if (selected?.id !== id) return;
  selected = item;
  const s = item.summary || {}, p = item.progress || {};
  const errors = s.errors || [];
  $('dcTaskSummary').innerHTML = `<h3>${esc(item.old_label)} → ${esc(item.new_label)}</h3><p><span class="dc-badge" data-status="${esc(item.status)}">${esc(statusNames[item.status]||item.status)}</span> · ${esc(conclusions[s.conclusion]||'尚未完成')} · 已处理 ${p.tables_completed ?? s.tables_completed ?? 0} / ${p.tables_total ?? s.tables_total ?? '—'} 张表</p>
    <div class="dc-metrics">${[['结构明细',s.counts ? (s.counts.structure || 0) : undefined],['增加记录',s.rows_added],['减少记录',s.rows_removed],['修改记录',s.rows_modified],['修改字段',s.fields_modified]].map(([label,value])=>`<div class="dc-metric">${label}<br><strong>${value ?? '—'}</strong></div>`).join('')}</div>
    ${item.error?`<p style="color:#b91c1c">${esc(item.error)}</p>`:''}
    ${errors.length||s.limitations?.length?`<details><summary>覆盖限制与错误信息</summary>${valueHtml({错误信息:errors,覆盖限制:s.limitations||[]})}</details>`:''}
    <p class="dc-note">任务编号 ${esc(id)}${item.cancel_requested?' · 已请求取消':''}</p>`;
  updateActions();
  if (!terminal.has(item.status)) {
    timer = setTimeout(() => { if (!$('databaseCompareRoot').closest('section')?.classList.contains('hidden')) pollTask().catch(reportError); },2000);
    return;
  }
  if (resultKey !== id + item.status) {
    resultKey = id + item.status;
    try {
      const tables = (await json(`/tasks/${id}/tables`)).items;
      if (selected?.id !== id) return;
      tableCache = tables;
      $('dcTableSummary').innerHTML = `<details><summary>查看 ${tableCache.length} 张表的覆盖与匹配方式</summary><div class="dc-scroll"><table><thead><tr><th>数据库 / 表</th><th>状态</th><th>匹配方式</th><th>说明</th></tr></thead><tbody>${tableCache.map(t=>`<tr><td>${esc(t.database)} / ${esc(t.table)} ${t.internal?'（内部）':''}</td><td>${esc({compared:'已比较',partial:'部分完成',failed:'失败',unsupported:'不支持'}[t.status]||t.status)}</td><td>${esc({key:'主键 / 业务键',row_multiset:'整行及次数'}[t.match_mode]||t.match_mode)} ${esc(t.key?.join(', '))}</td><td>${esc(t.error||t.notes?.join('；')||'')}</td></tr>`).join('')}</tbody></table></div></details>`;
      $('dcFilterTable').innerHTML = '<option value="">全部数据库和表</option>' + tableCache.map((t,i)=>`<option value="${i}">${esc(t.database)} / ${esc(t.table)}</option>`).join('');
      $('dcDiffControls').hidden = false; $('dcDiffPager').hidden = false;
      await loadDifferences();
    } catch (e) { if (selected?.id === id) $('dcDifferences').textContent = e.message; }
    await loadHistory();
  }
}

async function loadDifferences() {
  const request = ++diffRequest;
  const id = selected?.id;
  if (!id) return;
  const params = new URLSearchParams({page:diffPage,page_size:30,preview:true,scope:$('dcFilterScope').value,kind:$('dcFilterKind').value});
  const table = $('dcFilterTable').value;
  if (table !== '' && tableCache[Number(table)]) {params.set('database',tableCache[Number(table)].database);params.set('table',tableCache[Number(table)].table);}
  const data = await json(`/tasks/${id}/differences?${params}`);
  if (selected?.id !== id || request !== diffRequest) return;
  $('dcDifferences').innerHTML = data.items.length ? `<div class="dc-diff-list">${data.items.map(d=>`<article class="dc-diff-item"><details><summary><span class="dc-diff-heading"><span class="dc-badge">${esc(kindNames[d.kind]||d.kind)}</span><strong>${esc(d.table||d.database)}</strong><span class="dc-note">${esc(d.details.field||d.details.property||'')}${d.details.count?' · '+esc(d.details.count)+' 条':''}</span></span><span class="dc-note">${esc(d.database)} · #${d.id}</span><span class="dc-expand">查看差异</span></summary><div class="dc-diff-body">${differenceHtml(d)}</div></details></article>`).join('')}</div>` : '<div class="dc-empty-state">当前筛选范围没有差异明细。<p class="dc-note">完整结论以任务状态和覆盖说明为准。</p></div>';

  $('dcDiffPage').textContent = `第 ${diffPage} 页，共 ${data.total} 条明细`;
  $('dcDiffPrev').disabled = diffPage <= 1;
  $('dcDiffNext').disabled = diffPage * 30 >= data.total;
}

async function download(format) {
  const id = selected.id;
  message('正在生成报告…');
  const info = await json(`/tasks/${id}/exports`,{method:'POST',body:{format}});
  const response = await api(info.download_url);
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement('a'); a.href = url; a.download = `数据库对比报告-${id}.${format==='excel'?'zip':'json'}`; a.click();
  setTimeout(()=>URL.revokeObjectURL(url),10000);
  message(format==='excel'?'报告包包含 Excel 与完整 JSON；长值节选可在 JSON 中复核。':'完整 JSON 报告已生成。');
}

async function activate() {
  initialize();
  await loadHistory();
  if (selected) await pollTask();
}

window.OmniQADatabaseCompareTab = {activate};
