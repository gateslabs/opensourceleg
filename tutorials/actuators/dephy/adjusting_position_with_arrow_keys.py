import select
import sys
import termios
import tty

import numpy as np

from opensourceleg.actuators.base import CONTROL_MODES
from opensourceleg.actuators.dephy import DephyActuator
from opensourceleg.utilities import SoftRealtimeLoop

PORT = "/dev/ttyACM1"
FREQUENCY = 500
DT = 1 / FREQUENCY
GEAR_RATIO = 1.0
SMALL_STEP_DEG = 2.0
LARGE_STEP_DEG = 10.0
PRINT_PERIOD_S = 0.2


def _read_key_non_blocking() -> str | None:  # noqa: C901
    if not select.select([sys.stdin], [], [], 0.0)[0]:
        return None

    # Drain all currently available input bytes so we can parse full escape sequences
    # even when terminals deliver them in one burst.
    data = []
    while select.select([sys.stdin], [], [], 0.0)[0]:
        data.append(sys.stdin.read(1))
    raw = "".join(data)

    if "q" in raw or "Q" in raw:
        return "QUIT"

    # Support both common arrow-key encodings:
    #   ESC [ A/B/C/D  and  ESC O A/B/C/D
    sequence_to_key = {
        "\x1b[A": "UP",
        "\x1b[B": "DOWN",
        "\x1b[C": "RIGHT",
        "\x1b[D": "LEFT",
        "\x1bOA": "UP",
        "\x1bOB": "DOWN",
        "\x1bOC": "RIGHT",
        "\x1bOD": "LEFT",
    }
    latest_match_index = -1
    latest_key = None
    for sequence, key in sequence_to_key.items():
        idx = raw.rfind(sequence)
        if idx > latest_match_index:
            latest_match_index = idx
            latest_key = key

    if latest_key is not None:
        return latest_key

    # Vim-style fallback keys are useful if a terminal remaps arrows.
    if "h" in raw:
        return "LEFT"
    if "j" in raw:
        return "DOWN"
    if "k" in raw:
        return "UP"
    if "l" in raw:
        return "RIGHT"
    return None


def position_control_with_arrow_keys() -> None:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "This script needs a real terminal for arrow-key input. "
            "Run it in an interactive terminal and keep focus on that terminal."
        )

    print("Arrow key position control")
    print(f"Port: {PORT}")
    print(f"LEFT/RIGHT: {SMALL_STEP_DEG:.1f} deg")
    print(f"UP/DOWN: {LARGE_STEP_DEG:.1f} deg")
    print("Press q to quit. (h/j/k/l also works)")

    old_tty_state = termios.tcgetattr(sys.stdin.fileno())
    tty.setcbreak(sys.stdin.fileno())

    actuator = DephyActuator(
        port=PORT,
        gear_ratio=GEAR_RATIO,
        frequency=FREQUENCY,
        debug_level=0,
        dephy_log=False,
    )
    clock = SoftRealtimeLoop(dt=DT)

    small_step_rad = np.deg2rad(SMALL_STEP_DEG)
    large_step_rad = np.deg2rad(LARGE_STEP_DEG)

    try:
        with actuator:
            actuator.set_control_mode(mode=CONTROL_MODES.POSITION)
            actuator.set_position_gains()
            actuator.update()

            command_position = actuator.output_position
            last_print_t = -PRINT_PERIOD_S

            for t in clock:
                key = _read_key_non_blocking()
                if key == "QUIT":
                    clock.stop()
                    continue
                if key == "LEFT":
                    command_position -= small_step_rad
                elif key == "RIGHT":
                    command_position += small_step_rad
                elif key == "UP":
                    command_position += large_step_rad
                elif key == "DOWN":
                    command_position -= large_step_rad

                actuator.set_output_position(value=command_position)
                actuator.update()

                if t - last_print_t >= PRINT_PERIOD_S:
                    print(
                        f"cmd={np.rad2deg(command_position):7.2f} deg | "
                        f"out={np.rad2deg(actuator.output_position):7.2f} deg"
                    )
                    last_print_t = t
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_tty_state)
        print("\nExited arrow-key position control.")


if __name__ == "__main__":
    position_control_with_arrow_keys()
