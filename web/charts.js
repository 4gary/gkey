/* Small dependency-free chart kit for the gkeystrokes dashboard.
   Colors come from CSS custom properties so light/dark themes just work. */
"use strict";

const GK = (() => {
  const SVGNS = "http://www.w3.org/2000/svg";
  // color tokens are read once per theme; a data-theme or OS color-scheme change clears the cache
  const darkMQ = matchMedia("(prefers-color-scheme: dark)");
  const tokVals = new Map();
  let tokTheme = null, rampVals = null;
  function syncTheme() {
    const k = (document.documentElement.getAttribute("data-theme") || "") + (darkMQ.matches ? "/dark" : "/light");
    if (k !== tokTheme) { tokTheme = k; tokVals.clear(); rampVals = null; }
  }
  const css = (name) => {
    syncTheme();
    let v = tokVals.get(name);
    if (!v) { v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); tokVals.set(name, v); }
    return v;
  };
  const ramp = () => {
    syncTheme();
    return rampVals || (rampVals = css("--seq").split(",").map((s) => s.trim()));
  };

  // ------------------------------------------------------------------ formatting
  const fmt = {
    int: (n) => Math.round(Number(n) || 0).toLocaleString(),
    num(n) {
      n = Number(n) || 0;
      const a = Math.abs(n);
      if (a < 10000) return Math.round(n).toLocaleString();
      for (const [v, s] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
        if (a >= v) {
          const x = n / v;
          return (x >= 100 ? Math.round(x) : x.toFixed(1).replace(/\.0$/, "")) + s;
        }
      }
      return String(n);
    },
    short(n) {
      n = Number(n) || 0;
      for (const [v, s] of [[1e9, "B"], [1e6, "M"], [1e3, "K"]]) if (Math.abs(n) >= v) return +(n / v).toFixed(1) + s;
      return String(Math.round(n));
    },
    bytes(b) {
      b = Number(b) || 0;
      const u = ["B", "KB", "MB", "GB", "TB", "PB"];
      let i = 0;
      while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
      return (i === 0 ? Math.round(b) : b >= 100 ? Math.round(b) : b.toFixed(1)) + " " + u[i];
    },
    speed(Bps) {           // bytes/s -> bits/s, the unit internet plans use
      let b = (Number(Bps) || 0) * 8;
      const u = ["bps", "Kbps", "Mbps", "Gbps"];
      let i = 0;
      while (b >= 1000 && i < u.length - 1) { b /= 1000; i++; }
      return (i === 0 ? Math.round(b) : b >= 100 ? Math.round(b) : b.toFixed(1)) + " " + u[i];
    },
    dur(s) {
      s = Math.round(Number(s) || 0);
      if (s < 60) return s + "s";
      const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
      if (d) return d + "d " + h + "h";
      if (h) return h + "h " + m + "m";
      return m + "m";
    },
    dist(mm) {
      const m = (Number(mm) || 0) / 1000;
      if (m < 1) return Math.round(m * 100) + " cm";
      if (m < 1000) return (m < 10 ? m.toFixed(1) : Math.round(m)) + " m";
      const km = m / 1000;
      return (km < 100 ? km.toFixed(2) : Math.round(km).toLocaleString()) + " km";
    },
    watts: (w) => Math.round(Number(w) || 0).toLocaleString() + " W",
    energy(wh) {
      wh = Number(wh) || 0;
      if (wh < 1000) return (wh < 10 ? wh.toFixed(1) : Math.round(wh)) + " Wh";
      const k = wh / 1000;
      return (k < 100 ? k.toFixed(2) : Math.round(k).toLocaleString()) + " kWh";
    },
    money(v, cur = "$") {
      v = Number(v) || 0;
      if (v > 0 && v < 0.01) return "<" + cur + "0.01";
      return cur + (v < 1000 ? v.toFixed(2) : Math.round(v).toLocaleString());
    },
    pct: (x) => (x * 100 < 10 ? (x * 100).toFixed(1) : Math.round(x * 100)) + "%",
  };

  // ------------------------------------------------------------------ tooltip
  const tipEl = () => document.getElementById("tip");
  const tip = {
    show(evt, title, rows) {
      const t = tipEl();
      t.replaceChildren();
      if (title) {
        const h = document.createElement("div");
        h.className = "tt";
        h.textContent = title;
        t.appendChild(h);
      }
      for (const r of rows) {
        const row = document.createElement("div");
        row.className = "row";
        const lab = document.createElement("span");
        if (r.color) {
          const i = document.createElement("i");
          i.style.background = r.color;
          lab.appendChild(i);
        }
        lab.appendChild(document.createTextNode(r.label || ""));
        const v = document.createElement("b");
        v.textContent = r.value;
        row.append(v, lab);
        t.appendChild(row);
      }
      t.classList.add("show");
      tip.move(evt);
    },
    move(evt) {
      const t = tipEl();
      const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
      let x = evt.clientX + pad, y = evt.clientY + pad;
      if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
      if (y + h > innerHeight - 8) y = evt.clientY - h - pad;
      t.style.left = Math.max(8, x) + "px";
      t.style.top = Math.max(8, y) + "px";
    },
    hide() { tipEl().classList.remove("show"); },
  };

  // ------------------------------------------------------------------ helpers
  function el(tag, attrs, parent) {
    const e = document.createElementNS(SVGNS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function h(tag, cls, text, parent) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }

  // in-place SVG updates: a node is reused when the same tag sits at the same position;
  // attributes it no longer gets are removed, so a reused node never keeps stale state
  function setAttrs(e, obj) {
    if (e._keys) for (const k of e._keys) if (!(k in obj)) e.removeAttribute(k);
    for (const k in obj) {
      const v = String(obj[k]);
      if (e.getAttribute(k) !== v) e.setAttribute(k, v);
    }
    e._keys = Object.keys(obj);
    return e;
  }
  function cursor(parent) { return { parent, i: 0 }; }
  function put(cur, tag, attrs, text) {
    let e = cur.parent.children[cur.i];
    if (!e || e.tagName !== tag) {
      const n = document.createElementNS(SVGNS, tag);
      if (e) cur.parent.replaceChild(n, e); else cur.parent.appendChild(n);
      e = n;
    }
    cur.i++;
    setAttrs(e, attrs);
    if (text !== undefined) {
      const s = text === null ? "" : String(text);
      if (e.textContent !== s) e.textContent = s;
    }
    return e;
  }
  function endCursor(cur) { while (cur.parent.children.length > cur.i) cur.parent.lastElementChild.remove(); }

  // one set of pointer listeners per container instead of one per node; a hover target carries _tip(): [title, rows]
  function tipTarget(e) {
    for (let t = e.target; t && t !== e.currentTarget; t = t.parentNode) if (t._tip) return t;
    return null;
  }
  function tipsOn(container) {
    if (container._tips) return;
    container._tips = true;
    container.addEventListener("pointerover", (e) => {
      const t = tipTarget(e);
      if (t) { const [title, rows] = t._tip(); tip.show(e, title, rows); }
    });
    container.addEventListener("pointermove", (e) => { if (tipTarget(e)) tip.move(e); });
    container.addEventListener("pointerout", (e) => {
      const t = tipTarget(e);
      if (t && !t.contains(e.relatedTarget)) tip.hide();
    });
  }

  function niceTicks(max, count = 4) {
    if (!(max > 0)) return [0, 1];
    const raw = max / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    const ticks = [];
    for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
    if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
    return ticks;
  }

  // ticks in round units: "bytes" -> 1 KB/MB/GB steps of 1024, "bits" -> round bit rates for byte/s values
  function ticksFor(max, count, mode) {
    if (mode === "bytes" && max > 0) {
      const unit = Math.pow(1024, Math.max(0, Math.floor(Math.log(max) / Math.log(1024))));
      return niceTicks(max / unit, count).map((t) => t * unit);
    }
    if (mode === "bits") return niceTicks(max * 8, count).map((t) => t / 8);
    return niceTicks(max, count);
  }

  function seqColor(t) {          // t in [0,1] -> sequential ramp (skips the faintest step)
    const r = ramp();
    const i = Math.round(1 + Math.max(0, Math.min(1, t)) * (r.length - 2));
    return r[i];
  }

  function luminance(hex) {
    const n = parseInt(hex.replace("#", ""), 16);
    const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }

  function topBar(x, y, w, hgt) {     // 4px rounded data-end, square at the baseline
    const r = Math.min(4, w / 2, hgt);
    return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
  }

  function empty(container, msg) {
    container.replaceChildren();
    h("div", "empty", msg || "Nothing recorded in this range yet.", container);
  }

  function legend(container, series, kind) {
    const lg = h("div", "legend", null, container);
    for (const s of series) {
      const sp = h("span", null, null, lg);
      const i = h("i", kind === "line" ? "line" + (s.dash ? " " + s.dash : "") : null, null, sp);
      if (s.dash) i.style.color = s.color; else i.style.background = s.color;
      sp.appendChild(document.createTextNode(s.name));
    }
  }

  // ------------------------------------------------------------------ column chart (1+ series, grouped)
  function columns(container, opt) {
    const { labels, tips, series } = opt;
    const height = opt.height || 180;
    const yFmt = opt.yFmt || fmt.num;
    const vFmt = opt.vFmt || yFmt;
    const stacked = !!opt.stacked;
    const max = stacked
      ? Math.max(0, ...labels.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0)))
      : Math.max(0, ...series.flatMap((s) => s.values));
    if (!labels.length || max <= 0) return empty(container, opt.emptyMsg);
    const W = Math.max(220, container.clientWidth || 600);     // size before writing to the DOM
    container.replaceChildren();
    tipsOn(container);
    if (series.length > 1) legend(container, series);
    const wrap = h("div", "chart", null, container);
    const ticks = ticksFor(max, opt.ticks || 4, opt.tickMode);
    const top = ticks[ticks.length - 1];
    const left = Math.max(...ticks.map((t) => yFmt(t).length)) * 6.5 + 10;
    const pad = { l: left, r: 4, t: 8, b: 22 };
    const pw = W - pad.l - pad.r, ph = height - pad.t - pad.b;
    const svg = el("svg", { viewBox: `0 0 ${W} ${height}`, height, role: "img", "aria-label": opt.aria || "" }, wrap);
    for (const t of ticks) {
      const y = pad.t + ph - (t / top) * ph;
      el("line", { x1: pad.l, x2: W - pad.r, y1: y, y2: y, class: t === 0 ? "baseline" : "gridline" }, svg);
      el("text", { x: pad.l - 8, y: y + 4, "text-anchor": "end", class: "tick" }, svg).textContent = yFmt(t);
    }
    const n = labels.length, band = pw / n, k = stacked ? 1 : series.length, gap = 2;
    const barW = Math.max(1, Math.min(24, (band * 0.72 - gap * (k - 1)) / k));
    const groupW = barW * k + gap * (k - 1);
    const every = Math.max(1, Math.ceil((n * (opt.labelW || 46)) / pw));
    labels.forEach((lab, i) => {
      const x0 = pad.l + i * band;
      const g = el("g", { class: "col" }, svg);
      const hit = el("rect", { x: x0, y: pad.t, width: band, height: ph, class: "hit" }, g);
      if (stacked) {                 // segments bottom-up, 2px surface gap between them, rounded top only
        const x = x0 + (band - barW) / 2;
        const segs = series.map((s) => ({ v: s.values[i] || 0, color: s.color })).filter((sg) => sg.v > 0);
        let acc = 0;
        segs.forEach((sg, idx) => {
          const yBottom = pad.t + ph - (acc / top) * ph;
          acc += sg.v;
          const yTop = pad.t + ph - (acc / top) * ph;
          const hgt = yBottom - yTop - (idx > 0 ? gap : 0);
          if (hgt <= 0.5) return;
          el("path", { d: idx === segs.length - 1 ? topBar(x, yTop, barW, hgt) : `M${x},${yTop}h${barW}v${hgt}h${-barW}Z`,
            fill: sg.color, class: "bar" }, g);
        });
      } else {
        series.forEach((s, j) => {
          const v = s.values[i] || 0;
          if (v <= 0) return;
          const bh = Math.max(1, (v / top) * ph);
          const x = x0 + (band - groupW) / 2 + j * (barW + gap);
          el("path", { d: topBar(x, pad.t + ph - bh, barW, bh), fill: s.color, class: "bar" }, g);
        });
      }
      hit._tip = () => [tips ? tips[i] : lab,
        series.map((s) => ({ label: s.name, value: (s.fmt || vFmt)(s.values[i] || 0), color: s.color }))
          .concat(opt.extraRows ? opt.extraRows(i) : [])];
      if (i % every === 0 && lab) {
        el("text", { x: x0 + band / 2, y: height - 6, "text-anchor": "middle", class: "tick" }, svg).textContent = lab;
      }
    });
  }

  // ------------------------------------------------------------------ line/area chart with crosshair
  // the chart in a container is patched in place each render; a.st holds the latest geometry for the hover handlers
  const areaKept = new WeakMap();      // container -> { wrap, svg, lsig, st }
  function areaHover(a, e) {
    const s = a.st;
    if (!s) return;
    const r = s.svg.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * s.W;
    const xv = s.x0 + ((px - s.pad.l) / s.pw) * (s.x1 - s.x0);
    let bi = 0;
    s.xs.forEach((x, i) => { if (Math.abs(x - xv) < Math.abs(s.xs[bi] - xv)) bi = i; });
    const cx = s.sx(s.xs[bi]);
    s.cross.setAttribute("x1", cx); s.cross.setAttribute("x2", cx); s.cross.setAttribute("visibility", "visible");
    s.series.forEach((ser, j) => {
      s.dots[j].setAttribute("cx", cx); s.dots[j].setAttribute("cy", s.sy(ser.values[bi] || 0));
      s.dots[j].setAttribute("visibility", "visible");
    });
    tip.show(e, s.opt.xFmt ? s.opt.xFmt(s.xs[bi]) : String(s.xs[bi]),
      s.series.map((ser) => ({ label: ser.name, value: s.yFmt(ser.values[bi] || 0), color: ser.color })));
  }
  function area(container, opt) {
    const { xs, series } = opt;
    const height = opt.height || 170;
    const yFmt = opt.yFmt || fmt.num;
    const W = Math.max(220, container.clientWidth || 600);      // size before writing to the DOM
    const max = Math.max(opt.yMax || 1, ...series.flatMap((s) => s.values));
    const ticks = ticksFor(max, 3, opt.tickMode);
    const top = ticks[ticks.length - 1];
    const left = Math.max(...ticks.map((t) => yFmt(t).length)) * 6.3 + 10;
    const pad = { l: left, r: 8, t: 8, b: 22 };
    const pw = W - pad.l - pad.r, ph = height - pad.t - pad.b;
    // keep wrap + svg while the container still holds them and the legend is unchanged
    const lsig = series.length > 1 ? series.map((s) => s.name + "|" + s.color).join(";") : "";
    let a = areaKept.get(container);
    if (!a || a.lsig !== lsig || container.lastChild !== a.wrap || container.childNodes.length !== (lsig ? 2 : 1)) {
      container.replaceChildren();
      if (series.length > 1) legend(container, series, "line");   // one series: the card title names it
      const wrap = h("div", "chart", null, container);
      a = { wrap, svg: el("svg", {}, wrap), lsig, st: null };
      areaKept.set(container, a);
    }
    const svg = a.svg, c = cursor(svg);
    setAttrs(svg, { viewBox: `0 0 ${W} ${height}`, height });
    for (const t of ticks) {
      const y = pad.t + ph - (t / top) * ph;
      put(c, "line", { x1: pad.l, x2: W - pad.r, y1: y, y2: y, class: t === 0 ? "baseline" : "gridline" });
      put(c, "text", { x: pad.l - 8, y: y + 4, "text-anchor": "end", class: "tick" }, yFmt(t));
    }
    if (xs.length < 2) { endCursor(c); a.st = null; return; }
    const x0 = opt.xMin != null ? opt.xMin : xs[0], x1 = opt.xMax != null ? opt.xMax : xs[xs.length - 1];
    const sx = (x) => pad.l + ((x - x0) / (x1 - x0 || 1)) * pw;
    const sy = (v) => pad.t + ph - (v / top) * ph;
    for (const s of series) {
      const pts = xs.map((x, i) => `${sx(x).toFixed(1)},${sy(s.values[i] || 0).toFixed(1)}`);
      put(c, "path", { d: `M${sx(xs[0])},${sy(0)}L${pts.join("L")}L${sx(xs[xs.length - 1])},${sy(0)}Z`,
        fill: s.color, "fill-opacity": 0.1 });
      put(c, "path", { d: "M" + pts.join("L"), fill: "none", stroke: s.color, "stroke-width": 2,
        "stroke-linejoin": "round", "stroke-linecap": "round" });
    }
    for (const [x, lab] of opt.xTicks || []) {
      put(c, "text", { x: sx(x), y: height - 6, "text-anchor": "middle", class: "tick" }, lab);
    }
    const cross = put(c, "line", { y1: pad.t, y2: pad.t + ph, class: "crosshair", visibility: "hidden" });
    const dots = series.map((s) => put(c, "circle", { r: 4, fill: s.color, stroke: css("--surface"), "stroke-width": 2, visibility: "hidden" }));
    const hit = put(c, "rect", { x: pad.l, y: pad.t, width: pw, height: ph, class: "hit" });
    endCursor(c);
    a.st = { xs, series, yFmt, opt, W, x0, x1, pad, pw, sx, sy, cross, dots, svg };
    if (!hit._wired) {          // wired once per hit rect; the handlers read the current state from a.st
      hit._wired = true;
      hit.addEventListener("pointermove", (e) => areaHover(a, e));
      hit.addEventListener("pointerleave", () => {
        tip.hide();
        if (a.st) {
          a.st.cross.setAttribute("visibility", "hidden");
          a.st.dots.forEach((d) => d.setAttribute("visibility", "hidden"));
        }
      });
    }
  }

  // ------------------------------------------------------------------ line chart over buckets: several series, crosshair,
  // running total, smoothing, a dashed "previous period", a dotted trend, drag-to-zoom on x.
  // A value is a number, or null for "no reading" (drawn as a gap). opt.cut = last bucket that has started; later ones stay empty.
  const lineKept = new WeakMap();     // container -> { wrap, svg, reset, lsig, zoom, zkey, st, opt }

  function linePath(pts) { return "M" + pts.map((p) => p[0].toFixed(1) + "," + p[1].toFixed(1)).join("L"); }
  function monotonePath(pts) {        // Fritsch-Carlson: smooth but never overshoots the data, so it never dips below 0
    const n = pts.length;
    if (n < 3) return linePath(pts);
    const d = [], m = [];
    for (let i = 0; i < n - 1; i++) d.push((pts[i + 1][1] - pts[i][1]) / (pts[i + 1][0] - pts[i][0]));
    m.push(d[0]);
    for (let i = 1; i < n - 1; i++) m.push(d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2);
    m.push(d[n - 2]);
    for (let i = 0; i < n - 1; i++) {
      if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
      const a = m[i] / d[i], b = m[i + 1] / d[i], s = a * a + b * b;
      if (s > 9) { const t = 3 / Math.sqrt(s); m[i] = t * a * d[i]; m[i + 1] = t * b * d[i]; }
    }
    let p = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
    for (let i = 0; i < n - 1; i++) {
      const [x0, y0] = pts[i], [x1, y1] = pts[i + 1], dx = (x1 - x0) / 3;
      p += `C${(x0 + dx).toFixed(1)},${(y0 + m[i] * dx).toFixed(1)} ${(x1 - dx).toFixed(1)},${(y1 - m[i + 1] * dx).toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
    }
    return p;
  }
  function runs(vals, i0, i1, sx, sy) {      // unbroken stretches of readings
    const out = [];
    let cur = null;
    for (let i = i0; i <= i1; i++) {
      const v = vals[i];
      if (v == null) { cur = null; continue; }
      if (!cur) out.push(cur = []);
      cur.push([sx(i), sy(v)]);
    }
    return out;
  }

  function line(container, opt) {
    const labels = opt.labels, n = labels.length;
    const height = opt.height || 180, yFmt = opt.yFmt || fmt.num, vFmt = opt.vFmt || yFmt;
    const cut = Math.min(n - 1, opt.cut != null ? opt.cut : n - 1);
    const prep = (s, last) => {
      let acc = 0;
      const values = [];
      for (let i = 0; i < n; i++) {
        let v = i > last ? null : s.values[i];
        if (v == null) v = null;
        else if (opt.cumulative) v = acc += v;
        values.push(v);
      }
      return Object.assign({}, s, { values });
    };
    const main = opt.series.map((s) => prep(s, cut));
    const comp = (opt.compare || []).map((s) => Object.assign(prep(s, n - 1), { dash: "dash" }));
    // projection: values already in chart units (null where there is none), drawn dashed from the last real reading on
    if (opt.projection) comp.push(Object.assign({}, opt.projection, { dash: "dash", proj: true }));
    const all = main.concat(comp);
    if (!n || !main.some((s) => s.values.some((v) => v))) { lineKept.delete(container); return empty(container, opt.emptyMsg); }
    const W = Math.max(220, container.clientWidth || 600);      // size before writing to the DOM
    const trend = opt.trend && main[0].values.filter((v) => v != null).length >= 3;
    const leg = all.length > 1 || trend ? all.concat(trend ? [{ name: "Trend", color: css("--muted"), dash: "dot" }] : []) : null;
    const lsig = leg ? leg.map((s) => s.name + "|" + s.color + "|" + (s.dash || "")).join(";") : "";
    let a = lineKept.get(container);
    if (!a || a.lsig !== lsig || container.lastChild !== a.wrap || container.childNodes.length !== (lsig ? 2 : 1)) {
      container.replaceChildren();
      if (leg) legend(container, leg, "line");
      const wrap = h("div", "chart", null, container);
      const svg = el("svg", {}, wrap);
      const reset = h("button", "btn zoomreset", "Reset zoom", wrap);
      reset.type = "button";
      a = { wrap, svg, reset, lsig, zoom: a ? a.zoom : null, zkey: a ? a.zkey : null, st: null, opt: null };
      lineKept.set(container, a);
      wireLine(container, a);
    }
    if (a.zkey !== opt.zoomKey || (a.zoom && a.zoom[1] > n - 1)) { a.zoom = null; a.zkey = opt.zoomKey; }
    a.opt = opt;
    const [z0, z1] = a.zoom || [0, n - 1], span = z1 - z0;
    a.reset.hidden = !a.zoom;
    const max = Math.max(opt.yMax || 0, ...all.flatMap((s) => s.values.slice(z0, z1 + 1).filter((v) => v != null)));
    const ticks = ticksFor(max, opt.ticks || 4, opt.tickMode);
    const top = ticks[ticks.length - 1];
    const left = Math.max(...ticks.map((t) => yFmt(t).length)) * 6.5 + 10;
    const pad = { l: left, r: 10, t: 8, b: 22 };
    const pw = W - pad.l - pad.r, ph = height - pad.t - pad.b;
    const sx = (i) => pad.l + (span ? ((i - z0) / span) * pw : pw / 2);
    const sy = (v) => pad.t + ph - (v / top) * ph;
    const svg = a.svg;
    setAttrs(svg, { viewBox: `0 0 ${W} ${height}`, height, role: "img", "aria-label": opt.aria || "" });
    svg.replaceChildren();
    for (const t of ticks) {
      const y = pad.t + ph - (t / top) * ph;
      el("line", { x1: pad.l, x2: W - pad.r, y1: y, y2: y, class: t === 0 ? "baseline" : "gridline" }, svg);
      el("text", { x: pad.l - 8, y: y + 4, "text-anchor": "end", class: "tick" }, svg).textContent = yFmt(t);
    }
    const every = Math.max(1, Math.ceil(((span + 1) * (opt.labelW || 46)) / pw));
    for (let i = z0; i <= z1; i++) {
      if (labels[i] && (i - z0) % every === 0) {
        el("text", { x: sx(i), y: height - 6, "text-anchor": "middle", class: "tick" }, svg).textContent = labels[i];
      }
    }
    const drawSeries = (s, dashed) => {
      for (const r of runs(s.values, z0, z1, sx, sy)) {
        if (r.length === 1) { el("circle", { cx: r[0][0], cy: r[0][1], r: 2.5, fill: s.color }, svg); continue; }
        const d = opt.smooth ? monotonePath(r) : linePath(r);
        if (!dashed && main.length === 1) {
          el("path", { d: `${d}L${r[r.length - 1][0].toFixed(1)},${sy(0)}L${r[0][0].toFixed(1)},${sy(0)}Z`, fill: s.color, "fill-opacity": 0.08 }, svg);
        }
        const at = { d, fill: "none", stroke: s.color, "stroke-width": dashed ? 1.5 : 2, "stroke-linejoin": "round", "stroke-linecap": "round" };
        if (dashed) { at["stroke-dasharray"] = s.proj ? "8 4" : "5 4"; at["stroke-opacity"] = s.proj ? 0.9 : 0.75; }
        el("path", at, svg);
      }
    };
    comp.forEach((s) => drawSeries(s, true));
    main.forEach((s) => drawSeries(s, false));
    if (opt.partial && cut >= z0 && cut <= z1) {      // the bucket holding "now" is still filling up: hollow marker, "so far" in the tooltip
      for (const s of main) {
        if (s.values[cut] != null) el("circle", { cx: sx(cut), cy: sy(s.values[cut]), r: 3, fill: css("--surface"), stroke: s.color, "stroke-width": 1.5 }, svg);
      }
    }
    if (trend) {          // least-squares fit over the readings on screen; drawn only across buckets that have happened
      const pts = [];
      for (let i = z0; i <= Math.min(z1, cut); i++) if (main[0].values[i] != null) pts.push([i, main[0].values[i]]);
      if (pts.length >= 3) {
        const k = pts.length, mx = pts.reduce((s, p) => s + p[0], 0) / k, my = pts.reduce((s, p) => s + p[1], 0) / k;
        const sxx = pts.reduce((s, p) => s + (p[0] - mx) ** 2, 0), sxy = pts.reduce((s, p) => s + (p[0] - mx) * (p[1] - my), 0);
        const b = sxx ? sxy / sxx : 0, f = (i) => Math.max(0, my + b * (i - mx));
        const i0 = pts[0][0], i1 = pts[k - 1][0];
        el("path", { d: `M${sx(i0).toFixed(1)},${sy(f(i0)).toFixed(1)}L${sx(i1).toFixed(1)},${sy(f(i1)).toFixed(1)}`,
          stroke: css("--muted"), "stroke-width": 2, "stroke-dasharray": "1 5", "stroke-linecap": "round", fill: "none" }, svg);
      }
    }
    const cross = el("line", { y1: pad.t, y2: pad.t + ph, class: "crosshair", visibility: "hidden" }, svg);
    const dots = all.map((s) => el("circle", { r: 4, fill: s.color, stroke: css("--surface"), "stroke-width": 2, visibility: "hidden" }, svg));
    const sel = el("rect", { y: pad.t, height: ph, class: "zoomsel", visibility: "hidden" }, svg);
    el("rect", { x: pad.l, y: pad.t, width: pw, height: ph, class: "hit" + (span ? " zoomable" : "") }, svg);
    a.st = { z0, z1, span, cut, W, pad, pw, sx, sy, cross, dots, sel, all, main, comp, opt, vFmt, svg };
  }

  function lineAt(s, e) {
    const r = s.svg.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * s.W;
    const i = s.span ? Math.round(s.z0 + ((px - s.pad.l) / s.pw) * s.span) : s.z0;
    return { px, i: Math.max(s.z0, Math.min(s.z1, i)) };
  }
  function lineHover(s, i, e) {
    const cx = s.sx(i);
    s.cross.setAttribute("x1", cx); s.cross.setAttribute("x2", cx); s.cross.setAttribute("visibility", "visible");
    s.all.forEach((ser, j) => {
      const v = ser.values[i], d = s.dots[j];
      if (v == null) { d.setAttribute("visibility", "hidden"); return; }
      d.setAttribute("cx", cx); d.setAttribute("cy", s.sy(v)); d.setAttribute("visibility", "visible");
    });
    const val = (ser) => (ser.values[i] == null ? "–" : (ser.fmt || s.vFmt)(ser.values[i]));
    const rows = s.main.map((ser) => ({ label: ser.name, value: val(ser), color: ser.color }))
      .concat(s.comp.filter((ser) => !ser.proj || ser.values[i] != null).map((ser) => ({ label: ser.name + (ser.tips && ser.tips[i] ? " · " + ser.tips[i] : ""), value: val(ser), color: ser.color })));
    if (s.opt.extraRows && i <= s.cut) rows.push(...s.opt.extraRows(i));
    const title = (s.opt.tips ? s.opt.tips[i] : s.opt.labels[i]) + (i > s.cut ? " (not yet)" : i === s.cut && s.opt.partial ? " (so far)" : "");
    tip.show(e, title, rows);
  }
  function lineLeave(a) {
    tip.hide();
    if (!a.st) return;
    a.st.cross.setAttribute("visibility", "hidden");
    a.st.dots.forEach((d) => d.setAttribute("visibility", "hidden"));
  }
  function wireLine(container, a) {      // wired once per svg; the handlers read the latest geometry from a.st
    const svg = a.svg;
    let drag = null;
    svg.addEventListener("pointerdown", (e) => {
      const s = a.st;
      if (!s || e.button !== 0 || !s.span || container.closest(".editing")) return;     // editing the layout: let the card drag
      const { px } = lineAt(s, e);
      if (px < s.pad.l || px > s.pad.l + s.pw) return;
      drag = { x: px };
      svg.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    svg.addEventListener("pointermove", (e) => {
      const s = a.st;
      if (!s) return;
      const { px, i } = lineAt(s, e);
      if (drag) {
        const x0 = Math.max(s.pad.l, Math.min(drag.x, px)), x1 = Math.min(s.pad.l + s.pw, Math.max(drag.x, px));
        s.sel.setAttribute("x", x0); s.sel.setAttribute("width", Math.max(0, x1 - x0)); s.sel.setAttribute("visibility", "visible");
        lineLeave(a);
        return;
      }
      if (px < s.pad.l - 4 || px > s.pad.l + s.pw + 4) { lineLeave(a); return; }
      lineHover(s, i, e);
    });
    svg.addEventListener("pointerup", (e) => {
      const s = a.st, start = drag;
      drag = null;
      if (!s || !start) return;
      s.sel.setAttribute("visibility", "hidden");
      const { px } = lineAt(s, e);
      if (Math.abs(px - start.x) < 8) return;          // a click, not a drag
      const toI = (x) => Math.round(s.z0 + ((x - s.pad.l) / s.pw) * s.span);
      const i0 = Math.max(s.z0, toI(Math.min(start.x, px))), i1 = Math.min(s.z1, toI(Math.max(start.x, px)));
      if (i1 - i0 < 1) return;                         // keep at least two buckets on screen
      a.zoom = [i0, i1];
      line(container, a.opt);
    });
    svg.addEventListener("pointercancel", () => { drag = null; if (a.st) a.st.sel.setAttribute("visibility", "hidden"); });
    svg.addEventListener("pointerleave", () => { if (!drag) lineLeave(a); });
    a.reset.addEventListener("click", () => { a.zoom = null; tip.hide(); line(container, a.opt); });
  }

  // ------------------------------------------------------------------ sparkline: no axes, x spans all of `values`, drawn up to `cut`
  function spark(container, values, color, cut = values.length - 1) {
    const sig = values.join() + "|" + color + "|" + cut;
    if (container._sig === sig) return;
    container._sig = sig;
    container.replaceChildren();
    const vals = values.slice(0, cut + 1), max = Math.max(0, ...vals);
    if (vals.length < 2 || max <= 0) return;
    const W = 100, H = 24, n = Math.max(1, values.length - 1);
    const pts = vals.map((v, i) => [(i / n) * W, H - 1 - (v / max) * (H - 2)]);
    const d = linePath(pts);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", "aria-hidden": "true" }, container);
    el("path", { d: `${d}L${pts[pts.length - 1][0].toFixed(1)},${H}L0,${H}Z`, fill: color, "fill-opacity": 0.12 }, svg);
    el("path", { d, fill: "none", stroke: color, "stroke-width": 1.5, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" }, svg);
  }

  // heatmap cells carry _pick = [...args]; one click listener per container calls the latest container._onPick
  function picksOn(container, onPick) {
    container._onPick = onPick || null;
    container.classList.toggle("pickable", !!onPick);
    if (container._picks) return;
    container._picks = true;
    container.addEventListener("click", (e) => {
      const c = e.target.closest(".cell");
      if (c && c._pick && container._onPick) { tip.hide(); container._onPick(...c._pick); }
    });
  }

  // ------------------------------------------------------------------ horizontal bar list
  function barList(container, rows, opt = {}) {
    if (!rows.length) return empty(container, opt.emptyMsg);
    let list = container.firstElementChild;
    if (!(list && container.childNodes.length === 1 && list.className === "barlist")) {
      container.replaceChildren();
      list = h("div", "barlist", null, container);
    }
    const max = Math.max(...rows.map((r) => r.value)) || 1;
    const cols = opt.labelW ? `minmax(0, ${opt.labelW}px) minmax(0, 1fr)` : "";
    rows.forEach((r, i) => {          // rows are reused by position; only changed text and widths are written
      let row = list.children[i];
      if (!row) {                   // build the row's nodes once
        row = h("div", "barrow", null, list);
        h("div", "name", null, row);
        const tr = h("div", "track", null, row);
        h("div", "fill", null, tr);
        h("div", "val", null, tr);
      }
      if (cols) { if (row.style.gridTemplateColumns !== cols) row.style.gridTemplateColumns = cols; }
      else if (row.style.gridTemplateColumns) row.removeAttribute("style");     // the row's only inline style
      const name = row.children[0], track = row.children[1], fill = track.children[0], val = track.children[1];
      if (r.node) {
        if (name.childNodes.length !== 1 || name.firstChild !== r.node) name.replaceChildren(r.node);
      } else {
        const s = r.label === null ? "" : String(r.label);
        if (name.childNodes.length !== 1 || name.firstChild.nodeType !== Node.TEXT_NODE) name.textContent = s;
        else if (name.firstChild.data !== s) name.firstChild.data = s;
      }
      if (name.title !== (r.label || "")) name.title = r.label || "";
      const w = `calc((100% - 70px) * ${r.value / max})`;
      if (fill.style.width !== w) fill.style.width = w;
      const bg = r.color || opt.color || "";
      if (fill.style.background !== bg) fill.style.background = bg;
      const txt = String(r.display != null ? r.display : fmt.num(r.value));
      if (val.textContent !== txt) val.textContent = txt;
    });
    while (list.children.length > rows.length) list.lastElementChild.remove();
  }

  function scaleLegend(container, steps = 5) {
    container.replaceChildren();
    h("span", null, "Less", container);
    const z = h("i", null, null, container);
    z.style.background = css("--zero");
    for (let i = 1; i <= steps; i++) h("i", null, null, container).style.background = seqColor(i / steps);
    h("span", null, "More", container);
  }

  // ------------------------------------------------------------------ weekday x hour punchcard
  const HOURS = (hr) => (hr % 12 || 12) + (hr < 12 ? "a" : "p");
  function punch(container, data, opt = {}) {
    container.replaceChildren();
    tipsOn(container);
    picksOn(container, opt.onPick);
    const hint = opt.onPick ? [{ label: opt.pickHint || "click to open", value: "" }] : [];
    const grid = h("div", "punch", null, container);
    const m = {};
    let max = 0;
    for (const [wd, hr, n] of data) { m[wd + ":" + hr] = n; max = Math.max(max, n); }
    h("div", null, null, grid);
    for (let hr = 0; hr < 24; hr++) h("div", "cl", hr % 3 === 0 ? HOURS(hr) : "", grid);
    const names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    for (const wd of [1, 2, 3, 4, 5, 6, 0]) {
      h("div", "rl", names[wd], grid);
      for (let hr = 0; hr < 24; hr++) {
        const n = m[wd + ":" + hr] || 0;
        const c = h("div", "cell", null, grid);
        if (n > 0) c.style.background = seqColor(Math.sqrt(n / max));
        c._tip = () => [`${names[wd]} ${HOURS(hr)}–${HOURS((hr + 1) % 24)}`, [{ label: "keys + clicks", value: fmt.int(n) }].concat(hint)];
        c._pick = [wd, hr];
      }
    }
    const sc = h("div", "scale", null, container);
    scaleLegend(sc);
  }

  // ------------------------------------------------------------------ 12-month calendar
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const dkey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  function calendar(container, days, opt = {}) {
    container.replaceChildren();
    tipsOn(container);
    picksOn(container, opt.onPick);
    const hint = opt.onPick ? [{ label: "click to open this day", value: "" }] : [];
    const by = {};
    let max = 0;
    for (const d of days) { const v = d[opt.key || "keys"] || 0; by[d.day] = d; max = Math.max(max, v); }
    const grid = h("div", "cal", null, container);
    // opt.start / opt.end (dates, end inclusive): the stretch to show; default the 52 weeks up to today
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const last = opt.end && opt.end < today ? new Date(opt.end) : today;
    const first = opt.start ? new Date(opt.start) : null;
    const start = first ? new Date(first) : new Date(today.getFullYear(), today.getMonth(), today.getDate() - 7 * 52);
    start.setDate(start.getDate() - ((start.getDay() + 6) % 7));   // back to Monday
    // weekday labels column
    h("div", "ml", "", grid);
    ["Mon", "", "Wed", "", "Fri", "", ""].forEach((t) => h("div", null, t, grid).style.lineHeight = "12px");
    let lastMonth = -1;
    for (let w = new Date(start); w <= last; w.setDate(w.getDate() + 7)) {
      const mon = w.getMonth();
      const partial = lastMonth === -1 && w.getDate() > 14;     // avoid "Oct Nov" crowding in column 1
      h("div", "ml", mon !== lastMonth && !partial ? MONTHS[mon] : "", grid);
      lastMonth = mon;
      for (let i = 0; i < 7; i++) {
        const d = new Date(w); d.setDate(d.getDate() + i);
        const c = h("div", "cell", null, grid);
        if (d > last || (first && d < first)) { c.style.visibility = "hidden"; continue; }
        const row = by[dkey(d)];
        const v = row ? row[opt.key || "keys"] || 0 : 0;
        if (v > 0) c.style.background = seqColor(Math.ceil(Math.sqrt(v / max) * 5) / 5);
        c._tip = () => [d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" }), row ? [
          { label: "keys", value: fmt.int(row.keys) }, { label: "clicks", value: fmt.int(row.clicks) },
          { label: "active", value: fmt.dur(row.active_s) }].concat(hint) : [{ label: "", value: "No activity" }].concat(hint)];
        c._pick = [dkey(d)];
      }
    }
    const sc = h("div", "scale", null, container);
    scaleLegend(sc);
  }

  // ------------------------------------------------------------------ keyboard heatmap
  // [id, label, width] rows; null = gap of `width`
  const KB_ROWS = [
    [["Esc", "Esc"], [null, 1], ["F1"], ["F2"], ["F3"], ["F4"], [null, 0.5], ["F5"], ["F6"], ["F7"], ["F8"], [null, 0.5],
      ["F9"], ["F10"], ["F11"], ["F12"], [null, 0.25], ["PrtSc", "PrtSc"], ["ScrLk", "ScrLk"], ["Pause", "Pause"]],
    [["`"], ["1"], ["2"], ["3"], ["4"], ["5"], ["6"], ["7"], ["8"], ["9"], ["0"], ["-"], ["="], ["Backspace", "Backspace", 2],
      [null, 0.25], ["Ins"], ["Home"], ["PgUp"], [null, 0.25], ["NumLock", "Num"], ["Num/", "/"], ["Num*", "*"], ["Num-", "-"]],
    [["Tab", "Tab", 1.5], ["Q"], ["W"], ["E"], ["R"], ["T"], ["Y"], ["U"], ["I"], ["O"], ["P"], ["["], ["]"], ["\\", "\\", 1.5],
      [null, 0.25], ["Del"], ["End"], ["PgDn"], [null, 0.25], ["Num7", "7"], ["Num8", "8"], ["Num9", "9"], ["Num+", "+", 1, 2]],
    [["CapsLock", "Caps", 1.75], ["A"], ["S"], ["D"], ["F"], ["G"], ["H"], ["J"], ["K"], ["L"], [";"], ["'"], ["Enter", "Enter", 2.25],
      [null, 3.5], ["Num4", "4"], ["Num5", "5"], ["Num6", "6"]],
    [["LShift", "Shift", 2.25], ["Z"], ["X"], ["C"], ["V"], ["B"], ["N"], ["M"], [","], ["."], ["/"], ["RShift", "Shift", 2.75],
      [null, 1.25], ["Up", "↑"], [null, 1.25], ["Num1", "1"], ["Num2", "2"], ["Num3", "3"], ["NumEnter", "Ent", 1, 2]],
    [["LCtrl", "Ctrl", 1.25], ["LWin", "Win", 1.25], ["LAlt", "Alt", 1.25], ["Space", "Space", 6.25], ["RAlt", "Alt", 1.25],
      ["RWin", "Win", 1.25], ["Menu", "Menu", 1.25], ["RCtrl", "Ctrl", 1.25], [null, 0.25], ["Left", "←"], ["Down", "↓"], ["Right", "→"],
      [null, 0.25], ["Num0", "0", 2], ["Num.", "."]],
  ];
  const KB_W = 22.5, KB_H = 6.5;
  const KB_IDS = new Set(KB_ROWS.flat().filter((k) => k[0]).map((k) => k[0]));

  function keyboard(container, counts, opt = {}) {
    container.replaceChildren();
    tipsOn(container);
    const vals = Object.values(counts);
    const max = Math.max(1, ...vals);
    const total = vals.reduce((a, b) => a + b, 0) || 1;
    KB_ROWS.forEach((row, ri) => {
      let x = 0;
      const y = ri === 0 ? 0 : ri + 0.25;
      for (const [id, label, w = 1, hh = 1] of row) {
        if (!id) { x += label; continue; }      // gap entries are [null, width]
        const k = h("div", "k", label || id, container);
        const gap = 0.08;
        k.style.left = ((x + gap / 2) / KB_W) * 100 + "%";
        k.style.top = ((y + gap / 2) / KB_H) * 100 + "%";
        k.style.width = ((w - gap) / KB_W) * 100 + "%";
        k.style.height = ((hh - gap) / KB_H) * 100 + "%";
        const n = counts[id] || 0;
        if (n > 0) {
          const c = seqColor(Math.sqrt(n / max));
          k.style.background = c;
          k.style.color = luminance(c) < 0.3 ? "#fff" : "#0b0b0b";
          k.style.borderColor = "transparent";
        }
        k._tip = () => [opt.names && opt.names[id] || label || id,
          [{ label: "presses", value: fmt.int(n) }, { label: "of all keys", value: fmt.pct(n / total) }]];
        x += w;
      }
    });
  }

  // ------------------------------------------------------------------ click heatmap over the monitor layout
  const clickKept = new WeakMap();     // canvas -> { key, cells, map } of the last full draw
  function clickMap(canvas, data) {
    const { grid, screen } = data;
    const cw = canvas.parentElement.clientWidth || 800;      // read sizes before writing
    const aspect = screen.h / screen.w;
    const ch = Math.round(Math.min(cw * aspect, 520));
    const scale = ch / screen.h;
    const width = Math.round(screen.w * scale);
    const dpr = window.devicePixelRatio || 1;
    const bw = Math.trunc(width * dpr), bh = Math.trunc(ch * dpr);
    const cells = grid.cells;
    // everything the pixels depend on; when it matches the last draw, the canvas already shows the right image
    const key = JSON.stringify([bw, bh, dpr, width, ch, screen, grid.w, grid.h, css("--seq"), css("--surface-2"), css("--axis")]);
    const prev = clickKept.get(canvas);
    const same = prev && prev.key === key && prev.cells.length === cells.length &&
      cells.every((c, i) => { const p = prev.cells[i]; return p[0] === c[0] && p[1] === c[1] && p[2] === c[2]; });
    if (!same) {
      if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }   // a resize clears the canvas
      canvas.style.width = width + "px"; canvas.style.height = ch + "px";
      canvas.style.margin = "0 auto";
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);     // set, not scale(): the transform persists when the canvas is not resized
      ctx.clearRect(0, 0, width, ch);
      // monitors
      ctx.fillStyle = css("--surface-2");
      ctx.strokeStyle = css("--axis");
      ctx.lineWidth = 1;
      for (const [mx, my, mw, mh] of screen.monitors) {
        const x = (mx - screen.x) * scale + 2, y = (my - screen.y) * scale + 2;
        ctx.beginPath();
        ctx.roundRect(x, y, mw * scale - 4, mh * scale - 4, 8);
        ctx.fill(); ctx.stroke();
      }
      if (cells.length) {
        // 1) density at grid resolution (one pixel per cell), log-scaled so a few hot spots don't wash out the rest
        const small = document.createElement("canvas");
        small.width = grid.w; small.height = grid.h;
        const sctx = small.getContext("2d");
        const sImg = sctx.createImageData(grid.w, grid.h);
        const lmax = Math.log1p(Math.max(...cells.map((c) => c[2])));
        for (const [gx, gy, n] of cells) sImg.data[(gy * grid.w + gx) * 4 + 3] = Math.round((Math.log1p(n) / lmax) * 255);
        sctx.putImageData(sImg, 0, 0);
        // 2) upscale with smoothing + blur for a continuous field
        const off = document.createElement("canvas");
        off.width = width; off.height = ch;
        const o = off.getContext("2d");
        o.imageSmoothingEnabled = true;
        o.imageSmoothingQuality = "high";
        o.filter = `blur(${Math.max(2, (width / grid.w) * 0.9)}px)`;
        o.drawImage(small, 0, 0, width, ch);
        // 3) colorize: interpolate smoothly along the sequential ramp (no banding), faint values stay transparent
        const img = o.getImageData(0, 0, width, ch);
        const r = ramp().slice(2).map((hx) => { const v = parseInt(hx.slice(1), 16); return [(v >> 16) & 255, (v >> 8) & 255, v & 255]; });
        const amax = img.data.reduce((m, v, i) => (i % 4 === 3 && v > m ? v : m), 1);
        for (let i = 3; i < img.data.length; i += 4) {
          const a = img.data[i];
          if (!a) continue;
          const t = a / amax, p = t * (r.length - 1), k = Math.min(r.length - 2, Math.floor(p)), f = p - k;
          img.data[i - 3] = r[k][0] + (r[k + 1][0] - r[k][0]) * f;
          img.data[i - 2] = r[k][1] + (r[k + 1][1] - r[k][1]) * f;
          img.data[i - 1] = r[k][2] + (r[k + 1][2] - r[k][2]) * f;
          img.data[i] = Math.min(255, Math.pow(t, 0.7) * 300);
        }
        o.filter = "none";
        o.putImageData(img, 0, 0);
        ctx.save();
        ctx.beginPath();
        for (const [mx, my, mw, mh] of screen.monitors) {
          ctx.roundRect((mx - screen.x) * scale + 2, (my - screen.y) * scale + 2, mw * scale - 4, mh * scale - 4, 8);
        }
        ctx.clip();
        ctx.drawImage(off, 0, 0, width, ch);
        ctx.restore();
      }
      const hover = {};
      for (const [gx, gy, n] of cells) hover[gx + ":" + gy] = n;
      clickKept.set(canvas, { key, cells, map: hover });
    }
    // hover readout
    const map = clickKept.get(canvas).map;
    canvas.onpointermove = (e) => {
      const b = canvas.getBoundingClientRect();
      const gx = Math.floor(((e.clientX - b.left) / b.width) * grid.w);
      const gy = Math.floor(((e.clientY - b.top) / b.height) * grid.h);
      let n = 0;
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) n += map[(gx + dx) + ":" + (gy + dy)] || 0;
      if (n) tip.show(e, "Around here", [{ label: "clicks", value: fmt.int(n) }]); else tip.hide();
    };
    canvas.onpointerleave = tip.hide;
  }

  return { fmt, tip, h, css, empty, columns, area, line, spark, barList, punch, calendar, keyboard, KB_IDS, clickMap, seqColor, dkey, MONTHS };
})();
