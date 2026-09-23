"""Low-level OMEZarr access for volumes held on S3.

Metadata is read with plain boto3 JSON GETs through :mod:`s3_handler`,
which keeps the credential and anonymous-access logic in one place.
Array data is read with zarr over s3fs, because hand-assembling chunks
would mean reimplementing zstd decoding and chunk-grid arithmetic.

The AIND fused volumes this targets are **zarr v2** hierarchies with 5-D
``(t, c, z, y, x)`` axes, big-endian ``uint16`` samples, zstd
compression and a ``"/"`` dimension separator. Axis names are read from
the metadata rather than assumed, so a differently shaped volume fails
loudly instead of returning silently transposed data.

Note on the s3fs version: releases before the calver scheme report
``async_impl is False``, and ``zarr.storage.FsspecStore`` rejects those
with "Filesystem needs to support async operations." ``pyproject.toml``
floors s3fs accordingly; an older resolved version is the one failure
mode that looks like zarr being broken.
"""

import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import s3fs
import zarr

from see_spot.s3_handler import s3_handler

logger = logging.getLogger(__name__)

__all__ = [
    "ZarrMetadataError",
    "LevelInfo",
    "MultiscaleInfo",
    "ChannelInfo",
    "ZarrStoreClient",
    "zarr_store",
    "parse_s3_uri",
]

SPATIAL_AXES = ("z", "y", "x")
MAX_POOL_CONNECTIONS = 128

# Matches "channel_488.zarr" and "ch_488" alike; the longer alternative
# is listed first so it is preferred over the "ch" prefix of "channel".
CHANNEL_RE = re.compile(r"(?:channel|ch)[_-]?(\d{3,4})", re.IGNORECASE)

# Where fused per-channel output lands, by pipeline vintage. Tried in
# order when the given path holds no channels itself.
FUSED_SUBDIRS = ("fusion", "image_tile_fusing/fused")


class ZarrMetadataError(RuntimeError):
    """Raised when OMEZarr metadata is missing, malformed or unsupported.

    Deliberately an error rather than a default. ``ng_utils`` returns a
    ``[1.0, 1.0, 1.0]`` voxel size when it cannot parse the metadata,
    which silently corrupts any micrometre-to-voxel conversion built on
    it; the bleed-through null is exactly such a conversion.
    """


@dataclass(frozen=True)
class LevelInfo:
    """One resolution level of a multiscale pyramid.

    Attributes:
        path: Level name inside the zarr group, e.g. ``"0"``.
        shape_zyx: Spatial extent in voxels, ordered z, y, x.
        scale_zyx: Physical size of one voxel in micrometres, z, y, x.
        chunks_zyx: Chunk shape at this level. Levels can be chunked
            differently, and the read cost of a block depends on the
            chunks of the level it is actually read from.
    """

    path: str
    shape_zyx: Tuple[int, int, int]
    scale_zyx: Tuple[float, float, float]
    chunks_zyx: Tuple[int, int, int] = (0, 0, 0)


@dataclass(frozen=True)
class MultiscaleInfo:
    """Parsed OMEZarr metadata for one array.

    Attributes:
        zarr_path: ``s3://`` URI of the zarr group.
        levels: Resolution levels, finest first.
        axis_names: Axis names in stored order, e.g. ``("t","c","z","y","x")``.
        dtype: NumPy dtype string as stored, e.g. ``">u2"``.
        chunks_zyx: Chunk shape along z, y, x.
        translation_zyx: Level-0 translation in micrometres, z, y, x.
    """

    zarr_path: str
    levels: List[LevelInfo]
    axis_names: Tuple[str, ...]
    dtype: str
    chunks_zyx: Tuple[int, int, int]
    translation_zyx: Tuple[float, float, float]

    @property
    def shape_zyx(self) -> Tuple[int, int, int]:
        """Full-resolution spatial shape.

        Returns:
            Level-0 extent in voxels, ordered z, y, x.
        """
        return self.levels[0].shape_zyx

    @property
    def voxel_size_zyx(self) -> Tuple[float, float, float]:
        """Full-resolution voxel size.

        Returns:
            Level-0 voxel size in micrometres, ordered z, y, x.
        """
        return self.levels[0].scale_zyx

    @property
    def lateral_voxel_um(self) -> float:
        """Lateral voxel size, which is what the displaced null uses.

        ``bleedthrough.pair_coefficients`` displaces along the last two
        axes, so the relevant scale is the in-plane one, not z.

        Returns:
            Mean of the y and x voxel sizes in micrometres.
        """
        _, size_y, size_x = self.voxel_size_zyx
        return float((size_y + size_x) / 2.0)


