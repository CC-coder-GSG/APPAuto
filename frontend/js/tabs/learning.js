import { api } from '../api.js';

let learningSseBound = false;

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function currentUserId() {
  return Number(window.currentUser?.id || 0);
}

function isAdmin() {
  return window.currentUser?.role === 'admin';
}

const TYPE_LABEL = { single: '单选', multi: '多选', judge: '判断', blank: '填空', short: '简答' };
const STATUS_LABEL = { drafting: '出题中', answering: '答题中', grading: '批改中', published: '已公示' };

// ============================ 入口 ============================
export async function loadLearningTab() {
  bindLearningSSE();
  document.getElementById('learningDetailArea').innerHTML = '';
  await loadTopicList();
}

async function loadTopicList() {
  const box = document.getElementById('learningTopicList');
  if (!box) return;
  box.innerHTML = '<div class="muted">加载中…</div>';
  try {
    const topics = await (await api('/learning/topics')).json();
    if (!topics.length) {
      box.innerHTML = '<div class="lc-empty">还没有学习主题，点击右上角「新建学习主题」开始。</div>';
      return;
    }
    box.innerHTML = `<div class="lc-topic-list">${topics.map((t) => {
      const la = t.latest_assessment;
      const aBadge = la
        ? `<span class="badge" style="background:#eef2ff;color:#4338ca;">第${la.round_no}轮·${STATUS_LABEL[la.status] || la.status}</span>`
        : '<span class="badge">暂无考核</span>';
      return `<div class="lc-topic" onclick="OmniQALearningTab.openTopic(${t.id})">
        <div class="row" style="justify-content:space-between; align-items:center; gap:8px;">
          <div>
            <b style="font-size:16px;">${esc(t.title)}</b>
            <div class="muted" style="margin-top:2px;">${esc(t.description || '')}</div>
          </div>
          <div class="row" style="gap:6px; align-items:center;">
            <span class="badge">📎 资料 ${t.material_count}</span>
            ${aBadge}
            <span class="muted" style="font-size:12px;">创建人 ${esc(t.created_by_name || '—')}</span>
          </div>
        </div>
      </div>`;
    }).join('')}</div>`;
  } catch (err) {
    box.innerHTML = `<div class="muted" style="color:#dc2626;">${esc(err.message || '加载失败')}</div>`;
  }
}

export async function openCreateTopic() {
  const title = prompt('请输入学习主题标题：');
  if (!title || !title.trim()) return;
  const description = prompt('主题简介（可留空）：') || '';
  try {
    await api('/learning/topics', { method: 'POST', headers: window.H, body: { title: title.trim(), description } });
    window.showMessage && window.showMessage('主题已创建', 'success');
    await loadTopicList();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '创建失败', 'error');
  }
}

