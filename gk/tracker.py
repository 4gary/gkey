"""In-memory counters fed by input events and a 1-second ticker, flushed to SQLite.

Privacy: only counts are kept. A keypress becomes "+1 for key E in this app, this hour";
the order of keys is never stored anywhere.

Gaming mode: on while a recognized game (see games.py) is the foreground window.
Game input is kept out of typing speed and keys/minute peaks and feeds APM instead.
A session runs from the first time the game is focused until its process exits.
"""
import json
import logging
import math
import threading
import time
from collections import Counter, defaultdict, deque

import psutil

from . import winapi
from .games import GameRules
from .power import DEFAULTS as POWER_DEFAULTS, PowerSampler
from .system import SystemSampler
from .keymap import MODIFIERS, PRINTABLE, check_vk, combo_label, key_id
from .store import GRID_H, GRID_W, PEAK_COLS, STAT_COLS, local_day

log = logging.getLogger("gk.tracker")

IDX = {c: i for i, c in enumerate(STAT_COLS)}
K, LC, RC, MC, XC, SC, MM, ACT, TY, TMS = (IDX[c] for c in STAT_COLS)
BUTTON_COL = {"left": LC, "right": RC, "middle": MC, "x1": XC, "x2": XC}

TOTAL_FIELDS = STAT_COLS + ("up", "down", "uptime_s", "backspaces")
PEAK_FIELDS = tuple(PEAK_COLS)

IDLE_S = 60            # no input for this long = idle (not counted as active time)
TYPING_GAP_S = 2.0     # gaps shorter than this between characters count as typing time
FLUSH_EVERY = 5        # seconds
POWER_EVERY = 2        # seconds between GPU/power samples
ENERGY_FIELDS = ("gpu_wh", "cpu_wh", "other_wh", "display_wh")

SKIP_IFACE = ("loopback", "pseudo", "vethernet", "virtualbox", "vmware", "hyper-v", "wsl",
              "docker", "isatap", "teredo", "local area connection*", "bluetooth", "npcap",
              "tailscale", "wireguard", "openvpn", "tap-windows", "nordlynx", "proton", "zerotier")


def skip_iface(name):
    n = name.lower()
    return any(s in n for s in SKIP_IFACE)


class Session:
    """One run of a game, from first focus until its process exits."""

    def __init__(self, sid, exe, name, pid, start):
        self.id, self.exe, self.name, self.pid, self.start = sid, exe, name, pid, start
        self.end = start
        self.keys = self.clicks = self.active_s = 0
        self.peak_apm = 0
        self.wh = 0.0                   # whole-PC energy while the game was open
        self.actions = deque()          # timestamps of in-game keys+clicks, last 60 s

    def apm(self, now):
        while self.actions and self.actions[0] < now - 60:
            self.actions.popleft()
        return len(self.actions)

    def row(self):
        return (self.end, self.keys, self.clicks, self.active_s, self.peak_apm, round(self.wh, 3), self.id)


