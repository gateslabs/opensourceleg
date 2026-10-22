#!/usr/bin/env python3
"""
Live EMG from a Ripple Summit for a controller on the Pi. This does what
xipppy's open() + cont_hires() did: a background thread keeps receiving the
Summit's Hi-Res stream into a rolling buffer, and latest(n) hands you the
newest n samples whenever your controller asks.

    from summit_stream import SummitStream
    s = SummitStream(host="192.168.43.1", record="run1.xipp")   # record is optional
    s.start()
    ts, X, behind = s.latest(100)  # newest 100 samples = 50 ms; X is 100 x 32, uV
    #   X[:, 0] is channel 1.  ts is the Summit timestamp of each sample (30 kHz ticks).
    #   behind = how much later than normal the newest sample is, in seconds.
    #   Normally a few ms. If it grows (WiFi stalled, or data arriving late in
    #   a burst), the window is stale and the controller should go to a safe output.
    s.stop()

"behind" works by comparing the Summit's timestamps with the Pi's clock: for
each sample it notes how long after its Summit timestamp it reached the Pi,
keeps the best (smallest) value from the last 10 s as "normal", and reports how
far the newest sample is beyond that. It doesn't include the fixed delay every
sample has (front end, filtering, WiFi); only the extra on top of normal.

Run this file directly for a demo: a 20 Hz loop that prints how far behind and
RMS of channels 1-4 (or --ch) once a second. Contract a muscle and watch the RMS move.
    python3 summit_stream.py --record test.xipp
"""

import socket
import struct
import threading
import time

import numpy as np

HDR = struct.Struct("<BBBBI")
HIRES = 2
STEP = 15  # 30 kHz ticks per 2 kHz sample


class SummitStream:
    def __init__(self, host="192.168.43.1", port=2046, buffer_s=5.0, record=None):
        self.host, self.port = host, port
        self.n_buf = int(buffer_s * 2000)
        self.record = record
        self.lock = threading.Lock()
        self.X = None  # allocated when the first packet tells us the channel count
        self.T = np.zeros(self.n_buf, np.int64)
        self.count = 0  # total samples written
        self.ts_newest = None  # Summit timestamp of the newest sample
        self._lag_min = []  # [(second, smallest arrival-minus-timestamp that second)]
        self.connected = False
        self.gaps = self.stale = self.reconnects = 0
        self.last_error = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    # ---- controller side -------------------------------------------------
    def start(self, wait_s=3.0):
        """Start receiving; waits up to wait_s for the first data."""
        self._thread.start()
        t0 = time.monotonic()
        while self.count == 0 and time.monotonic() - t0 < wait_s:
            time.sleep(0.01)
        if self.count == 0:
            raise RuntimeError(f"no data from {self.host}:{self.port} ({self.last_error or 'is Hi-Res on?'})")

    def latest(self, n):
        """Newest n samples -> (timestamps, samples x channels in uV, seconds behind normal), or None."""
        with self.lock:
            c = self.count
            if c < n:
                return None
            idx = np.arange(c - n, c) % self.n_buf
            ts, X = self.T[idx].copy(), self.X[idx].copy()
            if not self._lag_min:  # reconnected, nothing new yet: buffer is stale
                return ts, X, float("inf")
            normal = min(m for _, m in self._lag_min)
            ts_new = self.ts_newest
        return ts, X, (time.monotonic() - ts_new / 30000.0) - normal

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    # ---- receiver thread -------------------------------------------------
    def _run(self):
        f = open(self.record, "ab") if self.record else None
        try:
            while not self._stop.is_set():
                try:
                    sock = socket.create_connection((self.host, self.port), timeout=5)
                except OSError as e:
                    self.last_error = str(e)
                    time.sleep(1)
                    continue
                sock.settimeout(1.0)
                self.connected = True
                try:
                    self._read(sock, f)
                except (OSError, ValueError) as e:
                    self.last_error = str(e)
                finally:
                    self.connected = False
                    sock.close()
                if not self._stop.is_set():
                    self.reconnects += 1
                    time.sleep(0.5)
        finally:
            if f:
                f.close()

    def _read(self, sock, f):
        buf, last = bytearray(), None  # last resets on reconnect (the Summit may have restarted)
        with self.lock:
            self._lag_min = []  # so does "normal" delay
        while not self._stop.is_set():
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise OSError("Summit closed the connection")
            now = time.monotonic()
            if f:
                f.write(chunk)
            buf += chunk
            i, n = 0, len(buf)
            with self.lock:
                while i + 8 <= n:
                    words, proc, mod, strm, ts = HDR.unpack_from(buf, i)
                    size = words * 4
                    if size < 8 or proc != 1:
                        raise ValueError(f"stream out of step: {bytes(buf[i : i + 8]).hex(' ')}")
                    if i + size > n:
                        break
                    if strm == HIRES:
                        if last is not None and ts <= last:
                            self.stale += 1  # old duplicate the Summit re-sends; drop it
                        else:
                            if last is not None and ts != last + STEP:
                                self.gaps += (ts - last) // STEP - 1
                            nch = size // 4 - 3
                            if self.X is None:
                                self.X = np.zeros((self.n_buf, nch), np.float32)
                            k = self.count % self.n_buf
                            self.X[k] = np.frombuffer(buf, "<f4", nch, i + 12)
                            self.T[k] = ts
                            self.count += 1
                            self.ts_newest = ts
                            last = ts
                            lag, sec = now - ts / 30000.0, int(now)
                            if self._lag_min and self._lag_min[-1][0] == sec:
                                if lag < self._lag_min[-1][1]:
                                    self._lag_min[-1] = (sec, lag)
                            else:
                                self._lag_min.append((sec, lag))
                                while self._lag_min[0][0] < sec - 10:
                                    self._lag_min.pop(0)
                    i += size
            del buf[:i]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.43.1")
    ap.add_argument("--port", type=int, default=2046)
    ap.add_argument("--record", default=None, help="also save the raw stream to this .xipp file")
    ap.add_argument(
        "--ch",
        nargs="+",
        default=["1", "2", "3", "4"],
        help="channels to show RMS for, Trellis numbers; 1-2 = bipolar (1 minus 2)",
    )
    a = ap.parse_args()

    pairs = [(int(c.partition("-")[0]) - 1, int(c.partition("-")[2] or 0) - 1) for c in a.ch]
    s = SummitStream(a.host, a.port, record=a.record)
    s.start()
    print("streaming; Ctrl-C to stop")
    period, tick = 0.05, 0  # 20 Hz, like your decoder
    t_next = time.monotonic()
    worst = 0.0
    try:
        while True:
            t_next += period
            time.sleep(max(0.0, t_next - time.monotonic()))
            r = s.latest(100)  # newest 50 ms
            if r is None:  # less than 50 ms has arrived yet
                continue
            ts, X, behind = r
            worst = max(worst, behind)
            # if behind > 0.05: hold a safe output instead of decoding
            # ---- your decoder would go here: cmd = decoder(X[:, channels]) ----
            tick += 1
            if tick % 20 == 0:
                S = np.stack([X[:, i] - (X[:, j] if j >= 0 else 0) for i, j in pairs], axis=1)
                rms = np.sqrt(np.mean(S**2, axis=0))
                print(
                    f"behind now {behind * 1000:5.1f} ms, worst {worst * 1000:5.1f} ms | "
                    f"RMS ch {' '.join(a.ch)} {np.array2string(rms, precision=0)} uV | "
                    f"gaps {s.gaps} stale {s.stale} reconnects {s.reconnects}"
                )
                worst = 0.0
    except KeyboardInterrupt:
        pass
    finally:
        s.stop()
