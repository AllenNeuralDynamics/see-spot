import pandas as pd
import os
import sys
from pathlib import Path
import logging

# Add src directory to Python path to import see_spot modules
# Assumes the script is run from the root of the see-spot workspace
module_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'src'))
if module_path not in sys.path:
    sys.path.insert(0, module_path) # Insert at the beginning to prioritize local src

# Now try importing the modules
try:
    from see_spot.s3_handler import s3_handler # Assuming s3_handler is in src/see_spot
    from see_spot.s3_utils import (
        find_unmixed_spots_file,
        load_pkl_from_s3
    )
except ImportError as e:
    print(f"Error importing modules: {e}")
    print(f"Current sys.path: {sys.path}")
    print("Please ensure that the script is in the root of the 'see-spot' project and 'src' directory exists.")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration (should match app.py)
REAL_SPOTS_BUCKET = "aind-open-data"
PROCESSED_DATA_ROOT_PREFIX = "HCR_749315_2025-04-18_13-00-00_processed_2025-05-13_23-47-40"
SAMPLE_SIZE = 10000

def main():
    logger.info("--- Starting data inspection script ---")

    # 1. Find the data file
    unmixed_spots_prefix = f"{PROCESSED_DATA_ROOT_PREFIX}/image_spot_spectral_unmixing/"
    unmixed_spots_pattern = "unmixed_spots_*.pkl"

    logger.info(f"Searching for spots file in s3://{REAL_SPOTS_BUCKET}/{unmixed_spots_prefix} with pattern {unmixed_spots_pattern}")
    target_key = find_unmixed_spots_file(
        REAL_SPOTS_BUCKET, unmixed_spots_prefix, unmixed_spots_pattern
    )

    if not target_key:
        logger.error(f"Could not find spots file matching pattern '{unmixed_spots_pattern}' in '{unmixed_spots_prefix}'.")
        return

    # 2. Load the data
    logger.info(f"Found spots file: {target_key}. Loading data...")
    try:
        df = load_pkl_from_s3(REAL_SPOTS_BUCKET, target_key)
        if df is None:
            logger.error("Failed to load DataFrame from S3/cache.")
            return
        logger.info(f"Loaded DataFrame shape: {df.shape}")
    except Exception as e:
        logger.error(f"Exception during DataFrame loading: {e}", exc_info=True)
        return
    
    # show value counts for 'chan' and 'unmixed_chan' columns before subsampling
    print("\n--- Value Counts for 'chan' column before subsampling ---")
    print(df['chan'].value_counts(dropna=False))cell_body_segmentation
    print("\n--- Value Counts for 'unmixed_chan' column before subsampling ---")
    print(df['unmixed_chan'].value_counts(dropna=False))

    # 3. Subsample the data
    if len(df) > SAMPLE_SIZE:
        logger.info(f"Subsampling DataFrame from {len(df)} to {SAMPLE_SIZE} rows.")
        plot_df = df.sample(n=SAMPLE_SIZE, random_state=None) # Use None for true randomness
    else:
        plot_df = df.copy()
    logger.info(f"Sampled DataFrame shape: {plot_df.shape}")

    # 4. Add 'reassigned' column (mirroring app.py)
    if 'chan' in plot_df.columns and 'unmixed_chan' in plot_df.columns:
        plot_df['reassigned'] = plot_df['chan'] != plot_df['unmixed_chan']
        logger.info(f"Added 'reassigned' column. {plot_df['reassigned'].sum()} spots were reassigned in the sample.")
    else:
        logger.warning("'chan' or 'unmixed_chan' column not found, cannot create 'reassigned' column.")

    # 5. Display results
    print("\n--- Sampled DataFrame Head ---")
    print(plot_df.head())
    print(f"\n--- Sampled DataFrame Info ---")
    plot_df.info()
    
    if 'reassigned' in plot_df.columns:
        print("\n--- Value Counts for 'reassigned' column ---")
        print(plot_df['reassigned'].value_counts(dropna=False))

    # value counts for 'unmixed_chan' column and 'chan' column
    print("\n--- Value Counts for 'unmixed_chan' column ---")
    print(plot_df['unmixed_chan'].value_counts(dropna=False))
    print("\n--- Value Counts for 'chan' column ---")
    print(plot_df['chan'].value_counts(dropna=False))

    logger.info("--- Data inspection script finished ---")

if __name__ == '__main__':
    main() 