#!/usr/bin/env python3
"""Fresh-install integration check for the Planet X backend.

Runs against an interpreter where the backend was installed *only* from
``pyproject.toml``/``uv.lock`` (see ``scripts/fresh_install_check.sh``). It does
not import anything from ``tests/``, so a passing run proves the shipped
application alone can serve its API and produce a scientifically valid
comparison.

Every check prints ``PASS``/``FAIL``; the process exits non-zero on any failure.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import traceback
from pathlib import Path

TARGET_RA = 156.09328159
TARGET_DEC = -41.64466331
SOURCES = (
    (0.0, 0.0, 1e-2, 12.0),
    (60.0, 40.0, 6e-3, 10.0),
    (-55.0, -35.0, 4e-3, 14.0),
)
VARIANCE = 4e-8
SIZE = 0.2

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------- synthetic FITS
def _wcs(nx, ny, crpix, rotation_deg):
    from astropy.wcs import WCS

    scale = 6.15 / 3600.0
    theta = rotation_deg * 3.141592653589793 / 180.0
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = list(crpix)
    wcs.wcs.crval = [TARGET_RA, TARGET_DEC]
    wcs.wcs.cd = [
        [-scale * __import__("math").cos(theta), scale * __import__("math").sin(theta)],
        [scale * __import__("math").sin(theta), scale * __import__("math").cos(theta)],
    ]
    return wcs


def _epsf(fwhm_px: float, samples: int = 33, oversampling: int = 5):
    import numpy as np
    from astropy.io import fits

    sigma = fwhm_px * oversampling / 2.3548200450309493
    yy, xx = np.mgrid[0:samples, 0:samples]
    centre = (samples - 1) / 2.0
    kernel = np.exp(-((xx - centre) ** 2 + (yy - centre) ** 2) / (2.0 * sigma ** 2))
    kernel /= kernel.sum()
    columns = [
        fits.Column(name="CWAVE", format="E", unit="um", array=np.full(3, 2.0)),
        fits.Column(name="NSTAR", format="J", array=np.full(3, 500)),
        fits.Column(
            name="EPSF",
            format=f"{samples * samples}D",
            dim=f"({samples},{samples})",
            array=np.repeat(kernel.reshape(1, -1), 3, axis=0),
        ),
    ]
    hdu = fits.BinTableHDU.from_columns(columns, name="EPSF")
    hdu.header["OVSMPX"] = oversampling
    hdu.header["OVSMPY"] = oversampling
    return hdu


def sky_fits(*, nx=64, ny=64, crpix=(32.5, 32.5), rotation_deg=0.0, psf_fwhm_px=2.0) -> bytes:
    """SPHEREx-shaped synthetic product: IMAGE + FLAGS + VARIANCE + EPSF."""
    import numpy as np
    from astropy.io import fits

    wcs = _wcs(nx, ny, crpix, rotation_deg)
    yy, xx = np.mgrid[0:ny, 0:nx]
    ra, dec = wcs.all_pix2world(xx, yy, 0)
    dx = (np.asarray(ra) - TARGET_RA + 180.0) % 360.0 - 180.0
    dx = dx * np.cos(np.deg2rad(TARGET_DEC)) * 3600.0
    dy = (np.asarray(dec) - TARGET_DEC) * 3600.0

    data = np.full((ny, nx), 1e-3, dtype=np.float64)
    for sx, sy, amplitude, sigma in SOURCES:
        data += amplitude * np.exp(-((dx - sx) ** 2 + (dy - sy) ** 2) / (2.0 * sigma ** 2))
    data += np.random.default_rng(7).normal(0.0, 1e-6, data.shape)

    header = wcs.to_header(relax=True)
    header["BUNIT"] = "MJy / sr"
    header["EXPTIME"] = 113.58
    header["DATE-OBS"] = "2026-09-24T06:36:54"
    header["TELESCOP"] = "SPHEREx"
    header["INSTRUME"] = "SPHEREx"

    flags = fits.ImageHDU(data=np.zeros((ny, nx), dtype=np.int32), name="FLAGS")
    flags.header["EXTTYPE"] = "MASK"
    variance = fits.ImageHDU(data=np.full((ny, nx), VARIANCE, dtype=np.float32), name="VARIANCE")
    variance.header["BUNIT"] = "MJy2 / sr2"

    hdus = [
        fits.PrimaryHDU(),
        fits.ImageHDU(data=np.zeros((ny, nx), dtype=np.float32), name="ZODI"),
        fits.ImageHDU(data=data.astype(np.float32), header=header, name="IMAGE"),
        flags,
        variance,
        _epsf(psf_fwhm_px),
    ]
    buffer = io.BytesIO()
    fits.HDUList(hdus).writeto(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------------ fake IRSA
class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.status_code = 200
        self.headers = {"Content-Type": "image/fits"}
        self.text = ""

    def iter_content(self, chunk_size: int = 65536):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def raise_for_status(self) -> None:
        pass

    def close(self) -> None:
        pass


class _Session:
    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = routes

    def get(self, url: str, **_: object) -> _Response:
        for marker, content in self.routes.items():
            if marker in url:
                return _Response(content)
        raise AssertionError(f"unexpected download URL: {url}")


def _row(obs_id: str, did: str, t_min: float) -> dict:
    return {
        "s_ra": TARGET_RA,
        "s_dec": TARGET_DEC,
        "facility_name": "SPHEREx",
        "instrument_name": "SPHEREx",
        "dataproduct_subtype": "science",
        "calib_level": 2,
        "dataproduct_type": "image",
        "energy_bandpassname": "SPHEREx-D3",
        "obs_id": obs_id,
        "access_url": f"https://irsa.ipac.caltech.edu/ibe/data/spherex/qr3/level2/{obs_id}.fits",
        "access_format": "image/fits",
        "access_estsize": 65978,
        "t_exptime": 113.5826,
        "obs_collection": "spherex_qr3",
        "obs_publisher_did": did,
        "s_xel1": 2040,
        "s_xel2": 2040,
        "s_pixel_scale": 6.15,
        "t_min": t_min,
        "t_max": t_min + 0.0013,
        "s_fov": 3.485,
        "em_min": 1.6e-6,
        "em_max": 2.2e-6,
        "pol_states": "I",
        "s_region": "CIRCLE 156.093 -41.645 1.7",
        "target_name": "TARGET",
    }


class _FakeClient:
    def __init__(self, rows: list[dict], session: _Session) -> None:
        self.rows = rows
        self.session = session

    def search(self, *_, **__) -> list[dict]:
        return self.rows

    def find_by_publisher_did(self, did: str, collection: str | None = None) -> dict | None:
        return next((row for row in self.rows if row["obs_publisher_did"] == did), None)

    def close(self) -> None:
        pass


def main() -> int:
    # The script is run as ``scripts/fresh_install_check.py``; make the backend
    # package importable without installing the project itself.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    storage = Path(tempfile.mkdtemp(prefix="planetx-fresh-"))
    os.environ["STORAGE_DIR"] = str(storage)
    os.environ.setdefault("LOG_LEVEL", "WARNING")

    # --- 2. import the shipped application -------------------------------
    try:
        import astropy
        import numpy as np
        import scipy
        from fastapi.testclient import TestClient

        from app.api.deps import get_cache_store, get_irsa_client, get_settings_dep
        from app.core.cache import CacheStore
        from app.core.config import Settings
        from app.main import app
        from app.services.image_inspection import pixel_scale_arcsec
        from app.services.image_reprojection import CommonGrid, reproject
        from app.services.observation_service import ObservationService, observation_token

        check(
            "backend imports",
            True,
            f"python {sys.version.split()[0]}, numpy {np.__version__}, "
            f"scipy {scipy.__version__}, astropy {astropy.__version__}",
        )
    except Exception:  # noqa: BLE001 - a failed import is the finding
        traceback.print_exc()
        check("backend imports", False)
        return 1

    # --- 7/8 fixtures + fake IRSA wiring ---------------------------------
    try:
        settings = Settings(
            storage_dir=storage,
            connect_timeout=2.0,
            request_timeout=5.0,
            download_retries=1,
            max_download_bytes=5 * 1024 * 1024,
        )
        settings.ensure_dirs()
        epoch_a = sky_fits(crpix=(32.5, 32.5), rotation_deg=0.0, psf_fwhm_px=2.0)
        epoch_b = sky_fits(crpix=(40.5, 24.5), rotation_deg=18.0, psf_fwhm_px=2.1)
        rows = [
            _row("EPOCH_A", "ivo://irsa.ipac/spherex_qr3?EPOCH_A/D3", 61177.275636),
            _row("EPOCH_B", "ivo://irsa.ipac/spherex_qr3?EPOCH_B/D3", 61211.084272),
        ]
        client = _FakeClient(rows, _Session({"EPOCH_A.fits": epoch_a, "EPOCH_B.fits": epoch_b}))
        cache = CacheStore(60, 32)
        observations = ObservationService(client, cache, settings)
        observations.search_rows(TARGET_RA, TARGET_DEC, 0.01)
        token_a = observation_token(rows[0])
        token_b = observation_token(rows[1])
        assert token_a and token_b

        app.dependency_overrides[get_irsa_client] = lambda: client
        app.dependency_overrides[get_cache_store] = lambda: cache
        app.dependency_overrides[get_settings_dep] = lambda: settings
        check("deterministic synthetic FITS fixtures", True, f"storage={storage}")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("deterministic synthetic FITS fixtures", False)
        return 1

    # --- 3/4/5/6. app starts, endpoints answer ---------------------------
    try:
        with TestClient(app) as http:
            root = http.get("/")
            check("root endpoint", root.status_code == 200 and "message" in root.json(),
                  root.json().get("message", ""))

            health = http.get("/api/v1/health")
            body = health.json()
            check("health endpoint", health.status_code == 200 and body.get("status") == "ok",
                  body.get("version", ""))

            schema = http.get("/openapi.json")
            paths = schema.json().get("paths", {})
            check(
                "OpenAPI schema",
                schema.status_code == 200 and "/api/v1/images/compare" in paths,
                f"{len(paths)} paths",
            )

            # --- 7. inspection -------------------------------------------
            inspected = http.post(
                "/api/v1/images/inspect",
                json={"observation_id": token_a, "ra": TARGET_RA, "dec": TARGET_DEC, "size": SIZE},
            )
            check("synthetic FITS inspection", inspected.status_code == 200, f"HTTP {inspected.status_code}")
            if inspected.status_code == 200:
                inspection = inspected.json()["inspection"]
                check("inspection reports WCS", inspection["wcs"]["available"] is True)
                check("inspection reports VARIANCE", inspection["variance"]["available"] is True)
                check("inspection reports PSF", inspection["psf"]["available"] is True,
                      str(inspection["psf"].get("fwhm_arcsec")))

            # --- 8/10. comparison, validity, PSF --------------------------
            compared = http.post(
                "/api/v1/images/compare",
                json={
                    "observation_id_a": token_a,
                    "observation_id_b": token_b,
                    "ra": TARGET_RA,
                    "dec": TARGET_DEC,
                    "size": SIZE,
                },
            )
            check("image comparison executes", compared.status_code == 200, f"HTTP {compared.status_code}")
            if compared.status_code == 200:
                result = compared.json()
                check("comparison completed", result["status"] == "completed")
                check("difference product written", result["difference"] is not None)
                check("scientifically_valid is derived and true", result["scientifically_valid"] is True,
                      f"overall={result['quality']['overall_status']}")
                check("all critical checks PASS",
                      all(item["status"] == "PASS" for item in result["quality"]["checks"] if item["critical"]),
                      ",".join(result["quality"]["failed_checks"] + result["quality"]["unverified_checks"]) or "none failing")
                psf = result["psf"]
                check("PSF verified and not falsely matched",
                      psf["status"] == "PASS" and psf["verified"] is True and psf["matched"] is False,
                      f"ratio={psf['ratio']}")
                check("PSF spatial-variation limit declared", psf["spatially_varying"] is False)
                uncertainty = result["difference"]["uncertainty_valid_fraction"]
                check("difference uncertainty propagated", result["difference"]["uncertainty_available"] is True,
                      f"valid fraction={uncertainty}")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("API integration", False)
    finally:
        app.dependency_overrides.clear()

    # --- 9. analytic variance propagation -------------------------------
    try:
        reference = _wcs(8, 8, (4.5, 4.5), 0.0)
        source = _wcs(8, 8, (4.75, 5.0), 0.0)  # pixel offset (dx, dy) = (0.25, 0.5)
        grid = CommonGrid(wcs=reference, shape=(8, 8), pixel_scale_arcsec=pixel_scale_arcsec(reference))
        constant = np.full((8, 8), 4.0)
        out = reproject(
            np.ones((8, 8)), np.ones((8, 8), dtype=bool), constant, source, grid
        )
        weights = ((1 - 0.25) * (1 - 0.5), 0.25 * (1 - 0.5), (1 - 0.25) * 0.5, 0.25 * 0.5)
        expected = 4.0 * sum(w * w for w in weights)  # 1.25, not the scalar-interpolated 4.0
        got = float(out.variance[3, 3])
        check(
            "variance propagation is squared-weight",
            abs(got - expected) < 1e-6 and abs(got - 4.0) > 0.1,
            f"V_out={got:.6f}, expected={expected:.6f}, scalar interpolation would give 4.0",
        )
        check("science/variance alignment", out.variance.shape == out.data.shape == (8, 8))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("variance propagation is squared-weight", False)

    # --- cleanup --------------------------------------------------------
    import shutil

    shutil.rmtree(storage, ignore_errors=True)

    if failures:
        print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("\nAll fresh-install checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
