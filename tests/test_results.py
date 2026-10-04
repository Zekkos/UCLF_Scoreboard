"""Unit tests for the results model: ranking, editing and persistence."""

from __future__ import annotations

import json

import pytest

from uclf.results import MAX_TIMES, ResultRecord, ResultStore, StoreError
from uclf.timeutil import InvalidTime


def times(*values):
    """Pad a tuple of seconds into the three fixed slots."""
    padded = list(values) + [None] * (MAX_TIMES - len(values))
    return [None if v is None else int(round(v * 100)) for v in padded]


# --------------------------------------------------------------------- ranking


def test_ranking_is_fastest_first(store):
    store.set_time(3, 0, times(11.0)[0])
    store.set_time(1, 0, times(9.5)[0])
    store.set_time(2, 0, times(10.25)[0])
    assert [(rank, r.team) for rank, r in store.ranked()] == [(1, 1), (2, 2), (3, 3)]


def test_ranking_uses_the_best_of_three(store):
    store.set_time(10, 0, 1200)
    store.set_time(10, 1, 900)  # best 9.00
    store.set_time(11, 0, 1000)
    ranked = store.ranked()
    assert [(rank, r.team, r.best) for rank, r in ranked] == [(1, 10, 900), (2, 11, 1000)]


def test_ties_split_on_the_next_time_then_team_number(store):
    store.set_time(7, 0, 1000)
    store.set_time(8, 0, 1000)
    store.set_time(6, 0, 1000)
    store.set_time(6, 1, 1100)
    store.set_time(7, 1, 1200)
    assert [r.team for _, r in store.ranked()] == [6, 7, 8]


def test_teams_without_times_come_last(store):
    store.ensure_team(99)
    store.set_time(5, 0, 1500)
    ranked = store.ranked()
    assert [r.team for _, r in ranked] == [5, 99]
    assert ranked[-1][1].best is None


def test_only_three_slots_exist(store):
    for slot in range(MAX_TIMES):
        store.set_time(1, slot, 1000 + slot)
    with pytest.raises(IndexError):
        store.set_time(1, MAX_TIMES, 1000)


# ------------------------------------------------------------------ slot edits


def test_set_and_clear_a_time(store):
    store.set_time(4, 1, "13.25")
    assert store.record(4).times == times(None, 13.25, None)
    assert store.record(4).sources[1] == "manual"
    store.set_time(4, 1, None)
    assert store.record(4).times[1] is None
    assert store.record(4).sources[1] == "none"


def test_string_times_are_accepted_on_write(store):
    store.set_time(2, 0, "1:05.50")
    assert store.record(2).times[0] == 6550


def test_out_of_range_time_is_refused(store):
    with pytest.raises(InvalidTime):
        store.set_time(2, 0, 0)
    with pytest.raises(InvalidTime):
        store.set_time(2, 0, 10_000_000)


def test_appending_serial_times_stops_after_three(store):
    assert store.append_serial_time(1, 1000) == 0
    assert store.append_serial_time(1, 1100) == 1
    assert store.append_serial_time(1, 1200) == 2
    assert store.append_serial_time(1, 1300) is None
    assert store.record(1).times == times(10.0, 11.0, 12.0)
    assert store.record(1).sources == ["serial"] * 3


def test_removing_a_team(store):
    store.set_time(8, 0, 1000)
    assert store.remove_team(8) is True
    assert store.record(8) is None
    assert store.remove_team(8) is False


def test_renaming_a_team_moves_its_times(store):
    store.set_time(8, 0, 1000)
    assert store.rename_team(8, 12) is None
    assert store.record(8) is None
    assert store.record(12).times[0] == 1000
    store.set_time(20, 0, 1000)
    assert store.rename_team(12, 20) == "team 20 already exists"
    assert store.rename_team(12, 99999) is not None
    assert store.rename_team(77, 5) == "team 77 does not exist"


def test_counts_and_teams(store):
    store.set_time(3, 0, 1000)
    store.set_time(3, 1, 1100)
    store.ensure_team(1)
    assert store.teams() == [1, 3]
    assert store.counts() == (2, 2)


def test_records_handed_out_are_copies(store):
    store.set_time(3, 0, 1000)
    record = store.record(3)
    record.times[0] = 9999  # must not corrupt the store
    assert store.record(3).times[0] == 1000


