/* gkeystrokes dashboard: Macros and Layouts tabs (the engine's /api/macros and /api/layouts).
   Both tabs stay hidden until GET /api/macros answers; an older tracker answers 404 and the page carries on without them. */
"use strict";

const WS = {
  ok: false,             // the engine answered GET /api/macros
  macros: null,          // last GET /api/macros
  mDraft: null,          // the macro list being edited (saved with POST /api/macros)
  mDirty: false,
  layouts: null,         // last GET /api/layouts
  lDraft: null,          // { workspaces, zones } being edited
  lDirty: false,
  monSel: "*",           // zone editor: "*" (every monitor without its own) or a monitor id
  recording: null,       // macro index whose hotkey is being recorded
  exes: [],              // program file names seen by the tracker, for the rule picker
};
const clone = (x) => JSON.parse(JSON.stringify(x));

async function wsGet(path) {
  const r = await fetch("api/" + path, { cache: "no-store" });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

// ------------------------------------------------------------------ macros

const STATUS_TEXT = { ok: ["Active", "ok"], taken: ["Taken by another program", "bad"], invalid: ["Not a usable combo", "bad"],
  off: ["Off", ""], none: ["No hotkey", ""], pending: ["Registering…", ""], unsaved: ["Save to apply", ""] };
const NICE = { toggle: "Toggle", on: "Mute", off: "Unmute", play_pause: "Play / pause", next: "Next track", prev: "Previous track",
  stop: "Stop", vol_up: "Volume up", vol_down: "Volume down", vol_mute: "Volume mute", pause: "Pause / resume tracking",
  mini: "Show / hide the mini window", clickthrough: "Mini click-through on / off", apply: "Apply", leave: "Leave" };
const nice = (v) => NICE[v] || v;

// quick-add presets: only action types the engine has today
const PRESETS = [
  ["Mute / unmute microphone", [{ type: "mic_mute", mode: "toggle" }]],
  ["Mute / unmute headset", [{ type: "output_mute", mode: "toggle" }]],
  ["Mute both (going away)", [{ type: "mic_mute", mode: "on" }, { type: "output_mute", mode: "on" }]],
  ["Play / pause music", [{ type: "media", key: "play_pause" }]],
  ["Next track", [{ type: "media", key: "next" }]],
  ["Lock and screens off", [{ type: "lock" }, { type: "wait", ms: 500 }, { type: "screen_off" }]],
  ["Pause tracking", [{ type: "gk", what: "pause" }]],
  ["Show / hide the mini window", [{ type: "gk", what: "mini" }]],
  ["Work mode (apply a workspace)", [{ type: "workspace", id: "", mode: "apply" }]],
];

function newAction(type) {
  const cat = WS.macros.actions.find((a) => a.type === type);
  const a = { type };
  for (const [k, spec] of Object.entries(cat ? cat.fields : {})) {
    if (Array.isArray(spec)) a[k] = spec[0];
    else if (k === "ms") a[k] = 500;
    else if (k === "id") a[k] = (WS.lDraft && WS.lDraft.workspaces[0] && WS.lDraft.workspaces[0].id) || "";
    else a[k] = "";
  }
  return a;
}

function mDirty(on = true) {
  WS.mDirty = on;
  $("mcSave").disabled = !on;
  $("mcDiscard").disabled = !on;
  $("mcState").textContent = on ? "Unsaved changes" : "";
}

// e.code -> the key names the engine accepts
function hotkeyName(e) {
  const c = e.code;
  if (/^Key[A-Z]$/.test(c)) return c.slice(3);
  if (/^Digit\d$/.test(c)) return c.slice(5);
  if (/^F([1-9]|1\d|2[0-4])$/.test(c)) return c;
  if (/^Numpad\d$/.test(c)) return "Num" + c.slice(6);
  return { Space: "Space", Enter: "Enter", Tab: "Tab", Backspace: "Backspace", Pause: "Pause", Home: "Home", End: "End",
    PageUp: "PgUp", PageDown: "PgDn", Insert: "Ins", Delete: "Del", ArrowLeft: "Left", ArrowUp: "Up", ArrowRight: "Right",
    ArrowDown: "Down", PrintScreen: "PrtSc", ScrollLock: "ScrLk" }[c] || null;
}
document.addEventListener("keydown", (e) => {
  if (WS.recording == null) return;
  e.preventDefault();
  e.stopPropagation();
  if (e.code === "Escape" && !(e.ctrlKey || e.altKey || e.metaKey)) { WS.recording = null; renderMacros(); return; }
  const key = hotkeyName(e);
  if (!key) return;      // a modifier on its own: keep listening
  const msg = $("mcHint");
  if (!(e.ctrlKey || e.altKey || e.metaKey)) { msg.textContent = "Use Ctrl, Alt or Win with the key, so plain typing stays yours."; return; }
  const mods = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift", e.metaKey && "Win"].filter(Boolean);
  WS.mDraft[WS.recording].trigger = { type: "hotkey", keys: mods.concat(key).join("+") };
  WS.recording = null;
  msg.textContent = "";
  mDirty();
  renderMacros();
}, true);

function fieldInput(m, a, k, spec) {
  let el;
  if (Array.isArray(spec)) {
    el = h("select", "inp");
    for (const v of spec) { const o = h("option", null, nice(v), el); o.value = v; }
  } else if (k === "id") {          // a workspace
    el = h("select", "inp");
    const list = (WS.lDraft && WS.lDraft.workspaces) || [];
    if (!list.length) h("option", null, "No workspaces yet (Layouts tab)", el).value = "";
    for (const w of list) { const o = h("option", null, w.name, el); o.value = w.id; }
  } else {
    el = h("input", "inp" + (k === "ms" ? " num" : ""));
    if (k === "ms") { el.type = "number"; el.min = 0; el.max = 10000; el.step = 100; }
    el.placeholder = k === "path" ? "C:\\Program Files\\App\\app.exe" : k === "args" ? "arguments (optional)" : "";
  }
  el.value = a[k] == null ? "" : a[k];
  el.dataset.m = m;
  el.dataset.k = k;
  el.setAttribute("aria-label", k);
  return el;
}

function renderMacros() {
  const box = $("mcList"), M = WS.macros;
  if (!M) return;
  const au = M.audio || {};
  const st = (v) => (v == null ? "unknown" : v ? "muted" : "on");
  $("mcAudio").textContent = `Microphone ${st(au.mic_muted)} · Headset ${st(au.output_muted)}`;
  $("mcAudio").className = "priv-badge" + (au.mic_muted ? " bad" : au.mic_muted === false ? " ok" : "");
  box.replaceChildren();
  if (!WS.mDraft.length) h("div", "empty", "No macros yet. Add one, or pick a quick-add preset.", box);
  WS.mDraft.forEach((mc, i) => {
    const card = h("div", "macro", null, box);
    card.dataset.i = i;
    const head = h("div", "macro-head", null, card);
    const sw = h("label", "switch", null, head);
    const cb = h("input", null, null, sw);
    cb.type = "checkbox";
    cb.checked = mc.enabled;
    cb.dataset.act = "enable";
    cb.setAttribute("aria-label", "Macro on");
    h("span", null, null, sw);
    const name = h("input", "inp macro-name", null, head);
    name.value = mc.name;
    name.maxLength = 60;
    name.dataset.act = "name";
    name.setAttribute("aria-label", "Macro name");
    const trig = h("div", "inline", null, head);
    const rec = h("button", "btn" + (WS.recording === i ? " primary" : ""), WS.recording === i ? "Press the keys…"
      : mc.trigger.type === "hotkey" ? mc.trigger.keys : "Set hotkey", trig);
    rec.type = "button";
    rec.dataset.act = "record";
    rec.title = "Click, then press the combo (Esc cancels)";
    if (mc.trigger.type === "hotkey") { const x = h("button", "linkbtn", "clear", trig); x.type = "button"; x.dataset.act = "clear"; }
    const saved = !WS.mDirty && M.macros.some((s) => s.id === mc.id);
    const code = !mc.enabled ? "off" : mc.trigger.type !== "hotkey" ? "none" : saved ? M.status[mc.id] || "pending" : "unsaved";
    const [txt, cls] = STATUS_TEXT[code] || [code, ""];
    h("span", "badge " + cls, txt, trig);
    const tools = h("div", "inline", null, head);
    const run = h("button", "btn", "Run now", tools);
    run.type = "button";
    run.dataset.act = "run";
    run.disabled = !saved;
    run.title = saved ? "Run these actions once" : "Save first";
    const del = h("button", "linkbtn", "remove", tools);
    del.type = "button";
    del.dataset.act = "remove";
    const acts = h("ol", "actions", null, card);
    mc.actions.forEach((a, j) => {
      const li = h("li", null, null, acts);
      li.dataset.j = j;
      const cat = M.actions.find((c) => c.type === a.type);
      h("span", "act-label", cat ? cat.label : a.type, li);
      for (const [k, spec] of Object.entries(cat ? cat.fields : {})) li.appendChild(fieldInput(i, a, k, spec)).dataset.j = j;
      const mv = h("span", "inline act-tools", null, li);
      for (const [act, label, title, off] of [["up", "↑", "Move up", j === 0], ["down", "↓", "Move down", j === mc.actions.length - 1],
        ["drop", "✕", "Remove this step", false]]) {
        const b = h("button", "btn small", label, mv);
        b.type = "button"; b.dataset.act = act; b.title = title; b.setAttribute("aria-label", title); b.disabled = off;
      }
    });
    if (mc.actions.length < 20) {
      const add = h("select", "inp add-act", null, card);
      add.dataset.act = "add";
      add.setAttribute("aria-label", "Add a step");
      h("option", null, "+ Add a step…", add).value = "";
      for (const c of M.actions) { const o = h("option", null, c.label, add); o.value = c.type; }
    }
  });
}

$("mcList").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const i = +b.closest(".macro").dataset.i, mc = WS.mDraft[i], act = b.dataset.act;
  const j = b.closest("li") ? +b.closest("li").dataset.j : -1;
  if (act === "record") { WS.recording = WS.recording === i ? null : i; $("mcHint").textContent = ""; renderMacros(); return; }
  if (act === "clear") mc.trigger = { type: "none" };
  else if (act === "remove") WS.mDraft.splice(i, 1);
  else if (act === "up" || act === "down") { const k = act === "up" ? j - 1 : j + 1; [mc.actions[j], mc.actions[k]] = [mc.actions[k], mc.actions[j]]; }
  else if (act === "drop") mc.actions.splice(j, 1);
  else if (act === "run") {
    b.disabled = true;
    const r = await post("macros/run", { id: mc.id }).catch(() => null);
    b.textContent = r && r.ok ? "Done" : "Didn't run";
    setTimeout(() => loadMacros(), 1200);
    return;
  }
  mDirty();
  renderMacros();
});
$("mcList").addEventListener("change", (e) => {
  const t = e.target, card = t.closest(".macro");
  if (!card) return;
  const mc = WS.mDraft[+card.dataset.i];
  if (t.dataset.act === "enable") mc.enabled = t.checked;
  else if (t.dataset.act === "name") mc.name = t.value.slice(0, 60) || "Macro";
  else if (t.dataset.act === "add") { if (!t.value) return; mc.actions.push(newAction(t.value)); }
  else if (t.dataset.k) {
    const a = mc.actions[+t.dataset.j];
    a[t.dataset.k] = t.dataset.k === "ms" ? Math.max(0, Math.min(10000, Math.round(+t.value || 0))) : t.value;
  } else return;
  mDirty();
  if (t.dataset.act === "add") renderMacros();
});

