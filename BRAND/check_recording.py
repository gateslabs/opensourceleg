#!/usr/bin/env python3
"""
Check and convert .xipp recordings made by summit_record.py, summit_stream.py
or controller_template.py.

    python3 check_recording.py S01_T01.xipp [more.xipp ...]          # summary
    python3 check_recording.py S01_T01.xipp --npz                     # also save S01_T01.npz
    python3 check_recording.py S01_T01.xipp --mat                     # also save S01_T01.mat (needs scipy)

Saved files contain: ts (Summit timestamps, 30 kHz ticks), t (seconds from
the first sample), X (samples x channels, uV; column 0 = channel 1), fs = 2000.
"""

import argparse
import os

import numpy as np
from summit_record import HIRES, split_packets


def read(path):
    buf = open(path, "rb").read()
    pk, used = split_packets(buf)
    keep, last, stale, extra = [], None, 0, 0
    for i, size, mod, strm, ts in pk:
        if strm == HIRES:
            if last is not None and ts <= last:
                stale += 1
            else:
                keep.append((i, size, ts))
                last = ts
        elif mod != 0:
            extra += 1
    if not keep:
        raise ValueError("no Hi-Res data in this file")
    ts = np.array([t for _, _, t in keep], dtype=np.int64)
    X = np.vstack([np.frombuffer(buf, "<f4", size // 4 - 3, i + 12) for i, size, _ in keep])
    return ts, X, stale, extra, len(buf), len(buf) - used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--npz", action="store_true")
    ap.add_argument("--mat", action="store_true")
    a = ap.parse_args()
    for path in a.files:
        try:
            ts, X, stale, extra, nbytes, partial = read(path)
        except Exception as e:
            print(f"{path}: could not read ({e})")
            continue
        d = np.diff(ts)
        missing = int(((d // 15) - 1).sum()) if d.size else 0
        dur = (ts[-1] - ts[0]) / 30000 if ts.size > 1 else 0.0
        print(
            f"{path}: {X.shape[0]} samples x {X.shape[1]} ch, {dur:.2f} s, "
            f"missing {missing}, stale dropped {stale}, other-stream packets {extra}, "
            f"{nbytes / 1e6:.1f} MB" + (f", {partial} B cut off at end" if partial else "")
        )
        if extra:
            print("   note: streams other than Hi-Res were on; turn them off in Trellis for WiFi runs")
        base = os.path.splitext(path)[0]
        out = {"ts": ts, "t": (ts - ts[0]) / 30000.0, "X": X, "fs": 2000}
        if a.npz:
            np.savez(base + ".npz", **out)
            print(f"   saved {base}.npz")
        if a.mat:
            try:
                from scipy.io import savemat

                savemat(base + ".mat", out)
                print(f"   saved {base}.mat")
            except ImportError:
                print("   --mat needs scipy (pip install scipy); skipped")


if __name__ == "__main__":
    main()
