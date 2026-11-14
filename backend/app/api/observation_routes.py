"""Observation discovery, detail and cutout routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_observation_service
from app.schemas.common import ErrorResponse
from app.schemas.cutout import CutoutRequest, CutoutResponse
from app.schemas.observation import (
    ObservationDetailResponse,
    ObservationSearchRequest,
    ObservationSearchResponse,
)
from app.services.observation_service import ObservationService

router = APIRouter(prefix="/observations", tags=["observations"])

_ERRORS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}, 504: {"model": ErrorResponse}}


@router.post("/search", response_model=ObservationSearchResponse, responses=_ERRORS, summary="Search IRSA for SPHEREx observations")
def search_observations(
    payload: ObservationSearchRequest,
    response: Response,
    service: ObservationService = Depends(get_observation_service),
) -> ObservationSearchResponse:
    result = service.search(
        ra=payload.ra,
        dec=payload.dec,
        radius=payload.radius,
        collection=payload.collection,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    response.headers["X-Observation-Count"] = str(result.total_observations)
    return result


@router.get("/{observation_id}", response_model=ObservationDetailResponse, responses=_ERRORS, summary="Observation metadata by id")
def get_observation(
    observation_id: str,
    service: ObservationService = Depends(get_observation_service),
) -> ObservationDetailResponse:
    return service.get_observation(observation_id)


@router.post("/{observation_id}/cutout", response_model=CutoutResponse, responses=_ERRORS, summary="Download and process a cutout")
def create_cutout(
    observation_id: str,
    payload: CutoutRequest,
    service: ObservationService = Depends(get_observation_service),
) -> CutoutResponse:
    return service.create_cutout(observation_id, payload.ra, payload.dec, payload.size)
