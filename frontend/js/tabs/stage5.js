import {
  loadOverallTest,
  pushOverallTest,
  syncOverallTestFromZentao,
} from './overall-test.js';

export * from './overall-test.js';
export {
  loadOverallTest as loadStage5,
  pushOverallTest as pushStage5,
  syncOverallTestFromZentao as syncStage5FromZentao,
};

window.OmniQAStage5Tab = window.OmniQAOverallTestTab;
window.loadStage5 = loadOverallTest;
window.pushStage5 = pushOverallTest;
window.syncStage5FromZentao = syncOverallTestFromZentao;
