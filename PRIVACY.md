# Privacy: nothing leaves this PC

gkeystrokes counts keys, clicks and app time on your PC and keeps the counts in one file on your PC. It never
sends anything anywhere. You don't have to take our word for it: this page shows how to check it yourself in
about 5 minutes, with tools that come with Windows or from Microsoft.

## What is stored, and where

Everything lives in one folder: `%LOCALAPPDATA%\gkeystrokes` (Settings > Your data shows the exact path).

| File | What's in it |
|---|---|
| `gkeystrokes.db` (+ `-wal`, `-shm`) | The counts below, in a SQLite database |
| `backups\` | One copy of the database per day, newest 7 kept |
| `gkeystrokes.log` | Start/stop and error messages. No keys, no text |
| `edge-profile\` | The dashboard window's own Edge profile (separate from your normal Edge) |

**Stored:** how many times each key was pressed per hour and app ("E: 412 times between 2 and 3 pm in Code"),
key combos per day, clicks in a coarse 96×54 grid per day, mouse distance, scroll, active time per app (by
program name, like `code.exe`), bytes sent and received per network adapter per hour, CPU/GPU/RAM/power
readings per hour, and your settings.

**Never stored:** the order of keys (so nothing you type, passwords included, can be rebuilt), any text,
window titles, web addresses, file names, screenshots, or anything about other computers.

You can open the database with any SQLite viewer to see this for yourself. Copy it first: don't open the live
file while gkeystrokes is running.

## Check it yourself (5 minutes)

### 1. Look at the Privacy proof panel (30 seconds)
Open the dashboard, go to **Settings > Privacy proof**. It lists every network socket held by gkeystrokes,
its mini window and its dashboard window, live, and marks each one *This PC* or *Outside this PC*. It also
shows how many outgoing attempts gkeystrokes has blocked since it started (expected: 0).

The panel is gkeystrokes reporting on itself, so the next steps check it with tools gkeystrokes doesn't control.

### 2. TCPView (2 minutes)
1. Download TCPView from Microsoft Sysinternals: https://learn.microsoft.com/sysinternals/downloads/tcpview
2. Run it and type `python` (or `pythonw`) in the filter box. gkeystrokes runs as `pythonw.exe`.
3. You should see one listening line on `127.0.0.1:8413` and short-lived connections between `127.0.0.1`
   addresses (the dashboard asking for numbers). `127.0.0.1` means "this PC"; it never reaches a network.
4. If you turned on the phone link, you'll also see `0.0.0.0:8414` listening. That's the one place
   gkeystrokes accepts connections from other devices, and only with its secret key. Lockdown mode turns it off.

### 3. The same with built-in tools (1 minute)
In a Command Prompt:

```
netstat -abno | findstr /i "python 8413 8414"
```

Or open **Resource Monitor** (`resmon`) > Network > TCP Connections and Listening Ports, and tick `pythonw.exe`.

### 4. Windows Firewall log (optional, 5 minutes)
To see what would happen if gkeystrokes did try to connect out, block it and let Windows log any attempt:

1. Windows Security > Firewall & network protection > Advanced settings.
2. Outbound Rules > New Rule > Program > the `pythonw.exe` gkeystrokes runs with > Block the connection.
3. Properties (of your profile) > Logging > Log dropped packets: Yes.
4. Use gkeystrokes as normal. It keeps working, because it only ever talks to `127.0.0.1`. The log at
   `%systemroot%\system32\LogFiles\Firewall\pfirewall.log` shows any blocked attempt.

This rule also blocks every other Python program, so remove it when you're done. A rule that targets only
gkeystrokes comes with the single `gkeystrokes.exe` in a later version.

### 5. Wireshark (optional, for the thorough)
Capture on your network adapter while gkeystrokes runs. Nothing from gkeystrokes appears, because its traffic
never leaves the PC (loopback traffic isn't on the adapter at all).

## How it's built so it can't leak

- **It blocks itself.** At startup gkeystrokes turns on a Python audit hook (`gk/privacy.py`) that stops every
  attempt by its own code, or any library it loads, to connect or send to anything but this PC, and every
  name lookup (DNS, LLMNR, NetBIOS). An audit hook can't be removed once it's on. Each blocked attempt is
  counted in the Privacy proof panel.
- **Servers listen on 127.0.0.1 only.** The dashboard server answers only requests addressed to
  `127.0.0.1`/`localhost` (this also stops other websites from reading it). The phone link is the single,
  opt-in exception, view-only and keyed.
- **The pages can't send anything.** Every page is served with a strict Content-Security-Policy
  (`default-src 'self'`, no inline scripts or styles), so even a bug in the page couldn't load or send
  anything outside gkeystrokes' own server. No CDNs, fonts or analytics.
- **The code is checked.** The self-test (`python tools\selftest.py`) fails if any source file imports
  networking code outside the local server, contains a web address other than `127.0.0.1`, calls a
  networking DLL or a download command, binds a server to anything but `127.0.0.1` (phone link excepted), or
  if a page contains an outside address, inline script or style. It also tries to connect out and checks
  that the attempt is blocked.
- **No update checks, telemetry or crash reports.** gkeystrokes has none.

## The dashboard window

Since v2.6.0 the dashboard opens in its own window built on Edge WebView2 (through pywebview), the same way as
the mini window, with the same no-outside-traffic settings listed below. Measured on the live install
(2026-10-28, 10 minutes, Overview open): **no outside connections** from the window or any of its helper
processes, in socket samples every 5 seconds and in the Privacy proof panel polled every 10 seconds. The panel
lists it as *Dashboard window*.

Only if pywebview isn't installed does the dashboard fall back to a Microsoft Edge app window, described next.

### The Edge app window (fallback)

The fallback opens in Microsoft Edge as an app window with its own profile. Edge is a Microsoft program and
has its own background services. With only Edge's usual "turn off background networking" switches, we measured
it calling Microsoft servers and looking for Cast devices on the local network. So gkeystrokes also gives this
window a dead-end proxy (`127.0.0.1:9`, where nothing listens), turns off all name lookups except for
`127.0.0.1`/`localhost`, and turns off QUIC and Cast discovery. Measured after that (2026-10-28): the window went
from about 19 outside connections (Microsoft servers and a Chromecast) to none or 2. Those 2 are **Edge's own
browser process keeping connections to a Microsoft server open**. That process bypasses
the browser's proxy and lookup settings, so neither a page nor gkeystrokes controls it, and it carries none of
your data (the pages can't send anything outside, see above). The Privacy proof panel shows it in red when it
happens.

If you'd rather have zero, open `http://127.0.0.1:8413/` in any browser you trust instead (Settings > Privacy
proof shows the address), and close the Edge window. The mini and dashboard windows use Edge WebView2 with
the same settings; we measured no outside connections from either.

