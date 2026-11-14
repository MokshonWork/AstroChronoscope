"""Observation mapping, caching, detail resolution and cutout orchestration."""

from __future__ import annotations

import re

import pytest

from app.core.cache import CacheStore
from app.core.exceptions import (
    InvalidCoordinateError,
    InvalidCutoutSizeError,
    InvalidDateError,
    IrsaQueryError,
    MissingAccessUrlError,
    ObservationNotFoundError,
    UntrustedUrlError,
)
from app.services.observation_service import ObservationService, _encode_observation_id
from tests.conftest import (
    FakeIrsaClient,
    FakeResponse,
    FakeSession,
    TARGET_DEC,
    TARGET_RA,
    make_fits_bytes,
    sample_row,
)


def make_service(settings, rows=None, fits_bytes: bytes | None = None):
    fake = FakeIrsaClient(settings, rows)
    if fits_bytes is not None:
        fake.session = FakeSession(FakeResponse(content=fits_bytes, headers={"Content-Type": "image/fits"}))
    return ObservationService(fake, CacheStore(60, 32), settings), fake


def test_search_maps_sia_rows(settings) -> None:
    rows = [
        sample_row(t_min=61180.0, t_max=61180.1, obs_id="later", obs_publisher_did="ivo://irsa.ipac/spherex_qr3?later/D3"),
        sample_row(),
    ]
    service, _ = make_service(settings, rows)

    result = service.search(TARGET_RA, TARGET_DEC, 0.01, "spherex_qr3")

    assert result.total_observations == 2
    assert result.collection == "spherex_qr3"
    first = result.observations[0]
    assert first.obs_id == "2026W20_2A_0436_3"  # sorted by start time
    assert first.observation_date.startswith("2026-05-17T06:36:54")
    assert first.instrument == "SPHEREx"
    assert first.band == "SPHEREx-D3"
    assert first.calib_level == 2
    assert first.product_subtype == "science"
    assert first.fits_available is True
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first.observation_id)


def test_observation_ids_are_unique_per_product(settings) -> None:
    rows = [
        sample_row(),
        sample_row(energy_bandpassname="SPHEREx-D6", obs_publisher_did="ivo://irsa.ipac/spherex_qr3?2026W20_2A_0436_3/D6"),
    ]
    service, _ = make_service(settings, rows)
    result = service.search(TARGET_RA, TARGET_DEC, 0.01)
    ids = {item.observation_id for item in result.observations}
    assert len(ids) == 2


def test_empty_search_is_not_an_error(settings) -> None:
    service, _ = make_service(settings, [])
    result = service.search(TARGET_RA, TARGET_DEC, 0.01)
    assert result.total_observations == 0
    assert result.observations == []


def test_default_collection_is_explicit(settings) -> None:
    service, fake = make_service(settings)
    result = service.search(TARGET_RA, TARGET_DEC, 0.01)
    assert result.collection == "spherex_qr3"
    assert fake.search_calls[-1]["collection"] == "spherex_qr3"


def test_invalid_collection_rejected(settings) -> None:
    service, _ = make_service(settings)
    with pytest.raises(IrsaQueryError):
        service.search(TARGET_RA, TARGET_DEC, 0.01, collection="bad collection!")


def test_invalid_inputs(settings) -> None:
    service, _ = make_service(settings)
    with pytest.raises(InvalidCoordinateError):
        service.search(400.0, 0.0, 0.01)
    with pytest.raises(InvalidDateError):
        service.search(TARGET_RA, TARGET_DEC, 0.01, start_date="2026-01-02", end_date="2026-01-01")
    with pytest.raises(InvalidCutoutSizeError):
        service.create_cutout("abc", TARGET_RA, TARGET_DEC, 5.0)


def test_detail_uses_search_cache(settings) -> None:
    service, fake = make_service(settings)
    result = service.search(TARGET_RA, TARGET_DEC, 0.01)
    observation_id = result.observations[0].observation_id

    detail = service.get_observation(observation_id)

    assert detail.observation.obs_publisher_did == fake.rows[0]["obs_publisher_did"]
    assert detail.observation.exposure_time_seconds == pytest.approx(113.5826)
    assert detail.observation.width == 2040
    assert detail.observation.fits_access_url.startswith("https://irsa.ipac.caltech.edu/ibe/")
    assert detail.observation.processing_status == "ready"
    assert fake.did_calls == []


