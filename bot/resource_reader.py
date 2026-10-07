"""
Resource Reader — HUD Resource Monitoring
===========================================
Reads resource amounts (Gold, Elixir, Dark Elixir, Gems) from the
in-game HUD overlay. Also reads builder counts, scout screen loot,
and battle results.

The resource bar is at the TOP-RIGHT of the 1920x1080 screen with
resources stacked vertically: Gold → Elixir → Dark Elixir → Gems.

Resource regions are defined as class constants and can be overridden
via config for different screen layouts.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import numpy as np

from backends.base import Backend
from backends.coordinates import crop_normalized
from bot.ocr import GameOCR

logger = logging.getLogger(__name__)


class ResourceReader:
    """Reads resource amounts and builder counts from the game HUD.

    Uses OCR to read numeric values from specific screen regions.
    Regions are defined as normalized coordinates (0.0 - 1.0).

    Attributes:
        config: Bot configuration dictionary.
        backend: Backend instance.
        ocr: OCR engine for reading numbers.
    """

    # ── Normalized resource bar regions (nx, ny, nw, nh) ───────────────
    GOLD_REGION_NORM: tuple[float, float, float, float] = (0.8854, 0.0231, 0.1042, 0.0278)
    ELIXIR_REGION_NORM: tuple[float, float, float, float] = (0.8854, 0.0509, 0.1042, 0.0278)
    DARK_ELIXIR_REGION_NORM: tuple[float, float, float, float] = (0.8854, 0.0787, 0.1042, 0.0278)
    GEM_REGION_NORM: tuple[float, float, float, float] = (0.9271, 0.1065, 0.0729, 0.0278)

    # Legacy pixel regions at 1920x1080
    GOLD_REGION: tuple[int, int, int, int] = (1700, 25, 200, 30)
    ELIXIR_REGION: tuple[int, int, int, int] = (1700, 55, 200, 30)
    DARK_ELIXIR_REGION: tuple[int, int, int, int] = (1700, 85, 200, 30)
    GEM_REGION: tuple[int, int, int, int] = (1780, 115, 140, 30)

    # ── Builder count region (near top-center) ──────────────────────────
    BUILDER_COUNT_REGION_NORM: tuple[float, float, float, float] = (0.1406, 0.0139, 0.0365, 0.0278)
    BUILDER_COUNT_REGION: tuple[int, int, int, int] = (270, 15, 70, 30)

    # ── Scout screen loot regions (top-left during attack scouting) ─────
    SCOUT_GOLD_REGION_NORM: tuple[float, float, float, float] = (0.0521, 0.0926, 0.0833, 0.0278)
    SCOUT_ELIXIR_REGION_NORM: tuple[float, float, float, float] = (0.0521, 0.1204, 0.0833, 0.0278)
    SCOUT_DARK_ELIXIR_REGION_NORM: tuple[float, float, float, float] = (0.0521, 0.1481, 0.0833, 0.0278)
    SCOUT_GOLD_REGION: tuple[int, int, int, int] = (100, 100, 160, 30)
    SCOUT_ELIXIR_REGION: tuple[int, int, int, int] = (100, 130, 160, 30)
    SCOUT_DARK_ELIXIR_REGION: tuple[int, int, int, int] = (100, 160, 160, 30)

    # ── Battle results regions ("You got:") ─────────────────────────────
    RESULT_GOLD_REGION_NORM: tuple[float, float, float, float] = (0.3906, 0.3704, 0.1042, 0.0324)
    RESULT_ELIXIR_REGION_NORM: tuple[float, float, float, float] = (0.3906, 0.4074, 0.1042, 0.0324)
    RESULT_DARK_ELIXIR_REGION_NORM: tuple[float, float, float, float] = (0.3906, 0.4444, 0.1042, 0.0324)
    RESULT_GOLD_REGION: tuple[int, int, int, int] = (750, 400, 200, 35)
    RESULT_ELIXIR_REGION: tuple[int, int, int, int] = (750, 440, 200, 35)
    RESULT_DARK_ELIXIR_REGION: tuple[int, int, int, int] = (750, 480, 200, 35)


    # ── Max storage capacity lookup table ───────────────────────────────
    # {th_level: {"gold": max, "elixir": max, "dark_elixir": max}}
    STORAGE_CAPACITY: dict[int, dict[str, int]] = {
        1:  {"gold":   300_000, "elixir":   300_000, "dark_elixir":        0},
        2:  {"gold":   450_000, "elixir":   450_000, "dark_elixir":        0},
        3:  {"gold":   900_000, "elixir":   900_000, "dark_elixir":        0},
        4:  {"gold": 1_350_000, "elixir": 1_350_000, "dark_elixir":        0},
        5:  {"gold": 1_800_000, "elixir": 1_800_000, "dark_elixir":        0},
        6:  {"gold": 2_700_000, "elixir": 2_700_000, "dark_elixir":        0},
        7:  {"gold": 4_000_000, "elixir": 4_000_000, "dark_elixir":   75_000},
        8:  {"gold": 5_000_000, "elixir": 5_000_000, "dark_elixir":  100_000},
        9:  {"gold": 6_000_000, "elixir": 6_000_000, "dark_elixir":  150_000},
        10: {"gold": 8_000_000, "elixir": 8_000_000, "dark_elixir":  180_000},
        11: {"gold": 10_000_000, "elixir": 10_000_000, "dark_elixir": 200_000},
        12: {"gold": 12_000_000, "elixir": 12_000_000, "dark_elixir": 240_000},
        13: {"gold": 14_000_000, "elixir": 14_000_000, "dark_elixir": 280_000},
        14: {"gold": 16_000_000, "elixir": 16_000_000, "dark_elixir": 300_000},
        15: {"gold": 20_000_000, "elixir": 20_000_000, "dark_elixir": 350_000},
        16: {"gold": 23_500_000, "elixir": 23_500_000, "dark_elixir": 400_000},
    }

    # Maximum number of OCR retries for a single read
    MAX_READ_RETRIES: int = 3

    def __init__(
        self,
        config: dict,
        backend_or_screen: Any,
        ocr: Optional[GameOCR] = None,
        backend: Optional[Backend] = None,
    ) -> None:
        """Initialize the ResourceReader.

        Args:
            config: Bot configuration dictionary.
            backend_or_screen: Backend instance (or legacy ScreenCapture).
            ocr: OCR engine for reading numbers.
            backend: Explicit Backend instance.
        """
        self.config = config
        if backend is not None:
            self.backend: Optional[Backend] = backend
            self.screen: Any = backend_or_screen
        elif isinstance(backend_or_screen, Backend):
            self.backend = backend_or_screen
            self.screen = None
        else:
            self.backend = getattr(backend_or_screen, "backend", None)
            self.screen = backend_or_screen

        self.ocr = ocr or GameOCR(config)
        logger.info("ResourceReader initialized")

    def get_screenshot(self) -> np.ndarray:
        """Capture screenshot via backend or screen helper."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None and hasattr(self.screen, "capture_screenshot"):
            return self.screen.capture_screenshot()
        raise RuntimeError("ResourceReader has no backend or screen capture handle")


    # ── Primary read methods ────────────────────────────────────────────

    def read_all(self) -> dict[str, int | None]:
        """Read all resource values from the HUD.

        Captures a single screenshot and reads Gold, Elixir, Dark Elixir,
        and Gems in one pass.

        Returns:
            Dictionary with keys 'gold', 'elixir', 'dark_elixir', 'gems'.
            Values are integers or None if a read failed.
        """
        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                logger.error("ResourceReader: Failed to capture screenshot")
                return {
                    "gold": None,
                    "elixir": None,
                    "dark_elixir": None,
                    "gems": None,
                }

            result = {
                "gold": self._read_region(screenshot, self.GOLD_REGION_NORM, "gold"),
                "elixir": self._read_region(screenshot, self.ELIXIR_REGION_NORM, "elixir"),
                "dark_elixir": self._read_region(
                    screenshot, self.DARK_ELIXIR_REGION_NORM, "dark_elixir"
                ),
                "gems": self._read_region(screenshot, self.GEM_REGION_NORM, "gems"),
            }

            logger.info(
                "Resources: Gold=%s, Elixir=%s, DE=%s, Gems=%s",
                self._format_amount(result["gold"]),
                self._format_amount(result["elixir"]),
                self._format_amount(result["dark_elixir"]),
                self._format_amount(result["gems"]),
            )

            return result

        except Exception as e:
            logger.error("ResourceReader read_all failed: %s", e, exc_info=True)
            return {
                "gold": None,
                "elixir": None,
                "dark_elixir": None,
                "gems": None,
            }

    def read_gold(self) -> int | None:
        """Read the current gold amount from the HUD.

        Returns:
            Gold amount as integer, or None if read failed.
        """
        return self._read_single_resource(self.GOLD_REGION_NORM, "gold")

    def read_elixir(self) -> int | None:
        """Read the current elixir amount from the HUD.

        Returns:
            Elixir amount as integer, or None if read failed.
        """
        return self._read_single_resource(self.ELIXIR_REGION_NORM, "elixir")

    def read_dark_elixir(self) -> int | None:
        """Read the current dark elixir amount from the HUD.

        Returns:
            Dark elixir amount as integer, or None if read failed.
        """
        return self._read_single_resource(self.DARK_ELIXIR_REGION_NORM, "dark_elixir")

    def read_gems(self) -> int | None:
        """Read the current gem count from the HUD.

        Returns:
            Gem count as integer, or None if read failed.
        """
        return self._read_single_resource(self.GEM_REGION_NORM, "gems")

    # ── Builder count ───────────────────────────────────────────────────

    def read_builder_count(self) -> tuple[int, int] | None:
        """Read the builder count from the HUD (e.g., '2/5').

        The builder count is displayed near the top-center of the screen
        as "free/total" (e.g., "2/5" means 2 free out of 5 total).

        Returns:
            Tuple of (free_builders, total_builders), or None if read failed.
        """
        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                logger.error("ResourceReader: Failed to capture screenshot for builders")
                return None

            region = crop_normalized(screenshot, self.BUILDER_COUNT_REGION_NORM)
            if region is None or region.size == 0:
                return None

            # Read text — expecting "X/Y" format
            text = self.ocr.read_text(region)
            if text is None:
                logger.warning("ResourceReader: OCR returned None for builder count")
                return None

            # Parse "X/Y" format (handle OCR noise)
            text = text.strip()
            logger.debug("ResourceReader: Raw builder text = '%s'", text)

            # Try to find X/Y pattern
            match = re.search(r"(\d)\s*/\s*(\d)", text)
            if match:
                free = int(match.group(1))
                total = int(match.group(2))

                # Sanity check
                if 0 <= free <= total <= 7:
                    logger.info("Builders: %d free / %d total", free, total)
                    return (free, total)

                logger.warning(
                    "ResourceReader: Builder count out of range: %d/%d",
                    free,
                    total,
                )

            logger.warning(
                "ResourceReader: Could not parse builder count from '%s'", text
            )
            return None

        except Exception as e:
            logger.error(
                "ResourceReader read_builder_count failed: %s", e, exc_info=True
            )
            return None

    # ── Storage capacity ────────────────────────────────────────────────

    def get_storage_capacity(self, th_level: int) -> dict[str, int]:
        """Get the maximum storage capacity for a given Town Hall level.

        Args:
            th_level: Town Hall level (1–16).

        Returns:
            Dictionary with keys 'gold', 'elixir', 'dark_elixir' and
            their max capacities. Returns TH16 values for unknown levels.
        """
        if th_level in self.STORAGE_CAPACITY:
            capacity = self.STORAGE_CAPACITY[th_level]
            logger.debug("Storage capacity for TH%d: %s", th_level, capacity)
            return capacity

        logger.warning(
            "ResourceReader: Unknown TH level %d — using TH16 defaults",
            th_level,
        )
        return self.STORAGE_CAPACITY[16]

    def are_storages_full(
        self, th_level: int, threshold: float = 0.95
    ) -> bool:
        """Check if resource storages are full (above threshold).

        Compares current resources against the max capacity for the
        given TH level. All resource types (gold, elixir, dark_elixir)
        must be above the threshold for this to return True.

        Args:
            th_level: Town Hall level for capacity lookup.
            threshold: Fill ratio threshold (0.0–1.0). Default 0.95.

        Returns:
            True if all storages are above the threshold, False otherwise.
        """
        try:
            resources = self.read_all()
            capacity = self.get_storage_capacity(th_level)

            checks: list[tuple[str, int | None, int]] = [
                ("gold", resources.get("gold"), capacity["gold"]),
                ("elixir", resources.get("elixir"), capacity["elixir"]),
            ]

            # Only check DE if TH level supports it
            if capacity["dark_elixir"] > 0:
                checks.append(
                    ("dark_elixir", resources.get("dark_elixir"), capacity["dark_elixir"])
                )

            all_full = True
            for name, current, max_cap in checks:
                if current is None:
                    logger.warning(
                        "ResourceReader: Cannot check '%s' — read failed", name
                    )
                    all_full = False
                    continue

                ratio = current / max_cap if max_cap > 0 else 1.0
                is_full = ratio >= threshold
                logger.info(
                    "Storage check: %s = %s / %s (%.1f%%) — %s",
                    name,
                    self._format_amount(current),
                    self._format_amount(max_cap),
                    ratio * 100,
                    "FULL" if is_full else "not full",
                )
                if not is_full:
                    all_full = False

            return all_full

        except Exception as e:
            logger.error(
                "ResourceReader are_storages_full failed: %s", e, exc_info=True
            )
            return False

    # ── Scout and battle results ────────────────────────────────────────

    def read_loot_from_scout(self) -> dict[str, int | None]:
        """Read 'Available Loot' values from the attack scout screen.

        The loot display is in the top-left area of the scout screen,
        showing gold, elixir, and dark elixir stacked vertically.

        Returns:
            Dictionary with keys 'gold', 'elixir', 'dark_elixir'.
            Values are integers or None if read failed.
        """
        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                logger.error("ResourceReader: Failed to capture screenshot for scout loot")
                return {"gold": None, "elixir": None, "dark_elixir": None}

            result = {
                "gold": self._read_region(
                    screenshot, self.SCOUT_GOLD_REGION_NORM, "scout_gold"
                ),
                "elixir": self._read_region(
                    screenshot, self.SCOUT_ELIXIR_REGION_NORM, "scout_elixir"
                ),
                "dark_elixir": self._read_region(
                    screenshot, self.SCOUT_DARK_ELIXIR_REGION_NORM, "scout_de"
                ),
            }

            logger.info(
                "Scout loot: Gold=%s, Elixir=%s, DE=%s",
                self._format_amount(result["gold"]),
                self._format_amount(result["elixir"]),
                self._format_amount(result["dark_elixir"]),
            )

            return result

        except Exception as e:
            logger.error(
                "ResourceReader read_loot_from_scout failed: %s", e, exc_info=True
            )
            return {"gold": None, "elixir": None, "dark_elixir": None}

    def read_attack_results(self) -> dict[str, int | None]:
        """Read 'You got:' loot values from the battle results screen.

        Reads gold, elixir, and dark elixir gained from the post-battle
        results display.

        Returns:
            Dictionary with keys 'gold', 'elixir', 'dark_elixir'.
            Values are integers or None if read failed.
        """
        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                logger.error(
                    "ResourceReader: Failed to capture screenshot for attack results"
                )
                return {"gold": None, "elixir": None, "dark_elixir": None}

            result = {
                "gold": self._read_region(
                    screenshot, self.RESULT_GOLD_REGION_NORM, "result_gold"
                ),
                "elixir": self._read_region(
                    screenshot, self.RESULT_ELIXIR_REGION_NORM, "result_elixir"
                ),
                "dark_elixir": self._read_region(
                    screenshot, self.RESULT_DARK_ELIXIR_REGION_NORM, "result_de"
                ),
            }

            logger.info(
                "Attack results: Gold=%s, Elixir=%s, DE=%s",
                self._format_amount(result["gold"]),
                self._format_amount(result["elixir"]),
                self._format_amount(result["dark_elixir"]),
            )

            return result

        except Exception as e:
            logger.error(
                "ResourceReader read_attack_results failed: %s", e, exc_info=True
            )
            return {"gold": None, "elixir": None, "dark_elixir": None}

    # ── Internal helpers ────────────────────────────────────────────────

    def _read_single_resource(
        self,
        region: tuple[int, int, int, int] | tuple[float, float, float, float],
        label: str,
    ) -> int | None:
        """Read a single resource value by capturing fresh screenshot.

        Args:
            region: Screen region (normalized or pixel) to read from.
            label: Label for logging purposes.

        Returns:
            Resource amount or None if read failed.
        """
        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                logger.error("ResourceReader: Cannot capture for %s", label)
                return None
            return self._read_region(screenshot, region, label)
        except Exception as e:
            logger.error(
                "ResourceReader _read_single_resource(%s) failed: %s",
                label,
                e,
                exc_info=True,
            )
            return None

    def _read_region(
        self,
        screenshot: np.ndarray,
        region: tuple[int, int, int, int] | tuple[float, float, float, float],
        label: str,
    ) -> int | None:
        """Read a numeric value from a screen region using OCR.

        Crops the region from the screenshot and runs OCR with retries.
        Handles common OCR artifacts like 'O' instead of '0'.

        Args:
            screenshot: Full screenshot as numpy array.
            region: Normalized (nx, ny, nw, nh) or pixel (x, y, w, h).
            label: Label for logging purposes.

        Returns:
            Parsed integer value or None if all retries failed.
        """
        if len(region) == 4 and region[0] <= 1.0 and region[2] <= 1.0:
            cropped = crop_normalized(screenshot, region)
        else:
            x, y, w, h = int(region[0]), int(region[1]), int(region[2]), int(region[3])
            cropped = self._safe_crop(screenshot, x, y, w, h)
        if cropped is None or cropped.size == 0:
            return None

        for attempt in range(1, self.MAX_READ_RETRIES + 1):
            try:
                value = self.ocr.read_number(cropped)
                if value is not None and value >= 0:
                    return value

                logger.debug(
                    "ResourceReader: OCR attempt %d/%d for '%s' returned %s",
                    attempt,
                    self.MAX_READ_RETRIES,
                    label,
                    value,
                )
            except Exception as e:
                logger.debug(
                    "ResourceReader: OCR attempt %d/%d for '%s' failed: %s",
                    attempt,
                    self.MAX_READ_RETRIES,
                    label,
                    e,
                )

        logger.warning("ResourceReader: Failed to read '%s' after %d attempts", label, self.MAX_READ_RETRIES)
        return None

    def _safe_crop(
        self,
        screenshot: np.ndarray,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> np.ndarray | None:
        """Safely crop a region from a screenshot with bounds checking.

        Args:
            screenshot: Full screenshot as numpy array.
            x: Left edge X coordinate.
            y: Top edge Y coordinate.
            w: Width of the crop region.
            h: Height of the crop region.

        Returns:
            Cropped numpy array, or None if bounds are invalid.
        """
        img_h, img_w = screenshot.shape[:2]

        # Clamp to image bounds
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(img_w, x + w)
        y2 = min(img_h, y + h)

        if x2 <= x1 or y2 <= y1:
            logger.warning(
                "ResourceReader: Crop region (%d,%d,%d,%d) invalid for image (%d x %d)",
                x, y, w, h, img_w, img_h,
            )
            return None

        return screenshot[y1:y2, x1:x2]

    @staticmethod
    def _format_amount(value: int | None) -> str:
        """Format a resource amount for human-readable logging.

        Args:
            value: Integer amount or None.

        Returns:
            Formatted string (e.g., '1.2M', '450K', 'N/A').
        """
        if value is None:
            return "N/A"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{value / 1_000:.0f}K"
        return str(value)
