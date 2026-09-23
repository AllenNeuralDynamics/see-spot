"""Read preview planes and analysis blocks from OMEZarr volumes.

Sits on top of :mod:`zarr_store`, which owns the connection and the
metadata parsing. This module is where the reads that actually move
image data live, so it is also where the correctness rules that the
bleed-through analysis depends on are enforced.

The central rule is that voxel ``(z, y, x)`` must mean **the same
physical point in every channel**. The analysis pairs one channel's
value against another's *at one voxel*; if the channels are offset
relative to each other that pairing is meaningless. It also fails
misleadingly rather than obviously: a shift decorrelates the fit, which
shrinks the estimated coupling, so genuine bleed-through is written off
as tissue structure. Hence :func:`validate_common_grid` and the single
shared slice box in :func:`extract_block`.
"""

import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import zarr

from see_spot.zarr_store import (
    FUSED_SUBDIRS,
    SPATIAL_AXES,
    ChannelInfo,
    MultiscaleInfo,
    ZarrMetadataError,
    zarr_store,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PLANES",
    "MAX_BLOCK_SIDE",
    "MAX_TOTAL_BLOCK_BYTES",
    "detect_channels",
    "validate_common_grid",
    "choose_preview_level",
    "preview_plane",
    "plane_axes",
    "clamp_box",
    "extract_block",
    "extract_region_blocks",
]

# Plane name -> (axis held fixed, the two axes displayed as row, col).
PLANES = {
    "XY": ("z", ("y", "x")),
    "ZY": ("x", ("z", "y")),
    "ZX": ("y", ("z", "x")),
}

MAX_BLOCK_SIDE = 256
MAX_TOTAL_BLOCK_BYTES = 2 * 1024**3
_MAX_WORKERS = 8

_executor = ThreadPoolExecutor(
    max_workers=_MAX_WORKERS, thread_name_prefix="zarrread"
)
_zarr_configured = threading.Event()


def _configure_zarr() -> None:
    """Raise zarr's internal fetch concurrency, once per process.

    A block spans several chunks; the default concurrency fetches them
    in small waves and the read becomes latency-bound.
    """
    if _zarr_configured.is_set():
        return
    try:
        zarr.config.set({"async.concurrency": 16})
    except Exception as err:  # pragma: no cover - config API drift
        logger.warning("Could not set zarr async concurrency: %s", err)
    _zarr_configured.set()


def detect_channels(base_path: str) -> List[ChannelInfo]:
    """Find every channel for a dataset and read its metadata.

    Args:
        base_path: ``s3://`` URI of the directory holding the channels,
            or of the dataset root.

    Returns:
        One :class:`ChannelInfo` per channel, ordered by wavelength.

    Raises:
        ZarrMetadataError: If no channels are found.
    """
    pairs = zarr_store.list_channel_zarrs(base_path)
    if not pairs:
        raise ZarrMetadataError(
            f"No channels found under {base_path}. Expected either "
            "'<dir>/channel_488.zarr' or '<dir>/ch_488/fused.zarr', "
            "looking in the path given and in "
            f"{', '.join(FUSED_SUBDIRS)}. Point at the dataset root or "
            "at the directory that holds the per-channel folders."
        )
    channels = []
    for name, zarr_path in pairs:
        info = zarr_store.read_multiscale_metadata(zarr_path)
        channels.append(ChannelInfo(name=name, zarr_path=zarr_path, info=info))
    return channels


def validate_common_grid(channels: Sequence[ChannelInfo]) -> None:
    """Assert that channels share one voxel grid.

    Each channel of these datasets is fused independently, so a common
    grid is a property to check rather than assume. Without it, index
    ``(z, y, x)`` denotes a different physical point in each channel and
    every coupling number computed downstream is meaningless.

    Args:
        channels: Channels about to be analysed together.

    Raises:
        ZarrMetadataError: If shapes, voxel sizes or translations differ
            between channels.
    """
    if len(channels) < 2:
        return
    reference = channels[0]
    for channel in channels[1:]:
        for label, left, right in (
            (
                "shape",
                reference.info.shape_zyx,
                channel.info.shape_zyx,
            ),
            (
                "voxel size",
                reference.info.voxel_size_zyx,
                channel.info.voxel_size_zyx,
            ),
            (
                "translation",
                reference.info.translation_zyx,
                channel.info.translation_zyx,
            ),
        ):
            if not np.allclose(left, right):
                raise ZarrMetadataError(
                    f"channels {reference.name} and {channel.name} "
                    f"disagree on {label}: {left} vs {right}. They are "
                    "not on a common grid, so voxels cannot be compared "
                    "between them."
                )


