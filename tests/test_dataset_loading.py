import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from see_spot.s3_utils import (  # noqa: E402
    load_and_merge_spots_from_s3,
    find_processing_manifest,
    load_processing_manifest_from_s3,
)
from see_spot.s3_handler import s3_handler  # noqa: E402


DATASET = "HCR_799211_2025-10-02_17-50-00_processed_2025-11-06_22-50-31"
BUCKET = "aind-open-data"
SPOTS_PREFIX = f"{DATASET}/image_spot_spectral_unmixing/"


class TestProblemDatasetLoading(unittest.TestCase):
    """Integration-style checks for problematic dataset loading.

    This focuses on verifying prerequisites for spot_details creation:
    presence of coordinate and metadata columns (x,y,z,cell_id,round) after merge.
    """

    @classmethod
    def setUpClass(cls):
        # Ensure cache root exists
        Path("/s3-cache").mkdir(exist_ok=True)

    def test_processing_manifest_exists(self):
        manifest_key = find_processing_manifest(BUCKET, DATASET)
        self.assertIsNotNone(
            manifest_key,
            msg="processing_manifest.json not found in top-level or derived directory",
        )
        if manifest_key:
            manifest = load_processing_manifest_from_s3(BUCKET, manifest_key)
            self.assertIsInstance(manifest, dict, "Manifest did not parse to dict")
            self.assertIn(
                "spot_channels", manifest, "Manifest missing 'spot_channels' key"
            )

    def test_merge_dataframe_columns(self):
        df = load_and_merge_spots_from_s3(BUCKET, DATASET, SPOTS_PREFIX, valid_spots_only=False)
        self.assertIsNotNone(df, "Merged DataFrame is None")
        cols = set(df.columns)
        # Required columns for spot_details logic
        required_detail_cols = {"x", "y", "z", "cell_id", "round"}
        missing = required_detail_cols - cols
        # Assert we at least have 2 columns (backend requires >1 to build details)
        present_count = len(required_detail_cols & cols)
        self.assertGreater(
            present_count,
            1,
            msg=f"Insufficient detail columns for spot_details (have {present_count}, missing: {missing})",
        )

    def test_parquet_cached(self):
        parquet_path = Path(f"/s3-cache/{BUCKET}/{DATASET}/{DATASET}.parquet")
        # Trigger merge first to ensure file creation
        _ = load_and_merge_spots_from_s3(BUCKET, DATASET, SPOTS_PREFIX, valid_spots_only=True)
        self.assertTrue(parquet_path.exists(), "Merged parquet file not cached")
        self.assertGreater(parquet_path.stat().st_size, 0, "Parquet file size is zero")

    def test_s3_paths_accessible(self):
        # Metadata check for a representative object (manifest or unmixed file)
        manifest_key = find_processing_manifest(BUCKET, DATASET)
        self.assertIsNotNone(manifest_key, "Manifest key missing for accessibility check")
        meta = s3_handler.get_object_metadata(manifest_key, bucket_name=BUCKET)
        self.assertIsNotNone(meta, "Unable to retrieve metadata for manifest file")
        self.assertIn("ContentLength", meta, "Metadata lacks ContentLength")


if __name__ == "__main__":
    unittest.main()
