"""GRBL 1.1 ``error:N`` and ``ALARM:N`` codes → human-readable messages and fixes."""

from __future__ import annotations

ERRORS: dict[int, str] = {
    1: "G-code words consist of a letter and a value. Letter was not found.",
    2: "Numeric value format is not valid or missing an expected value.",
    3: "GRBL '$' system command was not recognized or supported.",
    4: "Negative value received for an expected positive value.",
    5: "Homing cycle is not enabled via settings ($22).",
    6: "Minimum step pulse time must be greater than 3 µs.",
    7: "EEPROM read failed. Reset and restored to default values.",
    8: "GRBL '$' command cannot be used unless GRBL is IDLE.",
    9: "G-code locked out during alarm or jog state. Home ($H) or unlock ($X) first.",
    10: "Soft limits cannot be enabled without homing also enabled.",
    11: "Max characters per line exceeded. Line was not processed.",
    12: "'$' setting value exceeds the maximum step rate supported.",
    13: "Safety door detected as opened and door state initiated.",
    14: "Build info or startup line exceeded EEPROM line length limit.",
    15: "Jog target exceeds machine travel. Command ignored.",
    16: "Jog command with no '=' or contains prohibited G-code.",
    17: "Laser mode requires PWM output.",
    20: "Unsupported or invalid G-code command found in block.",
    21: "More than one G-code command from the same modal group found in block.",
    22: "Feed rate has not yet been set or is undefined.",
    23: "G-code command in block requires an integer value.",
    24: "Two G-code commands that both require XYZ axis words were detected in the block.",
    25: "A G-code word was repeated in the block.",
    26: "A G-code command requires XYZ axis words in the block, but none were detected.",
    27: "N line number value is not within the valid range of 1 - 9,999,999.",
    28: "A G-code command is missing a required P or L value word.",
    29: "GRBL supports six work coordinate systems G54-G59 only.",
    30: "G53 requires either a G0 seek or G1 feed motion mode to be active.",
    31: "There are unused axis words in the block and G80 motion mode cancel is active.",
    32: "A G2/G3 arc has no XYZ axis words in the selected plane.",
    33: "The motion command has an invalid target.",
    34: "A G2/G3 arc traced with the radius definition had a mathematical error.",
    35: "A G2/G3 arc traced with the offset definition is missing the IJK offset word.",
    36: "There are unused, leftover G-code words that aren't used by any command in the block.",
    37: "G43.1 dynamic tool length offset cannot apply an offset to this axis.",
    38: "Tool number greater than max supported value.",
}

ALARMS: dict[int, tuple[str, str]] = {
    1: (
        "Hard limit triggered; position is likely lost.",
        "Check for an obstruction or a noisy endstop, then re-home with `plotter home`.",
    ),
    2: (
        "G-code motion target exceeds machine travel (soft limit); position retained.",
        "Unlock with `plotter unlock`, then fix the job bounds or home first.",
    ),
    3: (
        "Reset while in motion; position cannot be guaranteed.",
        "Re-home with `plotter home` before plotting again.",
    ),
    4: ("Probe fail: probe not in the expected initial state.", "Not used on this machine."),
    5: ("Probe fail: probe did not contact within travel.", "Not used on this machine."),
    6: ("Homing fail: reset during active homing cycle.", "Run `plotter home` again."),
    7: ("Homing fail: safety door opened during homing.", "Close the door, home again."),
    8: (
        "Homing fail: could not clear the limit switch when pulling off.",
        "Increase $27 (pull-off) or check the switch wiring.",
    ),
    9: (
        "Homing fail: could not find the limit switch within the search distance.",
        "Check switch wiring and $23 (homing direction); GRBL must be built to home X/Y only "
        "(no Z switch) — see firmware/GRBL_SETUP.md.",
    ),
    10: ("E-stop / safety alarm.", "Release the e-stop, reset, then home."),
}


def error_message(code: int) -> str:
    """Return the explanation for ``error:<code>``."""
    return ERRORS.get(code, f"unknown GRBL error {code}")


def alarm_message(code: int) -> str:
    """Return the explanation and the fix for ``ALARM:<code>``."""
    what, fix = ALARMS.get(code, (f"unknown GRBL alarm {code}", "Reset, then home the machine."))
    return f"{what} Fix: {fix}"