## Libraries

gkeystrokes uses four libraries, plus what they need. `requirements.lock` pins the exact version of each,
with SHA-256 hashes so `pip install --require-hashes -r requirements.lock` refuses anything that was changed,
and says what each one is for. We searched each one's source for network code:

| Library | Network code found | Verdict |
|---|---|---|
| psutil | `supports_ipv6()` binds a socket to `::1` (this PC) to test IPv6 | Local only |
| pystray, Pillow, six | None used (six only re-exports Python's urllib names) | No network |
| pywebview | A local file server (bottle) used only for file URLs; gkeystrokes passes `http://127.0.0.1`, so it never starts. Opens links meant for a new window in your browser; the mini and dashboard pages have none | Local only |
| pythonnet, clr_loader, cffi, pycparser, proxy_tools, typing_extensions, bottle | clr_loader has a download in a maintainer script that only runs when that file is run by hand (`if __name__ == "__main__"`) | No network at run time |

Installing the libraries (`pip install`) does go online, to the Python Package Index. That's a one-time setup
step, not something gkeystrokes does, and the hashes make sure you get exactly the reviewed files.

## Checking that you run the reviewed code

Each source release comes with a SHA-256 hash. To check a download, in PowerShell:

```
Get-FileHash .\gkeystrokes-<version>-src.zip -Algorithm SHA256
```

and compare it with the hash published next to the release. `python tools\release.py` rebuilds the same zip
from the same git commit, so anyone can reproduce the hash.
