"""Multi-epoch observation discovery: normalize SIA2 rows, group epochs, summarize.

Scientific rules implemented here:

* One SIA product row is not an observation. Products are grouped by ``obs_id``.
* An epoch is one unique ``obs_id`` (no arbitrary time-threshold grouping).
* Observation time is the midpoint of SIA2 ``t_min``/``t_max`` (MJD, UTC). FITS
  headers confirm ``MJD-AVG`` equals that midpoint for SPHEREx products.
* Missing values stay ``None``; they are never replaced by zero or invented.
* ``target_covered`` comes from the SIA2 ``s_region`` footprint, not from the
  search cone. ``None`` means "unknown", not "outside".
"""

from __future__ import annotations

import math
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable

from app.core.exceptions import InvalidBandFilterError, PlanetXError
from app.core.validation import mjd_to_iso, to_mjd, validate_coordinates, validate_date_range, validate_radius
from app.schemas.timeline import (
    TimelineEpoch,
    TimelineMetadata,
    TimelineProduct,
    TimelineQuality,
    TimelineQuery,
    TimelineResponse,
    TimelineTarget,
    TimelineWarning,
)
from app.services.fits_service import validate_fits_url
from app.services.observation_service import ObservationService, observation_token, validate_collection

_BAND_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
_MJD_MIN, _MJD_MAX = 0.0, 100000.0

_WARNING_MESSAGES: dict[str, str] = {
    "missing_product_identifier": "Product has neither obs_publisher_did nor access_url",
    "missing_publisher_did": "Product has no obs_publisher_did; id derived from access_url",
    "missing_obs_id": "Product has no obs_id; it cannot be grouped with sibling bands",
    "missing_band": "Product has no energy_bandpassname",
    "missing_timestamp": "Product has neither t_min nor t_max",
    "missing_start_time": "Product has no valid t_min",
    "missing_end_time": "Product has no valid t_max",
    "invalid_start_time": "Product t_min is present but not a valid MJD",
    "invalid_end_time": "Product t_max is present but not a valid MJD",
    "invalid_time_order": "Product t_max is earlier than t_min",
    "missing_spatial_metadata": "Product has no s_ra/s_dec/s_region",
    "invalid_spatial_metadata": "Product s_region is not a parseable POLYGON/CIRCLE",
    "target_outside_footprint": "Target is outside this product's s_region footprint",
    "untrusted_access_url": "Product access_url is not a trusted HTTPS IRSA URL",
    "conflicting_product_metadata": "Same obs_publisher_did appeared with different metadata",
    "inconsistent_epoch_timestamps": "Products in this obs_id disagree on t_min/t_max",
    "partial_epoch_timestamps": "Some products in this obs_id lack valid timestamps",
    "epoch_without_timestamp": "No product in this obs_id has a valid timestamp",
    "inconsistent_epoch_metadata": "Products in this obs_id disagree on facility/instrument/collection",
    "empty_result": "IRSA returned no products for this target",
}


@dataclass
class _Normalized:
    product: TimelineProduct
    obs_id: str | None
    t_min: float | None
    t_max: float | None
    index: int


