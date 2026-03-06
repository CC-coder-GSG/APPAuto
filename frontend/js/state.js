export const state = {
  currentUser: null,
  users: [],
  versions: [],
  assignReqs: [],
  reportLoaded: false,
  dataOverviewCache: null,
  dataViewState: { showUsers: true, showVersions: true },
  currentMineData: [],
  currentDispatchHtml: '',
  currentRetestData: [],
  stage5Rows: [],
  globalS5Options: { reqs: [], cases: [], bugs: [] },
  pendingImportReqs: [],
  currentDispatchBugId: null,
  charts: {
    trendChart: null,
    sourcePieChart: null,
    radarChart: null,
    teamCompareChart: null,
    versionBugChart: null,
    topReqChart: null,
    leakageChart: null,
    funnelChart: null,
    execChart: null,
  },
};

window.OmniQAState = state;
