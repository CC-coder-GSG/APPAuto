function renderLearningOriginal(q, index) {
  return `<div class="learning-original"><div class="muted">${learningEsc(learningType(q.question_type || ''))} · ${q.max_score ?? q.score ?? 0} 分</div><strong class="learning-prompt">${q.position ?? index}. ${learningEsc(q.prompt)}</strong>${(q.options || []).length ? `<ol class="learning-original-options" type="A">${q.options.map(o => `<li>${learningEsc(o)}</li>`).join('')}</ol>` : ''}</div>`;
}

let learningContentsCache = [];
let learningQuizzesCache = [];
let activeQuiz = null;
let activeQuizPreview = false;
let learningPreviewObjectUrl = null;

function learningEsc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

function learningStatus(status) {
  return {draft:'草稿（题目隐藏）', open:'答题中', closed:'已结束'}[status] || status;
}

function learningType(type) {
  return {single_choice:'单选题', multiple_choice:'多选题', fill_blank:'填空题', short_answer:'简答题'}[type] || type;
}

function learningFileSize(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function findLearningContentFile(fileId) {
  for (const item of learningContentsCache) {
    const file = (item.files || []).find(candidate => candidate.id === Number(fileId));
    if (file) return file;
  }
  return null;
}

function renderLearningFileList(files, canDelete = false, contentId = null) {
  if (!files || !files.length) return '<p class="muted">暂无附件</p>';
  return files.map(file => `<div class="learning-file-row">
    <span class="learning-file-name">📎 ${learningEsc(file.original_name)} <span class="muted">(${learningFileSize(file.file_size)})</span></span>
    <span class="learning-file-actions">
      ${file.previewable ? `<button class="secondary" onclick="previewLearningContentFile(${file.id})">预览</button>` : ''}
      <button class="secondary" onclick="downloadLearningContentFile(${file.id})">下载</button>
      ${canDelete ? `<button class="danger" onclick="deleteLearningContentFile(${file.id},${contentId})">删除</button>` : ''}
    </span>
  </div>`).join('');
}

function closeLearningPanels() {
  ['learningContentForm','quizEditor','quizTakePanel','quizResultPanel','quizReviewPanel'].forEach(id => document.getElementById(id).classList.add('hidden'));
  document.getElementById('learningHome').classList.remove('hidden');
  loadLearningCenter();
}

async function loadLearningCenter() {
  try {
    [learningContentsCache, learningQuizzesCache] = await Promise.all([
      api('/learning/contents').then(r => r.json()),
      api('/learning/quizzes').then(r => r.json())
    ]);
    renderLearningContents();
    renderLearningQuizzes();
  } catch (e) { showMessage(e.message || '学习中心加载失败', 'error'); }
}

function renderLearningContents() {
  const host = document.getElementById('learningContents');
  if (!learningContentsCache.length) {
    host.innerHTML = '<p class="muted">暂无已发布的学习资料或课程。</p>';
    return;
  }
  host.innerHTML = learningContentsCache.map(item => `
    <article class="learning-item">
      <span class="badge">${item.kind === 'course' ? '课程' : '资料'}${item.published ? '' : ' · 未发布'}</span>
      <h4>${learningEsc(item.title)}</h4>
      <p class="muted">创建人：${learningEsc(item.creator_name)}</p>
      ${item.description ? `<p>${learningEsc(item.description)}</p>` : ''}
      ${item.body ? `<details><summary>查看内容</summary><p>${learningEsc(item.body)}</p></details>` : ''}
      ${(item.files || []).length ? `<div class="learning-file-list">${renderLearningFileList(item.files)}</div>` : ''}
      <div class="learning-item-actions">
        ${item.resource_url ? `<a href="${learningEsc(item.resource_url)}" target="_blank" rel="noopener"><button class="secondary">打开资源</button></a>` : ''}
        ${currentUser && item.creator_id === currentUser.id ? `<button class="secondary" onclick="showLearningContentForm(${item.id})">编辑</button><button class="danger" onclick="deleteLearningContent(${item.id})">删除</button>` : ''}
      </div>
    </article>`).join('');
}

function renderLearningQuizzes() {
  const host = document.getElementById('learningQuizzes');
  if (!learningQuizzesCache.length) {
    host.innerHTML = '<p class="muted">暂无答题活动。</p>';
    return;
  }
  host.innerHTML = learningQuizzesCache.map(q => {
    let actions = '';
    if (q.is_creator) {
      if (q.status === 'draft') actions += `<button onclick="openQuizEditor(${q.id})">编辑题目</button><button class="secondary" onclick="previewQuiz(${q.id})">预览答题</button><button onclick="startLearningQuiz(${q.id})">开始答题</button><button class="danger" onclick="deleteLearningQuiz(${q.id})">删除</button>`;
      if (q.status === 'open') actions += `<button class="secondary" onclick="previewQuiz(${q.id})">预览答题</button><button onclick="reviewLearningQuiz(${q.id})">答题情况</button><button class="danger" onclick="closeLearningQuiz(${q.id})">结束答题</button>`;
      if (q.status === 'closed') actions += `<button onclick="reviewLearningQuiz(${q.id})">批阅与统计</button>`;
    } else if (q.status === 'open' && !q.my_submission) {
      actions += `<button onclick="openTakeQuiz(${q.id},false)">开始答题</button>`;
    } else if (q.my_submission) {
      actions += `<button onclick="viewMyQuizResult(${q.id})">${q.results_released ? '成绩与原题' : '查看原题与作答'}</button>`;
    }
    const mine = q.my_submission ? (q.results_released ? `成绩 ${q.my_submission.total_score ?? '-'} / ${q.total_points}` : '已提交，等待发布成绩') : '';
    return `<article class="learning-item">
      <span class="status-pill status-${q.status}">${learningStatus(q.status)}</span>
      <h4>${learningEsc(q.title)}</h4>
      <p class="muted">出题人：${learningEsc(q.creator_name)} · ${q.question_count} 题 · ${q.total_points} 分</p>
      ${q.description ? `<p>${learningEsc(q.description)}</p>` : ''}
      ${mine ? `<p><strong>${learningEsc(mine)}</strong></p>` : ''}
      <div class="learning-item-actions">${actions || '<span class="muted">当前无可用操作</span>'}</div>
    </article>`;
  }).join('');
}

function showLearningContentForm(id = null) {
  document.getElementById('learningHome').classList.add('hidden');
  document.getElementById('learningContentForm').classList.remove('hidden');
  const item = id ? learningContentsCache.find(x => x.id === id) : null;
  learningContentFormTitle.textContent = item ? '编辑学习内容' : '新增学习内容';
  learningContentId.value = item ? item.id : '';
  learningContentKind.value = item ? item.kind : 'material';
  learningContentTitle.value = item ? item.title : '';
  learningContentDescription.value = item ? item.description || '' : '';
  learningContentBody.value = item ? item.body || '' : '';
  learningContentUrl.value = item ? item.resource_url || '' : '';
  learningContentPublished.checked = item ? item.published : false;
  learningContentFiles.value = '';
  learningContentUploadStatus.textContent = '';
  learningContentExistingFiles.innerHTML = item
    ? `<h4>已上传附件</h4>${renderLearningFileList(item.files || [], true, item.id)}`
    : '';
}

async function saveLearningContent() {
  if (learningContentSaveBtn.disabled) return;
  const id = learningContentId.value;
  const payload = {
    kind: learningContentKind.value,
    title: learningContentTitle.value.trim(),
    description: learningContentDescription.value.trim() || null,
    body: learningContentBody.value.trim() || null,
    resource_url: learningContentUrl.value.trim() || null,
    published: learningContentPublished.checked
  };
  if (!payload.title) return showMessage('请输入标题', 'error');
  const pendingFiles = [...learningContentFiles.files];
  if (pendingFiles.some(file => file.size > 100 * 1024 * 1024)) return showMessage('单个附件不能超过 100 MB，请重新选择', 'error');
  learningContentSaveBtn.disabled = true;
  learningContentFiles.disabled = true;
  try {
    const saved = await api(id ? `/learning/contents/${id}` : '/learning/contents', {method:id ? 'PUT' : 'POST', headers:H, body:JSON.stringify(payload)}).then(r => r.json());
    learningContentId.value = saved.id;
    const failed = await uploadLearningFiles(`/learning/contents/${saved.id}/files`, pendingFiles, learningContentUploadStatus);
    retainLearningFiles(learningContentFiles, failed);
    if (failed.length) {
      showMessage(`${failed.length} 个附件上传失败，点击保存可重试；已成功的附件不会重复上传`, 'error');
      return;
    }
    showMessage(pendingFiles.length ? '学习内容及附件已保存' : '学习内容已保存');
    closeLearningPanels();
  } catch(e) {
    showMessage(`${e.message || '保存失败'}${learningContentId.value ? '；已保存的内容可重新编辑并继续上传' : ''}`, 'error');
  } finally {
    learningContentSaveBtn.disabled = false;
    learningContentFiles.disabled = false;
  }
}

function retainLearningFiles(input, files) {
  const transfer = new DataTransfer();
  files.forEach(file => transfer.items.add(file));
  input.files = transfer.files;
}

function showLearningSelectedFiles() {
  const files = [...learningContentFiles.files];
  learningContentUploadStatus.innerHTML = files.length ? `<div class="learning-upload-summary">已选择 ${files.length} 个附件 · 点击保存开始上传</div><div class="learning-upload-list">${files.map(file => `<div class="learning-file-row"><strong class="learning-file-name">${learningEsc(file.name)}</strong><span>${learningFileSize(file.size)}</span></div>`).join('')}</div>` : '';
}

async function uploadLearningFiles(url, files, host) {
  if (!files.length) return [];
  host.innerHTML = `<div class="learning-upload-summary" role="status"></div><div class="learning-upload-list">${files.map(file => `<div class="learning-upload-file"><strong>${learningEsc(file.name)}</strong><progress max="100" value="0" aria-label="${learningEsc(file.name)} 上传进度"></progress><span>等待上传 · ${learningFileSize(file.size)}</span></div>`).join('')}</div>`;
  const summary = host.querySelector('[role="status"]');
  const rows = host.querySelectorAll('.learning-upload-file');
  const failed = [];
  for (const [index, file] of files.entries()) {
    summary.textContent = `正在上传 ${index + 1} / ${files.length} 个附件`;
    const progress = rows[index].querySelector('progress');
    const label = rows[index].querySelector('span');
    try {
      const form = new FormData();
      form.append('file', file);
      await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url);
        const token = localStorage.getItem('token');
        if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
        label.textContent = '准备上传…';
        xhr.upload.onprogress = event => {
          if (!event.lengthComputable) { progress.removeAttribute('value'); label.textContent = '正在上传…'; return; }
          const percent = Math.round(event.loaded / event.total * 100);
          progress.value = percent;
          label.textContent = percent === 100 ? '上传完成，服务器处理中…' : `上传中 ${percent}%`;
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) return resolve();
          let message = xhr.status === 401 ? '登录已过期，请重新登录后重试' : `上传失败（HTTP ${xhr.status}）`;
          try { message = JSON.parse(xhr.responseText).detail || message; } catch {}
          reject(new Error(message));
        };
        xhr.onerror = () => reject(new Error('网络异常，请重试'));
        xhr.onabort = () => reject(new Error('上传已取消'));
        xhr.send(form);
      });
      progress.value = 100;
      label.textContent = '上传成功';
      rows[index].classList.add('is-success');
    } catch (error) {
      failed.push(file);
      label.textContent = error.message;
      rows[index].classList.add('is-error');
    }
  }
  summary.textContent = `上传结束：成功 ${files.length - failed.length} / ${files.length}${failed.length ? `，失败 ${failed.length} 个，可重试` : ''}`;
  return failed;
}

