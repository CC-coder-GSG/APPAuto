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

export function renderTaskStoryChangeBadge(task, id = task?.zentao_task_id || task?.task_id) {
  if (!task?.task_story_change_pending || !Number(id)) return '';
  const before = Number(task.task_story_version);
  const latest = Number(task.task_latest_story_version);
  const versions = before && latest ? `v${before} → v${latest}` : '';
  const kind = task.zentao_task_id || task.parent ? '禅道子任务' : '禅道任务';
  const label = `${kind} #${Number(id)} · 研发需求变更 · 待确认${versions ? `（${versions}）` : ''}，点击查看内容并确认`;
  return `<button type="button" class="qa-story-change-badge" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"
    onclick="event.preventDefault(); event.stopPropagation(); window.OmniQAPreview.openTaskStoryChange(${Number(id)})">
    <svg class="qa-story-change-badge__icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M7.1 2.4a1 1 0 0 1 1.8 0l5.3 9.8a1 1 0 0 1-.9 1.5H2.7a1 1 0 0 1-.9-1.5z"/><path d="M8 6v3.1"/><circle cx="8" cy="11.2" r=".65" fill="currentColor" stroke="none"/></svg>
    <span class="qa-story-change-badge__task">#${Number(id)}</span>
    <span class="qa-story-change-badge__label">研发需求变更</span>
    <span class="qa-story-change-badge__pending">待确认</span>
    ${versions ? `<span class="qa-story-change-badge__version">${versions}</span>` : ''}
    <svg class="qa-story-change-badge__arrow" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4"/></svg>
  </button>`;
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
  renderTaskStoryChangeBadge,
};
