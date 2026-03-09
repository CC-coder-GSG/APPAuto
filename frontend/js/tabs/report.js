import { api } from '../api.js';
import { state } from '../state.js';
import { renderReportCharts } from '../components/charts.js';
import { sourceTypeZh } from '../utils.js';

function getCurrentUser() {
  return state.currentUser || window.currentUser || null;
}

function isAllUsersMode() {
  const currentUser = getCurrentUser();
  return currentUser && currentUser.role === 'admin' && window.reportUserSelect && window.reportUserSelect.value === '0';
}

export async function queryReport() {
  const currentUser = getCurrentUser();
  if (!currentUser) {
    throw new Error('当前用户信息未加载，请刷新页面后重试');
  }

  const start_date = window.reportStartDate.value;
  const end_date = window.reportEndDate.value;
  const reportMajorSelect = document.getElementById('reportMajorSelect');
  const selectedMajorId = Number(reportMajorSelect?.value || 0);
  let url = `/reports/summary?start_date=${start_date}&end_date=${end_date}`;
  if (currentUser.role === 'admin' && window.reportUserSelect?.value) {
    url += `&user_id=${window.reportUserSelect.value}`;
  }
  if (selectedMajorId) {
    url += `&major_version_id=${selectedMajorId}`;
  }

  const data = await (await api(url)).json();
  state.reportLoaded = true;
  window.reportLoaded = true;
  window.mReq.innerText = data.overview.executed_requirements || 0;
  window.mCase.innerText = data.overview.created_cases || 0;
  window.mBug.innerText = data.overview.created_bugs || 0;
  window.mRetest.innerText = data.overview.retested_reqs || 0;
  window.mClosed.innerText = data.overview.closed_bugs || 0;

  const vbUrl = selectedMajorId ? `/reports/version-bugs?major_version_id=${selectedMajorId}` : '/reports/version-bugs';
  const versionBugs = await (await api(vbUrl)).json();
  const advUrl = selectedMajorId
    ? `/reports/advanced?start_date=${start_date}&end_date=${end_date}&major_version_id=${selectedMajorId}`
    : `/reports/advanced?start_date=${start_date}&end_date=${end_date}`;
  const advancedData = await (await api(advUrl)).json();

  renderReportCharts(data, advancedData, {
    sourceTypeZh,
    isAllUsersMode: isAllUsersMode(),
    versionBugs,
  });

  if (typeof window.adjustReportChartVisibility === 'function') {
    window.adjustReportChartVisibility();
  }
  if (typeof window.showMessage === 'function') {
    window.showMessage('报表查询成功', 'success');
  }
}

export function exportReportPdf() {
  const element = document.getElementById('reportContent');
  if (!element) return;
  if (typeof html2pdf === 'undefined') {
    window.showMessage && window.showMessage('PDF 导出组件未加载，请检查 /static/vendor/html2pdf.bundle.min.js', 'error');
    return;
  }

  // 物理隔离 0x0 canvas，避免 html2canvas 在克隆阶段崩溃
  const badCanvases = Array.from(element.querySelectorAll('canvas')).filter((c) => c.width === 0 || c.height === 0);
  const restoredList = badCanvases.map((canvas) => {
    const placeholder = document.createComment('hidden-canvas-placeholder');
    canvas.parentNode.insertBefore(placeholder, canvas);
    canvas.parentNode.removeChild(canvas);
    return { canvas, placeholder };
  });

  if (typeof window.showMessage === 'function') {
    window.showMessage('正在生成高清晰度报表 PDF，耗时约几秒钟，请稍候...', 'success');
  }

  const opt = {
    margin: 0.3,
    filename: '测试效能度量大盘.pdf',
    image: { type: 'jpeg', quality: 0.98 },
    html2canvas: { scale: 2, useCORS: true, backgroundColor: '#f8fafc' },
    jsPDF: { unit: 'in', format: 'a4', orientation: 'portrait' },
  };

  html2pdf().set(opt).from(element).save().then(() => {
    window.showMessage && window.showMessage('🎉 报表 PDF 导出成功！', 'success');
  }).catch((err) => {
    console.error('PDF导出失败:', err);
    window.showMessage && window.showMessage('导出失败，请打开 F12 查看报错', 'error');
  }).finally(() => {
    restoredList.forEach((item) => {
      if (item.placeholder && item.placeholder.parentNode) {
        item.placeholder.parentNode.insertBefore(item.canvas, item.placeholder);
        item.placeholder.parentNode.removeChild(item.placeholder);
      }
    });
  });
}

window.OmniQAReportTab = { queryReport, exportReportPdf };
