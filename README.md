# gkeystrokes

WhatPulse-style stats for your own PC: keystrokes, per-key heatmap, typing
speed, shortcuts, mouse clicks/distance/click map, download/upload, app time,
gaming sessions, GPU and power use, CPU/RAM/disk, records and milestones. Runs
in the tray, dashboard at http://127.0.0.1:8413.

Everything stays on this computer (unless you turn on the view-only phone link), and only **counts** are stored — never what
you typed. [PRIVACY.md](PRIVACY.md) lists exactly what's stored and how to check for yourself that nothing leaves your PC.

## What's in the dashboard

- **Overview**: live counters, activity, punchcard, top apps and keys, calendar, fun facts.
- **Keyboard, Mouse, Network**: heatmaps, typing speed, click map, data per hour and per adapter.
- **GPU & Power**: GPU and CPU watts measured by your hardware, rest-of-PC and monitor watts estimated, kWh and cost (default $0.20/kWh), GPU details, and what is using the GPU.
- **System**: live CPU, RAM, disk and GPU charts, busiest programs, drives and free space, and a per-program table (CPU time, RAM, disk I/O, GPU). Full NVIDIA details: clocks, PCIe, throttle reasons, driver, VBIOS, power limits.
- **Apps**: active time, keys, clicks and mouse distance per program. Mark any app as a game here.
- **Gaming**: a session runs from the first time you focus a game until it closes. Game input counts as **APM** (keys + clicks per minute) instead of WPM. Launchers, clients and updaters don't count.
- **Records**, **Settings** (pause, start with Windows, gaming, electricity, CSV export).
- **Mini display**: a small always-on-top window with a few live counters (keys, clicks, current app, keys per minute; more optional). Drag it, change its opacity, lock its position, or make it click-through.

Range picker (Today to All time) and All / Normal / Gaming switch apply across tabs.

## Start it

Double-click `gkeystrokes.pyw`. A tray icon appears (keyboard icon, near the
clock) and the dashboard opens in its own window.

Tray menu: **Open dashboard** · **Mini display** · **Mini click-through** · **Pause tracking** · **Start with Windows** · **Quit**.

Needs Python (tested on 3.13) with:

```
pip install -r requirements.txt
```

## Command-line options

| Flag | Effect |
|---|---|
| `--background` | don't open the dashboard on start (used by "Start with Windows") |
| `--no-tray` | no tray icon; stop with Ctrl+C |
| `--port N` | dashboard port (default 8413, next free one is used if taken) |

Running it a second time just opens the dashboard of the copy already running.

## Phone link

Settings → **Phone link** turns on a view-only copy of the dashboard on port 8414 for your other devices.
Each link carries a secret key; without it the page answers 403, and nothing can be changed from it.
It works on your home Wi-Fi, and anywhere if Tailscale runs on this PC and your phone. **New key** stops old links working.
The first time it starts, Windows asks whether Python may use the network: allow it.

## Data

- Database: `%LOCALAPPDATA%\gkeystrokes\gkeystrokes.db` (SQLite, schema v3, hourly buckets, WAL mode so a `-wal` file sits next to it)
- Backup: upgrading from the v1 format first copies the old file to `gkeystrokes.db.v1-backup` in the same folder. Keep it until your stats look right.
- Daily backups: `%LOCALAPPDATA%\gkeystrokes\backups\gkeystrokes-<date>.db`, one per day, newest 7 kept. To restore one: Quit from the tray, copy it over `gkeystrokes.db`, delete `gkeystrokes.db-wal` and `gkeystrokes.db-shm`, then start gkeystrokes again.
- Log: `%LOCALAPPDATA%\gkeystrokes\gkeystrokes.log`
- Export: Settings tab → **Download CSV** (one row per day, including energy and GPU columns)
- Electricity price and estimate settings: Settings tab → **Electricity**
- Override the folder with the `GK_DATA_DIR` environment variable.
- Broken database: if `gkeystrokes.db` can't be opened, fails its integrity check, or is missing while the backups folder has copies, gkeystrokes doesn't start (so nothing gets overwritten with an empty database). A message names the newest backup; restore it with the steps above.
- One copy only: starting gkeystrokes again brings the open dashboard window to the front (or opens one) and exits. The data folder is recorded on first run; Settings warns if a later start uses a different folder.

## Preview

```
python tools\preview.py --port 8501
```

Copies your real database read-only (SQLite backup from a `mode=ro` connection) into a new temp
folder and runs a second copy on that folder at `http://127.0.0.1:8501/`, with no tray icon.
The real tracker keeps running untouched. Ctrl+C stops the preview. `--open` also opens its
dashboard window, and `--data <folder>` reuses an earlier preview folder. It refuses to run on the
real data folder or on port 8413.

## Test

```
python tools\selftest.py
```

Prints PASS/FAIL for each check and exits non-zero on any failure.

## Notes

- Key and mouse input come from Windows Raw Input, so the tracker can't make
  your mouse or keyboard lag.
- GPU and CPU watts are measured only when the hardware reports them (NVIDIA
  driver for the GPU, Intel/AMD "Energy Meter" counters for the CPU). The rest
  of the PC, monitors and power-supply losses are estimates.
- Started from inside a packaged app's terminal (like the Claude desktop app),
  Windows would quietly save the database into that app's private folder
  (`AppData\Local\Packages\<app>\LocalCache`). gkeystrokes detects this and
  relaunches itself through Explorer so your stats land in the real folder.
- Input sent to an admin (elevated) window isn't visible to normal apps, so it
  isn't counted.
- Network totals skip loopback, virtual (Hyper-V/WSL/VMware/VirtualBox) and VPN
  adapters so traffic isn't counted twice.
- Typing speed: live WPM = characters in the last 30 s; "peak" needs a 30-second
  burst of 40+ characters; average WPM only counts time between keystrokes less
  than 2 s apart.
