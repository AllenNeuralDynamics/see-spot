"""Tests for the bleed-through block extraction and analysis.

Split into two classes. The first needs no network and covers the rules
that keep the analysis meaningful: blocks must stay the requested size,
channels must sit on one grid, and pyramid coordinates must convert
per axis. The second hits the public ``aind-open-data`` bucket, matching
the style of the other tests in this directory.
"""

import unittest

import numpy as np

from see_spot import bleedthrough as bt
from see_spot import zarr_reader as zr
from see_spot.bleedthrough_api import (
    _run_analysis,
    _to_png,
    bright_fraction,
    registration_scan,
    stratified_sample,
)
from see_spot.zarr_store import (
    CHANNEL_RE,
    LevelInfo,
    MultiscaleInfo,
    ChannelInfo,
    ZarrMetadataError,
    parse_s3_uri,
)

BUCKET = "aind-open-data"
# Flat layout: <dataset>/image_tile_fusing/fused/channel_488.zarr
DATASET = "HCR_747667_2025-04-18_13-00-00_processed_2025-05-13_23-21-01"
BASE_PATH = f"s3://{BUCKET}/{DATASET}/image_tile_fusing/fused"

# Nested layout: <dataset>/fusion/ch_488/fused.zarr, little-endian,
# 256-wide chunks and a different voxel size.
NESTED_DATASET = (
    "HCR_831990-s3-ls2_2026-05-28_00-00-00_processed_2026-05-30_03-10-11"
)
NESTED_PATH = f"s3://{BUCKET}/{NESTED_DATASET}/fusion"

# Mirrors the real dataset: 6 levels, x/y halved from level 0 and z only
# from level 3 onward.
_SHAPES = [
    (1457, 5586, 5599),
    (1457, 2793, 2799),
    (1457, 1396, 1399),
    (728, 698, 699),
    (364, 349, 349),
    (182, 174, 174),
]
_SCALES = [
    (1.0, 0.2450, 0.2450),
    (1.0, 0.4901, 0.4901),
    (1.0, 0.9802, 0.9802),
    (2.0, 1.9604, 1.9604),
    (4.0, 3.9209, 3.9209),
    (8.0, 7.8418, 7.8418),
]


def _fake_info(shape_scale=None, translation=(0.0, 0.0, 0.0)):
    """Build a MultiscaleInfo matching the real pyramid.

    Args:
        shape_scale: Optional (shapes, scales) override.
        translation: Level-0 translation to report.

    Returns:
        A populated :class:`MultiscaleInfo`.
    """
    shapes, scales = shape_scale or (_SHAPES, _SCALES)
    levels = [
        LevelInfo(path=str(i), shape_zyx=shapes[i], scale_zyx=scales[i])
        for i in range(len(shapes))
    ]
    return MultiscaleInfo(
        zarr_path="s3://b/k.zarr",
        levels=levels,
        axis_names=("t", "c", "z", "y", "x"),
        dtype=">u2",
        chunks_zyx=(128, 128, 128),
        translation_zyx=translation,
    )


