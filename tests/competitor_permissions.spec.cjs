const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = (file) => fs.readFileSync(path.join(__dirname, '../frontend', file), 'utf8');
const html = read('index.html');
const source = (name) => {
  const match = html.match(new RegExp(`function ${name}\\([^)]*\\)\\{([\\s\\S]*?)\\n\\}`));
  assert(match, `Missing dashboard function: ${name}`);
  return match[0];
};
const keys = html.match(/const ALL_TAB_KEYS=([^;]+);/)[1];
const defaults = html.match(/const DEFAULT_ALLOWED_TABS=([^;]+);/)[1];
const button = html.match(/<button\b[^>]*\bid="tabCompetitorAnalysisBtn"[^>]*>[\s\S]*?<\/button>/)[0];
assert(button.includes('class="hidden"'));
assert(button.includes("window.location.href='/competitor-analysis'"));
assert(read('js/tabs/data.js').includes("{ key: 'competitor-analysis', label: '竞品分析工作台' }"));

for (const [name, user, visible] of [
  ['administrator', {role: 'admin', allowed_tabs: []}, true],
  ['granted', {role: 'user', allowed_tabs: ['mine', 'competitor-analysis']}, true],
  ['denied', {role: 'user', allowed_tabs: ['mine']}, false],
  ['empty permissions', {role: 'user', allowed_tabs: []}, false],
  ['default permissions', {role: 'user'}, false],
]) {
  const classes = new Set(['hidden']);
  const button = {classList: {toggle(key, force) {if (force) classes.add(key); else classes.delete(key);}}};
  const context = {
    currentUser: user,
    document: {getElementById(id) {return id === 'tabCompetitorAnalysisBtn' ? button : null;}},
    canAccessWorkbench: () => true, applyWorkbenchSubtabVisibility: () => {},
  };
  vm.createContext(context);
  vm.runInContext(`const ALL_TAB_KEYS=${keys}; const DEFAULT_ALLOWED_TABS=${defaults};\n` +
    ['getAllowedTabs', 'canAccessRawTab', 'canAccessTab', 'applyTabVisibility'].map(source).join('\n') +
    '\napplyTabVisibility();', context);
  assert.equal(!classes.has('hidden'), visible, `Competitor navigation: ${name}`);
}

async function testDirectAccess() {
  const adapterSource = read('competitor-analysis/platform-adapter.js');
  const page = read('competitor-analysis/index.html');
  const bootstrap = page.match(/async function bootstrap\(\)\{[\s\S]*?\n\}/)[0];
  for (const status of [401, 403]) {
    for (const operation of ['loadReport', 'saveReport']) {
      const redirects = [];
      const removed = [];
      const context = {
        window: {location: {replace: (url) => redirects.push(url)}},
        localStorage: {getItem: () => 'session-token', removeItem: (key) => removed.push(key)},
        fetch: async () => ({status, ok: false, json: async () => ({detail: 'Denied'})}),
      };
      vm.createContext(context);
      vm.runInContext(adapterSource, context);
      await assert.rejects(context.window.CompetitorPlatformAdapter[operation]({data: {}, baseVersion: 1}),
        (error) => error.status === status);
      assert.deepEqual(redirects, [status === 401 ? '/login' : '/dashboard']);
      assert.deepEqual(removed, status === 401 ? ['token'] : []);
    }
    let renders = 0;
    const context = {
      setSyncState: () => {}, platformMeta: {},
      DataService: {
        init: async () => ({connected: true, user: {}}),
        load: async () => {throw Object.assign(new Error('Denied'), {status});},
      },
      render: () => renders++,
      filters: {}, state: {softwares: []}, updateUndoRedo: () => {},
      setInterval: () => {assert.fail('Denied access must not start report polling');},
    };
    vm.createContext(context);
    vm.runInContext(bootstrap, context);
    await context.bootstrap();
    assert.equal(renders, 0, `Denied access (${status}) must not render the fallback report`);
  }
}

testDirectAccess().then(() => console.log('Competitor permissions: navigation, direct access and fallback checks passed.'))
  .catch((error) => {console.error(error); process.exitCode = 1;});
