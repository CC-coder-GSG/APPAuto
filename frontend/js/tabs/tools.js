import './database-compare.js?v=20261027-files-1';

// Modules keep their own state while the tools page provides a common entry.
async function activate() {
  await window.OmniQADatabaseCompareTab.activate();
}

window.OmniQAToolsTab = {activate};
