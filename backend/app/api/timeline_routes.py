"""Multi-epoch target timeline route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_timeline_service
from app.schemas.common import ErrorResponse
from app.schemas.timeline import TimelineResponse
from app.services.timeline_service import TimelineService

router = APIRouter(prefix="/targets", tags=["timeline"])

_ERRORS = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


@router.get(
    "/timeline",
    response_model=TimelineResponse,
    responses=_ERRORS,
    summary="Discover multi-epoch SPHEREx observations for a target",
)
def target_timeline(
    ra: float = Query(..., ge=0.0, lt=360.0, description="ICRS right ascension in degrees"),
    dec: float = Query(..., ge=-90.0, le=90.0, description="ICRS declination in degrees"),
    radius: float = Query(0.01, gt=0.0, description="Search radius in degrees"),
    collection: str | None = Query(None, description="IRSA collection; defaults to server config"),
    bands: list[str] | None = Query(None, description="Repeatable band filter, e.g. ?bands=SPHEREx-D3&bands=SPHEREx-D6"),
    start_date: str | None = Query(None, description="ISO 8601 inclusive lower bound (UTC)"),
    end_date: str | None = Query(None, description="ISO 8601 inclusive upper bound (UTC)"),
    service: TimelineService = Depends(get_timeline_service),
) -> TimelineResponse:
    return service.build(
        ra=ra,
        dec=dec,
        radius=radius,
        collection=collection,
        bands=bands,
        start_date=start_date,
        end_date=end_date,
    )
