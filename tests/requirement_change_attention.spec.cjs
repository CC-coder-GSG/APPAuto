// Real workbench rendering; intercepted APIs, no production task writes.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.stack));
    let revision = 0, requests = 0, completed = false;
    const rows = () => Array.from({ length: 30 }, (_, i) => ({
      id: i + 1, zentao_req_id: `r#${i + 1}`, title: i === 3 ? '上方需求 '.repeat(1 + revision * 20) : `需求 ${i + 1}`,
      major_version_id: 470, final_test: true, test_completed: i === 17 && completed,
      case_completed: false, test_cases: [], free_bugs: [], test_tasks: [], story_tasks: [],
      change_retests: i === 17 ? [{ id: 1, source_task_id: 101, task_id: 101, owner_id: 7,
        owner_name: '测试负责人', story_version: 3, status: 'active', test_completed: completed }] : [],
    }));
    const html = `<!doctype html><meta charset="utf-8">
      <link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/css/weave.css">
      <link rel="stylesheet" href="/static/css/requirement-tasks.css">
      <style>html{scroll-behavior:auto}body{margin:0;padding:0}#inner{height:550px;overflow-y:auto;margin-top:50px;padding:10px}
      .mine-req-card{min-height:160px;box-sizing:border-box}</style>
      <select id="mineMajorSelect"><option value="470">V4.0.5.0</option></select>
      <div id="inner"><section id="tab-mine"><div id="mineCards"></div></section></div>
      <script type="module">
        import {state} from '/static/js/state.js';
        import * as mine from '/static/js/tabs/mine.js';
        window.state=state;window.mine=mine;state.currentUser={id:7};
        window.isWorkbenchSubtabActive=()=>true;
        await mine.loadMyWorkbench();window.ready=true;
      </script>`;
    await page.route('http://retest-attention.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname.startsWith('/static/')) return route.fulfill({
        contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript',
        body: fs.readFileSync(path.join(root, 'frontend', url.pathname.slice(8))),
      });
      if (url.pathname === '/workbench/mine') { requests++; return route.fulfill({ json: rows() }); }
      if (url.pathname === '/bugs/dispatched-to-me') return route.fulfill({ json: [] });
      return route.fulfill({ json: {} });
    });
    await page.goto('http://retest-attention.test/');
    await page.waitForFunction(() => window.ready);
    const card = page.locator('.mine-req-card[data-req-id="18"]');
    const top = () => card.evaluate(el => el.getBoundingClientRect().top);
    await card.evaluate(el => el.scrollIntoView({ block: 'center' }));
    await page.mouse.move(0, 0);
    assert(await card.evaluate(el => el.classList.contains('mine-change-retest-pending')));
    assert.equal(await page.locator('.mine-change-retest-pending').count(), 1);
    const ring = () => card.evaluate(el => ({opacity: getComputedStyle(el, '::before').opacity,
      animation: getComputedStyle(el, '::before').animationName}));
    assert.deepEqual(await ring(), {opacity: '1', animation: 'qaChangeFlow'});
    await card.hover();
    await page.mouse.move(0, 0);
    assert.deepEqual(await ring(), {opacity: '1', animation: 'qaChangeFlow'});
    const before = await top();
    const beforeRequests = requests;
    revision++;
    await page.evaluate(() => document.dispatchEvent(new CustomEvent('zentao-story-change-confirmed')));
    await page.waitForFunction(() => document.querySelector('[data-req-id="4"] summary').textContent.length > 50);
    assert(requests > beforeRequests);
    assert(Math.abs(await top() - before) < 3, 'confirmation preserves the nested scroll position');
    assert(await card.evaluate(el => el.classList.contains('mine-change-retest-pending')), 'reading does not consume persistent emphasis');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.deepEqual(await ring(), {opacity: '1', animation: 'none'});
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    // An ordinary document scrollbar uses the same card anchor strategy.
    await page.locator('#inner').evaluate(el => { el.style.height = 'auto'; el.style.overflowY = 'visible'; });
    await card.evaluate(el => el.scrollIntoView({ block: 'center' }));
    const documentTop = await top();
    revision++;
    await page.evaluate(() => document.dispatchEvent(new CustomEvent('zentao-story-change-confirmed')));
    await page.waitForFunction(() => document.querySelector('[data-req-id="4"] summary').textContent.length > 180);
    assert(Math.abs(await top() - documentTop) < 3, 'confirmation preserves document scroll position');
    completed = true;
    await page.evaluate(() => window.mine.loadMyWorkbench({ background: true }));
    assert.equal(await page.locator('.mine-change-retest-pending').count(), 0, 'test completion removes emphasis even before case checkbox completion');
    completed = false;
    await page.evaluate(() => { window.state.currentUser={id:11}; });
    await page.evaluate(() => window.mine.loadMyWorkbench({ background: true }));
    assert.equal(await page.locator('.mine-change-retest-pending').count(), 0, 'other assignees are not highlighted for this user');
    assert.deepEqual(errors, []);
    console.log('PASS: persistent owner emphasis, hover/read/reduced motion, completion removal, nested and document scroll preservation');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
