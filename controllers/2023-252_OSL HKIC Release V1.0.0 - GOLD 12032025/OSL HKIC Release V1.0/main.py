"""
Main file for the Locolab's Hybrid Kinematic Impedance Controller for OSL v2.

Note: You must have the HKIC_Simple_OSL.so library available for this script to run.
    Place it in either the same directory or update the 'library_path' argument below.

Kevin Best, Elissa Cimino
12/2/2025
Version 1.0
"""

import os

import numpy as np
from datetime import datetime
from HKIC_simple_osl import HKIC_Simple_OSL, eControlMode, eClassificationMode
from opensourceleg.robots.osl import OpenSourceLeg
from opensourceleg.actuators.base import CONTROL_MODES
from opensourceleg.actuators.dephy import DephyActuator
from opensourceleg.logging.logger import Logger
from opensourceleg.math.math import SaturatingRamp
from opensourceleg.sensors.encoder import AS5048B
from opensourceleg.sensors.loadcell import NBLoadcellDAQ
from opensourceleg.sensors.imu import LordMicrostrainIMU
from opensourceleg.utilities.softrealtimeloop import SoftRealtimeLoop
from shared.OSL_impedance_compensation import get_bias_angle, get_torque_error_friction

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

###### CONFIGURATION ######
LOADCELL_MATRIX = np.array([
    (14.902139, -1587.784287, 17.201291, 35.723768, -23.409902, 1613.62221),
    (-9.090427, 901.670625, -6.08154, -1848.437314, -5.533857, 947.600638),
    (-986.297204, -5.834769, -988.171548, 11.179974, -1001.015961, -4.970783),
    (20.890568, 0.892102, 0.031495, -0.88304, -21.28072, 0.058474),
    (-11.75241, 0.388692, 24.450053, 0.510546, -11.942883, -1.090377),
    (-0.37403, -23.224273, 0.282398, -24.649759, 0.65524, -26.588633)])
GEAR_RATIO = 9 * (83 / 18)  # Gear ratio of Dephy actuator

LOOP_FREQUENCY = 200  #Hz
CONTROL_LIBRARY_PATH = os.path.abspath(__file__)

USER_MASS = 86 # kg
WALK_INCLINE = 0 # deg [-10, 10]
WALKING_SPEED = 0.6 # m/s [0.6, 1.4]
STAIR_INCLINE = 19 # deg [19, 30]
STAIR_ASCEND = True # True: going up stairs, False: going down stairs
CONTROL_MODE = eControlMode.PEG # Selected mode for manual classification
CLASSIFICATION_MODE = eClassificationMode.MANUAL # Automatic or manual control mode selection (Stair only available in manual)
ENABLE_FRICTION_COMPENSATION = True

# Create timestamp for log file name
LOG_NAME = f'./OSL_Log_{timestamp}'
OFFLINE = False
###########################

osl = OpenSourceLeg(
        tag="opensourceleg",
        actuators={
            "knee": DephyActuator("knee", offline=OFFLINE, port="/dev/ttyACM0", frequency=LOOP_FREQUENCY, gear_ratio=GEAR_RATIO),
            "ankle": DephyActuator("ankle", offline=OFFLINE, port="/dev/ttyACM1", frequency=LOOP_FREQUENCY, gear_ratio=GEAR_RATIO),
        },
        sensors={
            "loadcell": NBLoadcellDAQ(offline = OFFLINE, tag="loadcell", calibration_matrix = LOADCELL_MATRIX, excitation_voltage=5.0, amp_gain=[34] * 3 + [151] * 3, spi_bus=1),
            "thighIMU": LordMicrostrainIMU(offline = OFFLINE, port=r"/dev/ttyAMA1", frequency=LOOP_FREQUENCY, update_timeout=500, max_packets=0),
            "joint_encoder_knee": AS5048B(offline = OFFLINE, tag="joint_encoder_knee", bus='/dev/i2c-2', A1_adr_pin=False,
                                          A2_adr_pin=False, zero_position=0, enable_diagnostics=False),
            "joint_encoder_ankle": AS5048B(offline = OFFLINE, tag="joint_encoder_ankle", bus='/dev/i2c-3', A1_adr_pin=False,
                                          A2_adr_pin=False, zero_position=0, enable_diagnostics=False),
        },
    )

