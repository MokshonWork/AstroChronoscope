"""Safe serving of generated FITS cutouts and PNG previews."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.deps import get_settings_dep
from app.core.config import Settings
from app.core.exceptions import StoredFileNotFoundError

router = APIRouter(prefix="/files", tags=["files"])

#: Only hash-named artefacts written by this application are servable.
_FILENAME_RE = re.compile(r"^[0-9a-f]{64}\.(fits|png)$")


@router.get("/{filename}", summary="Serve a generated FITS cutout or PNG preview")
def get_generated_file(filename: str, settings: Settings = Depends(get_settings_dep)) -> FileResponse:
    if not _FILENAME_RE.fullmatch(filename):
        raise StoredFileNotFoundError("File is not a registered application artefact")

    directory = settings.fits_dir if filename.endswith(".fits") else settings.previews_dir
    path = (directory / filename).resolve()
    storage_root = settings.storage_dir.resolve()
    if storage_root not in path.parents or not path.is_file():
        raise StoredFileNotFoundError("File not found")

    media_type = "image/fits" if filename.endswith(".fits") else "image/png"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )
