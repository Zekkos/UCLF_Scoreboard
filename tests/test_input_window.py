"""Tests for the operator console: capture, submit, edit, persist, reset.

The gate MCU is replaced by :class:`~uclf.links.SimulatedLink`, so these run
head-less on the offscreen Qt platform.
"""

from __future__ import annotations

import json

import pytest
from helpers import pump

from uclf.input_window import InputWindow
from uclf.links import SimulatedLink
from uclf.results import MAX_TIMES, ResultStore
from uclf.scoreboard_window import ScoreboardWindow


def type_into(edit, text: str) -> None:
    """Set a line edit's text and emit ``textEdited`` as a keyboard would."""
    edit.setText(text)
    edit.textEdited.emit(text)


@pytest.fixture()
def env(qapp, settings, tmp_path):
    path = tmp_path / "results.json"
    store = ResultStore(path)
    link = SimulatedLink()
    board = ScoreboardWindow(store, link)
    desk = InputWindow(store, link, scoreboard=board, settings=settings)
    yield {"store": store, "link": link, "desk": desk, "board": board, "path": path, "settings": settings}
    desk.close()
    board.close()


# ------------------------------------------------------------------ capturing


def test_manual_entry_needs_submit(env):
    desk, store = env["desk"], env["store"]
    desk.team_spin.setValue(7)
    type_into(desk.slot_edits[0], "12.34")
    assert store.teams() == [] and desk.table.rowCount() == 0, "result saved before Submit"
    assert desk.submit_btn.isEnabled(), "Submit should be waiting"
    assert desk.submit_form() is True
    assert store.record(7).times[0] == 1234
    assert desk.submit_btn.isEnabled() is False


def test_submit_rejects_bad_times_without_saving(env):
    desk, store = env["desk"], env["store"]
    desk.team_spin.setValue(3)
    type_into(desk.slot_edits[1], "12.3.4")
    assert desk.submit_form() is False
    assert store.teams() == []
    assert theme_role(desk.slot_edits[1]) == "invalid"
    type_into(desk.slot_edits[1], "12.34")
    assert desk.submit_form() is True
    assert store.record(3).times == [None, 1234, None]


def test_submit_overwrites_and_blanks_slots(env):
    desk, store = env["desk"], env["store"]
    desk.team_spin.setValue(4)
    type_into(desk.slot_edits[0], "10.00")
    type_into(desk.slot_edits[1], "11.00")
    desk.submit_form()
    desk.team_spin.setValue(5)  # switch teams: the form reloads empty
    desk.team_spin.setValue(4)
    assert [e.text() for e in desk.slot_edits] == ["10.00", "11.00", ""]
    type_into(desk.slot_edits[1], "")  # blank the second run
    desk.submit_form()
    assert store.record(4).times == [1000, None, None]


def test_switching_teams_asks_before_losing_edits(env, monkeypatch):
    desk, store = env["desk"], env["store"]
    desk.team_spin.setValue(2)
    type_into(desk.slot_edits[0], "9.99")
    answers = iter([False, True])  # first "No", then "Yes"
    monkeypatch.setattr(desk, "_confirm_discard", lambda team: next(answers))
    assert desk.team_spin.value() == 2, "the team should not have moved"
    desk.team_spin.setValue(8)
    assert desk.team_spin.value() == 2, "cancel must put the team back"
    desk.team_spin.setValue(8)
    assert desk.team_spin.value() == 8
    assert store.record(2) is None, "discarded edits must not be saved"


def test_editing_a_saved_time_in_the_table(env):
    desk, store = env["desk"], env["store"]
    store.set_time(5, 0, "10.00")
    desk.refresh_table()
    item = desk.table.item(0, 2)
    item.setText("9.85")
    desk._table_edited(item)
    assert store.record(5).times[0] == 985
    assert store.record(5).sources[0] == "manual"


def test_rejecting_a_bad_table_edit_keeps_the_old_value(env):
    desk, store = env["desk"], env["store"]
    store.set_time(5, 0, 1000)
    desk.refresh_table()
    item = desk.table.item(0, 2)
    item.setText("nonsense")
    desk._table_edited(item)
    assert store.record(5).times[0] == 1000
    assert desk.table.item(0, 2).text() == "10.00"


def test_renaming_a_team_in_the_table(env):
    desk, store = env["desk"], env["store"]
    store.set_time(5, 0, 1000)
    desk.refresh_table()
    item = desk.table.item(0, 1)
    item.setText("11")
    desk._table_edited(item)
    assert store.record(5) is None
    assert store.record(11).times[0] == 1000