def test_detail_resolves_from_irsa_on_cache_miss(settings) -> None:
    service, fake = make_service(settings)
    observation_id = _encode_observation_id(fake.rows[0]["obs_publisher_did"])

    detail = service.get_observation(observation_id)

    assert fake.did_calls == [fake.rows[0]["obs_publisher_did"]]
    assert detail.observation.obs_id == "2026W20_2A_0436_3"


def test_unknown_observation_id(settings) -> None:
    service, fake = make_service(settings)
    token = _encode_observation_id("ivo://irsa.ipac/spherex_qr3?NOPE/D3")
    with pytest.raises(ObservationNotFoundError):
        service.get_observation(token)
    with pytest.raises(ObservationNotFoundError):
        service.get_observation("not-base64!")


def test_cutout_end_to_end(settings) -> None:
    fits_bytes = make_fits_bytes()
    service, fake = make_service(settings, fits_bytes=fits_bytes)
    observation_id = service.search(TARGET_RA, TARGET_DEC, 0.01).observations[0].observation_id

    result = service.create_cutout(observation_id, TARGET_RA, TARGET_DEC, 0.1)

    assert result.cutout.width == 58 and result.cutout.height == 59
    assert result.cutout.unit == "MJy / sr"
    assert result.cutout.fits_url.startswith("/api/v1/files/") and result.cutout.fits_url.endswith(".fits")
    assert result.cutout.preview_url.endswith(".png")
    assert result.wcs.available is True
    assert result.wcs.target_inside_image is True
    assert result.wcs.pixel_x == pytest.approx(28.5, abs=1e-3)
    assert result.wcs.pixel_y == pytest.approx(29.0, abs=1e-3)

    requested_url = fake.session.calls[0]["url"]
    assert "center=156.09328159%2C-41.64466331" in requested_url
    assert "size=0.100000deg" in requested_url

    fits_path = settings.fits_dir / result.cutout.fits_url.rsplit("/", 1)[-1]
    preview_path = settings.previews_dir / result.cutout.preview_url.rsplit("/", 1)[-1]
    assert fits_path.is_file() and preview_path.is_file()

    # Second request is served from the disk cache.
    service.create_cutout(observation_id, TARGET_RA, TARGET_DEC, 0.1)
    assert len(fake.session.calls) == 1


def test_cutout_target_outside(settings) -> None:
    fits_bytes = make_fits_bytes(target_ra=TARGET_RA + 0.2, target_dec=TARGET_DEC)
    service, _ = make_service(settings, fits_bytes=fits_bytes)
    observation_id = service.search(TARGET_RA, TARGET_DEC, 0.01).observations[0].observation_id

    result = service.create_cutout(observation_id, TARGET_RA, TARGET_DEC, 0.1)

    assert result.wcs.available is True
    assert result.wcs.target_inside_image is False
    assert result.wcs.pixel_x is not None


def test_cutout_regenerates_missing_cached_files(settings) -> None:
    service, fake = make_service(settings, fits_bytes=make_fits_bytes())
    observation_id = service.search(TARGET_RA, TARGET_DEC, 0.01).observations[0].observation_id
    first = service.create_cutout(observation_id, TARGET_RA, TARGET_DEC, 0.1)

    fits_path = settings.fits_dir / first.cutout.fits_url.rsplit("/", 1)[-1]
    preview_path = settings.previews_dir / first.cutout.preview_url.rsplit("/", 1)[-1]
    fits_path.unlink()
    preview_path.unlink()

    second = service.create_cutout(observation_id, TARGET_RA, TARGET_DEC, 0.1)

    assert len(fake.session.calls) == 2
    assert second.cutout.fits_url == first.cutout.fits_url
    assert fits_path.is_file() and preview_path.is_file()


def test_cutout_requires_access_url(settings) -> None:
    service, _ = make_service(settings, [sample_row(access_url=None)], fits_bytes=make_fits_bytes())
    observation_id = service.search(TARGET_RA, TARGET_DEC, 0.01).observations[0].observation_id

    with pytest.raises(MissingAccessUrlError):
        service.create_cutout(observation_id, TARGET_RA, TARGET_DEC, 0.1)


def test_cutout_rejects_untrusted_url(settings) -> None:
    service, _ = make_service(settings, [sample_row(access_url="https://evil.example.com/x.fits")], fits_bytes=make_fits_bytes())
    observation_id = service.search(TARGET_RA, TARGET_DEC, 0.01).observations[0].observation_id

    with pytest.raises(UntrustedUrlError):
        service.create_cutout(observation_id, TARGET_RA, TARGET_DEC, 0.1)
