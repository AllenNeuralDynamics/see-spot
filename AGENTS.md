# Copilot Instructions for this repo

These notes teach an AI agent how to be productive quickly in this codebase. Keep answers concrete, reference files, and follow the repo’s conventions.

## Big picture
- App: FastAPI backend with a small JS front-end for interactive plots.
  - Entry: `src/see_spot/app.py`
  - Templating + static: `src/see_spot/templates/`, `src/see_spot/static/`
- Data: Loaded from S3, cached locally under `/s3-cache/{bucket}/{key}` to avoid re-downloads.
  - Low-level S3: `src/see_spot/s3_handler.py`
  - Higher-level helpers: `src/see_spot/s3_utils.py` (find files, load pickle/CSV/TXT, parse manifest)
- Neuroglancer links: `src/see_spot/ng_utils.py` builds a JSON state and returns a direct URL.

## How it runs (local dev)
- Python 3.11+ (see `pyproject.toml`), NumPy pinned to 1.26.4.
- Start server from repo root with venv activated and working dir at `src`:
  - `uvicorn see_spot.app:app --host 0.0.0.0 --port 9999 --reload`
- Front-end loads `/unmixed-spots` template and calls JSON APIs.
- S3 credentials from env (AWS_ACCESS_KEY_ID/SECRET/TOKEN). Create `/s3-cache` locally or mount it; the app will populate it on demand.

## Key runtime knobs and data flow
- Bucket: `REAL_SPOTS_BUCKET = "aind-open-data"` in `app.py`.
- Active dataset: `PROCESSED_DATA_ROOT_PREFIX` in `app.py` (e.g., `HCR_..._processed_...`).
  - Change via API rather than editing code:
    - `GET /api/datasets` → reports what’s in `/s3-cache/aind-open-data`.
    - `POST /api/datasets/download { dataset_name }` → verifies on S3 and caches manifest + data.
    - `POST /api/datasets/set-active { dataset_name }` → updates in-memory state and clears caches.
- Core data endpoint: `GET /api/real_spots_data?sample_size=10000&force_refresh=false`.
  - Loads processing manifest at: `{PROCESSED_DATA_ROOT_PREFIX}/derived/processing_manifest.json` to get `spot_channels`.
  - Finds `image_spot_spectral_unmixing/unmixed_spots_*.pkl`, loads with pandas, filters `valid_spot == True`.
  - Returns:
    - `channel_pairs`: pairs like `[ ["488","514"], ["488","561"], ... ]` (from manifest or inferred columns)
    - `spots_data`: list of records with at least
      - `spot_id`, `chan`, `unmixed_chan`, `r`, `dist`, and intensity columns named `chan_<channel>_intensity` (e.g., `chan_488_intensity`)
      - Backend adds `reassigned = (chan != unmixed_chan)`
    - `spot_details`: dict keyed by `spot_id` to `{x,y,z,cell_id,round,...}` when available
    - `fused_s3_paths`: `s3://.../image_tile_fusing/fused/channel_<ch>.zarr` for each channel
    - Optional: `ratios` (matrix from `*_ratios.txt`), `summary_stats` (rows from `summary_stats.csv` with `removed_spots` and `unchanged_spots` computed)

## Front-end expectations (keep contracts stable)
- JS at `static/js/unmixed_spots.js` expects the schema above and computes typed arrays for performance.
- Colors by `unmixed_chan` are defined in that file. Large sample sizes (>= 25,001) enable “large” mode in ECharts.
- Neuroglancer: client calls `POST /api/create-neuroglancer-link` with `fused_s3_paths`, `position`, `point_annotation`, `spot_id`, optional `cell_id`, etc.; server returns `{ url }`.

## S3 access and caching patterns
- Always pass `bucket_name` when using `s3_handler` helpers; default bucket in `s3_handler.py` is not relied on by the app.
- `s3_handler.download_file(..., use_cache=True)` stores to `/s3-cache/{bucket}/{key}` and short-circuits if present.
- `s3_utils.find_unmixed_spots_file(...)` and `find_related_files(...)` scan a directory prefix; pagination is limited to ~200 keys, so update max_keys if you add datasets with many files.

## Dev workflows and checks
- Tests and coverage (thresholds set to 100%):
  - `coverage run -m unittest discover && coverage report`
- Lint/format/doc coverage (also strict):
  - `flake8 .`, `black .`, `isort .`, `interrogate .` (fail-under=100 in `pyproject.toml`)
- Docs (Sphinx):
  - `sphinx-apidoc -o docs/source/ src` → `sphinx-build -b html docs/source/ docs/build/html`
- Commits: follow Angular-style messages; semantic-release semantics documented in `README.md`.

## When adding or changing features
- Preserve response shapes that `unmixed_spots.js` relies on (names like `chan_<ch>_intensity`, `reassigned`).
- If you introduce new channels, ensure the manifest’s `spot_channels` lists them; the app uses that to form `channel_pairs` and fused Zarr paths.
- Keep S3 downloads idempotent and cache-aware; prefer `download_file(..., use_cache=True)` over raw `get_object` when you need a local file path.
- Place new templates under `templates/` and static assets under `static/` alongside existing files to keep paths working with `app.mount('/static', ...)`.


# See-Spot Architecture Overview

## Core Components

1. **FastAPI Web Application**
   - Entry point: app.py
   - Serves the main web interface and API endpoints
   - Uses Jinja2 templating for HTML pages

2. **S3 Data Management**
   - s3_handler.py: Core implementation of S3 operations
   - s3_utils.py: Higher-level functions for file searching, loading, and caching

3. **Neuroglancer Integration**
   - ng_utils.py: Utilities for generating Neuroglancer visualizations
   - Creates interactive 3D visualizations of imaging data

4. **Frontend Interface**
   - Main page: unmixed_spots.html
   - Interactive JavaScript: unmixed_spots.js
   - Data visualization and interaction

## Key Features

1. **Spot Data Visualization**
   - Loads spot data from S3 (unmixed_spots_*.pkl files)
   - Displays interactive scatter plots of spots
   - Provides filtering and selection tools

2. **3D Visualization**
   - Integrates with Neuroglancer for 3D exploration
   - Supports navigation to specific spot locations
   - Shows multi-channel fluorescence data

3. **Data Analysis**
   - Displays statistical summaries of spot data
   - Shows channel assignment and reassignment information
   - Supports exploration of intensity relationships

4. **API Endpoints**
   - /api/real_spots_data: Retrieves spot data for visualization
   - /api/create-neuroglancer-link: Generates custom Neuroglancer views

## Data Flow

1. User visits /unmixed-spots
2. Frontend requests data through API
3. Backend loads data from S3, processes it, and returns JSON
4. Frontend renders interactive visualizations
5. User can select spots to view in Neuroglancer

## Technology Stack

- **Backend**: Python, FastAPI, Uvicorn
- **Frontend**: HTML, JavaScript, Apache ECharts
- **Storage**: S3 for data files
- **Visualization**: Neuroglancer for 3D images
- **Data Processing**: Pandas, NumPy

## Deployment

- Runs with Uvicorn server
- Requires S3 credentials for data access
- Deployed as a standalone web application