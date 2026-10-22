#!/usr/bin/env python3
"""
Direct myoelectric control (DMC) of the OSL ankle, after Posh, Schmiedeler and
Wensing, "Finite-State Impedance and Direct Myoelectric Control for Robotic
Ankle Prostheses", IEEE TNSRE 31, 2023, Sec. II-B (the PDF is in this folder).

The shared math, used the same way by dmc_calibrate.py, dmc_controller.py and
offline replay of .xipp recordings:

  1. envelope():    GAS and TA EMG -> remove the window mean, rectify, average
                    over 200 ms (the paper's 200 ms moving average).
  2. normalize():   envelope -> u_p (GAS), u_d (TA), 0 at rest and 1 at MVA.
                    Activity within 2 SD of the resting level is ignored.
  3. volitional_input(): (u_p, u_d) -> u in [-1, 1] by the co-contraction
                    mapping of Eq. 1: +1 is full plantarflexion intent, -1 full
                    dorsiflexion.
  4. The leg renders tau = -K (theta - theta_max * u): an impedance spring whose
     equilibrium is theta_max * u. OSL_brand_connection_impedance.py does that
     part in the Dephy firmware, with K = 0.05 Nm/deg per kg body weight (Table I).

Where the paper leaves a choice open, this file picks one and says so (look for
"Paper:"). Replay a recording offline with the same code:

    python3 dmc.py --cal S01_dmc_cal.json --replay S01_T01.xipp
"""

import json
import math
import time
from dataclasses import asdict, dataclass

import numpy as np

FS = 2000  # Summit Hi-Res sample rate
ENV_WINDOW = 400  # 200 ms moving average, as in the paper
THETA_MAX_DEG = 15.0  # Table I, DMC column
MIN_FIT_R = 0.1  # calibration samples below this activation don't count for the slopes


def parse_channels(text):
    """'1,2' -> two monopolar channels; '1-2,3-4' -> two bipolar pairs (1 minus 2, 3 minus 4).
    Channel numbers are Trellis numbers, 1-32. Returns [[a, b], ...] with b = 0 for monopolar."""
    spec = []
    for item in text.split(","):
        a, _, b = item.strip().partition("-")
        spec.append([int(a), int(b) if b else 0])
    return spec


def _signals(W, spec):
    """W: (..., n, 32) in uV -> (..., n, len(spec))."""
    return np.stack([W[..., a - 1] - (W[..., b - 1] if b else 0.0) for a, b in spec], axis=-1)


def envelope(W, gas, ta):
    """W: (..., ENV_WINDOW, 32) window(s) of EMG in uV -> (..., 2) envelope [GAS, TA] in uV.
    Several channels for one muscle are averaged after rectifying."""
    out = []
    for spec in (gas, ta):
        S = _signals(W, spec).astype(np.float64)
        S -= S.mean(axis=-2, keepdims=True)  # DC guard; the Hi-Res filter should already remove it
        out.append(np.abs(S).mean(axis=(-2, -1)))
    return np.stack(out, axis=-1)


def envelope_series(X, gas, ta, step=20):
    """Envelope of a long recording X (samples x 32), one value every `step` samples
    (20 = 10 ms), each computed from the ENV_WINDOW samples ending there, exactly as online."""
    V = np.lib.stride_tricks.sliding_window_view(X, ENV_WINDOW, axis=0)[::step]  # (k, 32, ENV_WINDOW)
    return np.concatenate([envelope(V[i : i + 500].swapaxes(-1, -2), gas, ta) for i in range(0, len(V), 500)])


@dataclass
class Calibration:
    gas: list  # channel spec, see parse_channels
    ta: list
    rest_mean: list  # [GAS, TA] resting envelope, uV
    rest_sd: list
    mva: list  # [GAS, TA] envelope at maximum voluntary activation, uV
    m_gas: float  # co-contraction slope u_p/u_d while trying to contract GAS alone
    m_ta: float  # same while contracting TA alone
    created: str = ""
    notes: str = ""

    @property
    def m0(self):
        # Paper: "m0 = atan(tan(m_GAS) + tan(m_TA)/2) is the slope of the line bisecting the
        # region". Read literally that mixes angles and slopes; the bisector of two lines with
        # slopes m_GAS and m_TA is this, which matches Fig. 2.
        return math.tan((math.atan(self.m_gas) + math.atan(self.m_ta)) / 2)

    def save(self, path):
        with open(path, "w") as f:
            json.dump(asdict(self) | {"m0 (computed, not read back)": self.m0}, f, indent=2)

    @classmethod
    def load(cls, path):
        d = json.load(open(path))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def normalize(env, cal):
    """env (..., 2) uV -> (u_p, u_d), each 0 (rest) to 1 (MVA), clipped.
    Paper: the resting mean is 0 and activity within 2 SD of it is ignored. Here the 0 point
    is mean + 2 SD so the output doesn't jump at the edge of the ignored band."""
    floor = np.asarray(cal.rest_mean) + 2 * np.asarray(cal.rest_sd)
    u = np.clip((env - floor) / (np.asarray(cal.mva) - floor), 0.0, 1.0)
    return u[..., 0], u[..., 1]