// ============================ 主题详情 ============================
export async function openTopic(topicId) {
  const area = document.getElementById('learningDetailArea');
  area.innerHTML = '<div class="muted">加载中…</div>';
  try {
    const t = await (await api(`/learning/topics/${topicId}`)).json();
    const meId = currentUserId();

    const materialsHtml = (t.materials || []).map((m) => {
      const previewBtn = m.previewable
        ? `<button class="secondary" style="padding:2px 8px;font-size:12px;" onclick="OmniQALearningTab.previewMaterial(${m.id},'${m.preview_kind}','${esc(m.original_name)}')">预览</button>`
        : '';
      const delBtn = `<button class="danger" style="padding:2px 8px;font-size:12px;" onclick="OmniQALearningTab.deleteMaterial(${m.id},${topicId})">删除</button>`;
      return `<div class="lc-row">
        <span>📄 ${esc(m.original_name)} <span class="muted" style="font-size:12px;">(${(m.file_size / 1024).toFixed(0)} KB)</span></span>
        <span class="row" style="gap:6px;">
          ${previewBtn}
          <button class="secondary" style="padding:2px 8px;font-size:12px;" onclick="OmniQALearningTab.downloadMaterial(${m.id})">下载</button>
          ${delBtn}
        </span>
      </div>`;
    }).join('') || '<div class="lc-empty">暂无资料</div>';

    const assessmentsHtml = (t.assessments || []).map((a) => {
      const isAuthor = a.author_id === meId || isAdmin();
      const actions = [];
      if (a.status === 'drafting' && isAuthor) {
        actions.push(`<button class="secondary" onclick="OmniQALearningTab.openAssessmentEditor(${a.id})">继续出题</button>`);
      }
      if (a.status === 'answering' && !isAuthor) {
        actions.push(`<button onclick="OmniQALearningTab.openPaper(${a.id})">去答题</button>`);
      }
      if (a.status === 'answering' && isAuthor) {
        actions.push(`<button class="secondary" onclick="OmniQALearningTab.openGrading(${a.id})">批改/公示</button>`);
      }
      if (a.status === 'published') {
        actions.push(`<button class="secondary" onclick="OmniQALearningTab.openResults(${a.id})">看成绩</button>`);
      }
      return `<div class="lc-row" style="flex-wrap:wrap;">
        <span>第 ${a.round_no} 轮 · <span class="badge">${STATUS_LABEL[a.status] || a.status}</span>
          <span class="muted" style="font-size:12px;">出题人 ${esc(a.author_name || '—')} · ${a.question_count} 题 / ${a.total_score} 分</span></span>
        <span class="row" style="gap:6px;">${actions.join('')}</span>
      </div>`;
    }).join('') || '<div class="lc-empty">暂无考核</div>';

    const hasActive = (t.assessments || []).some((a) => a.status !== 'published');

    area.innerHTML = `
      <div class="lc-panel">
        <div class="lc-section-head" style="border-bottom:none; margin-bottom:0; padding-bottom:0;">
          <span class="lc-title" style="font-size:18px;">${esc(t.title)}</span>
          <button class="secondary" onclick="OmniQALearningTab.refreshTopic(${topicId})">刷新</button>
        </div>
        ${t.description ? `<div class="muted" style="margin-top:6px;">${esc(t.description)}</div>` : ''}
      </div>

      <div class="lc-panel">
        <div class="lc-section-head">
          <span class="lc-title">📚 知识资料</span>
          <span class="row" style="gap:6px; align-items:center;"><input type="file" id="learningMatFile_${topicId}" style="font-size:12px;">
          <button class="secondary" style="padding:2px 10px;" onclick="OmniQALearningTab.uploadMaterial(${topicId})">上传</button></span>
        </div>
        <div>${materialsHtml}</div>
      </div>

      <div class="lc-panel">
        <div class="lc-section-head">
          <span class="lc-title">📝 考核（学习周期）</span>
          ${hasActive ? '' : `<button onclick="OmniQALearningTab.createAssessment(${topicId})">+ 我来出题（新一轮）</button>`}
        </div>
        <div>${assessmentsHtml}</div>
        ${hasActive ? '<div class="muted" style="font-size:12px;margin-top:10px;">本主题有进行中的考核，公示后才能开启新一轮。</div>' : ''}
      </div>`;
  } catch (err) {
    area.innerHTML = `<div class="muted" style="color:#dc2626;">${esc(err.message || '加载失败')}</div>`;
  }
}

export function refreshTopic(topicId) { return openTopic(topicId); }

export async function uploadMaterial(topicId) {
  const input = document.getElementById(`learningMatFile_${topicId}`);
  const file = input?.files?.[0];
  if (!file) { window.showMessage && window.showMessage('请先选择文件', 'error'); return; }
  const form = new FormData();
  form.append('file', file);
  try {
    await api(`/learning/topics/${topicId}/materials`, { method: 'POST', body: form });
    window.showMessage && window.showMessage('资料上传成功', 'success');
    await openTopic(topicId);
    await loadTopicList();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '上传失败', 'error');
  }
}

export function previewMaterial(materialId, kind) {
  const url = `/learning/materials/${materialId}/preview`;
  // 通过 api 拿到带鉴权的 blob 再在新窗口打开
  api(url).then((r) => r.blob()).then((blob) => {
    const objUrl = URL.createObjectURL(blob);
    window.open(objUrl, '_blank');
    setTimeout(() => URL.revokeObjectURL(objUrl), 60000);
  }).catch((err) => window.showMessage && window.showMessage(err.message || '预览失败，请改用下载', 'error'));
}

