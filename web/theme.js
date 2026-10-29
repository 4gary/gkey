// Applies the saved theme before the page paints (its own file: the page allows no inline scripts).
try { const t = localStorage.getItem("gk-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
