# Planet X — SPHEREx Data Acquisition Backend

Backend for an interactive SPHEREx time-domain sky explorer. It retrieves **genuine** SPHEREx
observations from NASA/IPAC IRSA, downloads real spatial cutouts, extracts the science
`IMAGE` extension, validates celestial WCS, renders PNG previews, discovers multi-epoch
timelines, inspects FITS products and compares two epochs on a common celestial grid —
all through a small JSON API.

No mock observations, no synthetic FITS, no hardcoded observation metadata.

> Scope note: **Phase 1 multi-epoch discovery** (`GET /api/v1/targets/timeline`),
> **Phase 2 FITS inspection + pairwise image comparison** (`POST /api/v1/images/inspect`,
> `POST /api/v1/images/compare`, `GET /api/v1/images/comparisons/{id}`) and
> **Phase 5 Gaia DR3 catalog integration** (`GET /api/v1/catalogs/gaia/search`,
> `POST /api/v1/catalogs/gaia/crossmatch`, `GET /api/v1/catalogs/gaia/{source_id}`,
> see `docs/GAIA_INTEGRATION.md`) are implemented. Light curves, variability detection
> and spectral extraction remain **out of scope** (Phase 3/4).

---

## 1. Verified data source behaviour

All of the following was verified live against IRSA on 2026-11-14:

| Item | Verified behaviour |
|---|---|
| SIA2 endpoint | `https://irsa.ipac.caltech.edu/SIA` |
| Required parameter | `POS=CIRCLE <ra> <dec> <radius_deg>` (`SIZE` is SIA1 and is **not** accepted) |
| Collection filter | `COLLECTION=<name>` (e.g. `spherex_qr3`) |
| Date filter | `TIME=<mjd_start> <mjd_end>` |
| Detail lookup | `ID=<obs_publisher_did>`; slow (~20 s), cached in-process |
| VOTable quirk | `FIELD` carries both `name` and `ID="col_N"`; Astropy names columns `col_N`, so the backend rebuilds the mapping |
| FITS URL | SIA `access_url` is a direct IBE FITS URL — never constructed from an id |
| Cutout API | append `?center=<ra>,<dec>&size=<deg>deg` to the FITS URL |
| Cutout reliability | IBE intermittently answers `503 Server busy, retry later.` — retried with backoff |
| Validated target | RA 156.09328159, Dec −41.64466331, collection `spherex_qr3`, radius 0.01° → **126 SIA rows** |
| Observation identity | 126 rows = **63 unique `obs_id`** = 126 unique `obs_publisher_did`; each `obs_id` has 2 band products sharing `t_min`/`t_max` |
| Timeline | 63 epochs over a **33.81-day** baseline (2026-05-17 → 2026-06-20); 125 products target-covered, 1 footprint intersects but excludes the target |
| Timestamps | SIA2 `t_min`/`t_max` are MJD UTC; FITS `MJD-BEG`/`MJD-END` match and `MJD-AVG` equals their midpoint (`TIMESYS=UTC`) |
| Cutout (0.1°) | 4,083,840 bytes, 7 HDUs (`PRIMARY`, `IMAGE`, `FLAGS`, `VARIANCE`, `ZODI`, `EPSF`, `WCS-WAVE`) |
| Science image | `IMAGE`, 58 × 59 px (`NAXIS1=58`, `NAXIS2=59`), `BUNIT = MJy / sr` |
| WCS | target lands inside the cutout near the centre (e.g. x≈28.68, y≈28.80 for the product tested) |

The 126-row count, dates, dimensions and pixel positions are **runtime results**, not
constants. The live test re-queries IRSA and asserts only that genuine metadata is returned.

---

## 2. Architecture

```
app/
  main.py                  FastAPI app, exception handlers, CORS, lifespan
  core/
    config.py              Settings from environment / .env (pydantic-settings)
    exceptions.py          Typed errors → HTTP status + error code
    validation.py          RA/Dec/radius/size/date validation, MJD ↔ ISO
    cache.py               Thread-safe TTL caches
    logging.py             key=value structured logging
  schemas/
    common.py              Target, ErrorResponse
    observation.py         Search/detail request + response models
    cutout.py              Cutout request/response + WCS info
    timeline.py            Multi-epoch timeline response models
    image_processing.py    Inspection + comparison request/response models
  services/
    irsa_client.py         SIA2 HTTP, VOTable parsing, ID lookup, retries/timeouts
    observation_service.py Mapping SIA rows → API models, caching, orchestration
    timeline_service.py    Normalization, dedupe, epoch grouping, quality summary
    fits_service.py        FITS product validation, streamed download, HDU extraction
    cutout_service.py      IBE cutout URL construction, hash-keyed disk cache, locks
    wcs_service.py         Celestial WCS + target-in-image validation
    preview_service.py     Percentile + asinh PNG rendering (display only)
    image_inspection.py    HDU/WCS/mask/variance/PSF extraction, image statistics
    image_reprojection.py  Common-grid resampling + phase-correlation alignment check
    image_comparison.py    Prerequisite checks, difference imaging, quality, provenance
  api/
    deps.py                Singletons: settings, HTTP client, cache store, service
    observation_routes.py  /observations/*
    timeline_routes.py     /targets/timeline
    image_routes.py        /images/inspect, /images/compare, /images/comparisons/*
    file_routes.py         /files/* (hash-name allowlist, no traversal)
tests/
  test_*.py                Deterministic unit/API tests (no network)
  test_live_integration.py Live IRSA test, opt-in
  test_live_timeline.py    Live multi-epoch invariants, opt-in
  test_live_image_comparison.py Live inspection/comparison, opt-in
storage/
  fits/                    Downloaded cutouts + generated FITS, <sha256>.fits
  previews/                Rendered previews, <sha256>.png
  comparisons/             Stored comparison records, <comparison_id>.json
```

Data flow:

```
POST /observations/search
  → validate input → SIA2 cone search → VOTable → SIA rows → metadata models
  → rows cached (TTL) under an opaque observation_id per SIA row

POST /observations/{id}/cutout
  → resolve row (cache → SIA ID lookup) → validate access_url (https + IRSA host)
  → append center/size → stream download (size cap, retries, FITS magic)
  → extract IMAGE by EXTNAME → WCS target check → render PNG
  → return /api/v1/files/<hash>.fits and /api/v1/files/<hash>.png

GET /api/v1/files/<hash>.{fits,png}
  → filename allowlist → containment check under storage/ → FileResponse

GET /api/v1/targets/timeline
  → validate target/filters → cached SIA cone search (same rows as /search)
  → normalize products → dedupe → group by obs_id → sort epochs
  → quality/warnings summary → JSON timeline

POST /api/v1/images/inspect
  → resolve product → cutout (shared target/size) or full FITS
  → identify IMAGE/FLAGS/VARIANCE/EPSF by EXTNAME → validate WCS
  → statistics, mask bits, PSF FWHM, quality warnings → JSON inspection

POST /api/v1/images/compare
  → resolve both products → shared cutouts (same target/size)
  → validate FITS/WCS/coverage/alignment/units/PSF prerequisites
  → reproject onto the reference product's grid (masked, non-flux-conserving)
  → background + optional photometric scale → D = I_b - alpha*I_a
  → write difference/uncertainty/mask FITS + PNG → quality + provenance
  → persist /api/v1/images/comparisons/<id>
```

Each SIA row is a **product**, not an independent observation. SPHEREx returns one row
per band (`SPHEREx-D3`, `SPHEREx-D6`, ...) for the same `obs_id`, so `obs_id` is **not**
unique across products; `obs_publisher_did` is. The API therefore exposes an opaque,
URL-safe `observation_id` derived from `obs_publisher_did` (base64url). **Treat it as
opaque and pass it back unchanged.**

The timeline endpoint groups sibling band products by `obs_id` and counts **epochs**,
not rows: for the validated target, 126 product rows collapse to **63 observations /
63 epochs** (two bands each).

---

## 3. Setup

Requires **Python 3.14** (pinned in `pyproject.toml`; the suite and the fresh-install
check were run on CPython 3.14.10) and network access to `irsa.ipac.caltech.edu`.

### Dependency management (authoritative: `uv`)

`pyproject.toml` is the single source of truth; `uv.lock` is the real lockfile and
`requirements.txt` is a generated pip-compatible export. Do not edit `requirements.txt`
or `uv.lock` by hand.

| File | Role |
|---|---|
| `pyproject.toml` | Authoritative dependencies, exact `==` pins, `requires-python`, dev group |
| `uv.lock` | Lockfile resolved from `pyproject.toml` (48 packages, all transitive deps) |
| `requirements.txt` | Generated `uv export` with hashes, for `pip install -r` |

Direct pins: `fastapi 0.141.1`, `starlette 1.6.0`, `pydantic 2.13.5`,
`pydantic-settings 2.15.0`, `uvicorn[standard] 0.52.4`, `requests 2.33.1`,
`urllib3 2.8.0`, `astropy 8.0.2`, `numpy 2.5.2`, `scipy 1.18.0`,
`matplotlib 3.11.1`, `pillow 12.3.0`. Dev group: `pytest 9.1.1`, `httpx 0.28.1`,
`pyflakes 4.1.0`. The scientific pins are exact because they determine numerical
behaviour; upgrading them requires re-running the full suite and the live checks.

