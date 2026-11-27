// 「我的工作台 → 指派给我的Bug」面板的行操作（闭环/编辑/删除/指派/重新激活）。
// 原 BUG特派中心 已下线，这里只保留工作台面板仍在使用的部分。
import { api } from '../api.js';
import { confirmDialog, promptDialog } from '../components/dialog.js?v=20261126-zai-1';
import { state } from '../state.js';
import { withPrefix } from '../utils.js';

export function toggleDispatchedClose(id, checked) {
  // 勾选闭环时为禅道 Bug 展开闭环说明输入框
  const wrap = document.getElementById('dcomment_wrap_' + id);
  const isZentao = !!(document.getElementById('dzt_' + id)?.value || '').trim();
  if (wrap) wrap.style.display = (checked && isZentao) ? '' : 'none';
}

export async function saveDispatchedBug(id) {
  const done = document.getElementById('ddone_' + id).checked;
  const res = document.getElementById('dres_' + id).value;
  const zentaoBugId = (document.getElementById('dzt_' + id)?.value || '').trim();
  const wasClosed = !!(document.getElementById('dwasclosed_' + id)?.value || '');
  const comment = (document.getElementById('dcomment_' + id)?.value || '').trim();
  const isZentaoBug = !!zentaoBugId;
  const minorId = Number(document.getElementById('mineMinorSelect')?.value || 0);

  // 勾选闭环且是禅道 Bug：先同步关闭禅道（与测试工作台一致）
  if (isZentaoBug && done) {
    const ztId = Number(zentaoBugId);
    if (ztId) {
      try {
        await api(`/zentao/bugs/${ztId}/close`, { method: 'POST', headers: window.H, body: { comment } });
      } catch (err) {
        window.showMessage && window.showMessage(err.message || '禅道关闭失败，本地未保存闭环结果', 'error');
        return;
      }
    }
  }

  // 取消闭环且禅道侧已关闭：必须先在禅道重新激活
  if (isZentaoBug && !done && wasClosed) {
    const ztId = Number(zentaoBugId);
    if (ztId) {
      const reactivate = window.OmniQAOverallTestTab?.openS5ReactivateModalAsync;
      if (typeof reactivate === 'function') {
        window.showMessage && window.showMessage('取消闭环需要先在禅道重新激活该 Bug', 'info');
        const activated = await reactivate(id, ztId);
        if (!activated) {
          window.showMessage && window.showMessage('未完成重新激活，已取消「取消闭环」操作', 'error');
          return;
        }
      }
    }
  }

  await api(`/overall-test/bugs/${id}/result`, {
    method: 'PUT', headers: window.H,
    body: ({ minor_version_id: minorId, test_done: done, newly_found_bug_id: null, resolution: res }),
  });
  window.showMessage && window.showMessage('验证结果已保存并同步至总盘');
  await window.OmniQAMineTab.loadMyWorkbench();
}

// ——「指派给我的Bug」面板行操作（编辑/删除/指派/重新激活）——
// 完全照搬测试工作台的操作能力；指派/重新激活复用 overall-test 的弹窗，
// 编辑/删除在本面板内直接完成并刷新需求工作台。
function findDispatchedBug(id) {
  return (state.currentDispatchData || []).find((b) => b.id === id) || null;
}

export async function dispatchEditBug(id) {
  const bug = findDispatchedBug(id);
  if (!bug) return;
  const zentaoBugId = bug.zentao_bug_id || '';
  if (zentaoBugId) {
    const current = bug.zentao_bug_title ? String(bug.zentao_bug_title).replace(/&amp;/g, '&') : '';
    const title = await promptDialog({
      icon: 'i-bug', title: '修改 Bug 标题', sub: `禅道 Bug #${zentaoBugId}`,
      label: 'Bug 标题', value: current, maxlength: 255, okText: '保存并同步禅道',
    });
    if (title === null) return;
    try {
      await api(`/zentao/bugs/${Number(zentaoBugId)}`, { method: 'PUT', headers: window.H, body: { title: title.trim() } });
      window.showMessage && window.showMessage('禅道 Bug 标题已更新', 'success');
      await window.OmniQAMineTab.loadMyWorkbench();
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '标题更新失败', 'error');
    }
  } else {
    const num = await promptDialog({
      icon: 'i-wrench', title: '纠正 Bug 编号', label: 'Bug 编号', prefix: 'b#',
      value: String(bug.bug_id || '').replace('b#', ''),
      validate: (v) => (/^\d+$/.test(v) ? '' : '请输入数字编号'), okText: '保存',
    });
    if (!num) return;
    try {
      await api('/bugs/' + id + '?new_bug_id=' + encodeURIComponent(withPrefix('b#', num)), { method: 'PUT' });
      window.showMessage && window.showMessage('Bug 编号已纠正', 'success');
      await window.OmniQAMineTab.loadMyWorkbench();
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '更新失败', 'error');
    }
  }
}

export async function dispatchRemoveBug(id) {
  const bug = findDispatchedBug(id);
  if (!bug) return;
  const zentaoBugId = bug.zentao_bug_id || '';
  if (zentaoBugId) {
    const confirmed = await confirmDialog({
      danger: true, title: '删除禅道 Bug', sub: `禅道 Bug #${zentaoBugId}`,
      message: '将对禅道执行删除操作（软删除）。当前禅道实例暂无稳定可用的恢复接口，请谨慎操作。\n\n本地记录会标记为「已删除」并从大盘隐藏，审计记录保留。',
      okText: '删除禅道 Bug',
    });
    if (!confirmed) return;
    try {
      await api(`/zentao/bugs/${Number(zentaoBugId)}`, { method: 'DELETE' });
      window.showMessage && window.showMessage('禅道 Bug 已删除并从大盘移除', 'success');
      await window.OmniQAMineTab.loadMyWorkbench();
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '删除失败', 'error');
    }
  } else {
    if (!(await confirmDialog({ danger: true, title: '移除 Bug', message: '从该大版本下彻底移除这个 Bug 记录。', okText: '移除' }))) return;
    try {
      await api('/bugs/' + id, { method: 'DELETE' });
      window.showMessage && window.showMessage('Bug 已彻底移除', 'success');
      await window.OmniQAMineTab.loadMyWorkbench();
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '删除失败', 'error');
    }
  }
}

export function dispatchAssignBug(id) {
  const bug = findDispatchedBug(id);
  if (!bug?.zentao_bug_id) return;
  const isClosed = String(bug.zentao_live_status || '').toLowerCase() === 'closed'
    || !!bug.zentao_close_date || !!bug.zentao_closed_by_name
    || String(bug.zentao_assigned_to_name || '').toLowerCase() === 'closed';
  if (isClosed) {
    window.showMessage && window.showMessage('已关闭的禅道 Bug 不能再指派，请先重新激活', 'error');
    return;
  }
  window.OmniQAOverallTestTab?.openS5AssignModal?.(id, bug.zentao_bug_id);
}

export function dispatchReactivateBug(id) {
  const bug = findDispatchedBug(id);
  if (!bug?.zentao_bug_id) return;
  // 激活成功后刷新我的工作台（本面板数据来自 loadMyWorkbench），
  // 而不是默认的测试工作台，保证当前列表立即更新。
  window.OmniQAOverallTestTab?.openS5ReactivateModal?.(id, bug.zentao_bug_id, {
    onDone: () => window.OmniQAMineTab?.loadMyWorkbench?.(),
  });
}

window.OmniQAMineAssignedBugs = { saveDispatchedBug, toggleDispatchedClose, dispatchEditBug, dispatchRemoveBug, dispatchAssignBug, dispatchReactivateBug };
