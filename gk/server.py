"""Local HTTP server: JSON API + the dashboard files in ./web.

Only answers requests whose Host is 127.0.0.1/localhost (blocks DNS rebinding),
and state-changing POSTs need an X-GK header (blocks cross-site form posts).

Phone link (off by default): a second, view-only server on REMOTE_PORT listens on all
networks and answers only requests carrying the secret key (?k=... once, then a cookie).
It never accepts POSTs. Lockdown mode keeps it off.

Every response carries a strict Content-Security-Policy, so the dashboard pages can't load or send
anything outside this server.
"""
import csv
import hmac
import io
import json
import logging
import os
import re
import secrets
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import psutil

from . import __version__, privacy, winapi
from .store import GRID_H, GRID_W, TooManyPoints, data_dir, local_day

log = logging.getLogger("gk.server")
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
LAYOUT_ID = re.compile(r"[a-z0-9-]{1,40}")
LAYOUTS = ("main", "mini")
REMOTE_PORT = 8414
_remote = {"srv": None, "error": ""}
LOCKED_MSG = "Off while Lockdown mode is on"
PREVIEW = bool(os.environ.get("GK_DATA_DIR"))     # a preview or self-test copy, not the installed tracker
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".ico": "image/x-icon", ".json": "application/json"}
_static_cache = {}      # web/ file path -> ((mtime_ns, size), bytes)
# Pages may only use files and API calls from this same server: no other sites, no inline scripts or styles,
# no plugins, no framing, no form posts elsewhere.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class LocalHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without the host name lookup in HTTPServer.server_bind (socket.getfqdn),
    which on Windows can ask the network (DNS, LLMNR, NetBIOS) who this PC is. The port is held
    exclusively: on Windows SO_REUSEADDR (HTTPServer's default) would let a second process bind it too."""
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def static_bytes(full):
    """A web/ file's bytes. Kept in memory; re-read only when its mtime or size changes, so dev edits still show."""
    st = os.stat(full)              # stat before reading: a write during the read is caught by the next request
    key = (st.st_mtime_ns, st.st_size)
    hit = _static_cache.get(full)
    if hit is not None and hit[0] == key:
        return hit[1]
    with open(full, "rb") as f:
        data = f.read()
    _static_cache[full] = (key, data)
    return data


def clean_layout(items):
    """Keep valid, unique widget entries: [{id, visible}], at most 64."""
    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        if len(out) == 64:
            break
        if not isinstance(it, dict) or not isinstance(it.get("id"), str) or not LAYOUT_ID.fullmatch(it["id"]):
            continue
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        out.append({"id": it["id"], "visible": bool(it.get("visible", True))})
    return out


MINI_DEFAULTS = {"open": False, "x": None, "y": None, "w": 260, "h": 200, "opacity": 0.95, "locked": False,
                 "clickthrough": False}
MINI_RANGE = {"x": (-32000, 32000, int), "y": (-32000, 32000, int), "w": (140, 1200, int),
              "h": (140, 1200, int), "opacity": (0.3, 1.0, float)}


DASH_DEFAULTS = {"x": None, "y": None, "w": 1440, "h": 960, "max": False}     # the dashboard window's placement
DASH_RANGE = {"x": (-32000, 32000, int), "y": (-32000, 32000, int), "w": (480, 10000, int), "h": (360, 10000, int)}


def mini_cfg(store, patch=None):
    """Mini display settings. A patch keeps only known keys, clamps the numbers and is saved."""
    return _window_cfg(store, "mini", MINI_DEFAULTS, MINI_RANGE, ("open", "locked", "clickthrough"), patch)


def dash_cfg(store, patch=None):
    """Dashboard window size, position and maximized flag, kept the same way as the mini's."""
    return _window_cfg(store, "dash", DASH_DEFAULTS, DASH_RANGE, ("max",), patch)


def _window_cfg(store, key, defaults, ranges, flags, patch):
    try:
        cfg = dict(defaults, **json.loads(store.get_meta(key, "{}")))
    except (TypeError, ValueError):
        cfg = dict(defaults)
    for k, v in (patch or {}).items():
        if k in flags:
            cfg[k] = bool(v)
        elif k in ranges:
            lo, hi, kind = ranges[k]
            if v is None and k in ("x", "y"):
                cfg[k] = None
            else:
                try:
                    cfg[k] = max(lo, min(hi, kind(v)))
                except (TypeError, ValueError, OverflowError):
                    pass
    if patch:
        store.set_meta(key, json.dumps(cfg))
    return cfg


def remote_cfg(store, patch=None):
    """Phone link settings: {enabled, key}. A key is made on first use; new_key replaces it."""
    try:
        cfg = dict({"enabled": False, "key": ""}, **json.loads(store.get_meta("remote", "{}")))
    except (TypeError, ValueError):
        cfg = {"enabled": False, "key": ""}
    patch = dict(patch or {})
    if "enabled" in patch:
        cfg["enabled"] = bool(patch["enabled"])
    if not cfg["key"] or patch.get("new_key"):
        cfg["key"] = secrets.token_urlsafe(12)
        patch["new_key"] = True
    if patch:
        store.set_meta("remote", json.dumps({"enabled": cfg["enabled"], "key": cfg["key"]}))
    return cfg


URLS_TTL = 30                   # s. Walking the adapters costs ~6 ms on Windows; GET /api/settings is polled often
_urls_cache = {"at": -1e9, "key": None, "urls": []}


def remote_urls(key):
    """Links to this PC: its name first (works with Tailscale MagicDNS), then each network address.
    Cached per key for URLS_TTL seconds."""
    c = _urls_cache
    now = time.monotonic()
    if c["key"] == key and now - c["at"] < URLS_TTL:
        return c["urls"]
    hosts = [socket.gethostname().lower()]
    try:
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    hosts.append(a.address)
    except Exception:
        pass
    urls = [{"host": h, "tailscale": h.startswith("100.") and 64 <= int(h.split(".")[1]) < 128,
             "url": "http://%s:%d/?k=%s" % (h, REMOTE_PORT, key)} for h in dict.fromkeys(hosts)]
    c.update(at=now, key=key, urls=urls)
    return urls


def set_remote(tracker, on):
    """Start or stop the view-only phone link server. Lockdown mode keeps it off."""
    srv = _remote["srv"]
    locked = privacy.lockdown(tracker.store)
    if locked and on:
        on = False
        _remote["error"] = LOCKED_MSG
    elif not locked and _remote["error"] == LOCKED_MSG:
        _remote["error"] = ""
    if on and srv is None:
        try:
            srv = LocalHTTPServer(("0.0.0.0", REMOTE_PORT), make_handler(tracker, REMOTE_PORT, remote=True))
        except OSError as e:
            _remote["error"] = "Port %d is busy (%s)" % (REMOTE_PORT, e.strerror or e)
            log.warning("phone link not started: %s", e)
            return
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, name="http-remote", daemon=True).start()
        _remote.update(srv=srv, error="")
        log.info("phone link on, port %d", REMOTE_PORT)
    elif not on and srv is not None:
        _remote["srv"] = None
        srv.shutdown()
        srv.server_close()
        log.info("phone link off")


