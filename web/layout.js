/* Reorderable, hideable card grids. Used by the Overview and reusable by other pages.
   A container holds [data-widget] children; saved layout is [{id, visible}]. */
"use strict";

const GKLayout = (() => {
  const SVGNS = "http://www.w3.org/2000/svg";
  const defaults = new WeakMap();   // container -> default id order
  const live = new WeakMap();       // container -> {onChange, dragged, next}
  const kids = (c) => [...c.querySelectorAll(":scope > [data-widget]")];
  const ids = (c) => kids(c).map((w) => w.dataset.widget);

  function remember(c) {
    if (!defaults.has(c)) defaults.set(c, ids(c));
    return defaults.get(c);
  }

  function apply(c, saved) {
    const def = remember(c);
    const by = Object.fromEntries(kids(c).map((w) => [w.dataset.widget, w]));
    const vis = {};
    const order = [];
    (Array.isArray(saved) ? saved : []).forEach((s) => {
      if (!s || !(s.id in by) || order.includes(s.id)) return;
      order.push(s.id);
      vis[s.id] = s.visible !== false;
    });
    def.forEach((id, i) => {        // new widgets go right after their default predecessor
      if (order.includes(id)) return;
      const at = i ? order.indexOf(def[i - 1]) + 1 : 0;
      order.splice(at, 0, id);
    });
    order.forEach((id) => {
      c.appendChild(by[id]);
      by[id].classList.toggle("hidden-widget", vis[id] === false);
    });
  }

  function read(c) {
    return kids(c).map((w) => ({ id: w.dataset.widget, visible: !w.classList.contains("hidden-widget") }));
  }

  function emit(c) {
    const s = live.get(c);
    if (s && s.onChange) s.onChange(read(c));
  }

  function label(b, hidden) {
    b.setAttribute("aria-label", hidden ? "Show card" : "Hide card");
    b.title = b.getAttribute("aria-label");
  }

  function eyeButton() {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "widget-eye";
    b.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/>` +
      `<circle cx="12" cy="12" r="3"/><path class="slash" d="M3 3l18 18"/></svg>`;
    label(b, false);
    return b;
  }

  function wire(c) {
    const s = { onChange: null, dragged: null, next: null };
    live.set(c, s);
    c.addEventListener("click", (e) => {          // one listener for all eye buttons in this container
      const b = e.target.closest(".widget-eye");
      const w = b && b.parentNode;
      if (!w || w.parentNode !== c || !w.hasAttribute("data-widget")) return;
      const hide = !w.classList.contains("hidden-widget");
      w.classList.toggle("hidden-widget", hide);
      label(b, hide);
      emit(c);
    });
    c.addEventListener("dragstart", (e) => {
      const w = e.target.closest("[data-widget]");
      if (!c.classList.contains("editing") || !w || w.parentNode !== c) return;
      s.dragged = w;
      s.next = w.nextSibling;
      w.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", w.dataset.widget);
    });
    c.addEventListener("dragover", (e) => {
      if (!s.dragged) return;
      e.preventDefault();             // dropping in a gap between cards keeps the new spot
      const t = e.target.closest("[data-widget]");
      if (!t || t === s.dragged || t.parentNode !== c) return;
      const forward = !!(s.dragged.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING);
      const ref = forward ? t.nextSibling : t;
      if (s.dragged.nextSibling === ref) return;      // already in that gap: skip the DOM move
      c.insertBefore(s.dragged, ref);
    });
    c.addEventListener("drop", (e) => {
      if (!s.dragged) return;
      e.preventDefault();
      emit(c);
    });
    c.addEventListener("dragend", (e) => {
      if (!s.dragged) return;
      if (e.dataTransfer.dropEffect === "none") c.insertBefore(s.dragged, s.next);   // cancelled: put it back
      s.dragged.classList.remove("dragging");
      s.dragged = null;
    });
  }

  function edit(c, on, onChange) {
    remember(c);
    if (!live.has(c)) wire(c);
    live.get(c).onChange = onChange;
    c.classList.toggle("editing", !!on);
    kids(c).forEach((w) => {
      const old = w.querySelector(":scope > .widget-eye");
      if (on && !old) w.prepend(eyeButton());
      if (!on && old) old.remove();
      w.draggable = !!on;
    });
  }

  function reset(c) {
    apply(c, remember(c).map((id) => ({ id, visible: true })));
  }

  return { apply, read, edit, reset };
})();
