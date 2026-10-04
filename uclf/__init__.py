"""UCLF line-follower scoreboard.

Package layout::

    timeutil           canonical race times (integer centiseconds) + parsing
    protocol           gate ASCII frames ('s', 'e,10.10', 'r') -> Event objects
    results            ResultStore: teams, three time slots, ranking, JSON save/load
    links              GateLink base, SerialLink (pyserial thread, hot-plug safe),
                       SimulatedLink (no hardware)
    theme              gruvbox dark palette + Qt stylesheet
    input_window       the operator's scoring desk
    scoreboard_window  the projected scoreboard (big timer + ranked table)
    gate_sim           optional gate-simulator window (used by --mock)
    app                command line, settings and wiring
"""

__version__ = "1.0.0"
