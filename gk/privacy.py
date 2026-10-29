"""Privacy proof: nothing leaves this PC.

1. install_guard() adds a Python audit hook that blocks (and counts) every outgoing network attempt made by
   gkeystrokes' own code or any library it loads: connects and sends to anything but this computer, and every
   name lookup (DNS, LLMNR, NetBIOS). Audit hooks can't be removed once added, so nothing can switch it off.
2. report() lists every socket held by gkeystrokes, its mini window and its dashboard window, live, so the
   Settings > Privacy proof panel can show them (psutil reads the Windows connection tables; no traffic).
3. Lockdown mode keeps the phone link (the one server that listens beyond this PC) off.
"""
import collections
import ipaddress
import logging
import os
import sys
import threading
import time

log = logging.getLogger("gk.privacy")
STARTED = time.time()
LOCAL_NAMES = ("localhost", "localhost.")
_lock = threading.Lock()
_guard = {"on": False, "blocked": 0, "recent": collections.deque(maxlen=20)}


def _is_ip(host):
    try:
        ipaddress.ip_address(str(host).split("%")[0])
        return True
    except ValueError:
        return False


def is_local(host):
    """True for this computer: loopback addresses and the name localhost. Anything else is the outside."""
    if host is None or host == "":
        return False
    h = host.decode() if isinstance(host, bytes) else str(host)
    if h.lower() in LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(h.split("%")[0]).is_loopback
    except ValueError:
        return False


def _target(event, args):
    """(host, why) for a network event that would reach the outside, or None when it stays on this PC."""
    if event in ("socket.connect", "socket.sendto"):
        addr = args[1]
        if isinstance(addr, tuple) and addr:
            return None if is_local(addr[0]) else (addr[0], addr[1] if len(addr) > 1 else "")
        return None                                   # not an internet address (e.g. AF_UNIX)
    if event == "socket.getaddrinfo":                 # a literal IP needs no lookup; its connect is checked
        host = args[0]
        return None if host is None or is_local(host) or _is_ip(host) else (host, args[1])
    if event in ("socket.gethostbyname", "socket.gethostbyaddr"):
        host = args[0]
        return None if is_local(host) else (host, "")
    if event == "http.client.connect":
        return None if is_local(args[1]) else (args[1], args[2])
    if event == "urllib.Request":
        from urllib.parse import urlsplit
        host = urlsplit(str(args[0])).hostname
        return None if host is None or is_local(host) else (host, "")
    return None


def _hook(event, args):
    if event[:7] != "socket." and event[:5] != "http." and event != "urllib.Request":
        return                                        # every other audit event (file opens, imports...) passes
    hit = _target(event, args)
    if hit is None:
        return
    with _lock:
        _guard["blocked"] += 1
        _guard["recent"].appendleft({"t": time.time(), "what": event, "to": "%s %s" % hit})
    log.warning("blocked outgoing network attempt: %s %s", event, hit)
    raise PermissionError("gkeystrokes never connects outside this PC (blocked %s to %s)" % (event, hit[0]))


def install_guard():
    """Turn on the outgoing-network block for this process (once; it stays on until the process ends)."""
    with _lock:
        if _guard["on"]:
            return
        _guard["on"] = True
    sys.addaudithook(_hook)


def guard_state():
    with _lock:
        return {"on": _guard["on"], "blocked": _guard["blocked"], "recent": list(_guard["recent"])}


# ---------------------------------------------------------------- lockdown

def lockdown(store, value=None):
    """Lockdown mode (saved). On means the phone link stays off and can't be turned on."""
    if value is not None:
        store.set_meta("lockdown", "1" if value else "0")
    return store.get_meta("lockdown", "0") == "1"


# ---------------------------------------------------------------- live socket list

_edge = {}                  # Edge browser-process pid -> runs gkeystrokes' profile? (command line read once)
_dash = {}                  # python pid -> is this folder's dashboard window? (command line read once)
_seen_outside = set()       # (process role, remote address) seen since start


def _ppids(psutil):
    """{pid: parent pid} for every process, from one system snapshot (Process.ppid() takes a snapshot per call)."""
    try:
        from psutil import _psplatform
        return dict(_psplatform.ppid_map())
    except Exception:
        return {p.pid: p.info["ppid"] for p in psutil.process_iter(["ppid"])}


