"""Shared fixtures. Every test uses an in-memory SQLite database and temp dirs:
nothing touches data/air_quality.db and nothing calls the network."""
import sqlite3

import pandas as pd
import pytest

import pipeline

NOW = pd.Timestamp("2026-01-20T00:00")  # fixed "current time" for deterministic tests


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.executescript(pipeline.SCHEMA)
    yield c
    c.close()


def hourly_times(start, n):
    return [t.strftime("%Y-%m-%dT%H:%M") for t in pd.date_range(start, periods=n, freq="h")]


def insert_raw(con, city, start, pm25, pm10=20.0, no2=10.0, aqi=40.0):
    """Insert one hourly row per value in `pm25` (use None for a missing reading)."""
    rows = [(city, t, v, pm10, no2, aqi, "2026-01-01T00:00:00") for t, v in zip(hourly_times(start, len(pm25)), pm25)]
    con.executemany("INSERT INTO raw_readings VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()


def api_payload(start, pm25):
    """Shape of an Open-Meteo hourly response."""
    n = len(pm25)
    return {"hourly": {"time": hourly_times(start, n), "pm2_5": list(pm25), "pm10": [20.0] * n,
                       "nitrogen_dioxide": [10.0] * n, "us_aqi": [40.0] * n}}
