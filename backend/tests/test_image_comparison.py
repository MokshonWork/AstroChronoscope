"""Scientific comparison engine tests on synthetic SPHEREx-shaped products."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest
from astropy.io import fits

from app.core.cache import CacheStore
from app.core.exceptions import ComparisonNotFoundError, ImageProcessingLimitError
from app.schemas.image_processing import CompareRequest, ComparisonConfig
from app.services.cutout_service import CutoutService
from app.services.image_comparison import ImageComparisonService, estimate_background
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
        psf_fwhm_px=2.2,
        seed=11,
    )
    options.update(overrides)
    return make_sky_fits_bytes(**options)


def harness(settings, fake_client: FakeIrsaClient, a: bytes, b: bytes, *, cache: CacheStore | None = None):
    token_a, token_b = seed_pair(fake_client, a, b)
    observations = ObservationService(fake_client, cache or CacheStore(60, 32), settings)
    observations.search_rows(TARGET_RA, TARGET_DEC, 0.01)
    return ImageComparisonService(observations), token_a, token_b


def compare(service, token_a, token_b, **config):
    request = CompareRequest(
        observation_id_a=token_a,
        observation_id_b=token_b,
        ra=TARGET_RA,
        dec=TARGET_DEC,
        size=0.2,
        config=ComparisonConfig(**config),
    )
    return service.compare(request)


def check(response, name: str):
    return next(item for item in response.quality.checks if item.name == name)


# --------------------------------------------------------------- core science
def test_identical_products_produce_a_zero_difference(settings, fake_client) -> None:
    payload = epoch_a()
    service, token_a, token_b = harness(settings, fake_client, payload, payload)

    response = compare(service, token_a, token_b)

    assert response.status == "completed"
    assert response.scientifically_valid is True
    assert response.difference is not None
    assert response.difference.statistics.max == pytest.approx(0.0, abs=1e-12)
    assert response.difference.statistics.min == pytest.approx(0.0, abs=1e-12)
    assert response.difference.statistics.robust_sigma == pytest.approx(0.0, abs=1e-12)
    assert check(response, "alignment").status == "PASS"


def test_different_wcs_becomes_comparable_after_reprojection(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b)

    assert response.status == "completed"
    assert response.reprojection is not None
    assert response.reprojection.output_shape == [64, 64]
    assert response.reprojection.common_coverage_fraction > 0.3
    assert response.reprojection.flux_conserving is False
    assert response.alignment.orientation_difference_deg == pytest.approx(18.0, abs=1.0)
    assert response.difference.statistics.max < 0.1 * 1e-2


def test_shifted_source_is_caught_as_misalignment(settings, fake_client) -> None:
    shifted = (
        (0.0 + 30.0, 0.0, 1e-2, 12.0),
        (60.0 + 30.0, 40.0, 6e-3, 10.0),
        (-55.0 + 30.0, -35.0, 4e-3, 14.0),
    )
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_a(sources=shifted, seed=21))

    response = compare(service, token_a, token_b)

    assert response.status == "rejected"
    assert response.difference is None
    assert check(response, "alignment").status == "FAIL"
    assert response.alignment.residual_offset_px > 1.5
    assert response.alignment.verified is True


def test_reference_second_uses_the_other_grid(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b, reference="second")

    assert response.status == "completed"
    assert response.reprojection.reference == "second"
    assert response.provenance.reference_wcs["shape"] == [64, 64]


# --------------------------------------------------------------- prerequisites
def test_incompatible_units_prevent_subtraction(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b(bunit="Jy"))

    response = compare(service, token_a, token_b)

    assert response.status == "rejected"
    assert response.scientifically_valid is False
    assert response.difference is None
    assert check(response, "units").status == "FAIL"
    assert response.photometric.units_compatible is False
    assert response.photometric.scale_factor == 1.0


def test_missing_bunit_is_not_verified_not_a_pass(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b(bunit=None))

    response = compare(service, token_a, token_b)

    assert response.status == "completed"
    assert check(response, "units").status == "NOT_VERIFIED"
    assert response.photometric.units_compatible is None
    assert response.photometric.absolute_calibration is False
    assert response.scientifically_valid is False


def test_insufficient_overlap_prevents_comparison(settings, fake_client) -> None:
    far = epoch_b(crval=(TARGET_RA + 1.0, TARGET_DEC))
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), far)

    response = compare(service, token_a, token_b)

    assert response.status == "rejected"
    assert response.difference is None
    assert check(response, "coverage").status == "FAIL"
    assert response.reprojection.common_valid_pixels == 0


def test_psf_incompatibility_is_not_ignored(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b(psf_fwhm_px=4.0))

    response = compare(service, token_a, token_b)

    assert response.status == "rejected"
    assert response.difference is None
    assert check(response, "psf").status == "FAIL"
    assert response.psf.ratio > 1.4
    assert response.psf.matched is False


def test_psf_matching_is_applied_only_when_requested(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b(psf_fwhm_px=2.4))

    unmatched = compare(service, token_a, token_b)
    assert unmatched.psf.status == "WARNING"
    assert unmatched.psf.matched is False
    assert unmatched.scientifically_valid is False

    matched = compare(service, token_a, token_b, psf_matching="gaussian")
    assert matched.status == "completed"
    assert matched.psf.matched is True
    assert matched.psf.matching_supported is True
    assert matched.psf.status == "PASS"
    assert "Gaussian kernel" in matched.psf.note


def test_missing_uncertainty_is_explicit(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b(with_variance=False))

    response = compare(service, token_a, token_b)

    assert response.status == "completed"
    assert response.difference.uncertainty_available is False
    assert response.difference.uncertainty_url is None
    assert check(response, "uncertainty").status == "NOT_VERIFIED"
    assert "no propagated uncertainty" in response.difference.uncertainty_note.lower()
    assert response.scientifically_valid is False


def test_uncertainty_is_propagated_when_available(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b)

    assert response.difference.uncertainty_available is True
    assert response.difference.uncertainty_url is not None
    path = settings.fits_dir / response.difference.uncertainty_url.rsplit("/", 1)[-1]
    with fits.open(path) as hdul:
        sigma = hdul[0].data
        assert hdul[0].header["UNCTYPE"] == "1-sigma standard deviation"
    # Interpolation averages independent pixels, so the propagated variance is
    # below the naive sum: sqrt(V_a) <= sigma_D < sqrt(V_a + V_b).
    naive = np.sqrt(4e-8 + 4e-8)
    single = np.sqrt(4e-8)
    assert np.nanmedian(sigma) < naive
    assert np.nanmedian(sigma) >= single
    assert np.nanmax(sigma) <= naive * (1 + 1e-6)
    assert response.difference.uncertainty_valid_fraction == pytest.approx(1.0)


# ------------------------------------------------------- photometry/background
def test_background_correction_removes_additive_offsets(settings, fake_client) -> None:
    offset = 5e-4
    service, token_a, token_b = harness(
        settings, fake_client, epoch_a(), epoch_b(background=1e-3 + offset)
    )

    corrected = compare(service, token_a, token_b)
    uncorrected = compare(service, token_a, token_b, background_correction="none")

    assert corrected.difference.statistics.median == pytest.approx(0.0, abs=2e-5)
    assert uncorrected.difference.statistics.median == pytest.approx(offset, abs=2e-5)
    assert uncorrected.backgrounds[1].applied is False


def test_median_ratio_scaling_is_relative_and_reported(settings, fake_client) -> None:
    service, token_a, token_b = harness(
        settings,
        fake_client,
        epoch_a(),
        epoch_b(background=1.5e-3, sources=tuple((x, y, 1.5 * amp, s) for x, y, amp, s in SOURCES)),
    )

    response = compare(service, token_a, token_b, photometric_scaling="median_ratio")

    assert response.photometric.scaling_method == "median_ratio"
    assert response.photometric.scale_factor == pytest.approx(1.5, rel=0.05)
    assert response.photometric.absolute_calibration is False
    assert "relative scale" in response.photometric.note
    assert response.scientifically_valid is False  # relative scale absorbs real variability


def test_absolute_calibration_requires_calibrated_units(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b)

    assert response.photometric.units_compatible is True
    assert response.photometric.absolute_calibration is True


def test_background_estimate_rejects_a_source_dominated_image() -> None:
    data = np.full((40, 40), 1e-3)
    data[18:22, 18:22] = 1.0
    valid = np.ones((40, 40), dtype=bool)

    estimate = estimate_background(data, valid)

    assert estimate.reliable is True
    assert estimate.level == pytest.approx(1e-3, rel=0.05)


def test_background_estimate_flags_too_few_pixels() -> None:
    data = np.full((40, 40), 1e-3)
    valid = np.zeros((40, 40), dtype=bool)

    estimate = estimate_background(data, valid)

    assert estimate.reliable is False
    assert estimate.level is None


# --------------------------------------------------------------------- files
def test_difference_products_are_written_with_headers(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b)

    difference_path = settings.fits_dir / response.difference.fits_url.rsplit("/", 1)[-1]
    mask_path = settings.fits_dir / response.difference.mask_url.rsplit("/", 1)[-1]
    preview_path = settings.previews_dir / response.difference.preview_url.rsplit("/", 1)[-1]

    assert difference_path.is_file() and mask_path.is_file() and preview_path.is_file()
    assert preview_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    with fits.open(difference_path) as hdul:
        header = hdul[0].header
        data = hdul[0].data
        assert header["BUNIT"] == "MJy / sr"
        assert header["COMPID"] == response.comparison_id
        assert header["ALPHA"] == pytest.approx(1.0)
        assert header["DIFFMETH"] == "I_b - ALPHA*I_a"
        assert header["REPROJ"] == "bilinear"
        assert header["SWVERS"]
        assert header["CRVAL1"] == pytest.approx(TARGET_RA, abs=1e-6)

    with fits.open(mask_path) as hdul:
        mask = hdul[0].data
        assert mask.dtype == np.uint8
        assert set(np.unique(mask)) <= {0, 1}
    assert np.isnan(data[mask == 0]).all()
    assert np.isfinite(data[mask == 1]).all()


def test_comparison_record_round_trips(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b)
    stored = service.get_comparison(response.comparison_id)

    assert stored.comparison_id == response.comparison_id
    assert stored.provenance.input_sha256 == response.provenance.input_sha256
    assert (settings.comparisons_dir / f"{response.comparison_id}.json").is_file()


def test_unknown_comparison_id_raises_not_found(settings, fake_client) -> None:
    service, *_ = harness(settings, fake_client, epoch_a(), epoch_b())

    with pytest.raises(ComparisonNotFoundError):
        service.get_comparison("0" * 32)
    with pytest.raises(ComparisonNotFoundError):
        service.get_comparison("../../etc/passwd")


def test_provenance_records_every_transformation(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b, photometric_scaling="median_ratio")
    provenance = response.provenance

    assert provenance.comparison_id == response.comparison_id
    assert provenance.created_at
    assert provenance.software["planet_x_backend"]
    assert provenance.software["astropy"]
    assert provenance.algorithm.startswith("D = I_b - alpha * I_a")
    assert provenance.reprojection_method.startswith("bilinear")
    assert provenance.background["correction"] == "median"
    assert provenance.photometric["method"] == "median_ratio"
    assert provenance.psf["matching"] == "none"
    assert provenance.uncertainty["available"] is True
    assert set(provenance.input_sha256) == {token_a, token_b}
    assert provenance.cache_key == response.provenance.cache_key
    assert provenance.quality["alignment"] == "PASS"


# ------------------------------------------------------- safety and determinism
def test_input_files_are_never_modified(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())
    compare(service, token_a, token_b)

    cutouts = CutoutService(settings)
    inputs = [
        settings.fits_dir / f"{cutouts.cache_key(row['access_url'], TARGET_RA, TARGET_DEC, 0.2)}.fits"
        for row in fake_client.rows
    ]
    assert all(path.is_file() for path in inputs)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}

    compare(service, token_a, token_b, reference="second")

    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    assert before == after


def test_repeated_processing_is_deterministic(settings, fake_client) -> None:
    payload_a, payload_b = epoch_a(), epoch_b()
    first_service, token_a, token_b = harness(settings, fake_client, payload_a, payload_b)
    first = compare(first_service, token_a, token_b)

    second_service, token_a2, token_b2 = harness(settings, fake_client, payload_a, payload_b)
    second = compare(second_service, token_a2, token_b2)

    assert first.comparison_id == second.comparison_id
    assert first.difference.statistics.model_dump() == second.difference.statistics.model_dump()
    assert first.reprojection.model_dump() == second.reprojection.model_dump()

    name = first.difference.fits_url.rsplit("/", 1)[-1]
    with fits.open(settings.fits_dir / name) as hdul:
        repeated = hdul[0].data
    assert np.isfinite(repeated).any()


def test_pixel_limit_is_enforced(settings, fake_client) -> None:
    settings.max_comparison_pixels = 1000
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    with pytest.raises(ImageProcessingLimitError):
        compare(service, token_a, token_b)


def test_source_flags_are_not_masked_out_of_the_comparison(settings, fake_client) -> None:
    """Real SPHEREx FLAGS mark known sources; those pixels must survive into the difference."""
    flags = np.zeros((64, 64), dtype=np.int32)
    flags[:, :] = 1  # SOURCE bit in the fixture
    service, token_a, token_b = harness(
        settings,
        fake_client,
        epoch_a(flags=flags, mask_bits={"SOURCE": "Pixel mapped to a known source"}),
        epoch_b(flags=flags, mask_bits={"SOURCE": "Pixel mapped to a known source"}),
    )

    response = compare(service, token_a, token_b)

    assert response.status == "completed"
    assert response.difference.statistics.valid_fraction > 0.3
    assert response.reprojection.valid_fraction_a == pytest.approx(1.0)


def test_inputs_are_reported_with_real_metadata(settings, fake_client) -> None:
    service, token_a, token_b = harness(settings, fake_client, epoch_a(), epoch_b())

    response = compare(service, token_a, token_b)

    assert [item.band for item in response.inputs] == ["SPHEREx-D3", "SPHEREx-D3"]
    assert response.inputs[0].bunit == "MJy / sr"
    assert response.inputs[0].shape == [64, 64]
    assert response.inputs[0].pixel_scale_arcsec == pytest.approx(6.15, abs=1e-6)
    assert response.inputs[0].fwhm_arcsec == pytest.approx(12.3, abs=0.3)
    assert response.inputs[0].source == "cutout"
    assert response.target.ra == pytest.approx(TARGET_RA)


def test_inspection_through_the_service_matches_the_file(settings, fake_client) -> None:
    service, token_a, _ = harness(settings, fake_client, epoch_a(), epoch_b())

    inspection = service.inspect(token_a, TARGET_RA, TARGET_DEC, 0.2)

    assert inspection.source == "cutout"
    assert inspection.wcs.target_inside_image is True
    assert inspection.psf.available is True
    assert len(inspection.fits_sha256) == 64
