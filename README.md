# see-spot

[![License](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)
![Code Style](https://img.shields.io/badge/code%20style-black-black)
[![semantic-release: angular](https://img.shields.io/badge/semantic--release-angular-e10079?logo=semantic-release)](https://github.com/semantic-release/semantic-release)
![Interrogate](https://img.shields.io/badge/interrogate-100.0%25-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen?logo=codecov)
![Python](https://img.shields.io/badge/python->=3.10-blue?logo=python)

## Quick Start

### Installation
```bash
# Clone repository
git clone https://github.com/AllenNeuralDynamics/see-spot.git
cd see-spot

# Run installer (uses defaults: port 5555, cache at ~/.seespot/cache)
./install.sh

# Or customize settings interactively
./install.sh --interactive
```

### Launch
```bash
# Start the server
seespot start

# Access at http://localhost:5555
```

For detailed installation instructions, AWS credentials setup, and troubleshooting, see [INSTALL.md](INSTALL.md).

## App UI
![Spot Visualization](img/seespot-app-v6.8.png)
*Interactive dashboard showing spot channel analysis*

## Development Setup
+ Install
  + `uv sync`
+ Launch with auto-reload: 
```bash
cd /home/matt.davis/code/see-spot && source .venv/bin/activate && cd src && uvicorn see_spot.app:app --host 0.0.0.0 --port 9999 --reload
```

## Dataset Types

### Regular (Fused) Datasets
Standard datasets with fused image data at the top level:
```
dataset_name/
  image_spot_spectral_unmixing/
    mixed_spots_*.pkl
    unmixed_spots_*.pkl
  image_tile_fusing/fused/
    channel_*.zarr
```

### Tiled (Non-Fused) Datasets
Datasets with independent tile processing, where each tile has separate spot data:
```
dataset_name/
  image_spot_spectral_unmixing/
    Tile_X_0001_Y_0000_Z_0000/
      mixed_spots_*_tile_*.pkl
      unmixed_spots_*_tile_*.pkl
    Tile_X_0002_Y_0000_Z_0000/
      ...
```

When downloading a tiled dataset, the system automatically:
- Detects tile subfolders (beginning with "Tile")
- Creates virtual dataset entries for each tile
- Names them as: `{dataset_name}_X_####_Y_####_Z_####`

Example: Downloading `HCR_799211_2025-10-02_15-10-00_processed_2025-11-06_22-50-54` with tiles creates:
- `HCR_799211_2025-10-02_15-10-00_processed_2025-11-06_22-50-54_X_0001_Y_0000_Z_0000`
- `HCR_799211_2025-10-02_15-10-00_processed_2025-11-06_22-50-54_X_0002_Y_0000_Z_0000`
- etc.

Each virtual tile dataset appears as a separate entry in the dataset management table and can be loaded independently.

## Changelog
+ v0.5.0 (09-19-2025)
  + backend downloads mixed + unmixed tables, merges and saves as .parquet (massive compression)
  + polars for dataframe manipulation (huge speedup)
  + optimized data types in tables, reduced memory demands
  + added removed_spots key in data table and plotting in frontend
  + Added mixed/unmixed channel display mode toggle with dedicated UI controls
  + Implemented chart axis limit controls (auto, fixed, min/max, percentile modes)
  + Added Sankey flow diagram showing channel reassignment patterns with backend data calculation
  + Enhanced data filtering with valid spots toggle and removed spots highlighting

## Contributing

### Linters and testing
```bash
coverage run -m unittest discover && coverage report
interrogate .
flake8 .
black .
isort .
```

### Pull requests

+ Internal members, please create a branch. 
+ External members, fork repo and open PR

### Commit style
+ We primarily use [Angular](https://github.com/angular/angular/blob/main/CONTRIBUTING.md#commit) style for commit messages. Roughly, they should follow the pattern: 
+ `<type>: <short summary>`


#### Type:
- **build**: Changes that affect build tools or external dependencies (example scopes: pyproject.toml, setup.py)
- **ci**: Changes to our CI configuration files and scripts (examples: .github/workflows/ci.yml)
- **docs**: Documentation only changes
- **feat**: A new feature
- **fix**: A bugfix
- **perf**: A code change that improves performance
- **refactor**: A code change that neither fixes a bug nor adds a feature
- **test**: Adding missing tests or correcting existing tests

