/**
 * 织流 Weaveflow — 动效引擎
 *
 * 职责（与业务零耦合，挂在 showTab 之后做纯视觉增强）：
 *  1. 左侧导航 rail 的流体弹簧指示器（FLIP）+ 选中态（修复 active-tab 从未被设置的问题）
 *  2. Tab 切换时章节内容的 stagger 弹簧入场
 *  3. .metric-value 数字 count-up（easeOutExpo），含 SSE/报表异步刷新时的增量动画
 *  4. 章节编号注入（01/02/…，对应每个 section 的 h2）
 *
 * 加载方式：<script defer>，在 index.html 内联脚本（定义 showTab）之后执行。
 */
(function () {
  "use strict";

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const TAB_BUTTON_MAP = {
    assign: "tabAssignBtn", mine: "tabMineBtn", "task-board": "tabTaskBoardBtn",
    feedback: "tabFeedbackBtn", "field-test": "tabFieldTestBtn",
    "build-records": "tabBuildRecordsBtn", "testcase-center": "tabTestcaseCenterBtn",
    report: "tabReportBtn", activity: "tabActivityBtn",
    data: "tabDataBtn", dispatch: "tabDispatchBtn", "zentao-ai": "tabZentaoAiBtn",
    jenkins: "tabJenkinsBtn", "cad-test": "tabCadTestBtn", terminal: "tabTerminalBtn",
    learning: "tabLearningBtn",
  };
  // retest / overall-test 是工作台子页，归并到 mine
  const ALIAS = { retest: "mine", "overall-test": "mine" };

  /* ── 1. 流体导航指示器 ─────────────────────────────── */

  let indicator = null;

  function ensureIndicator() {
    const tabs = document.getElementById("tabs");
    if (!tabs) return null;
    if (!indicator || !indicator.isConnected) {
      indicator = document.createElement("div");
      indicator.id = "weaveRailIndicator";
      indicator.setAttribute("aria-hidden", "true");
      tabs.prepend(indicator);
    }
    return indicator;
  }

  function markActive(name) {
    const resolved = ALIAS[name] || name;
    Object.entries(TAB_BUTTON_MAP).forEach(([key, id]) => {
      const btn = document.getElementById(id);
      if (btn) btn.classList.toggle("active-tab", key === resolved);
    });
  }

  function moveIndicator() {
    const ind = ensureIndicator();
    const active = document.querySelector("#tabs button.active-tab");
    if (!ind) return;
    if (!active || active.classList.contains("hidden")) {
      ind.style.opacity = "0";
      return;
    }
    ind.style.opacity = "1";
    ind.style.transform = `translate(${active.offsetLeft}px, ${active.offsetTop}px)`;
    ind.style.width = `${active.offsetWidth}px`;
    ind.style.height = `${active.offsetHeight}px`;
  }

  /* ── 2. 章节 stagger 入场 ──────────────────────────── */

  const STAGGER_CAP = 12;

  function staggerSection(name) {
    if (reduceMotion) return;
    const resolved = ALIAS[name] || name;
    const section = document.getElementById("tab-" + resolved);
    if (!section || section.classList.contains("hidden")) return;
    const children = Array.from(section.children).filter(
      (el) => !el.classList.contains("hidden") && el.tagName !== "SCRIPT"
    );
    children.forEach((el, i) => {
      el.classList.remove("weave-enter");
      if (i < STAGGER_CAP) {
        el.style.setProperty("--wi", i);
        // 强制 reflow 以便重复触发动画
        void el.offsetWidth;
        el.classList.add("weave-enter");
      }
    });
  }

  // 入场动画结束后摘掉类，避免 transform 残留影响 sticky/fixed 子元素
  document.addEventListener("animationend", (e) => {
    if (e.animationName === "weaveFloatIn" && e.target.classList) {
      e.target.classList.remove("weave-enter");
    }
  });

  /* ── 3. 数字 count-up ─────────────────────────────── */

  function animateNumber(el, from, to, suffix, dur) {
    el.dataset.weaveAnimating = "1";
    const t0 = performance.now();
    function tick(now) {
      const p = Math.min((now - t0) / dur, 1);
      const eased = p === 1 ? 1 : 1 - Math.pow(2, -10 * p);
      el.textContent = Math.round(from + (to - from) * eased) + suffix;
      if (p < 1) {
        requestAnimationFrame(tick);
      } else {
        delete el.dataset.weaveAnimating;
      }
    }
    requestAnimationFrame(tick);
  }

  // 解析 "128" / "86%" / "12 条" 这类文本；不可解析则返回 null
  function parseMetric(text) {
    const m = /^\s*(-?\d[\d,]*)\s*(.*)$/.exec(text || "");
    if (!m) return null;
    const value = parseInt(m[1].replace(/,/g, ""), 10);
    if (!Number.isFinite(value) || Math.abs(value) > 1e7) return null;
    return { value, suffix: m[2] ? (text.includes(" ") ? " " + m[2] : m[2]) : "" };
  }

  function countUpIn(rootEl) {
    if (reduceMotion) return;
    rootEl.querySelectorAll(".metric-value").forEach((el) => {
      if (el.dataset.weaveAnimating) return;
      const parsed = parseMetric(el.textContent);
      if (!parsed || parsed.value === 0) return;
      animateNumber(el, 0, parsed.value, parsed.suffix, Math.min(900, 450 + Math.abs(parsed.value)));
    });
  }

  // 异步刷新（报表加载 / SSE 推送）时，让数字从旧值滚到新值
  function observeMetrics() {
    if (reduceMotion || typeof MutationObserver === "undefined") return;
    const seen = new WeakMap();
    const mo = new MutationObserver((muts) => {
      muts.forEach((m) => {
        const el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
        if (!el || !el.classList || !el.classList.contains("metric-value")) return;
        if (el.dataset.weaveAnimating) return;
        const parsed = parseMetric(el.textContent);
        if (!parsed) return;
        const prev = seen.get(el);
        seen.set(el, parsed.value);
        if (prev === undefined || prev === parsed.value) return;
        animateNumber(el, prev, parsed.value, parsed.suffix, 650);
      });
    });
    mo.observe(document.body, { subtree: true, childList: true, characterData: true });
  }

  /* ── 4. 章节编号注入 ──────────────────────────────── */

  function injectChapterNumbers() {
    let n = 0;
    document.querySelectorAll("section.card[id^='tab-']").forEach((section) => {
      const h2 = section.querySelector("h2");
      if (!h2 || h2.querySelector(".chapter-no")) return;
      n += 1;
      const span = document.createElement("span");
      span.className = "chapter-no";
      span.textContent = String(n).padStart(2, "0");
      h2.prepend(span);
    });
  }

  /* ── 接线 ─────────────────────────────────────────── */

  function onTabShown(requestedName) {
    // showTab 内部可能因权限/模块未就绪而改用 fallback tab，
    // 因此以"实际可见的 section"为准，而非入参
    const name = currentVisibleTab() || ALIAS[requestedName] || requestedName;
    markActive(name);
    // 等按钮 hidden 状态/布局稳定后再定位指示器
    requestAnimationFrame(moveIndicator);
    staggerSection(name);
    const section = document.getElementById("tab-" + (ALIAS[name] || name));
    if (section) countUpIn(section);
  }

  function wrapShowTab() {
    const orig = window.showTab;
    if (typeof orig !== "function" || orig.__weaveWrapped) return;
    const wrapped = async function (name) {
      await orig.apply(this, arguments);
      onTabShown(name);
    };
    wrapped.__weaveWrapped = true;
    window.showTab = wrapped;
  }

  function currentVisibleTab() {
    // tab-retest / tab-overall-test 是「我的工作台」的子页，被 mountWorkbenchSubpages
    // 搬进 tab-mine 内部，且自身永远不带 .hidden（仅靠祖先隐藏）。若按文档顺序直接取
    // 第一个 :not(.hidden) 的 section，会在离开 mine 后误命中这段嵌套子页，导致排在
    // mine 之后的 tab 拿不到指示器/入场动画。这里跳过子页，并用 offsetParent 排除
    // 「祖先被隐藏」的实际不可见 section。
    const list = document.querySelectorAll("section.card[id^='tab-']:not(.hidden)");
    for (const s of list) {
      if (s.id === "tab-retest" || s.id === "tab-overall-test") continue;
      if (s.offsetParent === null) continue;
      return s.id.replace(/^tab-/, "");
    }
    return null;
  }

  function init() {
    document.documentElement.classList.add("weave");
    wrapShowTab();
    injectChapterNumbers();
    observeMetrics();
    ensureIndicator();
    // 应用启动时 app.js 可能已经展示了默认 tab
    const name = currentVisibleTab();
    if (name) onTabShown(name);
    // 登录完成后按钮可见性会变化，多校准几次指示器
    let tries = 0;
    const timer = setInterval(() => {
      moveIndicator();
      const visible = currentVisibleTab();
      if (visible && !document.querySelector("#tabs button.active-tab")) markActive(visible);
      if (++tries >= 10) clearInterval(timer);
    }, 500);
    window.addEventListener("resize", moveIndicator);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.WeaveMotion = { moveIndicator, staggerSection, countUpIn };
})();