class TestPureLogic(unittest.TestCase):
    """Rules that must hold without touching the network."""

    def test_parse_s3_uri(self):
        """URIs split into bucket and key, with or without a scheme."""
        self.assertEqual(parse_s3_uri("s3://b/a/c"), ("b", "a/c"))
        self.assertEqual(parse_s3_uri("b/a/"), ("b", "a"))
        with self.assertRaises(ZarrMetadataError):
            parse_s3_uri("s3://")

    def test_clamp_box_keeps_requested_size_at_every_face(self):
        """A block near any face shifts inward instead of shrinking.

        Ragged blocks would weight regions unevenly in the pooled fit.
        """
        shape = (1457, 5586, 5599)
        size = (128, 128, 128)
        for center in [
            (0, 0, 0),
            (10**6, 10**6, 10**6),
            (0, 5585, 2000),
            (1456, 0, 5598),
        ]:
            box = zr.clamp_box(center, size, shape)
            got = tuple(s.stop - s.start for s in box)
            self.assertEqual(got, size, f"centre {center}")
            for axis, sl in enumerate(box):
                self.assertGreaterEqual(sl.start, 0)
                self.assertLessEqual(sl.stop, shape[axis])

    def test_clamp_box_clips_size_to_volume(self):
        """A block larger than the volume is capped, not negative."""
        box = zr.clamp_box((5, 5, 5), (64, 64, 64), (10, 10, 10))
        self.assertEqual(tuple(s.stop - s.start for s in box), (10, 10, 10))

    def test_choose_preview_level_is_per_plane(self):
        """Level choice honours the axes each plane actually shows."""
        info = _fake_info()
        for plane in ("XY", "ZY", "ZX"):
            level = zr.choose_preview_level(info, plane, target_px=512)
            spec = zr.plane_axes(plane)
            row = zr.SPATIAL_AXES.index(spec[1][0])
            col = zr.SPATIAL_AXES.index(spec[1][1])
            shape = info.levels[level].shape_zyx
            self.assertGreaterEqual(max(shape[row], shape[col]), 512)
            if level + 1 < len(info.levels):
                coarser = info.levels[level + 1].shape_zyx
                self.assertLess(max(coarser[row], coarser[col]), 512)

    def test_pyramid_ratio_is_anisotropic(self):
        """z and x downsample differently, so one factor cannot serve.

        A uniform 2**level would misplace every region above level 2.
        """
        info = _fake_info()
        full = info.levels[0].shape_zyx
        level5 = info.levels[5].shape_zyx
        ratio_z = full[0] / level5[0]
        ratio_x = full[2] / level5[2]
        self.assertAlmostEqual(ratio_z, 8.0, places=1)
        self.assertAlmostEqual(ratio_x, 32.2, places=0)
        self.assertNotAlmostEqual(ratio_z, ratio_x, places=1)

    def test_validate_common_grid_rejects_mismatches(self):
        """Channels off a shared grid are refused, not silently used."""
        good = ChannelInfo("488", "s3://b/a.zarr", _fake_info())
        same = ChannelInfo("514", "s3://b/b.zarr", _fake_info())
        zr.validate_common_grid([good, same])

        shifted = ChannelInfo(
            "561", "s3://b/c.zarr", _fake_info(translation=(0.0, 3.0, 0.0))
        )
        with self.assertRaises(ZarrMetadataError):
            zr.validate_common_grid([good, shifted])

        smaller = list(_SHAPES)
        smaller[0] = (1457, 5586, 5000)
        odd = ChannelInfo(
            "594", "s3://b/d.zarr", _fake_info((smaller, _SCALES))
        )
        with self.assertRaises(ZarrMetadataError):
            zr.validate_common_grid([good, odd])

    def test_block_budget_guard(self):
        """Oversized requests are refused before any bytes are read."""
        with self.assertRaises(ValueError):
            zr._check_budget((512, 512, 512), 5, 1)
        with self.assertRaises(ValueError):
            zr._check_budget((256, 256, 256), 8, 12)
        zr._check_budget((128, 128, 128), 5, 4)

    def test_stratified_sample_spans_regions_and_keeps_position(self):
        """Every region contributes, and rows carry global coordinates."""
        rng = np.random.default_rng(0)
        names = ["488", "514"]
        blocks = [
            {n: rng.normal(size=(8, 8, 8)).astype(np.float32) for n in names}
            for _ in range(3)
        ]
        origins = [(0, 0, 0), (100, 200, 300), (50, 60, 70)]
        rows = stratified_sample(blocks, origins, names, n_sample=90)

        self.assertEqual({int(r[0]) for r in rows}, {0, 1, 2})
        self.assertTrue(all(len(r) == 4 + len(names) for r in rows))
        for row in rows:
            origin = origins[int(row[0])]
            for axis in range(3):
                self.assertGreaterEqual(row[1 + axis], origin[axis])
                self.assertLess(row[1 + axis], origin[axis] + 8)

    def test_channel_regex_reads_both_naming_styles(self):
        """Channel names parse from flat and nested folder names."""
        cases = {
            "channel_488.zarr": "488",
            "ch_405": "405",
            "CH_638": "638",
            "ch-561": "561",
            "channel_1064.zarr": "1064",
        }
        for text, expected in cases.items():
            match = CHANNEL_RE.search(text)
            self.assertIsNotNone(match, text)
            self.assertEqual(match.group(1), expected, text)

        # Must not claim unrelated groups sitting at a dataset root.
        for text in ("corrected.ome.zarr", "metadata", "fused_ng.json"):
            self.assertIsNone(CHANNEL_RE.search(text), text)

    def test_contrast_window_changes_the_rendering(self):
        """The viewer window restretches, and survives bad input.

        Applied server-side because the PNG is 8-bit: a narrower window
        has to recover dim detail that a CSS filter could not.
        """
        import io

        from PIL import Image

        rng = np.random.default_rng(3)
        # Mostly dim background with a sparse bright tail, like a plane
        # containing a specimen surrounded by empty field.
        plane = rng.normal(100, 8, size=(64, 64))
        plane[rng.random(plane.shape) > 0.98] += 900

        def _stats(low, high):
            """Mean intensity of the encoded PNG for one window."""
            data = np.array(Image.open(io.BytesIO(_to_png(plane, low, high))))
            return float(data.mean())

        wide = _stats(0.5, 99.5)
        narrow = _stats(98.0, 100.0)
        self.assertNotAlmostEqual(wide, narrow, places=1)
        # Pushing the floor up must darken the image, not brighten it.
        self.assertLess(narrow, wide)

        # An inverted window falls back rather than raising.
        self.assertGreater(len(_to_png(plane, 99.0, 1.0)), 0)

    def test_bright_fraction_separates_tissue_from_background(self):
        """The signal gate can tell flat background from real signal.

        Regions with nothing in them must be droppable: pooling them
        distorts the coefficients, and a pair whose source is never
        bright divides by zero and returns NaN.
        """
        rng = np.random.default_rng(7)
        flat = {"a": rng.normal(100, 5, size=(8, 32, 32))}
        signal = {"a": rng.normal(100, 5, size=(8, 32, 32))}
        signal["a"][rng.random(signal["a"].shape) > 0.97] += 400

        self.assertLess(bright_fraction(flat, 3.0), 0.002)
        self.assertGreater(bright_fraction(signal, 3.0), 0.002)

    def test_level_scale_factors_track_the_stored_shapes(self):
        """Level factors come from shapes, and are anisotropic here."""
        info = _fake_info()
        self.assertEqual(zr.level_scale_factors(info, 0), (1.0, 1.0, 1.0))
        fz, fy, fx = zr.level_scale_factors(info, 5)
        self.assertAlmostEqual(fz, 8.0, places=1)
        self.assertAlmostEqual(fx, 32.1, places=0)
        self.assertNotAlmostEqual(fz, fx, places=1)

    def test_registration_scan_detects_a_real_shift(self):
        """A deliberate offset is found; aligned data reports aligned.

        Guards the check against becoming decorative: it has to move
        when the data moves.
        """
        rng = np.random.default_rng(1)
        base = rng.random((32, 48, 48)).astype(np.float32) * 6.0
        aligned = [{"a": base, "b": base * 0.4}]
        result = registration_scan(aligned, ["a", "b"], 1.0)["a|b"]
        self.assertEqual((result["dy"], result["dx"]), (0, 0))
        self.assertTrue(result["aligned"])

        rolled = [{"a": base, "b": np.roll(base * 0.4, 3, axis=2)}]
        shifted = registration_scan(rolled, ["a", "b"], 1.0)["a|b"]
        self.assertEqual(abs(shifted["dx"]), 3)
        self.assertFalse(shifted["aligned"])


