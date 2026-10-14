// Run: node tests/assignment_batch.spec.cjs (requires playwright or PLAYWRIGHT_MODULE).
// Real assignment markup/modules/styles; intercepted APIs never touch real Zentao or WeCom.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let holdWrite = false, releaseWrite, holdList = false, releaseList, heldListStarted = false;
    const rows = [
      { id: 1, zentao_req_id: 'r#7276', title: '已有测试子任务：支持更新日期', owner_id: 1, zentao_task_id: 19924, task_start_date: '2026-09-14', task_deadline: '2026-09-18' },
      { id: 2, zentao_req_id: 'r#7277', title: '未选中的需求', owner_id: 1, zentao_task_id: 19925, task_dates_synced: false },
      { id: 3, zentao_req_id: 'r#7278', title: '未创建子任务：日期更新时跳过', owner_id: null, zentao_task_id: null },
    ];
    const other = [{ id: 4, zentao_req_id: 'r#8888', title: '另一个版本的需求', owner_id: 2, zentao_task_id: null }];
    const index = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
    const start = index.indexOf('<section id="tab-assign"');
    const markup = index.slice(start, index.indexOf('</section>', start) + 10).replace('class="hidden card"', 'class="card"');
    assert.ok(markup.includes('assignSelectAll'));
    const html = `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/css/main.css"><link rel="stylesheet" href="/static/css/weave.css"><style>body{padding:28px;background:#f5f5f4}#tab-assign{max-width:1220px;margin:auto}</style>${markup}
      <script>
      window.currentSoftwareId=1; window.users=[{id:1,username:'alice',display_name:'爱丽丝'},{id:2,username:'bob',display_name:'鲍勃'}];
      window.messages=[]; window.confirmations=0; window.showMessage=(...args)=>messages.push(args);
      window.confirmPush=()=>{window.confirmations++;return true};
      </script><script type="module">
      import '/static/js/components/entity-preview-modal.js';
      import * as assign from '/static/js/tabs/assign.js';
      window.loadAssignBoard=assign.loadAssignBoard; window.publishAssign=assign.publishAssign; window.createZentaoTasks=assign.createZentaoTasks;
      document.getElementById('assignMajorSelect').innerHTML='<option value="1">V4.0.3.15</option><option value="2">V4.0.3.16</option><option value="0">全部版本</option>';
      await assign.loadAssignBoard(); window.ready=true;
      </script>`;
    page.on('pageerror', e => errors.push(e.stack));
    await page.route('http://assignment.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname.startsWith('/static/')) {
        const local = path.join(root, 'frontend', url.pathname.slice(8));
        return route.fulfill({ body: fs.readFileSync(local), contentType: local.endsWith('.css') ? 'text/css' : 'text/javascript' });
      }
      if (url.pathname === '/requirements/admin/list') {
        const major = url.searchParams.get('major_version_id');
        const responseRows = major === '2' ? other : rows;
        if (holdList && major === '1') {
          heldListStarted = true;
          await new Promise(resolve => { releaseList = resolve; });
        }
        return route.fulfill({ json: responseRows });
      }
      if (url.pathname === '/requirements/admin/progress') return route.fulfill({ json: { summary: {}, owners: [], retest_pending_by_major: [] } });
      if (url.pathname === '/final-test/status') return route.fulfill({ json: { enabled: false } });
      if (['/requirements/update-zentao-task-dates', '/requirements/assign-and-publish', '/requirements/create-zentao-tasks'].includes(url.pathname)) {
        const body = route.request().postDataJSON();
        writes.push({ url: url.pathname, body });
        if (holdWrite) await new Promise(resolve => { releaseWrite = resolve; });
        if (url.pathname.endsWith('update-zentao-task-dates')) {
          rows[0].task_start_date = body.task_start_date; rows[0].task_deadline = body.task_deadline;
          return route.fulfill({ json: { total: 2, counts: { updated: 1, unchanged: 0, skipped: 1, failed: 0, unverified: 0 },
            task_start_date: body.task_start_date, task_deadline: body.task_deadline, results: [
              { requirement_id: 1, zentao_req_id: rows[0].zentao_req_id, title: rows[0].title, task_id: 19924, status: 'updated', reason: '预计起止日期已更新并核实' },
              { requirement_id: 3, zentao_req_id: rows[2].zentao_req_id, title: rows[2].title, task_id: null, status: 'skipped', reason: '尚未创建禅道子任务，已跳过' },
            ] } });
        }
        return route.fulfill({ json: { zentao: { ok: true, created_tasks: [19924], adopted_tasks: [19924], reassigned_tasks: [19924], errors: [], unassigned: [] } } });
      }
      return route.fulfill({ json: [] });
    });
    await page.goto('http://assignment.test/');
    await page.waitForFunction(() => window.ready);
    assert.match(await page.locator('#assignTable').innerText(), /日期待同步/);
    assert.equal(await page.locator('#assignSelectAll').isChecked(), false);
    assert.equal(await page.locator('[data-assign-action]:enabled').count(), 0);
    await page.locator('[data-assign-id="1"]').check();
    await page.locator('[data-assign-id="3"]').check();
    await page.locator('#o_1').selectOption('2');
    assert.equal(await page.locator('#assignSelectAll').evaluate(el => el.indeterminate), true);
    assert.match(await page.locator('#assignSelectionCount').innerText(), /已选 2 \/ 3/);
    await page.getByRole('button', { name: '一键发布（所选需求）', exact: true }).click();
    assert.equal(writes.length, 0);
    assert.equal(await page.evaluate(() => window.confirmations), 0, 'validation must precede WeCom confirmation');
    assert.match(await page.evaluate(() => window.messages.at(-1)[0]), /完整/);
    await page.locator('#assignTaskStartDate').fill('2026-10-21');
    await page.locator('#assignTaskDeadline').fill('2026-10-20');
    await page.getByRole('button', { name: '修改预计起止时间', exact: true }).click();
    assert.equal(writes.length, 0);
    assert.match(await page.evaluate(() => window.messages.at(-1)[0]), /不能晚于/);
    await page.locator('#assignTaskStartDate').fill('2026-10-15');
    holdWrite = true;
    await page.getByRole('button', { name: '修改预计起止时间', exact: true }).click();
    await page.waitForFunction(() => document.getElementById('assignMajorSelect').disabled);
    assert.equal(await page.locator('[data-assign-action]:enabled').count(), 0);
    await page.evaluate(() => window.updateZentaoTaskDates());
    assert.equal(writes.length, 1, 'in-flight operation cannot be submitted twice');
    assert.deepEqual(writes[0].body, { major_version_id: 1, requirement_ids: [1, 3], task_start_date: '2026-10-15', task_deadline: '2026-10-20' });
    await page.waitForTimeout(50);
    holdWrite = false; releaseWrite();
    await page.locator('#assignResultDialog').waitFor();
    await page.waitForFunction(() => !document.getElementById('assignMajorSelect').disabled);
    assert.match(await page.locator('#assignResultDialog').innerText(), /更新 1，日期一致 0，跳过 1，失败 0/);
    assert.match(await page.locator('#assignResultDialog').innerText(), /尚未创建禅道子任务/);
    assert.equal(await page.locator('.assign-result-status.updated').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(236, 253, 245)');
    if (process.env.RESULT_SCREENSHOT) await page.screenshot({ path: process.env.RESULT_SCREENSHOT, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('#assignResultDialog').evaluate(el => el.getBoundingClientRect().right <= innerWidth), true);
    await page.getByRole('button', { name: '关闭处理结果' }).click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    assert.equal(await page.locator('[data-assign-id="1"]').isChecked(), true);
    assert.equal(await page.locator('#o_1').inputValue(), '2', 'same-context refresh retains unsaved owner draft');
    assert.match(await page.locator('#assignTable').innerText(), /2026-10-15 → 2026-10-20/);
    await page.getByRole('button', { name: '查看处理结果', exact: true }).click();
    await page.getByRole('button', { name: '关闭处理结果' }).click();
    for (const name of ['更新禅道任务（所选需求）', '一键发布（所选需求）']) {
      await page.getByRole('button', { name, exact: true }).click();
      await page.locator('#assignResultDialog').waitFor();
      await page.waitForFunction(() => !document.getElementById('assignMajorSelect').disabled);
      const body = writes.at(-1).body;
      assert.deepEqual(body.assignments, [{ requirement_id: 1, owner_id: 2 }, { requirement_id: 3, owner_id: null }]);
      assert.match(await page.locator('#assignResultDialog').innerText(), /认领已有子任务 1/);
      assert.doesNotMatch(await page.locator('#assignResultDialog').innerText(), /新建子任务 1/);
      await page.getByRole('button', { name: '关闭处理结果' }).click();
    }
    assert.equal(await page.evaluate(() => window.confirmations), 1);
    rows[1].owner_id = 2;
    await page.evaluate(() => window.loadAssignBoard({ skipSync: true }));
    assert.equal(await page.locator('#o_2').inputValue(), '2', 'refresh reflects remote owner changes when there is no local draft');
    assert.equal(await page.locator('#o_1').inputValue(), '2', 'refresh preserves a changed owner draft');
    assert.equal(await page.locator('[data-assign-id="1"]').isChecked(), true);
    await page.locator('#assignSelectAll').check();
    assert.equal(await page.locator('#assignTable input:checked').count(), 3);
    assert.equal(await page.locator('#assignSelectAll').evaluate(el => el.indeterminate), false);
    await page.locator('#assignSelectAll').uncheck();
    assert.equal(await page.locator('#assignTable input:checked').count(), 0);
    await page.locator('#assignMajorSelect').selectOption('2');
    await page.waitForFunction(() => document.getElementById('o_4') && !document.getElementById('assignSelectAll').disabled);
    assert.equal(await page.locator('#assignTable input:checked').count(), 0);
    assert.equal(await page.locator('#assignBatchResult').isVisible(), false);
    holdList = true;
    await page.evaluate(() => {
      document.getElementById('assignMajorSelect').value = '1';
      window.pendingLoad = window.loadAssignBoard({ skipSync: true });
    });
    for (let attempt = 0; !heldListStarted && attempt < 500; attempt++) await page.waitForTimeout(10);
    assert.equal(heldListStarted, true, 'held version lookup started');
    await page.evaluate(async () => {
      document.getElementById('assignMajorSelect').value = '2';
      await window.loadAssignBoard({ skipSync: true });
    });
    releaseList(); holdList = false;
    await page.evaluate(() => window.pendingLoad);
    assert.equal(await page.locator('#o_4').count(), 1, 'stale version response cannot overwrite current list');
    assert.equal(await page.locator('#o_1').count(), 0);
    await page.locator('[data-assign-id="4"]').check();
    await page.evaluate(async () => {
      window.currentSoftwareId = 2;
      await window.loadAssignBoard({ skipSync: true });
    });
    assert.equal(await page.locator('#assignTable input:checked').count(), 0, 'software switch clears selection even for same version');
    await page.locator('#assignMajorSelect').selectOption('0');
    await page.waitForFunction(() => document.getElementById('assignReqCount').textContent.startsWith('全部版本'));
    assert.equal(await page.locator('#assignSelectAll').isDisabled(), true);
    assert.equal(await page.locator('[data-assign-action]:enabled').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: selected-only actions, date validation before publication, busy guards, partial result dialog, owner draft retention, all/partial selection, context reset and stale-load protection.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
