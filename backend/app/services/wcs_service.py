"""Celestial WCS validation for returned cutouts."""

from __future__ import annotations

import logging
import math

from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS

from app.schemas.cutout import WcsInfo

log = logging.getLogger(__name__)


def celestial_wcs(header: fits.Header) -> WCS | None:
    """Return a WCS only when it has celestial axes; never guess one."""
    try:
        wcs = WCS(header)
    except Exception as exc:  # malformed WCS cards
        log.warning("wcs_parse_failed", extra={"error": str(exc)})
        return None
    if not wcs.has_celestial:
        return None
    return wcs


def validate_target_in_cutout(ra: float, dec: float, wcs: WCS | None, image_shape: tuple[int, ...]) -> WcsInfo:
    """Map RA/Dec to pixels and report whether the target is inside the image."""
    if wcs is None:
        return WcsInfo(available=False, message="FITS header has no celestial WCS")

    if len(image_shape) < 2:
        return WcsInfo(available=True, target_inside_image=None, message="Image has no 2-D pixel grid")

    ny, nx = int(image_shape[-2]), int(image_shape[-1])
    try:
        pixel_x, pixel_y = wcs.world_to_pixel(SkyCoord(ra=ra, dec=dec, unit="deg", frame="icrs"))
    except Exception as exc:
        log.warning("wcs_transform_failed", extra={"error": str(exc), "ra": ra, "dec": dec})
        return WcsInfo(available=True, target_inside_image=None, message="WCS could not transform the target coordinates")

    x, y = float(pixel_x), float(pixel_y)
    if not (math.isfinite(x) and math.isfinite(y)):
        return WcsInfo(
            available=True,
            target_inside_image=None,
            message="WCS transform produced non-finite pixel coordinates",
        )

    # FITS pixel centres are at 0..N-1; the image footprint extends half a pixel beyond.
    inside = (-0.5 <= x <= nx - 0.5) and (-0.5 <= y <= ny - 0.5)
    return WcsInfo(
        available=True,
        target_inside_image=bool(inside),
        pixel_x=round(x, 4),
        pixel_y=round(y, 4),
        message=None if inside else "Target falls outside the returned cutout",
    )
