"""Projected scoreboard: one huge timer and the ranked results.

Behaviour demanded by ``proj.md``:

* teams are listed fastest -> slowest with rank, team number and up to three
  times, and the list is rebuilt on every results change;
* ``s`` starts a count-up timer, ``e,10.10`` stops it and the displayed number
  is *exactly* the time from that message (:meth:`ScoreboardWindow.finish_run`);
* ``r`` - whether it comes from the gate box or from the operator's reset
  button - stops the count immediately.
"""

from __future__ import annotations

from datetime import datetime

from qtpy import QtCore, QtGui, QtWidgets

from . import protocol, theme
from .links import GateLink
from .results import MAX_TIMES, ResultStore
from .timeutil import format_time as fmt

TICK_MS = 20  # 10 ms resolution, refreshed a little faster than it changes
MEDALS = {1: "yellow_b", 2: "gray", 3: "orange_b"}  # rank colours (QSS ignores cell backgrounds)
STATE_TEXT = {"standby": "STANDBY", "running": "RUNNING", "finished": "FINISHED", "reset": "RESET"}
STATE_ATTR = {"standby": "waiting", "running": "running", "finished": "stopped", "reset": "reset"}


class ScoreboardWindow(QtWidgets.QMainWindow):
    """The audience-facing window."""

    def __init__(
        self,
        store: ResultStore,
        link: GateLink,
        *,
        title: str = "UC Line Follower",
        subtitle: str = "",
        timer_px: int = 190,
        table_px: int = 26,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.link = link
        self.timer_px = int(timer_px)
        self.table_px = int(table_px)
        self._state = "standby"
        self._elapsed_timer = QtCore.QElapsedTimer()
        self._shown_cs = 0

        self.setWindowTitle(title)
        self._build_ui(title, subtitle)
        self._wire()
        self._tick_timer = QtCore.QTimer(self)
        self._tick_timer.setInterval(TICK_MS)
        self._tick_timer.timeout.connect(self._tick)
        self._clock_timer = QtCore.QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._apply_fonts()
        self.rebuild()
        self._paint_timer()
        self._update_clock()

    # ==================================================================== layout

    def _build_ui(self, title: str, subtitle: str) -> None:
        root = QtWidgets.QWidget()
        root.setObjectName("ScoreboardRoot")
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(16)

        head = QtWidgets.QHBoxLayout()
        titles = QtWidgets.QVBoxLayout()
        title_label = QtWidgets.QLabel(title.upper())
        title_label.setObjectName("BoardTitle")
        self.subtitle_label = QtWidgets.QLabel(subtitle)
        self.subtitle_label.setObjectName("BoardSubtitle")
        self.subtitle_label.setVisible(bool(subtitle))
        titles.addWidget(title_label)
        titles.addWidget(self.subtitle_label)
        head.addLayout(titles, 1)

        self.link_pill = QtWidgets.QLabel("gates offline")
        self.link_pill.setObjectName("Pill")
        self.clock_label = QtWidgets.QLabel("")
        self.clock_label.setObjectName("Pill")
        head.addWidget(self.link_pill)
        head.addWidget(self.clock_label)
        layout.addLayout(head)

        panel = QtWidgets.QFrame()
        panel.setObjectName("TimerPanel")
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 14, 20, 14)
        self.state_label = QtWidgets.QLabel(STATE_TEXT["standby"])
        self.state_label.setObjectName("RaceState")
        self.state_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.timer_label = QtWidgets.QLabel("0.00")
        self.timer_label.setObjectName("TimerValue")
        self.timer_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        unit = QtWidgets.QLabel("seconds")
        unit.setObjectName("TimerUnit")
        unit.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.last_run_label = QtWidgets.QLabel("waiting for the first run")
        self.last_run_label.setObjectName("LastRun")
        self.last_run_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        panel_layout.addWidget(self.state_label)
        panel_layout.addWidget(self.timer_label)
        panel_layout.addWidget(unit)
        panel_layout.addWidget(self.last_run_label)
        layout.addWidget(panel)

        self.table = QtWidgets.QTableWidget(0, 3 + MAX_TIMES)
        self.table.setHorizontalHeaderLabels(["#", "Team", *[f"Time {i + 1}" for i in range(MAX_TIMES)], "Best"])
        self.table.setAlternatingRowColors(True)
        self.table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setHighlightSections(False)
        self.table.setShowGrid(False)
        layout.addWidget(self.table, 1)

    def _wire(self) -> None:
        self.store.changed.connect(self.rebuild)
        self.link.event.connect(self._on_event)
        self.link.tx_line.connect(self._on_tx_line)
        self.link.connected_changed.connect(self._on_connected)
        self._on_connected(self.link.connected)

    # ------------------------------------------------------------------ rendering

    # ------------------------------------------------------------------ type size
    # A widget-level stylesheet is used on purpose: an app-wide ``* { font-size }``
    # rule (see theme.stylesheet) would otherwise win over QWidget.setFont().

    def _apply_fonts(self) -> None:
        self.timer_label.setStyleSheet(
            f"QLabel#TimerValue {{ font-family: {theme.MONO_FAMILIES}; font-size: {self.timer_px}px;"
            f" font-weight: 800; background: transparent; }}"
        )
        self.table.setStyleSheet(f"QTableView {{ font-family: {theme.MONO_FAMILIES}; font-size: {self.table_px}px; }}")
        self.table.horizontalHeader().setStyleSheet(
            f"QHeaderView::section {{ font-size: {max(10, int(self.table_px * 0.62))}px; }}"
        )

    def set_scale(self, timer_px: int, table_px: int | None = None) -> None:
        """Resize the type (used by ``--scale`` and the +/- keys)."""
        self.timer_px = max(40, int(timer_px))
        if table_px is not None:
            self.table_px = max(10, int(table_px))
        self._apply_fonts()

    def rebuild(self) -> None:
        """Redraw the ranked table from the store."""
        ranked = self.store.ranked()
        self.table.blockSignals(True)
        self.table.clearSpans()
        self.table.setRowCount(len(ranked) or 1)
        if not ranked:
            item = QtWidgets.QTableWidgetItem("no results yet")
            item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            item.setForeground(QtGui.QBrush(QtGui.QColor(theme.color("gray0"))))
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, self.table.columnCount())
            self.table.blockSignals(False)
            return
        for row, (rank, record) in enumerate(ranked):
            cells = [
                str(rank),
                str(record.team),
                *(fmt(t, blank="\u2013") for t in record.times),
                fmt(record.best, blank="\u2013"),
            ]
            for column, text in enumerate(cells):
                item = self.table.item(row, column)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(text)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QtGui.QBrush(QtGui.QColor(theme.color(self._cell_colour(rank, column, record)))))
                font = item.font()
                font.setBold(column in (0, 1, 2 + MAX_TIMES))
                item.setFont(font)
        self.table.blockSignals(False)

    @staticmethod
    def _cell_colour(rank: int, column: int, record) -> str:
        if column == 0:
            return MEDALS.get(rank, "gray0")
        if column == 1:
            return "fg0"
        if 2 <= column <= 1 + MAX_TIMES:
            value = record.times[column - 2]
            if value is None:
                return "bg4"
            return "green_b" if value == record.best else "fg1"
        return "yellow_b" if rank == 1 else "fg0"  # best column

    # --------------------------------------------------------------------- timer

    def _set_state(self, state: str) -> None:
        self._state = state
        self.state_label.setText(STATE_TEXT[state])
        theme.set_state(self.state_label, "state", STATE_ATTR[state])

    def start_run(self) -> None:
        """``s`` - begin counting up from zero."""
        self._elapsed_timer.start()
        self._shown_cs = 0
        self._set_state("running")
        self._tick_timer.start()
        self.last_run_label.setText("car on the track\u2026")
        self._paint_timer()

    def finish_run(self, centiseconds: int, raw: str = "") -> None:
        """``e,<t>`` - stop counting and show exactly what the gates reported."""
        self._tick_timer.stop()
        self._shown_cs = int(centiseconds)
        self._set_state("finished")
        shown = fmt(centiseconds)
        self.last_run_label.setText(f"last run  {shown} s   (gate frame '{raw.strip()}')" if raw else f"last run  {shown} s")
        self._paint_timer()

    def abort_run(self, *, reason: str = "reset") -> None:
        """Stop counting without recording anything (reset button or ``r``)."""
        was_running = self._state == "running"
        self._tick_timer.stop()
        self._elapsed_timer.invalidate()
        self._shown_cs = 0
        self._set_state("reset")
        self._paint_timer()
        if was_running or reason:
            self.last_run_label.setText(f"timer cleared \u2014 {reason}")
        QtCore.QTimer.singleShot(1800, self._return_to_standby)

    def _return_to_standby(self) -> None:
        if self._state == "reset":
            self._set_state("standby")
            self.last_run_label.setText("waiting for the next run")

    def _tick(self) -> None:
        if self._state != "running":
            return
        cs = max(0, self._elapsed_timer.elapsed() * 100 // 1000)
        if cs != self._shown_cs:
            self._shown_cs = cs
            self._paint_timer()

    def _paint_timer(self) -> None:
        self.timer_label.setText(fmt(self._shown_cs))
        theme.set_state(self.timer_label, "state", STATE_ATTR[self._state])

    # --------------------------------------------------------------- link events

    def _on_event(self, event: protocol.Event) -> None:
        if isinstance(event, protocol.StartEvent):
            self.start_run()
        elif isinstance(event, protocol.FinishEvent):
            self.finish_run(event.centiseconds, event.line)
        elif isinstance(event, protocol.ResetEvent):
            self.abort_run(reason="reset pressed at the gates")

    def _on_tx_line(self, line: str) -> None:
        """Our own reset command stops the timer even with no gates attached."""
        if line.strip().lower() == "r":
            self.abort_run(reason="reset button")

    def _on_connected(self, connected: bool) -> None:
        theme.set_state(self.link_pill, "state", "ok" if connected else "bad")
        self.link_pill.setText("gates connected" if connected else "gates offline")

    def _update_clock(self) -> None:
        self.clock_label.setText(datetime.now().strftime("%H:%M:%S"))

    # --------------------------------------------------------------- presentation

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (QtCore.Qt.Key.Key_F11, QtCore.Qt.Key.Key_F):
            self.toggle_fullscreen()
        elif key == QtCore.Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        elif key == QtCore.Qt.Key.Key_R:
            self.abort_run(reason="scoreboard reset key")
        elif key in (QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Equal):
            self.set_scale(self.timer_px * 1.15, self.table_px * 1.1)
        elif key in (QtCore.Qt.Key.Key_Minus, QtCore.Qt.Key.Key_Underscore):
            self.set_scale(self.timer_px / 1.15, self.table_px / 1.1)
        elif key == QtCore.Qt.Key.Key_Q:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self._tick_timer.stop()
        self._clock_timer.stop()
        super().closeEvent(event)