def knee_homing_complete():
    osl.joint_encoder_knee.update()
    osl.joint_encoder_knee.zero_position = osl.joint_encoder_knee.counts
    print("Knee homing complete!")

def ankle_homing_complete():
    osl.joint_encoder_ankle.update()
    osl.joint_encoder_ankle.zero_position = osl.joint_encoder_ankle.counts - osl.joint_encoder_ankle.deg_to_counts(30)
    print("Ankle homing complete!")

startupRamp = SaturatingRamp(loop_frequency=LOOP_FREQUENCY, ramp_time=2.5)

hkic = HKIC_Simple_OSL(LOOP_FREQUENCY, CONTROL_LIBRARY_PATH)
hkic.inputs.controlParameters.userMass_kg = USER_MASS
hkic.inputs.controlParameters.controlMode = CONTROL_MODE.value
hkic.inputs.controlParameters.classificationMode = CLASSIFICATION_MODE.value
hkic.inputs.controlParameters.inputSpeed_mps = WALKING_SPEED
hkic.inputs.controlParameters.inputIncline_deg = WALK_INCLINE
hkic.inputs.controlParameters.stairIncline_deg = STAIR_INCLINE
hkic.inputs.controlParameters.stair_ascent = STAIR_ASCEND

datalog = Logger(file_name=LOG_NAME)

hkic.log_all_outputs(datalog)
hkic.log_all_inputs(datalog)
datalog.track_attributes(osl.knee, ["output_torque"])
datalog.track_attributes(osl.ankle, ["output_torque"])

