"""Glue between the gate MCU and the GUI.

Two implementations of :class:`GateLink`:

``SerialLink``
    Real hardware.  A worker thread owns the pyserial handle, reads lines and
    transparently survives the USB device being pulled and plugged back in.
``SimulatedLink``
    No hardware: tests and demos inject ``s`` / ``e,10.10`` / ``r`` directly.

Keeping the GUI behind one interface means the windows never touch pyserial and
can be exercised head-less.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from qtpy import QtCore

from . import protocol

__all__ = ["GateLink", "SerialLink", "SimulatedLink", "available_ports", "default_port", "port_present"]


def available_ports() -> list[tuple[str, str]]:
    """``[(device, description), ...]`` for the currently attached serial ports."""
    try:  # imported lazily so the GUI still starts without pyserial installed
        import serial.tools.list_ports
    except ImportError:  # pragma: no cover
        return []
    return [(p.device, p.description) for p in serial.tools.list_ports.comports()]


def port_present(port: str) -> bool:
    """Is ``port`` still attached?  Used to spot a USB device being unplugged."""
    if not port:
        return False
    if port.upper().startswith("COM"):  # Windows: only list_ports can answer
        return port.upper() in {d.upper() for d, _ in available_ports()}
    return Path(port).exists()


def default_port() -> str | None:
    """Best guess at the gates port: a ``ttyACM`` device, then any USB serial, then anything."""
    ports = sorted(d for d, _ in available_ports())
    if not ports:
        return None
    for needle in ("ACM", "USB"):
        matches = [p for p in ports if needle in p.upper()]
        if matches:
            return matches[0]
    return ports[0]


class GateLink(QtCore.QObject):
    """Interface used by the windows."""

    raw_line = QtCore.Signal(str)  # inbound text line, newline stripped
    tx_line = QtCore.Signal(str)  # outbound text line
    event = QtCore.Signal(object)  # protocol.Event
    connected_changed = QtCore.Signal(bool)
    status = QtCore.Signal(str)  # human readable one-liner for the log

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_status(self) -> str:
        """Most recent status text, for views that were built after the link."""
        return getattr(self, "_last_status", "")

    # --- helpers shared by the implementations -------------------------------
    def deliver(self, line: str) -> None:
        """Push one received line through the parser and emit the results."""
        event = protocol.parse_line(line)
        self.raw_line.emit(line)
        if event is not None:
            self.event.emit(event)

    # --- API -----------------------------------------------------------------
    def send(self, text: str) -> bool:
        """Send one command line; returns ``False`` when not connected."""
        raise NotImplementedError

    def reset_gates(self) -> bool:
        """Transmit the reset command (``r``)."""
        return self.send("r")

    def stop(self) -> None:
        """Release resources.  Overridden by :class:`SerialLink`."""

    def note(self, text: str) -> None:
        """Emit a status line that did not come off the wire."""
        self.status.emit(text)

    def describe(self) -> str:
        return type(self).__name__


class SerialLink(GateLink):
    """pyserial reader/writer with hot-plug recovery.

    The worker thread owns the file descriptor.  Every failure - device
    unplugged, ``OSError`` mid-read, wrong baud rate, port busy - is handled the
    same way: close the handle, report the disconnect and keep retrying the
    open until the port answers again.  The GUI thread only ever sees signals.
    """

    RETRY_SECONDS = 1.0
    POLL_SECONDS = 0.02
    PRESENT_CHECK_SECONDS = 2.0

    def __init__(
        self,
        port: str | None = None,
        baudrate: int = 115200,
        *,
        auto_connect: bool = True,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._port = port
        self._baudrate = int(baudrate)
        self._auto_connect = bool(auto_connect)
        self._tx: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._reopen = threading.Event()
        self._last_status = ""
        self._thread = threading.Thread(target=self.run, name="gate-serial", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------- settings
    @property
    def port(self) -> str | None:
        with self._lock:
            return self._port

    @property
    def baudrate(self) -> int:
        with self._lock:
            return self._baudrate

    @property
    def auto_connect(self) -> bool:
        with self._lock:
            return self._auto_connect

    def configure(self, *, port: str | None = None, baudrate: int | None = None, auto_connect: bool | None = None) -> None:
        """Update port/baud/enabled.  Any change forces an immediate reopen."""
        with self._lock:
            changed = False
            if port is not None and port != self._port:
                self._port = port.strip() or None
                changed = True
            if baudrate is not None and int(baudrate) != self._baudrate:
                self._baudrate = int(baudrate)
                changed = True
            if auto_connect is not None and bool(auto_connect) != self._auto_connect:
                self._auto_connect = bool(auto_connect)
                changed = True
        if changed:
            self._reopen.set()

    # ------------------------------------------------------------- lifecycle
    def stop(self, timeout: float = 3.0) -> None:
        """Ask the worker to finish and wait for it (idempotent)."""
        self._stop.set()
        self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def describe(self) -> str:
        port, baud = self._settings()[:2]
        return f"{port or 'no port selected'} @ {baud}"

    # ------------------------------------------------------------------ send
    def send(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        if not self.connected:
            self._report(f"Gates not connected - '{text}' not sent")
            return False
        self._tx.put(text)
        return True

    # ------------------------------------------------------------------ thread
    def run(self) -> None:
        ser = None
        buffer = b""
        next_attempt = 0.0
        next_presence_check = 0.0
        opened: tuple[str, int] | None = None
        while not self._stop.is_set():
            port, baudrate, enabled = self._settings()

            if not enabled:
                ser = self._drop(ser, "connection disabled")
                self._connected_set(False, "connection disabled")
                self._pause(0.25)
                continue
            if not port:
                ser = self._drop(ser, "no serial port selected")
                self._connected_set(False, "no serial port selected")
                self._pause(0.25)
                continue

            if ser is not None and opened != (port, baudrate):
                # the operator picked another port or baud rate: hang up, then redial
                ser = self._drop(ser, f"reconfiguring the link to {port} @ {baudrate}")
                opened = None

            if ser is None:
                now = time.monotonic()
                if not self._reopen.is_set() and now < next_attempt:
                    self._pause(min(self.POLL_SECONDS, max(next_attempt - now, 0.001)))
                    continue
                self._reopen.clear()
                next_attempt = time.monotonic() + self.RETRY_SECONDS
                try:
                    ser = self._open(port, baudrate)
                except Exception as exc:  # pyserial raises a wide family of errors
                    self._connected_set(False, f"{port} unavailable: {exc}")
                    continue
                buffer = b""
                opened = (port, baudrate)
                next_presence_check = time.monotonic() + self.PRESENT_CHECK_SECONDS
                self._connected_set(True, f"connected to {port} @ {baudrate}")
                continue

            chunk = self._pump(ser)
            if chunk is None:  # transmit/receive error -> reconnect
                opened = None
                ser = self._drop(ser, "link lost, retrying")
                next_attempt = time.monotonic() + self.RETRY_SECONDS
                continue
            if chunk:
                events, buffer = protocol.feed(buffer, chunk)
                for event in events:
                    self.deliver(event.line)
            if time.monotonic() >= next_presence_check:
                next_presence_check = time.monotonic() + self.PRESENT_CHECK_SECONDS
                if not port_present(port):
                    opened = None
                    ser = self._drop(
                        ser, f"{port} disappeared (USB unplugged?) - waiting for it to come back"
                    )
                    next_attempt = time.monotonic() + self.RETRY_SECONDS

        self._drop(ser, "closed")
        self._connected_set(False, "closed")

    def _settings(self) -> tuple[str | None, int, bool]:
        with self._lock:
            return self._port, self._baudrate, self._auto_connect

    def _pause(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def _open(self, port: str, baudrate: int):
        import serial

        handle = serial.Serial(port=port, baudrate=baudrate, timeout=0.1, write_timeout=1.0)
        try:  # discard anything the MCU queued while we were away
            handle.reset_input_buffer()
        except Exception:
            pass
        return handle

    def _pump(self, ser) -> bytes | None:
        """Flush pending commands, then read what is available.  ``None`` = link lost."""
        try:
            self._drain_tx(ser)
            waiting = int(getattr(ser, "in_waiting", 0) or 0)
            chunk = ser.read(waiting or 1)
        except Exception as exc:
            dropped = self._flush_tx()
            if dropped:
                self._report(f"{dropped} queued command(s) dropped while reconnecting")
            self._report(f"read failed ({type(exc).__name__}: {exc})")
            return None
        return chunk or b""

    def _drain_tx(self, ser) -> None:
        while True:
            try:
                command = self._tx.get_nowait()
            except queue.Empty:
                return
            ser.write(protocol.encode(command))
            ser.flush()
            self.tx_line.emit(command)

    def _flush_tx(self) -> int:
        dropped = 0
        while True:
            try:
                self._tx.get_nowait()
                dropped += 1
            except queue.Empty:
                return dropped

    def _drop(self, ser, reason: str):
        """Close the handle (if any) and make the offline state visible."""
        if ser is None:
            return None
        try:
            ser.close()
        except Exception:
            pass
        self._connected_set(False, reason)
        return None

    def _connected_set(self, connected: bool, text: str) -> None:
        changed = connected != self._connected
        if changed:
            self._connected = connected
            self.connected_changed.emit(connected)
        if changed or text != self._last_status:
            self._report(text)

    def _report(self, text: str) -> None:
        if text and text != self._last_status:
            self._last_status = text
            self.status.emit(text)

    @property
    def last_status(self) -> str:
        return self._last_status


class SimulatedLink(GateLink):
    """In-process stand-in for the gates (tests, demos, development without hardware)."""

    def __init__(self, connected: bool = True, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._connected = connected
        self.sent: list[str] = []

    def describe(self) -> str:
        return "simulated gates"

    def send(self, text: str) -> bool:
        text = text.strip()
        if not self.connected or not text:
            self.status.emit("simulated link is offline")
            return False
        self.sent.append(text)
        self.tx_line.emit(text)
        return True

    def inject(self, line: str) -> None:
        """Simulate one line arriving from the MCU."""
        self.deliver(line.strip())

    def inject_run(self, seconds: float = 10.10) -> None:
        """Simulate a complete run: start then finish."""
        self.inject("s")
        self.inject(f"e,{seconds:.2f}")

    def set_connected(self, connected: bool) -> None:
        connected = bool(connected)
        if connected != self._connected:
            self._connected = connected
            self.connected_changed.emit(connected)
            self.status.emit("simulated link online" if connected else "simulated link offline")
