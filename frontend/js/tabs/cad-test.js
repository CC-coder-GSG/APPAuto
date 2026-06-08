// CAD 测试统计 —— 版本分组卡片视图。
// 数据：统计表(board) → 版本(version) + 自定义列(column) + 条目(item) → 记录(item×version) → 附件(CAD/截图)。
// 依赖全局：api()、token、window.OmniQAUtils、window.OmniQAPreview、window.showMessage。

const U = () => window.OmniQAUtils || {};
const esc = (s) => (U().escapeHtml ? U().escapeHtml(String(s ?? '')) : String(s ?? ''));
const toast = (m, t = 'success') => window.showMessage && window.showMessage(m, t);
const authHeaders = () => ({ Authorization: 'Bearer ' + (localStorage.getItem('token') || window.token || '') });
// 视频/流式访问：JWT 走 query token，<video src> 无法设置请求头。
const streamSrc = (a) => `${a.stream_url}?token=${encodeURIComponent(localStorage.getItem('token') || window.token || '')}`;
const splitAtts = (rec) => {
  const all = rec ? rec.attachments : [];
  return {
    cad: all.filter((a) => a.kind === 'cad'),
    video: all.filter((a) => a.is_video || a.kind === 'video'),
    shot: all.filter((a) => a.kind === 'screenshot' && !a.is_video),
  };
};

const state = {
  boards: [],
  boardId: null,
  board: null, // detail
  versionId: null,
};

