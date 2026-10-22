import { escapeHtml } from '../utils.js';

const resolvedStatuses = new Set(['resolved', 'closed']);
const statusOf = bug => String(bug.zentao_live_status || (bug.closed ? 'closed' : bug.fixed_minor_version_no ? 'resolved' : 'active')).trim().toLowerCase();
const versionName = value => [...new Set(String(value || '').split(',')
  .map(part => part.trim()).filter(Boolean)
  .map(part => /^\d+$/.test(part) ? '版本名称待获取' : part))].join(', ');
const hasBuildId = value => String(value || '').split(',').some(part => /^\d+$/.test(part.trim()));

function displayVersion(button) {
  const resolved = button.dataset.versionMode === 'resolved';
  const value = resolved ? versionName(button.dataset.resolvedVersion) || '未填写' : versionName(button.dataset.foundVersion) || '未知';
  const label = resolved ? '解决于' : '发现于';
  button.textContent = `(${resolved ? '✅' : ''}${label}: 🏷️${value}${button.disabled ? '' : ' ⇄'})`;
  button.title = button.disabled ? `${label}：${value}` : `${label}：${value}；点击查看${resolved ? '发现' : '解决'}版本`;
  button.setAttribute('aria-label', button.title);
}

export function renderBugVersion(bug) {
  const status = statusOf(bug);
  const toggle = resolvedStatuses.has(status);
  // Empty remote values mean "not supplied"; only absent remote data uses local verification.
  const fixed = versionName(bug.resolved_version_no ?? bug.fixed_minor_version_no ?? '');
  const found = versionName(bug.found_minor_version_no) || '未知';
  return `<button type="button" class="qa-bug-version" data-bug-status="${escapeHtml(status)}"
    data-found-version="${escapeHtml(found)}" data-resolved-version="${escapeHtml(fixed)}"
    data-version-mode="${toggle ? 'resolved' : 'found'}" ${toggle ? '' : 'disabled'}
    title="${toggle ? '点击查看发现版本' : '发现版本'}">(${toggle ? '✅解决于' : '发现于'}: 🏷️${escapeHtml(toggle ? fixed || '未填写' : found)}${toggle ? ' ⇄' : ''})</button>`;
}

export function updateBugVersion(parent, data) {
  const button = parent?.querySelector('.qa-bug-version');
  if (!button || !data || data.deleted) return;
  const status = String(data.status || button.dataset.bugStatus).trim().toLowerCase();
  if (data.build) {
    const cached = button.dataset.foundVersion;
    // An unresolved live reference must not replace a known discovery-version name.
    if (!hasBuildId(data.build) || !cached || ['未知', '版本名称待获取'].includes(cached) || hasBuildId(cached)) {
      button.dataset.foundVersion = data.build;
    }
  }
  if (data.resolved_build !== undefined && data.resolved_build !== null) button.dataset.resolvedVersion = data.resolved_build;
  // Keep the user's view during same-status hydration; a status change restores the default.
  if (status !== button.dataset.bugStatus) button.dataset.versionMode = resolvedStatuses.has(status) ? 'resolved' : 'found';
  button.dataset.bugStatus = status;
  button.disabled = !resolvedStatuses.has(status);
  if (button.disabled) button.dataset.versionMode = 'found';
  displayVersion(button);
}

document.addEventListener('click', event => {
  const button = event.target.closest?.('button.qa-bug-version');
  if (!button || button.disabled) return;
  event.preventDefault();
  event.stopPropagation();
  button.dataset.versionMode = button.dataset.versionMode === 'resolved' ? 'found' : 'resolved';
  displayVersion(button);
});
