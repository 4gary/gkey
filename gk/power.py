"""GPU and power sampling.

Measured when the hardware allows it:
  * GPU load / temperature / VRAM / board power / energy  -> NVIDIA NVML (nvml.dll ships with the driver)
  * per-process GPU use and VRAM                          -> Windows "GPU Engine" / "GPU Process Memory"
                                                             performance counters (what Task Manager shows)
  * CPU package power                                     -> Windows "Energy Meter" counters (Intel/AMD RAPL)
Estimated (no sensor exists): the rest of the PC (board, RAM, drives, fans), power-supply losses, and
monitors. Those come from the user's settings.
"""
import ctypes
import logging
import re
import time
from ctypes import wintypes

import psutil

from . import winapi

log = logging.getLogger("gk.power")

DEFAULTS = {"price": 0.20, "currency": "$", "other_w": 50.0, "display_w": 75.0, "psu_eff": 0.90, "cpu_tdp": 125.0}
DISPLAY_ON_S = 600          # monitors assumed on while there was input in the last 10 minutes


# ---------------------------------------------------------------- PDH (performance counters)

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2


class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _FmtValue)]


class Pdh:
    def __init__(self):
        d = self.dll = ctypes.WinDLL("pdh")
        for fn in ("PdhOpenQueryW", "PdhAddEnglishCounterW", "PdhCollectQueryData",
                   "PdhGetFormattedCounterArrayW", "PdhCloseQuery"):
            getattr(d, fn).restype = ctypes.c_uint32
        d.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        d.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t,
                                            ctypes.POINTER(wintypes.HANDLE)]
        d.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        d.PdhGetFormattedCounterArrayW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                   ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.q = wintypes.HANDLE()
        if d.PdhOpenQueryW(None, 0, ctypes.byref(self.q)):
            raise OSError("PdhOpenQuery failed")
        self.counters = {}
        self._bufs = {}                     # counter key -> reusable buffer for formatted reads

    def add(self, key, path):
        h = wintypes.HANDLE()
        if self.dll.PdhAddEnglishCounterW(self.q, path, 0, ctypes.byref(h)) == 0:
            self.counters[key] = h
            return True
        return False

    def collect(self):
        return self.dll.PdhCollectQueryData(self.q) == 0

    def _array(self, key):
        """(buffer, item count) of a formatted read, or (None, 0).

        Each counter keeps one buffer and reuses it, so a read is normally a single PDH call with no allocation.
        The buffer is replaced only when PDH says it is too small. Names inside it are copied out by the callers."""
        h = self.counters.get(key)
        if h is None:
            return None, 0
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        count = wintypes.DWORD(0)
        buf = self._bufs.get(key)
        if buf is not None:
            size = wintypes.DWORD(ctypes.sizeof(buf))
            rc = self.dll.PdhGetFormattedCounterArrayW(h, fmt, ctypes.byref(size), ctypes.byref(count), buf)
            if rc == 0:
                return buf, count.value
            if rc != PDH_MORE_DATA:
                return None, 0
        size = wintypes.DWORD(0)
        if self.dll.PdhGetFormattedCounterArrayW(h, fmt, ctypes.byref(size), ctypes.byref(count), None) != PDH_MORE_DATA:
            return None, 0
        buf = self._bufs[key] = (ctypes.c_byte * size.value)()
        if self.dll.PdhGetFormattedCounterArrayW(h, fmt, ctypes.byref(size), ctypes.byref(count), buf):
            return None, 0
        return buf, count.value

    def items(self, key):
        """[(instance name, value), ...] keeping duplicate instance names."""
        buf, n = self._array(key)
        if buf is None:
            return []
        it = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        return [(it[i].szName, it[i].FmtValue.doubleValue) for i in range(n) if it[i].FmtValue.CStatus in (0, 1)]

    def values(self, key):
        """{instance name: value} for a (wildcard) counter."""
        buf, n = self._array(key)
        if buf is None:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        return {items[i].szName: items[i].FmtValue.doubleValue
                for i in range(n) if items[i].FmtValue.CStatus in (0, 1)}


# ---------------------------------------------------------------- NVML (NVIDIA)

