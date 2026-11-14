"""Deterministic variance-propagation tests for common-grid reprojection.

The expected values here are derived analytically from the bilinear weights,
never read back from the implementation. For a pure pixel translation
``(dx, dy)`` the four taps of output pixel ``(y, x)`` are

    w00 = (1-dx)(1-dy)   source (y,   x)
    w01 = dx(1-dy)       source (y,   x+1)
    w10 = (1-dx)dy       source (y+1, x)
    w11 = dx*dy          source (y+1, x+1)

and independent input noise propagates as ``V_out = sum_i w_i^2 V_i``.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.ndimage import map_coordinates

from app.services.image_inspection import pixel_scale_arcsec
from app.services.image_reprojection import CommonGrid, reproject
from tests.conftest import make_sky_wcs

SHAPE = (8, 8)


def shifted_wcs(dx: float, dy: float):
    """Reference grid plus a source WCS whose pixels are offset by (dx, dy)."""
    reference = make_sky_wcs(nx=SHAPE[1], ny=SHAPE[0], crpix=(4.5, 4.5))
    source = make_sky_wcs(nx=SHAPE[1], ny=SHAPE[0], crpix=(4.5 + dx, 4.5 + dy))
    return reference, source


def grid(wcs) -> CommonGrid:
    return CommonGrid(wcs=wcs, shape=SHAPE, pixel_scale_arcsec=pixel_scale_arcsec(wcs))


def bilinear_weights(dx: float, dy: float) -> tuple[float, float, float, float]:
    return ((1 - dx) * (1 - dy), dx * (1 - dy), (1 - dx) * dy, dx * dy)


def test_constant_variance_is_scaled_by_the_squared_weight_sum() -> None:
    """V_out = V * sum(w_i^2) = 4.0 * 0.3125 for a (0.25, 0.5) shift."""
    reference, source = shifted_wcs(0.25, 0.5)
    data = np.ones(SHAPE)
    variance = np.full(SHAPE, 4.0)
    valid = np.ones(SHAPE, dtype=bool)

    result = reproject(data, valid, variance, source, grid(reference))

    weights = bilinear_weights(0.25, 0.5)
    expected = 4.0 * sum(w * w for w in weights)
    assert expected == pytest.approx(1.25)  # 0.375^2 + 0.125^2 + 0.375^2 + 0.125^2
    assert result.variance[3, 3] == pytest.approx(expected, rel=1e-6)
    assert result.variance_valid_fraction == pytest.approx(1.0)


def test_variance_is_not_interpolated_like_a_scalar_image() -> None:
    """Scalar interpolation would report 4.0; squared-weight propagation reports 1.25."""
    reference, source = shifted_wcs(0.25, 0.5)
    variance = np.full(SHAPE, 4.0)

    result = reproject(np.ones(SHAPE), np.ones(SHAPE, dtype=bool), variance, source, grid(reference))

    scalar_interpolation = 4.0
    assert result.variance[3, 3] != pytest.approx(scalar_interpolation)
    assert result.variance[3, 3] < scalar_interpolation


def test_integer_coordinates_preserve_variance_exactly() -> None:
    reference, source = shifted_wcs(0.0, 0.0)
    variance = np.arange(SHAPE[0] * SHAPE[1], dtype=float).reshape(SHAPE) + 1.0

    result = reproject(np.ones(SHAPE), np.ones(SHAPE, dtype=bool), variance, source, grid(reference))

    interior = np.zeros(SHAPE, dtype=bool)
    interior[1:-1, 1:-1] = True
    np.testing.assert_allclose(result.variance[interior], variance[interior], rtol=1e-9, atol=0)


def test_nonuniform_variance_uses_each_tap_weight_squared() -> None:
    reference, source = shifted_wcs(0.25, 0.5)
    yy, xx = np.mgrid[0:SHAPE[0], 0:SHAPE[1]]
    variance = 1.0 + 0.3 * yy + 0.7 * xx
    valid = np.ones(SHAPE, dtype=bool)

    result = reproject(np.ones(SHAPE), valid, variance, source, grid(reference))

    w00, w01, w10, w11 = bilinear_weights(0.25, 0.5)
    y, x = 3, 3
    expected = (
        w00 ** 2 * variance[y, x]
        + w01 ** 2 * variance[y, x + 1]
        + w10 ** 2 * variance[y + 1, x]
        + w11 ** 2 * variance[y + 1, x + 1]
    )
    assert result.variance[y, x] == pytest.approx(expected, rel=1e-6)
    # Sanity: the linear interpolation of the same map is a different number.
    linear = (
        w00 * variance[y, x]
        + w01 * variance[y, x + 1]
        + w10 * variance[y + 1, x]
        + w11 * variance[y + 1, x + 1]
    )
    assert result.variance[y, x] != pytest.approx(linear)


def test_science_image_interpolation_matches_scipy_bilinear() -> None:
    """The taps used for variance are the ones used for the science image."""
    reference, source = shifted_wcs(0.37, 0.63)
    data = np.random.default_rng(11).normal(1e-3, 2e-4, SHAPE)
    valid = np.ones(SHAPE, dtype=bool)

    result = reproject(data, valid, None, source, grid(reference))

    yy, xx = np.mgrid[0:SHAPE[0], 0:SHAPE[1]]
    world = reference.all_pix2world(xx.ravel().astype(float), yy.ravel().astype(float), 0)
    source_x, source_y = source.all_world2pix(world[0], world[1], 0)
    expected = map_coordinates(data, np.vstack([source_y, source_x]), order=1, mode="nearest").reshape(SHAPE)

    np.testing.assert_allclose(result.data[result.valid], expected[result.valid], rtol=0, atol=1e-9)


def test_masked_neighbour_invalidates_the_output_and_its_variance() -> None:
    reference, source = shifted_wcs(0.25, 0.5)
    valid = np.ones(SHAPE, dtype=bool)
    valid[4, 4] = False  # tap for output pixels (3, 3), (3, 4), (4, 3), (4, 4)

    result = reproject(np.ones(SHAPE), valid, np.full(SHAPE, 4.0), source, grid(reference))

    for y, x in ((3, 3), (3, 4), (4, 3), (4, 4)):
        assert not result.valid[y, x]
        assert np.isnan(result.data[y, x])
        assert np.isnan(result.variance[y, x])
    # A pixel whose taps are all valid keeps a finite propagated variance.
    assert result.valid[1, 1]
    assert np.isfinite(result.variance[1, 1])


def test_nan_science_pixels_do_not_leak_into_valid_output() -> None:
    reference, source = shifted_wcs(0.25, 0.5)
    data = np.ones(SHAPE)
    data[4, 4] = np.nan
    valid = np.isfinite(data)

    result = reproject(data, valid, np.full(SHAPE, 4.0), source, grid(reference))

    assert not result.valid[3, 3]
    assert np.isnan(result.data[3, 3])
    assert np.isfinite(result.data[result.valid]).all()


def test_nan_variance_keeps_the_science_value_but_invalidates_the_variance() -> None:
    reference, source = shifted_wcs(0.25, 0.5)
    variance = np.full(SHAPE, 4.0)
    variance[4, 4] = np.nan
    valid = np.ones(SHAPE, dtype=bool)

    result = reproject(np.ones(SHAPE), valid, variance, source, grid(reference))

    assert result.valid[3, 3]
    assert np.isfinite(result.data[3, 3])
    assert np.isnan(result.variance[3, 3])
    assert result.variance_valid_fraction < 1.0


def test_negative_variance_is_rejected_as_unphysical() -> None:
    reference, source = shifted_wcs(0.25, 0.5)
    variance = np.full(SHAPE, 4.0)
    variance[4, 4] = -1.0

    result = reproject(np.ones(SHAPE), np.ones(SHAPE, dtype=bool), variance, source, grid(reference))

    assert np.isnan(result.variance[3, 3])
    assert result.valid[3, 3]  # the science value itself is still usable


def test_boundary_pixels_use_clamped_weights_that_still_sum_to_one() -> None:
    """At the last column the x taps collapse onto one pixel; y still interpolates."""
    reference, source = shifted_wcs(0.45, 0.45)
    variance = np.tile(np.arange(SHAPE[1], dtype=float) + 1.0, (SHAPE[0], 1))

    result = reproject(np.ones(SHAPE), np.ones(SHAPE, dtype=bool), variance, source, grid(reference))

    last = SHAPE[1] - 1
    assert result.valid[3, last]
    # source_x = 7.45 is clamped to column 7 (total weight 1 there); source_y =
    # 3.45 interpolates rows 3 and 4, so V_out = (0.55^2 + 0.45^2) * V[3, 7].
    expected = ((1 - 0.45) ** 2 + 0.45 ** 2) * variance[3, last]
    assert result.variance[3, last] == pytest.approx(expected, rel=1e-6)
    # If the x taps were not collapsed, the same variance would be reported twice.
    assert result.variance[3, last] != pytest.approx(2 * expected)


def test_no_valid_support_produces_nan_not_zero() -> None:
    reference, source = shifted_wcs(0.25, 0.5)
    valid = np.zeros(SHAPE, dtype=bool)

    result = reproject(np.ones(SHAPE), valid, np.full(SHAPE, 4.0), source, grid(reference))

    assert not result.valid.any()
    assert np.isnan(result.data).all()
    assert np.isnan(result.variance).all()
    assert result.variance_valid_fraction == 0.0


def test_science_and_variance_outputs_are_aligned() -> None:
    reference, source = shifted_wcs(0.25, 0.5)
    data = np.random.default_rng(3).normal(0.0, 1.0, SHAPE)
    variance = np.random.default_rng(4).uniform(1.0, 5.0, SHAPE)

    result = reproject(data, np.ones(SHAPE, dtype=bool), variance, source, grid(reference))

    assert result.variance.shape == result.data.shape == result.valid.shape == SHAPE
    np.testing.assert_array_equal(np.isfinite(result.variance), result.valid)


def test_nearest_neighbour_copies_variance_without_averaging() -> None:
    reference, source = shifted_wcs(0.25, 0.6)
    variance = np.arange(SHAPE[0] * SHAPE[1], dtype=float).reshape(SHAPE) + 1.0

    result = reproject(np.ones(SHAPE), np.ones(SHAPE, dtype=bool), variance, source, grid(reference), "nearest")

    # Source coordinate (3.25, 3.6) rounds to pixel (4, 3) via floor(x + 0.5).
    assert result.variance[3, 3] == pytest.approx(variance[4, 3], rel=1e-6)


def test_same_grid_variance_is_copied_not_interpolated() -> None:
    reference, _ = shifted_wcs(0.0, 0.0)
    variance = np.arange(SHAPE[0] * SHAPE[1], dtype=float).reshape(SHAPE) + 1.0
    valid = np.ones(SHAPE, dtype=bool)
    valid[0, 0] = False

    result = reproject(np.ones(SHAPE), valid, variance, reference, grid(reference), same_grid=True)

    np.testing.assert_allclose(result.variance[valid], variance[valid], rtol=0, atol=0)
    assert np.isnan(result.variance[0, 0])
