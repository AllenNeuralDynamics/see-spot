"""HTTP endpoints and orchestration for the bleed-through page.

Wires :mod:`zarr_reader` (which fetches image blocks) to
:mod:`bleedthrough` (which measures channel coupling) and serves the
result to the browser.

The numerical core of :mod:`bleedthrough` drives the interactive view;
its matplotlib figures are produced only for the download button. The
module itself is used unmodified.

Two properties of the analysis shape almost everything here:

* **Statistics pool across regions.** ``normalize_blocks`` derives one
  median and one sigma per channel from every region concatenated, and
  ``pair_coefficients`` accumulates the fit sums across regions before
  dividing. A slope is therefore a single pooled estimate, not an
  average of per-region slopes.
* **A voxel is one point in every channel.** The sample sent to the
  browser is one row per voxel carrying every channel's value, which
  makes co-location structural rather than a convention a caller could
  get wrong.
"""

import inspect
import logging
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
from fastapi import APIRouter, Query  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402
from PIL import Image  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

from see_spot import bleedthrough as bt  # noqa: E402
from see_spot import ng_utils, zarr_reader  # noqa: E402
from see_spot.zarr_store import ZarrMetadataError  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bleedthrough", tags=["bleedthrough"])

DEFAULT_SAMPLE = 12000
DEFAULT_BRIGHT_FRACTION = 0.5
DEFAULT_WINDOW_UM = 2.0
# Least share of bright voxels for a region to count as tissue.
# Gaussian noise alone puts ~0.13% of voxels above 3 sigma, so this
# sits just above that.
DEFAULT_MIN_BRIGHT = 0.002
# Below this many pooled regions the coefficients are not stable: on a
# real dataset alpha fell by 3-7x going from 1 region to 25.
MIN_STABLE_REGIONS = 10
# Viewer contrast window, as percentiles of the displayed plane.
DEFAULT_LOW_PCT = 0.5
DEFAULT_HIGH_PCT = 99.5
REGISTRATION_MAX_OFFSET = 5
# Below this peak correlation a pair has no locatable alignment peak,
# so its argmax is noise and no offset claim is made.
REGISTRATION_MIN_NCC = 0.15
# Offsets up to this magnitude are treated as aligned. The scan masks on
# the source channel, so the two directions of a pair see different
# voxels and disagree by about a voxel even on well-registered data;
# one voxel is also only 0.245 um here, below the optical resolution.
# A genuine misregistration is several voxels and still gets flagged.
REGISTRATION_TOLERANCE = 1
_CACHE_BUDGET_BYTES = 3 * 1024**3


class _BlockCache:
    """Byte-budgeted LRU holding extracted blocks between requests.

    Only exists so the figure download can re-render without pulling
    gigabytes from S3 again. A plain dict would pin every block ever
    analysed for the life of the process.
    """

    def __init__(self, budget_bytes: int):
        """Initialize the cache.

        Args:
            budget_bytes: Total array bytes to keep before evicting.
        """
        self._budget = int(budget_bytes)
        self._entries: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    @staticmethod
    def _size_of(entry: Dict[str, Any]) -> int:
        """Total array bytes held by one entry.

        Args:
            entry: Cached run payload holding a list of block dicts.

        Returns:
            Sum of ``nbytes`` over every array in the entry.
        """
        return sum(
            int(arr.nbytes)
            for block in entry.get("blocks", [])
            for arr in block.values()
        )

    def put(self, key: str, entry: Dict[str, Any]) -> None:
        """Store an entry, evicting oldest entries to stay in budget.

        Args:
            key: Run identifier.
            entry: Run payload to keep.
        """
        size = self._size_of(entry)
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            self._bytes += size
            while self._bytes > self._budget and len(self._entries) > 1:
                _, dropped = self._entries.popitem(last=False)
                self._bytes -= self._size_of(dropped)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Fetch an entry and mark it most recently used.

        Args:
            key: Run identifier.

        Returns:
            The cached entry, or None when it is absent or evicted.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry


_block_cache = _BlockCache(_CACHE_BUDGET_BYTES)

# Jobs in flight, so a run can report progress instead of blocking for
# half a minute behind one request. Bounded by _JOB_LIMIT.
_jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_jobs_lock = Lock()
_JOB_LIMIT = 24
_job_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="btrun")


def _set_job(job_id: str, **fields: Any) -> None:
    """Merge fields into a job record.

    Args:
        job_id: Job identifier.
        **fields: Values to store, e.g. stage, done, total, result.
    """
    with _jobs_lock:
        job = _jobs.setdefault(job_id, {})
        job.update(fields)
        _jobs.move_to_end(job_id)
        while len(_jobs) > _JOB_LIMIT:
            _jobs.popitem(last=False)


