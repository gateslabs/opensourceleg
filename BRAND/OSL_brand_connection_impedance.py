#!/usr/bin/env python3
"""
OSL Controller Script with BRAND Socket Interface — IMPEDANCE variant (Posh DMC)

Fork of OSL_brand_connection. Identical transport/homing/loop; the ONLY change is the
per-joint control mode: this runs the Dephy firmware in IMPEDANCE mode (a soft spring
about a commanded equilibrium) instead of POSITION mode (a rigid tracker).

The Posh Direct Myoelectric Controller decodes an EQUILIBRIUM angle; the leg renders
    tau = K * (theta_eq - theta)                (firmware impedance — no custom torque code)
so each received setpoint is the spring's equilibrium, not a hard target.

  IMPEDANCE K — WEIGHT-NORMALIZED. Posh's stiffness is 0.05 Nm/deg PER KG of BODY WEIGHT,
  not a flat 0.05 Nm/deg (that omission made it ~70x too soft and the leg sagged under
  gravity). set_output_impedance takes Nm/RAD, so:
      K_RAD = 0.05 * (180/pi) * weight_kg = 2.8648 * weight_kg   (e.g. 80 kg -> 229.2 Nm/rad)
  Pass the SUBJECT's weight with --weight-kg, or override K directly with --k-rad.
  set_output_impedance auto-scales K_RAD to the motor by gear_ratio**2.

  DAMPING b — the paper uses 0, but at the weight-scaled K a stiff spring can ring on real
  hardware, so a small b (~0.02-0.03*K, i.e. a few Nm/rad/s) is used for stability. Tune
  with --damping: raise until any buzz/oscillation stops, then back off ~20%; too high
  feels laggy. First powered run seated/supported, ready to E-stop.

CALIBRATION (copied from the Locolab HKIC controller):
  * Joint zeroing: home() drives each joint to a mechanical HARD STOP, then home_offset_rad
    shifts the zero to the ANATOMICAL neutral (knee stop == full extension, offset 0; the
    ankle stop is ~30 deg off neutral, so HKIC uses 30 deg). After homing, set_output_position(0)
    should hold the joint at neutral. *** VERIFY the ankle offset SIGN + MAGNITUDE, and the homing
    direction, on the actual OSL before powering — a wrong sign sends the ankle to the wrong side. ***
  * Smooth engagement: enter impedance LIMP (k=0) with the equilibrium at the CURRENT position,
    then a 2.5 s SaturatingRamp brings stiffness 0 -> K so the leg eases in instead of snapping.

Receives joint EQUILIBRIUM setpoints from the BRAND `osl` node via UDP (radians, actuator
output frame — the BRAND node does the 0-1 -> radian mapping via its control_ranges), and
sends actual joint positions back.

Use `--mode` to drive only the knee, only the ankle, or both. Tune the spring live with
`--weight-kg` / `--k-rad` / `--damping` (no code edit needed for the first powered runs).

Network protocol:
    UDP msgpack on  --listen-port (default 9000)
    UDP msgpack to  --brand-ip:--send-port (default 9001)

Incoming payload (flat msgpack dict from the BRAND osl node):
    { "knee": <rad>, "ankle_flexion": <rad>, ... }   (extra keys ignored)

Outgoing payload (only active joints included):
    { "knee": [rad], "ankle_flexion": [rad] }
"""

import argparse
import math
import socket
import threading
import time
from contextlib import ExitStack

import msgpack
import msgpack_numpy as m
import numpy as np

m.patch()

from opensourceleg.actuators.base import CONTROL_MODES
from opensourceleg.actuators.dephy import DephyActuator
from opensourceleg.math.math import SaturatingRamp
from opensourceleg.utilities import SoftRealtimeLoop

# Edit these to change defaults without touching the CLI. The matching
# --mode / --knee-port / --ankle-port flags still override at runtime.
#   DEFAULT_MODE: "knee", "ankle", or "both"
DEFAULT_MODE = "both"

# Bind by the STABLE physical-socket path (/dev/serial/by-path/...), NOT /dev/ttyACMx
# (which floats with USB enumeration order). Device IDs are now unique (knee/ankle), so
# flexsea can tell the two Actpacks apart. This is the confirmed-working mapping:
DEFAULT_ANKLE_PORT = "/dev/serial/by-path/platform-xhci-hcd.1-usb-0:1:1.0"
DEFAULT_KNEE_PORT = "/dev/serial/by-path/platform-xhci-hcd.0-usb-0:1:1.0"

