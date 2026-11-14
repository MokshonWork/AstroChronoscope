# AstroChronoscope — Planet X

AstroChronoscope / Planet X is a time-domain astronomical exploration platform powered by
NASA SPHEREx. It discovers real SPHEREx observations through IRSA, retrieves FITS products
and spatial cutouts, validates their celestial WCS, and compares two epochs of the same
target on a common sky grid with explicit scientific quality checks.

Everything shown comes from live IRSA data. There are no mock observations, no synthetic
FITS files in the product path and no hardcoded observation metadata.

## Project structure

```text
AstroChronoscope/
├── src/                       # React + TypeScript frontend (Vite, at repo root)
├── public/
├── index.html
├── package.json               # pnpm scripts: dev / build / lint / preview
├── vite.config.ts
├── tsconfig*.json
├── notebooks/
├── backend/                   # FastAPI service (Planet X phases 1-2)
│   ├── app/                   # routes, schemas, services, core config
│   ├── tests/                 # deterministic + opt-in live tests
│   ├── docs/                  # frontend integration contract
│   ├── storage/               # runtime artifacts (git-ignored, .gitkeep only)
│   ├── README.md              # detailed backend documentation
│   ├── TODO.md                # phase checklists and validation logs
│   ├── pyproject.toml         # authoritative dependencies (Python 3.14, exact pins)
│   ├── uv.lock                # lockfile resolved from pyproject.toml
│   ├── requirements.txt       # generated pip-compatible export (hashes)
│   ├── scripts/               # fresh-install integration check
│   ├── pytest.ini
│   └── .env.example
├── README.md                  # this file
└── LICENSE
```

The frontend is a static SPA that talks to the backend over JSON. The backend is the only
component that contacts IRSA; the browser never does.

> Note: `backend/main.py` is the original frontend-connection stub and is retained
> unchanged for history. The production backend entry point is `backend/app/main.py`
> (run with `uvicorn app.main:app`), and `backend/pyproject.toml` now describes the real
> backend, documented in [`backend/README.md`](backend/README.md).

## Backend capabilities

Implemented and verified in source and tests:

- **SPHEREx observation discovery** — IRSA SIA2 cone search, VOTable parsing, metadata mapping.
- **Multi-epoch timeline generation** — normalization, duplicate removal, `obs_id` epoch grouping, quality/warning summary.
- **FITS download and cutout retrieval** — streamed, size-capped, retried, hash-addressed local cache.
- **WCS validation** — celestial WCS extraction, target-in-footprint check, footprint and pixel scale.
- **FITS inspection** — HDUs by `EXTNAME`, statistics, FLAGS mask bits, VARIANCE, EPSF PSF metrics.
- **Image reprojection** — resampling onto a reference product's WCS/grid with invalid-pixel propagation.
- **Image alignment validation** — FFT phase-correlation residual measurement (measured, never applied).
- **Scientific image comparison** — prerequisite checks (units, coverage, alignment, PSF), background estimation, photometric scaling.
- **Difference-image generation** — `D = I_b - α·I_a` with uncertainty and mask products, written as separate FITS files.
- **Quality assessment and provenance** — `PASS`/`WARNING`/`FAIL` per check, input checksums, software versions, deterministic cache key.

API endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | frontend connectivity check |
| `GET` | `/api/v1/health` | liveness |
| `POST` | `/api/v1/observations/search` | cone search |
| `GET` | `/api/v1/observations/{observation_id}` | product metadata |
| `POST` | `/api/v1/observations/{observation_id}/cutout` | FITS cutout + preview |
| `GET` | `/api/v1/targets/timeline` | multi-epoch timeline |
| `POST` | `/api/v1/images/inspect` | FITS inspection |
| `POST` | `/api/v1/images/compare` | validated pairwise comparison |
| `GET` | `/api/v1/images/comparisons/{comparison_id}` | stored comparison record |
| `GET` | `/api/v1/files/{filename}` | generated FITS/PNG files |

Full field-level contracts, error codes and scientific limitations are in
[`backend/README.md`](backend/README.md) and
[`backend/docs/FRONTEND_INTEGRATION.md`](backend/docs/FRONTEND_INTEGRATION.md).

## Frontend

The frontend is a React 19 + TypeScript + Vite app styled with Tailwind CSS. It reads the
API origin from `VITE_API_URL` (see `.env.example`, default `http://localhost:8000`) and
uses `GET /` as its backend connectivity check.

```bash
# from the repository root
cp .env.example .env
pnpm install
pnpm run dev
```

Available scripts: `pnpm run dev`, `pnpm run build`, `pnpm run lint`, `pnpm run preview`.

The frontend is maintained separately; backend changes must not modify it.

## Local development

### Backend

```bash
cd backend

uv sync                      # authoritative deps + lockfile (uv.lock)

cp .env.example .env

uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`backend/pyproject.toml` is the single dependency source; `backend/uv.lock` is the
lockfile and `backend/requirements.txt` is a generated pip-compatible export
(`pip install -r requirements.txt` also works).

Python 3.14 is required and pinned in `backend/pyproject.toml`. `scipy` is required for
reprojection and convolution; `reproject`, `photutils` and `scikit-image` are
deliberately not dependencies (see the backend README).

### API documentation

Interactive OpenAPI docs: <http://localhost:8000/docs>

## Testing

```bash
cd backend

uv run pytest -q                    # deterministic suite, no network
uv run python -m compileall -q app tests scripts
uv run python -m pyflakes app tests scripts
```

Fresh-install verification (throwaway venv, installs only from `uv.lock`, runs the
integration check, the suite, compile and lint, and confirms the frontend is untouched):

```bash
cd backend
./scripts/fresh_install_check.sh
```

Live IRSA tests are opt-in and never run by default:

```bash
RUN_LIVE=1 uv run pytest -q tests/test_live_integration.py \
  tests/test_live_api_workflow.py tests/test_live_timeline.py \
  tests/test_live_image_comparison.py
```

Current deterministic suite: **223 passed, 6 skipped** (the skips are the opt-in live
tests). Live validation on 2026-11-14 inspected a real SPHEREx product and compared two
D3 epochs 33 days apart (all checks `PASS`, 89.2 % coverage, 0.03 px residual).

## Project roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Multi-epoch observation discovery | Completed |
| 2 | Scientific image processing and comparison | Completed |
| 3 | Source detection, photometry and time-domain analysis | Pending |
| 4 | Spectral data processing | Pending |
| 5 | Gaia integration | Pending |
| 6 | WISE/NEOWISE integration | Pending |

Status labels describe project progress; they are not a substitute for running the tests.

## Prerequisites

- Node.js and pnpm (frontend)
- Python 3.14 (backend)
- Network access to IRSA for live data

## Usage

1. Start the backend (`uvicorn app.main:app --host 127.0.0.1 --port 8000`).
2. Start the frontend (`pnpm run dev`) and open <http://localhost:5173>.
3. Use the connectivity check to confirm the backend is reachable.

## License

See [LICENSE](LICENSE).
