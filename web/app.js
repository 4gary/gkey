/* gkeystrokes dashboard */
"use strict";

const { fmt, tip, h, css } = GK;
const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
};

const state = {
  tab: (location.hash || "#overview").slice(1),
  range: store.get("gk-range", "today"),
  mode: store.get("gk-mode", "all"),       // all | normal | gaming
  live: null,
  data: {},          // last fetched data per tab (+ its JSON), so resize/theme can re-render without refetching
  recent: [],        // tabs shown, most recent first; only these keep their data
  calendar: null,
  calendarAt: 0,
};

async function api(path) {
  const r = await fetch("api/" + path, { cache: "no-store" });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

async function post(path, body) {
  const r = await fetch("api/" + path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-GK": "1" }, body: JSON.stringify(body),
  });
  return r.json();
}

// ------------------------------------------------------------------ ranges & buckets

const pad2 = (n) => String(n).padStart(2, "0");
const dkey = GK.dkey;
const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const hourLabel = (hr) => (hr % 12 || 12) + (hr < 12 ? " am" : " pm");
const shortDate = (d) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
const longDate = (d) => d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });

const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };

// ranges: today | 7d | 30d | year | all (page), 1h | 24h (rolling, per card), day:YYYY-MM-DD (a clicked day)
function rangeInfo(range) {
  const now = new Date();
  const mid = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const hNow = Math.floor(now / 3.6e6);
  // counts are stored per hour, so "1h" is this hour so far next to the one before
  if (range === "1h") return spanInfo(range, new Date((hNow - 1) * 3.6e6), new Date((hNow + 1) * 3.6e6), "hour", "This hour and the last");
  if (range === "month") {
    const m0 = new Date(now.getFullYear(), now.getMonth(), 1);
    return spanInfo(range, m0, new Date(now.getFullYear(), now.getMonth() + 1, 1), "day", m0.toLocaleDateString(undefined, { month: "long", year: "numeric" }));
  }
  if (range === "24h") return spanInfo(range, new Date((hNow - 23) * 3.6e6), new Date((hNow + 1) * 3.6e6), "hour", "Last 24 hours");
  if (range === "today") return spanInfo(range, mid, addDays(mid, 1), "hour", longDate(mid));
  if (/^day:\d{4}-\d\d-\d\d$/.test(range)) {
    const d = parseDay(range.slice(4));
    return spanInfo(range, d, addDays(d, 1), "hour", longDate(d));
  }
  let days;
  if (range === "7d") days = 7;
  else if (range === "30d") days = 30;
  else if (range === "year") days = 364;
  else {
    const first = state.live && state.live.first_day ? parseDay(state.live.first_day) : addDays(mid, -29);
    days = Math.max(1, Math.round((mid - first) / 864e5) + 1);
  }
  const from = addDays(mid, -(days - 1));
  const bucket = range === "year" ? "week" : range === "all" ? (days <= 92 ? "day" : days <= 730 ? "week" : "month") : "day";
  return spanInfo(range, from, addDays(mid, 1), bucket, `${shortDate(from)} – ${shortDate(mid)}`);
}

// bucket keys, axis labels and tooltips for [from, to); cut = index of the bucket holding "now" (later ones haven't happened)
function spanInfo(range, from, to, bucket, text) {
  bucket = fitBucket(from, to, bucket);
  const keys = [], labels = [], tips = [], starts = [];
  const days = Math.round((to - from) / 864e5);
  if (bucket === "hour") {
    for (let hh = Math.floor(from / 3.6e6); hh < Math.floor(to / 3.6e6); hh++) {
      const d = new Date(hh * 3.6e6);
      keys.push(hh); starts.push(+d);
      labels.push(d.getHours() % 6 === 0 ? hourLabel(d.getHours()).replace(" ", "") : "");
      tips.push(`${range === "today" || range.startsWith("day:") ? "" : shortDate(d) + ", "}${hourLabel(d.getHours())} – ${hourLabel((d.getHours() + 1) % 24)}`);
    }
  } else if (bucket === "day") {
    for (let d = new Date(from); d < to; d = addDays(d, 1)) {
      keys.push(dkey(d)); starts.push(+d);
      labels.push(days <= 7 ? d.toLocaleDateString(undefined, { weekday: "short" }) : shortDate(d));
      tips.push(longDate(d));
    }
  } else if (bucket === "week") {
    for (let d = addDays(from, -((from.getDay() + 6) % 7)); d < to; d = addDays(d, 7)) {
      keys.push(dkey(d)); starts.push(+d);
      labels.push(d.getDate() <= 7 ? GK.MONTHS[d.getMonth()] : "");
      tips.push("Week of " + shortDate(d));
    }
  } else {
    for (let d = new Date(from.getFullYear(), from.getMonth(), 1); d < to; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
      keys.push(`${d.getFullYear()}-${pad2(d.getMonth() + 1)}`); starts.push(+d);
      labels.push(GK.MONTHS[d.getMonth()] + (d.getMonth() === 0 ? " " + d.getFullYear() : ""));
      tips.push(d.toLocaleDateString(undefined, { month: "long", year: "numeric" }));
    }
  }
  let cut = starts.length - 1;
  while (cut > 0 && starts[cut] > Date.now()) cut--;
  const q = `from=${from.getTime() / 1000}&to=${to.getTime() / 1000}`;
  return { range, from, to, bucket, keys, labels, tips, starts, q, text, cut, live: +to > Date.now(), labelW: bucket === "hour" ? 30 : 46 };
}

// the server refuses a series over 50,000 points: step up to the next bucket until the range fits
const SERIES_MAX = 50000;
const BUCKET_H = { hour: 1, day: 24, week: 168, month: 720 };
const BIGGER = { hour: "day", day: "week", week: "month" };
function fitBucket(from, to, bucket) {
  while (BIGGER[bucket] && (to - from) / 3.6e6 / BUCKET_H[bucket] > SERIES_MAX) bucket = BIGGER[bucket];
  return bucket;
}

// the same-length period just before `info`, for "compare to previous period"
function prevInfo(info) {
  if (info.bucket === "hour") {
    const ms = info.to - info.from;
    return spanInfo("prev", new Date(info.from - ms), info.from, "hour", "");
  }
  const days = Math.round((info.to - info.from) / 864e5);
  return spanInfo("prev", addDays(info.from, -days), info.from, info.bucket, "");
}

// map server series rows onto the full bucket list (missing buckets = 0)
function fill(info, series, field, transform) {
  const by = {};
  for (const r of series) by[r.b] = r;
  return info.keys.map((k) => { const r = by[k]; return r ? (transform ? transform(r) : r[field] || 0) : 0; });
}
// for averages and peaks: a bucket without a reading is a gap (null), not a zero
function fillGap(info, series, transform) {
  const by = {};
  for (const r of series) by[r.b] = r;
  return info.keys.map((k) => (by[k] ? transform(by[k]) : null));
}

// ------------------------------------------------------------------ /api/series: any metric, any range
// One dense point per bucket ({t, b, metric: value}). A tracker without the endpoint answers 404; then the same
// points are rebuilt from /api/summary, /api/power and /api/system, so the page works against either.

let seriesOK = null, seriesNo = 0;      // null = not asked yet; a 404 is asked again after 5 minutes or a tracker restart
const FB_SUM = ["keys", "clicks", "active_s", "mouse_mm", "scroll", "typed", "typing_ms", "up", "down"];
const FB_POW = ["wh", "gpu_wh", "cpu_wh", "other_wh", "display_wh", "gpu_util", "gpu_temp_max"];

async function seriesLoad(metrics, info, mode = "all") {
  const q = `${info.q}&bucket=${info.bucket}${mode === "all" ? "" : "&mode=" + mode}`;
  if (seriesOK === false && Date.now() - seriesNo > 300000) seriesOK = null;
  if (seriesOK !== false) {
    const r = await fetch("api/series?metrics=" + encodeURIComponent(metrics.join(",")) + "&" + q, { cache: "no-store" });
    if (r.ok) { seriesOK = true; return r.json(); }
    if (r.status !== 404) throw new Error("series -> " + r.status);
    seriesOK = false;
    seriesNo = Date.now();
  }
  const want = (list) => metrics.some((m) => list.includes(m));
  const sysKeys = metrics.filter((m) => /^m:.+:(avg|max|sum)$/.test(m));
  const [sum, pow, sys] = await Promise.all([
    want(FB_SUM) ? api(`summary?${q}`) : null,
    want(FB_POW) ? api(`power?${info.q}&bucket=${info.bucket}`) : null,
    sysKeys.length ? api(`system?${info.q}&bucket=${info.bucket}`) : null]);
  const by = (r) => { const o = {}; if (r) for (const x of r.series) o[x.b] = x; return o; };
  const S = by(sum), P = by(pow), Y = by(sys);
  const ok = metrics.filter((m) => FB_SUM.includes(m) || FB_POW.includes(m) || sysKeys.includes(m));
  const points = [];
  for (let i = 0; i <= info.cut; i++) {
    const b = info.keys[i], pt = { t: info.starts[i] / 1000, b };
    for (const m of ok) {
      if (FB_SUM.includes(m)) pt[m] = (S[b] && S[b][m]) || 0;
      else if (m === "wh") pt[m] = P[b] ? sumEnergy(P[b]) : 0;
      else if (m === "gpu_util" || m === "gpu_temp_max") pt[m] = P[b] && P[b][m] ? P[b][m] : null;
      else if (FB_POW.includes(m)) pt[m] = (P[b] && P[b][m]) || 0;
      else {
        const [, k, kind] = m.match(/^m:(.+):(avg|max|sum)$/), v = Y[b] && Y[b][k];
        pt[m] = kind === "sum" ? (v ? v[2] : 0) : v ? v[kind === "avg" ? 0 : 1] : null;
      }
    }
    points.push(pt);
  }
  return { bucket: info.bucket, from: info.from / 1000, to: info.to / 1000, metrics: ok,
    unknown: metrics.filter((m) => !ok.includes(m)), points, fallback: true };
}

// series points in the {series: [{b, ...}], totals} shape the summary-based charts read; totals are exact sums
function seriesRows(resp) {
  const totals = {};
  for (const m of resp.metrics) totals[m] = resp.points.reduce((a, p) => a + (p[m] || 0), 0);
  return { series: resp.points, totals };
}

const MODE_TABS = new Set(["overview", "keyboard", "mouse", "apps"]);
const powerCfg = () => (state.live && state.live.power_cfg) || { price: 0.2, currency: "$" };
const cost = (wh) => fmt.money(((wh || 0) / 1000) * powerCfg().price, powerCfg().currency);
const ENERGY_PARTS = [["gpu_wh", "GPU", "--s1"], ["cpu_wh", "CPU", "--s2"], ["other_wh", "Rest of PC", "--s3"],
  ["display_wh", "Monitors", "--s4"]];
const sumEnergy = (e) => (e ? ENERGY_PARTS.reduce((a, [k]) => a + (e[k] || 0), 0) : 0);
const mq = () => (state.mode === "all" ? "" : "&mode=" + state.mode);
const clicksOf = (t) => (t.lclicks || 0) + (t.rclicks || 0) + (t.mclicks || 0) + (t.xclicks || 0);
const wpmOf = (typed, ms) => (ms > 5000 ? typed / 5 / (ms / 60000) : 0);

// ------------------------------------------------------------------ tiles

function tile(container, { label, value, unit, foot, color }) {
  const t = h("div", "tile", null, container);
  const l = h("div", "label", null, t);
  const key = color ? h("span", "key", null, l) : null;
  if (key) key.style.background = color;
  const labelNode = document.createTextNode(label);
  l.appendChild(labelNode);
  const v = h("div", "value", null, t);
  const valueNode = document.createTextNode(value == null ? "" : String(value));
  v.appendChild(valueNode);
  const unitEl = unit ? h("small", null, unit, v) : null;
  const footEl = foot ? h("div", "foot", foot, t) : null;
  t.parts = { shape: [!!color, !!unit, !!foot].join(), key, label: labelNode, value: valueNode, unit: unitEl, foot: footEl };
  return t;
}

function tiles(id, list, fixed = true) {
  const c = $(id);
  c.replaceChildren();
  if (fixed) {        // keep rows even: 8 tiles -> 8 / 4 / 2 per row as the window narrows
    c.classList.add("fixed");
    c.style.setProperty("--cols", Math.min(list.length, 8));
    c.style.setProperty("--cols-md", Math.ceil(Math.min(list.length, 8) / 2));
  }
  list.forEach((x) => tile(c, x));
}

// live tiles update every second: rewrite the text where it stands; false if the tile's shape changed
function setTile(t, { label, value, unit, foot, color }) {
  const p = t.parts;
  if (!p || p.shape !== [!!color, !!unit, !!foot].join()) return false;
  if (p.key) p.key.style.background = color;
  if (p.label.nodeValue !== label) p.label.nodeValue = label;
  const text = value == null ? "" : String(value);
  if (p.value.nodeValue !== text) p.value.nodeValue = text;
  if (p.unit && p.unit.textContent !== unit) p.unit.textContent = unit;
  if (p.foot && p.foot.textContent !== foot) p.foot.textContent = foot;
  return true;
}

// rebuild only when the list length or a tile's shape changes, so the 1 s ticks do not churn the DOM
function liveTiles(c, list) {
  const kids = c.children;
  let ok = kids.length === list.length;
  for (let i = 0; ok && i < list.length; i++) ok = setTile(kids[i], list[i]);
  if (!ok) { c.replaceChildren(); list.forEach((x) => tile(c, x)); }
}

// ------------------------------------------------------------------ live (1 s)

