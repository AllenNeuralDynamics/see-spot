from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import numpy as np
from datetime import datetime, timedelta
import uvicorn
import logging
import os
import json
from pathlib import Path
import polars as pl
import itertools
from typing import List, Tuple, Dict, Any

# Import your modules
from see_spot.s3_handler import s3_handler
from see_spot.s3_utils import (
    find_unmixed_spots_file, find_related_files,
    load_ratios_from_s3, load_summary_stats_from_s3,
    load_processing_manifest_from_s3, load_and_merge_spots_from_s3
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Get the directory of the current file (app.py)
current_file_dir = Path(__file__).parent
static_dir = current_file_dir / "static"
templates_dir = current_file_dir / "templates"

# Mount static files
app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

# Configuration for the spots data
REAL_SPOTS_BUCKET = "aind-open-data"
PROCESSED_DATA_ROOT_PREFIX = "HCR_749315_2025-05-08_14-00-00_processed_2025-05-17_22-15-31"
SAMPLE_SIZE = 10000

# In-memory cache for DataFrame to avoid reloading on every request
df_cache = {
    "data": None,
    "last_loaded": None,
    "target_key": None,
    "processing_manifest": None,
    "spot_channels_from_manifest": None
}

def get_channel_pairs(df: pl.DataFrame) -> List[Tuple[str, str]]:
    """Extracts channel pairs from intensity column names."""
    intensity_cols = [col for col in df.columns if col.endswith('_intensity')]
    channels = sorted([col.split('_')[1] for col in intensity_cols])
    return list(itertools.combinations(channels, 2))

@app.get("/api/real_spots_data")
async def get_real_spots_data(sample_size: int = SAMPLE_SIZE, force_refresh: bool = False):
    logger.info(f"Real spots data requested with sample size: {sample_size}, force_refresh: {force_refresh}")

    # Check if we can use cached DataFrame
    if not force_refresh and df_cache["data"] is not None:
        logger.info(f"Using cached DataFrame from {df_cache['last_loaded']}. Shape: {df_cache['data'].shape}")
        df = df_cache["data"]
        # Load manifest and channels from cache if available, or re-fetch
        processing_manifest = df_cache.get("processing_manifest")
        spot_channels_from_manifest = df_cache.get("spot_channels_from_manifest")
        if not processing_manifest or not spot_channels_from_manifest:
            # Construct manifest path and load
            manifest_key = f"{PROCESSED_DATA_ROOT_PREFIX}/derived/processing_manifest.json"
            logger.info(f"Attempting to load processing manifest from: s3://{REAL_SPOTS_BUCKET}/{manifest_key}")
            processing_manifest = load_processing_manifest_from_s3(REAL_SPOTS_BUCKET, manifest_key)
            if processing_manifest and "spot_channels" in processing_manifest:
                spot_channels_from_manifest = processing_manifest["spot_channels"]
                df_cache["processing_manifest"] = processing_manifest
                df_cache["spot_channels_from_manifest"] = spot_channels_from_manifest
                logger.info(f"Loaded spot channels from manifest: {spot_channels_from_manifest}")
            else:
                logger.error(f"Could not load processing manifest or find 'spot_channels'. Manifest: {processing_manifest}")
                spot_channels_from_manifest = []
    else:
        # Need to load DataFrame from S3
        # 1. Load processing manifest to determine paths and channels
        manifest_key = f"{PROCESSED_DATA_ROOT_PREFIX}/derived/processing_manifest.json"
        logger.info(f"Attempting to load processing manifest from: s3://{REAL_SPOTS_BUCKET}/{manifest_key}")
        processing_manifest = load_processing_manifest_from_s3(REAL_SPOTS_BUCKET, manifest_key)

        if not processing_manifest:
            logger.error(f"Could not load processing manifest from {manifest_key}.")
            return JSONResponse(status_code=500, content={'error': 'Failed to load processing manifest'})

        spot_channels_from_manifest = processing_manifest.get("spot_channels", [])
        if not spot_channels_from_manifest:
            logger.warning("No 'spot_channels' found in manifest. Channel pairs might be incomplete.")
        else:
            logger.info(f"Loaded spot channels from manifest: {spot_channels_from_manifest}")

        # 2. Find and load the merged data
        unmixed_spots_prefix = f"{PROCESSED_DATA_ROOT_PREFIX}/image_spot_spectral_unmixing/"
        
        logger.info(f"Loading merged spots data for dataset: {PROCESSED_DATA_ROOT_PREFIX}")
        try:
            df_polars = load_and_merge_spots_from_s3(
                REAL_SPOTS_BUCKET, 
                PROCESSED_DATA_ROOT_PREFIX, 
                unmixed_spots_prefix
            )
            if df_polars is None:
                logger.error("Failed to load merged DataFrame from S3/cache.")
                return JSONResponse(status_code=500, content={'error': 'Failed to load spots data'})
            logger.info(f"Loaded merged Polars DataFrame shape: {df_polars.shape}")
            
            # Convert to pandas for frontend compatibility
            df = df_polars.to_pandas()
            logger.info(f"Converted to pandas DataFrame shape: {df.shape}")

            # Update cache
            df_cache["data"] = df
            df_cache["last_loaded"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            df_cache["target_key"] = f"{unmixed_spots_prefix}merged"
            df_cache["processing_manifest"] = processing_manifest
            df_cache["spot_channels_from_manifest"] = spot_channels_from_manifest
            logger.info(f"Updated DataFrame cache at {df_cache['last_loaded']}")
        except Exception as e:
            logger.error(f"Exception during DataFrame loading: {e}", exc_info=True)
            return JSONResponse(status_code=500, content={'error': 'Error loading spots data'})

    # 3. Find and load related files (ratios and summary_stats)
    ratios_data = None
    summary_stats_data = None

    related_files_prefix = f"{PROCESSED_DATA_ROOT_PREFIX}/image_spot_spectral_unmixing/"
    unmixed_target_key = find_unmixed_spots_file(
        REAL_SPOTS_BUCKET, related_files_prefix, "unmixed_spots_*.pkl"
    )
    
    if unmixed_target_key:
        related_files = find_related_files(REAL_SPOTS_BUCKET, related_files_prefix, unmixed_target_key)
        logger.info(f"Searching for related files in '{related_files_prefix}'. Found: {related_files}")

        # Load ratios file if found
        if related_files['ratios']:
            ratios_data = load_ratios_from_s3(REAL_SPOTS_BUCKET, related_files['ratios'])
            if ratios_data is not None:
                logger.info(f"Loaded ratios matrix with shape: {ratios_data.shape}")
        
        # Load summary stats file if found
        if related_files['summary_stats']:
            summary_stats_df = load_summary_stats_from_s3(REAL_SPOTS_BUCKET, related_files['summary_stats'])
            if summary_stats_df is not None:
                logger.info(f"Loaded summary stats with shape: {summary_stats_df.shape}")
                summary_stats_data = summary_stats_df.to_dict(orient='records')
                logger.info(f"Prepared {len(summary_stats_data)} summary stat records")

    # 4. Subsample the data
    if len(df) > sample_size:
        logger.info(f"Subsampling DataFrame from {len(df)} to {sample_size} rows.")
        plot_df = df.sample(n=sample_size, random_state=None).copy()
    else:
        plot_df = df.copy()
    logger.info(f"Plotting DataFrame shape: {plot_df.shape}")

    # Add 'reassigned' column indicating where chan != unmixed_chan
    plot_df['reassigned'] = plot_df['chan'] != plot_df['unmixed_chan']
    logger.info(f"Added 'reassigned' column. {plot_df['reassigned'].sum()} spots were reassigned.")

    # 5. Determine available channels and pairs
    try:
        if spot_channels_from_manifest:
            channels = sorted(spot_channels_from_manifest)
            channel_pairs = list(itertools.combinations(channels, 2))
            logger.info(f"Using channel pairs from manifest: {channel_pairs}")
        else:
            logger.warning("Falling back to deriving channel pairs from DataFrame columns.")
            channel_pairs = get_channel_pairs(pl.from_pandas(plot_df))

        if not channel_pairs:
             logger.error("Could not determine channel pairs from manifest or DataFrame columns.")
             return JSONResponse(status_code=500, content={'error': 'Could not determine channel pairs'})
        logger.info(f"Found channel pairs: {channel_pairs}")
    except Exception as e:
        logger.error(f"Error determining channel pairs: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={'error': 'Error processing channel data'})

    # 6. Prepare data for JSON response
    required_cols = ['spot_id', 'chan', 'r', 'dist', 'unmixed_chan', 'reassigned']
    intensity_cols = [f'chan_{c}_intensity' for pair in channel_pairs for c in pair]
    all_needed_cols = list(dict.fromkeys(required_cols + intensity_cols))

    # Check if all needed columns exist
    missing_cols = [col for col in all_needed_cols if col not in plot_df.columns]
    if missing_cols:
        logger.error(f"Missing required columns in DataFrame: {missing_cols}")
        return JSONResponse(status_code=500, content={'error': f'Missing columns: {missing_cols}'})

    plot_df_subset = plot_df[all_needed_cols].copy()

    # Create spot details for neuroglancer
    detail_cols = ['spot_id', 'cell_id', 'round', 'z', 'y', 'x']
    available_detail_cols = [col for col in detail_cols if col in plot_df.columns]
    
    if len(available_detail_cols) > 1:
        logger.info(f"Creating spot_details with columns: {available_detail_cols}")
        spot_details_df = plot_df[available_detail_cols].copy()
        
        spot_details = {
            str(row['spot_id']): {
                col: row[col] for col in available_detail_cols if col != 'spot_id'
            }
            for _, row in spot_details_df.iterrows()
        }
        logger.info(f"Created spot_details dictionary with {len(spot_details)} entries")
    else:
        spot_details = {}
        logger.warning("Could not create spot_details: required columns not found in DataFrame")

    # 7. Generate fused S3 paths
    base_fuse_path = f"s3://{REAL_SPOTS_BUCKET}/{PROCESSED_DATA_ROOT_PREFIX}/image_tile_fusing/fused"

    if spot_channels_from_manifest:
        chs_for_fused_paths = spot_channels_from_manifest
    else:
        logger.warning("Spot channels from manifest not available for generating fused paths.")
        if channel_pairs:
             unique_channels_from_pairs = sorted(list(set(itertools.chain(*channel_pairs))))
             chs_for_fused_paths = unique_channels_from_pairs
        else:
             logger.error("Cannot determine channels for fused paths.")
             chs_for_fused_paths = []

    fused_s3_paths = [f"{base_fuse_path}/channel_{ch}.zarr" for ch in chs_for_fused_paths]
    logger.info(f"Generated fused S3 paths: {fused_s3_paths}")

    # 8. Convert DataFrame to list of records (dictionaries)
    try:
        data_for_frontend = plot_df_subset.to_dict(orient='records')
        logger.info(f"Prepared {len(data_for_frontend)} records for frontend.")
    except Exception as e:
        logger.error(f"Error converting DataFrame to dict: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={'error': 'Error formatting data'})

    # 9. Prepare ratios data for JSON response
    ratios_json = None
    if ratios_data is not None:
        ratios_json = ratios_data.tolist()
        logger.info("Converted ratios matrix to JSON serializable format")

    # 10. Build the response
    response = {
        "channel_pairs": channel_pairs,
        "spots_data": data_for_frontend,
        "spot_details": spot_details,
        "fused_s3_paths": fused_s3_paths
    }
    
    if ratios_json:
        response["ratios"] = ratios_json
    
    if summary_stats_data:
        response["summary_stats"] = summary_stats_data
    
    return response

@app.get("/")
@app.get("/unmixed-spots")
async def unmixed_spots_page(request: Request):
    logger.info("Unmixed spots page accessed")
    return templates.TemplateResponse("unmixed_spots.html", {"request": request})

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8000)