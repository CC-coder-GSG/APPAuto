import { state } from '../state.js';

function reqStatusZh(v) {
  const raw = String(v || '').trim();
  const key = raw.includes('.') ? raw.split('.').pop().toLowerCase() : raw.toLowerCase();
  const m = {
    pending: '待开始',
    assigned: '已分配',
    case_done: '用例完成',
    testing: '测试中',
    test_done: '测试完成',
    retest_pending: '待复测',
    retest_done: '复测完成',
  };
  return m[key] || raw;
}

// 报表图表统一主题（2026-11-27 报表中心 v2）：分类色按固定顺序取，文字/坐标轴走 Weave 墨色，
// 标题改由卡片 HTML 承担，图表内不再画标题。
const INK = '#1c1917';
const INK_SOFT = '#57534e';
const INK_FAINT = '#a8a29e';
const HAIR = 'rgba(28, 25, 23, 0.09)';
export const SERIES_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'];
let themeRegistered = false;
function ensureTheme() {
  if (themeRegistered || typeof echarts === 'undefined') return;
  const axis = {
    axisLine: { lineStyle: { color: HAIR } },
    axisTick: { show: false },
    axisLabel: { color: INK_SOFT, fontSize: 11 },
    splitLine: { lineStyle: { color: HAIR, type: [3, 3] } },
    nameTextStyle: { color: INK_FAINT },
  };
  echarts.registerTheme('omniqa', {
    color: SERIES_COLORS,
    backgroundColor: 'transparent',
    textStyle: { fontFamily: 'inherit', color: INK_SOFT },
    title: { textStyle: { color: INK, fontSize: 14, fontWeight: 650 }, subtextStyle: { color: INK_FAINT } },
    legend: { textStyle: { color: INK_SOFT, fontSize: 12 }, icon: 'roundRect', itemWidth: 12, itemHeight: 8, itemGap: 14, pageIconSize: 10 },
    tooltip: {
      backgroundColor: 'rgba(255, 255, 255, 0.98)', borderColor: HAIR, borderWidth: 1, padding: [8, 10],
      textStyle: { color: INK, fontSize: 12 }, extraCssText: 'border-radius:10px; box-shadow:0 8px 24px rgba(28,25,23,.12);',
    },
    categoryAxis: axis,
    valueAxis: { ...axis, axisLine: { show: false } },
    line: { symbol: 'circle', symbolSize: 6, lineStyle: { width: 2 } },
    bar: { barMaxWidth: 22, itemStyle: { borderRadius: [4, 4, 0, 0] } },
    pie: { itemStyle: { borderColor: '#fff', borderWidth: 2 } },
    funnel: { itemStyle: { borderColor: '#fff', borderWidth: 2 } },
    radar: { axisName: { color: INK_SOFT }, splitLine: { lineStyle: { color: HAIR } }, splitArea: { show: false }, axisLine: { lineStyle: { color: HAIR } } },
  });
  themeRegistered = true;
}

// 图表放在 .rpt-card 卡片里：显隐要作用在整张卡片上，否则会留下空卡片头。
function setCardVisible(el, visible) {
  if (!el) return;
  const card = el.closest('.rpt-card') || el;
  card.style.display = visible ? '' : 'none';
}

function initChart(key, elementId) {
  if (typeof echarts === 'undefined') {
    console.error('ECharts failed to load');
    return null;
  }
  const element = document.getElementById(elementId);
  if (!element) return null;
  ensureTheme();
  if (!state.charts[key]) {
    state.charts[key] = echarts.init(element, 'omniqa');
  }
  return state.charts[key];
}

const EMPTY_TEXT = (text) => [{ type: 'text', left: 'center', top: 'middle', style: { text, fill: INK_FAINT, fontSize: 13 } }];

export function resizeAllCharts() {
  Object.values(state.charts).forEach((chart) => chart && chart.resize());
}

// Bug 分布饼图的双视图数据缓存：major=按大版本，source=按来源；zh 为来源类型中文化函数
let bugDistState = { source: [], major: [], zh: (v) => v };

function bindBugDistModeSelect() {
  const sel = document.getElementById('bugDistModeSelect');
  if (!sel || sel.dataset.bound) return;
  sel.dataset.bound = '1';
  sel.addEventListener('change', () => renderBugDistChart());
}

export function renderBugDistChart() {
  const sel = document.getElementById('bugDistModeSelect');
  const isSource = !!sel && sel.value === 'source';
  const rows = isSource ? bugDistState.source : bugDistState.major;
  const pieData = isSource
    ? rows.map((i) => ({ name: bugDistState.zh(i.source_type), value: i.count }))
    : rows.map((i) => ({ name: i.major_version_no || '未关联大版本', value: i.count }));
  initChart('sourcePieChart', 'sourcePieChart')?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c}个 ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    // minShowLabelAngle：大版本很多时小切片不出标签（避免糊成一团），靠 tooltip/图例查看
    series: [{
      type: 'pie', radius: ['42%', '66%'], center: ['50%', '46%'], showEmptyCircle: false, minShowLabelAngle: 10, data: pieData,
      label: { color: INK_SOFT, formatter: '{b}\n{c} 个' }, labelLine: { length: 8, length2: 8 },
    }],
    graphic: pieData.length ? [] : EMPTY_TEXT('当前筛选条件下暂无 Bug 数据'),
  }, { replaceMerge: ['graphic', 'series'] });
}

