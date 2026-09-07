let learningContentsCache = [];
let learningQuizzesCache = [];
let activeQuiz = null;
let activeQuizPreview = false;

function learningEsc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

function learningStatus(status) {
  return {draft:'草稿（题目隐藏）', open:'答题中', closed:'已结束'}[status] || status;
}

function learningType(type) {
  return {single_choice:'单选题', multiple_choice:'多选题', fill_blank:'填空题', short_answer:'简答题'}[type] || type;
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
    } else if (q.my_submission && q.results_released) {
      actions += `<button onclick="viewMyQuizResult(${q.id})">查看成绩</button>`;
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
}

async function saveLearningContent() {
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
  try {
    await api(id ? `/learning/contents/${id}` : '/learning/contents', {method:id ? 'PUT' : 'POST', headers:H, body:JSON.stringify(payload)});
    showMessage('学习内容已保存'); closeLearningPanels();
  } catch(e) { showMessage(e.message, 'error'); }
}

async function deleteLearningContent(id) {
  if (!confirm('确定删除这项学习内容？')) return;
  try { await api(`/learning/contents/${id}`, {method:'DELETE'}); showMessage('已删除'); loadLearningCenter(); }
  catch(e) { showMessage(e.message, 'error'); }
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
  addQuestionEditorFromData({question_type:type, prompt:'', score:1, options:['选项A','选项B'], correct_answers:[], fill_grading_mode:'exact', match_count:1});
}

function addQuestionEditorFromData(q) {
  const host = document.getElementById('quizQuestionEditors');
  const el = document.createElement('div');
  el.className = 'question-editor';
  el.dataset.type = q.question_type;
  const choice = ['single_choice','multiple_choice'].includes(q.question_type);
  const fill = q.question_type === 'fill_blank';
  const modeOptions = [
    ['exact','必须一字不差'],['contains','填写内容连续包含答案'],['normalized','去除标点/空格/符号后匹配'],['match_any','与任一参考答案匹配'],['match_count','匹配指定数量的参考答案']
  ].map(([v,t]) => `<option value="${v}" ${q.fill_grading_mode===v?'selected':''}>${t}</option>`).join('');
  el.innerHTML = `<div class="question-editor-head"><strong>${learningType(q.question_type)}</strong><button class="danger" onclick="this.closest('.question-editor').remove();renumberQuestionEditors()">移除</button></div>
    <div class="question-editor-grid">
      <label class="wide">题目<textarea class="qe-prompt" rows="2">${learningEsc(q.prompt)}</textarea></label>
      <label>分值<input class="qe-score" type="number" min="0.5" step="0.5" value="${q.score || 1}"></label>
      ${choice ? `<label class="wide">选项（每行一个）<textarea class="qe-options" rows="4">${learningEsc((q.options||[]).join('\n'))}</textarea></label><label class="wide">正确答案（填写完整选项，多选每行一个）<textarea class="qe-correct" rows="3">${learningEsc((q.correct_answers||[]).join('\n'))}</textarea></label>` : ''}
      ${fill ? `<label class="wide">参考答案（每行一个）<textarea class="qe-correct" rows="3">${learningEsc((q.correct_answers||[]).join('\n'))}</textarea></label><label>自动批改方式<select class="qe-mode">${modeOptions}</select></label><label>至少匹配答案数<input class="qe-count" type="number" min="1" value="${q.match_count || 1}"></label>` : ''}
      ${q.question_type === 'short_answer' ? '<p class="muted wide">简答题提交后由出题人手工评分。</p>' : ''}
    </div>`;
  host.appendChild(el);
  renumberQuestionEditors();
}

function renumberQuestionEditors() {
  [...document.querySelectorAll('#quizQuestionEditors .question-editor')].forEach((el,i) => {
    const title = el.querySelector('.question-editor-head strong');
    title.textContent = `${i+1}. ${learningType(el.dataset.type)}`;
  });
}

function lines(value) { return value.split(/\r?\n/).map(x => x.trim()).filter(Boolean); }

