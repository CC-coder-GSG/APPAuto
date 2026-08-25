export const state = {
  currentUser: null,
  users: [],
  versions: [],
  assignReqs: [],
  assignProgressData: null,
  reportLoaded: false,
  dataOverviewCache: null,
  requirementLinkLogsCache: [],
  requirementLinkLogsLoaded: false,
  dataTreeExpandedMajors: {},
  dataViewState: { showUsers: true, showVersions: true },
  currentMineData: [],
  currentFeedbackTodoHtml: '',
  currentDispatchHtml: '',
  currentDispatchData: [],
  currentRetestData: [],
  overallTestRows: [],
  overallTestAllVersionsMode: false,
  overallTestPage: 1,
  overallTestPageSize: 20,
  overallTestOptions: { reqs: [], cases: [], bugs: [] },
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
    fieldTestTrendChart: null,
    fieldTestPurposeChart: null,
    fieldTestUserChart: null,
    governReqAgingChart: null,
    governBugAgingChart: null,
  },
};

window.OmniQAState = state;

export function setCurrentUser(user) {
  state.currentUser = user || null;
  window.currentUser = state.currentUser;
}

export function setUsers(users) {
  state.users = Array.isArray(users) ? users : [];
  window.users = state.users;
}

export function setVersions(versions) {
  state.versions = Array.isArray(versions) ? versions : [];
  window.versions = state.versions;
}

export function syncLegacyGlobalsToState() {
  if (window.currentUser && !state.currentUser) state.currentUser = window.currentUser;
  if (Array.isArray(window.users) && state.users.length === 0) state.users = window.users;
  if (Array.isArray(window.versions) && state.versions.length === 0) state.versions = window.versions;
}

window.OmniQAStateActions = {
  setCurrentUser,
  setUsers,
  setVersions,
  syncLegacyGlobalsToState,
};
