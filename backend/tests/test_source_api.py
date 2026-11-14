"""Phase 3 API integration tests (routes, caching, error envelope)."""

from __future__ import annotations

import json
import math

import pytest

from tests.conftest import TARGET_DEC, TARGET_RA, make_sky_fits_bytes, seed_pair

SIGMA_ARCSEC = 12.0
PSF_FWHM_PX = (SIGMA_ARCSEC / 6.15) * 2.3548200450309493
EXPECTED_JY = 1e-2 * 2.0 * math.pi * (SIGMA_ARCSEC / 206264.80624709636) ** 2 * 1e6


def strict_json(text: str) -> dict:
    return json.loads(
        text, parse_constant=lambda value: (_ for _ in ()).throw(AssertionError(f"non-finite JSON: {value}"))
    )


def _fits() -> bytes:
    return make_sky_fits_bytes(
        sources=((0.0, 0.0, 1e-2, SIGMA_ARCSEC),),
        noise=1e-5,
        with_psf=True,
        psf_fwhm_px=PSF_FWHM_PX,
    )


def _seed(api):
    _client, fake_client, _settings, _cache = api
    fits = _fits()
    token_a, token_b = seed_pair(fake_client, fits, fits)
    for row in fake_client.rows:
        row["t_max"] = row["t_min"] + 0.01
    return token_a, token_b


def test_detect_route_returns_sources(api) -> None:
    client, fake_client, _settings, _cache = api
    token_a, _token_b = _seed(api)

    response = client.post(
        "/api/v1/sources/detect",
        json={"observation_id": token_a, "ra": TARGET_RA, "dec": TARGET_DEC, "size": 0.05},
    )

    assert response.status_code == 200, response.text
    body = strict_json(response.text)
    assert body["success"] is True
    assert body["observation_id"] == token_a
    assert body["total_detections"] >= 1
    assert body["detections"][0]["detection_id"]
    assert body["provenance"]["algorithm_version"] == "phase3-source-detection-v1"
    assert fake_client.session.calls


def test_photometry_route_returns_jy_measurements(api) -> None:
    client, _fake_client, _settings, _cache = api
    token_a, _token_b = _seed(api)

    response = client.post(
        "/api/v1/sources/photometry",
        json={"observation_id": token_a, "ra": TARGET_RA, "dec": TARGET_DEC, "size": 0.05},
    )

    assert response.status_code == 200, response.text
    body = strict_json(response.text)
    assert body["success"] is True
    assert body["total_measurements"] >= 1
    measurement = body["measurements"][0]
    assert measurement["flux_jy"] == pytest.approx(EXPECTED_JY, rel=0.05)
    assert measurement["flux_jy_error"] > 0
    assert measurement["aperture_sum_unit"] == "MJy / sr"
    assert measurement["aperture_correction_applied"] is False


def test_photometry_cache_is_reused_across_requests(api) -> None:
    client, fake_client, _settings, _cache = api
    token_a, _token_b = _seed(api)
    payload = {"observation_id": token_a, "ra": TARGET_RA, "dec": TARGET_DEC, "size": 0.05}

    first = client.post("/api/v1/sources/photometry", json=payload)
    calls_after_first = len(fake_client.session.calls)
    second = client.post("/api/v1/sources/photometry", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(fake_client.session.calls) == calls_after_first


def test_lightcurve_route_returns_per_band_series(api) -> None:
    client, _fake_client, _settings, _cache = api
    token_a, _token_b = _seed(api)

    response = client.post(
        "/api/v1/targets/lightcurve",
        json={
            "ra": TARGET_RA,
            "dec": TARGET_DEC,
            "radius_deg": 0.05,
            "cutout_size_deg": 0.05,
            "max_observations": 5,
        },
    )

    assert response.status_code == 200, response.text
    body = strict_json(response.text)
    assert body["success"] is True
    assert body["epochs_processed"] == 2
    assert body["products_processed"] == 2
    assert body["association"]["source_count"] == 1
    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert source["epoch_count"] == 2
    assert source["bands"] == ["SPHEREx-D3"]
    series = source["series"][0]
    assert series["band"] == "SPHEREx-D3"
    assert len(series["points"]) == 2
    assert series["non_detections"] == 0
    assert series["metrics"]["status"] == "computed"
    assert series["metrics"]["unit"] == "Jy"
    for point in series["points"]:
        assert point["unit"] == "Jy"
        assert point["flux_jy"] == pytest.approx(EXPECTED_JY, rel=0.05)
    assert body["target"]["ra"] == TARGET_RA


def test_lightcurve_route_returns_empty_result_for_no_epochs(api) -> None:
    client, fake_client, _settings, _cache = api
    _seed(api)
    fake_client.rows = []

    response = client.post(
        "/api/v1/targets/lightcurve",
        json={"ra": TARGET_RA, "dec": TARGET_DEC, "radius_deg": 0.05},
    )

    assert response.status_code == 200
    body = strict_json(response.text)
    assert body["epochs_processed"] == 0
    assert body["sources"] == []
    assert "no_multi_epoch_sources" in body["warnings"]


def test_unknown_observation_is_a_typed_404(api) -> None:
    client, _fake_client, _settings, _cache = api

    response = client.post(
        "/api/v1/sources/detect",
        json={"observation_id": "not-a-real-token", "ra": TARGET_RA, "dec": TARGET_DEC},
    )

    assert response.status_code == 404
    body = strict_json(response.text)
    assert body["success"] is False
    assert body["error"]["code"]


def test_detection_validation_returns_the_error_envelope(api) -> None:
    client, _fake_client, _settings, _cache = api

    response = client.post(
        "/api/v1/sources/detect",
        json={"observation_id": "token", "ra": 999.0, "dec": TARGET_DEC},
    )

    assert response.status_code == 422
    body = strict_json(response.text)
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["details"]["errors"]


def test_openapi_exposes_phase3_routes(api) -> None:
    client, _fake_client, _settings, _cache = api
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/sources/detect" in paths
    assert "/api/v1/sources/photometry" in paths
    assert "/api/v1/targets/lightcurve" in paths
