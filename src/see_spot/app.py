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
import pandas as pd
import itertools
from typing import List, Tuple, Dict, Any

# Import your modules
from see_spot.s3_handler import s3_handler
from see_spot.s3_utils import (
    find_unmixed_spots_file, find_related_files, 
    load_pkl_from_s3, load_ratios_from_s3, load_summary_stats_from_s3,
    load_processing_manifest_from_s3
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
# REAL_SPOTS_BUCKET = "codeocean-s3datasetsbucket-1u41qdg42ur9"
# REAL_SPOTS_PREFIX = "4c76aae1-d7f0-4ed7-b7f6-72b460f5724d/"


REAL_SPOTS_BUCKET = "aind-open-data"
# Define the root prefix for a processed dataset.
# This will be the base for finding manifest, spots, and fused data.
# Example: "HCR_747667_2025-04-18_13-00-00_processed_2025-05-13_23-21-01"
#PROCESSED_DATA_ROOT_PREFIX = "HCR_747667_2025-04-18_13-00-00_processed_2025-05-13_23-21-01"
#PROCESSED_DATA_ROOT_PREFIX = "HCR_749315_2025-04-18_13-00-00_processed_2025-05-13_23-47-40"
#PROCESSED_DATA_ROOT_PREFIX = "HCR_749315_2025-04-25_13-00-00_processed_2025-05-17_22-13-57"
PROCESSED_DATA_ROOT_PREFIX = "HCR_749315_2025-05-08_14-00-00_processed_2025-05-17_22-15-31"
# REAL_SPOTS_PREFIX will be derived: PROCESSED_DATA_ROOT_PREFIX + "/image_spot_spectral_unmixing/"
# REAL_SPOTS_PATTERN is static: "unmixed_spots_*.pkl"
SAMPLE_SIZE = 10000 # Number of points to send to frontend

# In-memory cache for DataFrame to avoid reloading on every request
df_cache = {
    "data": None,
    "last_loaded": None,
    "target_key": None,
    "processing_manifest": None,
    "spot_channels_from_manifest": None
}

def get_channel_pairs(df: pd.DataFrame) -> List[Tuple[str, str]]:
    """Extracts channel pairs from intensity column names."""
    intensity_cols = [col for col in df.columns if col.endswith('_intensity')]
    channels = sorted([col.split('_')[1] for col in intensity_cols]) # Extract e.g., '488'
    return list(itertools.combinations(channels, 2))

@app.get("/api/real_spots_data")
async def get_real_spots_data(sample_size: int = SAMPLE_SIZE, force_refresh: bool = False):
    logger.info(f"Real spots data requested with sample size: {sample_size}, force_refresh: {force_refresh}")

    # Check if we can use cached DataFrame
    if not force_refresh and df_cache["data"] is not None:
        logger.info(f"Using cached DataFrame from {df_cache['last_loaded']}. Shape: {df_cache['data'].shape}")
        df = df_cache["data"]
        # We still need to find related files if they weren't cached
        target_key = df_cache.get("target_key")
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
                # Fallback or error handling if manifest is crucial for channel pairs
                spot_channels_from_manifest = [] # Default to empty or handle error response
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
            # Potentially return an error if these are strictly required later on
        else:
            logger.info(f"Loaded spot channels from manifest: {spot_channels_from_manifest}")


        # 2. Find the data file
        unmixed_spots_prefix = f"{PROCESSED_DATA_ROOT_PREFIX}/image_spot_spectral_unmixing/"
        unmixed_spots_pattern = "unmixed_spots_*.pkl"
        target_key = find_unmixed_spots_file(
            REAL_SPOTS_BUCKET, unmixed_spots_prefix, unmixed_spots_pattern
        )
        if not target_key:
            logger.error(f"Could not find spots file matching pattern '{unmixed_spots_pattern}' in '{unmixed_spots_prefix}'.")
            return JSONResponse(status_code=404, content={'error': 'Spots data file not found on S3'})

        # 3. Load the data (uses caching via download_file)
        logger.info(f"Loading data from s3://{REAL_SPOTS_BUCKET}/{target_key}")
        try:
            df = load_pkl_from_s3(REAL_SPOTS_BUCKET, target_key)
            if df is None:
                logger.error("Failed to load DataFrame from S3/cache.")
                return JSONResponse(status_code=500, content={'error': 'Failed to load spots data'})
            logger.info(f"Loaded DataFrame shape: {df.shape}")

            # Update cache
            df_cache["data"] = df
            df_cache["last_loaded"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            df_cache["target_key"] = target_key
            df_cache["processing_manifest"] = processing_manifest # Cache manifest
            df_cache["spot_channels_from_manifest"] = spot_channels_from_manifest # Cache channels
            logger.info(f"Updated DataFrame cache at {df_cache['last_loaded']}")
        except Exception as e:
            logger.error(f"Exception during DataFrame loading: {e}", exc_info=True)
            return JSONResponse(status_code=500, content={'error': 'Error loading spots data'})

    # 4. Find and load related files (ratios and summary_stats)
    # Related files are expected to be in the same directory as the target_key (unmixed_spots file)
    ratios_data = None
    summary_stats_data = None

    if target_key:
        # The prefix for related files should be the directory of the target_key
        related_files_prefix = str(Path(target_key).parent) + "/" # Ensure trailing slash
        related_files = find_related_files(REAL_SPOTS_BUCKET, related_files_prefix, target_key)
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
                # Convert to list of records for JSON serialization
                summary_stats_data = summary_stats_df.to_dict(orient='records')
                logger.info(f"Prepared {len(summary_stats_data)} summary stat records")

    # 5. Subsample the data
    if len(df) > sample_size:
        logger.info(f"Subsampling DataFrame from {len(df)} to {sample_size} rows.")
        plot_df = df.sample(n=sample_size, random_state=None).copy() # Use None for true randomness on each call
    else:
        plot_df = df.copy()
    logger.info(f"Plotting DataFrame shape: {plot_df.shape}")

    # Add 'reassigned' column indicating where chan != unmixed_chan
    plot_df['reassigned'] = plot_df['chan'] != plot_df['unmixed_chan']
    logger.info(f"Added 'reassigned' column. {plot_df['reassigned'].sum()} spots were reassigned.")

    # 6. Determine available channels and pairs
    try:
        # If spot_channels_from_manifest is available, use it. Otherwise, fallback to get_channel_pairs.
        if spot_channels_from_manifest:
            # Assuming spot_channels_from_manifest is a list like ['488', '514', '561']
            channels = sorted(spot_channels_from_manifest)
            channel_pairs = list(itertools.combinations(channels, 2))
            logger.info(f"Using channel pairs from manifest: {channel_pairs}")
        else:
            # Fallback to deriving from DataFrame columns if manifest or channels are missing
            logger.warning("Falling back to deriving channel pairs from DataFrame columns.")
            channel_pairs = get_channel_pairs(plot_df)

        if not channel_pairs:
             logger.error("Could not determine channel pairs from manifest or DataFrame columns.")
             return JSONResponse(status_code=500, content={'error': 'Could not determine channel pairs'})
        logger.info(f"Found channel pairs: {channel_pairs}")
    except Exception as e:
        logger.error(f"Error determining channel pairs: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={'error': 'Error processing channel data'})

    # 7. Prepare data for JSON response
    # Select columns needed for plotting and table population
    required_cols = ['spot_id', 'chan', 'r', 'dist', 'unmixed_chan', 'reassigned']
    intensity_cols = [f'chan_{c}_intensity' for pair in channel_pairs for c in pair]
    # Ensure unique columns
    all_needed_cols = list(dict.fromkeys(required_cols + intensity_cols))

    # Check if all needed columns exist
    missing_cols = [col for col in all_needed_cols if col not in plot_df.columns]
    if missing_cols:
        logger.error(f"Missing required columns in DataFrame: {missing_cols}")
        return JSONResponse(status_code=500, content={'error': f'Missing columns: {missing_cols}'})

    # Select the subset of columns for plotting
    plot_df_subset = plot_df[all_needed_cols].copy()

    # Create a separate DataFrame for spot details with neuroglancer coordinates
    detail_cols = ['spot_id', 'cell_id', 'round', 'z', 'y', 'x']
    
    # Check which of these columns exist in the original data
    available_detail_cols = [col for col in detail_cols if col in plot_df.columns]
    
    if len(available_detail_cols) > 1:  # At least spot_id and one more column
        logger.info(f"Creating spot_details with columns: {available_detail_cols}")
        spot_details_df = plot_df[available_detail_cols].copy()
        
        # Convert to dictionary keyed by spot_id for fast lookup
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

    # 8. Generate the fused S3 paths
    # base_fuse_path is derived from PROCESSED_DATA_ROOT_PREFIX
    base_fuse_path = f"s3://{REAL_SPOTS_BUCKET}/{PROCESSED_DATA_ROOT_PREFIX}/image_tile_fusing/fused"

    # Use spot_channels_from_manifest if available, otherwise fallback to a default or error
    if spot_channels_from_manifest:
        chs_for_fused_paths = spot_channels_from_manifest
    else:
        # Fallback: if manifest channels are not available, what should we do?
        # Option 1: Use channels derived from plot_df (less reliable for *all* fused data)
        # Option 2: Use a hardcoded default list (as it was before)
        # Option 3: Return an error or an empty list for fused_s3_paths
        logger.warning("Spot channels from manifest not available for generating fused paths. Using unique channels from current channel_pairs as a fallback.")
        # Derive unique channels from the current channel_pairs (which might also be a fallback)
        if channel_pairs:
             unique_channels_from_pairs = sorted(list(set(itertools.chain(*channel_pairs))))
             chs_for_fused_paths = unique_channels_from_pairs
        else:
             logger.error("Cannot determine channels for fused paths. Manifest missing and no channel pairs found.")
             chs_for_fused_paths = [] # Or handle as an error state

    fused_s3_paths = [f"{base_fuse_path}/channel_{ch}.zarr" for ch in chs_for_fused_paths]
    logger.info(f"Generated fused S3 paths: {fused_s3_paths}")

    # 9. Convert DataFrame to list of records (dictionaries)
    try:
        data_for_frontend = plot_df_subset.to_dict(orient='records')
        logger.info(f"Prepared {len(data_for_frontend)} records for frontend.")
    except Exception as e:
        logger.error(f"Error converting DataFrame to dict: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={'error': 'Error formatting data'})

    # 10. Prepare ratios data for JSON response
    ratios_json = None
    if ratios_data is not None:
        ratios_json = ratios_data.tolist()
        logger.info(f"Converted ratios matrix to JSON serializable format")

    # 11. Build the response
    response = {
        "channel_pairs": channel_pairs,
        "spots_data": data_for_frontend,
        "spot_details": spot_details,
        "fused_s3_paths": fused_s3_paths
    }
    
    # Add optional data if available
    if ratios_json:
        response["ratios"] = ratios_json
    
    if summary_stats_data:
        response["summary_stats"] = summary_stats_data
    
    return response

# Add a new endpoint for creating neuroglancer links
@app.post("/api/create-neuroglancer-link")
async def create_neuroglancer_link(request: Request):
    """Creates a neuroglancer link with a point annotation at specified coordinates."""
    # Parse the JSON data from the request
    data = await request.json()
    
    # Extract the parameters from the request
    fused_s3_paths = data.get("fused_s3_paths")
    position = data.get("position")
    point_annotation = data.get("point_annotation")
    cell_id = data.get("cell_id", 42)  # Default value if not provided
    spot_id = data.get("spot_id")
    annotation_color = data.get("annotation_color", "#FFFF00")
    cross_section_scale = data.get("cross_section_scale", 1.0)
    
    # Input validation
    if not fused_s3_paths or not position or not point_annotation or not spot_id:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing required parameters: fused_s3_paths, position, point_annotation, or spot_id"}
        )
    
    try:
        # Import the ng_utils module
        from see_spot import ng_utils
        
        # Create the neuroglancer link
        ng_link = ng_utils.create_link_no_upload(
            fused_s3_paths,
            annotation_color=annotation_color,
            cross_section_scale=cross_section_scale,
            cell_id=cell_id,
            spot_id=spot_id,
            position=position,
            point_annotation=point_annotation
        )
        
        return {"url": ng_link}
    except Exception as e:
        logger.error(f"Error creating neuroglancer link: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to create neuroglancer link: {str(e)}"}
        )

@app.get("/api/datasets")
async def list_datasets():
    """List all available datasets in the local cache."""
    try:
        cache_path = Path("/s3-cache") / REAL_SPOTS_BUCKET
        datasets = []
        
        if cache_path.exists():
            for dataset_dir in cache_path.iterdir():
                if dataset_dir.is_dir() and not dataset_dir.name.startswith('.'):
                    # Get directory creation time
                    stat = dataset_dir.stat()
                    creation_time = datetime.fromtimestamp(stat.st_mtime)
                    
                    # Check if dataset has the required structure
                    spots_dir = dataset_dir / "image_spot_spectral_unmixing"
                    has_data = spots_dir.exists()
                    
                    datasets.append({
                        "name": dataset_dir.name,
                        "creation_date": creation_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "has_data": has_data,
                        "is_current": dataset_dir.name == PROCESSED_DATA_ROOT_PREFIX
                    })
        
        # Sort by creation date (newest first)
        datasets.sort(key=lambda x: x["creation_date"], reverse=True)
        
        return {"datasets": datasets}
    
    except Exception as e:
        logger.error(f"Error listing datasets: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/datasets/download")
async def download_dataset(request: Request):
    """Download a dataset from S3 to local cache."""
    try:
        data = await request.json()
        dataset_name = data.get("dataset_name")
        
        if not dataset_name:
            return JSONResponse(status_code=400, content={"error": "Dataset name is required"})
        
        # Check if dataset exists on S3 by looking for the processing manifest
        manifest_key = f"{dataset_name}/derived/processing_manifest.json"
        
        logger.info(f"Checking if dataset exists: s3://{REAL_SPOTS_BUCKET}/{manifest_key}")
        
        # Try to get the manifest to verify the dataset exists
        manifest_content = s3_handler.get_object(key=manifest_key, bucket_name=REAL_SPOTS_BUCKET)
        
        if manifest_content is None:
            return JSONResponse(
                status_code=404, 
                content={
                    "error": f"Dataset not found on S3",
                    "checked_path": f"s3://{REAL_SPOTS_BUCKET}/{manifest_key}"
                }
            )
        
        # Download the processing manifest first
        manifest_local_path = s3_handler.download_file(
            key=manifest_key,
            bucket_name=REAL_SPOTS_BUCKET,
            use_cache=True
        )
        
        if manifest_local_path is None:
            return JSONResponse(status_code=500, content={"error": "Failed to download processing manifest"})
        
        # Download the unmixed spots file
        spots_key = f"{dataset_name}/image_spot_spectral_unmixing/"
        spots_file = find_unmixed_spots_file(REAL_SPOTS_BUCKET, spots_key, "unmixed_spots_*.pkl")
        
        if not spots_file:
            return JSONResponse(
                status_code=404, 
                content={
                    "error": "Spots data file not found",
                    "checked_path": f"s3://{REAL_SPOTS_BUCKET}/{spots_key}unmixed_spots_*.pkl"
                }
            )
        
        # Download the spots file
        spots_local_path = s3_handler.download_file(
            key=spots_file,
            bucket_name=REAL_SPOTS_BUCKET,
            use_cache=True
        )
        
        if spots_local_path is None:
            return JSONResponse(status_code=500, content={"error": "Failed to download spots data"})
        
        # Try to download related files (ratios and summary stats)
        related_files = find_related_files(REAL_SPOTS_BUCKET, spots_key, spots_file)
        
        downloaded_files = [str(manifest_local_path), str(spots_local_path)]
        
        if related_files['ratios']:
            ratios_local_path = s3_handler.download_file(
                key=related_files['ratios'],
                bucket_name=REAL_SPOTS_BUCKET,
                use_cache=True
            )
            if ratios_local_path:
                downloaded_files.append(str(ratios_local_path))
        
        if related_files['summary_stats']:
            stats_local_path = s3_handler.download_file(
                key=related_files['summary_stats'],
                bucket_name=REAL_SPOTS_BUCKET,
                use_cache=True
            )
            if stats_local_path:
                downloaded_files.append(str(stats_local_path))
        
        return {
            "success": True,
            "dataset_name": dataset_name,
            "downloaded_files": downloaded_files
        }
    
    except Exception as e:
        logger.error(f"Error downloading dataset: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/datasets/set-active")
async def set_active_dataset(request: Request):
    """Set the active dataset for the application."""
    try:
        data = await request.json()
        dataset_name = data.get("dataset_name")
        
        if not dataset_name:
            return JSONResponse(status_code=400, content={"error": "Dataset name is required"})
        
        # Verify the dataset exists locally
        cache_path = Path("/s3-cache") / REAL_SPOTS_BUCKET / dataset_name
        if not cache_path.exists():
            return JSONResponse(status_code=404, content={"error": "Dataset not found in local cache"})
        
        # Update the global variable
        global PROCESSED_DATA_ROOT_PREFIX
        PROCESSED_DATA_ROOT_PREFIX = dataset_name
        
        # Clear the cache to force reload with new dataset
        df_cache["data"] = None
        df_cache["last_loaded"] = None
        df_cache["target_key"] = None
        df_cache["processing_manifest"] = None
        df_cache["spot_channels_from_manifest"] = None
        
        logger.info(f"Active dataset changed to: {dataset_name}")
        
        return {
            "success": True,
            "dataset_name": dataset_name,
            "message": "Active dataset updated successfully"
        }
    
    except Exception as e:
        logger.error(f"Error setting active dataset: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/")
@app.get("/unmixed-spots")
async def unmixed_spots_page(request: Request):
    logger.info("Unmixed spots page accessed")
    return templates.TemplateResponse("unmixed_spots.html", {"request": request})

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8000)