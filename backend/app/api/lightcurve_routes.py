"""Phase 3 target-level light-curve route."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_target_lightcurve_service
from app.schemas.common import ErrorResponse
from app.schemas.lightcurve import TargetLightCurveRequest, TargetLightCurveResponse
from app.services.lightcurve_service import TargetLightCurveService

router = APIRouter(prefix="/targets", tags=["lightcurve"])

_ERRORS = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


@router.post(
    "/lightcurve",
    response_model=TargetLightCurveResponse,
    responses=_ERRORS,
    summary="Detect, photometer and associate a target across SPHEREx epochs",
)
def target_lightcurve(
    payload: TargetLightCurveRequest,
    service: TargetLightCurveService = Depends(get_target_lightcurve_service),
) -> TargetLightCurveResponse:
    return service.build(payload)
