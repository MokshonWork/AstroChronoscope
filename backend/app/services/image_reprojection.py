"""Common celestial grid reprojection and residual alignment measurement.

The reference image defines the output grid (its own WCS and shape); the other
image is resampled onto it. Resampling uses interpolation of *surface
brightness* (MJy/sr), which is what the SPHEREx level-2 pixels store, so no
pixel-area scaling is required. It is deliberately not flux-conserving for
point-source photometry; see ``ReprojectionInfo.flux_conserving``.

Invalid pixels are propagated as a weight image: an output pixel is valid only
if every source pixel contributing to it is valid. Pixels outside the source
footprint stay NaN, never zero.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from astropy.wcs import WCS
from scipy.ndimage import map_coordinates

from app.core.exceptions import ReprojectionError

log = logging.getLogger(__name__)

#: Minimum phase-correlation peak-to-noise ratio accepted as a real alignment.
_MIN_PEAK_CONFIDENCE = 5.0


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
    """Resample one image onto ``grid``, preserving its invalid-pixel mask."""
    ny, nx = grid.shape
    if same_grid:
        data_out = np.array(data, dtype=np.float64, copy=True)
        valid_out = np.array(valid, dtype=bool, copy=True)
        data_out[~valid_out] = np.nan
        variance_out = None
        if variance is not None:
            variance_out = np.array(variance, dtype=np.float64, copy=True)
            variance_out[~valid_out] = np.nan
        return Reprojected(
            data=data_out,
            valid=valid_out,
            variance=variance_out,
            valid_fraction=float(valid_out.mean()) if valid_out.size else 0.0,
        )

    order = 0 if method == "nearest" else 1
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
    coords = np.vstack([source_y, source_x])
    threshold = 0.5 if order == 0 else 0.999

    filled = np.where(valid, data, 0.0)
    weights = valid.astype(np.float64)
    try:
        values = map_coordinates(filled, coords, order=order, mode="nearest")
        weight = map_coordinates(weights, coords, order=order, mode="nearest")
    except Exception as exc:
        raise ReprojectionError("Interpolation failed during reprojection", details={"error": str(exc)}) from exc

    valid_out = (inside & (weight >= threshold)).reshape(ny, nx)
    data_out = values.reshape(ny, nx)
    data_out[~valid_out] = np.nan

    variance_out = None
    if variance is not None:
        variance_filled = np.where(valid & np.isfinite(variance), variance, 0.0)
        variance_out = map_coordinates(variance_filled, coords, order=order, mode="nearest").reshape(ny, nx)
        variance_out[~valid_out] = np.nan

    return Reprojected(
        data=data_out,
        valid=valid_out,
        variance=variance_out,
        valid_fraction=float(valid_out.mean()) if valid_out.size else 0.0,
    )


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
