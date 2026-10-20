// Run: node --test tests/request_id.spec.cjs
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { webcrypto } = require('node:crypto');
const { test } = require('node:test');
const path = require('node:path');

// Load the browser module without changing the application's module settings.
const source = readFileSync(path.join(__dirname, '../frontend/js/request-id.js'), 'utf8');
const modulePromise = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const uuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test('uses native randomUUID with its Crypto receiver', async () => {
  const { createRequestId } = await modulePromise;
  const provider = {
    randomUUID() {
      assert.equal(this, provider);
      return 'native-request-id';
    },
    getRandomValues() { assert.fail('native UUID should be used'); },
  };
  assert.equal(createRequestId(provider), 'native-request-id');
});

test('HTTP fallback produces distinct UUID v4 request IDs using browser-compatible crypto', async () => {
  const { createRequestId } = await modulePromise;
  const provider = {
    getRandomValues(bytes) {
      assert.equal(this, provider);
      return webcrypto.getRandomValues(bytes);
    },
  };
  const ids = Array.from({ length: 1000 }, () => createRequestId(provider));
  assert.ok(ids.every(id => uuidV4.test(id)));
  assert.equal(new Set(ids).size, ids.length);
});

test('fallback retains random bits while setting UUID version and variant', async () => {
  const { createRequestId } = await modulePromise;
  assert.equal(createRequestId({ getRandomValues: bytes => bytes.fill(0xff) }),
    'ffffffff-ffff-4fff-bfff-ffffffffffff');
  assert.equal(createRequestId({ getRandomValues: bytes => bytes.fill(0) }),
    '00000000-0000-4000-8000-000000000000');
});

test('missing crypto reports an actionable browser compatibility error', async () => {
  const { createRequestId } = await modulePromise;
  for (const provider of [null, {}, { randomUUID: true }]) {
    assert.throws(() => createRequestId(provider), /升级浏览器/);
  }
});