async function deleteLearningContent(id) {
  if (!confirm('确定删除这项学习内容？')) return;
  try { await api(`/learning/contents/${id}`, {method:'DELETE'}); showMessage('已删除'); loadLearningCenter(); }
  catch(e) { showMessage(e.message, 'error'); }
}

async function downloadLearningContentFile(fileId) {
  const file = findLearningContentFile(fileId);
  try {
    const response = await api(`/learning/content-files/${fileId}/download`);
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = file?.original_name || 'download';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 60000);
  } catch(e) { showMessage(e.message || '附件下载失败', 'error'); }
}

async function previewLearningContentFile(fileId) {
  const file = findLearningContentFile(fileId);
  if (!file?.previewable) return showMessage('该附件不支持在线预览，请下载查看', 'error');
  const modal = document.getElementById('learningFilePreviewModal');
  const body = document.getElementById('learningFilePreviewBody');
  learningFilePreviewTitle.textContent = file.original_name;
  body.innerHTML = '<p class="muted" style="padding:20px">正在加载预览…</p>';
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  try {
    const response = await api(`/learning/content-files/${fileId}/preview`);
    if (file.preview_kind === 'markdown') {
      body.innerHTML = `<pre class="learning-markdown-preview">${learningEsc(await response.text())}</pre>`;
      return;
    }
    const blob = await response.blob();
    if (learningPreviewObjectUrl) URL.revokeObjectURL(learningPreviewObjectUrl);
    learningPreviewObjectUrl = URL.createObjectURL(blob);
    body.innerHTML = `<iframe src="${learningPreviewObjectUrl}" title="${learningEsc(file.original_name)}"></iframe>`;
  } catch(e) {
    body.innerHTML = `<p style="padding:20px;color:#dc2626">${learningEsc(e.message || '预览失败，请下载查看')}</p>`;
  }
}