def _get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a job record.

    Args:
        job_id: Job identifier.

    Returns:
        The record, or None when unknown or evicted.
    """
    with _jobs_lock:
        return _jobs.get(job_id)


def bright_fraction(
    block: Dict[str, np.ndarray], bright_nsigma: float
) -> float:
    """Largest fraction of voxels above threshold in any channel.

    Args:
        block: One region's block, in raw counts.
        bright_nsigma: Threshold in sigmas.

    Returns:
        The fraction, between 0 and 1.
    """
    best = 0.0
    for values in block.values():
        centred = np.asarray(values, dtype=np.float32) - np.median(values)
        sigma = bt.robust_sigma(centred)
        if sigma <= 0:
            continue
        best = max(best, float(np.mean(centred > bright_nsigma * sigma)))
    return best


def _shifted_sums(
    source: np.ndarray,
    dest: np.ndarray,
    mask: np.ndarray,
    axis: int,
    offset: int,
) -> Tuple[float, float, float]:
    """Cross-correlation sums for a pair slid along one axis.

    Returns all three sums rather than a slope because the registration
    peak must be found with a *normalised* measure. A bare slope
    ``Sxy/Sxx`` changes scale as the overlap window moves, so comparing
    it across offsets finds the largest window, not the best alignment.

    ``bleedthrough._shifted_views`` only handles non-negative offsets
    and the peak may lie either side of zero, so the slicing is done
    here rather than by modifying that module.

    Args:
        source: Source channel in sigma units.
        dest: Destination channel, same shape.
        mask: Boolean selection over the source-aligned voxels.
        axis: Axis to displace along.
        offset: Voxel displacement, may be negative.

    Returns:
        ``(sum_xy, sum_xx, sum_yy, count)``; zeros when the shift is too
        large to leave any overlap.
    """
    length = source.shape[axis]
    if abs(offset) >= length:
        return 0.0, 0.0, 0.0, 0
    keep: List[Any] = [slice(None)] * source.ndim
    moved: List[Any] = [slice(None)] * source.ndim
    if offset >= 0:
        keep[axis] = slice(0, length - offset)
        moved[axis] = slice(offset, length)
    else:
        shift = -offset
        keep[axis] = slice(shift, length)
        moved[axis] = slice(0, length - shift)
    selected = mask[tuple(keep)]
    left = source[tuple(keep)][selected]
    right = dest[tuple(moved)][selected]
    if left.size == 0:
        return 0.0, 0.0, 0.0, 0
    return (
        float(np.einsum("i,i->", left, right, dtype=np.float64)),
        float(np.einsum("i,i->", left, left, dtype=np.float64)),
        float(np.einsum("i,i->", right, right, dtype=np.float64)),
        int(left.size),
    )


def registration_scan(
    normalized: Sequence[Dict[str, np.ndarray]],
    names: Sequence[str],
    bright_nsigma: float,
    max_offset: int = REGISTRATION_MAX_OFFSET,
) -> Dict[str, Dict[str, Any]]:
    """Locate the lateral offset at which each channel pair couples most.

    The fused channels declare a common grid, but each is fused
    independently and the pipeline's cross-channel alignment result is
    not carried into the zarr metadata, so a residual shift is possible
    and invisible. It matters because it biases the answer downward: a
    misregistered pair decorrelates, so real bleed-through looks like
    a smaller coefficient.

    Sums accumulate across regions, matching how the coefficients
    themselves are pooled.

    A pair that barely couples has no correlation peak to find, so its
    argmax is just noise. Reporting that as a misalignment would cry
    wolf on most of the matrix, so such pairs are marked inconclusive
    instead: the scan only claims an offset when the peak correlation
    clears :data:`REGISTRATION_MIN_NCC`.

    Args:
        normalized: Blocks in sigma units, one per region.
        names: Channel names to scan.
        bright_nsigma: Source-brightness threshold for the fit.
        max_offset: Largest displacement to try, in voxels.

    Returns:
        A dict keyed ``"src|dst"`` giving the peak offset ``dy``/``dx``,
        the peak correlation, and whether the pair is aligned or the
        scan was inconclusive.
    """
    offsets = list(range(-int(max_offset), int(max_offset) + 1))
    masks = [
        {name: block[name] > bright_nsigma for name in names}
        for block in normalized
    ]
    lateral = {"dy": -2, "dx": -1}

    out: Dict[str, Dict[str, Any]] = {}
    for src in names:
        for dst in names:
            if src == dst:
                continue
            peak: Dict[str, int] = {}
            strength = 0.0
            for label, axis in lateral.items():
                best_offset, best_ncc = 0, -np.inf
                for offset in offsets:
                    # Correlate within each region, then average. Pooling
                    # the raw sums first would compare quantities with
                    # different scales: the Cauchy-Schwarz gap between
                    # sqrt(SUM xx . SUM yy) and the per-region terms
                    # drifts with offset and invents a peak at the edge
                    # of the scan even for perfectly aligned channels.
                    weighted = 0.0
                    weight = 0.0
                    for block, mask in zip(normalized, masks):
                        add_xy, add_xx, add_yy, count = _shifted_sums(
                            block[src], block[dst], mask[src], axis, offset
                        )
                        if add_xx <= 0 or add_yy <= 0 or count == 0:
                            continue
                        local = add_xy / float(np.sqrt(add_xx * add_yy))
                        weighted += local * count
                        weight += count
                    if weight <= 0:
                        continue
                    ncc = weighted / weight
                    if ncc > best_ncc:
                        best_offset, best_ncc = offset, ncc
                peak[label] = best_offset
                strength = max(strength, float(best_ncc))

            # An argmax sitting on the edge of the scan means the curve
            # was still climbing: there is no maximum in range, so the
            # value is a trend, not an alignment. Weakly coupled pairs
            # do this routinely. Calling those misaligned would flag
            # most of the matrix, so they are reported as inconclusive.
            largest = max(abs(peak.get("dy", 0)), abs(peak.get("dx", 0)))
            at_edge = largest >= max_offset
            confident = strength >= REGISTRATION_MIN_NCC and not at_edge
            shifted = largest > REGISTRATION_TOLERANCE
            out[f"{src}|{dst}"] = {
                "dy": peak.get("dy", 0),
                "dx": peak.get("dx", 0),
                "ncc": round(strength, 4),
                "conclusive": confident,
                "aligned": bool(confident and not shifted),
            }
    return out


def stratified_sample(
    normalized: Sequence[Dict[str, np.ndarray]],
    origins: Sequence[Sequence[int]],
    names: Sequence[str],
    n_sample: int = DEFAULT_SAMPLE,
    bright_fraction: float = DEFAULT_BRIGHT_FRACTION,
    seed: int = 0,
    scale: Sequence[float] = (1.0, 1.0, 1.0),
) -> List[List[float]]:
    """Draw display voxels from every region, biased toward signal.

    A uniform draw from these blocks is almost all background and
    renders as one uninformative blob, so part of the sample is taken
    from voxels that exceed one sigma in at least one channel. Regions
    contribute in proportion to their voxel counts, so the picture
    reflects the pooled fit rather than whichever region was added last.

    Each row carries one voxel's value in *every* channel plus its
    global position, which is what lets the browser build any channel
    pair's panel and still resolve a click back to a real location.

    Args:
        normalized: Blocks in sigma units, one per region.
        origins: Level-0 ``(z, y, x)`` origin of each block.
        names: Channel names, fixing the column order.
        n_sample: Total rows to return across all regions.
        bright_fraction: Share of each region's rows drawn from bright
            voxels.
        seed: Seed for reproducible sampling.
        scale: Level-0 voxels per block voxel, per axis. Blocks read
            from a coarser level index in that level's voxels, so a
            position only maps back to the volume once scaled.

    Returns:
        Rows of ``[region, gz, gy, gx, v0, v1, ...]``.
    """
    rng = np.random.default_rng(seed)
    sizes = [int(block[names[0]].size) for block in normalized]
    grand_total = sum(sizes) or 1

    rows: List[List[float]] = []
    for index, block in enumerate(normalized):
        shape = block[names[0]].shape
        total = sizes[index]
        quota = max(1, int(round(n_sample * total / grand_total)))
        quota = min(quota, total)

        flat = np.stack([block[name].reshape(-1) for name in names])
        bright = np.flatnonzero((flat > 1.0).any(axis=0))

        want_bright = min(len(bright), int(round(quota * bright_fraction)))
        picks = []
        if want_bright > 0:
            picks.append(rng.choice(bright, size=want_bright, replace=False))
        want_uniform = quota - want_bright
        if want_uniform > 0:
            picks.append(
                rng.choice(total, size=min(want_uniform, total), replace=False)
            )
        chosen = (
            np.unique(np.concatenate(picks))
            if picks
            else np.array([], dtype=int)
        )

        coords = np.unravel_index(chosen, shape)
        origin = origins[index]
        values = flat[:, chosen]
        for position in range(chosen.size):
            row = [
                float(index),
                float(origin[0] + coords[0][position] * scale[0]),
                float(origin[1] + coords[1][position] * scale[1]),
                float(origin[2] + coords[2][position] * scale[2]),
            ]
            row.extend(
                round(float(values[channel, position]), 3)
                for channel in range(len(names))
            )
            rows.append(row)
    return rows


def _to_png(
    plane: np.ndarray,
    low_pct: float = DEFAULT_LOW_PCT,
    high_pct: float = DEFAULT_HIGH_PCT,
) -> bytes:
    """Encode a 2-D array as a contrast-stretched grayscale PNG.

    The window is applied here rather than in the browser because the
    PNG is only 8-bit: anything clipped at this step is gone, so a CSS
    filter could never recover dim signal from the tails. Restretching
    server-side costs a round trip but keeps the full 16-bit range
    reachable.

    Args:
        plane: Image data.
        low_pct: Percentile mapped to black.
        high_pct: Percentile mapped to white.

    Returns:
        PNG bytes.
    """
    data = np.asarray(plane, dtype=np.float32)
    low_pct = float(np.clip(low_pct, 0.0, 100.0))
    high_pct = float(np.clip(high_pct, 0.0, 100.0))
    if high_pct <= low_pct:
        low_pct, high_pct = DEFAULT_LOW_PCT, DEFAULT_HIGH_PCT
    low, high = np.percentile(data, (low_pct, high_pct))
    if not np.isfinite(high) or high <= low:
        low, high = float(data.min()), float(max(data.max(), data.min() + 1))
    scaled = np.clip((data - low) / (high - low), 0.0, 1.0)
    image = Image.fromarray((scaled * 255).astype(np.uint8), mode="L")
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _figure_to_png(figure) -> str:
    """Render a matplotlib figure to a base64 data URI.

    Args:
        figure: Figure to render; closed afterwards.

    Returns:
        A ``data:image/png;base64,...`` string.
    """
    import base64

    import matplotlib.pyplot as plt

    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    plt.close(figure)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _coefficients_payload(
    coeffs: Dict[Tuple[str, str], Dict[str, float]],
) -> Dict[str, Dict[str, Optional[float]]]:
    """Make the coefficient dict JSON-safe.

    Tuple keys become ``"src|dst"`` and non-finite floats become null,
    which the standard JSON encoder cannot represent.

    Args:
        coeffs: Output of ``bleedthrough.pair_coefficients``.

    Returns:
        A JSON-serialisable mapping.
    """
    payload: Dict[str, Dict[str, Optional[float]]] = {}
    for (src, dst), entry in coeffs.items():
        payload[f"{src}|{dst}"] = {
            key: (float(value) if np.isfinite(value) else None)
            for key, value in entry.items()
        }
    return payload


def _run_analysis(
    base_path: str,
    channel_names: Sequence[str],
    regions: Sequence[Sequence[int]],
    size_zyx: Sequence[int],
    background: Optional[str],
    bright_nsigma: float,
    voxel_size_um: Optional[float],
    window_um: Optional[float],
    n_sample: int,
    sample_bright_fraction: float,
    check_registration: bool,
    level: int = 0,
    min_bright_fraction: float = DEFAULT_MIN_BRIGHT,
    progress: Optional[Any] = None,
) -> Dict[str, Any]:
    """Fetch every region's block and run the pooled analysis.

    Regions carrying no signal are dropped before fitting. Background
    has nothing to correlate, so pooling it in distorts the
    coefficients, and a pair whose source is never bright anywhere
    divides by zero and comes back NaN.

    Args:
        base_path: ``s3://`` URI of the fused directory.
        channel_names: Channels to include.
        regions: Block centres in level-0 voxels.
        size_zyx: Block extent, in voxels of ``level``.
        background: Passed to ``normalize_blocks``.
        bright_nsigma: Source-brightness threshold in sigmas.
        voxel_size_um: Lateral voxel size **of the sampled level**;
            needed for the null.
        window_um: Displacement window lower bound; needed for the null.
        n_sample: Display sample size.
        sample_bright_fraction: Share of the display sample drawn from
            bright voxels.
        check_registration: Whether to run the offset scan.
        level: Pyramid level the blocks are read from.
        min_bright_fraction: Least share of bright voxels for a region
            to be kept. Zero keeps every region.
        progress: Optional ``(stage, done, total)`` callback used to
            drive the progress bar.

    Returns:
        The JSON payload for the run endpoint.

    Raises:
        ValueError: If fewer than two channels resolve, or the gate
            rejected every region.
    """

    def _say(stage, done=0, total=0):
        """Forward a stage to the progress callback when there is one."""
        if progress is not None:
            progress(stage, done, total)

    _say("reading metadata")
    available = zarr_reader.detect_channels(base_path)
    wanted = [c for c in available if c.name in set(channel_names)]
    if len(wanted) < 2:
        raise ValueError(
            "at least two channels are required; asked for "
            f"{list(channel_names)}, found "
            f"{[c.name for c in available]}"
        )

    info = wanted[0].info
    level = max(0, min(int(level), len(info.levels) - 1))
    scale = zarr_reader.level_scale_factors(info, level)

    _say("reading blocks", 0, len(regions) * len(wanted))
    all_blocks, all_origins = zarr_reader.extract_region_blocks(
        wanted,
        regions,
        size_zyx,
        level=level,
        on_progress=lambda done, total: _say("reading blocks", done, total),
    )
    names = [c.name for c in wanted]

    fractions = [bright_fraction(block, bright_nsigma) for block in all_blocks]
    keep = [
        i for i, frac in enumerate(fractions) if frac >= min_bright_fraction
    ]
    dropped = [
        {
            "index": i,
            "center": [int(v) for v in regions[i]],
            "bright_fraction": round(fractions[i], 5),
        }
        for i in range(len(all_blocks))
        if i not in set(keep)
    ]
    if not keep:
        raise ValueError(
            f"every region was background (gate "
            f"{min_bright_fraction:.3%}); the brightest reached "
            f"{max(fractions, default=0.0):.3%}. Move the regions onto "
            "tissue or lower the signal gate."
        )

    blocks = [all_blocks[i] for i in keep]
    origins = [all_origins[i] for i in keep]

    _say("normalising")
    normalized, sigmas, baselines = bt.normalize_blocks(blocks, background)
    _say("fitting coefficients")
    coeffs = bt.pair_coefficients(
        normalized,
        bright_nsigma=bright_nsigma,
        voxel_size_um=voxel_size_um,
        window_um=window_um,
    )
    _say("sampling voxels")
    points = stratified_sample(
        normalized,
        origins,
        names,
        n_sample,
        sample_bright_fraction,
        scale=scale,
    )
    if check_registration:
        _say("checking registration")
        registration = registration_scan(normalized, names, bright_nsigma)
    else:
        registration = {}

    run_id = uuid.uuid4().hex
    _block_cache.put(
        run_id,
        {
            "blocks": blocks,
            "names": names,
            "origins": origins,
            "background": background,
            "coefficients": coeffs,
            "base_path": base_path,
        },
    )

    block_shape = blocks[0][names[0]].shape
    return {
        "run_id": run_id,
        "channels": names,
        "sigmas": {k: float(v) for k, v in sigmas.items()},
        "baselines": {k: float(v) for k, v in baselines.items()},
        "coefficients": _coefficients_payload(coeffs),
        "points": points,
        "regions": [
            {
                "index": index,
                "origin": [int(v) for v in origin],
                "center": [int(v) for v in regions[keep[index]]],
                "bright_fraction": round(fractions[keep[index]], 5),
            }
            for index, origin in enumerate(origins)
        ],
        "dropped": dropped,
        "level": level,
        "voxel_size_um": float(voxel_size_um or 0.0),
        "block_shape": [int(v) for v in block_shape],
        "block_um": [
            round(float(block_shape[i] * info.levels[level].scale_zyx[i]), 1)
            for i in range(3)
        ],
        "voxels_per_region": int(np.prod(block_shape)),
        "has_null": bool(voxel_size_um and window_um),
        "registration": registration,
        "zarr_paths": [c.zarr_path for c in wanted],
    }


@router.post("/detect")
async def detect(payload: Dict[str, Any]):
    """Enumerate channels and grid metadata for a fused directory.

    Args:
        payload: JSON body carrying ``base_path``.

    Returns:
        Channel list, shared voxel size and pyramid shapes.
    """
    base_path = (payload or {}).get("base_path", "").strip()
    if not base_path:
        return JSONResponse(
            status_code=400, content={"error": "base_path is required"}
        )
    try:
        channels = await run_in_threadpool(
            zarr_reader.detect_channels, base_path
        )
        await run_in_threadpool(zarr_reader.validate_common_grid, channels)
    except ZarrMetadataError as err:
        logger.warning("detect failed for %s: %s", base_path, err)
        return JSONResponse(status_code=422, content={"error": str(err)})
    except Exception as err:
        logger.error("detect failed for %s: %s", base_path, err)
        return JSONResponse(status_code=502, content={"error": str(err)})

    first = channels[0].info
    return {
        "base_path": base_path,
        "channels": [
            {
                "name": c.name,
                "zarr_path": c.zarr_path,
                "shape_zyx": [int(v) for v in c.info.shape_zyx],
            }
            for c in channels
        ],
        "shape_zyx": [int(v) for v in first.shape_zyx],
        "voxel_size_zyx": [float(v) for v in first.voxel_size_zyx],
        "lateral_voxel_um": first.lateral_voxel_um,
        "chunks_zyx": [int(v) for v in first.chunks_zyx],
        "levels": [
            {
                "level": index,
                "shape_zyx": [int(v) for v in level.shape_zyx],
                "chunks_zyx": [int(v) for v in level.chunks_zyx],
                "voxel_um": [float(v) for v in level.scale_zyx],
            }
            for index, level in enumerate(first.levels)
        ],
    }


@router.get("/plane")
async def plane(
    zarr_path: str = Query(...),
    plane: str = Query("XY"),
    position: int = Query(0),
    level: Optional[int] = Query(None),
    low_pct: float = Query(DEFAULT_LOW_PCT),
    high_pct: float = Query(DEFAULT_HIGH_PCT),
):
    """Serve one viewer slice as a PNG.

    Returned as image bytes rather than base64 JSON so the browser can
    cache it and an ``<img>`` tag can point straight at the URL. The
    contrast percentiles are part of the URL, so revisiting a setting
    is served from the browser cache with no round trip.

    Args:
        zarr_path: ``s3://`` URI of the channel's zarr group.
        plane: ``XY``, ``ZY`` or ``ZX``.
        position: Index along the fixed axis, in level voxels.
        level: Pyramid level; auto-selected when omitted.
        low_pct: Percentile rendered as black.
        high_pct: Percentile rendered as white.

    Returns:
        A PNG response, with the chosen level in the headers.
    """
    try:
        image, chosen, shape = await run_in_threadpool(
            zarr_reader.preview_plane, zarr_path, plane, position, level
        )
        body = await run_in_threadpool(_to_png, image, low_pct, high_pct)
    except Exception as err:
        logger.error("plane failed for %s: %s", zarr_path, err)
        return JSONResponse(status_code=502, content={"error": str(err)})

    return Response(
        content=body,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Zarr-Level": str(chosen),
            "X-Level-Shape": ",".join(str(int(v)) for v in shape),
            "X-Image-Size": f"{image.shape[1]},{image.shape[0]}",
        },
    )


@router.get("/slab")
async def slab(
    zarr_path: str = Query(...),
    plane: str = Query("XY"),
    start: int = Query(0),
    count: int = Query(32),
    level: Optional[int] = Query(None),
    target_px: int = Query(320),
    crop: Optional[str] = Query(None),
):
    """Serve a run of viewer planes as raw little-endian uint16.

    Raw rather than PNG, and a slab rather than a plane, because both
    of the viewer's slow paths are round trips. Sixteen-bit samples let
    the browser re-window the contrast with no request at all, and
    consecutive planes share chunks so the slab costs about what one
    plane costs. After this lands, scrubbing and contrast are local.

    Args:
        zarr_path: ``s3://`` URI of the channel's zarr group.
        plane: ``XY``, ``ZY`` or ``ZX``.
        start: First index along the fixed axis, in level voxels.
        count: How many planes to return.
        level: Pyramid level; auto-selected when omitted.
        target_px: Longest side of a plane after decimation.
        crop: Optional ``"row0,row1,col0,col1"`` in level voxels, to
            spend the resolution budget on the imaged region instead of
            the empty margin around it.

    Returns:
        ``application/octet-stream`` of ``count * rows * cols`` uint16,
        with the geometry in the headers.
    """
    box = None
    if crop:
        try:
            box = [int(v) for v in crop.split(",")]
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": "crop must be 'row0,row1,col0,col1'"},
            )
        if len(box) != 4:
            return JSONResponse(
                status_code=400,
                content={"error": "crop must be 'row0,row1,col0,col1'"},
            )
    try:
        data, chosen, shape, step = await run_in_threadpool(
            zarr_reader.read_slab,
            zarr_path,
            plane,
            start,
            count,
            level,
            target_px,
            box,
        )
    except Exception as err:
        logger.error("slab failed for %s: %s", zarr_path, err)
        return JSONResponse(status_code=502, content={"error": str(err)})

    body = np.ascontiguousarray(data, dtype="<u2").tobytes()
    return Response(
        content=body,
        media_type="application/octet-stream",
        headers={
            # Immutable for a given URL, so re-visiting a slab during a
            # session never touches the network again.
            "Cache-Control": "public, max-age=3600",
            "X-Slab-Shape": ",".join(str(int(v)) for v in data.shape),
            "X-Zarr-Level": str(chosen),
            "X-Level-Shape": ",".join(str(int(v)) for v in shape),
            "X-Step": str(int(step)),
            "X-Start": str(int(start)),
        },
    )


@router.post("/grid")
async def grid(payload: Dict[str, Any]):
    """Propose an evenly spaced lattice of region centres.

    Hand-placing regions one at a time does not reach the count the
    coefficients need: pooling 1 region instead of 25 inflated alpha
    several-fold on a real dataset. A lattice gets there in one click
    and is reproducible, and the caller can still edit the result.

    Args:
        payload: JSON body with ``base_path``, ``counts`` per axis,
            optional ``block_size``, ``level`` and ``margin``.

    Returns:
        A dict holding the proposed level-0 ``regions``.
    """
    body = payload or {}
    base_path = str(body.get("base_path", "")).strip()
    if not base_path:
        return JSONResponse(
            status_code=400, content={"error": "base_path is required"}
        )
    counts = body.get("counts") or [2, 3, 3]
    size = body.get("block_size") or [128, 128, 128]
    margin = float(body.get("margin", 0.1))
    level = int(body.get("level", 0))

    try:
        channels = await run_in_threadpool(
            zarr_reader.detect_channels, base_path
        )
    except Exception as err:
        return JSONResponse(status_code=502, content={"error": str(err)})

    info = channels[0].info
    level = max(0, min(level, len(info.levels) - 1))
    shape = info.levels[level].shape_zyx
    chunks = info.levels[level].chunks_zyx
    scale = zarr_reader.level_scale_factors(info, level)
    align = bool(body.get("align_to_chunks", True))

    per_axis = []
    for axis in range(3):
        extent = int(shape[axis])
        span = min(int(size[axis]), extent)
        number = max(int(counts[axis]), 1)
        low = int(margin * extent) + span // 2
        high = extent - span // 2 - int(margin * extent)
        if high < low:
            low = high = extent // 2
        if number == 1:
            positions = [(low + high) // 2]
        else:
            positions = [
                int(round(low + (high - low) * i / (number - 1)))
                for i in range(number)
            ]

        # Snap so each block sits inside one chunk instead of
        # straddling two. Measured on a real dataset: 4 chunks per read
        # down to 1, and the pass twice as fast for identical data.
        # Free here because a lattice position is arbitrary anyway --
        # hand-placed regions are never moved.
        step = int(chunks[axis]) if align else 0
        if step > 0:
            snapped = []
            for centre in positions:
                start = (max(centre - span // 2, 0) // step) * step
                start = min(start, max(extent - span, 0))
                snapped.append(start + span // 2)
            positions = sorted(set(snapped))
        per_axis.append(positions)

    regions = [
        [
            int(round(z * scale[0])),
            int(round(y * scale[1])),
            int(round(x * scale[2])),
        ]
        for z in per_axis[0]
        for y in per_axis[1]
        for x in per_axis[2]
    ]
    return {"regions": regions, "level": level, "count": len(regions)}


@router.post("/run")
async def run(payload: Dict[str, Any]):
    """Extract blocks for every region and run the pooled analysis.

    Args:
        payload: JSON body with ``base_path``, ``channels``,
            ``regions``, ``block_size`` and the analysis parameters.

    Returns:
        ``{job_id}``; poll ``/run-status`` for progress and the result.
    """
    body = payload or {}
    base_path = str(body.get("base_path", "")).strip()
    channels = body.get("channels") or []
    regions = body.get("regions") or []
    if not base_path or not channels or not regions:
        return JSONResponse(
            status_code=400,
            content={"error": "base_path, channels and regions are required"},
        )

    size = body.get("block_size") or [128, 128, 128]
    if isinstance(size, (int, float)):
        size = [int(size)] * 3
    voxel_um = body.get("voxel_size_um")
    window_um = body.get("window_um", DEFAULT_WINDOW_UM)

    job_id = uuid.uuid4().hex
    args = (
        base_path,
        channels,
        [[int(v) for v in r] for r in regions],
        [int(v) for v in size],
        body.get("background", "median"),
        float(body.get("bright_nsigma", 3.0)),
        float(voxel_um) if voxel_um else None,
        float(window_um) if window_um else None,
        int(body.get("n_sample", DEFAULT_SAMPLE)),
        float(body.get("bright_fraction", DEFAULT_BRIGHT_FRACTION)),
        bool(body.get("check_registration", True)),
        int(body.get("level", 0)),
        float(body.get("min_bright_fraction", DEFAULT_MIN_BRIGHT)),
    )

    def _work() -> None:
        """Run the analysis, recording progress against the job."""
        try:
            result = _run_analysis(
                *args,
                progress=lambda stage, done, total: _set_job(
                    job_id, stage=stage, done=done, total=total
                ),
            )
            _set_job(job_id, state="done", stage="done", result=result)
        except (ValueError, ZarrMetadataError) as err:
            logger.warning("run rejected: %s", err)
            _set_job(job_id, state="error", error=str(err), status=422)
        except Exception as err:  # pragma: no cover - unexpected
            logger.error("run failed: %s", err, exc_info=True)
            _set_job(job_id, state="error", error=str(err), status=500)

    _set_job(job_id, state="running", stage="queued", done=0, total=0)
    _job_pool.submit(_work)
    return {"job_id": job_id}


@router.get("/run-status")
async def run_status(job_id: str = Query(...)):
    """Report a run's progress, and its result once finished.

    Polled rather than streamed: a run is tens of seconds of blocking
    S3 reads, and this keeps the request that started it from being
    held open for all of it.

    Args:
        job_id: Identifier returned by ``/run``.

    Returns:
        ``{state, stage, done, total}`` while running, plus ``result``
        when done or ``error`` when it failed.
    """
    job = _get_job(job_id)
    if job is None:
        return JSONResponse(
            status_code=404, content={"error": "unknown or expired job"}
        )
    if job.get("state") == "error":
        return JSONResponse(
            status_code=job.get("status", 500),
            content={"error": job.get("error", "run failed")},
        )
    if job.get("state") == "done":
        return JSONResponse(
            content={"state": "done", "result": job.get("result")}
        )
    return {
        "state": "running",
        "stage": job.get("stage", ""),
        "done": job.get("done", 0),
        "total": job.get("total", 0),
    }


@router.post("/figures")
async def figures(payload: Dict[str, Any]):
    """Render the publication figures for a completed run.

    Uses the blocks cached by ``/run`` so nothing is re-downloaded.

    Args:
        payload: JSON body carrying ``run_id``.

    Returns:
        Base64 PNG data URIs for the density grid and each pair
        composite.
    """
    run_id = str((payload or {}).get("run_id", ""))
    entry = _block_cache.get(run_id)
    if entry is None:
        return JSONResponse(
            status_code=404,
            content={"error": "run not found or evicted; re-run diagnostics"},
        )

    def _render() -> Dict[str, Any]:
        """Build every figure from the cached blocks."""
        grid, _ = bt.plot_density_grid(
            entry["blocks"], background=entry["background"]
        )
        labels = [f"region {i}" for i in range(len(entry["blocks"]))]
        extra = {}
        # One normalisation across every column, so regions can be
        # compared with each other; scaling each separately hides
        # exactly the differences between them. Only some revisions of
        # bleedthrough.py expose it, so ask before passing it.
        if (
            "shared_scale"
            in inspect.signature(bt.plot_pair_composite).parameters
        ):
            extra["shared_scale"] = True
        composites = bt.plot_pair_composites(
            entry["blocks"],
            coefficients=entry["coefficients"],
            background=entry["background"],
            column_labels=labels,
            **extra,
        )
        return {
            "density_grid": _figure_to_png(grid),
            "composites": {
                f"{src}|{dst}": _figure_to_png(fig)
                for (src, dst), fig in composites.items()
            },
        }

    try:
        return JSONResponse(content=await run_in_threadpool(_render))
    except Exception as err:
        logger.error("figure rendering failed: %s", err, exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(err)})


@router.post("/ng-link")
async def ng_link(payload: Dict[str, Any]):
    """Build a Neuroglancer link centred on one voxel.

    Args:
        payload: JSON body with ``zarr_paths`` and a ``position`` given
            as level-0 ``[z, y, x]``.

    Returns:
        A dict holding the Neuroglancer ``url``.
    """
    body = payload or {}
    paths = body.get("zarr_paths") or []
    position = body.get("position") or []
    if not paths or len(position) != 3:
        return JSONResponse(
            status_code=400,
            content={"error": "zarr_paths and a [z,y,x] position required"},
        )

    z, y, x = (int(v) for v in position)
    try:
        url = await run_in_threadpool(
            ng_utils.create_link_no_upload,
            paths,
            annotation_color=body.get("annotation_color", "#FFFF00"),
            cross_section_scale=body.get("cross_section_scale", 0.2),
            cell_id=body.get("cell_id", 0),
            spot_id=body.get("spot_id", 0),
            position=[x, y, z, 0],
            point_annotation=[x, y, z, 0.5, 0],
        )
    except Exception as err:
        logger.error("ng-link failed: %s", err, exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(err)})
    return {"url": url}
