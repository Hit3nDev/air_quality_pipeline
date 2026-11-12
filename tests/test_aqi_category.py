import numpy as np
import pytest

from pipeline import aqi_category


@pytest.mark.parametrize("aqi,expected", [
    (0, "Good"), (50, "Good"), (50.1, "Moderate"), (100, "Moderate"),
    (101, "Unhealthy for Sensitive"), (150, "Unhealthy for Sensitive"),
    (151, "Unhealthy"), (200, "Unhealthy"), (201, "Very Unhealthy"),
    (300, "Very Unhealthy"), (301, "Hazardous"), (500, "Hazardous"),
])
def test_category_boundaries(aqi, expected):
    assert aqi_category(aqi) == expected


def test_missing_aqi_is_unknown():
    assert aqi_category(np.nan) == "Unknown"
    assert aqi_category(None) == "Unknown"
