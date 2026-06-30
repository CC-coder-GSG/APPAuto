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

// SVG "open in window" icon — used by the preview buttons across mine.js,
// assign.js, retest.js, etc. Kept inline so callers don't need to ship more
// vendor assets.
const _PREVIEW_ICON_SVG = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 2.5h5"/><path d="M2.5 2.5v5"/><path d="M13.5 13.5h-5"/><path d="M13.5 13.5v-5"/><path d="M13.5 2.5L9 7"/><path d="M2.5 13.5L7 9"/></svg>';

/**
 * Render a uniform preview button. type ∈ {'story','bug','testcase'}.
 * The numeric Zentao id is required; the button calls OmniQAPreview.open<Kind>(id).
 */
export function renderPreviewBtn(type, id, opts = {}) {
  const numericId = String(id || '').replace(/\D/g, '');
  if (!numericId) return '';
  const fn = type === 'bug' ? 'openBug' : type === 'testcase' ? 'openTestcase' : type === 'task' ? 'openTask' : 'openStory';
  const tip = opts.title || (type === 'bug' ? '预览禅道 Bug' : type === 'testcase' ? '预览禅道用例' : type === 'task' ? '预览禅道任务' : '预览禅道需求正文');
  const extra = opts.extraStyle || '';
  return `<a href="javascript:void(0)" class="qa-preview-btn" title="${escapeHtml(tip)}" style="${extra}"
    onclick="event.preventDefault(); event.stopPropagation(); window.OmniQAPreview && window.OmniQAPreview.${fn}(${numericId})">${_PREVIEW_ICON_SVG}</a>`;
}

window.OmniQAUtils = {
  withPrefix,
  digitsOnlyValue,
  sourceTypeZh,
  versionTypeZh,
  escapeHtml,
  renderBugLink,
  renderCaseLink,
  renderPreviewBtn,
};