export function downloadMaterial(materialId) {
  const url = `/learning/materials/${materialId}/download`;
  api(url).then((r) => r.blob().then((b) => ({ b, r }))).then(({ b, r }) => {
    const cd = r.headers.get('Content-Disposition') || '';
    let name = decodeURIComponent((cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/) || [])[1] || 'download');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  }).catch((err) => window.showMessage && window.showMessage(err.message || '下载失败', 'error'));
}

export async function deleteMaterial(materialId, topicId) {
  if (!confirm('确定删除该资料吗？')) return;
  try {
    await api(`/learning/materials/${materialId}`, { method: 'DELETE' });
    window.showMessage && window.showMessage('已删除', 'success');
    await openTopic(topicId);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '删除失败', 'error');
  }
}

// ============================ 出题 ============================
export async function createAssessment(topicId) {
  try {
    const res = await (await api(`/learning/topics/${topicId}/assessments`, { method: 'POST', headers: window.H, body: {} })).json();
    window.showMessage && window.showMessage('已创建考核，开始出题', 'success');
    await openAssessmentEditor(res.id);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '创建失败', 'error');
  }
}

export async function openAssessmentEditor(assessmentId) {
  const area = document.getElementById('learningDetailArea');
  let data;
  try {
    data = await (await api(`/learning/assessments/${assessmentId}/edit`)).json();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载失败', 'error');
    return;
  }
  window._learningEditQuestions = (data.questions || []).map(normalizeEditQuestion);
  if (!window._learningEditQuestions.length) window._learningEditQuestions.push(blankQuestion());
  window._learningCurrentAssessment = assessmentId;
  window._learningCurrentRound = data.round_no;
  renderEditor(assessmentId, data);
}

function blankQuestion() { return { type: 'single', prompt: '', optionsText: 'A. \nB. \nC. \nD. ', correct: 'A', score: 5 }; }

function normalizeEditQuestion(q) {
  const optionsText = (q.options || []).map((o) => `${o.key}. ${o.text}`).join('\n');
  let correct = '';
  if (q.type === 'single' || q.type === 'judge') correct = q.answer == null ? '' : String(q.answer);
  else if (q.type === 'multi') correct = (q.answer || []).join(',');
  else if (q.type === 'blank') correct = (q.answer || []).join(',');
  else if (q.type === 'short') correct = q.answer == null ? '' : String(q.answer);
  return { type: q.type, prompt: q.prompt, optionsText, correct, score: q.score };
}

function renderEditor(assessmentId, meta) {
  const area = document.getElementById('learningDetailArea');
  const qs = window._learningEditQuestions;
  const rows = qs.map((q, i) => editorRow(q, i)).join('');
  area.innerHTML = `
    <div class="lc-panel">
      <div class="lc-section-head">
        <span class="lc-title">出题（第 ${meta.round_no} 轮）</span>
        <span class="muted">题型：单选/多选/判断/填空/简答</span>
      </div>
      <div id="learningQEditor">${rows}</div>
      <div class="row" style="gap:8px; margin-top:10px;">
        <button class="secondary" onclick="OmniQALearningTab.addQuestion()">+ 添加题目</button>
        <button class="secondary" onclick="OmniQALearningTab.saveQuestions(${assessmentId})">保存草稿</button>
        <button onclick="OmniQALearningTab.saveAndPublish(${assessmentId})">提交出题（发布答题）</button>
      </div>
      <div class="muted" style="font-size:12px; margin-top:6px;">提示：选择题每行一个选项（如「A. 内容」）；多选/填空正确答案用英文逗号分隔；判断答案填 true/false。</div>
    </div>`;
}

