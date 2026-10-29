/* Mini display: a few live stats in a small always-on-top window. gk/mini.py hosts the window. */
"use strict";

const { fmt } = GK;
const $ = (id) => document.getElementById(id);
const grid = $("miniWidgets");
const SHOWN = new Set(["keys-today", "clicks-today", "current-app", "kpm"]);
const DEFAULT = [...grid.querySelectorAll(":scope > [data-widget]")].map((w) => ({ id: w.dataset.widget, visible: SHOWN.has(w.dataset.widget) }));

// A request gives up after 4 s, so one stuck request cannot hold the polls.
async function api(path) {
  const r = await fetch("api/" + path, { cache: "no-store", signal: AbortSignal.timeout ? AbortSignal.timeout(4000) : undefined });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

async function post(path, body) {
  const r = await fetch("api/" + path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-GK": "1" }, body: JSON.stringify(body),
  });
  return r.json();
}

// ------------------------------------------------------------------ live values (1 s)

const clicksOf = (t) => (t.lclicks || 0) + (t.rclicks || 0) + (t.mclicks || 0) + (t.xclicks || 0);
const NONE = "–";
const VALUES = {
  "keys-today": (L) => fmt.int(L.today.keys),
  "clicks-today": (L) => fmt.int(clicksOf(L.today)),
  "current-app": (L) => (L.idle ? `${L.app.name} (idle)` : L.app.name),
  kpm: (L) => fmt.int(L.now.kpm),
  wpm: (L) => Math.round(L.now.wpm),
  "active-today": (L) => fmt.dur(L.today.active_s),
  "down-now": (L) => fmt.speed(L.now.down),
  "up-now": (L) => fmt.speed(L.now.up),
  apm: (L) => (L.gaming ? fmt.int(L.gaming.apm) : NONE),
  "power-now": (L) => (L.power ? fmt.watts(L.power.total_w) : NONE),
};

// The value cells are found once. A cell is written only when its text changed, so an idle
// display does no DOM work. Hidden cards are skipped until they are shown again.
const cells = [...grid.querySelectorAll("[data-v]")].map((el) => ({
  el, card: el.closest("[data-widget]"), fn: VALUES[el.dataset.v], text: null,
}));

let live = null;
function render() {
  if (!live) return;
  const editing = grid.classList.contains("editing");
  for (const c of cells) {
    if (!editing && c.card.classList.contains("hidden-widget")) continue;
    const text = String(c.fn(live));
    if (text !== c.text) { c.text = text; c.el.textContent = text; }
  }
}

let liveBusy = false;
async function pollLive() {
  if (liveBusy) return;           // the last request is still out: don't stack another one
  liveBusy = true;
  try {
    live = await api("live");
    render();
  } catch (e) { /* server busy or gone: keep the last numbers */ }
  finally { liveBusy = false; }
}

// ------------------------------------------------------------------ settings

const drag = $("miniDrag"), lockBox = $("miniLock"), clickBox = $("miniClick"), opacity = $("miniOpacity");
const opacityVal = $("miniOpacityVal");
const panel = $("miniPanel");

function showSettings(m) {
  const locked = !!m.locked;
  if (drag.classList.contains("pywebview-drag-region") === locked) drag.classList.toggle("pywebview-drag-region", !locked);
  if (lockBox.checked !== locked) lockBox.checked = locked;
  if (clickBox.checked !== !!m.clickthrough) clickBox.checked = !!m.clickthrough;
  if (!opacity.matches(":active")) {
    const v = String(Math.round(m.opacity * 20) * 5);
    if (opacity.value !== v) opacity.value = v;
  }
  const label = opacity.value + "%";
  if (opacityVal.textContent !== label) opacityVal.textContent = label;
}

let laidOut = false;
let settingsBusy = false;
async function pollSettings() {
  if (settingsBusy) return;
  settingsBusy = true;
  try {
    const s = await api("settings");
    if (!laidOut) {       // a layout is saved as soon as it is edited; empty means the defaults
      const saved = (s.layouts || {}).mini || [];
      GKLayout.apply(grid, saved.length ? saved : DEFAULT);
      laidOut = true;
    }
    showSettings(s.mini);
    if (!s.mini.open) window.close();      // fallback Edge window: the host window closes itself
  } catch (e) { /* keep the last settings */ }
  finally { settingsBusy = false; }
}

// ------------------------------------------------------------------ edit stats

let saveT;
function saveLayout() {
  clearTimeout(saveT);
  saveT = setTimeout(() => post("settings", { layout: { name: "mini", items: GKLayout.read(grid) } }).catch(() => {}), 400);
}
function setEditing(on) {
  GKLayout.edit(grid, on, saveLayout);
  $("miniDone").hidden = !on;
  panel.hidden = on;
}
$("miniEdit").addEventListener("click", () => setEditing(true));
$("miniDone").addEventListener("click", () => setEditing(false));
$("miniReset").addEventListener("click", () => {
  const on = grid.classList.contains("editing");
  GKLayout.apply(grid, DEFAULT);
  if (on) { setEditing(false); setEditing(true); }     // redraw the eye buttons for the new state
  saveLayout();
});

// ------------------------------------------------------------------ panel and window settings

$("miniGear").addEventListener("click", () => {
  const show = panel.hidden;
  panel.hidden = !show;
  if (show) pollSettings();              // the checkboxes show the current settings (the tray can change click-through)
});
$("miniClose").addEventListener("click", () => post("settings", { mini: { open: false } }).catch(() => {}));
lockBox.addEventListener("change", () => {
  drag.classList.toggle("pywebview-drag-region", !lockBox.checked);
  post("settings", { mini: { locked: lockBox.checked } }).catch(() => {});
});
clickBox.addEventListener("change", () => post("settings", { mini: { clickthrough: clickBox.checked } }).catch(() => {}));
let opacityT;
opacity.addEventListener("input", () => {
  opacityVal.textContent = opacity.value + "%";
  clearTimeout(opacityT);
  opacityT = setTimeout(() => post("settings", { mini: { opacity: opacity.value / 100 } }).catch(() => {}), 250);
});

// ------------------------------------------------------------------ start
// One 1 s timer. /api/live is read on every tick and /api/settings on every second tick (2 s), but
// settings are only read when they can change something here: while the panel is open, or outside
// pywebview (the Edge fallback window closes itself when "open" goes false). Inside pywebview,
// gk/mini.py watches "open" and destroys the window itself, and opening the panel reads settings again.
let ticks = 0;
function tick() {
  ticks++;
  pollLive();
  if (ticks % 2 === 0 && (typeof window.pywebview === "undefined" || !panel.hidden)) pollSettings();
}
pollLive();
pollSettings();
setInterval(tick, 1000);
