"""Software version provenance shared by scientific services."""

from __future__ import annotations

import platform

import astropy
import numpy as np
import scipy


def backend_version() -> str:
    try:
        from app.main import VERSION

        return VERSION
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def software_versions() -> dict[str, str]:
    return {
        "planet_x_backend": backend_version(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "astropy": astropy.__version__,
        "scipy": scipy.__version__,
    }


__all__ = ["backend_version", "software_versions"]
