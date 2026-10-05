"""Application wiring: settings, results store, gate link and the two windows.

``python -m uclf`` (or the ``uclf-scoreboard`` console script) lands here.  The
store and link are created once and handed to both windows, so the projected
scoreboard and the operator desk always agree.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qtpy import QtCore, QtWidgets

from . import __version__, theme
from .input_window import InputWindow
from .links import GateLink, SerialLink, SimulatedLink, available_ports, default_port
from .results import ResultStore, StoreError
from .gate_sim import GateSimulator
from .scoreboard_window import ScoreboardWindow

__all__ = ["build_parser", "run", "main", "ORGANISATION", "APPLICATION"]

ORGANISATION = "UC"
APPLICATION = "Scoreboard"
DEFAULT_RESULTS = "results.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uclf-scoreboard",
        description="Scoreboard for a line-follower competition: gruvbox dark, Qt through qtpy.",
        epilog="Run 'python -m uclf --mock' to drive the gates from a simulator window instead of real hardware.",
    )
    parser.add_argument("--port", default=None, help="USB serial device (default: remembered choice, else auto-detect)")
    parser.add_argument("--baud", type=int, default=None, help="baud rate (default: remembered choice, else 115200)")
    parser.add_argument("--results", default=None, help="results file to open (default: ./results.json)")
    parser.add_argument("--mock", action="store_true", help="no hardware: use the built-in gate simulator")
    parser.add_argument("--gates-off", action="store_true", help="start with the serial link switched off")
    parser.add_argument("--no-autosave", action="store_true", help="do not write the results file after every change")
    parser.add_argument("--scoreboard-only", action="store_true", help="show only the projected scoreboard")
    parser.add_argument("--fullscreen", action="store_true", help="show the scoreboard full screen")
    parser.add_argument("--scale", type=float, default=1.0, help="scoreboard type scale, 0.4 .. 3 (default 1)")
    parser.add_argument("--list-ports", action="store_true", help="print the serial ports found on this machine and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def make_link(args: argparse.Namespace, settings: QtCore.QSettings) -> GateLink:
    """The serial link, unless ``--mock`` asks for the simulator."""
    if args.mock:
        return SimulatedLink()
    port = args.port or str(settings.value("port", "") or "") or (default_port() or "")
    baud = args.baud or int(settings.value("baud", 115200) or 115200)
    link_on = not args.gates_off and str(settings.value("linkOn", "true")).lower() != "false"
    return SerialLink(port=port or None, baudrate=baud, auto_connect=link_on)


def make_store(args: argparse.Namespace, settings: QtCore.QSettings, problems: list[str]) -> ResultStore:
    """Store pointed at the requested file, loaded first when it exists.

    A results file that cannot be read must not stop the meet from running: the
    problem is reported to the desk and we start from an empty sheet instead.
    """
    raw = args.results or str(settings.value("resultsPath", "") or "")
    path = Path(raw).expanduser() if raw else Path(DEFAULT_RESULTS).resolve()
    store = ResultStore(path)
    store.autosave = not args.no_autosave
    if path.exists():
        try:
            store.load(path)
        except StoreError as exc:
            problems.append(f"could not read {path.name}: {exc}")
            store.clear_all()
    return store


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_ports:
        ports = available_ports()
        if not ports:
            print("no serial ports found")
        for device, description in ports:
            print(f"{device}\t{description}")
        return 0
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setOrganizationName(ORGANISATION)
    app.setApplicationName(APPLICATION)
    app.setStyle("Fusion")  # identical widget metrics on Windows, macOS and Linux
    app.setStyleSheet(theme.stylesheet())

    settings = QtCore.QSettings(ORGANISATION, APPLICATION)
    problems: list[str] = []
    store = make_store(args, settings, problems)
    link = make_link(args, settings)

    scale = max(0.4, min(3.0, args.scale))
    scoreboard = ScoreboardWindow(
        store,
        link,
        timer_px=int(190 * scale),
        table_px=int(26 * scale),
        title="UC Line Follower",
        subtitle="ranked by best of three runs",
    )
    desk = InputWindow(store, link, scoreboard=scoreboard, settings=settings)
    simulator = GateSimulator(link) if args.mock else None  # alive until exec() returns

    # The projected window belongs on the second screen: restore where it was left.
    fullscreen = args.fullscreen or str(settings.value("boardFullscreen", "0")) not in ("0", "", "false")
    geometry = settings.value("boardGeometry")
    if geometry and not fullscreen and not args.scoreboard_only:
        scoreboard.restoreGeometry(QtCore.QByteArray(geometry))
    scoreboard.showFullScreen() if fullscreen else scoreboard.show()
    if not args.scoreboard_only:
        desk.show()
        desk.raise_()
    if simulator is not None:
        simulator.show()

    for problem in problems:
        desk.notify_system(problem, level="bad")
    link.note(f"results file: {store.path}" if store.path else "no results file chosen")

    def shutdown() -> None:
        link.stop()  # joins the reader thread, so nothing writes after this
        store.flush()
        desk.write_settings()

    app.aboutToQuit.connect(shutdown)
    return app.exec()


def main() -> int:
    return run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