function closeLearningFilePreview() {
  learningFilePreviewModal.classList.add('hidden');
  learningFilePreviewModal.setAttribute('aria-hidden', 'true');
  learningFilePreviewBody.innerHTML = '';
  if (learningPreviewObjectUrl) {
    URL.revokeObjectURL(learningPreviewObjectUrl);
    learningPreviewObjectUrl = null;
  }
}

async function deleteLearningContentFile(fileId, contentId) {
  if (!confirm('确定删除这个附件？')) return;
  try {
    await api(`/learning/content-files/${fileId}`, {method:'DELETE'});
    await loadLearningCenter();
    showLearningContentForm(contentId);
    showMessage('附件已删除');
  } catch(e) { showMessage(e.message || '附件删除失败', 'error'); }
}

async function openQuizEditor(id = null) {
  document.getElementById('learningHome').classList.add('hidden');
  document.getElementById('quizEditor').classList.remove('hidden');
  quizEditId.value = id || '';
  quizEditorTitle.textContent = id ? '编辑答题活动' : '创建答题活动';
  quizTitle.value = '';
  quizDescription.value = '';
  quizQuestionEditors.innerHTML = '';
  if (!id) { addQuestionEditor('single_choice'); return; }
  try {
    const quiz = await api(`/learning/quizzes/${id}`).then(r => r.json());
    quizTitle.value = quiz.title;
    quizDescription.value = quiz.description || '';
    quiz.questions.forEach(addQuestionEditorFromData);
  } catch(e) { showMessage(e.message, 'error'); closeLearningPanels(); }
}

