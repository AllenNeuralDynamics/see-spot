from typing import Optional, Dict, List, Tuple, Any
import polars as pl
import pandas as pd  # Keep for compatibility where needed
import numpy as np
from see_spot.s3_handler import s3_handler
from pathlib import Path
import fnmatch
import io
import json  # Added for JSON parsing

import logging
import tempfile
import os
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def merge_spots_tables(spots_mixed, spots_unmixed):
    """Merge mixed and unmixed spots tables using Polars.
    
    Args:
        spots_mixed (pl.DataFrame): Mixed spots DataFrame
        spots_unmixed (pl.DataFrame): Unmixed spots DataFrame
        
    Returns:
        pl.DataFrame: Merged DataFrame with unmixed_removed column
    """
    # Keep spot_id from mixed table (it's the main table)
    mixed_clean = spots_mixed.clone()
    unmixed_clean = spots_unmixed.drop('spot_id', strict=False)
    
    # Get columns that are unique to unmixed table
    mixed_cols = set(mixed_clean.columns)
    unmixed_cols = set(unmixed_clean.columns)
    unique_unmixed_cols = list(unmixed_cols - mixed_cols)
    
    # Keep only merge keys and unique columns from unmixed
    merge_keys = ['chan', 'chan_spot_id']
    select_cols = merge_keys + unique_unmixed_cols
    unmixed_subset = unmixed_clean.select(select_cols)
    
    # Perform left merge
    merged = mixed_clean.join(unmixed_subset, on=merge_keys, how='left')
    
    # Add unmixed_removed column - True where any unique unmixed column is null
    if unique_unmixed_cols:
        # Create condition: all unique unmixed columns are null
        null_conditions = [pl.col(col).is_null() for col in unique_unmixed_cols]
        all_null = pl.fold(True, lambda acc, x: acc & x, null_conditions)
        merged = merged.with_columns(unmixed_removed=all_null)
    else:
        merged = merged.with_columns(unmixed_removed=pl.lit(False))
    
    return merged


def find_mixed_spots_file(bucket: str, prefix: str, pattern: str) -> Optional[str]:
    """Finds the first mixed spots file matching the pattern within the prefix."""
    logger.info(f"Searching for mixed spots pattern '{pattern}' in bucket '{bucket}' with prefix '{prefix}'...")
    try:
        # List objects - consider increasing max_keys if many files share the prefix
        objects = s3_handler.list_objects(bucket_name=bucket, prefix=prefix, max_keys=200)
        if not objects:
            logger.warning(f"No objects found with prefix '{prefix}'.")
            return None

        found_files = []
        for key in objects:
            # Use Pathlib to easily get the filename part of the key
            filename = Path(key).name
            if fnmatch.fnmatch(filename, pattern):
                logger.info(f"Found matching mixed spots file: {key}")
                found_files.append(key)

        if not found_files:
            logger.warning(f"No mixed spots files matching pattern '{pattern}' found within the first {len(objects)} objects listed under prefix '{prefix}'.")
            return None

        if len(found_files) > 1:
             logger.warning(f"Multiple mixed spots files ({len(found_files)}) matching pattern found. Using the first one: {found_files[0]}")

        return found_files[0] # Return the full key of the first match

    except Exception as e:
        logger.error(f"Error listing or searching objects: {e}", exc_info=True) # Log traceback
        return None


def get_base_pattern_from_unmixed(unmixed_key: str) -> str:
    """Extract the round pattern (e.g., R3) from unmixed_spots_R3_minDist_3.pkl to find mixed_spots_R3.pkl"""
    filename = Path(unmixed_key).name
    # Extract pattern like R3 from unmixed_spots_R3_minDist_3.pkl
    parts = filename.split('_')
    for part in parts:
        if part.startswith('R') and part[1:].isdigit():
            return part
    return 'R3'  # Default fallback


