#!/usr/bin/env python3

import sys
import os
sys.path.insert(0, '/home/matt.davis/code/see-spot/src')

from see_spot.s3_utils import load_and_merge_spots_from_s3

# Test the merge function
print("Testing the merge function...")

dataset_name = "HCR_749315_2025-05-08_14-00-00_processed_2025-05-17_22-15-31"
bucket = "aind-open-data"
prefix = f"{dataset_name}/image_spot_spectral_unmixing/"

print(f"Dataset: {dataset_name}")
print(f"Bucket: {bucket}")
print(f"Prefix: {prefix}")
print()

try:
    merged_df = load_and_merge_spots_from_s3(bucket, dataset_name, prefix)
    
    if merged_df is not None:
        print(f"✅ Success! Merged DataFrame shape: {merged_df.shape}")
        print(f"✅ Columns: {list(merged_df.columns)}")
        print(f"✅ Has spot_id column: {'spot_id' in merged_df.columns}")
        print(f"✅ Sample spot_id values: {merged_df['spot_id'].head().tolist()}")
        print(f"✅ Has unmixed_removed column: {'unmixed_removed' in merged_df.columns}")
        if 'unmixed_removed' in merged_df.columns:
            print(f"✅ Unmixed removed count: {merged_df['unmixed_removed'].sum()}")
        
        # Check if parquet file was created
        import pathlib
        parquet_file = pathlib.Path(f"/s3-cache/{bucket}/{dataset_name}/{dataset_name}.parquet")
        print(f"✅ Parquet file created: {parquet_file.exists()}")
        if parquet_file.exists():
            print(f"✅ Parquet file size: {parquet_file.stat().st_size / (1024*1024):.1f} MB")
    else:
        print("❌ Failed to load merged DataFrame")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()