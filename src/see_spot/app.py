from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import numpy as np
import pandas as pd
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
    load_processing_manifest_from_s3, load_and_merge_spots_from_s3,
    find_processing_manifest
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
S3_BUCKET = "aind-open-data"
DATA_PREFIX = "HCR_749315_2025-05-08_14-00-00_processed_2025-05-17_22-15-31"  # set default for app load
SAMPLE_SIZE = 5000

# In-memory cache for DataFrame to avoid reloading on every request
df_cache = {
    "data": None,
    "last_loaded": None,
    "target_key": None,
    "processing_manifest": None,
    "spot_channels_from_manifest": None,
    "sankey_data": None  # Cache Sankey data to avoid recalculation
}


def get_channel_pairs(df: pl.DataFrame) -> List[Tuple[str, str]]:
    """Extracts channel pairs from intensity column names."""
    intensity_cols = [col for col in df.columns if col.endswith('_intensity')]
    channels = sorted([col.split('_')[1] for col in intensity_cols])
    return list(itertools.combinations(channels, 2))

def calculate_sankey_data_from_polars(df_polars: pl.DataFrame) -> Dict[str, Any]:
    """
    Calculate Sankey diagram data directly from Polars DataFrame for maximum performance.
    
    Args:
        df_polars: Polars DataFrame with all spots
        
    Returns:
        Dictionary containing nodes and links for Sankey diagram
    """
    logger.info(f"Calculating Sankey data from {df_polars.height} total spots using native Polars")
    
    # Handle removed/none spots efficiently
    df_processed = df_polars.with_columns([
        pl.when(
            (pl.col('unmixed_chan').is_null()) |
            (pl.col('unmixed_chan') == 'none') |
            (pl.col('unmixed_chan') == '')
        ).then(pl.lit('Removed'))
        .otherwise(pl.col('unmixed_chan'))
        .alias('final_chan')
    ])
    
    # Count flows efficiently using group_by
    flow_counts = (
        df_processed
        .group_by(['chan', 'final_chan'])
        .agg(pl.len().alias('count'))
        .sort(['chan', 'final_chan'])
    )
    
    # Get unique channels efficiently
    original_channels = df_processed.select(pl.col('chan').unique().sort()).to_series().to_list()
    final_channels = df_processed.select(pl.col('final_chan').unique().sort()).to_series().to_list()
    
    # Sort final channels to put 'Removed' last
    final_channels = sorted(final_channels, key=lambda x: (x == 'Removed', x))
    
    logger.info(f"Found {len(original_channels)} original channels and {len(final_channels)} final channels")
    
    # Create nodes for Sankey diagram
    nodes = []
    
    # Add original channel nodes (sources)
    for chan in original_channels:
        nodes.append({
            "name": f"{chan} (Original)",
            "category": "original",
            "channel": chan
        })
    
    # Add final channel nodes (targets)
    for chan in final_channels:
        nodes.append({
            "name": f"{chan} (Final)",
            "category": "final",
            "channel": chan
        })
    
    # Create links for Sankey diagram
    links = []
    total_spots = df_polars.height
    min_threshold = max(5, int(total_spots * 0.001))  # At least 5 spots or 0.1% of data
    
    # Convert Polars result to Python for processing
    flow_data = flow_counts.to_dicts()
    
    for flow in flow_data:
        count = flow['count']
        if count >= min_threshold:  # Only include significant flows
            original = flow['chan']
            final = flow['final_chan']
            
            flow_type = "unchanged" if original == final else ("removed" if final == "Removed" else "reassigned")
            
            links.append({
                "source": f"{original} (Original)",
                "target": f"{final} (Final)",
                "value": count,
                "flow_type": flow_type,
                "percentage": round((count / total_spots) * 100, 2)
            })
    
    logger.info(f"Created Sankey data: {len(nodes)} nodes, {len(links)} links (threshold: {min_threshold})")
    
    return {
        "nodes": nodes,
        "links": links,
        "total_spots": total_spots,
        "threshold_used": min_threshold
    }


