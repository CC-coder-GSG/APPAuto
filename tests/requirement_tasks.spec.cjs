// Real workbench/assignment markup and modules, intercepted APIs; no live task is changed.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [], operations = [], edits = [];
    page.on('pageerror', err => errors.push(err.stack));
    page.on('dialog', dialog => dialog.type() === 'prompt' ? dialog.accept('1') : dialog.accept());
    let finalTest = false, failOperation = false;
    const tasks = [
      { task_id: 101, name: '任务一', status: 'wait', estimate: 4, assigned_to: 'alice', assigned_to_me: true, can_operate: true },
      { task_id: 102, name: '任务二 <img src=x onerror=window.injected=true>', status: 'pause', estimate: 7, assigned_to: 'alice', assigned_to_me: true, can_operate: true },
      { task_id: 103, name: '其他人员任务', status: 'done', estimate: 3, assigned_to: 'bob', assigned_to_me: false, can_operate: false },
    ].map(t => ({ ...t, type: 'test', has_time_tracking: false, est_started: '2026-10-22', deadline: '2026-10-23' }));
    const done = t => ['done', 'closed'].includes(t.status);
    const workbench = () => [{ id: 1, zentao_req_id: 'r#333', title: '同一需求下的多任务验证',
      major_version_id: 1, major_version_name: 'V1', zentao_story_id: 333, zentao_task_id: 101,
      zentao_task_status: tasks[0].status, task_assigned_to_me: true, estimated_test_hours: 4,
      final_test: finalTest, case_completed: true,
      test_completed: (finalTest ? tasks.filter(t => t.assigned_to_me) : tasks).every(done),
      all_linked_test_tasks_completed: tasks.every(done), has_multiple_test_tasks: true,
      test_tasks: tasks, test_cases: [], free_bugs: [], story_tasks: [],
    }];
    const index = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
    const section = name => {
      const start = index.indexOf(`<section id="tab-${name}"`);
      return index.slice(start, index.indexOf('</section>', start) + 10).replace('class="hidden card"', 'class="card"');
    };
    const html = `<!doctype html><meta charset="utf-8">
      <link rel="stylesheet" href="/static/css/main.css"><link rel="stylesheet" href="/static/css/weave.css">
      <link rel="stylesheet" href="/static/css/assign-progress.css"><link rel="stylesheet" href="/static/css/requirement-tasks.css">
      <style>body{padding:24px;background:#f5f5f4}section{max-width:1200px;margin:auto}</style>
      ${section('mine')}${section('assign')}
      <script>window.currentSoftwareId=1;window.messages=[];window.showMessage=(...args)=>messages.push(args);
      window.users=[{id:1,username:'alice',display_name:'A',zentao_account:'alice'},{id:2,username:'bob',display_name:'B',zentao_account:'bob'}];
      window.versions=[{id:1,version_type:'major',version_no:'V1'},{id:2,version_type:'minor',version_no:'V1.1',parent_id:1}];</script>
      <script type="module">
      import '/static/js/components/entity-preview-modal.js';
      import * as mine from '/static/js/tabs/mine.js';
      import * as assign from '/static/js/tabs/assign.js';
      import {state} from '/static/js/state.js';
      Object.assign(window,mine,assign);window.mine=mine;
      state.currentUser={id:1,zentao_account:'alice'};state.users=window.users;state.versions=window.versions;
      document.getElementById('mineMajorSelect').innerHTML='<option value="1">V1</option>';
      document.getElementById('mineMinorSelect').innerHTML='<option value="2">V1.1</option>';
      document.getElementById('assignMajorSelect').innerHTML='<option value="1">V1</option>';
      await mine.loadMyWorkbench();await assign.loadAssignBoard({skipSync:true});window.ready=true;
      </script>`;
    await page.route('http://requirement-tasks.test/**', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(root, 'frontend', url.pathname.slice(8));
        return route.fulfill({ body: fs.readFileSync(file), contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript' });
      }
      if (url.pathname === '/workbench/mine') return route.fulfill({ json: workbench() });
      if (url.pathname === '/requirements/admin/list') return route.fulfill({ json: [{ ...workbench()[0], owner_id: 1 }] });
      if (url.pathname === '/requirements/admin/progress') return route.fulfill({ json: { owners: [], summary: {} } });
      if (url.pathname === '/final-test/status') return route.fulfill({ json: { enabled: finalTest } });
      if (url.pathname === '/requirements/1/linked-tasks') return route.fulfill({ json: { tasks } });
      const op = url.pathname.match(/^\/requirements\/1\/linked-tasks\/(\d+)\/operate$/);
      if (op) {
        const taskId = Number(op[1]), body = request.postDataJSON();
        operations.push({ taskId, ...body });
        assert.notEqual(taskId, 103, 'another user task must never be operated');
        if (failOperation) { failOperation = false; return route.fulfill({ status: 502, json: { detail: '任务操作未生效' } }); }
        const task = tasks.find(t => t.task_id === taskId);
        if (body.action === 'set_time') task.estimate = body.hours;
        else task.status = { start: 'doing', pause: 'pause', finish: 'done', reactivate: 'doing' }[body.action];
        return route.fulfill({ json: { ok: true, task } });
      }
      const edit = url.pathname.match(/^\/requirements\/1\/linked-tasks\/(\d+)$/);
      if (edit && request.method() === 'PUT') {
        edits.push({ taskId: Number(edit[1]), ...request.postDataJSON() });
        return route.fulfill({ json: { ok: true } });
      }
      if (url.pathname === '/workbench/preflight-refresh') return route.fulfill({ json: {} });
      return route.fulfill({ json: [] });
    });
    await page.goto('http://requirement-tasks.test/');
    await page.waitForFunction(() => window.ready);
    const card = page.locator('.mine-req-card[data-req-id="1"]');
    const taskLabel = () => card.locator('.qa-task-selector-heading');
    const checkbox = () => card.locator('label').filter({ hasText: '测试完成' }).locator('input[type="checkbox"]');
    const strike = () => card.locator('summary').first().locator('[style*="line-through"]').count();
    const choose = async id => {
      if (!(await card.evaluate(el => el.open))) await card.locator('summary').first().click();
      await card.getByLabel('切换需求 1 的当前任务', { exact: true }).click();
      await card.locator(`[data-mine-task="${id}"]`).click();
    };
    assert.equal(await page.locator('.mine-req-card').count(), 1);
    assert.match(await card.innerText(), /存在 2 个关联任务/);
    assert.match(await taskLabel().innerText(), /禅道子任务 #101/);
    await choose(102);
    assert.equal(await card.locator('[data-mine-task="103"]').count(), 0);
    assert.equal(await card.locator('.qa-task-options img').count(), 0);
    assert.match(await taskLabel().innerText(), /禅道子任务 #102/);
    await card.getByRole('button', { name: '▶ 继续', exact: true }).click();
    await card.getByRole('button', { name: '⏸ 暂停', exact: true }).waitFor();
    assert.deepEqual(operations.at(-1), { taskId: 102, action: 'start' });
    const estimate = card.locator('input[type="number"]');
    await estimate.fill('9');await estimate.press('Tab');
    await page.waitForFunction(() => window.messages.at(-1)?.[0] === '预计测试用时已更新');
    assert.deepEqual(operations.at(-1), { taskId: 102, action: 'set_time', hours: 9 });
    await page.reload();await page.waitForFunction(() => window.ready);
    assert.match(await taskLabel().innerText(), /禅道子任务 #102/, 'selection survives reload');
    await checkbox().check();
    await page.locator('#mineTestExecModal').waitFor({ state: 'visible' });
    await page.locator('#mineTestExecNotes').fill('任务二完成');
    await page.locator('#mineTestExecModal').getByRole('button', { name: '确认提交' }).click();
    await page.waitForFunction(() => document.getElementById('mineTestExecModal').dataset.open === '0');
    await page.waitForFunction(() => document.querySelector('.qa-task-progress').textContent.includes('2 / 3'));
    assert.equal(operations.at(-1).taskId, 102);
    assert.equal(operations.at(-1).notes, '任务二完成');
    assert.equal(operations.at(-1).minor_version_id, 2);
    assert.equal(await strike(), 0, 'finishing one task must not strike the requirement');
    assert.equal(await checkbox().isChecked(), true, 'checkbox reflects the selected completed task');
    await choose(101);
    assert.equal(await checkbox().isChecked(), false);
    await checkbox().check();
    await page.locator('#mineTestExecModal').getByRole('button', { name: '确认提交' }).click();
    await page.waitForFunction(() => document.querySelector('.mine-req-card > summary [style*="line-through"]'));
    assert.equal(operations.at(-1).taskId, 101);
    await choose(102);
    await checkbox().uncheck();
    await page.waitForFunction(() => !document.querySelector('.mine-req-card > summary [style*="line-through"]'));
    assert.deepEqual(operations.at(-1), { taskId: 102, action: 'reactivate' });
    failOperation = true;
    await card.getByRole('button', { name: '⏸ 暂停', exact: true }).click();
    await page.waitForFunction(() => window.messages.at(-1)?.[0] === '任务操作未生效');
    assert.match(await taskLabel().innerText(), /进行中/, 'failed remote operation retains known state');
    finalTest = true;
    await page.evaluate(() => window.mine.loadMyWorkbench());
    await checkbox().check();
    await page.waitForFunction(() => document.querySelector('.mine-req-card > summary [style*="line-through"]'));
    assert.equal(operations.at(-1).taskId, 102, 'final test also targets the selected task');
    await page.setViewportSize({ width: 390, height: 844 });
    await choose(101);
    await card.getByLabel('切换需求 1 的当前任务', { exact: true }).click();
    assert.equal(await card.locator('.qa-task-options').evaluate(el => el.getBoundingClientRect().right <= innerWidth && el.getBoundingClientRect().left >= 0), true);
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.locator('#assignTable').getByRole('button', { name: '选择关联任务并修改' }).click();
    const dialog = page.locator('#assignLinkedTasksDialog');
    await dialog.locator('[data-task]').selectOption('102');
    await dialog.locator('[data-owner]').selectOption('2');
    await dialog.locator('[data-start]').fill('2026-11-01');
    await dialog.locator('[data-deadline]').fill('2026-11-03');
    await dialog.getByRole('button', { name: '保存所选任务' }).click();
    await page.waitForFunction(() => !document.getElementById('assignLinkedTasksDialog').open);
    assert.deepEqual(edits, [{ taskId: 102, owner_id: 2, task_start_date: '2026-11-01', task_deadline: '2026-11-03' }]);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.deepEqual(errors, []);
    if (process.env.TASKS_SCREENSHOT) await card.screenshot({ path: process.env.TASKS_SCREENSHOT });
    console.log('PASS: task selection, independent status/hours, all-task completion, reactivation, final test, assignment edit, and narrow-screen dropdown');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
