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
    field_test: '外业测试',
  };
  return map[value] || value;
}

export function versionTypeZh(value) {
  return value === 'major' ? '大版本' : '子版本';
}

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function toAbsoluteUrl(url) {
  const v = String(url || '').trim();
  if (!v) return '';
  if (/^https?:\/\//i.test(v)) return v;
  if (v.startsWith('/')) return `${window.location.origin}${v}`;
  return `${window.location.origin}/${v}`;
}

export function renderBugLink(bug) {
  const bugId = escapeHtml(bug?.bug_id || '-');
  const href = toAbsoluteUrl(bug?.zentao_bug_url);
  const title = escapeHtml(bug?.zentao_bug_title || bug?.bug_title || '');
  const numericId = String(bug?.bug_id || '').replace(/\D/g, '');
  // When no URL is available locally, render with qa-bug-id-nohref so the
  // hydrator can upgrade this span to a link after fetching the URL from Zentao.
  const link = href
    ? `<a class="qa-ext-link" href="${href}" target="_blank" rel="noopener noreferrer">${bugId}</a>`
    : numericId
      ? `<span class="qa-bug-id-nohref" data-zt-bug-id="${numericId}">${bugId}</span>`
      : `<span>${bugId}</span>`;
  const tip = title ? `<span class="qa-title-tip" title="${title}">ⓘ</span>` : '';
  return `${link}${tip}`;
}

export function renderCaseLink(testCase) {
  const caseId = escapeHtml(testCase?.zentao_case_id || '-');
  const href = toAbsoluteUrl(testCase?.zentao_case_url);
  if (!href) return `<span>${caseId}</span>`;
  return `<a class="qa-ext-link" href="${href}" target="_blank" rel="noopener noreferrer">${caseId}</a>`;
}

window.OmniQAUtils = {
  withPrefix,
  digitsOnlyValue,
  sourceTypeZh,
  versionTypeZh,
  escapeHtml,
  renderBugLink,
  renderCaseLink,
};
