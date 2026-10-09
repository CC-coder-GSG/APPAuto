const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
const source = (name) => {
  const match = html.match(new RegExp(`function ${name}\\([^)]*\\)\\{([\\s\\S]*?)\\n\\}`));
  assert(match, `Missing dashboard function: ${name}`);
  return match[0];
};
const button = html.match(/<button\b[^>]*\bid="tabBuglyBtn"[^>]*>([\s\S]*?)<\/button>/);
assert(button, 'Bugly entry must exist in the dashboard');
assert(button[0].includes("window.location.href='/bugly'"));

for (const [name, user, visible] of [
  ['administrator', {role:'admin',allowed_tabs:[]}, true],
  ['authorized colleague', {role:'user',allowed_tabs:['mine','bugly']}, true],
  ['unauthorized colleague', {role:'user',allowed_tabs:['mine']}, false],
  ['explicit empty permissions', {role:'user',allowed_tabs:[]}, false],
]) {
  const classes = new Set(['hidden']);
  const bugly = {classList:{toggle(key, force){if(force)classes.add(key);else classes.delete(key);}}};
  const context = {
    currentUser:user, ALL_TAB_KEYS:['mine','bugly'], DEFAULT_ALLOWED_TABS:['mine'],
    document:{getElementById(id){return id === 'tabBuglyBtn' ? bugly : null;}},
    canAccessWorkbench:()=>true, applyWorkbenchSubtabVisibility:()=>{},
  };
  vm.createContext(context);
  vm.runInContext(['getAllowedTabs','canAccessRawTab','canAccessTab','applyTabVisibility'].map(source).join('\n')+'\napplyTabVisibility();', context);
  assert.equal(!classes.has('hidden'), visible, `Bugly navigation visibility for ${name}`);
}
console.log('Bugly dashboard navigation: administrator, granted, denied and empty permissions passed.');