async function saveQuizDraft() {
  const editors = [...document.querySelectorAll('#quizQuestionEditors .question-editor')];
  const payload = {title:quizTitle.value.trim(), description:quizDescription.value.trim() || null, questions:editors.map(el => ({
    question_type:el.dataset.type,
    prompt:el.querySelector('.qe-prompt').value.trim(),
    score:Number(el.querySelector('.qe-score').value),
    options:el.querySelector('.qe-options') ? lines(el.querySelector('.qe-options').value) : [],
    correct_answers:el.querySelector('.qe-correct') ? lines(el.querySelector('.qe-correct').value) : [],
    fill_grading_mode:el.querySelector('.qe-mode') ? el.querySelector('.qe-mode').value : null,
    match_count:el.querySelector('.qe-count') ? Number(el.querySelector('.qe-count').value) : 1
  }))};
  if (!payload.title || !payload.questions.length || payload.questions.some(q => !q.prompt)) return showMessage('请填写标题和所有题目', 'error');
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
  return `<div class="take-question" data-id="${q.id}" data-type="${q.question_type}"><strong>${q.position}. ${learningEsc(q.prompt)}</strong><span class="badge" style="float:right">${q.score} 分</span><div style="margin-top:12px">${input}</div></div>`;
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
    if (!result.released) { quizResultBody.innerHTML = '<p>答卷已提交，成绩尚未发布。</p>'; return; }
    quizResultBody.innerHTML = `<div class="quiz-score">${result.score} / ${result.total_points} 分</div>` + result.answers.map((a,i) => `
      <div class="result-answer"><strong>${i+1}. ${learningEsc(a.prompt)}</strong><p>你的答案：${learningEsc(formatLearningAnswer(a.answer))}</p><p>得分：${a.awarded_score} / ${a.max_score}</p>${a.reviewer_comment ? `<p>批语：${learningEsc(a.reviewer_comment)}</p>` : ''}${result.show_answers ? `<p>参考答案：${learningEsc(formatLearningAnswer(a.correct_answers))}</p>` : ''}</div>`).join('');
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
      stats.questions.map(q => `<div class="learning-item" style="margin-bottom:10px"><strong>${q.position}. ${learningEsc(q.prompt)}</strong><p>作答 ${q.answered_count} 人 · ${q.correct_rate === null ? '待人工评分' : `正确率 ${q.correct_rate}%`}</p><div class="stats-bar"><span style="width:${q.correct_rate || 0}%"></span></div><details><summary>查看所有人的答案</summary>${q.answers.map(a => `<p>${learningEsc(a.username)}：${learningEsc(formatLearningAnswer(a.answer))} ${a.is_correct === null ? '（待评）' : a.is_correct ? '✓' : '✗'}</p>`).join('') || '<p class="muted">暂无</p>'}</details></div>`).join('');
    const release = quiz.status === 'closed' ? `<div class="learning-item" style="margin-bottom:16px"><strong>${quiz.results_released ? '成绩已发布' : '完成批阅后发布成绩'}</strong><div class="learning-item-actions"><button onclick="releaseLearningResults(${quiz.id},true)">发布成绩并展示答案</button><button class="secondary" onclick="releaseLearningResults(${quiz.id},false)">发布成绩但隐藏答案</button></div></div>` : '';
    quizSubmissionsBody.innerHTML = release + (submissions.map(renderReviewSubmission).join('') || '<p class="muted">暂时没有答卷。</p>');
  } catch(e) { showMessage(e.message, 'error'); }
}

function renderReviewSubmission(s) {
  return `<div class="review-submission" data-submission="${s.id}"><h4>${learningEsc(s.username)} ${s.is_preview ? '<span class="badge">出题人预览</span>' : ''} <span class="status-pill">${s.status === 'graded' ? `已批阅 ${s.total_score} 分` : '待批阅'}</span></h4>
    <div class="learning-answer-table"><table><thead><tr><th>题目/答案</th><th>参考答案</th><th>得分</th><th>批语</th></tr></thead><tbody>${s.answers.map(a => `<tr data-question="${a.question_id}"><td><strong>${learningEsc(a.prompt)}</strong><br>${learningEsc(formatLearningAnswer(a.answer))}</td><td>${learningEsc(formatLearningAnswer(a.correct_answers))}</td><td><input class="review-score" type="number" min="0" max="${a.max_score}" step="0.5" value="${a.awarded_score ?? ''}" placeholder="/${a.max_score}"></td><td><input class="review-comment" value="${learningEsc(a.reviewer_comment || '')}" placeholder="可选"></td></tr>`).join('')}</tbody></table></div><button onclick="gradeLearningSubmission(${activeQuiz.id},${s.id})">保存批阅</button></div>`;
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