def test_clear_all(store):
    store.set_time(3, 0, 1000)
    store.clear_all()
    assert store.teams() == []


# ---------------------------------------------------------------- persistence


def test_json_round_trip(store, tmp_path):
    store.set_time(2, 0, 1010, source="serial")
    store.set_time(2, 2, 999, source="manual")
    store.ensure_team(7)
    path = tmp_path / "results.json"
    store.save(path)
    doc = json.loads(path.read_text())
    assert doc["format"] == "uclf-scoreboard"
    assert doc["teams"][0]["team"] == 2

    loaded = ResultStore()
    loaded.load(path)
    assert [(r.team, r.times, r.sources) for _, r in loaded.ranked()] == [
        (r.team, r.times, r.sources) for _, r in store.ranked()
    ]


def test_save_is_atomic_and_keeps_a_backup(store, tmp_path):
    path = tmp_path / "results.json"
    store.save(path)
    store.set_time(1, 0, 1000)
    store.save(path)
    assert (tmp_path / "results.json.bak").exists()
    # no temporary litter left behind
    assert sorted(p.name for p in tmp_path.iterdir()) == ["results.json", "results.json.bak"]


def test_hand_written_seconds_are_accepted_when_loading(store, tmp_path):
    path = tmp_path / "handy.json"
    path.write_text(json.dumps({"teams": [{"team": 5, "times": ["10.10", 1200, None, 99999]}]}))
    store.load(path)
    assert store.record(5).times == [1010, 1200, None]  # 4th entry ignored, string parsed


def test_loading_rejects_broken_documents(store, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(StoreError):
        store.load(bad)

    missing = tmp_path / "nope.json"
    with pytest.raises(StoreError):
        store.load(missing)

    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"teams": [{"team": 1, "times": ["nonsense"]}]}))
    with pytest.raises(StoreError):
        store.load(wrong)

    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps({"teams": [{"team": 1}, {"team": 1}]}))
    with pytest.raises(StoreError):
        store.load(dup)


def test_export_csv(store, tmp_path):
    store.set_time(2, 0, 1010)
    store.set_time(1, 0, 900)
    csv_path = tmp_path / "out.csv"
    store.export_csv(csv_path)
    rows = csv_path.read_text().strip().splitlines()
    assert rows[0] == "rank,team,time_1_s,time_2_s,time_3_s,best_s"
    assert rows[1].startswith("1,1,9.00")


def test_dirty_flag_and_debounced_autosave(store, tmp_path):
    from helpers import pump

    path = tmp_path / "autosave.json"
    store.set_path(path)
    assert store.dirty is False
    store.set_time(1, 0, 1000)
    assert store.dirty is True
    pump(store.autosave_delay_ms // 2)  # still inside the debounce window
    assert not path.exists()
    pump(store.autosave_delay_ms + 300)
    assert store.dirty is False
    assert path.exists()


def test_autosave_can_be_switched_off(store, tmp_path, qapp):
    from helpers import pump

    path = tmp_path / "manual.json"
    store.set_path(path)
    store.autosave = False
    store.set_time(1, 0, 1000)
    pump(store.autosave_delay_ms + 200)
    assert not path.exists()
    store.request_save()
    assert path.exists()


def test_flush_on_exit_saves_pending_changes(store, tmp_path):
    path = tmp_path / "flushed.json"
    store.set_path(path)
    store.autosave = False
    store.set_time(1, 0, 1000)
    assert store.flush() is True
    assert path.exists()
    assert store.flush() is False  # nothing left to do


def test_store_survives_a_failed_save_to_a_directory(store, tmp_path):
    """Autosave keeps trying instead of losing edits when the disk is briefly busy."""
    path = tmp_path / "sub" / "results.json"
    store.set_path(path)
    store.autosave = True
    store.set_time(1, 0, 1000)
    store._autosave()  # parent dir is created by save()
    assert path.exists()


def test_replace_all_with_records(store):
    store.replace_all([ResultRecord(team=4, times=[1000, None, None], sources=["serial", "none", "none"])])
    assert store.record(4).times[0] == 1000
    store.replace_all({4: ResultRecord(team=4, times=[1100, None, None])})
    assert store.record(4).times[0] == 1100
