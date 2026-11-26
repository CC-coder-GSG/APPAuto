// 竞品分析工作台（2026-11-26 并入主应用页签，原 /competitor-analysis 独立页面改为重定向到这里）
// 工作台本身是一个约 1600 行的独立应用（矩阵编辑、富文本、撤销重做、三方合并），整体以 iframe 嵌入：
//   - 地址 /competitor-analysis?embed=1：页面进入嵌入态（隐藏品牌与返回按钮、页签并入顶栏、Weave 配色）
//   - 对话框走主应用的 window.OmniQADialog（页面内通过 parent 调用），悬浮提示同样用主应用组件
//   - 主题色：加载完成后、以及用户在账户菜单换主题色时，把 --w-accent* 发给嵌入页
// 外框样式 weave.css R24（.cpa-*）。iframe 高度 = 视口剩余高度，工作台在框内自己滚动（顶栏 / 侧栏吸顶照常工作）。

const SRC = '/competitor-analysis?embed=1&v=20261126-bgl-1';
const ACCENT_VARS = ['--w-accent', '--w-accent-hover', '--w-accent-ink', '--w-accent-rgb', '--w-accent-2'];

let frame = null;
let ready = false;
let themeObserver = null;

function themeVars() {
  const cs = getComputedStyle(document.documentElement);
  const vars = {};
  ACCENT_VARS.forEach((k) => { const v = cs.getPropertyValue(k).trim(); if (v) vars[k] = v; });
  return vars;
}

function pushTheme() {
  if (!frame?.contentWindow) return;
  try { frame.contentWindow.postMessage({ type: 'omniqa-theme', vars: themeVars() }, location.origin); } catch (_) { /* 页面未就绪 */ }
}

function fit() {
  const wrap = document.getElementById('competitorFrameWrap');
  if (!wrap || wrap.offsetParent === null) return;
  const top = wrap.getBoundingClientRect().top;
  // 视口剩余高度，最低 560；页面滚动时不重复计算（只在显示 / 窗口变化时调用）
  const h = Math.max(560, Math.round(window.innerHeight - Math.max(top, 0) - 16));
  wrap.style.height = `${h}px`;
}

function onMessage(event) {
  if (event.origin !== location.origin || event.source !== frame?.contentWindow) return;
  if (event.data?.type === 'omniqa-competitor-ready') {
    ready = true;
    document.getElementById('competitorLoading')?.remove();
    frame.classList.add('is-ready');
    pushTheme();
  }
}

function mount() {
  const wrap = document.getElementById('competitorFrameWrap');
  if (!wrap) return;
  frame = document.createElement('iframe');
  frame.className = 'cpa-frame';
  frame.title = '竞品分析工作台';
  frame.src = SRC;
  // 嵌入页不发 ready（例如加载失败被重定向）时也要收起加载提示
  frame.addEventListener('load', () => {
    setTimeout(() => {
      if (ready) return;
      document.getElementById('competitorLoading')?.remove();
      frame.classList.add('is-ready');
      pushTheme();
    }, 1500);
  });
  wrap.appendChild(frame);
  window.addEventListener('message', onMessage);
  window.addEventListener('resize', fit);
  // 账户菜单换主题色时 shell.js 改写 <html> 的内联 style
  themeObserver = new MutationObserver(pushTheme);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
}

export function activate() {
  if (!frame) mount();
  requestAnimationFrame(fit);
  setTimeout(fit, 400);   // 页签切换动效结束后再校一次
}

export function reload() {
  if (!frame) return activate();
  ready = false;
  frame.classList.remove('is-ready');
  frame.src = SRC;
  return undefined;
}

window.OmniQACompetitorTab = { activate, reload };
