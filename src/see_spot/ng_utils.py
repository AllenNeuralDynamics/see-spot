"""
copied from: https://codeocean.allenneuraldynamics.org/capsule/1780528/tree
on 2025-04-17
"""

import os
import pathlib
import re
import subprocess
import json
import s3fs
import zarr
from ng_link import NgState
from ng_link import link_utils
import boto3

import json
import urllib.parse


def create_direct_neuroglancer_url(
    json_data, base_url="https://neuroglancer-demo.appspot.com"
):
    """
    Creates a direct Neuroglancer URL by removing the ng_link field
    and encoding the remaining JSON as part of the URL.

    Args:
        json_data: Either a JSON string or a Python dictionary containing the Neuroglancer state
        base_url: The base Neuroglancer URL to use

    Returns:
        str: A complete Neuroglancer URL with the JSON state encoded in the fragment
    """
    # Convert string to dictionary if needed
    if isinstance(json_data, str):
        data = json.loads(json_data)
    else:
        data = json_data.copy()

    # Remove the ng_link key if it exists
    if "ng_link" in data:
        del data["ng_link"]

    # Convert to JSON string and encode for URL
    json_str = json.dumps(data)
    encoded_json = urllib.parse.quote(json_str)

    # Ensure base URL ends with /
    if not base_url.endswith("/"):
        base_url += "/"

    # Create the full URL
    full_url = f"{base_url}#!{encoded_json}"

    # Check URL length and print warning if too long
    url_length = len(full_url)
    print(f"URL character count: {url_length}")

    if url_length > 5000:
        print(
            f"WARNING: URL length ({url_length} characters) exceeds 5000 characters."
        )
        print("This may cause issues in some browsers or web servers.")
        print("Consider reducing JSON complexity or using a URL shortener.")

    return full_url


