"""Input validation shared by services and API schemas."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from astropy.time import Time

from app.core.exceptions import (
    InvalidCoordinateError,
    InvalidCutoutSizeError,
    InvalidDateError,
    InvalidRadiusError,
)


def validate_coordinates(ra: float, dec: float) -> tuple[float, float]:
    """Validate ICRS degrees. RA in [0, 360), Dec in [-90, 90]; no NaN/inf."""
    for name, value in (("ra", ra), ("dec", dec)):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise InvalidCoordinateError(f"{name} must be a finite number", details={name: value})
    if not 0.0 <= ra < 360.0:
        raise InvalidCoordinateError("ra must be in [0, 360) degrees", details={"ra": ra})
    if not -90.0 <= dec <= 90.0:
        raise InvalidCoordinateError("dec must be in [-90, 90] degrees", details={"dec": dec})
    return float(ra), float(dec)


def validate_radius(radius: float, max_radius_deg: float) -> float:
    if not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0:
        raise InvalidRadiusError("radius must be a positive number of degrees", details={"radius": radius})
    if radius > max_radius_deg:
        raise InvalidRadiusError(
            f"radius must not exceed {max_radius_deg} degrees",
            details={"radius": radius, "max_radius_deg": max_radius_deg},
        )
    return float(radius)


def validate_cutout_size(size: float, max_size_deg: float) -> float:
    if not isinstance(size, (int, float)) or not math.isfinite(size) or size <= 0:
        raise InvalidCutoutSizeError("size must be a positive number of degrees", details={"size": size})
    if size > max_size_deg:
        raise InvalidCutoutSizeError(
            f"size must not exceed {max_size_deg} degrees",
            details={"size": size, "max_size_deg": max_size_deg},
        )
    return float(size)


def to_mjd(value: datetime | date | str | None, *, field: str) -> float | None:
    if value is None:
        return None
    try:
        t = Time(_as_datetime(value), scale="utc")
    except (ValueError, TypeError) as exc:
        raise InvalidDateError(f"{field} is not a valid ISO date/datetime", details={"field": field, field: str(value)}) from exc
    if t.mjd != t.mjd:  # NaN guard
        raise InvalidDateError(f"{field} is not a valid date", details={"field": field, field: str(value)})
    return float(t.mjd)


def _as_datetime(value: datetime | date | str) -> datetime:
    """Astropy 8 rejects ISO strings carrying a UTC offset; normalize first."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def validate_date_range(start_mjd: float | None, end_mjd: float | None) -> None:
    if start_mjd is not None and end_mjd is not None and start_mjd > end_mjd:
        raise InvalidDateError(
            "start_date must be earlier than or equal to end_date",
            details={"start_mjd": start_mjd, "end_mjd": end_mjd},
        )


def mjd_to_iso(mjd: float | None) -> str | None:
    if mjd is None or not math.isfinite(float(mjd)):
        return None
    dt = Time(float(mjd), format="mjd", scale="utc").to_datetime(timezone=timezone.utc)
    return dt.isoformat()
