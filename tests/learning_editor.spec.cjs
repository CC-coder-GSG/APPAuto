// Run: node tests/learning_editor.spec.cjs (requires playwright, or PLAYWRIGHT_MODULE).
// Uses the real editor in Chromium and a stub API; never touches application data.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
    const html = read('frontend/index.html');
    await page.setContent(`<meta charset="utf-8"><section class="learning-center">${html.slice(html.indexOf('<div id="quizEditor"'), html.indexOf('<div id="quizTakePanel"'))}</section>`);
    for (const name of ['frontend/style.css', 'frontend/css/main.css', 'frontend/css/weave.css', 'frontend/learning.css']) {
      await page.addStyleTag({ content: read(name).replace(/@import[^;]+;/g, '') });
    }
    await page.addScriptTag({ content: read('frontend/learning.js') });
    await page.evaluate(() => {
      quizEditor.classList.remove('hidden');
      window.H = {};
      window.saved = [];
      window.messages = [];
      window.api = async (url, options) => {
        saved.push(JSON.parse(options.body));
        return { json: async () => ({}) };
      };
      window.showMessage = (...args) => messages.push(args);
      window.closeLearningPanels = () => {};
      addQuestionEditor('single_choice');
      quizTitle.value = '出题交互回归';
    });
    const cards = page.locator('.question-editor');
    let single = cards.nth(0);
    await single.locator('.qe-prompt').fill('请选择正确的测试方法');
    for (const [i, text] of ['功能测试', '边界值测试', '兼容性测试', '性能测试'].entries()) {
      await single.locator('.qe-option-text').nth(i).fill(text);
    }
    await single.locator('.qe-option-correct').nth(2).check();
    await single.locator('.qe-option-row').nth(0).locator('button').click();
    assert.equal(await single.locator('.qe-option-letter').allTextContents().then(x => x.join('')), 'ABC');
    assert.equal(await single.locator('.qe-option-correct').nth(1).isChecked(), true);
    await single.locator('.qe-option-text').nth(1).fill('兼容性测试（更新）');
    await single.locator('.qe-option-correct').nth(0).check();
    assert.equal(await single.locator('.qe-option-correct:checked').count(), 1);
    await single.locator('.qe-option-correct').nth(1).check();
    await single.locator('summary').click();
    assert.match(await single.locator('.qe-preview-body').innerText(), /兼容性测试（更新）.*正确答案/);

    await page.evaluate(() => addQuestionEditor('multiple_choice'));
    const multi = cards.nth(1);
    await multi.locator('.qe-prompt').fill('选择以下安全显示的文本');
    for (const [i, text] of ['<script>alert(1)</script>', '第二项', '第三项', '第四项'].entries()) {
      await multi.locator('.qe-option-text').nth(i).fill(text);
    }
    await multi.locator('.qe-option-correct').nth(0).check();
    await multi.locator('.qe-option-correct').nth(3).check();
    await multi.locator('.qe-option-row').nth(1).locator('button').click();
    await multi.locator('.qe-add-option').click();
    await multi.locator('.qe-option-text').last().fill('新增项');
    assert.equal(await multi.locator('.qe-option-letter').last().textContent(), 'D');

    await page.evaluate(() => addQuestionEditor('fill_blank'));
    const fill = cards.nth(2);
    await fill.locator('.qe-prompt').fill('平台名称是 OmniQA，请填写。');
    await fill.locator('.qe-prompt').evaluate(input => { input.focus(); input.setSelectionRange(6, 12); });
    await fill.locator('.qe-insert-blank').click();
    assert.equal(await fill.locator('.qe-reference-text').inputValue(), 'OmniQA');
    assert.match(await fill.locator('.qe-prompt').inputValue(), /______.*，请填写/);
    assert.equal(await fill.locator('.qe-count').isVisible(), false);
    await fill.locator('.qe-add-reference').click();
    await fill.locator('.qe-reference-text').last().fill('Omni QA');
    await fill.locator('.qe-mode').selectOption('match_count');
    await fill.locator('.qe-count').fill('3');
    await page.evaluate(() => saveQuizDraft());
    assert.equal(await page.evaluate(() => saved.length), 0);
    assert.match(await fill.locator('.qe-error').innerText(), /不能超过参考答案数量/);
    await fill.locator('.qe-count').fill('2');
    await page.evaluate(() => addQuestionEditorFromData({ question_type:'short_answer', prompt:'请说明测试依据', score:5 }));
    await page.evaluate(() => saveQuizDraft());
    const first = await page.evaluate(() => saved.at(-1));
    assert.deepEqual(first.questions[0].options, ['边界值测试', '兼容性测试（更新）', '性能测试']);
    assert.deepEqual(first.questions[0].correct_answers, ['兼容性测试（更新）']);
    assert.deepEqual(first.questions[1].correct_answers, ['<script>alert(1)</script>', '第四项']);
    assert.deepEqual(first.questions[2].correct_answers, ['OmniQA', 'Omni QA']);
    assert.equal(first.questions[2].match_count, 2);
    assert.deepEqual(first.questions[3].correct_answers, []);

    // Existing draft data round-trips without injecting labels into option values.
    await page.evaluate(() => {
      quizQuestionEditors.innerHTML = '';
      saved.at(-1).questions.forEach(addQuestionEditorFromData);
    });
    await page.evaluate(() => saveQuizDraft());
    assert.deepEqual(await page.evaluate(() => saved.at(-1)), first);
    single = cards.nth(0);
    await single.locator('.qe-option-row').nth(1).locator('button').click();
    assert.equal(await single.locator('.qe-remove-option:disabled').count(), 2);
    const savedCount = await page.evaluate(() => saved.length);
    await page.evaluate(() => saveQuizDraft());
    assert.equal(await page.evaluate(() => saved.length), savedCount);
    assert.match(await single.locator('.qe-error').innerText(), /请选择正确答案/);
    await single.locator('.qe-option-correct').first().check();
    await single.locator('.qe-option-text').last().fill('边界值测试');
    await page.evaluate(() => saveQuizDraft());
    assert.match(await single.locator('.qe-error').innerText(), /不能重复/);
    await single.locator('.qe-option-text').last().fill('性能测试');
    // An irrelevant stale match count must not block exact matching.
    await cards.nth(2).locator('.qe-mode').selectOption('exact');
    await page.evaluate(() => saveQuizDraft());
    assert.equal(await page.evaluate(() => saved.at(-1).questions[2].match_count), 1);

    await single.locator('summary').click();
    if (process.env.EDITOR_SCREENSHOT) await page.screenshot({ path: process.env.EDITOR_SCREENSHOT, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);

    // Android loads this same /mobile page: render and submit editor-generated values.
    const mobile = await browser.newPage({ viewport: { width:390, height:844 } });
    mobile.on('pageerror', error => errors.push(error.message));
    mobile.on('dialog', dialog => dialog.accept());
    await mobile.route('http://learning-editor.test/**', route => {
      if (route.request().url().endsWith('/mobile')) {
        return route.fulfill({ contentType:'text/html', body:read('frontend/mobile.html').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '') });
      }
      return route.fulfill({ contentType:'text/css', body:read('frontend/mobile.css') });
    });
    await mobile.goto('http://learning-editor.test/mobile');
    await mobile.addScriptTag({ content:read('frontend/mobile.js') });
    await mobile.evaluate(async quiz => {
      window.mApi = async (url, options) => {
        if (options?.method === 'POST') window.mobileSubmission = JSON.parse(options.body);
        return { json:async () => url.endsWith('/123') ? quiz : [] };
      };
      await openMobileQuiz(123);
    }, { ...first, id:123, questions:first.questions.map((q, i) => ({ ...q, id:i + 1, position:i + 1 })) });
    await mobile.locator('input[name="mq_1"]').nth(1).check();
    await mobile.locator('input[name="mq_2"]').nth(0).check();
    await mobile.locator('input[name="mq_2"]').nth(2).check();
    await mobile.locator('#ma_3').fill('OmniQA; Omni QA');
    await mobile.locator('#ma_4').fill('检查边界值与兼容性');
    assert.match(await mobile.locator('.mobile-question').nth(2).innerText(), /______/);
    await mobile.evaluate(() => submitMobileQuiz());
    assert.deepEqual(await mobile.evaluate(() => mobileSubmission.answers), [
      { question_id:1, answer:'兼容性测试（更新）' },
      { question_id:2, answer:['<script>alert(1)</script>', '第四项'] },
      { question_id:3, answer:'OmniQA; Omni QA' },
      { question_id:4, answer:'检查边界值与兼容性' }
    ]);
    assert.deepEqual(errors, []);
    console.log('PASS: option numbering/selection/deletion, text edits, blank insertion, validation, draft round-trip, responsive layout, mobile rendering/submission for all four types, no browser errors.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