function addQuestionEditor(type) {
  addQuestionEditorFromData({question_type:type, prompt:'', score:1, options:['','','',''], correct_answers:[], fill_grading_mode:'exact', match_count:1});
  const card = document.getElementById('quizQuestionEditors').lastElementChild;
  card.scrollIntoView({block:'center', behavior:'smooth'});
  card.querySelector('.qe-prompt').focus({preventScroll:true});
}

let questionEditorSequence = 0;

function addQuestionEditorFromData(q) {
  const host = document.getElementById('quizQuestionEditors');
  const el = document.createElement('div');
  el.className = 'question-editor';
  el.dataset.type = q.question_type;
  el.dataset.editorId = String(++questionEditorSequence);
  const choice = ['single_choice','multiple_choice'].includes(q.question_type);
  const fill = q.question_type === 'fill_blank';
  const modeOptions = [
    ['exact','必须一字不差'],['contains','填写内容连续包含答案'],['normalized','去除标点/空格/符号后匹配'],['match_any','与任一参考答案匹配'],['match_count','匹配指定数量的参考答案']
  ].map(([v,t]) => `<option value="${v}" ${q.fill_grading_mode===v?'selected':''}>${t}</option>`).join('');
  el.innerHTML = `<div class="question-editor-head"><strong>${learningType(q.question_type)}</strong><button type="button" class="secondary qe-remove-question">删除题目</button></div>
    <div class="question-editor-grid">
      <label class="wide">题干<textarea class="qe-prompt" rows="3" placeholder="在这里输入题目内容…">${learningEsc(q.prompt)}</textarea></label>
      ${fill ? '<div class="wide qe-blank-tools"><button type="button" class="secondary qe-insert-blank">＋ 插入填空位</button><span class="muted">在光标处插入；选中文字后点击，可将文字转为参考答案。</span></div>' : ''}
      <label class="qe-score-field">分值<input class="qe-score" type="number" min="0.5" max="10000" step="0.5" value="${learningEsc(q.score ?? 1)}"></label>
      ${choice ? `<div class="wide qe-section"><div class="qe-section-head"><strong>选项与正确答案</strong><span class="muted">${q.question_type === 'single_choice' ? '点击圆圈设为正确答案（单选）' : '勾选所有正确答案（多选）'}</span></div><div class="qe-option-list"></div><button type="button" class="secondary qe-add-option">＋ 添加选项</button></div>` : ''}
      ${fill ? `<div class="wide qe-section"><div class="qe-section-head"><strong>参考答案</strong><span class="muted">每个输入框填写一个答案，无需手动分隔</span></div><div class="qe-reference-list"></div><button type="button" class="secondary qe-add-reference">＋ 添加参考答案</button></div><label>自动批改方式<select class="qe-mode">${modeOptions}</select></label><label class="qe-count-field">至少匹配答案数<input class="qe-count" type="number" min="1" step="1" value="${learningEsc(q.match_count || 1)}"></label><p class="wide muted qe-mode-help"></p><p class="wide muted">多个空位的答案由答题人统一填写在答案框中，按本题的参考答案和批改方式评分，不按空位逐一计分。</p>` : ''}
      ${q.question_type === 'short_answer' ? '<p class="muted wide">简答题提交后由出题人手工评分。</p>' : ''}
    </div><details class="qe-preview"><summary>查看题目预览</summary><div class="qe-preview-body"></div></details><p class="qe-error" role="alert" hidden></p>`;
  host.appendChild(el);
  el.querySelector('.qe-remove-question').onclick = () => {
    if (!confirm('确定删除这道题目？未保存的内容将被移除。')) return;
    el.remove(); renumberQuestionEditors();
  };
  if (choice) {
    (q.options?.length ? q.options : ['','']).forEach(text => addQuizOption(el, text, (q.correct_answers || []).includes(text)));
    el.querySelector('.qe-add-option').onclick = () => addQuizOption(el, '', false, true);
  }
  if (fill) {
    (q.correct_answers?.length ? q.correct_answers : ['']).forEach(text => addQuizReference(el, text));
    el.querySelector('.qe-add-reference').onclick = () => addQuizReference(el, '', true);
    el.querySelector('.qe-insert-blank').onclick = () => insertQuizBlank(el);
    el.querySelector('.qe-mode').onchange = () => updateQuizFillMode(el);
    updateQuizFillMode(el);
  }
  el.addEventListener('input', () => { clearQuizQuestionError(el); updateQuizQuestionPreview(el); });
  el.addEventListener('change', () => updateQuizQuestionPreview(el));
  updateQuizQuestionPreview(el);
  renumberQuestionEditors();
}

