"""Channel bleed-through diagnostics for multiplex fluorescence blocks.

Both figures here are driven from *sample blocks* handed in by the
caller rather than from a dataset on disk, so a pipeline can pass arrays
it already holds in memory.

A **sample block** is a mapping from channel name to a 3-D ``(z, y, x)``
array of raw counts covering one region. Every channel in a block must
share the same shape. Functions that pool statistics accept either one
block or a sequence of blocks sampled from different regions.

Two entry points:

``plot_density_grid``
    An N x N grid of channel-against-channel intensity densities with
    every channel divided by **its own robust sigma**. Normalizing is
    not cosmetic: in raw counts the fitted slope carries the ratio of
    the two channels' scales, so a noisy destination channel outranks a
    genuinely coupled pair. Dividing by sigma turns the slope into a
    quantity that is comparable between pairs.

``plot_pair_composite``
    A two-colour image of one channel pair -- source in green,
    destination in magenta -- so voxels carrying equal signal relative
    to their own noise render **neutral grey**. Green and magenta are
    used rather than green and red because the equal point is then a
    neutral rather than a hue: a 2:1 imbalance moves the colour 22.7
    OKLab units off the equal point instead of 14.3, so the same
    imbalance is 1.6x more visible. Colour-vision deficiency is not the
    reason; both pairs clear the usual separation threshold.

Example:
    Blocks are plain arrays, so a caller that already holds a chunk of
    a fused volume passes it straight in::

        from see_spot import bleedthrough as bt

        block = {"ch_488": arr_488, "ch_561": arr_561}  # (z, y, x)

        fig, coeffs = bt.plot_density_grid(
            block, voxel_size_um=0.1534, window_um=0.93
        )
        figures = bt.plot_pair_composites(block, coefficients=coeffs)

    ``voxel_size_um`` and ``window_um`` are what enable the displaced
    null; without them only the observed slope is reported, which
    conflates optical coupling with shared tissue structure. Pass a
    list of blocks to pool statistics over several sampled regions, and
    read the numbers alone -- no figures -- with
    :func:`normalize_blocks` followed by :func:`pair_coefficients`.

Nothing here decides whether an observed coupling *is* spectral
bleed-through. Channels image the same tissue, so correlation is
expected without any optical coupling at all. The displaced null
subtracts the part of the slope that survives sliding the destination
sideways, which is the part tissue structure can explain; what remains
is local. Co-location still cannot separate leakage from genuine
co-expression.
"""

import re
from collections.abc import Mapping
from typing import Dict, List, Optional, Sequence, Tuple, Union

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import cm
from matplotlib.figure import Figure

__all__ = [
    "robust_sigma",
    "normalize_blocks",
    "through_origin_slope",
    "displaced_slope",
    "displacement_offsets",
    "pair_coefficients",
    "composite_rgb",
    "channel_colors",
    "plot_density_grid",
    "plot_pair_composite",
    "plot_pair_composites",
]

SampleBlock = Mapping[str, np.ndarray]
Blocks = Union[SampleBlock, Sequence[SampleBlock]]

MAD_TO_SIGMA = 1.4826
DEFAULT_BRIGHT_NSIGMA = 3.0
DEFAULT_SAMPLE = 500_000
DEFAULT_LOW_SIGMAS = -1.5
SOURCE_RGB = (0.0, 0.78, 0.30)
DEST_RGB = (0.85, 0.0, 0.85)
_WAVELENGTH_RE = re.compile(r"(\d{3,4})")
_TINY = 1e-9


def robust_sigma(values: np.ndarray) -> float:
    """Noise scale of an array from its median absolute deviation.

    The plain standard deviation is inflated by the signal it is meant
    to exclude -- on lightsheet data by 15-30% -- which would make the
    brightest channels look relatively less noisy than they are.

    Args:
        values: Any numeric array. NaNs are ignored.

    Returns:
        ``1.4826 * MAD``, or ``0.0`` for an empty or all-NaN array.
    """
    flat = np.asarray(values).reshape(-1)
    finite = flat[np.isfinite(flat)]
    if finite.size == 0:
        return 0.0
    med = np.median(finite)
    return float(MAD_TO_SIGMA * np.median(np.abs(finite - med)))


