"""Coordinate, radius, size and date validation."""

from __future__ import annotations

import math

import pytest

from app.core.exceptions import (
    InvalidCoordinateError,
    InvalidCutoutSizeError,
    InvalidDateError,
    InvalidRadiusError,
)
from app.core.validation import (
    mjd_to_iso,
    to_mjd,
    validate_coordinates,
    validate_cutout_size,
    validate_date_range,
    validate_radius,
)


def test_valid_coordinates_pass_through() -> None:
    assert validate_coordinates(156.09328159, -41.64466331) == (156.09328159, -41.64466331)


@pytest.mark.parametrize(
    ("ra", "dec"),
    [(-0.1, 0.0), (360.0, 0.0), (0.0, -90.1), (0.0, 90.1), (math.nan, 0.0), (0.0, math.inf)],
)
def test_invalid_coordinates_rejected(ra: float, dec: float) -> None:
    with pytest.raises(InvalidCoordinateError):
        validate_coordinates(ra, dec)


def test_invalid_coordinate_type_rejected() -> None:
    with pytest.raises(InvalidCoordinateError):
        validate_coordinates("156", 0.0)  # type: ignore[arg-type]


def test_radius_validation() -> None:
    assert validate_radius(0.01, 1.0) == 0.01
    for bad in (0.0, -1.0, math.nan, 2.0):
        with pytest.raises(InvalidRadiusError):
            validate_radius(bad, 1.0)


def test_cutout_size_validation() -> None:
    assert validate_cutout_size(0.1, 1.0) == 0.1
    for bad in (0.0, -0.1, math.inf, 2.0):
        with pytest.raises(InvalidCutoutSizeError):
            validate_cutout_size(bad, 1.0)


def test_date_parsing_and_iso_roundtrip() -> None:
    mjd = to_mjd("2026-05-17T06:32:37+00:00", field="start_date")
    assert mjd is not None and 61177.0 < mjd < 61178.0
    assert mjd_to_iso(mjd) == "2026-05-17T06:32:37+00:00"
    assert mjd_to_iso(None) is None
    assert to_mjd(None, field="start_date") is None


def test_invalid_date_rejected() -> None:
    with pytest.raises(InvalidDateError):
        to_mjd("not-a-date", field="start_date")


def test_reversed_date_range_rejected() -> None:
    with pytest.raises(InvalidDateError):
        validate_date_range(61200.0, 61100.0)
