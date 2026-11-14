"""IRSA SIA2 VOTable parsing and transport errors (no network)."""

from __future__ import annotations

import pytest
import requests

from app.core.exceptions import (
    InvalidCoordinateError,
    InvalidRadiusError,
    IrsaHttpError,
    IrsaQueryError,
    IrsaResponseError,
    IrsaTimeoutError,
    IrsaUnavailableError,
)
from app.services.irsa_client import IrsaClient
from tests.conftest import (
    FakeResponse,
    FakeSession,
    sample_row,
    votable_xml,
)


def make_client(settings, response: FakeResponse) -> tuple[IrsaClient, FakeSession]:
    session = FakeSession(response)
    return IrsaClient(settings, session=session), session


def test_search_parses_metadata_and_maps_col_ids(settings) -> None:
    rows = [sample_row(), sample_row(obs_id="2026W20_2A_0436_1", obs_publisher_did="ivo://irsa.ipac/spherex_qr3?2026W20_2A_0436_1/D6")]
    client, session = make_client(settings, FakeResponse(content=votable_xml(rows), headers={"Content-Type": "application/x-votable+xml"}))

    parsed = client.search(156.09328159, -41.64466331, 0.01, collection="spherex_qr3")

    assert len(parsed) == 2
    first = parsed[0]
    assert first["obs_id"] == "2026W20_2A_0436_3"
    assert first["obs_publisher_did"].endswith("/D3")
    assert first["access_url"].startswith("https://irsa.ipac.caltech.edu/")
    assert first["t_min"] == pytest.approx(61177.275636)
    assert first["calib_level"] == 2
    assert first["dataproduct_subtype"] == "science"
    params = session.calls[0]["params"]
    assert params["POS"] == "CIRCLE 156.09328159 -41.64466331 0.01000000"
    assert params["COLLECTION"] == "spherex_qr3"


def test_empty_result_is_success(settings) -> None:
    client, _ = make_client(settings, FakeResponse(content=votable_xml([])))
    assert client.search(10.0, 10.0, 0.01) == []


def test_query_status_error_raises_irsa_query_error(settings) -> None:
    body = votable_xml(status="ERROR", message="UsageFault: BAD_REQUEST")
    client, _ = make_client(settings, FakeResponse(content=body))
    with pytest.raises(IrsaQueryError) as info:
        client.search(10.0, 10.0, 0.01)
    assert "BAD_REQUEST" in str(info.value.details)


def test_http_error(settings) -> None:
    client, _ = make_client(settings, FakeResponse(status_code=500))
    with pytest.raises(IrsaHttpError):
        client.search(10.0, 10.0, 0.01)


def test_timeout(settings) -> None:
    session = FakeSession()

    def boom(*args, **kwargs):
        raise requests.Timeout("slow")

    session.get = boom  # type: ignore[method-assign]
    client = IrsaClient(settings, session=session)
    with pytest.raises(IrsaTimeoutError):
        client.search(10.0, 10.0, 0.01)


def test_connection_error(settings) -> None:
    session = FakeSession()

    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    session.get = boom  # type: ignore[method-assign]
    client = IrsaClient(settings, session=session)
    with pytest.raises(IrsaUnavailableError):
        client.search(10.0, 10.0, 0.01)


def test_non_votable_response_rejected(settings) -> None:
    client, _ = make_client(settings, FakeResponse(content=b"<html>oops</html>", headers={"Content-Type": "text/html"}))
    with pytest.raises(IrsaResponseError):
        client.search(10.0, 10.0, 0.01)


def test_broken_xml_rejected(settings) -> None:
    client, _ = make_client(settings, FakeResponse(content=b"not xml at all", headers={"Content-Type": "application/x-votable+xml"}))
    with pytest.raises(IrsaResponseError):
        client.search(10.0, 10.0, 0.01)


def test_time_window_filters_rows_client_side(settings) -> None:
    inside = sample_row(t_min=61177.27, t_max=61177.28)
    outside = sample_row(obs_id="other", t_min=61200.0, t_max=61200.1)
    client, _ = make_client(settings, FakeResponse(content=votable_xml([inside, outside])))

    parsed = client.search(10.0, 10.0, 0.01, start_mjd=61177.0, end_mjd=61177.5)

    assert [row["obs_id"] for row in parsed] == ["2026W20_2A_0436_3"]


def test_find_by_publisher_did_matches_exact_row(settings) -> None:
    did = "ivo://irsa.ipac/spherex_qr3?2026W20_2A_0436_3/D3"
    client, session = make_client(settings, FakeResponse(content=votable_xml([sample_row()])))
    row = client.find_by_publisher_did(did)
    assert row is not None and row["obs_publisher_did"] == did
    assert session.calls[0]["params"]["ID"] == did
    assert session.calls[0]["params"]["COLLECTION"] == "spherex_qr3"


def test_search_rejects_bad_input(settings) -> None:
    client = IrsaClient(settings, session=FakeSession())
    with pytest.raises(InvalidCoordinateError):
        client.search(400.0, 0.0, 0.01)
    with pytest.raises(InvalidRadiusError):
        client.search(10.0, 10.0, 5.0)
