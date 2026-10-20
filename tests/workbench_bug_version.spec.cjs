// Run: node tests/workbench_bug_version.spec.cjs (requires playwright or PLAYWRIGHT_MODULE).
// Exercises the real workbench and hydrator against intercepted APIs only.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], requests = [], checks = [];
    page.on('pageerror', error => errors.push(error.stack));
    let releaseHydration;
    const hydrationGate = new Promise(resolve => { releaseHydration = resolve; });
    const remote = {
      101: { status: 'active', resolved_build: 'stale-fix' },
      102: { status: 'resolved', resolved_build: '4.0.3' },
      103: { status: 'closed', resolved_build: '4.0.4' },
      104: { status: 'closed', resolved_build: '' },
      105: { status: 'resolved', resolved_build: '4.0.5' },
      106: { status: 'active', resolved_build: '' },
    };
    const html = `<!doctype html><meta charset="utf-8">
      <link rel="stylesheet" href="/static/css/main.css"><link rel="stylesheet" href="/static/css/weave.css">
      <section id="tab-mine"><div id="mineCards"></div></section>
      <script type="module">
        import '/static/js/components/entity-preview-modal.js';
        import '/static/js/zentao-hydrator.js';
        import { renderMineCards } from '/static/js/tabs/mine.js';
        import { state } from '/static/js/state.js';
        const bug = (id, status, extra={}) => ({ id, bug_id:'b#'+id,
          zentao_live_status:status, found_minor_version_no:'4.0.1', ...extra });
        window.previewCalls=0; window.OmniQAPreview={openBug(){window.previewCalls++}};
        state.currentMineData=[{id:1,title:'版本显示验收',test_cases:[{
          id:20,zentao_case_id:'',bugs:[bug(101,'active',{resolved_version_no:'stale-fix'})]
        }],free_bugs:[
          bug(102,'resolved',{resolved_version_no:'4.0.2'}),
          bug(103,'closed',{resolved_version_no:'4.0.4'}),
          bug(104,'closed',{resolved_version_no:''}),
          bug(105,'active'),
          bug(106,'resolved',{resolved_version_no:'old-fix'}),
          bug(107,'resolved',{resolved_version_no:'<img src=x onerror=alert(1)>',found_minor_version_no:'A&B <found>'}),
        ]}];
        window.rerender=renderMineCards; renderMineCards(); window.ready=true;
      </script>`;
    await page.route('http://app.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(root, 'frontend', url.pathname.slice(8));
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
      }
      requests.push({ path: url.pathname, method: route.request().method() });
      assert.equal(url.pathname, '/zentao/hydrate/bugs', 'Only read-only bug hydration is expected');
      await hydrationGate;
      const data = Object.fromEntries(url.searchParams.get('ids').split(',').filter(id => remote[id])
        .map(id => [id, { id: Number(id), title: 'Bug '+id, ...remote[id] }]));
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
    });
    await page.goto('http://app.test/');
    await page.waitForFunction(() => window.ready);
    const chip = id => page.locator('.badge').filter({ has: page.locator(`.zt-bug-slot[data-zt-bug-id="${id}"]`) }).locator('.qa-bug-version');
    assert.match(await chip(101).innerText(), /发现于:.*4\.0\.1/);
    assert.equal(await chip(101).isDisabled(), true);
    assert.match(await chip(102).innerText(), /解决于:.*4\.0\.2/);
    assert.match(await chip(103).innerText(), /解决于:.*4\.0\.4/);
    assert.match(await chip(104).innerText(), /解决于:.*未填写/);
    checks.push('cached status selects the correct default for case and free bugs');

    const card = page.locator('.mine-req-card');
    const wasOpen = await card.evaluate(el => el.open);
    const originalRequests = requests.length;
    await chip(102).click();
    assert.match(await chip(102).innerText(), /发现于:.*4\.0\.1/);
    assert.equal(await card.evaluate(el => el.open), wasOpen);
    assert.equal(await page.evaluate(() => window.previewCalls), 0);
    assert.equal(requests.length, originalRequests);
    checks.push('click toggles version without requests, preview or card collapse');

    await chip(102).press('Enter');
    assert.match(await chip(102).innerText(), /解决于:.*4\.0\.2/);
    await chip(102).press('Space');
    assert.match(await chip(102).innerText(), /发现于:.*4\.0\.1/);
    await chip(103).click();
    assert.match(await chip(103).innerText(), /发现于:/);
    await chip(103).click();
    assert.match(await chip(103).innerText(), /解决于:/);
    checks.push('mouse and keyboard switch both ways on resolved and closed bugs');

    await chip(104).click();
    assert.match(await chip(104).innerText(), /发现于:.*4\.0\.1/);
    await chip(107).click();
    assert.match(await chip(107).innerText(), /A&B <found>/);
    await chip(107).click();
    assert.equal(await page.locator('#mineCards img').count(), 0);
    checks.push('missing fix remains switchable and version values are escaped');

    releaseHydration();
    await page.waitForFunction(() => document.querySelector('[data-zt-bug-id="106"].zt-bug-slot').dataset.ztLoaded === '1');
    assert.match(await chip(105).innerText(), /解决于:.*4\.0\.5/);
    assert.match(await chip(106).innerText(), /发现于:.*4\.0\.1/);
    assert.equal(await chip(106).isDisabled(), true);
    assert.match(await chip(102).innerText(), /发现于:/);
    await chip(102).click();
    assert.match(await chip(102).innerText(), /解决于:.*4\.0\.3/);
    checks.push('live hydration updates fix and status, preserves selection, and handles reactivation');

    await chip(102).hover();
    await page.waitForFunction(() => {
      const el = [...document.querySelectorAll('.qa-bug-version')].find(button => button.dataset.resolvedVersion === '4.0.3');
      return getComputedStyle(el).color === 'rgb(22, 163, 74)';
    });
    const css = await chip(102).evaluate(el => {
      const style = getComputedStyle(el);
      return { color: style.color, background: style.backgroundColor, shadow: style.boxShadow };
    });
    assert.equal(css.color, 'rgb(22, 163, 74)');
    assert.equal(css.background, 'rgba(0, 0, 0, 0)');
    assert.equal(css.shadow, 'none');
    checks.push('version keeps compact tag styling with the production CSS');
    assert.ok(requests.every(request => request.method === 'GET'));
    assert.deepEqual(errors, []);
    if (process.env.BUG_VERSION_SCREENSHOT) await page.screenshot({ path: process.env.BUG_VERSION_SCREENSHOT, fullPage: true });
    console.log(JSON.stringify({ passed: checks.length, checks }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