async function japi(url, opt) {
  const r = await api(url, opt);
  if (!r.ok) {
    let msg = '请求失败';
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  try { return await r.json(); } catch { return {}; }
}

export async function activate() {
  const root = document.getElementById('cadTestRoot');
  if (!root) return;
  try {
    state.boards = await japi('/api/cad/boards');
  } catch (e) {
    root.innerHTML = `<div class="muted" style="padding:20px;">加载失败：${esc(e.message)}</div>`;
    return;
  }
  if (!state.boardId && state.boards.length) state.boardId = state.boards[0].id;
  await loadBoard();
}

async function loadBoard() {
  if (state.boardId) {
    try {
      state.board = await japi(`/api/cad/boards/${state.boardId}`);
      if (!state.versionId || !state.board.versions.some((v) => v.id === state.versionId)) {
        state.versionId = state.board.versions[0]?.id || null;
      }
    } catch (e) {
      state.board = null;
      toast(e.message, 'error');
    }
  } else {
    state.board = null;
  }
  render();
}

function render() {
  const root = document.getElementById('cadTestRoot');
  if (!root) return;
  root.innerHTML = `
    <div class="row" style="justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
      <div class="row" style="gap:10px; align-items:center; flex-wrap:wrap;">
        <h2 style="margin:0;">CAD测试统计</h2>
        <select id="cadBoardSelect" style="min-width:200px;">
          ${state.boards.map((b) => `<option value="${b.id}" ${b.id === state.boardId ? 'selected' : ''}>${esc(b.name)}</option>`).join('')}
        </select>
        <button class="secondary" onclick="window.OmniQACadTab._newBoard()">+ 新建统计表</button>
        ${state.board ? `<button class="secondary" onclick="window.OmniQACadTab._renameBoard()">重命名</button>
        <button class="secondary" onclick="window.OmniQACadTab._delBoard()" style="color:#dc2626;">删除表</button>` : ''}
      </div>
    </div>
    ${state.board ? renderBoardBody() : `<div class="muted" style="padding:30px; text-align:center;">还没有统计表，点击「+ 新建统计表」开始。</div>`}
  `;
  const sel = document.getElementById('cadBoardSelect');
  if (sel) sel.onchange = async () => { state.boardId = Number(sel.value); state.versionId = null; await loadBoard(); };
  const vsel = document.getElementById('cadVersionSelect');
  if (vsel) vsel.onchange = () => { state.versionId = Number(vsel.value); render(); };
  hydrateScreenshots(root);
}

function renderBoardBody() {
  const b = state.board;
  if (!b.versions.length) {
    return `<div class="card" style="margin-top:14px; background:#f8fafc;">
      <div class="muted">该统计表还没有版本。</div>
      <button style="margin-top:10px;" onclick="window.OmniQACadTab._newVersion()">+ 新增版本</button>
    </div>`;
  }
  const ver = b.versions.find((v) => v.id === state.versionId) || b.versions[0];
  const items = b.items;
  return `
    <div class="row" style="margin-top:14px; gap:10px; align-items:center; flex-wrap:wrap; background:#f8fafc; padding:12px; border-radius:10px; border:1px solid #e2e8f0;">
      <label style="color:#475569; font-size:13px;">版本</label>
      <select id="cadVersionSelect" style="min-width:240px;">
        ${b.versions.map((v) => `<option value="${v.id}" ${v.id === ver.id ? 'selected' : ''}>${esc(v.name)}</option>`).join('')}
      </select>
      <button class="secondary" onclick="window.OmniQACadTab._newVersion()">+ 版本</button>
      <button class="secondary" onclick="window.OmniQACadTab._renameVersion(${ver.id})">重命名版本</button>
      <button class="secondary" onclick="window.OmniQACadTab._delVersion(${ver.id})" style="color:#dc2626;">删版本</button>
      <span style="flex:1;"></span>
      <button class="secondary" onclick="window.OmniQACadTab._manageColumns()">自定义列</button>
      <button onclick="window.OmniQACadTab._newItem()">+ 新增条目</button>
    </div>
    <div style="margin-top:14px; display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:14px;">
      ${items.length ? items.map((it) => renderCard(it, ver.id)).join('') : '<div class="muted" style="padding:20px;">该统计表还没有条目，点击「+ 新增条目」。</div>'}
    </div>`;
}

function renderCard(item, versionId) {
  const rec = state.board.records[`${item.id}:${versionId}`];
  const bug = item.bug;
  const hasAbnormal = rec && rec.abnormal_count > 0;
  const headBug = item.zentao_bug_id
    ? `<span class="row" style="gap:4px; align-items:center;">
         <span class="badge" style="background:#eff6ff; color:#1d4ed8;">Bug ${item.zentao_bug_id}</span>
         ${U().renderPreviewBtn ? U().renderPreviewBtn('bug', item.zentao_bug_id) : ''}
         ${bug && bug.zentao_bug_url ? `<a class="qa-ext-link" href="${esc(bug.zentao_bug_url)}" target="_blank" rel="noopener" title="${esc(bug.zentao_bug_title || '')}">跳转</a>` : ''}
       </span>`
    : '';
  const { cad: cadFiles, video: videos, shot: shots } = splitAtts(rec);
  const customs = (state.board.columns || [])
    .map((c) => {
      const val = rec && rec.custom_values ? rec.custom_values[String(c.id)] : '';
      if (!val) return '';
      return `<div style="font-size:12px; color:#475569;"><b>${esc(c.name)}：</b>${esc(val)}</div>`;
    })
    .join('');

  return `
    <div class="card" style="padding:14px; border:1px solid ${hasAbnormal ? '#fecaca' : '#e2e8f0'}; background:#fff; display:flex; flex-direction:column; gap:8px;">
      <div class="row" style="justify-content:space-between; align-items:center;">
        <div class="row" style="gap:6px; align-items:center;">
          <span style="font-weight:700; color:#0f172a;">#${item.seq ?? '-'}</span>
          ${item.title ? `<span style="color:#334155;">${esc(item.title)}</span>` : ''}
        </div>
        ${headBug}
      </div>
      <div class="row" style="gap:14px; align-items:center;">
        <span class="badge" style="background:#f0fdf4; color:#16a34a; border:1px solid #bbf7d0;">正常 ${rec ? rec.normal_count : 0}</span>
        <span class="badge" style="background:${hasAbnormal ? '#fef2f2' : '#f1f5f9'}; color:${hasAbnormal ? '#dc2626' : '#94a3b8'}; border:1px solid ${hasAbnormal ? '#fecaca' : '#e2e8f0'};">异常 ${rec ? rec.abnormal_count : 0}</span>
      </div>
      ${rec && rec.description ? `<div style="font-size:13px; color:#334155; white-space:pre-wrap; line-height:1.55; background:#f8fafc; border-left:3px solid ${hasAbnormal ? '#f87171' : '#94a3b8'}; border-radius:6px; padding:8px 10px;"><span style="color:#64748b; font-size:11px;">问题说明</span><br>${esc(rec.description)}</div>` : ''}
      ${(shots.length || videos.length || cadFiles.length) ? `<div class="row" style="flex-wrap:wrap; gap:8px; align-items:center;">
        ${shots.map((a) => `<img data-cad-src="${a.download_url}" style="width:56px;height:56px;object-fit:cover;border-radius:6px;border:1px solid #e2e8f0;cursor:pointer;" onclick="window.OmniQACadTab._viewShot(${a.id})" title="查看图片：${esc(a.original_name)}">`).join('')}
        ${videos.map((a) => `<button class="secondary" style="padding:4px 10px; font-size:12px;" onclick="window.OmniQACadTab._viewVideo(${a.id})" title="${esc(a.original_name)}">▶ 预览视频</button>`).join('')}
        ${cadFiles.map((a) => `<button class="secondary" style="padding:4px 10px; font-size:12px;" onclick="window.OmniQACadTab._download(${a.id})" title="下载：${esc(a.original_name)}">⬇ ${esc(a.original_name.length > 16 ? a.original_name.slice(0, 16) + '…' : a.original_name)}</button>`).join('')}
      </div>` : ''}
      ${customs}
      <div class="row" style="justify-content:flex-end; gap:6px; margin-top:2px;">
        <button class="secondary" style="padding:3px 8px; font-size:12px;" onclick="window.OmniQACadTab._editItem(${item.id})">编辑条目</button>
        <button style="padding:3px 8px; font-size:12px;" onclick="window.OmniQACadTab._editRecord(${item.id}, ${versionId})">填写/编辑</button>
      </div>
    </div>`;
}

// 截图需要带 JWT 拉取，转成 objectURL 注入 <img>。
function hydrateScreenshots(container) {
  container.querySelectorAll('img[data-cad-src]').forEach(async (img) => {
    if (img.dataset.loaded) return;
    img.dataset.loaded = '1';
    try {
      const r = await fetch(img.getAttribute('data-cad-src'), { headers: authHeaders() });
      if (!r.ok) return;
      img.src = URL.createObjectURL(await r.blob());
    } catch {}
  });
}

// ---------------------------------------------------------------- board ops
async function _newBoard() {
  const name = prompt('统计表名称（如：40311版本测试统计表）');
  if (!name || !name.trim()) return;
  try {
    const res = await japi('/api/cad/boards', { method: 'POST', body: { name: name.trim() } });
    state.boardId = res.id; state.versionId = null;
    state.boards = await japi('/api/cad/boards');
    await loadBoard();
    toast('已创建统计表');
  } catch (e) { toast(e.message, 'error'); }
}
async function _renameBoard() {
  const name = prompt('重命名统计表', state.board?.name || '');
  if (!name || !name.trim()) return;
  try { await japi(`/api/cad/boards/${state.boardId}`, { method: 'PUT', body: { name: name.trim() } });
    state.boards = await japi('/api/cad/boards'); await loadBoard(); toast('已重命名'); } catch (e) { toast(e.message, 'error'); }
}
async function _delBoard() {
  if (!confirm(`确定删除统计表「${state.board?.name}」？该表下所有版本/条目/记录/附件将一并删除。`)) return;
  try { await japi(`/api/cad/boards/${state.boardId}`, { method: 'DELETE' });
    state.boardId = null; state.board = null; state.versionId = null;
    state.boards = await japi('/api/cad/boards');
    if (state.boards.length) state.boardId = state.boards[0].id;
    await loadBoard(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// -------------------------------------------------------------- version ops
async function _newVersion() {
  const name = prompt('版本名称（如：4.0.3.11.260603(40311022)版本测试）');
  if (!name || !name.trim()) return;
  try { const res = await japi(`/api/cad/boards/${state.boardId}/versions`, { method: 'POST', body: { name: name.trim() } });
    state.versionId = res.id; await loadBoard(); toast('已新增版本'); } catch (e) { toast(e.message, 'error'); }
}
async function _renameVersion(id) {
  const cur = state.board.versions.find((v) => v.id === id);
  const name = prompt('重命名版本', cur?.name || '');
  if (!name || !name.trim()) return;
  try { await japi(`/api/cad/versions/${id}`, { method: 'PUT', body: { name: name.trim() } }); await loadBoard(); toast('已重命名'); } catch (e) { toast(e.message, 'error'); }
}
async function _delVersion(id) {
  if (!confirm('删除该版本？该版本下的记录与附件将一并删除。')) return;
  try { await japi(`/api/cad/versions/${id}`, { method: 'DELETE' }); state.versionId = null; await loadBoard(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// --------------------------------------------------------------- column ops
function _manageColumns() {
  const cols = state.board.columns || [];
  const body = `
    <div style="display:flex; flex-direction:column; gap:8px;">
      ${cols.length ? cols.map((c) => `
        <div class="row" data-col="${c.id}" style="gap:8px; align-items:center;">
          <input value="${esc(c.name)}" data-colname="${c.id}" style="flex:1;">
          <button class="secondary" onclick="window.OmniQACadTab._renameColumn(${c.id})">改名</button>
          <button class="secondary" style="color:#dc2626;" onclick="window.OmniQACadTab._delColumn(${c.id})">删除</button>
        </div>`).join('') : '<div class="muted">还没有自定义列。</div>'}
    </div>
    <div class="row" style="margin-top:12px; gap:8px;">
      <input id="cadNewColName" placeholder="新列名称（如：华测-测地通）" style="flex:1;">
      <button onclick="window.OmniQACadTab._addColumn()">+ 添加列</button>
    </div>`;
  openModal('自定义备注列', body);
}
async function _addColumn() {
  const inp = document.getElementById('cadNewColName');
  const name = inp && inp.value.trim();
  if (!name) return;
  try { await japi(`/api/cad/boards/${state.boardId}/columns`, { method: 'POST', body: { name } }); await loadBoard(); _manageColumns(); toast('已添加列'); } catch (e) { toast(e.message, 'error'); }
}
async function _renameColumn(id) {
  const inp = document.querySelector(`input[data-colname="${id}"]`);
  const name = inp && inp.value.trim();
  if (!name) return;
  try { await japi(`/api/cad/columns/${id}`, { method: 'PUT', body: { name } }); await loadBoard(); toast('已改名'); } catch (e) { toast(e.message, 'error'); }
}
async function _delColumn(id) {
  if (!confirm('删除该自定义列？已填写的对应值将不再显示。')) return;
  try { await japi(`/api/cad/columns/${id}`, { method: 'DELETE' }); await loadBoard(); _manageColumns(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// ----------------------------------------------------------------- item ops
function _newItem() { openItemModal(null); }
function _editItem(id) { openItemModal(state.board.items.find((i) => i.id === id)); }
function openItemModal(item) {
  const body = `
    <div style="display:flex; flex-direction:column; gap:10px;">
      <label>序号<input id="cadItemSeq" type="number" value="${item?.seq ?? ''}" style="width:100%;" placeholder="留空自动顺延"></label>
      <label>标题/图纸主名（可选）<input id="cadItemTitle" value="${esc(item?.title || '')}" style="width:100%;"></label>
      <label>关联禅道 Bug ID（可选，填数字）<input id="cadItemBug" type="number" value="${item?.zentao_bug_id ?? ''}" style="width:100%;" placeholder="如 29614"></label>
    </div>
    <div class="row" style="justify-content:space-between; margin-top:14px;">
      ${item ? `<button class="secondary" style="color:#dc2626;" onclick="window.OmniQACadTab._delItem(${item.id})">删除条目</button>` : '<span></span>'}
      <button onclick="window.OmniQACadTab._saveItem(${item ? item.id : 'null'})">保存</button>
    </div>`;
  openModal(item ? '编辑条目' : '新增条目', body);
}
async function _saveItem(id) {
  const seq = document.getElementById('cadItemSeq').value;
  const title = document.getElementById('cadItemTitle').value;
  const bug = document.getElementById('cadItemBug').value;
  const payload = { seq: seq === '' ? null : Number(seq), title: title || null, zentao_bug_id: bug === '' ? null : Number(bug) };
  try {
    if (id && id !== 'null') await japi(`/api/cad/items/${id}`, { method: 'PUT', body: payload });
    else await japi(`/api/cad/boards/${state.boardId}/items`, { method: 'POST', body: payload });
    closeModal(); await loadBoard(); toast('已保存');
  } catch (e) { toast(e.message, 'error'); }
}
async function _delItem(id) {
  if (!confirm('删除该条目？其所有版本下的记录与附件将一并删除。')) return;
  try { await japi(`/api/cad/items/${id}`, { method: 'DELETE' }); closeModal(); await loadBoard(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// --------------------------------------------------------------- record edit
function _editRecord(itemId, versionId) {
  const item = state.board.items.find((i) => i.id === itemId);
  const rec = state.board.records[`${itemId}:${versionId}`];
  const cols = state.board.columns || [];
  const { cad: cadFiles, video: videos, shot: shots } = splitAtts(rec);
  const body = `
    <div style="display:flex; flex-direction:column; gap:12px;">
      <div class="muted">条目 #${item?.seq ?? '-'} ${item?.zentao_bug_id ? `· Bug ${item.zentao_bug_id}` : ''}</div>
      <div class="row" style="gap:12px;">
        <label style="flex:1;">正常数<input id="cadRecNormal" type="number" min="0" value="${rec ? rec.normal_count : 0}" style="width:100%;"></label>
        <label style="flex:1;">异常数<input id="cadRecAbnormal" type="number" min="0" value="${rec ? rec.abnormal_count : 0}" style="width:100%;"></label>
      </div>
      <label>问题说明 / 异常说明
        <textarea id="cadRecDesc" rows="4" style="width:100%;">${esc(rec ? rec.description || '' : '')}</textarea>
      </label>
      ${cols.length ? `<div style="display:flex; flex-direction:column; gap:8px;">
        <div style="font-size:12px; color:#64748b;">自定义列</div>
        ${cols.map((c) => `<label>${esc(c.name)}<input data-cv="${c.id}" value="${esc(rec && rec.custom_values ? (rec.custom_values[String(c.id)] || '') : '')}" style="width:100%;"></label>`).join('')}
      </div>` : ''}
      <div style="border-top:1px dashed #e2e8f0; padding-top:10px;">
        <div class="row" style="justify-content:space-between; align-items:center;">
          <b style="font-size:13px;">CAD 文件</b>
          <label class="secondary" style="cursor:pointer; padding:4px 10px; border:1px solid #e2e8f0; border-radius:8px;">+ 上传CAD
            <input type="file" accept=".dwg,.dxf,.dwf,.dgn,.dwt" style="display:none;" onchange="window.OmniQACadTab._upload(${itemId},${versionId},'cad',this)">
          </label>
        </div>
        <div id="cadRecCadList" style="display:flex; flex-wrap:wrap; gap:6px; margin-top:6px;">
          ${cadFiles.map((a) => attChip(a)).join('') || '<span class="muted" style="font-size:12px;">无</span>'}
        </div>
      </div>
      <div>
        <div class="row" style="justify-content:space-between; align-items:center;">
          <b style="font-size:13px;">截图</b>
          <label class="secondary" style="cursor:pointer; padding:4px 10px; border:1px solid #e2e8f0; border-radius:8px;">+ 上传截图
            <input type="file" accept="image/*" style="display:none;" onchange="window.OmniQACadTab._upload(${itemId},${versionId},'screenshot',this)">
          </label>
        </div>
        <div id="cadRecShotList" style="display:flex; flex-wrap:wrap; gap:6px; margin-top:6px;">
          ${shots.map((a) => `<span style="position:relative; display:inline-block;">
            <img data-cad-src="${a.download_url}" style="width:64px;height:64px;object-fit:cover;border-radius:6px;border:1px solid #e2e8f0;">
            <button onclick="window.OmniQACadTab._delAtt(${a.id},${itemId},${versionId})" title="删除" style="position:absolute;top:-6px;right:-6px;width:18px;height:18px;border-radius:50%;border:none;background:#dc2626;color:#fff;cursor:pointer;line-height:1;">×</button>
          </span>`).join('') || '<span class="muted" style="font-size:12px;">无</span>'}
        </div>
      </div>
      <div>
        <div class="row" style="justify-content:space-between; align-items:center;">
          <b style="font-size:13px;">视频（支持在线播放）</b>
          <label class="secondary" style="cursor:pointer; padding:4px 10px; border:1px solid #e2e8f0; border-radius:8px;">+ 上传视频
            <input type="file" accept="video/*" style="display:none;" onchange="window.OmniQACadTab._upload(${itemId},${versionId},'video',this)">
          </label>
        </div>
        <div id="cadRecVideoList" style="display:flex; flex-wrap:wrap; gap:10px; margin-top:6px;">
          ${videos.map((a) => `<span style="position:relative; display:inline-block;">
            <video src="${streamSrc(a)}" controls preload="metadata" playsinline style="width:240px; max-height:160px; border-radius:8px; border:1px solid #e2e8f0; background:#000;"></video>
            <button onclick="window.OmniQACadTab._delAtt(${a.id},${itemId},${versionId})" title="删除" style="position:absolute;top:-6px;right:-6px;width:18px;height:18px;border-radius:50%;border:none;background:#dc2626;color:#fff;cursor:pointer;line-height:1;">×</button>
          </span>`).join('') || '<span class="muted" style="font-size:12px;">无</span>'}
        </div>
      </div>
    </div>
    <div class="row" style="justify-content:flex-end; margin-top:14px;">
      <button onclick="window.OmniQACadTab._saveRecord(${itemId},${versionId})">保存记录</button>
    </div>`;
  openModal('填写记录', body);
  hydrateScreenshots(document.getElementById('cadModalBody'));
}
function attChip(a) {
  return `<span class="row" style="gap:4px; align-items:center; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:3px 8px;">
    <a class="qa-ext-link" href="javascript:void(0)" onclick="window.OmniQACadTab._download(${a.id})">📐 ${esc(a.original_name)}</a>
    <button onclick="window.OmniQACadTab._delAtt(${a.id})" title="删除" style="border:none;background:none;color:#dc2626;cursor:pointer;">×</button>
  </span>`;
}
async function _saveRecord(itemId, versionId) {
  const normal = Number(document.getElementById('cadRecNormal').value || 0);
  const abnormal = Number(document.getElementById('cadRecAbnormal').value || 0);
  const desc = document.getElementById('cadRecDesc').value;
  const custom = {};
  document.querySelectorAll('#cadModalBody input[data-cv]').forEach((inp) => {
    const v = inp.value.trim();
    if (v) custom[inp.getAttribute('data-cv')] = v;
  });
  try {
    await japi('/api/cad/records', { method: 'POST', body: { item_id: itemId, version_id: versionId, normal_count: normal, abnormal_count: abnormal, description: desc || null, custom_values: custom } });
    closeModal(); await loadBoard(); toast('已保存');
  } catch (e) { toast(e.message, 'error'); }
}
async function _upload(itemId, versionId, kind, inputEl) {
  const file = inputEl.files && inputEl.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(`/api/cad/records/attachment?item_id=${itemId}&version_id=${versionId}&kind=${kind}`, {
      method: 'POST', headers: authHeaders(), body: fd,
    });
    if (!r.ok) { let m = '上传失败'; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
    await loadBoard();
    _editRecord(itemId, versionId); // 重新打开刷新附件区
    toast('上传成功');
  } catch (e) { toast(e.message, 'error'); }
}
async function _delAtt(attId, itemId, versionId) {
  if (!confirm('删除该附件？')) return;
  try {
    await japi(`/api/cad/attachments/${attId}`, { method: 'DELETE' });
    await loadBoard();
    if (itemId != null && versionId != null) _editRecord(itemId, versionId);
    else closeModal();
    toast('已删除');
  } catch (e) { toast(e.message, 'error'); }
}
async function _download(attId) {
  try {
    const r = await fetch(`/api/cad/attachments/${attId}/download`, { headers: authHeaders() });
    if (!r.ok) throw new Error('下载失败');
    const blob = await r.blob();
    const dispo = r.headers.get('Content-Disposition') || '';
    const m = /filename\*?=(?:UTF-8'')?\"?([^\";]+)/i.exec(dispo);
    const name = m ? decodeURIComponent(m[1]) : `cad_${attId}`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } catch (e) { toast(e.message, 'error'); }
}
async function _viewShot(attId) {
  try {
    const r = await fetch(`/api/cad/attachments/${attId}/download`, { headers: authHeaders() });
    if (!r.ok) return;
    const url = URL.createObjectURL(await r.blob());
    const ov = document.createElement('div');
    ov.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,.8); z-index:11000; display:flex; align-items:center; justify-content:center; cursor:zoom-out;';
    ov.onclick = () => { ov.remove(); URL.revokeObjectURL(url); };
    ov.innerHTML = `<img src="${url}" style="max-width:94vw; max-height:94vh; border-radius:8px;">`;
    document.body.appendChild(ov);
  } catch {}
}
function _viewVideo(attId) {
  // 视频走流式端点（Range），不预下整段；点击空白处关闭，点击播放器本身不关闭。
  const token = encodeURIComponent(localStorage.getItem('token') || window.token || '');
  const url = `/api/cad/attachments/${attId}/stream?token=${token}`;
  const ov = document.createElement('div');
  ov.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,.85); z-index:11000; display:flex; align-items:center; justify-content:center; cursor:zoom-out;';
  ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  ov.innerHTML = `<video src="${url}" controls autoplay playsinline style="max-width:94vw; max-height:94vh; border-radius:8px; background:#000;"></video>`;
  document.body.appendChild(ov);
}

// ------------------------------------------------------------------- modal
function openModal(title, bodyHtml) {
  closeModal();
  const m = document.createElement('div');
  m.id = 'cadModal';
  m.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,.42); z-index:10000; display:flex; align-items:center; justify-content:center;';
  m.onclick = (e) => { if (e.target === m) closeModal(); };
  m.innerHTML = `
    <div style="width:min(560px,94vw); max-height:90vh; overflow:auto; background:#fff; border-radius:14px; box-shadow:0 16px 40px rgba(0,0,0,.22); padding:20px;">
      <div class="row" style="justify-content:space-between; align-items:center; margin-bottom:12px;">
        <h3 style="margin:0; color:#0f172a;">${esc(title)}</h3>
        <button class="secondary" onclick="window.OmniQACadTab._close()">关闭</button>
      </div>
      <div id="cadModalBody">${bodyHtml}</div>
    </div>`;
  document.body.appendChild(m);
}
function closeModal() { const m = document.getElementById('cadModal'); if (m) m.remove(); }

window.OmniQACadTab = {
  activate,
  _newBoard, _renameBoard, _delBoard,
  _newVersion, _renameVersion, _delVersion,
  _manageColumns, _addColumn, _renameColumn, _delColumn,
  _newItem, _editItem, _saveItem, _delItem,
  _editRecord, _saveRecord, _upload, _delAtt, _download, _viewShot, _viewVideo,
  _close: closeModal,
};
