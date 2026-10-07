"""Coordinate normalization utilities.

All bot brain modules operate strictly on normalized coordinates:
    (nx, ny) where 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0
and normalized regions:
    (nx, ny, nw, nh) where all components are in [0.0, 1.0].

This module converts between physical device pixels (e.g. 1920x1080)
and normalized screen coordinates.
"""

from __future__ import annotations

from typing import Tuple
import numpy as np


def to_device_px(nx: float, ny: float, width: int, height: int) -> Tuple[int, int]:
    """Convert normalized (nx, ny) to device pixel coordinates (x, y).

    Parameters
    ----------
    nx : float
        Normalized X coordinate in range [0.0, 1.0].
    ny : float
        Normalized Y coordinate in range [0.0, 1.0].
    width : int
        Active viewport or screen width in pixels.
    height : int
        Active viewport or screen height in pixels.

    Returns
    -------
    Tuple[int, int]
        (x, y) coordinates clamped within [0, width] and [0, height].
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid viewport dimensions: width={width}, height={height}")

    clamped_x = max(0.0, min(1.0, float(nx)))
    clamped_y = max(0.0, min(1.0, float(ny)))

    x = int(round(clamped_x * (width - 1)))
    y = int(round(clamped_y * (height - 1)))
    return x, y


def to_normalized(x: int | float, y: int | float, width: int, height: int) -> Tuple[float, float]:
    """Convert device pixel coordinates (x, y) to normalized (nx, ny).

    Parameters
    ----------
    x : int or float
        Device pixel X coordinate.
    y : int or float
        Device pixel Y coordinate.
    width : int
        Active viewport or screen width in pixels.
    height : int
        Active viewport or screen height in pixels.

    Returns
    -------
    Tuple[float, float]
        (nx, ny) in range [0.0, 1.0].
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid viewport dimensions: width={width}, height={height}")

    nx = max(0.0, min(1.0, float(x) / float(width)))
    ny = max(0.0, min(1.0, float(y) / float(height)))
    return nx, ny


def box_to_device_px(
    region: Tuple[float, float, float, float],
    width: int,
    height: int,
) -> Tuple[int, int, int, int]:
    """Convert normalized bounding box (nx, ny, nw, nh) to device pixels (x, y, w, h).

    Parameters
    ----------
    region : Tuple[float, float, float, float]
        Normalized (nx, ny, nw, nh).
    width : int
        Active viewport width.
    height : int
        Active viewport height.

    Returns
    -------
    Tuple[int, int, int, int]
        Pixel (x, y, w, h) clamped within device bounds.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid viewport dimensions: width={width}, height={height}")

    nx, ny, nw, nh = region
    x = int(round(max(0.0, min(1.0, nx)) * width))
    y = int(round(max(0.0, min(1.0, ny)) * height))
    w = int(round(max(0.0, min(1.0, nw)) * width))
    h = int(round(max(0.0, min(1.0, nh)) * height))

    # Clamp to avoid exceeding device bounds
    x = min(x, width - 1)
    y = min(y, height - 1)
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))

    return x, y, w, h


def box_px_to_normalized(
    box: Tuple[int, int, int, int],
    width: int,
    height: int,
    is_xywh: bool = True,
) -> Tuple[float, float, float, float]:
    """Convert pixel bounding box to normalized (nx, ny, nw, nh).

    Parameters
    ----------
    box : Tuple[int, int, int, int]
        Either (x, y, w, h) if is_xywh is True, or (x1, y1, x2, y2) if False.
    width : int
        Device width in pixels (e.g. 1920).
    height : int
        Device height in pixels (e.g. 1080).
    is_xywh : bool, optional
        Format flag, default True.

    Returns
    -------
    Tuple[float, float, float, float]
        Normalized (nx, ny, nw, nh) rounded to 4 decimal places.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid viewport dimensions: width={width}, height={height}")

    if is_xywh:
        x, y, w, h = box
    else:
        x1, y1, x2, y2 = box
        x = min(x1, x2)
        y = min(y1, y2)
        w = abs(x2 - x1)
        h = abs(y2 - y1)

    nx = round(max(0.0, min(1.0, x / width)), 4)
    ny = round(max(0.0, min(1.0, y / height)), 4)
    nw = round(max(0.0, min(1.0, w / width)), 4)
    nh = round(max(0.0, min(1.0, h / height)), 4)

    return nx, ny, nw, nh


def crop_normalized(
    image: np.ndarray,
    region: Tuple[float, float, float, float],
) -> np.ndarray:
    """Crop an image array using normalized coordinates (nx, ny, nw, nh).

    Parameters
    ----------
    image : np.ndarray
        Source image array of shape (H, W) or (H, W, C).
    region : Tuple[float, float, float, float]
        Normalized region (nx, ny, nw, nh).

    Returns
    -------
    np.ndarray
        Cropped sub-array.
    """
    h_img, w_img = image.shape[:2]
    if h_img == 0 or w_img == 0:
        return image

    x, y, w, h = box_to_device_px(region, w_img, h_img)
    return image[y : y + h, x : x + w].copy()