def _role_map(profile, psutil):
    """({pid: role}, {pid: name}) for gkeystrokes itself, its child processes (the mini window and its WebView2)
    and the Edge processes running gkeystrokes' own profile (the dashboard window). Costs a few ms: one
    process snapshot, and a command line read only for an Edge browser process not seen before (each Edge
    window's helper processes hang under its browser process, so they're sorted by that)."""
    ppid = _ppids(psutil)
    names = {p.pid: (p.info["name"] or "") for p in psutil.process_iter(["name"])}
    edge = {pid for pid, n in names.items() if n.lower() == "msedge.exe"}
    key = os.path.normcase(profile)
    for pid in [p for p in _edge if p not in edge]:
        del _edge[pid]
    roles = {}
    for pid in edge:
        root, hops = pid, 0
        while ppid.get(root) in edge and hops < 8:
            root, hops = ppid[root], hops + 1
        if root not in _edge:
            try:
                _edge[root] = any(key in os.path.normcase(a) for a in psutil.Process(root).cmdline())
            except (psutil.Error, OSError):
                _edge[root] = False
        if _edge[root]:
            roles[pid] = "Dashboard window"     # set first: an Edge window we started is also our child
    me = os.getpid()
    kids = {}
    for pid, parent in ppid.items():
        kids.setdefault(parent, []).append(pid)
    # the pywebview dashboard window: a python process started with --dashboard for this folder, and its WebView2
    from .mini import dash_args
    want = os.path.normcase(os.path.dirname(os.path.abspath(profile)))
    py = {pid for pid, n in names.items() if n.lower().startswith("python")}
    for pid in [p for p in _dash if p not in py]:
        del _dash[pid]
    for pid in py:
        if pid not in _dash:
            try:
                a = dash_args(psutil.Process(pid).cmdline())
                _dash[pid] = bool(a and a[0] == want)
            except (psutil.Error, OSError):
                _dash[pid] = False
        if _dash[pid]:
            todo = [pid]
            while todo:
                p = todo.pop()
                if p not in roles:
                    roles[p] = "Dashboard window"
                    todo.extend(kids.get(p, []))
    todo = list(kids.get(me, []))
    while todo:
        pid = todo.pop()
        if pid != me and pid not in roles:
            roles[pid] = "Mini window"
            todo.extend(kids.get(pid, []))
    roles[me] = "gkeystrokes"
    return roles, {pid: names.get(pid) or "?" for pid in roles}


def _kind(c, remote_port, phone_on):
    """local | phone | phone-viewer | outside, for one socket."""
    lip, lport = (c.laddr.ip, c.laddr.port) if c.laddr else ("", 0)
    rip = c.raddr.ip if c.raddr else ""
    if lport == remote_port and phone_on:
        return "phone" if not rip else "phone-viewer"
    if rip:
        return "local" if is_local(rip) else "outside"
    return "local" if is_local(lip) else "outside"   # listening or unconnected: local only if bound to loopback


def report(port, profile, remote_port, phone_on, lockdown_on):
    """Everything the Privacy proof panel shows."""
    import psutil
    roles, names = _role_map(profile, psutil)
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError) as e:
        conns, err = [], str(e)
    else:
        err = ""
    rows = []
    for c in conns:
        role = roles.get(c.pid)
        if role is None:
            continue
        kind = _kind(c, remote_port, phone_on and not lockdown_on)
        local = "%s:%d" % (c.laddr.ip, c.laddr.port) if c.laddr else ""
        remote = "%s:%d" % (c.raddr.ip, c.raddr.port) if c.raddr else ""
        if kind == "outside":
            _seen_outside.add((role, remote or local))
        rows.append({"proc": role, "pid": c.pid, "type": "TCP" if c.type == 1 else "UDP", "local": local,
                     "remote": remote, "status": c.status if c.status != "NONE" else "", "kind": kind})
    order = {"gkeystrokes": 0, "Mini window": 1, "Dashboard window": 2}
    rows.sort(key=lambda r: (order.get(r["proc"], 3), r["status"] != "LISTEN", r["local"]))
    return {"guard": guard_state(), "since": STARTED, "lockdown": lockdown_on, "error": err,
            "all_local": not any(r["kind"] == "outside" for r in rows),
            "outside_seen": len(_seen_outside),
            "processes": [{"pid": p, "name": names.get(p, "?"), "role": r} for p, r in roles.items() if p in names],
            "sockets": rows, "url": "http://127.0.0.1:%d/" % port}