def plane_axes(plane: str) -> Tuple[str, Tuple[str, str]]:
    """Resolve a plane name to its fixed and displayed axes.

    Args:
        plane: One of ``"XY"``, ``"ZY"`` or ``"ZX"``.

    Returns:
        A tuple ``(fixed_axis, (row_axis, col_axis))``.

    Raises:
        ValueError: If the plane name is not recognised.
    """
    key = str(plane).upper()
    if key not in PLANES:
        raise ValueError(
            f"unknown plane {plane!r}; expected one of {sorted(PLANES)}"
        )
    return PLANES[key]


def choose_preview_level(
    info: MultiscaleInfo, plane: str, target_px: int = 512
) -> int:
    """Pick the cheapest pyramid level that still looks reasonable.

    Args:
        info: Multiscale metadata for the channel.
        plane: Plane being displayed.
        target_px: Desired minimum length of the plane's longer side.

    Returns:
        Index of the chosen level; 0 when even full resolution is
        smaller than ``target_px``.
    """
    _, (row_axis, col_axis) = plane_axes(plane)
    row_i = SPATIAL_AXES.index(row_axis)
    col_i = SPATIAL_AXES.index(col_axis)
    for index in range(len(info.levels) - 1, -1, -1):
        shape = info.levels[index].shape_zyx
        if max(shape[row_i], shape[col_i]) >= target_px:
            return index
    return 0


def _full_index(
    axis_names: Sequence[str], zyx: Sequence[object]
) -> Tuple[object, ...]:
    """Expand a z/y/x selection to the array's full dimensionality.

    Non-spatial axes (t, c) are pinned to 0 so a 5-D ``(t,c,z,y,x)``
    array and a bare 3-D one are indexed the same way.

    Args:
        axis_names: Axis names in stored order.
        zyx: Three selections (``int`` or ``slice``) for z, y and x.

    Returns:
        An index tuple matching the array's rank.
    """
    index: List[object] = []
    for name in axis_names:
        if name in SPATIAL_AXES:
            index.append(zyx[SPATIAL_AXES.index(name)])
        else:
            index.append(0)
    return tuple(index)


def preview_plane(
    zarr_path: str,
    plane: str,
    position: int,
    level: Optional[int] = None,
    target_px: int = 512,
) -> Tuple[np.ndarray, int, Tuple[int, int, int]]:
    """Read one 2-D slice for the viewer.

    Args:
        zarr_path: ``s3://`` URI of the channel's zarr group.
        plane: ``"XY"``, ``"ZY"`` or ``"ZX"``.
        position: Index along the fixed axis, in that level's voxels.
        level: Pyramid level; chosen automatically when None.
        target_px: Target size used when choosing the level.

    Returns:
        A tuple ``(image, level, level_shape_zyx)``.
    """
    _configure_zarr()
    info = zarr_store.read_multiscale_metadata(zarr_path)
    chosen = (
        choose_preview_level(info, plane, target_px)
        if (level is None)
        else int(level)
    )
    chosen = max(0, min(chosen, len(info.levels) - 1))

    fixed_axis, _ = plane_axes(plane)
    shape = info.levels[chosen].shape_zyx
    fixed_i = SPATIAL_AXES.index(fixed_axis)
    index = max(0, min(int(position), shape[fixed_i] - 1))

    selection: List[object] = [slice(None), slice(None), slice(None)]
    selection[fixed_i] = index

    array = zarr_store.open_level(zarr_path, chosen)
    raw = array[_full_index(info.axis_names, selection)]
    return np.asarray(raw, dtype=np.uint16), chosen, shape


