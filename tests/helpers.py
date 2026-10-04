"""Tiny helpers so the suite does not need pytest-qt."""

from __future__ import annotations

from qtpy import QtCore


def pump(ms: int = 60) -> None:
    """Process Qt events for about ``ms`` milliseconds (queued signals + timers)."""
    timer = QtCore.QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QtCore.QCoreApplication.processEvents()
        QtCore.QThread.usleep(2000)


def signal_args(signal, timeout_ms: int = 2000) -> tuple | None:
    """Blocks until ``signal`` fires once; returns its arguments, or ``None`` on timeout."""
    captured: list = []

    def slot(*args):
        captured.append(args)

    signal.connect(slot)
    timer = QtCore.QElapsedTimer()
    timer.start()
    while not captured and timer.elapsed() < timeout_ms:
        QtCore.QCoreApplication.processEvents()
        QtCore.QThread.usleep(2000)
    signal.disconnect(slot)
    return captured[0] if captured else None


def signal_count(signal, ms: int) -> int:
    """How many times ``signal`` fires during ``ms`` of event processing."""
    state = {"n": 0}

    def slot(*_args):
        state["n"] += 1

    signal.connect(slot)
    pump(ms)
    signal.disconnect(slot)
    return state["n"]
