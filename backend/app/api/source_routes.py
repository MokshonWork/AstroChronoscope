"""Phase 3 source detection and aperture-photometry routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_photometry_service, get_source_detection_service
from app.schemas.common import ErrorResponse
from app.schemas.photometry import PhotometryRequest, PhotometryResponse
from app.schemas.sources import DetectionRequest, DetectionResponse
from app.services.photometry import PhotometryService
from app.services.source_detection import SourceDetectionService

router = APIRouter(prefix="/sources", tags=["sources"])

_ERRORS = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


@router.post(
    "/detect",
    response_model=DetectionResponse,
    responses=_ERRORS,
    summary="Detect compact sources in one SPHEREx epoch",
)
def detect_sources(
    payload: DetectionRequest,
    service: SourceDetectionService = Depends(get_source_detection_service),
) -> DetectionResponse:
    return service.detect(payload)


@router.post(
    "/photometry",
    response_model=PhotometryResponse,
    responses=_ERRORS,
    summary="Measure aperture photometry for detected sources in one epoch",
)
def measure_sources(
    payload: PhotometryRequest,
    service: PhotometryService = Depends(get_photometry_service),
) -> PhotometryResponse:
    return service.measure(payload)