def create_link_no_upload(
    fused_s3_path,
    resolution_zyx=None,
    max_dr=1200,
    opacity=1.0,
    blend="additive",
    annotation_color="#ff0000",
    spacing=3.0,
    cross_section_scale=1.0,
    position=None,
    cell_id: int = 0,
    spot_id=None,
    point_annotation=None,
    output_folder=None,
):
    """
    Create a Neuroglancer JSON file for multiple channels with a single point annotation.

    Parameters:
    fused_s3_path (str or list): S3 path(s) to the fused dataset(s). Can be a single string or list of strings.
    resolution_zyx (list, optional): Resolution in z,y,x order. If None, attempts to read from YAML.
    max_dr (int): Maximum dynamic range for shader controls
    opacity (float): Opacity value for the layer
    blend (str): Blending mode for the layer
    annotation_color (str): Hex color for annotations
    spacing (float): Spacing for annotations in cross-section view
    cross_section_scale (float): Scale for cross-section view
    position (list or dict): Initial position to view in Neuroglancer
    cell_id (int): cell id to plot (gets added to NG json filename)
    spot_id (str or int, optional): ID for the spot annotation
    point_annotation (dict or list, optional): Coordinates [x,y,z] for a single point annotation

    Returns:
    str: URL to the Neuroglancer link
    """
    # Convert single paths to lists for consistent processing
    if isinstance(fused_s3_path, str):
        fused_s3_path = [fused_s3_path]

    # If resolution not provided, try to read from first zarr file
    if resolution_zyx is None:
        try:
            resolution_zyx = read_zarr_resolution_boto(fused_s3_path[0])
            print(f"Found resolution from zarr: {resolution_zyx}")
        except Exception as e:
            print(
                f"Warning: Could not read resolution from zarr file: {str(e)}"
            )
            # Provide a default resolution if we can't read it
            resolution_zyx = [1.0, 1.0, 1.0]
            print(f"Using default resolution: {resolution_zyx}")

    output_dimensions = {
        "x": {"voxel_size": resolution_zyx[2], "unit": "microns"},
        "y": {"voxel_size": resolution_zyx[1], "unit": "microns"},
        "z": {"voxel_size": resolution_zyx[0], "unit": "microns"},
        "c'": {"voxel_size": 1, "unit": ""},
        "t": {"voxel_size": 0.001, "unit": "seconds"},
    }

    # Initialize layers list
    layers = []  # Represent Neuroglancer Tabs

    # Process each fused path
    for idx, fused_path in enumerate(fused_s3_path):
        # Extract channel number from fused path
        pattern = r"(ch|CH|channel)_(\d+)"
        match = re.search(pattern, fused_path)
        if not match:
            raise ValueError(
                f"Could not extract channel number from path: {fused_path}"
            )

        channel = int(match.group(2))
        hex_val = wavelength_to_hex_pure_colours(channel)
        hex_str = f"#{hex_val:06x}"

        # Add image layer
        image_layer = {
            "type": "image",
            "source": fused_path,
            "channel": 0,
            "shaderControls": {"normalized": {"range": [90, max_dr]}},
            "shader": {
                "color": hex_str,
                "emitter": "RGB",
                "vec": "vec3",
            },
            "localPosition": [0.5],
            "visible": True,
            "opacity": opacity,
            "name": f"CH_{channel}",
            "blend": blend,
        }
        layers.append(image_layer)

    # Add specific point annotation if provided
    if point_annotation is not None:
        # convert output_dimensions to a meter]}
        # Create a single annotation layer for the point
        annotation_layer = {
            "type": "annotation",
            # "source": {
            #     "url":"local://annotations",
            #     "transform": output_dimensions
            # },
            # "source": "local://annotations",
            "name": f"Spot {spot_id}",
            "tab": "annotations",
            "visible": True,
            "annotationColor": annotation_color,
            "crossSectionAnnotationSpacing": spacing,
            "projectionAnnotationSpacing": 10,
            "tool": "annotatePoint",
        }

        # point = {"x":point_annotation[0], "y":point_annotation[1], "z":point_annotation[2], "t":point_annotation[3]}

        annotation = {
            "type": "point",
            "id": str(spot_id) if spot_id is not None else "spot",
            "point": point_annotation,
            # "description": f"Spot ID: {spot_id}" if spot_id is not None else "Point annotation"
        }

        annotation_layer["annotations"] = [annotation]

        # Use the point coordinates as the position if no position is specified
        if position is None:
            position = point + [0]  # Add time dimension (t=0)

        # Add the annotation layer
        annotation_layer
    print(f"annotation_layer: {annotation_layer}")

    # Set input config with dimensions from resolution_zyx
    input_config = {
        "dimensions": output_dimensions,
        "layers": layers,
        "showScaleBar": False,
        "showAxisLines": False,
    }

    # Extract bucket and dataset from first fused path
    parts = fused_s3_path[0].split("/")
    bucket_name = parts[2]
    dataset_name = parts[3]

    # Set up output folder
    if output_folder is None:
        cd = os.getcwd()
        output_folder = f"{cd}/{dataset_name}/"
    if not pathlib.Path(output_folder).exists():
        pathlib.Path(output_folder).mkdir(parents=True, exist_ok=True)

    # Create JSON file name
    json_name = f"point_annotation_ng_link_{spot_id if spot_id is not None else 'spot'}.json"

    # Generate the Neuroglancer state
    neuroglancer_link = NgState(
        input_config,
        "s3",
        bucket_name,
        output_folder,
        dataset_name=pathlib.Path(output_folder).stem,
        base_url="https://neuroglancer-demo.appspot.com",
        json_name=json_name,
    )

    state_dict = neuroglancer_link.state
    # add crossSectionScale to state_dict
    # append annotation_layer to state_dict["layers"]
    # annotation_layer["source"]["transform"] = state_dict["dimensions"] # THIS BRINGS METERS IN

    state_dict["layers"].append(annotation_layer)
    state_dict["crossSectionScale"] = cross_section_scale
    state_dict["position"] = position

    direct_url = create_direct_neuroglancer_url(state_dict)

    return direct_url


