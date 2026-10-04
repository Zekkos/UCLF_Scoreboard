"""Tests for the projected scoreboard: ranking rows, the big timer, reset."""

from __future__ import annotations

import pytest
from qtpy import QtCore, QtTest

from helpers import pump

from uclf.input_window import InputWindow
from uclf.links import SimulatedLink
from uclf.results import ResultStore
from uclf.scoreboard_window import ScoreboardWindow

GRAB = lambda board, row, col: board.table.item(row, col).text()  # noqa: E731


@pytest.fixture()
def board_env(qapp, settings):
    store = ResultStore()
    link = SimulatedLink()
    board = ScoreboardWindow(store, link, title="UCLF", subtitle="test", timer_px=90, table_px=16)
    board.show()
    pump()
    yield {"store": store, "link": link, "board": board}
    board.close()


# --------------------------------------------------------------------- ranking


def test_rows_show_rank_team_and_all_times(board_env):
    store, board = board_env["store"], board_env["board"]
    store.set_time(3, 0, "11.11")
    store.set_time(3, 2, "9.85")
    store.set_time(12, 0, "10.10")
    store.ensure_team(21)
    assert board.table.columnCount() == 6
    rows = [[GRAB(board, r, c) for c in range(6)] for r in range(board.table.rowCount())]
    blank = "\u2013"  # an empty slot reads as a dash on the projected board
    assert rows == [
        ["1", "3", "11.11", blank, "9.85", "9.85"],
        ["2", "12", "10.10", blank, blank, "10.10"],
        ["3", "21", blank, blank, blank, blank],
    ]


def test_the_table_rebuilds_when_results_change(board_env):
    store, board = board_env["store"], board_env["board"]
    store.set_time(1, 0, "10.00")
    store.set_time(2, 0, "12.00")
    assert GRAB(board, 0, 1) == "1"
    store.set_time(2, 0, "8.00")  # team 2 now leads
    assert [GRAB(board, r, 1) for r in range(2)] == ["2", "1"]
    assert GRAB(board, 0, 2) == "8.00"


def test_medal_ranks_are_coloured(board_env):
    store, board = board_env["store"], board_env["board"]
    for team, value in ((5, "10.00"), (6, "11.00"), (7, "12.00"), (8, "13.00")):
        store.set_time(team, 0, value)
    from uclf import theme

    shades = [board.table.item(row, 0).foreground().color().name() for row in range(4)]
    assert shades == [theme.color(name) for name in ("yellow_b", "gray", "orange_b", "gray0")]


def test_empty_board_shows_a_placeholder(board_env):
    board = board_env["board"]
    assert board.table.rowCount() == 1
    assert "no results" in GRAB(board, 0, 0)


# ------------------------------------------------------------------ the timer


def test_start_message_begins_counting_up(board_env):
    board, link = board_env["board"], board_env["link"]
    assert board.state_label.text() == "STANDBY"
    link.inject("s")
    assert board.state_label.text() == "RUNNING"
    assert board._tick_timer.isActive()
    pump(140)
    first = float(board.timer_label.text())
    pump(140)
    assert float(board.timer_label.text()) > first >= 0.10


def test_finish_message_freezes_the_reported_time(board_env):
    board, link = board_env["board"], board_env["link"]
    link.inject("s")
    pump(120)
    link.inject("e,10.10")
    assert board.timer_label.text() == "10.10", "display must show the gate's number"
    assert board.state_label.text() == "FINISHED"
    assert board._tick_timer.isActive() is False
    frozen = board.timer_label.text()
    pump(200)
    assert board.timer_label.text() == frozen, "timer kept counting after the finish frame"


def test_long_times_keep_two_decimals(board_env):
    board, link = board_env["board"], board_env["link"]
    link.inject("e,125.5")
    assert board.timer_label.text() == "125.50"
    link.inject("e,200")
    assert board.timer_label.text() == "200.00"


def test_reset_from_the_gates_clears_the_timer(board_env):
    board, link = board_env["board"], board_env["link"]
    link.inject("s")
    pump(80)
    link.inject("r")
    assert board.timer_label.text() == "0.00"
    assert board.state_label.text() == "RESET"
    assert board._tick_timer.isActive() is False
    pump(2000)  # the RESET badge is temporary
    assert board.state_label.text() == "STANDBY"


def test_our_own_reset_command_clears_the_timer(board_env):
    board, link = board_env["board"], board_env["link"]
    link.inject("s")
    pump(60)
    assert link.reset_gates() is True  # the operator's reset button
    assert board.timer_label.text() == "0.00"
    assert board.state_label.text() == "RESET"


def test_a_new_start_restarts_from_zero(board_env):
    board, link = board_env["board"], board_env["link"]
    link.inject("s")
    link.inject("e,9.00")
    link.inject("s")
    assert board.state_label.text() == "RUNNING"
    pump(60)
    assert float(board.timer_label.text()) < 9.00


def test_garbage_frames_leave_the_timer_alone(board_env):
    board, link = board_env["board"], board_env["link"]
    link.inject("s")
    pump(60)
    counting = board.timer_label.text()
    link.inject("e,oops")
    assert board.state_label.text() == "RUNNING"
    assert board.timer_label.text() >= counting


# --------------------------------------------------------------- presentation


def test_keyboard_shortcuts(board_env):
    board = board_env["board"]
    before = board.timer_px
    QtTest.QTest.keyClick(board, QtCore.Qt.Key.Key_Plus)
    assert board.timer_px > before
    QtTest.QTest.keyClick(board, QtCore.Qt.Key.Key_Minus)
    assert abs(board.timer_px - before) <= 2  # integer type sizes round slightly
    QtTest.QTest.keyClick(board, QtCore.Qt.Key.Key_R)
    assert board.timer_label.text() == "0.00"


def test_scoreboard_and_desk_share_one_store(qapp, settings):
    store = ResultStore()
    link = SimulatedLink()
    board = ScoreboardWindow(store, link)
    desk = InputWindow(store, link, scoreboard=board, settings=settings)
    desk.show()
    board.show()
    pump()
    desk.team_spin.setValue(42)
    desk.slot_edits[0].setText("10.10")
    desk.slot_edits[0].textEdited.emit("10.10")
    assert board.table.rowCount() != 1 or GRAB(board, 0, 0) != "42"
    desk.submit_form()
    assert GRAB(board, 0, 1) == "42"
    assert GRAB(board, 0, 2) == "10.10"
    link.inject("s")
    assert board.state_label.text() == "RUNNING"
    desk.reset_btn.click()
    assert board.timer_label.text() == "0.00"
    desk.close()
    board.close()
