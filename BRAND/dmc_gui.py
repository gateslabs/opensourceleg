#!/usr/bin/env python3
"""
dmc_gui.py: a point-and-click page for running a DMC session, in the browser.

It drives the same tmux session "emg" that summit_live.sh sets up (windows
0 check / 1 leg / 2 dmc). Every button types into one of those windows, and the
page shows what each window prints, so the GUI and an SSH terminal
(laptop_connect.sh) can be used side by side and always agree.

    python3 dmc_gui.py              # on the Pi; summit_live.sh starts it for you
    ./laptop_connect.sh gui         # laptop/desktop: SSH tunnel + opens http://localhost:8080

It listens on 127.0.0.1 only, so the leg can't be driven from the campus
network; the page is reached through SSH (laptop_connect.sh gui, VS Code's port
forwarding, or ssh -L 8080:localhost:8080 jkanetis@OSL.local).

The settings box (subject, spring K and damping, theta_max, channels, saved
calibration, trial length) builds the commands; they apply the next time you
press Start in that step. A button only works when the program in that window
is at the right prompt; the server checks before typing anything.

The software Stop is not an e-stop: keep the real e-stop in someone's hand.
"""

import argparse
import contextlib
import csv
import json
import math
import re
import shlex
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

DIR = Path(__file__).resolve().parent
PY = Path.home() / "opensourceleg/.venv/bin/python3"
VENV = f"source {Path.home()}/opensourceleg/.venv/bin/activate"
SESSION = "emg"
WIN = {"check": 0, "leg": 1, "dmc": 2}
SUMMIT_SSID = "R04500-0094-WIFI"
HOME_WIFI = "MWireless"
NMCLI = "/usr/bin/nmcli"
SETTINGS_FILE = DIR / "gui_settings.json"
K_PER_KG = 0.05 * 180 / math.pi  # Posh: 0.05 Nm/deg per kg of body weight, in Nm/rad

DEFAULTS = {
    "subject": "S01",
    "spring": "bench",  # bench: K from k_rad; worn: K from weight_kg
    "k_rad": 60.0,
    "weight_kg": 80.0,
    "damping": 1.5,
    "theta_max_deg": 15.0,
    "ta": "1-2",
    "gas": "3-4",
    "cal": "",  # "" = calibrate fresh; otherwise a saved *_calN.json
    "seconds": 0.0,  # 0 = trial runs until End trial
}
CHANNELS = re.compile(r"^\d{1,2}(-\d{1,2})?(,\d{1,2}(-\d{1,2})?)*$")
DATA_FILE = re.compile(r"^[\w.-]+\.(xipp|csv|json|npz|txt)$")
CAL_FILE = re.compile(r"^[\w.-]+_cal\d*\.json$")

lock = threading.Lock()
last_action = {}  # window -> time of the last keys sent, to swallow double clicks


# ---------- settings ----------


