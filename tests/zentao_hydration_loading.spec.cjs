// Real browser and modules, with controlled upstream timing and no live API calls.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const html = `<!doctype html><meta charset="utf-8"><span id="ztSyncDot"></span>
      <section id="tab-mine"><div id="mineCards"></div></section>
      <script>
        window.reads=[];
        window.userToken=name=>'header.'+btoa(JSON.stringify({sub:name}))+'.signature';
        if(!localStorage.token) localStorage.token=userToken('alice');
        const nativeFetch=window.fetch.bind(window);
        window.fetch=(url, options={})=>{
          if(String(url).startsWith('/static/')) return nativeFetch(url,options);
          return new Promise((resolve,reject)=>{
            reads.push({url,signal:options.signal,reply:data=>resolve(new Response(JSON.stringify(data)))});
            options.signal?.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError')),{once:true});
          });
        };
        window.render=(stories=[],bugs=[])=>{
          document.getElementById('mineCards').innerHTML=stories.map(id=>'<span class="zt-story-slot" data-zt-story-id="'+id+'"></span>').join('')
            +bugs.map(id=>'<span class="zt-bug-slot" data-zt-bug-id="'+id+'"></span>').join('');
          window.current=OmniQAZentao.hydrateContainer(document.getElementById('mineCards'));
        };
      </script>
      <script type="module">import '/static/js/zentao-hydrator.js';window.ready=true;</script>`;
    await page.route('http://hydration-loading.test/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      assert.ok(url.pathname.startsWith('/static/'));
      const file = path.join(root, 'frontend', url.pathname.slice(8));
      return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
    });
    await page.goto('http://hydration-loading.test/');
    await page.waitForFunction(() => window.ready);
    await page.evaluate(() => render([1, 2]));
    await page.waitForFunction(() => reads.length === 1);
    await page.evaluate(() => reads[0].reply({ 1: { status:'active', status_zh:'激活', assigned_to:'wangjie' }, __fetch_errors__:['2'] }));
    await page.waitForFunction(() => document.querySelector('[data-zt-story-id="1"]').dataset.ztLoaded);
    assert.match(await page.locator('[data-zt-story-id="1"]').innerText(), /wangjie/);
    await page.waitForFunction(() => reads.length === 2);
    assert.match(await page.evaluate(() => reads[1].url), /ids=2$/);
    assert.equal(await page.locator('[data-zt-story-id="2"][data-zt-loaded]').count(), 0);
    await page.evaluate(() => reads[1].reply({ 2: { status:'closed', status_zh:'已关闭' } }));
    await page.evaluate(() => current);
    console.log('PASS: successful labels render before retries; only failed IDs retry');

    await page.evaluate(() => {
      OmniQAZentao.invalidate();
      render([], Array.from({length:11}, (_,i)=>100+i));
    });
    await page.waitForFunction(() => reads.length === 3);
    await page.evaluate(() => render([3], Array.from({length:11}, (_,i)=>100+i)));
    await page.waitForFunction(() => reads.length === 4);
    assert.match(await page.evaluate(() => reads[3].url), /stories\?ids=3$/);
    await page.evaluate(() => reads[3].reply({ 3: { status:'active', status_zh:'激活', assigned_to:'fast-story' } }));
    await page.waitForFunction(() => document.querySelector('[data-zt-story-id="3"]').dataset.ztLoaded);
    await page.evaluate(() => OmniQAZentao.invalidate());
    assert.equal(await page.evaluate(() => reads[2].signal.aborted), true);
    await page.evaluate(() => render([1]));
    await page.waitForFunction(() => reads.length === 5);
    await page.evaluate(() => reads[4].reply({ 1: { status:'active', status_zh:'激活', assigned_to:'cached-person' } }));
    await page.evaluate(() => current);
    console.log('PASS: Bug requests do not block story lane; invalidation aborts old work');

    await page.waitForFunction(() => sessionStorage.getItem('omniqa.zentao-labels.v1'));
    await page.reload();
    await page.waitForFunction(() => window.ready);
    await page.evaluate(() => render([1]));
    await page.evaluate(() => current);
    assert.equal(await page.evaluate(() => reads.length), 0);
    assert.match(await page.locator('[data-zt-story-id="1"]').innerText(), /cached-person/);
    console.log('PASS: page reload immediately reuses successful labels without a new request');

    await page.evaluate(() => {
      const saved=JSON.parse(sessionStorage.getItem('omniqa.zentao-labels.v1'));
      saved.stories[0][1].expiry=0;
      saved.stories[0][1].saved=Date.now()-120000;
      sessionStorage.setItem('omniqa.zentao-labels.v1',JSON.stringify(saved));
    });
    await page.reload();
    await page.waitForFunction(() => window.ready);
    await page.evaluate(() => render([1]));
    await page.waitForFunction(() => reads.length === 1);
    assert.match(await page.locator('[data-zt-story-id="1"]').innerText(), /cached-person/);
    assert.equal(await page.locator('[data-zt-stale]').count(), 1);
    await page.evaluate(() => reads[0].reply({ __fetch_errors__:['1'], __auth_required__:true }));
    await page.evaluate(() => current);
    assert.match(await page.locator('[data-zt-story-id="1"]').getAttribute('title'), /更新失败/);
    assert.match(await page.locator('[data-zt-story-id="1"]').innerText(), /cached-person/);
    await page.locator('.zt-err').click();
    await page.waitForFunction(() => reads.length === 2);
    await page.evaluate(() => reads[1].reply({ 1: { status:'closed', status_zh:'已关闭', assigned_to:'new-person' } }));
    await page.waitForFunction(() => document.querySelector('[data-zt-story-id="1"]').dataset.ztLoaded);
    assert.equal(await page.locator('[data-zt-stale], .zt-err').count(), 0);
    console.log('PASS: stale labels stay visible on failure and manual retry replaces them');

    await page.evaluate(() => { localStorage.token=userToken('bob'); render([1]); });
    await page.waitForFunction(() => reads.length === 3);
    assert.doesNotMatch(await page.locator('#mineCards').innerText(), /new-person|cached-person/);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('omniqa.zentao-labels.v1')), null);
    await page.evaluate(() => reads[2].reply({ 1: { status:'active', assigned_to:'bob' } }));
    await page.evaluate(() => current);
    assert.deepEqual(errors, []);
    console.log('PASS: switching users clears previous labels and stored snapshot');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