function editorRow(q, i) {
  const typeSel = ['single', 'multi', 'judge', 'blank', 'short'].map((t) => `<option value="${t}" ${q.type === t ? 'selected' : ''}>${TYPE_LABEL[t]}</option>`).join('');
  const needOptions = q.type === 'single' || q.type === 'multi';
  const optionsBlock = needOptions
    ? `<div style="margin-top:6px;"><div class="muted" style="font-size:12px;">选项（每行一个，如「A. 内容」）</div>
        <textarea rows="4" style="width:100%;" oninput="OmniQALearningTab.updateQ(${i},'optionsText',this.value)">${esc(q.optionsText)}</textarea></div>`
    : '';
  let correctBlock = '';
  if (q.type === 'single') correctBlock = `<label>正确选项 <input style="width:80px;" value="${esc(q.correct)}" oninput="OmniQALearningTab.updateQ(${i},'correct',this.value)" placeholder="如 B"></label>`;
  else if (q.type === 'multi') correctBlock = `<label>正确选项(逗号分隔) <input style="width:140px;" value="${esc(q.correct)}" oninput="OmniQALearningTab.updateQ(${i},'correct',this.value)" placeholder="如 A,C"></label>`;
  else if (q.type === 'judge') correctBlock = `<label>正确答案 <select onchange="OmniQALearningTab.updateQ(${i},'correct',this.value)"><option value="true" ${q.correct === 'true' ? 'selected' : ''}>对</option><option value="false" ${q.correct === 'false' ? 'selected' : ''}>错</option></select></label>`;
  else if (q.type === 'blank') correctBlock = `<label>可接受答案(逗号分隔任一即对) <input style="width:240px;" value="${esc(q.correct)}" oninput="OmniQALearningTab.updateQ(${i},'correct',this.value)"></label>`;
  else if (q.type === 'short') correctBlock = `<label style="display:block;">参考答案(供你批改参考)<textarea rows="2" style="width:100%;" oninput="OmniQALearningTab.updateQ(${i},'correct',this.value)">${esc(q.correct)}</textarea></label>`;

  return `<div class="lc-q">
    <div class="row" style="gap:8px; align-items:center; flex-wrap:wrap;">
      <b>第 ${i + 1} 题</b>
      <select onchange="OmniQALearningTab.updateQ(${i},'type',this.value)">${typeSel}</select>
      <label>分值 <input type="number" min="1" style="width:70px;" value="${q.score}" oninput="OmniQALearningTab.updateQ(${i},'score',this.value)"></label>
      <button class="danger" style="padding:2px 8px;font-size:12px;" onclick="OmniQALearningTab.removeQuestion(${i})">删除</button>
    </div>
    <textarea rows="2" style="width:100%; margin-top:6px;" placeholder="题干" oninput="OmniQALearningTab.updateQ(${i},'prompt',this.value)">${esc(q.prompt)}</textarea>
    ${optionsBlock}
    <div style="margin-top:6px;">${correctBlock}</div>
  </div>`;
}

export function updateQ(i, key, val) {
  const qs = window._learningEditQuestions;
  if (!qs[i]) return;
  qs[i][key] = key === 'score' ? Number(val) : val;
  if (key === 'type') renderEditor(window._learningCurrentAssessment || 0, { round_no: window._learningCurrentRound || 1, questions: [] });
}

export function addQuestion() {
  window._learningEditQuestions.push(blankQuestion());
  rerenderEditorKeepMeta();
}
export function removeQuestion(i) {
  window._learningEditQuestions.splice(i, 1);
  if (!window._learningEditQuestions.length) window._learningEditQuestions.push(blankQuestion());
  rerenderEditorKeepMeta();
}
function rerenderEditorKeepMeta() {
  const editor = document.getElementById('learningQEditor');
  if (editor) editor.innerHTML = window._learningEditQuestions.map((q, i) => editorRow(q, i)).join('');
}

