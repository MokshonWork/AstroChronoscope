"""Live multi-epoch timeline against real IRSA — skipped unless RUN_LIVE=1.

Asserts structural invariants, never a fixed row/epoch count.
"""

from __future__ import annotations

import os

import pytest

from app.core.cache import CacheStore
from app.core.config import Settings
from app.services.irsa_client import IrsaClient
from app.services.observation_service import ObservationService
from app.services.timeline_service import TimelineService

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to query IRSA"),
]

RA, DEC = 156.09328159, -41.64466331
COLLECTION = "spherex_qr3"


@pytest.fixture
def service(tmp_path) -> TimelineService:
    settings = Settings(
        storage_dir=tmp_path / "storage",
        default_collection=COLLECTION,
        request_timeout=240.0,
        download_retries=4,
    )
    settings.ensure_dirs()
    return TimelineService(ObservationService(IrsaClient(settings), CacheStore(60, 512), settings))


def test_live_timeline_invariants(service) -> None:
    result = service.build(RA, DEC, 0.01, COLLECTION)

    assert result.total_product_rows > 0, "IRSA returned no products for the validated target"
    assert result.total_products > 0
    assert result.total_products == sum(epoch.product_count for epoch in result.epochs)
    assert result.unique_epochs == len(result.epochs)
    assert result.unique_observations == result.unique_epochs

    keys = [epoch.epoch_key for epoch in result.epochs]
    assert len(keys) == len(set(keys))
    assert all(epoch.identified for epoch in result.epochs)
    assert all(epoch.obs_id == epoch.epoch_key for epoch in result.epochs)

    times = [epoch.midpoint_mjd for epoch in result.epochs if epoch.midpoint_mjd is not None]
    assert times == sorted(times)
    assert result.earliest_observation_time is not None
    assert result.latest_observation_time is not None
    assert result.earliest_observation_time.endswith("+00:00")
    assert result.latest_observation_time.endswith("+00:00")
    assert result.temporal_baseline_days is not None and result.temporal_baseline_days >= 0

    quality = result.quality
    assert quality.products_target_covered + quality.products_target_outside + quality.products_target_coverage_unknown == result.total_products
    assert quality.metadata_complete_products + quality.metadata_incomplete_products == result.total_products
    assert quality.products_with_valid_timestamps + quality.products_without_valid_timestamps == result.total_products
    assert quality.products_with_band + quality.products_without_band == result.total_products

    assert result.available_bands
    assert sum(result.band_counts.values()) == quality.products_with_band
    assert result.metadata.comparability_assessed is False

    # Sibling bands of one obs_id share an exposure interval in the real data.
    for epoch in result.epochs:
        assert epoch.product_count >= 1
        assert len(epoch.products) == epoch.product_count
        if epoch.identified and epoch.timeline_eligible:
            assert epoch.start_time is not None and epoch.end_time is not None