BRAND_IP = "127.0.0.1"
LISTEN_PORT = 9000
SEND_PORT = 9001
FREQUENCY = 200
GEAR_RATIO = 9 * 83 / 18

# Posh impedance is WEIGHT-NORMALIZED: 0.05 Nm/deg per kg of body weight.
K_PER_KG = 0.05 * (180.0 / math.pi)  # = 2.8648 Nm/rad per kg
DEFAULT_WEIGHT_KG = 80.0  # SUBJECT body weight (kg); override with --weight-kg
DEFAULT_DAMPING = 4.0  # Nm/rad/s — small b for stability at the stiff K (paper: 0)
RAMP_TIME_S = 2.5  # HKIC-style startup ramp: impedance 0 -> full over this
PRINT_INTERVAL_S = 0.1

# Per-joint defaults. `payload_key` must match what the BRAND osl node sends (its `dof_mapping`).
# `safety_rad` = hard clamp on every commanded position. `neutral_rad` = rest posture held before
# packets arrive. `home_offset_rad` = HKIC joint-zeroing: home() drives to a hard stop, this offset
# moves the zero from the stop to ANATOMICAL neutral. *** VERIFY the ankle offset (sign + magnitude)
# and homing direction on the real OSL: after homing, set_output_position(0) must sit at neutral. ***
JOINT_DEFAULTS = {
    "knee": {
        "payload_key": "knee",
        "port": DEFAULT_KNEE_PORT,
        "neutral_rad": 1.5708,  # 90 deg — seated rest (bent)
        "safety_rad": (0.0, 2.15),  # covers the 110 deg (1.9199) knee ROM (seated); was 2.15 for 120
        "home_offset_rad": 0.0,  # knee hard stop = full extension = zero (HKIC: knee zero = homed)
    },
    "ankle": {
        "payload_key": "ankle_flexion",
        "port": DEFAULT_ANKLE_PORT,
        "neutral_rad": 0.0,  # 0 deg — rest
        "safety_rad": (-0.4, 1.2),  # ankle +/-15 deg ROM sits inside; unchanged
        "home_offset_rad": 0.5236,  # ~30 deg stop->neutral (HKIC). *** VERIFY SIGN/MAG ON HARDWARE ***
    },
}

MODE_TO_JOINTS = {
    "knee": ["knee"],
    "ankle": ["ankle"],
    "both": ["knee", "ankle"],
}


def extract_scalar(value) -> float | None:
    if isinstance(value, np.ndarray):
        return float(value.flat[0]) if value.size else None
    if isinstance(value, (list, tuple)):
        return float(value[0]) if value else None
    if isinstance(value, (int, float, np.floating, np.integer)):
        return float(value)
    return None