```bash
cd backend
uv sync                       # create .venv and install the locked dependency set
cp .env.example .env          # optional
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

To regenerate the lock and the pip export after changing `pyproject.toml`:

```bash
uv lock
uv export --format requirements-txt --no-dev --no-emit-project --output-file requirements.txt
```

`uv sync --frozen` installs exactly `uv.lock` without re-resolving, and is what the
fresh-install check uses. With plain `pip`:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # hashes present; add --require-hashes to enforce
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> `backend/main.py` is a legacy placeholder kept for history; the application entry
> point is `app.main:app` and the project metadata lives in `backend/pyproject.toml`.

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SIA_ENDPOINT` | `https://irsa.ipac.caltech.edu/SIA` | IRSA SIA2 service |
| `DEFAULT_COLLECTION` | `spherex_qr3` | Collection used when the request omits one |
| `TRUSTED_HOSTS` | `["irsa.ipac.caltech.edu"]` | Allowed hosts for FITS URLs (JSON tuple) |
| `STORAGE_DIR` | `storage` | Parent directory for FITS + previews |
| `API_PREFIX` | `/api/v1` | Route prefix |
| `CONNECT_TIMEOUT` | `10` | HTTP connect timeout (s) |
| `REQUEST_TIMEOUT` | `120` | HTTP read timeout (s) |
| `DOWNLOAD_RETRIES` | `3` | Retries for transient 429/5xx cutout responses |
| `MAX_DOWNLOAD_BYTES` | `268435456` | Hard download cap (256 MiB) |
| `MAX_SEARCH_RADIUS_DEG` | `1.0` | Maximum cone radius |
| `MAX_CUTOUT_SIZE_DEG` | `1.0` | Maximum cutout size |
| `CACHE_TTL_SECONDS` | `600` | In-process SIA cache TTL |
| `CACHE_MAX_ENTRIES` | `512` | Cache size bound |
| `PREVIEW_CMAP` | `inferno` | Matplotlib colormap |
| `PREVIEW_PERCENTILE` | `99.5` | Upper percentile for display scaling |
| `PREVIEW_ASINH_A` | `0.1` | `AsinhStretch` parameter |
| `COMPARISON_SIZE_DEG` | `0.2` | Default shared cutout size for `/images/compare` |
| `MAX_COMPARISON_PIXELS` | `1000000` | Reject pairs whose combined science pixels exceed this |
| `MIN_COMMON_COVERAGE` | `0.3` | Minimum common valid fraction of the output grid |
| `MAX_ALIGNMENT_RESIDUAL_PX` | `1.5` | Residual misalignment that fails the alignment check |
| `PSF_WARN_RATIO` | `1.15` | PSF FWHM ratio above which compatibility is a warning |
| `PSF_FAIL_RATIO` | `1.4` | PSF FWHM ratio above which direct subtraction is refused |
| `MAX_CONCURRENT_COMPARISONS` | `2` | Bounded concurrency for comparison processing |
| `LOG_LEVEL` | `INFO` | Log level |
| `CORS_ORIGINS` | `["http://localhost:3000","http://127.0.0.1:3000","http://localhost:5173","http://127.0.0.1:5173"]` | Explicit browser origins (JSON tuple). `"*"` is rejected at startup. |

Tuple-valued variables use JSON syntax in `.env`.

---

## 4. API

Base URL: `http://localhost:8000/api/v1`

All successful `/api/v1` payloads contain `"success": true`. All failures use the same envelope:

```json
{
  "success": false,
  "error": {
    "code": "IRSA_TIMEOUT",
    "message": "IRSA SIA request timed out",
    "details": { "endpoint": "https://irsa.ipac.caltech.edu/SIA" }
  }
}
```

### 4.1 `POST /observations/search`

Finds genuine observations around a position.

Request (all fields except `ra`/`dec` optional):

```json
{
  "ra": 156.09328159,
  "dec": -41.64466331,
  "radius": 0.01,
  "collection": "spherex_qr3",
  "start_date": null,
  "end_date": null
}
```

- `ra`: 0 ≤ ra < 360; `dec`: −90…90; `radius`: > 0, ≤ 1°.
- `start_date` / `end_date`: ISO 8601 date or datetime, UTC, inclusive.
- Unknown JSON fields are rejected with `422`.

Response (abridged; a real search returned 126 rows):

```json
{
  "success": true,
  "target": { "ra": 156.09328159, "dec": -41.64466331 },
  "collection": "spherex_qr3",
  "total_observations": 126,
  "observations": [
    {
      "observation_id": "aXZvOi8vaXJzYS5pcGFjL3NwaGVyZXhfcXIzPzIwMjZXMjBfMkFfMDQzNl8xL0Qz",
      "obs_id": "2026W20_2A_0436_1",
      "obs_publisher_did": "ivo://irsa.ipac/spherex_qr3?2026W20_2A_0436_1/D3",
      "observation_date": "2026-05-17T06:32:37.132800+00:00",
      "observation_date_mjd": 61177.272652,
      "instrument": "SPHEREx",
      "facility": "SPHEREx",
      "collection": "spherex_qr3",
      "band": "SPHEREx-D3",
      "product_type": "image",
      "product_subtype": "science",
      "calib_level": 2,
      "fits_available": true
    }
  ]
}
```

Zero matches are **success** with `total_observations: 0` and `observations: []`
(a 200 without a table is not an API failure).

### 4.2 `GET /observations/{observation_id}`

Resolves metadata from the search cache, or re-queries SIA by `obs_publisher_did`
on a cache miss (slow first call, ~20 s).

Response:

```json
{
  "success": true,
  "observation": {
    "observation_id": "aXZvOi8vaXJzYS5pcGFjL3NwaGVyZXhfcXIzPzIwMjZXMjBfMkFfMDQzNl8xL0Qz",
    "obs_id": "2026W20_2A_0436_1",
    "obs_publisher_did": "ivo://irsa.ipac/spherex_qr3?2026W20_2A_0436_1/D3",
    "observation_date": "2026-05-17T06:32:37.132800+00:00",
    "observation_date_mjd": 61177.272652,
    "instrument": "SPHEREx",
    "facility": "SPHEREx",
    "collection": "spherex_qr3",
    "band": "SPHEREx-D3",
    "product_type": "image",
    "product_subtype": "science",
    "calib_level": 2,
    "fits_available": true,
    "s_ra": 156.25964649,
    "s_dec": -41.83597626,
    "t_min_mjd": 61177.272652,
    "t_max_mjd": 61177.273984,
    "start_time": "2026-05-17T06:32:37.132800+00:00",
    "end_time": "2026-05-17T06:32:52.217600+00:00",
    "exposure_time_seconds": 113.5826,
    "access_format": "image/fits",
    "access_estsize_bytes": 67565824,
    "width": 2040,
    "height": 2040,
    "pixel_scale_arcsec": 6.15,
    "fov_deg": 3.485,
    "fits_access_url": "https://irsa.ipac.caltech.edu/ibe/data/.../level2_...fits",
    "processing_status": "ready"
  }
}
```

`processing_status` is `ready` when a trusted FITS URL exists, else `no_fits_product`.
`fits_access_url` is informational provenance. **The frontend must not fetch it**;
all retrieval goes through the cutout endpoint.

### 4.3 `POST /observations/{observation_id}/cutout`

Request:

```json
{ "ra": 156.09328159, "dec": -41.64466331, "size": 0.1 }
```

Response (values generated from the actual FITS file):

```json
{
  "success": true,
  "observation_id": "aXZvOi8vaXJzYS5pcGFjL3NwaGVyZXhfcXIzPzIwMjZXMjBfMkFfMDQzNl8xL0Qz",
  "cutout": {
    "fits_url": "/api/v1/files/8d5c6495...b681.fits",
    "preview_url": "/api/v1/files/8d5c6495...b681.png",
    "width": 58,
    "height": 59,
    "unit": "MJy / sr"
  },
  "wcs": {
    "available": true,
    "target_inside_image": true,
    "pixel_x": 28.6825,
    "pixel_y": 28.8042,
    "message": null
  }
}
```

- `width`/`height` are `NAXIS1`/`NAXIS2` of the science image.
- `unit` is `BUNIT`; `null` when absent.
- `wcs.available=false` means the product has no celestial WCS.
- `target_inside_image` may be `null` when no reliable pixel transform exists.
- A target outside the cutout is **not** an error; inspect `target_inside_image`.
- `pixel_x`/`pixel_y` are 0-based FITS pixel coordinates.
- `fits_url`/`preview_url` are absolute **paths** rooted at the API origin, e.g.
  `/api/v1/files/<hash>.png`. Resolve them against the API origin, not the frontend
  origin: `new URL(cutout.preview_url, "http://localhost:8000")`.

Cutout retrieval retries transient IRSA `503` responses. Persistent failures return
`503 IRSA_CUTOUT_UNAVAILABLE`.

### 4.4 `GET /files/{filename}`

Serves only files generated by this backend (`<64-hex>.fits` / `<64-hex>.png`).
Any other name, traversal attempt or missing file returns `404 FILE_NOT_FOUND`.
FITS → `image/fits`, PNG → `image/png`, both with `Cache-Control: public, max-age=3600`
and the same CORS headers as the JSON endpoints (so `<img src>` works cross-origin).

### 4.5 `GET /` (frontend connectivity)

Compatibility endpoint for AstroChronoscope's connectivity check. It sits outside the
`/api/v1` prefix because the frontend fetches the API origin root.

```json
{ "message": "Planet X SPHEREx API is running" }
```

Returns `200` with CORS headers. `/api/v1/health` remains the detailed health endpoint
for existing clients.

### 4.6 `GET /health`

```json
{ "success": true, "status": "ok", "version": "0.1.0", "default_collection": "spherex_qr3" }
```

### 4.7 `GET /targets/timeline`

Phase 1 multi-epoch discovery. Accepts a target and returns chronologically ordered
epochs with normalized products, provenance, spatial relevance and metadata-quality
indicators. It reuses the same cached SIA2 search as `/observations/search`.

Query parameters:

| Name | Required | Meaning |
|---|---|---|
| `ra` | yes | ICRS right ascension, 0 ≤ ra < 360 |
| `dec` | yes | ICRS declination, −90…90 |
| `radius` | no | Search radius in degrees, default `0.01`, ≤ configured max (1°) |
| `collection` | no | IRSA collection; defaults to `spherex_qr3` |
| `bands` | no | Repeatable exact band filter, e.g. `?bands=SPHEREx-D3&bands=SPHEREx-D6` |
| `start_date` / `end_date` | no | ISO 8601 UTC bounds; forwarded as SIA2 `TIME` and overlap-filtered locally |

