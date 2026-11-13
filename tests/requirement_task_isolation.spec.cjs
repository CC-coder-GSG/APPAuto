// An uncertain write to one task must not lock its sibling tasks.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const page=await browser.newPage();
    const calls=[],errors=[];
    page.on('pageerror',e=>errors.push(e.stack));
    const tasks=[18665,18636,17199].map((id,i)=>({task_id:id,name:`任务 ${id}`,type:'test',
      status:i?'pause':'doing',assigned_to_me:true,can_operate:true,estimate:4}));
    const rows=()=>[{id:647,zentao_req_id:'r#6518',title:'增加爱尔兰坐标系',major_version_id:470,
      zentao_task_id:null,has_multiple_test_tasks:true,test_tasks:tasks,test_cases:[],free_bugs:[],
      story_tasks:[],case_completed:true,test_completed:false,all_linked_test_tasks_completed:false}];
    const html=`<!doctype html><meta charset="utf-8"><select id="mineMajorSelect"><option value="470">V4.0.5.0</option></select><div id="mineCards"></div>
      <script type="module">
      import * as mine from '/static/js/tabs/mine.js';import {state} from '/static/js/state.js';
      window.mine=mine;state.currentUser={id:5};window.showMessage=()=>{};
      await mine.loadMyWorkbench();window.ready=true;
      </script>`;
    await page.route('http://task-isolation.test/**',async route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
      if(url.pathname.startsWith('/static/'))return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(root,'frontend',url.pathname.slice(8)))});
      if(url.pathname==='/workbench/mine')return route.fulfill({json:rows()});
      if(url.pathname==='/bugs/dispatched-to-me')return route.fulfill({json:[]});
      if(url.pathname==='/zentao/task/18665/detail')return route.fulfill({status:500,body:'Internal Server Error'});
      const match=url.pathname.match(/^\/requirements\/647\/linked-tasks\/(\d+)\/operate$/);
      if(match){
        const taskId=Number(match[1]),body=route.request().postDataJSON();calls.push({taskId,...body});
        if(taskId===18665)return route.fulfill({status:500,body:'Internal Server Error'});
        tasks.find(t=>t.task_id===taskId).status=body.action==='pause'?'pause':'doing';
        return route.fulfill({json:{ok:true}});
      }
      return route.fulfill({json:{}});
    });
    await page.goto('http://task-isolation.test/');await page.waitForFunction(()=>window.ready);
    await page.evaluate(async()=>{window.mine.selectRequirementTask(647,18665);await window.mine.pauseReqTask(647);});
    const dialog=page.getByRole('dialog');
    await dialog.getByText('第 1 次核对仍未确认成功',{exact:false}).waitFor();
    await dialog.getByRole('button',{name:'收起',exact:true}).click();
    await page.evaluate(async()=>{window.mine.selectRequirementTask(647,18636);await window.mine.startReqTask(647);});
    await page.evaluate(async()=>{window.mine.selectRequirementTask(647,17199);await window.mine.startReqTask(647);await window.mine.pauseReqTask(647);});
    assert.deepEqual(calls,[{taskId:18665,action:'pause'},{taskId:18636,action:'start'},
      {taskId:17199,action:'start'},{taskId:17199,action:'pause'}]);
    await page.evaluate(async()=>{window.mine.selectRequirementTask(647,18665);await window.mine.pauseReqTask(647);});
    assert.equal(calls.filter(c=>c.taskId===18665).length,1,'same uncertain task must still prevent duplicate writes');
    assert.deepEqual(errors,[]);
    console.log('PASS: failed pause/check on #18665 does not block #18636 or #17199, same-task uncertainty stays protected');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