function newMacroId() {
  let n = WS.mDraft.length + 1;
  while (WS.mDraft.some((m) => m.id === "macro-" + n)) n++;
  return "macro-" + n;
}
$("mcAdd").addEventListener("click", () => {
  WS.mDraft.push({ id: newMacroId(), name: "New macro", enabled: true, trigger: { type: "none" }, actions: [] });
  mDirty();
  renderMacros();
});
$("mcPreset").addEventListener("change", (e) => {
  const p = PRESETS[+e.target.value];
  e.target.value = "";
  if (!p) return;
  const actions = clone(p[1]);
  for (const a of actions) if (a.type === "workspace") a.id = (WS.lDraft && WS.lDraft.workspaces[0] && WS.lDraft.workspaces[0].id) || "";
  WS.mDraft.push({ id: newMacroId(), name: p[0], enabled: true, trigger: { type: "none" }, actions });
  mDirty();
  renderMacros();
});
$("mcSave").addEventListener("click", async () => {
  WS.recording = null;
  const r = await post("macros", { macros: WS.mDraft }).catch(() => null);
  if (!r || !r.macros) { $("mcState").textContent = "Couldn't save."; return; }
  setMacros(r);
});
$("mcDiscard").addEventListener("click", () => { WS.recording = null; setMacros(WS.macros); });