```bash
curl -s 'http://localhost:8000/api/v1/targets/timeline?ra=156.09328159&dec=-41.64466331&radius=0.01&collection=spherex_qr3'
```

Response (abridged; values from the live 2026-11-14 run):

```json
{
  "success": true,
  "target": { "ra": 156.09328159, "dec": -41.64466331, "frame": "ICRS" },
  "query": {
    "radius_deg": 0.01,
    "collection": "spherex_qr3",
    "bands": null,
    "start_date": null,
    "end_date": null
  },
  "total_product_rows": 126,
  "total_products": 126,
  "unique_observations": 63,
  "unique_epochs": 63,
  "earliest_observation_time": "2026-05-17T06:33:34.675200+00:00",
  "latest_observation_time": "2026-06-20T01:57:54.604800+00:00",
  "temporal_baseline_days": 33.8086,
  "available_bands": ["SPHEREx-D1", "SPHEREx-D2", "SPHEREx-D3", "SPHEREx-D4", "SPHEREx-D5", "SPHEREx-D6"],
  "band_counts": { "SPHEREx-D1": 25, "SPHEREx-D2": 19, "SPHEREx-D3": 19, "SPHEREx-D4": 25, "SPHEREx-D5": 19, "SPHEREx-D6": 19 },
  "epochs": [
    {
      "epoch_index": 0,
      "epoch_key": "2026W20_2A_0436_1",
      "obs_id": "2026W20_2A_0436_1",
      "identified": true,
      "observation_time": "2026-05-17T06:33:34.675200+00:00",
      "start_time": "2026-05-17T06:32:37.132800+00:00",
      "end_time": "2026-05-17T06:34:32.217600+00:00",
      "midpoint_mjd": 61177.273318,
      "duration_seconds": 115.0848,
      "timeline_eligible": true,
      "bands": ["SPHEREx-D3", "SPHEREx-D6"],
      "product_count": 2,
      "target_covered": true,
      "target_covered_products": 2,
      "footprint_center_ra": 156.50602926,
      "footprint_center_dec": -41.48915383,
      "footprint_fov_deg": 3.485,
      "facility": "SPHEREx",
      "instrument": "SPHEREx",
      "collection": "spherex_qr3",
      "warnings": [],
      "products": [
        {
          "observation_id": "aXZvOi8vaXJzYS5pcGFjL3NwaGVyZXhfcXIzPzIwMjZXMjBfMkFfMDQzNl8xL0Qz",
          "obs_publisher_did": "ivo://irsa.ipac/spherex_qr3?2026W20_2A_0436_1/D3",
          "obs_id": "2026W20_2A_0436_1",
          "band": "SPHEREx-D3",
          "em_min_m": 1.6301e-06,
          "em_max_m": 2.4346e-06,
          "t_min_mjd": 61177.272652,
          "t_max_mjd": 61177.273984,
          "start_time": "2026-05-17T06:32:37.132800+00:00",
          "end_time": "2026-05-17T06:34:32.217600+00:00",
          "observation_time": "2026-05-17T06:33:34.675200+00:00",
          "timeline_eligible": true,
          "exposure_time_seconds": 113.5826,
          "calib_level": 2,
          "dataproduct_type": "image",
          "dataproduct_subtype": "science",
          "target_name": "SPS_130424_S34",
          "fits_available": true,
          "fits_access_url": "https://irsa.ipac.caltech.edu/ibe/data/.../level2_...fits",
          "access_url_trusted": true,
          "processing_status": "ready",
          "s_ra": 156.50602926,
          "s_dec": -41.48915383,
          "fov_deg": 3.485,
          "width": 2040,
          "height": 2040,
          "pixel_scale_arcsec": 6.15,
          "target_covered": true,
          "angular_distance_deg": 0.3458,
          "metadata_complete": true,
          "missing_fields": [],
          "warnings": []
        }
      ]
    }
  ],
  "quality": {
    "total_product_rows": 126,
    "products_included": 126,
    "duplicate_products_removed": 0,
    "products_with_valid_timestamps": 126,
    "products_without_valid_timestamps": 0,
    "products_with_band": 126,
    "products_without_band": 0,
    "products_with_spatial_metadata": 126,
    "products_without_spatial_metadata": 0,
    "products_target_covered": 125,
    "products_target_outside": 1,
    "products_target_coverage_unknown": 0,
    "products_missing_identifier": 0,
    "inconsistent_epochs": 0,
    "metadata_complete_products": 126,
    "metadata_incomplete_products": 0
  },
  "warnings": [
    { "code": "target_outside_footprint", "message": "Target is outside this product's s_region footprint", "count": 1 }
  ],
  "metadata": {
    "time_standard": "UTC",
    "time_format": "ISO 8601",
    "timestamp_source": "IRSA SIA2 t_min/t_max (MJD, UTC)",
    "observation_time_definition": "Arithmetic midpoint of t_min and t_max",
    "epoch_grouping": "One epoch per unique obs_id; no arbitrary time-threshold grouping",
    "frame": "ICRS",
    "comparability_assessed": false,
    "comparability_note": "Discovery and timeline eligibility only. Image subtraction, photometry and cross-band comparability are Phase 2/3 work and are not asserted here."
  }
}
```

Semantics:

- **Product ≠ observation.** `obs_publisher_did` identifies a product row; `obs_id`
  identifies the observation/exposure. Sibling bands share `obs_id` and the same
  `t_min`/`t_max`, so they form one epoch.
- **Epoch grouping is not a time threshold.** One epoch = one unique `obs_id`.
  No documented visit/sequence grouping exists in SIA2, so consecutive exposures are
  deliberately kept separate. If `obs_id` is absent, the product is its own epoch
  (`identified: false`) rather than being merged by guesswork.
- **Timestamps.** `t_min`/`t_max` are SIA2 MJD (UTC). `observation_time` is their
  arithmetic midpoint. FITS headers for the same product confirm `MJD-BEG = t_min`,
  `MJD-END = t_max`, `MJD-AVG ≈ midpoint`, `TIMESYS = UTC`. Missing/invalid values
  remain `null` and are counted; they are never invented.
- **Spatial relevance.** SIA2 returns footprints that intersect the search cone.
  `target_covered` is computed from the product's `s_region` (`POLYGON`/`CIRCLE`),
  not from the cone. `null` means unknown (missing/unparseable region), not outside.
  The live run found 125 covered products and 1 footprint that intersects the cone but
  does not contain the target.
- **Bands.** `band` is the original `energy_bandpassname`. No mapping is invented.
  `em_min_m`/`em_max_m` are SIA2 values in metres.
- **Quality.** `quality` separates discovery/timeline eligibility from comparability.
  `metadata_complete` means identifier + band + both timestamps + spatial metadata are
  present. `warnings` aggregate per-product and per-epoch issues with counts.
- **Comparability is not asserted.** `metadata.comparability_assessed` is `false`.
  Multiple epochs do not imply image subtraction or photometry is valid.
- **Empty results are success:** `total_product_rows: 0`, `epochs: []`, and an
  `empty_result` warning.
- **Caching.** Raw rows are shared with `/observations/search`. Timeline responses are
  cached separately per `(ra, dec, radius, collection, bands, start_date, end_date)`,
  so band/date filters never collide.

### 4.8 `POST /images/inspect`

Phase 2 FITS inspection. Returns the HDUs, science-image statistics, WCS validation,
mask/uncertainty availability and EPSF PSF metrics for one product. Nothing is
inferred: missing metadata is reported as `null` plus a machine-readable warning.

Body:

| Field | Type | Required | Rules |
|---|---|---|---|
| `observation_id` | string | yes | an id from `/observations/search` or `/targets/timeline` |
| `ra` | number \| null | no | ICRS degrees; must be sent together with `dec` |
| `dec` | number \| null | no | ICRS degrees |
| `size` | number \| null | no | cutout size in degrees; when `ra`/`dec` are given the service inspects a cutout, otherwise the full product |

```bash
curl -s -X POST http://localhost:8000/api/v1/images/inspect \
  -H 'Content-Type: application/json' \
  -d '{"observation_id":"<id>","ra":156.09328159,"dec":-41.64466331,"size":0.2}'
```

Response (abridged; a synthetic 64×64 fixture, structure is what matters):

