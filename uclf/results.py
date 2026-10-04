"""The results model: teams, their (up to three) race times, and persistence.

:class:`ResultStore` is the single source of truth shared by the input window
and the scoreboard; both views listen to :attr:`ResultStore.changed`, so an
accepted edit repaints both windows immediately.

On disk a store is a small JSON document.  Times are integer centiseconds (see
:mod:`uclf.timeutil`) and writes are atomic - the document goes to a temporary
file in the same directory and is then ``os.replace``-d over the target, so a
crash mid-write can never leave a half-written results file.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from qtpy import QtCore

from .timeutil import CS_PER_SECOND, MAX_CS, InvalidTime, format_time, parse_time

__all__ = ["MAX_TIMES", "SOURCES", "ResultRecord", "ResultStore", "StoreError"]

#: Each team may hold at most three race times.
MAX_TIMES = 3
SOURCES = ("none", "manual", "serial")
SCHEMA_VERSION = 1


class StoreError(RuntimeError):
    """Raised for unreadable or invalid results files."""


@dataclass
class ResultRecord:
    """One team: three time slots plus where each came from."""

    team: int
    times: list[int | None] = field(default_factory=lambda: [None] * MAX_TIMES)
    sources: list[str] = field(default_factory=lambda: ["none"] * MAX_TIMES)

    def __post_init__(self) -> None:
        # Normalise ragged or odd lists that may come from a hand-edited file.
        self.times = (list(self.times) + [None] * MAX_TIMES)[:MAX_TIMES]
        self.times = [None if value is None else int(value) for value in self.times]
        padded = list(self.sources or []) + ["none"] * MAX_TIMES
        self.sources = [(value if value in SOURCES else "none") for value in padded[:MAX_TIMES]]

    @property
    def valid_times(self) -> list[int]:
        return [t for t in self.times if t is not None]

    @property
    def best(self) -> int | None:
        return min(self.valid_times, default=None)

    @property
    def sorted_times(self) -> list[int]:
        return sorted(self.valid_times)

    def copy(self) -> ResultRecord:
        return ResultRecord(self.team, list(self.times), list(self.sources))


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class ResultStore(QtCore.QObject):
    """Teams + times, with change signals and debounced autosave."""

    changed = QtCore.Signal()  # any edit to a team or time
    message = QtCore.Signal(str, str)  # ("info"|"warn"|"error", text)
    dirty_changed = QtCore.Signal(bool)  # False once the file is on disk

    def __init__(self, path: Path | str | None = None, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._records: dict[int, ResultRecord] = {}
        self._path: Path | None = Path(path) if path else None
        self._dirty = False
        self._autosave_enabled = True
        self.autosave_delay_ms = 400
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._autosave)

    # ------------------------------------------------------------------ getters

    @property
    def path(self) -> Path | None:
        return self._path

    def set_path(self, path: Path | str | None) -> None:
        self._path = Path(path) if path else None

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def autosave(self) -> bool:
        return self._autosave_enabled

    @autosave.setter
    def autosave(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._autosave_enabled:
            return
        self._autosave_enabled = enabled
        if enabled:
            self._schedule_save()
        else:
            self._timer.stop()

    def teams(self) -> list[int]:
        return sorted(self._records)

    def record(self, team: int) -> ResultRecord | None:
        record = self._records.get(int(team))
        return record.copy() if record else None

    def counts(self) -> tuple[int, int]:
        """``(teams, runs recorded)`` - used by the status bars."""
        return len(self._records), sum(len(r.valid_times) for r in self._records.values())

    def ranked(self) -> list[tuple[int, ResultRecord]]:
        """Teams ordered fastest -> slowest, each with its rank.

        Rank comes from the best time; equal best times are split by the next
        fastest run and finally by team number, so the order is stable.  Teams
        with nothing recorded sink to the bottom.
        """
        sentinel = MAX_CS + 1
        ordered = sorted(
            self._records.values(),
            key=lambda r: (r.best is None, r.sorted_times + [sentinel] * (MAX_TIMES - len(r.sorted_times)), r.team),
        )
        return [(i + 1, record.copy()) for i, record in enumerate(ordered)]

    # -------------------------------------------------------------------- edits

    def ensure_team(self, team: int) -> ResultRecord:
        team = int(team)
        record = self._records.get(team)
        if record is None:
            self._records[team] = ResultRecord(team=team)
            self._touch()
        return self._records[team].copy()

    def remove_team(self, team: int) -> bool:
        if self._records.pop(int(team), None) is None:
            return False
        self._touch()
        return True

    def rename_team(self, old: int, new: int) -> str | None:
        """Change a team number, keeping its times.  Returns an error string."""
        old, new = int(old), int(new)
        if old not in self._records:
            return f"team {old} does not exist"
        if not 1 <= new <= 9999:
            return "team numbers run from 1 to 9999"
        if new in self._records:
            return f"team {new} already exists"
        record = self._records.pop(old)
        record.team = new
        self._records[new] = record
        self._touch()
        return None

    def set_time(self, team: int, slot: int, value: int | str | None, *, source: str = "manual") -> bool:
        """Write ``value`` into ``slot`` (0-based) of ``team``.

        ``value`` is centiseconds, a string time such as ``"10.10"``, or
        ``None``/``""`` to blank the slot.  Returns ``True`` when the store
        changed.  Creating a team and submitting no times is not an edit.
        """
        slot = int(slot)
        if not 0 <= slot < MAX_TIMES:
            raise IndexError(f"time slot must be 0..{MAX_TIMES - 1}")
        team = int(team)
        if isinstance(value, str):
            value = parse_time(value) if value.strip() else None
        if value is not None:
            value = int(value)
            if not 0 < value <= MAX_CS:
                raise InvalidTime(f"time must be between 0.01 s and {format_time(MAX_CS)} s")
        source = source if source in SOURCES else "manual"

        record = self._records.get(team)
        if record is None:
            if value is None:
                return False
            record = ResultRecord(team=team)
            self._records[team] = record
        new_source = source if value is not None else "none"
        if record.times[slot] == value and record.sources[slot] == new_source:
            return False
        record.times[slot] = value
        record.sources[slot] = new_source
        self._touch()
        return True

    def next_free_slot(self, team: int) -> int | None:
        record = self._records.get(int(team))
        if record is None:
            return 0
        for slot, value in enumerate(record.times):
            if value is None:
                return slot
        return None

    def append_serial_time(self, team: int, centiseconds: int) -> int | None:
        """Auto-record a gate time into the team's next free slot.

        Returns the slot written, or ``None`` when all three are already used
        (the operator must then overwrite one deliberately).
        """
        slot = self.next_free_slot(team)
        if slot is None:
            return None
        self.set_time(team, slot, centiseconds, source="serial")
        return slot

    def clear_team_times(self, team: int) -> bool:
        record = self._records.get(int(team))
        if record is None or not record.valid_times:
            return False
        record.times = [None] * MAX_TIMES
        record.sources = ["none"] * MAX_TIMES
        self._touch()
        return True

    def clear_all(self) -> None:
        if not self._records:
            return
        self._records.clear()
        self._touch()

    def replace_all(self, records: dict[int, ResultRecord] | list[ResultRecord]) -> None:
        source = records.values() if isinstance(records, dict) else records
        self._records = {int(record.team): record.copy() for record in source}
        self._touch()

    # -------------------------------------------------------------- persistence

    def to_document(self) -> dict:
        return {
            "format": "uclf-scoreboard",
            "version": SCHEMA_VERSION,
            "saved_at": _now_iso(),
            "unit": f"centiseconds (1/{CS_PER_SECOND} s)",
            "max_times": MAX_TIMES,
            "teams": [
                {"team": record.team, "times": list(record.times), "sources": list(record.sources)}
                for record in sorted(self._records.values(), key=lambda r: r.team)
            ],
        }

    def save(self, path: Path | str | None = None) -> Path:
        """Atomically write the document; also keeps one ``.bak`` of the previous save."""
        target = Path(path or self._path)
        if target.parent != Path(""):
            target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_document(), indent=2, ensure_ascii=False) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent or Path(".")), prefix=target.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists():
                bak = target.with_suffix(target.suffix + ".bak")
                try:
                    bak.write_bytes(target.read_bytes())
                except OSError:  # a missing backup is not worth failing the save over
                    pass
            os.replace(tmp_name, target)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self._path = target
        self._set_dirty(False)
        self.message.emit("info", f"saved {len(self._records)} team(s) to {target.name}")
        return target

    def load(self, path: Path | str) -> Path:
        source = Path(path)
        try:
            doc = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StoreError(f"file not found: {source}") from None
        except json.JSONDecodeError as exc:
            raise StoreError(f"{source.name} is not valid JSON (line {exc.lineno}, column {exc.colno})") from None
        except UnicodeDecodeError:
            raise StoreError(f"{source.name} is not a text file") from None
        self._records = self.from_document(doc)
        self._path = source
        self._timer.stop()
        self._set_dirty(False)
        self.changed.emit()
        self.message.emit("info", f"loaded {len(self._records)} team(s) from {source.name}")
        return source

    @classmethod
    def from_document(cls, doc: object) -> dict[int, ResultRecord]:
        if not isinstance(doc, dict):
            raise StoreError("a results file must contain a JSON object")
        entries = doc.get("teams", [])
        if not isinstance(entries, list):
            raise StoreError("'teams' must be a list")
        records: dict[int, ResultRecord] = {}
        for position, item in enumerate(entries):
            if not isinstance(item, dict):
                raise StoreError(f"team #{position + 1} is not an object")
            try:
                team = int(item.get("team"))
            except (TypeError, ValueError):
                raise StoreError(f"team #{position + 1} has no usable team number") from None
            if not 1 <= team <= 9999:
                raise StoreError(f"team number {team} is outside 1..9999")
            if team in records:
                raise StoreError(f"team {team} appears twice")
            times_in = item.get("times") or []
            sources_in = item.get("sources") or []
            if not isinstance(times_in, list) or not isinstance(sources_in, list):
                raise StoreError(f"team {team} has malformed 'times' or 'sources'")
            times = [cls._parse_stored_time(value, team) for value in times_in[:MAX_TIMES]]
            records[team] = ResultRecord(team=team, times=times, sources=[str(s) for s in sources_in])
        return records

    @classmethod
    def _parse_stored_time(cls, value: object, team: int) -> int | None:
        """Numbers are centiseconds; strings are re-parsed as entered times."""
        if value is None:
            return None
        if isinstance(value, bool):
            raise StoreError(f"team {team} has a boolean time")
        if isinstance(value, (int, float)):
            parsed = int(round(float(value)))
        elif isinstance(value, str):
            if not value.strip():
                return None
            try:
                parsed = parse_time(value)
            except InvalidTime:
                raise StoreError(f"team {team} has an unreadable time: {value!r}") from None
        else:
            raise StoreError(f"team {team} has an invalid time entry: {value!r}")
        if not 0 < parsed <= MAX_CS:
            raise StoreError(f"team {team} has an out-of-range time: {value!r}")
        return parsed

    def export_csv(self, path: Path | str) -> Path:
        """Ranked CSV: rank, team, one column per time (seconds), best."""
        target = Path(path)
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["rank", "team", *[f"time_{i + 1}_s" for i in range(MAX_TIMES)], "best_s"])
            for rank, record in self.ranked():
                writer.writerow(
                    [rank, record.team, *[format_time(t, "") for t in record.times], format_time(record.best, "")]
                )
        self.message.emit("info", f"exported {len(self._records)} team(s) to {target.name}")
        return target

    def request_save(self) -> None:
        """Explicit save (File > Save); bypasses the autosave debounce."""
        if self._path is None:
            self.message.emit("warn", "no file chosen yet - use Save as")
            return
        try:
            self.save()
        except OSError as exc:
            self.message.emit("error", f"could not save: {exc}")
            self._set_dirty(True)

    def mark_saved(self) -> None:
        """Treat the file on disk as up to date without writing it."""
        self._timer.stop()
        self._set_dirty(False)

    def flush(self) -> bool:
        """Save right now if anything is pending (called when the window closes)."""
        self._timer.stop()
        if not self._dirty or self._path is None:
            return False
        self.request_save()
        return True

    # ----------------------------------------------------------------- internal

    def _touch(self) -> None:
        self.changed.emit()
        self._set_dirty(True)
        self._schedule_save()

    def _set_dirty(self, dirty: bool) -> None:
        if dirty == self._dirty:
            return
        self._dirty = dirty
        self.dirty_changed.emit(dirty)

    def _schedule_save(self) -> None:
        if self._autosave_enabled and self._path is not None and self._dirty:
            self._timer.start(self.autosave_delay_ms)

    def _autosave(self) -> None:
        if not self._autosave_enabled or self._path is None or not self._dirty:
            return
        try:
            self.save()
        except OSError as exc:
            self.message.emit("error", f"autosave failed: {exc}")
            self._timer.start(2000)  # keep trying, gently, so nothing is lost