def calculate_sankey_data(df: Any) -> Dict[str, Any]:
    """
    Calculate Sankey diagram data from the full dataset showing flows from original to final channels.
    Optimized using Polars for better performance.
    
    Args:
        df: Full pandas DataFrame with all spots
        
    Returns:
        Dictionary containing nodes and links for Sankey diagram
    """
    logger.info(f"Calculating Sankey data from {len(df)} total spots using Polars optimization")
    
    # Convert to Polars for faster processing
    df_polars = pl.from_pandas(df[['chan', 'unmixed_chan']].copy())
    
    # Handle removed/none spots efficiently
    df_polars = df_polars.with_columns([
        pl.when(
            (pl.col('unmixed_chan').is_null()) |
            (pl.col('unmixed_chan') == 'none') |
            (pl.col('unmixed_chan') == '')
        ).then(pl.lit('Removed'))
        .otherwise(pl.col('unmixed_chan'))
        .alias('final_chan')
    ])
    
    # Count flows efficiently using group_by
    flow_counts = (
        df_polars
        .group_by(['chan', 'final_chan'])
        .agg(pl.len().alias('count'))
        .sort(['chan', 'final_chan'])
    )
    
    # Get unique channels
    original_channels = df_polars.select(pl.col('chan').unique().sort()).to_series().to_list()
    final_channels = df_polars.select(pl.col('final_chan').unique().sort()).to_series().to_list()
    
    # Sort final channels to put 'Removed' last
    final_channels = sorted(final_channels, key=lambda x: (x == 'Removed', x))
    
    logger.info(f"Found {len(original_channels)} original channels and {len(final_channels)} final channels")
    
    # Create nodes for Sankey diagram
    nodes = []
    
    # Add original channel nodes (sources)
    for chan in original_channels:
        nodes.append({
            "name": f"{chan} (Original)",
            "category": "original",
            "channel": chan
        })
    
    # Add final channel nodes (targets)
    for chan in final_channels:
        nodes.append({
            "name": f"{chan} (Final)",
            "category": "final",
            "channel": chan
        })
    
    # Create links for Sankey diagram
    links = []
    total_spots = len(df)
    min_threshold = max(5, int(total_spots * 0.001))  # At least 5 spots or 0.1% of data
    
    # Convert Polars result to Python for processing
    flow_data = flow_counts.to_dicts()
    
    for flow in flow_data:
        count = flow['count']
        if count >= min_threshold:  # Only include significant flows
            original = flow['chan']
            final = flow['final_chan']
            
            flow_type = "unchanged" if original == final else ("removed" if final == "Removed" else "reassigned")
            
            links.append({
                "source": f"{original} (Original)",
                "target": f"{final} (Final)",
                "value": count,
                "flow_type": flow_type,
                "percentage": round((count / total_spots) * 100, 2)
            })
    
    logger.info(f"Created Sankey data: {len(nodes)} nodes, {len(links)} links (threshold: {min_threshold})")
    
    return {
        "nodes": nodes,
        "links": links,
        "total_spots": total_spots,
        "threshold_used": min_threshold
    }


