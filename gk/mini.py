"""Mini display: a small always-on-top window with a few live stats, and the dashboard window.

The main process runs each window as its own process (pywebview needs its own UI thread):
    python -m gk.mini --port 8413
    python -m gk.mini --dashboard --port 8413 --folder <data folder>
Without pywebview the pages open in Edge app windows instead, and the mini page closes itself
when its saved "open" flag goes false.
"""
import argparse
import http.client
import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request

from . import privacy, winapi

log = logging.getLogger("gk.mini")
# Talks only to our own server: never through a system proxy (urllib would otherwise read the Windows
# proxy settings, and its bypass check looks up host names).
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TITLE = "gkeystrokes mini"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLL_FAST = 2.0      # settings poll, seconds: while the window is moved or a watched setting just changed
POLL_IDLE = 5.0      # settings poll, seconds: otherwise (a dashboard or tray change shows within ~5 s)
FAST_FOR = 10.0      # seconds a move or a change keeps the fast poll on
GONE_AFTER = 10.0    # main app unreachable this long: the window closes itself
WATCHED = ("open", "locked", "opacity", "clickthrough")   # settings the window acts on or reacts to
NET_ERRORS = (OSError, ValueError, KeyError, http.client.HTTPException)


def has_webview():
    return importlib.util.find_spec("webview") is not None


def mini_cfg(store, patch=None):
    """Saved mini settings; the rules live in gk.server. Imported here so the window process stays small."""
    from .server import mini_cfg as server_mini_cfg
    return server_mini_cfg(store, patch)


def mutex_name(folder):
    """One mini window process per data folder."""
    return winapi.instance_name("gkeystrokes_mini", folder)


def webview_env():
    """WebView2 is Edge inside: it gets the same no-outside-traffic flags as the Edge app window."""
    from .main import EDGE_FLAGS
    return dict(os.environ, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=" ".join(
        '"%s"' % f if " " in f else f for f in EDGE_FLAGS if f not in ("--no-first-run", "--no-default-browser-check")))


def _due(now, last, every):
    return now - last >= every - 0.5     # the loop wakes once a second, so allow a slightly early wake-up


