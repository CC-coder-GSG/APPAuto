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
  // 勾选闭环时禁用「引出Bug」隐藏输入（沿用旧逻辑），同时为禅道 Bug 展开闭环说明输入框
  const hidden = document.getElementById('dnb_hidden_' + id);
  if (hidden) hidden.disabled = checked;
  const wrap = document.getElementById('dcomment_wrap_' + id);
  const isZentao = !!(document.getElementById('dzt_' + id)?.value || '').trim();
  if (wrap) wrap.style.display = (checked && isZentao) ? '' : 'none';
}

export async function saveDispatchedBug(id) {
  const done = document.getElementById('ddone_' + id).checked;
  const n = document.getElementById('dnb_hidden_' + id).value || null;
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
    body: ({ minor_version_id: minorId, test_done: done, newly_found_bug_id: n, resolution: res }),
  });
  window.showMessage && window.showMessage('特派专项验证已保存并同步至总盘');
  await window.OmniQAMineTab.loadMyWorkbench();
}

export async function addDerivedBug(id) {
  const n = prompt('请输入引出的新Bug数字编号：');
  if (!n) return;
  const newBug = withPrefix('b#', n);
  const hiddenEl = document.getElementById('dnb_hidden_' + id);
  let arr = hiddenEl.value ? hiddenEl.value.split(',') : [];
  if (arr.includes(newBug)) {
    window.showMessage && window.showMessage('该 Bug 编号已经添加过了！', 'error');
    return;
  }
  arr.push(newBug);
  hiddenEl.value = arr.join(',');
  await saveDispatchedBug(id);
}

export async function removeDerivedBug(id, bugToRemove) {
  if (!confirm(`确定要移除引出的 Bug [${bugToRemove}] 吗？`)) return;
  const hiddenEl = document.getElementById('dnb_hidden_' + id);
  let arr = hiddenEl.value ? hiddenEl.value.split(',') : [];
  arr = arr.filter((x) => x !== bugToRemove);
  hiddenEl.value = arr.join(',');
  await saveDispatchedBug(id);
}

window.OmniQADispatchTab = { searchDispatchBug, confirmDispatchBug, loadDispatchedAll, saveDispatchedBug, addDerivedBug, removeDerivedBug, toggleDispatchedClose };

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
