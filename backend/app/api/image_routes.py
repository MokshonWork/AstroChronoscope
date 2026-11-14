"""FITS inspection and multi-epoch comparison routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_image_comparison_service
from app.schemas.common import ErrorResponse
from app.schemas.image_processing import (
    CompareRequest,
    ComparisonResponse,
    InspectRequest,
    InspectResponse,
)
from app.services.image_comparison import ImageComparisonService

router = APIRouter(prefix="/images", tags=["images"])

_ERRORS = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


@router.post(
    "/inspect",
    response_model=InspectResponse,
    responses=_ERRORS,
    summary="Inspect a SPHEREx FITS product (HDUs, WCS, masks, uncertainty, PSF)",
)
def inspect_image(
    payload: InspectRequest,
    service: ImageComparisonService = Depends(get_image_comparison_service),
) -> InspectResponse:
    inspection = service.inspect(
        observation_id=payload.observation_id,
        ra=payload.ra,
        dec=payload.dec,
        size=payload.size,
    )
    return InspectResponse(inspection=inspection)


@router.post(
    "/compare",
    response_model=ComparisonResponse,
    responses=_ERRORS,
    summary="Compare two SPHEREx epochs on a common grid",
)
def compare_images(
    payload: CompareRequest,
    service: ImageComparisonService = Depends(get_image_comparison_service),
) -> ComparisonResponse:
    return service.compare(payload)


@router.get(
    "/comparisons/{comparison_id}",
    response_model=ComparisonResponse,
    responses=_ERRORS,
    summary="Retrieve a stored comparison by id",
)
def get_comparison(
    comparison_id: str,
    service: ImageComparisonService = Depends(get_image_comparison_service),
) -> ComparisonResponse:
    return service.get_comparison(comparison_id)
