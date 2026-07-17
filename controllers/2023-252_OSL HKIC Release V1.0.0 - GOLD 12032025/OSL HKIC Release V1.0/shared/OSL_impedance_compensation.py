"""
OSL Impedance Compensation Module

This module provides functions for compensating for the hardware dynamics in the Open Source Leg (OSL)
drivetrain, specifically addressing belt spring nonlinearity and friction effects that degrade
impedance control performance.

The OSL drivetrain exhibits nonlinear behavior due to belt elasticity and friction, which
causes deviations from ideal impedance control. This module implements compensation algorithms
to mitigate these effects and improve control accuracy.

Functions:
    get_bias_angle(tau_0): Calculates bias angle to compensate for belt spring nonlinearity
    get_torque_error_friction(joint, tau_j_des): Calculates friction compensation torque

Author: Kevin Best
Date: 2024
References: 
    T. K. Best, G. C. Thomas, S. R. Ayyappan, R. D. Gregg and E. J. Rouse, 
    "A Compensated Open-Loop Impedance Controller Evaluated on the Second-Generation
    Open-Source Leg Prosthesis," in IEEE/ASME Transactions on Mechatronics, 
    doi: 10.1109/TMECH.2024.3508469
"""

import numpy as np

# Smooth sign function to avoid discrete jumps in output for friction terms
smooth_sign = lambda x, alpha: x/(abs(x)+alpha)


def get_bias_angle(tau_0):
    """
    Calculate the bias angle to compensate for belt spring nonlinearity in the OSL drivetrain.
    
    The OSL uses timing belts that exhibit nonlinear elastic behavior, causing the relationship
    between commanded position and actual output torque to deviate from ideal. This function
    compensates for the belt deflection that occurs under load.
    
    Args:
        tau_0 (float): Expected torque in Nm (joint space)
        
    Returns:
        float: Bias angle in radians to add to the equilibrium angle command
        
    Notes:
        - Command as: osl.(joint).set_output_position(eqAngle_rad + theta_a_star_star)
        - See TMECH paper for more details
    """
    # Belt deflection model parameters 
    p2 = 14913 
    p1 = 876    
    
    # Gamma inverse: Function relating torque to deflection
    # theta_s = f(tau) where theta_s is belt deflection
    theta_s_func = lambda tau: -(-p1 + np.sqrt(p1**2 + 4*p2*np.abs(tau)))/(2*p2) * smooth_sign(tau, 5)
    
    # dGamma/dtheta_s: Function from deflection to belt stiffness
    # Ks = g(theta_s) where Ks is current belt stiffness
    stiffness_func = lambda theta_s: 2*p2*np.abs(theta_s) + p1

    # Calculate bias angle using the compensation formula
    expected_theta_s = theta_s_func(tau_0)  # Expected deflection based on desired torque
    Ks = stiffness_func(expected_theta_s)   # Current belt stiffness based on expected deflection
    
    # Bias angle calculation
    theta_a_star_star = (-tau_0/Ks - expected_theta_s)
    
    return theta_a_star_star


def get_torque_error_friction(actuator_velocity, tau_j_des):
    """
    Calculate the friction compensation torque needed to overcome drivetrain friction effects.
    
    This function compensates for both Coulomb and gear friction to improve impedance control performance.
    
    Args:
        actuator_velocity (float): Actuator velocity in rad/s.
            Note that this is not the joint velocity due to the belt transmission ratio. 
        tau_j_des (float): Desired torque in Nm (joint space)
        
    Returns:
        float: Additional torque to add to nominal command to overcome friction (joint space)
        
    Notes:
        - Add this value to your nominal torque command
        - Considers both Coulomb friction (fc) and gear friction (fg)
        - See TMECH paper for parameter identification and validation
    """
    # Transmission ratios
    GR_TRANS = 83/18  # Transmission ratio (actuator to joint)
    GR_ACT = 9        # Actuator gear ratio
    
    # Motor and friction parameters
    kt = 0.11         # Motor torque constant (Nm/A)
    fc = 0.171        # Coulomb friction (Nm at actuator output)
    fg = 0.0821       # Gear friction coefficient (Nm/A at actuator output)
        
    # Convert desired joint torque to actuator torque
    tau_a_des = tau_j_des / GR_TRANS

    # Calculate compensated current considering friction
    # This solves for the motor current needed to achieve the desired actuator torque
    Iq_compensated = (tau_a_des + smooth_sign(actuator_velocity, 1.5)*fc)/ \
                    (kt * GR_ACT - smooth_sign(tau_a_des*actuator_velocity + np.abs(actuator_velocity) * fc, 1.5)*fg)
    
    # Calculate the torque error (difference between compensated and nominal)
    torque_error_actuator = Iq_compensated * kt * GR_ACT - tau_a_des
    torque_error_output_Nm = torque_error_actuator * GR_TRANS  # Convert back to joint space
    
    return torque_error_output_Nm