function setMacros(m) {
  WS.macros = m;
  WS.mDraft = clone(m.macros);
  mDirty(false);
  renderMacros();
}
async function loadMacros() {
  try {
    const m = await wsGet("macros");
    if (WS.mDirty || WS.recording != null) { WS.macros.audio = m.audio; WS.macros.status = m.status; renderMacros(); } else setMacros(m);
  } catch (e) { console.error(e); }
}

// ------------------------------------------------------------------ layouts

const SVGNS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs, parent) => {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
};
const pct = (v) => Math.round(v * 100) + "%";
const zoneText = (z) => `${pct(z.x)}, ${pct(z.y)} · ${pct(z.w)} × ${pct(z.h)}`;
const sameZone = (a, b) => ["x", "y", "w", "h"].every((k) => Math.abs((a[k] || 0) - (b[k] || 0)) < 0.002);
const zonesFor = (monId) => WS.lDraft.zones[monId] || WS.lDraft.zones["*"] || [];
const monName = (m) => `Monitor ${m.index + 1}${m.primary ? " (main)" : ""}`;

function lDirty(on = true) {
  WS.lDirty = on;
  $("lySave").disabled = !on;
  $("lyDiscard").disabled = !on;
  $("lyState").textContent = on ? "Unsaved changes" : "";
}

