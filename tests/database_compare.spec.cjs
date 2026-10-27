// Real browser -> isolated FastAPI -> independent worker -> real SQLite inputs.
// Run with PLAYWRIGHT_MODULE pointing to an existing Playwright installation.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const python = process.env.PYTHON || path.join(root, '.venv', 'Scripts', 'python.exe');

(async () => {
  fs.mkdirSync(path.join(root, '.codex-tmp'), {recursive:true});
  const scratch = fs.mkdtempSync(path.join(root, '.codex-tmp', 'database-compare-ui-'));
  const a = path.join(scratch,'old.db'), b = path.join(scratch,'new.db');
  execFileSync(python, ['-c', `import sqlite3,sys
for i,p in enumerate(sys.argv[1:]):
 c=sqlite3.connect(p)
 c.executescript('CREATE TABLE items(id INTEGER PRIMARY KEY,value TEXT,nullable TEXT,big INTEGER,payload BLOB); CREATE TABLE EXTRADOMAINVALUE(ID INTEGER,value TEXT);')
 c.execute('INSERT INTO items VALUES(1,?,?,?,?)',('old' if i==0 else '<script>new</script>',None if i==0 else '',9223372036854775806+i,bytes([i,255])))
 c.executemany('INSERT INTO EXTRADOMAINVALUE VALUES(5,?)',[('repeat',)]*(3 if i==0 else 2))
 c.commit();c.close()
`, a, b]);
  const socket = net.createServer();
  await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
  const port = socket.address().port;
  await new Promise(resolve=>socket.close(resolve));
  let log = '';
  const server = spawn(python,[path.join(root,'tests','database_compare_server.py'),String(port)],{
    cwd:root, env:{...process.env,DATABASE_COMPARE_TEST_ROOT:scratch,PYTHONIOENCODING:'utf-8'},windowsHide:true,
    stdio:['ignore','pipe','pipe']
  });
  server.stdout.on('data',chunk=>{log+=chunk});server.stderr.on('data',chunk=>{log+=chunk});
  let browser;
  const base = `http://127.0.0.1:${port}`;
  try {
    let ready = false;
    for (let i=0;i<100;i++) {
      if (server.exitCode !== null) throw new Error('Harness exited: '+log);
      try {if ((await fetch(base)).ok) {ready=true;break;}} catch {}
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    assert(ready,'Test server did not start: '+log);
    browser = await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
    const page = await browser.newPage({viewport:{width:1360,height:1000}});
    const errors = [];
    page.on('pageerror',e=>{errors.push(e.message);console.error('Browser error:', e.message);});
    page.on('requestfailed',request=>console.error('Browser request failed:',request.url(),request.failure()?.errorText));
    page.on('response',response=>{if(response.status()>=400)console.error('HTTP error:',response.status(),response.url());});
    await page.goto(base);
    await page.waitForFunction(()=>window.testReady);
    assert.equal(await page.locator('#tab-tools h2').first().innerText(), '实用工具');
    assert.equal(await page.getByRole('tab', {name:'数据库对比'}).getAttribute('aria-selected'), 'true');
    assert(await page.locator('#tab-tools h2').first().evaluate(el => el.getBoundingClientRect().top < document.getElementById('databaseCompareRoot').getBoundingClientRect().top));
    await page.locator('#dcOldFiles').setInputFiles(['ATTRIBUTE.db','SYSTEM.db','TASK.db'].map(name => ({name,mimeType:'application/octet-stream',buffer:fs.readFileSync(a)})));
    assert.equal(await page.locator('#dcOldFileList .dc-file-card').count(),3);
    assert.equal(await page.locator('#dcOldFileList .dc-file-icon svg').count(),3);
    assert.match(await page.locator('#dcOldFileSummary').innerText(),/共 .* KB/);
    await page.locator('#dcOldFileList [aria-label="移除 TASK.db"]').click();
    assert.equal(await page.locator('#dcOldFileList .dc-file-card').count(),2);
    // Drop real SQLite bytes into the target side and verify its native FileList.
    await page.evaluate(data => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([Uint8Array.from(atob(data), c=>c.charCodeAt(0))], 'ATTRIBUTE.db'));
      document.getElementById('dcNewDropzone').dispatchEvent(new DragEvent('drop', {bubbles:true,cancelable:true,dataTransfer:transfer}));
    }, fs.readFileSync(b).toString('base64'));
    assert.equal(await page.locator('#dcNewFiles').evaluate(el=>el.files[0].name),'ATTRIBUTE.db');
    assert.equal(await page.locator('#dcNewFileList .dc-file-card').count(),1);
    await page.locator('#dcNewFiles').setInputFiles({name:'SYSTEM.sqlite3',mimeType:'application/octet-stream',buffer:fs.readFileSync(b)});
    assert.equal(await page.locator('#dcNewFileList .dc-file-card').count(),2,'Additional selections preserve earlier files');
    await page.screenshot({path:path.join(scratch,'file-cards.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'File cards overflow on mobile');
    await page.screenshot({path:path.join(scratch,'file-cards-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1360,height:1000});
    await page.locator('#dcOldFiles').setInputFiles([]);
    await page.locator('#dcNewFiles').setInputFiles([]);
    await page.locator('#dcOldFiles').setInputFiles(a);
    await page.locator('#dcNewFiles').setInputFiles(b);
    await page.locator('#dcOldLabel').fill('v1');await page.locator('#dcNewLabel').fill('v2');
    await page.locator('#dcPreview').click();
    await page.waitForFunction(()=>!document.getElementById('dcStart').disabled);
    assert.match(await page.locator('#dcPreviewArea').innerText(),/EXTRADOMAINVALUE/);
    assert.equal(await page.locator('#dcOldFileList .dc-file-state').innerText(),'已上传');
    // A changed snapshot must invalidate the previous preview before comparison.
    const inputDirectory = path.join(scratch,'storage','inputs');
    const abandonedPreview = fs.readdirSync(inputDirectory).find(id=>fs.existsSync(path.join(inputDirectory,id,'old.db')));
    await page.locator('#dcOldFileList [data-remove-file]').click();
    assert.equal(await page.locator('#dcStart').isDisabled(),true);
    assert.equal(await page.locator('#dcPreviewArea').innerText(),'');
    await page.locator('#dcOldFiles').setInputFiles(a);
    await page.locator('#dcPreview').click();
    await page.waitForFunction(()=>!document.getElementById('dcStart').disabled);
    await page.locator('#dcStart').click();
    await page.waitForFunction(()=>document.getElementById('dcTaskSummary').textContent.includes('已完成'),null,{timeout:30000});
    await page.waitForSelector('#dcDifferences .dc-diff-item');
    assert.match(await page.locator('#dcTaskSummary').innerText(),/发现差异/);
    assert.equal(await page.locator('#dcDifferences script').count(),0);
    await page.locator('#dcFilterScope').selectOption('data');
    await page.locator('#dcFilterKind').selectOption('row_removed');
    await page.waitForFunction(()=>document.querySelectorAll('#dcDifferences .dc-diff-item').length===1 && document.getElementById('dcDifferences').textContent.includes('减少记录'));
    assert.match(await page.locator('#dcDifferences').innerText(),/1 条/);
    await page.locator('#dcFilterKind').selectOption('value_changed');
    await page.waitForFunction(()=>document.querySelectorAll('#dcDifferences .dc-diff-item').length===4 && document.getElementById('dcDifferences').textContent.includes('值修改'));
    for (const item of await page.locator('#dcDifferences summary').all()) await item.click();
    const readable = await page.locator('#dcDifferences').innerText();
    assert.match(readable,/<script>new<\/script>/);
    assert.match(readable,/记录定位/);
    assert.match(readable,/NULL/);
    assert.match(readable,/空字符串/);
    assert.match(readable,/9223372036854775807/);
    assert.match(readable,/Base64/);
    assert.match(await page.locator('#dcDifferences .dc-location').first().innerText(),/id/);
    assert.equal(await page.locator('#dcDifferences pre').count(),0);
    assert.equal(await page.locator('#dcDifferences script').count(),0);
    await page.screenshot({path:path.join(scratch,'readable-differences.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'Mobile layout overflows');
    await page.screenshot({path:path.join(scratch,'readable-differences-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1360,height:1000});
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#dcExcel').click();
    const download = await downloadPromise;
    const archive = path.join(scratch,'report.zip');
    await download.saveAs(archive);
    execFileSync(python,['-c',`import json,sys,zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
 d=json.loads(z.read('complete.json'))
 assert d['summary']['rows_removed']==1 and d['summary']['rows_modified']==1
 assert d['summary']['old_label']=='v1'
 assert any(x['details'].get('new',{}).get('value')=='<script>new</script>' for x in d['differences'])
`,archive]);
    await page.waitForFunction(()=>!document.getElementById('dcRerun').disabled);
    await page.locator('#dcRerun').click();
    await page.waitForFunction(()=>document.getElementById('dcHistory').querySelectorAll('tbody tr').length===2);
    await page.waitForFunction(()=>document.getElementById('dcTaskSummary').textContent.includes('已完成'));
    await page.screenshot({path:path.join(scratch,'database-compare.png'),fullPage:true});
    const selectedId = (await page.locator('#dcTaskSummary').innerText()).match(/任务编号 ([0-9a-f]{32})/)[1];
    // Dismissing confirmation preserves both history and files.
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator(`[data-delete-task="${selectedId}"]`).click();
    await page.waitForFunction(()=>!document.getElementById('dcCleanup').disabled);
    assert.equal(await page.locator('#dcHistory tbody tr').count(),2);
    // Removing the task currently open also clears its result panel.
    page.once('dialog', dialog => dialog.accept());
    await page.locator(`[data-delete-task="${selectedId}"]`).click();
    await page.waitForFunction(()=>document.getElementById('dcTaskPanel').hidden).catch(async error=>{
      console.error(await page.locator('#dcMessage').innerText());
      console.error(await page.locator('#dcHistory').innerText());
      throw error;
    });
    await page.waitForFunction(()=>document.getElementById('dcHistory').querySelectorAll('tbody tr').length===1);
    await page.locator('#dcSelectPage').check();
    assert.match(await page.locator('#dcDeleteSelected').innerText(),/1/);
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#dcDeleteSelected').click();
    await page.waitForFunction(()=>document.getElementById('dcHistory').textContent.includes('暂无比较任务'));
    assert.equal(await page.locator('#dcStart').isDisabled(),true);
    assert.equal(await page.locator('#dcPreviewArea').innerText(),'');
    assert.equal(fs.readdirSync(path.join(scratch,'storage','tasks')).length,0);
    // Uploads from abandoned previews are outside task-history cleanup.
    assert.deepEqual(fs.readdirSync(inputDirectory),[abandonedPreview]);
    // Re-upload after releasing snapshots, then clear all finished history.
    await page.locator('#dcPreview').click();
    await page.waitForFunction(()=>!document.getElementById('dcStart').disabled);
    await page.locator('#dcStart').click();
    await page.waitForFunction(()=>document.getElementById('dcTaskSummary').textContent.includes('已完成'));
    await page.waitForFunction(()=>!document.getElementById('dcCleanup').disabled);
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#dcCleanup').click();
    await page.waitForFunction(()=>document.getElementById('dcHistory').textContent.includes('暂无比较任务'));
    assert.deepEqual(fs.readdirSync(inputDirectory),[abandonedPreview]);
    await page.screenshot({path:path.join(scratch,'history-cleaned.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('Database compare browser/worker acceptance passed. Artifacts: '+scratch);
  } finally {
    if (browser) await browser.close();
    // Ask this test-only server to shut down its worker through its own process handle.
    if (server.exitCode===null) {
      await fetch(base+'/__test__/shutdown',{method:'POST'}).catch(()=>{});
      await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,6000))]);
      if (server.exitCode===null) server.kill();
    }
    fs.writeFileSync(path.join(scratch,'server.log'),log);
  }
})().catch(error=>{console.error(error);process.exitCode=1});
