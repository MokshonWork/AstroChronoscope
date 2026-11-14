"""Deterministic multi-epoch normalization, grouping and quality tests."""

from __future__ import annotations

import pytest

from app.core.cache import CacheStore
from app.core.exceptions import (
    InvalidBandFilterError,
    InvalidCoordinateError,
    InvalidDateError,
    InvalidRadiusError,
)
from app.core.validation import mjd_to_iso, to_mjd
from app.services.observation_service import ObservationService
from app.services.timeline_service import TimelineService
from tests.conftest import FakeIrsaClient, TARGET_DEC, TARGET_RA, sample_row

COLLECTION = "spherex_qr3"
T0 = 61177.0


def product(obs_id: str, band: str | None, t_min: float | None = T0, t_max: float | None = None, **overrides):
    if t_max is None and t_min is not None:
        t_max = t_min + 0.001
    suffix = band.rsplit("-", 1)[-1] if band else "X"
    did = overrides.pop("obs_publisher_did", f"ivo://irsa.ipac/spherex_qr3?{obs_id}/{suffix}")
    access_url = overrides.pop(
        "access_url",
        f"https://irsa.ipac.caltech.edu/ibe/data/spherex/qr3/level2/{obs_id}_{suffix}.fits",
    )
    return sample_row(
        obs_id=obs_id,
        energy_bandpassname=band,
        t_min=t_min,
        t_max=t_max,
        obs_publisher_did=did,
        access_url=access_url,
        **overrides,
    )


def make_service(settings, rows):
    fake = FakeIrsaClient(settings, rows)
    return TimelineService(ObservationService(fake, CacheStore(60, 512), settings)), fake


def build(service, **overrides):
    params = {"ra": TARGET_RA, "dec": TARGET_DEC, "radius": 0.01, "collection": COLLECTION}
    params.update(overrides)
    return service.build(**params)


def test_products_of_one_observation_form_one_epoch(settings) -> None:
    rows = [
        product("obs-a", "SPHEREx-D3"),
        product("obs-a", "SPHEREx-D6"),
    ]
    service, _ = make_service(settings, rows)

    result = build(service)

    assert result.total_product_rows == 2
    assert result.total_products == 2
    assert result.unique_observations == 1
    assert result.unique_epochs == 1
    epoch = result.epochs[0]
    assert epoch.epoch_key == "obs-a"
    assert epoch.identified is True
    assert epoch.product_count == 2
    assert epoch.bands == ["SPHEREx-D3", "SPHEREx-D6"]
    assert epoch.observation_time == mjd_to_iso(T0 + 0.0005)
    assert epoch.observation_time.endswith("+00:00")
    assert epoch.duration_seconds == pytest.approx(0.001 * 86400.0)


def test_distinct_obs_ids_are_sorted_epochs(settings) -> None:
    rows = [
        product("late", "SPHEREx-D1", t_min=T0 + 10.0),
        product("early", "SPHEREx-D2", t_min=T0),
    ]
    service, _ = make_service(settings, rows)

    result = build(service)

    assert [epoch.epoch_key for epoch in result.epochs] == ["early", "late"]
    assert [epoch.epoch_index for epoch in result.epochs] == [0, 1]
    assert result.earliest_observation_time == mjd_to_iso(T0 + 0.0005)
    assert result.latest_observation_time == mjd_to_iso(T0 + 10.0005)
    assert result.temporal_baseline_days == pytest.approx(10.0)


def test_multiple_bands_are_counted(settings) -> None:
    rows = [
        product("obs-a", "SPHEREx-D3"),
        product("obs-a", "SPHEREx-D6"),
        product("obs-b", "SPHEREx-D3", t_min=T0 + 1.0),
    ]
    service, _ = make_service(settings, rows)

    result = build(service)

    assert result.available_bands == ["SPHEREx-D3", "SPHEREx-D6"]
    assert result.band_counts == {"SPHEREx-D3": 2, "SPHEREx-D6": 1}


def test_exact_duplicate_products_are_removed(settings) -> None:
    row = product("obs-a", "SPHEREx-D3")
    service, _ = make_service(settings, [row, dict(row)])

    result = build(service)

    assert result.total_product_rows == 2
    assert result.total_products == 1
    assert result.quality.duplicate_products_removed == 1
    assert result.unique_epochs == 1


def test_conflicting_same_did_is_kept_and_flagged(settings) -> None:
    rows = [
        product("obs-a", "SPHEREx-D3", t_min=T0),
        product("obs-a", "SPHEREx-D3", t_min=T0 + 0.5),
    ]
    service, _ = make_service(settings, rows)

    result = build(service)

    assert result.total_products == 2
    assert any(w.code == "conflicting_product_metadata" for w in result.warnings)
    assert result.quality.inconsistent_epochs == 1
    epoch = result.epochs[0]
    assert "inconsistent_epoch_timestamps" in epoch.warnings
    assert epoch.start_time == mjd_to_iso(T0)
    assert epoch.end_time == mjd_to_iso(T0 + 0.501)


def test_missing_timestamp_is_preserved_as_none(settings) -> None:
    service, _ = make_service(settings, [product("obs-a", "SPHEREx-D3", t_min=None, t_max=None)])

    result = build(service)

    product_row = result.epochs[0].products[0]
    assert product_row.t_min_mjd is None and product_row.t_max_mjd is None
    assert product_row.observation_time is None
    assert product_row.timeline_eligible is False
    assert {"t_min", "t_max"} <= set(product_row.missing_fields)
    assert "missing_timestamp" in product_row.warnings
    assert result.epochs[0].timeline_eligible is False
    assert result.epochs[0].observation_time is None
    assert "epoch_without_timestamp" in result.epochs[0].warnings
    assert result.earliest_observation_time is None
    assert result.latest_observation_time is None
    assert result.temporal_baseline_days is None
    assert result.quality.products_without_valid_timestamps == 1