function grid(cols, rows) {
  const out = [];
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) out.push({ x: c / cols, y: r / rows, w: 1 / cols, h: 1 / rows });
  return out.map((z) => Object.fromEntries(Object.entries(z).map(([k, v]) => [k, Math.round(v * 10000) / 10000])));
}
const ZONE_PRESETS = { "halves-h": () => grid(1, 2), "halves-v": () => grid(2, 1), "thirds-v": () => grid(3, 1),
  "thirds-h": () => grid(1, 3), quarters: () => grid(2, 2) };

// every monitor drawn to scale in one SVG; the selected monitor shows its zones
function renderMonitors() {
  const L = WS.layouts, wrap = $("lyMonitors");
  wrap.replaceChildren();
  if (!L.monitors.length) { h("div", "empty", "No monitors reported.", wrap); return; }
  const x0 = Math.min(...L.monitors.map((m) => m.x)), y0 = Math.min(...L.monitors.map((m) => m.y));
  const x1 = Math.max(...L.monitors.map((m) => m.x + m.w)), y1 = Math.max(...L.monitors.map((m) => m.y + m.h));
  const W = x1 - x0, H = y1 - y0, pad = W * 0.01;
  const svg = svgEl("svg", { viewBox: `${-pad} ${-pad} ${W + 2 * pad} ${H + 2 * pad}`, class: "monitors", role: "img",
    "aria-label": "Your monitors to scale" }, wrap);
  for (const m of L.monitors) {
    const g = svgEl("g", { class: "mon" + (WS.monSel === m.id ? " sel" : ""), "data-mon": m.id }, svg);
    svgEl("rect", { x: m.x - x0, y: m.y - y0, width: m.w, height: m.h, rx: W * 0.004, class: "mon-box" }, g);
    const wk = m.work;
    const zs = WS.monSel === m.id || (WS.monSel === "*" && !WS.lDraft.zones[m.id]) ? zonesFor(m.id) : [];
    zs.forEach((z, i) => {
      svgEl("rect", { x: wk.x - x0 + z.x * wk.w, y: wk.y - y0 + z.y * wk.h, width: z.w * wk.w, height: z.h * wk.h, class: "zone" }, g);
      svgEl("text", { x: wk.x - x0 + (z.x + z.w / 2) * wk.w, y: wk.y - y0 + (z.y + z.h / 2) * wk.h, class: "zone-n",
        "font-size": Math.min(wk.w, wk.h) * 0.08, "text-anchor": "middle", "dominant-baseline": "middle" }, g).textContent = i + 1;
    });
    svgEl("text", { x: m.x - x0 + m.w * 0.03, y: m.y - y0 + m.h * 0.09, class: "mon-label", "font-size": Math.min(m.w, m.h) * 0.08 }, g)
      .textContent = `${m.index + 1}${m.primary ? " · main" : ""} · ${m.w}×${m.h}`;
  }
}
$("lyMonitors").addEventListener("click", (e) => {
  const g = e.target.closest("[data-mon]");
  if (!g) return;
  WS.monSel = WS.monSel === g.dataset.mon ? "*" : g.dataset.mon;
  renderLayouts();
});

