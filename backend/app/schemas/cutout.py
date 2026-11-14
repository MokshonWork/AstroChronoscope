"""Cutout request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import StrictModel


class CutoutRequest(StrictModel):
    ra: float = Field(..., ge=0.0, lt=360.0, allow_inf_nan=False)
    dec: float = Field(..., ge=-90.0, le=90.0, allow_inf_nan=False)
    size: float = Field(0.1, gt=0.0, allow_inf_nan=False, description="Cutout width/height in degrees")


class CutoutInfo(StrictModel):
    fits_url: str
    preview_url: str
    width: int
    height: int
    unit: str | None = None


class WcsInfo(StrictModel):
    available: bool
    target_inside_image: bool | None = None
    pixel_x: float | None = None
    pixel_y: float | None = None
    message: str | None = None


class CutoutResponse(StrictModel):
    success: Literal[True] = True
    observation_id: str
    cutout: CutoutInfo
    wcs: WcsInfo
