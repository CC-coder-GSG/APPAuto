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

function initChart(key, elementId) {
  if (typeof echarts === 'undefined') {
    console.error('ECharts failed to load');
    return null;
  }
  const element = document.getElementById(elementId);
  if (!element) return null;
  if (!state.charts[key]) {
    state.charts[key] = echarts.init(element);
  }
  return state.charts[key];
}

export function resizeAllCharts() {
  Object.values(state.charts).forEach((chart) => chart && chart.resize());
}

export function renderReportCharts(data, advancedData, helpers = {}) {
  const { sourceTypeZh = (v) => v, isAllUsersMode = false, fieldTestData = null } = helpers;

  // 先把会改变 .chart-grid 布局的显隐/整行类切换完，再渲染任何图表，
  // 否则后续 ECharts 会在旧布局/零尺寸下测量容器，导致同格其他图表概率性空白。
  const radarEl = document.getElementById('radarChart');
  const teamEl = document.getElementById('teamCompareChart');
  if (radarEl) radarEl.style.display = isAllUsersMode ? 'none' : '';
  if (teamEl) {
    teamEl.style.display = isAllUsersMode ? '' : 'none';
    teamEl.classList.toggle('full-row', isAllUsersMode);
  }

  const x = data.trend.map((i) => i.date);
  const yReq = data.trend.map((i) => i.executed_requirements);
  const yCase = data.trend.map((i) => i.created_cases);
  const yBug = data.trend.map((i) => i.created_bugs);
  const yRetest = data.trend.map((i) => i.retested_reqs || 0);
  const yClosed = data.trend.map((i) => i.closed_bugs || 0);
  const yCreatedFeedback = data.trend.map((i) => i.created_feedbacks || 0);
  const yProcessedFeedback = data.trend.map((i) => i.processed_feedbacks || 0);

  initChart('trendChart', 'trendChart')?.setOption({
    title: { text: '趋势折线图' },
    tooltip: { trigger: 'axis' },
    grid: { top: 60, bottom: 40, left: 50, right: 30 },
    legend: { data: ['执行需求', '创建用例', '创建Bug', '创建反馈', '处理反馈', '复测需求', '关闭Bug'], top: 30 },
    xAxis: { type: 'category', data: x },
    yAxis: { type: 'value' },
    series: [
      { name: '执行需求', type: 'line', data: yReq, smooth: true },
      { name: '创建用例', type: 'line', data: yCase, smooth: true },
      { name: '创建Bug', type: 'line', data: yBug, smooth: true },
      { name: '创建反馈', type: 'line', data: yCreatedFeedback, smooth: true },
      { name: '处理反馈', type: 'line', data: yProcessedFeedback, smooth: true },
      { name: '复测需求', type: 'line', data: yRetest, smooth: true },
      { name: '关闭Bug', type: 'line', data: yClosed, smooth: true },
    ],
  });

  initChart('sourcePieChart', 'sourcePieChart')?.setOption({
    title: { text: 'Bug来源分布', left: 'center' },
    tooltip: { trigger: 'item' },
    legend: { top: 'bottom' },
    series: [{ type: 'pie', radius: '50%', center: ['50%', '55%'], data: (data.bug_source_dist || []).map((i) => ({ name: sourceTypeZh(i.source_type), value: i.count })) }],
  });

  if (!isAllUsersMode) {
    const radar = initChart('radarChart', 'radarChart');
    radar?.setOption({
      title: { text: '个人能力雷达图' },
      tooltip: {},
      radar: {
        radius: '60%',
        center: ['50%', '55%'],
        indicator: [
          { name: '执行需求', max: Math.max(10, data.overview.executed_requirements || 0) },
          { name: '创建用例', max: Math.max(10, data.overview.created_cases || 0) },
          { name: '创建Bug', max: Math.max(10, data.overview.created_bugs || 0) },
          { name: '处理反馈', max: Math.max(10, data.overview.processed_feedbacks || 0) },
          { name: '复测需求', max: Math.max(10, data.overview.retested_reqs || 0) },
          { name: '关闭Bug', max: Math.max(10, data.overview.closed_bugs || 0) },
        ],
      },
      series: [{ type: 'radar', data: [{ value: [data.overview.executed_requirements || 0, data.overview.created_cases || 0, data.overview.created_bugs || 0, data.overview.processed_feedbacks || 0, data.overview.retested_reqs || 0, data.overview.closed_bugs || 0], name: '指标' }] }],
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
      title: { text: '团队对比（全员模式）' },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { type: 'scroll', data: metricDefs.map(([n]) => n), top: 28 },
      series: baseSeries,
    };

    let opt;
    if (horizontal) {
      const endPct = Math.min(100, (visiblePeople / count) * 100);
      opt = {
        ...common,
        grid: { top: 64, bottom: 40, left: 90, right: 56 },
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
        grid: { top: 64, bottom: rotate ? 70 : 44, left: 50, right: 30 },
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
    title: { text: '需求质量“刺客”排行榜 (Top 7)', textStyle: { fontSize: 15 } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 100, right: 40, bottom: 20 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: advancedData.top_reqs.map((r) => r.req_id).reverse(), axisLabel: { interval: 0, formatter: (val) => val.substring(0, 8) } },
    series: [{ name: 'Bug数量', type: 'bar', data: advancedData.top_reqs.map((r) => r.count).reverse(), label: { show: true, position: 'right' }, itemStyle: { color: '#ef4444', borderRadius: [0, 4, 4, 0] } }],
  });

  // 漏测率分母=需求/用例创建的 bug，MANUAL 不计入（多为禅道整体测试场景）
  const leakageCase = Number(advancedData.leakage.case ?? 0);
  const leakageReq = Number(advancedData.leakage.requirement ?? 0);
  const leakageManual = Number(advancedData.leakage.manual ?? 0);
  const leakageNormal = Number(advancedData.leakage.normal ?? (leakageCase + leakageReq));
  const leakageRetest = Number(advancedData.leakage.retest || 0);
  initChart('leakageChart', 'leakageChart')?.setOption({
    title: { text: '交叉复测漏测率', left: 'center', textStyle: { fontSize: 15 } },
    tooltip: { trigger: 'item', formatter: '{b}: {c}个 ({d}%)' },
    legend: { top: 'bottom' },
    series: [{
      type: 'pie', radius: ['40%', '65%'], center: ['50%', '50%'],
      data: [
        { name: '原测发现 (正常)', value: leakageNormal, itemStyle: { color: '#3b82f6' } },
        { name: '复测新增 (漏测)', value: leakageRetest, itemStyle: { color: '#f97316' } },
        { name: '整体测试 (手工)', value: leakageManual, itemStyle: { color: '#94a3b8' } },
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
    title: {
      text: '缺陷闭环转化漏斗',
      subtext: '发现 → 开发已解决 → 禅道已关闭 → 本地验收',
      left: 'center',
      textStyle: { fontSize: 15 },
      subtextStyle: { fontSize: 11, color: '#94a3b8' },
    },
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    series: [{
      type: 'funnel', left: '10%', top: 60, bottom: 20, width: '80%',
      min: 0, max: Math.max(funnelTotal, 1), minSize: '10%', maxSize: '100%', sort: 'descending', gap: 2,
      label: { show: true, position: 'inside', formatter: '{b}: {c}' },
      itemStyle: { borderColor: '#fff', borderWidth: 1 },
      data: [
        { name: '发现Bug总数', value: funnelTotal, itemStyle: { color: '#ef4444' } },
        { name: '开发已解决', value: funnelResolved, itemStyle: { color: '#f97316' } },
        { name: '禅道已关闭', value: funnelClosed, itemStyle: { color: '#eab308' } },
        { name: '本地验收闭环', value: funnelVerified, itemStyle: { color: '#22c55e' } },
      ],
    }],
  });

  const execMap = {
    passed: '✅通过',
    failed: '❌失败',
    blocked: '⛔阻塞',
    partial: '🟨部分完成',
    untested: '⏳未测',
  };
  initChart('execChart', 'execChart')?.setOption({
    title: { text: '用例执行结果分布', left: 'center', textStyle: { fontSize: 15 } },
    tooltip: { trigger: 'item', formatter: '{b}: {c}个 ({d}%)' },
    legend: { top: 'bottom' },
    series: [{
      type: 'pie',
      radius: '60%',
      center: ['50%', '50%'],
      data: advancedData.executions.map((e) => {
        const raw = String(e.status || '').trim();
        const key = raw.includes('.') ? raw.split('.').pop().toLowerCase() : raw.toLowerCase();
        return { name: execMap[key] || raw, value: e.count };
      }),
    }],
  });

  const ft = fieldTestData || { overview: {}, by_day: [], by_purpose: [], by_user: [], mode: 'personal' };
  const daySeries = (ft.by_day || []).map((x) => x.minutes || 0);
  const hasDayData = daySeries.some((v) => v > 0);
  initChart('fieldTestTrendChart', 'fieldTestTrendChart')?.setOption({
    title: { text: '外业测试时长趋势（分钟）', textStyle: { fontSize: 15 } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: (ft.by_day || []).map((x) => x.date) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{ type: 'line', smooth: true, data: daySeries, lineStyle: { color: '#0ea5e9' }, itemStyle: { color: '#0284c7' } }],
    graphic: hasDayData ? [] : [{
      type: 'text',
      left: 'center',
      top: 'middle',
      style: { text: '当前筛选条件下暂无外业测试时长数据', fill: '#94a3b8', fontSize: 13 },
    }],
  }, { replaceMerge: ['graphic'] });

  const purposeMap = { requirement: '需求测试', feature: '功能测试' };
  const purposeRows = ft.by_purpose || [];
  const hasPurpose = purposeRows.length > 0;
  initChart('fieldTestPurposeChart', 'fieldTestPurposeChart')?.setOption({
    title: { text: '外业测试目的分布（时长）', textStyle: { fontSize: 15 } },
    tooltip: { trigger: 'item' },
    legend: { top: 'bottom' },
    series: [{
      type: 'pie',
      radius: '58%',
      center: ['50%', '50%'],
      data: purposeRows.map((x) => ({ name: purposeMap[x.purpose_type] || x.purpose_type, value: x.minutes || 0 })),
    }],
    graphic: hasPurpose ? [] : [{
      type: 'text',
      left: 'center',
      top: 'middle',
      style: { text: '暂无外业测试目的数据', fill: '#94a3b8', fontSize: 13 },
    }],
  }, { replaceMerge: ['graphic'] });

  const userChartEl = document.getElementById('fieldTestUserChart');
  if (userChartEl) userChartEl.style.display = isAllUsersMode ? '' : 'none';
  if (isAllUsersMode) {
    const userRows = (ft.by_user || []).slice().sort((a, b) => (b.minutes || 0) - (a.minutes || 0));
    const hasUserRows = userRows.length > 0;
    initChart('fieldTestUserChart', 'fieldTestUserChart')?.setOption({
      title: { text: '人员外业测试时长对比（分钟）', textStyle: { fontSize: 15 } },
      tooltip: { trigger: 'axis' },
      grid: { top: 56, bottom: 40, left: 50, right: 20 },
      xAxis: { type: 'category', data: userRows.map((x) => x.username) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{ type: 'bar', data: userRows.map((x) => x.minutes || 0), itemStyle: { color: '#06b6d4', borderRadius: [4, 4, 0, 0] } }],
      graphic: hasUserRows ? [] : [{
        type: 'text',
        left: 'center',
        top: 'middle',
        style: { text: '暂无人员外业测试数据', fill: '#94a3b8', fontSize: 13 },
      }],
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
      axisLabel: { fontSize: 11, color: '#64748b', width: 220, overflow: 'truncate' },
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
      barMaxWidth: 22,
      itemStyle: { color: '#3b82f6', borderRadius: [0, 4, 4, 0] },
      data: counts,
      label: { show: has, position: 'right', color: '#1e293b', fontWeight: 'bold' },
    }],
    graphic: has ? [] : [{
      type: 'text',
      left: 'center',
      top: 'middle',
      style: { text: '该大版本暂无检出 Bug', fill: '#94a3b8', fontSize: 14, fontWeight: 600 },
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
      title: { text: '需求状态停留时长（平均天）', textStyle: { fontSize: 15 } },
      tooltip: { trigger: 'axis' },
      legend: { data: ['平均停留天数', '最大停留天数'], top: 28 },
      grid: { top: 60, bottom: 40, left: 50, right: 30 },
      xAxis: { type: 'category', data: reqStay.map((i) => reqStatusZh(i.status)) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        {
          name: '平均停留天数',
          type: 'bar',
          data: reqStay.map((i) => i.avg_days),
          itemStyle: { color: '#2563eb', borderRadius: [4, 4, 0, 0] },
          label: { show: true, position: 'top' },
        },
        {
          name: '最大停留天数',
          type: 'line',
          data: reqStay.map((i) => i.max_days),
          smooth: true,
          lineStyle: { color: '#f97316' },
          itemStyle: { color: '#f97316' },
        },
      ],
    });
  } else {
    initChart('governReqAgingChart', 'governReqAgingChart')?.setOption({
      title: { text: '需求关闭耗时分布（降级）', textStyle: { fontSize: 15 } },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: reqAging.map((i) => i.bucket) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{
        type: 'bar',
        data: reqAging.map((i) => i.count),
        itemStyle: { color: '#2563eb', borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top' },
      }],
    });
  }

  initChart('governBugAgingChart', 'governBugAgingChart')?.setOption({
    title: { text: 'Bug关闭耗时分布', textStyle: { fontSize: 15 } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: bugAging.map((i) => i.bucket) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'bar',
      data: bugAging.map((i) => i.count),
      itemStyle: { color: '#16a34a', borderRadius: [4, 4, 0, 0] },
      label: { show: true, position: 'top' },
    }],
  });
}

window.OmniQACharts = { renderReportCharts, renderGovernanceCharts, resizeAllCharts };
