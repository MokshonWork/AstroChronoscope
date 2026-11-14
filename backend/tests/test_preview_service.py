"""Preview rendering: valid PNG, correct dimensions, original FITS untouched."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from app.core.exceptions import PreviewGenerationError
from app.services.preview_service import generate_preview
from tests.conftest import make_fits_bytes


def test_generate_preview_png(settings, tmp_path) -> None:
    fits_path = tmp_path / "cutout.fits"
    fits_path.write_bytes(make_fits_bytes())
    before = hashlib.sha256(fits_path.read_bytes()).hexdigest()

    output = generate_preview(fits_path, settings=settings)

    assert output == fits_path.with_suffix(".png")
    assert output.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(output) as image:
        assert image.size == (58, 59)
        assert image.mode in ("RGB", "RGBA")
    assert hashlib.sha256(fits_path.read_bytes()).hexdigest() == before


def test_preview_custom_output_path(settings, tmp_path) -> None:
    fits_path = tmp_path / "cutout.fits"
    fits_path.write_bytes(make_fits_bytes())
    output = tmp_path / "previews" / "custom.png"

    assert generate_preview(fits_path, output, settings=settings) == output
    assert output.is_file()


def test_all_nan_image_fails_clearly(settings, tmp_path) -> None:
    path = tmp_path / "nan.fits"
    header = fits.Header()
    header["BUNIT"] = "MJy / sr"
    data = np.full((10, 10), np.nan, dtype=np.float32)
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(data=data, header=header, name="IMAGE")]).writeto(path)

    with pytest.raises(PreviewGenerationError):
        generate_preview(path, settings=settings)
