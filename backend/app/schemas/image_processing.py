"""Image inspection and multi-epoch comparison request/response schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import StrictModel, Target

Status = Literal["PASS", "WARNING", "FAIL"]


# ------------------------------------------------------------------ inspection
class HduInfo(StrictModel):
    index: int
    name: str
    kind: Literal["primary", "image", "table"]
    shape: list[int] | None = None
    dtype: str | None = None
    unit: str | None = None


class WcsValidation(StrictModel):
    available: bool
    ctype: list[str] | None = None
    projection: str | None = None
    has_sip: bool = False
    crval_ra_deg: float | None = None
    crval_dec_deg: float | None = None
    crpix: list[float] | None = None
    pixel_scale_arcsec: float | None = None
    orientation_deg: float | None = None
    footprint: list[list[float]] | None = None
    center_ra_deg: float | None = None
    center_dec_deg: float | None = None
    target_inside_image: bool | None = None
    target_pixel: list[float] | None = None
    message: str | None = None


class MaskInfo(StrictModel):
    available: bool
    extname: str | None = None
    invalid_pixels: int | None = None
    invalid_fraction: float | None = None
    policy: str | None = None
    bits: dict[str, str] = Field(default_factory=dict)


class VarianceInfo(StrictModel):
    available: bool
    extname: str | None = None
    unit: str | None = None
    finite_pixels: int | None = None
    min: float | None = None
    max: float | None = None


class PsfInfo(StrictModel):
    available: bool
    extname: str | None = None
    entries: int | None = None
    oversampling: int | None = None
    fwhm_samples: float | None = None
    fwhm_px: float | None = None
    fwhm_arcsec: float | None = None
    wavelength_min_um: float | None = None
    wavelength_max_um: float | None = None
    stars_median: int | None = None
    message: str | None = None


class ImageStatistics(StrictModel):
    shape: list[int]
    dtype: str
    bunit: str | None = None
    finite_pixels: int
    nan_pixels: int
    inf_pixels: int
    finite_fraction: float
    valid_pixels: int
    valid_fraction: float
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None


class ImageHeaderMetadata(StrictModel):
    observation_id: str | None = None
    obs_publisher_did: str | None = None
    obs_id: str | None = None
    band: str | None = None
    date_obs: str | None = None
    mjd_obs: float | None = None
    date_avg: str | None = None
    mjd_avg: float | None = None
    timesys: str | None = None
    exposure_time_seconds: float | None = None
    telescope: str | None = None
    instrument: str | None = None
    software_version: str | None = None


class InspectionWarning(StrictModel):
    code: str
    message: str


class ImageInspection(StrictModel):
    source: Literal["cutout", "product"]
    source_url: str
    fits_sha256: str
    science_extname: str
    hdus: list[HduInfo]
    stats: ImageStatistics
    wcs: WcsValidation
    mask: MaskInfo
    variance: VarianceInfo
    psf: PsfInfo
    metadata: ImageHeaderMetadata
    quality: Status
    warnings: list[InspectionWarning]


class InspectRequest(StrictModel):
    observation_id: str = Field(..., min_length=1, max_length=1024)
    ra: float | None = Field(None, ge=0.0, lt=360.0, allow_inf_nan=False)
    dec: float | None = Field(None, ge=-90.0, le=90.0, allow_inf_nan=False)
    size: float | None = Field(None, gt=0.0, allow_inf_nan=False, description="Cutout size in degrees")


class InspectResponse(StrictModel):
    success: Literal[True] = True
    inspection: ImageInspection


# ------------------------------------------------------------------ comparison
class ComparisonConfig(StrictModel):
    background_correction: Literal["none", "median"] = "median"
    photometric_scaling: Literal["none", "median_ratio"] = "none"
    psf_matching: Literal["none", "gaussian"] = "none"
    reference: Literal["first", "second"] = "first"
    interpolation: Literal["bilinear", "nearest"] = "bilinear"


class CompareRequest(StrictModel):
    observation_id_a: str = Field(..., min_length=1, max_length=1024)
    observation_id_b: str = Field(..., min_length=1, max_length=1024)
    ra: float = Field(..., ge=0.0, lt=360.0, allow_inf_nan=False)
    dec: float = Field(..., ge=-90.0, le=90.0, allow_inf_nan=False)
    size: float = Field(0.2, gt=0.0, allow_inf_nan=False, description="Shared cutout size in degrees")
    config: ComparisonConfig = Field(default_factory=ComparisonConfig)


class CheckResult(StrictModel):
    name: str
    status: Status
    message: str
    details: dict[str, Any] | None = None


class QualityAssessment(StrictModel):
    overall_status: Status
    checks: list[CheckResult]
    warnings: list[str]


class InputProduct(StrictModel):
    observation_id: str
    obs_publisher_did: str | None = None
    obs_id: str | None = None
    band: str | None = None
    observation_time: str | None = None
    bunit: str | None = None
    shape: list[int]
    pixel_scale_arcsec: float | None = None
    fwhm_arcsec: float | None = None
    fits_sha256: str
    source: Literal["cutout", "product"]


class ReprojectionInfo(StrictModel):
    method: Literal["bilinear", "nearest"]
    reference: Literal["first", "second"]
    output_shape: list[int]
    pixel_scale_arcsec: float
    valid_fraction_a: float
    valid_fraction_b: float
    common_valid_pixels: int
    common_coverage_fraction: float
    overlap_fraction: float
    flux_conserving: bool = False
    note: str


class AlignmentInfo(StrictModel):
    target_pixel_a: list[float] | None = None
    target_pixel_b: list[float] | None = None
    pixel_scale_ratio: float | None = None
    orientation_difference_deg: float | None = None
    residual_dx_px: float | None = None
    residual_dy_px: float | None = None
    residual_offset_px: float | None = None
    residual_offset_arcsec: float | None = None
    peak_confidence: float | None = None
    verified: bool
    status: Status
    message: str


class BackgroundInfo(StrictModel):
    method: Literal["none", "median"]
    level: float | None = None
    sigma: float | None = None
    mesh_variation: float | None = None
    reliable: bool | None = None
    applied: bool
    note: str


class PhotometricInfo(StrictModel):
    bunit_a: str | None = None
    bunit_b: str | None = None
    units_compatible: bool | None = None
    scale_factor: float
    scaling_method: Literal["none", "median_ratio"]
    absolute_calibration: bool
    note: str


class PsfCompatibility(StrictModel):
    fwhm_a_arcsec: float | None = None
    fwhm_b_arcsec: float | None = None
    ratio: float | None = None
    matching: Literal["none", "gaussian"]
    matched: bool
    status: Literal["PASS", "WARNING", "FAIL", "UNKNOWN"]
    note: str


class DifferenceStatistics(StrictModel):
    valid_pixels: int
    valid_fraction: float
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    robust_sigma: float | None = None


class DifferenceProduct(StrictModel):
    fits_url: str
    preview_url: str | None = None
    uncertainty_url: str | None = None
    mask_url: str | None = None
    width: int
    height: int
    unit: str | None = None
    uncertainty_available: bool
    uncertainty_note: str
    statistics: DifferenceStatistics


class ProvenanceInfo(StrictModel):
    comparison_id: str
    created_at: str
    software: dict[str, str]
    algorithm: str
    reprojection_method: str
    reference_wcs: dict[str, Any]
    background: dict[str, Any]
    photometric: dict[str, Any]
    psf: dict[str, Any]
    masking: dict[str, Any]
    uncertainty: dict[str, Any]
    input_sha256: dict[str, str]
    quality: dict[str, str]
    cache_key: str


class ComparisonMetadata(StrictModel):
    size_deg: float
    difference_model: str = "D = I_b - alpha * I_a"
    note: str = (
        "Pixel-level difference of two reprojected SPHEREx products. A produced difference "
        "image is a measurement product, not a variability detection or photometric catalogue."
    )


class ComparisonResponse(StrictModel):
    success: Literal[True] = True
    comparison_id: str
    status: Literal["completed", "rejected"]
    scientifically_valid: bool
    comparability_assessed: Literal[True] = True
    target: Target
    config: ComparisonConfig
    inputs: list[InputProduct]
    reprojection: ReprojectionInfo | None = None
    alignment: AlignmentInfo | None = None
    backgrounds: list[BackgroundInfo] | None = None
    photometric: PhotometricInfo | None = None
    psf: PsfCompatibility | None = None
    difference: DifferenceProduct | None = None
    quality: QualityAssessment
    provenance: ProvenanceInfo
    metadata: ComparisonMetadata