def _as_block_list(blocks: Blocks) -> List[SampleBlock]:
    """Accept one sample block or a sequence and return a list.

    Args:
        blocks: One sample block, or a sequence of them.

    Returns:
        A list holding at least one block.

    Raises:
        ValueError: If the sequence is empty.
    """
    if isinstance(blocks, Mapping):
        return [blocks]
    listed = list(blocks)
    if not listed:
        raise ValueError("no sample blocks were given")
    return listed


def _channel_names(block_list: Sequence[SampleBlock]) -> List[str]:
    """Validate the blocks and return the channel names they share.

    Args:
        block_list: Sample blocks, all covering the same channels.

    Returns:
        Channel names in the order of the first block.

    Raises:
        ValueError: If fewer than two channels are present, if the
            blocks disagree on which channels they hold, or if the
            channels within one block have different shapes.
    """
    names = list(block_list[0])
    if len(names) < 2:
        raise ValueError("a sample block needs at least two channels")
    for block in block_list:
        if set(block) != set(names):
            raise ValueError(
                f"blocks hold different channels: {sorted(set(names))} "
                f"vs {sorted(set(block))}"
            )
        shapes = {np.shape(block[name]) for name in names}
        if len(shapes) != 1:
            raise ValueError(
                f"channels within a block differ in shape: {shapes}"
            )
    return names


def normalize_blocks(
    blocks: Blocks,
    background: Union[str, Mapping[str, float], None] = "median",
    sigmas: Optional[Mapping[str, float]] = None,
) -> Tuple[List[Dict[str, np.ndarray]], Dict[str, float], Dict[str, float]]:
    """Background-subtract and z-score every channel of every block.

    Scales are pooled across blocks so that all blocks end up in one
    common unit, which is what makes figures from different regions
    comparable.

    Args:
        blocks: One sample block or a sequence of them, in raw counts.
        background: ``"median"`` to subtract each channel's pooled
            median, ``None`` for blocks that are already
            background-subtracted, or a mapping of channel name to an
            explicit pedestal.
        sigmas: Optional precomputed per-channel sigmas. When omitted,
            the pooled robust sigma of the background-subtracted data
            is used.

    Returns:
        A tuple ``(normalized, sigmas, baselines)``. ``normalized`` is
        one dict of float32 arrays in sigma units per input block.

    Raises:
        ValueError: If ``background`` is not one of the accepted forms.
    """
    block_list = _as_block_list(blocks)
    names = _channel_names(block_list)

    if background is None:
        baselines = {name: 0.0 for name in names}
    elif isinstance(background, Mapping):
        baselines = {name: float(background[name]) for name in names}
    elif background == "median":
        baselines = {
            name: float(
                np.median(
                    np.concatenate(
                        [np.asarray(b[name]).reshape(-1) for b in block_list]
                    )
                )
            )
            for name in names
        }
    else:
        raise ValueError(
            "background must be 'median', None, or a mapping of "
            f"channel to pedestal, not {background!r}"
        )

    centred = [
        {
            name: (np.asarray(block[name], dtype=np.float32) - baselines[name])
            for name in names
        }
        for block in block_list
    ]

    if sigmas is None:
        scales = {
            name: robust_sigma(
                np.concatenate([b[name].reshape(-1) for b in centred])
            )
            for name in names
        }
    else:
        scales = {name: float(sigmas[name]) for name in names}

    normalized = [
        {name: block[name] / max(scales[name], _TINY) for name in names}
        for block in centred
    ]
    return normalized, scales, baselines


