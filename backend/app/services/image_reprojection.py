"""Common celestial grid reprojection and residual alignment measurement.

The reference image defines the output grid (its own WCS and shape); the other
image is resampled onto it. Resampling uses interpolation of *surface
brightness* (MJy/sr), which is what the SPHEREx level-2 pixels store, so no
pixel-area scaling is required. It is deliberately not flux-conserving for
point-source photometry; see ``ReprojectionInfo.flux_conserving``.

Interpolation kernel
--------------------
Science pixels are resampled with bilinear interpolation (or nearest neighbour
when ``method="nearest"``). The taps and weights are computed here, not by an
opaque library call, because the variance map must use exactly the same weights
as the science image. ``_taps`` reproduces ``scipy.ndimage.map_coordinates``
with ``order=1, mode="nearest"``; a regression test asserts that equivalence.

Variance propagation
--------------------
A variance map is *not* an image and must never be interpolated like one. For
independent input pixels and interpolation weights ``w_i``::

    V_out = sum_i w_i^2 * V_i,      sum_i w_i = 1

``w_i`` are the effective weights after boundary clamping, so the weights always
sum to one and no renormalisation is needed. Interpolating variance directly
would give ``sum_i w_i * V_i``, which overstates the variance of any averaged
pixel (for two equal-weight taps it would report ``V`` instead of ``V/2``).

Assumptions and limits
----------------------
* Input pixel noise is independent between pixels. Correlated input noise is
  not modelled.
* Interpolation correlates *neighbouring output* pixels even when the input is
  white. Only the per-pixel variance is propagated; the full output covariance
  matrix is not. Do not describe this as covariance propagation.
* A source pixel with non-finite or negative variance cannot contribute a
  physical variance, so output pixels that depend on it are NaN even when the
  science value itself is valid.
* An output pixel is valid only if every source pixel with a non-zero weight is
  valid; otherwise it is NaN. Pixels outside the source footprint stay NaN,
  never zero.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from astropy.wcs import WCS

from app.core.exceptions import ReprojectionError

log = logging.getLogger(__name__)

#: Minimum phase-correlation peak-to-noise ratio accepted as a real alignment.
_MIN_PEAK_CONFIDENCE = 5.0

#: Fraction of the total interpolation weight that must come from valid pixels.
_VALID_WEIGHT_THRESHOLD = {"bilinear": 0.999, "nearest": 0.5}


@dataclass(frozen=True)
class CommonGrid:
    """Output pixel grid, taken from the reference image."""

    wcs: WCS
    shape: tuple[int, int]
    pixel_scale_arcsec: float | None


@dataclass
class Reprojected:
    data: np.ndarray
    valid: np.ndarray
    variance: np.ndarray | None
    valid_fraction: float
    #: Fraction of the valid output pixels that have a finite propagated variance.
    variance_valid_fraction: float | None = None


@dataclass(frozen=True)
class ShiftMeasurement:
    dx_px: float
    dy_px: float
    confidence: float
    verified: bool


def reproject(
    data: np.ndarray,
    valid: np.ndarray,
    variance: np.ndarray | None,
    source_wcs: WCS,
    grid: CommonGrid,
    method: str = "bilinear",
    *,
    same_grid: bool = False,
) -> Reprojected:
    """Resample one image onto ``grid``, preserving its invalid-pixel mask.

    ``variance`` is the per-pixel variance (unit^2) of ``data``. It is propagated
    with squared interpolation weights, never interpolated as a scalar image.
    """
    ny, nx = grid.shape
    if same_grid:
        data_out = np.array(data, dtype=np.float64, copy=True)
        valid_out = np.array(valid, dtype=bool, copy=True)
        data_out[~valid_out] = np.nan
        variance_out = None
        variance_valid_fraction = None
        if variance is not None:
            variance_out = np.array(variance, dtype=np.float64, copy=True)
            variance_out[~valid_out] = np.nan
            variance_out[~(np.isfinite(variance_out) & (variance_out >= 0.0))] = np.nan
            valid_count = int(valid_out.sum())
            variance_valid_fraction = (
                float(np.isfinite(variance_out).sum() / valid_count) if valid_count else 0.0
            )
        return Reprojected(
            data=data_out,
            valid=valid_out,
            variance=variance_out,
            valid_fraction=float(valid_out.mean()) if valid_out.size else 0.0,
            variance_valid_fraction=variance_valid_fraction,
        )

    try:
        yy, xx = np.mgrid[0:ny, 0:nx]
        world = grid.wcs.all_pix2world(xx.ravel().astype(float), yy.ravel().astype(float), 0)
        source_x, source_y = source_wcs.all_world2pix(world[0], world[1], 0)
    except Exception as exc:
        raise ReprojectionError("WCS transformation failed during reprojection", details={"error": str(exc)}) from exc

    source_ny, source_nx = data.shape
    inside = (
        np.isfinite(source_x)
        & np.isfinite(source_y)
        & (source_x >= -0.5)
        & (source_x <= source_nx - 0.5)
        & (source_y >= -0.5)
        & (source_y <= source_ny - 0.5)
    )
    order = 0 if method == "nearest" else 1
    threshold = _VALID_WEIGHT_THRESHOLD["nearest" if order == 0 else "bilinear"]

    taps = _taps(source_x, source_y, data.shape, order)
    filled = np.where(valid, data, 0.0)
    weights = valid.astype(np.float64)
    try:
        values = _sample(filled, taps)
        weight = _sample(weights, taps)
    except Exception as exc:
        raise ReprojectionError("Interpolation failed during reprojection", details={"error": str(exc)}) from exc

    valid_out = (inside & (weight >= threshold)).reshape(ny, nx)
    data_out = values.reshape(ny, nx)
    data_out[~valid_out] = np.nan

    variance_out = None
    variance_valid_fraction = None
    if variance is not None:
        usable = valid & np.isfinite(variance) & (variance >= 0.0)
        propagated, supported = _propagate_variance(np.asarray(variance, dtype=np.float64), usable, taps)
        variance_out = np.where((valid_out & supported.reshape(ny, nx)), propagated.reshape(ny, nx), np.nan)
        valid_count = int(valid_out.sum())
        variance_valid_fraction = (
            float(np.isfinite(variance_out).sum() / valid_count) if valid_count else 0.0
        )

    return Reprojected(
        data=data_out,
        valid=valid_out,
        variance=variance_out,
        valid_fraction=float(valid_out.mean()) if valid_out.size else 0.0,
        variance_valid_fraction=variance_valid_fraction,
    )


# ------------------------------------------------------------------ interpolation
def _taps(
    x: np.ndarray, y: np.ndarray, shape: tuple[int, int], order: int
) -> tuple[tuple[np.ndarray, np.ndarray, np.ndarray], ...]:
    """Effective (row, column, weight) taps for ``order`` 0/1, mode="nearest".

    Coordinates are clamped to the array, which is exactly what
    ``scipy.ndimage.map_coordinates(..., mode="nearest")`` does for these two
    orders. After clamping, duplicated taps always carry zero weight, so the
    weights sum to one and squared-weight propagation cannot double count.
    """
    ny, nx = shape
    xc = np.clip(x, 0.0, nx - 1.0)
    yc = np.clip(y, 0.0, ny - 1.0)
    if order == 0:
        return ((np.floor(yc + 0.5).astype(np.intp), np.floor(xc + 0.5).astype(np.intp), np.ones_like(xc)),)

    x0 = np.floor(xc).astype(np.intp)
    y0 = np.floor(yc).astype(np.intp)
    x1 = np.minimum(x0 + 1, nx - 1)
    y1 = np.minimum(y0 + 1, ny - 1)
    fx = xc - x0
    fy = yc - y0
    return (
        (y0, x0, (1.0 - fx) * (1.0 - fy)),
        (y0, x1, fx * (1.0 - fy)),
        (y1, x0, (1.0 - fx) * fy),
        (y1, x1, fx * fy),
    )


def _sample(values: np.ndarray, taps: tuple[tuple[np.ndarray, np.ndarray, np.ndarray], ...]) -> np.ndarray:
    total = np.zeros(taps[0][2].shape, dtype=np.float64)
    for rows, columns, weights in taps:
        total += weights * values[rows, columns]
    return total


def _propagate_variance(
    variance: np.ndarray,
    usable: np.ndarray,
    taps: tuple[tuple[np.ndarray, np.ndarray, np.ndarray], ...],
) -> tuple[np.ndarray, np.ndarray]:
    """``V_out = sum_i w_i^2 V_i`` and whether every contributing tap was usable."""
    total = np.zeros(taps[0][2].shape, dtype=np.float64)
    supported = np.ones(taps[0][2].shape, dtype=bool)
    for rows, columns, weights in taps:
        contributes = weights > 0.0
        ok = contributes & usable[rows, columns]
        contribution = np.where(ok, variance[rows, columns], 0.0)
        total += np.where(ok, weights * weights * contribution, 0.0)
        supported &= ~contributes | ok
    return total, supported


def measure_residual_shift(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> ShiftMeasurement:
    """Phase correlation of two same-grid images: residual shift of B relative to A.

    The shift is measured, never applied. A low peak-to-noise ratio means the
    images share too little structure for an independent alignment check.
    """
    ny, nx = a.shape
    if mask.sum() < 64 or ny < 8 or nx < 8:
        return ShiftMeasurement(dx_px=0.0, dy_px=0.0, confidence=0.0, verified=False)

    window = np.outer(np.hanning(ny), np.hanning(nx))
    first = _prepared(a, mask, window)
    second = _prepared(b, mask, window)
    if first is None or second is None:
        return ShiftMeasurement(dx_px=0.0, dy_px=0.0, confidence=0.0, verified=False)

    cross = np.fft.rfft2(first) * np.conj(np.fft.rfft2(second))
    magnitude = np.abs(cross)
    magnitude[magnitude == 0] = 1.0
    correlation = np.fft.irfft2(cross / magnitude, s=(ny, nx))

    peak_index = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
    confidence = float(correlation.max() / (correlation.std() + 1e-12))
    dy = _signed_offset(peak_index[0], ny)
    dx = _signed_offset(peak_index[1], nx)
    dy += _parabolic(correlation, peak_index, axis=0)
    dx += _parabolic(correlation, peak_index, axis=1)

    return ShiftMeasurement(
        dx_px=float(dx),
        dy_px=float(dy),
        confidence=confidence,
        verified=confidence >= _MIN_PEAK_CONFIDENCE,
    )


def _prepared(image: np.ndarray, mask: np.ndarray, window: np.ndarray) -> np.ndarray | None:
    values = image[mask]
    if values.size < 64:
        return None
    median = float(np.median(values))
    scatter = 1.4826 * float(np.median(np.abs(values - median)))
    if not math.isfinite(scatter) or scatter <= 0:
        return None
    normalized = np.where(mask, (image - median) / scatter, 0.0)
    return normalized * window


def _signed_offset(index: int, size: int) -> int:
    return index if index <= size // 2 else index - size


def _parabolic(correlation: np.ndarray, peak: tuple[int, int], axis: int) -> float:
    size = correlation.shape[axis]
    centre = correlation[peak]
    before = list(peak)
    after = list(peak)
    before[axis] = (peak[axis] - 1) % size
    after[axis] = (peak[axis] + 1) % size
    low = float(correlation[tuple(before)])
    high = float(correlation[tuple(after)])
    denominator = low - 2.0 * centre + high
    if denominator == 0:
        return 0.0
    return float(np.clip(0.5 * (low - high) / denominator, -0.5, 0.5))