def read_slab(
    zarr_path: str,
    plane: str,
    start: int,
    count: int,
    level: Optional[int] = None,
    target_px: int = 384,
    crop: Optional[Sequence[int]] = None,
) -> Tuple[np.ndarray, int, Tuple[int, int, int], int]:
    """Read several consecutive viewer planes in one go.

    Scrubbing one plane at a time is dominated by latency, and reads a
    chunk per step. Consecutive planes usually live in the *same* chunk
    -- these volumes are chunked 128 deep -- so a slab of them costs
    about the same as one plane and then scrubbing inside it is free.

    The result is decimated to ``target_px`` so the payload stays
    bounded: this feeds navigation and contrast, not measurement.

    Args:
        zarr_path: ``s3://`` URI of the channel's zarr group.
        plane: ``"XY"``, ``"ZY"`` or ``"ZX"``.
        start: First index along the fixed axis, in level voxels.
        count: How many planes to read.
        level: Pyramid level; chosen automatically when None.
        target_px: Longest side of a returned plane, after decimation.
        crop: Optional ``(row0, row1, col0, col1)`` in level voxels.
            Decimation is then computed from the cropped extent, so
            zooming in spends the same budget on a smaller area and
            genuinely gains detail rather than just scaling up.

    Returns:
        ``(slab, level, level_shape_zyx, step)`` where ``slab`` is
        ``(n, rows, cols)`` uint16 and ``step`` is the decimation used.
    """
    _configure_zarr()
    info = zarr_store.read_multiscale_metadata(zarr_path)
    chosen = (
        choose_preview_level(info, plane, target_px)
        if level is None
        else int(level)
    )
    chosen = max(0, min(chosen, len(info.levels) - 1))

    fixed_axis, (row_axis, col_axis) = plane_axes(plane)
    shape = info.levels[chosen].shape_zyx
    fixed_i = SPATIAL_AXES.index(fixed_axis)
    row_i = SPATIAL_AXES.index(row_axis)
    col_i = SPATIAL_AXES.index(col_axis)

    extent = shape[fixed_i]
    start = max(0, min(int(start), extent - 1))
    count = max(1, min(int(count), extent - start))

    row0, row1 = 0, shape[row_i]
    col0, col1 = 0, shape[col_i]
    if crop is not None and len(crop) == 4:
        row0 = max(0, min(int(crop[0]), shape[row_i] - 1))
        row1 = max(row0 + 1, min(int(crop[1]), shape[row_i]))
        col0 = max(0, min(int(crop[2]), shape[col_i] - 1))
        col1 = max(col0 + 1, min(int(crop[3]), shape[col_i]))

    step = max(1, int(np.ceil(max(row1 - row0, col1 - col0) / target_px)))

    selection: List[object] = [slice(None), slice(None), slice(None)]
    selection[fixed_i] = slice(start, start + count)
    selection[row_i] = slice(row0, row1, step)
    selection[col_i] = slice(col0, col1, step)

    array = zarr_store.open_level(zarr_path, chosen)
    raw = np.asarray(
        array[_full_index(info.axis_names, selection)], dtype=np.uint16
    )
    # raw keeps the spatial axes in the order the file stores them;
    # reorder to (plane, row, col) for whichever axis is held fixed.
    stored = [name for name in info.axis_names if name in SPATIAL_AXES]
    perm = tuple(
        stored.index(name) for name in (fixed_axis, row_axis, col_axis)
    )
    return np.ascontiguousarray(np.transpose(raw, perm)), chosen, shape, step


def clamp_box(
    center_zyx: Sequence[int],
    size_zyx: Sequence[int],
    shape_zyx: Sequence[int],
) -> Tuple[slice, slice, slice]:
    """Build a slice box of exactly ``size_zyx``, shifted to fit.

    A region placed near a face is moved inward rather than truncated,
    so every region yields an identically shaped block. Ragged blocks
    would make the per-region composites impossible to lay out and would
    silently weight regions differently in the pooled fit.

    Args:
        center_zyx: Requested centre in level-0 voxels.
        size_zyx: Requested extent in voxels.
        shape_zyx: Volume extent in voxels.

    Returns:
        Three slices covering z, y and x.
    """
    slices = []
    for center, size, extent in zip(center_zyx, size_zyx, shape_zyx):
        span = max(1, min(int(size), int(extent)))
        start = int(round(float(center) - span / 2.0))
        start = max(0, min(start, int(extent) - span))
        slices.append(slice(start, start + span))
    return tuple(slices)


def _check_budget(
    size_zyx: Sequence[int], n_channels: int, n_regions: int
) -> None:
    """Reject a request that would not fit in memory.

    Args:
        size_zyx: Block extent in voxels.
        n_channels: Channels per block.
        n_regions: Number of regions requested.

    Raises:
        ValueError: If any side exceeds :data:`MAX_BLOCK_SIDE` or the
            total allocation exceeds :data:`MAX_TOTAL_BLOCK_BYTES`.
    """
    for axis, side in zip(SPATIAL_AXES, size_zyx):
        if int(side) > MAX_BLOCK_SIDE:
            raise ValueError(
                f"block {axis} side {side} exceeds the {MAX_BLOCK_SIDE} "
                "voxel limit"
            )
    total = (
        int(np.prod([int(s) for s in size_zyx]))
        * int(n_channels)
        * int(n_regions)
        * 2
    )
    if total > MAX_TOTAL_BLOCK_BYTES:
        raise ValueError(
            f"request needs {total / 1024 ** 3:.2f} GB "
            f"({n_regions} regions x {n_channels} channels); the limit "
            f"is {MAX_TOTAL_BLOCK_BYTES / 1024 ** 3:.2f} GB. Use fewer "
            "regions or a smaller block."
        )