def load_and_merge_spots_from_s3(bucket: str, dataset_name: str, unmixed_spots_prefix: str) -> Optional[pl.DataFrame]:
    """
    Load both mixed and unmixed spots files, merge them, cache as parquet, and return merged DataFrame.
    
    Args:
        bucket: S3 bucket name
        dataset_name: Dataset name (used for parquet filename)
        unmixed_spots_prefix: S3 prefix where spots files are located
        
    Returns:
        Merged Polars DataFrame or None if loading failed
    """
    cache_dir = Path("/s3-cache") / bucket / dataset_name
    parquet_file = cache_dir / f"{dataset_name}.parquet"
    
    # Check if merged parquet file already exists
    if parquet_file.exists():
        logger.info(f"Loading merged data from cached parquet: {parquet_file}")
        try:
            df = pl.read_parquet(parquet_file)
            # Filter for valid spots and return
            df_valid = df.filter(pl.col('valid_spot'))
            logger.info(f"Loaded DataFrame from parquet. Shape: {df_valid.shape}")
            return df_valid
        except Exception as e:
            logger.error(f"Error loading parquet file: {e}", exc_info=True)
            # Fall through to regenerate the file
    
    # Need to download, merge, and cache
    logger.info(f"Parquet file not found or corrupted. Downloading and merging spots files...")
    
    # 1. Find unmixed spots file
    unmixed_key = find_unmixed_spots_file(bucket, unmixed_spots_prefix, "unmixed_spots_*.pkl")
    if not unmixed_key:
        logger.error(f"Could not find unmixed spots file in {unmixed_spots_prefix}")
        return None
    
    # 2. Find mixed spots file based on pattern from unmixed file
    base_pattern = get_base_pattern_from_unmixed(unmixed_key)
    mixed_pattern = f"mixed_spots_{base_pattern}.pkl"
    mixed_key = find_mixed_spots_file(bucket, unmixed_spots_prefix, mixed_pattern)
    if not mixed_key:
        logger.error(f"Could not find mixed spots file matching pattern {mixed_pattern} in {unmixed_spots_prefix}")
        return None
    
    logger.info(f"Found unmixed file: {unmixed_key}")
    logger.info(f"Found mixed file: {mixed_key}")
    
    # 3. Download both files to /tmp
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        
        # Download unmixed file
        unmixed_tmp_path = tmp_dir_path / f"unmixed_{os.getpid()}.pkl"
        logger.info(f"Downloading unmixed file to {unmixed_tmp_path}")
        unmixed_local = s3_handler.download_file(
            key=unmixed_key,
            bucket_name=bucket,
            local_path=str(unmixed_tmp_path),
            use_cache=False
        )
        if not unmixed_local:
            logger.error("Failed to download unmixed spots file")
            return None
        
        # Download mixed file
        mixed_tmp_path = tmp_dir_path / f"mixed_{os.getpid()}.pkl"
        logger.info(f"Downloading mixed file to {mixed_tmp_path}")
        mixed_local = s3_handler.download_file(
            key=mixed_key,
            bucket_name=bucket,
            local_path=str(mixed_tmp_path),
            use_cache=False
        )
        if not mixed_local:
            logger.error("Failed to download mixed spots file")
            return None
        
        # 4. Load both DataFrames using Polars (via pandas for pickle support)
        try:
            logger.info("Loading unmixed spots DataFrame...")
            df_unmixed_pd = pd.read_pickle(unmixed_local)
            df_unmixed = pl.from_pandas(df_unmixed_pd)
            logger.info(f"Loaded unmixed DataFrame. Shape: {df_unmixed.shape}")
            
            logger.info("Loading mixed spots DataFrame...")
            df_mixed_pd = pd.read_pickle(mixed_local)
            df_mixed = pl.from_pandas(df_mixed_pd)
            logger.info(f"Loaded mixed DataFrame. Shape: {df_mixed.shape}")
        except Exception as e:
            logger.error(f"Error loading pickle files: {e}", exc_info=True)
            return None
        
        # 5. Merge the DataFrames
        try:
            logger.info("Merging DataFrames...")
            df_merged = merge_spots_tables(df_mixed, df_unmixed)
            logger.info(f"Merged DataFrame. Shape: {df_merged.shape}")
        except Exception as e:
            logger.error(f"Error merging DataFrames: {e}", exc_info=True)
            return None
        
        # 6. Add spot_id column based on index
        try:
            logger.info("Adding spot_id column...")
            df_merged = df_merged.with_row_index("spot_id")
            logger.info(f"Added spot_id column. Final shape: {df_merged.shape}")
        except Exception as e:
            logger.error(f"Error adding spot_id column: {e}", exc_info=True)
            return None
        
        # 7. Save merged result as parquet to cache
        try:
            # Ensure cache directory exists
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"Saving merged DataFrame to parquet: {parquet_file}")
            df_merged.write_parquet(parquet_file, compression='snappy')
            logger.info(f"Successfully saved merged data to {parquet_file}")
        except Exception as e:
            logger.error(f"Error saving parquet file: {e}", exc_info=True)
            # Continue anyway - we have the data in memory
        
        # 8. Filter for valid spots and return
        df_valid = df_merged.filter(pl.col('valid_spot'))
        logger.info(f"Returning valid spots DataFrame. Shape: {df_valid.shape}")
        return df_valid