class TimelineService:
    """Build a normalized multi-epoch timeline from the existing IRSA search."""

    def __init__(self, observations: ObservationService) -> None:
        self.observations = observations
        self.settings = observations.settings
        self.cache = observations.cache

    def build(
        self,
        ra: float,
        dec: float,
        radius: float = 0.01,
        collection: str | None = None,
        bands: Iterable[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> TimelineResponse:
        ra, dec = validate_coordinates(ra, dec)
        radius = validate_radius(radius, self.settings.max_search_radius_deg)
        collection = collection or self.settings.default_collection
        validate_collection(collection)
        band_filter = _normalize_bands(bands)

        start_mjd = to_mjd(start_date, field="start_date")
        end_mjd = to_mjd(end_date, field="end_date")
        validate_date_range(start_mjd, end_mjd)

        key = (
            round(ra, 6),
            round(dec, 6),
            round(radius, 6),
            collection,
            tuple(sorted(b.casefold() for b in band_filter)) if band_filter else None,
            start_mjd,
            end_mjd,
        )
        cached = self.cache.timeline.get(key)
        if cached is not None:
            return cached

        rows = self.observations.search_rows(
            ra, dec, radius, collection=collection, start_date=start_date, end_date=end_date
        )
        result = self._build_response(ra, dec, radius, collection, band_filter, start_date, end_date, rows)
        self.cache.timeline.set(key, result)
        return result

    # ------------------------------------------------------------ construction
    def _build_response(
        self,
        ra: float,
        dec: float,
        radius: float,
        collection: str,
        bands: list[str] | None,
        start_date: str | None,
        end_date: str | None,
        rows: list[dict[str, Any]],
    ) -> TimelineResponse:
        normalized = [_normalize(row, index, self.settings, ra, dec) for index, row in enumerate(rows)]
        if bands:
            wanted = {band.casefold() for band in bands}
            normalized = [item for item in normalized if item.product.band and item.product.band.casefold() in wanted]

        kept, duplicates_removed = _dedupe(normalized)
        epochs, inconsistent = _group_epochs(kept)

        included = kept
        band_counts = Counter(item.product.band for item in included if item.product.band)
        available_bands = sorted(band_counts)

        eligible = [epoch for epoch in epochs if epoch.midpoint_mjd is not None]
        earliest = eligible[0] if eligible else None
        latest = eligible[-1] if eligible else None
        baseline = (latest.midpoint_mjd - earliest.midpoint_mjd) if earliest and latest else None

        quality = TimelineQuality(
            total_product_rows=len(rows),
            products_included=len(included),
            duplicate_products_removed=duplicates_removed,
            products_with_valid_timestamps=sum(1 for item in included if item.product.timeline_eligible),
            products_without_valid_timestamps=sum(1 for item in included if not item.product.timeline_eligible),
            products_with_band=sum(1 for item in included if item.product.band),
            products_without_band=sum(1 for item in included if not item.product.band),
            products_with_spatial_metadata=sum(1 for item in included if item.product.s_ra is not None and item.product.s_dec is not None and item.product.target_covered is not None),
            products_without_spatial_metadata=sum(1 for item in included if item.product.s_ra is None or item.product.s_dec is None or item.product.target_covered is None),
            products_target_covered=sum(1 for item in included if item.product.target_covered is True),
            products_target_outside=sum(1 for item in included if item.product.target_covered is False),
            products_target_coverage_unknown=sum(1 for item in included if item.product.target_covered is None),
            products_missing_identifier=sum(1 for item in included if item.product.observation_id is None),
            inconsistent_epochs=inconsistent,
            metadata_complete_products=sum(1 for item in included if item.product.metadata_complete),
            metadata_incomplete_products=sum(1 for item in included if not item.product.metadata_complete),
        )

        warning_counts: Counter[str] = Counter()
        for item in included:
            warning_counts.update(item.product.warnings)
        for epoch in epochs:
            warning_counts.update(epoch.warnings)
        if not rows:
            warning_counts["empty_result"] += 1
        warnings = [
            TimelineWarning(code=code, message=_WARNING_MESSAGES.get(code, code), count=count)
            for code, count in sorted(warning_counts.items())
        ]

        return TimelineResponse(
            target=TimelineTarget(ra=ra, dec=dec),
            query=TimelineQuery(
                radius_deg=radius,
                collection=collection,
                bands=bands,
                start_date=start_date,
                end_date=end_date,
            ),
            total_product_rows=len(rows),
            total_products=len(included),
            unique_observations=len(epochs),
            unique_epochs=len(epochs),
            earliest_observation_time=earliest.observation_time if earliest else None,
            latest_observation_time=latest.observation_time if latest else None,
            temporal_baseline_days=baseline,
            available_bands=available_bands,
            band_counts=dict(sorted(band_counts.items())),
            epochs=epochs,
            quality=quality,
            warnings=warnings,
            metadata=TimelineMetadata(),
        )


# ------------------------------------------------------------------ normalization
def _normalize(row: dict[str, Any], index: int, settings: Any, target_ra: float, target_dec: float) -> _Normalized:
    did = _text(row.get("obs_publisher_did"))
    url = _text(row.get("access_url"))
    obs_id = _text(row.get("obs_id"))
    band = _text(row.get("energy_bandpassname"))

    raw_min, raw_max = row.get("t_min"), row.get("t_max")
    t_min = _valid_mjd(raw_min)
    t_max = _valid_mjd(raw_max)
    midpoint = _midpoint(t_min, t_max)

    s_ra = _finite(row.get("s_ra"))
    s_dec = _finite(row.get("s_dec"))
    region = _text(row.get("s_region"))
    covered = _region_contains(region, target_ra, target_dec) if region else None
    distance = _angular_distance(target_ra, target_dec, s_ra, s_dec) if s_ra is not None and s_dec is not None else None

    warnings: list[str] = []
    missing: list[str] = []
    if not did:
        missing.append("obs_publisher_did")
        warnings.append("missing_publisher_did" if url else "missing_product_identifier")
    if not url:
        missing.append("access_url")
    if not obs_id:
        missing.append("obs_id")
        warnings.append("missing_obs_id")
    if not band:
        missing.append("energy_bandpassname")
        warnings.append("missing_band")
    if t_min is None:
        missing.append("t_min")
        warnings.append("invalid_start_time" if _present(raw_min) else "missing_start_time")
    if t_max is None:
        missing.append("t_max")
        warnings.append("invalid_end_time" if _present(raw_max) else "missing_end_time")
    if t_min is None and t_max is None:
        warnings.append("missing_timestamp")
    if t_min is not None and t_max is not None and t_max < t_min:
        warnings.append("invalid_time_order")
    if s_ra is None:
        missing.append("s_ra")
    if s_dec is None:
        missing.append("s_dec")
    if not region:
        missing.append("s_region")
        warnings.append("missing_spatial_metadata")
    elif covered is None:
        warnings.append("invalid_spatial_metadata")
    if covered is False:
        warnings.append("target_outside_footprint")

    trusted = False
    status = "no_fits_product"
    if url:
        try:
            validate_fits_url(url, settings)
            trusted = True
            status = "ready"
        except PlanetXError:
            status = "untrusted_access_url"
            warnings.append("untrusted_access_url")

    observation_id = observation_token(row)
    product = TimelineProduct(
        observation_id=observation_id,
        obs_publisher_did=did,
        obs_id=obs_id,
        band=band,
        em_min_m=_finite(row.get("em_min")),
        em_max_m=_finite(row.get("em_max")),
        t_min_mjd=t_min,
        t_max_mjd=t_max,
        start_time=mjd_to_iso(t_min),
        end_time=mjd_to_iso(t_max),
        observation_time=mjd_to_iso(midpoint),
        timeline_eligible=midpoint is not None,
        exposure_time_seconds=_finite(row.get("t_exptime")),
        calib_level=_integer(row.get("calib_level")),
        dataproduct_type=_text(row.get("dataproduct_type")),
        dataproduct_subtype=_text(row.get("dataproduct_subtype")),
        target_name=_text(row.get("target_name")),
        facility=_text(row.get("facility_name")),
        instrument=_text(row.get("instrument_name")),
        collection=_text(row.get("obs_collection")),
        fits_available=bool(url),
        fits_access_url=url,
        access_url_trusted=trusted,
        processing_status=status,
        s_ra=s_ra,
        s_dec=s_dec,
        fov_deg=_finite(row.get("s_fov")),
        width=_integer(row.get("s_xel1")),
        height=_integer(row.get("s_xel2")),
        pixel_scale_arcsec=_finite(row.get("s_pixel_scale")),
        target_covered=covered,
        angular_distance_deg=distance,
        metadata_complete=not missing,
        missing_fields=sorted(set(missing)),
        warnings=sorted(set(warnings)),
    )
    return _Normalized(product=product, obs_id=obs_id, t_min=t_min, t_max=t_max, index=index)


def _dedupe(items: list[_Normalized]) -> tuple[list[_Normalized], int]:
    """Drop exact duplicate products; keep conflicting rows and flag them."""
    kept: list[_Normalized] = []
    signatures: set[tuple[Any, ...]] = set()
    by_did: dict[str, tuple[Any, ...]] = {}
    removed = 0
    for item in items:
        p = item.product
        signature = (p.obs_publisher_did, p.fits_access_url, p.band, p.t_min_mjd, p.t_max_mjd, p.obs_id, p.s_ra, p.s_dec)
        if signature in signatures:
            removed += 1
            continue
        signatures.add(signature)
        if p.obs_publisher_did:
            previous = by_did.get(p.obs_publisher_did)
            if previous is not None and previous != signature:
                p.warnings = sorted(set(p.warnings) | {"conflicting_product_metadata"})
            by_did[p.obs_publisher_did] = signature
        kept.append(item)
    return kept, removed


# ---------------------------------------------------------------------- grouping
def _group_epochs(items: list[_Normalized]) -> tuple[list[TimelineEpoch], int]:
    groups: OrderedDict[str, list[_Normalized]] = OrderedDict()
    for item in items:
        key = item.obs_id or item.product.obs_publisher_did or f"unidentified-{item.index}"
        groups.setdefault(key, []).append(item)

    epochs: list[TimelineEpoch] = []
    inconsistent = 0
    for key, members in groups.items():
        members.sort(key=lambda m: (m.product.band or "", m.product.obs_publisher_did or "", m.index))
        products = [m.product for m in members]
        mins = [m.t_min for m in members if m.t_min is not None]
        maxs = [m.t_max for m in members if m.t_max is not None]
        start = min(mins) if mins else None
        end = max(maxs) if maxs else None
        midpoint = _midpoint(start, end)

        warnings: set[str] = set()
        if len({round(value, 9) for value in mins}) > 1 or len({round(value, 9) for value in maxs}) > 1:
            warnings.add("inconsistent_epoch_timestamps")
            inconsistent += 1
        if len(mins) != len(members) or len(maxs) != len(members):
            warnings.add("partial_epoch_timestamps")
        if midpoint is None:
            warnings.add("epoch_without_timestamp")

        facilities = {p.facility for p in products if p.facility}
        instruments = {p.instrument for p in products if p.instrument}
        collections = {p.collection for p in products if p.collection}
        if len(facilities) > 1 or len(instruments) > 1 or len(collections) > 1:
            warnings.add("inconsistent_epoch_metadata")
            inconsistent += 1

        covered_values = [p.target_covered for p in products]
        covered: bool | None
        if any(value is True for value in covered_values):
            covered = True
        elif covered_values and all(value is False for value in covered_values):
            covered = False
        else:
            covered = None

        representative = next((p for p in products if p.s_ra is not None and p.s_dec is not None), None)
        bands = sorted({p.band for p in products if p.band})
        epoch = TimelineEpoch(
            epoch_index=0,
            epoch_key=key,
            obs_id=members[0].obs_id,
            identified=members[0].obs_id is not None,
            observation_time=mjd_to_iso(midpoint),
            start_time=mjd_to_iso(start),
            end_time=mjd_to_iso(end),
            midpoint_mjd=midpoint,
            duration_seconds=(end - start) * 86400.0 if start is not None and end is not None else None,
            timeline_eligible=midpoint is not None,
            bands=bands,
            product_count=len(products),
            target_covered=covered,
            target_covered_products=sum(1 for value in covered_values if value is True),
            footprint_center_ra=representative.s_ra if representative else None,
            footprint_center_dec=representative.s_dec if representative else None,
            footprint_fov_deg=representative.fov_deg if representative else None,
            facility=next((p.facility for p in products if p.facility), None),
            instrument=next((p.instrument for p in products if p.instrument), None),
            collection=next((p.collection for p in products if p.collection), None),
            products=products,
            warnings=sorted(warnings),
        )
        epochs.append(epoch)

    epochs.sort(key=lambda e: (e.midpoint_mjd if e.midpoint_mjd is not None else math.inf, e.epoch_key))
    return [epoch.model_copy(update={"epoch_index": index}) for index, epoch in enumerate(epochs)], inconsistent


# ------------------------------------------------------------------------ helpers
def _normalize_bands(bands: Iterable[str] | None) -> list[str] | None:
    if bands is None:
        return None
    cleaned = []
    for band in bands:
        text = (band or "").strip()
        if not text:
            continue
        if not _BAND_RE.match(text):
            raise InvalidBandFilterError("Invalid band filter value", details={"band": band})
        cleaned.append(text)
    return cleaned or None


def _midpoint(t_min: float | None, t_max: float | None) -> float | None:
    if t_min is not None and t_max is not None:
        return (t_min + t_max) / 2.0
    return t_min if t_min is not None else t_max


def _valid_mjd(value: Any) -> float | None:
    number = _finite(value)
    if number is None or not (_MJD_MIN < number < _MJD_MAX):
        return None
    return number


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _unit_vector(ra: float, dec: float) -> tuple[float, float, float]:
    ra_r, dec_r = math.radians(ra), math.radians(dec)
    return (math.cos(dec_r) * math.cos(ra_r), math.cos(dec_r) * math.sin(ra_r), math.sin(dec_r))


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _angular_distance(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    cosine = max(-1.0, min(1.0, _dot(_unit_vector(ra1, dec1), _unit_vector(ra2, dec2))))
    return math.degrees(math.acos(cosine))


def _region_contains(region: str, ra: float, dec: float) -> bool | None:
    parsed = _parse_region(region)
    if parsed is None:
        return None
    shape, data = parsed
    if shape == "circle":
        center_ra, center_dec, radius = data
        return _angular_distance(ra, dec, center_ra, center_dec) <= radius
    return _point_in_polygon(ra, dec, data)


def _parse_region(region: str) -> tuple[str, Any] | None:
    tokens = region.split()
    if not tokens:
        return None
    shape = tokens[0].upper()
    values = tokens[1:]
    if values and values[0].upper() == "ICRS":
        values = values[1:]
    try:
        numbers = [float(value) for value in values]
    except ValueError:
        return None
    if shape == "POLYGON":
        if len(numbers) < 6 or len(numbers) % 2:
            return None
        points = list(zip(numbers[0::2], numbers[1::2]))
        if len(points) > 1 and points[0] == points[-1]:
            points = points[:-1]
        return ("polygon", points) if len(points) >= 3 else None
    if shape == "CIRCLE" and len(numbers) == 3:
        return ("circle", (numbers[0], numbers[1], numbers[2]))
    return None


def _point_in_polygon(ra: float, dec: float, vertices: list[tuple[float, float]]) -> bool:
    """Spherical winding number; boundary vertices count as inside."""
    point = _unit_vector(ra, dec)
    total = 0.0
    count = len(vertices)
    for index in range(count):
        a = _unit_vector(*vertices[index])
        b = _unit_vector(*vertices[(index + 1) % count])
        if _dot(point, a) > 1.0 - 1e-12:
            return True
        numerator = _dot(point, _cross(a, b))
        denominator = _dot(a, b) - _dot(a, point) * _dot(b, point)
        total += math.atan2(numerator, denominator)
    return abs(total) > math.pi