function quizOptionLetter(index) {
  let label = '';
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) label = String.fromCharCode(65 + (n - 1) % 26) + label;
  return label;
}

function addQuizOption(el, text = '', checked = false, focus = false) {
  const row = document.createElement('div');
  row.className = 'qe-option-row';
  row.innerHTML = `<label class="qe-option-check"><input class="qe-option-correct" type="${el.dataset.type === 'single_choice' ? 'radio' : 'checkbox'}" name="qe-answer-${el.dataset.editorId}" ${checked ? 'checked' : ''}><span class="qe-option-letter"></span></label><textarea class="qe-option-text" rows="2" placeholder="输入选项内容，无需填写 A、B 编号">${learningEsc(text)}</textarea><button type="button" class="secondary qe-remove-option">删除</button>`;
  el.querySelector('.qe-option-list').appendChild(row);
  row.querySelector('.qe-remove-option').onclick = () => {
    row.remove(); refreshQuizOptionRows(el); clearQuizQuestionError(el); updateQuizQuestionPreview(el);
  };
  refreshQuizOptionRows(el);
  clearQuizQuestionError(el);
  updateQuizQuestionPreview(el);
  if (focus) row.querySelector('textarea').focus();
}

function refreshQuizOptionRows(el) {
  const rows = [...el.querySelectorAll('.qe-option-row')];
  rows.forEach((row, i) => {
    const letter = quizOptionLetter(i);
    row.querySelector('.qe-option-letter').textContent = letter;
    row.querySelector('input').setAttribute('aria-label', `将选项 ${letter} 设为正确答案`);
    row.querySelector('textarea').setAttribute('aria-label', `选项 ${letter} 内容`);
    const remove = row.querySelector('button');
    remove.disabled = rows.length <= 2;
    remove.setAttribute('aria-label', `删除选项 ${letter}`);
    remove.title = rows.length <= 2 ? '选择题至少保留两个选项' : `删除选项 ${letter}`;
  });
}

function addQuizReference(el, text = '', focus = false) {
  const row = document.createElement('div');
  row.className = 'qe-reference-row';
  row.innerHTML = `<span class="qe-reference-number"></span><textarea class="qe-reference-text" rows="2" placeholder="输入可接受的答案">${learningEsc(text)}</textarea><button type="button" class="secondary">删除</button>`;
  el.querySelector('.qe-reference-list').appendChild(row);
  row.querySelector('button').onclick = () => {
    row.remove(); refreshQuizReferences(el); clearQuizQuestionError(el); updateQuizQuestionPreview(el);
  };
  refreshQuizReferences(el);
  clearQuizQuestionError(el);
  if (focus) row.querySelector('textarea').focus();
}

function refreshQuizReferences(el) {
  const rows = [...el.querySelectorAll('.qe-reference-row')];
  rows.forEach((row, i) => {
    row.querySelector('span').textContent = i + 1;
    row.querySelector('textarea').setAttribute('aria-label', `参考答案 ${i + 1}`);
    row.querySelector('button').disabled = rows.length <= 1;
    row.querySelector('button').setAttribute('aria-label', `删除参考答案 ${i + 1}`);
  });
}

function insertQuizBlank(el) {
  const prompt = el.querySelector('.qe-prompt');
  const selected = prompt.value.slice(prompt.selectionStart, prompt.selectionEnd).trim();
  prompt.setRangeText(' ______ ', prompt.selectionStart, prompt.selectionEnd, 'end');
  prompt.focus();
  if (selected) {
    const refs = [...el.querySelectorAll('.qe-reference-text')];
    if (!refs.some(input => input.value.trim() === selected)) {
      const empty = refs.find(input => !input.value.trim());
      if (empty) empty.value = selected;
      else addQuizReference(el, selected);
    }
  }
  clearQuizQuestionError(el);
  updateQuizQuestionPreview(el);
}

function updateQuizFillMode(el) {
  const mode = el.querySelector('.qe-mode').value;
  el.querySelector('.qe-count-field').hidden = mode !== 'match_count';
  el.querySelector('.qe-mode-help').textContent = {
    exact:'填写内容与任一参考答案完全一致即可得分。',
    contains:'填写内容连续包含任一参考答案即可得分，不区分英文大小写。',
    normalized:'去除空白、标点和符号，统一全半角及英文大小写后，与任一参考答案一致即可得分。',
    match_any:'填写内容可用换行、逗号或分号分隔，规范化后命中任一参考答案即可得分。',
    match_count:'填写内容可用换行、逗号或分号分隔，至少命中指定数量的不同参考答案即可得分。'
  }[mode];
  clearQuizQuestionError(el);
}