function buildPayloadQuestions() {
  return window._learningEditQuestions.map((q) => {
    const out = { type: q.type, prompt: (q.prompt || '').trim(), score: Number(q.score) || 0 };
    if (q.type === 'single' || q.type === 'multi') {
      const opts = (q.optionsText || '').split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
        const m = line.match(/^([A-Za-z0-9]+)[\.、\):：]\s*(.*)$/);
        return m ? { key: m[1].toUpperCase(), text: m[2] } : { key: line[0].toUpperCase(), text: line };
      });
      out.options = opts;
      out.answer = q.type === 'single' ? (q.correct || '').trim().toUpperCase() : (q.correct || '').split(',').map((x) => x.trim().toUpperCase()).filter(Boolean);
    } else if (q.type === 'judge') {
      out.answer = (q.correct || 'true').trim();
    } else if (q.type === 'blank') {
      out.answer = (q.correct || '').split(',').map((x) => x.trim()).filter(Boolean);
    } else if (q.type === 'short') {
      out.answer = (q.correct || '').trim();
    }
    return out;
  });
}

export async function saveQuestions(assessmentId, silent) {
  const questions = buildPayloadQuestions();
  try {
    await api(`/learning/assessments/${assessmentId}/questions`, { method: 'PUT', headers: window.H, body: { questions } });
    if (!silent) window.showMessage && window.showMessage('题目已保存', 'success');
    return true;
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '保存失败', 'error');
    return false;
  }
}

export async function saveAndPublish(assessmentId) {
  if (!confirm('确认提交出题并发布答题？发布后题目不可再修改，其余成员将收到答题通知。')) return;
  const ok = await saveQuestions(assessmentId, true);
  if (!ok) return;
  try {
    await api(`/learning/assessments/${assessmentId}/publish`, { method: 'POST', headers: window.H, body: {} });
    window.showMessage && window.showMessage('已发布，成员可开始答题', 'success');
    document.getElementById('learningDetailArea').innerHTML = '';
    await loadTopicList();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '发布失败', 'error');
  }
}

// ============================ 答题 ============================
export async function openPaper(assessmentId) {
  const area = document.getElementById('learningDetailArea');
  let paper;
  try {
    paper = await (await api(`/learning/assessments/${assessmentId}/paper`)).json();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载失败', 'error');
    return;
  }
  if (paper.already_submitted) {
    area.innerHTML = `<div class="lc-panel"><div class="muted">您已提交本次考核（状态：${STATUS_LABEL[paper.my_submission_status] || paper.my_submission_status || '已提交'}）。成绩公示后可查看排行与解析。</div></div>`;
    return;
  }
  const qHtml = (paper.questions || []).map((q, i) => paperQuestion(q, i)).join('');
  area.innerHTML = `
    <div class="lc-panel">
      <div class="lc-section-head"><span class="lc-title">答题（第 ${paper.round_no} 轮 · 共 ${paper.total_score} 分）</span></div>
      <div id="learningPaper">${qHtml}</div>
      <button onclick="OmniQALearningTab.submitPaper(${assessmentId})" style="margin-top:12px;">提交作答</button>
    </div>`;
}

function paperQuestion(q, i) {
  let body = '';
  if (q.type === 'single') {
    body = (q.options || []).map((o) => `<label style="display:block;"><input type="radio" name="lq_${q.id}" value="${esc(o.key)}"> ${esc(o.key)}. ${esc(o.text)}</label>`).join('');
  } else if (q.type === 'multi') {
    body = (q.options || []).map((o) => `<label style="display:block;"><input type="checkbox" name="lq_${q.id}" value="${esc(o.key)}"> ${esc(o.key)}. ${esc(o.text)}</label>`).join('');
  } else if (q.type === 'judge') {
    body = `<label style="margin-right:14px;"><input type="radio" name="lq_${q.id}" value="true"> 对</label><label><input type="radio" name="lq_${q.id}" value="false"> 错</label>`;
  } else if (q.type === 'blank') {
    body = `<input type="text" id="lq_${q.id}" style="width:60%;" placeholder="填写答案">`;
  } else if (q.type === 'short') {
    body = `<textarea id="lq_${q.id}" rows="3" style="width:100%;" placeholder="简答"></textarea>`;
  }
  return `<div class="lc-q">
    <div><b>第 ${i + 1} 题</b> <span class="badge">${TYPE_LABEL[q.type]}</span> <span class="muted">(${q.score} 分)</span></div>
    <div style="margin:6px 0; white-space:pre-wrap;">${esc(q.prompt)}</div>
    <div data-qid="${q.id}" data-qtype="${q.type}">${body}</div>
  </div>`;
}

