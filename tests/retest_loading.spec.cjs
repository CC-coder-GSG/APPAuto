// Real ES modules and retest markup; delayed fetch responses exercise both abort
// handling and responses that still arrive after cancellation. No live APIs.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  let passed = 0;
  try {
    const page = await browser.newPage();
    page.on('pageerror', err => errors.push(err.message));
    const index = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
    const start = index.indexOf('<section id="tab-retest"');
    const section = index.slice(start, index.indexOf('</section>', start) + 10).replace('hidden card', 'card');
    const html = `<!doctype html><meta charset="utf-8">${section}
      <script>
      window.requests=[];window.jobs=[];window.messages=[];window.currentSoftwareId=1;
      window.showMessage=(...args)=>messages.push(args);
      window.isWorkbenchSubtabActive=()=>true;
      window.honorAbort=false;
      const nativeFetch=window.fetch.bind(window);
      window.fetch=(url,options={})=>{
        if(!String(url).startsWith('/workbench/')&&!String(url).startsWith('/final-test/')) return nativeFetch(url,options);
        if(String(url).includes('preflight-refresh')) return Promise.resolve(new Response(JSON.stringify({bugs:{cached:true},testcases:{cached:true}})));
        return new Promise((resolve,reject)=>{
          const req={url,signal:options.signal,resolve:body=>resolve(new Response(JSON.stringify(body))),reject};
          requests.push(req);
          if(honorAbort) options.signal?.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError')),{once:true});
        });
      };
      </script>
      <script type="module">
      import * as retest from '/static/js/tabs/retest.js';
      import {state} from '/static/js/state.js';
      window.state=state;window.retest=retest;
      window.loadRetest=()=>retest.loadRetest();window.toggleRetestMode=()=>retest.toggleRetestMode();
      document.getElementById('retestMajorSelect').innerHTML='<option value="1">4.0.5.0</option><option value="2">4.0.4.11</option>';
      window.startLoad=(major,options={})=>{
        document.getElementById('retestMajorSelect').value=major;
        const id=jobs.length;
        jobs.push(retest.loadRetest(options).then(()=>null,err=>err.message));return id;
      };
      window.ready=true;
      </script>`;
    await page.route('http://retest-loading.test/**', route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (pathname.startsWith('/static/')) return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(root, 'frontend', pathname.slice(8))) });
      return route.abort();
    });
    const reset = async () => { await page.goto('http://retest-loading.test/'); await page.waitForFunction(() => window.ready); };
    const load = major => page.evaluate(major => startLoad(major), major);
    const resolve = async (i, body) => {
      await page.waitForFunction(i => !!window.requests[i], i);
      await page.evaluate(([i, body]) => requests[i].resolve(body), [i, body]);
    };
    const fail = async (i, message) => {
      await page.waitForFunction(i => !!window.requests[i], i);
      await page.evaluate(([i, message]) => requests[i].reject(new Error(message)), [i, message]);
    };
    const finish = id => page.evaluate(id => jobs[id], id);
    const card = id => [{ id, title: `Version requirement ${id}`, retest_conclusion: 'pending', test_cases: [], free_bugs: [] }];
    const snapshot = () => page.evaluate(() => ({ text: document.getElementById('retestCardsArea').textContent, busy: document.getElementById('retestCardsArea').getAttribute('aria-busy'), ids: state.currentRetestData.map(r => r.id), requests: requests.map(r => ({ url:r.url, aborted:r.signal?.aborted })) }));
    const check = name => { passed++; console.log(`PASS ${name}`); };

    for (const enabled of [false, true]) {
      await reset();
      const old = await load('1');
      let view = await snapshot();
      assert.match(view.text, /正在加载/); assert.equal(view.busy, 'true');
      const current = await load('2');
      assert.equal((await snapshot()).requests[0].aborted, true);
      await resolve(1, { enabled: false }); await resolve(2, card(2)); await finish(current);
      await resolve(0, { enabled }); await finish(old);
      view = await snapshot();
      assert.deepEqual(view.ids, [2]); assert.match(view.text, /Version requirement 2/);
      assert.equal(view.requests.length, 3); assert.equal(view.busy, 'false');
      check(`late old final-test status (enabled=${enabled}) cannot replace current cards`);
    }

    for (const reject of [false, true]) {
      for (const currentFirst of [false, true]) {
        await reset();
        const old = await load('1'); await resolve(0, { enabled: false });
        const current = await load('2');
        if (currentFirst) {
          await resolve(2, { enabled: false }); await resolve(3, card(2)); await finish(current);
        }
        if (reject) await fail(1, 'old request failed');
        else await resolve(1, card(1));
        await finish(old);
        let view = await snapshot();
        if (!currentFirst) {
          assert.equal(view.busy, 'true'); assert.match(view.text, /正在加载/); assert.deepEqual(view.ids, []);
          await resolve(2, { enabled: false }); await resolve(3, card(2)); await finish(current);
        }
        view = await snapshot(); assert.deepEqual(view.ids, [2]); assert.equal(view.busy, 'false');
        check(`late old list ${reject ? 'failure' : 'success'} preserves current ${currentFirst ? 'result' : 'loading state'}`);
      }
    }

    await reset(); await page.evaluate(() => window.honorAbort=true);
    const cancelled = await load('1'), active = await load('2');
    assert.equal(await finish(cancelled), null);
    await resolve(1, { enabled: false }); await resolve(2, []); await finish(active);
    assert.match((await snapshot()).text, /没有需要您复测/);
    assert.deepEqual(await page.evaluate(() => messages), []);
    check('real AbortError is silent; empty result ends loading');

    await reset();
    let job = await load('1'); await resolve(0, { enabled: false }); await resolve(1, card(1)); await finish(job);
    job = await load('2');
    let view = await snapshot(); assert.deepEqual(view.ids, []); assert.doesNotMatch(view.text, /Version requirement 1/);
    await resolve(2, { enabled: false });
    await fail(3, '<img src=x onerror=alert(1)> network failed');
    assert.match(await finish(job), /network failed/);
    assert.equal(await page.locator('#retestCardsArea img').count(), 0);
    view = await snapshot(); assert.match(view.text, /加载复测需求失败/); assert.equal(view.busy, 'false');
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await resolve(4, { enabled: false }); await resolve(5, card(2));
    await page.waitForFunction(() => state.currentRetestData[0]?.id===2);
    check('switch removes old cards; escaped failure message and retry recover');

    await reset();
    job = await load('1'); await resolve(0, { enabled: true }); await finish(job);
    view = await snapshot(); assert.match(view.text, /最终测试阶段/); assert.equal(view.requests.length, 1); assert.equal(view.busy, 'false');
    check('current final-test version renders pause message without fetching list');

    await reset();
    const pending = await load('1'); await load('');
    await resolve(0, { enabled: true }); await finish(pending);
    view = await snapshot(); assert.match(view.text, /请选择大版本/); assert.equal(view.busy, 'false');
    check('clearing version invalidates pending load');

    await reset();
    const versionJob = await load('1');
    await page.evaluate(() => { document.getElementById('retestDisplayMode').value='all_pending'; window.currentSoftwareId=2; });
    const allJob = await load('2');
    await resolve(1, card(22)); await finish(allJob); await resolve(0, { enabled: true }); await finish(versionJob);
    view = await snapshot(); assert.deepEqual(view.ids, [22]); assert.match(view.requests[1].url, /mode=all_pending&software_id=2/);
    check('mode and software switch cannot be overwritten by old version status');

    await reset();
    job = await load('1'); await resolve(0, { enabled: false }); await resolve(1, card(1)); await finish(job);
    const background = await page.evaluate(() => startLoad('1', { background:true }));
    view = await snapshot(); assert.deepEqual(view.ids, [1]); assert.match(view.text, /Version requirement 1/); assert.equal(view.busy, 'true');
    const switched = await load('2'); await resolve(2, { enabled: false }); await finish(background);
    assert.match((await snapshot()).text, /正在加载/);
    await resolve(3, { enabled: false }); await resolve(4, card(2)); await finish(switched);
    assert.deepEqual((await snapshot()).ids, [2]);
    check('same-filter background refresh keeps cards and yields to version switch');

    assert.deepEqual(errors, []);
    console.log(`${passed} retest loading checks passed`);
  } finally { await browser.close(); }
})().catch(err => { console.error(err); process.exitCode=1; });
