"""PSF compatibility: what is verified, what is matched, and what is unsupported."""

from __future__ import annotations

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


def product(*, fwhm_px: float = 2.0, with_psf: bool = True, rotation_deg: float = 0.0,
            crpix=(32.5, 32.5), seed: int = 7) -> bytes:
    return make_sky_fits_bytes(
        sources=SOURCES,
        noise=1e-6,
        rotation_deg=rotation_deg,
        crpix=crpix,
        with_psf=with_psf,
        psf_fwhm_px=fwhm_px,
        seed=seed,
    )


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


def test_compatible_psf_is_verified_but_not_matched(settings, fake_client) -> None:
    service, token_a, token_b = harness(
        settings, fake_client, product(fwhm_px=2.0), product(fwhm_px=2.1, rotation_deg=18.0, crpix=(40.5, 24.5), seed=11)
    )

    response = compare(service, token_a, token_b)

    assert response.psf.status == "PASS"
    assert response.psf.verified is True
    assert response.psf.metadata_available_a is True
    assert response.psf.metadata_available_b is True
    assert response.psf.matched is False
    assert response.psf.matching_supported is False
    assert response.psf.spatially_varying is False
    assert response.psf.ratio < 1.15
    assert check(response, "psf").status == "PASS"


def test_missing_psf_metadata_is_not_verified(settings, fake_client) -> None:
    service, token_a, token_b = harness(
        settings,
        fake_client,
        product(with_psf=False),
        product(with_psf=False, rotation_deg=18.0, crpix=(40.5, 24.5), seed=11),
    )

    response = compare(service, token_a, token_b)

    assert response.psf.status == "NOT_VERIFIED"
    assert response.psf.verified is False
    assert response.psf.metadata_available_a is False
    assert response.psf.metadata_available_b is False
    assert response.psf.matched is False
    assert response.scientifically_valid is False
    assert check(response, "psf").status == "NOT_VERIFIED"


def test_one_sided_psf_metadata_is_not_verified(settings, fake_client) -> None:
    service, token_a, token_b = harness(
        settings,
        fake_client,
        product(fwhm_px=2.0),
        product(with_psf=False, rotation_deg=18.0, crpix=(40.5, 24.5), seed=11),
    )

    response = compare(service, token_a, token_b)

    assert response.psf.metadata_available_a is True
    assert response.psf.metadata_available_b is False
    assert response.psf.verified is False
    assert response.psf.status == "NOT_VERIFIED"


def test_psf_mismatch_fails_the_comparison(settings, fake_client) -> None:
    service, token_a, token_b = harness(
        settings, fake_client, product(fwhm_px=2.0), product(fwhm_px=4.0, rotation_deg=18.0, crpix=(40.5, 24.5), seed=11)
    )

    response = compare(service, token_a, token_b)

    assert response.psf.status == "FAIL"
    assert response.psf.ratio > 1.4
    assert response.psf.matched is False
    assert response.status == "rejected"
    assert response.scientifically_valid is False


def test_psf_warning_between_thresholds_blocks_validity(settings, fake_client) -> None:
    service, token_a, token_b = harness(
        settings, fake_client, product(fwhm_px=2.0), product(fwhm_px=2.4, rotation_deg=18.0, crpix=(40.5, 24.5), seed=11)
    )

    response = compare(service, token_a, token_b)

    assert response.psf.status == "WARNING"
    assert response.psf.matched is False
    assert response.status == "completed"
    assert response.scientifically_valid is False


def test_matching_is_unsupported_without_psf_metadata(settings, fake_client) -> None:
    service, token_a, token_b = harness(
        settings,
        fake_client,
        product(with_psf=False),
        product(with_psf=False, rotation_deg=18.0, crpix=(40.5, 24.5), seed=11),
    )

    response = compare(service, token_a, token_b, psf_matching="gaussian")

    assert response.psf.status == "NOT_VERIFIED"
    assert response.psf.matching_supported is False
    assert response.psf.matched is False
    assert "no convolution was applied" in response.psf.note


def test_matching_is_performed_only_when_requested(settings, fake_client) -> None:
    service, token_a, token_b = harness(
        settings, fake_client, product(fwhm_px=2.0), product(fwhm_px=2.4, rotation_deg=18.0, crpix=(40.5, 24.5), seed=11)
    )

    response = compare(service, token_a, token_b, psf_matching="gaussian")

    assert response.psf.matched is True
    assert response.psf.matching_supported is True
    assert response.psf.method == "global_gaussian_convolution"
    assert response.psf.sigma_px is not None and response.psf.sigma_px > 0
    assert response.psf.spatially_varying is False
    assert response.psf.status == "PASS"
    assert "spatially varying PSF not modelled" in response.psf.note


def test_provenance_records_psf_provenance(settings, fake_client) -> None:
    service, token_a, token_b = harness(
        settings, fake_client, product(fwhm_px=2.0), product(fwhm_px=2.4, rotation_deg=18.0, crpix=(40.5, 24.5), seed=11)
    )

    response = compare(service, token_a, token_b, psf_matching="gaussian")
    psf = response.provenance.psf

    assert psf["matching"] == "gaussian"
    assert psf["matched"] is True
    assert psf["matching_supported"] is True
    assert psf["spatially_varying"] is False
    assert psf["method"] == "global_gaussian_convolution"
    assert psf["verified"] is True
    assert psf["status"] == "PASS"
    assert len(psf["fwhm_arcsec"]) == 2