export async function submitPaper(assessmentId) {
  const answers = {};
  document.querySelectorAll('#learningPaper [data-qid]').forEach((wrap) => {
    const qid = wrap.getAttribute('data-qid');
    const type = wrap.getAttribute('data-qtype');
    if (type === 'single' || type === 'judge') {
      const sel = wrap.querySelector(`input[name="lq_${qid}"]:checked`);
      if (sel) answers[qid] = sel.value;
    } else if (type === 'multi') {
      const sels = Array.from(wrap.querySelectorAll(`input[name="lq_${qid}"]:checked`)).map((el) => el.value);
      if (sels.length) answers[qid] = sels;
    } else {
      const el = document.getElementById(`lq_${qid}`);
      if (el && el.value.trim()) answers[qid] = el.value.trim();
    }
  });
  if (!confirm('确认提交作答？提交后不可修改。')) return;
  try {
    const res = await (await api(`/learning/assessments/${assessmentId}/submit`, { method: 'POST', headers: window.H, body: { answers } })).json();
    window.showMessage && window.showMessage(res.message || '已提交', 'success');
    document.getElementById('learningDetailArea').innerHTML = '';
    await loadTopicList();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '提交失败', 'error');
  }
}

// ============================ 批改 ============================
export async function openGrading(assessmentId) {
  const area = document.getElementById('learningDetailArea');
  let data;
  try {
    data = await (await api(`/learning/assessments/${assessmentId}/grading-queue`)).json();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载失败', 'error');
    return;
  }
  const items = (data.items || []).map((it) => `
    <div class="lc-q">
      <div class="row" style="justify-content:space-between;"><b>${esc(it.user_name)}</b><span class="muted">满分 ${it.max_score}</span></div>
      <div class="muted" style="margin-top:4px;">题：${esc(it.question_prompt)}</div>
      <div style="margin-top:4px;">参考答案：<span class="muted">${esc(it.reference_answer || '无')}</span></div>
      <div style="margin-top:6px; background:var(--w-raised); border:1px solid var(--w-hairline); border-radius:8px; padding:8px 10px; white-space:pre-wrap;">${esc(it.response || '（未作答）')}</div>
      <div class="row" style="gap:8px; margin-top:6px; align-items:center;">
        <label>得分 <input type="number" id="grade_${it.answer_id}" min="0" max="${it.max_score}" value="${it.current_score || 0}" style="width:80px;"></label>
        <input type="text" id="gradec_${it.answer_id}" placeholder="批注(可选)" style="width:40%;">
        <button class="secondary" onclick="OmniQALearningTab.gradeAnswer(${it.answer_id}, ${assessmentId})">赋分</button>
        ${it.graded ? '<span class="badge" style="background:#dcfce7;color:#166534;">已批</span>' : '<span class="badge" style="background:#fef9c3;color:#854d0e;">待批</span>'}
      </div>
    </div>`).join('') || '<div class="lc-empty">没有需要人工批改的简答题。</div>';

  area.innerHTML = `
    <div class="lc-panel">
      <div class="lc-section-head"><span class="lc-title">简答批改</span><span class="muted" style="font-size:13px;">待批 ${data.pending_count} / 共 ${data.total_short}</span></div>
      <div>${items}</div>
      <button onclick="OmniQALearningTab.finalize(${assessmentId})" style="margin-top:12px;">完成批改并公示成绩</button>
      <div class="muted" style="font-size:12px;margin-top:6px;">公示前需把所有简答批改完（确为 0 分也请填写批注）。</div>
    </div>`;
}

export async function gradeAnswer(answerId, assessmentId) {
  const score = Number(document.getElementById(`grade_${answerId}`)?.value || 0);
  const comment = document.getElementById(`gradec_${answerId}`)?.value || '';
  try {
    await api(`/learning/answers/${answerId}/grade`, { method: 'POST', headers: window.H, body: { score, comment } });
    window.showMessage && window.showMessage('已赋分', 'success');
    await openGrading(assessmentId);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '赋分失败', 'error');
  }
}

