// Real binding modal and hydrator; all API requests are intercepted.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [], reads = [];
    page.on('pageerror', error => errors.push(error.stack));
    let refreshCount = 0, ready = false;
    const binding = { base_url: 'https://zentao.invalid', zentao_account: 'alice', has_password: true,
      has_token: true, last_refresh_status: 'error', last_error_message: '禅道登录接口暂时不可用（HTTP 500）' };
    const index = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
    const modal = index.slice(index.indexOf('<div id="zentaoBindingModal"'), index.indexOf('<!-- Personal AI Provider'));
    const html = `<!doctype html><meta charset="utf-8">
      <link rel="stylesheet" href="/static/css/main.css">
      <section id="tab-mine"><div id="mineCards">
        <span class="zt-bug-slot" data-zt-bug-id="101"></span>
        <span class="zt-story-slot" data-zt-story-id="202"></span>
      </div></section>${modal}
      <script>window.messages=[];window.showMessage=(...args)=>messages.push(args);</script>
      <script type="module">
        import '/static/js/zentao-binding.js';import '/static/js/zentao-hydrator.js';
        await window.OmniQAZentao.hydrateContainer(document.getElementById('mineCards'));
        await window.OmniQAZentaoBinding.open();window.ready=true;
      </script>`;
    await page.route('http://token-recovery.test/**', route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(root, 'frontend', url.pathname.slice(8));
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
      }
      if (url.pathname === '/zentao-binding/me') return route.fulfill({ json: { binding } });
      if (url.pathname === '/zentao-binding/me/refresh') {
        refreshCount++;
        if (refreshCount === 1) return route.fulfill({ status: 500, json: { detail: '临时服务故障（HTTP 500）' } });
        if (refreshCount === 2) return route.fulfill({ json: { ok: false, message: binding.last_error_message, binding } });
        ready = true;
        return route.fulfill({ json: { ok: true, binding: { ...binding, last_refresh_status: 'ok', last_error_message: null } } });
      }
      assert.ok(url.pathname.startsWith('/zentao/hydrate/'), 'Only binding and read-only hydration APIs are expected');
      reads.push(url.pathname);
      if (!ready) return route.fulfill({ status: 502, json: { detail: binding.last_error_message } });
      const bug = url.pathname.endsWith('/bugs');
      return route.fulfill({ json: bug ? { 101: { status: 'active', status_zh: '激活', title: 'Bug', assigned_to: 'A' } }
        : { 202: { status: 'active', status_zh: '激活', stage: 'testing', stage_zh: '测试中' } } });
    });
    await page.goto('http://token-recovery.test/');
    await page.waitForFunction(() => window.ready);
    const slots = page.locator('#mineCards [data-zt-bug-id], #mineCards [data-zt-story-id]');
    assert.equal(await page.locator('#mineCards .zt-err').count(), 2);
    assert.equal(await page.locator('#mineCards [data-zt-loaded]').count(), 0, 'Failed authentication must remain retryable');
    const refresh = page.locator('#ztRefreshBtn');
    await refresh.click();
    await page.waitForFunction(() => messages.at(-1)?.[0].includes('刷新请求异常'));
    assert.equal(await refresh.isEnabled(), true);
    assert.equal(reads.length, 2);
    await refresh.click();
    await page.waitForFunction(() => messages.at(-1)?.[0].includes('Token 刷新失败'));
    assert.equal(await refresh.isEnabled(), true);
    assert.equal(reads.length, 2, 'Failed manual refresh must not trigger data reload');
    await refresh.click();
    await page.waitForFunction(() => document.querySelectorAll('#mineCards [data-zt-loaded]').length === 2);
    assert.equal(reads.length, 4, 'Successful manual refresh reloads current data without a page reload');
    assert.match(await slots.allTextContents().then(text => text.join(' ')), /激活.*测试中/s);
    assert.equal(await page.locator('#mineCards .zt-miss, #mineCards .zt-err').count(), 0);
    assert.equal(await page.locator('#ztPassword').inputValue(), '');
    assert.deepEqual(errors, []);
    console.log('PASS: transient token errors remain retryable; refresh button recovers after HTTP/business failures; successful refresh reloads visible Zentao information');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