def find_unmixed_spots_file(bucket: str, prefix: str, pattern: str) -> Optional[str]:
    """Finds the first S3 object key matching the pattern within the prefix."""
    logger.info(f"Searching for pattern '{pattern}' in bucket '{bucket}' with prefix '{prefix}'...")
    try:
        # List objects - consider increasing max_keys if many files share the prefix
        objects = s3_handler.list_objects(bucket_name=bucket, prefix=prefix, max_keys=200)
        if not objects:
            logger.warning(f"No objects found with prefix '{prefix}'.")
            return None

        found_files = []
        for key in objects:
            # Use Pathlib to easily get the filename part of the key
            filename = Path(key).name
            if fnmatch.fnmatch(filename, pattern):
                logger.info(f"Found matching file: {key}")
                found_files.append(key)

        if not found_files:
            logger.warning(f"No files matching pattern '{pattern}' found within the first {len(objects)} objects listed under prefix '{prefix}'.")
            # Consider adding logic here to list more objects if needed (pagination)
            return None

        if len(found_files) > 1:
             logger.warning(f"Multiple files ({len(found_files)}) matching pattern found. Using the first one: {found_files[0]}")

        return found_files[0] # Return the full key of the first match

    except Exception as e:
        logger.error(f"Error listing or searching objects: {e}", exc_info=True) # Log traceback
        return None

def find_related_files(bucket: str, prefix: str, spots_file: str) -> Dict[str, str]:
    """
    Find related ratios.txt and summary_stats.csv files based on the unmixed spots file pattern.
    
    Parameters:
    -----------
    bucket: str
        S3 bucket name
    prefix: str
        S3 prefix (folder path)
    spots_file: str
        Full key of the spots file that was found
    
    Returns:
    --------
    Dict[str, str]
        Dictionary with keys 'ratios' and 'summary_stats' pointing to file keys if found
    """
    result = {'ratios': None, 'summary_stats': None}
    
    try:
        # Extract base filename without extension
        spots_filename = Path(spots_file).stem
        base_pattern = spots_filename.replace('unmixed_spots', '*')
        
        # List objects in the same directory
        objects = s3_handler.list_objects(bucket_name=bucket, prefix=prefix, max_keys=200)
        print(objects)
        
        # Look for ratios.txt
        for key in objects:
            filename = Path(key).name
            if '_ratios.txt' in filename:
                logger.info(f"Found ratios file: {key}")
                result['ratios'] = key
                break
                
        # Look for summary_stats.csv
        for key in objects:
            filename = Path(key).name
            if 'summary_stats.csv' in filename:
                logger.info(f"Found summary stats file: {key}")
                result['summary_stats'] = key
                break
    
    except Exception as e:
        logger.error(f"Error finding related files: {e}", exc_info=True)
    
    return result

def load_ratios_from_s3(bucket: str, key: str) -> Optional[np.ndarray]:
    """
    Load a ratios.txt file from S3.
    
    Parameters:
    -----------
    bucket: str
        S3 bucket name
    key: str
        S3 key for the ratios file
    
    Returns:
    --------
    Optional[np.ndarray]
        Numpy array containing the ratios or None if loading failed
    """
    if not key:
        logger.warning("No ratios file key provided")
        return None
        
    logger.info(f"Loading ratios from s3://{bucket}/{key}")
    
    try:
        # Download the file content
        content = s3_handler.get_object(key=key, bucket_name=bucket)
        if content is None:
            logger.error(f"Failed to get object content for {key}")
            return None
            
        # Parse the content as a matrix of numbers
        content_str = content.decode('utf-8')
        rows = content_str.strip().split('\n')
        ratios_matrix = []
        
        for row in rows:
            # Split by tabs and convert to integers
            values = [int(val) for val in row.strip().split()]
            ratios_matrix.append(values)
            
        return np.array(ratios_matrix)
        
    except Exception as e:
        logger.error(f"Error loading ratios file: {e}", exc_info=True)
        return None

def load_summary_stats_from_s3(bucket: str, key: str) -> Optional[pd.DataFrame]:
    """
    Load a summary_stats.csv file from S3.
    
    Parameters:
    -----------
    bucket: str
        S3 bucket name
    key: str
        S3 key for the summary stats file
    
    Returns:
    --------
    Optional[pd.DataFrame]
        DataFrame containing the summary stats or None if loading failed
    """
    if not key:
        logger.warning("No summary stats file key provided")
        return None
        
    logger.info(f"Loading summary stats from s3://{bucket}/{key}")
    
    try:
        # Download the file content
        content = s3_handler.get_object(key=key, bucket_name=bucket)
        if content is None:
            logger.error(f"Failed to get object content for {key}")
            return None
            
        # Parse CSV
        df = pd.read_csv(io.BytesIO(content))
        
        # Add 'removed_spots' column
        if 'total_spots' in df.columns and 'kept_spots' in df.columns:
            df['removed_spots'] = df['total_spots'] - df['kept_spots']
            df['unchanged_spots'] = df['kept_spots'] - df['reassigned_spots']

        return df
        
    except Exception as e:
        logger.error(f"Error loading summary stats file: {e}", exc_info=True)
        return None


