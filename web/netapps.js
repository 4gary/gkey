/* gkeystrokes dashboard: per-program network use on the Network tab (the engine's /api/net_apps).
   An older tracker answers 404: the Network tab stays as it was and the Settings switch stays hidden. */
"use strict";

const NA = {
  ok: false,         // the engine answered /api/net_apps
  pick: store.get("gk-na-pick", ""),       // the program shown in the one-program chart
  names: {},         // exe -> display name
  nowT: 0,
};
const naName = (exe) => NA.names[exe] || exe;

async function naGet(path) {
  const r = await fetch("api/" + path, { cache: "no-store" });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}
// rows [bucket, exe, up, down] -> {exe: [{b, up, down}]}
function naByApp(series) {
  const out = {};
  for (const [b, exe, up, down] of series || []) (out[exe] = out[exe] || []).push({ b, up, down });
  return out;
}

// ------------------------------------------------------------------ charts (step 5 cards, with their own pickers)

const naUrl = (i) => `net_apps?${i.q}&bucket=${i.bucket}`;
const NA_CARDS = {
  naSeries: { tab: "network", view: "bars", page: (d) => d.na, url: naUrl,
    draw(r, i) {
      const by = naByApp(r.series), tot = {};
      for (const exe in by) tot[exe] = by[exe].reduce((a, x) => a + x.down, 0);
      const top = Object.keys(tot).filter((e) => tot[e] > 0).sort((a, b) => tot[b] - tot[a]).slice(0, 5);
      const colors = ["--s1", "--s2", "--s3", "--s4", "--ink-2"];
      const series = top.map((exe, k) => ({ name: naName(exe), color: css(colors[k]), values: fill(i, by[exe], "down") }));
      const rest = (r.series || []).filter(([, exe]) => !top.includes(exe)).map(([b, , up, down]) => ({ b, up, down }));
      const other = {};
      for (const x of rest) other[x.b] = (other[x.b] || 0) + x.down;
      if (rest.some((x) => x.down > 0)) series.push({ name: "Other", color: css("--muted"), values: i.keys.map((k) => other[k] || 0) });
      return { series, stacked: true, vFmt: fmt.bytes, tickMode: "bytes", yFmt: noSpace(fmt.bytes),
        emptyMsg: "No per-program traffic in this range yet." };
    } },
  naOne: { tab: "network", page: (d) => d.na, url: naUrl,
    draw(r, i) {
      const by = naByApp(r.series), exe = by[NA.pick] ? NA.pick : (r.apps[0] || {}).exe;
      $("naOneTitle").textContent = exe ? naName(exe) : "One program";
      const rows = by[exe] || [];
      return { series: [{ name: "Download ↓", color: css("--s3"), values: fill(i, rows, "down") },
        { name: "Upload ↑", color: css("--s4"), values: fill(i, rows, "up") }],
      vFmt: fmt.bytes, tickMode: "bytes", yFmt: noSpace(fmt.bytes), emptyMsg: "Nothing from this program in this range." };
    } },
};

// ------------------------------------------------------------------ the list, the live table, Windows history

function naRender(d, info) {
  const na = d.na;
  if (!na) return;
  for (const a of na.apps) NA.names[a.exe] = a.name || a.exe;
  $("naAdmin").hidden = na.admin;
  const when = (t) => new Date(t * 1000).toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  $("naSrum").textContent = na.srum_at
    ? `History from Windows loaded ${when(na.srum_at)}${na.srum_until ? `, covering up to ${when(na.srum_until)}; counted live after that` : ""}.`
    : na.admin ? "No history from Windows loaded yet. Windows keeps about a month of it." : "";
  $("naBackfill").hidden = !na.admin;
  GK.barList($("naTop"), na.apps.filter((a) => a.up + a.down > 0).slice(0, 12).map((a) => {
    const b = h("button", "linkbtn", a.name || a.exe);
    b.type = "button";
    b.dataset.exe = a.exe;
    b.title = a.exe;
    return { label: a.name || a.exe, node: b, value: a.down + a.up, display: `${fmt.bytes(a.down)} ↓ · ${fmt.bytes(a.up)} ↑` };
  }), { color: css("--s3"), labelW: 150, emptyMsg: na.admin ? "No per-program traffic in this range yet." : "Nothing recorded yet." });
  if (d.naHour) {          // download bursts: the program that downloaded most in each hour
    const tops = {}, best = {};
    for (const [b, exe, , down] of d.naHour.series) if (down > (best[b] || 0)) { best[b] = down; tops[b] = naName(exe); }
    renderBursts(d.bursts, tops);
  }
}
$("naTop").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-exe]");
  if (!b) return;
  NA.pick = b.dataset.exe;
  store.set("gk-na-pick", NA.pick);
  redrawCard("naOne");
  $("naCharts").scrollIntoView({ behavior: "smooth", block: "nearest" });
});
$("naToSettings").addEventListener("click", () => {
  showTab("settings");
  setTimeout(() => $("setAdminRow").scrollIntoView({ behavior: "smooth", block: "center" }), 300);
});
$("naBackfill").addEventListener("click", async (e) => {
  const b = e.target;
  b.disabled = true;
  const r = await post("net_apps/backfill", {}).catch(() => null);
  b.textContent = r && r.started ? "Loading… (takes a minute)" : "Couldn't start";
  setTimeout(() => { b.disabled = false; b.textContent = "Load Windows history"; loadTab(true); }, 60000);
});

