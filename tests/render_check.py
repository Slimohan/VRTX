#!/usr/bin/env python3
"""Renders the VRTX calibration macros with a made-up printer state and checks the G-code
that comes out. No Klipper needed, only Jinja2.

    tests/render_check.py

Klipper builds a macro with Jinja2 and the delimiters {% %} and { }. This does the same with
a stand-in for the `printer` object (three tools, a Nudge probe, a Voron-sized bed) and
prints the lines a macro would run. It checks the Nudge clear height, the error watchdog,
the return move and the two endings of VRTX_CALIBRATE (park_after_calibrate 0 and 1).
It cannot check what the lines do on a real printer.
"""
import os
import re
import sys

import jinja2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(REPO, "vrtx.cfg")


class D(dict):
    """dict with attribute access, like the objects Klipper hands to a template."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value


def d(**kw):
    return D(kw)


def load_macros(path):
    text = open(path).read()
    macros = {}
    sections = re.split(r"(?m)^\[", text)
    for sec in sections[1:]:
        head, _, body = sec.partition("]\n")
        kind, _, name = head.partition(" ")
        if kind not in ("gcode_macro", "delayed_gcode"):
            continue
        m = re.search(r"(?m)^gcode:\s*\n((?:[ \t]+.*\n|[ \t]*\n)*)", body)
        lines = []
        if m:
            for line in m.group(1).split("\n"):
                if line.strip().startswith("#"):
                    continue  # configparser drops comment lines
                lines.append(line)
        macros[name.strip()] = "\n".join(lines)
    return macros


MACROS = load_macros(CORE)


class Raise(Exception):
    pass


def make_env(echo):
    env = jinja2.Environment("{%", "%}", "{", "}")

    def respond(msg):
        echo.append(msg)
        return ""

    def raise_error(msg):
        raise Raise(msg)

    env.globals["action_respond_info"] = respond
    env.globals["action_raise_error"] = raise_error
    return env


def printer_state(**over):
    """A hotend swapper with T0, T1, T2, the Nudge at X97.5 Y321.5 Z53, safe_z 30."""
    settings = d(
        reference_tool=0, offset_method="tool_parameter", calibration="nudge",
        nudge_x=97.5, nudge_y=321.5, nudge_z=53.0, z_source="nudge",
        z_switch_x="unset", z_switch_y="unset", safe_z=30.0, travel_speed=200.0,
        z_speed=20.0, cal_temp=0.0, max_offset=10.0, nudge_margin=1.0,
        nudge_clear_z=10.0, park_after_calibrate=0,
    )
    tools = {"tool T%d" % n: d(gcode_x_offset=0.0, gcode_y_offset=0.0, gcode_z_offset=0.0, heater="")
             for n in range(3)}
    p = d(
        toolhead=d(position=d(x=120.0, y=200.0, z=75.0), homed_axes="xyz",
                   axis_minimum=d(x=0.0, y=0.0, z=-5.0), axis_maximum=d(x=350.0, y=350.0, z=340.0)),
        gcode_move=d(gcode_position=d(x=120.0, y=200.0, z=75.0)),
        toolchanger=d(tool_number=0, tool_numbers=[0, 1, 2], tool_names=["tool T0", "tool T1", "tool T2"]),
        save_variables=d(variables=d()),
        configfile=d(settings=d(tools_calibrate=d(spread=4.0))),
        tools_calibrate=d(),
        print_stats=d(state="standby"),
        vrtx_z_probe=None,
    )
    p["gcode_macro _VRTX_SETTINGS"] = settings
    p["gcode_macro _VRTX_STATE"] = d(cal_armed=0)
    p["gcode_macro _VRTX_DOCK_STATE"] = d(force_printing=0)
    p.update(tools)
    for k, v in over.items():
        if k in settings or k.startswith("s_"):
            settings[k.replace("s_", "", 1) if k.startswith("s_") else k] = v
        else:
            p[k] = v
    return p


def render(name, printer, **params):
    echo = []
    env = make_env(echo)
    tpl = env.from_string(MACROS[name])
    out = tpl.render(printer=printer, params=d(**params), rawparams=" ".join("%s=%s" % kv for kv in params.items()))
    lines = [l.strip() for l in out.split("\n") if l.strip()]
    return lines, echo


FAILED = []


def check(label, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else "   " + str(detail)))
    if not cond:
        FAILED.append(label)


def main():
    # --- _VRTX_NUDGE_CLEAR ------------------------------------------------------------
    lines, _ = render("_VRTX_NUDGE_CLEAR", printer_state())          # Z 75, clear 63
    check("clear: already above the clear height, no move", lines == [], lines)
    p = printer_state()
    p["gcode_move"].gcode_position.z = 49.3
    lines, _ = render("_VRTX_NUDGE_CLEAR", p)
    check("clear: at the pin it rises to nudge_z + 10 = 63 (not to safe_z 30)",
          lines == ["G90", "G1 Z63.0 F1200.0"], lines)
    p = printer_state(safe_z=100.0)
    p["gcode_move"].gcode_position.z = 49.3
    lines, _ = render("_VRTX_NUDGE_CLEAR", p)
    check("clear: a higher safe_z wins", lines == ["G90", "G1 Z100.0 F1200.0"], lines)
    p = printer_state()
    del p["gcode_macro _VRTX_SETTINGS"]["nudge_clear_z"]
    p["gcode_move"].gcode_position.z = 20.0
    lines, _ = render("_VRTX_NUDGE_CLEAR", p)
    check("clear: nudge_clear_z missing in an old config = 10 mm", lines == ["G90", "G1 Z63.0 F1200.0"], lines)

    # --- _VRTX_NUDGE_GOTO / _VRTX_NUDGE_LIFT --------------------------------------------
    p = printer_state()
    lines, _ = render("_VRTX_NUDGE_GOTO", p)
    i_clear = lines.index("_VRTX_NUDGE_CLEAR")
    i_xy = [i for i, l in enumerate(lines) if l.startswith("G1 X")][0]
    i_z = [i for i, l in enumerate(lines) if l.startswith("G1 Z")][0]
    check("goto: clear height first, then X/Y, then down to nudge_z", i_clear < i_xy < i_z, lines)
    check("goto: no _VRTX_SAFE_Z any more", "_VRTX_SAFE_Z" not in lines, lines)
    lines, _ = render("_VRTX_NUDGE_LIFT", p)
    check("lift: calls the clear height, no lift to safe_z", lines == ["_VRTX_NUDGE_CLEAR"], lines)

    # --- watchdog -------------------------------------------------------------------------
    p = printer_state()
    lines, _ = render("_VRTX_CAL_WATCHDOG", p)
    check("watchdog: not armed, does nothing", lines == [], lines)
    for label, x, y, expect in (("near the pin", 98.0, 322.0, True), ("at the docks", 22.0, 11.0, False)):
        p = printer_state()
        p["gcode_macro _VRTX_STATE"].cal_armed = 1
        p["gcode_move"].gcode_position.x = x
        p["gcode_move"].gcode_position.y = y
        lines, _ = render("_VRTX_CAL_WATCHDOG", p)
        lifted = "_VRTX_NUDGE_CLEAR" in lines
        check("watchdog: armed, %s -> %s" % (label, "raises" if expect else "leaves it"), lifted == expect, lines)
        check("watchdog: armed, %s -> disarms itself" % label, any("cal_armed VALUE=0" in l for l in lines), lines)

    # --- return move --------------------------------------------------------------------
    lines, _ = render("_VRTX_CAL_RETURN", printer_state(), X=120.0, Y=200.0, Z=75.0)
    check("return: clear height, then X/Y, then Z",
          lines[0] == "_VRTX_NUDGE_CLEAR" and lines[2].startswith("G1 X120.0 Y200.0")
          and lines[3].startswith("G1 Z75.0"), lines)
    lines, echo = render("_VRTX_CAL_RETURN", printer_state(), X=98.0, Y=322.0, Z=60.0)
    check("return: start over the pin below the clear height -> skipped with a warning",
          not any(l.startswith("G1") for l in lines) and any("_VRTX_ECHO" in l for l in lines), lines)
    lines, _ = render("_VRTX_CAL_RETURN", printer_state(), X=98.0, Y=322.0, Z=80.0)
    check("return: start over the pin but above the clear height -> goes back",
          any(l.startswith("G1 X98.0 Y322.0") for l in lines), lines)

    # --- VRTX_CALIBRATE --------------------------------------------------------------------
    for park in (0, 1):
        p = printer_state(park_after_calibrate=park)
        lines, _ = render("VRTX_CALIBRATE", p, SAVE=0)
        print("\n  VRTX_CALIBRATE, park_after_calibrate: %d" % park)
        for l in lines:
            print("     " + l)
        print()
        names = [l.split()[0] for l in lines]
        measures = [l for l in lines if l.startswith("_VRTX_MEASURE")]
        check("calibrate park=%d: measures T0 (reference), T1, T2" % park, len(measures) == 3, measures)
        check("calibrate park=%d: watchdog armed once and disarmed once" % park,
              names.count("_VRTX_CAL_ARM") == 1 and names.count("_VRTX_CAL_DISARM") == 1, lines)
        check("calibrate park=%d: armed before the first measurement, disarmed after the last" % park,
              names.index("_VRTX_CAL_ARM") < names.index("_VRTX_MEASURE")
              and names.index("_VRTX_CAL_DISARM") > len(names) - 1 - names[::-1].index("_VRTX_MEASURE"), lines)
        if park == 1:
            check("calibrate park=1: UNSELECT_TOOL after the disarm",
                  "UNSELECT_TOOL" in lines and lines.index("UNSELECT_TOOL") > names.index("_VRTX_CAL_DISARM"), lines)
            check("calibrate park=1: no swap back to the starting tool",
                  not any(l.startswith("_VRTX_SELECT") for l in lines), lines)
            check("calibrate park=1: offsets cleared, then the return to the start position",
                  "SET_GCODE_OFFSET X=0 Y=0 Z=0" in lines
                  and lines[-2].startswith("_VRTX_CAL_RETURN X=120.0 Y=200.0 Z=75.0"), lines)
        else:
            check("calibrate park=0: old ending, the starting tool is mounted again",
                  "_VRTX_SELECT T=0" in lines and "UNSELECT_TOOL" not in lines, lines)

    # refusals
    p = printer_state(nudge_clear_z=400.0)
    lines, _ = render("VRTX_CALIBRATE", p, SAVE=0)
    check("calibrate: nudge_clear_z too big -> refuses, moves nothing",
          any("Nothing was done" in l for l in lines) and not any(l.startswith("_VRTX_MEASURE") for l in lines), lines)
    p = printer_state(nudge_z="unset")
    lines, _ = render("VRTX_CALIBRATE", p, SAVE=0)
    check("calibrate: nudge not set -> refuses (and the new check does not choke on 'unset')",
          any("not set" in l for l in lines), lines)
    p = printer_state()
    p["toolhead"].homed_axes = "xy"
    lines, _ = render("VRTX_CALIBRATE", p, SAVE=0)
    check("calibrate: not homed -> refuses, no arming",
          any("Home all axes" in l for l in lines) and "_VRTX_CAL_ARM" not in lines, lines)

    print()
    if FAILED:
        print("%d check(s) failed" % len(FAILED))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
