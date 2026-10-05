"""Operator console: USB gate link, capture form, results table.

The layout follows the rules in ``proj.md``:

* a finished run is **staged**, never committed - the gate time is typed into
  the next empty slot of the capture form and stays listed as pending until the
  operator presses :qtcommand:`Submit` (:meth:`InputWindow.submit_form`);
* every stored time remains editable, either through the capture form or
  directly in the results table (:meth:`InputWindow._table_edited`);
* the traffic pane shows the raw ``rx`` / ``tx`` lines, so the serial side of
  the system is always visible;
* the reset button transmits ``r`` and stops the scoreboard's counting timer.
"""

from __future__ import annotations

import itertools
import time
from datetime import datetime
from pathlib import Path

from qtpy import QtCore, QtGui, QtWidgets

from . import theme
from .links import GateLink, SerialLink, available_ports, default_port
from .protocol import Event, FinishEvent, GarbageEvent, ResetEvent, StartEvent
from .results import MAX_TIMES, ResultStore, StoreError
from .timeutil import format_time, try_parse_time

BAUD_RATES = ("9600", "19200", "38400", "57600", "115200", "230400", "921600")
DASH = "\u2013"
MAX_STAGED = 20
RANK_COLOURS = ("yellow_b", "gray", "orange_b")  # gold / silver / bronze rank colours
#: message severity -> gruvbox colour (the stylesheet "role" names are not colours)
LOG_COLOURS = {"": "gray0", "info": "gray0", "ok": "green_b", "warn": "yellow_b",
               "bad": "red_b", "error": "red_b"}
_ids = itertools.count(1)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


class StagedItem:
    """One gate time that has not been submitted yet."""

    __slots__ = ("cid", "centiseconds", "raw", "when", "slot")

    def __init__(self, centiseconds: int, raw: str) -> None:
        self.cid = next(_ids)
        self.centiseconds = centiseconds
        self.raw = raw
        self.when = time.time()
        self.slot: int | None = None  # form slot currently holding this time

    @property
    def text(self) -> str:
        return format_time(self.centiseconds)