```json
{
  "success": true,
  "inspection": {
    "source": "cutout",
    "source_url": "https://irsa.ipac.caltech.edu/...",
    "fits_sha256": "…64 hex…",
    "science_extname": "IMAGE",
    "hdus": [
      {"index": 0, "name": "PRIMARY", "kind": "primary", "shape": null, "dtype": null, "unit": null},
      {"index": 1, "name": "IMAGE", "kind": "image", "shape": [59, 58], "dtype": "float32", "unit": "MJy / sr"},
      {"index": 2, "name": "FLAGS", "kind": "image", "shape": [59, 58], "dtype": "int32", "unit": null},
      {"index": 5, "name": "EPSF", "kind": "table", "shape": null, "dtype": null, "unit": null}
    ],
    "stats": {
      "shape": [59, 58], "dtype": "float32", "bunit": "MJy / sr",
      "finite_pixels": 3422, "nan_pixels": 0, "inf_pixels": 0, "finite_fraction": 1.0,
      "valid_pixels": 3400, "valid_fraction": 0.9936,
      "min": -1.2e-05, "max": 0.031, "mean": 0.00042, "median": 0.00039, "std": 0.0021
    },
    "wcs": {
      "available": true, "ctype": ["RA---TAN-SIP", "DEC--TAN-SIP"], "projection": "TAN",
      "has_sip": true, "crval_ra_deg": 156.50516929, "crval_dec_deg": -41.4914055863,
      "crpix": [145.5, -140.5], "pixel_scale_arcsec": 6.15, "orientation_deg": -118.3,
      "footprint": [[156.6, -41.4], [156.5, -41.4], [156.5, -41.5], [156.6, -41.5]],
      "center_ra_deg": 156.0933, "center_dec_deg": -41.6447,
      "target_inside_image": true, "target_pixel": [28.68, 28.80], "message": null
    },
    "mask": {
      "available": true, "extname": "FLAGS", "invalid_pixels": 22, "invalid_fraction": 0.0064,
      "policy": "any non-zero FLAGS bit marks the pixel invalid, except informational bits (SOURCE, FULLSAMPLE)",
      "bits": {"TRANSIENT": "Transient detected during SUR", "OVERFLOW": "Overflow detected during SUR"}
    },
    "variance": {"available": true, "extname": "VARIANCE", "unit": "MJy2 / sr2", "finite_pixels": 3422, "min": 1e-10, "max": 4e-07},
    "psf": {
      "available": true, "extname": "EPSF", "entries": 451, "oversampling": 5,
      "fwhm_samples": 10.0, "fwhm_px": 2.0, "fwhm_arcsec": 12.3,
      "wavelength_min_um": 1.6238, "wavelength_max_um": 2.4259, "stars_median": 704, "message": null
    },
    "metadata": {
      "observation_id": "<id>", "obs_publisher_did": "ivo://irsa.ipac/spherex_qr3?…/D3",
      "obs_id": "2026W20_2A_0436_1", "band": "SPHEREx-D3",
      "date_obs": "2026-05-17T06:32:37.132800", "mjd_obs": 61177.272652,
      "date_avg": null, "mjd_avg": null, "timesys": "UTC",
      "exposure_time_seconds": 113.58, "telescope": "SPHEREx", "instrument": "SPHEREx",
      "software_version": "7.0.5"
    },
    "quality": "PASS",
    "warnings": []
  }
}
```

Warning codes: `missing_wcs`, `all_pixels_invalid`, `non_finite_pixels`,
`no_flags_extension`, `no_variance_extension`, `no_psf_extension`,
`psf_scale_unknown`, `target_outside_image`, `no_bunit`. `quality` is `FAIL` only when
the science image has no finite pixels; any warning makes it `WARNING`.

### 4.9 `POST /images/compare`

Phase 2 multi-epoch comparison. Both products are downloaded as **cutouts at the same
target and size** (default `0.2°`), reprojected onto the reference product's grid and
subtracted only after the prerequisites pass.

Body:

| Field | Type | Required | Rules |
|---|---|---|---|
| `observation_id_a` | string | yes | first product (default reference grid) |
| `observation_id_b` | string | yes | second product |
| `ra`, `dec` | number | yes | shared cutout centre, ICRS degrees |
| `size` | number | no (default `0.2`) | shared cutout size, ≤ `MAX_CUTOUT_SIZE_DEG` |
| `config.background_correction` | `"median"` \| `"none"` | no (default `median`) | sigma-clipped median per image |
| `config.photometric_scaling` | `"none"` \| `"median_ratio"` | no (default `none`) | `alpha = 1` by default |
| `config.psf_matching` | `"none"` \| `"gaussian"` | no (default `none`) | Gaussian approximation of the EPSF |
| `config.reference` | `"first"` \| `"second"` | no (default `first`) | which product defines the common grid |
| `config.interpolation` | `"bilinear"` \| `"nearest"` | no (default `bilinear`) | resampling kernel |

```bash
curl -s -X POST http://localhost:8000/api/v1/images/compare \
  -H 'Content-Type: application/json' \
  -d '{"observation_id_a":"<id-A>","observation_id_b":"<id-B>",
       "ra":156.09328159,"dec":-41.64466331,"size":0.2,
       "config":{"background_correction":"median","photometric_scaling":"none"}}'
```

Response `200`, successful comparison (abridged):

```json
{
  "success": true,
  "comparison_id": "61b6f48041f58fc2f70c513040785214",
  "status": "completed",
  "scientifically_valid": true,
  "comparability_assessed": true,
  "target": {"ra": 156.09328159, "dec": -41.64466331},
  "config": {"background_correction": "median", "photometric_scaling": "none",
             "psf_matching": "none", "reference": "first", "interpolation": "bilinear"},
  "inputs": [
    {"observation_id": "<id-A>", "obs_id": "2026W20_2A_0436_1", "band": "SPHEREx-D3",
     "bunit": "MJy / sr", "shape": [64, 64], "pixel_scale_arcsec": 6.15,
     "fwhm_arcsec": 12.3, "fits_sha256": "…", "source": "cutout"},
    {"observation_id": "<id-B>", "obs_id": "2026W20_2A_0497_1", "band": "SPHEREx-D3",
     "bunit": "MJy / sr", "shape": [64, 64], "pixel_scale_arcsec": 6.15,
     "fwhm_arcsec": 12.3, "fits_sha256": "…", "source": "cutout"}
  ],
  "reprojection": {
    "method": "bilinear", "reference": "first", "output_shape": [64, 64],
    "pixel_scale_arcsec": 6.15, "valid_fraction_a": 1.0, "valid_fraction_b": 0.78,
    "common_valid_pixels": 3194, "common_coverage_fraction": 0.78, "overlap_fraction": 1.0,
    "flux_conserving": false,
    "note": "Interpolation of calibrated surface brightness on the reference pixel grid; not flux-conserving for aperture photometry. Invalid pixels are never zero-filled."
  },
  "alignment": {
    "target_pixel_a": [31.5, 31.5], "target_pixel_b": [39.5, 23.5],
    "pixel_scale_ratio": 1.0, "orientation_difference_deg": 18.0,
    "residual_dx_px": 0.12, "residual_dy_px": -0.06, "residual_offset_px": 0.13,
    "residual_offset_arcsec": 0.82, "peak_confidence": 25.35,
    "verified": true, "status": "PASS", "message": "Residual misalignment 0.13 px after reprojection"
  },
  "backgrounds": [
    {"method": "median", "level": 0.001, "sigma": 2e-05, "mesh_variation": 3e-06, "reliable": true, "applied": true, "note": "…"},
    {"method": "median", "level": 0.001, "sigma": 2e-05, "mesh_variation": 4e-06, "reliable": true, "applied": true, "note": "…"}
  ],
  "photometric": {
    "bunit_a": "MJy / sr", "bunit_b": "MJy / sr", "units_compatible": true,
    "scale_factor": 1.0, "scaling_method": "none", "absolute_calibration": true,
    "note": "Calibrated surface-brightness units; alpha = 1 by construction (no data-derived scaling)"
  },
  "psf": {
    "fwhm_a_arcsec": 12.3, "fwhm_b_arcsec": 12.3, "ratio": 1.0,
    "metadata_available_a": true, "metadata_available_b": true,
    "verified": true, "matching": "none", "matched": false,
    "matching_supported": false, "spatially_varying": false,
    "method": null, "sigma_px": null, "status": "PASS",
    "note": "PSF FWHM ratio 1.00; compatible within the warning threshold, no matching required"
  },
  "difference": {
    "fits_url": "/api/v1/files/<64-hex>.fits",
    "preview_url": "/api/v1/files/<64-hex>.png",
    "uncertainty_url": "/api/v1/files/<64-hex>.fits",
    "mask_url": "/api/v1/files/<64-hex>.fits",
    "width": 64, "height": 64, "unit": "MJy / sr",
    "uncertainty_available": true,
    "uncertainty_valid_fraction": 1.0,
    "uncertainty_note": "Propagated 1-sigma uncertainty from both VARIANCE extensions (squared interpolation weights, independent per-pixel errors; covariance not modelled)",
    "statistics": {"valid_pixels": 3194, "valid_fraction": 0.78,
                   "min": -3.1e-05, "max": 1.0e-04, "mean": -1.8e-07,
                   "median": 9.1e-08, "std": 2.5e-05, "robust_sigma": 1.4e-06}
  },
  "quality": {
    "overall_status": "PASS",
    "checks": [
      {"name": "fits_a", "status": "PASS", "critical": true, "message": "Science image A has usable pixels", "details": null},
      {"name": "reprojection", "status": "PASS", "critical": true, "message": "Both products reprojected onto the reference grid", "details": null},
      {"name": "coverage", "status": "PASS", "critical": true, "message": "Common valid coverage 78.0% of the output grid", "details": {"common_coverage_fraction": 0.7798}},
      {"name": "alignment", "status": "PASS", "critical": true, "message": "Residual misalignment 0.13 px after reprojection", "details": {"residual_offset_px": 0.13, "verified": true}},
      {"name": "background", "status": "PASS", "critical": true, "message": "…", "details": null},
      {"name": "units", "status": "PASS", "critical": true, "message": "Compatible units (MJy / sr)", "details": null},
      {"name": "photometric", "status": "PASS", "critical": true, "message": "…", "details": null},
      {"name": "masking", "status": "PASS", "critical": true, "message": "FLAGS mask applied to both products…", "details": null},
      {"name": "uncertainty", "status": "PASS", "critical": true, "message": "…", "details": null},
      {"name": "psf", "status": "PASS", "critical": true, "message": "…", "details": null}
    ],
    "critical_checks": ["fits_a", "fits_b", "wcs_a", "wcs_b", "reprojection", "coverage",
                        "alignment", "background", "units", "photometric", "masking",
                        "uncertainty", "psf"],
    "failed_checks": [],
    "unverified_checks": [],
    "warnings": []
  },
  "provenance": {
    "comparison_id": "61b6f48041f58fc2f70c513040785214",
    "created_at": "2026-11-14T18:00:00+00:00",
    "software": {"planet_x_backend": "0.1.0", "python": "3.14.x", "numpy": "2.5.2", "astropy": "8.0.2", "scipy": "1.18.0"},
    "algorithm": "D = I_b - alpha * I_a on a common reprojected grid",
    "reprojection_method": "bilinear interpolation onto the reference WCS",
    "reference_wcs": {"ctype": ["RA---TAN", "DEC--TAN"], "crval": [156.09328159, -41.64466331], "crpix": [32.5, 32.5], "shape": [64, 64], "pixel_scale_arcsec": 6.15},
    "background": {"correction": "median", "levels": [0.001, 0.001], "variation": [3e-06, 4e-06], "reliable": [true, true]},
    "photometric": {"method": "none", "scale_factor": 1.0, "units": ["MJy / sr", "MJy / sr"], "absolute_calibration": true},
    "psf": {"matching": "none", "matched": false, "matching_supported": false,
            "spatially_varying": false, "method": null, "sigma_px": null,
            "verified": true, "metadata_available": [true, true],
            "fwhm_arcsec": [12.3, 12.3], "ratio": 1.0, "status": "PASS"},
    "masking": {"science_invalid": "non-finite pixels and non-informational FLAGS bits (SOURCE/FULLSAMPLE excluded)",
                "reprojection": "output pixel valid only if all contributing source pixels are valid",
                "difference": "NaN outside the common valid footprint"},
    "uncertainty": {"available": true, "model": "sigma_D^2 = sigma_b^2 + alpha^2 * sigma_a^2",
                    "input_propagation": "V_out = sum_i w_i^2 V_i on the reference grid",
                    "assumptions": "independent input-pixel noise; interpolation-correlated output noise not modelled",
                    "covariance_modelled": false},
    "input_sha256": {"<id-A>": "…", "<id-B>": "…"},
    "quality": {"fits_a": "PASS", "alignment": "PASS", "coverage": "PASS", "units": "PASS", "psf": "PASS"},
    "cache_key": "61b6f48041f58fc2f70c513040785214…"
  },
  "metadata": {
    "size_deg": 0.2,
    "difference_model": "D = I_b - alpha * I_a",
    "note": "Pixel-level difference of two reprojected SPHEREx products. A produced difference image is a measurement product, not a variability detection or photometric catalogue."
  }
}
```