with osl, datalog:
    osl.home(callbacks={'knee':knee_homing_complete, 'ankle':ankle_homing_complete})

    input("\nPress any key to begin calibrating the load cell:")
    osl.loadcell.calibrate()

    # Wait for user start
    input('\nPress any key to begin walking:') 

    osl.update()
    osl.knee.set_control_mode(CONTROL_MODES.IMPEDANCE)
    osl.knee.set_output_impedance(k = 0, b = 0)
    osl.knee.set_output_position(osl.knee.output_position)
    osl.ankle.set_control_mode(CONTROL_MODES.IMPEDANCE)
    osl.ankle.set_output_impedance(k = 0, b = 0)
    osl.ankle.set_output_position(osl.ankle.output_position)

    loop = SoftRealtimeLoop(dt = 1/LOOP_FREQUENCY)
    for time in loop:
        osl.update()
        smoothTorqueRamp = startupRamp.update(True) 
        
        # Get IMU data
        imu_data = osl.sensors["thighIMU"]

        # Convert radians to degrees for encoder angles
        hkic.inputs.hardware.sensors.kneeMotorEncoder_deg = np.rad2deg(osl.knee.output_position)  ## deg
        hkic.inputs.hardware.sensors.ankleMotorEncoder_deg = np.rad2deg(osl.ankle.output_position)  ## deg
        hkic.inputs.hardware.sensors.kneeJointEncoder_deg = np.rad2deg(-osl.sensors["joint_encoder_knee"].position)
        hkic.inputs.hardware.sensors.ankleJointEncoder_deg = np.rad2deg(-osl.sensors["joint_encoder_ankle"].position)
        
        # IMU angle data (convert to degrees and add offset)
        hkic.inputs.hardware.sensors.thighIMU.Angles.X_deg = np.rad2deg(imu_data.roll + 0.6873)  ## to add OSL_Hardware mounting offset
        hkic.inputs.hardware.sensors.thighIMU.Angles.Y_deg = np.rad2deg(imu_data.pitch)  
        hkic.inputs.hardware.sensors.thighIMU.Angles.Z_deg = np.rad2deg(imu_data.yaw)  
        
        # IMU acceleration data (already in m/s^2)
        hkic.inputs.hardware.sensors.thighIMU.Accel.X_mps = imu_data.acc_x  ## m/s^2
        hkic.inputs.hardware.sensors.thighIMU.Accel.Y_mps = imu_data.acc_y  ## m/s^2
        hkic.inputs.hardware.sensors.thighIMU.Accel.Z_mps = imu_data.acc_z  ## m/s^2
        
        # IMU velocity data (already in m/s)
        hkic.inputs.hardware.sensors.thighIMU.Velocity.X_radPerSec = imu_data.vel_x  ## m/s
        hkic.inputs.hardware.sensors.thighIMU.Velocity.Y_radPerSec = imu_data.vel_y  ## m/s
        hkic.inputs.hardware.sensors.thighIMU.Velocity.Z_radPerSec = imu_data.vel_z  ## m/s
        
        # Load cell data
        loadcell = osl.sensors["loadcell"]
        hkic.inputs.hardware.sensors.LoadCellStruct.xForce_N = loadcell.fx
        hkic.inputs.hardware.sensors.LoadCellStruct.yForce_N = loadcell.fy
        hkic.inputs.hardware.sensors.LoadCellStruct.zForce_N = -(loadcell.fz)
        hkic.inputs.hardware.sensors.LoadCellStruct.xMoment_Nm = loadcell.mx
        hkic.inputs.hardware.sensors.LoadCellStruct.yMoment_Nm = loadcell.my
        hkic.inputs.hardware.sensors.LoadCellStruct.zMoment_Nm = loadcell.mz
        hkic.inputs.hardware.time = time
    
        outputs = hkic.run()
        
        kneeBiasAngle = get_bias_angle(outputs.kneeTau0)
        ankleBiasAngle = get_bias_angle(outputs.ankleTau0)

        kneeTorque_desired = outputs.kneeK1 * (outputs.kneeCommandsOut.theta_d_rad - osl.knee.output_position) \
                            + outputs.kneeB1 * (outputs.kneeCommandsOut.theta_dot_d_rps - osl.knee.output_velocity) \
                            + outputs.kneeK1 * kneeBiasAngle
        ankleTorque_desired = outputs.ankleK1 * (outputs.ankleCommandsOut.theta_d_rad - osl.ankle.output_position) \
                            + outputs.ankleB1 * (outputs.ankleCommandsOut.theta_dot_d_rps - osl.ankle.output_velocity) \
                            + outputs.ankleK1 * ankleBiasAngle
        kneeFrictionTorqueError = get_torque_error_friction(osl.knee.motor_velocity * 9, kneeTorque_desired) if ENABLE_FRICTION_COMPENSATION else 0
        ankleFrictionTorqueError = get_torque_error_friction(osl.ankle.motor_velocity * 9, ankleTorque_desired) if ENABLE_FRICTION_COMPENSATION else 0

        osl.knee.set_output_impedance(k = outputs.kneeK1 * smoothTorqueRamp, b = outputs.kneeB1 * smoothTorqueRamp)
        osl.ankle.set_output_impedance(k = outputs.ankleK1 * smoothTorqueRamp, b = outputs.ankleB1 * smoothTorqueRamp)

        osl.knee.set_output_position(outputs.kneeCommandsOut.theta_d_rad + kneeBiasAngle + kneeFrictionTorqueError/outputs.kneeK1)
        osl.ankle.set_output_position(outputs.ankleCommandsOut.theta_d_rad + ankleBiasAngle + ankleFrictionTorqueError/outputs.ankleK1)

        datalog.update()
        datalog.flush_buffer()

        print("Time: {:.2f}, Fz: {:.2f}, Walk Phase: {:.2f}, Sit/Stand Phase: {:.2f}, Stair Phase: {:.2f}, "
              "Active Mode: {:.0f}, Classified Mode: {:.0f}, Knee Torque: {:.2f}, Ankle Torque: {:.2f}".format(
                time,
                osl.loadcell.fz,
                outputs.phaseVariable_walk,
                outputs.phaseVariable_sitStand,
                outputs.phaseVariable_stair,
                outputs.activeControlMode,
                outputs.classifiedControlMode,
                outputs.kneeCommandsOut.tau_Nm,
                outputs.ankleCommandsOut.tau_Nm,
            ), end="\r")

print("\n")




