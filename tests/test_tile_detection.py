#!/usr/bin/env python3
"""
Quick test script to verify tile detection functionality.
"""

from see_spot.s3_utils import detect_tile_structure, extract_tile_suffix

# Test tile suffix extraction
test_cases = [
    "Tile_X_0001_Y_0000_Z_0000",
    "Tile_X_0002_Y_0001_Z_0000",
    "tile_X_0003_Y_0002_Z_0001",  # lowercase
]

print("Testing extract_tile_suffix():")
for tile_folder in test_cases:
    suffix = extract_tile_suffix(tile_folder)
    expected_dataset = f"HCR_799211_2025-10-02_15-10-00_processed_2025-11-06_22-50-54_{suffix}"
    print(f"  {tile_folder} -> {suffix}")
    print(f"    Virtual dataset name: {expected_dataset}")

print("\n" + "="*80)
print("Tile detection test complete!")
print("="*80)
print("\nTo test with a real dataset:")
print("1. In the UI, enter: HCR_799211_2025-10-02_15-10-00_processed_2025-11-06_22-50-54")
print("2. Click 'Download' button")
print("3. Check the response - it should show 'is_tiled: true' and list virtual datasets")
print("4. Refresh the dataset table - you should see individual tile entries")
print("5. Select a tile dataset and click 'Load' to visualize that tile's data")