When a mandatory check fails, the request is still a valid request, so the response is
`200` with:

```json
{
  "success": true,
  "comparison_id": "…",
  "status": "rejected",
  "scientifically_valid": false,
  "difference": null,
  "quality": {"overall_status": "FAIL",
              "checks": [{"name": "units", "critical": true, "status": "FAIL", "message": "BUNIT mismatch ('MJy / sr' vs 'Jy'); no supported conversion", "details": {"bunit_a": "MJy / sr", "bunit_b": "Jy"}}],
              "critical_checks": ["fits_a", "fits_b", "wcs_a", "wcs_b", "reprojection", "coverage", "alignment", "background", "units", "photometric", "masking", "uncertainty", "psf"],
              "failed_checks": ["units"], "unverified_checks": [],
              "warnings": ["BUNIT mismatch …"]}
}
```

The failing checks are always listed; no image is written. Transport/validation errors
(unknown id, size too large, pixel cap) use the normal error envelope with a non-200
status.

### Scientific validity (`scientifically_valid`)

Every check reports `PASS`, `WARNING`, `FAIL` or `NOT_VERIFIED`, and carries
`critical: true|false`. `scientifically_valid` is **derived, never asserted**:

```text
scientifically_valid = (status == "completed") AND
                       every critical check has status == "PASS"
```

- `FAIL` on any critical check rejects the comparison (`status: "rejected"`, no
  difference product).
- `NOT_VERIFIED` on a critical check **blocks validity** but still returns the
  computed difference, so the caller sees the data plus what could not be checked.
- `WARNING` on a critical check also blocks validity (for example a PSF ratio between
  the warning and failure thresholds, or `photometric_scaling="median_ratio"`).
- Non-critical warnings never invalidate a comparison.

Critical checks: `fits_a`, `fits_b`, `wcs_a`, `wcs_b`, `reprojection`, `coverage`,
`alignment`, `background`, `units`, `photometric`, `masking`, `uncertainty`, `psf`.

`NOT_VERIFIED` is returned when the evidence needed for a check is absent, not when the
check failed: missing `BUNIT`, missing `FLAGS`, missing `VARIANCE`, missing `EPSF`,
background correction disabled, relative photometric scaling, or an alignment that phase
correlation cannot confirm. A computationally successful difference image is therefore
never automatically reported as a scientifically valid comparison.

**Pipeline semantics.**

1. **FITS/WCS validation.** Both products must have finite science pixels and a
   celestial WCS, else the comparison is rejected. Pixels are invalid when they are
   non-finite or carry a non-informational `FLAGS` bit; the informational `SOURCE` and
   `FULLSAMPLE` bits are read from the product's own `MSKN`/`MSKD` definitions and do
   not mask data (unknown bits stay conservative = invalid).
2. **Common grid.** The reference product's own WCS and shape are the output grid. The
   reference image is copied untouched (`same_grid`) to avoid interpolation noise; the
   other is resampled. Pixels outside the source footprint stay `NaN`, never `0`.
3. **Interpolation.** Bilinear by default on calibrated surface brightness (`MJy/sr`).
   The taps and weights are computed explicitly (`_taps`) and reproduce
   `scipy.ndimage.map_coordinates(order=1, mode="nearest")`; nearest-neighbour is
   available via `config.interpolation`. This is **not flux-conserving** for point-source
   photometry and is reported as `flux_conserving: false`.
   - **Variance propagation.** A variance map is never interpolated as an image. With
     interpolation weights `w_i` (normalized, `Σ w_i = 1`, after boundary clamping):
     `V_out = Σ w_i² V_i`. Interpolating variance directly would give `Σ w_i V_i` and
     overstate the noise of every averaged pixel.
   - An output pixel is valid only if *every* source pixel with non-zero weight is valid;
     otherwise the science value and its variance are `NaN` (never zero-filled).
     Non-finite or negative source variance yields `NaN` variance while keeping a usable
     science value; `variance_valid_fraction` reports how much of the footprint that
     affects, and an incomplete fraction makes the `uncertainty` check `NOT_VERIFIED`.
   - Propagation assumes independent input-pixel noise. Interpolation correlates
     *neighbouring output* pixels even for white input noise; only the per-pixel variance
     is propagated and **covariance is not modelled** (`covariance_modelled: false`).
4. **Coverage.** The common valid fraction of the output grid must reach
   `MIN_COMMON_COVERAGE` (30%).
5. **Alignment.** FFT phase correlation on the reprojected pair measures the residual
   shift. `verified` requires a peak-to-noise ratio ≥ 5; a verified residual above
   `MAX_ALIGNMENT_RESIDUAL_PX` (1.5 px) fails. The shift is **measured, never applied**.
6. **Background.** Sigma-clipped median per product (with a 4×4 mesh variation and a
   reliability flag), subtracted when `background_correction="median"`.
7. **Photometry.** Units are compared by normalized `BUNIT`; a mismatch is a hard
   failure (no conversion is attempted). `alpha = 1` is the default because the inputs
   are already calibrated surface brightness. `median_ratio` estimates a **relative**
   scale from the data and is labelled as such. `absolute_calibration` is only `true`
   when units match, `alpha = 1`, and both products are `calib_level = 2`.
8. **PSF.** FWHM comes from the real `EPSF` BINTABLE (median kernel, half-maximum width
   in samples, divided by the header `OVSMPX/OVSMPY` oversampling and multiplied by the
   pixel scale). No oversampling → `fwhm_arcsec: null` + warning. The response separates
   four different facts: `metadata_available_a/b` (an EPSF exists), `verified` (both
   FWHMs were measured and compared), `matching_supported` (this implementation *can*
   match) and `matched` (a convolution was actually applied). Compatibility by FWHM
   ratio: ≤ `PSF_WARN_RATIO` PASS, ≤ `PSF_FAIL_RATIO` WARNING, above FAIL. Missing EPSF
   metadata is `NOT_VERIFIED`, never `PASS`. Optional `psf_matching="gaussian"`
   convolves the sharper product with a normalized Gaussian kernel to the broader FWHM
   (`method: "global_gaussian_convolution"`, `sigma_px` reported). This is a **global,
   Gaussian approximation of a spatially varying PSF**: the empirical kernel is not
   resampled and spatial PSF variation is not modelled
   (`spatially_varying: false`). A sub-0.05 px difference reports `matched: false`
   because no convolution was applied.
9. **Difference.** `D = I_b - alpha * I_a` only after all mandatory checks pass.
   `sigma_D² = sigma_b² + alpha² sigma_a²` when both `VARIANCE` extensions exist and
   their propagated variances are finite over the common footprint; otherwise
   `uncertainty_available: false` and no uncertainty file. `uncertainty_valid_fraction`
   reports the finite fraction, and `uncertainty_note` states the propagation model.
10. **Products.** Difference, uncertainty and mask FITS are written under `storage/fits/`
    with `sha256("<cache key>|<kind>")` names (served by `/files/{filename}`), plus a PNG
    preview under `storage/previews/`. Difference FITS carries `BUNIT`, `COMPID`,
    `OBSID1/2`, `DID1/2`, `BAND1/2`, `ALPHA`, `BGA/BGB`, `DIFFMETH`, `REPROJ`,
    `PSFMATCH`, `SWVERS`, `CREATED`. Inputs are never modified.
11. **Quality.** `overall_status` is `FAIL` if any check failed, else `NOT_VERIFIED` if any
    check could not be verified, else `WARNING` if any check warned, else `PASS`. The
    detailed lists (`critical_checks`, `failed_checks`, `unverified_checks`, `warnings`)
    are always present, so a single boolean never hides incomplete validation.
    `scientifically_valid` is derived from the critical checks (see above).
12. **Provenance.** Input ids, `obs_publisher_did`, input SHA-256 checksums, timestamps,
    software versions, algorithm, grid, background/photometric/PSF settings, masking
    rules, uncertainty model, per-check statuses and the deterministic cache key.