function updateQuizQuestionPreview(el) {
  const body = el.querySelector('.qe-preview-body');
  if (!body) return;
  const rows = [...el.querySelectorAll('.qe-option-row')];
  rows.forEach(row => row.classList.toggle('is-correct', row.querySelector('input').checked));
  body.innerHTML = `<div class="qe-preview-prompt">${learningEsc(el.querySelector('.qe-prompt').value || '尚未填写题干')}</div>` +
    rows.map((row, i) => `<div class="qe-preview-option ${row.querySelector('input').checked ? 'is-correct' : ''}"><b>${quizOptionLetter(i)}.</b> ${learningEsc(row.querySelector('textarea').value || '（未填写选项）')}${row.querySelector('input').checked ? '<span class="qe-correct-tag">正确答案</span>' : ''}</div>`).join('') +
    (rows.length ? '' : '<div class="qe-preview-answer">答题区域：请输入答案</div>');
}

function clearQuizQuestionError(el) {
  el.classList.remove('has-error');
  const error = el.querySelector('.qe-error');
  if (error) { error.hidden = true; error.textContent = ''; }
}

function renumberQuestionEditors() {
  [...document.querySelectorAll('#quizQuestionEditors .question-editor')].forEach((el,i) => {
    const title = el.querySelector('.question-editor-head strong');
    title.textContent = `${i+1}. ${learningType(el.dataset.type)}`;
  });
  const summary = document.getElementById('quizEditorSummary');
  if (summary) summary.textContent = `共 ${document.querySelectorAll('#quizQuestionEditors .question-editor').length} 道题 · 保存草稿后可试答预览`;
}

async function saveQuizDraft() {
  const editors = [...document.querySelectorAll('#quizQuestionEditors .question-editor')];
  const payload = {title:quizTitle.value.trim(), description:quizDescription.value.trim() || null, questions:editors.map(el => ({
    question_type:el.dataset.type,
    prompt:el.querySelector('.qe-prompt').value.trim(),
    score:Number(el.querySelector('.qe-score').value),
    options:[...el.querySelectorAll('.qe-option-text')].map(input => input.value.trim()),
    correct_answers:el.querySelector('.qe-option-list')
      ? [...el.querySelectorAll('.qe-option-row')].filter(row => row.querySelector('.qe-option-correct').checked).map(row => row.querySelector('.qe-option-text').value.trim())
      : [...el.querySelectorAll('.qe-reference-text')].map(input => input.value.trim()),
    fill_grading_mode:el.querySelector('.qe-mode') ? el.querySelector('.qe-mode').value : null,
    match_count:el.querySelector('.qe-mode')?.value === 'match_count' ? Number(el.querySelector('.qe-count').value) : 1
  }))};
  if (!payload.title) { quizTitle.focus(); return showMessage('请填写答题活动标题', 'error'); }
  if (!payload.questions.length) return showMessage('请至少添加一道题目', 'error');
  for (let i = 0; i < payload.questions.length; i++) {
    const q = payload.questions[i];
    const el = editors[i];
    clearQuizQuestionError(el);
    let message = '', selector = '.qe-prompt';
    if (!q.prompt) message = '请填写题干';
    else if (!Number.isFinite(q.score) || q.score <= 0 || q.score > 10000) { message = '分值必须大于 0 且不超过 10000'; selector = '.qe-score'; }
    else if (q.options.length) {
      selector = '.qe-option-text';
      if (q.options.length < 2 || q.options.some(x => !x)) message = '请填写所有选项内容，不需要的选项可以删除';
      else if (new Set(q.options).size !== q.options.length) message = '选项内容不能重复';
      else if (!q.correct_answers.length || (q.question_type === 'single_choice' && q.correct_answers.length !== 1)) { message = '请选择正确答案'; selector = '.qe-option-correct'; }
    } else if (q.question_type === 'fill_blank') {
      selector = '.qe-reference-text';
      if (!q.correct_answers.length || q.correct_answers.some(x => !x)) message = '请填写参考答案，或删除多余的空答案框';
      else if (new Set(q.correct_answers).size !== q.correct_answers.length) message = '参考答案不能重复';
      else if (!Number.isInteger(q.match_count) || q.match_count < 1 || q.match_count > q.correct_answers.length) { message = '匹配数量必须为正整数，且不能超过参考答案数量'; selector = '.qe-count'; }
    }
    if (message) {
      el.classList.add('has-error');
      el.querySelector('.qe-error').textContent = message;
      el.querySelector('.qe-error').hidden = false;
      el.querySelector(selector)?.focus();
      el.scrollIntoView({behavior:'smooth', block:'center'});
      return showMessage(`第 ${i + 1} 题：${message}`, 'error');
    }
  }
  const id = quizEditId.value;
  try {
    await api(id ? `/learning/quizzes/${id}` : '/learning/quizzes', {method:id ? 'PUT' : 'POST', headers:H, body:JSON.stringify(payload)});
    showMessage('题目草稿已保存，开始前其他人不可见'); closeLearningPanels();
  } catch(e) { showMessage(e.message, 'error'); }
}

async function startLearningQuiz(id) {
  if (!confirm('开始后题目将对团队成员可见，且不能再编辑。确定开始？')) return;
  try { await api(`/learning/quizzes/${id}/start`, {method:'POST'}); showMessage('答题已开始'); loadLearningCenter(); }
  catch(e) { showMessage(e.message, 'error'); }
}

