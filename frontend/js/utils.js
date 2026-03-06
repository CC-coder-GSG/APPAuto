export function withPrefix(prefix, value) {
  const digits = String(value || "").replace(/\D/g, "");
  return digits ? `${prefix}${digits}` : null;
}

export function digitsOnlyValue(value) {
  return String(value || "").replace(/\D/g, "");
}

export function sourceTypeZh(value) {
  const map = {
    manual: '自由问题',
    case: '用例来源',
    requirement: '需求来源',
    legacy_bug: '历史Bug来源',
    retest: '复测漏测',
  };
  return map[value] || value;
}

export function versionTypeZh(value) {
  return value === 'major' ? '大版本' : '子版本';
}

window.OmniQAUtils = { withPrefix, digitsOnlyValue, sourceTypeZh, versionTypeZh };
