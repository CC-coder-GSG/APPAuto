// Real workbench and change dialog; all network traffic is intercepted.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    const errors = [], calls = [];
    page.on('pageerror', e => errors.push(e.stack));
    let canManage = true, failSecond = true;
    const requirement = {id: 1, title: '变更需求', zentao_req_id: 'r#4030', zentao_story_id: 4030,
      major_version_id: 470, major_version_name: 'V4.0.5.0', final_test: true,
      case_completed: true, test_completed: true, test_cases: [], free_bugs: [],
      story_tasks: [{task_id: 101, type: 'test', status: 'closed', task_story_change_pending: true}],
      test_tasks: [], change_retests: []};
    const tasks = [
      {task_id: 101, name: '当前版本任务', status: 'closed', execution_name: 'V4.0.5.0',
        mode: 'reactivate', story_version: 2, latest_story_version: 6, suggested_owner_id: 7, owner_reason: '上次完成者'},
      {task_id: 102, name: '<img src=x onerror="window.injected=1">旧任务', status: 'closed', execution_name: 'V4.0.4.10',
        mode: 'create', story_version: 2, latest_story_version: 6, suggested_owner_id: null, owner_reason: '请手动选择'},
    ];
    const html = `<!doctype html><meta charset="utf-8"><select id="mineMajorSelect"><option value="470">V4.0.5.0</option></select><div id="mineCards"></div>
      <script type="module">
        import {state} from '/static/js/state.js';
        import {renderMineCards} from '/static/js/tabs/mine.js';
        state.currentUser={id:1,role:'admin'};state.currentMineData=${JSON.stringify([requirement])};
        renderMineCards();window.ready=true;
      </script>`;
    await page.route('http://requirement-change.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (url.pathname.startsWith('/static/')) {
        return route.fulfill({contentType: 'text/javascript', body: fs.readFileSync(path.join(root, 'frontend', url.pathname.slice(8)))});
      }
      if (url.pathname === '/requirements/1/change-retest') {
        if (route.request().method() === 'GET') return route.fulfill({json: {
          title: '<script>bad</script>变更需求', major_version_name: 'V4.0.5.0', can_manage: canManage,
          tasks, owners: [{id: 7, name: '王超'}, {id: 8, name: '李四'}], rounds: []}});
        const body = route.request().postDataJSON();
        calls.push(body);
        if (body.source_task_id === 102 && failSecond) return route.fulfill({status: 502, json: {detail: '禅道结果待核实，请重试'}});
        return route.fulfill({json: {ok: true, task_id: body.source_task_id + 1000}});
      }
      if (url.pathname === '/workbench/mine') return route.fulfill({json: [requirement]});
      return route.fulfill({json: {}});
    });
    await page.goto('http://requirement-change.test/');
    await page.waitForFunction(() => window.ready);
    const card = page.locator('.mine-req-card');
    assert.equal(await card.locator('summary [style*="line-through"]').count(), 0, 'changed requirement must not look complete');
    await card.getByRole('button', {name: '处理变更', exact: true}).click();
    const dialog = page.getByRole('dialog', {name: '处理需求变更'});
    await dialog.getByText('重新激活并指派', {exact: true}).waitFor();
    assert.match(await dialog.innerText(), /在当前大版本新建重测任务/);
    assert.equal(await dialog.locator('img,script').count(), 0);
    await dialog.getByLabel('选择任务 101', {exact: true}).check();
    await dialog.getByLabel('选择任务 102', {exact: true}).check();
    await dialog.getByRole('button', {name: '派发选中的重测任务'}).click();
    assert.equal(calls.length, 0, 'validate all assignees before dispatching');
    await dialog.getByLabel('任务 102 的负责人').selectOption('8');
    await dialog.getByRole('button', {name: '派发选中的重测任务'}).click();
    await dialog.getByText('已成功派发 1 / 2 个任务。失败项可核对后重试。').waitFor();
    assert.deepEqual(calls, [
      {source_task_id: 101, owner_id: 7, expected_story_version: 6},
      {source_task_id: 102, owner_id: 8, expected_story_version: 6},
    ]);
    assert(await dialog.getByLabel('选择任务 101', {exact: true}).isDisabled());
    failSecond = false;
    await dialog.getByRole('button', {name: '派发选中的重测任务'}).click();
    await dialog.getByText('已成功派发 1 / 1 个任务。失败项可核对后重试。').waitFor();
    assert.equal(calls.filter(c => c.source_task_id === 101).length, 1);
    await dialog.getByRole('button', {name: '关闭', exact: true}).click();
    canManage = false;
    await card.getByRole('button', {name: '处理变更', exact: true}).click();
    await dialog.getByText('你可以查看变更；派发重测需要需求分配权限。').waitFor();
    assert.equal(await dialog.getByRole('button', {name: '派发选中的重测任务'}).count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.deepEqual(errors, []);
    console.log('PASS: changed completion badge, version scope, escaped content, owner validation, partial results, safe retries, read-only permissions');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