13. **Caching.** The comparison cache key is SHA-256 of the two ids, target, size and
    config. `comparison_id = key[:32]`; the full response JSON is persisted to
    `storage/comparisons/<id>.json`, so `GET` survives the in-process TTL.
14. **Concurrency.** Processing is bounded by a process-wide semaphore
    (`MAX_CONCURRENT_COMPARISONS`); sync handlers already run in the threadpool, so
    comparisons never block unrelated requests. Exceeding the wait timeout returns
    `503 PROCESSING_BUSY`.

### 4.10 `GET /images/comparisons/{comparison_id}`

Returns the stored comparison record (same schema as `POST /images/compare`), including
configuration, inputs, validation results, provenance and product availability. The id
is the 32-hex prefix of the cache key. Unknown or malformed ids return
`404 COMPARISON_NOT_FOUND`.

```bash
curl -s http://localhost:8000/api/v1/images/comparisons/<comparison_id>
```

### 4.11 Gaia DR3 catalog endpoints (Phase 5)

Synchronous ESA Gaia Archive TAP queries against `gaiadr3.gaia_source` (verified live;
details in `docs/GAIA_INTEGRATION.md`). Missing catalog measurements are `null`, never
`0`. `source_id` is a string. Positions are ICRS at the Gaia reference epoch **J2016.0**.

**`GET /catalogs/gaia/search`** — cone search.

| Query param | Range | Default |
|---|---|---|
| `ra` | [0, 360) | required |
| `dec` | [−90, 90] | required |
| `radius_arcsec` | (0, 60] | 10 |
| `magnitude_limit` | [−10, 30] | none |
| `limit` | [1, 1000] | 1000 |

```json
{
  "success": true,
  "catalog": "Gaia DR3",
  "query": {"ra": 156.09328159, "dec": -41.64466331, "radius_arcsec": 10.0,
            "magnitude_limit": null, "limit": 5, "total_found": 1, "truncated": false},
  "sources": [{
    "source_id": "5416565780217346432",
    "coordinates": {"ra": 156.09786, "dec": -41.64594, "reference_epoch": 2016.0,
                    "ra_error_mas": 0.065, "dec_error_mas": 0.08},
    "astrometry": {"parallax_mas": 0.362, "pmra_mas_yr": -8.867, "pmdec_mas_yr": 5.384,
                   "ruwe": 1.039, "ruwe_note": "Renormalised unit weight error; …"},
    "photometry": {"g": 17.94, "bp": 18.41, "rp": 17.32},
    "provenance": {"catalog": "Gaia DR3", "retrieved_at": "2026-11-15T00:00:00+00:00",
                   "query_reference": "…",
                   "tap_endpoint": "https://gea.esac.esa.int/tap-server/tap/sync"}
  }],
  "separations_arcsec": [13.148498]
}
```

Empty results are success (`total_found: 0`).

**`POST /catalogs/gaia/crossmatch`** — match external coordinates against Gaia DR3.

```json
{
  "coordinates": [{"external_id": "target-001", "ra": 180.123, "dec": 45.678, "epoch": 2020.5}],
  "config": {"radius_arcsec": 3.0, "propagate_proper_motion": true}
}
```

Statuses are `MATCHED` / `AMBIGUOUS` / `UNMATCHED`; a match is never forced and
`best` is `null` for ambiguous results. Optional proper-motion propagation uses
Astropy space motion from J2016.0 to the target epoch and reports
`epoch.propagated`, the propagated coordinates, and a `limitation` string when
propagation was skipped (missing PM, no epoch, …). Separations are great-circle
arcseconds. **A coordinate match is not a confirmed physical association**, and a
missing Gaia counterpart never invalidates a SPHEREx detection (Gaia is optical).

**`GET /catalogs/gaia/{source_id}`** — one record by exact identifier;
malformed ids → 400 `INVALID_GAIA_SOURCE_ID`, unknown ids → 404
`GAIA_SOURCE_NOT_FOUND`.

---

## 5. Error codes

| HTTP | Code | When |
|---|---|---|
| 400 | `INVALID_COORDINATES` | RA/Dec out of range, NaN/inf |
| 400 | `INVALID_RADIUS` | radius ≤ 0 or > configured max |
| 400 | `INVALID_CUTOUT_SIZE` | size ≤ 0 or > configured max |
| 400 | `INVALID_DATE_RANGE` | unparseable date or start > end |
| 400 | `INVALID_BAND_FILTER` | band filter is empty/invalid |
| 404 | `OBSERVATION_NOT_FOUND` | unknown observation id |
| 404 | `COMPARISON_NOT_FOUND` | unknown or malformed comparison id |
| 404 | `FILE_NOT_FOUND` | file not generated by this app |
| 422 | `INVALID_REQUEST` | request schema validation failed |
| 422 | `IMAGE_PROCESSING_LIMIT` | image pair exceeds `MAX_COMPARISON_PIXELS` |
| 422 | `REPROJECTION_FAILED` | WCS transform or interpolation failed |
| 422 | `MISSING_ACCESS_URL` | SIA row has no FITS product |
| 422 | `UNTRUSTED_ACCESS_URL` | URL not HTTPS or not on IRSA host |
| 422 | `UNSUPPORTED_PRODUCT` | non-FITS `access_format` |
| 422 | `MISSING_IMAGE_EXTENSION` | no 2D science image in the product |
| 502 | `IRSA_HTTP_ERROR` | IRSA returned an HTTP error |
| 502 | `IRSA_INVALID_RESPONSE` | IRSA returned non-VOTable / unparseable data |
| 502 | `IRSA_QUERY_ERROR` | IRSA rejected the SIA query |
| 400 | `INVALID_GAIA_SOURCE_ID` | malformed Gaia `source_id` |
| 404 | `GAIA_SOURCE_NOT_FOUND` | no Gaia DR3 record for a well-formed id |
| 502 | `GAIA_HTTP_ERROR` | Gaia Archive returned an HTTP error |
| 502 | `GAIA_QUERY_ERROR` | Gaia Archive rejected the ADQL query |
| 502 | `GAIA_INVALID_RESPONSE` | Gaia response unparseable |
| 503 | `GAIA_UNAVAILABLE` | Gaia Archive unreachable |
| 503 | `GAIA_RATE_LIMITED` | Gaia Archive asked for back-off (HTTP 429) |
| 504 | `GAIA_TIMEOUT` | Gaia Archive request timed out |
| 502 | `INVALID_FITS_FILE` | HTML/XML body, corrupt or incomplete FITS |
| 503 | `IRSA_UNAVAILABLE` | connection failure |
| 503 | `IRSA_CUTOUT_UNAVAILABLE` | cutout service busy/error after retries |
| 503 | `PROCESSING_BUSY` | too many comparisons in flight; retry shortly |
| 504 | `IRSA_TIMEOUT` | IRSA request timed out |
| 500 | `PREVIEW_GENERATION_FAILED` | preview rendering failed (e.g. all pixels invalid) |
| 500 | `INTERNAL_ERROR` | unexpected server error |

Successful HTTP 200 with empty results only ever means "no matching observations".

---

## 6. Examples

### curl

```bash
# health
curl http://localhost:8000/api/v1/health

# search (validated target)
curl -s -X POST http://localhost:8000/api/v1/observations/search \
  -H 'Content-Type: application/json' \
  -d '{"ra":156.09328159,"dec":-41.64466331,"radius":0.01,"collection":"spherex_qr3"}'

# detail (paste an observation_id from the search response)
curl -s http://localhost:8000/api/v1/observations/<observation_id>

# cutout
curl -s -X POST http://localhost:8000/api/v1/observations/<observation_id>/cutout \
  -H 'Content-Type: application/json' \
  -d '{"ra":156.09328159,"dec":-41.64466331,"size":0.1}'

# files (paste paths from the cutout response)
curl -o preview.png http://localhost:8000/api/v1/files/<hash>.png
curl -o cutout.fits http://localhost:8000/api/v1/files/<hash>.fits

# multi-epoch timeline (Phase 1)
curl -s 'http://localhost:8000/api/v1/targets/timeline?ra=156.09328159&dec=-41.64466331&radius=0.01&collection=spherex_qr3'

# timeline, filtered to one band and a date window
curl -s 'http://localhost:8000/api/v1/targets/timeline?ra=156.09328159&dec=-41.64466331&radius=0.01&bands=SPHEREx-D3&start_date=2026-05-17&end_date=2026-05-18'

# Phase 2: inspect one product
curl -s -X POST http://localhost:8000/api/v1/images/inspect \
  -H 'Content-Type: application/json' \
  -d '{"observation_id":"<id>","ra":156.09328159,"dec":-41.64466331,"size":0.2}'

# Phase 2: compare two epochs (same band, same target)
curl -s -X POST http://localhost:8000/api/v1/images/compare \
  -H 'Content-Type: application/json' \
  -d '{"observation_id_a":"<id-A>","observation_id_b":"<id-B>",
       "ra":156.09328159,"dec":-41.64466331,"size":0.2}'

# Phase 2: retrieve a stored comparison
curl -s http://localhost:8000/api/v1/images/comparisons/<comparison_id>
```

### Python

