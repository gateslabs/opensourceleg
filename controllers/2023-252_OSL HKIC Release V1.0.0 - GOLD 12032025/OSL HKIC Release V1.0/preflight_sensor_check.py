"""
HKIC sensor preflight check.

Instantiates every actuator/sensor the HKIC controller (main.py) uses, with the
EXACT same configuration, and prints one live reading from each. Each device is
tested in isolation inside its own try/except, so one failure does NOT block the
rest -- you get a per-device PASS/FAIL summary at the end.

Safety: this script commands NO motion. Actuators are opened and read only.

Run from this directory (next to main.py):
    python3 preflight_sensor_check.py

Config below is copied verbatim from main.py -- keep them in sync if main.py changes.
"""

import time
import traceback

import numpy as np

from opensourceleg.actuators.dephy import DephyActuator
from opensourceleg.sensors.encoder import AS5048B
from opensourceleg.sensors.imu import LordMicrostrainIMU
from opensourceleg.sensors.loadcell import NBLoadcellDAQ

# ------------------------------------------------------------------ #
# CONFIGURATION -- copied verbatim from main.py                      #
# ------------------------------------------------------------------ #
LOADCELL_MATRIX = np.array([
    (14.902139, -1587.784287, 17.201291, 35.723768, -23.409902, 1613.62221),
    (-9.090427, 901.670625, -6.08154, -1848.437314, -5.533857, 947.600638),
    (-986.297204, -5.834769, -988.171548, 11.179974, -1001.015961, -4.970783),
    (20.890568, 0.892102, 0.031495, -0.88304, -21.28072, 0.058474),
    (-11.75241, 0.388692, 24.450053, 0.510546, -11.942883, -1.090377),
    (-0.37403, -23.224273, 0.282398, -24.649759, 0.65524, -26.588633),
])
GEAR_RATIO = 9 * (83 / 18)
LOOP_FREQUENCY = 200  # Hz
OFFLINE = False

# Number of update() calls before reading (lets serial/DAQ buffers fill).
WARMUP_SAMPLES = 5

results = {}  # name -> bool


def check(name, fn):
    """Run one device check in isolation, record PASS/FAIL."""
    print(f"\n=== {name} ===")
    try:
        fn()
        results[name] = True
        print(f"[PASS] {name}")
    except Exception as exc:  # -- we want every failure isolated
        results[name] = False
        print(f"[FAIL] {name}: {exc}")
        traceback.print_exc()


# ------------------------------------------------------------------ #
# Per-device checks                                                   #
# ------------------------------------------------------------------ #
def check_actuator(tag, port):
    def _run():
        act = DephyActuator(
            tag,
            offline=OFFLINE,
            port=port,
            frequency=LOOP_FREQUENCY,
            gear_ratio=GEAR_RATIO,
        )
        with act:
            for _ in range(WARMUP_SAMPLES):
                act.update()
                time.sleep(1 / LOOP_FREQUENCY)
            print(f"  port              = {port}")
            print(f"  motor_position    = {act.motor_position:.4f} rad")
            print(f"  output_position   = {act.output_position:.4f} rad")
            print(f"  motor_velocity    = {act.motor_velocity:.4f} rad/s")
            print(f"  motor_current     = {act.motor_current:.2f} mA")
            print(f"  case_temperature  = {act.case_temperature:.1f} C")

    return _run


def check_encoder(tag, bus):
    def _run():
        enc = AS5048B(
            offline=OFFLINE,
            tag=tag,
            bus=bus,
            A1_adr_pin=False,
            A2_adr_pin=False,
            zero_position=0,
            enable_diagnostics=False,
        )
        with enc:
            for _ in range(3):
                enc.update()
                time.sleep(0.01)
            print(f"  bus       = {bus}")
            print(f"  position  = {enc.position:.4f} rad " f"({np.rad2deg(enc.position):.2f} deg)")

    return _run


def check_loadcell():
    lc = NBLoadcellDAQ(
        offline=OFFLINE,
        tag="loadcell",
        calibration_matrix=LOADCELL_MATRIX,
        excitation_voltage=5.0,
        amp_gain=[34] * 3 + [151] * 3,
        spi_bus=1,
    )
    with lc:
        # NOTE: no calibrate() here -- values are uncalibrated (offset not zeroed).
        # This check only confirms the SPI/DAQ link responds. main.py calibrates
        # at startup with the leg unloaded.
        for _ in range(3):
            lc.update()
            time.sleep(0.01)
        print("  (uncalibrated -- confirms DAQ link only)")
        print(f"  Fx, Fy, Fz = {lc.fx:8.2f}, {lc.fy:8.2f}, {lc.fz:8.2f} N")
        print(f"  Mx, My, Mz = {lc.mx:8.2f}, {lc.my:8.2f}, {lc.mz:8.2f} Nm")


def check_imu():
    imu = LordMicrostrainIMU(
        offline=OFFLINE,
        port=r"/dev/ttyAMA1",
        frequency=LOOP_FREQUENCY,
        update_timeout=500,
        max_packets=0,
    )
    with imu:
        for _ in range(WARMUP_SAMPLES):
            imu.update()
            time.sleep(1 / LOOP_FREQUENCY)
        print(f"  roll, pitch, yaw = {imu.roll:.4f}, {imu.pitch:.4f}, {imu.yaw:.4f} rad")
        print(f"  acc  x, y, z     = {imu.acc_x:.3f}, {imu.acc_y:.3f}, {imu.acc_z:.3f} m/s^2")
        print(f"  vel  x, y, z     = {imu.vel_x:.3f}, {imu.vel_y:.3f}, {imu.vel_z:.3f} rad/s")


# ------------------------------------------------------------------ #
# Run all checks (same order as the recommended preflight sequence)  #
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    print("HKIC sensor preflight -- commands no motion, reads only.\n" f"OFFLINE = {OFFLINE}")

    check("Actuator: knee  (ttyACM0)", check_actuator("knee", "/dev/ttyACM0"))
    check("Actuator: ankle (ttyACM1)", check_actuator("ankle", "/dev/ttyACM1"))
    check("Encoder: joint_encoder_knee  (i2c-2)", check_encoder("joint_encoder_knee", "/dev/i2c-2"))
    check("Encoder: joint_encoder_ankle (i2c-3)", check_encoder("joint_encoder_ankle", "/dev/i2c-3"))
    check("Loadcell: NBLoadcellDAQ (spi_bus=1)", check_loadcell)
    check("IMU: LordMicrostrain thighIMU (ttyAMA1)", check_imu)

    # ---- Summary ----
    print("\n" + "=" * 50)
    print("PREFLIGHT SUMMARY")
    print("=" * 50)
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    n_pass = sum(results.values())
    n_total = len(results)
    print(f"\n{n_pass}/{n_total} devices OK")
    if n_pass == n_total:
        print("All HKIC sensors responding. Safe to proceed to main.py.")
    else:
        print("One or more devices failed -- resolve before running main.py.")
