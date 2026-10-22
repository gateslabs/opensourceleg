#!/usr/bin/env python3
"""
Quick DMC calibration (Posh et al. 2023, Sec. II-E.2), about 15 s, run at the
start of every session. dmc_controller.py --subject runs it for you; this file
can also run it on its own:

    python3 dmc_calibrate.py --subject S01 --gas 3-4 --ta 1-2

--gas / --ta are Trellis channel numbers (1-32); several are averaged, and "a-b"
means channel a minus channel b. The channels are saved in the calibration so
the controller always uses the same ones.

One Enter starts it, then it cues you through, no more key presses:
  RELAX      5 s  -> baseline and noise level
  TOES DOWN  3 s  push as hard as you can (half-step forward with the other foot) -> MVA_GAS, m_GAS
  relax      3 s
  TOES UP    3 s  pull as hard as you can -> MVA_TA, m_TA
Paper: 10 s of rest. 5 s gives the same mean and SD here and keeps it quick.

Saves <base>.json (edit it by hand to tune sensitivity, like the paper's manual
tuning: raise mva to make control less sensitive, lower it to make it more) and
<base>_phases.npz (the raw EMG of each phase, so the calibration can be
recomputed offline with dmc.calibrate()).
"""

import argparse
import time

import numpy as np
from dmc import calibrate, parse_channels
from summit_stream import SummitStream

# (phase kept for calibration or None, seconds, cue)
QUICK = [
    ("rest", 5.0, "RELAX  - lower-leg muscles loose"),
    ("gas", 3.0, "TOES DOWN - push as hard as you can!"),
    (None, 3.0, "relax"),
    ("ta", 3.0, "TOES UP - pull as hard as you can!"),
]
BUFFER_S = 15.0  # the stream buffer must hold the longest phase


def capture(s, seconds, cue):
    """Count down one phase, then return its EMG (samples x 32, uV)."""
    n = int(seconds * 2000)
    gaps0 = s.gaps
    t_end = time.monotonic() + seconds
    while (left := t_end - time.monotonic()) > 0:
        print(f"\r  {cue:40s} {left:3.0f} s ", end="", flush=True)
        time.sleep(0.1)
    time.sleep(0.05)  # let the last packets arrive
    r = s.latest(n)
    if r is None or s.gaps != gaps0 or r[2] > 0.05:
        raise ValueError("EMG data dropped during this phase; check WiFi and Trellis (Hi-Res only)")
    return r[1]


def quick_calibrate(s, gas, ta, base):
    """Run the cued phases on an open SummitStream -> Calibration, saved as <base>.json.
    Raises ValueError (with the reason) if the data or the result isn't usable."""
    input("\nCalibration: RELAX, then TOES DOWN, relax, TOES UP, cued on screen (15 s).\n" "  Press Enter to start...")
    raw = {}
    for name, sec, cue in QUICK:
        X = capture(s, sec, cue)
        if name:
            raw[name] = X
    print(f"\r  {'done':45s}")
    np.savez(base + "_phases.npz", gas_spec=gas, ta_spec=ta, **raw)
    cal = calibrate(raw["rest"], raw["gas"], raw["ta"], gas, ta)
    cal.save(base + ".json")
    report(cal)
    print(f"  saved {base}.json and {base}_phases.npz")
    return cal


def report(cal):
    print("\n            rest mean   rest SD     MVA")
    for i, name in enumerate(("GAS", "TA")):
        print(f"  {name:4s}  {cal.rest_mean[i]:9.1f}  {cal.rest_sd[i]:9.1f}  {cal.mva[i]:9.1f}   uV")
    print(
        f"  m_GAS {cal.m_gas:.3f}   m_TA {cal.m_ta:.3f}   m0 {cal.m0:.3f}"
        "   (paper, Table IV: m_GAS 1.5-5, m_TA 0.01-0.63)"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--gas", default="3-4", help="GAS channel(s), e.g. 3 or 3,4 or 3-4")
    ap.add_argument("--ta", default="1-2", help="TA channel(s)")
    ap.add_argument("--host", default="192.168.43.1")
    ap.add_argument("--port", type=int, default=2046)
    ap.add_argument("--record", default=None, help="also save the raw stream to this .xipp file")
    a = ap.parse_args()
    base = f"{a.subject}_dmc_cal"

    s = SummitStream(a.host, a.port, buffer_s=BUFFER_S, record=a.record)
    s.start()
    try:
        quick_calibrate(s, parse_channels(a.gas), parse_channels(a.ta), base)
    except ValueError as e:
        raise SystemExit(f"\ncalibration failed: {e}")
    finally:
        s.stop()
    print(f"next: python3 dmc_controller.py --cal {base}.json --dry-run   (watch u respond, leg not driven)")


if __name__ == "__main__":
    main()
