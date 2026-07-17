import numpy as np

from opensourceleg.actuators.dephy import DephyActuator
from opensourceleg.logging.logger import Logger
from opensourceleg.sensors.loadcell import DephyLoadcellAmplifier, NBLoadcellDAQ
from opensourceleg.utilities import SoftRealtimeLoop

FREQUENCY = 200
DT = 1 / FREQUENCY

LOADCELL_CALIBRATION_MATRIX_M3564F = np.array([
    (14.902139, -1587.784287, 17.201291, 35.723768, -23.409902, 1613.62221),
    (-9.090427, 901.670625, -6.08154, -1848.437314, -5.533857, 947.600638),
    (-986.297204, -5.834769, -988.171548, 11.179974, -1001.015961, -4.970783),
    (20.890568, 0.892102, 0.031495, -0.88304, -21.28072, 0.058474),
    (-11.75241, 0.388692, 24.450053, 0.510546, -11.942883, -1.090377),
    (-0.37403, -23.224273, 0.282398, -24.649759, 0.65524, -26.588633),
])

LOADCELL_CALIBRATION_MATRIX_M3554E = np.array([
    (-943.401, 4.143, 8.825, -16.57, 952.216, 10.892),
    (539.853, 14.985, -1111.656, -0.812, 546.9, -18.949),
    (13.155, 533.082, -4.582, 534.843, 10.827, 536.327),
    (0.138, -10.419, 0.202, 0.14, 0.063, 10.518),
    (-0.075, 6.213, -0.239, -12.094, 0.181, 6.156),
    (-19.912, 0.082, -20.347, 0.022, -19.486, 0.013),
])


def demo_loadcell_i2c(loadcell_logger, clock):
    loadcell = DephyLoadcellAmplifier(
        calibration_matrix=LOADCELL_CALIBRATION_MATRIX_M3564F,
        tag="loadcell",
        amp_gain=125,
        exc=5,
        bus=1,
        i2c_address=102,
    )

    loadcell_logger.track_function(lambda: loadcell.fx, "Fx")
    loadcell_logger.track_function(lambda: loadcell.fy, "Fy")
    loadcell_logger.track_function(lambda: loadcell.fz, "Fz")
    loadcell_logger.track_function(lambda: loadcell.mx, "Mx")
    loadcell_logger.track_function(lambda: loadcell.my, "My")
    loadcell_logger.track_function(lambda: loadcell.mz, "Mz")

    with loadcell:
        loadcell.calibrate(reset=True)
        for t in clock:
            loadcell.update()
            loadcell_logger.info(
                f"Time: {t}; Fx: {loadcell.fx}; Fy: {loadcell.fy}; Fz: {loadcell.fz};"
                f"Mx: {loadcell.mx}; My: {loadcell.my}; Mz: {loadcell.mz};"
            )
            loadcell_logger.update()


def demo_loadcell_actpack(loadcell_logger, clock):
    actpack = DephyActuator(
        port="/dev/ttyACM1",
        gear_ratio=9,
        frequency=FREQUENCY,
        debug_level=0,
        dephy_log=False,
    )

    loadcell = DephyLoadcellAmplifier(
        calibration_matrix=LOADCELL_CALIBRATION_MATRIX_M3564F,
        tag="loadcell",
        amp_gain=125,
        exc=5,
    )
    loadcell_logger.track_function(lambda: loadcell.fx, "Fx")
    loadcell_logger.track_function(lambda: loadcell.fy, "Fy")
    loadcell_logger.track_function(lambda: loadcell.fz, "Fz")
    loadcell_logger.track_function(lambda: loadcell.mx, "Mx")
    loadcell_logger.track_function(lambda: loadcell.my, "My")
    loadcell_logger.track_function(lambda: loadcell.mz, "Mz")

    def get_raw_loadcell_actpack():
        actpack.update()
        return actpack.genvars

    with actpack, loadcell:
        loadcell.calibrate(reset=True, data_callback=get_raw_loadcell_actpack)
        for t in clock:
            loadcell.update(data_callback=get_raw_loadcell_actpack)
            loadcell_logger.info(
                f"Time: {t}; Fx: {loadcell.fx}; Fy: {loadcell.fy}; Fz: {loadcell.fz};"
                f"Mx: {loadcell.mx}; My: {loadcell.my}; Mz: {loadcell.mz};"
            )
            loadcell_logger.update()


def demo_loadcell_nb_daq(loadcell_logger, clock):
    loadcell = NBLoadcellDAQ(
        LOADCELL_CALIBRATION_MATRIX_M3554E,
        tag="loadcell",
        excitation_voltage=5.0,
        amp_gain=[34] * 3 + [151] * 3,
        spi_bus=1,
    )
    loadcell_logger.track_function(lambda: loadcell.fx, "Fx")
    loadcell_logger.track_function(lambda: loadcell.fy, "Fy")
    loadcell_logger.track_function(lambda: loadcell.fz, "Fz")
    loadcell_logger.track_function(lambda: loadcell.mx, "Mx")
    loadcell_logger.track_function(lambda: loadcell.my, "My")
    loadcell_logger.track_function(lambda: loadcell.mz, "Mz")

    with loadcell:
        loadcell.calibrate()
        for t in clock:
            loadcell.update()
            loadcell_logger.info(
                f"Time: {t}; Fx: {loadcell.fx}; Fy: {loadcell.fy}; Fz: {loadcell.fz};"
                f"Mx: {loadcell.mx}; My: {loadcell.my}; Mz: {loadcell.mz};"
            )
            loadcell_logger.update()


if __name__ == "__main__":
    loadcell_logger = Logger(
        log_path="./logs",
        file_name="reading_loadcell_data",
    )

    clock = SoftRealtimeLoop(dt=DT)

    # demo_loadcell_actpack(loadcell_logger=loadcell_logger, clock=clock)
    # demo_loadcell_i2c(loadcell_logger=loadcell_logger, clock = clock)
    demo_loadcell_nb_daq(loadcell_logger=loadcell_logger, clock=clock)