def volitional_input(up, ud, cal):
    """Eq. 1: u = +r (m - m0)/(m_GAS - m0) if m >= m0, else -r (m - m0)/(m_TA - m0),
    with r = sqrt(u_p^2 + u_d^2) and m = u_p/u_d.
    Paper: Eq. 1 is unbounded past the calibration lines. Here the direction factor is clipped
    to [0, 1] (on or beyond m_GAS counts as pure plantarflexion, as Sec. IV-A describes) and
    u to [-1, 1]."""
    up, ud = np.asarray(up, float), np.asarray(ud, float)
    m0 = cal.m0
    r = np.hypot(up, ud)
    with np.errstate(divide="ignore", invalid="ignore"):
        m = np.where(ud > 0, up / np.where(ud > 0, ud, 1.0), np.inf)
        pf = np.clip((m - m0) / (cal.m_gas - m0), 0.0, 1.0)
        df = np.clip((m - m0) / (cal.m_ta - m0), 0.0, 1.0)
    u = np.where(m >= m0, r * pf, -r * df)
    return np.clip(u, -1.0, 1.0)


def decode(W, cal):
    """One window (ENV_WINDOW x 32, uV) -> (u_p, u_d, u). What the controller runs every tick."""
    up, ud = normalize(envelope(W, cal.gas, cal.ta), cal)
    return float(up), float(ud), float(volitional_input(up, ud, cal))


# ---- calibration --------------------------------------------------------------
def fit_slope(up, ud):
    """Slope u_p/u_d of the line through the origin that best fits the points (total least
    squares, so a near-vertical GAS line fits as well as a near-flat TA line).
    Paper: "slopes of linear fits to data for (u_d, u_p) from calibration"."""
    keep = np.hypot(up, ud) >= MIN_FIT_R
    if keep.sum() < 20:
        raise ValueError(f"only {keep.sum()} samples above {MIN_FIT_R} activation; contraction too weak to fit")
    _, _, vt = np.linalg.svd(np.column_stack([ud[keep], up[keep]]), full_matrices=False)
    d_ud, d_up = np.abs(vt[0])
    return float(min(d_up / max(d_ud, 1e-6), 1000.0))


def calibrate(rest, gas_trial, ta_trial, gas, ta, step=20):
    """rest, gas_trial, ta_trial: raw EMG (samples x 32, uV) from the three calibration
    phases -> Calibration.
    Paper: 10 s relaxed standing sets the baseline; one second of maximal GAS contraction
    (half-step forward) and of TA contraction (neutral stance) set MVA and the slopes. MVA is
    taken here as the highest 1 s average of the envelope in each trial."""
    e_rest = envelope_series(rest, gas, ta, step)
    e_gas = envelope_series(gas_trial, gas, ta, step)
    e_ta = envelope_series(ta_trial, gas, ta, step)
    n1s = max(1, FS // step)

    def best_1s(x):
        return float(np.convolve(x, np.ones(n1s) / n1s, "valid").max()) if len(x) >= n1s else float(x.max())

    cal = Calibration(
        gas=gas,
        ta=ta,
        rest_mean=e_rest.mean(0).tolist(),
        rest_sd=e_rest.std(0).tolist(),
        mva=[best_1s(e_gas[:, 0]), best_1s(e_ta[:, 1])],
        m_gas=0.0,
        m_ta=0.0,
        created=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    floor = np.asarray(cal.rest_mean) + 2 * np.asarray(cal.rest_sd)
    for i, name in enumerate(("GAS", "TA")):
        if cal.mva[i] <= 1.5 * floor[i]:
            raise ValueError(
                f"{name} MVA ({cal.mva[i]:.1f} uV) is barely above rest "
                f"({floor[i]:.1f} uV); check the electrodes and channel numbers"
            )
    cal.m_gas = fit_slope(*normalize(e_gas, cal))
    cal.m_ta = fit_slope(*normalize(e_ta, cal))
    if not cal.m_gas > cal.m_ta:
        raise ValueError(
            f"m_GAS ({cal.m_gas:.3f}) must be steeper than m_TA ({cal.m_ta:.3f}); "
            "GAS and TA channels swapped, or heavy cross-talk"
        )
    return cal


# ---- offline replay -------------------------------------------------------------
def replay(path, cal, step=20):
    """Run a .xipp recording through the same pipeline -> (ts, up, ud, u), one row per step."""
    from summit_record import load_xipp

    ts, X = load_xipp(path)
    env = envelope_series(X, cal.gas, cal.ta, step)
    up, ud = normalize(env, cal)
    return ts[ENV_WINDOW - 1 :: step][: len(env)], up, ud, volitional_input(up, ud, cal)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Replay a .xipp recording through the DMC pipeline")
    ap.add_argument("--cal", required=True)
    ap.add_argument("--replay", required=True, help=".xipp file")
    ap.add_argument("--out", default=None, help="CSV to write (default: <recording>_dmc.csv)")
    a = ap.parse_args()
    cal = Calibration.load(a.cal)
    ts, up, ud, u = replay(a.replay, cal)
    out = a.out or a.replay.rsplit(".", 1)[0] + "_dmc.csv"
    np.savetxt(
        out,
        np.column_stack([ts, up, ud, u, THETA_MAX_DEG * u]),
        delimiter=",",
        header="summit_ts,u_p,u_d,u,theta_eq_deg",
        comments="",
        fmt="%.6g",
    )
    print(
        f"{len(u)} rows -> {out} | u: min {u.min():+.2f} max {u.max():+.2f} | "
        f"time u>0.1: {np.mean(u > 0.1) * 100:.0f}%  u<-0.1: {np.mean(u < -0.1) * 100:.0f}%"
    )
