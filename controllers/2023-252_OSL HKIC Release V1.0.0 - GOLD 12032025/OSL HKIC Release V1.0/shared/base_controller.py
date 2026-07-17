"""
Abstract base class for OSL controllers

This module provides a common interface and shared functionality for all
OSL compiled controller implementations. Each specific controller should inherit
from this base class and implement the required abstract methods.

Kevin Best 9/27/2025
"""

from abc import ABC, abstractmethod
from opensourceleg.control.compiled import CompiledController
from opensourceleg.logging.logger import Logger
import numpy as np
import ctypes

class BaseController(ABC):
    """
    Abstract base class for OSL controllers.
    
    This class provides common functionality for all controller implementations,
    including logging, sensor data handling, and basic controller operations.
    Each specific controller should inherit from this class and implement
    the required abstract methods.
    """
    
    def __init__(self, loop_frequency: int, lib_path: str):
        """
        Initialize the base controller.
        
        Args:
            loop_frequency: Control loop frequency in Hz
            lib_path: Path to the compiled controller library
        """
        self._loop_frequency = loop_frequency
        self._lib_path = lib_path
        
        # Initialize the compiled controller
        self._compiledLib: CompiledController = self._define_controller()
        initialize_ctypes_fields_to_NaN(self._compiledLib.inputs)
        self.set_default_inputs()
        self._inputs_verified = False
    
    @abstractmethod
    def _define_controller(self) -> CompiledController:
        """
        Define the compiled controller interface.
        
        This method must be implemented by each controller to define:
        - Library name and path
        - Function names
        - Input/output types
        - Interface definitions

        """
        pass
    
    @abstractmethod
    def set_default_inputs(self) -> None:
        """
        Set default input parameters for the controller.
        """
        pass
    
    def run(self):
        """
        Run the controller for one iteration.
        
        Returns:
            Controller outputs
        """
        if not self._inputs_verified:
            self.verify_inputs()
        return self._compiledLib.run()
    
    @property
    def inputs(self):
        """Get controller inputs interface"""
        return self._compiledLib.inputs
    
    @property
    def outputs(self):
        """Get controller outputs interface"""
        return self._compiledLib.outputs
    
    def verify_inputs(self):
        """
        Traverse input tree structure and ensure that no uninitialized (sentinel) values persist.
        
        Checks for type-appropriate sentinel values:
        - Float types: NaN
        - Signed integer types: -999999
        - Unsigned integer types: 999999
        - Boolean types: Not checked (cannot detect uninitialized state)
        
        Raises:
            ValueError: If any leaf field in the inputs structure contains a sentinel value.
        """
        def check_leaf(parent_obj, field_name, value, path):
            field_type = _get_field_type(parent_obj, field_name)
            if _is_sentinel_value(value, field_type):
                if isinstance(value, (float, np.floating)):
                    sentinel_desc = "NaN"
                elif isinstance(value, (int, np.integer)):
                    sentinel_desc = str(value)
                else:
                    sentinel_desc = "uninitialized"
                raise ValueError(f"Input field '{path}' contains uninitialized value ({sentinel_desc}). All inputs must be set before running the controller.")
        
        call_function_on_tree_leaves(self._compiledLib.inputs, check_leaf, "inputs")
        
        # If it makes it to here, all inputs have been written to
        self._inputs_verified = True
    
    def log_all_outputs(self, logger: Logger, prefix: str = "outputs"):
        """
        Recursively track all leaf attributes in the outputs structure for logging.

        This method walks through the entire outputs structure (which may be deeply nested),
        and for every leaf attribute (i.e., a value that is not itself a struct/class with fields),
        it registers a logger entry using the logger's track_function method. The column name in the log
        will be a dot-separated path (e.g., 'outputs.kneeK1', 'outputs.record.common.kneeVel', etc.).

        Args:
            logger (Logger): The logger instance to use for tracking.
            prefix (str): The prefix to use for the root of the outputs structure in the log column names.

        Example:
            >>> control_module.log_all_outputs(LOGGER)
            # This will register all leaf fields in outputs for logging.
        """
        def log_leaf(parent_obj, field_name, value, path):
            # Register a logger entry for this leaf attribute.
            # The lambda uses default arguments to avoid late binding issues.
            logger.track_function(lambda o=parent_obj, f=field_name: getattr(o, f), path)
        
        call_function_on_tree_leaves(self._compiledLib.outputs, log_leaf, prefix)

    def log_all_inputs(self, logger: Logger, prefix: str = "inputs"):
        """
        Recursively track all leaf attributes in the inputs structure for logging.

        This method walks through the entire inputs structure (which may be deeply nested),
        and for every leaf attribute (i.e., a value that is not itself a struct/class with fields),
        it registers a logger entry using the logger's track_function method. The column name in the log
        will be a dot-separated path (e.g., 'inputs.kneeK1', 'inputs.record.common.kneeVel', etc.).

        Args:
            logger (Logger): The logger instance to use for tracking.
            prefix (str): The prefix to use for the root of the inputs structure in the log column names.

        Example:
            >>> control_module.log_all_inputs(LOGGER)
            # This will register all leaf fields in inputs for logging.
        """
        def log_leaf(parent_obj, field_name, value, path):
            # Register a logger entry for this leaf attribute.
            # The lambda uses default arguments to avoid late binding issues.
            logger.track_function(lambda o=parent_obj, f=field_name: getattr(o, f), path)
        
        call_function_on_tree_leaves(self._compiledLib.inputs, log_leaf, prefix)

