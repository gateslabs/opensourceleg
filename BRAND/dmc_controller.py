#!/usr/bin/env python3
"""
Direct myoelectric control of the OSL ankle (Posh et al. 2023), live from the Summit.

Two programs run on the Pi, in two terminals (summit_live.sh sets both up):

    # 1. the leg: firmware impedance about a UDP equilibrium, K = 0.05 Nm/deg/kg
    python3 OSL_brand_connection_impedance.py --mode ankle --weight-kg 72
    # 2. this: quick calibration, then EMG -> u -> equilibrium theta_max * u at 100 Hz
    python3 dmc_controller.py --subject S01

A session is one connection to the Summit: it calibrates first (15 s, cued),
then a menu runs trials until you quit:
    Enter  trial, driving the leg      d  dry-run trial (computed, not sent)
    c      recalibrate                 q  quit
Ctrl-C ends a trial (the ankle ramps to neutral) and returns to the menu.
--cal FILE skips the calibration and uses a saved one; --dry-run never sends.

Every tick it takes the newest 200 ms of EMG, computes u (dmc.decode) and sends
the equilibrium theta_eq = PF_SIGN * theta_max * u. u = 0 (relaxed) leaves the
ankle a passive spring about neutral. If the data is stale (WiFi stall) it sends
u = 0, the same passive spring. The equilibrium is rate limited, so a jump to the
fallback, or any glitch, can't snap the ankle. Between trials nothing is sent and
the leg holds neutral.

Files, all named <subject>_<date_time>_...:
    .xipp           the whole session's EMG, calibration included
    _cal1.json      calibration (and _cal1_phases.npz); _cal2 after recalibrating
    _T01_log.csv    every tick of trial 1: Summit timestamp, behind, u_p, u_d, u,
                    commanded and measured ankle angle
    _session.txt    one line per trial: which calibration, live or dry run, duration
"""

import argparse
import csv
import math
import socket
import threading
import time

import msgpack
from dmc import ENV_WINDOW, THETA_MAX_DEG, Calibration, decode, parse_channels
from dmc_calibrate import BUFFER_S, quick_calibrate, report
from summit_stream import SummitStream

PERIOD = 0.01  # 100 Hz; the leg loop runs at 200 Hz and holds the newest setpoint
MAX_BEHIND = 0.05  # seconds; data later than normal by more than this is stale
MAX_RATE_DEG_S = 150.0  # limit on how fast the equilibrium may move. With the 200 ms envelope,
# full-range intent changes take ~75 deg/s, so this only bites on jumps.
# Sign of plantarflexion in the leg's ankle frame. *** VERIFY ON THE BENCH: leg off the body,
# not a dry run, contract GAS: the toes must point DOWN. If they go up, set -1. ***
PF_SIGN = -1  # bench 2026-10-22: with +1, contracting GAS sent the toes UP
PF_SIGN_VERIFIED = True  # set True once the bench test has confirmed PF_SIGN
LEG_KEY = "ankle_flexion"  # payload key OSL_brand_connection_impedance.py reads for the ankle


