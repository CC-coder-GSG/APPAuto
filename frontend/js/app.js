import './api.js';
import './auth.js';
import './state.js';
import './utils.js';
import './components/common.js';
import { resizeAllCharts } from './components/charts.js';
import './tabs/mine.js';
import './tabs/report.js';
import './tabs/retest.js';
import './tabs/stage5.js';
import './tabs/assign.js';
import './tabs/dispatch.js';
import './tabs/data.js';

window.addEventListener('resize', () => {
  resizeAllCharts();
});
