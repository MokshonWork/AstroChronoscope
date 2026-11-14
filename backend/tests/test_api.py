"""HTTP contract tests through FastAPI (fake IRSA client, real FITS pipeline)."""

from __future__ import annotations

import base64

from app.core.exceptions import IrsaTimeoutError
from tests.conftest import FakeResponse, FakeSession, sample_row


def search_payload(**overrides):
    payload = {"ra": 156.09328159, "dec": -41.64466331, "radius": 0.01, "collection": "spherex_qr3"}
    payload.update(overrides)
    return payload


def test_health(api) -> None:
    client, *_ = api
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_search_contract(api) -> None:
    client, fake, *_ = api
    fake.rows = [
        sample_row(),
        sample_row(energy_bandpassname="SPHEREx-D6", obs_publisher_did="ivo://irsa.ipac/spherex_qr3?2026W20_2A_0436_3/D6"),
    ]

    response = client.post("/api/v1/observations/search", json=search_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["target"] == {"ra": 156.09328159, "dec": -41.64466331}
    assert body["collection"] == "spherex_qr3"
    assert body["total_observations"] == 2
    assert response.headers["X-Observation-Count"] == "2"
    first = body["observations"][0]
    for field in (
        "observation_id", "obs_id", "obs_publisher_did", "observation_date", "observation_date_mjd",
        "instrument", "facility", "collection", "band", "product_type", "product_subtype",
        "calib_level", "fits_available",
    ):
        assert field in first
    assert first["fits_available"] is True
    assert first["observation_date"].endswith("+00:00")


def test_search_empty_results(api) -> None:
    client, fake, *_ = api
    fake.rows = []
    response = client.post("/api/v1/observations/search", json=search_payload())
    assert response.status_code == 200
    assert response.json()["total_observations"] == 0
    assert response.json()["observations"] == []


def test_search_validation_error_shape(api) -> None:
    client, *_ = api
    response = client.post("/api/v1/observations/search", json=search_payload(ra=400))
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["details"]["errors"]


def test_search_unknown_field_rejected(api) -> None:
    client, *_ = api
    response = client.post("/api/v1/observations/search", json=search_payload(radius=0.01, bogus=1))
    assert response.status_code == 422


def test_irsa_timeout_maps_to_504(api) -> None:
    client, fake, *_ = api
    fake.error = IrsaTimeoutError("IRSA SIA request timed out")
    response = client.post("/api/v1/observations/search", json=search_payload())
    assert response.status_code == 504
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "IRSA_TIMEOUT"


def test_detail_and_unknown_id(api) -> None:
    client, fake, *_ = api
    search = client.post("/api/v1/observations/search", json=search_payload()).json()
    observation_id = search["observations"][0]["observation_id"]

    response = client.get(f"/api/v1/observations/{observation_id}")
    assert response.status_code == 200
    observation = response.json()["observation"]
    assert observation["obs_publisher_did"] == fake.rows[0]["obs_publisher_did"]
    assert observation["processing_status"] == "ready"
    assert observation["t_min_mjd"] == fake.rows[0]["t_min"]
    assert observation["access_estsize_bytes"] == int(fake.rows[0]["access_estsize"] * 1024)

    missing = base64.urlsafe_b64encode(b"ivo://irsa.ipac/spherex_qr3?NOPE/D3").decode().rstrip("=")
    response = client.get(f"/api/v1/observations/{missing}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "OBSERVATION_NOT_FOUND"


def test_cutout_and_file_serving(api) -> None:
    client, fake, settings, _ = api
    search = client.post("/api/v1/observations/search", json=search_payload()).json()
    observation_id = search["observations"][0]["observation_id"]

    response = client.post(
        f"/api/v1/observations/{observation_id}/cutout",
        json={"ra": 156.09328159, "dec": -41.64466331, "size": 0.1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["cutout"]["width"] == 58
    assert body["cutout"]["height"] == 59
    assert body["cutout"]["unit"] == "MJy / sr"
    assert body["wcs"]["available"] is True
    assert body["wcs"]["target_inside_image"] is True
    assert body["wcs"]["pixel_x"] == 28.5
    assert body["wcs"]["pixel_y"] == 29.0

    preview = client.get(body["cutout"]["preview_url"])
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content[:8] == b"\x89PNG\r\n\x1a\n"

    fits_response = client.get(body["cutout"]["fits_url"])
    assert fits_response.status_code == 200
    assert fits_response.headers["content-type"] == "image/fits"
    assert fits_response.content[:6] == b"SIMPLE"

    assert (settings.previews_dir / body["cutout"]["preview_url"].rsplit("/", 1)[-1]).is_file()


def test_files_reject_unregistered_names(api) -> None:
    client, *_ = api
    for name in ("notahash.png", "..%2F..%2Fetc%2Fpasswd", "a" * 64 + ".txt", "a" * 63 + ".png"):
        response = client.get(f"/api/v1/files/{name}")
        assert response.status_code == 404, name
        assert response.json()["success"] is False


def test_missing_access_url_is_not_a_success(api) -> None:
    client, fake, *_ = api
    fake.rows = [sample_row(access_url=None)]

    search = client.post("/api/v1/observations/search", json=search_payload())
    assert search.status_code == 200
    assert search.json()["observations"][0]["fits_available"] is False
    observation_id = search.json()["observations"][0]["observation_id"]

    detail = client.get(f"/api/v1/observations/{observation_id}")
    assert detail.json()["observation"]["processing_status"] == "no_fits_product"

    response = client.post(
        f"/api/v1/observations/{observation_id}/cutout",
        json={"ra": 156.09328159, "dec": -41.64466331, "size": 0.1},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MISSING_ACCESS_URL"


def test_html_instead_of_fits_returns_502(api) -> None:
    client, fake, *_ = api
    fake.session = FakeSession(FakeResponse(content=b"<html>Down for maintenance</html>", headers={"Content-Type": "text/html"}))
    observation_id = client.post("/api/v1/observations/search", json=search_payload()).json()["observations"][0]["observation_id"]

    response = client.post(
        f"/api/v1/observations/{observation_id}/cutout",
        json={"ra": 156.09328159, "dec": -41.64466331, "size": 0.1},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "INVALID_FITS_FILE"


def test_cutout_invalid_size_rejected(api) -> None:
    client, *_ = api
    observation_id = client.post("/api/v1/observations/search", json=search_payload()).json()["observations"][0]["observation_id"]
    response = client.post(
        f"/api/v1/observations/{observation_id}/cutout",
        json={"ra": 156.09328159, "dec": -41.64466331, "size": 0},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
