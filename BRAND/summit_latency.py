#!/usr/bin/env python3
"""
Measure the delay from a signal at the Summit's front end to the sample
arriving on the Pi. BENCH ONLY: nothing connected to a person.

Wiring (any ~1000:1 divider works; keep the bottom resistor small):
  Pi GPIO17 (pin 11) --[1 MOhm]--+-- spare channel socket (e.g. 32)
                                 |
  Pi GND    (pin 9)  --[1 kOhm]--+
  Pi GND also to the adapter's REF and GND, tied together.
  That channel's DIP switch ON. 3.3 V / 1001 = ~3.3 mV step.

The Pi toggles the pin, finds the edge in the Summit's stream, and times it
on its own clock, so no clock sync is needed. The number includes the
front end, the Summit's Hi-Res filter and packetizing, WiFi, and the Pi
receiving it: everything before your decoder.

  python3 -u summit_latency.py --chan 32 --trials 500 | tee latency.log
"""

import argparse
import collections
import random
import socket
import struct
import threading
import time

import numpy as np

HDR = struct.Struct("<BBBBI")
HIRES = 2

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="192.168.43.1")
ap.add_argument("--port", type=int, default=2046)
ap.add_argument("--chan", type=int, default=32, help="channel the divider feeds, 1-32 (Trellis numbering)")
ap.add_argument("--trials", type=int, default=500)
ap.add_argument("--thr-uv", type=float, default=1000.0, help="edge threshold in uV (~3300 uV step expected)")
ap.add_argument("--gpio", type=int, default=17, help="BCM pin number")
ap.add_argument("--chip", type=int, default=0, help="gpiochip (0 on a Pi 4)")
ap.add_argument("--out", default="latency.csv")
args = ap.parse_args()
col = args.chan - 1


def open_gpio(pin, chip):
    try:
        import lgpio

        h = lgpio.gpiochip_open(chip)
        lgpio.gpio_claim_output(h, pin, 0)
        return (lambda v: lgpio.gpio_write(h, pin, v)), (lambda: lgpio.gpiochip_close(h))
    except ImportError:
        pass
    try:
        import RPi.GPIO as GPIO

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.OUT, initial=0)
        return (lambda v: GPIO.output(pin, v)), GPIO.cleanup
    except ImportError:
        raise SystemExit("no GPIO library in this Python: run  pip install lgpio  (needs internet)")


class Receiver(threading.Thread):
    def __init__(self, sock):
        super().__init__(daemon=True)
        self.sock = sock
        self.lock = threading.Lock()
        self.recent = collections.deque(maxlen=20)  # last 10 ms of the loopback channel
        self.armed = False
        self.result = None
        self.hit = threading.Event()
        self.samples = self.stale = self.gaps = 0
        self.error = None

    def arm(self, direction):
        with self.lock:
            self.baseline = float(np.mean(self.recent)) if self.recent else 0.0
            self.direction = direction
            self.result = None
            self.hit.clear()
            self.armed = True

    def disarm(self):
        with self.lock:
            self.armed = False

    def run(self):
        buf, last = bytearray(), None
        try:
            while True:
                chunk = self.sock.recv(65536)
                t = time.perf_counter_ns()  # when this data reached the Pi
                if not chunk:
                    self.error = "Summit closed the connection"
                    return
                buf += chunk
                i, n = 0, len(buf)
                with self.lock:
                    while i + 8 <= n:
                        words, proc, mod, strm, ts = HDR.unpack_from(buf, i)
                        size = words * 4
                        if size < 8 or proc != 1:
                            self.error = f"stream out of step: {bytes(buf[i : i + 8]).hex(' ')}"
                            return
                        if i + size > n:
                            break
                        if strm == HIRES:
                            if last is not None and ts <= last:
                                self.stale += 1
                            else:
                                if last is not None and ts != last + 15:
                                    self.gaps += (ts - last) // 15 - 1
                                last = ts
                                self.samples += 1
                                x = struct.unpack_from("<f", buf, i + 12 + 4 * col)[0]
                                if self.armed and self.direction * (x - self.baseline) > args.thr_uv:
                                    self.result = (t, ts)
                                    self.armed = False
                                    self.hit.set()
                                self.recent.append(x)
                        i += size
                del buf[:i]
        except OSError as e:
            self.error = str(e)


try:
    sock = socket.create_connection((args.host, args.port), timeout=5)
except OSError as e:
    raise SystemExit(f"could not connect to {args.host}:{args.port} ({e})")
sock.settimeout(None)
rx = Receiver(sock)
rx.start()
time.sleep(1.0)
if rx.samples < 1500:
    raise SystemExit(f"only {rx.samples} samples in 1 s; is Hi-Res on? ({rx.error or 'no error'})")
print(f"connected, stream OK; channel {args.chan}, GPIO{args.gpio}, {args.trials} edges")

write, close = open_gpio(args.gpio, args.chip)
time.sleep(0.5)
rows, misses, level = [], 0, 0
try:
    for k in range(args.trials):
        if rx.error:
            print("stream error:", rx.error)
            break
        time.sleep(random.uniform(0.15, 0.35))  # random spacing so edges don't lock to packet timing
        level ^= 1
        rx.arm(+1 if level else -1)
        t0 = time.perf_counter_ns()
        write(level)
        if rx.hit.wait(0.5):
            t_arr, ts = rx.result
            rows.append((k, level, (t_arr - t0) / 1e6, ts))
        else:
            misses += 1
            rx.disarm()
        if k == 9 and misses == 10:
            raise SystemExit(f"no edges seen on channel {args.chan}: check the wiring, DIP switch and --chan")
        if (k + 1) % 50 == 0:
            lat = np.array([r[2] for r in rows])
            print(f"  {k + 1:4d} edges  median {np.median(lat):6.1f} ms  max {lat.max():6.1f} ms  misses {misses}")
finally:
    write(0)
    close()

lat = np.array([r[2] for r in rows])
print(f"\n{lat.size} edges measured, {misses} missed, {rx.gaps} samples missing, {rx.stale} stale dropped")
if lat.size:
    p50, p95, p99 = np.percentile(lat, [50, 95, 99])
    print(f"delay (ms):  min {lat.min():.1f}  median {p50:.1f}  p95 {p95:.1f}  p99 {p99:.1f}  max {lat.max():.1f}")
    np.savetxt(
        args.out,
        np.array(rows),
        delimiter=",",
        comments="",
        header="trial,level,delay_ms,summit_ts",
        fmt=["%d", "%d", "%.3f", "%d"],
    )
    print(f"saved {args.out}")
