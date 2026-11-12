import json

import pytest

import pipeline
from conftest import api_payload


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture
def mock_api(monkeypatch):
    """Replace requests.get so no test ever reaches Open-Meteo. Returns a dict to tweak the payload."""
    state = {"payload": api_payload("2026-01-10", [float(h) for h in range(24)]), "calls": []}

    def fake_get(url, params=None, timeout=None):
        state["calls"].append(params)
        return FakeResponse(state["payload"])

    monkeypatch.setattr(pipeline.requests, "get", fake_get)
    return state


def count(con, table="raw_readings"):
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_collect_inserts_one_row_per_city_hour(con, mock_api, tmp_path):
    total = pipeline.collect(con, 2, raw_dir=tmp_path)
    assert total == 5 * 24 and count(con) == 5 * 24
    assert len(mock_api["calls"]) == 5                       # one request per city
    assert mock_api["calls"][0]["past_days"] == 2


def test_running_collect_twice_does_not_duplicate_rows(con, mock_api, tmp_path):
    pipeline.collect(con, 2, raw_dir=tmp_path)
    pipeline.collect(con, 2, raw_dir=tmp_path)
    assert count(con) == 5 * 24


def test_revised_values_overwrite_instead_of_duplicating(con, mock_api, tmp_path):
    pipeline.collect(con, 2, raw_dir=tmp_path)
    mock_api["payload"]["hourly"]["pm2_5"][0] = 999.0       # provider revises the first hour
    pipeline.collect(con, 2, raw_dir=tmp_path)
    assert count(con) == 5 * 24
    got = con.execute("SELECT pm2_5 FROM raw_readings WHERE city='Delhi' AND time='2026-01-10T00:00'").fetchone()[0]
    assert got == 999.0


def test_raw_json_snapshot_is_written_per_city(con, mock_api, tmp_path):
    pipeline.collect(con, 2, raw_dir=tmp_path)
    files = sorted(tmp_path.glob("*.json"))
    assert {f.name.split("_")[0] for f in files} == set(pipeline.CITIES)
    assert json.loads(files[0].read_text())["hourly"]["time"][0] == "2026-01-10T00:00"
