"""Retry/backoff, specific error handling and failure auditing in pipeline_runs. No real network, no real sleeping."""
import pandas as pd
import pytest
import requests

import pipeline
from conftest import NOW, api_payload


class Resp:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code, self._payload, self._bad_json = status, payload, bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        if self._bad_json:
            raise ValueError("Expecting value")
        return self._payload


GOOD = api_payload("2026-01-10", [float(h) for h in range(24)])


def script_api(monkeypatch, outcomes):
    """Each requests.get call consumes the next outcome: a Resp is returned, an Exception is raised."""
    calls = []
    it = iter(outcomes)

    def fake_get(url, params=None, timeout=None):
        calls.append(params["latitude"])
        out = next(it)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(pipeline.requests, "get", fake_get)
    return calls


# ---------- fetch_city: retries and backoff ----------
def test_transient_errors_are_retried_with_exponential_backoff(monkeypatch):
    calls = script_api(monkeypatch, [requests.ConnectionError("net down"), Resp(503), Resp(200, GOOD)])
    sleeps = []
    out = pipeline.fetch_city("Delhi", 28.6, 77.2, 2, sleep=sleeps.append)
    assert out == GOOD and len(calls) == 3
    assert sleeps == [2, 4]                                  # 2s then 4s


def test_gives_up_after_max_attempts_with_a_clear_error(monkeypatch):
    calls = script_api(monkeypatch, [requests.Timeout("slow")] * 4)
    with pytest.raises(pipeline.FetchError, match="gave up after 4 attempts"):
        pipeline.fetch_city("Delhi", 28.6, 77.2, 2, sleep=lambda s: None)
    assert len(calls) == 4


def test_client_errors_are_not_retried(monkeypatch):
    calls = script_api(monkeypatch, [Resp(400)])
    with pytest.raises(pipeline.FetchError, match="HTTP 400"):
        pipeline.fetch_city("Delhi", 28.6, 77.2, 2, sleep=lambda s: None)
    assert len(calls) == 1


def test_rate_limit_429_is_retried(monkeypatch):
    calls = script_api(monkeypatch, [Resp(429), Resp(200, GOOD)])
    assert pipeline.fetch_city("Delhi", 28.6, 77.2, 2, sleep=lambda s: None) == GOOD
    assert len(calls) == 2


def test_malformed_json_is_retried_but_wrong_schema_is_not(monkeypatch):
    script_api(monkeypatch, [Resp(200, bad_json=True), Resp(200, GOOD)])
    assert pipeline.fetch_city("Delhi", 28.6, 77.2, 2, sleep=lambda s: None) == GOOD
    calls = script_api(monkeypatch, [Resp(200, {"error": True, "reason": "oops"})])
    with pytest.raises(pipeline.FetchError, match="no usable 'hourly'"):
        pipeline.fetch_city("Delhi", 28.6, 77.2, 2, sleep=lambda s: None)
    assert len(calls) == 1


# ---------- run(): failures are audited in pipeline_runs ----------
@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)


def last_run(con):
    return pd.read_sql("SELECT * FROM pipeline_runs ORDER BY run_id DESC LIMIT 1", con).iloc[0]


def test_total_api_outage_is_logged_as_failed_run(con, tmp_path, monkeypatch, no_sleep):
    script_api(monkeypatch, [requests.ConnectionError("net down")] * 100)
    status = pipeline.run(con, raw_dir=tmp_path / "raw", report_dir=tmp_path / "rep", now=NOW)
    row = last_run(con)
    assert status == "failed" and row.status == "failed"
    assert row.message.startswith("collect: FetchError") and "every city failed" in row.message
    assert row.rows_collected == 0


def test_one_failing_city_gives_a_partial_run_and_keeps_the_rest(con, tmp_path, monkeypatch, no_sleep):
    outcomes = [Resp(200, GOOD)] + [Resp(400)] + [Resp(200, GOOD)] * 3     # Mumbai (2nd city) rejected
    script_api(monkeypatch, outcomes)
    status = pipeline.run(con, raw_dir=tmp_path / "raw", report_dir=tmp_path / "rep", now=pd.Timestamp("2026-01-12"))
    row = last_run(con)
    assert status == "partial" and "Mumbai" in row.message
    cities = {r[0] for r in con.execute("SELECT DISTINCT city FROM raw_readings")}
    assert cities == set(pipeline.CITIES) - {"Mumbai"}
    assert row.rows_collected == 4 * 24 and row.rows_cleaned > 0     # later stages still ran


def test_successful_run_is_audited_with_row_counts(con, tmp_path, monkeypatch):
    script_api(monkeypatch, [Resp(200, GOOD)] * 5)
    status = pipeline.run(con, raw_dir=tmp_path / "raw", report_dir=tmp_path / "rep", now=pd.Timestamp("2026-01-12"))
    row = last_run(con)
    assert status == "success" and row.status == "success" and row.message == ""
    assert row.rows_collected == 120 and row.rows_cleaned == 120
    assert (tmp_path / "rep" / "summary.md").exists()


def test_database_error_in_a_stage_is_logged_with_the_stage_name(con, tmp_path, monkeypatch):
    import sqlite3
    script_api(monkeypatch, [Resp(200, GOOD)] * 5)

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pipeline, "transform", boom)
    assert pipeline.run(con, raw_dir=tmp_path / "raw", report_dir=tmp_path / "rep", now=pd.Timestamp("2026-01-12")) == "failed"
    assert last_run(con).message == "transform: OperationalError: database is locked"


def test_unexpected_bug_is_audited_then_re_raised(con, tmp_path, monkeypatch):
    script_api(monkeypatch, [Resp(200, GOOD)] * 5)
    monkeypatch.setattr(pipeline, "analyze", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        pipeline.run(con, raw_dir=tmp_path / "raw", report_dir=tmp_path / "rep", now=pd.Timestamp("2026-01-12"))
    row = last_run(con)
    assert row.status == "failed" and row.message.startswith("analyze: ZeroDivisionError")


def test_analyze_survives_data_covering_only_weekend_days(con, tmp_path):
    """Regression: a fresh database with one Saturday of data used to crash the weekday/weekend table."""
    from conftest import insert_raw
    insert_raw(con, "Delhi", "2026-01-10", [float(h) for h in range(24)])     # 2026-01-10 is a Saturday
    pipeline.clean(con, now=pd.Timestamp("2026-01-12"))
    pipeline.transform(con)
    pipeline.analyze(con, tmp_path)
    assert "Weekday vs weekend" in (tmp_path / "summary.md").read_text(encoding="utf-8")