def test_adding_and_deleting_a_team(env, monkeypatch):
    desk, store = env["desk"], env["store"]
    monkeypatch.setattr(
        "uclf.input_window.QtWidgets.QInputDialog.getInt", lambda *a, **k: (12, True)
    )
    desk.add_team()
    assert store.record(12) is not None
    assert desk.table.rowCount() == 1
    monkeypatch.setattr(
        "uclf.input_window.QtWidgets.QMessageBox.question",
        lambda *a, **k: __import__("qtpy").QtWidgets.QMessageBox.StandardButton.Yes,
    )
    desk.delete_selected_team()
    assert store.record(12) is None


# ---------------------------------------------------------- serial gate times


def test_gate_time_fills_the_form_but_waits_for_submit(env):
    desk, store, link = env["desk"], env["store"], env["link"]
    desk.team_spin.setValue(6)
    link.inject("s")
    link.inject("e,10.10")
    assert desk.slot_edits[0].text() == "10.10", "the gate time was not auto-filled"
    assert store.record(6) is None, "auto-fill must not save by itself"
    assert desk.staged.count == 1
    desk.submit_form()
    assert store.record(6).times[0] == 1010
    assert store.record(6).sources[0] == "serial"
    assert desk.staged.count == 0, "submitted time should leave the staged list"


def test_successive_runs_fill_successive_slots(env):
    desk, store, link = env["desk"], env["store"], env["link"]
    desk.team_spin.setValue(6)
    link.inject_run(10.10)
    link.inject_run(9.85)
    link.inject_run(9.90)
    assert [e.text() for e in desk.slot_edits] == ["10.10", "9.85", "9.90"]
    link.inject_run(9.80)  # fourth run: nowhere to go in the form, stays unplaced
    assert desk.staged.count == 4 and desk.staged.free_count == 1
    desk.submit_form()
    assert store.record(6).times == [1010, 985, 990]


def test_auto_record_writes_straight_to_the_selected_team(env):
    desk, store, link = env["desk"], env["store"], env["link"]
    desk.team_spin.setValue(9)
    desk.auto_box.setChecked(True)
    link.inject("s")
    link.inject("e,8.15")
    assert store.record(9).times[0] == 815
    assert store.record(9).sources[0] == "serial"
    assert desk.staged.count == 0


def test_auto_record_stops_after_three_times(env):
    desk, store, link = env["desk"], env["store"], env["link"]
    desk.team_spin.setValue(9)
    desk.auto_box.setChecked(True)
    for value in (8.15, 8.20, 8.10):
        link.inject(f"e,{value}")
    assert len(store.record(9).valid_times) == 3
    link.inject("e,7.00")
    assert len(store.record(9).valid_times) == 3
    assert desk.staged.count == 1


def test_staged_time_can_be_placed_later(env):
    desk, store, link = env["desk"], env["store"], env["link"]
    desk.team_spin.setValue(1)
    link.inject("e,10.00")
    link.inject("e,11.00")
    desk.blank_slots()
    assert desk.staged.count == 2
    desk.place_staged(1)
    assert desk.slot_edits[1].text() == "11.00", "newest staged time should be placed"
    desk.submit_form()
    assert store.record(1).times == [None, 1100, None]


def test_start_and_reset_frames_are_reported(env):
    desk, link = env["desk"], env["link"]
    link.inject("s")
    assert "start" in desk.last_frame_label.text().lower()
    link.inject("r")
    assert "reset" in desk.last_frame_label.text().lower()


def test_undecodable_lines_are_logged_not_applied(env):
    desk, store, link = env["desk"], env["store"], env["link"]
    before = desk.log_view.toPlainText()
    link.inject("garbage 42")
    assert "garbage 42" in desk.log_view.toPlainText()
    assert desk.log_view.toPlainText() != before
    assert store.teams() == []


# ------------------------------------------------------------------- the reset


def test_reset_button_sends_r_and_stops_the_timer(env):
    desk, link, board = env["desk"], env["link"], env["board"]
    link.inject("s")
    assert board._state == "running"
    desk.reset_btn.click()
    assert link.sent == ["r"], "the reset command was not transmitted"
    assert board._state == "reset"
    assert board.timer_label.text() == "0.00"
    assert board._tick_timer.isActive() is False, "still counting after reset"


def test_reset_from_the_gates_stops_the_timer(env):
    link, board = env["link"], env["board"]
    link.inject("s")
    link.inject("r")
    assert board._state == "reset"
    assert board.timer_label.text() == "0.00"


def test_reset_while_offline_still_stops_the_timer(env):
    desk, link, board = env["desk"], env["link"], env["board"]
    link.set_connected(False)
    link.inject("s")
    desk.reset_btn.click()
    assert board.timer_label.text() == "0.00"
    assert link.sent == []


# -------------------------------------------------------------------- files


