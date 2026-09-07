let mobileToken = localStorage.getItem('mobile_token');
let mobileUser = null;
let mobileActiveQuiz = null;

function mEsc(v){return String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function mAnswer(v){if(v===null||v===undefined||v==='')return '（未作答）';return Array.isArray(v)?v.join('；'):String(v)}
function toast(msg){mobileToast.textContent=msg;mobileToast.classList.add('show');setTimeout(()=>mobileToast.classList.remove('show'),2200)}
async function mApi(url,opt={}){opt.headers={...(opt.headers||{}),Authorization:'Bearer '+mobileToken};const r=await fetch(url,opt);if(r.status===401){mobileLogout(false);throw new Error('登录已过期')}if(!r.ok){let msg='操作失败';try{msg=(await r.json()).detail||msg}catch{}throw new Error(msg)}return r}

function showMobileView(id){document.querySelectorAll('.view').forEach(x=>x.classList.add('hidden'));document.getElementById(id).classList.remove('hidden');window.scrollTo(0,0)}

async function mobileLogin(){
  loginError.textContent=''; const body=new URLSearchParams({username:mobileUsername.value.trim(),password:mobilePassword.value});
  try{const r=await fetch('/auth/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});if(!r.ok)throw new Error('用户名或密码错误');mobileToken=(await r.json()).access_token;localStorage.setItem('mobile_token',mobileToken);await mobileBoot()}catch(e){loginError.textContent=e.message}
}

function mobileLogout(reload=true){localStorage.removeItem('mobile_token');mobileToken=null;mobileUser=null;logoutBtn.classList.add('hidden');showMobileView('loginView');if(reload)toast('已退出')}

async function mobileBoot(){
  if(!mobileToken){showMobileView('loginView');return}
  try{mobileUser=await mApi('/auth/me').then(r=>r.json());mobileMe.textContent=mobileUser.username;logoutBtn.classList.remove('hidden');showMobileView('homeView');await loadMobileHome()}catch(e){mobileLogout(false)}
}

async function loadMobileHome(){
  try{const [quizzes,contents]=await Promise.all([mApi('/learning/quizzes').then(r=>r.json()),mApi('/learning/contents').then(r=>r.json())]);renderMobileQuizzes(quizzes);renderMobileContents(contents.filter(x=>x.published))}catch(e){toast(e.message)}
}

function mobileTab(tab){navQuiz.classList.toggle('active',tab==='quiz');navContent.classList.toggle('active',tab==='content');mobileQuizList.classList.toggle('hidden',tab!=='quiz');mobileContentList.classList.toggle('hidden',tab!=='content')}

function renderMobileQuizzes(items){
  if(!items.length){mobileQuizList.innerHTML='<div class="card muted">暂无可参与的答题活动</div>';return}
  mobileQuizList.innerHTML=items.map(q=>{let action='<p class="meta">当前不可答题</p>';if(q.is_creator)action=q.status==='draft'?'<p class="meta">请在网页端编辑、预览和开始答题</p>':`<button onclick="openMobileStats(${q.id})">查看答题情况</button>`;else if(q.status==='open'&&!q.my_submission)action=`<button onclick="openMobileQuiz(${q.id})">开始答题</button>`;else if(q.my_submission&&q.results_released)action=`<button onclick="openMobileResult(${q.id})">查看成绩</button>`;else if(q.my_submission)action='<p class="meta">✓ 已提交，等待出题人发布成绩</p>';return `<article class="mobile-card"><span class="pill">${q.status==='open'?'答题中':q.status==='closed'?'已结束':'草稿'}</span><h3>${mEsc(q.title)}</h3><p class="meta">出题人：${mEsc(q.creator_name)} · ${q.question_count} 题 · ${q.total_points} 分</p>${q.description?`<p>${mEsc(q.description)}</p>`:''}${action}</article>`}).join('')
}

function renderMobileContents(items){
  if(!items.length){mobileContentList.innerHTML='<div class="card muted">暂无学习资料或课程</div>';return}
  mobileContentList.innerHTML=items.map(x=>`<article class="mobile-card"><span class="pill">${x.kind==='course'?'课程':'资料'}</span><h3>${mEsc(x.title)}</h3>${x.description?`<p>${mEsc(x.description)}</p>`:''}${x.body?`<details><summary>查看学习内容</summary><p>${mEsc(x.body)}</p></details>`:''}${x.resource_url?`<p><a href="${mEsc(x.resource_url)}" target="_blank" rel="noopener">打开外部资源</a></p>`:''}</article>`).join('')
}

async function openMobileQuiz(id){
  try{mobileActiveQuiz=await mApi(`/learning/quizzes/${id}`).then(r=>r.json());mobileTakeTitle.textContent=mobileActiveQuiz.title;mobileTakeDesc.textContent=mobileActiveQuiz.description||'';mobileQuestions.innerHTML=mobileActiveQuiz.questions.map(renderMobileQuestion).join('');showMobileView('takeView')}catch(e){toast(e.message)}
}

function renderMobileQuestion(q){
  let input;if(q.question_type==='single_choice'||q.question_type==='multiple_choice'){const type=q.question_type==='single_choice'?'radio':'checkbox';input=q.options.map(o=>`<label class="choice"><input type="${type}" name="mq_${q.id}" value="${mEsc(o)}"><span>${mEsc(o)}</span></label>`).join('')}else input=`<textarea id="ma_${q.id}" rows="${q.question_type==='short_answer'?5:2}" placeholder="请输入答案"></textarea>`;return `<div class="mobile-question"><div class="mobile-question-head"><strong>${q.position}. ${mEsc(q.prompt)}</strong><span class="pill">${q.score}分</span></div>${input}</div>`
}

async function submitMobileQuiz(){
  if(!mobileActiveQuiz||!confirm('每人只能提交一次，确认提交这份答卷？'))return;
  const answers=mobileActiveQuiz.questions.map(q=>{let answer;if(q.question_type==='single_choice')answer=document.querySelector(`input[name="mq_${q.id}"]:checked`)?.value??null;else if(q.question_type==='multiple_choice')answer=[...document.querySelectorAll(`input[name="mq_${q.id}"]:checked`)].map(x=>x.value);else answer=document.getElementById(`ma_${q.id}`).value;return {question_id:q.id,answer}});
  try{await mApi(`/learning/quizzes/${mobileActiveQuiz.id}/submissions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers,is_preview:false})});toast('提交成功');showMobileView('homeView');loadMobileHome()}catch(e){toast(e.message)}
}

async function openMobileResult(id){
  try{const r=await mApi(`/learning/quizzes/${id}/my-result`).then(x=>x.json());if(!r.released)return toast('成绩尚未发布');mobileResult.innerHTML=`<div class="score">${r.score} / ${r.total_points} 分</div>`+r.answers.map((a,i)=>`<article class="result-row"><strong>${i+1}. ${mEsc(a.prompt)}</strong><p>你的答案：${mEsc(mAnswer(a.answer))}</p><p>得分：${a.awarded_score} / ${a.max_score}</p>${a.reviewer_comment?`<p>批语：${mEsc(a.reviewer_comment)}</p>`:''}${r.show_answers?`<p>参考答案：${mEsc(mAnswer(a.correct_answers))}</p>`:''}</article>`).join('');showMobileView('resultView')}catch(e){toast(e.message)}
}

async function openMobileStats(id){
  try{const [stats,quiz]=await Promise.all([mApi(`/learning/quizzes/${id}/stats`).then(r=>r.json()),mApi(`/learning/quizzes/${id}`).then(r=>r.json())]);mobileStatsTitle.textContent=`答题情况：${quiz.title}`;mobileStats.innerHTML=`<article class="mobile-card"><strong>已提交 ${stats.submitted_count} / ${stats.expected_count} 人</strong><p class="meta">已批阅 ${stats.graded_count} 人 · 未提交 ${stats.pending_count} 人</p></article>`+stats.questions.map(q=>`<article class="mobile-card"><h3>${q.position}. ${mEsc(q.prompt)}</h3><p>${q.correct_rate===null?'等待人工评分':`正确率 ${q.correct_rate}%`} · ${q.answered_count} 人作答</p><details><summary>查看答题人的答案</summary>${q.answers.map(a=>`<p><strong>${mEsc(a.username)}</strong>：${mEsc(mAnswer(a.answer))} ${a.is_correct===null?'（待评）':a.is_correct?'✓':'✗'}</p>`).join('')||'<p class="muted">暂无答案</p>'}</details></article>`).join('');showMobileView('statsView')}catch(e){toast(e.message)}
}

mobilePassword.addEventListener('keydown',e=>{if(e.key==='Enter')mobileLogin()});
if(window.AndroidApp)serverBtn.classList.remove('hidden');
mobileBoot();