def create_link_from_json(
    ng_json_path,
    position,
    spot_id,
    point_annotation,
    annotation_color="#FF0000",
    spacing=3.0,
    cross_section_scale=None,
    base_url="https://neuroglancer-demo.appspot.com",
    hide_existing_annotations=True,
):
    """
    Create a Neuroglancer link from an existing JSON file with updated position and annotation.

    Parameters:
    -----------
    ng_json_path (str or Path): Path to the neuroglancer JSON file (can be local or S3 path)
    position (list): New position coordinates [x, y, z, t]
    spot_id (int or str): ID for the spot annotation
    point_annotation (list): Point annotation coordinates [x, y, z, ...]
    annotation_color (str, optional): Hex color for the annotation. Default: "#FFFF00"
    spacing (float, optional): Spacing for annotations in cross-section view. Default: 3.0
    cross_section_scale (float, optional): Scale for cross-section view. If None, keeps existing value
    base_url (str, optional): Base Neuroglancer URL. Default: "https://neuroglancer-demo.appspot.com"

    hide_existing_annotations (bool, optional): When True, sets existing annotation
        layers to invisible before adding the new spot annotation. Default: True

    Returns:
    --------
    str: Direct Neuroglancer URL with updated state
    """
    import json
    from pathlib import Path

    # Robust handling of S3 vs local paths: avoid Path() on s3:// to prevent scheme collapse
    is_s3 = isinstance(ng_json_path, str) and ng_json_path.startswith("s3://")
    json_path_str = ng_json_path if is_s3 else str(Path(ng_json_path))

    try:
        if is_s3:
            s3_path = json_path_str[5:]  # strip 's3://'
            parts = s3_path.split("/")
            bucket = parts[0]
            key = "/".join(parts[1:])
            print(f"[ng_utils] Fetching Neuroglancer JSON from S3: bucket={bucket} key={key}")
            s3_client = boto3.client("s3")
            response = s3_client.get_object(Bucket=bucket, Key=key)
            json_content = response["Body"].read().decode("utf-8")
            state_dict = json.loads(json_content)
            print(f"Loaded Neuroglancer state from S3: s3://{bucket}/{key}")
        else:
            print(f"[ng_utils] Loading Neuroglancer JSON from local path: {json_path_str}")
            with open(json_path_str, "r") as f:
                state_dict = json.load(f)
            print(f"Loaded Neuroglancer state from local file: {json_path_str}")
    except FileNotFoundError:
        raise FileNotFoundError(f"Neuroglancer JSON file not found: {json_path_str}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in file {json_path_str}: {e}")
    except Exception as e:
        raise Exception(f"Error loading Neuroglancer JSON from {json_path_str}: {e}")

    # Update position
    state_dict["position"] = position
    print(f"Updated position to: {position}")

    # Update cross-section scale if provided
    if cross_section_scale is not None:
        state_dict["crossSectionScale"] = cross_section_scale
        print(f"Updated crossSectionScale to: {cross_section_scale}")

    # Hide existing annotation layers if requested
    if hide_existing_annotations and "layers" in state_dict:
        hidden_layers = 0
        for layer in state_dict["layers"]:
            if layer.get("type") == "annotation":
                layer["visible"] = False
                hidden_layers += 1
        if hidden_layers:
            print(f"Hid {hidden_layers} existing annotation layer(s) before adding spot {spot_id}")

    # Ensure layers list exists and append fresh annotation layer for the selected spot
    if "layers" not in state_dict or not isinstance(state_dict["layers"], list):
        state_dict["layers"] = []

    spot_layer_name = f"Spot {spot_id}"

    # Remove any prior custom layer for this spot to avoid duplication
    state_dict["layers"] = [
        layer
        for layer in state_dict["layers"]
        if not (
            layer.get("type") == "annotation"
            and layer.get("name") == spot_layer_name
            and layer.get("tab") == "annotations"
        )
    ]

    annotation_layer = {
        "type": "annotation",
        "name": spot_layer_name,
        "tab": "annotations",
        "visible": True,
        "annotationColor": annotation_color,
        "crossSectionAnnotationSpacing": spacing,
        "projectionAnnotationSpacing": 10,
        "tool": "annotatePoint",
        "annotations": [
            {
                "type": "point",
                "id": str(spot_id),
                "point": point_annotation,
            }
        ],
    }
    state_dict["layers"].append(annotation_layer)
    print(f"Appended new annotation layer with spot {spot_id}")

    # Generate direct URL
    direct_url = create_direct_neuroglancer_url(state_dict, base_url=base_url)

    return direct_url