export function renderReportCharts(data, advancedData, helpers = {}) {
  const { sourceTypeZh = (v) => v, isAllUsersMode = false, fieldTestData = null } = helpers;

  // 先把会改变 .chart-grid 布局的显隐/整行类切换完，再渲染任何图表，
  // 否则后续 ECharts 会在旧布局/零尺寸下测量容器，导致同格其他图表概率性空白。
  const radarEl = document.getElementById('radarChart');
  const teamEl = document.getElementById('teamCompareChart');
  setCardVisible(radarEl, !isAllUsersMode);
  setCardVisible(teamEl, isAllUsersMode);
  const fieldUserEl = document.getElementById('fieldTestUserChart');
  setCardVisible(fieldUserEl, isAllUsersMode);
  fieldUserEl?.closest('.rpt-grid')?.classList.toggle('rpt-grid--two-up', !isAllUsersMode);
  document.querySelector('#tab-report .rpt-grid--main')?.classList.toggle('is-team', isAllUsersMode);

  const x = data.trend.map((i) => i.date);
  const yReq = data.trend.map((i) => i.executed_requirements);
  const yCase = data.trend.map((i) => i.created_cases);
  const yBug = data.trend.map((i) => i.created_bugs);
  const yRetest = data.trend.map((i) => i.retested_reqs || 0);
  const yClosed = data.trend.map((i) => i.closed_bugs || 0);
  const yCreatedFeedback = data.trend.map((i) => i.created_feedbacks || 0);
  const yProcessedFeedback = data.trend.map((i) => i.processed_feedbacks || 0);

  initChart('trendChart', 'trendChart')?.setOption({
    tooltip: { trigger: 'axis' },
    grid: { top: 40, bottom: 8, left: 8, right: 16, containLabel: true },
    legend: { type: 'scroll', data: ['执行需求', '创建用例', '创建Bug', '创建反馈', '处理反馈', '复测需求', '关闭Bug'], top: 0, left: 0 },
    xAxis: { type: 'category', data: x, boundaryGap: false },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      { name: '执行需求', type: 'line', data: yReq, smooth: 0.25, smoothMonotone: 'x', showSymbol: false },
      { name: '创建用例', type: 'line', data: yCase, smooth: 0.25, smoothMonotone: 'x', showSymbol: false },
      { name: '创建Bug', type: 'line', data: yBug, smooth: 0.25, smoothMonotone: 'x', showSymbol: false },
      { name: '创建反馈', type: 'line', data: yCreatedFeedback, smooth: 0.25, smoothMonotone: 'x', showSymbol: false },
      { name: '处理反馈', type: 'line', data: yProcessedFeedback, smooth: 0.25, smoothMonotone: 'x', showSymbol: false },
      { name: '复测需求', type: 'line', data: yRetest, smooth: 0.25, smoothMonotone: 'x', showSymbol: false },
      { name: '关闭Bug', type: 'line', data: yClosed, smooth: 0.25, smoothMonotone: 'x', showSymbol: false },
    ],
  });

  // Bug 分布饼图支持双视图（大版本/来源），数据缓存在模块级，切换下拉即时重渲染
  bugDistState = {
    source: data.bug_source_dist || [],
    major: data.bug_major_dist || [],
    zh: sourceTypeZh,
  };
  bindBugDistModeSelect();
  renderBugDistChart();

  if (!isAllUsersMode) {
    const radar = initChart('radarChart', 'radarChart');
    radar?.setOption({
      tooltip: {},
      radar: {
        radius: '64%',
        center: ['50%', '52%'],
        indicator: [
          { name: '执行需求', max: Math.max(10, data.overview.executed_requirements || 0) },
          { name: '创建用例', max: Math.max(10, data.overview.created_cases || 0) },
          { name: '创建Bug', max: Math.max(10, data.overview.created_bugs || 0) },
          { name: '处理反馈', max: Math.max(10, data.overview.processed_feedbacks || 0) },
          { name: '复测需求', max: Math.max(10, data.overview.retested_reqs || 0) },
          { name: '关闭Bug', max: Math.max(10, data.overview.closed_bugs || 0) },
        ],
      },
      series: [{ type: 'radar', symbolSize: 6, areaStyle: { opacity: 0.14 }, data: [{ value: [data.overview.executed_requirements || 0, data.overview.created_cases || 0, data.overview.created_bugs || 0, data.overview.processed_feedbacks || 0, data.overview.retested_reqs || 0, data.overview.closed_bugs || 0], name: '指标' }] }],
    });
    radar?.resize();
  }

  const team = data.team_comparison || [];
  if (isAllUsersMode) {
    const teamChart = initChart('teamCompareChart', 'teamCompareChart');
    const names = team.map((i) => i.username);
    const metricDefs = [
      ['执行需求', 'executed_requirements'],
      ['创建用例', 'created_cases'],
      ['创建Bug', 'created_bugs'],
      ['处理反馈', 'processed_feedbacks'],
      ['复测需求', 'retested_reqs'],
      ['关闭Bug', 'closed_bugs'],
    ];
    // 智能形态：人数多时转横向条形（人名不挤），并用 dataZoom 滚动浏览
    const count = team.length;
    const horizontal = count > 6;
    // 横向时每人需要约 78px 纵向空间放下 6 组柱；超过可视范围就靠 dataZoom 滚动
    const visiblePeople = horizontal ? Math.max(1, Math.min(count, 6)) : count;
    const baseSeries = metricDefs.map(([name, key]) => ({
      name,
      type: 'bar',
      data: team.map((i) => i[key] || 0),
    }));

    const common = {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { type: 'scroll', data: metricDefs.map(([n]) => n), top: 0, left: 0 },
      series: baseSeries,
    };

    let opt;
    if (horizontal) {
      const endPct = Math.min(100, (visiblePeople / count) * 100);
      opt = {
        ...common,
        grid: { top: 36, bottom: 8, left: 8, right: 48, containLabel: true },
        xAxis: { type: 'value' },
        // inverse 让第一个人显示在顶部，符合阅读习惯
        yAxis: { type: 'category', data: names, inverse: true, axisLabel: { interval: 0, fontSize: 12 } },
        dataZoom: [
          { type: 'slider', yAxisIndex: 0, width: 14, right: 12, start: 0, end: endPct, zoomLock: false },
          { type: 'inside', yAxisIndex: 0, start: 0, end: endPct },
        ],
      };
    } else {
      const rotate = count > 4 ? 22 : 0;
      opt = {
        ...common,
        grid: { top: 36, bottom: 8, left: 8, right: 16, containLabel: true },
        xAxis: {
          type: 'category',
          data: names,
          axisLabel: { interval: 0, rotate, fontSize: 12 },
        },
        yAxis: { type: 'value' },
      };
    }
    // notMerge=true：横/纵形态切换时清掉旧坐标轴与 dataZoom，避免残留
    teamChart?.setOption(opt, true);
    teamChart?.resize();
  }

  renderVersionBugChart(helpers.versionBugs || []);

  initChart('topReqChart', 'topReqChart')?.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { top: 8, left: 8, right: 36, bottom: 8, containLabel: true },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: { type: 'category', data: advancedData.top_reqs.map((r) => r.req_id).reverse(), axisLabel: { interval: 0, formatter: (val) => val.substring(0, 8) } },
    series: [{ name: 'Bug数量', type: 'bar', barMaxWidth: 16, data: advancedData.top_reqs.map((r) => r.count).reverse(), label: { show: true, position: 'right', color: INK, fontWeight: 600 }, itemStyle: { color: SERIES_COLORS[0], borderRadius: [0, 4, 4, 0] } }],
    graphic: advancedData.top_reqs.length ? [] : EMPTY_TEXT('时段内暂无需求关联 Bug'),
  });

  // 漏测率分母=需求/用例创建的 bug，MANUAL 不计入（多为禅道整体测试场景）
  const leakageCase = Number(advancedData.leakage.case ?? 0);
  const leakageReq = Number(advancedData.leakage.requirement ?? 0);
  const leakageManual = Number(advancedData.leakage.manual ?? 0);
  const leakageNormal = Number(advancedData.leakage.normal ?? (leakageCase + leakageReq));
  const leakageRetest = Number(advancedData.leakage.retest || 0);
  initChart('leakageChart', 'leakageChart')?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c}个 ({d}%)' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie', radius: ['42%', '66%'], center: ['50%', '46%'],
      label: { color: INK_SOFT, formatter: '{d}%' },
      data: [
        { name: '原测发现（正常）', value: leakageNormal, itemStyle: { color: SERIES_COLORS[0] } },
        { name: '复测新增（漏测）', value: leakageRetest, itemStyle: { color: SERIES_COLORS[1] } },
        { name: '整体测试（手工）', value: leakageManual, itemStyle: { color: '#a8a29e' } },
      ],
    }],
  });

  // 4-level funnel: 发现 → 开发已解决 → 禅道已关闭 → 本地验收闭环
  // resolved = live_status ∈ {resolved,closed}, closed = live_status==closed,
  // verified = 本地 BugTracking.closed=True（Stage5 验收完成）
  const funnel = advancedData.funnel || {};
  const funnelTotal = Number(funnel.total || 0);
  const funnelResolved = Number(funnel.resolved ?? funnel.fixed ?? 0);
  const funnelClosed = Number(funnel.closed || 0);
  const funnelVerified = Number(funnel.verified || 0);
  initChart('funnelChart', 'funnelChart')?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    series: [{
      type: 'funnel', left: '10%', top: 12, bottom: 12, width: '80%',
      min: 0, max: Math.max(funnelTotal, 1), minSize: '10%', maxSize: '100%', sort: 'descending', gap: 2,
      label: { show: true, position: 'inside', formatter: '{b}  {c}', color: '#fff', fontWeight: 600 },
      // 同一色相由浅到深：越往下越接近闭环
      data: [
        { name: '发现 Bug', value: funnelTotal, itemStyle: { color: '#8fb6e8' } },
        { name: '开发已解决', value: funnelResolved, itemStyle: { color: '#5293e0' } },
        { name: '禅道已关闭', value: funnelClosed, itemStyle: { color: '#2a78d6' } },
        { name: '本地验收闭环', value: funnelVerified, itemStyle: { color: '#1a4f93' } },
      ],
    }],
  });

  const execMap = {
    passed: '通过',
    failed: '失败',
    blocked: '阻塞',
    partial: '部分完成',
    untested: '未测',
  };
  // 执行结果是状态：用状态色并带文字标签，不单靠颜色区分
  const execColor = { passed: '#16a34a', failed: '#dc2626', blocked: '#d97706', partial: '#eda100', untested: '#a8a29e' };
  initChart('execChart', 'execChart')?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c}个 ({d}%)' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['42%', '66%'],
      center: ['50%', '46%'],
      label: { color: INK_SOFT, formatter: '{b} {d}%' },
      data: advancedData.executions.map((e) => {
        const raw = String(e.status || '').trim();
        const key = raw.includes('.') ? raw.split('.').pop().toLowerCase() : raw.toLowerCase();
        return { name: execMap[key] || raw, value: e.count, itemStyle: execColor[key] ? { color: execColor[key] } : undefined };
      }),
    }],
    graphic: advancedData.executions.length ? [] : EMPTY_TEXT('时段内暂无执行记录'),
  });

  const ft = fieldTestData || { overview: {}, by_day: [], by_purpose: [], by_user: [], mode: 'personal' };
  const daySeries = (ft.by_day || []).map((x) => x.minutes || 0);
  const hasDayData = daySeries.some((v) => v > 0);
  initChart('fieldTestTrendChart', 'fieldTestTrendChart')?.setOption({
    tooltip: { trigger: 'axis' },
    grid: { top: 12, bottom: 8, left: 8, right: 16, containLabel: true },
    xAxis: { type: 'category', data: (ft.by_day || []).map((x) => x.date) },
    yAxis: { type: 'value', minInterval: 1 },
    // 按天的时长是离散值，用柱而不是平滑曲线，避免在两个 0 之间画出不存在的波峰
    series: [{ name: '时长（分钟）', type: 'bar', barMaxWidth: 14, data: daySeries, itemStyle: { color: SERIES_COLORS[2], borderRadius: [4, 4, 0, 0] } }],
    graphic: hasDayData ? [] : EMPTY_TEXT('当前筛选条件下暂无外业测试时长数据'),
  }, { replaceMerge: ['graphic'] });

  const purposeMap = { requirement: '需求测试', feature: '功能测试' };
  const purposeRows = ft.by_purpose || [];
  const hasPurpose = purposeRows.length > 0;
  initChart('fieldTestPurposeChart', 'fieldTestPurposeChart')?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} 分钟 ({d}%)' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['42%', '66%'],
      center: ['50%', '46%'],
      label: { color: INK_SOFT, formatter: '{d}%' },
      data: purposeRows.map((x) => ({ name: purposeMap[x.purpose_type] || x.purpose_type, value: x.minutes || 0 })),
    }],
    graphic: hasPurpose ? [] : EMPTY_TEXT('暂无外业测试目的数据'),
  }, { replaceMerge: ['graphic'] });

  if (isAllUsersMode) {
    const userRows = (ft.by_user || []).slice().sort((a, b) => (b.minutes || 0) - (a.minutes || 0));
    const hasUserRows = userRows.length > 0;
    initChart('fieldTestUserChart', 'fieldTestUserChart')?.setOption({
      tooltip: { trigger: 'axis' },
      grid: { top: 12, bottom: 8, left: 8, right: 16, containLabel: true },
      xAxis: { type: 'category', data: userRows.map((x) => x.username) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{ name: '时长（分钟）', type: 'bar', data: userRows.map((x) => x.minutes || 0), itemStyle: { color: SERIES_COLORS[2], borderRadius: [4, 4, 0, 0] } }],
      graphic: hasUserRows ? [] : EMPTY_TEXT('暂无人员外业测试数据'),
    }, { replaceMerge: ['graphic'] });
  }

  // 布局（含整行独占切换）reflow 完成后再统一 resize 一次，
  // 兜底修正任何在布局未稳定时初始化/渲染的图表（双 rAF 等浏览器完成排版）。
  requestAnimationFrame(() => requestAnimationFrame(() => resizeAllCharts()));
}

