"""Multi-epoch timeline request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import StrictModel


class TimelineTarget(StrictModel):
    ra: float
    dec: float
    frame: Literal["ICRS"] = "ICRS"


class TimelineQuery(StrictModel):
    radius_deg: float
    collection: str
    bands: list[str] | None = Field(None, description="Band filter applied to products; null means all bands")
    start_date: str | None = None
    end_date: str | None = None


class TimelineProduct(StrictModel):
    observation_id: str | None = Field(None, description="Opaque id usable with /observations/{id} and /cutout")
    obs_publisher_did: str | None = None
    obs_id: str | None = None
    band: str | None = Field(None, description="Original IRSA energy_bandpassname")
    em_min_m: float | None = Field(None, description="SIA2 em_min in metres")
    em_max_m: float | None = Field(None, description="SIA2 em_max in metres")
    t_min_mjd: float | None = Field(None, description="SIA2 t_min, MJD UTC")
    t_max_mjd: float | None = Field(None, description="SIA2 t_max, MJD UTC")
    start_time: str | None = Field(None, description="t_min as ISO 8601 UTC")
    end_time: str | None = Field(None, description="t_max as ISO 8601 UTC")
    observation_time: str | None = Field(None, description="Midpoint of t_min/t_max as ISO 8601 UTC")
    timeline_eligible: bool = Field(..., description="True when a valid observation time exists; discovery may still succeed otherwise")
    exposure_time_seconds: float | None = None
    calib_level: int | None = None
    dataproduct_type: str | None = None
    dataproduct_subtype: str | None = None
    target_name: str | None = None
    facility: str | None = None
    instrument: str | None = None
    collection: str | None = None
    fits_available: bool
    fits_access_url: str | None = Field(None, description="Upstream IRSA URL (informational)")
    access_url_trusted: bool = False
    processing_status: Literal["ready", "no_fits_product", "untrusted_access_url"] = "no_fits_product"
    s_ra: float | None = None
    s_dec: float | None = None
    fov_deg: float | None = None
    width: int | None = None
    height: int | None = None
    pixel_scale_arcsec: float | None = None
    target_covered: bool | None = Field(None, description="True/False from s_region; null when spatial metadata is missing or unparseable")
    angular_distance_deg: float | None = Field(None, description="Great-circle distance from target to footprint centre")
    metadata_complete: bool
    missing_fields: list[str]
    warnings: list[str]


class TimelineEpoch(StrictModel):
    epoch_index: int
    epoch_key: str
    obs_id: str | None
    identified: bool
    observation_time: str | None
    start_time: str | None
    end_time: str | None
    midpoint_mjd: float | None
    duration_seconds: float | None
    timeline_eligible: bool
    bands: list[str]
    product_count: int
    target_covered: bool | None
    target_covered_products: int
    footprint_center_ra: float | None
    footprint_center_dec: float | None
    footprint_fov_deg: float | None
    facility: str | None
    instrument: str | None
    collection: str | None
    products: list[TimelineProduct]
    warnings: list[str]


class TimelineQuality(StrictModel):
    total_product_rows: int
    products_included: int
    duplicate_products_removed: int
    products_with_valid_timestamps: int
    products_without_valid_timestamps: int
    products_with_band: int
    products_without_band: int
    products_with_spatial_metadata: int
    products_without_spatial_metadata: int
    products_target_covered: int
    products_target_outside: int
    products_target_coverage_unknown: int
    products_missing_identifier: int
    inconsistent_epochs: int
    metadata_complete_products: int
    metadata_incomplete_products: int


class TimelineWarning(StrictModel):
    code: str
    message: str
    count: int


class TimelineMetadata(StrictModel):
    time_standard: Literal["UTC"] = "UTC"
    time_format: Literal["ISO 8601"] = "ISO 8601"
    timestamp_source: str = "IRSA SIA2 t_min/t_max (MJD, UTC)"
    observation_time_definition: str = "Arithmetic midpoint of t_min and t_max"
    epoch_grouping: str = "One epoch per unique obs_id; no arbitrary time-threshold grouping"
    frame: Literal["ICRS"] = "ICRS"
    comparability_assessed: Literal[False] = False
    comparability_note: str = (
        "Discovery and timeline eligibility only. Image subtraction, photometry and "
        "cross-band comparability are Phase 2/3 work and are not asserted here."
    )


class TimelineResponse(StrictModel):
    success: Literal[True] = True
    target: TimelineTarget
    query: TimelineQuery
    total_product_rows: int
    total_products: int
    unique_observations: int
    unique_epochs: int
    earliest_observation_time: str | None
    latest_observation_time: str | None
    temporal_baseline_days: float | None
    available_bands: list[str]
    band_counts: dict[str, int]
    epochs: list[TimelineEpoch]
    quality: TimelineQuality
    warnings: list[TimelineWarning]
    metadata: TimelineMetadata