class MiniController:
    """Main-process side: starts and stops the window. Its state is the saved "open" flag."""

    def __init__(self, port, store):
        self.port = port
        self.store = store
        self.proc = None

    def cfg(self):
        return mini_cfg(self.store)

    def is_open(self):
        if self.proc is not None:
            return self.proc.poll() is None
        return self.cfg()["open"]

    def start(self):
        if self.is_open() and self.proc is not None:
            return
        from .store import data_dir
        folder = data_dir()
        if has_webview():
            if winapi.instance_running(mutex_name(folder)):    # a mini from an earlier start is still up
                log.info("mini window already running")
                return
            self.proc = subprocess.Popen([sys.executable, "-m", "gk.mini", "--port", str(self.port)], cwd=ROOT,
                                         env=webview_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            log.warning("pywebview not installed; opening the mini display in an Edge window")
            if winapi.edge_windows(os.path.join(folder, "edge-profile"), TITLE):
                return
            from .main import open_dashboard
            open_dashboard("http://127.0.0.1:%d/mini" % self.port, size="260,200")

    def stop(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()

    def toggle(self):
        if self.is_open():
            mini_cfg(self.store, {"open": False})
            self.stop()
        else:
            mini_cfg(self.store, {"open": True})
            self.start()

    def set_clickthrough(self, flag):
        mini_cfg(self.store, {"clickthrough": bool(flag)})


# ---------------------------------------------------------------- dashboard window (launcher side)

DASH_TITLE = "gkeystrokes"          # also web/index.html <title>


def dash_mutex(folder):
    """One dashboard window process per data folder."""
    return winapi.instance_name("gkeystrokes_dash", folder)


def dash_cmd(port, folder):
    return [sys.executable, "-m", "gk.mini", "--dashboard", "--port", str(port), "--folder", os.path.abspath(folder)]


def dash_args(cmdline):
    """(normalized folder, port) if `cmdline` is a dashboard window process, else None."""
    cmd = list(cmdline or ())
    if "gk.mini" not in cmd or "--dashboard" not in cmd:
        return None
    try:
        folder = cmd[cmd.index("--folder") + 1]
        port = int(cmd[cmd.index("--port") + 1])
    except (ValueError, IndexError):
        return None
    return os.path.normcase(os.path.abspath(folder)), port


def dash_procs(folder):
    """[(pid, port)] of dashboard window processes for this data folder."""
    import psutil
    want = os.path.normcase(os.path.abspath(folder))
    out = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if not (p.info["name"] or "").lower().startswith("python"):
                continue
            a = dash_args(p.info["cmdline"])
        except (psutil.Error, OSError):
            continue
        if a and a[0] == want:
            out.append((p.pid, a[1]))
    return out


def show_dashboard_window(port, folder):
    """Focus this folder's dashboard window (restoring it if minimized), or start one.
    False when pywebview is missing, so the caller opens the Edge app window instead."""
    if not has_webview():
        return False
    import psutil
    for pid, p in dash_procs(folder):
        if p != port:                        # left from a run on another port: it shows a dead page
            try:
                psutil.Process(pid).terminate()
                psutil.Process(pid).wait(3)
            except (psutil.Error, OSError):
                pass
            continue
        wins = winapi.process_windows([pid], DASH_TITLE)
        if wins:
            winapi.focus_window(wins[0])
        return True                          # no window yet: it is still starting
    for _ in range(30):                      # a window just closed may hold its mutex for a moment
        if not winapi.instance_running(dash_mutex(folder)):
            break
        time.sleep(0.1)
    subprocess.Popen(dash_cmd(port, folder), cwd=ROOT, env=webview_env(),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return True


# ---------------------------------------------------------------- window process

def _api(port, body=None):
    req = urllib.request.Request("http://127.0.0.1:%d/api/settings" % port, headers={"Host": "127.0.0.1:%d" % port})
    if body is not None:
        req.data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-GK", "1")
    with _opener.open(req, timeout=3) as r:
        return json.loads(r.read())


def _watch(win, port):
    """Runs in pywebview's background thread: placement, look, and remembering the position.

    The window is checked every second. The settings poll runs every POLL_FAST seconds while the window
    is moving or a watched setting just changed, and every POLL_IDLE seconds otherwise. While the main app
    is unreachable it retries every tick and gives up after GONE_AFTER seconds.
    """
    hwnd, applied = None, {}
    cfg, cfg_at, down_at = None, float("-inf"), None
    fast_until = 0.0
    rect, rect_at, saved = None, 0.0, None
    started = time.monotonic()
    try:
        while True:
            time.sleep(1)
            now = time.monotonic()
            if down_at is not None or _due(now, cfg_at, POLL_FAST if now < fast_until else POLL_IDLE):
                try:
                    new = _api(port)["mini"]
                except NET_ERRORS:
                    down_at = down_at or now
                    if now - down_at >= GONE_AFTER:  # the main app has quit
                        break
                    continue
                cfg_at, down_at = now, None
                if cfg is not None and any(new.get(k) != cfg.get(k) for k in WATCHED):
                    fast_until = now + FAST_FOR      # changed from the dashboard or tray: poll quickly for a bit
                cfg = new
                if not cfg["open"]:
                    break
            if hwnd is None:                         # looked up until found, then the handle is kept
                hwnd = winapi.find_window(TITLE)
                if hwnd is None:
                    if now - started > 60:           # WebView2 can be slow on its first start
                        break
                    continue
                if cfg["x"] is not None and cfg["y"] is not None:
                    winapi.move_window(hwnd, cfg["x"], cfg["y"], cfg["w"], cfg["h"])
            if applied.get("opacity") != cfg["opacity"]:
                winapi.set_window_alpha(hwnd, cfg["opacity"])
                applied["opacity"] = cfg["opacity"]
            if applied.get("clickthrough") != cfg["clickthrough"]:
                winapi.set_click_through(hwnd, cfg["clickthrough"])
                applied["clickthrough"] = cfg["clickthrough"]
            r = winapi.window_rect(hwnd)
            if r is None:                            # window is gone
                break
            if r != rect:
                if rect is not None:                 # being dragged or resized: poll quickly
                    fast_until = now + FAST_FOR
                rect, rect_at = r, now
            elif r != saved and not cfg["locked"] and now - rect_at >= 2 and r[0] > -30000 and r[1] > -30000:
                saved = r
                try:
                    _api(port, {"mini": dict(zip(("x", "y", "w", "h"), r))})
                except NET_ERRORS:
                    pass
    except Exception:
        log.exception("mini window watcher failed")
    finally:
        try:
            win.destroy()
        except Exception:
            pass


def run_window(port):
    import webview
    winapi.set_dpi_aware()
    cfg = _api(port)["mini"]
    win = webview.create_window(TITLE, "http://127.0.0.1:%d/mini" % port, width=cfg["w"], height=cfg["h"],
                                frameless=True, on_top=True, easy_drag=False, resizable=True)
    webview.start(_watch, (win, port))
    try:       # closed by the user (e.g. Alt+F4): don't reopen it on the next start
        _api(port, {"mini": {"open": False}})
    except (OSError, ValueError):
        pass


def _watch_dash(win, port, cfg):
    """Runs in pywebview's background thread: puts the window where it was last time, then saves its size,
    position and maximized state once it has stayed put for 2 seconds. Keeps running while the main app
    restarts; the window closes only when the user closes it."""
    me = os.getpid()
    hwnd, rect, rect_at, saved, saved_max = None, None, 0.0, None, cfg.get("max")
    started = time.monotonic()
    try:
        while True:
            time.sleep(1)
            now = time.monotonic()
            if hwnd is None:
                wins = winapi.process_windows([me], DASH_TITLE)
                if not wins:
                    if now - started > 60:
                        break
                    continue
                hwnd = wins[0]
                if cfg.get("x") is not None and cfg.get("y") is not None:
                    winapi.move_window(hwnd, cfg["x"], cfg["y"], cfg["w"], cfg["h"], topmost=False)
                    saved = (cfg["x"], cfg["y"], cfg["w"], cfg["h"])
                if cfg.get("max"):
                    winapi.maximize_window(hwnd)
                winapi.focus_window(hwnd)
                continue
            r = winapi.window_rect(hwnd)
            if r is None:                            # window is gone
                break
            state = winapi.window_state(hwnd)
            if state == "min":
                continue
            patch = {}
            if (state == "max") != saved_max:
                patch["max"] = state == "max"
            if state == "normal":                    # a maximized window's rect is the screen, not its own size
                if r != rect:
                    rect, rect_at = r, now
                elif r != saved and now - rect_at >= 2 and r[0] > -30000 and r[1] > -30000:
                    patch.update(zip(("x", "y", "w", "h"), r))
            if patch:
                try:
                    _api(port, {"dash": patch})
                except NET_ERRORS:                   # main app restarting: try again next tick
                    continue
                saved_max = patch.get("max", saved_max)
                if "x" in patch:
                    saved = r
    except Exception:
        log.exception("dashboard window watcher failed")


def run_dashboard(port):
    import webview
    winapi.set_dpi_aware()
    try:
        cfg = _api(port)["dash"]
    except (NET_ERRORS + (TypeError,)):
        cfg = {"x": None, "y": None, "w": 1440, "h": 960, "max": False}
    webview.settings["ALLOW_DOWNLOADS"] = True       # Settings > Download CSV
    win = webview.create_window(DASH_TITLE, "http://127.0.0.1:%d/" % port, width=cfg["w"], height=cfg["h"],
                                resizable=True, min_size=(480, 360))
    webview.start(_watch_dash, (win, port, cfg))


if __name__ == "__main__":
    from .store import data_dir                      # only for the log file's location; no database is opened
    ap = argparse.ArgumentParser(prog="gk.mini")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--dashboard", action="store_true", help="the main dashboard window instead of the mini")
    ap.add_argument("--folder", help="data folder (names the dashboard process; must match this run's)")
    args = ap.parse_args()
    privacy.install_guard()
    logging.basicConfig(filename=os.path.join(data_dir(), "gkeystrokes.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", encoding="utf-8")
    if args.dashboard:
        if not args.folder or os.path.normcase(os.path.abspath(args.folder)) != os.path.normcase(data_dir()):
            log.error("dashboard window started for another data folder; exiting")
            sys.exit(1)
        if not winapi.acquire_single_instance(dash_mutex(data_dir())):
            log.info("dashboard window already running; exiting")
            sys.exit(0)
        try:
            run_dashboard(args.port)
        except Exception:
            log.exception("dashboard window failed")
            sys.exit(1)
        sys.exit(0)
    if not winapi.acquire_single_instance(mutex_name(data_dir())):
        log.info("mini window already running; exiting")
        sys.exit(0)
    try:
        run_window(args.port)
    except Exception:
        log.exception("mini window failed")
        sys.exit(1)