@dataclass(frozen=True)
class ChannelInfo:
    """One imaging channel of a fused dataset.

    Attributes:
        name: Channel label, normally a wavelength such as ``"488"``.
        zarr_path: ``s3://`` URI of that channel's zarr group.
        info: Parsed multiscale metadata for the channel.
    """

    name: str
    zarr_path: str
    info: MultiscaleInfo


def parse_s3_uri(uri: str) -> Tuple[str, str]:
    """Split an ``s3://bucket/key`` URI into its parts.

    Args:
        uri: URI to split. A leading ``s3://`` is optional and any
            trailing slash is dropped.

    Returns:
        A tuple ``(bucket, key)``; ``key`` is "" for a bare bucket.

    Raises:
        ZarrMetadataError: If no bucket can be read from the URI.
    """
    text = str(uri).strip().removeprefix("s3://").strip("/")
    if not text:
        raise ZarrMetadataError(f"not an S3 URI: {uri!r}")
    bucket, _, key = text.partition("/")
    return bucket, key


class ZarrStoreClient:
    """Opens OMEZarr arrays on S3 and parses their metadata.

    One filesystem instance is shared for all reads. Building a new
    ``S3FileSystem`` per array would create a fresh HTTP session each
    time and defeat connection pooling.
    """

    def __init__(self, bucket_name: Optional[str] = None):
        """Initialize the client.

        Args:
            bucket_name: Default bucket used when a path omits one.
        """
        self.bucket_name = bucket_name
        self._filesystems: Dict[bool, s3fs.S3FileSystem] = {}
        self._meta_cache: Dict[str, MultiscaleInfo] = {}
        self._array_cache: Dict[Tuple[str, int], zarr.Array] = {}
        self._lock = threading.Lock()

    def get_filesystem(self, bucket: str) -> s3fs.S3FileSystem:
        """Return the shared filesystem for a bucket.

        Signing follows whatever :mod:`s3_handler` already resolved for
        this bucket, so both layers authenticate identically.

        Args:
            bucket: Bucket the caller is about to read.

        Returns:
            A cached ``s3fs.S3FileSystem``.
        """
        anon = s3_handler.is_anonymous(bucket)
        with self._lock:
            if anon not in self._filesystems:
                logger.info(
                    "Creating s3fs filesystem (anon=%s) for '%s'",
                    anon,
                    bucket,
                )
                # asynchronous=True is what zarr's FsspecStore expects;
                # a sync-mode filesystem works but makes zarr warn on
                # every store it opens. Both then share fsspec's own IO
                # loop, so thread-pool fan-out stays safe. This instance
                # must therefore only be handed to zarr -- directory
                # listing goes through boto3 in s3_handler, not here.
                self._filesystems[anon] = s3fs.S3FileSystem(
                    anon=anon,
                    asynchronous=True,
                    config_kwargs={
                        "max_pool_connections": MAX_POOL_CONNECTIONS
                    },
                )
            return self._filesystems[anon]

    def open_level(self, zarr_path: str, level: int) -> zarr.Array:
        """Open one resolution level of a zarr group.

        Opened arrays are cached: each ``zarr.open_array`` costs a
        metadata GET, and a single analysis run opens the same arrays
        once per region.

        Args:
            zarr_path: ``s3://`` URI of the zarr group.
            level: Resolution level index; 0 is full resolution.

        Returns:
            The opened zarr array, still in its stored byte order.
        """
        cache_key = (zarr_path, int(level))
        with self._lock:
            cached = self._array_cache.get(cache_key)
        if cached is not None:
            return cached

        bucket, key = parse_s3_uri(zarr_path)
        filesystem = self.get_filesystem(bucket)
        store = zarr.storage.FsspecStore(
            filesystem, path=f"{bucket}/{key}", read_only=True
        )
        array = zarr.open_array(
            store=store, path=str(level), mode="r", zarr_format=2
        )
        with self._lock:
            self._array_cache[cache_key] = array
        return array

    def _get_json(self, bucket: str, key: str) -> dict:
        """Fetch and decode one JSON metadata object.

        Args:
            bucket: Bucket holding the object.
            key: Object key.

        Returns:
            The decoded JSON document.

        Raises:
            ZarrMetadataError: If the object is missing or not JSON.
        """
        raw = s3_handler.get_object(key=key, bucket_name=bucket)
        if raw is None:
            raise ZarrMetadataError(f"missing s3://{bucket}/{key}")
        try:
            return json.loads(raw)
        except ValueError as err:
            raise ZarrMetadataError(
                f"malformed JSON at s3://{bucket}/{key}: {err}"
            ) from err

    def read_multiscale_metadata(self, zarr_path: str) -> MultiscaleInfo:
        """Read and validate the multiscale metadata of a zarr group.

        Results are cached per path; this runs on every page load.

        Args:
            zarr_path: ``s3://`` URI of the zarr group.

        Returns:
            The parsed :class:`MultiscaleInfo`.

        Raises:
            ZarrMetadataError: If the metadata is absent, lacks z/y/x
                axes, or declares levels that cannot be read.
        """
        cached = self._meta_cache.get(zarr_path)
        if cached is not None:
            return cached

        bucket, key = parse_s3_uri(zarr_path)
        attrs = self._get_json(bucket, f"{key}/.zattrs")

        multiscales = attrs.get("multiscales")
        if not multiscales:
            raise ZarrMetadataError(
                f"no 'multiscales' in s3://{bucket}/{key}/.zattrs"
            )
        entry = multiscales[0]

        axis_names = tuple(
            str(axis["name"]).lower() for axis in entry.get("axes", [])
        )
        missing = [a for a in SPATIAL_AXES if a not in axis_names]
        if missing:
            raise ZarrMetadataError(
                f"{zarr_path}: axes {axis_names} lack {missing}; "
                "expected z, y and x to be present"
            )
        spatial_idx = [axis_names.index(a) for a in SPATIAL_AXES]

        datasets = entry.get("datasets") or []
        if not datasets:
            raise ZarrMetadataError(f"{zarr_path}: no multiscale datasets")

        levels: List[LevelInfo] = []
        dtype = ""
        chunks_zyx: Tuple[int, int, int] = (0, 0, 0)
        for dataset in datasets:
            path = str(dataset["path"])
            zarray = self._get_json(bucket, f"{key}/{path}/.zarray")
            shape = zarray["shape"]
            level_chunks = tuple(int(zarray["chunks"][i]) for i in spatial_idx)
            if not levels:
                # The top-level dtype and chunks describe level 0, which
                # is what MultiscaleInfo.chunks_zyx means; each level
                # also keeps its own, since they can differ.
                dtype = zarray["dtype"]
                chunks_zyx = level_chunks
            levels.append(
                LevelInfo(
                    path=path,
                    shape_zyx=tuple(int(shape[i]) for i in spatial_idx),
                    scale_zyx=_scale_of(dataset, spatial_idx, zarr_path),
                    chunks_zyx=level_chunks,
                )
            )

        info = MultiscaleInfo(
            zarr_path=zarr_path,
            levels=levels,
            axis_names=axis_names,
            dtype=dtype,
            chunks_zyx=chunks_zyx,
            translation_zyx=_translation_of(datasets[0], spatial_idx),
        )
        self._meta_cache[zarr_path] = info
        return info

    def _channels_in(
        self, bucket: str, base_key: str
    ) -> List[Tuple[str, str]]:
        """Find per-channel zarr groups directly under one prefix.

        Two layouts exist in the wild and both are supported:

        * flat -- ``<base>/channel_488.zarr``
        * nested -- ``<base>/ch_488/fused.zarr``

        Args:
            bucket: Bucket to list in.
            base_key: Key prefix to list under, without a trailing "/".

        Returns:
            Unsorted ``(channel_name, zarr_path)`` pairs.
        """
        found: List[Tuple[str, str]] = []
        for entry in s3_handler.list_prefixes(base_key, bucket_name=bucket):
            match = CHANNEL_RE.search(entry)
            # The name must identify a channel. A dataset root can hold
            # unrelated groups -- corrected.ome.zarr is a per-tile
            # container, not a channel -- and taking those would fail
            # later with a confusing "missing .zattrs".
            if not match:
                continue
            if entry.endswith(".zarr"):
                found.append(
                    (match.group(1), f"s3://{bucket}/{base_key}/{entry}")
                )
                continue
            # A channel-named folder holding the array, e.g. ch_488/.
            inner = f"{base_key}/{entry}"
            for child in s3_handler.list_prefixes(inner, bucket_name=bucket):
                if child.endswith(".zarr"):
                    found.append(
                        (match.group(1), f"s3://{bucket}/{inner}/{child}")
                    )
                    break
        return found

    def list_channel_zarrs(self, base_path: str) -> List[Tuple[str, str]]:
        """List the per-channel zarr groups for a dataset.

        Enumerates from S3 rather than from the processing manifest:
        ``spot_channels`` omits the background and nuclear channels,
        which still bleed into the others and so must be analysable.

        Accepts either the directory that holds the channels or the
        dataset root, descending through the known fused-output
        locations. Pipelines write to ``fusion/`` or to
        ``image_tile_fusing/fused/`` depending on their vintage, and
        pasting the dataset root is the natural thing to try.

        Args:
            base_path: ``s3://`` URI of the channel directory or of the
                dataset root.

        Returns:
            ``(channel_name, zarr_path)`` pairs, ordered by wavelength.
        """
        bucket, key = parse_s3_uri(base_path)
        base_key = key.rstrip("/")

        found = self._channels_in(bucket, base_key)
        if not found:
            for suffix in FUSED_SUBDIRS:
                found = self._channels_in(bucket, f"{base_key}/{suffix}")
                if found:
                    logger.info(
                        "No channels directly under '%s'; found them in "
                        "'%s'",
                        base_key,
                        suffix,
                    )
                    break

        def _order(pair: Tuple[str, str]) -> Tuple[int, object]:
            """Sort numerically when channels are wavelengths."""
            name = pair[0]
            return (0, int(name)) if name.isdigit() else (1, name)

        found.sort(key=_order)
        return found


