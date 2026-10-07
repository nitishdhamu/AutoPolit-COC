"""
OpenCV template-matching engine.

Provides lazy-loaded template caching, single and multi-match detection,
non-maximum suppression, and a blocking ``wait_for`` poller.  All matching
is performed in grayscale using ``cv2.TM_CCOEFF_NORMED``.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Optional

import cv2
import numpy as np

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


class VisionEngine:
    """Template-based computer vision engine for game-screen analysis.

    Parameters
    ----------
    template_dir : str
        Root directory containing template images, organised in
        subdirectories (``buttons/``, ``icons/``, ``screens/``, etc.).
    """

    # Non-maximum suppression overlap threshold
    _NMS_OVERLAP_THRESH: float = 0.40

    def __init__(self, template_dir: str) -> None:
        """Store template directory and initialise empty cache."""
        self._template_dir: str = template_dir
        self._cache: dict[str, np.ndarray] = {}
        self._missing: set[str] = set()
        logger.info("VisionEngine initialised — template_dir=%s", template_dir)

    # ------------------------------------------------------------------
    # Template loading
    # ------------------------------------------------------------------

    def _load_template(self, name: str) -> np.ndarray:
        """Lazy-load and cache a template image by *name*.

        Searches the template directory and its subdirectories for a file
        matching *name* (with or without ``.png`` extension).

        Parameters
        ----------
        name : str
            Template name, e.g. ``"attack_btn"`` or ``"attack_btn.png"``.

        Returns
        -------
        np.ndarray
            Grayscale template image.

        Raises
        ------
        FileNotFoundError
            If the template image cannot be found.
        """
        if name in self._cache:
            return self._cache[name]

        # Normalise: add .png extension if missing
        filename = name if name.endswith(".png") else f"{name}.png"

        # Search root and all subdirectories
        for dirpath, _dirs, files in os.walk(self._template_dir):
            if filename in files:
                path = os.path.join(dirpath, filename)
                img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                if img is None:
                    raise FileNotFoundError(
                        f"Template file exists but could not be read: {path}"
                    )
                self._cache[name] = img
                logger.debug("Loaded template '%s' from %s", name, path)
                return img

        raise FileNotFoundError(
            f"Template '{name}' ({filename}) not found in {self._template_dir}"
        )

    # ------------------------------------------------------------------
    # Public API — single match
    # ------------------------------------------------------------------

    def find(
        self,
        screenshot: np.ndarray,
        template_name: str,
        threshold: float = 0.8,
    ) -> Optional[tuple[int, int, int, int]]:
        """Find the single best match for *template_name* in *screenshot*.

        Both images are converted to grayscale before matching.

        Parameters
        ----------
        screenshot : np.ndarray
            Full or partial BGR screenshot.
        template_name : str
            Name of the template to search for.
        threshold : float
            Minimum confidence (0–1).  Matches below this are ignored.

        Returns
        -------
        tuple[int, int, int, int, float] or None
            ``(left, top, width, height)`` of the best match, or ``None`` if
            no match exceeds *threshold*.  This deliberately matches the
            coordinate convention used throughout the game modules.
        """
        try:
            template = self._load_template(template_name)
        except FileNotFoundError:
            if template_name not in self._missing:
                logger.warning("Template '%s' not found — returning None", template_name)
                self._missing.add(template_name)
            else:
                logger.debug("Template '%s' remains unavailable", template_name)
            return None

        gray = self._to_gray(screenshot)
        th, tw = template.shape[:2]

        # Guard: template must be smaller than the screenshot
        if th > gray.shape[0] or tw > gray.shape[1]:
            logger.warning(
                "Template '%s' (%dx%d) larger than screenshot (%dx%d)",
                template_name,
                tw,
                th,
                gray.shape[1],
                gray.shape[0],
            )
            return None

        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val >= threshold:
            x, y = max_loc
            logger.debug(
                "Found '%s' at (%d,%d) %dx%d conf=%.3f",
                template_name,
                x,
                y,
                tw,
                th,
                max_val,
            )
            return (int(x), int(y), int(tw), int(th))

        logger.debug(
            "Template '%s' not found (best=%.3f < threshold=%.3f)",
            template_name,
            max_val,
            threshold,
        )
        return None

    # ------------------------------------------------------------------
    # Public API — multiple matches
    # ------------------------------------------------------------------

    def find_all(
        self,
        screenshot: np.ndarray,
        template_name: str,
        threshold: float = 0.8,
    ) -> list[tuple[int, int, int, int]]:
        """Find **all** matches of *template_name* above *threshold*.

        Applies non-maximum suppression to eliminate overlapping detections.

        Returns
        -------
        list[tuple[int, int, int, int, float]]
            Each entry is ``(left, top, width, height)``.
        """
        try:
            template = self._load_template(template_name)
        except FileNotFoundError:
            if template_name not in self._missing:
                logger.warning("Template '%s' not found — returning []", template_name)
                self._missing.add(template_name)
            else:
                logger.debug("Template '%s' remains unavailable", template_name)
            return []

        gray = self._to_gray(screenshot)
        th, tw = template.shape[:2]

        if th > gray.shape[0] or tw > gray.shape[1]:
            logger.warning(
                "Template '%s' (%dx%d) larger than screenshot (%dx%d)",
                template_name,
                tw,
                th,
                gray.shape[1],
                gray.shape[0],
            )
            return []

        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)

        if len(locations[0]) == 0:
            logger.debug("No matches for '%s' above %.2f", template_name, threshold)
            return []

        # Build box list for NMS: (x1, y1, x2, y2, confidence)
        boxes: list[tuple[int, int, int, int, float]] = []
        for pt_y, pt_x in zip(locations[0], locations[1]):
            conf = float(result[pt_y, pt_x])
            boxes.append((int(pt_x), int(pt_y), int(pt_x + tw), int(pt_y + th), conf))

        filtered = self._nms(boxes)

        matches: list[tuple[int, int, int, int]] = []
        for x1, y1, x2, y2, conf in filtered:
            matches.append((x1, y1, tw, th))

        logger.debug(
            "Found %d match(es) for '%s' (pre-NMS: %d)",
            len(matches),
            template_name,
            len(boxes),
        )
        return matches

    # ------------------------------------------------------------------
    # Public API — polling wait
    # ------------------------------------------------------------------

    def wait_for(
        self,
        template_name: str,
        screen: ScreenCapture,
        timeout: float = 30.0,
        interval: float = 0.5,
        threshold: float = 0.8,
    ) -> Optional[tuple[int, int, int, int]]:
        """Poll the screen until *template_name* appears or *timeout* elapses.

        Parameters
        ----------
        template_name : str
            Template to wait for.
        screen : ScreenCapture
            Screen-capture instance used for periodic screenshots.
        timeout : float
            Maximum seconds to wait.
        interval : float
            Seconds between capture attempts.
        threshold : float
            Minimum match confidence.

        Returns
        -------
        tuple or None
            Match tuple on success, ``None`` on timeout.
        """
        deadline = time.monotonic() + timeout
        logger.info(
            "Waiting for '%s' (timeout=%.1fs, interval=%.1fs)",
            template_name,
            timeout,
            interval,
        )

        while time.monotonic() < deadline:
            try:
                shot = screen.capture_fullscreen()
                match = self.find(shot, template_name, threshold)
                if match is not None:
                    logger.info(
                        "Template '%s' appeared after %.1fs",
                        template_name,
                        timeout - (deadline - time.monotonic()),
                    )
                    return match
            except Exception as e:
                logger.warning("Capture/match error while waiting: %s", e)
            time.sleep(interval)

        logger.warning("Timed out waiting for '%s' after %.1fs", template_name, timeout)
        return None

    # ------------------------------------------------------------------
    # Public API — convenience helpers
    # ------------------------------------------------------------------

    def is_visible(
        self,
        screenshot: np.ndarray,
        template_name: str,
        threshold: float = 0.8,
    ) -> bool:
        """Quick boolean check — is *template_name* visible in *screenshot*?

        Returns
        -------
        bool
            ``True`` if the template matches above *threshold*.
        """
        return self.find(screenshot, template_name, threshold) is not None

    def find_best_match(
        self,
        screenshot: np.ndarray,
        template_names: list[str],
        threshold: float = 0.8,
    ) -> Optional[tuple[str, int, int, int, int]]:
        """Try multiple templates and return the one with the highest confidence.

        Useful for screen identification — pass a list of screen-specific
        templates and see which one matches best.

        Parameters
        ----------
        screenshot : np.ndarray
            BGR screenshot.
        template_names : list[str]
            Template names to try.
        threshold : float
            Minimum confidence.

        Returns
        -------
        tuple[str, int, int, int, int, float] or None
            ``(template_name, left, top, w, h)`` for the first matching
            template, or ``None`` if nothing matched.  ``find`` intentionally
            does not expose confidence as part of its public click contract.
        """
        best: Optional[tuple[str, int, int, int, int]] = None

        for name in template_names:
            match = self.find(screenshot, name, threshold)
            if match is not None:
                x, y, w, h = match
                best = (name, x, y, w, h)
                break

        if best is not None:
            logger.debug(
                "Found a matching template among %d candidates: '%s'",
                len(template_names),
                best[0],
            )
        else:
            logger.debug(
                "No template matched from %d candidates", len(template_names)
            )
        return best

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_gray(image: np.ndarray) -> np.ndarray:
        """Convert an image to grayscale if it isn't already."""
        if len(image.shape) == 2:
            return image
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    @classmethod
    def _nms(
        cls,
        boxes: list[tuple[int, int, int, int, float]],
    ) -> list[tuple[int, int, int, int, float]]:
        """Greedy non-maximum suppression on ``(x1, y1, x2, y2, conf)`` boxes.

        Eliminates overlapping detections, keeping the highest-confidence
        box in each cluster.
        """
        if not boxes:
            return []

        # Sort by confidence descending
        sorted_boxes = sorted(boxes, key=lambda b: b[4], reverse=True)
        keep: list[tuple[int, int, int, int, float]] = []

        while sorted_boxes:
            current = sorted_boxes.pop(0)
            keep.append(current)

            remaining: list[tuple[int, int, int, int, float]] = []
            for box in sorted_boxes:
                if cls._iou(current, box) < cls._NMS_OVERLAP_THRESH:
                    remaining.append(box)
            sorted_boxes = remaining

        return keep

    @staticmethod
    def _iou(
        a: tuple[int, int, int, int, float],
        b: tuple[int, int, int, int, float],
    ) -> float:
        """Compute Intersection-over-Union between two boxes."""
        x1 = max(a[0], b[0])
        y1 = max(a[1], b[1])
        x2 = min(a[2], b[2])
        y2 = min(a[3], b[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        area_a = (a[2] - a[0]) * (a[3] - a[1])
        area_b = (b[2] - b[0]) * (b[3] - b[1])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0
