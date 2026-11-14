"""Live IRSA integration test — skipped unless RUN_LIVE=1.

Reproduces the validated experiment: RA 156.09328159, Dec -41.64466331,
collection spherex_qr3, 0.01 deg search, 0.1 deg cutout. The number of rows is
whatever IRSA returns today, never an assumed constant.
"""

from __future__ import annotations

import os

import pytest

from app.core.cache import CacheStore
from app.core.config import Settings
from app.services.irsa_client import IrsaClient
from app.services.observation_service import ObservationService

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to query IRSA"),
]

RA, DEC = 156.09328159, -41.64466331
COLLECTION = "spherex_qr3"


@pytest.fixture
def service(tmp_path) -> ObservationService:
    settings = Settings(
        storage_dir=tmp_path / "storage",
        default_collection=COLLECTION,
        request_timeout=180.0,
        download_retries=4,
    )
    settings.ensure_dirs()
    # The validated target returns ~126 product rows; keep them all cached so
    # create_cutout resolves from the search result instead of an IRSA ID lookup.
    return ObservationService(IrsaClient(settings), CacheStore(60, 512), settings)


def test_live_search_returns_genuine_metadata(service) -> None:
    result = service.search(RA, DEC, 0.01, COLLECTION)

    assert result.collection == COLLECTION
    assert result.total_observations > 0, "IRSA returned no observations for the validated target"
    assert result.total_observations == len(result.observations)

    first = result.observations[0]
    assert first.obs_publisher_did and first.obs_publisher_did.startswith("ivo://irsa.ipac/")
    assert first.fits_available is True
    assert first.observation_date and first.observation_date.endswith("+00:00")
    assert first.instrument == "SPHEREx"
    assert first.calib_level == 2
    assert first.band and first.band.startswith("SPHEREx-D")


def test_live_cutout_matches_validated_experiment(service) -> None:
    result = service.search(RA, DEC, 0.01, COLLECTION)
    target = next((item for item in result.observations if item.fits_available), None)
    assert target is not None

    cutout = service.create_cutout(target.observation_id, RA, DEC, 0.1)

    assert cutout.cutout.width > 0 and cutout.cutout.height > 0
    assert cutout.cutout.unit == "MJy / sr"
    assert cutout.wcs.available is True
    assert cutout.wcs.target_inside_image is True
    assert cutout.wcs.pixel_x is not None and cutout.wcs.pixel_y is not None
    assert cutout.cutout.fits_url.endswith(".fits")
    assert cutout.cutout.preview_url.endswith(".png")

    fits_path = service.settings.fits_dir / cutout.cutout.fits_url.rsplit("/", 1)[-1]
    preview_path = service.settings.previews_dir / cutout.cutout.preview_url.rsplit("/", 1)[-1]
    assert fits_path.is_file() and fits_path.stat().st_size > 1_000_000
    assert preview_path.is_file() and preview_path.stat().st_size > 0

    image = service.cutouts.verify_cached(fits_path)
    assert image is True
