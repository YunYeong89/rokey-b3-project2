/* ================================================================
   관리자 📊 분석 (Analytics Dashboard)  - index.js 다음에 불러옴 ($, won, API, Server, toast, pad2 사용)
   서버가 집계한 결과(/api/analytics/*)만 받아 그림. 그래프는 static/vendor/chart.umd.min.js (Chart.js 4)
   - 탭을 바꾸거나 다시 그릴 때 이전 그래프를 모두 destroy (중복 표시·메모리 누수 방지)
   - 열려 있는 동안 10초마다 /api/analytics/version 만 확인하고, 값이 바뀌었을 때(주문 완료 등)만 다시 불러옴
   ================================================================ */
const Dash = (() => {
  const TABS = [
    ["overview", "Overview", "전체 운영 현황"], ["sales", "Sales", "매출 · 판매"], ["customers", "Customer", "고객 구매 행동"],
    ["inventory", "Inventory", "재고 최적화"], ["recommendations", "Recommendation", "추천 성과"],
    ["packing", "Packing", "포장 계획"], ["robot", "Robot", "로봇 작업"]];
  // 탭마다 쓰는 필터 (overview 는 정해진 기간: 오늘 / 최근 7일 / 누적)
  const USES = {overview: ["source"], sales: ["range", "product", "category", "source", "gran"],
    customers: ["range", "product", "category", "source"], inventory: ["range", "item", "source"],
    recommendations: ["range", "product", "category"], packing: ["range", "source"], robot: ["range", "source"]};
  const RANGES = [["today", "오늘"], ["7d", "최근 7일"], ["30d", "최근 30일"], ["all", "전체"], ["custom", "기간 지정"]];
  const POLL_MS = 10000;

  let tab = "overview", data = null, version = null, timer = null, seq = 0, opts = null, csv = null;
  const F = {range: "30d", start: "", end: "", product: "", category: "", source: "all", gran: "day", item: ""};
  const charts = new Map();

  // ---------------------------------------------------------- 공통 도우미
  const css = n => getComputedStyle(document.querySelector(".an-root") || document.documentElement).getPropertyValue(n).trim();
  const S = i => css(`--s${(i % 8) + 1}`);           // 범주 색 (고정 순서)
  const BRAND = () => css("--brand") || "#0f766e";
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const num = n => n == null ? "-" : Number(n).toLocaleString("ko-KR");
  const krw = n => n == null ? "-" : won(Math.round(n));
  const pct = (r, d = 1) => r == null ? "-" : (r * 100).toFixed(d) + "%";
  const sec = s => s == null ? "-" : s < 60 ? `${(+s).toFixed(1)}초` : `${Math.floor(s / 60)}분 ${Math.round(s % 60)}초`;
  const usd = n => n == null ? "-" : "$" + (+n).toFixed(4);
  const hms = t => {const d = new Date(t * 1000); return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`};
  const md = d => d && d.length >= 10 ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : d;   // 2026-11-27 -> 11/27

  function destroyCharts() {charts.forEach(c => c.destroy()); charts.clear()}

  function kpi(label, value, opt = {}) {
    const na = value == null || value === "-";
    return `<div class="kpi ${opt.cls || ""}"><small>${label}</small>${na
      ? `<b class="na">${opt.na || "데이터 수집 중"}</b>` : `<b>${value}</b>`}${opt.sub ? `<span class="kx">${opt.sub}</span>` : ""}</div>`;
  }
  function collect(c) {   // {status: collecting | not_collected, reason, have, need}
    if (!c) return "";
    const nc = c.status === "not_collected";
    const meter = c.need ? `<div class="meter"><i style="width:${Math.min(100, Math.round((c.have || 0) / c.need * 100))}%"></i></div>
      <small>${c.have ?? 0} / ${c.need}</small>` : "";
    return `<div class="collect ${nc ? "nc" : ""}"><b>${nc ? "⛔ 미수집" : "⏳ 데이터 수집 중"}</b><div>${esc(c.reason)}${meter}</div></div>`;
  }
  const isOk = x => x && x.status === "ok";
  const panel = (title, body, sub = "", cls = "") =>
    `<div class="panel ${cls}"><div class="ph"><h3>${title}</h3>${sub ? `<span class="sub" style="color:var(--muted)">${sub}</span>` : ""}</div>${body}</div>`;
  const box = (id, cls = "") => `<div class="chart-box ${cls}"><canvas id="${id}" role="img"></canvas></div>`;
  const legend = items => `<div class="legend">${items.map(([name, color, dash]) =>
    `<span><i class="${dash ? "dash" : ""}" style="${dash ? `border-color:${color}` : `background:${color}`}"></i>${name}</span>`).join("")}</div>`;
  function table(head, rows, opt = {}) {
    if (!rows.length) return `<div class="empty">${opt.empty || "아직 데이터가 없어요"}</div>`;
    return `<div class="tscroll"><table><thead><tr>${head.map(h => `<th class="${h[1] || ""}">${h[0]}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(r => `<tr>${r.map((v, i) => `<td class="${head[i][1] || ""}">${v}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  // ---------------------------------------------------------- 그래프 (Chart.js)
  function base(extra = {}) {
    const grid = "#efeadf", muted = css("--muted") || "#6b736f";
    return {
      responsive: true, maintainAspectRatio: false, animation: {duration: 250},
      interaction: {mode: "index", intersect: false},
      plugins: {legend: {display: false}, tooltip: {backgroundColor: "#1f2a28", padding: 10, cornerRadius: 10,
        titleFont: {weight: "700"}, bodyFont: {size: 12}, callbacks: extra.tooltip || {}}},
      scales: {
        x: {grid: {display: false}, border: {color: grid}, ticks: {color: muted, font: {size: 11}, maxRotation: 0, autoSkip: true,
          callback: extra.xTick}},
        y: {beginAtZero: true, grid: {color: grid}, border: {display: false},
          ticks: {color: muted, font: {size: 11}, precision: 0, callback: extra.yTick}}}};
  }
  function chart(id, type, labels, datasets, opt = {}) {
    const el = document.getElementById(id);
    if (!el || typeof Chart === "undefined") return;
    if (charts.has(id)) charts.get(id).destroy();
    const o = base(opt);
    if (opt.horizontal) {
      o.indexAxis = "y";
      o.interaction = {mode: "nearest", axis: "y", intersect: false};
      [o.scales.x, o.scales.y] = [o.scales.y, o.scales.x];
      o.scales.x.ticks.callback = opt.yTick;
      o.scales.y.ticks.callback = undefined;
      o.scales.y.ticks.autoSkip = false;
    }
    if (opt.yMax != null) o.scales[opt.horizontal ? "x" : "y"].max = opt.yMax;
    if (opt.stacked) {o.scales.x.stacked = true; o.scales.y.stacked = true}
    const ds = datasets.map((d, i) => {
      const color = d.color || S(i);
      return type === "line"
        ? {borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: labels.length > 31 ? 0 : 3,
          pointHoverRadius: 5, tension: .25, spanGaps: true, borderDash: d.dash ? [6, 4] : undefined, ...d}
        : {backgroundColor: color, borderRadius: 4, borderSkipped: "start", maxBarThickness: 26,
          borderColor: css("--card") || "#fffdf8", borderWidth: opt.stacked ? {top: 2} : 0, ...d};
    });
    charts.set(id, new Chart(el, {type, data: {labels, datasets: ds}, options: o}));
  }
  function donut(id, labels, values, opt = {}) {
    const el = document.getElementById(id);
    if (!el || typeof Chart === "undefined") return;
    if (charts.has(id)) charts.get(id).destroy();
    const total = values.reduce((a, b) => a + b, 0);
    charts.set(id, new Chart(el, {type: "doughnut", data: {labels, datasets: [{data: values,
      backgroundColor: labels.map((_, i) => opt.colors ? opt.colors[i] : S(i)), borderColor: css("--card") || "#fff", borderWidth: 2}]},
      options: {responsive: true, maintainAspectRatio: false, cutout: "62%", animation: {duration: 250},
        plugins: {legend: {position: "right", labels: {boxWidth: 10, boxHeight: 10, font: {size: 12},
          generateLabels: c => c.data.labels.map((l, i) => ({text: `${l}  ${opt.fmt ? opt.fmt(values[i]) : num(values[i])} (${total ? Math.round(values[i] / total * 100) : 0}%)`,
            fillStyle: c.data.datasets[0].backgroundColor[i], strokeStyle: "transparent", index: i}))}},
          tooltip: {backgroundColor: "#1f2a28", callbacks: {label: c => ` ${c.label}: ${opt.fmt ? opt.fmt(c.raw) : num(c.raw)} (${total ? (c.raw / total * 100).toFixed(1) : 0}%)`}}}}}));
  }
  // 히트맵: 한 가지 색의 진하기 (HTML 표, 칸에 마우스를 올리면 값)
  function heatmap(rows, cols, values, opt = {}) {
    const flat = values.flat().filter(v => v != null), max = Math.max(1, ...flat);
    const cell = (v, r, c) => {
      if (v == null) return `<td class="self" title="${esc(rows[r])}"></td>`;
      const a = v / max, bg = `color-mix(in srgb, ${BRAND()} ${Math.round(8 + a * 92)}%, #f4fbf9)`;
      return `<td style="background:${bg};color:${a > .55 ? "#fff" : "var(--ink)"}" title="${esc(opt.title ? opt.title(r, c, v) : `${rows[r]} · ${cols[c]}: ${v}`)}">${opt.show ? (v || "") : ""}</td>`;
    };
    return `<div class="tscroll"><table class="heat"><thead><tr><th></th>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead>
      <tbody>${values.map((row, r) => `<tr><th style="text-align:right">${esc(rows[r])}</th>${row.map((v, c) => cell(v, r, c)).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="heat-legend">0<i></i>${opt.maxLabel ? opt.maxLabel(max) : num(max)}</div>`;
  }

  // ---------------------------------------------------------- 필터 바
  function qs() {
    const p = new URLSearchParams();
    for (const k of USES[tab]) {
      if (k === "range") {p.set("range", F.range); if (F.range === "custom") {p.set("start", F.start); p.set("end", F.end)}}
      else if (k === "item") {if (F.item) p.set("item", F.item)}
      else if (F[k]) p.set(k, F[k]);
    }
    return p.toString();
  }
  function renderFilters() {
    const use = new Set(USES[tab]), o = opts || {products: [], categories: []};
    $("#anFilters").innerHTML = `
      <div class="seg" ${use.has("range") ? "" : "hidden"}>${RANGES.map(([k, t]) => `<button data-range="${k}" class="${F.range === k ? "on" : ""}">${t}</button>`).join("")}</div>
      <span ${use.has("range") && F.range === "custom" ? "" : "hidden"}><input type="date" id="anStart" value="${F.start}"> ~ <input type="date" id="anEnd" value="${F.end}"></span>
      <label ${use.has("product") ? "" : "hidden"}>상품 <select id="anProduct"><option value="">전체</option>${o.products.map(p => `<option value="${p.id}" ${F.product === p.id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      <label ${use.has("item") ? "" : "hidden"}>추이 상품 <select id="anItem"><option value="">전체 합계</option>${o.products.map(p => `<option value="${p.id}" ${F.item === p.id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      <label ${use.has("category") ? "" : "hidden"}>카테고리 <select id="anCategory"><option value="">전체</option>${o.categories.map(c => `<option ${F.category === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
      <label ${use.has("source") ? "" : "hidden"} title="mock = 데모 로봇(--mock)으로 만든 데이터, robot = 실제 로봇">데이터 <select id="anSource">
        ${[["all", "전체"], ["robot", "실제 로봇"], ["mock", "데모(mock)"]].map(([k, t]) => `<option value="${k}" ${F.source === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <div class="r"><span class="upd" id="anUpd">-</span>
        <button class="btn sm ghost" id="anReload" title="지금 다시 불러오기">↻</button>
        <button class="btn sm" id="anCsv">⬇ CSV</button></div>`;
  }
  function wireFilters() {
    const root = $("#anFilters");
    root.addEventListener("click", e => {
      const b = e.target.closest("[data-range]");
      if (b) {
        F.range = b.dataset.range;
        if (F.range === "custom" && !F.start) {const t = new Date(), s = new Date(Date.now() - 6 * 864e5); F.end = ymd(t); F.start = ymd(s)}
        renderFilters(); load(); return;
      }
      if (e.target.id === "anReload") load(true);
      if (e.target.id === "anCsv") downloadCsv();
    });
    root.addEventListener("change", e => {
      const id = e.target.id, v = e.target.value;
      if (id === "anStart") F.start = v; else if (id === "anEnd") F.end = v;
      else if (id === "anProduct") F.product = v; else if (id === "anCategory") F.category = v;
      else if (id === "anSource") F.source = v; else if (id === "anItem") F.item = v;
      else return;
      if ((id === "anStart" || id === "anEnd") && (!F.start || !F.end)) return;
      load();
    });
  }
  const ymd = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

  // ---------------------------------------------------------- 불러오기 / 자동 갱신
  async function load(manual) {
    const my = ++seq;
    if (API !== Server) {
      $("#anBody").innerHTML = collect({status: "not_collected", reason: "분석은 서버(web_server.py)를 켜야 볼 수 있어요. 지금은 브라우저 데모예요"});
      return;
    }
    $("#anBody").style.opacity = .55;
    try {
      const d = await API.analytics(tab, qs());
      if (my !== seq) return;   // 늦게 온 예전 응답은 버림
      data = d; version = d.version;
      if (d.options) opts = d.options;
      else if (!opts) {try {opts = (await API.analytics("products", "range=today")).options; renderFilters()} catch {}}
      destroyCharts();
      csv = null;
      $("#anBody").innerHTML = "";
      RENDER[tab](d);
      $("#anUpd").textContent = `마지막 갱신 ${hms(d.generated_at)}`;
      if (manual) toast("최신 데이터로 갱신했어요");
    } catch (e) {
      if (my !== seq) return;
      $("#anBody").innerHTML = `<div class="panel an-err">불러오지 못했어요: ${esc(e.message)}</div>`;
    } finally {if (my === seq) $("#anBody").style.opacity = 1}
  }
  async function poll() {
    if (document.hidden || API !== Server) return;
    try {const v = (await API.analytics("version", "")).version; if (version && v !== version) load()} catch {}
  }

  // ---------------------------------------------------------- CSV (지금 보고 있는 탭의 표)
  function setCsv(name, head, rows) {(csv ||= []).push({name, head, rows})}
  function downloadCsv() {
    if (!csv || !csv.length) {toast("내려받을 표가 없어요"); return}
    const q = v => {const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s};
    const text = csv.map(t => [`# ${t.name}`, t.head.map(q).join(","), ...t.rows.map(r => r.map(q).join(","))].join("\n")).join("\n\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + text], {type: "text/csv;charset=utf-8"}));   // BOM: 엑셀 한글
    a.download = `rokey_${tab}_${ymd(new Date())}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  const body = html => $("#anBody").insertAdjacentHTML("beforeend", html);
  const period = d => d.period ? `<div class="an-period">기간: ${esc(d.period.label)} · 한국 시간 기준</div>` : "";

  // ---------------------------------------------------------- 탭별 화면
  const RENDER = {
    overview(d) {
      const k = d.kpi;
      body(`<div class="kpis an">
        ${kpi("오늘 매출", d.empty ? null : krw(k.today_revenue))}${kpi("오늘 주문 건수", d.empty ? null : num(k.today_orders))}
        ${kpi("오늘 판매 수량", d.empty ? null : num(k.today_units))}${kpi("평균 주문 금액", k.aov == null ? null : krw(k.aov))}
        ${kpi("누적 매출", d.empty ? null : krw(k.total_revenue))}${kpi("누적 주문 건수", d.empty ? null : num(k.total_orders))}
        ${kpi("재고 보충 필요 상품", num(k.low_stock), {cls: k.low_stock ? "amber" : ""})}
        ${kpi("로봇 작업 성공률", k.robot_success == null ? null : pct(k.robot_success))}
        ${kpi("평균 포장 소요 시간", k.avg_pack_sec == null ? null : sec(k.avg_pack_sec), {na: "미수집"})}</div>
        <div class="an-grid two an-sec">
          ${panel("최근 7일 매출", d.empty ? collect({status: "collecting", reason: "완료된 주문이 생기면 표시돼요"}) : box("ovWeek"))}
          ${panel("판매량 상위 5개 상품", d.top5.length ? box("ovTop") : collect({status: "collecting", reason: "판매된 상품이 없어요"}))}
          ${panel("현재 재고 vs 목표 재고", legend([["현재 재고(진열+창고)", S(0)], ["적용 목표 재고", S(1)]]) + box("ovStock", "tall"), "보충 필요 = 목표 - 재고 포지션")}
          ${panel("주문 상태별 비율", d.status.length ? box("ovStatus") : collect({status: "collecting", reason: "주문이 없어요"}), "전체 주문")}
        </div>`);
      if (!d.empty) chart("ovWeek", "line", d.week.labels.map(md), [{label: "매출", data: d.week.revenue, color: BRAND()}],
        {yTick: v => num(v), tooltip: {label: c => ` 매출 ${krw(c.raw)}`}});
      if (d.top5.length) chart("ovTop", "bar", d.top5.map(p => p.name), [{label: "판매량", data: d.top5.map(p => p.qty), color: BRAND()}],
        {horizontal: true, tooltip: {label: c => ` ${num(c.raw)}개 · ${krw(d.top5[c.dataIndex].revenue)}`}});
      chart("ovStock", "bar", d.stock.map(r => r.name), [
        {label: "현재 재고", data: d.stock.map(r => r.on_hand)}, {label: "목표 재고", data: d.stock.map(r => r.target)}],
        {xTick: function (v) {const l = this.getLabelForValue(v); return l.length > 4 ? l.slice(0, 4) + "…" : l}});
      if (d.status.length) donut("ovStatus", d.status.map(s => s.name), d.status.map(s => s.count));
      setCsv("KPI", ["지표", "값"], Object.entries(k));
      setCsv("판매량 상위 5", ["상품", "판매량", "매출"], d.top5.map(p => [p.name, p.qty, p.revenue]));
      setCsv("재고 vs 목표", ["상품", "현재 재고", "목표 재고", "목표 기준"], d.stock.map(r => [r.name, r.on_hand, r.target, r.basis]));
      setCsv("주문 상태", ["상태", "건수"], d.status.map(s => [s.name, s.count]));
    },

    sales(d) {
      const k = d.kpi, t = d.trend;
      body(period(d) + `<div class="kpis an">
        ${kpi("매출", d.empty ? null : krw(k.revenue))}${kpi("주문 건수", d.empty ? null : num(k.orders))}
        ${kpi("판매 수량", d.empty ? null : num(k.units))}${kpi("평균 주문 금액", k.aov == null ? null : krw(k.aov))}
        ${kpi("주문당 평균 상품 수", k.items_per_order)}
        ${kpi("부분 완료 주문", num(k.partial), {sub: "실제로 담긴 수량만 매출 반영"})}
        ${kpi("취소 · 오류", num(k.cancelled + k.error), {cls: k.cancelled + k.error ? "red" : "", sub: k.cancel_rate == null ? "" : `비율 ${pct(k.cancel_rate)} · 매출 제외`})}
        ${kpi("만료(미수령)", num(k.expired), {sub: "매출 제외"})}</div>`);
      if (d.empty) {body(collect({status: "collecting", reason: "이 기간·필터에 판매 완료된 주문이 없어요"})); return}
      const G = {day: "일별", week: "주별", month: "월별"};
      body(`<div class="an-grid two an-sec">
        ${panel(`${G[t.gran]} 매출`, `<div class="seg" style="display:inline-flex;margin-bottom:6px" id="anGran">${Object.entries(G).map(([k2, v]) =>
          `<button data-g="${k2}" class="${t.gran === k2 ? "on" : ""}">${v}</button>`).join("")}</div>` + box("saRev"))}
        ${panel("일별 주문 건수", box("saOrders"))}
        ${panel("상품별 판매량 순위", box("saQty", "tall"), "상위 10")}
        ${panel("상품별 매출 순위", box("saRevRank", "tall"), "상위 10")}
        ${panel("카테고리별 매출 비중", box("saCat"))}
        ${panel("시간대별 판매량", box("saHour"))}
        ${panel("요일별 판매량", box("saWday"))}
        ${panel("요일 × 시간대 구매 패턴", heatmap(d.heatmap.rows, d.heatmap.cols.map(h => h % 3 ? "" : h), d.heatmap.values,
          {title: (r, c, v) => `${d.heatmap.rows[r]}요일 ${c}시: ${v}개`}), "판매 수량, 칸에 마우스를 올리면 값")}
        ${panel("월별 판매량", box("saMonth", "short") + `<div class="note">계절별: ${d.seasons.labels.map((s, i) => `${s} ${num(d.seasons.values[i])}개`).join(" · ")}</div>`)}
        ${panel(`평균 주문 금액 변화 (${G[t.gran]})`, box("saAov", "short"))}
        ${panel(`주문당 평균 상품 수 (${G[t.gran]})`, box("saIpo", "short"))}
      </div>`);
      body(panel("상품별 판매 실적", table([["상품"], ["카테고리"], ["판매량", "r"], ["매출", "r"], ["주문 수", "r"]],
        d.by_revenue.map(p => [esc(p.name), esc(p.category), num(p.qty), krw(p.revenue), num(p.orders)])), "매출 = 판매 수량 × 주문 시점 가격 (원가 데이터 없음 → 이익 미표시)"));
      $("#anGran").addEventListener("click", e => {const b = e.target.closest("[data-g]"); if (b) {F.gran = b.dataset.g; load()}});
      const lbl = t.labels.map(l => t.gran === "month" ? l : md(l)), top = a => a.slice(0, 10);
      chart("saRev", "line", lbl, [{label: "매출", data: t.revenue, color: BRAND()}], {yTick: v => num(v), tooltip: {label: c => ` 매출 ${krw(c.raw)}`}});
      chart("saOrders", "line", d.daily.labels.map(md), [{label: "주문", data: d.daily.orders, color: BRAND()}], {tooltip: {label: c => ` ${c.raw}건`}});
      chart("saQty", "bar", top(d.by_qty).map(p => p.name), [{label: "판매량", data: top(d.by_qty).map(p => p.qty), color: BRAND()}],
        {horizontal: true, tooltip: {label: c => ` ${num(c.raw)}개`}});
      chart("saRevRank", "bar", top(d.by_revenue).map(p => p.name), [{label: "매출", data: top(d.by_revenue).map(p => p.revenue), color: BRAND()}],
        {horizontal: true, yTick: v => num(v), tooltip: {label: c => ` ${krw(c.raw)}`}});
      // 카테고리 색은 이름에 고정 (필터로 개수가 바뀌어도 같은 색)
      const cats = (opts?.categories || []);
      donut("saCat", d.categories.map(c => c.name), d.categories.map(c => c.revenue), {fmt: krw, colors: d.categories.map(c => S(Math.max(0, cats.indexOf(c.name))))});
      chart("saHour", "bar", d.hours.map((_, h) => `${h}시`), [{label: "판매량", data: d.hours, color: BRAND()}], {tooltip: {label: c => ` ${c.raw}개`}});
      chart("saWday", "bar", d.weekdays.labels, [{label: "판매량", data: d.weekdays.values, color: BRAND()}], {tooltip: {label: c => ` ${c.raw}개`}});
      chart("saMonth", "line", d.monthly.labels, [{label: "판매량", data: d.monthly.values, color: BRAND()}], {tooltip: {label: c => ` ${c.raw}개`}});
      chart("saAov", "line", lbl, [{label: "평균 주문 금액", data: t.aov, color: BRAND()}], {yTick: v => num(v), tooltip: {label: c => ` ${krw(c.raw)}`}});
      chart("saIpo", "line", lbl, [{label: "주문당 상품 수", data: t.items_per_order, color: BRAND()}], {tooltip: {label: c => ` ${c.raw}개`}});
      setCsv(`${G[t.gran]} 추이`, ["기간", "매출", "주문 수", "평균 주문 금액", "주문당 상품 수"], t.labels.map((l, i) => [l, t.revenue[i], t.orders[i], t.aov[i], t.items_per_order[i]]));
      setCsv("상품별", ["상품", "카테고리", "판매량", "매출", "주문 수"], d.by_revenue.map(p => [p.name, p.category, p.qty, p.revenue, p.orders]));
      setCsv("카테고리별 매출", ["카테고리", "매출"], d.categories.map(c => [c.name, c.revenue]));
      setCsv("시간대별 판매량", ["시", "판매량"], d.hours.map((v, h) => [h, v]));
      setCsv("요일별 판매량", ["요일", "판매량"], d.weekdays.labels.map((w, i) => [w, d.weekdays.values[i]]));
      setCsv("월별 판매량", ["월", "판매량"], d.monthly.labels.map((m, i) => [m, d.monthly.values[i]]));
    },

    customers(d) {
      const b = d.basket, c = d.customers;
      body(period(d) + `<div class="kpis an">
        ${kpi("평균 장바구니 상품 수", b.avg_items)}${kpi("평균 장바구니 금액", b.avg_value == null ? null : krw(b.avg_value))}
        ${kpi("회원 주문", num(b.member_orders))}${kpi("비회원 주문", num(b.guest_orders), {sub: "고객별 분석에서 제외"})}
        ${isOk(c) ? kpi("구매 고객(회원)", num(c.active)) + kpi("고객당 주문 수", c.avg_orders)
          + kpi("평균 구매 주기", c.avg_interval_days == null ? null : `${c.avg_interval_days}일`, {na: "2회 이상 구매 고객 없음"}) : ""}</div>`);
      body(`<div class="an-grid two an-sec">
        ${panel("신규 · 재구매 고객", isOk(c) ? box("cuNew", "short") + `<div class="note">기간 안에 2번 이상 산 고객 ${c.repeat_in_period}명. 신규 = 이 기간에 처음 구매</div>` : collect(c))}
        ${panel("고객별 구매 빈도", isOk(c) ? box("cuFreq", "short") : collect(c), "기간 안 주문 수별 고객 수")}
        ${panel("장바구니 크기", b.orders ? box("cuSize", "short") : collect({status: "collecting", reason: "판매된 주문이 없어요"}), "주문당 상품 수별 주문 수 (비회원 포함)")}
        ${panel(`<span class="pill R">R</span> 상품별 재구매율`, isOk(d.repurchase) ? box("cuRep", "tall") : collect(d.repurchase), "2번 이상 산 고객 / 산 고객 (회원 전체 이력)")}
      </div>`);
      const a = d.association;
      body(panel(`<span class="pill S">S</span> <span class="pill C">C</span> 자주 함께 구매하는 상품 조합`, isOk(a)
        ? table([["상품 A"], ["상품 B"], ["패턴"], ["함께 구매", "r"], ["Support", "r"], ["Conf A→B", "r"], ["Conf B→A", "r"], ["Lift", "r"]],
          a.pairs.map(p => [esc(p.a_name), esc(p.b_name), `<span class="pill ${p.pattern}">${p.pattern}</span>`, num(p.count), pct(p.support), pct(p.conf_ab), pct(p.conf_ba), p.lift.toFixed(2)]),
          {empty: "2번 이상 함께 산 조합이 아직 없어요"})
          + `<div class="note">S = 같은 코너(products.json aisle) · C = 다른 코너. Lift &gt; 1 이면 우연보다 자주 함께 구매. 이 웹의 실제 주문으로 계산 (추천 엔진의 Instacart 분석과 별개)</div>`
        : collect(a)) + (isOk(a) ? panel("상품 간 연관성 (Lift)", heatmap(a.heatmap.names, a.heatmap.names.map(n => n.slice(0, 3)), a.heatmap.lift,
          {show: true, title: (r, c2, v) => `${a.heatmap.names[r]} × ${a.heatmap.names[c2]}: lift ${v}`, maxLabel: m => `lift ${m}`}), "판매 상위 상품") : ""));
      if (isOk(c)) {
        donut("cuNew", ["신규 고객", "기존 고객"], [c.new, c.returning]);
        chart("cuFreq", "bar", c.freq_hist.labels.map(l => l.endsWith("+") ? l : l + "회"), [{label: "고객 수", data: c.freq_hist.values, color: BRAND()}], {tooltip: {label: x => ` ${x.raw}명`}});
      }
      if (b.orders) chart("cuSize", "bar", b.size_hist.labels.map(l => l.endsWith("+") ? l : l + "개"), [{label: "주문 수", data: b.size_hist.values, color: BRAND()}], {tooltip: {label: x => ` ${x.raw}건`}});
      if (isOk(d.repurchase)) {
        const r = d.repurchase.rows.slice(0, 12);
        chart("cuRep", "bar", r.map(x => x.name), [{label: "재구매율", data: r.map(x => +(x.rate * 100).toFixed(1)), color: BRAND()}],
          {horizontal: true, yMax: 100, yTick: v => v + "%", tooltip: {label: x => ` ${x.raw}% (${r[x.dataIndex].repeaters}/${r[x.dataIndex].buyers}명)`}});
        setCsv("상품별 재구매율(R)", ["상품", "구매 고객", "재구매 고객", "재구매율"], d.repurchase.rows.map(x => [x.name, x.buyers, x.repeaters, x.rate]));
      }
      setCsv("장바구니", ["지표", "값"], Object.entries(b).filter(([, v]) => typeof v !== "object"));
      if (isOk(c)) setCsv("고객", ["지표", "값"], Object.entries(c).filter(([, v]) => typeof v !== "object"));
      if (isOk(a)) setCsv("상품 조합", ["A", "B", "패턴", "함께 구매", "support", "conf A->B", "conf B->A", "lift"], a.pairs.map(p => [p.a_name, p.b_name, p.pattern, p.count, p.support, p.conf_ab, p.conf_ba, p.lift]));
    },

    inventory(d) {
      const ST = {ok: ["ok", "정상"], reorder: ["reorder", "보충 필요"], out: ["out", "품절"]};
      const MS = {builtin: "서버 계산 (자체 판매)", external: "외부 결과 연동", not_connected: "미연동 (M5 연구 중)"};
      const mname = id => (d.models.find(m => m.id === id) || {}).name || (id === "recent_avg" ? "최근 평균" : id);
      if (d.history_note) body(`<div class="banner">📌 ${esc(d.history_note)}</div>`);
      body(`<div class="kpis an">${kpi("보충 필요 · 품절 상품", num(d.low_count), {cls: d.low_count ? "amber" : ""})}
        ${kpi("판매 기록 기간", `${d.history_days}일`)}
        ${kpi("보호기간", `${d.policy.lead_time_days + d.policy.review_period_days}일`, {sub: `입고 ${d.policy.lead_time_days}일 + 검토 주기 ${d.policy.review_period_days}일`})}
        ${kpi("목표 재고 기준", d.enough_history ? "예측 기반" : "관리자 설정")}</div>`);
      body(panel("상품별 재고 관리", table([["상품"], ["현재", "r"], ["일평균 판매", "r"], ["예상 판매(보호기간)", "r"], ["안전재고", "r"],
        ["설정 목표", "r"], ["예측 목표", "r"], ["입고 예정", "r"], ["처리 중 주문", "r"], ["보충 필요", "r"], ["상태"]],
        d.rows.map(r => [`${r.emoji || ""} ${esc(r.name)}`, `${r.on_hand} <small style="color:var(--muted)">(${r.stock}+${r.warehouse})</small>`,
          r.avg_daily, `${r.forecast_protect} <small style="color:var(--muted)">${esc(mname(r.forecast_model))}</small>`, r.safety,
          r.target_basis === "config" ? `<b>${r.target_config}</b>` : r.target_config, r.target_basis === "forecast" ? `<b>${r.target_forecast}</b>` : `<span style="color:var(--muted)">${r.target_forecast}</span>`,
          r.incoming, r.reserved, r.reorder ? `<b>${r.reorder}</b>` : 0, `<span class="tag ${ST[r.state][0]}">${ST[r.state][1]}</span>`])),
        "현재 = 진열+창고 · 굵은 목표가 지금 적용 중 · 보충 필요 = max(0, 목표 − (현재 + 입고 예정 − 처리 중 주문))"));
      const t = d.trend, held = d.held;
      body(`<div class="an-grid two an-sec">
        ${panel("현재 재고 vs 목표 재고", legend([["현재 재고", S(0)], ["적용 목표", S(1)]]) + box("ivCmp", "tall"))}
        ${panel("상품별 보충 필요 수량", d.rows.some(r => r.reorder) ? box("ivNeed", "tall") : `<div class="empty">보충이 필요한 상품이 없어요</div>`)}
        ${panel(`재고량 변화 · ${esc(t.name)}`, t.level.some(v => v != null) ? box("ivLevel") : collect({status: "collecting", reason: "재고 기록이 쌓이면 표시돼요"}), "하루 마지막 재고 (진열+창고)")}
        ${panel(`실제 판매량 vs 예측 판매량 · ${esc(t.name)}`, Object.keys(t.forecast).length
          ? legend([["실제", S(0)], ...Object.keys(t.forecast).map((m, i) => [mname(m), S(i + 1), true])]) + box("ivFc")
          : collect({status: "collecting", reason: "예측 기록이 없어요. 판매 기록이 하루 이상 쌓이면 다음 날부터 하루 한 번 예측을 저장해요"}))}
        ${panel(`월별 판매 패턴 · ${esc(t.name)}`, d.pattern.monthly.labels.length ? box("ivMonth", "short") + `<div class="note">계절별: ${d.pattern.seasons.labels.map((s, i) => `${s} ${d.pattern.seasons.values[i]}개`).join(" · ")}</div>`
          : collect({status: "collecting", reason: "판매 기록이 없어요"}))}
        ${panel(`품절 발생 추이 · ${esc(t.name)}`, t.stockout_total ? box("ivSo", "short") + `<div class="note">재고 부족으로 못 판 수량 합계 ${t.unmet_total}개</div>` : `<div class="empty">이 기간 품절 · 재고 부족 기록이 없어요</div>`)}
      </div>`);
      body(`<div class="an-grid two an-sec">
        ${panel("평균 보유 재고 · 재고 회전율", isOk(held) ? table([["상품"], ["평균 보유", "r"], ["판매량", "r"], ["회전율", "r"]],
          held.rows.map(r => [esc(r.name), r.avg_held, r.sold, r.turnover ?? "-"])) + `<div class="note">회전율 = 기간 판매량 / 평균 보유 재고. 선택 상품: 평균 보유 ${t.avg_held ?? "-"} · 회전율 ${t.turnover ?? "-"}</div>` : collect(held))}
        ${panel("수요 예측 모델", table([["모델"], ["상태"]], d.models.map(m => [m.name, `<span class="tag ${m.status}">${MS[m.status]}</span>`]))
          + `<div class="note">M5 데이터는 상품명이 익명이라 이 편의점 상품과 매핑하지 않아요. 외부 모델은 <code>AnalyticsDB.save_forecasts()</code> 로 결과를 넣으면 연동돼요.
            정책 값: <code>data/inventory_policy.json</code></div>`)}
      </div>`);
      const short = function (v) {const l = this.getLabelForValue(v); return l.length > 4 ? l.slice(0, 4) + "…" : l};
      chart("ivCmp", "bar", d.rows.map(r => r.name), [{label: "현재 재고", data: d.rows.map(r => r.on_hand)}, {label: "적용 목표", data: d.rows.map(r => r.target_applied)}], {xTick: short});
      const need = d.rows.filter(r => r.reorder).sort((a, b) => b.reorder - a.reorder);
      if (need.length) chart("ivNeed", "bar", need.map(r => r.name), [{label: "보충 필요", data: need.map(r => r.reorder), color: BRAND()}], {horizontal: true, tooltip: {label: x => ` ${x.raw}개`}});
      if (t.level.some(v => v != null)) chart("ivLevel", "line", t.labels.map(md), [{label: "재고", data: t.level, color: BRAND()}], {tooltip: {label: x => ` ${x.raw}개`}});
      if (Object.keys(t.forecast).length) chart("ivFc", "line", t.labels.map(md), [{label: "실제", data: t.actual},
        ...Object.entries(t.forecast).map(([m, v], i) => ({label: mname(m), data: v, color: S(i + 1), dash: true}))], {tooltip: {label: x => ` ${x.dataset.label}: ${x.raw ?? "-"}개`}});
      if (d.pattern.monthly.labels.length) chart("ivMonth", "line", d.pattern.monthly.labels, [{label: "판매량", data: d.pattern.monthly.values, color: BRAND()}]);
      if (t.stockout_total) chart("ivSo", "bar", t.labels.map(md), [{label: "품절 · 부족", data: t.stockouts, color: S(7)}], {tooltip: {label: x => ` ${x.raw}건`}});
      setCsv("상품별 재고", ["상품", "진열", "창고", "현재", "일평균 판매", "일 예측", "예측 모델", "보호기간 예상 판매", "안전재고", "설정 목표", "예측 목표", "적용 목표", "입고 예정", "처리 중 주문", "재고 포지션", "보충 필요", "상태"],
        d.rows.map(r => [r.name, r.stock, r.warehouse, r.on_hand, r.avg_daily, r.forecast_daily, r.forecast_model, r.forecast_protect, r.safety, r.target_config, r.target_forecast, r.target_applied, r.incoming, r.reserved, r.position, r.reorder, r.state]));
      setCsv(`추이 (${t.name})`, ["날짜", "재고", "실제 판매", ...Object.keys(t.forecast), "품절"], t.labels.map((l, i) => [l, t.level[i], t.actual[i], ...Object.values(t.forecast).map(v => v[i]), t.stockouts[i]]));
    },

    recommendations(d) {
      const k = d.kpi;
      body(period(d) + `<div class="kpis an">
        ${kpi("추천 노출", num(k.impression))}${kpi("추천 클릭 (= 장바구니 추가)", num(k.click))}
        ${kpi("클릭률 CTR", k.ctr == null ? null : pct(k.ctr, 2))}${kpi("추천 상품 구매", num(k.purchase))}
        ${kpi("구매 전환율", k.conversion == null ? null : pct(k.conversion, 2), {sub: "구매 / 노출"})}
        ${kpi("클릭 → 구매", k.click_to_purchase == null ? null : pct(k.click_to_purchase))}
        ${kpi("추천 상품 판매 금액", krw(k.rec_revenue), {sub: k.rec_revenue_share == null ? "" : `전체 매출의 ${pct(k.rec_revenue_share)}`})}</div>`);
      if (d.empty) {body(collect(d.status)); return}
      const algos = d.by_algo, head = [["구분"], ["노출", "r"], ["클릭", "r"], ["CTR", "r"], ["구매", "r"], ["구매 전환율", "r"], ["판매 금액", "r"]];
      const rowOf = r => [esc(r.name), num(r.impression), num(r.click), pct(r.ctr, 2), num(r.purchase), pct(r.conversion, 2), krw(r.revenue)];
      body(`<div class="an-grid two an-sec">
        ${panel("알고리즘별 CTR · 구매 전환율", legend([["CTR", S(0)], ["구매 전환율", S(1)]]) + box("rcAlgo"))}
        ${panel("R · S · C 추천 유형별 성과", table(head, algos.map(rowOf)))}
        ${panel("추천 영역별 성과", table(head, d.by_slot.map(rowOf)), "첫 화면 = R / 인기, 장바구니 = S / C / 인기")}
        ${panel("상품별 추천 노출 · 구매", box("rcProd", "tall"), "노출 상위 20")}
      </div>`);
      body(panel("상품별 추천 성과", table([["상품"], ["노출", "r"], ["클릭", "r"], ["CTR", "r"], ["구매", "r"], ["판매 금액", "r"]],
        d.by_product.map(r => [esc(r.name), num(r.impression), num(r.click), pct(r.ctr, 2), num(r.purchase), krw(r.revenue)]))
        + `<div class="note">${d.notes.map(esc).join("<br>")}</div>`));
      chart("rcAlgo", "bar", algos.map(r => r.name), [{label: "CTR", data: algos.map(r => r.ctr == null ? null : +(r.ctr * 100).toFixed(2))},
        {label: "구매 전환율", data: algos.map(r => r.conversion == null ? null : +(r.conversion * 100).toFixed(2))}], {yTick: v => v + "%", tooltip: {label: x => ` ${x.dataset.label} ${x.raw ?? "-"}%`}});
      const p = d.by_product;
      chart("rcProd", "bar", p.map(r => r.name), [{label: "노출", data: p.map(r => r.impression)}, {label: "구매", data: p.map(r => r.purchase)}], {horizontal: true});
      setCsv("알고리즘별", ["구분", "노출", "클릭", "CTR", "구매", "구매 전환율", "판매 금액"], algos.map(r => [r.name, r.impression, r.click, r.ctr, r.purchase, r.conversion, r.revenue]));
      setCsv("영역별", ["영역", "노출", "클릭", "CTR", "구매", "구매 전환율", "판매 금액"], d.by_slot.map(r => [r.name, r.impression, r.click, r.ctr, r.purchase, r.conversion, r.revenue]));
      setCsv("상품별", ["상품", "노출", "클릭", "CTR", "구매", "판매 금액"], p.map(r => [r.name, r.impression, r.click, r.ctr, r.purchase, r.revenue]));
    },

    packing(d) {
      body(panel("Planner 비교", table([["Planner"], ["운영 기록", "r"], ["오프라인 실험"]],
        d.planners.map(p => [p.name, num(p.live_count), p.offline === "있음" ? `<span class="tag ok">있음</span>` : `<span class="tag not_connected">실험 결과 없음</span>`]))
        + `<div class="note">실험하지 않은 Planner 의 결과는 만들지 않아요. 계획 검증 통과율과 실제 로봇 포장 성공률은 다른 지표로 따로 기록해요.</div>`));
      const L = d.live;
      body(panel("실제 운영 결과", isOk(L) ? table([["Planner"], ["건수", "r"], ["계획 생성", "r"], ["검증 통과", "r"], ["로봇 포장 성공", "r"], ["생성 시간", "r"], ["토큰", "r"], ["비용", "r"], ["재생성", "r"], ["포장 시간", "r"]],
        L.by_planner.map(r => [r.name, r.count, pct(r.generation_rate), pct(r.validation_rate), pct(r.robot_success_rate), sec(r.avg_gen_seconds), num(r.avg_tokens), usd(r.avg_cost_usd), r.avg_regenerations ?? "-", sec(r.avg_pack_seconds)]))
        + (L.pack_time_by_items.length ? `<div class="note">상품 수별 포장 시간: ${L.pack_time_by_items.map(x => `${x.n_items}개 ${sec(x.avg_seconds)}`).join(" · ")}</div>` : "")
        + (Object.keys(L.failure_reasons).length ? `<div class="note">실패 원인: ${Object.entries(L.failure_reasons).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}</div>` : "")
        + (Object.keys(L.damaged).length ? `<div class="note">손상 상품: ${Object.entries(L.damaged).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}</div>` : "")
        : collect(L), "packing_logs"));
      const O = d.offline;
      if (!isOk(O)) {body(panel("오프라인 실험 결과 (LLM 입력 방식 비교)", collect(O))); return}
      const m = O.by_mode;
      body(`<div class="an-sec">${panel("오프라인 실험 · LLM 입력 방식 비교", table([["입력 방식"], ["실행", "r"], ["계획 생성", "r"], ["검증 통과", "r"], ["평균 위반", "r"], ["평균 시간", "r"], ["평균 토큰", "r"], ["평균 비용", "r"]],
        m.map(r => [r.mode, `${r.runs} <small style="color:var(--muted)">(검증 ${r.validated_runs})</small>`, pct(r.generation_rate), r.validation_rate == null ? "검증 안 함" : pct(r.validation_rate),
          r.avg_violations ?? "-", sec(r.avg_seconds), num(r.avg_tokens), usd(r.avg_cost_usd)]))
        + `<div class="note">LLM-based Planner, 모델 ${esc(O.models.join(", "))}. v1 은 Validator 검증, v2 는 응답만 저장(검증 안 함). 실행 횟수가 적어 통계적 결론은 아니에요. 원본: ${esc(O.source)}</div>`)}</div>`);
      body(`<div class="an-grid two an-sec">
        ${panel("평균 위반 수 (v1 검증)", box("pkViol", "short"))}
        ${panel("평균 토큰", box("pkTok", "short"))}
        ${panel("실패 원인 (위반 유형, v1 합계)", legend(m.map((r, i) => [r.mode, S(i)])) + box("pkCause", "tall"))}
        ${panel("실행별 결과", table([["실행"], ["버전"], ["입력 방식"], ["생성"], ["검증"], ["위반", "r"], ["시간", "r"], ["토큰", "r"]],
          O.runs.flatMap(run => run.rows.map(r => [esc(run.run), run.version, esc(r.mode), r.generated ? "✓" : "✗", r.valid == null ? "-" : r.valid ? "통과" : "실패", r.violations ?? "-", sec(r.seconds), num(r.tokens)]))))}
      </div>`);
      chart("pkViol", "bar", m.map(r => r.mode), [{label: "평균 위반", data: m.map(r => r.avg_violations), color: BRAND()}], {tooltip: {label: x => ` ${x.raw ?? "-"}건`}});
      chart("pkTok", "bar", m.map(r => r.mode), [{label: "평균 토큰", data: m.map(r => r.avg_tokens), color: BRAND()}], {tooltip: {label: x => ` ${num(x.raw)} 토큰`}});
      const causes = [...new Set(m.flatMap(r => Object.keys(r.causes)))];
      chart("pkCause", "bar", causes, m.map((r, i) => ({label: r.mode, data: causes.map(c => r.causes[c] || 0), color: S(i)})), {horizontal: true});
      setCsv("입력 방식 비교", ["입력 방식", "실행", "검증 실행", "계획 생성률", "검증 통과율", "평균 위반", "평균 시간(초)", "평균 토큰", "평균 비용(USD)"],
        m.map(r => [r.mode, r.runs, r.validated_runs, r.generation_rate, r.validation_rate, r.avg_violations, r.avg_seconds, r.avg_tokens, r.avg_cost_usd]));
      setCsv("실행별", ["실행", "버전", "입력 방식", "생성", "검증", "위반", "시간", "토큰", "비용"], O.runs.flatMap(run => run.rows.map(r => [run.run, run.version, r.mode, r.generated, r.valid, r.violations, r.seconds, r.tokens, r.cost_usd])));
    },

    robot(d) {
      const k = d.kpi;
      body(period(d) + `<div class="kpis an">
        ${kpi("로봇 작업(주문) 건수", num(k.tasks), {sub: `완료 ${k.done} · 오류 ${k.error} · 손님 취소 ${k.cancelled}`})}
        ${kpi("로봇 작업 성공률", k.success_rate == null ? null : pct(k.success_rate), {sub: "완료 / (완료+오류)"})}
        ${kpi("평균 Pick & Place", k.avg_pick_sec == null ? null : sec(k.avg_pick_sec))}
        ${kpi("평균 주문 처리 시간", k.avg_order_sec == null ? null : sec(k.avg_order_sec), {sub: k.avg_robot_sec == null ? "" : `손님 선택 대기 제외 ${sec(k.avg_robot_sec)}`})}
        ${kpi("로봇 오류", num(k.errors), {cls: k.errors ? "red" : ""})}
        ${kpi("작업 시간 합계", sec(k.busy_sec))}
        ${kpi("가동 · 대기 시간", null, {na: "미수집"})}</div>`);
      if (d.empty) body(collect({status: "collecting", reason: "로봇이 주문을 처리하면 기록돼요 (/order_status 를 그대로 기록, ROS2 구조 변경 없음)"}));
      body(`<div class="an-grid two an-sec">
        ${panel("일별 작업 건수", legend([["전체 작업", S(0)], ["완료", S(1)]]) + box("rbDaily", "short"))}
        ${panel("오류 유형별 발생", d.error_types.length ? box("rbErr", "short") : `<div class="empty">오류 기록이 없어요</div>`)}
        ${panel("상품별 파지(집기) 성공률", isOk(d.grasp) ? box("rbGrasp", "tall") : collect(d.grasp))}
        ${panel("상품별 배치(놓기) 성공률", isOk(d.place) ? table([["상품"], ["시도", "r"], ["성공률", "r"]], d.place.rows.map(r => [esc(r.name), r.attempts, pct(r.rate)])) : collect(d.place))}
        ${panel("MoveIt 2 경로 계획 실패율", isOk(d.motion_plan) ? `<div class="kpis an">${kpi("경로 계획 시도", num(d.motion_plan.attempts))}${kpi("실패율", pct(d.motion_plan.fail_rate), {cls: "red"})}</div>` : collect(d.motion_plan))}
        ${panel("가동 · 대기 시간", collect(d.uptime))}
      </div>`);
      if (isOk(d.grasp)) body(panel("상품별 파지 기록", table([["상품"], ["시도", "r"], ["성공", "r"], ["성공률", "r"], ["평균 Pick & Place", "r"]],
        d.grasp.rows.map(r => [esc(r.name), r.attempts, r.success, pct(r.rate), sec(r.avg_sec)]))));
      if (d.recent_errors.length) body(panel("최근 오류", table([["시각"], ["주문"], ["유형"], ["메시지"]],
        d.recent_errors.map(r => [hms(r.ts), r.order_id ?? "-", r.type, esc(r.message)]))));
      chart("rbDaily", "bar", d.daily.labels.map(md), [{label: "전체 작업", data: d.daily.tasks}, {label: "완료", data: d.daily.done}]);
      if (d.error_types.length) chart("rbErr", "bar", d.error_types.map(e => e.name), [{label: "건수", data: d.error_types.map(e => e.count), color: S(7)}], {horizontal: true});
      if (isOk(d.grasp)) {
        const g = d.grasp.rows;
        chart("rbGrasp", "bar", g.map(r => r.name), [{label: "성공률", data: g.map(r => +(r.rate * 100).toFixed(1)), color: BRAND()}],
          {horizontal: true, yMax: 100, yTick: v => v + "%", tooltip: {label: x => ` ${x.raw}% (${g[x.dataIndex].success}/${g[x.dataIndex].attempts})`}});
        setCsv("상품별 파지", ["상품", "시도", "성공", "성공률", "평균 Pick&Place(초)"], g.map(r => [r.name, r.attempts, r.success, r.rate, r.avg_sec]));
      }
      setCsv("KPI", ["지표", "값"], Object.entries(k));
      setCsv("일별 작업", ["날짜", "전체", "완료"], d.daily.labels.map((l, i) => [l, d.daily.tasks[i], d.daily.done[i]]));
      setCsv("오류 유형", ["유형", "건수"], d.error_types.map(e => [e.name, e.count]));
    },
  };

  // ---------------------------------------------------------- 탭 열기 / 닫기 (index.js 의 show() 가 부름)
  let wired = false;
  function open() {
    document.body.classList.add("wide");
    if (!wired) {
      wired = true;
      $("#anSub").innerHTML = TABS.map(([k, en, ko]) => `<button data-t="${k}" title="${ko}">${en}<small style="font-weight:600;opacity:.8"> · ${ko}</small></button>`).join("");
      $("#anSub").addEventListener("click", e => {const b = e.target.closest("[data-t]"); if (b && b.dataset.t !== tab) {tab = b.dataset.t; markTab(); renderFilters(); load()}});
      wireFilters();
      document.addEventListener("visibilitychange", () => {if (!document.hidden && timer) poll()});
    }
    markTab(); renderFilters(); load();
    clearInterval(timer); timer = setInterval(poll, POLL_MS);
  }
  function close() {
    clearInterval(timer); timer = null; seq++;
    destroyCharts();
    document.body.classList.remove("wide");
  }
  function markTab() {document.querySelectorAll("#anSub [data-t]").forEach(b => b.classList.toggle("on", b.dataset.t === tab))}
  return {open, close};
})();