function renderLive() {
  const L = state.live;
  if (!L) return;
  const st = $("status");
  st.classList.toggle("paused", L.paused);
  $("statusText").textContent = L.paused ? "Paused" : L.idle ? "Recording (idle)" : "Recording";
  $("pauseBtn").textContent = L.paused ? "Resume" : "Pause";
  $("setPaused").checked = L.paused;

  if (state.tab === "overview") {
    const t = L.today, a = L.alltime;
    $("lvKeys").textContent = fmt.int(t.keys);
    $("lvKeysMeta").textContent = `≈ ${fmt.int(t.typed / 5)} words · ${fmt.num(a.keys)} all time`;
    $("lvApp").textContent = L.idle ? `${L.app.name} (idle)` : L.app.name;
    const clicks = clicksOf(t);
    liveTiles($("liveTiles"), [
      { label: "Clicks", value: fmt.int(clicks), foot: `${fmt.int(t.lclicks)} left · ${fmt.int(t.rclicks)} right` },
      { label: "Typing speed now", value: Math.round(L.now.wpm), unit: "wpm", foot: `peak today ${Math.round(t.peak_wpm)} wpm` },
      { label: "Power now", value: L.power ? fmt.watts(L.power.total_w) : "–",
        foot: `today ${fmt.energy(sumEnergy(L.today_energy))} · ${cost(sumEnergy(L.today_energy))}` },
      { label: "Mouse distance", value: fmt.dist(t.mouse_mm), foot: `${fmt.int(t.scroll)} scroll notches` },
      { label: "Downloaded", value: fmt.bytes(t.down), foot: `now ${fmt.speed(L.now.down)}`, color: css("--s3") },
      { label: "Uploaded", value: fmt.bytes(t.up), foot: `now ${fmt.speed(L.now.up)}`, color: css("--s4") },
      { label: "Active time", value: fmt.dur(t.active_s), foot: `of ${fmt.dur(t.uptime_s)} tracked · PC up ${fmt.dur(L.pc_uptime_s)}` },
      { label: "Gaming today", value: fmt.dur(L.today_game.active_s),
        foot: L.gaming ? `playing ${L.gaming.name}` : `${fmt.int(L.today_game.keys)} keys · ${fmt.int(L.today_game.clicks)} clicks` },
    ]);
    renderSparks();
  }
  if (state.tab === "network") renderNetLive();
  if (state.tab === "gaming") renderGamingLive();
  if (state.tab === "power") renderPowerLive();
  if (state.tab === "system") renderSystemLive();
  const g = L.gaming, chip = $("gameChip");
  chip.hidden = !g;
  if (g) chip.textContent = `🎮 ${g.name} · ${fmt.dur(Date.now() / 1000 - g.start)}`;
}

function renderNetLive() {
  const L = state.live;
  if (!L) return;
  const hist = L.net_hist;
  $("netNow").textContent = `↓ ${fmt.speed(L.now.down)}   ↑ ${fmt.speed(L.now.up)}`;
  const now = Date.now() / 1000;
  const xs = hist.map((p) => p[0]);
  if (liveOn("netLive")) GK.area($("netLive"), {
    xs, xMin: now - 120, xMax: now, height: 170, yFmt: fmt.speed, tickMode: "bits",
    xFmt: (x) => new Date(x * 1000).toLocaleTimeString(),
    xTicks: [[now - 120, "2 min ago"], [now - 60, "1 min ago"], [now - 1, "now"]],
    series: [
      { name: "Download", color: css("--s3"), values: hist.map((p) => p[2]) },
      { name: "Upload", color: css("--s4"), values: hist.map((p) => p[1]) },
    ],
  });
}

function renderPowerLive() {
  const L = state.live, el = $("pwLive");
  if (!L || !el) return;
  const P = L.power;
  el.replaceChildren();
  if (!P) { h("div", "meta", "Waiting for the first GPU and power reading…", el); return; }
  const g = P.gpu || {};
  const a = h("div", null, null, el);
  h("div", "label", g.name ? `GPU · ${g.name}` : "GPU", a);
  const big = h("div", "big", Math.round(P.util) + "%", a);
  h("small", null, "load", big);
  const bits = [];
  if (g.temp != null) bits.push(`${g.temp}°C`);
  if (g.vram_mb != null) bits.push(`${(g.vram_mb / 1024).toFixed(1)} / ${Math.round(g.vram_total_mb / 1024)} GB VRAM`);
  if (g.clock_mhz) bits.push(`${fmt.int(g.clock_mhz)} MHz`);
  if (g.fan != null) bits.push(`fan ${g.fan}%`);
  h("div", "meta", bits.join(" · "), a);
  const b = h("div", null, null, el);
  h("div", "label", "Whole PC right now", b);
  const bw = h("div", "big", fmt.int(P.total_w), b);
  h("small", null, "watts", bw);
  const perHour = (P.total_w / 1000) * powerCfg().price;
  h("div", "meta", `${fmt.money(perHour, powerCfg().currency)} per hour · ${fmt.money(perHour * 24 * 30, powerCfg().currency)}/month if left like this`, b);
  const E = sumEnergy(L.today_energy);
  h("div", "meta", `Today so far: ${fmt.energy(E)} · ${cost(E)}`, b).style.marginTop = "6px";
  const c = h("div", null, null, el);
  h("div", "label", `Where it's going · CPU ${Math.round(P.cpu_pct)}% busy`, c);
  const list = h("div", null, null, c);
  list.style.marginTop = "10px";
  const tag = (ok) => (ok ? "measured" : "estimate");
  GK.barList(list, [
    { label: "GPU", value: P.gpu_w, display: `${fmt.watts(P.gpu_w)} · ${tag(P.gpu_measured)}`, color: css("--s1") },
    { label: "CPU", value: P.cpu_w, display: `${fmt.watts(P.cpu_w)} · ${tag(P.cpu_measured)}`, color: css("--s2") },
    { label: "Rest of PC", value: P.other_w, display: `${fmt.watts(P.other_w)} · estimate`, color: css("--s3") },
    { label: "Monitors", value: P.display_w, display: P.display_w ? `${fmt.watts(P.display_w)} · estimate` : "off (idle)", color: css("--s4") },
  ], { labelW: 86 });
  renderGpuDetails(g);
  const hist = L.power_hist || [];
  const now = Date.now() / 1000;
  const common = { xs: hist.map((p) => p[0]), xMin: now - 120, xMax: now, height: 150,
    xFmt: (x) => new Date(x * 1000).toLocaleTimeString(), xTicks: [[now - 120, "2 min ago"], [now - 1, "now"]] };
  if (liveOn("pwLoadChart")) GK.area($("pwLoadChart"), Object.assign({}, common, { yMax: 100, yFmt: (v) => Math.round(v) + "%",
    series: [{ name: "GPU load", color: css("--s1"), values: hist.map((p) => p[1]) }] }));
  if (liveOn("pwWattChart")) GK.area($("pwWattChart"), Object.assign({}, common, { yFmt: fmt.watts,
    series: [{ name: "Whole PC", color: css("--muted"), values: hist.map((p) => p[2]) },
      { name: "GPU", color: css("--s1"), values: hist.map((p) => p[3]) }] }));
  GK.barList($("pwTopNow"), (P.top || []).map(([name, u, mb]) => ({
    label: name, value: Math.max(u, 0.01), display: `${u}% · ${fmt.bytes(mb * 2 ** 20)}` })),
  { labelW: 120, emptyMsg: "Nothing is using the GPU right now." });
}

function kv(container, k, v, sub, frac) {
  const d = h("div", "kv", null, container);
  h("div", "k", k, d);
  h("div", "v", v, d);
  if (sub) h("div", "s", sub, d);
  if (frac != null) h("i", null, null, h("div", "progress", null, d)).style.width = Math.min(100, frac * 100) + "%";
}

function renderGpuDetails(g) {
  const el = $("pwGpuInfo");
  if (!el) return;
  el.replaceChildren();
  if (!g || !g.name) { h("div", "empty", "Detailed GPU readings need an NVIDIA card.", el); return; }
  $("pwGpuMeta").textContent = [g.driver && "driver " + g.driver, g.vbios && "VBIOS " + g.vbios].filter(Boolean).join(" · ");
  const n = (v, unit) => (v == null ? "–" : fmt.int(v) + unit);
  kv(el, "Video memory (VRAM)", `${(g.vram_mb / 1024).toFixed(1)} / ${(g.vram_total_mb / 1024).toFixed(1)} GB`,
    `${fmt.int(g.vram_total_mb - g.vram_mb)} MB free`, g.vram_mb / g.vram_total_mb);
  kv(el, "GPU load", n(g.util, "%"), "share of time the GPU was busy", (g.util || 0) / 100);
  kv(el, "Memory controller load", n(g.mem_util, "%"), "how hard VRAM is being read/written", (g.mem_util || 0) / 100);
  kv(el, "Graphics clock", n(g.clock_mhz, " MHz"), g.max_clock_mhz ? `max ${fmt.int(g.max_clock_mhz)} MHz` : "",
    g.max_clock_mhz ? g.clock_mhz / g.max_clock_mhz : null);
  kv(el, "Memory clock", n(g.mem_clock_mhz, " MHz"), g.max_mem_clock_mhz ? `max ${fmt.int(g.max_mem_clock_mhz)} MHz` : "",
    g.max_mem_clock_mhz ? g.mem_clock_mhz / g.max_mem_clock_mhz : null);
  kv(el, "Power", n(g.power_w, " W"), g.limit_w ? `limit ${fmt.int(g.limit_w)} W` : "", g.limit_w ? g.power_w / g.limit_w : null);
  kv(el, "Temperature", n(g.temp, "°C"), g.slowdown_temp ? `slows down at ${g.slowdown_temp}°C` : "",
    g.slowdown_temp ? g.temp / g.slowdown_temp : null);
  kv(el, "Fan", n(g.fan, "%"), g.fan === 0 ? "fans stopped (quiet mode)" : "", (g.fan || 0) / 100);
  kv(el, "Video encoder", n(g.enc, "%"), "streaming / recording (NVENC)", (g.enc || 0) / 100);
  kv(el, "Video decoder", n(g.dec, "%"), "video playback (NVDEC)", (g.dec || 0) / 100);
  kv(el, "PCIe link", g.pcie_gen ? `Gen ${g.pcie_gen} ×${g.pcie_width}` : "–",
    g.pcie_gen_max ? `max Gen ${g.pcie_gen_max} ×${g.pcie_width_max} (drops when idle to save power)` : "");
  kv(el, "PCIe traffic", g.pcie_rx_mbs == null ? "–" : `↓ ${fmt.int(g.pcie_rx_mbs)}  ↑ ${fmt.int(g.pcie_tx_mbs)} MB/s`,
    "to / from the GPU");
  kv(el, "Performance state", g.pstate || "–", "P0 = full speed, higher = power saving");
  kv(el, "Held back by", g.throttle && g.throttle.length ? g.throttle.join(", ") : "nothing",
    g.throttle && g.throttle.includes("idle") ? "idle is normal when nothing's running" : "");
}

function renderSystemLive() {
  const L = state.live;
  if (!L || !$("syLive")) return;
  const S = L.system, P = L.power || {}, g = (P.gpu || {});
  const el = $("syLive");
  if (!S) { el.replaceChildren(); h("div", "meta", "Waiting for the first reading…", el); return; }
  liveTiles(el, [
    { label: "CPU", value: Math.round(S.cpu_pct) + "%",
      foot: `busiest core ${Math.round(S.core_max || 0)}% · ${S.cpu_mhz ? (S.cpu_mhz / 1000).toFixed(1) + " GHz" : ""} · ${S.cores} threads` },
    { label: "Memory", value: (S.ram_used_mb / 1024).toFixed(1), unit: `of ${Math.round(S.ram_total_mb / 1024)} GB`,
      foot: `${Math.round(S.ram_pct)}% used · ${(S.swap_mb / 1024).toFixed(1)} GB in page file` },
    { label: "Disk", value: fmt.bytes(S.disk_read_bps + S.disk_write_bps) + "/s",
      foot: `read ${fmt.bytes(S.disk_read_bps)}/s · write ${fmt.bytes(S.disk_write_bps)}/s · busiest ${Math.round(S.disk_busy)}%` },
    { label: "GPU", value: Math.round(P.util || 0) + "%",
      foot: g.vram_mb != null ? `${(g.vram_mb / 1024).toFixed(1)} / ${Math.round(g.vram_total_mb / 1024)} GB VRAM · ${g.temp}°C` : "" },
  ]);
  const hist = L.sys_hist || [];
  const now = Date.now() / 1000;
  const common = { xs: hist.map((p) => p[0]), xMin: now - 120, xMax: now, height: 140,
    xFmt: (x) => new Date(x * 1000).toLocaleTimeString(), xTicks: [[now - 120, "2 min ago"], [now - 1, "now"]] };
  const pct = (v) => Math.round(v) + "%";
  if (liveOn("syCpuChart")) GK.area($("syCpuChart"), Object.assign({}, common, { yMax: 100, yFmt: pct, series: [{ name: "CPU", color: css("--s2"), values: hist.map((p) => p[1]) }] }));
  if (liveOn("syRamChart")) GK.area($("syRamChart"), Object.assign({}, common, { yMax: 100, yFmt: pct, series: [{ name: "Memory", color: css("--s3"), values: hist.map((p) => p[2]) }] }));
  if (liveOn("syDiskChart")) GK.area($("syDiskChart"), Object.assign({}, common, { yFmt: (v) => fmt.bytes(v) + "/s", tickMode: "bytes",
    series: [{ name: "Read", color: css("--s1"), values: hist.map((p) => p[3]) }, { name: "Write", color: css("--s2"), values: hist.map((p) => p[4]) }] }));
  if (liveOn("syGpuChart")) GK.area($("syGpuChart"), Object.assign({}, common, { yMax: 100, yFmt: pct, series: [{ name: "GPU", color: css("--s1"), values: hist.map((p) => p[5]) }] }));
  GK.barList($("syTopNow"), (S.top || []).map(([name, c, ram]) => ({
    label: name, value: Math.max(c, 0.01), display: `${c.toFixed(1)}% · ${fmt.bytes(ram * 2 ** 20)}` })),
  { color: css("--s2"), labelW: 150, emptyMsg: "Collecting… (first numbers after ~10 seconds)" });
  GK.barList($("syDrives"), S.disks.map(([n, r, w, b]) => ({
    label: "Disk " + n, value: Math.max(b, 0.01), display: `${Math.round(b)}% busy · R ${fmt.bytes(r)}/s · W ${fmt.bytes(w)}/s` })),
  { labelW: 80, emptyMsg: "No disk counters." });
  GK.barList($("sySpace"), S.space.map(([m, used, total]) => ({
    label: m, value: used / total, display: `${fmt.bytes(used)} of ${fmt.bytes(total)} · ${fmt.pct(used / total)}` })),
  { color: css("--s3"), labelW: 80 });
}

