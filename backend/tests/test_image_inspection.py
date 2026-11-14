"""Deterministic FITS inspection tests (synthetic SPHEREx-shaped fixtures)."""

from __future__ import annotations

import numpy as np
import pytest
from astropy.io import fits

from app.core.exceptions import InvalidFitsFileError, MissingImageExtensionError
from app.services.image_inspection import file_sha256, inspect_fits
from tests.conftest import TARGET_DEC, TARGET_RA, make_sky_fits_bytes


def write(tmp_path, payload: bytes, name: str = "image.fits"):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def inspect(tmp_path, payload: bytes, **kwargs):
    path = write(tmp_path, payload)
    return inspect_fits(path, target_ra=TARGET_RA, target_dec=TARGET_DEC, **kwargs)


def test_inspection_reports_file_metadata(tmp_path) -> None:
    inspection = inspect(
        tmp_path,
        make_sky_fits_bytes(noise=1e-6, with_psf=True, psf_fwhm_px=2.0,
                            mask_bits={"TRANSIENT": "Transient detected during SUR"}),
    )

    assert inspection.quality == "PASS"
    assert inspection.warnings == []
    assert inspection.stats.shape == [64, 64]
    assert inspection.stats.bunit == "MJy / sr"
    assert inspection.stats.valid_fraction == pytest.approx(1.0)
    assert inspection.science_extname == "IMAGE"
    assert [hdu.name for hdu in inspection.hdus] == ["PRIMARY", "ZODI", "IMAGE", "FLAGS", "VARIANCE", "EPSF"]
    assert {hdu.kind for hdu in inspection.hdus} == {"primary", "image", "table"}
    assert inspection.metadata.telescope == "SPHEREx"
    assert inspection.metadata.exposure_time_seconds == pytest.approx(113.58)


def test_inspection_validates_wcs(tmp_path) -> None:
    inspection = inspect(tmp_path, make_sky_fits_bytes())

    assert inspection.wcs.available is True
    assert inspection.wcs.projection == "TAN"
    assert inspection.wcs.ctype == ["RA---TAN", "DEC--TAN"]
    assert inspection.wcs.pixel_scale_arcsec == pytest.approx(6.15, abs=1e-6)
    assert inspection.wcs.crpix == [32.5, 32.5]
    assert inspection.wcs.target_inside_image is True
    assert len(inspection.wcs.footprint) == 4
    assert inspection.wcs.center_ra_deg == pytest.approx(TARGET_RA, abs=1e-6)


def test_missing_wcs_is_reported_not_guessed(tmp_path) -> None:
    inspection = inspect(tmp_path, make_sky_fits_bytes(with_wcs=False))

    assert inspection.wcs.available is False
    assert inspection.quality == "WARNING"
    assert "missing_wcs" in {warning.code for warning in inspection.warnings}


def test_target_outside_footprint_warns(tmp_path) -> None:
    inspection = inspect(tmp_path, make_sky_fits_bytes(crval=(TARGET_RA + 0.5, TARGET_DEC)))

    assert inspection.wcs.target_inside_image is False
    assert "target_outside_image" in {warning.code for warning in inspection.warnings}


def test_nan_and_infinite_pixels_are_counted(tmp_path) -> None:
    inspection = inspect(tmp_path, make_sky_fits_bytes(nan_border=True))

    assert inspection.stats.nan_pixels == 64
    assert inspection.stats.inf_pixels == 1
    assert inspection.stats.finite_pixels == 64 * 64 - 65
    assert "non_finite_pixels" in {warning.code for warning in inspection.warnings}


def test_all_invalid_image_fails_quality(tmp_path) -> None:
    inspection = inspect(tmp_path, make_sky_fits_bytes(fill_value=float("nan")))

    assert inspection.stats.finite_pixels == 0
    assert inspection.quality == "FAIL"
    assert "all_pixels_invalid" in {warning.code for warning in inspection.warnings}


