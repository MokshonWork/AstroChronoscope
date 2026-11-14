"""API contract tests for FITS inspection and multi-epoch comparison."""

from __future__ import annotations

import json

import pytest

from tests.conftest import TARGET_DEC, TARGET_RA, make_sky_fits_bytes, seed_pair

SOURCES = (
    (0.0, 0.0, 1e-2, 12.0),
    (60.0, 40.0, 6e-3, 10.0),
    (-55.0, -35.0, 4e-3, 14.0),
)


def strict_json(text: str) -> dict:
    return json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(AssertionError(f"non-finite JSON: {value}")))


def epoch_a(**overrides) -> bytes:
    options = dict(sources=SOURCES, noise=1e-6, with_psf=True, psf_fwhm_px=2.0)
    options.update(overrides)
    return make_sky_fits_bytes(**options)


def epoch_b(**overrides) -> bytes:
    options = dict(
        sources=SOURCES,
        noise=1e-6,
        rotation_deg=18.0,
        crpix=(40.5, 24.5),
        with_psf=True,
        psf_fwhm_px=2.2,
        seed=11,
    )
    options.update(overrides)
    return make_sky_fits_bytes(**options)


def pair(api, a: bytes | None = None, b: bytes | None = None):
    client, fake, settings, cache = api
    token_a, token_b = seed_pair(fake, a or epoch_a(), b or epoch_b())
    return client, fake, settings, cache, token_a, token_b


def compare_payload(token_a: str, token_b: str, **config) -> dict:
    return {
        "observation_id_a": token_a,
        "observation_id_b": token_b,
        "ra": TARGET_RA,
        "dec": TARGET_DEC,
        "size": 0.2,
        "config": config,
    }


# --------------------------------------------------------------- inspection
def test_inspect_returns_metadata_and_warnings(api) -> None:
    client, _, _, _, token_a, _ = pair(api)

    response = client.post("/api/v1/images/inspect", json={"observation_id": token_a, "ra": TARGET_RA, "dec": TARGET_DEC, "size": 0.2})

    assert response.status_code == 200, response.text
    body = strict_json(response.text)
    assert body["success"] is True
    inspection = body["inspection"]
    assert inspection["source"] == "cutout"
    assert inspection["science_extname"] == "IMAGE"
    assert inspection["stats"]["shape"] == [64, 64]
    assert inspection["stats"]["bunit"] == "MJy / sr"
    assert inspection["wcs"]["available"] is True
    assert inspection["wcs"]["target_inside_image"] is True
    assert inspection["variance"]["unit"] == "MJy2 / sr2"
    assert inspection["psf"]["fwhm_px"] == pytest.approx(2.0, abs=0.05)
    assert inspection["quality"] in {"PASS", "WARNING"}