function renderZones() {
  const L = WS.layouts, sel = WS.monSel, mon = L.monitors.find((m) => m.id === sel);
  $("lyZoneFor").textContent = mon ? `${monName(mon)} (click it again for every monitor)` : "Every monitor without its own zones (click a monitor to give it its own)";
  const list = $("lyZoneList");
  list.replaceChildren();
  const own = sel !== "*" && WS.lDraft.zones[sel];
  zonesFor(sel === "*" ? "*" : sel).forEach((z, i) => h("span", "tier", `${i + 1}: ${zoneText(z)}`, list));
  if (sel !== "*" && !own) h("span", "muted", "Uses the shared zones.", list);
  $("lyZoneReset").hidden = !own;
}
$("lyZonePresets").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-z]");
  if (!b) return;
  const z = b.dataset.z === "grid" ? grid(Math.max(1, Math.min(6, +$("lyCols").value || 2)), Math.max(1, Math.min(4, +$("lyRows").value || 2)))
    : ZONE_PRESETS[b.dataset.z]();
  WS.lDraft.zones[WS.monSel] = z;
  lDirty();
  renderLayouts();
});
$("lyZoneReset").addEventListener("click", () => { delete WS.lDraft.zones[WS.monSel]; lDirty(); renderLayouts(); });

function ruleZoneSelect(r, wi, ri) {
  const s = h("select", "inp");
  const mon = WS.layouts.monitors.find((m) => m.id === r.monitor) || WS.layouts.monitors.find((m) => m.index === r.monitor_index);
  const zs = zonesFor(mon ? mon.id : "*");
  h("option", null, "Maximized", s).value = "max";
  h("option", null, "Whole work area", s).value = "full";
  zs.forEach((z, i) => { h("option", null, `Zone ${i + 1} (${zoneText(z)})`, s).value = "z" + i; });
  let v = r.max ? "max" : sameZone(r.zone, { x: 0, y: 0, w: 1, h: 1 }) ? "full" : "";
  if (!v) { const k = zs.findIndex((z) => sameZone(z, r.zone)); v = k >= 0 ? "z" + k : "custom"; }
  if (v === "custom") h("option", null, `Custom (${zoneText(r.zone)})`, s).value = "custom";
  s.value = v;
  Object.assign(s.dataset, { w: wi, r: ri, f: "zone" });
  s.setAttribute("aria-label", "Where it goes");
  return s;
}

