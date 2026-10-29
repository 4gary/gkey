"""CPU, memory, disk and per-program usage sampling.

Everything comes from Windows performance counters (the same ones Task Manager uses), which
also cover protected processes such as the WSL virtual machine (vmmemwsl).
"""
import logging
import re
import time

import psutil

from . import winapi
from .power import Pdh

log = logging.getLogger("gk.system")

PROC_EVERY = 5.0          # seconds between per-program scans
KEEP_CPU_S = 0.02         # per scan, keep programs that used at least this much CPU time,
KEEP_RAM_MB = 150         # or hold this much memory,
KEEP_IO = 1 << 20         # or read/wrote this many bytes
NAMES_MAX = 512           # friendly names cached per program (oldest dropped first)
PROC_COUNTERS = (("pcpu", "% Processor Time"), ("pram", "Working Set - Private"),
                 ("pread", "IO Read Bytes/sec"), ("pwrite", "IO Write Bytes/sec"), ("ppid", "ID Process"))


class SystemSampler:
    def __init__(self):
        self.pdh = None
        try:
            self.pdh = Pdh()
            self.pdh.add("dread", r"\PhysicalDisk(*)\Disk Read Bytes/sec")
            self.pdh.add("dwrite", r"\PhysicalDisk(*)\Disk Write Bytes/sec")
            self.pdh.add("didle", r"\PhysicalDisk(*)\% Idle Time")
            self.pdh.add("core", r"\Processor Information(*)\% Processor Utility")
            self.pdh.add("perf", r"\Processor Information(_Total)\% Processor Performance")
            self.pdh.collect()
        except Exception as e:
            log.info("disk/CPU counters unavailable: %s", e)
            self.pdh = None
        # Per-program counters get their own query. A collect over ~400 processes costs ~7 ms of CPU, so this
        # query is collected only when a scan runs (see _procs), not on every 2 s sample.
        self.proc = None
        if self.pdh:
            try:
                self.proc = Pdh()
                for key, ctr in PROC_COUNTERS:
                    self.proc.add(key, "\\Process(*)\\" + ctr)
                self.proc.collect()
            except Exception as e:
                log.info("per-program counters unavailable: %s", e)
        f = psutil.cpu_freq()
        self.base_mhz = (f.max or f.current) if f else 0
        self.cores = psutil.cpu_count() or 1
        self._proc_t = time.time()
        self._names = {}                # exe -> (friendly name, path)
        self.space = []
        self._space_t = 0
        self.last_procs = []

    def sample(self):
        out = {}
        if self.pdh:
            self.pdh.collect()
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        out["ram_used_mb"] = (vm.total - vm.available) / 2**20
        out["ram_total_mb"] = vm.total / 2**20
        out["ram_pct"] = vm.percent
        out["swap_mb"] = sw.used / 2**20
        disks = {}
        if self.pdh:
            r, w, idle = self.pdh.values("dread"), self.pdh.values("dwrite"), self.pdh.values("didle")
            for inst in r:
                if inst != "_Total":
                    disks[inst] = (r.get(inst, 0.0), w.get(inst, 0.0), min(100.0, max(0.0, 100 - idle.get(inst, 100.0))))
            cores = [v for k, v in self.pdh.values("core").items() if "_Total" not in k]
            out["core_max"] = min(100.0, max(cores, default=0.0))
            perf = self.pdh.values("perf")
            if perf and self.base_mhz:
                out["cpu_mhz"] = list(perf.values())[0] / 100.0 * self.base_mhz
        out["disks"] = disks
        out["disk_read_bps"] = sum(d[0] for d in disks.values())
        out["disk_write_bps"] = sum(d[1] for d in disks.values())
        out["disk_busy"] = max((d[2] for d in disks.values()), default=0.0)
        now = time.time()
        if now - self._space_t > 60:
            self._space_t = now
            self.space = []
            for part in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(part.mountpoint)
                    self.space.append((part.mountpoint, u.used, u.total))
                except OSError:
                    pass
        out["space"] = self.space
        out["procs"] = self._procs() if self.proc and now - self._proc_t >= PROC_EVERY else None
        return out

    def _name(self, exe, pid):
        r = self._names.get(exe)
        if r is None:
            if len(self._names) >= NAMES_MAX:               # FIFO: forget the oldest name
                del self._names[next(iter(self._names))]
            path = (winapi.process_path(pid) or "") if pid else ""
            r = self._names[exe] = ((winapi.file_description(path) if path else None) or exe.rsplit(".", 1)[0], path)
        return r

    def _procs(self):
        """{"dt": seconds since the last scan, "apps": {exe: [cpu_s, ram_mb, read_bytes, write_bytes, name, path]}}.
        The counters are rates, so they are collected here, once per scan: each value covers the whole scan window,
        and cpu_s = (% of one core) * dt is the CPU time used in that window."""
        self.proc.collect()
        now = time.time()
        dt = now - self._proc_t
        self._proc_t = now
        cols = [self.proc.items(k) for k in ("pcpu", "pram", "pread", "pwrite", "ppid")]
        n = min(len(c) for c in cols)
        agg = {}
        for i in range(n):
            name = cols[0][i][0]
            if any(c[i][0] != name for c in cols[1:]) or name in ("_Total", "Idle"):
                continue                       # arrays out of step, or not a real process
            exe = re.sub(r"#\d+$", "", name).lower() + ".exe"
            a = agg.get(exe)
            if a is None:
                a = agg[exe] = [0.0, 0.0, 0.0, 0.0, int(cols[4][i][1])]
            a[0] += cols[0][i][1] / 100.0 * dt       # % of one core -> CPU seconds
            a[1] += cols[1][i][1] / 2**20
            a[2] += cols[2][i][1] * dt
            a[3] += cols[3][i][1] * dt
        out = {}
        for exe, (c, ram, r, w, pid) in agg.items():
            if c >= KEEP_CPU_S or ram >= KEEP_RAM_MB or r + w >= KEEP_IO:
                name, path = self._name(exe, pid)
                out[exe] = [c, ram, r, w, name, path]
        self.last_procs = sorted(([v[4], v[0] / dt / self.cores * 100, v[1]] for v in out.values()),
                                 key=lambda x: -x[1])[:8]
        return {"dt": dt, "apps": out}