let gmTiles = null;      // kept between ticks so the gaming tiles update in place
function renderGamingLive() {
  const el = $("gmLive"), L = state.live;
  if (!el || !L) return;
  const g = L.gaming;
  el.replaceChildren();
  el.classList.toggle("idle", !g);
  if (!g) {
    h("div", "label", "Gaming mode", el);
    h("div", "title", "Not in a game right now", el);
    const tg = L.today_game;
    h("div", "meta", "Turns on by itself when a game window is in front. Launchers (like the League client) don't count." +
      (tg.active_s ? ` Today so far: ${fmt.dur(tg.active_s)} in games.` : ""), el);
    return;
  }
  const left = h("div", null, null, el);
  h("div", "label", g.focused ? "🎮 Playing now" : "🎮 Game open, but you're alt-tabbed (not counted as gaming)", left);
  h("div", "title", g.name, left);
  const apm = h("div", "apm", fmt.int(g.apm), left);
  h("small", null, "APM", apm);
  h("div", "meta", `Session ${fmt.dur(Date.now() / 1000 - g.start)} · ${fmt.dur(g.active_s)} active`, left);
  if (!gmTiles) {
    gmTiles = h("div", "tiles fixed", null);
    gmTiles.style.setProperty("--cols", 2);
    gmTiles.style.setProperty("--cols-md", 2);
  }
  el.appendChild(gmTiles);
  liveTiles(gmTiles, [{ label: "Peak APM", value: fmt.int(g.peak_apm), foot: "this session" },
    { label: "Average APM", value: g.active_s > 30 ? fmt.int((g.keys + g.clicks) / (g.active_s / 60)) : "–" },
    { label: "Keys", value: fmt.int(g.keys) },
    { label: "Clicks", value: fmt.int(g.clicks) }]);
}

async function pollLive() {
  if (document.hidden) return;
  try {
    const first = !state.live;
    state.live = await api("live");
    renderLive();
    if (first && state.range === "all") loadTab();
  } catch (e) {
    seriesOK = null;      // the tracker that comes back may be a newer one, so ask for /api/series again
    $("statusText").textContent = "Tracker not running";
    $("status").classList.add("paused");
  }
}

// live numbers tick every second only on tabs that show them; the status chip keeps a 3 s beat elsewhere
const LIVE_TABS = new Set(["overview", "network", "power", "system", "gaming"]);
let liveT = 0;
function liveLoop() {
  if (document.hidden) return;      // stops while hidden; visibilitychange starts it again
  pollLive();
  scheduleLive();
}
// one pending timer at a time: every call replaces it, so a tab switch picks the new cadence at once
function scheduleLive(ms = LIVE_TABS.has(state.tab) ? 1000 : 3000) {
  clearTimeout(liveT);
  liveT = setTimeout(liveLoop, ms);
}

// ------------------------------------------------------------------ chart cards
// Every over-time chart is a card with its own time frame (or "Page", the range at the top) and chart type.
// Choices are kept per viewer in localStorage. A card's own range is fetched by URL; cards asking for the same URL share one request.

const CARD_RANGES = [["page", "Page", "Follow the page range"], ["1h", "1h", "This hour so far and the hour before (counts are stored per hour)"], ["24h", "24h", "Last 24 hours"],
  ["7d", "7d", "Last 7 days"], ["month", "Month", "This month (Total shows a projection to the month's end)"],
  ["30d", "30d", "Last 30 days"], ["year", "12m", "Last 12 months"], ["all", "All", "All time"]];
const CARD_VIEWS = [["line", "Line"], ["smooth", "Smooth"], ["total", "Total"], ["bars", "Bars"]];
const sumUrl = (i, mode = true) => `summary?${i.q}&bucket=${i.bucket}${mode ? mq() : ""}`;
const hrsFmt = (hrs) => fmt.dur(hrs * 3600);
const noSpace = (f) => (v) => f(v).replace(" ", "");
const sysT = (r, k) => r.totals[k] || { avg: 0, max: 0, sum: 0 };
function sysCol(r, info, k, i, gap) {        // system rows hold [avg, max, sum] per metric
  const by = {};
  for (const x of r.series) by[x.b] = x;
  return info.keys.map((b) => (by[b] && by[b][k] ? by[b][k][i] : gap ? null : 0));
}
const pctFmt = (v) => Math.round(v) + "%";
const gbFmt = (mb) => (mb / 1024).toFixed(1) + " GB";

// [id, title, metric, column (0 avg, 1 max, 2 sum), color, value format, axis format, total text, additive, scale]
const SYS_MINIS = [
  ["syMiniCpu", "CPU load", "cpu.util", 0, "--s2", pctFmt, pctFmt, (r) => pctFmt(sysT(r, "cpu.util").avg) + " avg", false],
  ["syMiniRam", "Memory used", "ram.used_mb", 0, "--s3", (v) => v.toFixed(1) + " GB", (v) => v.toFixed(0) + "G", (r) => gbFmt(sysT(r, "ram.used_mb").avg) + " avg", false, 1 / 1024],
  ["syMiniRead", "Data read", "disk.read", 2, "--s1", fmt.bytes, noSpace(fmt.bytes), (r) => fmt.bytes(sysT(r, "disk.read").sum), true],
  ["syMiniWrite", "Data written", "disk.write", 2, "--s2", fmt.bytes, noSpace(fmt.bytes), (r) => fmt.bytes(sysT(r, "disk.write").sum), true],
  ["syMiniGpu", "GPU load", "gpu.util", 0, "--s1", pctFmt, pctFmt, (r) => pctFmt(sysT(r, "gpu.util").avg) + " avg", false],
  ["syMiniVram", "VRAM used", "gpu.vram_mb", 0, "--s1", (v) => v.toFixed(1) + " GB", (v) => v.toFixed(0) + "G", (r) => gbFmt(sysT(r, "gpu.vram_mb").avg) + " avg", false, 1 / 1024],
  ["syMiniClock", "GPU clock", "gpu.clock", 0, "--s1", (v) => fmt.int(v) + " MHz", fmt.num, (r) => fmt.int(sysT(r, "gpu.clock").avg) + " MHz avg", false],
  ["syMiniTemp", "GPU temperature", "gpu.temp", 1, "--s4", (v) => Math.round(v) + "°C", (v) => Math.round(v) + "°", (r) => Math.round(sysT(r, "gpu.temp").max) + "°C peak", false],
];
const OV_MINIS = [["ovMiniKeys", "Keystrokes"], ["ovMiniClicks", "Clicks"], ["ovMiniActive", "Active time"], ["ovMiniDist", "Mouse distance"]];

