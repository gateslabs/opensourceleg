"""
Hybrid Kinematic Impedance Controller OSL Implementation from Umich Locolab. 

Kevin Best, Elissa Cimino
Updated: 2025-09-29
"""

import os
from shared.base_controller import BaseController
from interface_HKIC_Simple_OSL import define_interface_types
from opensourceleg.control.compiled import CompiledController
from enum import Enum

class HKIC_Simple_OSL(BaseController):
    """
    Implementation of the LocoLab's walk/sit/stand and stair controller on the OSL v2.
    """
    
    def _define_controller(self) -> CompiledController:
        """
        Define the compiled controller interface.
        
        Returns:
            CompiledController: The configured compiled controller
        """
        compiled_lib = CompiledController(  
            library_name="HKIC_Simple_OSL.so",
            library_path=os.path.expanduser(self._lib_path),
            main_function_name="HKIC_Simple_OSL",
            initialization_function_name="HKIC_Simple_OSL_initialize",
            cleanup_function_name="HKIC_Simple_OSL_terminate",
        )

        # Define Types
        define_interface_types(compiled_lib)

        # Define Inputs
        compiled_lib.define_inputs(
            [
                ("hardware", compiled_lib.types.OSL_Hardware),
                ("controlParameters", compiled_lib.types.c_HKIC_Simple_OSL_ControllerPar)
            ]
        )

        # Define Outputs
        compiled_lib.define_outputs(
            [
            ("kneeCommandsOut", compiled_lib.types.ActuatorCommandsPD),
            ("ankleCommandsOut", compiled_lib.types.ActuatorCommandsPD),
            ("kneeK1", compiled_lib.types.c_double),
            ("kneeB1", compiled_lib.types.c_double),
            ("ankleK1", compiled_lib.types.c_double),
            ("ankleB1", compiled_lib.types.c_double),
            ("kneeTau0", compiled_lib.types.c_double),
            ("ankleTau0", compiled_lib.types.c_double),
            ("dt", compiled_lib.types.c_double),
            ("robustFC", compiled_lib.types.c_bool),
            ("activeControlMode", compiled_lib.types.c_uint8),
            ("classifiedControlMode", compiled_lib.types.c_uint8),
            ("phaseVariable_sitStand", compiled_lib.types.c_double),
            ("phaseVariable_walk", compiled_lib.types.c_double),
            ("phaseVariable_stair", compiled_lib.types.c_double),
            ("kneeVirtualConstraint_walk", compiled_lib.types.c_double),
            ("ankleVirtualConstraint_walk", compiled_lib.types.c_double),
            ("kneeVirtualConstraint_stair", compiled_lib.types.c_double),
            ("ankleVirtualConstraint_stair", compiled_lib.types.c_double),
            ]
        )

        return compiled_lib

    def set_default_inputs(self):
        """
        Set default input parameters for the controller.

        """
        # Set loop frequency
        self._compiledLib.inputs.hardware.nominalLoopFreq = self._loop_frequency

        # Controller parameters
        self._compiledLib.inputs.controlParameters.classificationMode = eClassificationMode.MANUAL.value
        self._compiledLib.inputs.controlParameters.controlMode = eControlMode.SIT_STAND.value
        self._compiledLib.inputs.controlParameters.inputSpeed_mps = 1.0
        self._compiledLib.inputs.controlParameters.inputIncline_deg = 0.0
        self._compiledLib.inputs.controlParameters.userMass_kg = 70.0
        self._compiledLib.inputs.controlParameters.stairIncline_deg = 19.0
        self._compiledLib.inputs.controlParameters.stair_ascent = True

class eControlMode(Enum):
    """Control mode enumeration"""
    PEG = 1
    SIT_STAND = 2
    WALK = 3
    STAIR = 4

class eClassificationMode(Enum):
    """Classification mode enumeration"""
    MANUAL = 0
    AUTOMATIC = 2