def read_zarr_resolution_boto(s3_path):
    """
    Read resolution from zarr using direct S3 access via boto3
    found s3fs/zarr was not working, so using boto3 (MD)

    Parameters:
    s3_path (str): S3 path to the zarr dataset

    Returns:
    list: Resolution in z,y,x order in micrometers
    """
    import json

    # Parse the S3 path
    if s3_path.startswith("s3://"):
        s3_path = s3_path[5:]  # Remove 's3://'

    parts = s3_path.split("/")
    bucket = parts[0]
    prefix = "/".join(parts[1:])

    # Create boto3 client
    s3_client = boto3.client("s3")

    try:
        # Try to get the .zattrs file which should contain resolution metadata
        zattrs_key = f"{prefix}/.zattrs"
        print(f"Reading {zattrs_key} from bucket {bucket}")
        response = s3_client.get_object(Bucket=bucket, Key=zattrs_key)
        zattrs_content = response["Body"].read().decode("utf-8")
        zattrs = json.loads(zattrs_content)

        # Look for resolution in multiscales metadata
        if "multiscales" in zattrs and zattrs["multiscales"]:
            multiscale = zattrs["multiscales"][0]

            if "axes" in multiscale:
                axes = multiscale["axes"]
                axes_map = {axis["name"]: i for i, axis in enumerate(axes)}

                z_idx = axes_map.get("z")
                y_idx = axes_map.get("y")
                x_idx = axes_map.get("x")

                if "datasets" in multiscale and multiscale["datasets"]:
                    dataset = multiscale["datasets"][0]
                    if "coordinateTransformations" in dataset:
                        for transform in dataset["coordinateTransformations"]:
                            if transform.get("type") == "scale":
                                scale = transform["scale"]

                                if all(
                                    idx is not None
                                    for idx in [z_idx, y_idx, x_idx]
                                ):
                                    print(
                                        (
                                            f"Found resolution from multiscales: "
                                            f"{[scale[z_idx], scale[y_idx], scale[x_idx]]}"
                                        )
                                    )
                                    return [
                                        scale[z_idx],
                                        scale[y_idx],
                                        scale[x_idx],
                                    ]

        # Check for direct resolution attribute
        if "resolution" in zattrs:
            print(f"Found direct resolution attribute: {zattrs['resolution']}")
            return list(zattrs["resolution"])

    except Exception as e:
        print(f"Error reading .zattrs: {str(e)}")

    print(f"Using default resolution for {s3_path}")
    return [1.0, 1.0, 1.0]


def wavelength_to_hex_pure_colours(wavelength: int) -> int:
    """
    Converts wavelength to corresponding color hex value.
    Parameters
    ------------------------
    wavelength: int
        Integer value representing wavelength.
    Returns
    ------------------------
    int:
        Hex value color.
    """

    # Each wavelength key is the upper bound to a wavelgnth band.
    # Wavelengths range from 380-750nm.
    # Color map wavelength/hex pairs are generated
    # by sampling along a CIE diagram arc.
    color_map = {
        0: 0xFFFFFF,  # white
        1: 0x00FF00,  # Blue
        2: 0xFF0000,  # Red
        3: 0x0000FF,  # Blue
        4: 0x00FFFF,  # cyan
        5: 0xFF00FF,  # magenta   #638
        # 420: 0xFFFFFF, #white       #405
        # 490: 0x5DF8D6,  # Green     #488
        # 520: 0x4B90FE,  # Blue      #515
        # 570: 0xE9EC02,  # Yellow    #561
        # 600: 0xF00050,  # Pink      #594
        # 650: 0xF0121E,  # Red       #638
        420: 0xFFFFFF,  # white       #405
        490: 0x00FF00,  # Green     #488
        520: 0xFF0000,  # Red       #515
        570: 0x0000FF,  # Blue      #561
        600: 0x00FFFF,  # cyan       #594 #600: 0xFFF000,  # Orange    #594 #or should be cyan?
        650: 0xFF00FF,  # magenta   #638
    }
    for ub, hex_val in color_map.items():
        if wavelength < ub:  # Exclusive
            return hex_val
    return hex_val  # hex_val is set to the last color in for loop