def main():
    parser = argparse.ArgumentParser(description="OSL BRAND Controller (IMPEDANCE / Posh DMC)")
    parser.add_argument(
        "--mode",
        default=DEFAULT_MODE,
        choices=list(MODE_TO_JOINTS),
        help=f"Which joints to drive (default: {DEFAULT_MODE})",
    )
    parser.add_argument("--brand-ip", default=BRAND_IP)
    parser.add_argument("--listen-port", type=int, default=LISTEN_PORT)
    parser.add_argument("--send-port", type=int, default=SEND_PORT)
    parser.add_argument("--frequency", type=int, default=FREQUENCY)
    parser.add_argument("--knee-port", default=JOINT_DEFAULTS["knee"]["port"])
    parser.add_argument("--ankle-port", default=JOINT_DEFAULTS["ankle"]["port"])
    parser.add_argument(
        "--weight-kg",
        type=float,
        default=DEFAULT_WEIGHT_KG,
        help="Subject body weight (kg). K_rad = 2.8648 * weight_kg (Posh K is weight-normalized).",
    )
    parser.add_argument(
        "--k-rad", type=float, default=None, help="Override K_rad directly (Nm/rad). Default: 2.8648 * weight_kg."
    )
    parser.add_argument(
        "--damping",
        type=float,
        default=DEFAULT_DAMPING,
        help="Impedance damping b (Nm/rad/s). ~0.02-0.03*K; raise only if it rings.",
    )
    args = parser.parse_args()

    # Weight-normalized K unless explicitly overridden with --k-rad.
    k_rad = args.k_rad if args.k_rad is not None else K_PER_KG * args.weight_kg

    active: list[str] = MODE_TO_JOINTS[args.mode]
    port_for = {"knee": args.knee_port, "ankle": args.ankle_port}
    payload_key = {j: JOINT_DEFAULTS[j]["payload_key"] for j in active}
    safety = {j: JOINT_DEFAULTS[j]["safety_rad"] for j in active}

    setpoints: dict[str, float] = {j: JOINT_DEFAULTS[j]["neutral_rad"] for j in active}
    setpoints_lock = threading.Lock()
    running = threading.Event()
    running.set()

    def reader_thread():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", args.listen_port))
        sock.settimeout(0.1)

        while running.is_set():
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            # Drain to the freshest packet so we never act on stale setpoints.
            sock.setblocking(False)
            try:
                while True:
                    data, _ = sock.recvfrom(65535)
            except BlockingIOError:
                pass
            finally:
                sock.settimeout(0.1)

            try:
                decoded = msgpack.unpackb(data)
            except Exception:
                continue
            if not isinstance(decoded, dict):
                continue

            with setpoints_lock:
                for joint in active:
                    key = payload_key[joint]
                    if key not in decoded:
                        continue
                    val = extract_scalar(decoded[key])
                    if val is None:
                        continue
                    lo, hi = safety[joint]
                    setpoints[joint] = float(np.clip(val, lo, hi))

        sock.close()

    threading.Thread(target=reader_thread, daemon=True).start()

    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    send_target = (args.brand_ip, args.send_port)

    print(f"Active joints: {active}  (mode={args.mode})")
    print(
        f"IMPEDANCE mode: K={k_rad:.4f} Nm/rad (weight {args.weight_kg:.0f} kg), "
        f"b={args.damping:.4f} Nm/rad/s (tau = K*(theta_eq - theta))"
    )
    print(f"Listening on :{args.listen_port}, sending to {args.brand_ip}:{args.send_port}")

    try:
        with ExitStack() as stack:
            actuators: dict[str, DephyActuator] = {}
            for joint in active:
                actuator = DephyActuator(
                    tag=joint,
                    firmware_version="7.2.0",
                    port=port_for[joint],
                    gear_ratio=GEAR_RATIO,
                    frequency=args.frequency,
                )
                stack.enter_context(actuator)
                actuators[joint] = actuator

            # Home each joint to its hard stop, then zero at the anatomical neutral via the
            # per-joint home_offset_rad (HKIC joint-position calibration).
            for joint, actuator in actuators.items():
                off = JOINT_DEFAULTS[joint]["home_offset_rad"]
                print(f"Homing {joint} (zeroing {off:+.3f} rad from the hard stop -> neutral)...")
                actuator.home(output_position_offset=off)
            input("Homing complete. Press Enter to start...")

            # --- IMPEDANCE setup, HKIC-style: enter LIMP (k=0) with the equilibrium at the CURRENT
            # position, then ramp stiffness in over RAMP_TIME_S so the leg engages gently. ---
            for actuator in actuators.values():
                actuator.update()
                actuator.set_control_mode(CONTROL_MODES.IMPEDANCE)
                actuator.set_impedance_cc_pidf_gains()  # inner current-ctrl PID (defaults)
                actuator.set_output_impedance(k=0.0, b=0.0)
                actuator.set_output_position(actuator.output_position)  # equilibrium = current (no jump)

            startup_ramp = SaturatingRamp(loop_frequency=args.frequency, ramp_time=RAMP_TIME_S)
            clock = SoftRealtimeLoop(dt=1 / args.frequency)
            last_print = 0.0

            for _ in clock:
                ramp = startup_ramp.update(True)  # 0 -> 1 over RAMP_TIME_S
                with setpoints_lock:
                    targets = dict(setpoints)

                positions: dict[str, float] = {}
                for joint, actuator in actuators.items():
                    actuator.update()
                    actuator.set_output_impedance(k=k_rad * ramp, b=args.damping * ramp)
                    actuator.set_output_position(targets[joint])  # equilibrium, not a hard target
                    positions[joint] = float(actuator.output_position)

                send_sock.sendto(
                    msgpack.packb({payload_key[j]: [positions[j]] for j in active}),
                    send_target,
                )

                now = time.monotonic()
                if now - last_print >= PRINT_INTERVAL_S:
                    last_print = now
                    status = "  ".join(f"{j}: {positions[j]:+.3f} (eq {targets[j]:+.3f})" for j in active)
                    print(status, end="\r")
    finally:
        running.clear()
        send_sock.close()
        print("\nShutdown complete.")


if __name__ == "__main__":
    main()