// live speeds per program, every 2.5 s while the Network tab is on screen
async function naNow() {
  clearTimeout(NA.nowT);
  if (!NA.ok || state.tab !== "network" || document.hidden) return;
  try {
    const r = await naGet("net_apps_now");
    const tbl = $("naNow");
    tbl.replaceChildren();
    if (!r.admin) tbl.appendChild(h("caption", "empty", "Needs Run as administrator."));
    else if (!r.apps.length) tbl.appendChild(h("caption", "empty", "Nothing is using the network right now."));
    else {
      const tr = h("tr", null, null, h("thead", null, null, tbl));
      [["Program"], ["↓", 1], ["↑", 1]].forEach(([l, n]) => h("th", n ? "num" : "", l, tr));
      const tb = h("tbody", null, null, tbl);
      for (const a of r.apps.slice(0, 12)) {
        const row = h("tr", null, null, tb);
        h("td", null, naName(a.exe), row).title = a.exe;
        h("td", "num", fmt.speed(a.down), row);
        h("td", "num", fmt.speed(a.up), row);
      }
    }
  } catch (e) { /* tracker restarting: try again on the next tick */ }
  NA.nowT = setTimeout(naNow, 2500);
}

// ------------------------------------------------------------------ Settings: run as administrator

let naTask = null;      // is the "run as administrator" sign-in task set up
function naSettings() {
  $("setAdminRow").hidden = !NA.ok || document.documentElement.classList.contains("view-only");
  if (naTask != null) $("setAdmin").checked = naTask;
}
$("setAdmin").addEventListener("change", async (e) => {
  const on = e.target.checked;
  const msg = on ? "Windows asks once for administrator rights, then gkeystrokes restarts elevated to read per-program network use. Go ahead?"
    : "Windows asks once for administrator rights to remove the task, then gkeystrokes restarts normally. Go ahead?";
  if (!confirm(msg)) { e.target.checked = !on; return; }
  e.target.disabled = true;
  const res = await fetch("api/net_apps/admin", { method: "POST", headers: { "Content-Type": "application/json", "X-GK": "1" },
    body: JSON.stringify({ on }) }).catch(() => null);
  const r = res ? await res.json().catch(() => ({})) : {};
  e.target.disabled = false;
  const note = $("setAdminNote"), was = note.dataset.text || (note.dataset.text = note.textContent);
  if (res && res.ok) { naTask = on; note.textContent = r.restarting ? "Restarting… this page reconnects by itself." : "Saved."; setTimeout(() => { note.textContent = was; }, 4000); }
  else { e.target.checked = !on; note.textContent = "Couldn't change it" + (r.error ? ": " + r.error : "."); }
});

// ------------------------------------------------------------------ hook into the Network and Settings tabs

{
  const net = TABS.network, set = TABS.settings;
  const netLoad = net.load, netRender = net.render, setRender = set.render;
  net.load = async (info) => {
    const d = await netLoad(info);
    if (!NA.ok) return d;
    const days = (info.to - info.from) / 864e5;
    [d.na, d.naHour] = await Promise.all([naGet(naUrl(info)).catch(() => null),
      days <= 31 && info.bucket !== "hour" ? naGet(`net_apps?${info.q}&bucket=hour`).catch(() => null) : null]);
    if (d.na && info.bucket === "hour") d.naHour = d.na;
    if (d.na) naTask = !!d.na.admin_task;
    return d;
  };
  net.render = (d, info) => { netRender(d, info); naRender(d, info); naNow(); };
  set.render = (d) => { setRender(d); naSettings(); };
}

// one probe: an older tracker answers 404 (one console line) and the page stays as it was
(async () => {
  await WS.probed;
  if (!WS.engine) return;      // an older tracker: one 404 (from the macros probe) is enough
  let r = null;
  try { const n = Math.floor(Date.now() / 1000); r = await naGet(`net_apps?from=${n - 3600}&to=${n}&bucket=hour`); } catch (e) { /* 404 */ }
  NA.ok = !!r;
  if (!r) return;
  naTask = !!r.admin_task;
  Object.assign(CARDS, NA_CARDS);
  $("naNone").hidden = true;
  $("naBox").hidden = false;
  $("naCharts").hidden = false;
  naSettings();
  if (state.tab === "network" || state.tab === "settings") loadTab(true);
})();
document.addEventListener("visibilitychange", () => { if (!document.hidden) naNow(); });
$("tabs").addEventListener("click", () => setTimeout(naNow, 0));