// 各发包(小版本) Bug 检出分布：横向条形 + 按 Bug 数降序 + dataZoom 滚动。
// 单独导出，供报表查询与「大版本」筛选下拉各自重渲染。
export function renderVersionBugChart(vbData) {
  const chart = initChart('versionBugChart', 'versionBugChart');
  if (!chart) return;
  // 后端已降序；前端再兜底排一次，保证「最多」永远在顶部
  const rows = (vbData || []).slice().sort((a, b) => (b.bug_count || 0) - (a.bug_count || 0));
  const has = rows.length > 0;
  // 纵轴只显示小版本号（短、可读）；大版本放进 tooltip，解决版本名挤一起
  const names = rows.map((r) => r.minor_name || r.version_name || '-');
  const counts = rows.map((r) => r.bug_count || 0);
  // 每条约 26px；超出可视区靠 dataZoom 滚动，默认展示最多的约 14 条
  const visible = Math.max(1, Math.min(rows.length, 14));
  const endPct = rows.length ? Math.min(100, (visible / rows.length) * 100) : 100;

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const p = ps[0];
        const r = rows[p.dataIndex] || {};
        const major = r.major_name ? `${r.major_name} / ` : '';
        return `${major}${r.minor_name || p.name}<br/>检出 Bug 数：<b>${p.value}</b>`;
      },
    },
    grid: { top: 16, bottom: 28, left: 12, right: 64, containLabel: true },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: {
      type: 'category',
      data: names,
      inverse: true, // 配合降序：检出最多的发包在最上方
      axisLabel: { fontSize: 11, width: 220, overflow: 'truncate' },
    },
    dataZoom: rows.length > visible
      ? [
        { type: 'slider', yAxisIndex: 0, width: 14, right: 14, start: 0, end: endPct },
        { type: 'inside', yAxisIndex: 0, start: 0, end: endPct },
      ]
      : [],
    series: [{
      name: '检出 Bug 数',
      type: 'bar',
      barMaxWidth: 16,
      itemStyle: { color: SERIES_COLORS[0], borderRadius: [0, 4, 4, 0] },
      data: counts,
      label: { show: has, position: 'right', color: INK, fontWeight: 600 },
    }],
    graphic: has ? [] : [{
      type: 'text',
      left: 'center',
      top: 'middle',
      style: { text: '该大版本暂无检出 Bug', fill: INK_FAINT, fontSize: 13 },
    }],
  }, { replaceMerge: ['graphic', 'dataZoom'] });
  chart.resize();
}

