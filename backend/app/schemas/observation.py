"""Observation request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import StrictModel, Target


class ObservationSearchRequest(StrictModel):
    ra: float = Field(..., ge=0.0, lt=360.0, allow_inf_nan=False, description="ICRS right ascension in degrees")
    dec: float = Field(..., ge=-90.0, le=90.0, allow_inf_nan=False, description="ICRS declination in degrees")
    radius: float = Field(0.01, gt=0.0, allow_inf_nan=False, description="Search radius in degrees")
    collection: str | None = Field(None, description="IRSA collection, e.g. spherex_qr3; defaults to server config")
    start_date: str | None = Field(None, description="ISO 8601 date/datetime, inclusive lower bound (UTC)")
    end_date: str | None = Field(None, description="ISO 8601 date/datetime, inclusive upper bound (UTC)")


class ObservationSummary(StrictModel):
    observation_id: str = Field(..., description="Opaque, URL-safe identifier for this SIA row")
    obs_id: str | None = None
    obs_publisher_did: str | None = None
    observation_date: str | None = Field(None, description="ISO 8601 UTC start time (from SIA t_min)")
    observation_date_mjd: float | None = None
    instrument: str | None = None
    facility: str | None = None
    collection: str | None = None
    band: str | None = Field(None, description="IRSA energy_bandpassname, e.g. SPHEREx-D3")
    product_type: str | None = None
    product_subtype: str | None = None
    calib_level: int | None = None
    fits_available: bool


class ObservationSearchResponse(StrictModel):
    success: Literal[True] = True
    target: Target
    collection: str | None
    total_observations: int
    observations: list[ObservationSummary]


class ObservationDetail(ObservationSummary):
    s_ra: float | None = None
    s_dec: float | None = None
    t_min_mjd: float | None = None
    t_max_mjd: float | None = None
    start_time: str | None = None
    end_time: str | None = None
    exposure_time_seconds: float | None = None
    access_format: str | None = None
    access_estsize_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    pixel_scale_arcsec: float | None = None
    fov_deg: float | None = None
    fits_access_url: str | None = Field(None, description="Upstream IRSA URL (informational; not fetched by the client)")
    processing_status: Literal["ready", "no_fits_product"] = "ready"


class ObservationDetailResponse(StrictModel):
    success: Literal[True] = True
    observation: ObservationDetail
