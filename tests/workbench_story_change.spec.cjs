// Real requirement cards with intercepted static files; no live task is modified.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.stack));
    const html = `<!doctype html><meta charset="utf-8">
      <link rel="stylesheet" href="/static/css/main.css"><link rel="stylesheet" href="/static/css/weave.css">
      <link rel="stylesheet" href="/static/css/requirement-tasks.css">
      <style>body{padding:24px;background:#f5f5f4}section{max-width:1200px;margin:auto}</style>
      <section id="tab-mine"><div id="mineCards"></div></section>
      <script type="module">
        import { renderMineCards, selectRequirementTask } from '/static/js/tabs/mine.js';
        import { state } from '/static/js/state.js';
        state.currentUser={id:1};
        window.changeCalls=[];window.previewCalls=[];
        window.OmniQAPreview={openTaskStoryChange(id){changeCalls.push(id)},openTask(id){previewCalls.push(id)}};
        window.showRequirements=data=>{state.currentMineData=data;renderMineCards()};
        window.selectTask=selectRequirementTask;window.ready=true;
      </script>`;
    await page.route('http://story-change.test/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      assert.ok(url.pathname.startsWith('/static/'), 'Only static files are expected');
      const file = path.join(root, 'frontend', url.pathname.slice(8));
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    });
    await page.goto('http://story-change.test/');
    await page.waitForFunction(() => window.ready);
    const changed = { task_story_change_pending: true, task_story_version: 1, task_latest_story_version: 3 };
    const developer = id => ({ task_id: id, name: `研发任务 ${id}`, status: 'doing', assigned_to_name: '研发人员', ...changed });
    const requirement = { id: 1, title: '需求变更显示验证', zentao_task_id: 101, zentao_parent_task_id: 100,
      zentao_task_status: 'doing', task_assigned_to_me: true, case_completed: true, test_completed: false,
      test_cases: [], free_bugs: [], story_tasks: [developer(201), developer(202)], ...changed };
    const show = async data => page.evaluate(data => window.showRequirements(data), data);
    const card = page.locator('.mine-req-card[data-req-id="1"]');
    await show([requirement]);
    assert.equal(await card.locator('.qa-story-change-badge').count(), 1, 'One prominent change notice per requirement, rather than three identical labels');
    assert.equal(await card.locator('.qa-task-chip').count(), 2);
    const beforeOpen = await card.evaluate(el => el.open);
    await card.locator('.qa-story-change-badge').click();
    assert.deepEqual(await page.evaluate(() => changeCalls), [101]);
    assert.equal(await card.evaluate(el => el.open), beforeOpen);
    const devChip = card.locator('.qa-task-chip').filter({ hasText: '研发任务 201' });
    assert.match(await devChip.innerText(), /研发人员/);
    assert.match(await devChip.innerText(), /变更待确认/);
    await devChip.click();
    assert.deepEqual(await page.evaluate(() => changeCalls), [101, 201], 'Developer changes still open the correct task');
    assert.equal(await card.evaluate(el => el.open), beforeOpen);

    const managed = id => ({ task_id: id, name: `测试任务 ${id}`, status: 'doing', assigned_to_me: true, can_operate: true, ...changed });
    requirement.has_multiple_test_tasks = true;
    requirement.test_tasks = [managed(101), managed(102)];
    requirement.story_tasks = [developer(201), developer(201), managed(102)];
    await show([requirement]);
    assert.equal(await card.locator('.qa-task-chip').count(), 1, 'Managed tasks and duplicate references are not repeated in story chips');
    assert.match(await card.locator('[data-mine-task="102"]').textContent(), /待确认变更/, 'Other managed tasks retain their pending status in the selector');
    await page.evaluate(() => window.selectTask(1, 102));
    assert.equal(await card.locator('.qa-story-change-badge').count(), 1);
    await card.locator('.qa-story-change-badge').click();
    assert.equal(await page.evaluate(() => changeCalls.at(-1)), 102, 'Change confirmation follows the selected task');
    assert.equal(await card.locator('label').filter({ hasText: '测试完成' }).locator('input').isDisabled(), true);
    assert.match(await card.locator('.qa-task-selector-heading').innerText(), /#102.*待确认变更/s);

    requirement.task_story_change_pending = false;
    requirement.test_tasks.forEach(t => { t.task_story_change_pending = false; });
    requirement.story_tasks = [developer(201), { ...developer(202), task_story_change_pending: false }];
    await show([requirement]);
    assert.equal(await card.locator('.qa-story-change-badge').count(), 0, 'Confirmed managed tasks clear their notice without hiding pending developer tasks');
    assert.match(await card.locator('.qa-task-chip').filter({ hasText: '研发任务 201' }).innerText(), /变更待确认/);
    const normalChip = card.locator('.qa-task-chip').filter({ hasText: '研发任务 202' });
    await normalChip.click();
    assert.deepEqual(await page.evaluate(() => previewCalls), [202]);
    assert.match(await normalChip.innerText(), /进行中/);
    const style = await normalChip.evaluate(el => ({ fontSize: getComputedStyle(el).fontSize, fontWeight: getComputedStyle(el).fontWeight }));
    const pendingStyle = await card.locator('.qa-task-chip').filter({ hasText: '研发任务 201' }).evaluate(el => ({ fontSize: getComputedStyle(el).fontSize, fontWeight: getComputedStyle(el).fontWeight }));
    assert.deepEqual(pendingStyle, style);
    assert.deepEqual(errors, []);
    await show([{ ...requirement, task_story_change_pending: true, test_tasks: [], has_multiple_test_tasks: false,
      story_tasks: [developer(201), developer(202)] }]);
    if (process.env.STORY_CHANGE_SCREENSHOT) await card.screenshot({ path: process.env.STORY_CHANGE_SCREENSHOT });
    console.log('PASS: single requirement notice, named developer changes, task deduplication, selected-task confirmation, and unchanged compact styling');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