def test_autosave_writes_the_results_file(env):
    desk, store, path = env["desk"], env["store"], env["path"]
    desk.team_spin.setValue(3)
    type_into(desk.slot_edits[0], "10.10")
    desk.submit_form()
    pump(store.autosave_delay_ms + 300)
    assert path.exists()
    doc = json.loads(path.read_text())
    assert doc["teams"] == [{"team": 3, "times": [1010, None, None], "sources": ["manual", "none", "none"]}]
    assert desk.save_label.text() == "saved"


def test_save_as_and_open(env, tmp_path):
    desk, store = env["desk"], env["store"]
    other = tmp_path / "meeting.json"
    store.save(other)
    desk.store.set_path(other)
    desk.team_spin.setValue(4)
    type_into(desk.slot_edits[0], "12.00")
    desk.submit_form()
    pump(store.autosave_delay_ms + 300)
    doc = json.loads(other.read_text())
    assert {team["team"] for team in doc["teams"]} == {4}

    desk.store.clear_all()
    assert desk.load_results(other) is True
    assert desk.store.record(4).times[0] == 1200
    assert str(other) in desk.file_label.text()


def test_opening_a_corrupt_file_reports_instead_of_crashing(env, tmp_path):
    desk = env["desk"]
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not json")
    from qtpy import QtWidgets

    shown = []
    monkey_message = staticmethod(lambda *a, **k: shown.append(a[2] if len(a) > 2 else a))
    QtWidgets.QMessageBox.critical = monkey_message  # tests must not block
    assert desk.load_results(bad) is False
    assert shown and "not valid JSON" in str(shown[0])


def test_autosave_off_keeps_the_file_and_says_so(env):
    desk, store, path = env["desk"], env["store"], env["path"]
    desk.team_spin.setValue(2)
    type_into(desk.slot_edits[0], "10.00")
    desk.submit_form()
    pump(store.autosave_delay_ms + 300)
    assert json.loads(path.read_text())["teams"][0]["times"] == [1000, None, None]

    desk.autosave_box.setChecked(False)
    assert desk.save_label.text() == "saved"  # the switch-off clears the label
    type_into(desk.slot_edits[1], "11.00")
    desk.submit_form()
    pump(store.autosave_delay_ms + 300)
    assert desk.save_label.text() == "unsaved changes", "the desk must not hide pending edits"
    assert json.loads(path.read_text())["teams"][0]["times"] == [1000, None, None]

    desk.save_btn.click()  # an explicit Save writes them
    assert json.loads(path.read_text())["teams"][0]["times"] == [1000, 1100, None]
    assert desk.save_label.text() == "saved"


def test_link_status_and_traffic_are_visible(env):
    desk, link = env["desk"], env["link"]
    link.set_connected(True)
    assert "connected" in desk.link_pill.text().lower()
    link.inject("e,10.10")
    log = desk.log_view.toPlainText()
    assert "rx" in log and "e,10.10" in log
    link.reset_gates()
    assert "tx" in desk.log_view.toPlainText()


def test_settings_are_remembered(env):
    desk, settings = env["desk"], env["settings"]
    desk.team_spin.setValue(21)
    desk.auto_box.setChecked(True)
    desk.write_settings()
    assert settings.value("team") in ("21", 21)
    assert str(settings.value("autoRecord")).lower() in ("true", "1")


def theme_role(widget) -> object:
    """The dynamic "role" property the gruvbox stylesheet paints with."""
    return widget.property("role")


def test_opening_another_file_asks_before_losing_work(env, monkeypatch):
    desk, store = env["desk"], env["store"]
    other = env["path"].parent / "other.json"
    store.save(other)
    store.set_path(env["path"])  # save() repoints the store; pretend we opened it read-only
    desk.team_spin.setValue(4)
    type_into(desk.slot_edits[0], "12.00")  # unsubmitted edits in the form

    monkeypatch.setattr(
        "uclf.input_window.QtWidgets.QFileDialog.getOpenFileName", lambda *a, **k: (str(other), "")
    )
    asked: list[str] = []
    monkeypatch.setattr(desk, "_confirm_replace", lambda what: asked.append(what) or False)
    desk.open_results()
    assert asked == ["open another results file"]
    assert store.record(4) is None, "the guard said no, so nothing should have loaded"
    assert desk.team_spin.value() == 4 and desk.slot_edits[0].text() == "12.00"


def test_new_results_keeps_a_backup_of_the_previous_sheet(env):
    desk, store, path = env["desk"], env["store"], env["path"]
    desk.team_spin.setValue(5)
    type_into(desk.slot_edits[0], "10.00")
    desk.submit_form()
    pump(store.autosave_delay_ms + 300)
    from qtpy import QtWidgets

    answers = iter([QtWidgets.QMessageBox.StandardButton.Yes, QtWidgets.QMessageBox.StandardButton.Yes])
    QtWidgets.QMessageBox.question = lambda *a, **k: next(answers)  # tests must not block
    desk.new_results()
    pump(store.autosave_delay_ms + 300)
    assert store.teams() == []
    assert json.loads(path.read_text())["teams"] == []
    assert (path.parent / (path.name + ".bak")).exists(), "the previous sheet should be kept as .bak"


