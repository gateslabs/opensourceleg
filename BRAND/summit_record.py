#!/usr/bin/env python3
"""
Record Hi-Res EMG from a Ripple Summit in TCP mode. No xipppy needed;
runs on the Pi (or any machine with Python 3 + numpy). Close Trellis first.

  WiFi:   python3 summit_record.py --host 192.168.43.1 --out run1
  wired:  python3 summit_record.py --host 192.168.42.1 --out run1
Stop with Ctrl-C or --seconds N. The raw stream is saved to run1.xipp.
Load it later with:
  from summit_record import load_xipp
  ts, X = load_xipp("run1.xipp")    # ts: 30 kHz ticks; X: samples x 32, uV
  X[:, :4]                           # channels 1-4 only

Each second it prints:
  samples/s  should be ~2000
  gaps       samples missing from the stream (should be 0)
  stale      out-of-date duplicate packets that were dropped (see below)
  lag        how much further behind real time the stream is than when it
             started; if this keeps climbing, WiFi can't keep up
  kB/s       ~292 with only Hi-Res on; much more means Raw/LFP/Spike are on

Packet format (checked bit-for-bit against a Trellis .nf3 recording):
  byte 0      packet size in 4-byte words, header included
  byte 1      processor (1)
  byte 2      module (2 = the front end, in this setup)
  byte 3      stream (2 = Hi-Res)
  bytes 4-7   uint32 timestamp, 30 kHz ticks
  bytes 8-11  uint32, always 1
  bytes 12-   one float32 per channel in uV, channel 1 first
One packet per sample, so timestamps step by 15. The Summit has been seen
re-sending one identical old Hi-Res packet every ~8 s; those are dropped
by checking that each timestamp is newer than the last one kept.
"""

import argparse
import socket
import struct
import time

import numpy as np

HDR = struct.Struct("<BBBBI")
HIRES = 2
STEP = 15  # 30 kHz ticks per Hi-Res sample


def split_packets(buf):
    """Complete packets in buf -> ([(offset, size, module, stream, ts)], bytes used)."""
    out, i, n = [], 0, len(buf)
    while i + 8 <= n:
        words, proc, mod, strm, ts = HDR.unpack_from(buf, i)
        size = words * 4
        if size < 8 or proc != 1:
            raise ValueError(f"stream out of step at byte {i}: {bytes(buf[i : i + 8]).hex(' ')}")
        if i + size > n:
            break
        out.append((i, size, mod, strm, ts))
        i += size
    return out, i


def load_xipp(path):
    """Decode a saved .xipp file -> (timestamps, samples x channels in uV), stale packets removed."""
    buf = open(path, "rb").read()
    pk, _ = split_packets(buf)
    keep, last = [], None
    for i, size, _mod, strm, ts in pk:
        if strm == HIRES and (last is None or ts > last):
            keep.append((i, size, ts))
            last = ts
    ts = np.array([t for _, _, t in keep], dtype=np.int64)
    X = np.vstack([np.frombuffer(buf, "<f4", size // 4 - 3, i + 12) for i, size, _ in keep])
    return ts, X


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.43.1", help="192.168.43.1 on WiFi, 192.168.42.1 wired")
    ap.add_argument("--port", type=int, default=2046)
    ap.add_argument("--out", default=time.strftime("emg_%Y%m%d_%H%M%S"))
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long; 0 = until Ctrl-C")
    a = ap.parse_args()

    try:
        sock = socket.create_connection((a.host, a.port), timeout=5)
    except OSError as e:
        raise SystemExit(
            f"could not connect to {a.host}:{a.port} ({e}). " "Is TCP ticked on the Summit, and is Trellis closed?"
        )
    sock.settimeout(2.0)
    print(f"connected to {a.host}:{a.port}, saving to {a.out}.xipp (Ctrl-C to stop)")

    f = open(a.out + ".xipp", "wb")
    buf = bytearray()
    last = ts0 = wall0 = None
    nch = ns = gaps = stale = nb = 0
    t_start = t_print = time.monotonic()
    lag_ms = 0.0
    try:
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                print("connected, but no data for 2 s")
                continue
            if not chunk:
                print("Summit closed the connection")
                break
            now = time.monotonic()
            f.write(chunk)
            nb += len(chunk)
            buf += chunk
            pk, used = split_packets(buf)
            for _i, size, _mod, strm, ts in pk:
                if strm != HIRES:
                    continue
                if last is not None and ts <= last:
                    stale += 1
                    continue
                if last is not None and ts != last + STEP:
                    gaps += (ts - last) // STEP - 1
                if ts0 is None:
                    ts0, wall0 = ts, now
                last, nch = ts, size // 4 - 3
                ns += 1
            del buf[:used]
            if ts0 is not None:
                lag_ms = ((now - wall0) - (last - ts0) / 30000) * 1000

            if now - t_print >= 1.0:
                dt = now - t_print
                print(
                    f"{ns / dt:6.0f} samples/s  {nch} ch  gaps {gaps}  stale {stale}  "
                    f"lag {lag_ms:+7.1f} ms  {nb / dt / 1e3:6.1f} kB/s"
                )
                ns = gaps = stale = nb = 0
                t_print = now
            if a.seconds and now - t_start >= a.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        f.close()
        sock.close()
    print(f"saved {a.out}.xipp")


if __name__ == "__main__":
    main()
