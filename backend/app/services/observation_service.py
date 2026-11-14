"""Observation discovery, metadata mapping and cutout orchestration."""

from __future__ import annotations

import base64
import binascii
import logging
import math
import re
from functools import lru_cache
from typing import Any

from app.core.cache import CacheStore
from app.core.config import Settings, get_settings
from app.core.exceptions import IrsaQueryError, ObservationNotFoundError
from app.core.validation import (
    mjd_to_iso,
    to_mjd,
    validate_coordinates,
    validate_cutout_size,
    validate_date_range,
    validate_radius,
)
from app.schemas.cutout import CutoutInfo, CutoutResponse
from app.schemas.observation import (
    ObservationDetail,
    ObservationDetailResponse,
    ObservationSearchResponse,
    ObservationSummary,
)
from app.schemas.common import Target
from app.services.cutout_service import CutoutService
from app.services.fits_service import extract_science_image, get_fits_product, validate_fits_url
from app.services.irsa_client import IrsaClient
from app.services.preview_service import generate_preview
from app.services.wcs_service import celestial_wcs, validate_target_in_cutout

log = logging.getLogger(__name__)

_COLLECTION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class ObservationService:
    def __init__(
        self,
        client: IrsaClient,
        cache: CacheStore,
        settings: Settings | None = None,
    ) -> None:
        self.client = client
        self.cache = cache
        self.settings = settings or get_settings()
        self.cutouts = CutoutService(self.settings)

    # --------------------------------------------------------------- search
    def search_rows(
        self,
        ra: float,
        dec: float,
        radius: float,
        collection: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Validated, cached raw SIA2 rows. Shared by search and timeline services."""
        ra, dec = validate_coordinates(ra, dec)
        radius = validate_radius(radius, self.settings.max_search_radius_deg)
        collection = collection or self.settings.default_collection
        validate_collection(collection)

        start_mjd = to_mjd(start_date, field="start_date")
        end_mjd = to_mjd(end_date, field="end_date")
        validate_date_range(start_mjd, end_mjd)

        key = (round(ra, 6), round(dec, 6), round(radius, 6), collection, start_mjd, end_mjd)
        rows = self.cache.search.get(key)
        if rows is None:
            rows = self.client.search(
                ra, dec, radius, collection=collection, start_mjd=start_mjd, end_mjd=end_mjd
            )
            self.cache.search.set(key, rows)
            log.info("observation_search", extra={"ra": ra, "dec": dec, "radius": radius, "collection": collection, "rows": len(rows)})

        for row in rows:
            token = observation_token(row)
            if token:
                self.cache.observations.set(token, row)
        return rows

    def search(
        self,
        ra: float,
        dec: float,
        radius: float,
        collection: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> ObservationSearchResponse:
        rows = self.search_rows(ra, dec, radius, collection, start_date, end_date)
        ra, dec = validate_coordinates(ra, dec)
        collection = collection or self.settings.default_collection

        summaries = []
        for row in rows:
            token = observation_token(row)
            if not token:
                log.warning("sia_row_without_identifier", extra={"obs_id": row.get("obs_id"), "access_url": row.get("access_url")})
                continue
            summaries.append(_row_to_summary(row, token))

        summaries.sort(key=lambda item: (item.observation_date_mjd if item.observation_date_mjd is not None else math.inf, item.obs_id or "", item.band or ""))

        return ObservationSearchResponse(
            target=Target(ra=ra, dec=dec),
            collection=collection,
            total_observations=len(summaries),
            observations=summaries,
        )

    # --------------------------------------------------------------- detail
    def get_observation(self, observation_id: str) -> ObservationDetailResponse:
        row = self._resolve_row(observation_id)
        return ObservationDetailResponse(observation=_row_to_detail(row, observation_id, self.settings))

    def _resolve_row(self, observation_id: str) -> dict[str, Any]:
        token = (observation_id or "").strip()
        row = self.cache.observations.get(token)
        if row is not None:
            return row

        did = _decode_observation_id(token)
        if did is None:
            raise ObservationNotFoundError(
                "Unknown observation id; run a search first or use an id returned by /observations/search",
                details={"observation_id": observation_id},
            )

        row = self.client.find_by_publisher_did(did)
        if row is None:
            raise ObservationNotFoundError(
                "Observation not found in IRSA",
                details={"observation_id": observation_id, "obs_publisher_did": did},
            )
        self.cache.observations.set(token, row)
        return row

    # --------------------------------------------------------------- cutout
    def create_cutout(self, observation_id: str, ra: float, dec: float, size: float) -> CutoutResponse:
        ra, dec = validate_coordinates(ra, dec)
        size = validate_cutout_size(size, self.settings.max_cutout_size_deg)

        row = self._resolve_row(observation_id)
        product = get_fits_product({**row, "observation_id": observation_id}, self.settings)

        artifact = self.cutouts.fetch(product.access_url, ra, dec, size, session=self.client.session)
        image = extract_science_image(artifact.path)
        wcs = celestial_wcs(image.header)
        validation = validate_target_in_cutout(ra, dec, wcs, image.data.shape)

        if not artifact.preview_path.is_file():
            generate_preview(artifact.path, artifact.preview_path, self.settings)
        elif not _is_png(artifact.preview_path):
            artifact.preview_path.unlink(missing_ok=True)
            generate_preview(artifact.path, artifact.preview_path, self.settings)

        prefix = self.settings.api_prefix.rstrip("/")
        return CutoutResponse(
            observation_id=observation_id,
            cutout=CutoutInfo(
                fits_url=f"{prefix}/files/{artifact.path.name}",
                preview_url=f"{prefix}/files/{artifact.preview_path.name}",
                width=image.width,
                height=image.height,
                unit=str(image.unit) if image.unit else None,
            ),
            wcs=validation,
        )


# --------------------------------------------------------------------- mapping
def observation_token(row: dict[str, Any]) -> str | None:
    did = row.get("obs_publisher_did")
    if isinstance(did, str) and did.strip():
        return _encode_observation_id(did.strip())
    url = row.get("access_url")
    if isinstance(url, str) and url.strip():
        # No publisher DID: derive a stable opaque token from the product URL.
        return _encode_observation_id("access:" + url.strip())
    return None


def _encode_observation_id(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_observation_id(token: str) -> str | None:
    if not token or len(token) > 1024:
        return None
    padded = token + "=" * (-len(token) % 4)
    try:
        value = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return value if value.startswith("ivo://") else None


def _row_to_summary(row: dict[str, Any], token: str) -> ObservationSummary:
    access_url = row.get("access_url")
    return ObservationSummary(
        observation_id=token,
        obs_id=_str(row.get("obs_id")),
        obs_publisher_did=_str(row.get("obs_publisher_did")),
        observation_date=mjd_to_iso(_float(row.get("t_min"))),
        observation_date_mjd=_float(row.get("t_min")),
        instrument=_str(row.get("instrument_name")),
        facility=_str(row.get("facility_name")),
        collection=_str(row.get("obs_collection")),
        band=_str(row.get("energy_bandpassname")),
        product_type=_str(row.get("dataproduct_type")),
        product_subtype=_str(row.get("dataproduct_subtype")),
        calib_level=_int(row.get("calib_level")),
        fits_available=bool(access_url and str(access_url).strip()),
    )


def _row_to_detail(row: dict[str, Any], token: str, settings: Settings) -> ObservationDetail:
    summary = _row_to_summary(row, token)
    url = _str(row.get("access_url"))
    trusted = False
    if url:
        try:
            validate_fits_url(url, settings)
            trusted = True
        except Exception:
            trusted = False

    estsize_kb = _float(row.get("access_estsize"))
    return ObservationDetail(
        **summary.model_dump(),
        s_ra=_float(row.get("s_ra")),
        s_dec=_float(row.get("s_dec")),
        t_min_mjd=_float(row.get("t_min")),
        t_max_mjd=_float(row.get("t_max")),
        start_time=mjd_to_iso(_float(row.get("t_min"))),
        end_time=mjd_to_iso(_float(row.get("t_max"))),
        exposure_time_seconds=_float(row.get("t_exptime")),
        access_format=_str(row.get("access_format")),
        access_estsize_bytes=int(estsize_kb * 1024) if estsize_kb is not None else None,
        width=_int(row.get("s_xel1")),
        height=_int(row.get("s_xel2")),
        pixel_scale_arcsec=_float(row.get("s_pixel_scale")),
        fov_deg=_float(row.get("s_fov")),
        fits_access_url=url,
        processing_status="ready" if trusted else "no_fits_product",
    )


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_collection(collection: str | None) -> None:
    if collection is not None and not _COLLECTION_RE.match(collection):
        raise IrsaQueryError("Invalid IRSA collection name", details={"collection": collection})


def _is_png(path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


# ------------------------------------------------- reusable module functions
@lru_cache
def _default_service() -> ObservationService:
    settings = get_settings()
    return ObservationService(
        IrsaClient(settings),
        CacheStore(settings.cache_ttl_seconds, settings.cache_max_entries),
        settings,
    )


def search_observations(
    ra: float,
    dec: float,
    radius: float,
    collection: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    service: ObservationService | None = None,
) -> ObservationSearchResponse:
    """Reusable entry point: genuine IRSA SIA2 cone search."""
    return (service or _default_service()).search(ra, dec, radius, collection, start_date, end_date)