def _scale_of(
    dataset: dict, spatial_idx: Sequence[int], zarr_path: str
) -> Tuple[float, float, float]:
    """Extract the z, y, x voxel size of one multiscale level.

    Args:
        dataset: One entry of ``multiscales[0].datasets``.
        spatial_idx: Indices of the z, y and x axes in stored order.
        zarr_path: Path used only for error messages.

    Returns:
        Voxel size in micrometres, ordered z, y, x.

    Raises:
        ZarrMetadataError: If the level declares no scale transform.
    """
    transforms = dataset.get("coordinateTransformations") or []
    for transform in transforms:
        if transform.get("type") == "scale":
            values = transform["scale"]
            return tuple(float(values[i]) for i in spatial_idx)
    raise ZarrMetadataError(
        f"{zarr_path}: level {dataset.get('path')} has no scale transform"
    )


def _translation_of(
    dataset: dict, spatial_idx: Sequence[int]
) -> Tuple[float, float, float]:
    """Extract the z, y, x translation of one multiscale level.

    Args:
        dataset: One entry of ``multiscales[0].datasets``.
        spatial_idx: Indices of the z, y and x axes in stored order.

    Returns:
        Translation in micrometres, ordered z, y, x; zeros when absent.
    """
    transforms = dataset.get("coordinateTransformations") or []
    for transform in transforms:
        if transform.get("type") == "translation":
            values = transform["translation"]
            return tuple(float(values[i]) for i in spatial_idx)
    return (0.0, 0.0, 0.0)


zarr_store = ZarrStoreClient("aind-open-data")
