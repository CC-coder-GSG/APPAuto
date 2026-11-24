/**
 * OmniQA 壳层 v2 —— 交互层（2026-11-24 改版，demo：frontend/ui-demo/shell-v2.html）
 *
 * 与业务零耦合，只做显示与交互增强：
 *  1. 分组侧栏：空分组隐藏、收起为图标栏（记忆）、窄屏抽屉
 *  2. 顶栏：面包屑跟随当前页面、账户菜单、头像首字
 *  3. Ctrl + K 快速跳转（数据源 = 侧栏中当前账号可见的入口）
 *  4. 页面标题下的一行说明
 *  5. 自绘下拉面板：拦截原生 <select> 的系统弹层，原生控件/取值/change 事件不变
 *  6. 自绘日历：拦截原生 <input type="date"> 的系统日历，仍可直接键入
 *  7. 展开态需求卡悬停流光：注入 GPU 旋转图层（见 weave.css R5.4）
 *
 * 加载方式：<script defer>，在 weave-motion.js 之后。
 */
(function () {
  "use strict";

  const doc = document;
  const root = doc.documentElement;
  const $ = (sel, el = doc) => el.querySelector(sel);
  const $$ = (sel, el = doc) => Array.from(el.querySelectorAll(sel));
  const ic = (id, cls = "ui-ic") => `<svg class="${cls}"><use href="#i-${id}"/></svg>`;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const finePointer = window.matchMedia("(pointer: fine)");
  const narrow = window.matchMedia("(max-width: 900px)");
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 隐私模式等 */ } },
  };

  /* ── 1. 侧栏 ─────────────────────────────────────────── */

  // 页面名 → 导航按钮（与 index.html applyTabVisibility / weave-motion 保持一致）
  const TAB_BUTTONS = {
    assign: "tabAssignBtn", mine: "tabMineBtn", "task-board": "tabTaskBoardBtn",
    feedback: "tabFeedbackBtn", "field-test": "tabFieldTestBtn",
    "build-records": "tabBuildRecordsBtn", "testcase-center": "tabTestcaseCenterBtn",
    report: "tabReportBtn", activity: "tabActivityBtn", data: "tabDataBtn",
    dispatch: "tabDispatchBtn", "zentao-ai": "tabZentaoAiBtn", jenkins: "tabJenkinsBtn",
    "cad-test": "tabCadTestBtn", terminal: "tabTerminalBtn", learning: "tabLearningBtn",
    tools: "tabToolsBtn",
  };

  function moveIndicator() {
    if (window.WeaveMotion && typeof window.WeaveMotion.moveIndicator === "function") {
      window.WeaveMotion.moveIndicator();
    }
  }

  function syncNavGroups() {
    $$("#tabs .nav-group").forEach((g) => {
      const anyVisible = $$("button", g).some((b) => !b.classList.contains("hidden"));
      g.classList.toggle("is-empty", !anyVisible);
    });
    requestAnimationFrame(moveIndicator);
  }

  function initNav() {
    const tabs = $("#tabs");
    if (!tabs) return;
    syncNavGroups();
    // applyTabVisibility 登录后才切换按钮 hidden，这里跟随刷新分组显隐
    new MutationObserver(syncNavGroups).observe(tabs, { subtree: true, attributes: true, attributeFilter: ["class"] });

    const collapseBtn = $("#navCollapseBtn");
    let animTimer = 0;
    const setCollapsed = (on, animate) => {
      if (animate) {
        root.classList.add("nav-animating");
        clearTimeout(animTimer);
        animTimer = setTimeout(() => root.classList.remove("nav-animating"), 450);
      }
      root.classList.toggle("nav-collapsed", on);
      store.set("omniqa.navCollapsed", on ? "1" : "0");
      if (collapseBtn) {
        const t = on ? "展开侧栏" : "收起侧栏";
        collapseBtn.title = t;
        collapseBtn.setAttribute("aria-label", t);
      }
    };
    setCollapsed(root.classList.contains("nav-collapsed"));
    collapseBtn && collapseBtn.addEventListener("click", () => {
      setCollapsed(!root.classList.contains("nav-collapsed"), true);
      requestAnimationFrame(moveIndicator);
      // 兜底：transitionend 未触发（如减弱动效）时也要在宽度变化后重新定位指示器
      setTimeout(() => { moveIndicator(); window.dispatchEvent(new Event("resize")); }, 380);
    });
    tabs.addEventListener("transitionend", (e) => {
      if (e.target === tabs && e.propertyName === "width") {
        moveIndicator();
        window.dispatchEvent(new Event("resize"));
      }
    });

    // 窄屏抽屉
    const closeDrawer = () => root.classList.remove("nav-drawer-open");
    const drawerBtn = $("#navDrawerBtn");
    drawerBtn && drawerBtn.addEventListener("click", () => {
      root.classList.add("nav-drawer-open");
      requestAnimationFrame(moveIndicator);
    });
    const scrim = $("#navScrim");
    scrim && scrim.addEventListener("click", closeDrawer);
    tabs.addEventListener("click", (e) => {
      if (narrow.matches && e.target.closest(".nav-scroll button")) closeDrawer();
    });
    narrow.addEventListener && narrow.addEventListener("change", closeDrawer);
  }

  /* ── 2. 顶栏：面包屑 / 账户菜单 ─────────────────────── */

  function currentVisibleTab() {
    for (const s of $$("section.card[id^='tab-']:not(.hidden)")) {
      if (s.id === "tab-retest" || s.id === "tab-overall-test") continue; // 工作台子页
      if (s.offsetParent === null) continue;
      return s.id.replace(/^tab-/, "");
    }
    return null;
  }

  function updateCrumb() {
    const groupEl = $("#topbarGroup");
    const pageEl = $("#topbarPage");
    if (!groupEl || !pageEl) return;
    const name = currentVisibleTab();
    if (!name) return;
    const btn = TAB_BUTTONS[name] && doc.getElementById(TAB_BUTTONS[name]);
    let group = "", page = "";
    if (btn) {
      const g = btn.closest(".nav-group");
      group = (g && g.dataset.group) || "";
      page = ($(".nav-label", btn) || btn).textContent.trim();
      // 不经 showTab 的切换（如功能图谱「返回」）也要恢复侧栏选中态
      if (!btn.classList.contains("active-tab")) {
        $$("#tabs button.active-tab").forEach((b) => b.classList.remove("active-tab"));
        btn.classList.add("active-tab");
        requestAnimationFrame(moveIndicator);
      }
    } else {
      // 无侧栏入口的整页视图（如功能图谱，不经 showTab 打开）：清掉残留选中态
      $$("#tabs button.active-tab").forEach((b) => b.classList.remove("active-tab"));
      requestAnimationFrame(moveIndicator);
      const h = name === "feature-tree" ? $("#ftreeTitleText") : $(`#tab-${name} h2`);
      page = h ? h.textContent.replace(/\s+/g, " ").trim() : name;
      group = name === "feature-tree" ? "功能图谱" : "";
    }
    groupEl.textContent = group;
    groupEl.hidden = !group;
    const sep = $(".topbar-crumb-sep");
    if (sep) sep.style.display = group ? "" : "none";
    pageEl.textContent = page;
  }

  function initCrumb() {
    const mo = new MutationObserver(() => requestAnimationFrame(updateCrumb));
    $$("section.card[id^='tab-']").forEach((s) => mo.observe(s, { attributes: true, attributeFilter: ["class"] }));
    updateCrumb();
  }

  function initUserMenu() {
    const wrap = $("#userMenu"), btn = $("#userMenuBtn"), pop = $("#userMenuPop");
    if (!wrap || !btn || !pop) return;
    const setOpen = (on) => {
      pop.classList.toggle("hidden", !on);
      wrap.classList.toggle("open", on);
      btn.setAttribute("aria-expanded", on ? "true" : "false");
    };
    btn.addEventListener("click", (e) => { e.stopPropagation(); setOpen(pop.classList.contains("hidden")); });
    pop.addEventListener("click", (e) => { if (e.target.closest("button")) setOpen(false); });
    doc.addEventListener("mousedown", (e) => { if (!wrap.contains(e.target)) setOpen(false); });
    doc.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.classList.contains("hidden")) setOpen(false); });

    // 头像首字 + 菜单内账号名（#me 由登录流程写入，如 "admin(admin)"）
    const me = $("#me");
    const syncName = () => {
      const text = (me && me.textContent || "").trim();
      const initial = text ? Array.from(text)[0].toUpperCase() : "·";
      ["userAvatar", "userMenuAvatar"].forEach((id) => { const el = doc.getElementById(id); if (el) el.textContent = initial; });
      const nameEl = $("#userMenuName");
      if (nameEl) nameEl.textContent = text || "未登录";
    };
    if (me) new MutationObserver(syncName).observe(me, { childList: true, characterData: true, subtree: true });
    syncName();
  }

  /* ── 3. Ctrl + K 快速跳转 ───────────────────────────── */

  function initCommandPalette() {
    const overlay = $("#cmdkOverlay"), input = $("#cmdkInput"), list = $("#cmdkList"), trigger = $("#cmdkBtn");
    if (!overlay || !input || !list) return;
    let results = [], sel = 0;

    function entries() {
      return $$("#tabs .nav-scroll button:not(.hidden)").map((b) => ({
        btn: b,
        label: ($(".nav-label", b) || b).textContent.trim(),
        group: (b.closest(".nav-group") && b.closest(".nav-group").dataset.group) || "",
        icon: (($("use", b) || {}).getAttribute ? $("use", b).getAttribute("href") : "") || "",
        external: b.hasAttribute("data-nav-external"),
        current: b.classList.contains("active-tab"),
      }));
    }
    function render() {
      const q = input.value.trim().toLowerCase();
      results = entries().filter((it) => !q || it.label.toLowerCase().includes(q) || it.group.includes(q));
      sel = Math.min(sel, Math.max(results.length - 1, 0));
      let html = "", last = null;
      results.forEach((it, i) => {
        if (it.group !== last) { html += `<div class="cmdk-group">${esc(it.group)}</div>`; last = it.group; }
        const hint = it.current ? "当前页面" : it.external ? "独立页面" : "";
        html += `<div class="cmdk-item${i === sel ? " sel" : ""}" role="option" data-i="${i}"><svg class="ui-ic"><use href="${esc(it.icon)}"/></svg>${esc(it.label)}<span class="hint">${hint}</span></div>`;
      });
      list.innerHTML = html || `<div class="cmdk-empty">没有匹配的页面</div>`;
      const cur = $(".cmdk-item.sel", list);
      cur && cur.scrollIntoView({ block: "nearest" });
    }
    const isOpen = () => !overlay.classList.contains("hidden");
    function open() {
      closeSelectMenu(); closeCalendar();
      overlay.classList.remove("hidden");
      overlay.setAttribute("aria-hidden", "false");
      input.value = ""; sel = 0; render();
      setTimeout(() => input.focus(), 10);
    }
    function close() { overlay.classList.add("hidden"); overlay.setAttribute("aria-hidden", "true"); }
    function pick(i) { const it = results[i]; if (!it) return; close(); it.btn.click(); }

    trigger && trigger.addEventListener("click", open);
    input.addEventListener("input", () => { sel = 0; render(); });
    list.addEventListener("mousemove", (e) => { const x = e.target.closest(".cmdk-item"); if (x && +x.dataset.i !== sel) { sel = +x.dataset.i; render(); } });
    list.addEventListener("click", (e) => { const x = e.target.closest(".cmdk-item"); if (x) pick(+x.dataset.i); });
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
    doc.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        isOpen() ? close() : open();
        return;
      }
      if (!isOpen()) return;
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); sel = (sel + 1) % Math.max(results.length, 1); render(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); sel = (sel - 1 + results.length) % Math.max(results.length, 1); render(); }
      else if (e.key === "Enter") { e.preventDefault(); pick(sel); }
    });
  }

  /* ── 4. 页面说明 ─────────────────────────────────────── */

  const PAGE_DESC = {
    assign: "为大版本需求批量创建、发布与调整禅道测试任务",
    "task-board": "按状态追踪禅道任务进度",
    feedback: "汇总内外部反馈，跟踪处理进度",
    "field-test": "外业采集测试的计划、设备与结果记录",
    "build-records": "各版本构建包与发布记录",
    "testcase-center": "按功能树组织禅道用例，支持审查与关联",
    report: "版本质量、缺陷趋势与团队效率",
    activity: "团队动态与关键事件",
    data: "用户、版本、产品等基础数据维护",
    dispatch: "跨团队特派 Bug 的跟踪与处理",
    "zentao-ai": "基于需求描述，用 AI 生成禅道测试用例",
    jenkins: "自动化任务的执行状态与历史",
    "cad-test": "CAD 模块测试数据统计与异常追踪",
    tools: "数据库对比、文件处理等实用工具",
    terminal: "测试设备的远程终端与状态",
    learning: "培训资料、考核与成绩榜",
  };
  function injectPageDescriptions() {
    Object.entries(PAGE_DESC).forEach(([name, text]) => {
      const h2 = $(`#tab-${name} > h2`);
      if (!h2) return;
      const next = h2.nextElementSibling;
      if (next && (next.classList.contains("page-desc") || next.classList.contains("muted"))) return;
      const p = doc.createElement("p");
      p.className = "page-desc";
      p.textContent = text;
      h2.insertAdjacentElement("afterend", p);
    });
  }

  /* ── 5. 自绘下拉面板 ─────────────────────────────────
     不替换、不包裹原生 <select>：仅在 mousedown / 展开快捷键时阻止系统弹层，
     改为显示自绘面板；选择后写回 selectedIndex 并派发 input + change。
     页面布局、内联样式、显示隐藏逻辑、select-memory 等全部照旧。
     触屏设备（pointer: coarse）保留系统选择器；data-native-select 可逐个退出。 */

  let menu = null, menuSel = null, menuAct = -1, menuItems = [];

  function eligibleSelect(el) {
    return !!el && el.tagName === "SELECT" && !el.multiple && !(el.size > 1) && !el.disabled
      && finePointer.matches && !el.closest("[data-native-select]");
  }
  function optionLabel(o) { return (o.label || o.text || "").trim(); }
  function enabledAt(i) { const o = menuSel && menuSel.options[menuItems[i]]; return !!o && !o.disabled; }
  function nextEnabled(from, dir) {
    for (let k = 0; k < menuItems.length; k++) {
      const i = (from + dir * k + menuItems.length * 2) % menuItems.length;
      if (enabledAt(i)) return i;
    }
    return -1;
  }
  function setMenuActive(i, scroll) {
    menuAct = i;
    $$(".xopt", menu).forEach((el) => el.classList.toggle("act", +el.dataset.i === i));
    if (scroll) { const a = $(".xopt.act", menu); a && a.scrollIntoView({ block: "nearest" }); }
  }
  function buildMenu(q) {
    q = (q || "").trim().toLowerCase();
    menuItems = [];
    let html = "", lastGroup = null;
    Array.from(menuSel.options).forEach((o, idx) => {
      if (o.hidden || o.style.display === "none") return;
      const label = optionLabel(o);
      if (q && !label.toLowerCase().includes(q)) return;
      const g = o.parentElement && o.parentElement.tagName === "OPTGROUP" ? o.parentElement.label : null;
      if (g && g !== lastGroup) html += `<div class="xgroup">${esc(g)}</div>`;
      lastGroup = g;
      const i = menuItems.push(idx) - 1;
      const isSel = idx === menuSel.selectedIndex;
      html += `<div class="xopt${isSel ? " sel" : ""}${o.disabled ? " dis" : ""}" role="option" aria-selected="${isSel}" data-i="${i}">${ic("check", "ui-ic ck")}<span>${esc(label) || "&nbsp;"}</span></div>`;
    });
    $(".xmenu-list", menu).innerHTML = html || `<div class="xempty">无匹配项</div>`;
    const s = menuItems.indexOf(menuSel.selectedIndex);
    setMenuActive(s >= 0 ? s : nextEnabled(0, 1), true);
  }
  function placePopup(pop, anchor, minWidth) {
    const r = anchor.getBoundingClientRect();
    if (minWidth) pop.style.minWidth = Math.max(r.width, minWidth) + "px";
    const mh = pop.offsetHeight, mw = pop.offsetWidth;
    const below = window.innerHeight - r.bottom - 8;
    const up = below < mh && r.top > below;
    pop.classList.toggle("up", up);
    pop.style.top = Math.max(8, up ? r.top - mh - 6 : r.bottom + 6) + "px";
    pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - mw - 8)) + "px";
  }
  function openSelectMenu(sel) {
    closeSelectMenu(); closeCalendar();
    menuSel = sel;
    sel.classList.add("xsel-open");
    menu = doc.createElement("div");
    menu.id = "omniqaSelectMenu";
    menu.setAttribute("role", "listbox");
    const searchable = sel.options.length > 8;
    menu.innerHTML = (searchable ? `<div class="xmenu-search">${ic("search")}<input type="text" placeholder="筛选…" autocomplete="off" aria-label="筛选选项"></div>` : "")
      + `<div class="xmenu-list"></div>`;
    doc.body.appendChild(menu);
    buildMenu("");
    placePopup(menu, sel, 160);
    const inp = $("input", menu);
    if (inp) {
      inp.addEventListener("input", () => { buildMenu(inp.value); placePopup(menu, sel); });
      setTimeout(() => inp.focus(), 0);
    }
    menu.addEventListener("mousedown", (e) => { if (e.target.tagName !== "INPUT") e.preventDefault(); });
    menu.addEventListener("mousemove", (e) => { const o = e.target.closest(".xopt:not(.dis)"); if (o && +o.dataset.i !== menuAct) setMenuActive(+o.dataset.i, false); });
    menu.addEventListener("click", (e) => { const o = e.target.closest(".xopt:not(.dis)"); if (o) chooseOption(+o.dataset.i); });
  }
  function chooseOption(i) {
    if (i < 0 || !enabledAt(i)) return;
    const sel = menuSel, idx = menuItems[i], changed = sel.selectedIndex !== idx;
    closeSelectMenu();
    sel.focus({ preventScroll: true });
    if (!changed) return;
    sel.selectedIndex = idx;
    sel.dispatchEvent(new Event("input", { bubbles: true }));
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function closeSelectMenu() {
    if (!menu) return;
    menu.remove();
    if (menuSel) menuSel.classList.remove("xsel-open");
    menu = null; menuSel = null; menuAct = -1; menuItems = [];
  }

  function initSelectMenus() {
    doc.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      const sel = e.target && e.target.closest ? e.target.closest("select") : null;
      if (!eligibleSelect(sel)) return;
      e.preventDefault(); // 阻止系统弹层
      if (menuSel === sel) { closeSelectMenu(); sel.focus({ preventScroll: true }); return; }
      sel.focus({ preventScroll: true });
      openSelectMenu(sel);
    }, true);
    doc.addEventListener("keydown", (e) => {
      if (!menu) {
        const sel = e.target;
        const opener = e.key === " " || e.key === "F4" || (e.altKey && (e.key === "ArrowDown" || e.key === "ArrowUp"));
        if (opener && eligibleSelect(sel)) { e.preventDefault(); openSelectMenu(sel); }
        return;
      }
      const inSearch = e.target && e.target.closest && e.target.closest("#omniqaSelectMenu");
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); const s = menuSel; closeSelectMenu(); s && s.focus({ preventScroll: true }); }
      else if (e.key === "ArrowDown") { e.preventDefault(); e.stopPropagation(); setMenuActive(nextEnabled(menuAct + 1, 1), true); }
      else if (e.key === "ArrowUp") { e.preventDefault(); e.stopPropagation(); setMenuActive(nextEnabled(menuAct - 1, -1), true); }
      else if (e.key === "Home" && !inSearch) { e.preventDefault(); setMenuActive(nextEnabled(0, 1), true); }
      else if (e.key === "End" && !inSearch) { e.preventDefault(); setMenuActive(nextEnabled(menuItems.length - 1, -1), true); }
      else if (e.key === "Enter" || (e.key === " " && !inSearch)) { e.preventDefault(); e.stopPropagation(); chooseOption(menuAct); }
      else if (e.key === "Tab") closeSelectMenu();
    }, true);
  }

  /* ── 6. 自绘日历 ──────────────────────────────────────
     原生 <input type="date"> 保留（可直接键入年月日），仅把系统日历换成自绘面板：
     点击输入框或 Alt+↓ / F4 打开；值仍为 YYYY-MM-DD，选择后派发 input + change。
     支持 min / max，以及 data-range-role="start|end" + data-range-pair="<对端 id>"
     的起止联动（区间高亮 + 互为上下限）。 */

  const WEEK = ["一", "二", "三", "四", "五", "六", "日"];
  const WEEK_FULL = ["日", "一", "二", "三", "四", "五", "六"];
  let cal = null, calInp = null, view = null;

  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (d) => (d ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` : "");
  function parseDate(s) {
    const m = String(s || "").trim().match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (!m) return null;
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    return d.getFullYear() === +m[1] && d.getMonth() === +m[2] - 1 && d.getDate() === +m[3] ? d : null;
  }
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  function addMonths(d, n) {
    const t = new Date(d.getFullYear(), d.getMonth() + n, 1);
    const last = new Date(t.getFullYear(), t.getMonth() + 1, 0).getDate();
    return new Date(t.getFullYear(), t.getMonth(), Math.min(d.getDate(), last));
  }
  const today = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate()); };
  const pairOf = (inp) => (inp.dataset.rangePair ? doc.getElementById(inp.dataset.rangePair) : null);
  function bounds(inp) {
    let min = parseDate(inp.min), max = parseDate(inp.max);
    const p = pairOf(inp), pv = p && parseDate(p.value);
    if (pv && inp.dataset.rangeRole === "start" && (!max || pv < max)) max = pv;
    if (pv && inp.dataset.rangeRole === "end" && (!min || pv > min)) min = pv;
    return { min, max };
  }
  function allowed(inp, d) { const b = bounds(inp); return !(b.min && d < b.min) && !(b.max && d > b.max); }
  function rangeOf(inp) {
    const p = pairOf(inp);
    if (!p) return {};
    const a = parseDate(inp.value), b = parseDate(p.value);
    return inp.dataset.rangeRole === "end" ? { s: b, e: a } : { s: a, e: b };
  }
  function eligibleDate(el) {
    return !!el && el.tagName === "INPUT" && el.type === "date" && !el.disabled && !el.readOnly
      && finePointer.matches && !el.closest("[data-native-date]");
  }

  function openCalendar(inp) {
    closeCalendar(); closeSelectMenu();
    calInp = inp;
    inp.classList.add("xdate-open");
    const b = bounds(inp);
    let f = parseDate(inp.value) || today();
    if (b.max && f > b.max) f = b.max;
    if (b.min && f < b.min) f = b.min;
    view = { y: f.getFullYear(), m: f.getMonth(), mode: "days", focus: f };
    cal = doc.createElement("div");
    cal.id = "omniqaCalendar";
    cal.setAttribute("role", "dialog");
    cal.setAttribute("aria-label", "选择日期");
    doc.body.appendChild(cal);
    renderCalendar();
    placePopup(cal, inp);
    cal.addEventListener("mousedown", (e) => e.preventDefault());
    cal.addEventListener("click", onCalendarClick);
    cal.addEventListener("keydown", onCalendarKey);
  }
  function renderCalendar() {
    const inp = calInp, sel = parseDate(inp.value), t = today(), { s, e } = rangeOf(inp);
    cal.classList.toggle("months", view.mode === "months");
    const head = (title, unit) => `<div class="xcal-head">`
      + `<button type="button" class="xcal-nav prev" data-act="prev" aria-label="上一${unit}">${ic("chevron-right")}</button>`
      + `<div class="xcal-title"><button type="button" data-act="mode">${title}${ic("chevron-down")}</button></div>`
      + `<button type="button" class="xcal-nav" data-act="next" aria-label="下一${unit}">${ic("chevron-right")}</button></div>`;
    let html = "";
    if (view.mode === "days") {
      html += head(`${view.y}年${view.m + 1}月`, "月");
      html += `<div class="xcal-week">${WEEK.map((w) => `<span>${w}</span>`).join("")}</div><div class="xcal-grid" role="grid">`;
      const first = new Date(view.y, view.m, 1);
      const start = addDays(first, -((first.getDay() + 6) % 7));
      for (let i = 0; i < 42; i++) {
        const d = addDays(start, i), k = fmt(d), cls = ["xday"];
        if (d.getMonth() !== view.m) cls.push("out");
        if (k === fmt(t)) cls.push("today");
        if (sel && k === fmt(sel)) cls.push("sel");
        if (s && e && d > s && d < e) cls.push("in-range");
        if (s && k === fmt(s)) cls.push("r-start");
        if (e && k === fmt(e)) cls.push("r-end");
        const ok = allowed(inp, d);
        if (!ok) cls.push("dis");
        const focus = k === fmt(view.focus);
        html += `<div class="${cls.join(" ")}" data-d="${k}" tabindex="${focus ? 0 : -1}" role="gridcell" aria-selected="${cls.includes("sel")}"${ok ? "" : ' aria-disabled="true"'} aria-label="${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 星期${WEEK_FULL[d.getDay()]}">${d.getDate()}</div>`;
      }
      html += `</div>`;
    } else {
      html += head(`${view.y}年`, "年");
      html += `<div class="xcal-months">`;
      for (let m = 0; m < 12; m++) {
        const cls = ["xmon"];
        if (sel && sel.getFullYear() === view.y && sel.getMonth() === m) cls.push("sel");
        if (t.getFullYear() === view.y && t.getMonth() === m) cls.push("now");
        html += `<div class="${cls.join(" ")}" data-m="${m}" tabindex="${m === view.m ? 0 : -1}" role="button">${m + 1}月</div>`;
      }
      html += `</div>`;
    }
    const todayOk = allowed(inp, t);
    html += `<div class="xcal-foot"><button type="button" data-act="today"${todayOk ? "" : " disabled"}>今天</button>`
      + (inp.required ? `<span class="xcal-hint">也可直接在输入框键入</span>` : `<button type="button" class="muted" data-act="clear">清除</button>`)
      + `</div>`;
    cal.innerHTML = html;
  }
  function focusCalendarCell() { const c = cal && $('.xday[tabindex="0"], .xmon[tabindex="0"]', cal); c && c.focus(); }
  function setDateValue(inp, v) {
    if (inp.value === v) return;
    inp.value = v;
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    inp.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function pickDate(d) { const inp = calInp; closeCalendar(); setDateValue(inp, fmt(d)); inp.focus({ preventScroll: true }); }
  function onCalendarClick(e) {
    const day = e.target.closest(".xday"), mon = e.target.closest(".xmon"), act = e.target.closest("[data-act]");
    if (day) { if (!day.classList.contains("dis")) pickDate(parseDate(day.dataset.d)); return; }
    if (mon) {
      view.m = +mon.dataset.m; view.mode = "days";
      view.focus = new Date(view.y, view.m, Math.min(view.focus.getDate(), 28));
      renderCalendar(); return;
    }
    if (!act || act.disabled) return;
    const a = act.dataset.act;
    if (a === "prev" || a === "next") {
      const n = a === "prev" ? -1 : 1;
      if (view.mode === "days") {
        const t = new Date(view.y, view.m + n, 1);
        view.y = t.getFullYear(); view.m = t.getMonth(); view.focus = addMonths(view.focus, n);
      } else view.y += n;
      renderCalendar();
    } else if (a === "mode") { view.mode = view.mode === "days" ? "months" : "days"; renderCalendar(); }
    else if (a === "today") pickDate(today());
    else if (a === "clear") { const inp = calInp; closeCalendar(); setDateValue(inp, ""); inp.focus({ preventScroll: true }); }
  }
  function onCalendarKey(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); const inp = calInp; closeCalendar(); inp && inp.focus({ preventScroll: true }); return; }
    const t = e.target;
    if (t.classList && t.classList.contains("xmon") && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); t.click(); focusCalendarCell(); return; }
    if (!(t.classList && t.classList.contains("xday"))) return;
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
    let f = null;
    if (step) f = addDays(view.focus, step);
    else if (e.key === "PageUp" || e.key === "PageDown") f = addMonths(view.focus, (e.key === "PageUp" ? -1 : 1) * (e.shiftKey ? 12 : 1));
    else if (e.key === "Home") f = addDays(view.focus, -((view.focus.getDay() + 6) % 7));
    else if (e.key === "End") f = addDays(view.focus, 6 - ((view.focus.getDay() + 6) % 7));
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (allowed(calInp, view.focus)) pickDate(view.focus); return; }
    if (!f) return;
    e.preventDefault();
    view.focus = f; view.y = f.getFullYear(); view.m = f.getMonth();
    renderCalendar(); focusCalendarCell();
  }
  function closeCalendar() {
    if (!cal) return;
    cal.remove();
    if (calInp) calInp.classList.remove("xdate-open");
    cal = null; calInp = null; view = null;
  }

  function initCalendars() {
    if (finePointer.matches) root.classList.add("x-pickers");
    doc.addEventListener("click", (e) => {
      const inp = e.target;
      if (eligibleDate(inp) && calInp !== inp) openCalendar(inp);
    }, true);
    doc.addEventListener("keydown", (e) => {
      const inp = e.target;
      if (!eligibleDate(inp)) return;
      if (e.key === "F4" || (e.altKey && (e.key === "ArrowDown" || e.key === "ArrowUp"))) {
        e.preventDefault();
        if (calInp !== inp) openCalendar(inp);
        focusCalendarCell();
      } else if ((e.key === "Escape" || e.key === "Enter") && calInp === inp) {
        if (e.key === "Escape") e.preventDefault();
        closeCalendar();
      } else if (e.key === "Tab" && calInp === inp) closeCalendar();
    }, true);
    // 键入日期时日历跟随跳转
    doc.addEventListener("input", (e) => {
      if (!cal || e.target !== calInp) return;
      const d = parseDate(calInp.value);
      if (d) { view.y = d.getFullYear(); view.m = d.getMonth(); view.focus = d; view.mode = "days"; }
      renderCalendar();
    });
  }

  /* 两类浮层共用的关闭时机：点外部 / 焦点移出 / 滚动 / 窗口变化 */
  function initPopupDismiss() {
    const inside = (t) => (menu && (menu.contains(t) || t === menuSel)) || (cal && (cal.contains(t) || t === calInp));
    doc.addEventListener("mousedown", (e) => {
      if (menu && !menu.contains(e.target) && e.target !== menuSel) closeSelectMenu();
      if (cal && !cal.contains(e.target) && e.target !== calInp) closeCalendar();
    }, true);
    doc.addEventListener("focusin", (e) => { if ((menu || cal) && !inside(e.target)) { closeSelectMenu(); closeCalendar(); } });
    window.addEventListener("scroll", (e) => {
      const t = e.target;
      if (menu && !(t instanceof Node && menu.contains(t))) closeSelectMenu();
      if (cal && !(t instanceof Node && cal.contains(t))) closeCalendar();
    }, true);
    window.addEventListener("resize", () => { closeSelectMenu(); closeCalendar(); });
    window.addEventListener("blur", () => { closeSelectMenu(); closeCalendar(); });
  }

  /* ── 7. 展开态需求卡：GPU 旋转流光层 ───────────────── */

  function initOpenCardRing() {
    doc.addEventListener("mouseover", (e) => {
      const card = e.target.closest && e.target.closest("#tab-mine details.mine-req-card[open], #tab-retest details.retest-req-card[open]");
      if (!card) return;
      let ring = card.querySelector(":scope > .lift-ring");
      if (!ring) {
        ring = doc.createElement("span");
        ring.className = "lift-ring";
        ring.setAttribute("aria-hidden", "true");
        ring.appendChild(doc.createElement("i"));
        card.appendChild(ring);
      }
      const d = Math.ceil(Math.hypot(card.offsetWidth, card.offsetHeight)) + 8;
      if (ring.dataset.d !== String(d)) {
        ring.dataset.d = String(d);
        ring.style.setProperty("--d", d + "px");
      }
    }, { passive: true });
  }

  /* ── 启动 ────────────────────────────────────────────── */

  function init() {
    const steps = [initNav, initCrumb, initUserMenu, initCommandPalette, injectPageDescriptions,
      initSelectMenus, initCalendars, initPopupDismiss, initOpenCardRing];
    steps.forEach((fn) => {
      try { fn(); } catch (err) { console.error("[OmniQA shell] init failed:", fn.name, err); }
    });
  }

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init);
  else init();

  window.OmniQAShell = { closeSelectMenu, closeCalendar, updateCrumb, syncNavGroups };
})();