def _origin_fit_sums(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    """Accumulate the two sums a through-origin fit needs.

    Both sums are accumulated in float64 even for float32 inputs;
    ``(x * y).sum()`` on float32 loses precision over millions of
    voxels and also materializes a full temporary array.

    Args:
        x: Predictor values, already flattened or flattenable.
        y: Response values of the same size.

    Returns:
        A tuple ``(sum_xy, sum_xx)``.
    """
    xf = np.asarray(x).reshape(-1)
    yf = np.asarray(y).reshape(-1)
    sxy = float(np.einsum("i,i->", xf, yf, dtype=np.float64))
    sxx = float(np.einsum("i,i->", xf, xf, dtype=np.float64))
    return sxy, sxx


def through_origin_slope(x: np.ndarray, y: np.ndarray) -> float:
    """Least-squares slope of y on x with the intercept fixed at zero.

    The intercept is fixed because a free intercept would absorb part
    of the leak being measured.

    Args:
        x: Predictor values.
        y: Response values of the same size.

    Returns:
        ``sum(x * y) / sum(x * x)``, or NaN when x carries no variance.
    """
    sxy, sxx = _origin_fit_sums(x, y)
    if sxx <= 0:
        return float("nan")
    return sxy / sxx


def _shifted_views(
    source: np.ndarray,
    dest: np.ndarray,
    mask: np.ndarray,
    axis: int,
    offset: int,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Slice source, destination and mask into an offset alignment.

    Args:
        source: Source-channel array.
        dest: Destination-channel array of the same shape.
        mask: Boolean array of the same shape.
        axis: Axis to displace along.
        offset: Displacement in voxels.

    Returns:
        A tuple of three views, or None when the offset exceeds the
        extent of that axis.
    """
    length = source.shape[axis]
    if offset >= length:
        return None
    keep = [slice(None)] * source.ndim
    moved = [slice(None)] * source.ndim
    keep[axis] = slice(0, length - offset)
    moved[axis] = slice(offset, length)
    return (
        source[tuple(keep)],
        dest[tuple(moved)],
        mask[tuple(keep)],
    )


def displaced_slope(
    source: np.ndarray,
    dest: np.ndarray,
    mask: np.ndarray,
    axis: int,
    offset: int,
) -> float:
    """Through-origin slope with the destination slid sideways.

    Args:
        source: Source-channel array.
        dest: Destination-channel array of the same shape.
        mask: Boolean array selecting the voxels to fit over.
        axis: Axis to displace along.
        offset: Displacement in voxels; ``0`` gives the observed slope.

    Returns:
        The fitted slope, or NaN when the offset leaves nothing to fit.
    """
    views = _shifted_views(source, dest, mask, axis, offset)
    if views is None:
        return float("nan")
    src, dst, sel = views
    return through_origin_slope(src[sel], dst[sel])


def displacement_offsets(
    voxel_size_um: float,
    window_um: float,
    max_window_factor: float = 3.0,
    max_offset: Optional[int] = None,
) -> List[int]:
    """Voxel offsets whose physical shift falls inside the null window.

    The window has to sit **above** the optical scale, so that genuine
    optical coupling has decorrelated, and **below** the tissue
    correlation length, so that tissue structure has not. Choosing it
    is the caller's job; this only converts the window into offsets.

    Args:
        voxel_size_um: Lateral voxel size in micrometres.
        window_um: Lower bound of the window in micrometres.
        max_window_factor: Upper bound as a multiple of ``window_um``.
        max_offset: Optional cap, normally the array extent.

    Returns:
        Sorted integer offsets, possibly empty when the window falls
        below one voxel at this resolution.
    """
    if voxel_size_um <= 0 or window_um <= 0:
        return []
    lo = int(np.ceil(window_um / voxel_size_um))
    hi = int(np.floor(max_window_factor * window_um / voxel_size_um))
    if max_offset is not None:
        hi = min(hi, int(max_offset))
    return [k for k in range(max(lo, 1), hi + 1)]


def pair_coefficients(
    normalized: Sequence[Mapping[str, np.ndarray]],
    bright_nsigma: float = DEFAULT_BRIGHT_NSIGMA,
    voxel_size_um: Optional[float] = None,
    window_um: Optional[float] = None,
    max_window_factor: float = 3.0,
) -> Dict[Tuple[str, str], Dict[str, float]]:
    """Coupling coefficients for every ordered pair of channels.

    Fits run over voxels where the **source** is bright. Selecting on
    the destination instead would condition on the dependent variable
    and bias the answer downward.

    Args:
        normalized: Blocks already in sigma units, as returned by
            :func:`normalize_blocks`.
        bright_nsigma: Source-brightness threshold, in sigmas.
        voxel_size_um: Lateral voxel size. Required for the null.
        window_um: Displacement window lower bound. Required for the
            null; without it only the observed slope is reported.
        max_window_factor: Upper bound of the window, as a multiple.

    Returns:
        A dict keyed by ``(source, destination)``. Each value holds
        ``observed``, ``displaced``, ``alpha`` and ``n_voxels``.
        ``displaced`` and ``alpha`` are NaN when no null was computed.
    """
    names = _channel_names(normalized)
    lateral = tuple(range(np.asarray(normalized[0][names[0]]).ndim))[-2:]

    offsets: List[int] = []
    if voxel_size_um is not None and window_um is not None:
        # capped by the LARGEST extent available: a block too small for a
        # given offset skips it, rather than that offset being dropped for
        # every block
        largest = max(
            max(np.asarray(b[names[0]]).shape[ax] for ax in lateral)
            for b in normalized
        )
        offsets = displacement_offsets(
            voxel_size_um, window_um, max_window_factor, largest - 1
        )

    masks = [
        {name: block[name] > bright_nsigma for name in names}
        for block in normalized
    ]

    out: Dict[Tuple[str, str], Dict[str, float]] = {}
    for src in names:
        for dst in names:
            sxy = sxx = 0.0
            n_vox = 0
            for block, mask in zip(normalized, masks):
                sel = mask[src]
                n_vox += int(sel.sum())
                add_xy, add_xx = _origin_fit_sums(
                    block[src][sel], block[dst][sel]
                )
                sxy += add_xy
                sxx += add_xx
            observed = sxy / sxx if sxx > 0 else float("nan")

            nulls = []
            for offset in offsets:
                for axis in lateral:
                    n_xy = n_xx = 0.0
                    for block, mask in zip(normalized, masks):
                        views = _shifted_views(
                            block[src], block[dst], mask[src], axis, offset
                        )
                        if views is None:
                            continue
                        a_src, a_dst, a_sel = views
                        add_xy, add_xx = _origin_fit_sums(
                            a_src[a_sel], a_dst[a_sel]
                        )
                        n_xy += add_xy
                        n_xx += add_xx
                    if n_xx > 0:
                        nulls.append(n_xy / n_xx)
            displaced = float(np.median(nulls)) if nulls else float("nan")

            out[(src, dst)] = {
                "observed": observed,
                "displaced": displaced,
                "alpha": observed - displaced,
                "n_voxels": float(n_vox),
            }
    return out


def channel_colors(names: Sequence[str]) -> Dict[str, tuple]:
    """Assign one display colour per channel.

    Names carrying a wavelength (``"ch_488"``, ``"488"``) are coloured
    along a perceptual ramp ordered by that wavelength, so the figures
    stay recognizable across datasets. Names without one fall back to a
    fixed cycle.

    Args:
        names: Channel names.

    Returns:
        A dict of channel name to RGBA tuple.
    """
    waves = {}
    for name in names:
        found = _WAVELENGTH_RE.search(str(name))
        if found:
            waves[name] = float(found.group(1))
    if len(waves) == len(names) and len(set(waves.values())) > 1:
        lo, hi = min(waves.values()), max(waves.values())
        return {
            name: cm.turbo(0.12 + 0.76 * (waves[name] - lo) / (hi - lo))
            for name in names
        }
    cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    return {
        name: mcolors.to_rgba(cycle[i % len(cycle)])
        for i, name in enumerate(names)
    }


def _mix(color, other, fraction: float) -> tuple:
    """Blend two colours.

    Args:
        color: Any matplotlib colour.
        other: The colour to blend towards.
        fraction: Weight given to ``other``, from 0 to 1.

    Returns:
        An RGB tuple.
    """
    first = np.array(mcolors.to_rgb(color))
    second = np.array(mcolors.to_rgb(other))
    return tuple((1 - fraction) * first + fraction * second)


def _channel_cmaps(colors: Mapping[str, tuple]) -> Dict[str, object]:
    """Build one pale-to-dark colormap per channel.

    A plain white-to-colour ramp would make single-count bins invisible
    against a white page, so the ramp starts at a pale tint instead.

    Args:
        colors: Channel name to base colour.

    Returns:
        A dict of channel name to colormap.
    """
    return {
        name: mcolors.LinearSegmentedColormap.from_list(
            f"cm_{name}",
            [_mix(color, "white", 0.88), color, _mix(color, "black", 0.55)],
        )
        for name, color in colors.items()
    }


def composite_rgb(
    z_source: np.ndarray, z_dest: np.ndarray, vmax: float
) -> np.ndarray:
    """Render a channel pair additively as green against magenta.

    The source drives green and the destination drives red and blue, so
    **equal inputs give (v, v, v) -- exactly neutral, by construction**.
    That is the whole point of the encoding: the eye judges a colour
    cast away from grey far better than it tells one hue from another.

    Args:
        z_source: Source values in sigma units.
        z_dest: Destination values in sigma units, same shape.
        vmax: Value rendered at full brightness. Values above it clip,
            values below zero go to black.

    Returns:
        A float32 RGB array with a trailing axis of length 3.
    """
    src = np.clip(np.asarray(z_source, np.float32) / vmax, 0, 1)
    dst = np.clip(np.asarray(z_dest, np.float32) / vmax, 0, 1)
    rgb = np.empty(src.shape + (3,), dtype=np.float32)
    rgb[..., 0] = dst
    rgb[..., 1] = src
    rgb[..., 2] = dst
    return rgb


def _plane(array: np.ndarray, z_index: Optional[int]) -> np.ndarray:
    """Reduce a 3-D block to the 2-D plane that gets displayed.

    Args:
        array: A 2-D or 3-D array.
        z_index: Plane to take from a 3-D array. The middle plane is
            used when omitted.

    Returns:
        A 2-D array.

    Raises:
        ValueError: If the array is neither 2-D nor 3-D.
    """
    arr = np.asarray(array)
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3:
        index = arr.shape[0] // 2 if z_index is None else int(z_index)
        return arr[min(max(index, 0), arr.shape[0] - 1)]
    raise ValueError(f"expected a 2-D or 3-D block, got {arr.ndim}-D")


def plot_density_grid(
    blocks: Blocks,
    background: Union[str, Mapping[str, float], None] = "median",
    bright_nsigma: float = DEFAULT_BRIGHT_NSIGMA,
    voxel_size_um: Optional[float] = None,
    window_um: Optional[float] = None,
    sample: int = DEFAULT_SAMPLE,
    low_sigmas: float = DEFAULT_LOW_SIGMAS,
    gridsize: int = 55,
    seed: int = 0,
    title: Optional[str] = None,
    panel_size: float = 3.6,
) -> Tuple[Figure, Dict[Tuple[str, str], Dict[str, float]]]:
    """Plot the N x N normalized intensity density grid.

    Every channel is divided by its own robust sigma first, so the
    fitted slopes are comparable between pairs. In raw counts they are
    not: the slope carries the ratio of the two channels' scales, which
    ranks a noisy destination channel above a genuinely coupled pair.

    Each off-diagonal cell shows the density of the **column** channel
    against the **row** channel, the observed through-origin fit, the
    displaced null when one could be computed, and the wedge between
    them -- the wedge area is the local coupling. Diagonal cells show
    that channel's intensity histogram with sigmas below and counts
    above, so the physical scale is still readable.

    Args:
        blocks: One sample block or a sequence sampled from the same
            resolution, in raw counts. Statistics are pooled.
        background: Passed to :func:`normalize_blocks`.
        bright_nsigma: Source-brightness threshold, in sigmas.
        voxel_size_um: Lateral voxel size; needed for the null.
        window_um: Displacement window lower bound; needed for the
            null. Without it the cells show the observed fit only.
        sample: Maximum voxels drawn for the density panels. Fits
            always use every voxel, not this sample.
        low_sigmas: Lower end of the shared axis range.
        gridsize: Hexbin resolution.
        seed: Seed for the display sample.
        title: Optional replacement for the default figure title.
        panel_size: Side of one cell in inches.

    Returns:
        A tuple ``(figure, coefficients)``, where the coefficients are
        those of :func:`pair_coefficients`.
    """
    normalized, sigmas, _ = normalize_blocks(blocks, background)
    names = _channel_names(normalized)
    coeffs = pair_coefficients(
        normalized, bright_nsigma, voxel_size_um, window_um
    )
    cmaps = _channel_cmaps(channel_colors(names))
    colors = channel_colors(names)

    pooled = {
        name: np.concatenate([b[name].reshape(-1) for b in normalized])
        for name in names
    }
    total = pooled[names[0]].size
    if sample and total > sample:
        index = np.random.default_rng(seed).choice(
            total, size=int(sample), replace=False
        )
        shown = {name: pooled[name][index] for name in names}
    else:
        shown = pooled

    high = max(float(np.percentile(shown[name], 99.9)) for name in names)
    high = max(high, bright_nsigma + 1.0)
    low = float(low_sigmas)

    count = len(names)
    fig, axes = plt.subplots(
        count, count, figsize=(panel_size * count, panel_size * count)
    )
    for row, src in enumerate(names):
        for col, dst in enumerate(names):
            ax = axes[row, col]
            if row == col:
                ax.hist(
                    shown[src],
                    bins=80,
                    range=(low, high),
                    log=True,
                    color=colors[src],
                )
                ax.set_yticks([])
                scale = max(sigmas[src], _TINY)
                secondary = ax.secondary_xaxis(
                    "top",
                    functions=(
                        lambda v, s=scale: v * s,
                        lambda u, s=scale: u / s,
                    ),
                )
                secondary.set_xlabel("counts", fontsize=8, color="0.3")
                secondary.tick_params(labelsize=7, colors="0.3")
                ax.text(
                    0.97,
                    0.94,
                    f"{src}\nintensity histogram",
                    transform=ax.transAxes,
                    ha="right",
                    va="top",
                    fontsize=9,
                    fontweight="bold",
                    color=colors[src],
                )
                ax.text(
                    0.97,
                    0.79,
                    f"1 sigma = {sigmas[src]:.1f} counts",
                    transform=ax.transAxes,
                    ha="right",
                    va="top",
                    fontsize=7.5,
                    color="0.35",
                )
            else:
                ax.hexbin(
                    shown[src],
                    shown[dst],
                    gridsize=gridsize,
                    cmap=cmaps[src],
                    mincnt=1,
                    norm=mcolors.LogNorm(),
                    extent=[low, high, low, high],
                )
                ax.axvline(
                    bright_nsigma, color="0.55", ls="-.", lw=0.9, alpha=0.8
                )
                entry = coeffs[(src, dst)]
                observed, null = entry["observed"], entry["displaced"]
                xs = np.array([0.0, high])
                if np.isfinite(observed) and np.isfinite(null):
                    ax.fill_between(
                        xs,
                        null * xs,
                        observed * xs,
                        color="crimson",
                        alpha=0.22,
                        lw=0,
                        zorder=2,
                    )
                if np.isfinite(observed):
                    ax.plot(
                        xs, observed * xs, "-", color="white", lw=3.0, zorder=3
                    )
                    ax.plot(
                        xs, observed * xs, "-", color="black", lw=1.5, zorder=4
                    )
                if np.isfinite(null):
                    ax.plot(
                        xs, null * xs, "--", color="white", lw=2.6, zorder=3
                    )
                    ax.plot(
                        xs, null * xs, "--", color="0.15", lw=1.3, zorder=4
                    )
                    label = f"$\\alpha_z$ = {entry['alpha']:.2f}"
                else:
                    label = f"obs {observed:.2f}\nnull n/a"
                ax.text(
                    0.04,
                    0.96,
                    label,
                    transform=ax.transAxes,
                    va="top",
                    ha="left",
                    fontsize=12,
                    fontweight="bold",
                    color="crimson",
                    zorder=5,
                    bbox=dict(fc="white", alpha=0.8, lw=0),
                )
                ax.set_ylim(low, high)
            ax.set_xlim(low, high)
            ax.tick_params(labelsize=7)
        axes[row, 0].annotate(
            f"SOURCE   {src}\nplotted on X",
            xy=(-0.32, 0.5),
            xycoords="axes fraction",
            rotation=90,
            ha="center",
            va="center",
            fontsize=11,
            fontweight="bold",
            color=colors[src],
        )
    for col, dst in enumerate(names):
        axes[0, col].set_title(
            f"DESTINATION   {dst}\nplotted on Y",
            fontsize=11,
            fontweight="bold",
            color=colors[dst],
            pad=34,
        )

    null_note = (
        "displaced null computed"
        if window_um is not None and voxel_size_um is not None
        else "no displaced null (pass voxel_size_um and window_um)"
    )
    fig.suptitle(
        title
        or (
            "Normalized intensity density, all channels vs all\n"
            "every channel divided by its own robust sigma   |   axes in "
            f"SIGMAS, one shared range [{low:.1f}, {high:.1f}]   |   "
            f"{null_note}\n"
            "each cell: x = ROW channel (source), y = COLUMN channel "
            f"(destination)   |   dash-dot = {bright_nsigma:g} sigma "
            "threshold\n"
            "solid = observed, dashed = displaced null, SHADED WEDGE = "
            "local coupling"
        ),
        fontsize=12,
        y=1.004,
    )
    fig.tight_layout()
    return fig, coeffs


def plot_pair_composite(
    blocks: Blocks,
    source: str,
    dest: str,
    background: Union[str, Mapping[str, float], None] = "median",
    z_index: Optional[int] = None,
    percentile: float = 99.5,
    alpha_z: Optional[float] = None,
    column_labels: Optional[Sequence[str]] = None,
    title: Optional[str] = None,
    panel_size: float = 4.0,
) -> Figure:
    """Plot one channel pair as a green-against-magenta composite.

    Each block is normalized by **its own** sigmas, because blocks
    handed to this function are usually views of one region at
    different resolutions, where the noise differs. Both channels then
    share one scale, set by the source, so a destination carrying a
    leak at coefficient ``a`` renders at ``a`` times the brightness.

    Neutral grey means the two channels carry equal signal relative to
    their own noise; a green cast means the source is stronger, magenta
    that the destination is. Both channels bright and co-located means
    either strong leakage **or** genuine co-expression -- this figure
    cannot separate them.

    Args:
        blocks: One sample block or a sequence, laid out as columns.
        source: Channel rendered in green.
        dest: Channel rendered in magenta.
        background: Passed to :func:`normalize_blocks`.
        z_index: Plane to show from 3-D blocks; middle plane if None.
        percentile: Percentile of the source that sets full brightness.
        alpha_z: Coefficient to mark on the colour key, if known.
        column_labels: One label per block.
        title: Optional replacement for the default figure title.
        panel_size: Width of one column in inches.

    Returns:
        The figure.
    """
    block_list = _as_block_list(blocks)
    _channel_names(block_list)
    columns = len(block_list)
    labels = (
        list(column_labels)
        if column_labels is not None
        else [f"block {i}" for i in range(columns)]
    )

    cmap_src = mcolors.LinearSegmentedColormap.from_list(
        "roi_src",
        [
            _mix(SOURCE_RGB, "white", 0.93),
            SOURCE_RGB,
            _mix(SOURCE_RGB, "black", 0.50),
        ],
    )
    cmap_dst = mcolors.LinearSegmentedColormap.from_list(
        "roi_dst",
        [
            _mix(DEST_RGB, "white", 0.93),
            DEST_RGB,
            _mix(DEST_RGB, "black", 0.50),
        ],
    )

    fig, axes = plt.subplots(
        3,
        columns + 1,
        figsize=(panel_size * columns + 4.4, 11.4),
        gridspec_kw={"width_ratios": [1] * columns + [0.95]},
    )
    first_vmax = 1.0
    for col, block in enumerate(block_list):
        normalized, sigmas, _ = normalize_blocks(block, background)
        z_src = _plane(normalized[0][source], z_index)
        z_dst = _plane(normalized[0][dest], z_index)
        vmax = max(float(np.percentile(z_src, percentile)), 1.0)
        if col == 0:
            first_vmax = vmax

        im_src = axes[0, col].imshow(
            z_src, cmap=cmap_src, vmin=0, vmax=vmax, interpolation="nearest"
        )
        bar = fig.colorbar(im_src, ax=axes[0, col], fraction=0.046, pad=0.03)
        bar.set_label(
            f"sigmas of {source}\n1 sigma = {sigmas[source]:.1f} counts",
            fontsize=8,
        )
        im_dst = axes[1, col].imshow(
            z_dst, cmap=cmap_dst, vmin=0, vmax=vmax, interpolation="nearest"
        )
        bar = fig.colorbar(im_dst, ax=axes[1, col], fraction=0.046, pad=0.03)
        bar.set_label(
            f"sigmas of {dest}\n1 sigma = {sigmas[dest]:.1f} counts",
            fontsize=8,
        )
        axes[2, col].imshow(
            composite_rgb(z_src, z_dst, vmax), interpolation="nearest"
        )
        # keep the composite the same width as the rows above it
        spacer = fig.colorbar(
            im_dst, ax=axes[2, col], fraction=0.046, pad=0.03
        )
        spacer.ax.set_visible(False)

        rows = [
            f"{source} (source, GREEN)",
            f"{dest} (destination, MAGENTA)",
            "composite\ngrey = equal signal",
        ]
        for row, label in enumerate(rows):
            ax = axes[row, col]
            ax.set_xticks([])
            ax.set_yticks([])
            if row == 0:
                ax.set_title(
                    f"{labels[col]}\nscale 0-{vmax:.1f} sigmas",
                    fontsize=10,
                    fontweight="bold",
                )
            if col == 0:
                ax.set_ylabel(label, fontsize=11, fontweight="bold")

    axes[0, columns].axis("off")
    axes[1, columns].axis("off")
    _draw_colour_key(axes[2, columns], first_vmax, source, dest, alpha_z)

    fig.suptitle(
        title
        or (
            f"{source} (green) vs {dest} (magenta)\n"
            "each channel divided by its OWN robust sigma, then BOTH shown "
            "on one scale = "
            f"p{percentile:g} of the source\n"
            "so NEUTRAL GREY means equal signal relative to each channel's "
            "own noise -- green = source stronger, magenta = destination "
            "stronger"
        ),
        fontsize=11.5,
        y=1.004,
    )
    fig.tight_layout()
    return fig


def _draw_colour_key(
    ax,
    vmax: float,
    source: str,
    dest: str,
    alpha_z: Optional[float] = None,
) -> None:
    """Draw the legend that decodes any composite colour into a pair.

    No diagonal is drawn: the neutral band is visible in the key
    itself, and a line would only compete with it.

    Args:
        ax: Axes to draw into.
        vmax: Full-brightness value, in sigmas.
        source: Source channel name, for the x label.
        dest: Destination channel name, for the y label.
        alpha_z: When given, marks the colour a pure leak at this
            coefficient would produce.
    """
    grid = np.linspace(0, vmax, 256)
    mesh_src, mesh_dst = np.meshgrid(grid, grid)
    ax.imshow(
        composite_rgb(mesh_src, mesh_dst, vmax),
        origin="lower",
        extent=[0, vmax, 0, vmax],
        interpolation="nearest",
    )
    corners = [
        (0.97, 0.04, "source only\n-> GREEN", "right", "bottom", "white"),
        (0.04, 0.96, "destination only\n-> MAGENTA", "left", "top", "white"),
        (0.96, 0.96, "EQUAL\n-> GREY", "right", "top", "black"),
        (0.04, 0.04, "neither\n-> black", "left", "bottom", "0.6"),
    ]
    for x, y, text, ha, va, color in corners:
        ax.text(
            x * vmax,
            y * vmax,
            text,
            fontsize=8.5,
            ha=ha,
            va=va,
            color=color,
            fontweight="bold",
        )
    if alpha_z is not None and np.isfinite(alpha_z):
        # on the ratio ray y = alpha * x, held inside the axes so the
        # marker is not clipped; the hue is exact, only the brightness
        # is below full scale
        x_mark = 0.86 * vmax
        ax.plot(
            [x_mark],
            [alpha_z * x_mark],
            "o",
            ms=11,
            mfc="none",
            mec="white",
            mew=2.0,
        )
        ax.annotate(
            f"a pure leak at alpha_z = {alpha_z:.2f}\nwould look like this",
            xy=(x_mark, alpha_z * x_mark),
            xytext=(0.22 * vmax, 0.62 * vmax),
            fontsize=8.5,
            color="white",
            fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="white", lw=1.4),
        )
    ax.set_xlabel(f"{source} (source), sigmas", fontsize=9)
    ax.set_ylabel(f"{dest} (destination), sigmas", fontsize=9)
    ax.tick_params(labelsize=8)
    ax.set_title(
        "COLOUR KEY\nwhat every colour above means",
        fontsize=10,
        fontweight="bold",
    )


def plot_pair_composites(
    blocks: Blocks,
    pairs: Optional[Sequence[Tuple[str, str]]] = None,
    coefficients: Optional[Mapping[Tuple[str, str], Mapping]] = None,
    **kwargs,
) -> Dict[Tuple[str, str], Figure]:
    """Build one composite figure per channel pair.

    Args:
        blocks: One sample block or a sequence, laid out as columns.
        pairs: Ordered pairs to plot. Defaults to every pair in channel
            order, taking each unordered pair once.
        coefficients: Output of :func:`pair_coefficients`; when given,
            each figure's colour key is marked with that pair's alpha.
        **kwargs: Forwarded to :func:`plot_pair_composite`.

    Returns:
        A dict of pair to figure, in the order the pairs were plotted.
    """
    block_list = _as_block_list(blocks)
    names = _channel_names(block_list)
    if pairs is None:
        pairs = [
            (names[i], names[j])
            for i in range(len(names))
            for j in range(i + 1, len(names))
        ]
    out: Dict[Tuple[str, str], Figure] = {}
    for src, dst in pairs:
        alpha = None
        if coefficients is not None and (src, dst) in coefficients:
            alpha = float(coefficients[(src, dst)]["alpha"])
        out[(src, dst)] = plot_pair_composite(
            block_list, src, dst, alpha_z=alpha, **kwargs
        )
    return out