@app.get("/api/real_spots_data")
async def get_real_spots_data(
    sample_size: int = SAMPLE_SIZE,
    force_refresh: bool = False,
    valid_spots_only: bool = False
):
    logger.info(f"Real spots data requested with sample size: {sample_size}, "
                f"force_refresh: {force_refresh}, valid_spots_only: {valid_spots_only}")

    # Check if we can use cached DataFrame
    if not force_refresh and df_cache["data"] is not None:
        logger.info(f"Using cached DataFrame from {df_cache['last_loaded']}. Shape: {df_cache['data'].shape}")
        df = df_cache["data"]
        # Load manifest and channels from cache if available, or re-fetch
        processing_manifest = df_cache.get("processing_manifest")
        spot_channels_from_manifest = df_cache.get("spot_channels_from_manifest")
        if not processing_manifest or not spot_channels_from_manifest:
            # Find manifest in either top level or derived folder
            manifest_key = find_processing_manifest(S3_BUCKET, DATA_PREFIX)
            if not manifest_key:
                logger.error(f"Could not find processing_manifest.json for dataset {DATA_PREFIX}")
                spot_channels_from_manifest = []
            else:
                logger.info(f"Attempting to load processing manifest from: s3://{S3_BUCKET}/{manifest_key}")
                processing_manifest = load_processing_manifest_from_s3(S3_BUCKET, manifest_key)
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
        manifest_key = find_processing_manifest(S3_BUCKET, DATA_PREFIX)
        if not manifest_key:
            logger.error(f"Could not find processing_manifest.json for dataset {DATA_PREFIX}.")
            return JSONResponse(status_code=500, content={'error': 'Failed to find processing manifest'})
        
        logger.info(f"Attempting to load processing manifest from: s3://{S3_BUCKET}/{manifest_key}")
        processing_manifest = load_processing_manifest_from_s3(S3_BUCKET, manifest_key)

        if not processing_manifest:
            logger.error(f"Could not load processing manifest from {manifest_key}.")
            return JSONResponse(status_code=500, content={'error': 'Failed to load processing manifest'})

        spot_channels_from_manifest = processing_manifest.get("spot_channels", [])
        if not spot_channels_from_manifest:
            logger.warning("No 'spot_channels' found in manifest. Channel pairs might be incomplete.")
        else:
            logger.info(f"Loaded spot channels from manifest: {spot_channels_from_manifest}")

        # 2. Find and load the merged data
        unmixed_spots_prefix = f"{DATA_PREFIX}/image_spot_spectral_unmixing/"
        
        logger.info(f"Loading merged spots data for dataset: {DATA_PREFIX}")
        try:
            df_polars = load_and_merge_spots_from_s3(
                S3_BUCKET, 
                DATA_PREFIX, 
                unmixed_spots_prefix,
                valid_spots_only
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
            df_cache["sankey_data"] = None  # Clear cached Sankey data when new data is loaded
            logger.info(f"Updated DataFrame cache at {df_cache['last_loaded']}")
        except Exception as e:
            logger.error(f"Exception during DataFrame loading: {e}", exc_info=True)
            return JSONResponse(status_code=500, content={'error': 'Error loading spots data'})

    # 3. Find and load related files (ratios and summary_stats)
    ratios_data = None
    summary_stats_data = None

    related_files_prefix = f"{DATA_PREFIX}/image_spot_spectral_unmixing/"
    unmixed_target_key = find_unmixed_spots_file(
        S3_BUCKET, related_files_prefix, "unmixed_spots_*.pkl"
    )
    
    if unmixed_target_key:
        related_files = find_related_files(S3_BUCKET, related_files_prefix, unmixed_target_key)
        logger.info(f"Searching for related files in '{related_files_prefix}'. Found: {related_files}")

        # Load ratios file if found
        if related_files['ratios']:
            ratios_data = load_ratios_from_s3(S3_BUCKET, related_files['ratios'])
            if ratios_data is not None:
                logger.info(f"Loaded ratios matrix with shape: {ratios_data.shape}")
        
        # Load summary stats file if found
        if related_files['summary_stats']:
            summary_stats_df = load_summary_stats_from_s3(S3_BUCKET, related_files['summary_stats'])
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
    required_cols = ['spot_id', 'chan', 'r', 'dist', 'unmixed_chan', 'reassigned', 'unmixed_removed']
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
    base_fuse_path = f"s3://{S3_BUCKET}/{DATA_PREFIX}/image_tile_fusing/fused"

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

    # 10. Calculate Sankey flow data from full dataset
    sankey_data = None
    
    # Check if we can use cached Sankey data
    if not force_refresh and df_cache.get("sankey_data") is not None:
        logger.info("Using cached Sankey data")
        sankey_data = df_cache["sankey_data"]
    else:
        try:
            # Try to use the most efficient method available
            if 'df_polars' in locals() and df_polars is not None:
                logger.info("Using native Polars DataFrame for Sankey calculation")
                sankey_data = calculate_sankey_data_from_polars(df_polars)
            else:
                logger.info("Converting pandas to Polars for Sankey calculation")
                # For cached data, convert to Polars for faster processing
                if 'chan' in df.columns and 'unmixed_chan' in df.columns:
                    df_polars_temp = pl.from_pandas(df[['chan', 'unmixed_chan']].copy())
                    sankey_data = calculate_sankey_data_from_polars(df_polars_temp)
                else:
                    logger.warning("Required columns not found, falling back to pandas method")
                    sankey_data = calculate_sankey_data(df)  # Use full dataset, not sampled
            
            # Cache the calculated Sankey data
            df_cache["sankey_data"] = sankey_data
            logger.info(f"Calculated and cached Sankey data: {len(sankey_data['nodes'])} nodes, "
                        f"{len(sankey_data['links'])} links")
        except Exception as e:
            logger.error(f"Error calculating Sankey data: {e}", exc_info=True)

    # 11. Build the response
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

    if sankey_data:
        response["sankey_data"] = sankey_data

    return response


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
        cache_path = Path("/s3-cache") / S3_BUCKET
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
                        "is_current": dataset_dir.name == DATA_PREFIX
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
        manifest_key = find_processing_manifest(S3_BUCKET, dataset_name)
        
        if not manifest_key:
            return JSONResponse(
                status_code=404, 
                content={
                    "error": f"Dataset not found on S3 - processing_manifest.json not found",
                    "checked_paths": [
                        f"s3://{S3_BUCKET}/{dataset_name}/processing_manifest.json",
                        f"s3://{S3_BUCKET}/{dataset_name}/derived/processing_manifest.json"
                    ]
                }
            )
        
        logger.info(f"Found dataset manifest at: s3://{S3_BUCKET}/{manifest_key}")
        
        # Download the processing manifest first
        manifest_local_path = s3_handler.download_file(
            key=manifest_key,
            bucket_name=S3_BUCKET,
            use_cache=True
        )
        
        if manifest_local_path is None:
            return JSONResponse(status_code=500, content={"error": "Failed to download processing manifest"})
        
        # Download the unmixed spots file (for merging and related files)
        spots_key = f"{dataset_name}/image_spot_spectral_unmixing/"
        spots_file = find_unmixed_spots_file(S3_BUCKET, spots_key, "unmixed_spots_*.pkl")
        
        if not spots_file:
            return JSONResponse(
                status_code=404, 
                content={
                    "error": "Spots data file not found",
                    "checked_path": f"s3://{S3_BUCKET}/{spots_key}unmixed_spots_*.pkl"
                }
            )
        
        # Try to create the merged parquet file by calling our new merge function
        try:
            merged_df = load_and_merge_spots_from_s3(S3_BUCKET, dataset_name, spots_key)
            if merged_df is not None:
                logger.info(f"Successfully created merged parquet file for dataset {dataset_name}")
            else:
                logger.warning(f"Could not create merged parquet file for dataset {dataset_name}")
        except Exception as e:
            logger.warning(f"Error creating merged parquet file: {e}")
            # Continue anyway - the individual files will still be available
        
        # Try to download related files (ratios and summary stats)
        related_files = find_related_files(S3_BUCKET, spots_key, spots_file)
        
        downloaded_files = [str(manifest_local_path)]
        
        # Add the parquet file to downloaded files if it was created
        parquet_file = Path("/s3-cache") / S3_BUCKET / dataset_name / f"{dataset_name}.parquet"
        if parquet_file.exists():
            downloaded_files.append(str(parquet_file))
        
        if related_files['ratios']:
            ratios_local_path = s3_handler.download_file(
                key=related_files['ratios'],
                bucket_name=S3_BUCKET,
                use_cache=True
            )
            if ratios_local_path:
                downloaded_files.append(str(ratios_local_path))
        
        if related_files['summary_stats']:
            stats_local_path = s3_handler.download_file(
                key=related_files['summary_stats'],
                bucket_name=S3_BUCKET,
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
        cache_path = Path("/s3-cache") / S3_BUCKET / dataset_name
        if not cache_path.exists():
            return JSONResponse(status_code=404, content={"error": "Dataset not found in local cache"})
        
        # Update the global variable
        global DATA_PREFIX
        DATA_PREFIX = dataset_name
        
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