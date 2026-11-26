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
 *  8. 主题色：账户菜单内的取色面板（预设 + 自定义），推导出 --w-accent* 写到 <html>
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
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* 同上 */ } },
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
    tools: "tabToolsBtn", bugly: "tabBuglyBtn", "competitor-analysis": "tabCompetitorAnalysisBtn",
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
      if (!on) showThemeView(false);
    };
    btn.addEventListener("click", (e) => { e.stopPropagation(); setOpen(pop.classList.contains("hidden")); });
    // 主题色入口及面板内的按钮不关闭菜单
    pop.addEventListener("click", (e) => {
      if (e.target.closest("button") && !e.target.closest("[data-keep-open], .theme-panel")) setOpen(false);
    });
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

  /* 2026-11-25 起每个页面的标题区直接写在 index.html 的 .ph 头部里（图标 + 标题 + 说明 + 操作），
     不再由脚本注入说明文字。 */

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

  /* ── 8. 主题色 ─────────────────────────────────────────
     用户选一个"基色"，这里在 OKLCH 空间推导出整套 token：
       --w-accent        按钮底色（保证白字对比 ≥ 4.5，不够就保持色相降明度）
       --w-accent-hover  悬停（再深一档；极深色则反向提亮）
       --w-accent-ink    作文字用（对暖灰画布对比 ≥ 4.6）
       --w-accent-rgb    浅底染色 / 光晕用的中等明度版本（r, g, b）
       --w-accent-2      渐变亮端（头像、侧栏选中竖线）
     存 localStorage omniqa.accent = { base, vars }；index.html head 内联脚本首帧写回。 */

  const ACCENT_KEY = "omniqa.accent";
  const RECENT_KEY = "omniqa.accentRecent";
  const ACCENT_VARS = ["--w-accent", "--w-accent-hover", "--w-accent-ink", "--w-accent-rgb", "--w-accent-2"];
  const DEFAULT_BASE = "#ea580c";
  const CANVAS_RGB = [244, 243, 241]; // --w-canvas
  const THEME_GROUPS = [
    { label: "鲜明", items: [
      ["胭脂", "#e11d48"], ["朱红", "#dc2626"], ["柿橙（默认）", DEFAULT_BASE], ["琥珀", "#d97706"],
      ["姜黄", "#ca8a04"], ["青柠", "#65a30d"], ["翠绿", "#16a34a"], ["翡翠", "#059669"],
      ["松石", "#0d9488"], ["青碧", "#0891b2"], ["天蓝", "#0284c7"], ["宝蓝", "#2563eb"],
      ["靛青", "#4f46e5"], ["堇紫", "#7c3aed"], ["洋红", "#c026d3"], ["桃红", "#db2777"]] },
    { label: "沉稳", items: [
      ["酒红", "#881337"], ["赭石", "#9a3412"], ["古铜", "#854d0e"], ["墨绿", "#166534"],
      ["黛青", "#115e59"], ["藏蓝", "#1e3a8a"], ["绛紫", "#581c87"], ["炭黑", "#292524"]] },
    { label: "柔和", items: [
      ["豆沙", "#b5727a"], ["陶土", "#b87355"], ["卡其", "#a08a5c"], ["苔绿", "#7d8f5a"],
      ["灰绿", "#5f8a7c"], ["雾蓝", "#6b8bae"], ["烟紫", "#8a7ca8"], ["岩灰", "#78716c"]] },
  ];

  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  function hexToRgb(hex) {
    let h = String(hex || "").trim().replace(/^#/, "");
    if (/^[0-9a-f]{3}$/i.test(h)) h = h.split("").map((c) => c + c).join("");
    if (!/^[0-9a-f]{6}$/i.test(h)) return null;
    const n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const rgbToHex = (rgb) => "#" + rgb.map((v) => Math.round(clamp(v, 0, 255)).toString(16).padStart(2, "0")).join("");
  const normHex = (hex) => { const rgb = hexToRgb(hex); return rgb ? rgbToHex(rgb) : null; };
  const toLin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  const fromLin = (v) => 255 * (v <= 0.0031308 ? 12.92 * v : 1.055 * Math.pow(v, 1 / 2.4) - 0.055);
  const luminance = (rgb) => 0.2126 * toLin(rgb[0]) + 0.7152 * toLin(rgb[1]) + 0.0722 * toLin(rgb[2]);
  const contrast = (a, b) => { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

  function rgbToOklch(rgb) {
    const [r, g, b] = rgb.map(toLin);
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    const L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;
    const A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s;
    const B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s;
    return [L, Math.hypot(A, B), Math.atan2(B, A)];
  }
  function oklchToLinear(L, C, H) {
    const A = C * Math.cos(H), B = C * Math.sin(H);
    const l = Math.pow(L + 0.3963377774 * A + 0.2158037573 * B, 3);
    const m = Math.pow(L - 0.1055613458 * A - 0.0638541728 * B, 3);
    const s = Math.pow(L - 0.0894841775 * A - 1.2914855480 * B, 3);
    return [
      4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    ];
  }
  /** OKLCH → sRGB；超出色域时保持明度与色相、收缩彩度 */
  function oklchToRgb(L, C, H) {
    L = clamp(L, 0, 1);
    let c = C, lin = oklchToLinear(L, c, H);
    for (let i = 0; i < 24 && lin.some((v) => v < -0.0005 || v > 1.0005); i++) { c *= 0.9; lin = oklchToLinear(L, c, H); }
    return lin.map((v) => fromLin(clamp(v, 0, 1)));
  }

  function deriveAccent(baseHex) {
    const base = hexToRgb(baseHex) || hexToRgb(DEFAULT_BASE);
    const [L, C, H] = rgbToOklch(base);
    const white = [255, 255, 255];
    let La = L;
    while (La > 0.18 && contrast(white, oklchToRgb(La, C, H)) < 4.55) La -= 0.005;
    const accent = oklchToRgb(La, C, H);
    const hover = oklchToRgb(La < 0.36 ? La + 0.07 : La - 0.065, C, H);
    let Li = Math.min(La, 0.62);
    while (Li > 0.15 && contrast(CANVAS_RGB, oklchToRgb(Li, C, H)) < 4.6) Li -= 0.005;
    const ink = oklchToRgb(Li, C, H);
    const tint = oklchToRgb(clamp(L, 0.56, 0.72), Math.max(C, 0.02), H);
    const light = oklchToRgb(Math.min(La + 0.13, 0.86), C * 0.95, H);
    return {
      base: rgbToHex(base),
      adjusted: L - La > 0.015,
      vars: {
        "--w-accent": rgbToHex(accent),
        "--w-accent-hover": rgbToHex(hover),
        "--w-accent-ink": rgbToHex(ink),
        "--w-accent-rgb": tint.map((v) => Math.round(v)).join(", "),
        "--w-accent-2": rgbToHex(light),
      },
    };
  }

  function rgbToHsv([r, g, b]) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0;
    if (d) {
      if (max === r) h = ((g - b) / d) % 6;
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60; if (h < 0) h += 360;
    }
    return [h, max ? d / max : 0, max];
  }
  function hsvToRgb(h, s, v) {
    const f = (n) => { const k = (n + h / 60) % 6; return 255 * (v - v * s * Math.max(0, Math.min(k, 4 - k, 1))); };
    return [f(5), f(3), f(1)];
  }

  function readSavedAccent() {
    try {
      const saved = JSON.parse(store.get(ACCENT_KEY) || "null");
      return saved && normHex(saved.base) ? saved : null;
    } catch (e) { return null; }
  }
  function applyAccentVars(vars) {
    ACCENT_VARS.forEach((k) => {
      if (vars && vars[k]) root.style.setProperty(k, vars[k]);
      else root.style.removeProperty(k);
    });
  }

  let showThemeView = () => {};

  function initThemePicker() {
    const pop = $("#userMenuPop"), panel = $("#themePanel"), entry = $("#themeMenuBtn");
    if (!pop || !panel || !entry) return;

    let current = (readSavedAccent() || {}).base || DEFAULT_BASE;
    let hsv = rgbToHsv(hexToRgb(current));
    let rendered = false, raf = 0, pending = null;

    const recent = () => {
      try { return (JSON.parse(store.get(RECENT_KEY) || "[]") || []).map(normHex).filter(Boolean).slice(0, 8); }
      catch (e) { return []; }
    };
    const pushRecent = (hex) => {
      const isPreset = THEME_GROUPS.some((g) => g.items.some(([, h]) => h === hex));
      if (isPreset) return;
      store.set(RECENT_KEY, JSON.stringify([hex].concat(recent().filter((h) => h !== hex)).slice(0, 8)));
    };

    /** 应用基色；persist=false 用于拖动中的实时预览 */
    function setAccent(hex, { persist = true, fromPicker = false } = {}) {
      hex = normHex(hex);
      if (!hex) return;
      current = hex;
      if (!fromPicker) hsv = rgbToHsv(hexToRgb(hex));
      const d = deriveAccent(hex);
      if (hex === DEFAULT_BASE) {
        applyAccentVars(null); // 默认橙走 weave.css 里手调过的原值
        if (persist) store.del(ACCENT_KEY);
      } else {
        applyAccentVars(d.vars);
        if (persist) { store.set(ACCENT_KEY, JSON.stringify({ base: hex, vars: d.vars })); pushRecent(hex); }
      }
      if (rendered) syncPanel(d);
    }
    const previewAccent = (hex) => {
      pending = hex;
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; setAccent(pending, { persist: false, fromPicker: true }); });
    };

    function swatch(name, hex, extraCls = "") {
      const shown = hex === DEFAULT_BASE ? "#cc4e0b" : deriveAccent(hex).vars["--w-accent"];
      return `<button type="button" class="tp-swatch${extraCls}" data-hex="${hex}" style="--sw:${shown}" title="${esc(name)}" aria-label="${esc(name)}"></button>`;
    }

    function render() {
      const eye = "EyeDropper" in window;
      panel.innerHTML = `
        <div class="tp-head">
          <button type="button" class="tp-back" aria-label="返回账户菜单">${ic("chevron-left")}</button>
          <strong>主题色</strong>
          <button type="button" class="tp-reset">恢复默认</button>
        </div>
        <div class="tp-preview" aria-hidden="true">
          <span class="tp-pv-btn">主要按钮</span>
          <span class="tp-pv-chip">选中态</span>
          <span class="tp-pv-link">链接文字</span>
        </div>
        ${THEME_GROUPS.map((g) => `
          <div class="tp-group">
            <div class="tp-label">${esc(g.label)}</div>
            <div class="tp-grid">${g.items.map(([n, h]) => swatch(n, h)).join("")}</div>
          </div>`).join("")}
        <div class="tp-group tp-custom">
          <div class="tp-label">自定义</div>
          <div class="tp-sv" tabindex="0" role="slider" aria-label="饱和度与亮度（方向键调整）"><i class="tp-sv-knob"></i></div>
          <div class="tp-hue" tabindex="0" role="slider" aria-label="色相（方向键调整）" aria-valuemin="0" aria-valuemax="360"><i class="tp-hue-knob"></i></div>
          <div class="tp-row">
            <span class="tp-cur" aria-hidden="true"></span>
            <label class="tp-hex"><span>#</span><input type="text" maxlength="7" spellcheck="false" autocomplete="off" aria-label="十六进制颜色"></label>
            ${eye ? `<button type="button" class="tp-eye" title="从屏幕取色" aria-label="从屏幕取色">${ic("pipette")}</button>` : ""}
          </div>
          <div class="tp-recent-wrap"><span class="tp-label">最近</span><div class="tp-recent"></div></div>
          <div class="tp-note" aria-live="polite"></div>
        </div>`;
      rendered = true;
      bindPanel();
      syncPanel(deriveAccent(current));
    }

    function syncPanel(d) {
      $$(".tp-grid .tp-swatch", panel).forEach((b) => {
        const on = b.dataset.hex === current;
        b.classList.toggle("is-on", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      const [h, s, v] = hsv;
      const sv = $(".tp-sv", panel), hue = $(".tp-hue", panel);
      sv.style.setProperty("--hue", `hsl(${h} 100% 50%)`);
      $(".tp-sv-knob", panel).style.cssText = `left:${s * 100}%;top:${(1 - v) * 100}%;background:${current}`;
      $(".tp-hue-knob", panel).style.cssText = `left:${(h / 360) * 100}%;background:hsl(${h} 100% 50%)`;
      sv.setAttribute("aria-valuetext", `饱和度 ${Math.round(s * 100)}%，亮度 ${Math.round(v * 100)}%`);
      hue.setAttribute("aria-valuenow", String(Math.round(h)));
      $(".tp-cur", panel).style.background = current;
      const inp = $(".tp-hex input", panel);
      if (doc.activeElement !== inp) { inp.value = current.slice(1).toUpperCase(); inp.classList.remove("is-bad"); }
      const rec = recent();
      $(".tp-recent-wrap", panel).hidden = !rec.length;
      $(".tp-recent", panel).innerHTML = rec.map((hx) => swatch(hx.toUpperCase(), hx, hx === current ? " is-on" : "")).join("");
      $(".tp-reset", panel).disabled = current === DEFAULT_BASE;
      $(".tp-note", panel).textContent = d.adjusted && current !== DEFAULT_BASE
        ? `按钮色已自动加深为 ${d.vars["--w-accent"].toUpperCase()}，保证白色文字清晰可读`
        : "";
    }

    function dragArea(el, onPoint) {
      el.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        el.focus({ preventScroll: true });
        el.setPointerCapture(e.pointerId);
        const move = (ev) => {
          const r = el.getBoundingClientRect();
          onPoint(clamp((ev.clientX - r.left) / r.width, 0, 1), clamp((ev.clientY - r.top) / r.height, 0, 1));
        };
        const up = () => {
          el.removeEventListener("pointermove", move);
          el.removeEventListener("pointerup", up);
          el.removeEventListener("pointercancel", up);
          if (raf) { cancelAnimationFrame(raf); raf = 0; }
          setAccent(fromHsv(), { fromPicker: true }); // 松手才持久化
        };
        el.addEventListener("pointermove", move);
        el.addEventListener("pointerup", up);
        el.addEventListener("pointercancel", up);
        move(e);
      });
    }
    const fromHsv = () => rgbToHex(hsvToRgb(hsv[0], hsv[1], hsv[2]));

    function bindPanel() {
      $(".tp-back", panel).addEventListener("click", () => { showThemeView(false); entry.focus(); });
      $(".tp-reset", panel).addEventListener("click", () => setAccent(DEFAULT_BASE));
      panel.addEventListener("click", (e) => {
        const sw = e.target.closest(".tp-swatch");
        if (sw) setAccent(sw.dataset.hex);
      });
      const sv = $(".tp-sv", panel), hue = $(".tp-hue", panel);
      dragArea(sv, (x, y) => { hsv = [hsv[0], x, 1 - y]; previewAccent(fromHsv()); });
      dragArea(hue, (x) => { hsv = [x * 360, hsv[1], hsv[2]]; previewAccent(fromHsv()); });
      const keyStep = (el, fn) => el.addEventListener("keydown", (e) => {
        const map = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
        if (!map[e.key]) return;
        e.preventDefault();
        fn(map[e.key], e.shiftKey ? 5 : 1);
        setAccent(fromHsv(), { fromPicker: true });
      });
      keyStep(sv, ([dx, dy], k) => { hsv = [hsv[0], clamp(hsv[1] + dx * 0.02 * k, 0, 1), clamp(hsv[2] + dy * 0.02 * k, 0, 1)]; });
      keyStep(hue, ([dx, dy], k) => { hsv = [(hsv[0] + (dx || dy) * 2 * k + 360) % 360, hsv[1], hsv[2]]; });

      const inp = $(".tp-hex input", panel);
      inp.addEventListener("input", () => {
        const raw = inp.value.trim().replace(/^#/, "");
        const hex = /^[0-9a-f]{6}$/i.test(raw) ? normHex(raw) : null;
        inp.classList.toggle("is-bad", !hex && raw.length >= 6);
        if (hex) setAccent(hex);
      });
      inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); const hex = normHex(inp.value); if (hex) setAccent(hex); inp.blur(); }
      });
      inp.addEventListener("blur", () => syncPanel(deriveAccent(current)));

      const eye = $(".tp-eye", panel);
      if (eye) eye.addEventListener("click", async () => {
        try { const r = await new window.EyeDropper().open(); if (r && r.sRGBHex) setAccent(r.sRGBHex); }
        catch (e) { /* 用户按 Esc 取消 */ }
      });
    }

    showThemeView = (on) => {
      if (on && !rendered) render();
      else if (on) syncPanel(deriveAccent(current));
      pop.classList.toggle("is-theme", !!on);
      panel.hidden = !on;
      entry.setAttribute("aria-expanded", on ? "true" : "false");
      if (on) { const b = $(".tp-back", panel); if (b) b.focus({ preventScroll: true }); }
    };
    entry.addEventListener("click", () => showThemeView(true));

    // 其它标签页改了主题色：同步过来
    window.addEventListener("storage", (e) => {
      if (e.key !== ACCENT_KEY) return;
      const saved = readSavedAccent();
      current = saved ? saved.base : DEFAULT_BASE;
      hsv = rgbToHsv(hexToRgb(current));
      applyAccentVars(saved ? saved.vars : null);
      if (rendered) syncPanel(deriveAccent(current));
    });
    // 推导规则升级后，老存档按新规则重算一次
    const saved = readSavedAccent();
    if (saved) setAccent(saved.base);
  }

  /* ── 启动 ────────────────────────────────────────────── */

  function init() {
    const steps = [initNav, initCrumb, initUserMenu, initCommandPalette,
      initSelectMenus, initCalendars, initPopupDismiss, initOpenCardRing, initThemePicker];
    steps.forEach((fn) => {
      try { fn(); } catch (err) { console.error("[OmniQA shell] init failed:", fn.name, err); }
    });
  }

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init);
  else init();

  window.OmniQAShell = { closeSelectMenu, closeCalendar, updateCrumb, syncNavGroups };
})();
