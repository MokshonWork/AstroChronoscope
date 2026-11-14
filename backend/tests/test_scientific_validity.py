"""Scientific-validity semantics: PASS / WARNING / FAIL / NOT_VERIFIED.

A comparison may complete computationally and still be scientifically invalid.
These tests pin that distinction: only a comparison whose every critical check
is PASS may report ``scientifically_valid=True``.
"""

from __future__ import annotations

import pytest

from app.core.cache import CacheStore
from app.schemas.image_processing import CompareRequest, ComparisonConfig
from app.services.image_comparison import ImageComparisonService
from app.services.observation_service import ObservationService
from tests.conftest import (
    TARGET_DEC,
    TARGET_RA,
    FakeIrsaClient,
    make_sky_fits_bytes,
    seed_pair,
)

SOURCES = (
    (0.0, 0.0, 1e-2, 12.0),
    (60.0, 40.0, 6e-3, 10.0),
    (-55.0, -35.0, 4e-3, 14.0),
)


def epoch_a(**overrides) -> bytes:
    options = dict(sources=SOURCES, noise=1e-6, with_psf=True, psf_fwhm_px=2.0)
    options.update(overrides)
    return make_sky_fits_bytes(**options)


def epoch_b(**overrides) -> bytes:
    options = dict(
        sources=SOURCES,
        noise=1e-6,
        rotation_deg=18.0,
        crpix=(40.5, 24.5),
        with_psf=True,
        psf_fwhm_px=2.1,
        seed=11,
    )
    options.update(overrides)
    return make_sky_fits_bytes(**options)


def harness(settings, fake_client: FakeIrsaClient, a: bytes, b: bytes):
    token_a, token_b = seed_pair(fake_client, a, b)
    observations = ObservationService(fake_client, CacheStore(60, 32), settings)
    observations.search_rows(TARGET_RA, TARGET_DEC, 0.01)
    return ImageComparisonService(observations), token_a, token_b


def compare(service, token_a, token_b, **config):
    return service.compare(
        CompareRequest(
            observation_id_a=token_a,
            observation_id_b=token_b,
            ra=TARGET_RA,
            dec=TARGET_DEC,
            size=0.2,
            config=ComparisonConfig(**config),
        )
    )


def check(response, name: str):
    return next(item for item in response.quality.checks if item.name == name)


def test_fully_verified_comparison_is_scientifically_valid(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b)

    assert response.status == "completed"
    assert response.scientifically_valid is True
    assert response.quality.overall_status == "PASS"
    assert response.quality.failed_checks == []
    assert response.quality.unverified_checks == []
    assert set(response.quality.critical_checks) == {
        check_.name for check_ in response.quality.checks if check_.critical
    }


def test_every_check_is_explicitly_marked_critical(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b)

    for item in response.quality.checks:
        assert item.critical is True
    names = {item.name for item in response.quality.checks}
    assert {
        "fits_a", "fits_b", "wcs_a", "wcs_b", "reprojection", "coverage", "alignment",
        "background", "units", "photometric", "masking", "uncertainty", "psf",
    } <= names


def test_missing_units_is_not_verified_and_blocks_validity(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b(bunit=None))

    response = compare(service, token_a, token_b)

    assert response.status == "completed"  # the difference is still computed
    assert check(response, "units").status == "NOT_VERIFIED"
    assert "units" in response.quality.unverified_checks
    assert response.quality.overall_status == "NOT_VERIFIED"
    assert response.scientifically_valid is False


def test_missing_variance_is_not_verified_and_blocks_validity(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b(with_variance=False))

    response = compare(service, token_a, token_b)

    assert response.status == "completed"
    assert check(response, "uncertainty").status == "NOT_VERIFIED"
    assert response.difference.uncertainty_available is False
    assert response.scientifically_valid is False


def test_missing_flags_mask_is_not_verified(settings, fake_client) -> None:
    service, token_a, token_b = harness(
        settings, fake_client, epoch_a(with_flags=False), epoch_b(with_flags=False)
    )

    response = compare(service, token_a, token_b)

    assert check(response, "masking").status == "NOT_VERIFIED"
    assert response.scientifically_valid is False


def test_unverifiable_alignment_is_not_verified(settings, fake_client) -> None:
    """Featureless images cannot be aligned independently, so validity is refused."""
    service, token_a, token_b = harness(
        settings, fake_client, epoch_a(fill_value=1e-3), epoch_b(fill_value=1e-3)
    )

    response = compare(service, token_a, token_b)

    assert response.status == "completed"
    assert check(response, "alignment").status == "NOT_VERIFIED"
    assert response.alignment.verified is False
    assert response.scientifically_valid is False


def test_disabled_background_correction_is_not_verified(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b, background_correction="none")

    assert check(response, "background").status == "NOT_VERIFIED"
    assert response.scientifically_valid is False


def test_relative_photometric_scaling_is_not_verified(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b, photometric_scaling="median_ratio")

    assert response.photometric.absolute_calibration is False
    assert check(response, "photometric").status == "NOT_VERIFIED"
    assert response.scientifically_valid is False


def test_failed_critical_check_rejects_and_lists_the_failure(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b(bunit="Jy"))

    response = compare(service, token_a, token_b)

    assert response.status == "rejected"
    assert response.difference is None
    assert response.quality.overall_status == "FAIL"
    assert "units" in response.quality.failed_checks
    assert response.scientifically_valid is False


def test_not_verified_is_never_serialized_as_pass(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b(bunit=None))

    response = compare(service, token_a, token_b)
    payload = response.model_dump()

    statuses = {item["name"]: item["status"] for item in payload["quality"]["checks"]}
    assert statuses["units"] == "NOT_VERIFIED"
    assert statuses["photometric"] == "NOT_VERIFIED"
    assert payload["scientifically_valid"] is False
    assert set(payload["quality"]["unverified_checks"]) == {"units", "photometric"}


def test_validity_requires_completion(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b(bunit="Jy"))

    response = compare(service, token_a, token_b)

    assert response.status == "rejected"
    assert response.scientifically_valid is False
    assert response.quality.overall_status == "FAIL"


@pytest.mark.parametrize("config", [{}, {"background_correction": "none"}])
def test_validity_is_a_derived_field(settings, fake_client, config) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b, **config)

    derived = all(item.status == "PASS" for item in response.quality.checks if item.critical)
    assert response.scientifically_valid is derived
