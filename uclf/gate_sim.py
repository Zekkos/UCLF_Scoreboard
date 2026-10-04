"""A tiny gate simulator window, shown for ``python -m uclf --mock``.

It only ever talks to :class:`~uclf.links.SimulatedLink`, so the whole app can be
exercised - including the "gates vanished" case - without any hardware.
"""

from __future__ import annotations

import random

from qtpy import QtWidgets

from . import theme
from .links import SimulatedLink


class GateSimulator(QtWidgets.QMainWindow):
    """Press the buttons the way the physical gate box does."""

    def __init__(self, link: SimulatedLink, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.link = link
        self._running = False
        self.setWindowTitle("Gate simulator")
        self.resize(340, 300)

        central = QtWidgets.QWidget()
        central.setObjectName("PageRoot")
        self.setCentralWidget(central)
        box = QtWidgets.QVBoxLayout(central)
        box.setContentsMargins(14, 14, 14, 14)
        box.setSpacing(10)

        self.start_btn = QtWidgets.QPushButton("Start  (s)")
        self.start_btn.setProperty("role", "primary")
        self.start_btn.clicked.connect(self.send_start)
        self.finish_btn = QtWidgets.QPushButton("Finish  (e,<time>)")
        self.finish_btn.clicked.connect(self.send_finish)
        self.reset_btn = QtWidgets.QPushButton("Reset  (r)")
        self.reset_btn.setProperty("role", "danger")
        self.reset_btn.clicked.connect(self.send_reset)
        for button in (self.start_btn, self.finish_btn, self.reset_btn):
            box.addWidget(button)

        row = QtWidgets.QHBoxLayout()
        self.seconds = QtWidgets.QDoubleSpinBox()
        self.seconds.setRange(0.01, 600.0)
        self.seconds.setDecimals(2)
        self.seconds.setSingleStep(0.05)
        self.seconds.setValue(10.10)
        self.random_btn = QtWidgets.QPushButton("Random")
        self.random_btn.setToolTip("Use a random time for the next finish")
        self.random_btn.clicked.connect(lambda: self.seconds.setValue(round(random.uniform(8.0, 14.0), 2)))
        row.addWidget(QtWidgets.QLabel("Finish time (s)"))
        row.addWidget(self.seconds, 1)
        row.addWidget(self.random_btn)
        box.addLayout(row)

        self.online_box = QtWidgets.QCheckBox("Gates plugged in")
        self.online_box.setChecked(True)
        self.online_box.setToolTip("Unplug the USB device while the scoreboard is running")
        self.online_box.toggled.connect(self._online_changed)
        box.addWidget(self.online_box)

        self.note = QtWidgets.QLabel("sends frames straight into the app")
        theme.set_state(self.note, "role", "hint")
        box.addWidget(self.note)
        box.addStretch(1)

        self.link.connected_changed.connect(self._from_link)
        self._sync()

    # ------------------------------------------------------------------ actions
    def send_start(self) -> None:
        self.link.inject("s")
        self._running = True
        self._sync()

    def send_finish(self) -> None:
        self.link.inject(f"e,{self.seconds.value():.2f}")
        self._running = False
        self._sync()

    def send_reset(self) -> None:
        self.link.inject("r")
        self._running = False
        self._sync()

    def _online_changed(self, on: bool) -> None:
        self.link.set_connected(on)
        self._sync()

    def _from_link(self, _connected: bool) -> None:
        self.online_box.blockSignals(True)
        self.online_box.setChecked(self.link.connected)
        self.online_box.blockSignals(False)
        self._sync()

    def _sync(self) -> None:
        self.finish_btn.setEnabled(self._running)
        self.start_btn.setEnabled(not self._running)
        state = "counting" if self._running else "idle"
        self.note.setText(f"gates {'plugged in' if self.link.connected else 'unplugged'} - timer {state}")
        theme.set_state(self.note, "role", "hint" if self.link.connected else "bad")