export async function finalize(assessmentId) {
  if (!confirm('确认完成批改并公示成绩？公示后所有人可见排行与逐题解析。')) return;
  try {
    await api(`/learning/assessments/${assessmentId}/finalize`, { method: 'POST', headers: window.H, body: {} });
    window.showMessage && window.showMessage('成绩已公示', 'success');
    await openResults(assessmentId);
    await loadTopicList();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '公示失败', 'error');
  }
}

// ============================ 成绩公示 ============================
export async function openResults(assessmentId) {
  const area = document.getElementById('learningDetailArea');
  let data;
  try {
    data = await (await api(`/learning/assessments/${assessmentId}/results`)).json();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载失败', 'error');
    return;
  }
  const qById = {};
  (data.questions || []).forEach((q) => { qById[q.id] = q; });

  const board = (data.people || []).map((p, idx) => {
    const medal = ['🥇', '🥈', '🥉'][idx] || `${idx + 1}.`;
    const detail = (p.answers || []).map((ans) => {
      const q = qById[ans.question_id] || {};
      return `<div style="border-bottom:1px dashed #e2e8f0; padding:4px 0;">
        <div class="muted" style="font-size:12px;">${TYPE_LABEL[q.type] || ''} · ${q.score || 0}分 · 得 ${ans.score} 分</div>
        <div style="white-space:pre-wrap;">${esc(q.prompt || '')}</div>
        <div>作答：<b>${esc(formatResp(ans.response))}</b></div>
        <div class="muted" style="font-size:12px;">正确答案：${esc(formatResp(q.answer))}${ans.grader_comment ? ' ｜ 批注：' + esc(ans.grader_comment) : ''}</div>
      </div>`;
    }).join('');
    return `<details class="lc-rank">
      <summary>${medal} ${esc(p.user_name)} — ${p.total_score} 分 <span class="muted" style="font-weight:400;">(客观 ${p.objective_score} + 主观 ${p.subjective_score})</span></summary>
      <div style="margin-top:8px;">${detail}</div>
    </details>`;
  }).join('') || '<div class="lc-empty">暂无人作答</div>';

  const absent = (data.absent || []).length
    ? `<div class="muted" style="margin-top:8px;">缺考：${data.absent.map((a) => esc(a.user_name)).join('、')}</div>`
    : '';

  area.innerHTML = `
    <div class="lc-panel">
      <div class="lc-section-head"><span class="lc-title">🏆 成绩公示（第 ${data.round_no} 轮 · 满分 ${data.total_score}）</span><span class="muted">出题人 ${esc(data.author_name || '—')}</span></div>
      <div>${board}</div>
      ${absent}
    </div>`;
}

function formatResp(v) {
  if (v == null) return '（空）';
  if (Array.isArray(v)) return v.join(', ');
  if (v === 'true') return '对';
  if (v === 'false') return '错';
  return String(v);
}

// ============================ SSE ============================
function bindLearningSSE() {
  if (learningSseBound) return;
  if (!window.OmniQASSE?.subscribe) return;
  window.OmniQASSE.subscribe('learning_assessment_published', () => {
    if (!document.getElementById('tab-learning')?.classList.contains('hidden')) loadTopicList();
  });
  window.OmniQASSE.subscribe('learning_assessment_published_results', () => {
    if (!document.getElementById('tab-learning')?.classList.contains('hidden')) loadTopicList();
  });
  learningSseBound = true;
}

window.OmniQALearningTab = {
  loadLearningTab,
  openCreateTopic,
  openTopic,
  refreshTopic,
  uploadMaterial,
  previewMaterial,
  downloadMaterial,
  deleteMaterial,
  createAssessment,
  openAssessmentEditor,
  updateQ,
  addQuestion,
  removeQuestion,
  saveQuestions,
  saveAndPublish,
  openPaper,
  submitPaper,
  openGrading,
  gradeAnswer,
  finalize,
  openResults,
};