def clean(p):
    """Validate settings from the page; raises ValueError with a message for the user."""
    s = dict(DEFAULTS)
    s.update({k: p[k] for k in DEFAULTS if k in p})

    def num(key, lo, hi):
        try:
            v = float(s[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key}: not a number") from None
        if not lo <= v <= hi:
            raise ValueError(f"{key}: {v:g} is outside {lo:g}-{hi:g}")
        s[key] = v

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", str(s["subject"])):
        raise ValueError("subject: letters, digits, _ and - only")
    if s["spring"] not in ("bench", "worn"):
        raise ValueError("spring: bench or worn")
    num("k_rad", 0, 400)
    num("weight_kg", 30, 200)
    num("damping", 0, 20)
    num("theta_max_deg", 1, 20)
    num("seconds", 0, 3600)
    for ch in ("ta", "gas"):
        s[ch] = str(s[ch]).replace(" ", "")
        if not CHANNELS.fullmatch(s[ch]):
            raise ValueError(f"{ch}: channels like 1-2 (bipolar) or 1,2")
    if s["cal"] and not (CAL_FILE.fullmatch(s["cal"]) and (DIR / s["cal"]).is_file()):
        raise ValueError(f"calibration file not found: {s['cal']}")
    return s


def load_settings():
    try:
        return clean(json.loads(SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        return dict(DEFAULTS)


def commands(s):
    spring = f"--k-rad {s['k_rad']:g}" if s["spring"] == "bench" else f"--weight-kg {s['weight_kg']:g}"
    dmc = (
        f"{PY} dmc_controller.py --subject {s['subject']} --ta {s['ta']} --gas {s['gas']}"
        f" --theta-max-deg {s['theta_max_deg']:g}"
    )
    if s["cal"]:
        dmc += f" --cal {shlex.quote(s['cal'])}"
    if s["seconds"]:
        dmc += f" --seconds {s['seconds']:g}"
    return {
        "check": f"{PY} summit_stream.py --ch {s['ta']} {s['gas']}",
        "leg": f"{PY} OSL_brand_connection_impedance.py --mode ankle {spring} --damping {s['damping']:g}",
        "dmc": dmc,
    }


def pf_sign():
    m = re.search(r"^PF_SIGN = ([+-]?\d)", (DIR / "dmc_controller.py").read_text(), re.M)
    v = re.search(r"^PF_SIGN_VERIFIED = (\w+)", (DIR / "dmc_controller.py").read_text(), re.M)
    return {"value": int(m.group(1)) if m else None, "verified": bool(v and v.group(1) == "True")}


# ---------- tmux ----------


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=5)


def has_session():
    return tmux("has-session", "-t", SESSION).returncode == 0


def ensure_session():
    """Create tmux "emg" (same layout as summit_live.sh) if it isn't there yet."""
    if has_session():
        return
    tmux("new-session", "-d", "-s", SESSION, "-n", "check", "-c", str(DIR))
    tmux("new-window", "-t", f"{SESSION}:1", "-n", "leg", "-c", str(DIR))
    tmux("new-window", "-t", f"{SESSION}:2", "-n", "dmc", "-c", str(DIR))
    for w in WIN.values():
        tmux("send-keys", "-t", f"{SESSION}:{w}", VENV, "Enter")
    time.sleep(0.5)


def pane(win):
    out = tmux("capture-pane", "-p", "-J", "-t", f"{SESSION}:{WIN[win]}", "-S", "-80").stdout
    return out.rstrip("\n")


def running(win):
    cmd = tmux("display-message", "-p", "-t", f"{SESSION}:{WIN[win]}", "#{pane_current_command}").stdout.strip()
    return cmd not in ("", "bash", "sh", "zsh")


def keys(win, *k):
    tmux("send-keys", "-t", f"{SESSION}:{WIN[win]}", *k)


def type_line(win, text):
    keys(win, "C-u")  # clear anything already typed (summit_live.sh leaves its command typed)
    keys(win, "-l", text)
    keys(win, "Enter")


# ---------- what each window is doing ----------


def nonblank(text):
    return [line for line in text.splitlines() if line.strip()]


def leg_state(text, run):
    if not run:
        return {"state": "idle"}
    lines = nonblank(text)
    last = lines[-1] if lines else ""
    m = re.search(r"ankle: ([+-]\d+\.\d+) \(eq ([+-]\d+\.\d+)\)", last)
    if m:
        return {
            "state": "running",
            "ankle_deg": math.degrees(float(m.group(1))),
            "eq_deg": math.degrees(float(m.group(2))),
        }
    if "Press Enter to start" in last:
        return {"state": "await_engage"}
    return {"state": "starting"}


def dmc_state(text, run):
    if not run:
        return {"state": "idle"}
    lines = nonblank(text)
    last = lines[-1] if lines else ""
    if "Press Enter to start" in last:
        return {"state": "cal_prompt"}
    if "[Enter] trial, drive the leg" in last:
        return {"state": "menu"}
    i_menu = max((i for i, ln in enumerate(lines) if "[Enter] trial, drive the leg" in ln), default=-1)
    i_trial = max((i for i, ln in enumerate(lines) if ln.startswith("trial T")), default=-1)
    if i_trial > i_menu:
        kind = "dry" if "DRY RUN" in lines[i_trial] else "live"
        if any("ramping to neutral" in ln for ln in lines[i_trial:]):
            return {"state": "ramping", "trial": kind}
        return {"state": "in_trial", "trial": kind}
    if re.search(r"RELAX|TOES|^\s*relax", last):
        return {"state": "calibrating"}
    return {"state": "busy"}


def window_states():
    out = {}
    for win in WIN:
        text, run = pane(win), running(win)
        st = {"check": lambda t, r: {"state": "running" if r else "idle"}, "leg": leg_state, "dmc": dmc_state}[win](
            text, run
        )
        st["text"] = "\n".join(text.splitlines()[-30:])
        out[win] = st
    return out


# ---------- actions ----------

# action -> (window, states it is allowed in, what to type)
ACTIONS = {
    "check_start": ("check", ["idle"], "cmd"),
    "check_stop": ("check", ["running"], ["C-c"]),
    "leg_start": ("leg", ["idle"], "cmd"),
    "leg_engage": ("leg", ["await_engage"], ["Enter"]),
    "leg_stop": ("leg", ["starting", "await_engage", "running"], ["C-c"]),
    "dmc_start": ("dmc", ["idle"], "cmd"),
    "dmc_calibrate": ("dmc", ["cal_prompt"], ["Enter"]),
    "dmc_dry": ("dmc", ["menu"], ["C-u", "d", "Enter"]),
    "dmc_live": ("dmc", ["menu"], ["C-u", "Enter"]),
    "dmc_end": ("dmc", ["in_trial"], ["C-c"]),
    "dmc_recal": ("dmc", ["menu"], ["C-u", "c", "Enter"]),
    "dmc_quit": ("dmc", ["menu"], ["C-u", "q", "Enter"]),
    "dmc_stop": ("dmc", ["cal_prompt", "calibrating", "menu", "in_trial", "ramping", "busy"], ["C-c"]),
}


def do_action(name, params):
    if name == "save_settings":
        s = clean(params)
        SETTINGS_FILE.write_text(json.dumps(s, indent=1))
        return "settings saved; they apply the next time you press Start"
    if name == "stop_all":
        threading.Thread(target=stop_all, daemon=True).start()
        return "stopping: trial ends (ankle ramps to neutral), then the controller, the leg and the check"
    if name in ("wifi_summit", "wifi_home"):
        return switch_wifi(SUMMIT_SSID if name == "wifi_summit" else HOME_WIFI)
    if name not in ACTIONS:
        raise ValueError(f"unknown action {name}")

    win, allowed, what = ACTIONS[name]
    ensure_session()
    states = window_states()
    state = states[win]["state"]
    if state not in allowed:
        raise ValueError(f"{win} is '{state}', not ready for that")
    if time.monotonic() - last_action.get(win, 0) < 1.0:
        raise ValueError("one moment: the last command is still arriving")
    if name == "dmc_start" and states["check"]["state"] != "idle":
        raise ValueError("stop the stream check first (step 1); the controller opens its own Summit connection")
    if name == "check_start" and states["dmc"]["state"] != "idle":
        raise ValueError("the controller is connected to the Summit; quit it first")
    if name == "dmc_live" and states["leg"]["state"] != "running":
        raise ValueError("engage the leg spring first (step 2), or use Dry run")

    if what == "cmd":
        cmd = commands(load_settings())[win]
        type_line(win, cmd)
        msg = f"{win}: {cmd}"
    else:
        for k in what:
            keys(win, k)
        msg = f"{win}: sent {' '.join(what)}"
    last_action[win] = time.monotonic()
    return msg


def stop_all():
    """End a trial (ramp to neutral), quit the controller, then stop the leg and the check."""
    if not has_session():
        return
    with lock:
        if running("dmc"):
            keys("dmc", "C-c")
            time.sleep(1.5)  # the trial ramps the ankle to neutral for 1 s
            if running("dmc"):
                keys("dmc", "C-c")  # at the menu: quits and closes the session files
                time.sleep(0.5)
        for win in ("leg", "check"):
            if running(win):
                keys(win, "C-c")


def wifi_now():
    ssid = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True).stdout.strip()
    ips = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
    return {"ssid": ssid or "(none)", "ips": ips}


def switch_wifi(name):
    ok = subprocess.run(["sudo", "-n", "-l", NMCLI, "connection", "up", name], capture_output=True).returncode == 0
    if not ok:
        raise ValueError(
            "the Pi needs a sudo password to change WiFi. Use ./summit_live.sh (switches to the Summit) or "
            "./summit_live.sh back in a terminal, or install the one-line sudo rule in NOTEBOOK.md section F."
        )
    subprocess.Popen(
        ["sudo", "-n", NMCLI, "connection", "up", name],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return f"switching the Pi to {name}; the page reconnects once the tunnel is back (laptop_connect.sh gui)"


# ---------- files and trial plots ----------


def sessions(n=8):
    groups = {}
    for p in DIR.iterdir():
        m = re.match(r"^(.+_\d{8}_\d{6})", p.name)
        if m and p.is_file() and DATA_FILE.fullmatch(p.name):
            groups.setdefault(m.group(1), []).append(p)
    out = []
    for base in sorted(groups, key=lambda b: b[-15:], reverse=True)[:n]:
        files = sorted(groups[base], key=lambda p: p.name)
        out.append({"base": base, "files": [{"name": p.name, "size": p.stat().st_size} for p in files]})
    return out


def cal_files():
    return sorted((p.name for p in DIR.glob("*_cal*.json") if CAL_FILE.fullmatch(p.name)), reverse=True)


def trial_logs(n=12):
    logs = sorted(DIR.glob("*_T[0-9][0-9]_log.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in logs[:n]]


def trial(name):
    """Downsampled traces of one trial log plus the numbers that say whether K is high enough."""
    if not name:
        logs = trial_logs(1)
        if not logs:
            return {"name": None}
        name = logs[0]
    if not re.fullmatch(r"[\w.-]+_T\d\d_log\.csv", name) or not (DIR / name).is_file():
        raise ValueError("no such trial log")
    t, cmd, meas, u = [], [], [], []
    stale = 0
    with open(DIR / name, newline="") as f:
        for r in csv.DictReader(f):
            try:
                t.append(float(r["pi_time_s"]))
                cmd.append(float(r["theta_cmd_deg"]))
                meas.append(float(r["theta_meas_deg"]))
                u.append(float(r["u"]))
            except (KeyError, ValueError):
                continue
            stale += r.get("state") == "stale"
    if not t:
        return {"name": name, "t": []}
    step = max(1, len(t) // 1500)
    fin = [m for m in meas if math.isfinite(m)]
    # "at rest": the command has sat within 0.5 deg of neutral for the last 0.5 s (50 ticks)
    rest, quiet = [], 0
    for c, m in zip(cmd, meas):
        quiet = quiet + 1 if abs(c) < 0.5 else 0
        if quiet >= 50 and math.isfinite(m):
            rest.append(abs(m))
    rest.sort()
    summary = {
        "duration_s": round(t[-1], 1),
        "stale_ticks": stale,
        "cmd_range": [round(min(cmd), 1), round(max(cmd), 1)],
        "meas_range": [round(min(fin), 1), round(max(fin), 1)] if fin else None,
        "rest_median": round(rest[len(rest) // 2], 1) if rest else None,
        "rest_worst": round(rest[-1], 1) if rest else None,
    }
    return {
        "name": name,
        "t": t[::step],
        "cmd": cmd[::step],
        "meas": [m if math.isfinite(m) else None for m in meas[::step]],
        "u": u[::step],
        "summary": summary,
    }


# ---------- HTTP ----------


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/":
                self.send(200, PAGE, "text/html; charset=utf-8")
            elif url.path == "/api/state":
                s = load_settings()
                body = {
                    "session": has_session(),
                    "windows": window_states() if has_session() else {},
                    "settings": s,
                    "commands": commands(s),
                    "k_worn": round(K_PER_KG * s["weight_kg"], 1),
                    "pf_sign": pf_sign(),
                    "wifi": wifi_now(),
                    "cals": cal_files(),
                    "logs": trial_logs(),
                    "sessions": sessions(),
                }
                self.send(200, json.dumps(body))
            elif url.path == "/api/trial":
                name = parse_qs(url.query).get("name", [""])[0]
                self.send(200, json.dumps(trial(name)))
            elif url.path.startswith("/files/"):
                name = unquote(url.path[len("/files/") :])
                p = DIR / name
                if not DATA_FILE.fullmatch(name) or not p.is_file():
                    self.send(404, '{"error": "not found"}')
                    return
                self.send(
                    200,
                    p.read_bytes(),
                    "application/octet-stream",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
            else:
                self.send(404, '{"error": "not found"}')
        except ValueError as e:
            self.send(400, json.dumps({"error": str(e)}))

    def do_POST(self):
        # The custom header makes a browser preflight cross-site requests (which we never
        # answer), so another web page can't press buttons through the tunnel.
        if urlparse(self.path).path != "/api/action" or self.headers.get("X-DMC") != "1":
            self.send(403, '{"error": "forbidden"}')
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            with lock:
                msg = do_action(str(req.get("action")), req.get("params") or {})
            self.send(200, json.dumps({"ok": True, "message": msg}))
        except ValueError as e:
            self.send(400, json.dumps({"ok": False, "error": str(e)}))


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OSL DMC Session</title>
<style>
:root {
  --bg: #f6f7f9; --card: #ffffff; --ink: #1b1f24; --muted: #5f6b7a; --line: #dde2e8;
  --accent: #2563eb; --accent-ink: #ffffff; --ok: #15803d; --warn: #b45309; --bad: #b91c1c;
  --code: #f1f3f6; --chip: #eef1f5; --cmd: #2563eb; --meas: #d97706;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1216; --card: #171b21; --ink: #e6e9ee; --muted: #9aa5b4; --line: #2a313b;
    --accent: #3b82f6; --accent-ink: #ffffff; --ok: #4ade80; --warn: #fbbf24; --bad: #f87171;
    --code: #0b0e12; --chip: #222832; --cmd: #60a5fa; --meas: #fbbf24;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header { position: sticky; top: 0; z-index: 2; background: var(--card); border-bottom: 1px solid var(--line);
  padding: 10px 16px; display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; }
header h1 { font-size: 16px; margin: 0 8px 0 0; }
.chip { background: var(--chip); border-radius: 999px; padding: 2px 10px; font-size: 12.5px; white-space: nowrap; }
.chip b { font-weight: 600; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; background: var(--muted); }
.dot.ok { background: var(--ok); } .dot.bad { background: var(--bad); } .dot.warn { background: var(--warn); }
.spacer { flex: 1; }
main { display: grid; grid-template-columns: 320px 1fr; gap: 14px; padding: 14px 16px; max-width: 1400px; margin: 0 auto; }
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
.card h2 { font-size: 14px; margin: 0 0 8px; display: flex; align-items: center; gap: 8px; }
.card h2 .n { background: var(--accent); color: var(--accent-ink); border-radius: 50%; width: 22px; height: 22px;
  display: inline-flex; align-items: center; justify-content: center; font-size: 12px; }
.state { margin-left: auto; font-weight: 500; font-size: 12.5px; }
.hint { color: var(--muted); font-size: 12.5px; margin: 4px 0 8px; }
.row { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
button { font: inherit; border: 1px solid var(--line); background: var(--chip); color: var(--ink);
  border-radius: 7px; padding: 6px 12px; cursor: pointer; }
button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
button.danger { background: var(--bad); border-color: var(--bad); color: #fff; }
button:disabled { background: var(--chip); border-color: var(--line); color: var(--muted); opacity: .55; cursor: not-allowed; }
pre { background: var(--code); border: 1px solid var(--line); border-radius: 7px; padding: 8px; margin: 6px 0 0;
  font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; max-height: 230px; overflow: auto;
  white-space: pre-wrap; word-break: break-all; }
code.cmd { display: block; font: 11.5px ui-monospace, Menlo, Consolas, monospace; color: var(--muted);
  word-break: break-all; }
label { display: block; font-size: 12.5px; color: var(--muted); margin-top: 8px; }
input, select { font: inherit; width: 100%; padding: 5px 7px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--bg); color: var(--ink); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.seg { display: flex; gap: 0; margin-top: 4px; }
.seg label { margin: 0; flex: 1; text-align: center; border: 1px solid var(--line); padding: 5px; cursor: pointer;
  color: var(--ink); }
.seg label:first-child { border-radius: 6px 0 0 6px; } .seg label:last-child { border-radius: 0 6px 6px 0; }
.seg input { display: none; }
.seg input:checked + span { font-weight: 600; color: var(--accent); }
.big { font-size: 26px; font-weight: 600; font-variant-numeric: tabular-nums; }
.stats { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; margin-top: 6px; }
.stats b { font-variant-numeric: tabular-nums; }
canvas { width: 100%; height: 220px; display: block; margin-top: 8px; }
.legend { font-size: 12px; color: var(--muted); }
.legend i { display: inline-block; width: 14px; height: 3px; vertical-align: middle; margin: 0 4px 0 10px; }
#toast { position: fixed; bottom: 14px; left: 50%; transform: translateX(-50%); max-width: min(680px, 92vw);
  padding: 9px 14px; border-radius: 8px; background: var(--ink); color: var(--bg); font-size: 13px;
  opacity: 0; transition: opacity .2s; pointer-events: none; z-index: 5; }
#toast.show { opacity: .95; } #toast.err { background: var(--bad); color: #fff; }
.files a { color: var(--accent); text-decoration: none; } .files li { font-size: 12.5px; }
.files ul { margin: 2px 0 8px; padding-left: 16px; }
.warnbox { border-left: 3px solid var(--warn); padding: 4px 8px; font-size: 12.5px; margin: 6px 0; }
#offline { display: none; background: var(--bad); color: #fff; padding: 6px 16px; font-size: 13px; }
</style>
</head>
<body>
<div id="offline">No answer from the Pi. If it is changing WiFi, the tunnel (laptop_connect.sh gui) reconnects by itself; this page keeps trying.</div>
<header>
  <h1>OSL ankle · DMC session</h1>
  <span class="chip" id="c-wifi">WiFi …</span>
  <span class="chip" id="c-tmux">tmux …</span>
  <span class="chip" id="c-pf">PF_SIGN …</span>
  <span class="chip" id="c-ankle">ankle —</span>
  <span class="spacer"></span>
  <button class="danger" data-act="stop_all" title="End trial (ramp to neutral), quit controller, stop leg">Stop all</button>
</header>
<main>
<div>
  <div class="card">
    <h2>Settings</h2>
    <div class="hint">Used the next time you press <b>Start</b> in a step. Restart the leg to change its spring.</div>
    <form id="f" autocomplete="off">
      <div class="two">
        <div><label>Subject</label><input name="subject"></div>
        <div><label>θmax (deg)</label><input name="theta_max_deg" inputmode="decimal"></div>
      </div>
      <label>Leg spring</label>
      <div class="seg">
        <label><input type="radio" name="spring" value="bench"><span>Bench (K)</span></label>
        <label><input type="radio" name="spring" value="worn"><span>Worn (weight)</span></label>
      </div>
      <div class="two">
        <div id="kbox"><label>K (Nm/rad)</label><input name="k_rad" inputmode="decimal"></div>
        <div id="wbox"><label>Weight (kg) → <span id="kworn"></span></label><input name="weight_kg" inputmode="decimal"></div>
        <div><label>Damping b (Nm·s/rad)</label><input name="damping" inputmode="decimal"></div>
      </div>
      <div class="hint" id="bhint"></div>
      <div class="two">
        <div><label>TA channels</label><input name="ta"></div>
        <div><label>GAS channels</label><input name="gas"></div>
      </div>
      <label>Calibration</label>
      <select name="cal" id="cal"></select>
      <label>Trial length (s, 0 = until End trial)</label>
      <input name="seconds" inputmode="decimal">
      <div class="row"><button class="primary" type="submit">Save settings</button></div>
    </form>
  </div>
  <div class="card">
    <h2>Pi network</h2>
    <div class="hint" id="ips"></div>
    <div class="row">
      <button data-act="wifi_summit" data-confirm="Move the Pi to the Summit WiFi? The page drops for a few seconds.">Pi → Summit WiFi</button>
      <button data-act="wifi_home" data-confirm="Move the Pi back to MWireless? The Summit session can't be reached after this.">Pi → MWireless</button>
    </div>
    <div class="hint">Needs the sudo rule in NOTEBOOK.md §F; otherwise use <code>./summit_live.sh</code> / <code>./summit_live.sh back</code> or power-cycle the Pi.</div>
  </div>
  <div class="card files">
    <h2>Session files</h2>
    <div id="files" class="hint">…</div>
  </div>
</div>

<div>
  <div class="card">
    <h2><span class="n">1</span> Stream check <span class="state" id="s-check"></span></h2>
    <div class="hint">Trellis: Wireless, TCP, <b>Hi-Res only</b>. Relaxed RMS ≈ 10–20 µV and jumps when you contract; <code>gaps 0</code>. Stop it before step 3.</div>
    <code class="cmd" id="cmd-check"></code>
    <div class="row">
      <button class="primary" data-act="check_start">Start</button>
      <button data-act="check_stop">Stop</button>
    </div>
    <pre id="o-check"></pre>
  </div>

  <div class="card">
    <h2><span class="n">2</span> Leg <span class="state" id="s-leg"></span></h2>
    <div class="hint">Start homes the ankle to its hard stop (it moves!). Then <b>Engage spring</b>: the foot should settle at neutral (ankle ≈ 0°). E-stop in hand.</div>
    <code class="cmd" id="cmd-leg"></code>
    <div class="row">
      <button class="primary" data-act="leg_start" data-confirm="Home the ankle now? It drives to its hard stop. E-stop in hand, hands clear.">Start (home)</button>
      <button class="primary" data-act="leg_engage">Engage spring</button>
      <button data-act="leg_stop" data-confirm="Stop the leg? The motor turns off and the ankle goes limp.">Stop leg</button>
      <span class="spacer"></span>
      <span><span class="hint">ankle</span> <span class="big" id="ankle">—</span> <span class="hint" id="eq"></span></span>
    </div>
    <div class="warnbox" id="legwarn" style="display:none"></div>
    <pre id="o-leg"></pre>
  </div>

  <div class="card">
    <h2><span class="n">3</span> Controller: calibrate, then trials <span class="state" id="s-dmc"></span></h2>
    <div class="hint">Calibration is cued on screen (15 s): RELAX 5 s · TOES DOWN 3 s (push hard) · relax 3 s · TOES UP 3 s (pull hard). Do a dry run first.</div>
    <code class="cmd" id="cmd-dmc"></code>
    <div class="row">
      <button class="primary" data-act="dmc_start">Start</button>
      <button class="primary" data-act="dmc_calibrate">Begin calibration</button>
      <button data-act="dmc_dry">Dry run</button>
      <button class="primary" data-act="dmc_live" data-confirm="Start a trial that DRIVES THE LEG? E-stop in hand.">Live trial</button>
      <button class="danger" data-act="dmc_end">End trial</button>
      <button data-act="dmc_recal">Recalibrate</button>
      <button data-act="dmc_quit">Quit</button>
      <button data-act="dmc_stop" title="Sends Ctrl-C">Ctrl-C</button>
    </div>
    <pre id="o-dmc"></pre>
  </div>

  <div class="card">
    <h2>Trial result <span class="state"><select id="logsel" style="width:auto"></select></span></h2>
    <div class="legend"><i style="background:var(--cmd)"></i>commanded θ_eq<i style="background:var(--meas)"></i>measured ankle (deg)</div>
    <canvas id="plot"></canvas>
    <div class="stats" id="stats"></div>
    <div class="warnbox" id="tunehint" style="display:none"></div>
  </div>
</div>
</main>
<div id="toast"></div>
<script>
const $ = (s) => document.querySelector(s);
let S = null, formDirty = false, plotted = "", plotTimer = 0;

function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "show" + (err ? " err" : "");
  clearTimeout(t._h); t._h = setTimeout(() => (t.className = ""), err ? 7000 : 3500);
}

async function act(action, params) {
  try {
    const r = await fetch("/api/action", { method: "POST", headers: { "Content-Type": "application/json", "X-DMC": "1" },
      body: JSON.stringify({ action, params }) });
    const j = await r.json();
    if (j.ok) toast(j.message); else toast(j.error || "failed", true);
    if (action === "save_settings" && j.ok) formDirty = false;
  } catch (e) { toast("no answer from the Pi", true); }
  poll();
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b || b.disabled) return;
  if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;
  act(b.dataset.act);
});

const f = $("#f");
f.addEventListener("input", () => { formDirty = true; springUI(); });
f.addEventListener("submit", (e) => {
  e.preventDefault();
  const p = Object.fromEntries(new FormData(f).entries());
  act("save_settings", p);
});

function springUI() {
  const worn = f.spring.value === "worn";
  $("#kbox").style.display = worn ? "none" : ""; $("#wbox").style.display = worn ? "" : "none";
  const k = worn ? 2.8648 * parseFloat(f.weight_kg.value || 0) : parseFloat(f.k_rad.value || 0);
  $("#kworn").textContent = "K " + (2.8648 * parseFloat(f.weight_kg.value || 0)).toFixed(0);
  $("#bhint").textContent = k ? `Suggested b for K ${k.toFixed(0)}: ${(0.02 * k).toFixed(1)}–${(0.03 * k).toFixed(1)}. Raise only if it buzzes.` : "";
}

function fillForm(s, cals) {
  const sel = $("#cal");
  const opts = ['<option value="">Calibrate fresh (recommended)</option>']
    .concat(cals.map((c) => `<option value="${c}">${c}</option>`));
  if (sel.dataset.key !== cals.join("|")) {   // rebuild only when the list changes, keeping the choice
    const keep = sel.value; sel.innerHTML = opts.join(""); sel.value = keep; sel.dataset.key = cals.join("|");
  }
  if (formDirty) return;
  for (const k of ["subject", "k_rad", "weight_kg", "damping", "theta_max_deg", "ta", "gas", "seconds"]) f[k].value = s[k];
  for (const r of f.querySelectorAll('input[name="spring"]')) r.checked = r.value === s.spring;
  sel.value = s.cal;
  springUI();
}

const LABEL = {
  idle: ["not running", ""], running: ["running", "ok"], starting: ["homing…", "warn"],
  await_engage: ["homed: press Engage spring", "warn"], cal_prompt: ["ready to calibrate", "warn"],
  calibrating: ["calibrating: follow the cues", "warn"], menu: ["menu: pick a trial", "ok"],
  in_trial: ["IN TRIAL", "bad"], ramping: ["ramping to neutral", "warn"], busy: ["connecting…", "warn"],
};
const ENABLE = {
  check_start: ["check", ["idle"]], check_stop: ["check", ["running"]],
  leg_start: ["leg", ["idle"]], leg_engage: ["leg", ["await_engage"]], leg_stop: ["leg", ["starting", "await_engage", "running"]],
  dmc_start: ["dmc", ["idle"]], dmc_calibrate: ["dmc", ["cal_prompt"]], dmc_dry: ["dmc", ["menu"]],
  dmc_live: ["dmc", ["menu"]], dmc_end: ["dmc", ["in_trial"]], dmc_recal: ["dmc", ["menu"]], dmc_quit: ["dmc", ["menu"]],
  dmc_stop: ["dmc", ["cal_prompt", "calibrating", "menu", "in_trial", "ramping", "busy"]],
};

function render(s) {
  S = s;
  $("#offline").style.display = "none";
  const onSummit = s.wifi.ssid === "R04500-0094-WIFI";
  $("#c-wifi").innerHTML = `<span class="dot ${onSummit ? "ok" : "warn"}"></span>Pi WiFi <b>${s.wifi.ssid}</b>`;
  $("#c-tmux").innerHTML = `<span class="dot ${s.session ? "ok" : ""}"></span>tmux emg <b>${s.session ? "up" : "not started (Start creates it)"}</b>`;
  const pf = s.pf_sign;
  $("#c-pf").innerHTML = `<span class="dot ${pf.verified ? "ok" : "bad"}"></span>PF_SIGN <b>${pf.value > 0 ? "+1" : pf.value}</b> ${pf.verified ? "verified" : "UNVERIFIED"}`;
  $("#ips").textContent = "Pi addresses: " + (s.wifi.ips.join(", ") || "none");
  fillForm(s.settings, s.cals);
  for (const w of ["check", "leg", "dmc"]) $("#cmd-" + w).textContent = "$ " + s.commands[w];

  const W = s.windows || {};
  for (const w of ["check", "leg", "dmc"]) {
    const st = W[w] ? W[w].state : "idle";
    const [txt, cls] = LABEL[st] || [st, ""];
    let extra = W[w] && W[w].trial ? ` (${W[w].trial === "dry" ? "dry run" : "driving the leg"})` : "";
    $("#s-" + w).innerHTML = `<span class="dot ${cls}"></span>${txt}${extra}`;
    const pre = $("#o-" + w), atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
    const t = W[w] ? W[w].text : "";
    if (pre.textContent !== t) { pre.textContent = t; if (atEnd) pre.scrollTop = pre.scrollHeight; }
  }
  for (const b of document.querySelectorAll("button[data-act]")) {
    const e = ENABLE[b.dataset.act];
    if (!e) continue;
    const st = W[e[0]] ? W[e[0]].state : "idle";
    b.disabled = !e[1].includes(st);
  }
  const live = $('button[data-act="dmc_live"]');
  if (!live.disabled && !(W.leg && W.leg.state === "running")) { live.disabled = true; live.title = "Engage the leg spring first"; }
  else live.title = "";

  const L = W.leg || {};
  if (L.state === "running") {
    $("#ankle").textContent = (L.ankle_deg >= 0 ? "+" : "") + L.ankle_deg.toFixed(1) + "°";
    $("#eq").textContent = "eq " + (L.eq_deg >= 0 ? "+" : "") + L.eq_deg.toFixed(1) + "°";
    $("#c-ankle").innerHTML = `ankle <b>${$("#ankle").textContent}</b>`;
  } else { $("#ankle").textContent = "—"; $("#eq").textContent = ""; $("#c-ankle").textContent = "ankle —"; }
  const lw = $("#legwarn");
  if (L.state === "running" && Math.abs(L.eq_deg) < 0.3 && Math.abs(L.ankle_deg) > 2) {
    lw.style.display = ""; lw.textContent = `At rest the ankle sits ${L.ankle_deg.toFixed(1)}° from neutral: friction is beating the spring. Raise K (Settings), Stop leg, Start again.`;
  } else lw.style.display = "none";

  // trial log picker
  const sel = $("#logsel"), cur = sel.value;
  if (sel.dataset.key !== s.logs.join("|")) {
    sel.innerHTML = ['<option value="">newest trial</option>'].concat(s.logs.map((l) => `<option value="${l}">${l}</option>`)).join("");
    sel.value = cur; sel.dataset.key = s.logs.join("|");
  }
  const want = sel.value || s.logs[0] || "";
  const inTrial = W.dmc && W.dmc.state === "in_trial";
  if (want && (want !== plotted || (inTrial && Date.now() - plotTimer > 3000))) loadTrial(want);

  // files
  const fl = s.sessions.map((g) => `<div><b>${g.base}</b><ul>` + g.files.map((x) =>
    `<li><a href="/files/${encodeURIComponent(x.name)}">${x.name.slice(g.base.length) || x.name}</a> <span class="hint">${(x.size / 1e6).toFixed(x.size > 1e6 ? 1 : 3)} MB</span></li>`).join("") + "</ul></div>").join("");
  $("#files").innerHTML = fl || "no sessions yet";
}

$("#logsel").addEventListener("change", () => { plotted = ""; if (S) render(S); });

async function loadTrial(name) {
  plotted = name; plotTimer = Date.now();
  try {
    const r = await fetch("/api/trial?name=" + encodeURIComponent(name));
    drawTrial(await r.json());
  } catch (e) {}
}

function drawTrial(d) {
  const c = $("#plot"), dpr = window.devicePixelRatio || 1;
  const W = c.clientWidth, H = c.clientHeight;
  c.width = W * dpr; c.height = H * dpr;
  const g = c.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.documentElement);
  const col = (v) => css.getPropertyValue(v).trim();
  if (!d.t || !d.t.length) { $("#stats").textContent = "no trials yet"; return; }
  const L = 34, R = 8, T = 8, B = 22, tmax = d.t[d.t.length - 1] || 1;
  const x = (t) => L + (t / tmax) * (W - L - R), y = (v) => T + (1 - (v + 20) / 40) * (H - T - B);
  g.font = "11px system-ui"; g.fillStyle = col("--muted"); g.strokeStyle = col("--line"); g.lineWidth = 1;
  for (const v of [-15, -10, -5, 0, 5, 10, 15]) {
    g.beginPath(); g.moveTo(L, y(v)); g.lineTo(W - R, y(v)); g.globalAlpha = v === 0 ? 1 : 0.5; g.stroke(); g.globalAlpha = 1;
    g.fillText((v > 0 ? "+" : "") + v, 4, y(v) + 4);
  }
  const step = tmax > 60 ? 10 : 5;
  for (let t = 0; t <= tmax; t += step) g.fillText(t + " s", x(t) - 8, H - 6);
  const line = (arr, color, w) => {
    g.strokeStyle = color; g.lineWidth = w; g.beginPath(); let pen = false;
    arr.forEach((v, i) => { if (v === null) { pen = false; return; } const px = x(d.t[i]), py = y(v);
      pen ? g.lineTo(px, py) : g.moveTo(px, py); pen = true; });
    g.stroke();
  };
  line(d.cmd, col("--cmd"), 1.5); line(d.meas, col("--meas"), 2);
  const s = d.summary, fmt = (a) => a ? `${a[0]}° … ${a[1]}°` : "—";
  $("#stats").innerHTML = `<span>${d.name}</span><span>length <b>${s.duration_s} s</b></span>` +
    `<span>stale <b>${s.stale_ticks}</b></span><span>commanded <b>${fmt(s.cmd_range)}</b></span>` +
    `<span>measured <b>${fmt(s.meas_range)}</b></span>` +
    `<span>off neutral at rest <b>${s.rest_median === null ? "—" : s.rest_median}°</b> median, <b>${s.rest_worst === null ? "—" : s.rest_worst}°</b> worst</span>`;
  const h = $("#tunehint"); let msg = [];
  if (s.rest_worst !== null && s.rest_worst > 2) msg.push(`The ankle stays up to ${s.rest_worst}° off neutral when you relax: friction is holding it. Raise K (bench: 60 → 120), keep b ≈ 0.025·K.`);
  if (s.meas_range && s.cmd_range) {
    const mc = s.cmd_range[1] - s.cmd_range[0], mm = s.meas_range[1] - s.meas_range[0];
    if (mc > 10 && mm < 0.6 * mc) msg.push(`The ankle travelled ${mm.toFixed(0)}° of the ${mc.toFixed(0)}° commanded.`);
  }
  h.style.display = msg.length ? "" : "none"; h.textContent = msg.join(" ");
}

async function poll() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    render(await r.json());
  } catch (e) { $("#offline").style.display = "block"; }
}
poll(); setInterval(poll, 800);
window.addEventListener("resize", () => { plotted = ""; });
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"DMC GUI on http://127.0.0.1:{a.port} (reach it from the laptop with ./laptop_connect.sh gui)")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()


if __name__ == "__main__":
    main()
