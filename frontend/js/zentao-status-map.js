// Lightweight en→zh mapping for Zentao status/stage strings. Applied only on
// the new zentao-ai tab and the AI-result modal (other tabs left untouched per spec).

export const ZENTAO_STATUS_MAP = {
  active: '激活',
  inactive: '未激活',
  draft: '草稿',
  reviewing: '评审中',
  changing: '变更中',
  wait: '待处理',
  doing: '进行中',
  changed: '进行中',
  developing: '开发完成',
  testing: '测试中',
  tested: '测试完成',
  verified: '已验证',
  released: '已发布',
  launched: '已上线',
  done: '已完成',
  closed: '已关闭',
  cancelled: '已取消',
  canceled: '已取消',
  pause: '暂停',
  blocked: '阻塞',
  resolved: '已解决',
  rejected: '已拒绝',
  fixing: '修复中',
  // stage-ish values
  projected: '已立项',
  planned: '已计划',
  design: '设计中',
};

export function mapZentaoStatus(value) {
  if (value === null || value === undefined) return '';
  const key = String(value).trim().toLowerCase();
  if (!key) return '';
  return ZENTAO_STATUS_MAP[key] || value;
}

window.mapZentaoStatus = mapZentaoStatus;
window.ZENTAO_STATUS_MAP = ZENTAO_STATUS_MAP;
