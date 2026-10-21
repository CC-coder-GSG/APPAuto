// Run with PLAYWRIGHT_MODULE pointing to Playwright if it is not installed globally.
// All APIs are intercepted; no real requirement, Zentao task or notification is changed.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const timestamp = '2026-10-21T10:30:00';

const reqs = [
  { id: 1, zentao_req_id: 'r#900', title: '定位功能优化 <img src=x onerror="window.injected=true">', owner_id: 1, owner_name: '张三', case_completed: true, test_completed: true, retest_conclusion: 'failed', retest_active_problem_count: 2, bug_count: 23, open_bug_count: 3, task_count: 2, task_done_count: 1, task_overdue_count: 1 },
  { id: 2, zentao_req_id: 'r#901', title: '未分配需求', owner_id: null, owner_name: '未分配', case_completed: false, test_completed: false, retest_conclusion: 'pending', bug_count: 0, open_bug_count: 0, task_count: 0 },
  { id: 3, zentao_req_id: 'r#902', title: '待复测需求', owner_id: 1, owner_name: '张三', case_completed: true, test_completed: true, retest_conclusion: 'pending', bug_count: 0, open_bug_count: 0, task_count: 0 },
  { id: 4, zentao_req_id: 'r#903', title: '已通过需求', owner_id: 2, owner_name: '李四', case_completed: true, test_completed: true, retest_conclusion: 'passed', bug_count: 0, open_bug_count: 0, task_count: 0 },
].map(r => ({ ...r, major_version_id: 1, major_version_name: 'V1', case_count: 3 }));
const secondVersion = [{ ...reqs[1], id: 5, zentao_req_id: 'r#999', title: '另一版本需求', major_version_id: 2, major_version_name: 'V2' }];

function summary(rows) {
  const owners = [];
  for (const row of rows) {
    let group = owners.find(o => o.owner_id === (row.owner_id ?? -1));
    if (!group) { group = { owner_id: row.owner_id ?? -1, owner_name: row.owner_name, requirements: [] }; owners.push(group); }
    group.requirements.push(row);
  }
  return { summary: { owners: 2, requirements: rows.length, case_pending: 1, test_pending: 1, retest_pending_total: 1, retest_failed_total: 1, unassigned_total: 1 }, owners, retest_pending_by_major: [{ major_version_name: 'V1', pending_retest_count: 1 }], loaded_at: timestamp };
}

