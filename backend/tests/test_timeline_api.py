"""HTTP contract tests for GET /api/v1/targets/timeline."""

from __future__ import annotations

import json

from app.core.exceptions import IrsaTimeoutError, IrsaUnavailableError
from tests.test_timeline_service import product

TARGET = {"ra": 156.09328159, "dec": -41.64466331}
COLLECTION = "spherex_qr3"


def strict_json(text: str) -> dict:
    return json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(AssertionError(f"non-finite JSON: {value}")))


def get_timeline(client, **params):
    query = {**TARGET, "radius": 0.01, "collection": COLLECTION}
    query.update(params)
    return client.get("/api/v1/targets/timeline", params=query)


def test_timeline_contract(api) -> None:
    client, fake, *_ = api
    fake.rows = [
        product("obs-a", "SPHEREx-D3", t_min=61177.0),
        product("obs-a", "SPHEREx-D6", t_min=61177.0),
        product("obs-b", "SPHEREx-D3", t_min=61178.0),
    ]

    response = get_timeline(client)

    assert response.status_code == 200, response.text
    body = strict_json(response.text)
    assert body["success"] is True
    assert body["target"] == {"ra": TARGET["ra"], "dec": TARGET["dec"], "frame": "ICRS"}
    assert body["query"]["radius_deg"] == 0.01
    assert body["query"]["collection"] == COLLECTION
    assert body["total_product_rows"] == 3
    assert body["total_products"] == 3
    assert body["unique_observations"] == 2
    assert body["unique_epochs"] == 2
    assert body["earliest_observation_time"].endswith("+00:00")
    assert body["latest_observation_time"].endswith("+00:00")
    assert body["temporal_baseline_days"] == 1.0
    assert body["available_bands"] == ["SPHEREx-D3", "SPHEREx-D6"]
    assert body["band_counts"] == {"SPHEREx-D3": 2, "SPHEREx-D6": 1}

    first = body["epochs"][0]
    assert first["epoch_key"] == "obs-a"
    assert first["product_count"] == 2
    assert first["bands"] == ["SPHEREx-D3", "SPHEREx-D6"]
    assert first["timeline_eligible"] is True
    for field in (
        "epoch_index", "observation_time", "start_time", "end_time", "midpoint_mjd",
        "duration_seconds", "target_covered", "products", "warnings",
    ):
        assert field in first

    product_row = first["products"][0]
    for field in (
        "observation_id", "obs_publisher_did", "band", "t_min_mjd", "t_max_mjd",
        "start_time", "end_time", "observation_time", "timeline_eligible",
        "fits_available", "processing_status", "target_covered", "metadata_complete",
        "missing_fields", "warnings",
    ):
        assert field in product_row

    assert body["quality"]["products_included"] == 3
    assert body["quality"]["metadata_complete_products"] == 3
    assert body["metadata"]["time_standard"] == "UTC"
    assert body["metadata"]["comparability_assessed"] is False


def test_timeline_observation_id_reuses_existing_detail_endpoint(api) -> None:
    client, fake, *_ = api
    fake.rows = [product("obs-a", "SPHEREx-D3")]

    body = get_timeline(client).json()
    observation_id = body["epochs"][0]["products"][0]["observation_id"]

    detail = client.get(f"/api/v1/observations/{observation_id}")

    assert detail.status_code == 200
    assert detail.json()["observation"]["obs_id"] == "obs-a"


def test_timeline_empty_result_is_success(api) -> None:
    client, fake, *_ = api
    fake.rows = []

    response = get_timeline(client)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["total_product_rows"] == 0
    assert body["total_products"] == 0
    assert body["unique_epochs"] == 0
    assert body["epochs"] == []
    assert body["earliest_observation_time"] is None
    assert body["temporal_baseline_days"] is None
    assert body["warnings"][0]["code"] == "empty_result"


def test_timeline_missing_metadata_is_reported(api) -> None:
    client, fake, *_ = api
    fake.rows = [product("obs-a", "SPHEREx-D3", t_min=None, t_max=None, s_region=None)]

    body = get_timeline(client).json()

    assert body["total_products"] == 1
    assert body["unique_epochs"] == 1
    assert body["epochs"][0]["timeline_eligible"] is False
    assert body["quality"]["products_without_valid_timestamps"] == 1
    codes = {warning["code"] for warning in body["warnings"]}
    assert {"missing_timestamp", "missing_spatial_metadata"} <= codes


def test_timeline_invalid_coordinates(api) -> None:
    client, *_ = api
    response = get_timeline(client, ra=400)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_timeline_invalid_radius(api) -> None:
    client, *_ = api
    response = get_timeline(client, radius=2.0)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_RADIUS"


def test_timeline_invalid_band_filter(api) -> None:
    client, *_ = api
    response = get_timeline(client, bands="bad band!")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_BAND_FILTER"


def test_timeline_invalid_date_range(api) -> None:
    client, *_ = api
    response = get_timeline(client, start_date="2026-01-02", end_date="2026-01-01")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_DATE_RANGE"


def test_timeline_band_filter(api) -> None:
    client, fake, *_ = api
    fake.rows = [
        product("obs-a", "SPHEREx-D3"),
        product("obs-a", "SPHEREx-D6"),
    ]

    body = get_timeline(client, bands=["SPHEREx-D3"]).json()

    assert body["total_products"] == 1
    assert body["available_bands"] == ["SPHEREx-D3"]
    assert body["query"]["bands"] == ["SPHEREx-D3"]


def test_timeline_irsa_timeout_maps_to_504(api) -> None:
    client, fake, *_ = api
    fake.error = IrsaTimeoutError("IRSA SIA request timed out")

    response = get_timeline(client)

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "IRSA_TIMEOUT"


def test_timeline_irsa_unavailable_maps_to_503(api) -> None:
    client, fake, *_ = api
    fake.error = IrsaUnavailableError("IRSA is down")

    response = get_timeline(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "IRSA_UNAVAILABLE"


def test_timeline_ignores_unknown_query_fields(api) -> None:
    client, *_ = api
    response = get_timeline(client, bogus=1)
    # Extra query parameters are ignored by FastAPI; the request still succeeds.
    assert response.status_code == 200


def test_timeline_is_documented_in_openapi(api) -> None:
    client, *_ = api
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/targets/timeline" in paths
    operation = paths["/api/v1/targets/timeline"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]
    assert {"400", "422", "502", "503", "504"} <= set(operation["responses"])