def test_settings_round_trip_including_the_board_state(env):
    desk, board, settings = env["desk"], env["board"], env["settings"]
    desk.team_spin.setValue(77)
    desk.auto_box.setChecked(True)
    desk.baud_combo.setCurrentText("57600")
    board.showFullScreen()
    pump(200)
    desk.write_settings()

    restored = InputWindow(store=env["store"], link=env["link"], scoreboard=board, settings=settings)
    pump(100)
    assert restored.team_spin.value() == 77
    assert restored.auto_box.isChecked() is True
    assert restored.baud_combo.currentText() == "57600"
    assert str(settings.value("boardFullscreen")) in ("1", "true", "True")
    restored.close()


def test_table_edits_cover_every_saved_time(env):
    desk, store = env["desk"], env["store"]
    for slot, value in enumerate(("10.00", "11.00", "12.00")):
        store.set_time(4, slot, value)
    desk.refresh_table()
    # blanking a cell clears that slot
    item = desk.table.item(0, 3)  # Time 2
    item.setText("")
    desk._table_edited(item)
    assert store.record(4).times == [1000, None, 1200]
    # and the table shows the new ranking value immediately
    assert desk.table.item(0, 3).text() == ""
    assert desk.table.item(0, 2 + MAX_TIMES).text() == "10.00"


def test_renaming_to_an_existing_team_is_refused(env):
    desk, store = env["desk"], env["store"]
    store.set_time(4, 0, 1000)
    store.set_time(9, 0, 1100)
    desk.refresh_table()
    row_of_4 = next(r for r in range(desk.table.rowCount()) if desk.table.item(r, 1).text() == "4")
    item = desk.table.item(row_of_4, 1)
    item.setText("9")
    desk._table_edited(item)
    assert store.record(4) is not None and store.record(4).times[0] == 1000
    assert desk.table.item(row_of_4, 1).text() == "4", "the table should snap back"
    assert "already exists" in desk.form_status.text()


def test_placing_a_staged_time_returns_the_previous_one(env):
    desk, link = env["desk"], env["link"]
    desk.team_spin.setValue(1)
    link.inject("e,10.00")  # auto-fills time 1
    link.inject("e,11.00")  # auto-fills time 2
    assert desk.staged.count == 2
    desk.place_staged(0)  # overwrite slot 1 with the newest free one (none left -> warn)
    assert "nothing is staged" in desk.form_status.text()
    desk.blank_slots()
    assert desk.staged.free_count == 2
    desk.place_staged(1)
    assert desk.slot_edits[1].text() == "11.00"
    assert desk.staged.free_count == 1


def test_syncing_the_autosave_tick_does_not_change_the_policy(env):
    desk, store = env["desk"], env["store"]
    assert desk.autosave_box.isChecked() and store.autosave
    desk.autosave_box.setChecked(False)  # the operator turns it off...
    assert store.autosave is False
    store.autosave = False  # ...then something else rewrites the tick
    desk._update_file_ui()
    assert desk.autosave_box.isChecked() is False
    assert store.autosave is False
    desk.autosave_box.setChecked(True)
    assert store.autosave is True


def test_logging_a_severity_does_not_crash(env):
    """Regression: 'bad' is a stylesheet role, not a gruvbox colour name."""
    desk = env["desk"]
    for level in ("", "info", "warn", "error", "bad", "ok", "nonsense"):
        desk._log("system", f"message with level {level or '(none)'}", level=level)
    for level in ("info", "warn", "error"):
        env["store"].message.emit(level, f"store says {level}")
    text = desk.log_view.toPlainText()
    assert "nonsense" in text and "store says error" in text


def test_a_corrupt_results_file_is_reported_not_fatal(tmp_path, qapp, settings):

    from uclf.app import build_parser, make_link, make_store

    bad = tmp_path / "corrupt.json"
    bad.write_text(json.dumps({"teams": [{"team": 3, "times": ["nonsense"]}]}))
    args = build_parser().parse_args(["--results", str(bad), "--mock"])
    problems: list[str] = []
    store = make_store(args, settings, problems)
    assert problems and "unparseable" not in problems[0]  # a human-readable complaint
    assert store.teams() == []
    link = make_link(args, settings)
    desk = InputWindow(store, link, settings=settings)
    for problem in problems:
        desk.notify_system(problem, level="bad")
    assert "could not read" in desk.log_view.toPlainText()
    desk.close()