```python
import requests

BASE = "http://localhost:8000/api/v1"
TARGET = {"ra": 156.09328159, "dec": -41.64466331, "radius": 0.01, "collection": "spherex_qr3"}

search = requests.post(f"{BASE}/observations/search", json=TARGET, timeout=180).json()
first = next(o for o in search["observations"] if o["fits_available"])

cutout = requests.post(
    f"{BASE}/observations/{first['observation_id']}/cutout",
    json={"ra": TARGET["ra"], "dec": TARGET["dec"], "size": 0.1},
    timeout=300,
).json()

print(cutout["cutout"]["width"], cutout["cutout"]["height"], cutout["cutout"]["unit"])
requests.get(f"http://localhost:8000{cutout['cutout']['preview_url']}", timeout=60).content  # PNG bytes

# Phase 2: inspect + compare two same-band epochs
inspection = requests.post(
    f"{BASE}/images/inspect",
    json={"observation_id": first["observation_id"], "ra": TARGET["ra"], "dec": TARGET["dec"], "size": 0.2},
    timeout=300,
).json()
print(inspection["inspection"]["wcs"]["pixel_scale_arcsec"], inspection["inspection"]["psf"])

timeline = requests.get(f"{BASE}/targets/timeline", params={**{k: TARGET[k] for k in ("ra", "dec", "radius", "collection")}, "bands": "SPHEREx-D3"}, timeout=180).json()
ids = [p["observation_id"] for e in timeline["epochs"] for p in e["products"] if p["observation_id"]][:2]
comparison = requests.post(
    f"{BASE}/images/compare",
    json={"observation_id_a": ids[0], "observation_id_b": ids[1], "ra": TARGET["ra"], "dec": TARGET["dec"], "size": 0.2},
    timeout=600,
).json()
if comparison["status"] == "completed":
    requests.get(f"http://localhost:8000{comparison['difference']['fits_url']}", timeout=60).content
else:
    print([c for c in comparison["quality"]["checks"] if c["status"] == "FAIL"])
```

### Reusable Python functions

```python
from app.services.observation_service import search_observations
from app.services.fits_service import get_fits_product, extract_science_image

result = search_observations(156.09328159, -41.64466331, 0.01, "spherex_qr3")
print(result.total_observations)
```

`get_fits_product(row)`, `download_cutout(fits_url, ra, dec, size)`,
`extract_science_image(path)`, `validate_target_in_cutout(...)` and
`generate_preview(path)` are all importable and independently reusable.

Phase 2 entry points:

```python
from app.core.cache import CacheStore
from app.core.config import get_settings
from app.schemas.image_processing import CompareRequest
from app.services.irsa_client import IrsaClient
from app.services.image_comparison import ImageComparisonService
from app.services.observation_service import ObservationService

settings = get_settings()
observations = ObservationService(IrsaClient(settings), CacheStore(600, 512), settings)
service = ImageComparisonService(observations)

service.inspect("<observation_id>", ra=156.09328159, dec=-41.64466331, size=0.2)
service.compare(CompareRequest(observation_id_a="<id-A>", observation_id_b="<id-B>",
                               ra=156.09328159, dec=-41.64466331, size=0.2))
service.get_comparison("<comparison_id>")
```

Lower-level building blocks are also reusable: `inspect_fits(path, ...)` (inspection),
`reproject(data, valid, variance, wcs, grid)` and `measure_residual_shift(a, b, mask)`
(reprojection/alignment), and `estimate_background(data, valid)`.

---

## 7. Frontend integration

Full contract (every endpoint, schemas, TypeScript/Python/curl examples, error handling,
CORS troubleshooting): [`docs/FRONTEND_INTEGRATION.md`](docs/FRONTEND_INTEGRATION.md).

- Base URL: `http://localhost:8000/api/v1` (configurable). The compatibility
  connectivity endpoint is at the API origin root: `http://localhost:8000/`.
- Current AstroChronoscope frontend (`src/lib/api.ts`) only calls
  `${VITE_API_URL}/` and expects `{ "message": string }`. Set
  `VITE_API_URL=http://localhost:8000` in the frontend `.env`. Vite's dev origin
  `http://localhost:5173` is already allowed by default.
- The search/detail/cutout flow below is available for future frontend work; the
  current AstroChronoscope UI does not call it yet.
- CORS defaults allow `http://localhost:3000`, `http://127.0.0.1:3000`,
  `http://localhost:5173` and `http://127.0.0.1:5173`. Add your origin via
  `CORS_ORIGINS`; the `"*"` wildcard is rejected at startup.
- `X-Observation-Count` (search responses) is CORS-exposed and equals
  `total_observations`.
- `observation_id` is **opaque** and URL-safe (`[A-Za-z0-9_-]+`). Do not decode,
  parse or build it yourself.
- `cutout.fits_url` and `cutout.preview_url` are API-relative paths
  (`/api/v1/files/<hash>.{fits,png}`). Resolve them against the API origin:
  `new URL(cutout.preview_url, "http://localhost:8000")`. Do not treat them as
  relative to the frontend page.
- Parse `observation_date` as ISO 8601 UTC for timelines/epoch grouping.
- The frontend never needs to understand VOTable or FITS.
- To display an image:
  1. `POST /observations/search`
  2. let the user pick an observation (group by `obs_id`/time, use `band` for labels)
  3. `POST /observations/{id}/cutout`
  4. set `<img src="{API_ORIGIN}{cutout.preview_url}">` (API origin, not the frontend origin)
  5. optionally overlay a marker at `wcs.pixel_x`, `wcs.pixel_y`
  6. offer `fits_url` as the science download
- Responses can be large (126 observations ≈ 60 KB). Cache search results in the UI.
- Cutout requests can take seconds to tens of seconds on a busy IRSA; use a long
  client timeout (≥ 300 s) and a loading state. Re-requesting the same
  observation+position+size is served from disk and is fast.
- If `wcs.target_inside_image` is `false`, still show the image but indicate the
  target is outside; do not compute your own pixel position.

---

## 8. Testing

Deterministic suite (mocked HTTP, synthetic FITS fixtures, fixed seeds):

```bash
uv run pytest -q
```

Expected: **282 passed, 6 skipped** (the 6 skips are the opt-in live IRSA tests;
59 of the 282 are Gaia DR3 catalog tests, all network-mocked).
`compileall` and `pyflakes` are part of the fresh-install check.

Phase 2 review additions:

| File | What it pins |
|---|---|
| `tests/test_variance_propagation.py` | 14 analytic tests: constant/nonuniform variance, integer and fractional sampling, bilinear weights, masked neighbours, NaN and negative variance, boundary clamping, no valid support, science/variance alignment, and an explicit proof that variance is *not* interpolated like a scalar image |
| `tests/test_scientific_validity.py` | `scientifically_valid` is derived; every critical check must be PASS; missing units/variance/mask/PSF, disabled background correction, relative photometry and unverifiable alignment are `NOT_VERIFIED` and block validity; `NOT_VERIFIED` is never serialized as `PASS` |
| `tests/test_psf_compatibility.py` | compatible PSF verified but not matched, missing/one-sided EPSF metadata `NOT_VERIFIED`, mismatch `FAIL`, warning band, matching unsupported without metadata, matching performed only on request, provenance |

### Fresh-install integration check

```bash
cd backend
./scripts/fresh_install_check.sh
```

The script creates a throwaway virtual environment (`UV_PROJECT_ENVIRONMENT` under
`mktemp -d`), installs **only** from `uv.lock` with `uv sync --frozen`, then:

1. imports the shipped app and reports the resolved scientific versions;
2. starts the FastAPI app and checks `/`, `/api/v1/health` and `/openapi.json`;
3. inspects a deterministic synthetic SPHEREx-shaped FITS (`IMAGE`, `FLAGS`,
   `VARIANCE`, `EPSF`) through `POST /api/v1/images/inspect`;
4. runs `POST /api/v1/images/compare` end-to-end and asserts `completed`,
   `scientifically_valid: true`, every critical check `PASS`, PSF `PASS` with
   `matched: false`, and a propagated difference uncertainty;
5. checks `V_out = Σ w_i² V_i` analytically (`1.25` for a `(0.25, 0.5)` pixel shift
   with constant variance `4.0`, where scalar interpolation would report `4.0`);
6. runs the full pytest suite, `compileall` and `pyflakes` inside that environment;
7. confirms the frontend sources and `pnpm-lock.yaml` are byte-identical to
   `upstream/main`.

All runtime output goes to a temporary `STORAGE_DIR`; the script removes it on exit.

Live IRSA tests (query the real service; skipped unless `RUN_LIVE=1`):

```bash
RUN_LIVE=1 uv run pytest -q tests/test_live_integration.py tests/test_live_api_workflow.py tests/test_live_timeline.py tests/test_live_image_comparison.py
```

`test_live_integration.py` re-runs the validated experiment against the services;
`test_live_api_workflow.py` exercises the full HTTP contract the frontend uses
(search → detail → cutout → preview → FITS download); `test_live_timeline.py`
asserts structural timeline invariants (product/epoch accounting, ordering,
UTC timestamps, quality sums) without assuming row counts;
`test_live_image_comparison.py` inspects a real product and compares two same-band
epochs when such a pair exists (it skips with an explicit message otherwise). All are
excluded from the default suite.

Coverage highlights: coordinate/radius/date/band validation, VOTable parsing including
the `ID=col_N` quirk, empty results, IRSA error mapping, access-URL trust checks,
streamed download retries/size caps/HTML rejection, HDU extraction by name,
NaN/inf handling, WCS inside/outside/missing, preview rendering, API contract and
failure envelopes, path traversal rejection, root connectivity endpoint and CORS
preflight from the Vite origin.

Phase 2 coverage (deterministic): FITS inspection (missing/corrupt image HDU, missing
WCS, all-NaN, NaN/inf counts, flags bits, variance units, EPSF FWHM + oversampling,
target outside); reprojection (identity, different WCS recovering the same sky,
invalid-pixel propagation without zero-fill, outside-footprint NaN, nearest neighbour,
variance resampling); alignment (known shift recovery, flat-image refusal); comparison
(identical products → zero difference, shifted source → rejected, units mismatch →
rejected, insufficient overlap → rejected, PSF incompatibility → rejected, PSF matching
on request, missing variance → explicit, uncertainty propagation, background
correction, median-ratio scaling, absolute-calibration gating, difference/mask/preview
FITS contents and headers, provenance, input immutability, deterministic re-processing,
pixel limit); API (inspect/compare/rejected/404/422/503 paths, file retrieval, cache
hits, OpenAPI paths).

