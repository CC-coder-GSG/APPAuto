const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  try {
    const page = await browser.newPage();
    const errors = [], requests = [];
    page.on('pageerror', e => errors.push(e.message));
    let block, finishActivation, mode = 'normal', active = 0, peak = 0;
    await page.route('http://async.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({contentType:'text/html',body:`<!doctype html><meta charset="utf-8"><div class="workbench-shell-header"><h2>Workbench</h2></div><div id="cards"></div><div id="editModal" role="dialog" style="display:flex"><div><input id="value" value="original"><button id="submit">Save</button></div></div><div id="otherModal" role="dialog" style="display:none">Other editor</div><div id="gallery"></div><script>window.messages=[];window.showMessage=(...args)=>messages.push(args)</script><script type="module">import '/static/js/zentao-hydrator.js'; import * as op from '/static/js/components/zentao-operation.js'; import * as viewer from '/static/js/components/image-viewer.js'; window.op=op; window.viewer=viewer;</script>`});
      if (url.pathname.startsWith('/static/')) return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(root,'frontend',url.pathname.slice(8)))});
      if (url.pathname.startsWith('/zentao/hydrate/')) {
        const ids = url.searchParams.get('ids').split(',');
        requests.push({kind:url.pathname,ids}); active++; peak=Math.max(peak,active);
        try {
          if (mode === 'blocked' && ids.includes('21')) await new Promise(resolve => { block=resolve; });
          if (mode === 'auth') return await route.fulfill({json:{__fetch_errors__:ids,__auth_required__:true}});
          if (mode === 'error') return await route.fulfill({status:502,json:{detail:'temporary'}});
          await route.fulfill({json:Object.fromEntries(ids.map(id=>[id,{id:Number(id),status:'active',status_zh:'Active',assigned_to:'Alice'}]))});
        } finally { active--; }
        return;
      }
      if (url.pathname.match(/^\/zentao\/story\/\d+\/detail$/)) return route.fulfill({json:{id:1,title:'Preview',spec:'<img src="/zentao/files/1">',verify:''}});
      if (url.pathname === '/zentao/files/1') return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>'});
      if (url.pathname.endsWith('/action-meta')) return route.fulfill({json:{users:{alice:'Alice'},builds:{1:'Version 1'},current_assigned:'alice'}});
      if (url.pathname === '/overall-test/overview') return route.fulfill({json:{bug_pool:[{id:7,my_minor_version_id:3,my_test_done:true,my_resolution:'fixed'}]}});
      if (url.pathname === '/zentao/bugs/77/preview') return route.fulfill({json:{status:'closed'}});
      if (url.pathname === '/zentao/bugs/11/active') {
        await new Promise(resolve=>{finishActivation=resolve;});
        return route.fulfill({json:{ok:true}});
      }
      throw new Error(`Unexpected request: ${url.pathname}`);
    });
    await page.goto('http://async.test/');
    await page.waitForFunction(()=>window.op && window.OmniQAZentao);
    const start = count => page.evaluate(n => {
      const cards=document.getElementById('cards');
      cards.innerHTML=Array.from({length:n},(_,i)=>`<span class="zt-story-slot" data-zt-story-id="${i+1}"></span>`).join('');
      window.hydrated=false;
      window.OmniQAZentao.hydrateContainer(cards).then(()=>window.hydrated=true);
    },count);
    for (const count of [10,50,200,501]) {
      await page.evaluate(()=>window.OmniQAZentao.invalidate()); requests.length=0; peak=0;
      await start(count); await page.waitForFunction(()=>window.hydrated);
      assert.equal(await page.locator('#cards [data-zt-loaded]').count(),count);
      assert.equal(new Set(requests.flatMap(r=>r.ids)).size,count);
      assert.ok(requests.every(r=>r.ids.length<=20)); assert.ok(peak<=2);
    }
    await page.evaluate(()=>window.OmniQAZentao.invalidate()); requests.length=0; mode='blocked';
    await start(50);
    await page.waitForFunction(()=>document.querySelectorAll('#cards [data-zt-loaded]').length>=20);
    assert.equal(await page.evaluate(()=>window.hydrated),false,'First batch renders before delayed batch');
    await page.evaluate(()=>{
      const card=document.createElement('div'); card.id='duplicate'; card.innerHTML='<span class="zt-story-slot" data-zt-story-id="21"></span>';
      document.body.append(card); window.OmniQAZentao.hydrateContainer(card);
    });
    assert.equal(requests.flatMap(r=>r.ids).filter(id=>id==='21').length,1,'Concurrent readers share requests');
    block(); mode='normal'; await page.waitForFunction(()=>window.hydrated);
    requests.length=0; await start(50); await page.waitForFunction(()=>window.hydrated);
    assert.equal(requests.length,0,'Fresh cache renders without network');
    for (const [failure,expected] of [['auth',1],['error',2]]) {
      await page.evaluate(()=>window.OmniQAZentao.invalidate()); requests.length=0; mode=failure;
      await start(10); await page.waitForFunction(()=>window.hydrated);
      assert.equal(requests.length,expected); assert.equal(await page.locator('#cards .zt-err').count(),10);
      assert.equal(await page.locator('#cards [data-zt-loaded]').count(),0);
    }
    console.log('PASS hydration: 10/50/200/501 IDs, <=20 per batch, <=2 concurrent, progressive display, dedup, cache, bounded retries, auth');

    await page.evaluate(()=>{
      window.writes=0; window.completed=0; window.current=1;
      window.config={key:'task:1',label:'Save effort',modal:'editModal',isCurrent:()=>current===1,
        restore:()=>{current=1;}, run:()=>{writes++;return new Promise(resolve=>window.finish=resolve);},onSuccess:()=>completed++};
      window.op.runZentaoOperation(config);
    });
    assert.equal(await page.locator('#submit').isDisabled(),true);
    await page.keyboard.press('Escape'); assert.equal(await page.locator('#editModal').isVisible(),false);
    await page.evaluate(()=>window.op.runZentaoOperation(config)); assert.equal(await page.evaluate(()=>writes),1);
    await page.evaluate(()=>{ current=2; document.getElementById('value').value='another task'; finish({ok:true}); });
    await page.waitForFunction(()=>completed===1);
    assert.equal(await page.locator('#value').inputValue(),'another task');
    assert.equal(await page.locator('#submit').isEnabled(),true);
    await page.evaluate(()=>{
      current=1; document.getElementById('value').value='preserved';
      window.failed=op.runZentaoOperation({...config,key:'task:2',run:()=>new Promise((_,reject)=>window.reject=reject)});
    });
    await page.keyboard.press('Escape');
    await page.evaluate(()=>{ document.getElementById('otherModal').style.display='flex'; reject(Object.assign(new Error('validation failed'),{status:422})); });
    await page.evaluate(()=>failed);
    assert.equal(await page.locator('#editModal').isVisible(),false,'Failure does not interrupt another editor');
    await page.evaluate(()=>document.getElementById('otherModal').style.display='none');
    await page.locator('#wb-operation-dock [data-open]').click();
    assert.equal(await page.locator('#value').inputValue(),'preserved');
    assert.match(await page.locator('#editModal').innerText(),/validation failed/);
    await page.locator('#wb-operation-dock [data-dismiss]').click();
    await page.evaluate(async()=>{
      window.verified=false; window.uncertainWrites=0;
      window.unknown={key:'task:3',label:'Uncertain',run:async()=>{uncertainWrites++;throw new Error('network lost');},verify:async()=>verified};
      // null means unconfirmed, false is a definite rejection.
      unknown.verify=async()=>verified ? true : null;
      await op.runZentaoOperation(unknown); await op.runZentaoOperation(unknown);
    });
    assert.equal(await page.evaluate(()=>uncertainWrites),1);
    await page.evaluate(()=>verified=true); await page.locator('#wb-operation-dock [data-check]').click();
    await page.waitForFunction(()=>document.getElementById('wb-operation-dock').hidden);
    console.log('PASS operations: minimize, duplicate suppression, immutable context, failed input recovery, other editor protection, uncertain write readback');

    await page.evaluate(()=>{
      const data='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="red"/></svg>');
      gallery.innerHTML=`<a href="/never"><img src="${data}"></a><img src="${data}">`;
      viewer.wireImageViewer(gallery); window.escaped=0;
      document.addEventListener('keydown',e=>{if(e.key==='Escape')escaped++;});
    });
    await page.locator('#editModal').evaluate(el=>el.style.display='none');
    await page.locator('#gallery img').first().click();
    await page.waitForFunction(()=>!document.querySelector('#entityImageViewer img').hidden);
    await page.locator('#entityImageViewer [data-action=in]').click();
    assert.match(await page.locator('#entityImageViewer img').getAttribute('style'),/scale\(1.3\)/);
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(()=>document.querySelector('.image-caption').textContent.startsWith('2 / 2'));
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#entityImageViewer').isVisible(),false); assert.equal(await page.evaluate(()=>escaped),0);
    assert.deepEqual(errors,[]);
    console.log('PASS image viewer: linked thumbnail interception, gallery, zoom, Escape isolation');
    await page.evaluate(async()=>{
      await import('/static/js/components/entity-preview-modal.js');
      window.revoked=[]; const revoke=URL.revokeObjectURL.bind(URL);
      URL.revokeObjectURL=url=>{revoked.push(url);revoke(url);};
      await window.OmniQAPreview.openStory(1);
    });
    await page.waitForFunction(()=>document.querySelector('#entityPreviewBody img')?.src.startsWith('blob:'));
    const blob = await page.locator('#entityPreviewBody img').getAttribute('src');
    await page.locator('#entityPreviewBody img').click();
    await page.waitForFunction(()=>!document.querySelector('#entityImageViewer img').hidden);
    assert.equal(await page.locator('#entityImageViewer img').getAttribute('src'),blob,'Viewer reuses authenticated blob');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#entityPreviewBody').isVisible(),true,'First Escape preserves detail modal');
    await page.evaluate(()=>window.OmniQAPreview.close());
    assert.ok(await page.evaluate(url=>revoked.includes(url),blob));
    assert.deepEqual(errors,[]);
    console.log('PASS authenticated preview integration: shared blob, Escape layering, URL cleanup');
    const markup=fs.readFileSync(path.join(root,'frontend/index.html'),'utf8');
    await page.evaluate(async html=>{
      const doc=new DOMParser().parseFromString(html,'text/html');
      document.body.append(doc.getElementById('s5ReactivateModal'));
      window.overall=await import('/static/js/tabs/overall-test.js');
      window.activated=null;
      overall.openS5ReactivateModalAsync(1,11).then(result=>activated=result);
    },markup);
    await page.waitForFunction(()=>document.getElementById('s5ReactivateUserSelect')?.value==='alice');
    await page.evaluate(()=>{window.activation=overall.submitS5Reactivate();});
    await page.waitForFunction(()=>document.getElementById('s5ReactivateSubmitBtn').disabled);
    await page.evaluate(()=>overall.closeS5ReactivateModal());
    assert.equal(await page.evaluate(()=>activated),null,'Minimize does not cancel caller workflow');
    await page.evaluate(()=>overall.openS5ReactivateModal(2,22));
    await page.waitForFunction(()=>document.getElementById('s5ReactivateUserSelect')?.value==='alice');
    await page.locator('#s5ReactivateComment').fill('second bug input');
    finishActivation(); await page.evaluate(()=>activation);
    assert.equal(await page.evaluate(()=>activated),true);
    assert.equal(await page.locator('#s5ReactivateModal').isVisible(),true);
    assert.equal(await page.locator('#s5ReactivateComment').inputValue(),'second bug input');
    assert.deepEqual(errors,[]);
    console.log('PASS real Bug reactivation: minimize keeps workflow alive; completion does not close or overwrite another Bug editor');
    const readback = await page.evaluate(async()=>{
      const {verifyBugResult}=await import('/static/js/components/bug-result-operation.js');
      const target={bugId:7,ztId:77,majorId:1,minorId:3,done:true,resolution:'fixed'};
      return [await verifyBugResult(target),await verifyBugResult({...target,minorId:4}),await verifyBugResult({...target,resolution:'rejected'})];
    });
    assert.deepEqual(readback,[true,null,null]);
    console.log('PASS composite readback: verifies remote status and exact local minor/resolution before confirming success');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
