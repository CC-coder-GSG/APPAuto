// Run: node tests/ai_case_generation.spec.cjs (requires playwright or PLAYWRIGHT_MODULE).
// Exercises the real frontend modules with an intercepted API; no application data is touched.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    const requests = [];
    const dialogs = [];
    let acceptConfirmation = false;
    let failSubmission = false;
    page.on('pageerror', error => errors.push(error.stack));
    page.on('dialog', async dialog => {
      dialogs.push(dialog.message());
      await (acceptConfirmation ? dialog.accept() : dialog.dismiss());
    });
    const cases = Array.from({ length: 40 }, (_, index) => ({
      title: `小用例 ${index + 1}`, case_type: 'feature', priority: 2,
      precondition: '', keywords: '', module_id: 0, stage: '',
      steps: [{ step: '检查操作', expected: '成功' }],
    }));
    const results = new Map([
      [41, { id: 1, story_id: 41, execution_id: 99, ai_status: 'success', test_cases: cases }],
      [42, { id: 2, story_id: 42, execution_id: 99, ai_status: 'failed', ai_error_message: '生成失败' }],
    ]);
    const workspace = {
      id: 'workspace', revision: 1, base_url: 'http://zentao.test',
      context: { product_name: '产品', product_id: 1, story_id: 41, story_title: '需求',
        types: { feature: '功能测试' }, stages: {}, modules: [{ id: 0, name: '根模块' }], module_options_complete: true },
      sources: cases.map((content, index) => ({ index, content })),
      drafts: [{ id: 'draft', sources: [0], content: cases[0], submit_status: 'draft', parents: [] }],
    };
    const html = `<!doctype html><meta charset="utf-8"><style>.hidden{display:none!important}</style>
      <section id="tab-mine"><div id="mineCards"></div></section><button id="other" onclick="window.otherActions++">其他操作</button>
      <select id="zentaoAiExecutionSelect"><option value="99">执行</option></select>
      <input type="checkbox" id="zentaoAiSelectAll"><textarea id="zentaoAiUserNote"></textarea>
      <div id="zentaoAiSubmitState"></div><div id="zentaoAiResult"></div>
      <script>
        window.messages=[]; window.otherActions=0; window.handlers={};
        window.showMessage=(...args)=>messages.push(args);
        window.canAccessRawTab=()=>true;
        window.OmniQASSE={subscribe:(event, callback)=>handlers[event]=callback};
      </script>
      <script type="module">
        import '/static/js/ai-tasks.js';
        import '/static/js/components/ai-result-modal.js';
        import '/static/js/components/entity-preview-modal.js';
        import '/static/js/tabs/mine.js';
        import '/static/js/tabs/zentao-ai.js';
        import {state} from '/static/js/state.js';
        state.currentMineData=[
          {id:1,title:'已有用例',zentao_req_id:'r#41',zentao_story_id:41,zentao_execution_id:99,test_cases:[{id:1,zentao_case_id:'u#100'}]},
          {id:2,title:'失败需求',zentao_req_id:'r#42',zentao_execution_id:99,test_cases:[]},
          {id:3,title:'新需求',zentao_req_id:'r#43',zentao_story_id:43,zentao_execution_id:99,test_cases:[]},
          {id:4,title:'未关联执行',zentao_req_id:'r#44',zentao_story_id:44,test_cases:[]}
        ];
        window.OmniQAMineTab.renderMineCards(); window.ready=true;
      </script>`;
    await page.route('http://app.test/**', async route => {
      const url = new URL(route.request().url());
      const send = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname.startsWith('/static/')) {
        return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(root, 'frontend', url.pathname.slice(8)), 'utf8') });
      }
      if (url.pathname.endsWith('/batch-latest')) {
        return send({ latest: Object.fromEntries(results) });
      }
      const latest = url.pathname.match(/\/story\/(\d+)\/latest$/);
      if (latest) return send(results.get(Number(latest[1])) || null);
      if (url.pathname.endsWith('/workspace')) return send(workspace);
      if (url.pathname.endsWith('/generate-and-save')) {
        requests.push(route.request().postDataJSON());
        if (failSubmission) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"生成服务暂不可用"}' });
        const data = requests.at(-1);
        const batch_id = `batch-${requests.length}`;
        data.story_ids.forEach(story_id => results.set(story_id, {
          id: 10 + requests.length, story_id, execution_id: data.execution_id, ai_status: 'pending', batch_id,
        }));
        return send({ batch_id, story_ids: data.story_ids, status: 'pending', expected_duration_seconds: 300 });
      }
      const batch = url.pathname.match(/\/batch\/(batch-\d+)$/);
      if (batch) {
        const rows = [...results.values()].filter(row => row.batch_id === batch[1]);
        return send({ results: rows, pending: rows.filter(row => row.ai_status === 'pending').length,
          success: rows.filter(row => row.ai_status === 'success').length, failed: rows.filter(row => row.ai_status === 'failed').length });
      }
      throw new Error(`Unexpected API call: ${url.pathname}`);
    });
    await page.goto('http://app.test/');
    await page.waitForFunction(() => window.ready);
    for (const name of ['frontend/style.css', 'frontend/css/main.css', 'frontend/css/weave.css']) {
      await page.addStyleTag({ content: fs.readFileSync(path.join(root, name), 'utf8').replace(/@import[^;]+;/g, '') });
    }
    const generate = page.locator('[data-ai-generate-story-id="43"]');
    assert.equal(await page.locator('[data-req-id="3"] summary .ai-generate-chip').count(), 1);
    assert.equal(await generate.evaluate(element => element.previousElementSibling.classList.contains('ai-result-slot')), true);
    const card = page.locator('[data-req-id="3"]');
    const wasOpen = await card.evaluate(element => element.open);
    if (process.env.AI_UI_SCREENSHOT) await page.screenshot({ path: process.env.AI_UI_SCREENSHOT });
    assert.equal(await page.locator('[data-req-id="1"] [data-ai-generate-story-id]').count(), 0);
    assert.equal(await page.locator('[data-ai-generate-story-id="44"]').isDisabled(), true);
    await generate.click();
    assert.equal(requests.length, 0, 'Cancellation must not submit');
    assert.equal(await card.evaluate(element => element.open), wasOpen, 'Clicking the chip must not toggle the requirement card');
    acceptConfirmation = true;
    await generate.click();
    await page.waitForFunction(() => document.querySelector('#aiTaskBanner')?.textContent.includes('用例正在生成，请稍后'));
    assert.deepEqual(requests[0], { execution_id: 99, story_ids: [43], select_all: false, user_note: '' });
    assert.equal(await generate.isDisabled(), true);
    await page.evaluate(() => window.OmniQAMineTab.renderMineCards());
    assert.equal(await generate.isDisabled(), true, 'Rerender during generation must prevent repeat submissions');
    await page.locator('#other').click();
    assert.equal(await page.evaluate(() => otherActions), 1, 'Generation must allow other operations');
    assert.doesNotMatch(await page.locator('#aiTaskBanner').innerText(), /剩余|预估|分钟|\d+s/);
    results.get(43).ai_status = 'success';
    results.get(43).test_cases = cases;
    await page.evaluate(() => handlers.zentao_ai_batch_completed({ payload: { batch_id: 'batch-1', success: 1, failed: 0 } }));
    await page.getByText('AI 用例生成完成，成功 1 条', { exact: true }).waitFor();
    await page.getByRole('button', { name: '查看结果' }).click();
    await page.locator('#aiResultRegenerate').waitFor({ state: 'visible' });
    assert.equal((await page.locator('#aiResultModalTitle').innerText()).trim(), 's#43');
    assert.equal(await page.locator('#aiResultRegenerate').isEnabled(), true);
    await page.evaluate(() => window.OmniQAStoryAI.closeModal());

    // Both failed and successful result dialogs allow regeneration with confirmation.
    await page.evaluate(() => window.OmniQAStoryAI.openForStory(42));
    await page.getByText('生成失败', { exact: true }).waitFor();
    acceptConfirmation = false;
    await page.locator('#aiResultRegenerate').click();
    assert.equal(requests.length, 1);
    acceptConfirmation = true;
    await page.locator('#aiResultRegenerate').click();
    await page.locator('#aiResultModal').waitFor({ state: 'hidden' });
    assert.equal(requests[1].story_ids[0], 42);
    assert.match(dialogs.at(-1), /是否重新生成/);
    results.get(42).ai_status = 'failed';
    await page.evaluate(() => handlers.zentao_ai_batch_completed({ payload: { batch_id: 'batch-2', success: 0, failed: 1 } }));
    await page.getByText('AI 处理失败，全部 1 条未生成', { exact: true }).waitFor();
    await page.evaluate(() => window.OmniQAStoryAI.openForStory(42));
    failSubmission = true;
    await page.locator('#aiResultRegenerate').click();
    await page.waitForFunction(() => messages.some(message => message[0] === '生成服务暂不可用'));
    assert.equal(await page.locator('#aiResultRegenerate').isDisabled(), false);
    assert.equal(await page.locator('#aiResultModal').isVisible(), true, 'Submission failure must allow retry in place');
    failSubmission = false;

    // The editor retains the independently scrolling source list after every selection.
    await page.evaluate(() => window.OmniQAStoryAI.openForStory(41));
    await page.getByRole('button', { name: '整理并提交禅道' }).click();
    const editor = page.locator('#aiCaseEditor');
    const sourceScroller = editor.locator('[data-source-scroll]');
    await sourceScroller.waitFor();
    for (const index of [10, 11, 12]) {
      const checkbox = editor.locator(`[data-source="${index}"]`);
      await checkbox.scrollIntoViewIfNeeded();
      const before = await sourceScroller.evaluate(element => element.scrollTop);
      const outerBefore = await editor.evaluate(element => element.scrollTop);
      assert.ok(before > 0);
      await checkbox.check();
      assert.equal(await sourceScroller.evaluate(element => element.scrollTop), before);
      assert.equal(await editor.evaluate(element => element.scrollTop), outerBefore);
    }
    const savedScroll = await sourceScroller.evaluate(element => element.scrollTop);
    await editor.locator('[data-draft="draft"]').check();
    await editor.locator('[data-action="preview"]').click();
    await editor.locator('[data-action="cancel-preview"]').click();
    assert.equal(await sourceScroller.evaluate(element => element.scrollTop), savedScroll);
    await editor.locator('[data-action="next"]').click();
    assert.equal(await sourceScroller.evaluate(element => element.scrollTop), 0);
    await editor.locator('[data-source="35"]').scrollIntoViewIfNeeded();
    await editor.locator('[data-source-search]').fill('小用例');
    assert.equal(await sourceScroller.evaluate(element => element.scrollTop), 0);
    await editor.locator('[data-action="close"]').click();
    await page.evaluate(() => window.OmniQAStoryAI.closeModal());

    // The original Zentao AI entry uses the same notice, including long-running batches.
    await page.evaluate(() => {
      window.OmniQAZentaoAiTab.onStoryToggle(45, true);
      return window.OmniQAZentaoAiTab.submit();
    });
    assert.equal(requests.at(-1).story_ids[0], 45);
    assert.doesNotMatch(await page.evaluate(() => JSON.stringify(messages)), /大约需要|分钟/);
    await page.evaluate(() => window.OmniQAStoryAI.startBatchWatch({ batch_id: 'long-running', story_ids: [46], started_at: Date.now() - 3600000 }));
    assert.doesNotMatch(await page.locator('#aiTaskBanner').innerText(), /预估|剩余/);
    assert.deepEqual(errors, []);
    console.log('AI generation UI passed: scroll retention, confirmations, retries, background operations, completion notifications and shared waiting notice.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
