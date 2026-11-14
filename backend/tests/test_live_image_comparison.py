"""Live SPHEREx inspection/comparison against real IRSA — skipped unless RUN_LIVE=1.

The target and collection are the Phase 1 validated ones. The test discovers the
timeline first and only compares two products in the same band when such a pair
actually exists; otherwise it reports the limitation instead of inventing one.
"""

from __future__ import annotations

import os

import pytest

from app.core.cache import CacheStore
from app.core.config import Settings
from app.schemas.image_processing import CompareRequest, ComparisonConfig
from app.services.image_comparison import ImageComparisonService
from app.services.irsa_client import IrsaClient
from app.services.observation_service import ObservationService
from app.services.timeline_service import TimelineService

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to query IRSA"),
]

RA, DEC = 156.09328159, -41.64466331
COLLECTION = "spherex_qr3"
SIZE_DEG = 0.2


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    settings = Settings(
        storage_dir=tmp_path_factory.mktemp("live_storage"),
        default_collection=COLLECTION,
        request_timeout=300.0,
        download_retries=4,
        max_download_bytes=512 * 1024 * 1024,
    )
    settings.ensure_dirs()
    observations = ObservationService(IrsaClient(settings), CacheStore(600, 512), settings)
    timeline = TimelineService(observations).build(RA, DEC, 0.01, COLLECTION)
    return settings, observations, timeline


def _products(timeline):
    for epoch in timeline.epochs:
        for product in epoch.products:
            if product.processing_status == "ready" and product.observation_id and product.fits_available:
                yield epoch, product


def test_live_inspection_reports_real_product_metadata(live) -> None:
    settings, observations, timeline = live
    epoch, product = next(_products(timeline), (None, None))
    if product is None:
        pytest.skip("IRSA returned no downloadable SPHEREx product for the validated target")

    service = ImageComparisonService(observations)
    inspection = service.inspect(product.observation_id, RA, DEC, SIZE_DEG)

    assert inspection.stats.finite_pixels > 0
    assert inspection.wcs.available is True
    assert inspection.wcs.target_inside_image is True
    assert inspection.wcs.pixel_scale_arcsec is not None
    assert inspection.stats.bunit is not None
    assert inspection.fits_sha256 and len(inspection.fits_sha256) == 64
    assert inspection.quality in {"PASS", "WARNING", "FAIL"}


def test_live_same_band_comparison_is_honest_about_quality(live) -> None:
    settings, observations, timeline = live
    by_band: dict[str, list] = {}
    for epoch, product in _products(timeline):
        if product.band:
            by_band.setdefault(product.band, []).append((epoch, product))

    candidates = [
        (band, pairs[0][1], pairs[1][1])
        for band, pairs in by_band.items()
        if len(pairs) >= 2
    ]
    if not candidates:
        pytest.skip("no two same-band SPHEREx products with FITS access were discovered")

    band, product_a, product_b = candidates[0]
    service = ImageComparisonService(observations)
    response = service.compare(
        CompareRequest(
            observation_id_a=product_a.observation_id,
            observation_id_b=product_b.observation_id,
            ra=RA,
            dec=DEC,
            size=SIZE_DEG,
            config=ComparisonConfig(),
        )
    )

    assert response.inputs[0].band == band == response.inputs[1].band
    assert response.quality.checks, "quality assessment must always be populated"
    assert response.provenance.input_sha256
    assert response.provenance.software["astropy"]

    if response.status == "completed":
        assert response.scientifically_valid is True
        assert response.difference is not None
        assert response.difference.statistics.valid_pixels > 0
        assert (settings.fits_dir / response.difference.fits_url.rsplit("/", 1)[-1]).is_file()
        assert response.reprojection.common_coverage_fraction > 0
    else:
        assert response.scientifically_valid is False
        assert response.difference is None
        assert any(check.status == "FAIL" for check in response.quality.checks)