def test_invalid_timestamp_is_flagged_not_dropped(settings) -> None:
    service, _ = make_service(settings, [product("obs-a", "SPHEREx-D3", t_min="not-a-number", t_max=T0 + 0.001)])

    result = build(service)

    product_row = result.epochs[0].products[0]
    assert product_row.t_min_mjd is None
    assert "invalid_start_time" in product_row.warnings
    assert result.total_products == 1


def test_missing_band_and_identifier(settings) -> None:
    row = product("obs-a", None, obs_publisher_did=None, access_url=None)
    service, _ = make_service(settings, [row])

    result = build(service)

    product_row = result.epochs[0].products[0]
    assert product_row.band is None
    assert product_row.observation_id is None
    assert product_row.fits_available is False
    assert product_row.processing_status == "no_fits_product"
    assert product_row.metadata_complete is False
    assert {"energy_bandpassname", "obs_publisher_did", "access_url"} <= set(product_row.missing_fields)
    assert "missing_band" in product_row.warnings
    assert "missing_product_identifier" in product_row.warnings
    assert result.quality.products_missing_identifier == 1


def test_missing_obs_id_creates_unidentified_epoch(settings) -> None:
    row = sample_row(
        obs_id=None,
        energy_bandpassname="SPHEREx-D3",
        obs_publisher_did=None,
        access_url=None,
        t_min=T0,
        t_max=T0 + 0.001,
    )
    service, _ = make_service(settings, [row])

    result = build(service)

    epoch = result.epochs[0]
    assert epoch.identified is False
    assert epoch.epoch_key.startswith("unidentified-")
    assert "missing_obs_id" in epoch.products[0].warnings


def test_target_coverage_uses_s_region(settings) -> None:
    containing = "POLYGON ICRS 155 -40 157 -40 157 -43 155 -43 155 -40"
    outside = "POLYGON ICRS 10 10 11 10 11 11 10 11 10 10"
    rows = [
        product("inside", "SPHEREx-D3", s_region=containing),
        product("outside", "SPHEREx-D3", s_region=outside, t_min=T0 + 1.0),
    ]
    service, _ = make_service(settings, rows)

    result = build(service)

    assert result.epochs[0].target_covered is True
    assert result.epochs[1].target_covered is False
    assert result.quality.products_target_covered == 1
    assert result.quality.products_target_outside == 1
    assert any(w.code == "target_outside_footprint" for w in result.warnings)


def test_missing_spatial_metadata_is_unknown(settings) -> None:
    service, _ = make_service(settings, [product("obs-a", "SPHEREx-D3", s_region=None, s_ra=None, s_dec=None)])

    result = build(service)

    product_row = result.epochs[0].products[0]
    assert product_row.target_covered is None
    assert "missing_spatial_metadata" in product_row.warnings
    assert result.quality.products_target_coverage_unknown == 1
    assert result.epochs[0].target_covered is None


def test_band_filter_restricts_products_and_epochs(settings) -> None:
    rows = [
        product("obs-a", "SPHEREx-D3"),
        product("obs-a", "SPHEREx-D6"),
        product("obs-b", "SPHEREx-D3", t_min=T0 + 1.0),
    ]
    service, _ = make_service(settings, rows)

    result = build(service, bands=["SPHEREx-D3"])

    assert result.total_products == 2
    assert result.unique_epochs == 2
    assert result.available_bands == ["SPHEREx-D3"]
    assert all(p.band == "SPHEREx-D3" for e in result.epochs for p in e.products)


def test_invalid_band_filter_rejected(settings) -> None:
    service, _ = make_service(settings, [])
    with pytest.raises(InvalidBandFilterError):
        build(service, bands=["bad band!"])


def test_empty_result_is_valid_success(settings) -> None:
    service, _ = make_service(settings, [])

    result = build(service)

    assert result.success is True
    assert result.total_product_rows == 0
    assert result.total_products == 0
    assert result.unique_epochs == 0
    assert result.epochs == []
    assert result.earliest_observation_time is None
    assert result.temporal_baseline_days is None
    assert [w.code for w in result.warnings] == ["empty_result"]


def test_invalid_inputs(settings) -> None:
    service, _ = make_service(settings, [])
    with pytest.raises(InvalidCoordinateError):
        build(service, ra=400.0)
    with pytest.raises(InvalidRadiusError):
        build(service, radius=2.0)
    with pytest.raises(InvalidDateError):
        build(service, start_date="2026-01-02", end_date="2026-01-01")


def test_date_filters_are_forwarded_to_sia(settings) -> None:
    service, fake = make_service(settings, [product("obs-a", "SPHEREx-D3")])

    build(service, start_date="2026-05-17", end_date="2026-05-18")

    call = fake.search_calls[-1]
    assert call["start_mjd"] == pytest.approx(to_mjd("2026-05-17", field="start_date"))
    assert call["end_mjd"] == pytest.approx(to_mjd("2026-05-18", field="end_date"))


def test_cache_hit_skips_irsa(settings) -> None:
    service, fake = make_service(settings, [product("obs-a", "SPHEREx-D3")])

    first = build(service)
    second = build(service)

    assert second is first
    assert len(fake.search_calls) == 1


def test_band_filter_has_its_own_cache_entry(settings) -> None:
    rows = [
        product("obs-a", "SPHEREx-D3"),
        product("obs-a", "SPHEREx-D6"),
    ]
    service, fake = make_service(settings, rows)

    all_bands = build(service)
    filtered = build(service, bands=["SPHEREx-D3"])

    assert all_bands.total_products == 2
    assert filtered.total_products == 1
    assert len(fake.search_calls) == 1  # raw rows reused, timeline results differ
