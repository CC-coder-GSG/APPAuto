// Real browser/modules/markup. Deferred responses also deliberately ignore abort.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  let passed = 0;
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const index = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
    const start = index.indexOf('<select id="mineDisplayMode"');
    assert.ok(start > 0);
    const markup = index.slice(start, index.indexOf('<div id="mineCards"></div>', start) + 26);
    const html = `<!doctype html><meta charset="utf-8">
      <link rel="stylesheet" href="/static/css/main.css">${markup}
      <script>
      window.requests=[];window.jobs=[];window.currentSoftwareId=1;
      window.isWorkbenchSubtabActive=()=>true;window.honorAbort=false;window.holdRefresh=false;
      const nativeFetch=window.fetch.bind(window);
      window.fetch=(url,options={})=>{
        if(String(url).startsWith('/static/')) return nativeFetch(url,options);
        if(String(url).includes('preflight-refresh')) return Promise.resolve(new Response(JSON.stringify({bugs:{cached:true},testcases:{cached:true}})));
        if(String(url).includes('refresh-task-status')&&!holdRefresh) return Promise.resolve(new Response(JSON.stringify({refreshed:false})));
        return new Promise((resolve,reject)=>{
          requests.push({url,signal:options.signal,resolve:body=>resolve(new Response(JSON.stringify(body))),reject});
          if(honorAbort) options.signal?.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError')),{once:true});
        });
      };
      </script>
      <script type="module">
      import * as mine from '/static/js/tabs/mine.js';
      import '/static/js/tabs/feedback.js';
      import {state} from '/static/js/state.js';
      window.mine=mine;window.state=state;state.currentUser={id:1};
      window.loadMyWorkbench=()=>mine.loadMyWorkbench();
      document.getElementById('mineMajorSelect').innerHTML='<option value="1">4.0.5.0</option><option value="2">4.0.4.11</option>';
      window.startLoad=(major,options={})=>{
        document.getElementById('mineMajorSelect').value=major;
        const id=jobs.length;jobs.push(mine.loadMyWorkbench(options).then(()=>null,e=>e.message));return id;
      };
      window.ready=true;
      </script>`;
    await page.route('http://mine-loading.test/**', route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/') return route.fulfill({ contentType:'text/html', body:html });
      if (pathname.startsWith('/static/')) {
        const file = path.join(root, 'frontend', pathname.slice(8));
        return route.fulfill({ contentType:file.endsWith('.css')?'text/css':'text/javascript', body:fs.readFileSync(file) });
      }
      return route.abort();
    });
    const reset = async () => { await page.goto('http://mine-loading.test/'); await page.waitForFunction(()=>window.ready); };
    const load = (major, options={}) => page.evaluate(([m,o])=>startLoad(m,o),[major,options]);
    const response = async (kind, major, body, fail=false) => {
      const needle = `${kind}${major === null ? '' : `?major_version_id=${major}`}`;
      const find = needle => requests.find(r=>!r.finished && (needle.startsWith('/workbench/mine')
        ? r.url.startsWith('/workbench/mine?') && r.url.includes('major_version_id='+needle.split('=').at(-1))
        : r.url===needle));
      // Pass the predicate source explicitly; no live network or timing dependency.
      await page.waitForFunction(({needle,source})=>!!eval('('+source+')')(needle), {needle,source:find.toString()});
      await page.evaluate(({needle,body,fail,source})=>{
        const r=eval('('+source+')')(needle);r.finished=true;
        if(fail) r.reject(new Error(body));else r.resolve(body);
      }, {needle,body,fail,source:find.toString()});
    };
    const finish = id => page.evaluate(id=>jobs[id],id);
    const card = id => [{id,title:`Requirement ${id}`,test_cases:[],free_bugs:[],story_tasks:[]}];
    const view = () => page.evaluate(()=>({
      text:document.getElementById('mineCards').textContent,
      busy:document.getElementById('mineCards').getAttribute('aria-busy'),
      ids:state.currentMineData.map(r=>r.id),feedback:state.currentFeedbackTodoHtml,
      dispatch:state.currentDispatchData.map(r=>r.id),
    }));
    const check = name => { passed++; console.log('PASS '+name); };

    await reset();
    let job = await load('1');
    assert.equal((await view()).busy,'true');
    assert.match((await view()).text,/正在加载工作台/);
    assert.equal(await page.locator('#mineCards .mine-loading-spinner').evaluate(el=>getComputedStyle(el).animationName),'mine-loading-spin');
    await page.evaluate(()=>mine.renderMineCards());
    assert.match((await view()).text,/正在加载工作台/);
    await response('/workbench/mine','1',card(1)); await finish(job);
    assert.deepEqual((await view()).ids,[1]); assert.equal((await view()).busy,'false');
    assert.match((await view()).text,/正在加载反馈待办/);
    await page.locator('.mine-req-card').evaluate(el=>{el.dataset.sentinel='preserved';el.open=true;});
    await response('/feedbacks/my-todo','1',[{id:11,summary:'Feedback one',status:'pending'}]);
    await response('/bugs/dispatched-to-me','1','unavailable',true);
    await page.waitForFunction(()=>document.getElementById('mine-dispatch-panel').textContent.includes('加载失败'));
    assert.match((await view()).feedback,/Feedback one/);
    assert.equal(await page.locator('.mine-req-card').getAttribute('data-sentinel'),'preserved');
    check('animated first load; main data renders before slow panels; panel error preserves cards');

    for(const rejectOld of [false,true]) {
      await reset();
      const old=await load('1'); const current=await load('2');
      assert.equal(await page.evaluate(()=>requests.filter(r=>r.url.includes('major_version_id=1')).every(r=>r.signal.aborted)),true);
      await response('/workbench/mine','1',rejectOld?'old failure':card(1),rejectOld);await finish(old);
      assert.match((await view()).text,/正在加载工作台/);assert.equal((await view()).busy,'true');
      await response('/workbench/mine','2',card(2));await finish(current);
      await response('/feedbacks/my-todo','2',[{id:22,summary:'Current feedback'}]);
      await response('/bugs/dispatched-to-me','2',[]);
      await response('/feedbacks/my-todo','1',[{id:11,summary:'STALE feedback'}]);
      await response('/bugs/dispatched-to-me','1',[{id:11,bug_id:'b#11'}]);
      await page.waitForFunction(()=>state.currentFeedbackTodoHtml.includes('Current feedback'));
      assert.deepEqual((await view()).ids,[2]);assert.deepEqual((await view()).dispatch,[]);
      assert.doesNotMatch((await view()).feedback,/STALE/);
      check(`late old ${rejectOld?'error':'success'} and auxiliary responses cannot overwrite new version`);
    }

    await reset();await page.evaluate(()=>honorAbort=true);
    const cancelled=await load('1');job=await load('2');assert.equal(await finish(cancelled),null);
    await response('/workbench/mine','2',[]);await finish(job);
    assert.match((await view()).text,/当前筛选条件下没有需求/);
    check('real cancellation is silent and empty result ends loading');

    await reset();job=await load('1');
    await response('/workbench/mine','1','<img src=x onerror=alert(1)> failed',true);await finish(job);
    assert.equal(await page.locator('#mineCards img').count(),0);
    assert.match((await view()).text,/加载工作台失败/);assert.equal((await view()).busy,'false');
    await page.locator('#mineCards').getByRole('button',{name:'重试',exact:true}).click();
    await response('/workbench/mine','1',card(1));
    await page.waitForFunction(()=>state.currentMineData[0]?.id===1);
    check('escaped main error and working retry');

    await reset();job=await load('1');await response('/workbench/mine','1',card(1));await finish(job);
    const background=await load('1',{background:true});assert.deepEqual((await view()).ids,[1]);
    assert.match(await page.locator('#workbenchActivity').textContent(),/\u9700\u6c42\u66f4\u65b0\u4e2d/);
    const switched=await load('2');await response('/workbench/mine','1',card(99));await finish(background);
    assert.match((await view()).text,/正在加载工作台/);
    await response('/workbench/mine','2',card(2));await finish(switched);
    assert.deepEqual((await view()).ids,[2]);
    check('background keeps cards; user version switch supersedes it');

    await reset();const pending=await load('1');await load('');
    await response('/workbench/mine','1',card(1));await finish(pending);
    assert.deepEqual((await view()).ids,[]);assert.match((await view()).text,/请选择大版本/);
    check('clearing version invalidates all pending results');

    await reset();const version=await load('1');
    await page.evaluate(()=>{document.getElementById('mineDisplayMode').value='all_pending';window.currentSoftwareId=2;});
    job=await load('2');
    await page.waitForFunction(()=>requests.some(r=>r.url==='/workbench/mine?mode=all_pending&software_id=2'));
    await page.evaluate(()=>requests.find(r=>r.url==='/workbench/mine?mode=all_pending&software_id=2').resolve([{id:22,title:'All pending',test_cases:[],free_bugs:[]}]))
    await finish(job);await response('/workbench/mine','1',card(1));await finish(version);
    assert.deepEqual((await view()).ids,[22]);
    check('mode/software switch protected from old requests');

    await reset();await page.evaluate(()=>holdRefresh=true);
    job=await load('1');await response('/workbench/mine','1',card(1));await finish(job);
    assert.deepEqual((await view()).ids,[1]);
    await response('/workbench/refresh-task-status',null,{refreshed:true});
    await response('/workbench/mine','1',card(3));
    await page.waitForFunction(()=>state.currentMineData[0]?.id===3);
    assert.equal(await page.evaluate(()=>requests.filter(r=>r.url==='/workbench/refresh-task-status').length),1);
    check('remote task sync does not block main load; one background refresh without a loop');

    assert.deepEqual(errors,[]);
    console.log(`${passed} mine loading checks passed`);
  } finally { await browser.close(); }
})().catch(err=>{console.error(err);process.exitCode=1;});
