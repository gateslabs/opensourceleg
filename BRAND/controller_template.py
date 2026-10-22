#!/usr/bin/env python3
"""
Template for an EMG controller on the Pi. Fill in the four functions marked
FILL IN; everything else (live data, stale-data safety, recording, logging,
timing) is handled.

    python3 controller_template.py --record S01_T01.xipp --log S01_T01_log.csv

Every 50 ms it takes the newest 50 ms of EMG from the Summit. If the data is
fresh it decodes and sends a command; if it's stale (WiFi stalled) it holds a
safe output instead. Each tick is logged to the CSV with the Summit timestamp
so it can be lined up with the EMG recording afterwards.
"""

import argparse
import csv
import time

import numpy as np
from summit_stream import SummitStream

WINDOW = 100  # samples per decode (100 at 2 kHz = 50 ms)
PERIOD = 0.05  # seconds between decodes (20 Hz)
MAX_BEHIND = 0.05  # seconds; data later than normal by more than this is stale
CHANNELS = [1, 2, 3, 4]  # Trellis channel numbers (1-32) the decoder uses


# ---------------------------------------------------------------- FILL IN ---
def load_decoder():
    """Load your trained model. Return whatever decode() needs."""
    return None


def decode(model, X):
    """X: WINDOW x len(CHANNELS) array of EMG in uV, oldest row first.
    Return the command. Placeholder: mean RMS across channels."""
    return float(np.sqrt(np.mean(X**2)))


def send(cmd):
    """Send the command to whatever the controller drives."""
    pass


def safe_output():
    """What to do when the data is stale or the program stops."""
    pass


# ----------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.43.1")
    ap.add_argument("--port", type=int, default=2046)
    ap.add_argument("--record", default=time.strftime("emg_%Y%m%d_%H%M%S.xipp"))
    ap.add_argument("--log", default=None, help="CSV log (default: same name as --record)")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long; 0 = until Ctrl-C")
    a = ap.parse_args()
    log_path = a.log or a.record.rsplit(".", 1)[0] + "_log.csv"
    cols = [c - 1 for c in CHANNELS]

    model = load_decoder()
    s = SummitStream(a.host, a.port, record=a.record)
    s.start()
    print(f"streaming from {a.host}; recording {a.record}; logging {log_path}; Ctrl-C to stop")

    f = open(log_path, "w", newline="")
    w = csv.writer(f)
    w.writerow(["pi_time_s", "summit_ts_newest", "behind_ms", "state", "cmd", "decode_ms"])
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

            r = s.latest(WINDOW)
            ts, X, behind = r if r else ([0], None, float("inf"))  # None until WINDOW samples have arrived
            t0 = time.monotonic()
            if behind > MAX_BEHIND:
                safe_output()
                state, cmd = "stale", float("nan")
                stale += 1
            else:
                cmd = decode(model, X[:, cols])
                send(cmd)
                state = "ok"
            dt_ms = (time.monotonic() - t0) * 1000
            w.writerow([f"{t0 - t_start:.4f}", int(ts[-1]), f"{behind * 1000:.2f}", state, cmd, f"{dt_ms:.2f}"])

            ticks += 1
            worst = max(worst, behind)
            if ticks % 20 == 0:
                print(
                    f"behind worst {worst * 1000:6.1f} ms | stale ticks {stale} | "
                    f"overruns {overruns} | gaps {s.gaps} stale pkts {s.stale} reconnects {s.reconnects} | "
                    f"cmd {cmd:.3g} | decode {dt_ms:.1f} ms"
                )
                worst = 0.0
                stale = overruns = 0
            if a.seconds and time.monotonic() - t_start >= a.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        safe_output()
        s.stop()
        f.close()
        print(f"saved {a.record} and {log_path}")


if __name__ == "__main__":
    main()