class StagedModel(QtCore.QAbstractListModel):
    """FIFO of staged gate times (newest at the bottom)."""

    #: emitted with the number of unplaced staged times, to drive the status label
    changed_count = QtCore.Signal(int)

    def __init__(self, limit: int = MAX_STAGED, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.items: list[StagedItem] = []
        self.limit = limit

    # Qt API ---------------------------------------------------------------
    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            clock = datetime.fromtimestamp(item.when).strftime("%H:%M:%S")
            where = f"   \u2192 in time {item.slot + 1}" if item.slot is not None else ""
            return f"{item.text:>9} s   '{item.raw}'  {clock}{where}"
        if role == QtCore.Qt.ItemDataRole.TextAlignmentRole:
            return int(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft)
        if role == QtCore.Qt.ItemDataRole.ForegroundRole:
            return QtGui.QBrush(QtGui.QColor(theme.color("fg2" if item.slot is None else "yellow_b")))
        return None

    # Model API ------------------------------------------------------------
    def push(self, centiseconds: int, raw: str) -> StagedItem:
        item = StagedItem(centiseconds, raw)
        if len(self.items) >= self.limit:
            self.items.pop(0)  # oldest first, the newest run matters most
            self.items.append(item)
            self.beginResetModel()
            self.endResetModel()
        else:
            row = len(self.items)
            self.beginInsertRows(QtCore.QModelIndex(), row, row)
            self.items.append(item)
            self.endInsertRows()
        self.changed_count.emit(self.free_count)
        return item

    def by_id(self, cid: int) -> StagedItem | None:
        return next((item for item in self.items if item.cid == cid), None)

    def newest_free(self) -> StagedItem | None:
        for item in reversed(self.items):
            if item.slot is None:
                return item
        return None

    def assign(self, cid: int, slot: int | None) -> None:
        item = self.by_id(cid)
        if item is None or item.slot == slot:
            return
        item.slot = slot
        self._refresh_row(item)
        self.changed_count.emit(self.free_count)

    def drop(self, cids: set[int]) -> None:
        if not cids:
            return
        keep = [item for item in self.items if item.cid not in cids]
        if len(keep) == len(self.items):
            return
        self.items = keep
        self.beginResetModel()
        self.endResetModel()
        self.changed_count.emit(len(self.items))

    def drop_all(self) -> None:
        if self.items:
            self.items = []
            self.beginResetModel()
            self.endResetModel()
            self.changed_count.emit(0)

    def _refresh_row(self, item: StagedItem) -> None:
        row = self.items.index(item)
        index = self.index(row)
        self.dataChanged.emit(index, index)

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def free_count(self) -> int:
        return sum(1 for item in self.items if item.slot is None)


class InputWindow(QtWidgets.QMainWindow):
    """The scoring desk."""

    def __init__(
        self,
        store: ResultStore,
        link: GateLink,
        *,
        scoreboard: QtWidgets.QWidget | None = None,
        settings: QtCore.QSettings | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.link = link
        self.scoreboard = scoreboard
        self.settings = settings or QtCore.QSettings("UCLF", "Scoreboard")
        self.staged = StagedModel(parent=self)
        self._slot_source = ["manual"] * MAX_TIMES
        self._slot_item: list[int | None] = [None] * MAX_TIMES
        self._form_team = 1
        self._port_text = ""
        self._refreshing = False  # guards table signals while repainting
        self._filling = False  # guards form signals while loading a team

        self.setWindowTitle("UC Line Follower \u2014 Scoring Desk")
        self.resize(1340, 880)
        self._build_ui()
        self._wire()
        self._restore_settings()
        self.refresh_table()
        self.load_team_into_form(self.team_spin.value())
        self._update_link_ui()
        self._update_file_ui()
        self._log("system", f"{MAX_TIMES} time slots per team - use Submit to save a result")

    # ==================================================================== layout

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        root.setObjectName("PageRoot")
        self.setCentralWidget(root)
        grid = QtWidgets.QGridLayout(root)
        grid.setContentsMargins(14, 8, 14, 12)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.addWidget(self._build_link_box(), 0, 0)
        grid.addWidget(self._build_traffic_box(), 1, 0)
        grid.addWidget(self._build_capture_box(), 0, 1)
        grid.addWidget(self._build_results_box(), 1, 1)
        grid.setColumnStretch(0, 10)
        grid.setColumnStretch(1, 11)
        grid.setRowStretch(1, 1)

        self.setStatusBar(QtWidgets.QStatusBar())
        self.stage_label = QtWidgets.QLabel("no staged times")
        theme.set_state(self.stage_label, "role", "muted")
        self.save_label = QtWidgets.QLabel("")
        theme.set_state(self.save_label, "role", "muted")
        self.statusBar().addPermanentWidget(self.stage_label)
        self.statusBar().addPermanentWidget(self.save_label)
        self._build_menus()

    @staticmethod
    def _caption(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text.upper())
        label.setObjectName("FieldLabel")
        return label

    def _build_link_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("USB serial gates")
        layout = QtWidgets.QVBoxLayout(box)

        row = QtWidgets.QHBoxLayout()
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(250)
        self.port_combo.setToolTip("Choose a port, or type a device path such as /dev/ttyACM0")
        self.port_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.rescan_btn = QtWidgets.QPushButton("Rescan")
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(BAUD_RATES)
        self.baud_combo.setCurrentText("115200")
        self.enable_box = QtWidgets.QCheckBox("Link on")
        self.enable_box.setChecked(True)
        self.enable_box.setToolTip("Switch off to release the port; switch on to reconnect")
        row.addWidget(self._caption("Port"))
        row.addWidget(self.port_combo, 1)
        row.addWidget(self.rescan_btn)
        row.addWidget(self._caption("Baud"))
        row.addWidget(self.baud_combo)
        row.addWidget(self.enable_box)
        layout.addLayout(row)

        self.link_pill = QtWidgets.QLabel("offline")
        self.link_pill.setObjectName("Pill")
        self.link_detail = QtWidgets.QLabel("")
        theme.set_state(self.link_detail, "role", "muted")
        self.scoreboard_btn = QtWidgets.QPushButton("Show scoreboard")
        self.scoreboard_btn.setToolTip("Open or raise the projected scoreboard (Ctrl+B)")
        self.scoreboard_btn.setShortcut(QtGui.QKeySequence("Ctrl+B"))
        self.reset_btn = QtWidgets.QPushButton("Reset gates")
        self.reset_btn.setProperty("role", "danger")
        self.reset_btn.setToolTip("Send 'r' to the gates and stop the scoreboard timer (Ctrl+R)")
        self.reset_btn.setShortcut(QtGui.QKeySequence("Ctrl+R"))
        hint = QtWidgets.QLabel(
            "frames   \u00b7   rx  s = start,  e,10.10 = finished in 10.10 s,  r = reset at the gates"
            "   \u00b7   tx  r = reset the gates"
        )
        theme.set_state(hint, "role", "hint")
        layout.addWidget(hint)

        status_row = QtWidgets.QHBoxLayout()
        status_row.addWidget(self.link_pill)
        status_row.addWidget(self.link_detail, 1)
        status_row.addWidget(self.scoreboard_btn)
        status_row.addWidget(self.reset_btn)
        layout.addLayout(status_row)
        layout.addStretch(1)
        return box

    def _build_traffic_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Serial traffic")
        layout = QtWidgets.QVBoxLayout(box)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setObjectName("SerialLog")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log_view, 1)

        row = QtWidgets.QHBoxLayout()
        self.show_junk_box = QtWidgets.QCheckBox("Show undecodable lines")
        self.show_junk_box.setChecked(True)
        self.clear_log_btn = QtWidgets.QPushButton("Clear")
        self.last_frame_label = QtWidgets.QLabel("no frames yet")
        theme.set_state(self.last_frame_label, "role", "muted")
        row.addWidget(self.show_junk_box)
        row.addWidget(self.last_frame_label, 1)
        row.addWidget(self.clear_log_btn)
        layout.addLayout(row)
        return box

    def _build_capture_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Capture \u2014 a result is saved only when you press Submit")
        grid = QtWidgets.QGridLayout(box)
        grid.setColumnStretch(1, 1)

        team_row = QtWidgets.QHBoxLayout()
        self.team_spin = QtWidgets.QSpinBox()
        self.team_spin.setRange(1, 9999)
        self.team_spin.setToolTip("Team currently on the track / being edited")
        self.team_note = QtWidgets.QLabel("")
        theme.set_state(self.team_note, "role", "muted")
        self.auto_box = QtWidgets.QCheckBox("Auto-record")
        self.auto_box.setToolTip(
            "Off (default): finished runs wait here for you to assign them.\n"
            "On: each incoming time goes straight into this team's next free slot."
        )
        team_row.addWidget(self._caption("Team"))
        team_row.addWidget(self.team_spin)
        team_row.addWidget(self.team_note, 1)
        team_row.addWidget(self.auto_box)
        grid.addLayout(team_row, 0, 0, 1, 3)

        self.slot_edits: list[QtWidgets.QLineEdit] = []
        self.slot_notes: list[QtWidgets.QLabel] = []
        for slot in range(MAX_TIMES):
            edit = QtWidgets.QLineEdit()
            edit.setPlaceholderText("10.10    (or  1:05.50 )")
            edit.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            edit.setMinimumHeight(32)
            note = QtWidgets.QLabel("")
            theme.set_state(note, "role", "hint")
            grid.addWidget(self._caption(f"Time {slot + 1}"), slot + 1, 0)
            grid.addWidget(edit, slot + 1, 1)
            grid.addWidget(note, slot + 1, 2)
            self.slot_edits.append(edit)
            self.slot_notes.append(note)

        buttons = QtWidgets.QHBoxLayout()
        self.submit_btn = QtWidgets.QPushButton("Submit")
        self.submit_btn.setProperty("role", "primary")
        self.submit_btn.setToolTip("Write these times into the results (Ctrl+Return)")
        self.submit_btn.setShortcut(QtGui.QKeySequence("Ctrl+Return"))
        self.revert_btn = QtWidgets.QPushButton("Revert")
        self.revert_btn.setToolTip("Reload this team's saved times")
        self.blank_btn = QtWidgets.QPushButton("Blank")
        self.blank_btn.setToolTip("Empty the slots; Submit to delete this team's times")
        self.form_status = QtWidgets.QLabel("")
        theme.set_state(self.form_status, "role", "hint")
        buttons.addWidget(self.submit_btn)
        buttons.addWidget(self.revert_btn)
        buttons.addWidget(self.blank_btn)
        buttons.addWidget(self.form_status, 1)
        grid.addLayout(buttons, MAX_TIMES + 1, 0, 1, 3)

        head = QtWidgets.QHBoxLayout()
        self.stage_title = self._caption("Staged gate times (not submitted)")
        self.stage_clear_btn = QtWidgets.QPushButton("Drop all")
        head.addWidget(self.stage_title, 1)
        head.addWidget(self.stage_clear_btn)
        grid.addLayout(head, MAX_TIMES + 2, 0, 1, 3)

        self.stage_view = QtWidgets.QListView()
        self.stage_view.setObjectName("PendingView")
        self.stage_view.setModel(self.staged)
        self.stage_view.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stage_view.setMinimumHeight(76)
        grid.addWidget(self.stage_view, MAX_TIMES + 3, 0, 1, 3)

        place = QtWidgets.QHBoxLayout()
        for slot in range(MAX_TIMES):
            button = QtWidgets.QPushButton(f"Newest \u2192 time {slot + 1}")
            button.setToolTip(f"Put the newest unplaced gate time into form slot {slot + 1}, then Submit")
            button.clicked.connect(lambda _checked=False, s=slot: self.place_staged(s))
            place.addWidget(button)
        place.addStretch(1)
        grid.addLayout(place, MAX_TIMES + 4, 0, 1, 3)
        return box

    def _build_results_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Results")
        layout = QtWidgets.QVBoxLayout(box)

        row = QtWidgets.QHBoxLayout()
        self.file_label = QtWidgets.QLabel("")
        theme.set_state(self.file_label, "role", "muted")
        self.autosave_box = QtWidgets.QCheckBox("Autosave")
        self.autosave_box.setChecked(True)
        self.autosave_box.setToolTip("Write the results file after every change")
        self.new_btn = QtWidgets.QPushButton("New")
        self.open_btn = QtWidgets.QPushButton("Open\u2026")
        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_as_btn = QtWidgets.QPushButton("Save as\u2026")
        row.addWidget(self.file_label, 1)
        for widget in (self.autosave_box, self.new_btn, self.open_btn, self.save_btn, self.save_as_btn):
            row.addWidget(widget)
        layout.addLayout(row)

        self.table = QtWidgets.QTableWidget(0, 3 + MAX_TIMES)
        self.table.setHorizontalHeaderLabels(["Rank", "Team", *[f"Time {i + 1}" for i in range(MAX_TIMES)], "Best"])
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
            | QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked
            | QtWidgets.QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(240)
        layout.addWidget(self.table, 1)

        foot = QtWidgets.QHBoxLayout()
        self.counts_label = QtWidgets.QLabel("")
        theme.set_state(self.counts_label, "role", "muted")
        self.add_team_btn = QtWidgets.QPushButton("Add team")
        self.to_form_btn = QtWidgets.QPushButton("Edit selected in capture")
        self.delete_btn = QtWidgets.QPushButton("Delete selected")
        foot.addWidget(self.counts_label, 1)
        for widget in (self.add_team_btn, self.to_form_btn, self.delete_btn):
            foot.addWidget(widget)
        layout.addLayout(foot)
        return box

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addAction("&New results", self.new_results)
        file_menu.addAction("&Open\u2026", self.open_results)
        file_menu.addAction("&Save", self.save_results)
        file_menu.addAction("Save &as\u2026", self.save_results_as)
        file_menu.addSeparator()
        file_menu.addAction("Export &CSV", self.export_csv)
        file_menu.addSeparator()
        file_menu.addAction("&Quit", self.close)

        gates_menu = bar.addMenu("&Gates")
        gates_menu.addAction("&Reset gates", self.reset_gates)
        gates_menu.addAction("Rescan &ports", self.rescan_ports)
        gates_menu.addAction("Protocol &help", self._show_protocol_help)

        view_menu = bar.addMenu("&View")
        view_menu.addAction("&Scoreboard", self.show_scoreboard)
        if self.scoreboard is not None:
            view_menu.addAction("Scoreboard &fullscreen", getattr(self.scoreboard, "toggle_fullscreen"))

    # ===================================================================== wiring

    def _wire(self) -> None:
        self.rescan_btn.clicked.connect(self.rescan_ports)
        self.port_combo.editTextChanged.connect(self._port_edited)
        self.baud_combo.currentTextChanged.connect(self._baud_changed)
        self.enable_box.toggled.connect(self._enable_changed)
        self.reset_btn.clicked.connect(self.reset_gates)
        self.scoreboard_btn.clicked.connect(self.show_scoreboard)
        self.clear_log_btn.clicked.connect(self.log_view.clear)

        self.team_spin.valueChanged.connect(self._team_requested)
        self.auto_box.toggled.connect(self._auto_record_changed)
        self.submit_btn.clicked.connect(self.submit_form)
        self.revert_btn.clicked.connect(self._revert_clicked)
        self.blank_btn.clicked.connect(self.blank_slots)
        self.stage_clear_btn.clicked.connect(self._drop_all_staged)
        for edit in self.slot_edits:
            edit.textEdited.connect(lambda _text, e=edit: self._slot_typed(e))

        self.autosave_box.toggled.connect(self._autosave_changed)
        self.new_btn.clicked.connect(self.new_results)
        self.open_btn.clicked.connect(self.open_results)
        self.save_btn.clicked.connect(self.save_results)
        self.save_as_btn.clicked.connect(self.save_results_as)
        self.add_team_btn.clicked.connect(self.add_team)
        self.delete_btn.clicked.connect(self.delete_selected_team)
        self.to_form_btn.clicked.connect(self.edit_selected_team)
        self.table.itemChanged.connect(self._table_edited)

        self.store.changed.connect(self.refresh_table)
        self.store.message.connect(self._on_store_message)
        self.store.dirty_changed.connect(self._on_dirty_changed)
        self.staged.changed_count.connect(self._refresh_stage_label)

        self.link.raw_line.connect(lambda line: self._log("rx", line))
        self.link.tx_line.connect(lambda line: self._log("tx", line))
        self.link.status.connect(lambda text: self._log("system", text))
        self.link.connected_changed.connect(self._on_connected)
        self.link.event.connect(self._on_event)

    def _restore_settings(self) -> None:
        def flag(key: str, default: bool) -> bool:
            value = self.settings.value(key, default)
            return value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes")

        self.auto_box.setChecked(flag("autoRecord", False))
        self.autosave_box.setChecked(flag("autosave", True))
        self.show_junk_box.setChecked(flag("showJunk", True))
        self.enable_box.setChecked(flag("linkOn", True))
        baud = str(self.settings.value("baud", "115200"))
        if baud in BAUD_RATES:
            self.baud_combo.setCurrentText(baud)
        port = str(self.settings.value("port", ""))
        self._port_text = port
        if isinstance(self.link, SerialLink):
            self.link.configure(
                port=port or None, baudrate=int(self.baud_combo.currentText()), auto_connect=self.enable_box.isChecked()
            )
        self._fill_ports(port or default_port() or "")
        team = str(self.settings.value("team", ""))
        if team.isdigit():
            self.team_spin.setValue(max(1, min(9999, int(team))))

    def write_settings(self) -> None:
        self.settings.setValue("autoRecord", self.auto_box.isChecked())
        self.settings.setValue("autosave", self.autosave_box.isChecked())
        self.settings.setValue("showJunk", self.show_junk_box.isChecked())
        self.settings.setValue("linkOn", self.enable_box.isChecked())
        self.settings.setValue("team", self.team_spin.value())
        self.settings.setValue("baud", self.baud_combo.currentText())
        self.settings.setValue("port", self.port_combo.currentData() or self.port_combo.currentText())
        if self.store.path:
            self.settings.setValue("resultsPath", str(self.store.path))
        if self.scoreboard is not None:
            self.settings.setValue("boardGeometry", self.scoreboard.saveGeometry())
            self.settings.setValue("boardFullscreen", int(self.scoreboard.isFullScreen()))

    # ------------------------------------------------------------------ serial UI

    def rescan_ports(self) -> None:
        desired = self.port_combo.currentData() or self.port_combo.currentText()
        self._fill_ports(desired)
        self._log("system", f"rescan found {len(available_ports())} serial port(s)")

    def _fill_ports(self, desired: str = "") -> None:
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        devices: list[str] = []
        for device, description in available_ports():
            tag = description.split("(")[0].strip() or description
            self.port_combo.addItem(f"{device} \u2014 {tag}", device)
            devices.append(device)
        if desired and desired not in devices:
            self.port_combo.addItem(desired, desired)
            devices.append(desired)
        if not devices:
            self.port_combo.addItem("(no serial ports found)", "")
            self.port_combo.setCurrentIndex(0)
        else:
            self.port_combo.setCurrentIndex(max(0, self.port_combo.findData(desired)))
        self.port_combo.blockSignals(False)

    def _port_edited(self, text: str) -> None:
        port = (self.port_combo.currentData() or text).strip()
        if port.startswith("(") or port == self._port_text:
            return
        self._port_text = port
        if isinstance(self.link, SerialLink):
            self.link.configure(port=port or None)
            self._log("system", f"port set to {port or '(none)'}")

    def _baud_changed(self, text: str) -> None:
        if isinstance(self.link, SerialLink) and text.isdigit():
            self.link.configure(baudrate=int(text))

    def _enable_changed(self, on: bool) -> None:
        if isinstance(self.link, SerialLink):
            self.link.configure(auto_connect=on)
        self._log("system", "link switched on" if on else "link switched off")
        self._update_link_ui()

    def _update_link_ui(self, *, first_time: bool = False) -> None:
        if first_time and getattr(self.link, "last_status", ""):
            self._log("system", self.link.last_status)  # happened before we were listening
        connected = self.link.connected
        theme.set_state(self.link_pill, "state", "ok" if connected else "bad")
        self.link_pill.setText("gates connected" if connected else "gates offline")
        self.link_detail.setText(f"{self.link.describe()}  \u00b7  {'reading' if connected else 'retrying automatically'}")
        if not isinstance(self.link, SerialLink):  # a simulated link has no port to choose
            for widget in (self.port_combo, self.baud_combo, self.enable_box, self.rescan_btn):
                widget.setEnabled(False)

    def _on_connected(self, connected: bool) -> None:
        self._update_link_ui()
        if not connected and getattr(self.link, "auto_connect", True):
            self._log("system", "port closed \u2014 waiting for the USB device to come back")

    def _on_store_message(self, level: str, text: str) -> None:
        self._log("system", text, level=level)

    def _on_dirty_changed(self, dirty: bool) -> None:
        self.save_label.setText("saved" if not dirty else "unsaved changes")
        theme.set_state(self.save_label, "role", "ok" if not dirty else "warn")

    # ----------------------------------------------------------------- gate events

    def _on_event(self, event: Event) -> None:
        if isinstance(event, StartEvent):
            self.last_frame_label.setText(f"{_stamp()}  start \u2014 the scoreboard is counting")
            theme.set_state(self.last_frame_label, "role", "ok")
        elif isinstance(event, FinishEvent):
            self._on_finish(event)
        elif isinstance(event, ResetEvent):
            self.last_frame_label.setText(f"{_stamp()}  the gates reset themselves")
            theme.set_state(self.last_frame_label, "role", "warn")
        elif isinstance(event, GarbageEvent) and self.show_junk_box.isChecked():
            self._log("junk", f"{event.line}  \u2190 {event.reason}")

    def _on_finish(self, event: FinishEvent) -> None:
        self.last_frame_label.setText(f"{_stamp()}  finished in {event.text} s")
        theme.set_state(self.last_frame_label, "role", "ok")
        team = self.team_spin.value()
        if self.auto_box.isChecked():
            slot = self.store.append_serial_time(team, event.centiseconds)
            if slot is not None:
                self._log("system", f"auto-recorded {event.text} s \u2192 team {team}, time {slot + 1}")
                self.load_team_into_form(team)
                return
            self._log("system", f"team {team} already has {MAX_TIMES} times \u2014 {event.text} s staged instead")

        item = self.staged.push(event.centiseconds, event.line)
        filled = self._fill_next_empty_slot(item)
        if filled is not None:
            self._set_form_status(f"{event.text} s typed into time {filled + 1} \u2014 Submit to save", "ok")
        else:
            self._set_form_status(f"{event.text} s staged \u2014 choose a slot below", "warn")

    def _auto_record_changed(self, on: bool) -> None:
        self._log("system", f"auto-record on for team {self.team_spin.value()}" if on else "auto-record off")

    # -------------------------------------------------------------- capture form

    def _team_requested(self, team: int) -> None:
        """The team spin changed: switch the form, but never silently drop edits."""
        if self._filling or self._form_is_clean():
            self.load_team_into_form(team)
            return
        if self._confirm_discard(team):
            self.load_team_into_form(team)
            return
        self._filling = True
        self.team_spin.setValue(self._form_team)
        self._filling = False

    def _confirm_discard(self, team: int) -> bool:
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Unsubmitted times",
            f"The capture form holds times that were never submitted (team {self._form_team}).\n\n"
            f"Discard them and switch to team {team}?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    def load_team_into_form(self, team: int) -> None:
        """Put a team's saved times into the form (release any staged items first)."""
        for cid in self._slot_item:
            if cid is not None:
                self.staged.assign(cid, None)
        record = self.store.record(team)
        self._form_team = team
        values = record.times if record else [None] * MAX_TIMES
        self._slot_source = list(record.sources) if record else ["manual"] * MAX_TIMES
        self._slot_item = [None] * MAX_TIMES
        self._filling = True
        for edit, value in zip(self.slot_edits, values):
            edit.setText(format_time(value, ""))
            theme.set_state(edit, "role", "filled" if value is not None else None)
        self._filling = False
        self._sync_notes()
        self._set_form_status("", "hint")

    def _fill_next_empty_slot(self, item: StagedItem) -> int | None:
        for slot, edit in enumerate(self.slot_edits):
            if edit.text().strip():
                continue
            self._filling = True
            edit.setText(item.text)
            self._filling = False
            theme.set_state(edit, "role", "filled")
            self._slot_source[slot] = "serial"
            self._slot_item[slot] = item.cid
            self.staged.assign(item.cid, slot)
            self._sync_notes()
            return slot
        return None

    def place_staged(self, slot: int) -> None:
        item = self.staged.newest_free()
        if item is None:
            self._set_form_status("nothing is staged", "warn")
            return
        previous = self._slot_item[slot]
        if previous is not None:
            self.staged.assign(previous, None)  # give back whatever was in that slot
        edit = self.slot_edits[slot]
        self._filling = True
        edit.setText(item.text)
        self._filling = False
        theme.set_state(edit, "role", "filled")
        self._slot_source[slot] = "serial"
        self._slot_item[slot] = item.cid
        self.staged.assign(item.cid, slot)
        self._sync_notes()
        edit.setFocus()
        self._set_form_status(f"{item.text} s \u2192 time {slot + 1} \u2014 Submit to save", "ok")

    def _slot_typed(self, edit: QtWidgets.QLineEdit) -> None:
        slot = self.slot_edits.index(edit)
        if self._slot_item[slot] is not None:  # operator overwrote a staged time
            self.staged.assign(self._slot_item[slot], None)
            self._slot_item[slot] = None
        self._slot_source[slot] = "manual"
        _value, error = try_parse_time(edit.text())
        theme.set_state(edit, "role", "invalid" if error else ("filled" if edit.text().strip() else None))
        self._set_form_status(error or self._dirty_note(), "bad" if error else "hint")
        self._sync_notes()

    def blank_slots(self) -> None:
        self._filling = True
        for edit in self.slot_edits:
            edit.clear()
            theme.set_state(edit, "role", None)
        self._filling = False
        for slot, cid in enumerate(self._slot_item):
            if cid is not None:
                self.staged.assign(cid, None)
        self._slot_item = [None] * MAX_TIMES
        self._slot_source = ["manual"] * MAX_TIMES
        self._sync_notes()
        self._set_form_status("slots blanked \u2014 Submit to delete these times", "hint")

    def _revert_clicked(self) -> None:
        self.load_team_into_form(self._form_team)
        self._set_form_status("reverted to the saved times", "hint")

    def _drop_all_staged(self) -> None:
        """Forget the unplaced gate times.  Values already typed into the form stay."""
        self.staged.drop_all()
        self._slot_item = [None] * MAX_TIMES  # they no longer refer to a staged entry
        self._sync_notes()
        self._set_form_status("staged times dropped", "hint")

    def _dirty_note(self) -> str:
        return "" if self._form_is_clean() else "not submitted yet"

    def _form_is_clean(self) -> bool:
        record = self.store.record(self._form_team)
        for slot, edit in enumerate(self.slot_edits):
            value, error = try_parse_time(edit.text())
            if error or value != (record.times[slot] if record else None):
                return False
        return True

    def _sync_notes(self) -> None:
        record = self.store.record(self._form_team)
        for slot, note in enumerate(self.slot_notes):
            stored = record.times[slot] if record else None
            typed, error = try_parse_time(self.slot_edits[slot].text())
            if error:
                note.setText("not a time")
                theme.set_state(note, "role", "bad")
            elif typed != stored:
                note.setText(f"saved {format_time(stored, DASH)} \u2014 unsubmitted")
                theme.set_state(note, "role", "warn")
            else:
                source = self._slot_source[slot] if stored is not None else "empty"
                note.setText(f"saved {format_time(stored, DASH)} ({source})")
                theme.set_state(note, "role", "hint")
        clean = self._form_is_clean()
        self.submit_btn.setEnabled(not clean)
        self.submit_btn.setText("Submit" if clean else f"Submit team {self._form_team}")
        if record is None:
            self.team_note.setText(f"team {self._form_team} is not in the results yet")
        else:
            times = "  ".join(format_time(t, DASH) for t in record.times)
            self.team_note.setText(f"best {format_time(record.best, DASH)}   ({times})")

    def submit_form(self) -> bool:
        """Validate the form and commit it - the only manual path into the table."""
        team = self._form_team
        record = self.store.record(team)
        values: list[int | None] = []
        for slot, edit in enumerate(self.slot_edits):
            value, error = try_parse_time(edit.text())
            if error:
                theme.set_state(edit, "role", "invalid")
                self._set_form_status(f"time {slot + 1}: {error}", "bad")
                edit.setFocus()
                edit.selectAll()
                return False
            values.append(value)

        previous = list(record.times) if record else [None] * MAX_TIMES
        changed = [slot for slot in range(MAX_TIMES) if values[slot] != previous[slot]]
        if not changed:
            self._set_form_status("nothing changed", "hint")
            return True
        for slot in changed:
            self.store.set_time(team, slot, values[slot], source=self._slot_source[slot])
        consumed = {cid for cid in self._slot_item if cid is not None}
        self.staged.drop(consumed)
        details = ", ".join(f"time {slot + 1}={format_time(values[slot], DASH)}" for slot in changed)
        self._log("system", f"submitted team {team}: {details}")
        self._set_form_status(f"team {team} saved", "ok")
        self.load_team_into_form(team)
        return True

    def _refresh_stage_label(self, count: int | None = None) -> None:
        free = self.staged.free_count if count is None else count
        self.stage_label.setText(f"{free} staged time{'s' if free != 1 else ''}" if free else "no staged times")
        theme.set_state(self.stage_label, "role", "warn" if free else "muted")

    # ------------------------------------------------------------- results table

    def refresh_table(self) -> None:
        ranked = self.store.ranked()
        selected = self._selected_team()
        self._refreshing = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(ranked))
        for row, (rank, record) in enumerate(ranked):
            texts = [str(rank), str(record.team), *(format_time(t, "") for t in record.times), format_time(record.best, "")]
            for column, text in enumerate(texts):
                item = self.table.item(row, column)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(text)
                flags = QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
                if column >= 1:
                    flags |= QtCore.Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, record.team)
                fg, bold = "fg1", False
                if column == 0:
                    bold = True
                    fg = RANK_COLOURS[rank - 1] if rank <= 3 else "gray0"
                elif column == 1:
                    fg, bold = "fg0", True
                elif column <= MAX_TIMES + 1:
                    slot = column - 2
                    value = record.times[slot]
                    is_best = value is not None and value == record.best
                    fg = "bg4" if value is None else ("green_b" if is_best else "fg1")
                    bold = is_best
                    item.setToolTip(f"team {record.team} \u00b7 time {slot + 1} \u00b7 recorded by {record.sources[slot]}")
                else:  # best column
                    fg = "yellow_b" if rank == 1 else "fg0"
                item.setForeground(QtGui.QBrush(QtGui.QColor(theme.color(fg))))
                font = item.font()
                font.setBold(bold)
                item.setFont(font)
        self.table.blockSignals(False)
        self._refreshing = False
        if selected is not None:
            self._select_team(selected)
        teams, runs = self.store.counts()
        self.counts_label.setText(f"{teams} team{'s' if teams != 1 else ''} \u00b7 {runs} run{'s' if runs != 1 else ''} recorded")
        self._sync_notes()

    def _table_edited(self, item: QtWidgets.QTableWidgetItem) -> None:
        """Live edits in the table - every saved time stays editable."""
        if self._refreshing:
            return
        team = item.data(QtCore.Qt.ItemDataRole.UserRole)
        column = item.column()
        text = item.text().strip()
        if column == 1:
            self._rename_team(team, text)
        elif 2 <= column <= MAX_TIMES + 1:
            value, error = try_parse_time(text)
            if error:
                self._log("system", f"team {team} time {column - 1} left unchanged: {error}")
                self._set_form_status(error, "bad")
            else:
                self.store.set_time(team, column - 2, value, source="manual")
                self._log("system", f"team {team} time {column - 1} set to {format_time(value, '(blank)')}")
        self.refresh_table()

    def _rename_team(self, old: int, text: str) -> None:
        try:
            new = int(text)
        except ValueError:
            self._log("system", f"'{text}' is not a team number")
            self.refresh_table()
            return
        error = self.store.rename_team(old, new)
        if error:
            self._log("system", f"team {old} kept: {error}")
            self._set_form_status(error, "bad")
        else:
            self._log("system", f"team {old} renamed to {new}")
            if self._form_team == old:
                self.team_spin.setValue(new)
        self.refresh_table()

    def _selected_team(self) -> int | None:
        item = self.table.currentItem() or (self.table.selectedItems() or [None])[0]
        return item.data(QtCore.Qt.ItemDataRole.UserRole) if item is not None else None

    def _select_team(self, team: int) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is not None and item.data(QtCore.Qt.ItemDataRole.UserRole) == team:
                self.table.blockSignals(True)
                self.table.selectRow(row)
                self.table.blockSignals(False)
                return

    def edit_selected_team(self) -> None:
        team = self._selected_team()
        if team is None:
            self._set_form_status("select a row first", "warn")
            return
        self.team_spin.setValue(team)
        self.submit_btn.setFocus()

    def add_team(self) -> None:
        team, ok = QtWidgets.QInputDialog.getInt(self, "Add team", "Team number:", self.team_spin.value() + 1, 1, 9999)
        if not ok:
            return
        if self.store.record(team) is not None:
            self._set_form_status(f"team {team} already exists", "bad")
            return
        self.store.ensure_team(team)
        self.team_spin.setValue(team)
        self._log("system", f"team {team} added with no times yet")

    def delete_selected_team(self) -> None:
        team = self._selected_team() or self.team_spin.value()
        record = self.store.record(team)
        if record is None:
            self._set_form_status(f"team {team} is not in the results", "warn")
            return
        times = ", ".join(format_time(t, DASH) for t in record.valid_times) or "no times"
        answer = QtWidgets.QMessageBox.question(self, "Delete team", f"Delete team {team} ({times})?\nThis cannot be undone.")
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.store.remove_team(team)
        self._log("system", f"team {team} deleted")
        if self._form_team == team:
            self.load_team_into_form(team)

    # --------------------------------------------------------------- file actions

    def _autosave_changed(self, on: bool) -> None:
        self.store.autosave = on
        if not on:
            self.store.mark_saved()  # stop nagging about changes we will not write
        self._log("system", f"autosave {'on' if on else 'off'}")

    def open_results(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open results", str(self.store.path or ""), "Scoreboard results (*.json);;All files (*)"
        )
        if not path:
            return
        if Path(path) == self.store.path:
            self._set_form_status("that is already the open file", "hint")
            return
        if not self._confirm_replace("open another results file"):
            return
        self.load_results(path)

    def _confirm_replace(self, what: str) -> bool:
        """Warn before throwing away work: unsubmitted edits and unsaved results."""
        warnings = []
        if not self._form_is_clean():
            warnings.append(f"  \u00b7 the capture form has times for team {self._form_team} that were never submitted")
        if self.store.dirty:
            warnings.append("  \u00b7 the results file on disk is older than what is on screen")
        if not warnings:
            return True
        answer = QtWidgets.QMessageBox.warning(
            self,
            f"Before you {what}",
            "Before you do:\n\n" + "\n".join(warnings) + "\n\nContinue and lose them?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    def load_results(self, path: str | Path) -> bool:
        try:
            self.store.load(path)
        except StoreError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot open results", str(exc))
            return False
        self.load_team_into_form(self._form_team)
        self._update_file_ui()
        return True

    def save_results(self) -> None:
        if self.store.path is None:
            self.save_results_as()
        else:
            self.store.request_save()
        self._update_file_ui()

    def save_results_as(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save results", str(self.store.path or "results.json"), "Scoreboard results (*.json);;All files (*)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            self.store.save(path)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot save results", str(exc))
            return
        self._update_file_ui()

    def new_results(self) -> None:
        path = self.store.path
        detail = (
            f"\n\nWith autosave on, {path.name} is overwritten with the empty sheet "
            f"(the previous version is kept as {path.name}.bak)."
            if path and self.store.autosave
            else ""
        )
        answer = QtWidgets.QMessageBox.question(
            self, "New results", "Clear all results and start a fresh sheet?" + detail
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if not self._confirm_replace("clear the results"):
            return
        self.staged.drop_all()
        self.store.clear_all()
        self.load_team_into_form(self._form_team)
        self._log("system", "results cleared")

    def export_csv(self) -> None:
        suggested = (self.store.path or Path("results.json")).with_suffix(".csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export CSV", str(suggested), "CSV file (*.csv)")
        if not path:
            return
        try:
            self.store.export_csv(path)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot export", str(exc))
            return

    def _update_file_ui(self) -> None:
        path = self.store.path
        self.file_label.setText(str(path) if path else "(no file chosen \u2014 use Save as)")
        self.autosave_box.blockSignals(True)  # syncing the tick must not change the policy
        self.autosave_box.setChecked(self.store.autosave)
        self.autosave_box.blockSignals(False)

    # -------------------------------------------------------------- other actions

    def reset_gates(self) -> None:
        """Send ``r`` to the gates and stop the scoreboard's counting timer."""
        abort = getattr(self.scoreboard, "abort_run", None)
        if abort is not None:
            abort(reason="reset button")
        sent = self.link.reset_gates()
        self._log("system", "reset ('r') sent to the gates" if sent else "gates offline: timer stopped, reset not sent")

    def show_scoreboard(self) -> None:
        if self.scoreboard is None:
            return
        self.scoreboard.show()
        self.scoreboard.raise_()
        self.scoreboard.activateWindow()

    def _set_form_status(self, text: str, role: str = "hint") -> None:
        self.form_status.setText(text)
        theme.set_state(self.form_status, "role", role)

    def _log(self, kind: str, text: str, level: str = "") -> None:
        colours = {
            "rx": theme.color("aqua_b"),
            "tx": theme.color("yellow_b"),
            "system": theme.color(LOG_COLOURS.get(level, "gray0")),
            "junk": theme.color("red_b"),
        }
        colour = colours.get(kind, theme.color("fg1"))
        self.log_view.appendHtml(
            f'<span style="color:{theme.color("bg4")}">{_stamp()}</span>  '
            f'<span style="color:{colour}">{kind:<7}{_escape(text)}</span>'
        )
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def notify_system(self, text: str, *, level: str = "") -> None:
        """Public hook for start-up messages raised before the desk existed."""
        self._log("system", text, level=level)

    def _show_protocol_help(self) -> None:
        QtWidgets.QMessageBox.information(
            self,
            "Gate protocol",
            "Received from the gates (one ASCII line each)\n"
            "  s          start of a run \u2192 the big timer counts up\n"
            "  e,10.10    end of run, 10.10 s elapsed (10 ms steps)\n"
            "  r          someone pressed the reset button on the gates\n\n"
            "Sent to the gates\n"
            "  r          reset the gates, and stop the scoreboard timer\n\n"
            "Times are kept as whole centiseconds, so 10.10 is exactly 1010.",
        )

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.store.flush()
        self.write_settings()
        super().closeEvent(event)