def close_thread_db(store):
    """Close the calling thread's SQLite connection. Store keeps one per thread, and a finished request thread's
    connection sits in a reference cycle, so without this it stays open (page cache and all) until the cyclic GC."""
    loc = getattr(store, "_local", None)
    c = getattr(loc, "c", None)
    if c is not None:
        loc.c = None
        c.close()


def make_handler(tracker, port, remote=False):
    store = tracker.store
    folder = data_dir()             # fixed for the life of the process; data_dir() does a makedirs per call
    allowed = {"127.0.0.1:%d" % port, "localhost:%d" % port}

    class H(BaseHTTPRequestHandler):
        server_version = "gkeystrokes"
        timeout = 20    # s: a client that goes silent (or never sends) can't hold its thread forever

        def log_message(self, fmt, *args):
            pass

        def finish(self):
            try:
                super().finish()
            finally:
                close_thread_db(store)

        def _head(self, code, ctype, length=None, extra=None):
            """Status line and headers. With length None there is no Content-Length; the body ends at the close."""
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            if getattr(self, "_cookie", None):
                self.send_header("Set-Cookie", self._cookie)
                self._cookie = None
            self.end_headers()

        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, separators=(",", ":"))
            if isinstance(body, str):
                body = body.encode("utf-8")
            self._head(code, ctype, len(body), extra)
            self.wfile.write(body)

        def _host_ok(self):
            if remote:
                return self._key_ok()
            if self.headers.get("Host", "") not in allowed:
                self._send(403, {"error": "forbidden host"})
                return False
            return True

        def _key_ok(self):
            key = remote_cfg(store)["key"]
            q = parse_qs(urlparse(self.path).query)
            given = q.get("k", [""])[0]
            if given and hmac.compare_digest(given.encode(), key.encode()):
                self._cookie = "gk_k=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict" % key
                return True
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, val = part.strip().partition("=")
                if name == "gk_k" and hmac.compare_digest(val.encode(), key.encode()):
                    return True
            self._send(403, "This link needs its key. Open the full link from gkeystrokes Settings > Phone link.",
                       "text/plain; charset=utf-8")
            return False

        def _range(self, q):
            now = time.time()
            t0 = float(q.get("from", [now - 86400])[0])
            t1 = float(q.get("to", [now])[0])
            if t1 > now - 60:
                tracker.flush_if_stale()
            return t0, t1

        @staticmethod
        def _mode(q):
            """(mode, game exes) filter for the stats queries."""
            mode = q.get("mode", ["all"])[0]
            if mode not in ("normal", "gaming"):
                return {"mode": "all", "games": ()}
            return {"mode": mode, "games": tracker.games.game_exes()}

        def _games(self):
            rules = tracker.games
            rows = store.conn().execute(
                "SELECT a.exe, a.name, a.path, COALESCE(SUM(s.active_s), 0) FROM apps a "
                "LEFT JOIN stats s ON s.app = a.exe GROUP BY a.exe ORDER BY 4 DESC").fetchall()
            out = []
            for exe, name, path, active in rows:
                is_game, why = rules.classify(exe, path or "")
                if exe in ("idle", "system", "unknown"):
                    continue
                out.append({"exe": exe, "name": name, "path": path, "active_s": active, "game": is_game, "why": why})
            return {"auto": rules.auto, "apps": out}

        def do_GET(self):
            if not self._host_ok():
                return
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path.startswith("/api/"):
                    return self._api(u.path[5:], q)
                return self._static(u.path)
            except Exception as e:
                log.exception("GET %s", self.path)
                self._send(500, {"error": str(e)})

        def do_POST(self):
            if remote:
                return self._send(403, {"error": "the phone link is view-only"})
            if not self._host_ok():
                return
            if self.headers.get("X-GK") != "1":
                return self._send(403, {"error": "missing X-GK header"})
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                if u.path == "/api/games":
                    if "auto" in body:
                        tracker.games.set_auto(bool(body["auto"]))
                    if body.get("exe"):
                        tracker.games.set_game(str(body["exe"]), bool(body.get("game")))
                    tracker.games_changed()
                    return self._send(200, self._games())
                if u.path == "/api/settings":
                    if "paused" in body:
                        tracker.set_paused(bool(body["paused"]))
                    if "autostart" in body and not PREVIEW:      # a preview never touches the real startup entry
                        winapi.set_autostart(bool(body["autostart"]))
                    if isinstance(body.get("power"), dict):
                        tracker.set_power_cfg(body["power"])
                    if "lockdown" in body:
                        privacy.lockdown(store, bool(body["lockdown"]))
                        set_remote(tracker, remote_cfg(store)["enabled"])
                    if isinstance(body.get("remote"), dict):
                        set_remote(tracker, remote_cfg(store, body["remote"])["enabled"])
                    if isinstance(body.get("mini"), dict):
                        mini_cfg(store, body["mini"])
                    if isinstance(body.get("dash"), dict):
                        dash_cfg(store, body["dash"])
                    lay = body.get("layout")
                    if isinstance(lay, dict) and lay.get("name") in LAYOUTS:
                        store.set_meta("layout_" + lay["name"], json.dumps(clean_layout(lay.get("items"))))
                    return self._send(200, self._settings())
                self._send(404, {"error": "not found"})
            except Exception as e:
                log.exception("POST %s", self.path)
                self._send(500, {"error": str(e)})

        def _static(self, path):
            if path in ("", "/"):
                path = "/index.html"
            elif path == "/mini":
                path = "/mini.html"
            full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
            if not full.startswith(WEB + os.sep):
                return self._send(404, {"error": "not found"})
            try:
                body = static_bytes(full)
            except OSError:                 # missing, or a folder
                return self._send(404, {"error": "not found"})
            self._send(200, body, TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

        def _settings(self):
            db = store.path
            size = sum(os.path.getsize(p) for p in (db, db + "-wal") if os.path.exists(p))
            d = {"paused": tracker.paused, "autostart": winapi.get_autostart(), "autostart_fixed": PREVIEW,
                 "data_dir": folder, "data_dir_pinned": store.pinned_dir,
                 "data_dir_moved": store.dir_moved, "db_bytes": size, "version": __version__,
                 "power": dict(tracker.power_cfg),
                 "layouts": {n: json.loads(store.get_meta("layout_" + n, "[]")) for n in LAYOUTS},
                 "mini": mini_cfg(store), "dash": dash_cfg(store), "backups": store.backups(),
                 "monitors": len(winapi.monitors())}
            if remote:
                d.update(data_dir="", data_dir_pinned="", data_dir_moved=False, view_only=True)
            else:
                rc = remote_cfg(store)
                d["remote"] = {"enabled": rc["enabled"], "on": _remote["srv"] is not None, "error": _remote["error"],
                               "port": REMOTE_PORT, "urls": remote_urls(rc["key"])}
                d["lockdown"] = privacy.lockdown(store)
            return d

        def _privacy(self):
            return privacy.report(port, os.path.join(folder, "edge-profile"), REMOTE_PORT,
                                  _remote["srv"] is not None, privacy.lockdown(store))

        def _api(self, name, q):
            if name == "live":
                return self._send(200, tracker.live())
            if name == "settings":
                return self._send(200, self._settings())
            if name == "privacy" and not remote:
                return self._send(200, self._privacy())
            if name == "summary":
                t0, t1 = self._range(q)
                bucket = q.get("bucket", ["day"])[0]
                f = self._mode(q)
                return self._send(200, {"totals": store.totals(t0, t1, **f),
                                        "series": store.series(t0, t1, bucket, **f),
                                        "punch": store.punchcard(t0, t1, **f)})
            if name == "series":
                t0, t1 = self._range(q)
                metrics = [m.strip() for m in ",".join(q.get("metrics", [])).split(",") if m.strip()]
                try:
                    return self._send(200, store.metric_series(t0, t1, metrics, q.get("bucket", ["hour"])[0],
                                                               **self._mode(q)))
                except TooManyPoints as e:
                    return self._send(400, {"error": str(e)})
            if name == "keys":
                t0, t1 = self._range(q)
                f = self._mode(q)
                return self._send(200, {"keys": store.keys(t0, t1, **f), "combos": store.combos(t0, t1, **f)})
            if name == "apps":
                t0, t1 = self._range(q)
                apps = store.apps(t0, t1, **self._mode(q))
                rules = tracker.games
                paths = dict(store.conn().execute("SELECT exe, path FROM apps").fetchall())
                for a in apps:
                    a["game"] = rules.classify(a["exe"], paths.get(a["exe"]) or "")[0]
                return self._send(200, {"apps": apps})
            if name == "games":
                return self._send(200, self._games())
            if name == "gaming":
                t0, t1 = self._range(q)
                bucket = q.get("bucket", ["day"])[0]
                f = {"mode": "gaming", "games": tracker.games.game_exes()}
                return self._send(200, {"totals": store.totals(t0, t1, **f),
                                        "series": store.series(t0, t1, bucket, **f),
                                        "per_game": store.apps(t0, t1, **f),
                                        "session_games": store.session_games(t0, t1),
                                        "sessions": store.sessions(t0, t1, 100),
                                        "game_count": len(f["games"])})
            if name == "system":
                t0, t1 = self._range(q)
                d = store.system(t0, t1, q.get("bucket", ["day"])[0])
                d["cores"] = tracker.sys_sampler.cores if getattr(tracker, "sys_sampler", None) else 1
                return self._send(200, d)
            if name == "top_at":
                hours = [h for h in ",".join(q.get("hours", [])).split(",") if h.strip().lstrip("-").isdigit()]
                return self._send(200, {str(h): v for h, v in store.top_apps_at(hours).items()})
            if name == "power":
                t0, t1 = self._range(q)
                d = store.power(t0, t1, q.get("bucket", ["day"])[0])
                d["cfg"] = dict(tracker.power_cfg)
                return self._send(200, d)
            if name == "mouse":
                t0, t1 = self._range(q)
                vx, vy, vw, vh = winapi.virtual_screen()
                return self._send(200, {
                    "grid": {"w": GRID_W, "h": GRID_H, "cells": store.clicks(t0, t1, **self._mode(q))},
                    "screen": {"x": vx, "y": vy, "w": vw, "h": vh, "monitors": winapi.monitors()}})
            if name == "net":
                t0, t1 = self._range(q)
                d = {"ifaces": store.net_ifaces(t0, t1)}
                if "bucket" in q:
                    d["series"] = store.net_series(t0, t1, q["bucket"][0])
                return self._send(200, d)
            if name == "calendar":
                days = int(q.get("days", ["371"])[0])
                tracker.flush_if_stale()
                return self._send(200, {"days": store.calendar(time.time() - days * 86400)})
            if name == "records":
                tracker.flush_if_stale()
                return self._send(200, store.records(local_day(time.time())))
            if name == "export.csv":
                tracker.flush()
                rows = store.daily_rows()
                cols = ["day", "keys", "clicks", "active_s", "typed", "mouse_mm", "scroll", "down", "up",
                        "uptime_s", "peak_wpm", "peak_kpm", "peak_apm", "peak_down", "peak_up",
                        "wh", "gpu_wh", "peak_watts", "peak_gpu_temp"]
                # Streamed in ~64 KB pieces rather than built as one string. No Content-Length: this server
                # answers HTTP/1.0 and closes after the body, so the download ends at the close.
                self._head(200, "text/csv; charset=utf-8", None,
                           {"Content-Disposition": 'attachment; filename="gkeystrokes-daily.csv"'})
                buf = io.StringIO()
                w = csv.writer(buf, lineterminator="\n")
                w.writerow(cols)
                for r in rows:
                    w.writerow([r.get(c, 0) if not isinstance(r.get(c), float) else round(r[c], 2) for c in cols])
                    if buf.tell() >= 65536:
                        self.wfile.write(buf.getvalue().encode("utf-8"))
                        buf.seek(0)
                        buf.truncate()
                self.wfile.write(buf.getvalue().encode("utf-8"))
                return None
            self._send(404, {"error": "unknown endpoint"})

    return H


def start_server(tracker, port=8413, tries=10):
    """Start on the first free port from `port` upward. Returns (server, port)."""
    last = None
    for p in range(port, port + tries):
        try:
            srv = LocalHTTPServer(("127.0.0.1", p), make_handler(tracker, p))
        except OSError as e:
            last = e
            continue
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()
        return srv, p
    raise last