class TestAgainstRealData(unittest.TestCase):
    """End-to-end checks against the public bucket."""

    @classmethod
    def setUpClass(cls):
        """Detect the dataset's channels once for all tests."""
        cls.channels = zr.detect_channels(BASE_PATH)

    def test_detects_every_channel_not_just_spot_channels(self):
        """S3 listing finds channels the manifest omits.

        ``spot_channels`` lists only 488/514/561, but 405 and 594 also
        bleed and must be analysable.
        """
        names = {c.name for c in self.channels}
        self.assertTrue({"405", "488", "514", "561", "594"} <= names)

    def test_nested_layout_and_root_descend(self):
        """Both fused layouts resolve, from their dir or dataset root.

        One pipeline writes ``fusion/ch_488/fused.zarr`` and an older
        one ``image_tile_fusing/fused/channel_488.zarr``. Pasting the
        dataset root is the natural thing to try, so that works too.
        """
        nested = zr.detect_channels(NESTED_PATH)
        self.assertTrue(
            {"405", "488", "561", "594", "638"} <= {c.name for c in nested}
        )
        zr.validate_common_grid(nested)

        for root in (
            f"s3://{BUCKET}/{NESTED_DATASET}",
            f"s3://{BUCKET}/{DATASET}",
        ):
            found = zr.detect_channels(root)
            self.assertGreaterEqual(len(found), 2, root)

    def test_nested_layout_reports_level_zero_chunks(self):
        """Chunk shape comes from level 0, not from the last level.

        This volume is chunked 128x256x256 at full resolution while
        coarser levels differ; the block cost estimate depends on the
        level actually read.
        """
        info = zr.detect_channels(NESTED_PATH)[0].info
        self.assertEqual(info.chunks_zyx, (128, 256, 256))
        self.assertAlmostEqual(info.lateral_voxel_um, 0.15336, places=4)

    def test_slab_orientation_and_decimation(self):
        """A slab comes back as (plane, row, col) for every plane.

        The stored axes are z, y, x, so ZY and ZX need a transpose;
        getting it wrong silently returns a rotated image and every
        region placed on it lands in the wrong tissue.
        """
        channel = self.channels[1]
        info = channel.info
        for plane in ("XY", "ZY", "ZX"):
            fixed, (row_axis, col_axis) = zr.plane_axes(plane)
            slab, level, shape, step = zr.read_slab(
                channel.zarr_path, plane, 20, 4, target_px=192
            )
            self.assertEqual(slab.shape[0], 4, plane)
            self.assertEqual(slab.dtype, np.dtype("uint16"), plane)

            level_shape = info.levels[level].shape_zyx
            rows = level_shape[zr.SPATIAL_AXES.index(row_axis)]
            cols = level_shape[zr.SPATIAL_AXES.index(col_axis)]
            self.assertEqual(slab.shape[1], len(range(0, rows, step)), plane)
            self.assertEqual(slab.shape[2], len(range(0, cols, step)), plane)
            self.assertLessEqual(max(slab.shape[1:]), 192 + step, plane)

    def test_slab_matches_single_plane_reads(self):
        """Plane k of a slab is the same data as reading plane k alone.

        This is what makes the cached slab safe to scrub through.
        """
        channel = self.channels[1]
        slab, level, _, step = zr.read_slab(
            channel.zarr_path, "XY", 30, 3, target_px=192
        )
        for offset in range(3):
            single, _, _ = zr.preview_plane(
                channel.zarr_path, "XY", 30 + offset, level=level
            )
            np.testing.assert_array_equal(slab[offset], single[::step, ::step])

    def test_metadata_matches_the_volume(self):
        """Voxel size, levels and dtype come back as stored."""
        info = self.channels[0].info
        self.assertEqual(len(info.levels), 6)
        self.assertAlmostEqual(info.lateral_voxel_um, 0.2450, places=3)
        self.assertEqual(info.chunks_zyx, (128, 128, 128))
        self.assertEqual(info.axis_names, ("t", "c", "z", "y", "x"))

    def test_channels_share_one_grid(self):
        """The co-location assumption holds for this dataset."""
        zr.validate_common_grid(self.channels)

    def test_block_is_exact_size_and_native_endian(self):
        """Corner blocks keep their size and lose the big-endian order.

        ``>u2`` leaking downstream would silently corrupt arithmetic.
        """
        pair = [c for c in self.channels if c.name in ("488", "514")]
        blocks, origin = zr.extract_block(pair, (0, 0, 0), (64, 64, 64))
        self.assertEqual(origin, (0, 0, 0))
        for name, data in blocks.items():
            self.assertEqual(data.shape, (64, 64, 64), name)
            self.assertEqual(data.dtype, np.dtype("uint16"), name)
            self.assertTrue(data.dtype.isnative, name)

    def test_pooling_differs_from_either_region_alone(self):
        """Statistics really pool; they are not per-region averages."""
        pair = [c for c in self.channels if c.name in ("488", "514")]
        regions = [(700, 2700, 2700), (700, 3200, 3100)]
        blocks, _ = zr.extract_region_blocks(pair, regions, (64, 64, 64))

        _, pooled_sigma, _ = bt.normalize_blocks(blocks, "median")
        _, sigma_a, _ = bt.normalize_blocks([blocks[0]], "median")
        _, sigma_b, _ = bt.normalize_blocks([blocks[1]], "median")

        for name in pooled_sigma:
            self.assertNotAlmostEqual(
                pooled_sigma[name], sigma_a[name], places=4
            )
            self.assertNotAlmostEqual(
                pooled_sigma[name], sigma_b[name], places=4
            )

    def test_run_over_multiple_regions(self):
        """A full run pools regions and returns usable coefficients."""
        result = _run_analysis(
            BASE_PATH,
            ["488", "514", "561"],
            regions=[[700, 2700, 2700], [700, 3200, 3100]],
            size_zyx=[64, 64, 64],
            background="median",
            bright_nsigma=3.0,
            voxel_size_um=0.2450,
            window_um=0.93,
            n_sample=1500,
            sample_bright_fraction=0.5,
            check_registration=False,
            min_bright_fraction=0.0,
        )
        self.assertEqual(len(result["regions"]), 2)
        self.assertTrue(result["has_null"])
        self.assertEqual({int(p[0]) for p in result["points"]}, {0, 1})
        # One row per voxel holding every channel's value plus position.
        self.assertEqual(len(result["points"][0]), 4 + 3)

        for src in ("488", "514", "561"):
            for dst in ("488", "514", "561"):
                if src == dst:
                    continue
                entry = result["coefficients"][f"{src}|{dst}"]
                self.assertIsNotNone(entry["observed"])
                self.assertIsNotNone(entry["alpha"])


if __name__ == "__main__":
    unittest.main()
