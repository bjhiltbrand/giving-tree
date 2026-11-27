(function () {
  "use strict";
  const dataEl = document.getElementById("tree-data");
  if (!dataEl) return;
  const DATA = JSON.parse(dataEl.textContent);
  const ALL = DATA.items;
  const svg = document.getElementById("tree");
  const tip = document.getElementById("tree-tip");
  const NS = "http://www.w3.org/2000/svg";

  const CX = 300, APEX_Y = 78, BASE_Y = 628, MAX_HW = 262;
  // {key: {label, icon, colors: [light, dark]}}, from the categories set in Admin > Settings.
  const CATS = DATA.categories || {};
  const cat = (key) => CATS[key] || CATS.other || { label: "Other", icon: "✨", colors: ["#f4f4fa", "#8d8da6"] };
  const catKey = (key) => (CATS[key] ? key : "other");

  const el = (tag, attrs, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };

  // ----------------------------------------------------------------- static tree art
  function drawTree() {
    svg.innerHTML = "";
    const defs = el("defs", {}, svg);
    const g1 = el("linearGradient", { id: "leaf", x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
    el("stop", { offset: "0", "stop-color": "#2f8a5b" }, g1);
    el("stop", { offset: "1", "stop-color": "#14512f" }, g1);
    const glow = el("radialGradient", { id: "starglow" }, defs);
    el("stop", { offset: "0", "stop-color": "#fff6c2", "stop-opacity": ".9" }, glow);
    el("stop", { offset: "1", "stop-color": "#fff6c2", "stop-opacity": "0" }, glow);
    for (const key of Object.keys(CATS).concat(CATS.other ? [] : ["other"])) {
      const [light, dark] = cat(key).colors;
      const rg = el("radialGradient", { id: "orn-" + key, cx: ".35", cy: ".3", r: ".75" }, defs);
      el("stop", { offset: "0", "stop-color": "#ffffff", "stop-opacity": ".95" }, rg);
      el("stop", { offset: ".18", "stop-color": light }, rg);
      el("stop", { offset: "1", "stop-color": dark }, rg);
    }
    const gray = el("filter", { id: "taken" }, defs);
    el("feColorMatrix", { type: "saturate", values: "0.15" }, gray);

    // trunk
    el("rect", { x: CX - 26, y: BASE_Y - 10, width: 52, height: 62, rx: 6, fill: "#6b4226" }, svg);
    el("rect", { x: CX - 26, y: BASE_Y - 10, width: 18, height: 62, rx: 6, fill: "#7d5131" }, svg);

    // three tiers with scalloped bottoms
    const tiers = [
      [APEX_Y - 10, 250, 0.36],
      [170, 430, 0.68],
      [320, BASE_Y + 6, 1.0],
    ];
    tiers.forEach(([top, bottom, w]) => {
      const hw = MAX_HW * w + 14;
      const scallops = Math.max(4, Math.round(hw / 30));
      let d = `M ${CX} ${top} L ${CX + hw} ${bottom}`;
      const step = (2 * hw) / scallops;
      for (let i = 0; i < scallops; i++) {
        const x1 = CX + hw - step * i, x2 = x1 - step;
        d += ` Q ${(x1 + x2) / 2} ${bottom + 16} ${x2} ${bottom}`;
      }
      d += " Z";
      el("path", { d, fill: "url(#leaf)", stroke: "#0f3d23", "stroke-width": 2, "stroke-linejoin": "round" }, svg);
    });

    // garlands
    [[150, 0.3], [255, 0.5], [375, 0.72], [500, 0.92]].forEach(([y, w], i) => {
      const hw = MAX_HW * w;
      el("path", {
        d: `M ${CX - hw} ${y - 30 + (i % 2) * 18} Q ${CX} ${y + 42} ${CX + hw} ${y - 12 - (i % 2) * 18}`,
        fill: "none", stroke: "#f3e3b0", "stroke-width": 3, "stroke-dasharray": "1 9",
        "stroke-linecap": "round", opacity: ".8",
      }, svg);
    });

    // star
    el("circle", { cx: CX, cy: APEX_Y - 18, r: 60, fill: "url(#starglow)", class: "star-glow" }, svg);
    const pts = [];
    for (let i = 0; i < 10; i++) {
      const r = i % 2 ? 14 : 34, a = -Math.PI / 2 + (i * Math.PI) / 5;
      pts.push(`${CX + r * Math.cos(a)},${APEX_Y - 18 + r * Math.sin(a)}`);
    }
    el("polygon", { points: pts.join(" "), fill: "#ffd54a", stroke: "#c9971a", "stroke-width": 2 }, svg);
  }

  // ----------------------------------------------------------------- ornament layout
  const halfWidth = (y) => ((y - APEX_Y) / (BASE_Y - APEX_Y)) * MAX_HW;

  function rowsFor(s) {
    const rows = [];
    for (let y = APEX_Y + s * 1.15 + 20; y <= BASE_Y - s * 0.45; y += s * 0.92) {
      const hw = halfWidth(y) - s * 0.45;
      if (hw < 0) continue;
      const n = Math.floor((2 * hw) / s) + 1;
      rows.push({ y, hw, n });
    }
    return rows;
  }

  function layout(n) {
    if (!n) return { slots: [], s: 60 };
    let lo = 16, hi = 150;
    for (let i = 0; i < 30; i++) {
      const mid = (lo + hi) / 2;
      const count = rowsFor(mid).reduce((a, r) => a + r.n, 0);
      if (count >= n) lo = mid; else hi = mid;
    }
    const s = lo;
    const slots = [];
    rowsFor(s).forEach((row) => {
      for (let i = 0; i < row.n; i++) {
        const x = row.n === 1 ? CX : CX - row.hw + (2 * row.hw * i) / (row.n - 1);
        slots.push({ x, y: row.y });
      }
    });
    // Spread the ornaments evenly over all available slots.
    const T = slots.length, picked = [];
    for (let i = 0; i < n; i++) picked.push(slots[Math.min(T - 1, Math.floor((i * T) / n + T / (2 * n)))]);
    return { slots: picked, s };
  }

  function jitter(id, amt) {
    const x = Math.sin(id * 12.9898) * 43758.5453;
    return ((x - Math.floor(x)) - 0.5) * 2 * amt;
  }

  function drawOrnaments(items) {
    svg.querySelectorAll(".orn").forEach((n) => n.remove());
    const { slots, s } = layout(items.length);
    const r = Math.max(9, Math.min(30, s * 0.36));
    items.forEach((item, i) => {
      const p = slots[i];
      const x = p.x + jitter(item.id, s * 0.08), y = p.y + jitter(item.id + 7, s * 0.06);
      const taken = item.remaining <= 0;
      const g = el("g", {
        class: "orn" + (taken ? " taken" : ""),
        transform: `translate(${x.toFixed(1)} ${y.toFixed(1)})`,
        tabindex: "0",
        role: "button",
        "aria-label": `Ornament ${item.id}: ${item.title}${item.recipient ? ", for " + item.recipient : ""}. ` +
          (taken ? "Already claimed." : "Available."),
      }, svg);
      g.dataset.id = item.id;
      const inner = el("g", { class: "orn-inner" }, g);
      el("line", { x1: 0, y1: -r - 12, x2: 0, y2: -r, stroke: "#e9d8a6", "stroke-width": 1.5 }, inner);
      el("rect", { x: -r * 0.28, y: -r - 4, width: r * 0.56, height: r * 0.32, rx: 2, fill: "#c9a640" }, inner);
      el("circle", { r, fill: `url(#orn-${catKey(item.category)})`,
        stroke: "rgba(0,0,0,.25)", "stroke-width": 1, filter: taken ? "url(#taken)" : null }, inner);
      if (r >= 13) {
        const t = el("text", { "text-anchor": "middle", "dominant-baseline": "central", "font-size": r * 0.85,
          y: r * 0.06, class: "orn-icon" }, inner);
        t.textContent = cat(item.category).icon;
      }
      if (taken) {
        el("circle", { cx: r * 0.62, cy: -r * 0.55, r: Math.max(6, r * 0.38), fill: "#ffd54a", stroke: "#8a6a10" }, inner);
        const c = el("text", { x: r * 0.62, y: -r * 0.53, "text-anchor": "middle", "dominant-baseline": "central",
          "font-size": Math.max(8, r * 0.5), "font-weight": 700, fill: "#5a4206" }, inner);
        c.textContent = "✓";
      } else if (item.quantity > 1) {
        el("circle", { cx: r * 0.66, cy: r * 0.6, r: Math.max(7, r * 0.4), fill: "#fff", stroke: "#b3261e" }, inner);
        const c = el("text", { x: r * 0.66, y: r * 0.62, "text-anchor": "middle", "dominant-baseline": "central",
          "font-size": Math.max(8, r * 0.45), "font-weight": 700, fill: "#b3261e" }, inner);
        c.textContent = item.remaining;
      }
    });
  }

  // ----------------------------------------------------------------- filters
  const fFamily = document.getElementById("f-family");
  const fCat = document.getElementById("f-category");
  const fAvail = document.getElementById("f-available");

  function filtered() {
    return ALL.filter((it) =>
      (!fFamily || !fFamily.value || String(it.familyId) === fFamily.value) &&
      (!fCat.value || it.category === fCat.value) &&
      (!fAvail.checked || it.remaining > 0));
  }

  function refresh() {
    const items = filtered();
    drawOrnaments(items);
    const ids = new Set(items.map((i) => i.id));
    document.querySelectorAll(".card[data-id]").forEach((c) => { c.hidden = !ids.has(+c.dataset.id); });
    document.querySelectorAll(".family-block").forEach((b) => {
      b.hidden = !b.querySelector(".card:not([hidden])");
    });
    let empty = document.getElementById("tree-empty");
    if (!items.length) {
      if (!empty) {
        empty = el("text", { id: "tree-empty", x: CX, y: 380, "text-anchor": "middle", fill: "#fff",
          "font-size": 22, class: "tree-empty" }, svg);
      }
      empty.textContent = ALL.length ? (fAvail.checked && !(fFamily && fFamily.value) && !fCat.value
        ? "Every ornament has been claimed! 🎉" : "No ornaments match those filters") : "Decorating soon…";
    } else if (empty) empty.remove();
  }
  [fFamily, fCat, fAvail].forEach((f) => f && f.addEventListener("change", refresh));

  // ----------------------------------------------------------------- tooltip
  function showTip(g) {
    const item = ALL.find((i) => i.id === +g.dataset.id);
    if (!item) return;
    tip.innerHTML = "";
    const b = document.createElement("strong"); b.textContent = item.title; tip.appendChild(b);
    const meta = [item.recipient, item.size && "Size " + item.size].filter(Boolean).join(" · ");
    if (meta) { const d = document.createElement("div"); d.textContent = meta; tip.appendChild(d); }
    const st = document.createElement("div"); st.className = "tip-state";
    st.textContent = item.remaining > 0
      ? (item.quantity > 1 ? `${item.remaining} of ${item.quantity} needed · click to claim` : "Click to claim")
      : "Claimed — thank you!";
    tip.appendChild(st);
    tip.hidden = false;
    const wrap = svg.parentElement.getBoundingClientRect();
    const rect = g.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - wrap.left;
    left = Math.max(90, Math.min(wrap.width - 90, left));
    tip.style.left = left + "px";
    tip.style.top = rect.top - wrap.top - 8 + "px";
  }
  const hideTip = () => { tip.hidden = true; };

  svg.addEventListener("mouseover", (e) => { const g = e.target.closest(".orn"); if (g) showTip(g); });
  svg.addEventListener("mouseout", (e) => { if (e.target.closest(".orn")) hideTip(); });
  svg.addEventListener("focusin", (e) => { const g = e.target.closest(".orn"); if (g) showTip(g); });
  svg.addEventListener("focusout", hideTip);
  svg.addEventListener("click", (e) => { const g = e.target.closest(".orn"); if (g) openItem(+g.dataset.id); });
  svg.addEventListener("keydown", (e) => {
    const g = e.target.closest(".orn");
    if (g && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openItem(+g.dataset.id); }
  });

  // ----------------------------------------------------------------- claim dialog
  const dlg = document.getElementById("claim-dialog");
  const $ = (id) => document.getElementById(id);

  function openItem(id) {
    const item = ALL.find((i) => i.id === id);
    if (!item) return;
    hideTip();
    $("dlg-ornament").className = "dlg-ornament cat-" + catKey(item.category);
    $("dlg-meta").textContent = `Ornament #${item.id} · ${cat(item.category).label}`;
    $("dlg-title").textContent = item.title;
    const dl = $("dlg-details"); dl.innerHTML = "";
    const add = (k, v) => { if (!v) return; const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd"); dd.textContent = v; dl.append(dt, dd); };
    add("For", item.recipient);
    add("Size", item.size);
    if (item.quantity > 1) add("Needed", `${item.remaining} of ${item.quantity}`);
    $("dlg-desc").textContent = item.description || "";
    $("dlg-desc").hidden = !item.description;
    const link = $("dlg-link");
    link.hidden = !item.link;
    if (item.link) link.querySelector("a").href = item.link;
    $("dlg-family").hidden = !item.family;
    $("dlg-family").textContent = item.family ? `From the wish list of ${item.family}` +
      (item.familyDescription ? ` — ${item.familyDescription}` : "") : "";

    const available = item.remaining > 0;
    $("dlg-claimed").hidden = available;
    $("dlg-form").hidden = !available || !DATA.claimsOpen;
    const form = dlg.querySelector("form");
    form.action = `/ornament/${item.id}/claim`;
    const maxQ = Math.max(1, Math.min(item.remaining, DATA.maxQty || 1));
    const sel = form.querySelector("select[name=quantity]");
    sel.innerHTML = "";
    for (let n = 1; n <= maxQ; n++) { const o = document.createElement("option"); o.value = o.textContent = n; sel.appendChild(o); }
    form.querySelector(".qty-field").hidden = maxQ <= 1;

    history.replaceState(null, "", "#ornament-" + item.id);
    if (typeof dlg.showModal === "function") dlg.showModal(); else window.location = "/ornament/" + item.id;
    const first = form.querySelector("input[name=name]");
    if (available && first) setTimeout(() => (first.value ? form.querySelector("button[type=submit]") : first).focus(), 50);
  }

  dlg.addEventListener("close", () => history.replaceState(null, "", location.pathname + location.search));
  dlg.addEventListener("click", (e) => { if (e.target === dlg || e.target.hasAttribute("data-close")) dlg.close(); });
  dlg.querySelector("form").addEventListener("submit", (e) => {
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true; btn.textContent = "Claiming…";
  });

  document.querySelectorAll(".js-open").forEach((a) => a.addEventListener("click", (e) => {
    if (typeof dlg.showModal !== "function") return;
    e.preventDefault();
    openItem(+a.dataset.id);
  }));

  // ----------------------------------------------------------------- snow
  const snow = document.querySelector(".snow");
  if (snow && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    for (let i = 0; i < 46; i++) {
      const f = document.createElement("i");
      f.style.left = Math.random() * 100 + "%";
      f.style.animationDuration = 8 + Math.random() * 12 + "s";
      f.style.animationDelay = -Math.random() * 20 + "s";
      f.style.opacity = 0.35 + Math.random() * 0.6;
      const size = 2 + Math.random() * 4;
      f.style.width = f.style.height = size + "px";
      snow.appendChild(f);
    }
  }

  drawTree();
  refresh();
  const m = location.hash.match(/^#ornament-(\d+)$/);
  if (m) openItem(+m[1]);
})();
