"""FITS product validation, streamed download and extension extraction."""

from __future__ import annotations

import numpy as np
import pytest

from app.core.exceptions import (
    CutoutUnavailableError,
    InvalidFitsFileError,
    MissingAccessUrlError,
    MissingImageExtensionError,
    UnsupportedProductError,
    UntrustedUrlError,
)
from app.services import fits_service
from app.services.fits_service import (
    download_fits,
    extract_science_image,
    get_fits_product,
    validate_fits_url,
)
from tests.conftest import FakeResponse, FakeSession, FITS_URL, make_fits_bytes, sample_row


# ------------------------------------------------------------ product lookup
def test_get_fits_product_uses_sia_url(settings) -> None:
    product = get_fits_product(sample_row(), settings)
    assert product.access_url == FITS_URL
    assert product.obs_publisher_did.endswith("/D3")


def test_missing_access_url(settings) -> None:
    with pytest.raises(MissingAccessUrlError):
        get_fits_product(sample_row(access_url=None), settings)


def test_untrusted_host_rejected(settings) -> None:
    with pytest.raises(UntrustedUrlError):
        get_fits_product(sample_row(access_url="https://evil.example.com/a.fits"), settings)


def test_http_scheme_rejected(settings) -> None:
    with pytest.raises(UntrustedUrlError):
        validate_fits_url("http://irsa.ipac.caltech.edu/ibe/a.fits", settings)


def test_credentials_in_url_rejected(settings) -> None:
    with pytest.raises(UntrustedUrlError):
        validate_fits_url("https://user:pass@irsa.ipac.caltech.edu/ibe/a.fits", settings)


def test_unsupported_access_format(settings) -> None:
    with pytest.raises(UnsupportedProductError):
        get_fits_product(sample_row(access_format="application/json"), settings)


# ----------------------------------------------------------------- download
def test_download_streams_and_verifies(settings, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(fits_service.time, "sleep", lambda _: None)
    payload = make_fits_bytes()
    session = FakeSession(FakeResponse(content=payload, headers={"Content-Type": "image/fits", "Content-Length": str(len(payload))}))
    destination = tmp_path / "out.fits"

    result = download_fits(FITS_URL, destination, session=session, settings=settings)

    assert result == destination
    assert destination.read_bytes()[:6] == b"SIMPLE"
    assert not destination.with_suffix(".fits.part").exists()


def test_download_rejects_html(settings, tmp_path) -> None:
    session = FakeSession(FakeResponse(content=b"<html>error</html>", headers={"Content-Type": "text/html"}))
    with pytest.raises(InvalidFitsFileError):
        download_fits(FITS_URL, tmp_path / "out.fits", session=session, settings=settings)


def test_download_rejects_non_fits_body(settings, tmp_path) -> None:
    session = FakeSession(FakeResponse(content=b"X" * 4000, headers={"Content-Type": "image/fits"}))
    with pytest.raises(InvalidFitsFileError):
        download_fits(FITS_URL, tmp_path / "out.fits", session=session, settings=settings)


def test_download_detects_incomplete_body(settings, tmp_path) -> None:
    session = FakeSession(FakeResponse(content=b"X" * 3000, headers={"Content-Length": "999999"}))
    with pytest.raises(InvalidFitsFileError):
        download_fits(FITS_URL, tmp_path / "out.fits", session=session, settings=settings)


def test_download_enforces_size_limit(settings, tmp_path) -> None:
    settings.max_download_bytes = 1000
    payload = make_fits_bytes()
    assert len(payload) > 1000
    session = FakeSession(FakeResponse(content=payload, headers={"Content-Type": "image/fits"}))
    with pytest.raises(InvalidFitsFileError):
        download_fits(FITS_URL, tmp_path / "out.fits", session=session, settings=settings)


def test_download_retries_busy_server(settings, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(fits_service.time, "sleep", lambda _: None)
    payload = make_fits_bytes()
    session = FakeSession(
        [
            FakeResponse(status_code=503, content=b"Server busy, retry later.", headers={"Content-Type": "text/plain"}),
            FakeResponse(status_code=503, content=b"Server busy, retry later.", headers={"Content-Type": "text/plain"}),
            FakeResponse(content=payload, headers={"Content-Type": "image/fits"}),
        ]
    )

    download_fits(FITS_URL, tmp_path / "out.fits", session=session, settings=settings)

    assert len(session.calls) == 3
    assert (tmp_path / "out.fits").is_file()


def test_download_gives_up_when_server_stays_busy(settings, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(fits_service.time, "sleep", lambda _: None)
    session = FakeSession([FakeResponse(status_code=503, content=b"Server busy")])
    with pytest.raises(CutoutUnavailableError):
        download_fits(FITS_URL, tmp_path / "out.fits", session=session, settings=settings)
    assert not (tmp_path / "out.fits").exists()


def test_download_rejects_untrusted_url_before_request(settings, tmp_path) -> None:
    session = FakeSession()
    with pytest.raises(UntrustedUrlError):
        download_fits("https://evil.example.com/a.fits", tmp_path / "out.fits", session=session, settings=settings)
    assert session.calls == []


# ---------------------------------------------------------------- extraction
def test_extract_science_image_by_name(settings, tmp_path) -> None:
    path = tmp_path / "cutout.fits"
    path.write_bytes(make_fits_bytes())

    image = extract_science_image(path)

    assert image.extname == "IMAGE"
    assert image.data.shape == (59, 58)
    assert image.height == 59 and image.width == 58
    assert image.unit == "MJy / sr"
    assert image.flags is not None and image.flags.shape == image.data.shape
    assert image.variance is not None
    assert np.isnan(image.data[0, 0])
    stats = image.stats()
    assert stats["finite_pixels"] == 59 * 58 - 59  # 58 NaN in row 0 + 1 inf
    assert image.available_hdus == ["PRIMARY", "ZODI", "IMAGE", "FLAGS", "VARIANCE"]


def test_extract_without_flags_or_variance(settings, tmp_path) -> None:
    path = tmp_path / "cutout.fits"
    path.write_bytes(make_fits_bytes(with_flags=False, with_variance=False))
    image = extract_science_image(path)
    assert image.flags is None and image.variance is None


def test_mismatched_flags_shape_is_ignored(settings, tmp_path) -> None:
    from astropy.io import fits

    path = tmp_path / "cutout.fits"
    path.write_bytes(make_fits_bytes())
    with fits.open(path, mode="update") as hdul:
        hdul["FLAGS"].data = np.zeros((3, 3), dtype=np.int32)
    image = extract_science_image(path)
    assert image.flags is None


def test_missing_image_extension(settings, tmp_path) -> None:
    from astropy.io import fits

    path = tmp_path / "no-image.fits"
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(data=np.zeros((4, 4), dtype=np.float32), name="ZODI")]).writeto(path)
    with pytest.raises(MissingImageExtensionError):
        extract_science_image(path)


def test_corrupt_fits(settings, tmp_path) -> None:
    path = tmp_path / "bad.fits"
    path.write_bytes(b"SIMPLE  =                    T" + b"\x00" * 10)
    with pytest.raises(InvalidFitsFileError):
        extract_science_image(path)
