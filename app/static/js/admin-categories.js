// Admin > Settings > Gift categories: "Add category" rows, live color dots and an emoji picker.
// Progressive enhancement only: without this script the table is a plain form with one blank row.
(function () {
  "use strict";
  const form = document.querySelector("form.js-categories");
  if (!form) return;
  const table = form.querySelector(".categories-table");
  const tbody = table.tBodies[0];
  const tmpl = document.getElementById("new-category-row");
  const addBtn = form.querySelector(".js-add-category");
  let palette = {};
  try { palette = JSON.parse(table.dataset.palette || "{}"); } catch (e) { /* dots just won't update live */ }

  const ICONS = [
    ["👕", "T-shirt"], ["👖", "Jeans"], ["🧥", "Coat"], ["👗", "Dress"], ["🧦", "Socks"], ["👟", "Shoes"],
    ["🥾", "Boots"], ["🧤", "Gloves"], ["🧣", "Scarf"], ["🧢", "Cap"], ["🎒", "Backpack"], ["👜", "Bag"],
    ["🧸", "Teddy bear"], ["🎮", "Video game"], ["🧩", "Puzzle"], ["🎲", "Game"], ["🪀", "Toy"], ["🚲", "Bike"],
    ["⚽", "Ball"], ["🏀", "Basketball"], ["🛹", "Skateboard"], ["🎨", "Art"], ["✏️", "School supplies"],
    ["📚", "Books"], ["🎧", "Headphones"], ["💻", "Electronics"], ["📱", "Phone"], ["🎵", "Music"],
    ["💳", "Gift card"], ["💵", "Cash"], ["🎟️", "Tickets"], ["🎁", "Gift"],
    ["🛒", "Groceries"], ["🥫", "Canned food"], ["🍎", "Fruit"], ["🍞", "Bread"], ["🥛", "Milk"], ["🍪", "Treats"],
    ["🍼", "Baby"], ["🧷", "Diapers"], ["🧴", "Toiletries"], ["🧼", "Soap"], ["🪥", "Toothbrush"], ["💊", "Health"],
    ["🏠", "Household"], ["🛏️", "Bedding"], ["🍳", "Kitchen"], ["🧹", "Cleaning"], ["🔦", "Tools"], ["🐾", "Pets"],
    ["⛄", "Winter"], ["🎄", "Holiday"], ["⭐", "Star"], ["❤️", "Heart"], ["✨", "Other"],
  ];

  // ---------------------------------------------------------------- color dots follow the dropdown
  function paintDot(select) {
    const dot = select.parentElement.querySelector(".dot");
    const p = palette[select.value];
    if (dot && p) dot.style.background = `radial-gradient(circle at 35% 30%, ${p[0]}, ${p[2]} 40%, ${p[3]})`;
  }
  form.addEventListener("change", (e) => {
    if (e.target.matches("select.cat-color")) paintDot(e.target);
  });

  // ---------------------------------------------------------------- icon picker
  const picker = document.createElement("div");
  picker.className = "icon-picker";
  picker.hidden = true;
  picker.setAttribute("role", "dialog");
  picker.setAttribute("aria-label", "Choose an icon");
  picker.innerHTML =
    '<div class="icon-grid"></div>' +
    '<label class="icon-custom"><span class="small">Or any emoji</span>' +
    '<input type="text" maxlength="8" aria-label="Type or paste an emoji"></label>' +
    '<p class="help">Tip: <kbd>Windows</kbd> + <kbd>.</kbd> (or <kbd>Ctrl</kbd> + <kbd>⌘</kbd> + <kbd>Space</kbd> on a Mac) opens the emoji keyboard.</p>';
  const grid = picker.querySelector(".icon-grid");
  const custom = picker.querySelector(".icon-custom input");
  for (const [icon, name] of ICONS) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = icon;
    b.title = name;
    b.setAttribute("aria-label", name);
    b.dataset.icon = icon;
    grid.appendChild(b);
  }
  document.body.appendChild(picker);
  let target = null; // the icon button the picker is open for

  function setIcon(btn, icon) {
    icon = (icon || "").trim();
    if (!icon) return;
    btn.textContent = icon;
    btn.previousElementSibling.value = icon;
  }

  function openPicker(btn) {
    target = btn;
    custom.value = "";
    picker.hidden = false;
    const r = btn.getBoundingClientRect();
    const left = Math.min(r.left + window.scrollX, window.scrollX + document.documentElement.clientWidth - picker.offsetWidth - 8);
    picker.style.left = Math.max(8, left) + "px";
    picker.style.top = r.bottom + window.scrollY + 6 + "px";
    btn.setAttribute("aria-expanded", "true");
    (grid.querySelector(`[data-icon="${CSS.escape(btn.textContent)}"]`) || grid.firstChild).focus();
  }

  function closePicker(refocus) {
    if (!target) return;
    target.setAttribute("aria-expanded", "false");
    if (refocus) target.focus();
    picker.hidden = true;
    target = null;
  }

  grid.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-icon]");
    if (b && target) { setIcon(target, b.dataset.icon); closePicker(true); }
  });
  custom.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); if (target) setIcon(target, custom.value); closePicker(true); }
  });
  custom.addEventListener("input", () => { if (target) setIcon(target, custom.value); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePicker(true); });
  document.addEventListener("mousedown", (e) => {
    if (target && !picker.contains(e.target) && e.target !== target) closePicker(false);
  });
  window.addEventListener("resize", () => closePicker(false));

  function enhanceIcon(input) {
    input.type = "hidden";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "icon-btn";
    btn.textContent = input.value || "✨";
    btn.setAttribute("aria-haspopup", "dialog");
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-label", (input.getAttribute("aria-label") || "Icon") + ": change");
    btn.title = "Change icon";
    btn.addEventListener("click", () => (target === btn ? closePicker(true) : openPicker(btn)));
    input.after(btn);
  }

  // ---------------------------------------------------------------- new rows
  function unusedColor() {
    const used = new Set([...form.querySelectorAll("select.cat-color")].map((s) => s.value));
    return Object.keys(palette).find((name) => !used.has(name) && name !== "silver") || "green";
  }

  function addRow() {
    const row = tmpl.content.firstElementChild.cloneNode(true);
    const select = row.querySelector("select.cat-color");
    select.value = unusedColor();
    const name = row.querySelector('input[name="cat_label"]');
    name.required = true; // a row you added on purpose needs a name; use ✕ to drop it
    const discard = row.querySelector(".js-discard");
    discard.hidden = false;
    discard.addEventListener("click", () => {
      row.remove();
      addBtn.focus();
    });
    // New rows go just above the fallback category, matching where the server will put them.
    const otherRow = tbody.querySelector('input[name="cat_key"][value="other"]');
    tbody.insertBefore(row, otherRow ? otherRow.closest("tr") : null);
    enhanceIcon(row.querySelector(".icon-input"));
    paintDot(select);
    name.focus();
  }

  form.querySelectorAll(".js-fallback-row").forEach((r) => r.remove());
  form.querySelectorAll(".icon-input").forEach(enhanceIcon);
  addBtn.hidden = false;
  addBtn.addEventListener("click", addRow);
})();