// a weekday x hour cell names one date only when the range holds each weekday at most once
function punchDay(info, wd, hr) {
  if (!(info.bucket === "hour" || info.range === "7d")) return null;
  for (let d = new Date(info.from); d < info.to; d = addDays(d, 1)) {
    const t = new Date(d.getFullYear(), d.getMonth(), d.getDate(), hr);
    if (t.getDay() === wd && t >= info.from && t < info.to) return dkey(t);
  }
  return null;
}
function openDay(k) {
  setRange("day:" + k);
  $("filters").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

const CARDS = {
  ovMiniKeys: { tab: "overview", mini: true, page: (d) => d.sum, url: sumUrl, metrics: ["keys"],
    draw: (r, i) => ({ total: fmt.int(r.totals.keys), series: [{ name: "Keystrokes", color: css("--s1"), values: fill(i, r.series, "keys") }],
      vFmt: fmt.int, yFmt: fmt.num }) },
  ovMiniClicks: { tab: "overview", mini: true, page: (d) => d.sum, url: sumUrl, metrics: ["clicks"],
    draw: (r, i) => ({ total: fmt.int(r.totals.clicks != null ? r.totals.clicks : clicksOf(r.totals)), series: [{ name: "Clicks", color: css("--s2"), values: fill(i, r.series, "clicks") }],
      vFmt: fmt.int, yFmt: fmt.num }) },
  ovMiniActive: { tab: "overview", mini: true, page: (d) => d.sum, url: sumUrl, metrics: ["active_s"],
    draw: (r, i) => ({ total: fmt.dur(r.totals.active_s), series: [{ name: "Active time", color: css("--s1"),
      values: fill(i, r.series, "active_s").map((v) => v / 3600) }], vFmt: hrsFmt, yFmt: (hrs) => +hrs.toFixed(1) + "h" }) },
  ovMiniDist: { tab: "overview", mini: true, page: (d) => d.sum, url: sumUrl, metrics: ["mouse_mm"],
    draw: (r, i) => ({ total: fmt.dist(r.totals.mouse_mm), series: [{ name: "Mouse distance", color: css("--s2"), values: fill(i, r.series, "mouse_mm") }],
      vFmt: fmt.dist, yFmt: noSpace(fmt.dist) }) },
  ovPunch: { tab: "overview", page: (d) => d.sum, url: sumUrl,
    render(el, r, i) {
      const pick = i.bucket === "hour" || i.range === "7d";
      GK.punch(el, r.punch, pick ? { onPick: (wd, hr) => { const k = punchDay(i, wd, hr); if (k) openDay(k); }, pickHint: "click to open that day" } : {});
      const sub = el.closest(".card").querySelector(".sub");
      if (sub) sub.textContent = "Keys + clicks by weekday and hour" + (pick ? " · click a cell to open that day" : "");
    } },
  kbSeries: { tab: "keyboard", page: (d) => d.sum, url: sumUrl, metrics: ["keys"],
    draw: (r, i) => ({ sub: i.bucket === "hour" ? "Per hour" : `Per ${i.bucket}`,
      series: [{ name: "Keystrokes", color: css("--s1"), values: fill(i, r.series, "keys") }], vFmt: fmt.int }) },
  kbWpm: { tab: "keyboard", additive: false, page: (d) => d.sum, url: sumUrl, metrics: ["typed", "typing_ms"],
    draw: (r, i) => ({ height: 180, trend: true, vFmt: (v) => Math.round(v) + " wpm", yFmt: (v) => String(Math.round(v)),
      series: [{ name: "Avg WPM", color: css("--s1"), values: fillGap(i, r.series, (x) => (x.typing_ms > 5000 ? Math.round(wpmOf(x.typed, x.typing_ms)) : null)) }],
      emptyMsg: "Type a bit to see your speed." }) },
  msClicks: { tab: "mouse", page: (d) => d.sum, url: sumUrl, metrics: ["clicks"],
    draw: (r, i) => ({ series: [{ name: "Clicks", color: css("--s2"), values: fill(i, r.series, "clicks") }], vFmt: fmt.int }) },
  msDist: { tab: "mouse", page: (d) => d.sum, url: sumUrl, metrics: ["mouse_mm"],
    draw: (r, i) => ({ series: [{ name: "Distance", color: css("--s2"), values: fill(i, r.series, "mouse_mm") }], vFmt: fmt.dist, yFmt: noSpace(fmt.dist) }) },
  netSeries: { tab: "network", page: (d) => d.sum, url: (i) => sumUrl(i, false), metrics: ["down", "up"],
    draw: (r, i) => ({ vFmt: fmt.bytes, tickMode: "bytes", yFmt: noSpace(fmt.bytes),
      series: [{ name: "Download", color: css("--s3"), values: fill(i, r.series, "down") },
        { name: "Upload", color: css("--s4"), values: fill(i, r.series, "up") }] }) },
  netIfSeries: { tab: "network", page: (d) => d.net, url: (i) => `net?${i.q}&bucket=${i.bucket}`,
    draw(r, i) {        // rows are [bucket, adapter, up, down]
      const rows = r.series || [], tot = {};
      for (const [, n, u, dn] of rows) tot[n] = (tot[n] || 0) + u + dn;
      const names = Object.keys(tot).sort((a, b) => tot[b] - tot[a]).slice(0, 2);
      const colors = [["--s3", "--s4"], ["--s1", "--s2"]];
      const series = names.flatMap((n, k) => {
        const mine = rows.filter((x) => x[1] === n).map(([b, , up, down]) => ({ b, up, down }));
        return [["down", "↓"], ["up", "↑"]].map(([f, arrow], j) => ({ name: `${n} ${arrow}`, color: css(colors[k][j]), values: fill(i, mine, f) }));
      });
      return { series, vFmt: fmt.bytes, tickMode: "bytes", yFmt: noSpace(fmt.bytes), emptyMsg: "No traffic in this range yet." };
    } },
  pwEnergy: { tab: "power", view: "bars", page: (d) => d, url: (i) => `power?${i.q}&bucket=${i.bucket}`,
    metrics: ENERGY_PARTS.map(([k]) => k), adapt: (r) => Object.assign(seriesRows(r), { cfg: powerCfg() }),
    draw(r, i) {
      const series = ENERGY_PARTS.map(([k, name, c]) => ({ name, color: css(c), values: fill(i, r.series, k) }));
      const money = (wh) => fmt.money(((wh || 0) / 1000) * r.cfg.price, r.cfg.currency);
      return { series, stacked: true, vFmt: fmt.energy, yFmt: (v) => (v >= 1000 ? +(v / 1000).toFixed(1) + " kWh" : Math.round(v) + " Wh"),
        extraRows: (k) => {
          const tot = series.reduce((a, x) => a + (x.values[k] || 0), 0);
          return [{ label: "total", value: fmt.energy(tot) }, { label: "cost", value: money(tot) }];
        },
        emptyMsg: "No energy recorded in this range yet. It adds up from the moment the tracker runs." };
    } },
  pwUtil: { tab: "power", additive: false, page: (d) => d, url: (i) => `power?${i.q}&bucket=${i.bucket}`, metrics: ["gpu_util"],
    draw: (r, i) => ({ height: 150, vFmt: pctFmt, yFmt: pctFmt, emptyMsg: "No GPU data in this range yet.",
      series: [{ name: "Avg GPU load", color: css("--s1"), values: fillGap(i, r.series, (x) => (x.gpu_util == null ? null : x.gpu_util)) }] }) },
  gmSeries: { tab: "gaming", page: (d) => d.g, url: (i) => `gaming?${i.q}&bucket=${i.bucket}`, metrics: ["active_s"], smode: "gaming",
    adapt: (r) => Object.assign(seriesRows(r), { game_count: state.data.gaming ? state.data.gaming.data.g.game_count : 1 }),
    draw(r, i) {
      const unit = i.bucket === "hour" ? 60 : 3600;
      return { series: [{ name: "In game", color: css("--s1"), values: fill(i, r.series, "active_s").map((v) => v / unit) }],
        vFmt: (v) => fmt.dur(v * unit), yFmt: (v) => fmt.dur(v * unit).replace(" ", ""),
        emptyMsg: r.game_count ? "No game time in this range yet." : "No games recognized yet. Play one, or mark one in the Apps tab." };
    } },
};
for (const [id, title, k, col, color, vFmt, yFmt, total, additive, scale] of SYS_MINIS) {
  CARDS[id] = { tab: "system", mini: true, additive, page: (d) => d, url: (i) => `system?${i.q}&bucket=${i.bucket}`,
    draw(r, i) {
      const peak = col === 0 ? sysCol(r, i, k, 1, true) : null;
      const values = sysCol(r, i, k, col, !additive).map((v) => (v == null ? null : v * (scale || 1)));
      return { total: total(r), vFmt, yFmt, series: [{ name: title, color: css(color), values }],
        extraRows: peak ? (j) => [{ label: "peak", value: peak[j] == null ? "–" : vFmt(peak[j] * (scale || 1)) }] : null };
    } };
}

// the mini chart cards are built once and then reused, so each keeps its controls and zoom between renders
function miniSlots(container, list) {
  if (container.children.length === list.length) return;
  container.replaceChildren();
  for (const [id, title] of list) {
    const c = h("div", "card mini", null, container);
    h("h3", null, title, c);
    h("div", "total", null, c);
    h("div", null, null, c).id = id;
  }
}

const cardData = new Map();      // url -> { at, data, tab, p (pending fetch) }
function cardResp(url, tab, load = () => api(url)) {
  let e = cardData.get(url);
  if (!e || (!e.p && Date.now() - e.at > 25000)) {      // stale data stays on screen until the new numbers arrive
    const entry = e || { data: null, tab };
    entry.at = Date.now();
    entry.p = load().then((data) => { entry.data = data; entry.at = Date.now(); }, (err) => console.error(err))
      .finally(() => { entry.p = null; });
    cardData.delete(url);
    cardData.set(url, entry);
    while (cardData.size > 40) cardData.delete(cardData.keys().next().value);
    e = entry;
  }
  return e;
}

// a card with its own range: cards that name their metrics ask /api/series for just those; the rest use their own endpoint
function cardFetch(spec, info) {
  if (!spec.metrics) return cardResp(spec.url(info), spec.tab);
  const mode = spec.smode || (MODE_TABS.has(spec.tab) ? state.mode : "all");
  return cardResp(`series:${spec.metrics}|${info.q}|${info.bucket}|${mode}`, spec.tab,
    () => seriesLoad(spec.metrics, info, mode).then(spec.adapt || seriesRows));
}

// the running total carried on to the end of a range that is still under way, at the pace so far (drawn dashed)
function projection(s, info) {
  const n = info.keys.length, now = Date.now();
  // pace since the range began, or since tracking began when that is later
  const t0 = Math.max(+info.from, state.live && state.live.first_day ? +parseDay(state.live.first_day) : 0);
  if (!info.live || info.cut >= n - 1 || now - t0 < 3.6e6) return null;
  let tot = 0;
  for (let i = 0; i <= info.cut; i++) tot += s.values[i] || 0;
  if (!tot) return null;
  const rate = tot / (now - t0);
  const end = (i) => (i + 1 < n ? info.starts[i + 1] : +info.to);
  return { name: "Projection at this pace", color: s.color,
    values: info.keys.map((k, i) => (i < info.cut ? null : i === info.cut ? tot : tot + rate * (end(i) - now))) };
}

function cardPrefs(id) {
  const spec = CARDS[id];
  let p;
  try { p = JSON.parse(store.get("gk-card-" + id, "{}")) || {}; } catch (e) { p = {}; }
  const r = CARD_RANGES.some(([k]) => k === p.r) ? p.r : "page";
  let v = CARD_VIEWS.some(([k]) => k === p.v) ? p.v : spec.view || "line";
  if (v === "total" && spec.additive === false) v = "line";
  return { r, v, c: !!p.c };
}

function cardControl(card, id, spec, p) {
  let c = card.querySelector(":scope > .cctl");
  if (!c) {
    c = h("div", "cctl");
    c.dataset.card = id;
    const r = h("select", "cc-range", null, c);
    r.title = "Time frame for this chart";
    r.setAttribute("aria-label", r.title);
    for (const [k, label, title] of CARD_RANGES) { const o = h("option", null, label, r); o.value = k; o.title = title; }
    if (!spec.render) {
      const v = h("select", "cc-view", null, c);
      v.title = "Chart type. Total = running total.";
      v.setAttribute("aria-label", "Chart type");
      for (const [k, label] of CARD_VIEWS) if (k !== "total" || spec.additive !== false) { const o = h("option", null, label, v); o.value = k; }
      const b = h("button", "cc-cmp", "vs prev", c);
      b.type = "button";
      b.title = "Compare to the period just before (dashed line)";
    }
    card.prepend(c);
  }
  const [r, v, b] = c.children;
  r.value = p.r;
  r.classList.toggle("own", p.r !== "page");
  if (v) v.value = p.v;
  if (b) { b.classList.toggle("on", p.c); b.setAttribute("aria-pressed", String(p.c)); b.hidden = p.v === "bars"; }
}

function drawCard(id, pageData, pageInfo) {
  const spec = CARDS[id], el = $(id);
  if (!el) return;
  const card = el.closest(".card");
  const p = cardPrefs(id);
  cardControl(card, id, spec, p);
  let info = pageInfo, resp = spec.page(pageData), waiting = null, prev = null, pInfo = null;
  if (p.r !== "page") {
    info = rangeInfo(p.r);
    const e = cardFetch(spec, info);
    resp = e.data; waiting = e.p;
  }
  const compare = p.c && !spec.render && p.v !== "bars" && info.range !== "all" && info.range !== "1h";
  if (compare) {
    pInfo = prevInfo(info);
    const e = cardFetch(spec, pInfo);
    prev = e.data; waiting = waiting || e.p;
  }
  card.classList.toggle("fetching", !!waiting);
  if (waiting) waiting.then(() => redrawCard(id));
  if (!resp) { if (!waiting) GK.empty(el, "Couldn't load this range."); return; }
  if (spec.render) return spec.render(el, resp, info);
  const o = spec.draw(resp, info);
  const totalEl = card.querySelector(".total");
  if (totalEl && o.total != null) totalEl.textContent = o.total;
  const subEl = card.querySelector(".sub");
  if (subEl && o.sub != null) subEl.textContent = o.sub;
  const base = { labels: info.labels, tips: info.tips, labelW: info.labelW + (spec.mini ? 8 : 0),
    height: o.height || (spec.mini ? 120 : 180), ticks: spec.mini ? 2 : undefined, yFmt: o.yFmt, vFmt: o.vFmt, tickMode: o.tickMode,
    emptyMsg: o.emptyMsg || (spec.mini ? "No data yet" : undefined), extraRows: o.extraRows };
  // two hourly buckets read best side by side
  if (p.v === "bars" || info.range === "1h") return GK.columns(el, Object.assign(base, { series: o.series, stacked: o.stacked }));
  const total = p.v === "total";
  const named = (s, tail) => Object.assign({}, s, { name: s.name + tail });
  const cmp = compare && prev
    ? spec.draw(prev, pInfo).series.map((s) => Object.assign(named(s, " (before)"), { tips: pInfo.tips })) : null;
  GK.line(el, Object.assign(base, {
    series: total ? o.series.map((s) => named(s, " (running total)")) : o.series, compare: cmp,
    projection: total && !cmp && spec.additive !== false && o.series.length === 1 ? projection(o.series[0], info) : null,
    cumulative: total, smooth: p.v === "smooth", cut: info.cut, partial: info.live, trend: o.trend && !total,
    extraRows: total ? null : o.extraRows, zoomKey: info.q + p.v + (cmp ? "+prev" : ""),
  }));
}

function redrawCard(id) {
  const spec = CARDS[id], d = state.data[spec.tab];
  if (spec.tab === state.tab && d) drawCard(id, d.data, d.info);
}
function drawCards(tab, data, info) {
  for (const id in CARDS) if (CARDS[id].tab === tab) drawCard(id, data, info);
  drawHists(tab, info);
}
// on a reload with unchanged page numbers, cards with their own range (or a comparison) still check for new numbers
function refreshCards(tab) {
  for (const id in CARDS) {
    if (CARDS[id].tab !== tab) continue;
    const p = cardPrefs(id);
    if (p.r !== "page" || p.c) redrawCard(id);
  }
  drawHists(tab);
}

document.addEventListener("change", (e) => {
  const s = e.target.closest(".cctl select");
  if (!s || !s.parentNode.dataset.card) return;
  const id = s.parentNode.dataset.card, p = cardPrefs(id);
  if (s.classList.contains("cc-range")) p.r = s.value; else p.v = s.value;
  store.set("gk-card-" + id, JSON.stringify(p));
  redrawCard(id);
});
document.addEventListener("click", (e) => {
  const b = e.target.closest(".cctl .cc-cmp");
  if (!b) return;
  const id = b.parentNode.dataset.card, p = cardPrefs(id);
  p.c = !p.c;
  store.set("gk-card-" + id, JSON.stringify(p));
  redrawCard(id);
});

// ------------------------------------------------------------------ live 2-minute charts: history
// Each live chart can switch its card to an hourly (or longer) series from /api/series and back. While a card
// shows history, the 1 s live tick leaves it alone.

const HIST_RANGES = [["live", "Live", "Last 2 minutes, every second"], ["24h", "24h", "Last 24 hours"],
  ["7d", "7d", "Last 7 days"], ["30d", "30d", "Last 30 days"], ["year", "12m", "Last 12 months"], ["all", "All", "All time"]];
const whFmt = (v) => (v >= 1000 ? +(v / 1000).toFixed(1) + " kWh" : Math.round(v) + " Wh");
// [metric, name, color]; avg = a reading per bucket (gaps stay gaps), otherwise a count per bucket
const LIVE_HIST = {
  netLive: { tab: "network", height: 170, what: "Data", series: [["down", "Download", "--s3"], ["up", "Upload", "--s4"]],
    vFmt: fmt.bytes, yFmt: noSpace(fmt.bytes), tickMode: "bytes" },
  pwLoadChart: { tab: "power", height: 150, avg: true, yMax: 100, what: "Average load", series: [["gpu_util", "GPU load", "--s1"]], vFmt: pctFmt },
  pwWattChart: { tab: "power", height: 150, what: "Energy (Wh in an hour = average watts)",
    series: [["wh", "Whole PC", "--muted"], ["gpu_wh", "GPU", "--s1"]], vFmt: fmt.energy, yFmt: whFmt },
  syCpuChart: { tab: "system", height: 140, avg: true, yMax: 100, what: "Average load", series: [["m:cpu.util:avg", "CPU", "--s2"]], vFmt: pctFmt },
  syRamChart: { tab: "system", height: 140, avg: true, yMax: 100, what: "Average use", series: [["m:ram.pct:avg", "Memory", "--s3"]], vFmt: pctFmt },
  syDiskChart: { tab: "system", height: 140, what: "Data", series: [["m:disk.read:sum", "Read", "--s1"], ["m:disk.write:sum", "Write", "--s2"]],
    vFmt: fmt.bytes, yFmt: noSpace(fmt.bytes), tickMode: "bytes" },
  syGpuChart: { tab: "system", height: 140, avg: true, yMax: 100, what: "Average load", series: [["gpu_util", "GPU", "--s1"]], vFmt: pctFmt },
};
const histOf = (id) => { const v = store.get("gk-hist-" + id, "live"); return HIST_RANGES.some(([k]) => k === v) ? v : "live"; };
const liveOn = (id) => histOf(id) === "live";

function histControl(id) {
  const card = $(id).closest(".card"), r = histOf(id);
  let c = card.querySelector(":scope > .cctl");
  if (!c) {
    c = h("div", "cctl");
    const sel = h("select", "cc-hist", null, c);
    sel.dataset.hist = id;
    sel.title = "Time frame for this chart";
    sel.setAttribute("aria-label", sel.title);
    for (const [k, label, title] of HIST_RANGES) { const o = h("option", null, label, sel); o.value = k; o.title = title; }
    card.prepend(c);
    const sub = card.querySelector(".sub");
    sub.replaceChildren(h("span"), document.createTextNode(" · "), h("button", "linkbtn hist-link"));
    sub.lastChild.type = "button";
    sub.lastChild.dataset.hist = id;
  }
  const sel = c.firstChild;
  sel.value = r;
  sel.classList.toggle("own", r !== "live");
  const sub = card.querySelector(".sub"), spec = LIVE_HIST[id];
  const text = r === "live" ? "Last 2 minutes" : `${spec.what} per ${rangeInfo(r).bucket} · ${HIST_RANGES.find(([k]) => k === r)[2].toLowerCase()}`;
  if (sub.firstChild.textContent !== text) sub.firstChild.textContent = text;
  sub.lastChild.textContent = r === "live" ? "open history" : "back to live";
}

function drawHist(id) {
  const spec = LIVE_HIST[id], el = $(id);
  if (!el || spec.tab !== state.tab) return;
  histControl(id);
  const r = histOf(id);
  if (r === "live") return;
  const info = rangeInfo(r), metrics = spec.series.map(([m]) => m);
  const e = cardResp(`series:${metrics}|${info.q}|${info.bucket}|all`, spec.tab, () => seriesLoad(metrics, info));
  const card = el.closest(".card");
  card.classList.toggle("fetching", !!e.p);
  if (e.p) e.p.then(() => drawHist(id));
  if (!e.data) { if (!e.p) GK.empty(el, "Couldn't load this range."); else if (!el.querySelector(".chart svg")) GK.empty(el, "Loading…"); return; }
  const pts = e.data.points;
  GK.line(el, { labels: info.labels, tips: info.tips, labelW: info.bucket === "hour" ? 6 : info.labelW, height: spec.height, ticks: 3,
    yFmt: spec.yFmt || spec.vFmt, vFmt: spec.vFmt, tickMode: spec.tickMode, yMax: spec.yMax, cut: info.cut, partial: info.live,
    zoomKey: id + info.q, emptyMsg: "Nothing recorded in this range yet.",
    series: spec.series.map(([m, name, color]) => ({ name, color: css(color),
      values: spec.avg ? fillGap(info, pts, (x) => (x[m] == null ? null : x[m])) : fill(info, pts, m) })) });
}
function drawHists(tab, info) {
  for (const id in LIVE_HIST) if (LIVE_HIST[id].tab === tab) drawHist(id);
  if (tab === "system") for (const id in SX) drawSx(id, info);
}

function setHist(id, r) {
  store.set("gk-hist-" + id, r);
  histControl(id);
  if (r === "live") { $(id).replaceChildren(); $(id).closest(".card").classList.remove("fetching"); renderLive(); }
  else drawHist(id);
}
document.addEventListener("change", (e) => {
  const s = e.target.closest(".cctl .cc-hist");
  if (s) setHist(s.dataset.hist, s.value);
});
document.addEventListener("click", (e) => {
  const b = e.target.closest(".hist-link");
  if (b) setHist(b.dataset.hist, liveOn(b.dataset.hist) ? "24h" : "live");
});

// ------------------------------------------------------------------ system detail: any metric over time (average + peak)

const unitFmt = { "%": pctFmt, MHz: (v) => fmt.int(v) + " MHz", "°C": (v) => Math.round(v) + "°C", W: fmt.watts,
  GB: (mb) => gbFmt(mb), "B/s": (v) => fmt.bytes(v) + "/s" };
const unitAxis = { "%": pctFmt, MHz: fmt.num, "°C": (v) => Math.round(v) + "°", W: (v) => Math.round(v) + "W",
  GB: (mb) => (mb / 1024).toFixed(0) + "G", "B/s": noSpace(fmt.bytes) };
// [metrics-table key, name, unit, color]
const SX = {
  sxCpu: [["cpu.util", "Load", "%", "--s2"], ["cpu.core_max", "Busiest core", "%", "--s2"], ["cpu.mhz", "Clock", "MHz", "--s2"]],
  sxGpu: [["gpu.util", "Load", "%", "--s1"], ["gpu.temp", "Temperature", "°C", "--s4"], ["gpu.clock", "Clock", "MHz", "--s1"],
    ["gpu.mem_clock", "Memory clock", "MHz", "--s1"], ["gpu.power", "Power", "W", "--s1"], ["gpu.fan", "Fan", "%", "--s1"],
    ["gpu.vram_mb", "VRAM used", "GB", "--s1"], ["gpu.mem_util", "Memory controller", "%", "--s1"],
    ["gpu.enc", "Video encoder", "%", "--s1"], ["gpu.dec", "Video decoder", "%", "--s1"]],
  sxSys: [["ram.used_mb", "Memory used", "GB", "--s3"], ["ram.pct", "Memory used (%)", "%", "--s3"], ["swap.mb", "Page file", "GB", "--s3"],
    ["disk.read", "Disk read", "B/s", "--s1"], ["disk.write", "Disk write", "B/s", "--s2"], ["disk.busy", "Disk busy", "%", "--s1"]],
};
function sxPrefs(id) {
  let p;
  try { p = JSON.parse(store.get("gk-sx-" + id, "{}")) || {}; } catch (e) { p = {}; }
  return { m: SX[id].some(([k]) => k === p.m) ? p.m : SX[id][0][0], r: CARD_RANGES.some(([k]) => k === p.r) ? p.r : "page" };
}
function sxControl(id, p) {
  const card = $(id).closest(".card");
  let c = card.querySelector(":scope > .cctl");
  if (!c) {
    c = h("div", "cctl");
    const m = h("select", "sx-metric", null, c);
    m.title = "What to chart";
    m.setAttribute("aria-label", m.title);
    for (const [k, name] of SX[id]) { const o = h("option", null, name, m); o.value = k; }
    const r = h("select", "sx-range", null, c);
    r.title = "Time frame for this chart";
    r.setAttribute("aria-label", r.title);
    for (const [k, label, title] of CARD_RANGES) { const o = h("option", null, label, r); o.value = k; o.title = title; }
    for (const s of c.children) s.dataset.sx = id;
    card.prepend(c);
  }
  c.children[0].value = p.m;
  c.children[1].value = p.r;
  c.children[1].classList.toggle("own", p.r !== "page");
}
function drawSx(id, pageInfo = state.data.system && state.data.system.info) {
  const el = $(id);
  if (!el || state.tab !== "system" || !pageInfo) return;
  const p = sxPrefs(id);
  sxControl(id, p);
  const [k, name, unit, color] = SX[id].find(([x]) => x === p.m);
  const info = p.r === "page" ? pageInfo : rangeInfo(p.r), metrics = [`m:${k}:avg`, `m:${k}:max`];
  const e = cardResp(`series:${metrics}|${info.q}|${info.bucket}|all`, "system", () => seriesLoad(metrics, info));
  el.closest(".card").classList.toggle("fetching", !!e.p);
  if (e.p) e.p.then(() => drawSx(id));
  if (!e.data) { if (!e.p) GK.empty(el, "Couldn't load this range."); return; }
  const pts = e.data.points, col = (m) => fillGap(info, pts, (x) => (x[m] == null ? null : x[m]));
  GK.line(el, { labels: info.labels, tips: info.tips, labelW: info.bucket === "hour" ? 6 : info.labelW, height: 160, ticks: 3,
    yFmt: unitAxis[unit], vFmt: unitFmt[unit], yMax: unit === "%" ? 100 : undefined, cut: info.cut, partial: info.live,
    zoomKey: id + info.q + k, emptyMsg: "Nothing recorded in this range yet.",
    series: [{ name: name + " · average", color: css(color), values: col(metrics[0]) },
      { name: "peak", color: css("--muted"), values: col(metrics[1]) }] });
}
document.addEventListener("change", (e) => {
  const s = e.target.closest(".cctl select[data-sx]");
  if (!s) return;
  const id = s.dataset.sx, p = sxPrefs(id);
  if (s.classList.contains("sx-metric")) p.m = s.value; else p.r = s.value;
  store.set("gk-sx-" + id, JSON.stringify(p));
  drawSx(id);
});

const PEAKS = [["syPeakCpu", "m:cpu.util:avg", "m:cpu.util:max", "cpu", "--s2"], ["syPeakGpu", "m:gpu.util:avg", "m:gpu.util:max", "gpu", "--s1"]];
const topBy = (points, k, n) => points.filter((p) => p[k] > 0).sort((a, b) => b[k] - a[k]).slice(0, n);
// the 5 buckets (hours, or days over a very long range) with the highest average load, with the program that did the most
function renderPeaks(r, tops) {
  for (const [el, avg, max, kind, color] of PEAKS) {
    GK.barList($(el), topBy(r.points, avg, 5).map((p) => {
      const d = typeof p.b === "number" ? new Date(p.b * 3.6e6) : parseDay(p.b), who = tops && tops[p.b] && tops[p.b][kind];
      const b = h("button", "linkbtn", typeof p.b === "number" ? `${shortDate(d)}, ${hourLabel(d.getHours())}` : shortDate(d));
      b.type = "button";
      b.dataset.day = dkey(d);
      return { label: b.textContent, node: b, value: p[avg],
        display: `${pctFmt(p[avg])} avg · ${pctFmt(p[max] || 0)} peak${who ? " · " + who[1] : ""}` };
    }), { color: css(color), labelW: 110, emptyMsg: "No readings in this range yet." });
  }
}
for (const [id] of PEAKS) $(id).addEventListener("click", (e) => { const b = e.target.closest("button[data-day]"); if (b) openDay(b.dataset.day); });

function renderTopPrograms(apps) {
  const list = (el, key, f, color) => GK.barList($(el), apps.filter((x) => x[key] > 0).sort((a, b) => b[key] - a[key]).slice(0, 5)
    .map((x) => ({ label: x.name, value: x[key], display: f(x[key]) })), { color: css(color), labelW: 140, emptyMsg: "Nothing recorded in this range yet." });
  list("syTopCpu", "cpu_s", fmt.dur, "--s2");
  list("syTopGpu", "gpu_s", fmt.dur, "--s1");
  list("syTopRam", "ram_max", (mb) => fmt.bytes(mb * 2 ** 20), "--s3");
}

// ------------------------------------------------------------------ overview sparklines (today, per hour, all modes like the live tiles)

const SPARKS = [[0, "clicks", "--s2"], [3, "mouse_mm", "--s2"], [4, "down", "--s3"], [5, "up", "--s4"], [6, "active_s", "--s1"]];
function renderSparks() {
  const S = state.spark;
  if (!S || state.tab !== "overview") return;
  let hero = $("lvSpark");
  if (!hero) { hero = h("div", "spark"); hero.id = "lvSpark"; $("lvKeysMeta").after(hero); }
  GK.spark(hero, fill(S.info, S.sum.series, "keys"), css("--s1"), S.info.cut);
  const kids = $("liveTiles").children;
  for (const [i, k, color] of SPARKS) {
    const t = kids[i];
    if (!t) continue;
    const sp = t.querySelector(".spark") || h("div", "spark", null, t);
    GK.spark(sp, fill(S.info, S.sum.series, k), css(color), S.info.cut);
  }
}

// ------------------------------------------------------------------ calendar card (last 12 months, or one calendar year)

function calRange() {
  const v = store.get("gk-cal", "12m"), today = new Date(), mid = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  if (/^\d{4}$/.test(v) && +v <= today.getFullYear()) {
    const y = +v, end = new Date(y + 1, 0, 1);
    return { key: v, title: v, text: "in " + v, from: new Date(y, 0, 1), to: end < mid ? end : addDays(mid, 1), start: new Date(y, 0, 1) };
  }
  return { key: "12m", title: "Last 12 months", text: "in the last year", from: addDays(mid, -7 * 52 - 6), to: addDays(mid, 1) };
}

function calOptions() {
  const sel = $("calRange"), cur = store.get("gk-cal", "12m");
  const y1 = new Date().getFullYear(), y0 = state.live && state.live.first_day ? +state.live.first_day.slice(0, 4) : y1;
  const want = [["12m", "Last 12 months"]];
  for (let y = y1; y >= y0; y--) want.push([String(y), String(y)]);
  if (sel.options.length !== want.length) {
    sel.replaceChildren();
    for (const [k, label] of want) { const o = h("option", null, label, sel); o.value = k; }
  }
  sel.value = want.some(([k]) => k === cur) ? cur : "12m";
  sel.classList.toggle("own", sel.value !== "12m");
}

// days come from /api/series (day buckets); cached for a minute, or until the time frame changes
async function renderCalendar() {
  calOptions();
  const cr = calRange();
  const draw = () => {
    if (state.tab !== "overview" || state.calendarKey !== cr.key) return;
    GK.calendar($("ovCal"), state.calendar, { onPick: openDay, start: cr.start, end: addDays(cr.to, -1) });
    $("calTitle").textContent = cr.title;
    $("calTotal").textContent = `${fmt.int(state.calendar.reduce((a, r) => a + (r.keys || 0), 0))} keys ${cr.text}`;
  };
  if (state.calendar && state.calendarKey === cr.key) draw();
  if (state.calendar && state.calendarKey === cr.key && Date.now() - state.calendarAt < 60000) return;
  try {
    const r = await seriesLoad(["keys", "clicks", "active_s"], spanInfo("cal", cr.from, cr.to, "day", ""));
    if (calRange().key !== cr.key) return;
    state.calendar = r.points.map((p) => ({ day: p.b, keys: p.keys, clicks: p.clicks, active_s: p.active_s }));
    state.calendarKey = cr.key;
    state.calendarAt = Date.now();
    draw();
  } catch (e) { console.error(e); }
}
$("calRange").addEventListener("change", (e) => { store.set("gk-cal", e.target.value); renderCalendar(); });

// ------------------------------------------------------------------ tabs

const TABS = {
  overview: {
    async load(info) {
      const [sum, apps, keys] = await Promise.all([
        api(`summary?${info.q}&bucket=${info.bucket}${mq()}`), api(`apps?${info.q}${mq()}`), api(`keys?${info.q}${mq()}`)]);
      // sparklines in the live tiles: today per hour, every mode (the page summary already is that when it shows today in All)
      const ti = rangeInfo("today");
      state.spark = { info: ti, sum: info.range === "today" && state.mode === "all" ? sum : await api(sumUrl(ti, false)) };
      return { sum, apps, keys };
    },
    render(d, info) {
      miniSlots($("ovMinis"), OV_MINIS);
      drawCards("overview", d, info);
      renderSparks();
      GK.barList($("ovApps"), d.apps.apps.filter((a) => a.active_s > 0).slice(0, 6).map((a) => ({
        label: a.name, value: a.active_s, display: fmt.dur(a.active_s) })), { emptyMsg: "No app time yet." });
      GK.barList($("ovKeys"), d.keys.keys.slice(0, 10).map(([k, n]) => ({
        label: k, node: keyChip(k), value: n, display: fmt.int(n) })), { labelW: 110, emptyMsg: "No keys yet." });
      renderFacts();
      renderCalendar();
    },
  },

  keyboard: {
    async load(info) {
      const [sum, keys] = await Promise.all([api(`summary?${info.q}&bucket=${info.bucket}${mq()}`), api(`keys?${info.q}${mq()}`)]);
      return { sum, keys };
    },
    render(d, info) {
      const t = d.sum.totals;
      const counts = Object.fromEntries(d.keys.keys);
      const combosTotal = d.keys.combos.reduce((a, c) => a + c[1], 0);
      const top = d.keys.keys[0];
      tiles("kbTiles", [
        { label: "Keystrokes", value: fmt.int(t.keys), color: css("--s1") },
        { label: "Characters typed", value: fmt.int(t.typed), foot: `≈ ${fmt.int(t.typed / 5)} words` },
        { label: "Average speed", value: Math.round(wpmOf(t.typed, t.typing_ms)) || "–", unit: t.typed ? "wpm" : "",
          foot: state.mode === "gaming" ? "game keys aren't typing" : "while typing" },
        ...(state.mode === "gaming" ? [
          { label: "Peak APM", value: t.peak_apm ? fmt.int(t.peak_apm) : "–", foot: "busiest 60 seconds in a game" },
          { label: "Keys per active minute", value: t.active_s > 60 ? fmt.int(t.keys / (t.active_s / 60)) : "–" },
        ] : [
          { label: "Peak speed", value: Math.round(t.peak_wpm), unit: "wpm", foot: "best 30-second burst" },
          { label: "Peak keys/minute", value: fmt.int(t.peak_kpm), foot: "outside games" },
        ]),
        { label: "Backspace rate", value: t.keys ? fmt.pct(t.backspaces / t.keys) : "–", foot: `${fmt.int(t.backspaces)} backspaces` },
        { label: "Shortcuts used", value: fmt.int(combosTotal), foot: d.keys.combos[0] ? `favorite: ${d.keys.combos[0][0]}` : "" },
        { label: "Most-pressed key", value: top ? prettyKey(top[0]) : "–", foot: top ? `${fmt.int(top[1])} presses` : "" },
      ]);
      GK.keyboard($("kbHeat"), counts);
      const scale = $("kbScale");
      scale.replaceChildren();
      const offBoard = d.keys.keys.filter(([k]) => !GK.KB_IDS.has(k)).reduce((a, k) => a + k[1], 0);
      $("kbHeatNote").textContent = offBoard ? `+ ${fmt.int(offBoard)} presses on media/extra keys` : "";
      drawCards("keyboard", d, info);
      GK.barList($("kbTop"), d.keys.keys.slice(0, 20).map(([k, n]) => ({
        label: k, node: keyChip(k), value: n, display: `${fmt.int(n)} · ${fmt.pct(n / (t.keys || 1))}` })),
        { labelW: 110, emptyMsg: "No keys yet." });
      GK.barList($("kbCombos"), d.keys.combos.slice(0, 20).map(([c, n]) => ({
        label: c, node: comboChips(c), value: n, display: fmt.int(n) })), { labelW: 170, emptyMsg: "No shortcuts yet." });
    },
  },

  mouse: {
    async load(info) {
      const [sum, mouse] = await Promise.all([api(`summary?${info.q}&bucket=${info.bucket}${mq()}`), api(`mouse?${info.q}${mq()}`)]);
      return { sum, mouse };
    },
    render(d, info) {
      const t = d.sum.totals, clicks = clicksOf(t);
      tiles("msTiles", [
        { label: "Clicks", value: fmt.int(clicks), color: css("--s2") },
        { label: "Left", value: fmt.int(t.lclicks) },
        { label: "Right", value: fmt.int(t.rclicks) },
        { label: "Middle", value: fmt.int(t.mclicks) },
        { label: "Side buttons", value: fmt.int(t.xclicks) },
        { label: "Scroll notches", value: fmt.int(t.scroll), foot: `≈ ${fmt.dist(t.scroll * 26)} of page` },
        { label: "Distance", value: fmt.dist(t.mouse_mm), foot: distanceFact(t.mouse_mm) },
        { label: "Clicks per active hour", value: t.active_s > 60 ? fmt.int(clicks / (t.active_s / 3600)) : "–" },
      ]);
      GK.clickMap($("msMap"), d.mouse);
      const cellsTotal = d.mouse.grid.cells.reduce((a, c) => a + c[2], 0);
      $("msMapNote").textContent = `${d.mouse.screen.monitors.length} monitor${d.mouse.screen.monitors.length === 1 ? "" : "s"} · ${fmt.int(cellsTotal)} clicks`;
      drawCards("mouse", d, info);
      GK.barList($("msButtons"), [["Left", t.lclicks], ["Right", t.rclicks], ["Middle", t.mclicks], ["Side", t.xclicks]]
        .map(([l, n]) => ({ label: l, value: n, display: `${fmt.int(n)} · ${fmt.pct(n / (clicks || 1))}` })),
        { color: css("--s2"), labelW: 70, emptyMsg: "No clicks yet." });
      if (!clicks) GK.barList($("msButtons"), []);
    },
  },

  network: {
    async load(info) {
      const [sum, net, bursts] = await Promise.all([api(`summary?${info.q}&bucket=${info.bucket}`),
        api(`net?${info.q}&bucket=${info.bucket}`), seriesLoad(["down", "up"], spanInfo("bursts", info.from, info.to, "hour", ""))]);
      return { sum, net, bursts };
    },
    render(d, info) {
      const t = d.sum.totals;
      tiles("netTiles", [
        { label: "Downloaded", value: fmt.bytes(t.down), color: css("--s3"), foot: downloadFact(t.down) },
        { label: "Uploaded", value: fmt.bytes(t.up), color: css("--s4") },
        { label: "Total data", value: fmt.bytes(t.down + t.up) },
        { label: "Fastest download", value: fmt.speed(t.peak_down), foot: "1-second peak" },
        { label: "Fastest upload", value: fmt.speed(t.peak_up), foot: "1-second peak" },
        { label: "Download : upload", value: t.up ? (t.down / t.up).toFixed(1) + " : 1" : "–" },
      ]);
      drawCards("network", d, info);
      GK.barList($("netIfaces"), d.net.ifaces.map(([name, up, down]) => ({
        label: name, value: up + down, display: `↓ ${fmt.bytes(down)} ↑ ${fmt.bytes(up)}` })),
        { color: css("--s3"), labelW: 110, emptyMsg: "No traffic yet." });
      renderBursts(d.bursts);
      renderNetLive();
    },
  },

  power: {
    async load(info) { return api(`power?${info.q}&bucket=${info.bucket}`); },
    render(d, info) {
      renderPowerLive();
      const t = d.totals, pr = d.cfg.price, cu = d.cfg.currency;
      const money = (wh) => fmt.money(((wh || 0) / 1000) * pr, cu);
      const avgW = t.secs ? t.wh / (t.secs / 3600) : 0;
      const perMonth = t.days ? (t.wh / t.days) * 30 : 0;
      tiles("pwTiles", [
        { label: "Energy used", value: fmt.energy(t.wh), foot: t.secs ? `over ${fmt.dur(t.secs)} of PC time` : "" },
        { label: "Cost", value: money(t.wh), foot: `at ${fmt.money(pr, cu)} per kWh` },
        { label: "Average draw", value: t.secs ? fmt.watts(avgW) : "–", foot: "while the PC was on" },
        { label: "Peak draw", value: t.peak_watts ? fmt.watts(t.peak_watts) : "–", foot: "whole PC" },
        { label: "Monthly pace", value: t.days ? money(perMonth) : "–", foot: t.days ? `projection · ${fmt.energy(perMonth)} a month` : "" },
        { label: "GPU work", value: fmt.dur(t.gpu_busy_s), foot: "time at 100% load" },
        { label: "Avg GPU load", value: t.gpu_secs ? Math.round(t.gpu_util_avg) + "%" : "–", foot: t.gpu_secs ? `peak ${Math.round(t.gpu_util_max)}%` : "" },
        { label: "Hottest GPU", value: t.gpu_temp_max ? Math.round(t.gpu_temp_max) + "°C" : "–",
          foot: t.gpu_w_max ? `${fmt.watts(t.gpu_w_max)} max draw` : "" },
      ]);
      drawCards("power", d, info);
      GK.barList($("pwSplit"), ENERGY_PARTS.filter(([k]) => t[k] > 0).map(([k, name, c]) => ({
        label: name, value: t[k], color: css(c), display: `${fmt.energy(t[k])} · ${fmt.pct(t[k] / (t.wh || 1))}` })),
      { labelW: 90, emptyMsg: "No energy recorded yet." });
      const tbl = $("pwApps");
      tbl.replaceChildren();
      if (!d.apps.length) tbl.appendChild(h("caption", "empty", "No GPU use recorded in this range yet."));
      else {
        const tr = h("tr", null, null, h("thead", null, null, tbl));
        [["App"], ["GPU work", 1], ["Avg load", 1], ["Energy", 1], ["Cost", 1], ["Peak VRAM", 1]]
          .forEach(([l, n]) => { h("th", n ? "num" : "", l, tr).style.cursor = "default"; });
        const tb = h("tbody", null, null, tbl);
        const maxWork = Math.max(...d.apps.map((x) => x.gpu_s)) || 1;
        for (const x of d.apps) {
          const row = h("tr", null, null, tb);
          const td = h("td", null, null, row);
          const wrap = h("div", "appname", null, td);
          h("div", "avatar", (x.name || "?").trim().charAt(0).toUpperCase(), wrap);
          const names = h("div", null, null, wrap);
          h("div", null, x.name, names);
          h("div", "exe", x.exe, names);
          const work = h("td", "num", fmt.dur(x.gpu_s), row);
          h("i", null, null, h("span", "sharebar", null, work)).style.width = (x.gpu_s / maxWork) * 100 + "%";
          h("td", "num", x.secs ? Math.round((x.gpu_s / x.secs) * 100) + "%" : "–", row);
          h("td", "num", fmt.energy(x.wh), row);
          h("td", "num", money(x.wh), row);
          h("td", "num", fmt.bytes(x.vram_max * 2 ** 20), row);
        }
      }
      const facts = $("pwFacts");
      facts.replaceChildren();
      [["📱", `${fmt.int(t.wh / 15)} phone charges`, "about 15 Wh each"],
        ["💡", `${t.wh / 10 < 48 ? Math.round(t.wh / 10) + " hours" : fmt.int(t.wh / 240) + " days"} of a 10 W LED bulb`, "same energy, spent on light"],
        ["☕", `${fmt.int(t.wh / 110)} kettles boiled`, "1 liter each, about 110 Wh"],
        ["🚗", `${(t.wh / 300).toFixed(1)} miles in an electric car`, "at about 300 Wh per mile"],
        ["🔥", `${fmt.int(t.wh * 3.412)} BTU of heat`, "almost all of it ends up warming your room"],
      ].forEach(([e, big, txt]) => {
        const f = h("div", "fact", null, facts);
        h("span", "emoji", e, f);
        h("div", "big", big, f);
        h("div", "txt", txt, f);
      });
      $("pwNote").textContent = `GPU and CPU watts come from your hardware's own sensors. "Rest of PC" (${d.cfg.other_w} W plus ` +
        `power-supply losses at ${Math.round(d.cfg.psu_eff * 100)}% efficiency) and monitors (${d.cfg.display_w} W while you're active) ` +
        "are estimates you can change in Settings → Electricity.";
    },
  },

  apps: {
    async load(info) { return api(`apps?${info.q}${mq()}`); },
    render(d) {
      const tbl = $("appsTable");
      tbl.replaceChildren();
      const apps = d.apps.filter((a) => a.active_s || a.keys || a.clicks);
      if (!apps.length) { tbl.appendChild(h("caption", "empty", "No apps recorded in this range yet.")); return; }
      const cols = [
        ["App", "name", false], ["Active time", "active_s", true], ["Keys", "keys", true], ["Clicks", "clicks", true],
        ["Mouse", "mouse_mm", true], ["Avg WPM", "wpm", true], ["Game?", "game", false],
      ];
      apps.forEach((a) => { a.wpm = wpmOf(a.typed, a.typing_ms); });
      const sortKey = state.appsSort || "active_s";
      apps.sort((a, b) => (sortKey === "name" ? a.name.localeCompare(b.name) : (b[sortKey] || 0) - (a[sortKey] || 0)));
      const maxActive = Math.max(...apps.map((a) => a.active_s)) || 1;
      const thead = h("thead", null, null, tbl), tr = h("tr", null, null, thead);
      for (const [label, key, num] of cols) {
        const th = h("th", (num ? "num " : "") + (key === sortKey ? "sorted" : ""), label, tr);
        th.dataset.sort = key;      // one delegated click listener handles the sort (see wiring)
      }
      const tb = h("tbody", null, null, tbl);
      for (const a of apps) {
        const row = h("tr", null, null, tb);
        const td = h("td", null, null, row);
        const wrap = h("div", "appname", null, td);
        h("div", "avatar", (a.name || "?").trim().charAt(0).toUpperCase(), wrap);
        const names = h("div", null, null, wrap);
        h("div", null, a.name, names);
        h("div", "exe", a.exe, names);
        const act = h("td", "num", fmt.dur(a.active_s), row);
        const bar = h("span", "sharebar", null, act);
        h("i", null, null, bar).style.width = (a.active_s / maxActive) * 100 + "%";
        h("td", "num", fmt.int(a.keys), row);
        h("td", "num", fmt.int(a.clicks), row);
        h("td", "num", fmt.dist(a.mouse_mm), row);
        h("td", "num", a.wpm ? Math.round(a.wpm) : "–", row);
        const gt = h("td", null, null, row);
        const b = h("button", "gtoggle" + (a.game ? " on" : ""), a.game ? "🎮 Game" : "Mark as game", gt);
        b.title = a.game ? "Counted in gaming mode. Click to stop treating it as a game." : "Treat this app as a game";
        b.dataset.exe = a.exe ?? "";
        b.dataset.game = a.game ? "1" : "";
      }
    },
  },

  records: {
    noRange: true,
    async load() { return api("records"); },
    render(r) {
      const A = state.live ? state.live.alltime : {};
      const when = (x) => (x ? longDate(parseDay(x.day)) : "not yet");
      const rec = (label, x, f) => ({ label, value: x ? f(x.v) : "–", foot: when(x) });
      tiles("recTiles", [
        rec("Most keys in a day", r.best_keys, fmt.int),
        rec("Most clicks in a day", r.best_clicks, fmt.int),
        rec("Longest active day", r.best_active, fmt.dur),
        rec("Peak typing speed", r.peak_wpm, (v) => Math.round(v) + " wpm"),
        rec("Peak keys per minute", r.peak_kpm, fmt.int),
        rec("Most mouse travel in a day", r.best_mouse, fmt.dist),
        rec("Biggest download day", r.best_down, fmt.bytes),
        rec("Fastest download", r.peak_down, fmt.speed),
        { label: "Busiest hour ever", value: r.best_hour ? fmt.int(r.best_hour.v) + " keys" : "–",
          foot: r.best_hour ? (() => { const d = new Date(r.best_hour.hour * 3.6e6); return `${shortDate(d)}, ${hourLabel(d.getHours())}`; })() : "not yet" },
        { label: "Current streak", value: r.streak + (r.streak === 1 ? " day" : " days"), foot: `days with ${r.streak_min}+ keys & clicks` },
        { label: "Longest streak", value: r.longest_streak + (r.longest_streak === 1 ? " day" : " days") },
        { label: "Favorite key", value: r.top_key ? prettyKey(r.top_key.key) : "–", foot: r.top_key ? fmt.int(r.top_key.v) + " presses" : "" },
        { label: "Most-used app", value: r.top_app ? r.top_app.name : "–", foot: r.top_app ? fmt.dur(r.top_app.v) + " active" : "" },
        rec("Peak APM (gaming)", r.peak_apm, fmt.int),
        rec("Most energy in a day", r.best_wh, (v) => `${fmt.energy(v)} · ${cost(v)}`),
        rec("Peak power draw", r.peak_watts, fmt.watts),
        rec("Hottest GPU", r.peak_gpu_temp, (v) => Math.round(v) + "°C"),
        { label: "Longest gaming session", value: r.longest_session ? fmt.dur(r.longest_session.v) : "–",
          foot: r.longest_session ? `${r.longest_session.name}, ${shortDate(new Date(r.longest_session.start * 1000))}` : "not yet" },
        { label: "Most-played game", value: r.top_game ? r.top_game.name : "–", foot: r.top_game ? fmt.dur(r.top_game.v) + " with it open" : "not yet" },
      ], false);
      renderMilestones(A, r);
      tiles("recTotals", [
        { label: "Keystrokes", value: fmt.int(A.keys) },
        { label: "Clicks", value: fmt.int(clicksOf(A)) },
        { label: "Mouse distance", value: fmt.dist(A.mouse_mm) },
        { label: "Scroll notches", value: fmt.int(A.scroll) },
        { label: "Downloaded", value: fmt.bytes(A.down) },
        { label: "Uploaded", value: fmt.bytes(A.up) },
        { label: "Active time", value: fmt.dur(A.active_s) },
        { label: "Tracked since", value: r.first_day ? shortDate(parseDay(r.first_day)) : "today", foot: `${r.days_tracked} days with data` },
      ]);
    },
  },

  gaming: {
    async load(info) {
      const [g, keys, mouse] = await Promise.all([api(`gaming?${info.q}&bucket=${info.bucket}`),
        api(`keys?${info.q}&mode=gaming`), api(`mouse?${info.q}&mode=gaming`)]);
      return { g, keys, mouse };
    },
    render(d, info) {
      renderGamingLive();
      const t = d.g.totals, clicks = clicksOf(t), sg = d.g.session_games;
      const play = sg.reduce((a, x) => a + x.play_s, 0);
      const sessions = sg.reduce((a, x) => a + x.sessions, 0);
      const longest = Math.max(0, ...sg.map((x) => x.longest_s));
      const apm = t.active_s > 30 ? (t.keys + clicks) / (t.active_s / 60) : 0;
      const top = d.g.per_game.find((a) => a.active_s > 0);
      tiles("gmTiles", [
        { label: "Time in games", value: fmt.dur(t.active_s), foot: "active, with the game in front" },
        { label: "Sessions", value: fmt.int(sessions), foot: play ? `${fmt.dur(play)} with a game open` : "none yet" },
        { label: "Average APM", value: apm ? fmt.int(apm) : "–", foot: "keys + clicks per minute" },
        { label: "Peak APM", value: t.peak_apm ? fmt.int(t.peak_apm) : "–", foot: "busiest 60 seconds" },
        { label: "Keys in games", value: fmt.int(t.keys) },
        { label: "Clicks in games", value: fmt.int(clicks), foot: `${fmt.int(t.rclicks)} right · ${fmt.int(t.lclicks)} left` },
        { label: "Longest session", value: longest ? fmt.dur(longest) : "–" },
        { label: "Most played", value: top ? top.name : "–", foot: top ? fmt.dur(top.active_s) + " active" : "" },
      ]);
      drawCards("gaming", d, info);
      GK.barList($("gmGames"), d.g.per_game.filter((a) => a.active_s > 0).map((a) => ({
        label: a.name, value: a.active_s, display: fmt.dur(a.active_s) })), { emptyMsg: "No games played in this range." });
      GK.keyboard($("gmKb"), Object.fromEntries(d.keys.keys));
      GK.clickMap($("gmMap"), d.mouse);
      const tbl = $("gmSessions");
      tbl.replaceChildren();
      if (!d.g.sessions.length) { tbl.appendChild(h("caption", "empty", "No sessions in this range. Start a game and it shows up here.")); return; }
      const tr = h("tr", null, null, h("thead", null, null, tbl));
      [["Game"], ["Started"], ["Length", 1], ["Keys", 1], ["Clicks", 1], ["Avg APM", 1], ["Peak APM", 1], ["Energy", 1], ["Cost", 1]]
        .forEach(([l, n]) => { const th = h("th", n ? "num" : "", l, tr); th.style.cursor = "default"; });
      const tb = h("tbody", null, null, tbl);
      if (d.g.sessions.length > 25) h("caption", "muted", `Latest 25 of ${d.g.sessions.length}`, tbl).style.captionSide = "bottom";
      for (const x of d.g.sessions.slice(0, 25)) {
        const row = h("tr", null, null, tb);
        h("td", null, x.name, row);
        h("td", null, new Date(x.start * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }), row);
        h("td", "num", fmt.dur(x.end - x.start), row);
        h("td", "num", fmt.int(x.keys), row);
        h("td", "num", fmt.int(x.clicks), row);
        h("td", "num", x.active_s > 30 ? fmt.int((x.keys + x.clicks) / (x.active_s / 60)) : "–", row);
        h("td", "num", x.peak_apm ? fmt.int(x.peak_apm) : "–", row);
        h("td", "num", x.wh ? fmt.energy(x.wh) : "–", row);
        h("td", "num", x.wh ? cost(x.wh) : "–", row);
      }
    },
  },

  system: {
    async load(info) {
      const [d, peaks] = await Promise.all([api(`system?${info.q}&bucket=${info.bucket}`),
        seriesLoad(PEAKS.flatMap(([, avg, max]) => [avg, max]), spanInfo("peaks", info.from, info.to, "hour", ""))]);
      const hours = new Set(PEAKS.flatMap(([, avg]) => topBy(peaks.points, avg, 5).map((p) => p.b)));
      d.peaks = peaks;      // the program per hour needs hourly buckets (a range under ~5 years)
      d.tops = peaks.bucket === "hour" && hours.size ? await api("top_at?hours=" + [...hours].join(",")).catch(() => null) : null;
      return d;
    },
    render(d, info) {
      renderSystemLive();
      const T = (k) => sysT(d, k), gb = gbFmt;
      tiles("syTiles", [
        { label: "Average CPU", value: Math.round(T("cpu.util").avg) + "%", foot: `peak ${Math.round(T("cpu.util").max)}%`, color: css("--s2") },
        { label: "Busiest core peak", value: Math.round(T("cpu.core_max").max) + "%", foot: T("cpu.mhz").max ? `up to ${(T("cpu.mhz").max / 1000).toFixed(1)} GHz` : "" },
        { label: "Average memory", value: gb(T("ram.used_mb").avg), foot: `${Math.round(T("ram.pct").avg)}% of RAM`, color: css("--s3") },
        { label: "Peak memory", value: gb(T("ram.used_mb").max), foot: `${Math.round(T("ram.pct").max)}% of RAM` },
        { label: "Data read", value: fmt.bytes(T("disk.read").sum), foot: `peak ${fmt.bytes(T("disk.read").max)}/s`, color: css("--s1") },
        { label: "Data written", value: fmt.bytes(T("disk.write").sum), foot: `peak ${fmt.bytes(T("disk.write").max)}/s` },
        { label: "Average GPU", value: Math.round(T("gpu.util").avg) + "%", foot: `encoder avg ${Math.round(T("gpu.enc").avg)}%` },
        { label: "Peak VRAM", value: gb(T("gpu.vram_mb").max), foot: `avg ${gb(T("gpu.vram_mb").avg)}` },
      ]);
      miniSlots($("syMinis"), SYS_MINIS);
      drawCards("system", d, info);
      renderPeaks(d.peaks, d.tops);
      renderTopPrograms(d.apps);
      const tbl = $("syApps");
      tbl.replaceChildren();
      if (!d.apps.length) { tbl.appendChild(h("caption", "empty", "No program usage recorded in this range yet.")); return; }
      const tr = h("tr", null, null, h("thead", null, null, tbl));
      [["Program"], ["CPU time", 1], ["Avg CPU", 1], ["Avg memory", 1], ["Peak memory", 1], ["Read", 1], ["Written", 1], ["GPU work", 1]]
        .forEach(([l, n]) => { h("th", n ? "num" : "", l, tr).style.cursor = "default"; });
      const tb = h("tbody", null, null, tbl);
      for (const x of d.apps) {
        const row = h("tr", null, null, tb);
        const wrap = h("div", "appname", null, h("td", null, null, row));
        h("div", "avatar", (x.name || "?").trim().charAt(0).toUpperCase(), wrap);
        const names = h("div", null, null, wrap);
        h("div", null, x.name, names);
        h("div", "exe", x.exe, names);
        h("td", "num", fmt.dur(x.cpu_s), row);
        h("td", "num", x.secs ? ((x.cpu_s / x.secs / d.cores) * 100).toFixed(1) + "%" : "–", row);
        h("td", "num", fmt.bytes(x.ram_avg * 2 ** 20), row);
        h("td", "num", fmt.bytes(x.ram_max * 2 ** 20), row);
        h("td", "num", fmt.bytes(x.read), row);
        h("td", "num", fmt.bytes(x.write), row);
        h("td", "num", x.gpu_s >= 1 ? fmt.dur(x.gpu_s) : "–", row);
      }
    },
  },

  settings: {
    noRange: true,
    async load() { const [s, g] = await Promise.all([api("settings"), api("games")]); return { s, g }; },
    render(d) {
      const s = d.s;
      $("setGamesAuto").checked = d.g.auto;
      const games = d.g.apps.filter((a) => a.game);
      const box = $("setGames");
      box.replaceChildren();
      if (!games.length) box.textContent = "None seen yet. Games show up after you play one, or mark one in the Apps tab.";
      else {
        const chips = h("div", "chips", null, box);
        const why = { known: "built-in list", library: "found in a game folder", added: "you marked it" };
        for (const g of games) {
          const c = h("span", "tier got", g.name, chips);
          c.title = `${g.exe} · ${why[g.why] || ""}`;
        }
      }
      $("setPaused").checked = s.paused;
      $("setAutostart").checked = s.autostart;
      $("setAutostart").disabled = !!s.autostart_fixed;     // a preview copy can't change the real startup entry
      $("setDir").textContent = s.data_dir;
      $("setDirMoved").hidden = !s.data_dir_moved;
      $("setDirPinned").textContent = s.data_dir_pinned || "";
      $("setSize").textContent = fmt.bytes(s.db_bytes);
      $("setVer").textContent = s.version;
      const bk = s.backups || [];
      $("setBackups").textContent = bk.length
        ? `${bk.length} kept in the backups folder, newest ${bk[0].slice(12, 22)}` : "the first one is made at startup";
      const pc = s.power || {};
      $("pwCur").value = pc.currency || "$";
      $("pwPrice").value = pc.price;
      $("pwOther").value = pc.other_w;
      $("pwDisplay").value = pc.display_w;
      $("pwEff").value = Math.round((pc.psu_eff || 0.9) * 100);
      $("pwDispHint").textContent = `${s.monitors} monitor${s.monitors === 1 ? "" : "s"} detected. Counted while you've used the PC in the last 10 minutes.`;
      const rm = s.remote || {};
      $("setRemote").checked = !!rm.enabled;
      $("remPort").textContent = rm.port || 8414;
      $("remBox").hidden = !rm.enabled;
      const links = $("remLinks");
      links.replaceChildren();
      for (const u of rm.urls || []) {
        const row = h("div", "rem-link", null, links);
        h("code", "path", u.url, row);
        if (u.tailscale) h("span", "tag", "Tailscale", row);
        const b = h("button", "btn", "Copy", row);
        b.dataset.url = u.url;      // copied by the delegated listener on #remLinks (see wiring)
      }
      $("remMsg").textContent = rm.error || (rm.enabled && !rm.on ? "Not running" : "");
      $("setLockdown").checked = !!s.lockdown;
      $("setRemote").disabled = !!s.lockdown;
      pollPrivacy();
      const cur = store.get("gk-theme", "");
      document.querySelectorAll("#themeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.theme === cur));
    },
  },
};

// the 10 buckets (hours, or days over a very long range) with the most downloaded; each opens its day
function renderBursts(r, tops) {      // tops: bucket label -> the program that downloaded most (per-app engine only)
  const top = r.points.filter((p) => p.down > 0).sort((a, b) => b.down - a.down).slice(0, 10);
  GK.barList($("netBursts"), top.map((p) => {
    const d = typeof p.b === "number" ? new Date(p.b * 3.6e6) : parseDay(p.b);
    const b = h("button", "linkbtn", typeof p.b === "number" ? `${shortDate(d)}, ${hourLabel(d.getHours())}` : shortDate(d));
    b.type = "button";
    b.dataset.day = dkey(d);
    return { label: b.textContent, node: b, value: p.down, display: `${fmt.bytes(p.down)} ↓ · ${fmt.bytes(p.up)} ↑${tops && tops[p.b] ? " · " + tops[p.b] : ""}` };
  }), { color: css("--s3"), labelW: 110, emptyMsg: "No downloads in this range yet." });
}
$("netBursts").addEventListener("click", (e) => { const b = e.target.closest("button[data-day]"); if (b) openDay(b.dataset.day); });

// ------------------------------------------------------------------ privacy proof (Settings)

const PRIV_KIND = { local: "This PC", phone: "Phone link, listening", "phone-viewer": "Phone viewer", outside: "Outside this PC" };
let privT;
async function pollPrivacy() {      // only while Settings is on screen: the socket list costs a few ms to read
  clearTimeout(privT);
  if (state.tab !== "settings" || document.hidden) return;
  try { renderPrivacy(await api("privacy")); } catch (e) { $("privSummary").textContent = "Couldn't read the connection list."; }
  if (state.tab === "settings" && !document.hidden) privT = setTimeout(pollPrivacy, 10000);
}
function renderPrivacy(p) {
  const outside = p.sockets.filter((r) => r.kind === "outside").length;
  const ok = p.all_local && !p.guard.blocked;
  const badge = $("privBadge");
  badge.textContent = ok ? "✓ All local" : "⚠ Check below";
  badge.className = "priv-badge " + (ok ? "ok" : "bad");
  const since = new Date(p.since * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  $("privSummary").textContent = (p.error ? `Couldn't read the connection list (${p.error}). `
    : outside ? `${outside} connection${outside === 1 ? "" : "s"} to outside this PC right now. `
    : `${p.sockets.length} open socket${p.sockets.length === 1 ? "" : "s"}, all on this PC. `)
    + `Since ${since}: ${p.outside_seen} outside connection${p.outside_seen === 1 ? "" : "s"} seen.`;
  $("privBlocked").textContent = fmt.int(p.guard.blocked);
  $("privBlocked").title = p.guard.recent.map((r) => `${new Date(r.t * 1000).toLocaleTimeString()} ${r.what} ${r.to}`).join("\n");
  $("privUrl").textContent = p.url;
  const tbl = $("privTable");
  tbl.replaceChildren();
  const hr = h("tr", null, null, h("thead", null, null, tbl));
  for (const c of ["Program", "Where", "Local address", "Remote address", "State"]) h("th", null, c, hr);
  const body = h("tbody", null, null, tbl);
  for (const r of p.sockets) {
    const tr = h("tr", null, null, body);
    h("td", null, r.proc, tr).title = `PID ${r.pid}`;
    h("td", "priv-kind " + r.kind, PRIV_KIND[r.kind] || r.kind, tr);
    h("td", null, `${r.type} ${r.local}`, tr);
    h("td", null, r.remote || "-", tr);
    h("td", null, r.status || "-", tr);
  }
}

// ------------------------------------------------------------------ key / fun helpers

const KEY_NAMES = { Space: "Space", Backspace: "Backspace", LShift: "Left Shift", RShift: "Right Shift", LCtrl: "Left Ctrl",
  RCtrl: "Right Ctrl", LAlt: "Left Alt", RAlt: "Right Alt", LWin: "Win", RWin: "Right Win", Up: "↑", Down: "↓", Left: "←", Right: "→" };
const prettyKey = (k) => KEY_NAMES[k] || k;
function keyChip(k) { const s = document.createElement("span"); s.className = "kbd-chip"; s.textContent = prettyKey(k); return s; }
function comboChips(c) {
  const wrap = document.createElement("span");
  c.split("+").forEach((p, i) => {
    if (i) wrap.appendChild(document.createTextNode(" + "));
    wrap.appendChild(keyChip(p));
  });
  return wrap;
}

function distanceFact(mm) {
  const m = mm / 1000;
  if (m >= 42195) return `${(m / 42195).toFixed(1)} marathons`;
  if (m >= 400) return `${(m / 400).toFixed(1)} laps of a running track`;
  if (m >= 100) return `${(m / 100).toFixed(1)} football fields`;
  if (m >= 1) return `${(m / 1.75).toFixed(1)}× your height`;
  return "";
}
function downloadFact(b) {
  if (b >= 4 * 2 ** 30) return `≈ ${(b / (4 * 2 ** 30)).toFixed(1)} HD movies`;
  if (b >= 4 * 2 ** 20) return `≈ ${fmt.int(b / (4 * 2 ** 20))} songs`;
  return "";
}

function renderFacts() {
  const A = state.live ? state.live.alltime : null;
  const c = $("ovFacts");
  c.replaceChildren();
  if (!A) return;
  const facts = [];
  const pages = A.typed / 1800;
  facts.push(["⌨️", fmt.int(A.keys) + " keys", pages >= 1 ? `≈ ${fmt.num(pages)} pages of a novel` : "Every press counts"]);
  facts.push(["🖱️", fmt.dist(A.mouse_mm), distanceFact(A.mouse_mm) || "of mouse travel"]);
  facts.push(["📥", fmt.bytes(A.down), downloadFact(A.down) || "downloaded"]);
  if (A.uptime_s > 0) facts.push(["⏱️", fmt.pct(A.active_s / A.uptime_s) + " active", `${fmt.dur(A.active_s)} of ${fmt.dur(A.uptime_s)} tracked`]);
  const scrollM = A.scroll * 0.026;
  facts.push(["📜", `${scrollM >= 1000 ? (scrollM / 1000).toFixed(2) + " km" : Math.round(scrollM) + " m"} scrolled`,
    scrollM >= 330 ? `${(scrollM / 330).toFixed(1)}× the Eiffel Tower` : `${fmt.int(A.scroll)} wheel notches`]);
  for (const [e, big, txt] of facts) {
    const f = h("div", "fact", null, c);
    h("span", "emoji", e, f);
    h("div", "big", big, f);
    h("div", "txt", txt, f);
  }
}

const MILESTONES = [
  ["Keystrokes", (A) => A.keys, [1e3, 1e4, 1e5, 5e5, 1e6, 5e6, 1e7], fmt.short],
  ["Clicks", (A) => clicksOf(A), [1e3, 1e4, 1e5, 5e5, 1e6], fmt.short],
  ["Mouse distance", (A) => A.mouse_mm, [1e5, 1e6, 1e7, 42195e3, 1e8], (mm) => fmt.dist(mm).replace(/\.00 km$/, " km")],
  ["Downloaded", (A) => A.down, [2 ** 30, 10 * 2 ** 30, 100 * 2 ** 30, 2 ** 40, 10 * 2 ** 40], fmt.bytes],
  ["Active time", (A) => A.active_s, [3600, 36000, 360000, 3600000], (s) => fmt.int(s / 3600) + "h"],
  ["Streak", (A, r) => r.longest_streak, [3, 7, 30, 100, 365], (d) => d + "d"],
  ["Typing speed", (A, r) => (r.peak_wpm ? r.peak_wpm.v : 0), [40, 60, 80, 100, 120], (w) => Math.round(w) + " wpm"],
];
function renderMilestones(A, r) {
  const c = $("recMilestones");
  c.replaceChildren();
  for (const [name, get, tiers, f] of MILESTONES) {
    const v = get(A, r) || 0;
    const row = h("div", "ms-row", null, c);
    h("div", "cat", name, row);
    const ts = h("div", "tiers", null, row);
    tiers.forEach((t) => h("span", "tier" + (v >= t ? " got" : ""), f(t), ts));
    const nxt = tiers.find((t) => v < t);
    const right = h("div", null, null, row);
    if (nxt) {
      const prev = [...tiers].reverse().find((t) => v >= t) || 0;
      const p = h("div", "progress", null, right);
      h("i", null, null, p).style.width = Math.min(100, ((v - prev) / (nxt - prev)) * 100) + "%";
      h("div", "next", `${f(v)} / next ${f(nxt)}`, right);
    } else {
      h("div", "next", "All unlocked!", right);
    }
  }
}

// ------------------------------------------------------------------ navigation & loading

let loadSeq = 0;
async function loadTab(force) {
  const tab = TABS[state.tab] ? state.tab : "overview";
  const def = TABS[tab];
  const view = $("view-" + tab);
  const info = rangeInfo(state.range);
  const seq = ++loadSeq;
  view.classList.add("loading");
  try {
    const data = await def.load(info);
    if (seq !== loadSeq) return;
    // same range, mode, numbers and live totals as the last paint: the DOM already shows this, so skip the rebuild
    const json = JSON.stringify([state.range, state.mode, info.q, data, state.live && state.live.alltime, state.live && state.live.power_cfg]);
    const same = !force && state.data[tab] && state.data[tab].json === json;
    if (!same) def.render(data, info);
    state.data[tab] = { data, info, json };     // recorded once painted, so a failed render is retried next time
    if (same) refreshCards(tab);
    if (same && tab === "overview") renderCalendar();      // its own fetch, so it can be newer than the page numbers
  } catch (e) {
    console.error(e);
  } finally {
    view.classList.remove("loading");
  }
}

function rerender() {
  const d = state.data[state.tab];
  if (d) TABS[state.tab].render(d.data, d.info);
  renderLive();
}

// after a resize or theme change the open tab repaints now; the others repaint the next time they are shown
function repaint() {
  for (const k of Object.keys(state.data)) if (k !== state.tab) state.data[k].json = null;
  rerender();
}

// keep data only for the tab on screen and the 2 shown before it (RAM); older tabs fetch again when shown
function keepRecent(tab) {
  state.recent = [tab, ...state.recent.filter((t) => t !== tab)].slice(0, 3);
  for (const k of Object.keys(state.data)) if (!state.recent.includes(k)) delete state.data[k];
  for (const [url, e] of cardData) if (!state.recent.includes(e.tab) && !e.p) cardData.delete(url);
}

function showTab(tab) {
  if (!TABS[tab]) tab = "overview";
  state.tab = tab;
  keepRecent(tab);
  history.replaceState(null, "", "#" + tab);
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + tab));
  const slot = document.querySelector(`#view-${tab} .range-slot`);
  const filters = $("filters");
  if (slot) { slot.appendChild(filters); filters.style.display = ""; } else filters.style.display = "none";
  $("modeSeg").style.display = MODE_TABS.has(tab) ? "" : "none";
  tip.hide();
  renderLive();
  loadTab();
  scheduleLive();
  pollPrivacy();
}

