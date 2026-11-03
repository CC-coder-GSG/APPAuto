const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 720 } });
    const errors = [], requests = [];
    let navigations = 0, finalTest = false;
    let holdSnapshot = false, releaseSnapshot;
    page.on('pageerror', error => errors.push(error.message));
    page.on('framenavigated', () => navigations++);
    page.on('dialog', dialog => dialog.accept());
    const rows = Array.from({ length: 24 }, (_, i) => ({
      id:i+1, zentao_req_id:`r#${i+1}`, title:`Requirement ${i+1}`,
      major_version_id:1, major_version_name:'V1', zentao_story_id:i+1,
      case_completed:false, test_completed:false, test_cases:[], free_bugs:[], story_tasks:[],
    }));
    const index = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
    const start = index.indexOf('<section id="tab-mine"');
    const section = index.slice(start,index.indexOf('</section>',start)+10).replace('hidden card','card');
    const html = `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/css/main.css">${section}
      <script>window.sseHandlers={};window.OmniQASSE={subscribe:(name,fn)=>{sseHandlers[name]=fn}};window.currentSoftwareId=1;window.versions=[{id:1,version_type:'major',version_no:'V1'},{id:2,version_type:'minor',version_no:'V1.1',parent_id:1}];window.showMessage=()=>{};window.isWorkbenchSubtabActive=()=>true;</script>
      <script type="module">
        import * as mine from '/static/js/tabs/mine.js';import {state} from '/static/js/state.js';
        window.mine=mine;window.state=state;window.loadMyWorkbench=()=>mine.loadMyWorkbench();
        window.setReqStatus=(...args)=>{window.beforeChangeTop=document.querySelector('.mine-req-card[data-req-id="11"]').getBoundingClientRect().top;return mine.setReqStatus(...args)};
        window.handleTestCompletedToggle=(...args)=>mine.handleTestCompletedToggle(...args);
        window.setFinalTestStatus=(...args)=>mine.setFinalTestStatus(...args);
        window.confirmMineTestExecutionModal=()=>mine.confirmMineTestExecutionModal();
        state.currentUser={id:1};document.getElementById('mineMajorSelect').innerHTML='<option value="1">V1</option>';
        document.getElementById('mineMinorSelect').innerHTML='<option value="2">V1.1</option>';
        await mine.loadMyWorkbench();window.ready=true;
      </script>`;
    await page.route('http://mine-status.test/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({ contentType:'text/html', body:html });
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(root,'frontend',url.pathname.slice(8));
        return route.fulfill({ contentType:file.endsWith('.css')?'text/css':'text/javascript', body:fs.readFileSync(file) });
      }
      requests.push(`${route.request().method()} ${url.pathname}`);
      if (url.pathname === '/workbench/mine') {
        const snapshot = JSON.parse(JSON.stringify(rows.map(row=>({...row,final_test:finalTest}))));
        if (holdSnapshot) return new Promise(resolve => { releaseSnapshot = () => route.fulfill({json:snapshot}).then(resolve); });
        return route.fulfill({json:snapshot});
      }
      if (url.pathname === '/requirements/11/status' && route.request().method()==='PATCH') {
        Object.assign(rows[10],route.request().postDataJSON());return route.fulfill({json:{ok:true}});
      }
      if (url.pathname === '/requirements/11/test-execution') {
        rows[10].test_completed=true;return route.fulfill({json:{ok:true}});
      }
      if (url.pathname === '/final-test/requirements/11/status') {
        Object.assign(rows[10],route.request().postDataJSON());
        if (rows[10].test_completed) rows[10].testing=false;
        rows[10].final_test_people=[...(rows[10].test_completed||rows[10].testing ? [{user_id:1,user_name:'Alice',status:rows[10].test_completed?'completed':'testing'}]:[])];
        return route.fulfill({json:{ok:true}});
      }
      if (url.pathname === '/workbench/preflight-refresh') return route.fulfill({json:{bugs:{cached:true},testcases:{cached:true}}});
      if (url.pathname === '/workbench/refresh-task-status') return route.fulfill({json:{refreshed:false}});
      return route.fulfill({json:[]});
    });
    await page.goto('http://mine-status.test/');await page.waitForFunction(()=>window.ready);
    const target = page.locator('.mine-req-card[data-req-id="11"]');
    const other = page.locator('.mine-req-card[data-req-id="12"]');
    await target.scrollIntoViewIfNeeded();
    await page.evaluate(() => {
      const a=document.querySelector('.mine-req-card[data-req-id="11"]');
      const b=document.querySelector('.mine-req-card[data-req-id="12"]');
      a.open=true;b.open=true;b.dataset.preserved='yes';
    });
    const initialNavigations=navigations;
    const caseBox=()=>target.locator('label').filter({hasText:'用例完成'}).locator('input');
    const testBox=()=>target.locator('label').filter({hasText:'测试完成'}).locator('input');
    const count = requests.filter(x=>x==='GET /workbench/mine').length;
    await caseBox().check();
    await page.waitForFunction(()=>window.state?.currentMineData?.find(r=>r.id===11)?.case_completed===true);
    assert.equal(requests.filter(x=>x==='GET /workbench/mine').length,count+1,'one immediate list refresh');
    assert.equal(await target.evaluate(el=>el.open),true);
    assert.equal(await other.getAttribute('data-preserved'),'yes','unchanged card is same DOM node');
    assert.equal(await other.evaluate(el=>el.open),true);
    const before = await page.evaluate(()=>window.beforeChangeTop);
    const after = await target.evaluate(el=>el.getBoundingClientRect().top);
    assert.ok(Math.abs(after-before)<5,'scroll anchor stays put');
    assert.equal(navigations,initialNavigations);
    console.log('PASS case completion preserves scroll, opened cards, DOM identity and uses one immediate list request');

    await testBox().check();
    await page.locator('#mineTestExecModal').waitFor({state:'visible'});
    await page.locator('#mineTestExecModal').getByRole('button',{name:'确认提交'}).click();
    await page.waitForFunction(()=>document.querySelector('.mine-req-card[data-req-id="11"] input[type="checkbox"]:checked') && document.querySelector('.mine-req-card[data-req-id="11"] summary [style*="line-through"]'));
    assert.equal(await other.getAttribute('data-preserved'),'yes');
    assert.equal(await other.evaluate(el=>el.open),true);
    assert.equal(await target.evaluate(el=>el.open),true);
    assert.equal(navigations,initialNavigations);
    console.log('PASS test completion preserves other cards and target expansion');

    await caseBox().uncheck();
    await page.waitForFunction(()=>!document.querySelector('.mine-req-card[data-req-id="11"] input').checked);
    assert.equal(await other.getAttribute('data-preserved'),'yes');
    console.log('PASS unchecking completion keeps view state');

    finalTest=true;
    rows[10].test_completed=false;
    await page.evaluate(()=>mine.loadMyWorkbench());
    await other.evaluate(el=>{el.dataset.preserved='yes';el.open=true;});
    await caseBox().check();
    await page.waitForFunction(()=>document.querySelector('.mine-req-card[data-req-id="11"] input').checked);
    assert.equal(await other.getAttribute('data-preserved'),'yes');
    assert.equal(await target.evaluate(el=>el.open),true);
    console.log('PASS final-test checkbox keeps unrelated cards');
    const testingBox=()=>target.locator('input[onchange*="testing"]');
    await testingBox().check();
    await target.locator('.final-test-person.is-testing').waitFor();
    assert.match(await target.locator('.final-test-people').innerText(),/Alice/);
    await testBox().check();
    await target.locator('.final-test-person.is-completed').waitFor();
    assert.equal(await testingBox().count(),0);
    assert.equal(await target.locator('.final-test-person.is-testing').count(),0);
    const readsBeforePresence = requests.filter(x=>x==='GET /workbench/mine').length;
    await page.evaluate(()=>sseHandlers.final_test_requirement_status_changed({payload:{requirement_id:11,user_id:2,user_name:'<Bob>',testing:true,test_completed:false}}));
    await page.waitForFunction(()=>document.querySelector('.mine-req-card[data-req-id="11"] .final-test-people')?.textContent.includes('<Bob>'));
    assert.equal(await target.locator('.final-test-people bob').count(),0);
    assert.equal(requests.filter(x=>x==='GET /workbench/mine').length,readsBeforePresence,'live labels require no workbench request');
    await page.evaluate(()=>sseHandlers.final_test_requirement_status_changed({payload:{requirement_id:11,user_id:2,user_name:'<Bob>',testing:false,test_completed:true}}));
    assert.equal(await target.locator('.final-test-person.is-testing').count(),0);
    assert.equal(await target.locator('.final-test-person.is-completed').count(),2);
    holdSnapshot = true;
    await page.evaluate(()=>{window.pendingSnapshot=mine.loadMyWorkbench({background:true});});
    while (!releaseSnapshot) await new Promise(resolve=>setTimeout(resolve,10));
    await page.evaluate(()=>sseHandlers.final_test_requirement_status_changed({payload:{requirement_id:11,user_id:2,user_name:'<Bob>',testing:true,test_completed:false}}));
    holdSnapshot = false;
    await releaseSnapshot();
    await page.evaluate(()=>window.pendingSnapshot);
    assert.match(await target.locator('.final-test-person.is-testing').innerText(),/Bob/,'an older HTTP snapshot must not erase the live tag');
    rows[10].final_test_people.push({user_id:2,user_name:'<Bob>',status:'completed'});
    await page.evaluate(()=>sseHandlers.connection_restored({}));
    await page.waitForFunction(()=>document.querySelectorAll('.mine-req-card[data-req-id="11"] .final-test-person.is-completed').length===2);
    assert.equal(await other.getAttribute('data-preserved'),'yes');
    assert.equal(await target.evaluate(el=>el.open),true);
    console.log('PASS final-test shared presence, completion hides testing, other-user SSE, escaped names and preserved cards');
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