function detail(req, query, refreshed = false) {
  const allBugs = req.id === 1 ? Array.from({ length: 23 }, (_, i) => ({
    id: 100 + i, bug_id: `b#${100 + i}`, zentao_bug_id: String(100 + i),
    zentao_bug_title: i ? `缺陷 ${i}` : '<script>window.injected=true</script>',
    zentao_live_status: i ? 'active' : 'resolved', closed: false,
    association_sources: ['用例关联', '需求归集'], found_minor_version_no: 'V1.1',
    major_version_id: i === 1 ? 2 : 1, major_version_no: i === 1 ? 'V2' : 'V1',
    synced_at: timestamp, checked_at: timestamp, opened_at: timestamp,
    retest_problem_kind: i < 2 ? ['activated', 'collected'][i] : null,
    retest_problem: i < 2, retest_activated_at: timestamp, retest_activated_by_name: '王五',
  })) : [];
  const tasks = req.id === 1 ? [
    { id: 801, source: 'zentao', name: '需求开发', type: 'devel', status: 'done', assigned_to_name: '当前指派人', finished_by_name: '真实完成者', synced_at: timestamp },
    { id: 802, source: 'local', name: '回归验证', description: '<img src=x onerror="window.injected=true">', status: 'in_progress', assigned_to_name: '李四', overdue: true, updated_at: timestamp },
  ] : [];
  function paginate(items, name) {
    const pageSize = 20, page = Number(query.get(name) || 1);
    return { items: items.slice((page - 1) * pageSize, page * pageSize), page, page_size: pageSize, total: items.length };
  }
  return { requirement: req, bugs: paginate(allBugs, 'bug_page'), retest_bugs: paginate(allBugs.slice(0, 2), 'retest_page'), tasks: paginate(tasks, 'task_page'), retest_records: req.id === 1 ? [{ user_name: '李四', passed: true, updated_at: timestamp }] : [], loaded_at: timestamp, warnings: refreshed ? ['任务同步失败：offline'] : [] };
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], requests = [];
    let failDetail = false, holdDetail = false, heldDetailStarted = false, releaseDetail;
    let failSummary = false, holdSummary = false, heldSummaryStarted = false, releaseSummary;
    const index = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
    const start = index.indexOf('<section id="tab-assign"');
    const markup = index.slice(start, index.indexOf('</section>', start) + 10).replace('class="hidden card"', 'class="card"');
    const html = `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/css/main.css"><link rel="stylesheet" href="/static/css/weave.css"><link rel="stylesheet" href="/static/css/assign-progress.css"><style>body{padding:24px;background:#f5f5f4}#tab-assign{max-width:1250px;margin:auto}</style>${markup}<script>
      window.currentSoftwareId=1;window.users=[{id:1,username:'alice',display_name:'张三'}];window.previews=[];
      window.OmniQAPreview={openBug:id=>previews.push(['bug',id]),openTask:id=>previews.push(['task',id]),openStory:id=>previews.push(['story',id])};
      window.showMessage=()=>{};
      </script><script type="module">
      import '/static/js/components/entity-preview-modal.js';
      import * as assign from '/static/js/tabs/assign.js';
      window.OmniQAPreview={openBug:id=>previews.push(['bug',id]),openTask:id=>previews.push(['task',id]),openStory:id=>previews.push(['story',id])};
      window.assign=assign;window.loadAssignBoard=assign.loadAssignBoard;
      document.getElementById('assignMajorSelect').innerHTML='<option value="1">V1</option><option value="2">V2</option>';
      await assign.loadAssignBoard({skipSync:true});window.ready=true;
      </script>`;
    page.on('pageerror', error => errors.push(error.stack));
    await page.route('http://progress.test/**', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname === '/') return route.fulfill({ body: html, contentType: 'text/html' });
      if (url.pathname.startsWith('/static/')) {
        const local = path.join(root, 'frontend', url.pathname.slice(8));
        return route.fulfill({ body: fs.readFileSync(local), contentType: local.endsWith('.css') ? 'text/css' : 'text/javascript' });
      }
      requests.push({ method: request.method(), url });
      const rows = url.searchParams.get('major_version_id') === '2' ? secondVersion : reqs;
      if (url.pathname === '/requirements/admin/list') return route.fulfill({ json: rows });
      if (url.pathname === '/requirements/admin/progress') {
        if (failSummary) { failSummary = false; return route.fulfill({ status: 500, json: { detail: 'summary offline' } }); }
        if (holdSummary) { holdSummary = false; heldSummaryStarted = true; await new Promise(resolve => { releaseSummary = resolve; }); }
        return route.fulfill({ json: summary(rows) });
      }
      const match = url.pathname.match(/^\/requirements\/admin\/progress\/(\d+)(\/refresh)?$/);
      if (match) {
        if (failDetail) { failDetail = false; return route.fulfill({ status: 500, json: { detail: 'detail offline' } }); }
        if (holdDetail) { holdDetail = false; heldDetailStarted = true; await new Promise(resolve => { releaseDetail = resolve; }); }
        return route.fulfill({ json: detail([...reqs, ...secondVersion].find(r => r.id === Number(match[1])), url.searchParams, Boolean(match[2])) });
      }
      if (url.pathname === '/final-test/status') return route.fulfill({ json: { enabled: false } });
      return route.fulfill({ json: [] });
    });
    await page.goto('http://progress.test/');
    await page.waitForFunction(() => window.ready);
    assert.equal(await page.locator('[data-progress-row]').count(), 4);
    assert.equal(requests.filter(r => /progress\/\d/.test(r.url.pathname)).length, 0, 'details load only on demand');
    assert.equal(await page.locator('#assignProgressRows img').count(), 0, 'titles are escaped');

    await page.locator('[data-progress-row="1"] [data-progress-action="expand"]').click();
    const first = page.locator('#assignProgressDetail-1');
    await first.locator('#assign-progress-1-bugs tbody tr').first().waitFor();
    assert.equal(await first.locator('#assign-progress-1-bugs tbody tr').count(), 20);
    assert.match(await first.innerText(), /平台未闭环/);
    assert.match(await first.innerText(), /已解决/);
    assert.match(await first.innerText(), /来自 V2/);
    assert.match(await first.innerText(), /原 Bug 再次激活/);
    assert.match(await first.innerText(), /真实完成者/);
    assert.equal(await first.locator('script,img').count(), 0);
    await first.locator('[data-entity-kind="bug"][data-entity-id="100"]').first().click();
    await first.locator('[data-entity-kind="task"][data-entity-id="801"]').click();
    assert.deepEqual(await page.evaluate(() => window.previews), [['bug', 100], ['task', 801]]);
    await first.locator('[data-progress-action="local-task"]').first().click();
    assert.match(await page.locator('#assignProgressTaskDialog').innerText(), /回归验证/);
    assert.equal(await page.locator('#assignProgressTaskDialog img').count(), 0);
    await page.locator('#assignProgressTaskDialog button').click();

    await first.locator('[data-section="bugs"][data-page="2"]').click();
    await page.waitForFunction(() => document.querySelector('#assign-progress-1-bugs tbody')?.rows.length === 3);
    assert.equal(await first.locator('#assign-progress-1-retest_bugs tbody tr').count(), 2);
    assert.equal(await first.locator('#assign-progress-1-tasks tbody tr').count(), 2);
    await first.locator('[data-progress-action="refresh"]').click();
    await page.waitForFunction(() => document.querySelector('#assignProgressDetail-1')?.textContent.includes('任务同步失败：offline'));
    await page.waitForFunction(() => !document.querySelector('#assignProgressDetail-1 [data-progress-action="refresh"]').disabled);
    assert.ok(requests.some(r => r.method === 'POST' && r.url.pathname.endsWith('/1/refresh')));

    const search = page.locator('#assignProgressSearch');
    await search.fill('r#900');
    assert.equal(await page.locator('[data-progress-row]').count(), 1);
    assert.equal(await search.evaluate(el => el === document.activeElement), true, 'typing does not replace the input');
    assert.equal(await first.isVisible(), true, 'expanded details survive filtering');
    await search.fill('');
    await page.locator('#assignProgressOwner').selectOption('-1');
    assert.equal(await page.locator('[data-progress-row="2"]').count(), 1);
    assert.equal(await page.locator('[data-progress-row]').count(), 1);
    await page.locator('#assignProgressOwner').selectOption('');
    for (const [filter, id] of [['unfinished', 2], ['retest', 3], ['failed', 1], ['open-bugs', 1]]) {
      await page.locator('#assignProgressStatus').selectOption(filter);
      assert.equal(await page.locator('[data-progress-row]').count(), 1);
      assert.equal(await page.locator(`[data-progress-row="${id}"]`).count(), 1);
    }
    await page.locator('#assignProgressStatus').selectOption('');
    failDetail = true;
    await page.locator('[data-progress-row="2"] [data-progress-action="expand"]').click();
    await page.locator('#assignProgressDetail-2 [role="alert"]').waitFor();
    await page.locator('#assignProgressDetail-2 [data-progress-action="retry"]').click();
    await page.locator('#assign-progress-2-bugs .assign-progress-empty').waitFor();
    assert.equal(await first.isVisible(), true, 'multiple requirements can stay expanded');

    failSummary = true;
    await page.getByRole('button', { name: '刷新进度', exact: true }).click();
    await page.waitForFunction(() => document.getElementById('assignProgressNotice').textContent.includes('summary offline'));
    assert.equal(await page.locator('[data-progress-row]').count(), 4, 'failed summary refresh preserves data');
    await page.getByRole('button', { name: '刷新进度', exact: true }).click();
    await page.waitForFunction(() => document.getElementById('assignProgressNotice').textContent === '');
    await first.locator('#assign-progress-1-bugs tbody tr').first().waitFor();
    assert.equal(await first.locator('.qa-preview-btn svg').first().evaluate(el => el.getBoundingClientRect().height <= 16), true, 'real preview styles keep icons compact');
    if (process.env.PROGRESS_SCREENSHOT) {
      await page.locator('#assignProgressArea').evaluate(el => window.scrollTo(0, el.getBoundingClientRect().top + scrollY - 12));
      await page.screenshot({ path: process.env.PROGRESS_SCREENSHOT });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('#assignProgressSearch').evaluate(el => el.getBoundingClientRect().width > 0), true);
    await page.waitForFunction(() => document.getElementById('assignProgressDetail-1').getBoundingClientRect().width <= innerWidth);
    assert.equal(await first.locator('[data-progress-action="refresh"]').evaluate(el => el.getBoundingClientRect().right <= innerWidth), true, 'detail actions remain visible on narrow screens');
    assert.equal(await first.locator('.assign-progress-scroll').first().evaluate(el => el.scrollWidth >= el.clientWidth), true);
    if (process.env.PROGRESS_SCREENSHOT) {
      await page.locator('#assignProgressArea').evaluate(el => window.scrollTo(0, el.getBoundingClientRect().top + scrollY - 12));
      await page.screenshot({ path: process.env.PROGRESS_SCREENSHOT.replace('.png', '-mobile.png') });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });

    holdDetail = true;
    await page.locator('[data-progress-row="3"] [data-progress-action="expand"]').click();
    await page.waitForFunction(() => document.getElementById('assignProgressDetail-3').textContent.includes('正在加载'));
    for (let i = 0; !heldDetailStarted && i < 100; i++) await page.waitForTimeout(10);
    assert.ok(heldDetailStarted);
    await page.locator('#assignMajorSelect').selectOption('2');
    await page.locator('[data-progress-row="5"]').waitFor();
    releaseDetail();
    await page.waitForTimeout(50);
    assert.equal(await page.locator('#assignProgressDetail-3').count(), 0, 'stale details cannot enter another version');

    holdSummary = true;
    await page.evaluate(() => { window.oldProgress = window.assign.loadAssignProgress(); });
    for (let i = 0; !heldSummaryStarted && i < 100; i++) await page.waitForTimeout(10);
    assert.ok(heldSummaryStarted);
    await page.locator('#assignMajorSelect').selectOption('1');
    await page.locator('[data-progress-row="1"]').waitFor();
    releaseSummary();
    await page.evaluate(() => window.oldProgress);
    assert.equal(await page.locator('[data-progress-row="5"]').count(), 0, 'stale summary cannot overwrite the selected version');
    assert.equal(await page.locator('[data-progress-row]').count(), 4);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.deepEqual(errors, []);
    console.log('Assignment progress browser checks passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