function setMode(m) {
  state.mode = m;
  store.set("gk-mode", m);
  state.data = {};        // cached numbers belong to the old mode, so the next load paints fresh
  cardData.clear();
  document.querySelectorAll("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
  loadTab();
}

function setRange(r) {
  state.range = r;
  store.set("gk-range", r);
  state.data = {};        // cached numbers belong to the old range, so the next load paints fresh
  document.querySelectorAll("#rangeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.range === r));
  $("rangeLabel").textContent = rangeInfo(r).text;
  loadTab();
}

// ------------------------------------------------------------------ overview layout

const ovWidgets = $("ovWidgets");
let layoutSaveT;
function saveLayout() {
  clearTimeout(layoutSaveT);
  layoutSaveT = setTimeout(() => post("settings", { layout: { name: "main", items: GKLayout.read(ovWidgets) } }).catch(() => {}), 400);
}
function layoutChanged() { saveLayout(); rerender(); }
function setEditing(on) {
  GKLayout.edit(ovWidgets, on, layoutChanged);
  $("layoutEdit").textContent = on ? "Done" : "Edit layout";
  $("layoutReset").hidden = !on;
  $("layoutHint").hidden = !on;
}
$("layoutEdit").addEventListener("click", () => setEditing(!ovWidgets.classList.contains("editing")));
$("layoutReset").addEventListener("click", () => { GKLayout.reset(ovWidgets); layoutChanged(); });

// ------------------------------------------------------------------ wiring

$("tabs").addEventListener("click", (e) => { const b = e.target.closest(".tab"); if (b) showTab(b.dataset.tab); });
$("rangeSeg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setRange(b.dataset.range); });
$("modeSeg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setMode(b.dataset.mode); });
$("setGamesAuto").addEventListener("change", async (e) => { await post("games", { auto: e.target.checked }); loadTab(true); });
// one delegated listener per list, so re-rendering the Apps table or the remote links adds no listeners
$("appsTable").addEventListener("click", async (e) => {
  const th = e.target.closest("th[data-sort]");
  if (th) { if (state.data.apps) { state.appsSort = th.dataset.sort; TABS.apps.render(state.data.apps.data); } return; }
  const b = e.target.closest(".gtoggle");
  if (!b) return;
  b.disabled = true;
  await post("games", { exe: b.dataset.exe, game: b.dataset.game !== "1" });
  loadTab(true);
});
$("remLinks").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-url]");
  if (b) navigator.clipboard.writeText(b.dataset.url).then(() => { b.textContent = "Copied"; }, () => {});
});
$("pwSave").addEventListener("click", async () => {
  await post("settings", { power: { currency: $("pwCur").value || "$", price: +$("pwPrice").value || 0,
    other_w: +$("pwOther").value || 0, display_w: +$("pwDisplay").value || 0, psu_eff: (+$("pwEff").value || 90) / 100 } });
  $("pwSaved").textContent = "Saved";
  setTimeout(() => { $("pwSaved").textContent = ""; }, 2000);
  await pollLive();
});
async function setPaused(p) { await post("settings", { paused: p }); await pollLive(); }
$("pauseBtn").addEventListener("click", () => setPaused(!(state.live && state.live.paused)));
$("setPaused").addEventListener("change", (e) => setPaused(e.target.checked));
$("setAutostart").addEventListener("change", async (e) => { await post("settings", { autostart: e.target.checked }); loadTab(true); });
$("setRemote").addEventListener("change", async (e) => { await post("settings", { remote: { enabled: e.target.checked } }); loadTab(true); });
$("setLockdown").addEventListener("change", async (e) => { await post("settings", { lockdown: e.target.checked }); loadTab(true); });
$("remNewKey").addEventListener("click", async () => {
  if (!confirm("Make a new key? Links you already shared stop working.")) return;
  await post("settings", { remote: { new_key: true } });
  loadTab(true);
});
$("themeSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  const t = b.dataset.theme;
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  store.set("gk-theme", t);
  document.querySelectorAll("#themeSeg button").forEach((x) => x.classList.toggle("on", x === b));
});
let resizeT;      // trailing only: one repaint once the window stops changing size
addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(repaint, 250); });
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", repaint);
new MutationObserver(repaint).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
addEventListener("hashchange", () => { const t = location.hash.slice(1); if (t !== state.tab) showTab(t); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) { scheduleLive(0); loadTab(); pollPrivacy(); } });

document.querySelectorAll("#rangeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.range === state.range));
document.querySelectorAll("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === state.mode));
$("rangeLabel").textContent = rangeInfo(state.range).text;
let pageVersion = null;      // a restart keeps the open window, so the page reloads itself when the tracker's version changes
const layoutReady = api("settings").then((s) => {
  pageVersion = s.version;
  if (s.view_only) document.documentElement.classList.add("view-only");
  GKLayout.apply(ovWidgets, (s.layouts || {}).main);
}, () => {});
layoutReady.then(pollLive).then(() => showTab(state.tab));     // showTab starts the live timer
setInterval(() => { if (!document.hidden && !TABS[state.tab].noRange) loadTab(); }, 30000);
setInterval(() => {
  if (document.hidden || !pageVersion) return;
  api("settings").then((s) => { if (s.version && s.version !== pageVersion) location.reload(); }, () => {});
}, 60000);
