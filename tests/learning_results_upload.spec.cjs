// Run with node; PLAYWRIGHT_MODULE may point to an existing Playwright install.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');

(async () => {
  const browser = await chromium.launch({ channel:'chrome', headless:true });
  try {
    const page = await browser.newPage({ viewport:{width:1280,height:960} });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://learning.test/**', route => route.fulfill({contentType:'text/html',body:'<html></html>'}));
    await page.goto('http://learning.test/');
    const html = read('frontend/index.html');
    const section = html.slice(html.indexOf('<section id="tab-learning"'), html.indexOf('<div id="quizTakePanel"'));
    await page.setContent(section + '<div id="quizResultPanel"><div id="quizResultBody"></div></div></section>');
    for (const file of ['frontend/style.css','frontend/css/main.css','frontend/css/weave.css','frontend/learning.css']) {
      await page.addStyleTag({content:read(file).replace(/@import[^;]+;/g,'')});
    }
    await page.addScriptTag({content:read('frontend/learning.js')});
    const question = {position:1,question_type:'single_choice',prompt:'原题第一行\n原题第二行',options:['选项一','<img src=x onerror=alert(1)>'],max_score:5,answer:'选项一',awarded_score:5};
    await page.evaluate(async question => {
      document.querySelector('#tab-learning').classList.remove('hidden');
      window.api = async () => ({json:async () => ({released:false,answers:[question]})});
      window.showMessage = () => {};
      await viewMyQuizResult(1);
    },question);
    assert.deepEqual(await page.locator('#quizResultBody li').allTextContents(), question.options);
    assert.equal(await page.locator('#quizResultBody img').count(),0);
    assert.doesNotMatch(await page.locator('#quizResultBody').innerText(),/得分：|参考答案：/);
    await page.evaluate(async question => {
      window.api = async () => ({json:async () => ({released:true,score:5,total_points:5,show_answers:false,answers:[question]})});
      await viewMyQuizResult(1);
    },question);
    assert.match(await page.locator('#quizResultBody').innerText(),/得分：5/);
    assert.doesNotMatch(await page.locator('#quizResultBody').innerText(),/参考答案：/);
    await page.evaluate(() => {
      quizResultPanel.classList.add('hidden'); quizEditor.classList.remove('hidden');
      addQuestionEditor('short_answer');
    });
    await page.locator('.question-toolbar').last().getByRole('button',{name:'+ 多选'}).click();
    assert.equal(await page.locator('.question-editor').count(),2);
    assert.equal(await page.locator('.qe-prompt').last().evaluate(el => el===document.activeElement),true);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth),true);

    // Simulate real XHR progress and a partial failure, then retry only the failed file.
    await page.evaluate(() => {
      quizEditor.classList.add('hidden'); showLearningContentForm();
      learningContentTitle.value='附件课程'; window.H={}; window.sent=[]; window.shouldFail=true;
      window.api=async()=>({json:async()=>({id:42})});
      window.closeLearningPanels=()=>{window.didClose=true;};
      window.XMLHttpRequest=class {
        constructor(){this.upload={};}
        open(method,url){this.url=url;}
        setRequestHeader(){}
        send(form){
          const name=form.get('file').name; sent.push(name);
          setTimeout(()=>this.upload.onprogress({lengthComputable:true,loaded:50,total:100}),10);
          setTimeout(()=>{
            this.status=name==='second.md'&&shouldFail?500:201;
            this.responseText=JSON.stringify({detail:'模拟上传失败'}); this.onload();
          },180);
        }
      };
    });
    await page.locator('#learningContentFiles').setInputFiles([
      {name:'first.md',mimeType:'text/markdown',buffer:Buffer.from('one')},
      {name:'second.md',mimeType:'text/markdown',buffer:Buffer.from('two')},
      {name:'third.md',mimeType:'text/markdown',buffer:Buffer.from('three')},
    ]);
    assert.match(await page.locator('#learningContentUploadStatus').innerText(),/已选择 3 个附件/);
    await page.locator('#learningContentSaveBtn').click();
    await page.waitForFunction(()=>document.querySelector('progress')?.value===50);
    assert.equal(await page.locator('#learningContentSaveBtn').isDisabled(),true);
    await page.waitForFunction(()=>!learningContentSaveBtn.disabled);
    assert.match(await page.locator('#learningContentUploadStatus').innerText(),/成功 2 \/ 3/);
    assert.deepEqual(await page.evaluate(()=>[...learningContentFiles.files].map(f=>f.name)),['second.md']);
    await page.evaluate(()=>{window.shouldFail=false;});
    await page.locator('#learningContentSaveBtn').click();
    await page.waitForFunction(()=>window.didClose);
    assert.deepEqual(await page.evaluate(()=>sent),['first.md','second.md','third.md','second.md']);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);

    const mobile = await browser.newPage({viewport:{width:390,height:844}});
    mobile.on('pageerror',error=>errors.push(error.message));
    await mobile.route('http://learning.test/**',route=>route.fulfill({contentType:'text/html',body:read('frontend/mobile.html').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link[^>]+>/g,'')}));
    await mobile.goto('http://learning.test/mobile');
    await mobile.addStyleTag({content:read('frontend/mobile.css')});
    await mobile.addScriptTag({content:read('frontend/mobile.js')});
    await mobile.evaluate(async question=>{
      window.mApi=async()=>({json:async()=>({released:false,answers:[question]})});
      await openMobileResult(1);
    },question);
    assert.deepEqual(await mobile.locator('#mobileResult li').allTextContents(),question.options);
    assert.doesNotMatch(await mobile.locator('#mobileResult').innerText(),/得分：|参考答案：/);
    await mobile.evaluate(async question=>{
      window.mApi=async url=>({json:async()=>url.endsWith('/stats')?{submitted_count:1,expected_count:1,graded_count:1,pending_count:0,questions:[{...question,correct_answers:['选项一'],answered_count:1,correct_rate:100,answers:[]}]}:{title:'讲解原题'}});
      await openMobileStats(1);
    },question);
    assert.deepEqual(await mobile.locator('#mobileStats li').allTextContents(),question.options);
    assert.match(await mobile.locator('#mobileStats').innerText(),/参考答案：选项一/);
    assert.equal(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    if(process.env.RESULT_SCREENSHOT) await mobile.screenshot({path:process.env.RESULT_SCREENSHOT,fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('PASS: original questions, hidden answers, mobile author review, bottom toolbar, multi-file progress, partial failure retry, narrow layouts.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