def test_flags_mask_invalidates_pixels(tmp_path) -> None:
    flags = np.zeros((64, 64), dtype=np.int32)
    flags[5:10, 5:10] = 2
    inspection = inspect(tmp_path, make_sky_fits_bytes(flags=flags, mask_bits={"OVERFLOW": "Overflow detected"}))

    assert inspection.mask.available is True
    assert inspection.mask.invalid_pixels == 25
    assert inspection.mask.invalid_fraction == pytest.approx(25 / 4096)
    assert inspection.mask.bits == {"OVERFLOW": "Overflow detected"}
    assert inspection.stats.valid_pixels == 4096 - 25


def test_informational_flags_do_not_invalidate_pixels(tmp_path) -> None:
    """SOURCE marks a known source, not bad data: masking it would delete the science."""
    flags = np.zeros((64, 64), dtype=np.int32)
    flags[:, :] = 1  # the SOURCE bit in this fixture
    inspection = inspect(tmp_path, make_sky_fits_bytes(flags=flags, mask_bits={"SOURCE": "Pixel mapped to a known source"}))

    assert inspection.mask.invalid_pixels == 0
    assert inspection.stats.valid_pixels == inspection.stats.finite_pixels == 4096
    assert "SOURCE" in inspection.mask.policy


def test_missing_flags_and_variance_are_explicit(tmp_path) -> None:
    inspection = inspect(tmp_path, make_sky_fits_bytes(with_flags=False, with_variance=False))
    codes = {warning.code for warning in inspection.warnings}

    assert inspection.mask.available is False
    assert inspection.variance.available is False
    assert {"no_flags_extension", "no_variance_extension"} <= codes


def test_variance_unit_and_range(tmp_path) -> None:
    inspection = inspect(tmp_path, make_sky_fits_bytes(variance=2.5e-8))

    assert inspection.variance.available is True
    assert inspection.variance.unit == "MJy2 / sr2"
    assert inspection.variance.min == pytest.approx(2.5e-8, rel=1e-6)
    assert inspection.variance.finite_pixels == 4096


def test_psf_fwhm_is_converted_using_the_real_oversampling(tmp_path) -> None:
    inspection = inspect(tmp_path, make_sky_fits_bytes(with_psf=True, psf_fwhm_px=2.0, oversampling=5))

    assert inspection.psf.available is True
    assert inspection.psf.oversampling == 5
    assert inspection.psf.fwhm_px == pytest.approx(2.0, abs=0.05)
    assert inspection.psf.fwhm_arcsec == pytest.approx(12.3, abs=0.3)


def test_psf_without_oversampling_is_not_converted(tmp_path) -> None:
    payload = make_sky_fits_bytes(with_psf=True)
    with fits.open(write(tmp_path, payload)) as hdul:
        del hdul[-1].header["OVSMPX"]
        del hdul[-1].header["OVSMPY"]
        path = tmp_path / "no_oversampling.fits"
        hdul.writeto(path)

    inspection = inspect_fits(path, target_ra=TARGET_RA, target_dec=TARGET_DEC)
    assert inspection.psf.available is True
    assert inspection.psf.fwhm_px is None
    assert "psf_scale_unknown" in {warning.code for warning in inspection.warnings}


def test_corrupt_file_is_rejected(tmp_path) -> None:
    path = write(tmp_path, b"not a fits file at all")
    with pytest.raises(InvalidFitsFileError):
        inspect_fits(path)


def test_missing_image_hdu_is_rejected(tmp_path) -> None:
    hdul = fits.HDUList([fits.PrimaryHDU()])
    path = tmp_path / "no_image.fits"
    hdul.writeto(path)

    with pytest.raises(MissingImageExtensionError):
        inspect_fits(path)


def test_checksum_is_content_addressed(tmp_path) -> None:
    payload = make_sky_fits_bytes()
    first = write(tmp_path, payload, "a.fits")
    second = write(tmp_path, payload, "b.fits")

    assert file_sha256(first) == file_sha256(second)
    assert len(file_sha256(first)) == 64