def test_inspect_unknown_observation_is_a_typed_404(api) -> None:
    client, *_ = api

    response = client.post("/api/v1/images/inspect", json={"observation_id": "not-an-observation"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "OBSERVATION_NOT_FOUND"


def test_inspect_requires_ra_and_dec_together(api) -> None:
    client, _, _, _, token_a, _ = pair(api)

    response = client.post("/api/v1/images/inspect", json={"observation_id": token_a, "ra": TARGET_RA})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_COORDINATES"


def test_inspect_rejects_out_of_range_coordinates(api) -> None:
    client, _, _, _, token_a, _ = pair(api)

    response = client.post("/api/v1/images/inspect", json={"observation_id": token_a, "ra": 400.0, "dec": TARGET_DEC})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


# --------------------------------------------------------------- comparison
def test_compare_returns_difference_products(api) -> None:
    client, _, settings, _, token_a, token_b = pair(api)

    response = client.post("/api/v1/images/compare", json=compare_payload(token_a, token_b))

    assert response.status_code == 200, response.text
    body = strict_json(response.text)
    assert body["status"] == "completed"
    assert body["scientifically_valid"] is True
    assert body["difference"]["fits_url"].startswith("/api/v1/files/")
    assert body["difference"]["mask_url"].startswith("/api/v1/files/")
    assert body["difference"]["preview_url"].endswith(".png")
    assert body["difference"]["uncertainty_available"] is True
    assert body["reprojection"]["common_coverage_fraction"] > 0.3
    assert body["alignment"]["verified"] is True
    assert body["provenance"]["comparison_id"] == body["comparison_id"]
    assert body["quality"]["overall_status"] in {"PASS", "WARNING"}

    for url in (body["difference"]["fits_url"], body["difference"]["mask_url"], body["difference"]["uncertainty_url"]):
        served = client.get(url)
        assert served.status_code == 200, url
        assert served.content[:6] == b"SIMPLE"
    preview = client.get(body["difference"]["preview_url"])
    assert preview.status_code == 200
    assert preview.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_compare_quality_failure_is_reported_not_hidden(api) -> None:
    client, _, _, _, token_a, token_b = pair(api, b=epoch_b(bunit="Jy"))

    response = client.post("/api/v1/images/compare", json=compare_payload(token_a, token_b))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["scientifically_valid"] is False
    assert body["difference"] is None
    failed = {check["name"] for check in body["quality"]["checks"] if check["status"] == "FAIL"}
    assert "units" in failed
    assert any("BUNIT" in warning for warning in body["quality"]["warnings"])


def test_compare_rejects_unknown_observation(api) -> None:
    client, *_ = api

    response = client.post("/api/v1/images/compare", json=compare_payload("bad-a", "bad-b"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "OBSERVATION_NOT_FOUND"


def test_compare_rejects_oversized_cutout(api) -> None:
    client, _, _, _, token_a, token_b = pair(api)

    response = client.post(
        "/api/v1/images/compare",
        json={**compare_payload(token_a, token_b), "size": 5.0},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CUTOUT_SIZE"


def test_compare_enforces_the_pixel_limit(api) -> None:
    client, _, settings, _, token_a, token_b = pair(api)
    settings.max_comparison_pixels = 1000

    response = client.post("/api/v1/images/compare", json=compare_payload(token_a, token_b))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IMAGE_PROCESSING_LIMIT"


def test_comparison_cache_hit_avoids_reprocessing(api) -> None:
    client, fake, _, _, token_a, token_b = pair(api)

    first = client.post("/api/v1/images/compare", json=compare_payload(token_a, token_b)).json()
    downloads = len(fake.session.calls)
    second = client.post("/api/v1/images/compare", json=compare_payload(token_a, token_b)).json()

    assert second["comparison_id"] == first["comparison_id"]
    assert second["difference"] == first["difference"]
    assert len(fake.session.calls) == downloads


def test_get_comparison_round_trips_and_404s(api) -> None:
    client, *_ , token_a, token_b = pair(api)
    created = client.post("/api/v1/images/compare", json=compare_payload(token_a, token_b)).json()

    fetched = client.get(f"/api/v1/images/comparisons/{created['comparison_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["comparison_id"] == created["comparison_id"]
    assert fetched.json()["provenance"] == created["provenance"]

    missing = client.get("/api/v1/images/comparisons/" + "0" * 32)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "COMPARISON_NOT_FOUND"

    malformed = client.get("/api/v1/images/comparisons/not-a-hash")
    assert malformed.status_code == 404
    assert malformed.json()["error"]["code"] == "COMPARISON_NOT_FOUND"


def test_compare_config_is_echoed_and_validated(api) -> None:
    client, _, _, _, token_a, token_b = pair(api)

    response = client.post(
        "/api/v1/images/compare",
        json=compare_payload(token_a, token_b, psf_matching="gaussian", background_correction="none"),
    )
    assert response.status_code == 200
    assert response.json()["config"]["psf_matching"] == "gaussian"
    assert response.json()["config"]["background_correction"] == "none"

    invalid = client.post(
        "/api/v1/images/compare",
        json=compare_payload(token_a, token_b, interpolation="lanczos"),
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