def create_link_with_multiple_annotations(
    fused_s3_paths,
    annotations,
    position=None,
    layer_name="SeeSpot",
    annotation_color="#00FF00",
    spacing=3.0,
    cross_section_scale=1.0,
    resolution_zyx=None,
    max_dr=1200,
    opacity=1.0,
    blend="additive",
    output_folder=None,
):
    """
    Create a Neuroglancer link with multiple point annotations.

    Parameters:
    -----------
    fused_s3_paths (dict or list): Dictionary mapping channel names to S3 paths, or list of S3 paths
    annotations (list): List of annotation dicts, each containing:
        - spot_id: Unique identifier for the spot
        - point: Coordinates [x, y, z, t, ...] for the annotation
    position (list, optional): Initial position to view [x, y, z, t]. If None, uses first annotation
    layer_name (str): Name for the annotation layer. Default: "SeeSpot"
    annotation_color (str): Hex color for annotations. Default: "#00FF00" (green)
    spacing (float): Spacing for annotations in cross-section view. Default: 3.0
    cross_section_scale (float): Scale for cross-section view. Default: 1.0
    resolution_zyx (list, optional): Resolution in z,y,x order. If None, reads from zarr
    max_dr (int): Maximum dynamic range for shader controls. Default: 1200
    opacity (float): Opacity value for the layer. Default: 1.0
    blend (str): Blending mode for the layer. Default: "additive"
    output_folder (str, optional): Output folder path

    Returns:
    --------
    str: Direct Neuroglancer URL with multiple annotations
    """
    # Convert fused_s3_paths to list if it's a dict
    if isinstance(fused_s3_paths, dict):
        fused_s3_path = list(fused_s3_paths.values())
    elif isinstance(fused_s3_paths, str):
        fused_s3_path = [fused_s3_paths]
    else:
        fused_s3_path = fused_s3_paths

    # If resolution not provided, try to read from first zarr file
    if resolution_zyx is None:
        try:
            resolution_zyx = read_zarr_resolution_boto(fused_s3_path[0])
            print(f"Found resolution from zarr: {resolution_zyx}")
        except Exception as e:
            print(
                f"Warning: Could not read resolution from zarr file: {str(e)}"
            )
            # Provide a default resolution if we can't read it
            resolution_zyx = [1.0, 1.0, 1.0]
            print(f"Using default resolution: {resolution_zyx}")

    output_dimensions = {
        "x": {"voxel_size": resolution_zyx[2], "unit": "microns"},
        "y": {"voxel_size": resolution_zyx[1], "unit": "microns"},
        "z": {"voxel_size": resolution_zyx[0], "unit": "microns"},
        "c'": {"voxel_size": 1, "unit": ""},
        "t": {"voxel_size": 0.001, "unit": "seconds"},
    }

    # Initialize layers list
    layers = []

    # Process each fused path to create image layers
    for idx, fused_path in enumerate(fused_s3_path):
        # Extract channel number from fused path
        pattern = r"(ch|CH|channel)_(\d+)"
        match = re.search(pattern, fused_path)
        if not match:
            raise ValueError(
                f"Could not extract channel number from path: {fused_path}"
            )

        channel = int(match.group(2))
        hex_val = wavelength_to_hex_pure_colours(channel)
        hex_str = f"#{hex_val:06x}"

        # Add image layer
        image_layer = {
            "type": "image",
            "source": fused_path,
            "channel": 0,
            "shaderControls": {"normalized": {"range": [90, max_dr]}},
            "shader": {
                "color": hex_str,
                "emitter": "RGB",
                "vec": "vec3",
            },
            "localPosition": [0.5],
            "visible": True,
            "opacity": opacity,
            "name": f"CH_{channel}",
            "blend": blend,
        }
        layers.append(image_layer)

    # Create annotation layer with multiple points
    annotation_layer = {
        "type": "annotation",
        "name": layer_name,
        "tab": "annotations",
        "visible": True,
        "annotationColor": annotation_color,
        "crossSectionAnnotationSpacing": spacing,
        "projectionAnnotationSpacing": 10,
        "tool": "annotatePoint",
        "annotations": []
    }

    # Add all annotations to the layer
    for annot in annotations:
        annotation = {
            "type": "point",
            "id": str(annot["spot_id"]),
            "point": annot["point"],
        }
        annotation_layer["annotations"].append(annotation)

    print(f"Created annotation layer '{layer_name}' with {len(annotations)} points")

    # Use the first annotation's coordinates as the position if no position is specified
    if position is None and len(annotations) > 0:
        first_point = annotations[0]["point"]
        position = first_point[:4] if len(first_point) >= 4 else first_point + [0] * (4 - len(first_point))

    # Set input config with dimensions from resolution_zyx
    input_config = {
        "dimensions": output_dimensions,
        "layers": layers,
        "showScaleBar": False,
        "showAxisLines": False,
    }

    # Extract bucket and dataset from first fused path
    parts = fused_s3_path[0].split("/")
    bucket_name = parts[2]
    dataset_name = parts[3]

    # Set up output folder
    if output_folder is None:
        cd = os.getcwd()
        output_folder = f"{cd}/{dataset_name}/"
    if not pathlib.Path(output_folder).exists():
        pathlib.Path(output_folder).mkdir(parents=True, exist_ok=True)

    # Create JSON file name
    json_name = f"multi_annotation_ng_link_{len(annotations)}_spots.json"

    # Generate the Neuroglancer state
    neuroglancer_link = NgState(
        input_config,
        "s3",
        bucket_name,
        output_folder,
        dataset_name=pathlib.Path(output_folder).stem,
        base_url="https://neuroglancer-demo.appspot.com",
        json_name=json_name,
    )

    state_dict = neuroglancer_link.state

    # Add annotation layer and other state properties
    state_dict["layers"].append(annotation_layer)
    state_dict["crossSectionScale"] = cross_section_scale
    state_dict["position"] = position

    direct_url = create_direct_neuroglancer_url(state_dict)

    return direct_url