function renderWorkspaces() {
  const L = WS.layouts, box = $("lyWorkspaces");
  box.replaceChildren();
  if (!WS.lDraft.workspaces.length) h("div", "empty", "No workspaces yet. Add one, or arrange your windows and press \"Save what I have now\".", box);
  WS.lDraft.workspaces.forEach((w, wi) => {
    const card = h("div", "macro" + (L.active === w.id ? " active" : ""), null, box);
    card.dataset.w = wi;
    const head = h("div", "macro-head", null, card);
    const name = h("input", "inp macro-name", null, head);
    name.value = w.name;
    name.maxLength = 60;
    Object.assign(name.dataset, { w: wi, f: "name" });
    name.setAttribute("aria-label", "Workspace name");
    if (L.active === w.id) h("span", "badge ok", "Active", head);
    const tools = h("div", "inline", null, head);
    const saved = !WS.lDirty && L.workspaces.some((s) => s.id === w.id);
    for (const [act, label] of [["apply", "Apply"], ["leave", "Leave"], ["wremove", "remove"]]) {
      if (act === "leave" && L.active !== w.id) continue;
      const b = h("button", act === "wremove" ? "linkbtn" : "btn" + (act === "apply" ? " primary" : ""), label, tools);
      b.type = "button";
      b.dataset.act = act;
      if (act === "apply") { b.disabled = !saved || !w.rules.length; b.title = saved ? "Move these programs into place" : "Save first"; }
    }
    const tbl = h("table", "data rules", null, card);
    const hr = h("tr", null, null, h("thead", null, null, tbl));
    for (const c of ["Program", "Monitor", "Where", "Start it if it isn't running", ""]) h("th", null, c, hr);
    const tb = h("tbody", null, null, tbl);
    w.rules.forEach((r, ri) => {
      const tr = h("tr", null, null, tb);
      const exe = h("input", "inp", null, h("td", null, null, tr));
      exe.value = r.exe;
      exe.setAttribute("list", "lyExes");
      exe.placeholder = "code.exe";
      Object.assign(exe.dataset, { w: wi, r: ri, f: "exe" });
      exe.setAttribute("aria-label", "Program file name");
      const ms = h("select", "inp", null, h("td", null, null, tr));
      for (const m of L.monitors) { const o = h("option", null, monName(m), ms); o.value = m.id; }
      const mon = L.monitors.find((m) => m.id === r.monitor) || L.monitors.find((m) => m.index === r.monitor_index);
      if (!mon) h("option", null, `Monitor ${r.monitor_index + 1} (not connected)`, ms).value = r.monitor || "?";
      ms.value = mon ? mon.id : r.monitor || "?";
      Object.assign(ms.dataset, { w: wi, r: ri, f: "monitor" });
      ms.setAttribute("aria-label", "Monitor");
      h("td", null, null, tr).appendChild(ruleZoneSelect(r, wi, ri));
      const ln = h("input", "inp", null, h("td", null, null, tr));
      ln.value = r.launch || "";
      ln.placeholder = "optional: full path to start it";
      Object.assign(ln.dataset, { w: wi, r: ri, f: "launch" });
      ln.setAttribute("aria-label", "Start command");
      const x = h("button", "linkbtn", "remove", h("td", null, null, tr));
      x.type = "button";
      x.dataset.act = "rremove";
      x.dataset.r = ri;
    });
    const add = h("button", "btn mt-s", "+ Add a program", card);
    add.type = "button";
    add.dataset.act = "radd";
  });
}

$("lyWorkspaces").addEventListener("change", (e) => {
  const t = e.target, f = t.dataset.f;
  if (!f) return;
  const w = WS.lDraft.workspaces[+t.dataset.w], r = t.dataset.r != null ? w.rules[+t.dataset.r] : null;
  if (f === "name") w.name = t.value.slice(0, 60) || "Workspace";
  else if (f === "exe") r.exe = t.value.trim().toLowerCase();
  else if (f === "launch") r.launch = t.value;
  else if (f === "monitor") {
    const m = WS.layouts.monitors.find((x) => x.id === t.value);
    if (m) { r.monitor = m.id; r.monitor_index = m.index; }
  } else if (f === "zone") {
    const zs = zonesFor(r.monitor && WS.lDraft.zones[r.monitor] ? r.monitor : "*");
    if (t.value === "max") { r.max = true; r.zone = { x: 0, y: 0, w: 1, h: 1 }; }
    else if (t.value === "full") { r.max = false; r.zone = { x: 0, y: 0, w: 1, h: 1 }; }
    else if (t.value[0] === "z") { r.max = false; r.zone = clone(zs[+t.value.slice(1)]); }
  }
  lDirty();
  if (f === "monitor") renderWorkspaces();
});
$("lyWorkspaces").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const wi = +b.closest("[data-w]").dataset.w, w = WS.lDraft.workspaces[wi], act = b.dataset.act;
  if (act === "apply" || act === "leave") {
    b.disabled = true;
    const r = await post(act === "apply" ? "layouts/apply" : "layouts/leave", act === "apply" ? { id: w.id } : {}).catch(() => null);
    b.textContent = r && r.ok ? "Done" : "Didn't work";
    setTimeout(loadLayouts, 1200);
    return;
  }
  if (act === "wremove") WS.lDraft.workspaces.splice(wi, 1);
  else if (act === "rremove") w.rules.splice(+b.dataset.r, 1);
  else if (act === "radd") {
    const m = WS.layouts.monitors.find((x) => x.primary) || WS.layouts.monitors[0] || { id: "", index: 0 };
    const z = zonesFor(m.id)[0] || { x: 0, y: 0, w: 1, h: 1 };
    w.rules.push({ exe: "", monitor: m.id, monitor_index: m.index, zone: clone(z), max: false, launch: "" });
  }
  lDirty();
  renderWorkspaces();
});
$("lyAdd").addEventListener("click", () => {
  let n = WS.lDraft.workspaces.length + 1;
  while (WS.lDraft.workspaces.some((w) => w.id === "workspace-" + n)) n++;
  WS.lDraft.workspaces.push({ id: "workspace-" + n, name: "New workspace", rules: [] });
  lDirty();
  renderWorkspaces();
});
$("lySave").addEventListener("click", async () => {
  const r = await post("layouts", { workspaces: WS.lDraft.workspaces, zones: WS.lDraft.zones }).catch(() => null);
  if (!r || !r.workspaces) { $("lyState").textContent = "Couldn't save."; return; }
  setLayouts(r);
});
$("lyDiscard").addEventListener("click", () => setLayouts(WS.layouts));
$("lyCapture").addEventListener("click", async () => {
  if (WS.lDirty) { $("lyState").textContent = "Save or discard your changes first."; return; }
  const name = $("lyCaptureName").value.trim() || "Saved layout";
  const r = await post("layouts/capture", { name }).catch(() => null);
  if (!r || !r.workspaces) { $("lyState").textContent = "Couldn't save the windows."; return; }
  $("lyCaptureName").value = "";
  setLayouts(r);
});