def initialize_ctypes_fields_to_NaN(obj, prefix: str = "inputs"):
    """
    Recursively initialize all leaf fields in a ctypes structure to sentinel values.
    
    Uses type-appropriate sentinel values to mark fields as uninitialized:
    - Float types (c_double, c_float): NaN
    - Signed integer types: -999999 (unlikely to be a valid input value)
    - Unsigned integer types: 999999 (unlikely to be a valid input value)
    - Boolean types: Skipped (cannot detect uninitialized state)
    
    Args:
        obj: The root object to initialize
        prefix: Optional prefix for the path (used for nested structures)
    """
    def set_leaf_to_sentinel(parent_obj, field_name, value, path):
        field_type = _get_field_type(parent_obj, field_name)
        sentinel = _get_sentinel_value(field_type)
        if sentinel is not None:
            setattr(parent_obj, field_name, sentinel)
    
    call_function_on_tree_leaves(obj, set_leaf_to_sentinel, prefix)

def call_function_on_tree_leaves(obj, func, prefix: str = ""):
    """
    Recursively traverse a tree structure and call a function on all leaf nodes.
    
    Args:
        obj: The root object to traverse
        func: Function to call on each leaf. Should accept (parent_obj, field_name, value, path)
        prefix: Optional prefix for the path (used for nested structures)
    """
    def _process_branch(parent_obj, field_name, value, path, func):
        if _is_leaf(value):
            func(parent_obj, field_name, value, path)
        else:
            for field in _get_fields(value):
                child_value = getattr(value, field)
                _process_branch(value, field, child_value, f"{path}.{field}" if path else field, func)
    
    # Start processing from the root
    for field in _get_fields(obj):
        value = getattr(obj, field)
        path = f"{prefix}.{field}" if prefix else field
        _process_branch(obj, field, value, path, func)
    
    
def _is_leaf(obj):
    """
    Determine if an object is a 'leaf' node in a tree structure.

    A leaf is defined as an object that does not have further attributes to recurse into.
    This function returns True if the object does NOT have either a __dict__ (typical for Python classes)
    or a _fields_ attribute (typical for ctypes.Structure), meaning it is a primitive or simple value.

    Args:
        obj: The object to check.

    Returns:
        bool: True if the object is a leaf node, False if it is a container/struct/class.
    """
    return not (hasattr(obj, '__dict__') or hasattr(obj, '_fields_'))


def _get_fields(obj):
    """
    Get the list of field names for a given object.

    This function supports both ctypes.Structure (using _fields_) and regular Python classes (using __dict__).
    If neither is present, returns an empty list.

    Args:
        obj: The object whose fields to retrieve.

    Returns:
        list[str]: List of field/attribute names.
    """
    if hasattr(obj, '_fields_'):
        return [name for name, _ in obj._fields_]
    elif hasattr(obj, '__dict__'):
        return list(obj.__dict__.keys())
    else:
        return []


def _get_field_type(parent_obj, field_name):
    """
    Get the type of a field from a parent object.
    
    Args:
        parent_obj: The parent object containing the field
        field_name: Name of the field
        
    Returns:
        The type of the field, or None if not found or not a ctypes structure
    """
    if hasattr(parent_obj, '_fields_'):
        for name, field_type in parent_obj._fields_:
            if name == field_name:
                return field_type
    return None


def _get_sentinel_value(field_type):
    """
    Get an appropriate sentinel value for a field type that indicates uninitialized state.
    
    Args:
        field_type: The ctypes type of the field (or None if unknown)
        
    Returns:
        A sentinel value appropriate for the type, or None if type cannot be determined
    """
    
    # Try to determine type from field_type first
    if field_type is not None:
        # Check for float types
        if field_type in (ctypes.c_double, ctypes.c_float):
            return np.nan
        # Check for signed integer types - use a sentinel value unlikely to be valid
        elif field_type in (ctypes.c_int, ctypes.c_int8, ctypes.c_int16, ctypes.c_int32, ctypes.c_int64,
                           ctypes.c_long, ctypes.c_short):
            # Use a very large negative number as sentinel (unlikely to be a valid input)
            return -999999
        # Check for unsigned integer types - use a very large positive number as sentinel
        elif field_type in (ctypes.c_uint, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64,
                           ctypes.c_ulong, ctypes.c_ushort):
            # Use a very large positive number as sentinel (unlikely to be a valid input)
            return 999999
        # For booleans, we can't really detect uninitialized state
        # Return None to indicate we should skip initialization
        elif field_type == ctypes.c_bool:
            return None
    
    return None


def _is_sentinel_value(value, field_type=None):
    """
    Check if a value is a sentinel value indicating uninitialized state.
    
    Args:
        value: The value to check
        field_type: Optional field type for more precise checking
        
    Returns:
        True if the value is a sentinel, False otherwise
    """
    
    # Check for NaN (float sentinel)
    if isinstance(value, (float, np.floating)):
        return np.isnan(value)
    
    # Check for integer sentinel (both signed and unsigned)
    if isinstance(value, (int, np.integer)):
        return value == -999999 or value == 999999
    
    # For booleans, we can't detect uninitialized state
    # Return False (assume initialized)
    return False