class Tracker:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.resolver = winapi.AppResolver()
        self.games = GameRules(store)
        self.paused = store.get_meta("paused", "0") == "1"
        self.app = ("idle", "Desktop", "")
        self.gaming = False                 # a game is the foreground window right now
        self.session = None
        self.seen_apps = set()
        self.held = {}                      # key id -> vk to verify it's still down
        self.typed_times = deque()          # timestamps only, for live WPM
        self.key_times = deque()
        self.last_input = 0.0
        self.last_typed = 0.0
        self.wpm_now = 0.0
        self.kpm_now = 0
        self._mouse_mm = 0.0
        self.net_last = {}
        self.net_hist = deque(maxlen=120)   # (t, up B/s, down B/s)
        self.rates = (0.0, 0.0)
        self.vscreen = winapi.virtual_screen()
        self.boot_time = psutil.boot_time()
        self._stop = threading.Event()
        self._last_tick = time.time()
        self._ticks = 0
        self._last_flush = 0.0
        self.power_cfg = dict(POWER_DEFAULTS, **json.loads(store.get_meta("power", "{}")))
        self.power_now = None
        self.power_hist = deque(maxlen=60)  # (t, gpu %, total W, gpu W) every 2 s = 2 minutes
        self._power_t = time.time()
        try:
            self.sampler = PowerSampler(self.power_cfg)
        except Exception:
            log.exception("power sampling disabled")
            self.sampler = None
        try:
            self.sys_sampler = SystemSampler()
        except Exception:
            log.exception("system sampling disabled")
            self.sys_sampler = None
        self.sys_now = None
        self.sys_hist = deque(maxlen=60)    # (t, cpu %, RAM %, read B/s, write B/s, gpu %, VRAM MB)
        self._reset_pending()
        self._load_totals()

    # ------------------------------------------------------------ state

    def _reset_pending(self):
        self.p_stats = defaultdict(lambda: [0] * len(STAT_COLS))
        self.p_keys = Counter()
        self.p_net = defaultdict(lambda: [0, 0])
        self.p_uptime = Counter()
        self.p_clicks = Counter()
        self.p_combos = Counter()
        self.p_daily = {}
        self.p_apps = {}
        self.p_sessions = {}
        self.p_energy = defaultdict(lambda: [0.0] * 5)        # hour -> secs, gpu, cpu, other, display Wh
        self.p_gpu = defaultdict(lambda: [0.0] * 7)           # hour -> secs, util_sum, util_max, temp_sum, temp_max, W max, VRAM max
        self.p_gpu_apps = defaultdict(lambda: [0.0] * 4)      # (hour, exe) -> secs, gpu_s, Wh, VRAM max
        self.p_metrics = defaultdict(lambda: [0.0, 0.0, 0.0])   # (hour, key) -> value*secs, max, secs
        self.p_proc = defaultdict(lambda: [0.0] * 6)          # (hour, exe) -> secs, cpu_s, RAM MB*secs, RAM max, read, write

    def _load_totals(self):
        now = time.time()
        self.day = local_day(now)
        midnight = time.mktime(time.strptime(self.day, "%Y-%m-%d"))
        self.today = self.store.totals(midnight, now + 3600)
        self.alltime = self.store.totals()
        g = self.store.totals(midnight, now + 3600, "gaming", self.games.game_exes())
        self.today_game = {"keys": g["keys"], "clicks": g["lclicks"] + g["rclicks"] + g["mclicks"] + g["xclicks"],
                           "active_s": g["active_s"]}
        pt = self.store.power(midnight, now + 3600)["totals"]
        self.today_energy = {f: pt[f] for f in ENERGY_FIELDS}
        self.first_day = self.store.get_meta("first_day")
        if not self.first_day:
            self.first_day = self.day
            self.store.set_meta("first_day", self.day)

    def _bump(self, field, v):
        self.today[field] += v
        self.alltime[field] += v

    def _peak(self, field, v):
        if v > self.today[field]:
            self.today[field] = v
            row = self.p_daily.setdefault(self.day, dict.fromkeys(PEAK_FIELDS, 0))
            row[field] = max(row[field], v)
        if v > self.alltime[field]:
            self.alltime[field] = v

    def _stat(self, now):
        return self.p_stats[(int(now // 3600), self.app[0])]

    def _game_action(self, now, kind):
        s = self.session
        if self.gaming and s and s.exe == self.app[0]:
            setattr(s, kind, getattr(s, kind) + 1)
            s.actions.append(now)
        if self.gaming:
            self.today_game[kind] += 1

    # ------------------------------------------------------------ input callbacks (raw-input thread)

    def on_key(self, vk, scancode, e0, down):
        kid = key_id(vk, scancode, e0)
        if kid is None:
            return
        now = time.time()
        with self.lock:
            if not down:
                self.held.pop(kid, None)
                return
            if kid in self.held:            # auto-repeat while held
                return
            self.held[kid] = check_vk(kid, vk)
            self.last_input = now
            if self.paused:
                return
            app = self.app[0]
            st = self._stat(now)
            st[K] += 1
            self.p_keys[(int(now // 3600), app, kid)] += 1
            self._bump("keys", 1)
            if kid == "Backspace":
                self._bump("backspaces", 1)
            if self.gaming:                 # WASD spam isn't typing: keep it out of WPM / keys-per-minute
                self._game_action(now, "keys")
            else:
                self.key_times.append(now)
                if kid in PRINTABLE:
                    gap = now - self.last_typed
                    if gap < TYPING_GAP_S:
                        st[TMS] += gap * 1000
                        self._bump("typing_ms", gap * 1000)
                    self.last_typed = now
                    st[TY] += 1
                    self._bump("typed", 1)
                    self.typed_times.append(now)
            if kid not in MODIFIERS:
                combo = combo_label(self.held, kid)
                if combo:
                    self.p_combos[(self.day, app, combo)] += 1

    def on_mouse(self, flags, data):
        now = time.time()
        pos = winapi.cursor_pos()
        with self.lock:
            self.last_input = now
            if self.paused:
                return
            st = self._stat(now)
            for bit, name in winapi.MOUSE_DOWN:
                if flags & bit:
                    st[BUTTON_COL[name]] += 1
                    self._bump(STAT_COLS[BUTTON_COL[name]], 1)
                    self._game_action(now, "clicks")
                    if pos:
                        vx, vy, vw, vh = self.vscreen
                        gx = min(GRID_W - 1, max(0, int((pos[0] - vx) * GRID_W / vw)))
                        gy = min(GRID_H - 1, max(0, int((pos[1] - vy) * GRID_H / vh)))
                        self.p_clicks[(self.day, self.app[0], gx, gy)] += 1
            if flags & (winapi.RI_MOUSE_WHEEL | winapi.RI_MOUSE_HWHEEL):
                ticks = abs(data) / 120.0
                st[SC] += ticks
                self._bump("scroll", ticks)

    # ------------------------------------------------------------ background loops

    def _cursor_loop(self):
        """Mouse distance: poll the cursor at 40 Hz and convert pixels to mm."""
        last = winapi.cursor_pos()
        while not self._stop.wait(0.025):
            p = winapi.cursor_pos()
            if p and last and p != last:
                d = math.hypot(p[0] - last[0], p[1] - last[1])
                if d < 4000:                # ignore teleports (remote desktop, display changes)
                    mm = d / winapi.monitor_dpi_at(*p) * 25.4
                    with self.lock:
                        self._mouse_mm += mm
                        self.last_input = time.time()
            last = p

    def _tick_loop(self):
        while not self._stop.wait(1.0):
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")

    def tick(self):
        now = time.time()
        dt = now - self._last_tick
        self._last_tick = now
        awake = dt < 5                      # a big gap means the PC was asleep
        day = local_day(now)
        if day != self.day:
            self.flush()
            self.backup(day)
            with self.lock:
                self.day = day
                self.today = {f: 0 for f in TOTAL_FIELDS + PEAK_FIELDS}
                self.today_game = {"keys": 0, "clicks": 0, "active_s": 0}
                self.today_energy = dict.fromkeys(ENERGY_FIELDS, 0.0)
        try:
            app = self.resolver.current()
            pid = self.resolver.pid
        except Exception:
            app, pid = ("unknown", "Unknown", ""), None
        gaming = (not self.paused) and self.games.is_game(app[0], app[2])
        self._update_session(now, app, pid, gaming)
        with self.lock:
            self.app = app
            self.gaming = gaming
            if app[0] not in self.seen_apps:
                self.seen_apps.add(app[0])
                self.p_apps[app[0]] = (app[1], app[2])
            for kid, vk in list(self.held.items()):   # recover from missed key-ups
                if not winapi.key_is_down(vk):
                    del self.held[kid]
            mm, self._mouse_mm = self._mouse_mm, 0.0
            if not self.paused:
                st = self._stat(now)
                if awake:
                    self.p_uptime[int(now // 3600)] += 1
                    self._bump("uptime_s", 1)
                    if now - self.last_input < IDLE_S:
                        st[ACT] += 1
                        self._bump("active_s", 1)
                        if gaming:
                            self.today_game["active_s"] += 1
                            if self.session and self.session.exe == app[0]:
                                self.session.active_s += 1
                st[MM] += mm
                self._bump("mouse_mm", mm)
            self._update_speed(now)
        self._sample_net(now, dt)
        if self._ticks % POWER_EVERY == 0:
            self._sample_power(now)
        self._ticks += 1
        if self._ticks % FLUSH_EVERY == 0:
            self.flush()
        if self._ticks % 60 == 0:
            self.vscreen = winapi.virtual_screen()

    def _update_session(self, now, app, pid, gaming):
        s = self.session
        if s and (not psutil.pid_exists(s.pid) or (gaming and app[0] != s.exe)):
            self._end_session(now)          # game closed, or switched to a different game
            s = None
        if gaming and s is None and pid:
            try:
                sid = self.store.start_session(app[0], now)
            except Exception:
                log.exception("could not start session")
                return
            with self.lock:
                self.session = Session(sid, app[0], app[1], pid, now)
            log.info("gaming session started: %s", app[1])
        if self.session:
            self.session.end = now

    def _end_session(self, now):
        with self.lock:
            s, self.session = self.session, None
            if s:
                s.end = now
                self.p_sessions[s.id] = s.row()
        if s:
            log.info("gaming session ended: %s (%ds, %d keys, %d clicks)", s.name, now - s.start, s.keys, s.clicks)

    def _update_speed(self, now):
        for dq in (self.typed_times, self.key_times):
            while dq and dq[0] < now - 60:
                dq.popleft()
        n30 = 0
        for t in reversed(self.typed_times):
            if t < now - 30:
                break
            n30 += 1
        self.wpm_now = n30 / 5.0 * 2.0      # chars in last 30 s -> words per minute
        self.kpm_now = len(self.key_times)
        if not self.paused:
            if n30 >= 40:                   # needs a real burst of typing to count as a peak
                self._peak("peak_wpm", round(self.wpm_now, 1))
            if self.kpm_now >= 30:
                self._peak("peak_kpm", self.kpm_now)
            s = self.session
            if s:
                apm = s.apm(now)
                if apm > s.peak_apm:
                    s.peak_apm = apm
                if apm >= 10:
                    self._peak("peak_apm", apm)

    def _sample_power(self, now):
        dt = now - self._power_t
        self._power_t = now
        if self.sampler is None:
            return
        try:
            s = self.sampler.sample(dt, now - self.last_input)
        except Exception:
            log.exception("power sample failed")
            return
        sy = None
        if self.sys_sampler:
            try:
                sy = self.sys_sampler.sample()
            except Exception:
                log.exception("system sample failed")
        g = s["gpu"] or {}
        top = sorted(s["procs"].values(), key=lambda v: -v[2])[:6]
        hour = int(now // 3600)
        with self.lock:
            self.power_now = {
                "gpu": {k: v for k, v in g.items() if k != "energy_mj"} if g else None,
                "util": s["util"], "gpu_w": s["gpu_w"], "cpu_w": s["cpu_w"], "cpu_pct": s["cpu_pct"],
                "other_w": s["other_w"], "display_w": s["display_w"], "total_w": s["total_w"],
                "gpu_measured": bool(g.get("power_w") is not None), "cpu_measured": self.sampler.cpu_measured,
                "top": [[name, round(u, 1), round(mb)] for name, _path, u, mb in top if u >= 0.5 or mb >= 256],
            }
            self.power_hist.append((round(now, 1), round(s["util"], 1), round(s["total_w"]), round(s["gpu_w"])))
            if sy:
                self.sys_now = {
                    "cpu_pct": s["cpu_pct"], "core_max": sy.get("core_max"), "cpu_mhz": sy.get("cpu_mhz"),
                    "cores": self.sys_sampler.cores, "ram_used_mb": sy["ram_used_mb"], "ram_total_mb": sy["ram_total_mb"],
                    "ram_pct": sy["ram_pct"], "swap_mb": sy["swap_mb"], "disk_read_bps": sy["disk_read_bps"],
                    "disk_write_bps": sy["disk_write_bps"], "disk_busy": sy["disk_busy"],
                    "disks": [[n, r, w, b] for n, (r, w, b) in sorted(sy["disks"].items())],
                    "space": [list(x) for x in sy["space"]], "top": self.sys_sampler.last_procs,
                }
                self.sys_hist.append((round(now, 1), round(s["cpu_pct"], 1), round(sy["ram_pct"], 1),
                                      round(sy["disk_read_bps"]), round(sy["disk_write_bps"]), round(s["util"], 1),
                                      round(g.get("vram_mb") or 0)))
            if self.paused or not (0 < dt < 10):     # paused, or the PC just woke from sleep
                return
            wh = [s[f] for f in ENERGY_FIELDS]
            e = self.p_energy[hour]
            e[0] += dt
            for i, v in enumerate(wh):
                e[i + 1] += v
            for f, v in zip(ENERGY_FIELDS, wh):
                self.today_energy[f] += v
            gp = self.p_gpu[hour]
            gp[0] += dt
            gp[1] += s["util"] * dt
            gp[2] = max(gp[2], s["util"])
            if g.get("temp") is not None:
                gp[3] += g["temp"] * dt
                gp[4] = max(gp[4], g["temp"])
            gp[5] = max(gp[5], s["gpu_w"])
            gp[6] = max(gp[6], g.get("vram_mb") or 0)
            for exe, (name, path, u, mb) in s["procs"].items():
                a = self.p_gpu_apps[(hour, exe)]
                if u >= 0.5:
                    a[0] += dt
                a[1] += u / 100.0 * dt
                a[2] += u / 100.0 * s["gpu_w"] * dt / 3600     # its share of GPU power (idle draw isn't billed to anyone)
                a[3] = max(a[3], mb)
                if exe not in self.seen_apps:
                    self.seen_apps.add(exe)
                    self.p_apps[exe] = (name, path)
            self._peak("peak_watts", round(s["total_w"]))
            self._peak("peak_gpu_w", round(s["gpu_w"]))
            if g.get("temp") is not None:
                self._peak("peak_gpu_temp", g["temp"])
            if self.session:
                self.session.wh += sum(wh)
            if sy:
                vals = {"cpu.util": s["cpu_pct"], "cpu.core_max": sy.get("core_max"), "cpu.mhz": sy.get("cpu_mhz"),
                        "ram.used_mb": sy["ram_used_mb"], "ram.pct": sy["ram_pct"], "swap.mb": sy["swap_mb"],
                        "disk.read": sy["disk_read_bps"], "disk.write": sy["disk_write_bps"], "disk.busy": sy["disk_busy"]}
                for name, (r, w, b) in sy["disks"].items():
                    vals["disk:%s:read" % name], vals["disk:%s:write" % name], vals["disk:%s:busy" % name] = r, w, b
                for key, gk in (("util", "util"), ("vram_mb", "vram_mb"), ("mem_util", "mem_util"), ("clock", "clock_mhz"),
                                ("mem_clock", "mem_clock_mhz"), ("temp", "temp"), ("fan", "fan"), ("power", "power_w"),
                                ("enc", "enc"), ("dec", "dec"), ("pcie_rx", "pcie_rx_mbs"), ("pcie_tx", "pcie_tx_mbs")):
                    vals["gpu." + key] = g.get(gk)
                for key, v in vals.items():
                    if v is not None:
                        m = self.p_metrics[(hour, key)]
                        m[0] += v * dt
                        m[1] = max(m[1], v)
                        m[2] += dt
                procs = sy.get("procs")
                if procs:
                    pdt = procs["dt"]
                    for exe, (cpu_s, ram, rd, wr, name, path) in procs["apps"].items():
                        pr = self.p_proc[(hour, exe)]
                        pr[0] += pdt
                        pr[1] += cpu_s
                        pr[2] += ram * pdt
                        pr[3] = max(pr[3], ram)
                        pr[4] += rd
                        pr[5] += wr
                        if exe not in self.seen_apps:
                            self.seen_apps.add(exe)
                            self.p_apps[exe] = (name, path)

    def set_power_cfg(self, cfg):
        clean = dict(self.power_cfg)
        for k, v in cfg.items():
            if k == "currency":
                clean[k] = str(v)[:4]
            elif k in POWER_DEFAULTS:
                clean[k] = float(v)
        clean["psu_eff"] = min(1.0, max(0.5, clean["psu_eff"]))
        self.store.set_meta("power", json.dumps(clean))
        self.power_cfg = clean
        if self.sampler:
            self.sampler.settings = dict(clean)
        return clean

    def _sample_net(self, now, dt):
        try:
            counters = psutil.net_io_counters(pernic=True)
        except Exception:
            return
        hour = int(now // 3600)
        tot_up = tot_down = 0
        with self.lock:
            for name, c in counters.items():
                if skip_iface(name):
                    continue
                prev = self.net_last.get(name)
                self.net_last[name] = (c.bytes_sent, c.bytes_recv)
                if prev is None:
                    continue
                up = max(0, c.bytes_sent - prev[0])
                down = max(0, c.bytes_recv - prev[1])
                if not (up or down):
                    continue
                tot_up += up
                tot_down += down
                if not self.paused:
                    row = self.p_net[(hour, name)]
                    row[0] += up
                    row[1] += down
                    self._bump("up", up)
                    self._bump("down", down)
            span = max(dt, 1.0)
            self.rates = (tot_up / span, tot_down / span)
            self.net_hist.append((round(now, 1), round(self.rates[0]), round(self.rates[1])))
            if not self.paused and dt < 5:
                self._peak("peak_up", round(self.rates[0]))
                self._peak("peak_down", round(self.rates[1]))

    # ------------------------------------------------------------ persistence

    def flush(self):
        with self.lock:
            stats, keys, net = self.p_stats, self.p_keys, self.p_net
            uptime, clicks, combos = self.p_uptime, self.p_clicks, self.p_combos
            daily, apps, sessions = self.p_daily, self.p_apps, self.p_sessions
            energy, gpu, gpu_apps = self.p_energy, self.p_gpu, self.p_gpu_apps
            metrics, proc = self.p_metrics, self.p_proc
            if self.session:
                sessions[self.session.id] = self.session.row()
            self._reset_pending()
            self._last_flush = time.time()
        batch = {
            "stats": [(h, a) + tuple(v) for (h, a), v in stats.items() if any(v)],
            "keys": [(h, a, k, n) for (h, a, k), n in keys.items()],
            "net": [(h, i, u, d) for (h, i), (u, d) in net.items()],
            "uptime": list(uptime.items()),
            "clicks": [(d, a, x, y, n) for (d, a, x, y), n in clicks.items()],
            "combos": [(d, a, c, n) for (d, a, c), n in combos.items()],
            "daily": [(d,) + tuple(r[f] for f in PEAK_FIELDS) for d, r in daily.items()],
            "apps": [(exe, name, path) for exe, (name, path) in apps.items()],
            "sessions": list(sessions.values()),
            "energy": [(h,) + tuple(v) for h, v in energy.items()],
            "gpu": [(h,) + tuple(v) for h, v in gpu.items()],
            "gpu_apps": [(h, exe) + tuple(v) for (h, exe), v in gpu_apps.items()],
            "metrics": [(h, k) + tuple(v) for (h, k), v in metrics.items()],
            "proc_usage": [(h, exe) + tuple(v) for (h, exe), v in proc.items()],
        }
        try:
            self.store.write(batch)
        except Exception:
            log.exception("flush failed; %d stat rows lost", len(batch["stats"]))

    def backup(self, day=None):
        try:
            path = self.store.backup(day or self.day)
            if path:
                log.info("daily backup: %s", path)
        except Exception:
            log.exception("daily backup failed")

    def flush_if_stale(self, max_age=1.0):
        if time.time() - self._last_flush > max_age:
            self.flush()

    # ------------------------------------------------------------ control

    def set_paused(self, paused):
        with self.lock:
            self.paused = bool(paused)
            self.typed_times.clear()
            self.key_times.clear()
        self.store.set_meta("paused", "1" if paused else "0")

    def games_changed(self):
        """Called after the game list changes: recompute today's gaming totals."""
        self.flush()
        self.games.reload()
        now = time.time()
        midnight = time.mktime(time.strptime(self.day, "%Y-%m-%d"))
        g = self.store.totals(midnight, now + 3600, "gaming", self.games.game_exes())
        with self.lock:
            self.today_game = {"keys": g["keys"], "clicks": g["lclicks"] + g["rclicks"] + g["mclicks"] + g["xclicks"],
                               "active_s": g["active_s"]}

    def live(self):
        now = time.time()
        with self.lock:
            s = self.session
            return {
                "paused": self.paused,
                "day": self.day,
                "first_day": self.first_day,
                "app": {"exe": self.app[0], "name": self.app[1]},
                "idle": now - self.last_input >= IDLE_S,
                "now": {"wpm": round(self.wpm_now, 1), "kpm": self.kpm_now,
                        "up": round(self.rates[0]), "down": round(self.rates[1])},
                "today": dict(self.today),
                "today_game": dict(self.today_game),
                "alltime": dict(self.alltime),
                "gaming": {"focused": self.gaming, "exe": s.exe, "name": s.name, "start": s.start,
                           "keys": s.keys, "clicks": s.clicks, "active_s": s.active_s,
                           "apm": s.apm(now), "peak_apm": s.peak_apm, "wh": s.wh} if s else None,
                "net_hist": list(self.net_hist),
                "power": self.power_now,
                "power_hist": list(self.power_hist),
                "system": self.sys_now,
                "sys_hist": list(self.sys_hist),
                "today_energy": dict(self.today_energy),
                "power_cfg": dict(self.power_cfg),
                "pc_uptime_s": int(now - self.boot_time),
            }

    def start(self):
        self.listener = winapi.RawInputListener(self.on_key, self.on_mouse)
        self.listener.start()
        self.listener.ready.wait(5)
        if self.listener.error:
            raise RuntimeError("Raw Input registration failed: %s" % self.listener.error)
        threading.Thread(target=self._cursor_loop, name="cursor", daemon=True).start()
        threading.Thread(target=self._tick_loop, name="ticker", daemon=True).start()
        threading.Thread(target=self.backup, name="backup", daemon=True).start()

    def stop(self):
        self._stop.set()
        try:
            self.listener.stop()
        except Exception:
            pass
        if self.session:
            self._end_session(time.time())
        self.flush()
