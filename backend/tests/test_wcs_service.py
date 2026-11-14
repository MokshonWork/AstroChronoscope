"""Celestial WCS conversion and cutout membership."""

from __future__ import annotations

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from app.services.wcs_service import celestial_wcs, validate_target_in_cutout
from tests.conftest import TARGET_DEC, TARGET_RA


def make_wcs(target_x: float = 28.5, target_y: float = 29.0, ra: float = TARGET_RA, dec: float = TARGET_DEC) -> WCS:
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [target_x + 1.0, target_y + 1.0]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.cdelt = [-6.15 / 3600.0, 6.15 / 3600.0]
    return wcs


def test_target_inside_cutout() -> None:
    result = validate_target_in_cutout(TARGET_RA, TARGET_DEC, make_wcs(), (59, 58))
    assert result.available is True
    assert result.target_inside_image is True
    assert result.pixel_x == pytest.approx(28.5, abs=1e-3)
    assert result.pixel_y == pytest.approx(29.0, abs=1e-3)


def test_target_outside_cutout() -> None:
    result = validate_target_in_cutout(TARGET_RA, TARGET_DEC, make_wcs(ra=TARGET_RA + 0.2), (59, 58))
    assert result.available is True
    assert result.target_inside_image is False
    assert result.pixel_x is not None and np.isfinite(result.pixel_x)
    assert "outside" in (result.message or "").lower()


def test_missing_wcs_is_reported_not_fabricated() -> None:
    result = validate_target_in_cutout(TARGET_RA, TARGET_DEC, None, (59, 58))
    assert result.available is False
    assert result.target_inside_image is None
    assert result.pixel_x is None and result.pixel_y is None


def test_non_celestial_header_has_no_wcs() -> None:
    header = fits.Header()
    header["CTYPE1"] = "WAVE"
    header["CTYPE2"] = "WAVE"
    assert celestial_wcs(header) is None


def test_one_dimensional_shape_is_handled() -> None:
    result = validate_target_in_cutout(TARGET_RA, TARGET_DEC, make_wcs(), (58,))
    assert result.available is True
    assert result.target_inside_image is None


def test_malformed_wcs_returns_none() -> None:
    header = fits.Header()
    header["CTYPE1"] = "RA---XXX"
    header["CTYPE2"] = "DEC--TAN"
    header["CRPIX1"] = 1.0
    header["CRPIX2"] = 1.0
    header["CRVAL1"] = 0.0
    header["CRVAL2"] = 0.0
    header["CDELT1"] = 1.0
    header["CDELT2"] = 1.0
    assert celestial_wcs(header) is None
