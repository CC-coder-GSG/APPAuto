const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
const source = name => {
  const match = html.match(new RegExp(`(?:async )?function ${name}\\([^)]*\\)\\{([\\s\\S]*?)\\n\\}`));
  assert(match, `Missing dashboard function: ${name}`);
  return match[0];
};
const keys = html.match(/const ALL_TAB_KEYS=([^;]+);/)[1];
const defaults = html.match(/const DEFAULT_ALLOWED_TABS=([^;]+);/)[1];

(async () => {
  for (const [role, allowed, visible] of [
    ['admin', [], true], ['user', ['database-compare'], true],
    ['user', ['mine'], false], ['user', [], false],
  ]) {
    const classes = new Set(['hidden']);
    const toolsButton = {classList:{toggle(key, value) {value ? classes.add(key) : classes.delete(key);}}};
    const sections = new Map(['tools', 'mine', 'feedback'].map(key => [key, {
      classList:{hidden:true, add(){this.hidden = true;}, remove(){this.hidden = false;}},
    }]));
    let activated = 0;
    const context = {
      currentUser:{role, allowed_tabs:allowed},
      document:{getElementById(id) {
        if (id === 'tabToolsBtn') return toolsButton;
        return typeof id === 'string' ? sections.get(id.replace(/^tab-/, '')) || null : null;
      }},
      window:{OmniQAMineTab:{}, OmniQAToolsTab:{activate:async () => {activated++;}}},
      canAccessWorkbench:() => true, applyWorkbenchSubtabVisibility:() => {},
      mountWorkbenchSubpages:() => {}, getResolvedWorkbenchSubtab:() => 'demand',
      getDefaultWorkbenchSubtab:() => 'demand', switchWorkbenchSubtab:async () => {},
    };
    vm.createContext(context);
    vm.runInContext(`const ALL_TAB_KEYS=${keys}; const DEFAULT_ALLOWED_TABS=${defaults};\n` +
      ['getAllowedTabs', 'canAccessRawTab', 'canAccessTab', 'applyTabVisibility',
        'getDefaultVisibleTab', 'getTabNamespaceName', 'isTabRuntimeReady',
        'getFirstRuntimeReadyTab', 'showTab'].map(source).join('\n'), context);
    context.applyTabVisibility();
    assert.equal(!classes.has('hidden'), visible);
    assert.equal(context.canAccessTab('tools'), visible);
    if (!visible) continue;
    await context.showTab('tools');
    assert.equal(sections.get('tools').classList.hidden, false);
    assert.equal(activated, 1);
    await context.showTab('database-compare');
    assert.equal(activated, 2, 'Legacy entry still opens tools');
    await context.showTab('mine');
    assert.equal(sections.get('tools').classList.hidden, true);
  }
  console.log('Tools navigation and legacy permissions passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