export function renderGovernanceCharts(governanceData) {
  if (!governanceData) return;
  const reqAging = governanceData?.requirements?.close_aging?.bands || [];
  const reqStay = governanceData?.requirements?.status_stay_distribution || [];
  const bugAging = governanceData?.bugs?.close_aging?.bands || [];

  if (reqStay.length > 0) {
    initChart('governReqAgingChart', 'governReqAgingChart')?.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['平均停留天数', '最大停留天数'], top: 0, left: 0 },
      grid: { top: 36, bottom: 8, left: 8, right: 16, containLabel: true },
      xAxis: { type: 'category', data: reqStay.map((i) => reqStatusZh(i.status)) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        {
          name: '平均停留天数',
          type: 'bar',
          data: reqStay.map((i) => i.avg_days),
          itemStyle: { color: SERIES_COLORS[0], borderRadius: [4, 4, 0, 0] },
          label: { show: true, position: 'top', color: INK_SOFT },
        },
        {
          name: '最大停留天数',
          type: 'line',
          data: reqStay.map((i) => i.max_days),
          itemStyle: { color: SERIES_COLORS[1] },
        },
      ],
    });
  } else {
    initChart('governReqAgingChart', 'governReqAgingChart')?.setOption({
      tooltip: { trigger: 'axis' },
      grid: { top: 16, bottom: 8, left: 8, right: 16, containLabel: true },
      xAxis: { type: 'category', data: reqAging.map((i) => i.bucket) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{
        type: 'bar',
        data: reqAging.map((i) => i.count),
        itemStyle: { color: SERIES_COLORS[0], borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', color: INK_SOFT },
      }],
    }, true);
  }

  initChart('governBugAgingChart', 'governBugAgingChart')?.setOption({
    tooltip: { trigger: 'axis' },
    grid: { top: 16, bottom: 8, left: 8, right: 16, containLabel: true },
    xAxis: { type: 'category', data: bugAging.map((i) => i.bucket) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'bar',
      data: bugAging.map((i) => i.count),
      itemStyle: { color: SERIES_COLORS[2], borderRadius: [4, 4, 0, 0] },
      label: { show: true, position: 'top', color: INK_SOFT },
    }],
  });
}

window.OmniQACharts = { renderReportCharts, renderGovernanceCharts, resizeAllCharts };
