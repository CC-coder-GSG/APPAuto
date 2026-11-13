const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage();
    let posts = 0, reads = 0, outcome = 'not-started', succeed = false, status = 'doing';
    const errors = [];
    page.on('pageerror', e => errors.push(e.stack));
    const html = `<!doctype html><meta charset="utf-8"><script type="module">
      import {api} from '/static/js/api.js';
      import {runZentaoOperation} from '/static/js/components/zentao-operation.js';
      window.showMessage=()=>{};window.hang=false;
      const timer=window.setTimeout.bind(window);
      window.setTimeout=(fn,ms,...args)=>timer(fn,ms===20000?50:ms,...args);
      window.pause=()=>runZentaoOperation({key:'task:18665',label:'暂停任务 #18665',
        run:()=>api('/pause',{method:'POST',body:{}}),
        verify:async ({signal})=>{if(window.hang)return new Promise(()=>{});
          const data=await(await api('/state',{signal})).json();return data.status==='pause'?true:null;}
      });window.ready=true;
      </script>`;
    await page.route('http://operation-recovery.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({contentType:'text/html',body:html});
      if (url.pathname.startsWith('/static/')) return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(root,'frontend',url.pathname.slice(8)))});
      if (url.pathname === '/pause') {
        posts++;
        return succeed ? route.fulfill({json:{ok:true}}) : route.fulfill({status:502,
          headers:outcome?{'X-Operation-Outcome':outcome}:{},json:{detail:'无法读取所选任务，未执行修改'}});
      }
      if (url.pathname === '/state') { reads++; return route.fulfill({json:{status}}); }
      return route.fulfill({json:{}});
    });
    await page.goto('http://operation-recovery.test/');
    await page.waitForFunction(()=>window.ready);
    await page.evaluate(()=>window.pause());
    assert.equal(posts,1);
    assert.equal(reads,0,'known preflight failures must not become uncertain');
    const dialog = page.getByRole('dialog');
    await dialog.getByText('本次未提交修改，可重试。',{exact:false}).waitFor();
    succeed=true;
    await dialog.getByRole('button',{name:'重试',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('[role="dialog"]').length===0);
    assert.equal(posts,2,'explicit retry is unlocked');
    outcome=null;succeed=false;
    await page.evaluate(()=>window.pause());
    await dialog.getByText('第 1 次核对仍未确认成功',{exact:false}).waitFor();
    const count=posts;
    await page.evaluate(()=>window.pause());
    assert.equal(posts,count,'genuinely uncertain writes remain protected');
    await dialog.getByRole('button',{name:'核对结果',exact:true}).click();
    await dialog.getByText('第 2 次核对仍未确认成功',{exact:false}).waitFor();
    assert.equal(posts,count,'checking never resubmits the pause');
    await page.evaluate(()=>{window.hang=true;});
    await dialog.getByRole('button',{name:'核对结果',exact:true}).click();
    await dialog.getByText('核对超时',{exact:false}).waitFor();
    assert(await dialog.getByRole('button',{name:'核对结果',exact:true}).isEnabled());
    await page.evaluate(()=>{window.hang=false;});status='pause';
    await dialog.getByRole('button',{name:'核对结果',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('[role="dialog"]').length===0);
    assert.equal(posts,count);
    assert.deepEqual(errors,[]);
    console.log('PASS: not-started failures retry, uncertain writes stay protected, checks show progress and time out, live confirmation unlocks');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
