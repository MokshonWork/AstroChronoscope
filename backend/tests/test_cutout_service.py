"""Reusable cutout entry point and its disk cache."""

from __future__ import annotations

import pytest

from app.core.exceptions import InvalidCutoutSizeError
from app.services.cutout_service import download_cutout
from tests.conftest import FakeResponse, FakeSession, FITS_URL, TARGET_DEC, TARGET_RA, make_fits_bytes


def test_download_cutout_wrapper_and_cache(settings) -> None:
    session = FakeSession(FakeResponse(content=make_fits_bytes(), headers={"Content-Type": "image/fits"}))

    path = download_cutout(FITS_URL, TARGET_RA, TARGET_DEC, 0.1, settings=settings, session=session)

    assert path.is_file() and path.read_bytes()[:6] == b"SIMPLE"
    assert path.parent == settings.fits_dir
    url = session.calls[0]["url"]
    assert "center=156.09328159%2C-41.64466331" in url
    assert "size=0.100000deg" in url

    download_cutout(FITS_URL, TARGET_RA, TARGET_DEC, 0.1, settings=settings, session=session)
    assert len(session.calls) == 1


def test_download_cutout_rejects_bad_size(settings) -> None:
    with pytest.raises(InvalidCutoutSizeError):
        download_cutout(FITS_URL, TARGET_RA, TARGET_DEC, 5.0, settings=settings, session=FakeSession())
