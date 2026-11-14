"""IRSA IBE cutout requests with a hash-keyed disk cache.

IRSA exposes cutouts by appending ``center=RA,DEC&size=<deg>deg`` to the FITS
access_url returned by SIA. Verified live against spherex_qr3 level-2 products.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from app.core.config import Settings, get_settings
from app.core.validation import validate_coordinates, validate_cutout_size
from app.services.fits_service import download_fits, validate_fits_url, verify_fits

log = logging.getLogger(__name__)

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class CutoutArtifact:
    path: Path
    key: str
    preview_path: Path
    cache_hit: bool


class CutoutService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build_cutout_url(self, fits_url: str, ra: float, dec: float, size: float) -> str:
        validated = validate_fits_url(fits_url, self.settings)
        parts = urlsplit(validated)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["center"] = f"{ra:.8f},{dec:.8f}"
        query["size"] = f"{size:.6f}deg"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def cache_key(self, fits_url: str, ra: float, dec: float, size: float) -> str:
        raw = f"{fits_url}|{ra:.8f}|{dec:.8f}|{size:.6f}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def fetch(
        self,
        fits_url: str,
        ra: float,
        dec: float,
        size: float,
        *,
        session: requests.Session | None = None,
    ) -> CutoutArtifact:
        ra, dec = validate_coordinates(ra, dec)
        size = validate_cutout_size(size, self.settings.max_cutout_size_deg)
        self.settings.ensure_dirs()
        key = self.cache_key(fits_url, ra, dec, size)
        path = self.settings.fits_dir / f"{key}.fits"
        preview_path = self.settings.previews_dir / f"{key}.png"

        if path.is_file() and _looks_like_fits(path):
            return CutoutArtifact(path=path, key=key, preview_path=preview_path, cache_hit=True)

        with _lock_for(key):
            if path.is_file() and _looks_like_fits(path):
                return CutoutArtifact(path=path, key=key, preview_path=preview_path, cache_hit=True)

            cutout_url = self.build_cutout_url(fits_url, ra, dec, size)
            log.info("cutout_request", extra={"url": cutout_url})
            download_fits(cutout_url, path, session=session, settings=self.settings)
            # verify_fits already ran; a corrupt cached file is replaced next request.
            return CutoutArtifact(path=path, key=key, preview_path=preview_path, cache_hit=False)

    def verify_cached(self, path: Path) -> bool:
        try:
            verify_fits(path)
            return True
        except Exception:
            path.unlink(missing_ok=True)
            return False


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _looks_like_fits(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(6) == b"SIMPLE"
    except OSError:
        return False


def download_cutout(
    fits_url: str,
    ra: float,
    dec: float,
    size: float,
    *,
    settings: Settings | None = None,
    session: requests.Session | None = None,
) -> Path:
    """Reusable entry point: fetch an IRSA cutout and return the cached FITS path."""
    return CutoutService(settings).fetch(fits_url, ra, dec, size, session=session).path