function renderLayouts() {
  if (!WS.layouts) return;
  renderMonitors();
  renderZones();
  renderWorkspaces();
}
function setLayouts(L) {
  WS.layouts = L;
  WS.lDraft = { workspaces: clone(L.workspaces), zones: clone(L.zones) };
  if (WS.monSel !== "*" && !L.monitors.some((m) => m.id === WS.monSel)) WS.monSel = "*";
  lDirty(false);
  renderLayouts();
  if (WS.mDraft) renderMacros();      // the workspace pickers in macro steps
}
async function loadLayouts() {
  try {
    const [L, g] = await Promise.all([wsGet("layouts"), WS.exes.length ? null : wsGet("games").catch(() => null)]);
    if (g) {
      WS.exes = g.apps.map((a) => a.exe).filter(Boolean);
      const dl = $("lyExes");
      dl.replaceChildren();
      for (const x of WS.exes) h("option", null, null, dl).value = x;
    }
    if (WS.lDirty) { WS.layouts.active = L.active; WS.layouts.monitors = L.monitors; renderLayouts(); } else setLayouts(L);
  } catch (e) { console.error(e); }
}

// ------------------------------------------------------------------ tabs (only when the engine answers)

TABS.macros = { noRange: true, async load() { if (!WS.ok) return {}; await Promise.all([loadMacros(), WS.layouts ? null : loadLayouts()]); return {}; }, render() {} };
TABS.layouts = { noRange: true, async load() { if (WS.ok) await loadLayouts(); return {}; }, render() {} };

let audioT;
function pollAudio() {      // the mute state can change from the keyboard while the tab is open
  clearTimeout(audioT);
  if (state.tab !== "macros" || document.hidden) return;
  audioT = setTimeout(() => { loadMacros().then(pollAudio); }, 3000);
}
$("tabs").addEventListener("click", () => setTimeout(pollAudio, 0));

// one probe: an older tracker answers 404 (one console line), and both tabs stay hidden
WS.probed = (async () => {
  let ok = false;
  try { ok = (await fetch("api/macros", { cache: "no-store" })).ok; } catch (e) { /* tracker not running */ }
  await layoutReady;      // the settings call marks a phone-link (view-only) page
  const viewOnly = document.documentElement.classList.contains("view-only");
  WS.engine = ok;        // the Rust engine (netapps.js skips its own probe without it)
  WS.ok = ok && !viewOnly;
  for (const t of ["macros", "layouts"]) document.querySelector(`.tab[data-tab="${t}"]`).hidden = !ok || viewOnly;
  if (ok && !viewOnly) { if (state.tab === "macros" || state.tab === "layouts") { loadTab(true); pollAudio(); } }
  else if (state.tab === "macros" || state.tab === "layouts") showTab("overview");
})();
