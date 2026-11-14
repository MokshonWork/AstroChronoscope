"""Live end-to-end API workflow against real IRSA (opt-in: RUN_LIVE=1).

Exercises the exact HTTP contract the frontend uses, but against genuine
observations. Skipped by default so the deterministic suite never needs network.
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_cache_store, get_irsa_client, get_settings_dep
from app.core.cache import CacheStore
from app.core.config import Settings
from app.main import app
from app.services.irsa_client import IrsaClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to query IRSA"),
]

RA, DEC = 156.09328159, -41.64466331
ORIGIN = "http://localhost:3000"


def strict_json(text: str) -> dict:
    return json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(AssertionError(f"non-finite JSON: {value}")))


@pytest.fixture
def live_api(tmp_path):
    settings = Settings(
        storage_dir=tmp_path / "storage",
        default_collection="spherex_qr3",
        request_timeout=240.0,
        download_retries=4,
    )
    settings.ensure_dirs()
    client = IrsaClient(settings)
    cache = CacheStore(60, 128)

    app.dependency_overrides[get_irsa_client] = lambda: client
    app.dependency_overrides[get_cache_store] = lambda: cache
    app.dependency_overrides[get_settings_dep] = lambda: settings

    # No context manager: the deterministic lifespan (repo storage dirs) is not needed.
    http = TestClient(app, raise_server_exceptions=False)
    try:
        yield http, settings
    finally:
        app.dependency_overrides.clear()
        client.close()


def test_live_frontend_workflow(live_api) -> None:
    http, settings = live_api

    health = http.get("/api/v1/health", headers={"Origin": ORIGIN})
    assert health.status_code == 200
    assert health.headers["access-control-allow-origin"] == ORIGIN

    search = http.post(
        "/api/v1/observations/search",
        json={"ra": RA, "dec": DEC, "radius": 0.01, "collection": "spherex_qr3"},
        headers={"Origin": ORIGIN},
    )
    assert search.status_code == 200
    body = strict_json(search.text)
    assert body["success"] is True
    assert body["collection"] == "spherex_qr3"
    assert body["total_observations"] > 0
    assert body["total_observations"] == len(body["observations"])
    assert len({item["observation_id"] for item in body["observations"]}) == body["total_observations"]
    assert search.headers["access-control-allow-origin"] == ORIGIN

    target = next(item for item in body["observations"] if item["fits_available"])
    assert target["obs_publisher_did"].startswith("ivo://irsa.ipac/")
    assert target["observation_date"].endswith("+00:00")

    detail = http.get(f"/api/v1/observations/{target['observation_id']}")
    assert detail.status_code == 200
    observation = strict_json(detail.text)["observation"]
    assert observation["obs_publisher_did"] == target["obs_publisher_did"]
    assert observation["processing_status"] == "ready"
    assert observation["fits_access_url"].startswith("https://irsa.ipac.caltech.edu/")

    cutout = http.post(
        f"/api/v1/observations/{target['observation_id']}/cutout",
        json={"ra": RA, "dec": DEC, "size": 0.1},
    )
    assert cutout.status_code == 200, cutout.text
    cutout_body = strict_json(cutout.text)
    assert cutout_body["cutout"]["width"] > 0
    assert cutout_body["cutout"]["height"] > 0
    assert cutout_body["cutout"]["unit"] == "MJy / sr"
    assert cutout_body["wcs"]["available"] is True
    assert cutout_body["wcs"]["target_inside_image"] is True

    preview = http.get(cutout_body["cutout"]["preview_url"], headers={"Origin": ORIGIN})
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["access-control-allow-origin"] == ORIGIN
    assert preview.content[:8] == b"\x89PNG\r\n\x1a\n"

    fits_response = http.get(cutout_body["cutout"]["fits_url"])
    assert fits_response.status_code == 200
    assert fits_response.headers["content-type"] == "image/fits"
    assert fits_response.content[:6] == b"SIMPLE"
    assert len(fits_response.content) > 1_000_000

    # Frontend-visible failure contract.
    assert http.get("/api/v1/observations/not-a-real-id").status_code == 404
    assert http.post("/api/v1/observations/search", json={"ra": 400, "dec": 0}).status_code == 422

    assert (settings.previews_dir / cutout_body["cutout"]["preview_url"].rsplit("/", 1)[-1]).is_file()