Timeline coverage (deterministic): multi-band product grouping by `obs_id`, distinct
epochs and chronological ordering, exact-duplicate removal, conflicting-DID handling,
missing/invalid timestamps, missing band/identifier/`obs_id`, UTC midpoint conversion,
`POLYGON`/`CIRCLE` target containment, unknown spatial coverage, inconsistent epoch
metadata, band filters, date-filter forwarding, empty results, cache hits, and
per-filter timeline cache separation.

Live verification on 2026-11-14:

```
search 126 rows (spherex_qr3) in ~5.5 s
cutout 4,083,840 bytes, 7 HDUs, IMAGE 58×59, BUNIT "MJy / sr"
preview PNG generated, target_inside_image=true
timeline 126 products → 63 observations → 63 epochs, 33.81-day baseline,
         125 target-covered / 1 footprint outside, 6 bands, 0 metadata gaps
188 passed, 6 skipped (live) | live timeline: 1 passed in 4.14 s
live service: 2 passed in 93 s | live API workflow: 1 passed in 73 s
```

Phase 2 deterministic suite (`uv run pytest -q`): **223 passed, 6 skipped** —
98 Phase 2 tests for inspection, reprojection, variance propagation, alignment,
scientific validity, PSF compatibility, comparison and the new API, with the same
6 opt-in live skips.

Phase 2 live validation on 2026-11-14 (after IRSA's proxy recovered):

```
inspect 2026W20_2A_0436_1/D3  117×117, MJy/sr, 6.156″/px, target inside,
                              EPSF FWHM 1.077 px = 6.63″, valid fraction 0.965
compare 0436_1/D3 ↔ 0224_2/D3 (33-day baseline)
        completed, scientifically_valid=true, overall PASS
        coverage 89.2%, alignment 0.03 px, PSF ratio 1.0002, alpha=1
        difference median 0.0032 MJy/sr, robust sigma 0.0237
        difference/uncertainty/mask FITS + PNG served over HTTP
RUN_LIVE=1 pytest -q tests/test_live_image_comparison.py → 2 passed in 46 s
```

Live validation also found a real masking bug: SPHEREx's `FLAGS` marks known sources
with the `SOURCE` bit, and the original "any non-zero bit" policy discarded 84.5% of
the pixels (valid fraction 0.155). `SOURCE`/`FULLSAMPLE` are now informational and the
valid fraction is 0.965; the comparison passes coverage.

Live-suite caveat: the live tests re-query IRSA and are only as reliable as that
service. IRSA's SIA `ID=` lookup currently returns `502 Proxy Error` or times out
intermittently, and even `POS` queries occasionally return `502`; a live run can fail
with `IRSA_UNAVAILABLE`/`IRSA_CUTOUT_UNAVAILABLE` even though the backend is correct.
Re-run when IRSA is healthy. The validated target's ~126 product rows exceed small
in-process cache bounds; live test fixtures use `CacheStore(60, 512)` so
`create_cutout` resolves from the search result instead of an `ID=` lookup.

---

## 9. Resource management

- Downloads are streamed to `<hash>.fits.part` and atomically renamed after FITS validation.
- Hard byte cap enforced from both `Content-Length` and streamed chunk count.
- FITS files are opened with `memmap=True` and closed via context managers.
- Per-key locks prevent duplicate concurrent downloads of the same cutout
  (within one process; separate uvicorn workers may each download it once).
- In-process SIA caches: TTL + LRU bound. Disk cache: cutouts/previews keyed by
  SHA-256 of `fits_url | ra | dec | size` (observation, coordinates and size).
- Cached cutouts are checked for the FITS magic (`SIMPLE`) before reuse; a file
  without it is re-downloaded.
- `GET /files/*` only serves hash-named files inside the configured storage root.

Phase 2:

- Comparison inputs are the same cutouts the existing endpoints produce (shared target
  and size), so no extra downloads beyond the first fetch; the disk cache is reused.
- `MAX_COMPARISON_PIXELS` rejects oversized pairs before any resampling; the check runs
  on the extracted science arrays.
- Only `IMAGE`, `FLAGS`, `VARIANCE` and `EPSF` are read; FITS files are opened with
  `memmap=True` and closed via context managers.
- Reprojection allocates one `float64` output per input plus one `float64` coordinate
  array pair of the output size; no full-array copies beyond that.
- Processing is bounded by a process-wide `BoundedSemaphore(MAX_CONCURRENT_COMPARISONS)`
  with a wait timeout; sync handlers run in the threadpool, so other endpoints stay
  responsive.
- Generated products are named `sha256("<cache key>|<kind>")`, so re-running the same
  comparison rewrites the same files instead of accumulating new ones. Storage has no
  eviction policy (same as cutouts) — add a sweeper if it grows.

Not implemented (deliberately): Redis, Celery, databases, background workers.

---

## 10. Known limitations / future work

1. **Phase 2 scope.** Multi-epoch discovery, FITS inspection and pairwise image
   comparison are implemented. There is no light-curve, catalogue photometry or spectral
   endpoint; timeline responses still report `comparability_assessed: false` because the
   timeline itself does not assess comparability — `/images/compare` does, per pair.
2. **Epoch = `obs_id`.** No documented visit/sequence grouping exists in SIA2, so
   consecutive exposures are separate epochs. If a future data release documents
   visits, grouping can be added without changing product-level fields.
3. **Timestamp provenance.** Observation time is the SIA2 `t_min`/`t_max` midpoint.
   For the validated SPHEREx product, FITS `MJD-AVG` matches that midpoint; other
   collections may define `t_min`/`t_max` differently and should be checked before
   cross-collection comparisons.
4. **Spatial relevance is footprint-based.** `target_covered` uses `s_region`
   (`POLYGON`/`CIRCLE`); unsupported region encodings return `null` with a warning
   rather than a guessed result.
5. **No spectral extraction.** `WCS-WAVE` is inspected but not used; the `EPSF` table is
   used only for its median FWHM. Spectral cubes and band-to-band SED work are Phase 3+.
6. **Preview is not calibrated.** Percentile + asinh scaling is for display only.
7. **SIA ID lookup latency.** First detail lookup after cache expiry takes ~20 s.
   Increase TTL or persist the row cache if this matters.
8. **Disk cache has no eviction.** Small cutouts and comparison products only; add a
   sweeper if storage grows.
9. **IBE cutout availability.** IRSA's cutout service is occasionally busy; requests
   retry then fail with `503`. The frontend should offer a retry.
10. **Collection-specific cutout behaviour.** Only IBE-hosted products (all SPHEREx
    level-2 products here) support the `center`/`size` cutout parameters. Other
    collections may need their own access pattern; failures surface explicitly.
11. **Single-process caches.** Multiple uvicorn workers each keep their own cache
    (correct, just less efficient). The comparison semaphore is also per process.
12. **No authentication / rate limiting.** Add a gateway or middleware before public
    deployment.

Phase 2 scientific limitations (explicit, not hidden):

13. **Reprojection is not flux-conserving.** Bilinear/nearest interpolation of calibrated
    surface brightness on the reference grid. It is valid for pixel-level comparison but
    not for aperture photometry or total-flux measurements; `flux_conserving` is reported
    as `false`.
14. **Alignment is measured, not applied.** Phase correlation only validates the WCS-based
    alignment; a residual above 1.5 px rejects the comparison. There is no image-based
    re-registration step (no `photutils`/`scikit-image` available).
15. **PSF matching is a global Gaussian approximation.** The real `EPSF` supplies the
    FWHM, but the convolution kernel is Gaussian, not the full empirical PSF, and it is
    one global kernel per product. Spatially varying PSFs are **not supported**
    (`spatially_varying: false`). Without `EPSF`/oversampling the check is
    `NOT_VERIFIED`, never `PASS`. Noise is not re-propagated through the matching
    convolution.
16. **Background model is global.** A sigma-clipped median per product plus a 4×4 mesh
    variation for reliability. No local background map is subtracted; strong extended
    emission can bias the level.
17. **Photometric scaling.** `alpha = 1` is the default and is only justified because both
    inputs are calibrated surface brightness in the same units. `median_ratio` is
    explicitly relative and can absorb real variability. No colour correction or
    absolute flux calibration is applied.
18. **No deconvolution / cosmic-ray rejection beyond FLAGS.** Masked pixels are those with
    non-finite values or a non-informational `FLAGS` bit (`SOURCE` and `FULLSAMPLE` are
    annotations, not defects, and are not masked — masking `SOURCE` would delete every
    real source; verified live: valid fraction 0.15 → 0.96). Unflagged transient
    artefacts remain.
19. **Difference products are measurements, not detections.** No source extraction,
    significance map, thresholding or light curve is produced. `robust_sigma` is a MAD
    estimate over the difference pixels.
20. **Only same-band pairs are scientifically meaningful.** The API does not enforce band
    equality (the caller decides), but cross-band subtraction is not valid with this
    model; the response reports both bands so the caller can check.
21. **Variance propagation assumes independent input pixels.** `V_out = Σ w_i² V_i` is
    per-pixel only. Interpolation correlates neighbouring output pixels; the output
    covariance matrix is not computed, so significance maps, matched-filter detection and
    aperture sums that rely on independent errors must not be derived from the
    uncertainty FITS. Correlated (resampling-correlated) input noise is also not
    modelled.
22. **PSF matching does not propagate noise.** When `psf_matching="gaussian"` is applied,
    the convolution correlates neighbouring pixels and the propagated uncertainty FITS
    is computed from the pre-matching variance. Treat the matched-product uncertainty as
    approximate.
23. **Scientific validity is conservative.** Any `NOT_VERIFIED` critical check blocks
    `scientifically_valid`, so products missing `BUNIT`, `FLAGS`, `VARIANCE` or `EPSF`
    will report `false` even when the difference image itself looks reasonable. This is
    intentional: the API never upgrades missing evidence to a pass.