def get_s3_object_size(bucket: str, key: str) -> Optional[int]:
    """Gets the size of an S3 object in bytes using the handler."""
    logger.info(f"Checking size for object: s3://{bucket}/{key}")
    try:
        # Use the get_object_metadata method (assumes it was added to S3Handler)
        # Check if the method exists before calling
        if not hasattr(s3_handler, 'get_object_metadata'):
             logger.error("Error: S3Handler instance does not have 'get_object_metadata' method. Please add it to s3_handler.py.")
             return None

        metadata = s3_handler.get_object_metadata(key=key, bucket_name=bucket)

        if metadata and 'ContentLength' in metadata and metadata['ContentLength'] is not None:
            size_bytes = metadata['ContentLength']
            # Convert size to MB for readability
            size_mb = size_bytes / (1024 * 1024)
            logger.info(f"Object size: {size_bytes} bytes ({size_mb:.2f} MB)")
            return size_bytes
        else:
            logger.warning(f"Could not retrieve valid 'ContentLength' metadata for {key}. Metadata received: {metadata}")
            return None
    except Exception as e:
        logger.error(f"Error getting object metadata for {key}: {e}", exc_info=True)
        return None

def load_pkl_from_s3(bucket: str, key: str) -> Optional[pd.DataFrame]:
    """Loads a pickle file from S3 into a pandas DataFrame, using local caching."""
    logger.info(f"Attempting to load pickle file: s3://{bucket}/{key} (using cache)")

    # 1. Get object size for context (optional, still useful)
    get_s3_object_size(bucket, key)

    # 2. Download the file (or get from cache)
    logger.info("Checking cache or downloading file...")
    try:
        # Use the new download_file method
        local_file_path = s3_handler.download_file(key=key, bucket_name=bucket)
        # Default cache location is /s3-cache

        if local_file_path is None:
            logger.error("Failed to download or retrieve file from cache.")
            return None
        
        logger.info(f"File available locally at: {local_file_path}")

    except Exception as e:
        logger.error(f"Error during file download/cache check: {e}", exc_info=True)
        return None

    # 3. Load the pickle data using pandas from the local path
    logger.info(f"Loading pickle data from local file: {local_file_path}...")
    df = None
    try:
        df = pd.read_pickle(local_file_path)
        n_all = df.shape[0]
        df = df[df['valid_spot'] == True]
        n_valid = df.shape[0]
        logger.info(f"Successfully loaded DataFrame. Shape: {df.shape}")
        logger.info(f"Total spots: {n_all}, Valid spots: {n_valid}")
        return df
    except pd.errors.EmptyDataError:
         logger.error(f"Error loading pickle from {local_file_path}: The file seems to be empty or contains no data.")
         return None
    except FileNotFoundError:
         logger.error(f"Error loading pickle: Local file not found at {local_file_path} (should not happen if download succeeded)." )
         return None
    except Exception as e:
        logger.error(f"Error loading pickle data from {local_file_path}: {e}", exc_info=True)
        return None

def load_processing_manifest_from_s3(bucket: str, key: str) -> Optional[Dict[str, Any]]:
    """
    Loads a processing_manifest.json file from S3.

    Parameters:
    -----------
    bucket: str
        S3 bucket name
    key: str
        S3 key for the processing_manifest.json file

    Returns:
    --------
    Optional[Dict[str, Any]]
        Dictionary containing the manifest data or None if loading failed
    """
    if not key:
        logger.warning("No processing manifest file key provided")
        return None

    logger.info(f"Loading processing manifest from s3://{bucket}/{key}")

    try:
        # Download the file content
        content = s3_handler.get_object(key=key, bucket_name=bucket)
        if content is None:
            logger.error(f"Failed to get object content for {key}")
            return None

        # Parse the JSON content
        manifest_data = json.loads(content.decode('utf-8'))
        logger.info(f"Successfully loaded and parsed processing manifest: {key}")
        return manifest_data

    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {key}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Error loading processing manifest file: {e}", exc_info=True)
        return None