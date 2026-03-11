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
  const { sourceTypeZh = (v) => v, isAllUsersMode = false } = helpers;

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

  const radarEl = document.getElementById('radarChart');
  const teamEl = document.getElementById('teamCompareChart');
  if (radarEl) radarEl.style.display = isAllUsersMode ? 'none' : '';
  if (teamEl) teamEl.style.display = isAllUsersMode ? '' : 'none';

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
    teamChart?.setOption({
      title: { text: '团队对比（全员模式）' },
      tooltip: { trigger: 'axis' },
      grid: { top: 60, bottom: 40, left: 50, right: 30 },
      legend: { data: ['执行需求', '创建用例', '创建Bug', '处理反馈', '复测需求', '关闭Bug'], top: 30 },
      xAxis: { type: 'category', data: team.map((i) => i.username) },
      yAxis: { type: 'value' },
      series: [
        { name: '执行需求', type: 'bar', data: team.map((i) => i.executed_requirements || 0) },
        { name: '创建用例', type: 'bar', data: team.map((i) => i.created_cases || 0) },
        { name: '创建Bug', type: 'bar', data: team.map((i) => i.created_bugs || 0) },
        { name: '处理反馈', type: 'bar', data: team.map((i) => i.processed_feedbacks || 0) },
        { name: '复测需求', type: 'bar', data: team.map((i) => i.retested_reqs || 0) },
        { name: '关闭Bug', type: 'bar', data: team.map((i) => i.closed_bugs || 0) },
      ],
    });
    teamChart?.resize();
  }

  const vbData = helpers.versionBugs || [];
  const hasVersionBugData = vbData.length > 0;
  const versionBugChart = initChart('versionBugChart', 'versionBugChart');
  versionBugChart?.setOption({
    title: { text: '各发包(小版本) Bug 检出分布', textStyle: { fontSize: 15, color: '#334155' } },
    tooltip: { trigger: 'axis' },
    grid: { top: 60, bottom: '15%' },
    xAxis: { type: 'category', data: vbData.map((i) => i.version_name), axisLabel: { interval: 0, fontSize: 11, color: '#64748b' } },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      name: '检出 Bug 数', type: 'bar', barMaxWidth: 40,
      itemStyle: { color: '#3b82f6', borderRadius: [4, 4, 0, 0] },
      data: vbData.map((i) => i.bug_count),
      label: { show: hasVersionBugData, position: 'top', color: '#1e293b', fontWeight: 'bold' },
    }],
    graphic: hasVersionBugData
      ? []
      : [{
        type: 'text',
        left: 'center',
        top: 'middle',
        style: {
          text: '该大版本暂无检出 Bug',
          fill: '#94a3b8',
          fontSize: 14,
          fontWeight: 600,
        },
      }],
  }, { replaceMerge: ['graphic'] });

  initChart('topReqChart', 'topReqChart')?.setOption({
    title: { text: '需求质量“刺客”排行榜 (Top 7)', textStyle: { fontSize: 15 } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 100, right: 40, bottom: 20 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: advancedData.top_reqs.map((r) => r.req_id).reverse(), axisLabel: { interval: 0, formatter: (val) => val.substring(0, 8) } },
    series: [{ name: 'Bug数量', type: 'bar', data: advancedData.top_reqs.map((r) => r.count).reverse(), label: { show: true, position: 'right' }, itemStyle: { color: '#ef4444', borderRadius: [0, 4, 4, 0] } }],
  });

  initChart('leakageChart', 'leakageChart')?.setOption({
    title: { text: '交叉复测漏测率', left: 'center', textStyle: { fontSize: 15 } },
    tooltip: { trigger: 'item', formatter: '{b}: {c}个 ({d}%)' },
    legend: { top: 'bottom' },
    series: [{
      type: 'pie', radius: ['40%', '65%'], center: ['50%', '50%'],
      data: [
        { name: '原测发现 (正常)', value: advancedData.leakage.normal, itemStyle: { color: '#3b82f6' } },
        { name: '复测新增 (漏测)', value: advancedData.leakage.retest, itemStyle: { color: '#f97316' } },
      ],
    }],
  });

  initChart('funnelChart', 'funnelChart')?.setOption({
    title: { text: '缺陷闭环转化漏斗', left: 'center', textStyle: { fontSize: 15 } },
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    series: [{
      type: 'funnel', left: '10%', top: 50, bottom: 50, width: '80%', min: 0, max: Math.max(advancedData.funnel.total, 1), minSize: '10%', maxSize: '100%', sort: 'descending', gap: 2,
      label: { show: true, position: 'inside', formatter: '{b}: {c}' },
      itemStyle: { borderColor: '#fff', borderWidth: 1 },
      data: [
        { name: '发现Bug总数', value: advancedData.funnel.total, itemStyle: { color: '#ef4444' } },
        { name: '开发处理完毕', value: advancedData.funnel.fixed, itemStyle: { color: '#eab308' } },
        { name: '验证彻底闭环', value: advancedData.funnel.closed, itemStyle: { color: '#22c55e' } },
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
