import { api } from '../api.js';
import { state } from '../state.js';
import { withPrefix, renderBugLink } from '../utils.js';

let dispatchSseBound = false;
let dispatchSseRefreshTimer = null;

export async function searchDispatchBug() {
  const bugId = withPrefix('b#', document.getElementById('dispatchBugNo')?.value || '');
  if (!bugId) return;
  const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  try {
    const params = new URLSearchParams({ bug_id: bugId });
    if (softwareId) params.set('software_id', String(softwareId));
    const res = await (await api('/bugs/search?' + params.toString())).json();
    state.currentDispatchBugId = res.id;
    document.getElementById('dispatchBugTitle').innerHTML = `找到缺陷：${renderBugLink(res)} <span style="font-size:14px; color:#475569;">(归属：${res.req_title})</span>`;
    if (res.dispatched_to_id) document.getElementById('dispatchUserSelect').value = String(res.dispatched_to_id);
    document.getElementById('dispatchBugInfo').classList.remove('hidden');
  } catch (err) {
    window.showMessage && window.showMessage(err.message, 'error');
    document.getElementById('dispatchBugInfo').classList.add('hidden');
  }
}

export async function confirmDispatchBug() {
  if (!state.currentDispatchBugId) return;
  const uid = document.getElementById('dispatchUserSelect')?.value;
  try {
    await api('/bugs/' + state.currentDispatchBugId + '/dispatch', {
      method: 'POST',
      headers: window.H,
      body: ({ user_id: Number(uid) }),
    });
    window.showMessage && window.showMessage('特派成功，企微已通知', 'success');
    await loadDispatchedAll();
  } catch (err) {
    window.showMessage && window.showMessage(err.message, 'error');
  }
}

export async function loadDispatchedAll() {
  try {
    const softwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
    const params = new URLSearchParams();
    if (softwareId) params.set('software_id', String(softwareId));
    const data = await (await api(`/bugs/dispatched-all${params.toString() ? `?${params.toString()}` : ''}`)).json();
    const hideClosed = document.getElementById('hideClosedDispatch')?.checked;
    const filteredData = hideClosed ? data.filter((b) => !b.closed) : data;
    const table = document.getElementById('dispatchAllTable');
    if (!table) return;
    const resZh = { fixed: '修复通过', false_alarm: '误报', rejected: '拒绝修复' };
    if (filteredData.length === 0) {
      table.innerHTML = '<tr><td colspan="3" style="text-align:center; padding: 20px; color:#94a3b8;">当前没有待处理的特派 Bug</td></tr>';
      return;
    }
    table.innerHTML = filteredData.map((b) => {
      const statusHtml = b.closed ? `<span style="color:#16a34a;font-weight:bold;">已闭环 (${resZh[b.resolution] || '修复'})</span>` : '<span style="color:#dc2626;">处理中</span>';
      const ztBugId = (b.bug_id || '').replace(/\D/g, '');
      const ztSlot = ztBugId ? `<span class="zt-bug-slot" data-zt-bug-id="${ztBugId}" style="margin-left:4px;"></span>` : '';
      return `<tr class="dispatch-row-card" data-bug-id="${b.id}">
        <td>${renderBugLink(b)}${ztSlot}</td>
        <td><span class="badge" style="background:#ffedd5;color:#ea580c; border:1px solid #fdba74;">特派给 ${b.dispatched_to_name}</span></td>
        <td>${statusHtml}</td>
      </tr>`;
    }).join('');
    window.OmniQAZentao?.hydrateContainer(table.closest('table') || table);
  } catch (e) {
    console.error('加载特派列表失败', e);
  }
}

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
  window.showMessage && window.showMessage('特派专项验证已保存并同步至总盘');
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
    const title = prompt('请输入 Bug 标题：', current);
    if (title === null) return;
    if (!title.trim()) {
      window.showMessage && window.showMessage('标题不能为空', 'error');
      return;
    }
    try {
      await api(`/zentao/bugs/${Number(zentaoBugId)}`, { method: 'PUT', headers: window.H, body: { title: title.trim() } });
      window.showMessage && window.showMessage('禅道 Bug 标题已更新', 'success');
      await window.OmniQAMineTab.loadMyWorkbench();
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '标题更新失败', 'error');
    }
  } else {
    const num = prompt('请输入正确的 Bug 数字部分：', String(bug.bug_id || '').replace('b#', ''));
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
    const confirmed = window.confirm(
      `⚠️ 删除禅道 Bug\n\n` +
      `将对禅道执行删除操作（软删除，deleted=true）。\n` +
      `当前实例删除后暂无稳定可用的 REST 恢复接口，请谨慎操作！\n\n` +
      `本地记录将标记为“已删除”并从大盘隐藏，不会彻底清除（保留审计记录）。\n\n` +
      `确认删除禅道 Bug #${zentaoBugId} 吗？`
    );
    if (!confirmed) return;
    try {
      await api(`/zentao/bugs/${Number(zentaoBugId)}`, { method: 'DELETE' });
      window.showMessage && window.showMessage('禅道 Bug 已删除并从大盘移除', 'success');
      await window.OmniQAMineTab.loadMyWorkbench();
    } catch (err) {
      window.showMessage && window.showMessage(err.message || '删除失败', 'error');
    }
  } else {
    if (!confirm('确定要在该大版本下移除这个 Bug 吗？')) return;
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
  window.OmniQAOverallTestTab?.openS5ReactivateModal?.(id, bug.zentao_bug_id);
}

window.OmniQADispatchTab = { searchDispatchBug, confirmDispatchBug, loadDispatchedAll, saveDispatchedBug, toggleDispatchedClose, dispatchEditBug, dispatchRemoveBug, dispatchAssignBug, dispatchReactivateBug };

function bindDispatchSSE() {
  if (dispatchSseBound) return;
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;
  const makeDispatchHandler = (tone) => ({ payload }) => {
    const tab = document.getElementById('tab-dispatch');
    if (!tab || tab.classList.contains('hidden')) return;

    const id = Number(payload?.id || 0);
    if (id > 0 && typeof window.OmniQASSE.pulseBoundaryGlow === 'function') {
      const existed = document.querySelector(`.dispatch-row-card[data-bug-id='${id}']`);
      if (existed) window.OmniQASSE.pulseBoundaryGlow(existed, tone);
    }

    if (dispatchSseRefreshTimer) clearTimeout(dispatchSseRefreshTimer);
    dispatchSseRefreshTimer = setTimeout(() => {
      const t = document.getElementById('tab-dispatch');
      if (!t || t.classList.contains('hidden')) return;
      loadDispatchedAll().catch(() => {});
    }, 450);
  };

  window.OmniQASSE.subscribe('bug_dispatch_created', makeDispatchHandler('amber'));
  window.OmniQASSE.subscribe('bug_dispatch_updated', makeDispatchHandler('teal'));
  dispatchSseBound = true;
}

bindDispatchSSE();
