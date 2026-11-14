"""Common-grid reprojection and residual-alignment tests."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.image_inspection import pixel_scale_arcsec
from app.services.image_reprojection import CommonGrid, measure_residual_shift, reproject
from tests.conftest import make_sky_wcs, render_sky

SHAPE = (64, 64)
SOURCES = (
    (0.0, 0.0, 1e-2, 12.0),
    (60.0, 40.0, 6e-3, 10.0),
    (-55.0, -35.0, 4e-3, 14.0),
)


def grid_for(wcs) -> CommonGrid:
    return CommonGrid(wcs=wcs, shape=SHAPE, pixel_scale_arcsec=pixel_scale_arcsec(wcs))


def test_same_grid_reference_is_copied_untouched() -> None:
    wcs = make_sky_wcs()
    data = render_sky(wcs, noise=1e-6)
    valid = np.ones(SHAPE, dtype=bool)
    valid[3, 4] = False

    result = reproject(data, valid, None, wcs, grid_for(wcs), same_grid=True)

    np.testing.assert_allclose(result.data[valid], data[valid])
    assert np.isnan(result.data[3, 4])
    assert result.valid_fraction == pytest.approx(valid.mean())


def test_identical_wcs_reprojection_is_identity() -> None:
    wcs = make_sky_wcs()
    data = render_sky(wcs, noise=1e-6)
    valid = np.ones(SHAPE, dtype=bool)

    result = reproject(data, valid, None, wcs, grid_for(wcs))

    interior = np.zeros(SHAPE, dtype=bool)
    interior[2:-2, 2:-2] = True
    np.testing.assert_allclose(result.data[interior], data[interior], atol=1e-12)
    assert result.valid.all()


def test_reprojection_recovers_the_same_sky_through_a_different_wcs() -> None:
    """A fixed sky rendered through two WCSs must agree after reprojection."""
    reference_wcs = make_sky_wcs(rotation_deg=0.0, crpix=(32.5, 32.5))
    rotated_wcs = make_sky_wcs(rotation_deg=25.0, crpix=(41.5, 25.5))
    reference = render_sky(reference_wcs, sources=SOURCES, noise=1e-6, seed=1)
    rotated = render_sky(rotated_wcs, sources=SOURCES, noise=1e-6, seed=2)

    result = reproject(rotated, np.ones(SHAPE, dtype=bool), None, rotated_wcs, grid_for(reference_wcs))

    common = result.valid & np.isfinite(reference)
    assert common.sum() > 0.4 * np.prod(SHAPE)
    residual = np.abs(result.data[common] - reference[common])
    peak = float(reference.max() - reference.min())
    # Bilinear interpolation of a ~2 px FWHM source leaves a few-percent peak error;
    # the naive (unreprojected) difference is ~100% of peak.
    assert residual.max() < 0.1 * peak
    assert np.median(residual) < 0.005 * peak


def test_different_wcs_is_not_comparable_without_reprojection() -> None:
    reference_wcs = make_sky_wcs(rotation_deg=0.0)
    rotated_wcs = make_sky_wcs(rotation_deg=25.0, crpix=(41.5, 25.5))
    reference = render_sky(reference_wcs, sources=SOURCES, noise=1e-6, seed=1)
    rotated = render_sky(rotated_wcs, sources=SOURCES, noise=1e-6, seed=2)

    naive = np.abs(rotated - reference).max()
    projected = reproject(rotated, np.ones(SHAPE, dtype=bool), None, rotated_wcs, grid_for(reference_wcs))
    common = projected.valid & np.isfinite(reference)
    after = np.abs(projected.data[common] - reference[common]).max()

    assert after < naive / 10.0


def test_invalid_source_pixels_do_not_leak_into_the_output() -> None:
    wcs = make_sky_wcs()
    data = render_sky(wcs, noise=1e-6)
    valid = np.ones(SHAPE, dtype=bool)
    valid[30:34, 30:34] = False

    result = reproject(data, valid, None, wcs, grid_for(wcs))

    # Bilinear interpolation touches the 8 neighbours of each invalid pixel, so
    # the hole is masked, never zero-filled.
    assert not result.valid[30:34, 30:34].any()
    assert np.isnan(result.data[30:34, 30:34]).all()
    assert not np.any(result.data[result.valid] == 0.0)


def test_pixels_outside_the_source_footprint_are_nan() -> None:
    reference_wcs = make_sky_wcs(crval=(156.5, -41.2))
    source_wcs = make_sky_wcs()
    data = render_sky(source_wcs, noise=1e-6)

    result = reproject(data, np.ones(SHAPE, dtype=bool), None, source_wcs, grid_for(reference_wcs))

    assert result.valid.sum() == 0
    assert np.isnan(result.data).all()


def test_nearest_neighbour_is_available_and_exact_on_the_same_grid() -> None:
    wcs = make_sky_wcs()
    data = render_sky(wcs, noise=1e-6)

    result = reproject(data, np.ones(SHAPE, dtype=bool), None, wcs, grid_for(wcs), "nearest")

    interior = np.zeros(SHAPE, dtype=bool)
    interior[1:-1, 1:-1] = True
    np.testing.assert_allclose(result.data[interior], data[interior])


def test_variance_is_resampled_with_the_same_mask() -> None:
    wcs = make_sky_wcs()
    data = render_sky(wcs, noise=1e-6)
    variance = np.full(SHAPE, 4e-8)
    valid = np.ones(SHAPE, dtype=bool)

    result = reproject(data, valid, variance, wcs, grid_for(wcs))

    assert result.variance is not None
    np.testing.assert_allclose(result.variance[valid], variance[valid])


def test_phase_correlation_measures_a_known_shift() -> None:
    wcs = make_sky_wcs()
    first = render_sky(wcs, sources=((0.0, 0.0, 1e-2, 12.0),), noise=1e-6, seed=1)
    shifted = render_sky(wcs, sources=((30.0, 18.0, 1e-2, 12.0),), noise=1e-6, seed=2)

    measurement = measure_residual_shift(first, shifted, np.ones(SHAPE, dtype=bool))

    assert measurement.verified is True
    assert measurement.dx_px == pytest.approx(30.0 / 6.15, abs=0.2)
    assert measurement.dy_px == pytest.approx(-18.0 / 6.15, abs=0.2)


def test_phase_correlation_refuses_to_verify_flat_images() -> None:
    flat = np.full(SHAPE, 1e-3)

    measurement = measure_residual_shift(flat, flat, np.ones(SHAPE, dtype=bool))

    assert measurement.verified is False
    assert measurement.confidence == 0.0