def extract_block(
    channels: Sequence[ChannelInfo],
    center_zyx: Sequence[int],
    size_zyx: Sequence[int],
) -> Tuple[Dict[str, np.ndarray], Tuple[int, int, int]]:
    """Read one co-located block across every channel.

    The slice box is computed **once** and reused unchanged for all
    channels. Deriving it per channel would let rounding or clamping
    differ between them and break the co-location the analysis needs.

    Args:
        channels: Channels to read; must share a grid.
        center_zyx: Requested block centre in level-0 voxels.
        size_zyx: Requested block extent in voxels.

    Returns:
        A tuple ``(blocks, origin_zyx)`` where ``blocks`` maps channel
        name to a ``(z, y, x)`` uint16 array and ``origin_zyx`` is the
        block's first voxel, needed to map back to global coordinates.
    """
    _configure_zarr()
    validate_common_grid(channels)
    box = clamp_box(center_zyx, size_zyx, channels[0].info.shape_zyx)
    origin = tuple(int(s.start) for s in box)

    def _read(channel: ChannelInfo) -> Tuple[str, np.ndarray]:
        """Read the shared box from one channel."""
        array = zarr_store.open_level(channel.zarr_path, 0)
        raw = array[_full_index(channel.info.axis_names, box)]
        return channel.name, np.asarray(raw, dtype=np.uint16)

    results = dict(_executor.map(_read, channels))
    return results, origin


def level_scale_factors(
    info: MultiscaleInfo, level: int
) -> Tuple[float, float, float]:
    """Voxels of level 0 per voxel of ``level``, per axis.

    Read from the stored shapes rather than assumed to be a power of
    two: in these pyramids z is often left alone while y and x halve,
    so a single factor would place a region in the wrong tissue.

    Args:
        info: Multiscale metadata for the channel.
        level: Level to compare against level 0.

    Returns:
        One factor per axis, z, y then x.
    """
    full = info.levels[0].shape_zyx
    here = info.levels[level].shape_zyx
    return tuple(float(full[i]) / float(max(here[i], 1)) for i in range(3))


def extract_region_blocks(
    channels: Sequence[ChannelInfo],
    regions: Sequence[Sequence[int]],
    size_zyx: Sequence[int],
    level: int = 0,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[List[Dict[str, np.ndarray]], List[Tuple[int, int, int]]]:
    """Read one block per region, fanning out over region and channel.

    Args:
        channels: Channels to read; must share a grid.
        regions: Requested block centres as **level-0** ``(z, y, x)``
            triples, so the caller does not have to track the level.
        size_zyx: Block extent, in voxels **of the chosen level**.
        level: Pyramid level to read from.
        on_progress: Called with ``(done, total)`` as each
            region-channel read lands. This is the slow part of a run,
            so it is the part worth reporting.

    Returns:
        A tuple ``(blocks, origins)`` with one entry per region, in the
        order the regions were given. Origins are in level-0 voxels so
        they stay comparable across levels.

    Raises:
        ValueError: If no regions are given or the request is too large.
    """
    if not regions:
        raise ValueError("at least one region is required")
    _configure_zarr()
    validate_common_grid(channels)
    _check_budget(size_zyx, len(channels), len(regions))

    info = channels[0].info
    level = max(0, min(int(level), len(info.levels) - 1))
    factors = level_scale_factors(info, level)
    shape = info.levels[level].shape_zyx

    centres = [
        [int(round(float(c[i]) / factors[i])) for i in range(3)]
        for c in regions
    ]
    boxes = [clamp_box(centre, size_zyx, shape) for centre in centres]

    def _read(task: Tuple[int, ChannelInfo]) -> Tuple[int, str, np.ndarray]:
        """Read one (region, channel) pair."""
        region_index, channel = task
        array = zarr_store.open_level(channel.zarr_path, level)
        box = boxes[region_index]
        raw = array[_full_index(channel.info.axis_names, box)]
        return region_index, channel.name, np.asarray(raw, dtype=np.uint16)

    tasks = [
        (index, channel)
        for index in range(len(regions))
        for channel in channels
    ]
    blocks: List[Dict[str, np.ndarray]] = [{} for _ in regions]
    done = 0
    for region_index, name, data in _executor.map(_read, tasks):
        blocks[region_index][name] = data
        done += 1
        if on_progress is not None:
            on_progress(done, len(tasks))

    # Report origins in level-0 voxels so a clicked point, a
    # Neuroglancer link and a block read at any level all agree.
    origins = [
        tuple(int(round(box[i].start * factors[i])) for i in range(3))
        for box in boxes
    ]
    return blocks, origins


def estimate_chunk_reads(info: MultiscaleInfo, size_zyx: Sequence[int]) -> int:
    """Estimate how many chunks one block touches, worst case.

    Drives the size hint in the UI: an unaligned block spans one extra
    chunk per axis, and the cost grows as the cube of that.

    Args:
        info: Multiscale metadata supplying the chunk shape.
        size_zyx: Block extent in voxels.

    Returns:
        Worst-case chunk count for a single channel.
    """
    total = 1
    for size, chunk in zip(size_zyx, info.chunks_zyx):
        if chunk <= 0:
            continue
        total *= int(math.ceil(int(size) / int(chunk))) + 1
    return total
