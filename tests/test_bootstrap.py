"""The dashboard's 'build the database if it is missing or stale' logic. No network: pipeline.run is replaced."""
import sqlite3
from datetime import datetime, timedelta

import pytest

import bootstrap
import pipeline

NOW = datetime(2026, 1, 20, 12, 0)


def make_db(path, runs):
    """runs: list of (finished_at, status)."""
    con = sqlite3.connect(path)
    con.executescript(pipeline.SCHEMA)
    for finished, status in runs:
        con.execute("INSERT INTO pipeline_runs (started_at,finished_at,rows_collected,rows_cleaned,rows_archived,status,message) "
                    "VALUES (?,?,0,0,0,?,?)", (finished, finished, status, "boom" if status == "failed" else ""))
    con.commit()
    con.close()


def ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat(timespec="seconds")


def test_missing_database_is_stale(tmp_path):
    assert bootstrap.is_stale(tmp_path / "nope.db", now=NOW)


def test_recent_successful_run_is_fresh(tmp_path):
    make_db(tmp_path / "a.db", [(ago(3), "success")])
    assert not bootstrap.is_stale(tmp_path / "a.db", now=NOW)


def test_run_older_than_24_hours_is_stale(tmp_path):
    make_db(tmp_path / "a.db", [(ago(25), "success")])
    assert bootstrap.is_stale(tmp_path / "a.db", now=NOW)


def test_failed_or_partial_runs_do_not_count_as_fresh(tmp_path):
    make_db(tmp_path / "a.db", [(ago(1), "failed"), (ago(2), "partial")])
    assert bootstrap.is_stale(tmp_path / "a.db", now=NOW)


def test_unusable_database_files_are_stale_not_a_crash(tmp_path):
    (tmp_path / "empty.db").write_bytes(b"")
    (tmp_path / "junk.db").write_bytes(b"this is not sqlite")
    assert bootstrap.is_stale(tmp_path / "empty.db", now=NOW)
    assert bootstrap.is_stale(tmp_path / "junk.db", now=NOW)


def test_fresh_database_is_not_refetched(tmp_path, monkeypatch):
    make_db(tmp_path / "a.db", [(ago(2), "success")])
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: pytest.fail("pipeline must not run for a fresh database"))
    assert bootstrap.ensure_database(tmp_path / "a.db", now=NOW) == "fresh"


def test_stale_database_is_rebuilt_with_90_days(tmp_path, monkeypatch):
    make_db(tmp_path / "a.db", [(ago(48), "success")])
    seen = {}

    def fake_run(con, past_days=2, **kw):
        seen["days"] = past_days
        con.execute("INSERT INTO pipeline_runs (started_at,finished_at,status,message) VALUES ('x','x','success','')")
        con.commit()
        return "success"

    monkeypatch.setattr(pipeline, "run", fake_run)
    assert bootstrap.ensure_database(tmp_path / "a.db", now=NOW) == "built"
    assert seen["days"] == 90


def test_missing_database_is_created_in_a_new_folder(tmp_path, monkeypatch):
    seen = {}

    def fake_run(con, past_days=2, **kw):
        seen["tables"] = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.executescript(pipeline.SCHEMA)
        con.execute("INSERT INTO pipeline_runs (started_at,finished_at,status,message) VALUES ('x','x','success','')")
        con.commit()
        return "success"

    monkeypatch.setattr(pipeline, "run", fake_run)
    target = tmp_path / "data" / "sub" / "a.db"
    assert bootstrap.ensure_database(target, now=NOW) == "built" and target.exists()


def test_failed_build_raises_with_the_pipeline_message(tmp_path, monkeypatch):
    def fake_run(con, past_days=2, **kw):
        con.executescript(pipeline.SCHEMA)
        con.execute("INSERT INTO pipeline_runs (started_at,finished_at,status,message) "
                    "VALUES ('x','x','failed','collect: FetchError: every city failed')")
        con.commit()
        return "failed"

    monkeypatch.setattr(pipeline, "run", fake_run)
    with pytest.raises(pipeline.PipelineError, match="every city failed"):
        bootstrap.ensure_database(tmp_path / "a.db", now=NOW)


def test_has_data_requires_daily_rows(tmp_path):
    make_db(tmp_path / "a.db", [])
    assert not bootstrap.has_data(tmp_path / "a.db")
    con = sqlite3.connect(tmp_path / "a.db")
    con.execute("INSERT INTO daily_summary VALUES ('Delhi','2026-01-01',1,1,1,1,1,'Good',24)")
    con.commit()
    con.close()
    assert bootstrap.has_data(tmp_path / "a.db")