async function closeLearningQuiz(id) {
  if (!confirm('确定结束答题？未提交成员将无法再提交。')) return;
  try { await api(`/learning/quizzes/${id}/close`, {method:'POST'}); showMessage('答题已结束'); loadLearningCenter(); }
  catch(e) { showMessage(e.message, 'error'); }
}

async function deleteLearningQuiz(id) {
  if (!confirm('确定删除这个草稿？')) return;
  try { await api(`/learning/quizzes/${id}`, {method:'DELETE'}); showMessage('已删除'); loadLearningCenter(); }
  catch(e) { showMessage(e.message, 'error'); }
}

async function previewQuiz(id) {
  try {
    const existing = await api(`/learning/quizzes/${id}?preview=true`).then(r => r.json());
    if (existing.preview_submission_id) await api(`/learning/quizzes/${id}/preview`, {method:'DELETE'});
    await openTakeQuiz(id, true);
  } catch(e) { showMessage(e.message, 'error'); }
}

async function openTakeQuiz(id, preview) {
  try {
    activeQuiz = await api(`/learning/quizzes/${id}${preview ? '?preview=true' : ''}`).then(r => r.json());
    activeQuizPreview = preview;
    document.getElementById('learningHome').classList.add('hidden');
    document.getElementById('quizTakePanel').classList.remove('hidden');
    takeQuizTitle.textContent = (preview ? '预览：' : '') + activeQuiz.title;
    takeQuizDescription.textContent = activeQuiz.description || '';
    submitQuizButton.textContent = preview ? '提交预览答卷' : '确认提交（仅一次）';
    takeQuizQuestions.innerHTML = activeQuiz.questions.map(q => renderTakeQuestion(q)).join('');
  } catch(e) { showMessage(e.message, 'error'); }
}

function renderTakeQuestion(q) {
  let input = '';
  if (q.question_type === 'single_choice' || q.question_type === 'multiple_choice') {
    const inputType = q.question_type === 'single_choice' ? 'radio' : 'checkbox';
    input = q.options.map(option => `<label class="option-answer"><input type="${inputType}" name="q_${q.id}" value="${learningEsc(option)}">${learningEsc(option)}</label>`).join('');
  } else {
    input = `<textarea id="answer_${q.id}" rows="${q.question_type === 'short_answer' ? 5 : 2}" style="width:100%" placeholder="请输入答案"></textarea>`;
  }
  return `<div class="take-question" data-id="${q.id}" data-type="${q.question_type}"><strong class="learning-prompt">${q.position}. ${learningEsc(q.prompt)}</strong><span class="badge" style="float:right">${q.score} 分</span><div style="margin-top:12px">${input}</div></div>`;
}

async function submitCurrentQuiz() {
  if (!activeQuiz || !confirm(activeQuizPreview ? '提交这份预览答卷？' : '正式答卷只能提交一次，确认提交？')) return;
  const answers = activeQuiz.questions.map(q => {
    let answer;
    if (q.question_type === 'single_choice') answer = document.querySelector(`input[name="q_${q.id}"]:checked`)?.value ?? null;
    else if (q.question_type === 'multiple_choice') answer = [...document.querySelectorAll(`input[name="q_${q.id}"]:checked`)].map(x => x.value);
    else answer = document.getElementById(`answer_${q.id}`).value;
    return {question_id:q.id, answer};
  });
  try {
    await api(`/learning/quizzes/${activeQuiz.id}/submissions`, {method:'POST', headers:H, body:JSON.stringify({answers,is_preview:activeQuizPreview})});
    showMessage(activeQuizPreview ? '预览答卷已提交，可在批阅页检查' : '提交成功'); closeLearningPanels();
  } catch(e) { showMessage(e.message, 'error'); }
}

async function viewMyQuizResult(id) {
  try {
    const result = await api(`/learning/quizzes/${id}/my-result`).then(r => r.json());
    document.getElementById('learningHome').classList.add('hidden');
    document.getElementById('quizResultPanel').classList.remove('hidden');
    quizResultBody.innerHTML = (result.released ? `<div class="quiz-score">${result.score} / ${result.total_points} 分</div>` : '<p class="learning-upload-summary">答卷已提交，成绩尚未发布。可查看原题和自己的作答。</p>') + result.answers.map((a,i) => `
      <div class="result-answer">${renderLearningOriginal(a,i+1)}<p>你的答案：${learningEsc(formatLearningAnswer(a.answer))}</p>${result.released ? `<p>得分：${a.awarded_score} / ${a.max_score}</p>` : ''}${a.reviewer_comment ? `<p>批语：${learningEsc(a.reviewer_comment)}</p>` : ''}${result.show_answers ? `<p>参考答案：${learningEsc(formatLearningAnswer(a.correct_answers))}</p>` : ''}</div>`).join('');
  } catch(e) { showMessage(e.message, 'error'); }
}