class _NvUtil(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _NvMem(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


THROTTLE = ((0x1, "idle"), (0x2, "app clocks"), (0x4, "power limit"), (0x8, "hardware slowdown"),
            (0x20, "software thermal"), (0x40, "hardware thermal"), (0x80, "power brake"), (0x100, "display clock"))


class Nvml:
    PATHS = (r"C:\Windows\System32\nvml.dll", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll", "nvml.dll")

    def __init__(self):
        self.dll = None
        for p in self.PATHS:
            try:
                self.dll = ctypes.CDLL(p)
                break
            except OSError:
                continue
        if self.dll is None or self.dll.nvmlInit_v2() != 0:
            raise OSError("NVML not available")
        n = ctypes.c_uint()
        self.dll.nvmlDeviceGetCount_v2(ctypes.byref(n))
        best, best_limit = None, -1
        for i in range(n.value):                    # pick the most powerful NVIDIA card
            h = ctypes.c_void_p()
            if self.dll.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(h)) == 0:
                lim = ctypes.c_uint()
                self.dll.nvmlDeviceGetEnforcedPowerLimit(h, ctypes.byref(lim))
                if lim.value > best_limit:
                    best, best_limit = h, lim.value
        if best is None:
            raise OSError("no NVIDIA GPU")
        self.h = best
        self.limit_w = best_limit / 1000.0
        buf = ctypes.create_string_buffer(96)
        self.dll.nvmlDeviceGetName(self.h, buf, 96)
        self.name = buf.value.decode(errors="replace")
        e = ctypes.c_ulonglong()
        self.has_energy = self.dll.nvmlDeviceGetTotalEnergyConsumption(self.h, ctypes.byref(e)) == 0
        self.static = self._static()

    def _uint(self, fn, *args):
        v = ctypes.c_uint()
        return v.value if getattr(self.dll, fn)(self.h, *args, ctypes.byref(v)) == 0 else None

    def _static(self):
        """Things that don't change while running: max clocks, link width, driver, limits."""
        st = {"max_clock_mhz": self._uint("nvmlDeviceGetMaxClockInfo", 0),
              "max_mem_clock_mhz": self._uint("nvmlDeviceGetMaxClockInfo", 2),
              "pcie_gen_max": self._uint("nvmlDeviceGetMaxPcieLinkGeneration"),
              "pcie_width_max": self._uint("nvmlDeviceGetMaxPcieLinkWidth"),
              "slowdown_temp": self._uint("nvmlDeviceGetTemperatureThreshold", 1),
              "default_limit_w": (self._uint("nvmlDeviceGetPowerManagementDefaultLimit") or 0) / 1000.0 or None}
        for key, fn in (("driver", "nvmlSystemGetDriverVersion"), ("vbios", "nvmlDeviceGetVbiosVersion")):
            buf = ctypes.create_string_buffer(96)
            ok = (getattr(self.dll, fn)(buf, 96) if key == "driver" else getattr(self.dll, fn)(self.h, buf, 96)) == 0
            st[key] = buf.value.decode(errors="replace") if ok else None
        return st

    def read(self):
        d, h = self.dll, self.h
        u, m = _NvUtil(), _NvMem()
        t, p, f, c, e = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_ulonglong()
        out = {"name": self.name, "limit_w": self.limit_w}
        if d.nvmlDeviceGetUtilizationRates(h, ctypes.byref(u)) == 0:
            out["util"], out["mem_util"] = u.gpu, u.memory
        if d.nvmlDeviceGetTemperature(h, 0, ctypes.byref(t)) == 0:
            out["temp"] = t.value
        if d.nvmlDeviceGetPowerUsage(h, ctypes.byref(p)) == 0:
            out["power_w"] = p.value / 1000.0
        if d.nvmlDeviceGetMemoryInfo(h, ctypes.byref(m)) == 0:
            out["vram_mb"], out["vram_total_mb"] = m.used / 2**20, m.total / 2**20
        if d.nvmlDeviceGetFanSpeed(h, ctypes.byref(f)) == 0:
            out["fan"] = f.value
        if d.nvmlDeviceGetClockInfo(h, 0, ctypes.byref(c)) == 0:
            out["clock_mhz"] = c.value
        if self.has_energy and d.nvmlDeviceGetTotalEnergyConsumption(h, ctypes.byref(e)) == 0:
            out["energy_mj"] = e.value
        out["mem_clock_mhz"] = self._uint("nvmlDeviceGetClockInfo", 2)
        out["sm_clock_mhz"] = self._uint("nvmlDeviceGetClockInfo", 1)
        out["pcie_gen"] = self._uint("nvmlDeviceGetCurrPcieLinkGeneration")
        out["pcie_width"] = self._uint("nvmlDeviceGetCurrPcieLinkWidth")
        tx, rx = self._uint("nvmlDeviceGetPcieThroughput", 0), self._uint("nvmlDeviceGetPcieThroughput", 1)
        out["pcie_tx_mbs"] = tx / 1024.0 if tx is not None else None            # NVML reports KB/s
        out["pcie_rx_mbs"] = rx / 1024.0 if rx is not None else None
        for key, fn in (("enc", "nvmlDeviceGetEncoderUtilization"), ("dec", "nvmlDeviceGetDecoderUtilization")):
            v, period = ctypes.c_uint(), ctypes.c_uint()
            if d[fn](h, ctypes.byref(v), ctypes.byref(period)) == 0:
                out[key] = v.value
        ps = ctypes.c_int()
        if d.nvmlDeviceGetPerformanceState(h, ctypes.byref(ps)) == 0:
            out["pstate"] = "P%d" % ps.value
        tr = ctypes.c_ulonglong()
        if d.nvmlDeviceGetCurrentClocksThrottleReasons(h, ctypes.byref(tr)) == 0:
            out["throttle"] = [name for bit, name in THROTTLE if tr.value & bit]
        out.update(self.static)
        return out


# ---------------------------------------------------------------- sampler

ENGINE_RE = re.compile(r"pid_(\d+)_luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_(\d+)_eng_(\d+)_engtype_(.*)$", re.I)
PROCMEM_RE = re.compile(r"pid_(\d+)_luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+$", re.I)
ADAPTER_RE = re.compile(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+$", re.I)


class PowerSampler:
    def __init__(self, settings=None):
        self.settings = dict(DEFAULTS, **(settings or {}))
        self.nvml = self.pdh = None
        try:
            self.nvml = Nvml()
            log.info("GPU: %s (NVML, energy counter: %s)", self.nvml.name, self.nvml.has_energy)
        except Exception as e:
            log.info("NVML unavailable (%s); GPU watts will be estimated", e)
        try:
            self.pdh = Pdh()
            self.pdh.add("engine", r"\GPU Engine(*)\Utilization Percentage")
            self.pdh.add("procmem", r"\GPU Process Memory(*)\Dedicated Usage")
            self.pdh.add("adapter", r"\GPU Adapter Memory(*)\Dedicated Usage")
            self.cpu_measured = self.pdh.add("cpu_power", r"\Energy Meter(rapl_package0_pkg)\Power")
            self.pdh.add("cpu_util", r"\Processor Information(_Total)\% Processor Utility")   # Task Manager's CPU %
            self.pdh.collect()
        except Exception as e:
            log.info("performance counters unavailable: %s", e)
            self.pdh, self.cpu_measured = None, False
        psutil.cpu_percent(None)
        self.main_luid = None
        self._luid_at = 0
        self._last_energy = None
        self._names = {}
        self._names_at = self._pruned_at = time.time()

    def _name(self, pid):
        now = time.time()
        if now - self._names_at > 600:              # pids get reused: start over now and then
            self._names.clear()
            self._names_at = now
        if now - self._pruned_at > 60:              # drop programs that have exited
            self._pruned_at = now
            for p in [p for p in self._names if not psutil.pid_exists(p)]:
                del self._names[p]
        r = self._names.get(pid)
        if r is None:
            path = winapi.process_path(pid) or ""
            if path:
                exe = path.rsplit("\\", 1)[-1].lower()
                r = (exe, winapi.file_description(path) or exe.rsplit(".", 1)[0], path)
            else:
                r = ("system", "System", "") if pid == 4 else (None, None, None)
            self._names[pid] = r
        return r

    def sample(self, dt, last_input_age):
        s = self.settings
        out = {"gpu": None, "procs": {}}
        if self.pdh:
            self.pdh.collect()
        # --- GPU (NVML)
        gpu_w = None
        if self.nvml:
            try:
                g = self.nvml.read()
                out["gpu"] = g
                gpu_w = g.get("power_w")
                e = g.get("energy_mj")
                if e is not None:
                    if self._last_energy is not None and 0 <= e - self._last_energy < 3.6e6 * dt:
                        out["gpu_wh"] = (e - self._last_energy) / 3.6e6
                    self._last_energy = e
            except Exception:
                log.exception("NVML read failed")
        # --- per-process GPU (performance counters)
        engines = self.pdh.values("engine") if self.pdh else {}
        if self.pdh and (self.main_luid is None or time.time() - self._luid_at > 30):
            adapters = {}
            for inst, v in self.pdh.values("adapter").items():
                m = ADAPTER_RE.search(inst)
                if m:
                    luid = m.group(1).lower()           # counter sets disagree on hex case
                    adapters[luid] = adapters.get(luid, 0) + v
            if adapters:                             # the card holding the most VRAM is the main GPU
                self.main_luid = max(adapters, key=adapters.get)
            self._luid_at = time.time()
        per_pid, per_engine = {}, {}
        for inst, v in engines.items():
            m = ENGINE_RE.search(inst)
            if not m or (self.main_luid and m.group(2).lower() != self.main_luid):
                continue
            pid = int(m.group(1))
            per_pid[pid] = max(per_pid.get(pid, 0.0), v)          # Task Manager: busiest engine per process
            key = (m.group(3), m.group(4))
            per_engine[key] = per_engine.get(key, 0.0) + v
        vram = {}
        for inst, v in (self.pdh.values("procmem") if self.pdh else {}).items():
            m = PROCMEM_RE.search(inst)
            if m and (not self.main_luid or m.group(2).lower() == self.main_luid):
                vram[int(m.group(1))] = v / 2**20
        for pid in set(p for p, u in per_pid.items() if u >= 0.5) | set(p for p, mb in vram.items() if mb >= 64):
            exe, name, path = self._name(pid)
            if not exe:
                continue
            u, mb = per_pid.get(pid, 0.0), vram.get(pid, 0.0)
            prev = out["procs"].get(exe)
            out["procs"][exe] = (name, path, min(100.0, (prev[2] if prev else 0) + u), max(prev[3] if prev else 0, mb))
        util = out["gpu"].get("util") if out["gpu"] else None
        if util is None:
            util = min(100.0, max(per_engine.values(), default=0.0))
        out["util"] = util
        if gpu_w is None:                             # no NVML: rough estimate from load
            gpu_w = 15 + util / 100.0 * 200
        out["gpu_w"] = gpu_w
        out.setdefault("gpu_wh", gpu_w * dt / 3600)
        # --- CPU
        cpu_pct = psutil.cpu_percent(None)
        u = self.pdh.values("cpu_util") if self.pdh else {}
        if u:
            cpu_pct = min(100.0, list(u.values())[0])
        cpu_w = None
        if self.cpu_measured:
            v = self.pdh.values("cpu_power")
            if v:
                cpu_w = list(v.values())[0] / 1000.0          # counter is in milliwatts
        if cpu_w is None or not (0 < cpu_w < 1000):
            cpu_w = 8 + cpu_pct / 100.0 * (s["cpu_tdp"] - 8)
        out["cpu_pct"], out["cpu_w"] = cpu_pct, cpu_w
        # --- the rest (estimates) and wall power
        other_w = s["other_w"]
        eff = min(1.0, max(0.5, s["psu_eff"]))
        loss_w = (gpu_w + cpu_w + other_w) / eff - (gpu_w + cpu_w + other_w)
        display_w = s["display_w"] if last_input_age < DISPLAY_ON_S else 0.0
        out["other_w"] = other_w + loss_w
        out["display_w"] = display_w
        out["total_w"] = gpu_w + cpu_w + other_w + loss_w + display_w
        out["cpu_wh"] = cpu_w * dt / 3600
        out["other_wh"] = (other_w + loss_w) * dt / 3600
        out["display_wh"] = display_w * dt / 3600
        return out