def run_trial(s, cal, theta_max, send_fn, angle, log_path, seconds):
    """One trial until Ctrl-C (or `seconds`), then ramp to neutral. Returns its duration in s."""
    max_step = math.radians(MAX_RATE_DEG_S) * PERIOD
    theta_cmd = 0.0

    def send(target):
        nonlocal theta_cmd
        theta_cmd += max(-max_step, min(max_step, target - theta_cmd))
        send_fn(theta_cmd)

    f = open(log_path, "w", newline="")
    w = csv.writer(f)
    w.writerow([
        "pi_time_s",
        "summit_ts_newest",
        "behind_ms",
        "state",
        "u_p",
        "u_d",
        "u",
        "theta_cmd_deg",
        "theta_meas_deg",
        "decode_ms",
    ])
    t_start = t_next = time.monotonic()
    stale = overruns = ticks = 0
    worst = 0.0
    try:
        while True:
            t_next += PERIOD
            now = time.monotonic()
            if now > t_next:  # last tick ran long; skip ahead, don't try to catch up
                overruns += 1
                t_next = now
            else:
                time.sleep(t_next - now)

            r = s.latest(ENV_WINDOW)
            t0 = time.monotonic()
            ts, X, behind = r if r else ([0], None, float("inf"))  # None until 200 ms has arrived
            if behind > MAX_BEHIND:
                up = ud = u = 0.0  # stale: no intent -> passive spring about neutral
                state = "stale"
                stale += 1
            else:
                up, ud, u = decode(X, cal)
                state = "ok"
            send(PF_SIGN * theta_max * u)
            dt_ms = (time.monotonic() - t0) * 1000
            w.writerow([
                f"{t0 - t_start:.4f}",
                int(ts[-1]),
                f"{behind * 1000:.2f}",
                state,
                f"{up:.4f}",
                f"{ud:.4f}",
                f"{u:+.4f}",
                f"{math.degrees(theta_cmd):+.2f}",
                f"{math.degrees(angle[0]):+.2f}",
                f"{dt_ms:.2f}",
            ])

            ticks += 1
            worst = max(worst, behind)
            if ticks % 50 == 0:
                bar = int(round(u * 10))
                print(
                    f"u_p {up:4.2f} u_d {ud:4.2f} u {u:+5.2f} [{'-' * -bar:>10}|{'+' * bar:<10}] "
                    f"cmd {math.degrees(theta_cmd):+5.1f} meas {math.degrees(angle[0]):+5.1f} deg | "
                    f"behind worst {worst * 1000:5.1f} ms stale {stale} overruns {overruns} gaps {s.gaps}"
                )
                worst = 0.0
                stale = overruns = 0
            if seconds and time.monotonic() - t_start >= seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        print("\nramping to neutral...")
        for _ in range(int(1.0 / PERIOD)):  # rate limit brings 15 deg back in 0.1 s; send for 1 s to be sure
            send(0.0)
            time.sleep(PERIOD)
        f.close()
    return time.monotonic() - t_start


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", default="S01", help="file name prefix")
    ap.add_argument("--gas", default="3-4", help="GAS channel(s) for calibration, e.g. 3-4 (bipolar)")
    ap.add_argument("--ta", default="1-2", help="TA channel(s) for calibration")
    ap.add_argument("--cal", default=None, help="use this saved calibration instead of calibrating")
    ap.add_argument("--theta-max-deg", type=float, default=THETA_MAX_DEG)
    ap.add_argument("--dry-run", action="store_true", help="never send to the leg")
    ap.add_argument("--host", default="192.168.43.1")
    ap.add_argument("--port", type=int, default=2046)
    ap.add_argument("--leg-ip", default="127.0.0.1")
    ap.add_argument("--leg-port", type=int, default=9000)
    ap.add_argument("--listen-port", type=int, default=9001, help="where the leg reports its angle")
    ap.add_argument("--seconds", type=float, default=0, help="end each trial after this long; 0 = Ctrl-C")
    a = ap.parse_args()
    base = f"{a.subject}_{time.strftime('%Y%m%d_%H%M%S')}"
    theta_max = math.radians(a.theta_max_deg)
    gas, ta = parse_channels(a.gas), parse_channels(a.ta)

    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    leg = (a.leg_ip, a.leg_port)
    angle = [float("nan")]  # newest measured ankle angle from the leg

    def listen():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", a.listen_port))
        sock.settimeout(0.5)
        while True:
            try:
                d = msgpack.unpackb(sock.recvfrom(65535)[0])
                angle[0] = float(d[LEG_KEY][0])
            except (socket.timeout, KeyError, TypeError, ValueError, IndexError):
                pass

    threading.Thread(target=listen, daemon=True).start()

    def to_leg(theta):
        out.sendto(msgpack.packb({LEG_KEY: theta}), leg)

    s = SummitStream(a.host, a.port, buffer_s=BUFFER_S, record=base + ".xipp")
    s.start()
    print(f"session {base}: recording {base}.xipp")
    print(
        f"theta_max {a.theta_max_deg:.0f} deg, PF_SIGN {PF_SIGN:+d}"
        + ("" if PF_SIGN_VERIFIED else " (NOT VERIFIED: bench test first, leg off the body)")
        + (", DRY RUN: never sending" if a.dry_run else f", leg at {leg[0]}:{leg[1]}")
    )

    cal, cal_name, n_cal, trial = None, a.cal, 0, 0
    if a.cal:
        cal = Calibration.load(a.cal)
        print(f"using {a.cal}: GAS {cal.gas} TA {cal.ta}")
        report(cal)
    session = open(base + "_session.txt", "a")
    try:
        while True:
            if cal is None:
                n_cal += 1
                cal_name = f"{base}_cal{n_cal}"
                try:
                    cal = quick_calibrate(s, gas, ta, cal_name)
                    cal_name += ".json"
                except ValueError as e:
                    print(f"\ncalibration failed: {e}\n  try again (Ctrl-C to quit)")
                    continue
            choice = input("\n[Enter] trial, drive the leg   [d] dry run   [c] recalibrate   [q] quit: ")
            choice = choice.strip().lower()
            if choice == "q":
                break
            if choice == "c":
                cal = None
                continue
            dry = a.dry_run or choice == "d"
            trial += 1
            log_path = f"{base}_T{trial:02d}_log.csv"
            print(f"trial T{trial:02d} ({'DRY RUN' if dry else 'DRIVING THE LEG'}), {cal_name}; Ctrl-C to end")
            dur = run_trial(s, cal, theta_max, (lambda th: None) if dry else to_leg, angle, log_path, a.seconds)
            session.write(f"T{trial:02d}  {'dry' if dry else 'live'}  {cal_name}  {dur:.1f} s  {log_path}\n")
            session.flush()
            print(f"saved {log_path}")
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        s.stop()
        session.close()
        print(f"session saved: {base}.xipp, {base}_session.txt")


if __name__ == "__main__":
    main()