function formatLearningAnswer(answer) {
  if (answer === null || answer === undefined || answer === '') return '（未作答）';
  return Array.isArray(answer) ? answer.join('；') : String(answer);
}

async function reviewLearningQuiz(id) {
  try {
    const [quiz, stats, submissions] = await Promise.all([
      api(`/learning/quizzes/${id}`).then(r => r.json()),
      api(`/learning/quizzes/${id}/stats`).then(r => r.json()),
      api(`/learning/quizzes/${id}/submissions`).then(r => r.json())
    ]);
    activeQuiz = quiz;
    document.getElementById('learningHome').classList.add('hidden');
    document.getElementById('quizReviewPanel').classList.remove('hidden');
    reviewQuizTitle.textContent = `批阅与统计：${quiz.title}`;
    quizStatsBody.innerHTML = `<div class="learning-item" style="margin-bottom:16px"><strong>已提交 ${stats.submitted_count} / ${stats.expected_count} 人</strong> · 已批阅 ${stats.graded_count} 人 · 未提交 ${stats.pending_count} 人</div>` +
      stats.questions.map(q => `<div class="learning-item" style="margin-bottom:10px">${renderLearningOriginal(q,q.position)}<p>参考答案：${learningEsc(formatLearningAnswer(q.correct_answers))}</p><p>作答 ${q.answered_count} 人 · ${q.correct_rate === null ? '待人工评分' : `正确率 ${q.correct_rate}%`}</p><div class="stats-bar"><span style="width:${q.correct_rate || 0}%"></span></div><details><summary>查看所有人的答案</summary>${q.answers.map(a => `<p>${learningEsc(a.username)}：${learningEsc(formatLearningAnswer(a.answer))} ${a.is_correct === null ? '（待评）' : a.is_correct ? '✓' : '✗'}</p>`).join('') || '<p class="muted">暂无</p>'}</details></div>`).join('');
    const release = quiz.status === 'closed' ? `<div class="learning-item" style="margin-bottom:16px"><strong>${quiz.results_released ? '成绩已发布' : '完成批阅后发布成绩'}</strong><div class="learning-item-actions"><button onclick="releaseLearningResults(${quiz.id},true)">发布成绩并展示答案</button><button class="secondary" onclick="releaseLearningResults(${quiz.id},false)">发布成绩但隐藏答案</button></div></div>` : '';
    quizSubmissionsBody.innerHTML = release + (submissions.map(renderReviewSubmission).join('') || '<p class="muted">暂时没有答卷。</p>');
  } catch(e) { showMessage(e.message, 'error'); }
}

function renderReviewSubmission(s) {
  return `<div class="review-submission" data-submission="${s.id}"><h4>${learningEsc(s.username)} ${s.is_preview ? '<span class="badge">出题人预览</span>' : ''} <span class="status-pill">${s.status === 'graded' ? `已批阅 ${s.total_score} 分` : '待批阅'}</span></h4>
    <div class="learning-answer-table"><table><thead><tr><th>题目/答案</th><th>参考答案</th><th>得分</th><th>批语</th></tr></thead><tbody>${s.answers.map(a => `<tr data-question="${a.question_id}"><td>${renderLearningOriginal(a,a.position)}<p>作答：${learningEsc(formatLearningAnswer(a.answer))}</p></td><td>${learningEsc(formatLearningAnswer(a.correct_answers))}</td><td><input class="review-score" type="number" min="0" max="${a.max_score}" step="0.5" value="${a.awarded_score ?? ''}" placeholder="/${a.max_score}"></td><td><input class="review-comment" value="${learningEsc(a.reviewer_comment || '')}" placeholder="可选"></td></tr>`).join('')}</tbody></table></div><button onclick="gradeLearningSubmission(${activeQuiz.id},${s.id})">保存批阅</button></div>`;
}

async function gradeLearningSubmission(quizId, submissionId) {
  const card = document.querySelector(`[data-submission="${submissionId}"]`);
  const grades = [...card.querySelectorAll('tr[data-question]')].map(row => ({question_id:Number(row.dataset.question), awarded_score:Number(row.querySelector('.review-score').value), reviewer_comment:row.querySelector('.review-comment').value || null}));
  if (grades.some(g => Number.isNaN(g.awarded_score))) return showMessage('请为所有题目填写得分', 'error');
  try { await api(`/learning/quizzes/${quizId}/submissions/${submissionId}/grade`, {method:'PUT', headers:H, body:JSON.stringify({grades})}); showMessage('批阅已保存'); reviewLearningQuiz(quizId); }
  catch(e) { showMessage(e.message, 'error'); }
}

async function releaseLearningResults(id, showAnswers) {
  if (!confirm(`确定发布成绩并${showAnswers ? '展示' : '隐藏'}参考答案？`)) return;
  try { await api(`/learning/quizzes/${id}/release`, {method:'POST', headers:H, body:JSON.stringify({show_answers:showAnswers})}); showMessage('成绩已发布'); reviewLearningQuiz(id); }
  catch(e) { showMessage(e.message, 'error'); }
}
