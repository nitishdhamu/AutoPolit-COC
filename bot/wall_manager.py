"""
Wall Manager — Spending excess loot on wall upgrades.
=====================================================
When all builders are busy, excess gold/elixir can be spent on wall
segments.  Finds upgradeable walls on the base, taps them, verifies the
cost is resources (not gems), and confirms the upgrade.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from backends.base import Backend
from backends.coordinates import crop_normalized, to_device_px

if TYPE_CHECKING:
    from bot.ocr import GameOCR
    from bot.navigation import Navigator
    from bot.resource_reader import ResourceReader

logger = logging.getLogger(__name__)


class WallManager:
    """Manages wall upgrades to spend excess resources."""

    # ── Screen coordinate constants (1920×1080) ─────────────────────────

    # Wall segment scan region (base area where walls are typically found): (x, y, w, h)
    WALL_SCAN_REGION: tuple[int, int, int, int] = (300, 250, 1320, 600)
    WALL_SCAN_REGION_NORM: tuple[float, float, float, float] = (300 / 1920, 250 / 1080, 1320 / 1920, 600 / 1080)

    # Upgrade button position in the wall info popup
    WALL_UPGRADE_BTN_POS: tuple[int, int] = (960, 620)
    WALL_UPGRADE_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 620 / 1080)

    # Confirm upgrade button in wall upgrade dialog
    WALL_CONFIRM_BTN_POS: tuple[int, int] = (960, 580)
    WALL_CONFIRM_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 580 / 1080)

    # Cancel button in wall upgrade dialog
    WALL_CANCEL_BTN_POS: tuple[int, int] = (960, 680)
    WALL_CANCEL_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 680 / 1080)

    # Cost region in wall upgrade dialog (for gem check): (x, y, w, h)
    WALL_COST_REGION: tuple[int, int, int, int] = (870, 540, 180, 40)
    WALL_COST_REGION_NORM: tuple[float, float, float, float] = (870 / 1920, 540 / 1080, 180 / 1920, 40 / 1080)

    # Name/level region in wall info popup: (x, y, w, h)
    WALL_INFO_REGION: tuple[int, int, int, int] = (800, 380, 320, 40)
    WALL_INFO_REGION_NORM: tuple[float, float, float, float] = (800 / 1920, 380 / 1080, 320 / 1920, 40 / 1080)

    # Minimum resource threshold to keep (don't spend everything on walls)
    DEFAULT_MIN_RESOURCE_KEEP: int = 500_000

    def __init__(
        self,
        config: dict,
        screen: Optional[Any] = None,
        vision: Optional[Any] = None,
        ocr: Optional[Any] = None,
        input_ctrl: Optional[Any] = None,
        navigator: Optional[Any] = None,
        resource_reader: Optional[Any] = None,
        backend: Optional[Backend] = None,
    ) -> None:
        """Initialise WallManager."""
        self.config = config
        self.backend = backend
        self.screen = screen
        self.vision = vision
        self.ocr = ocr
        self.input_ctrl = input_ctrl
        self.navigator = navigator
        self.resource_reader = resource_reader

        # Config
        self._auto_upgrade: bool = config.get("walls", {}).get("auto_upgrade", True)

        timing_cfg = config.get("timing", {})
        self._action_delay: float = timing_cfg.get("between_actions_delay_sec", 1.5)
        self._dialog_wait: float = timing_cfg.get("dialog_animation_wait_sec", 0.8)
        self._screen_load_wait: float = timing_cfg.get("screen_load_wait_sec", 3.0)

        logger.info("WallManager initialised (auto_upgrade=%s).", self._auto_upgrade)

    def get_screenshot(self):
        """Capture screenshot via backend or screen fallback."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None:
            return self.screen.capture_screenshot()
        raise RuntimeError("No backend or screen capture available in WallManager")

    def tap(self, x: float | int, y: float | int, checks: bool = True) -> bool:
        """Tap at coordinate via backend or input_ctrl fallback."""
        if self.backend is not None:
            return self.backend.safe_click(x, y, checks=checks)
        if self.input_ctrl is not None:
            return self.input_ctrl.safe_click(x, y, checks=checks)
        raise RuntimeError("No backend or input controller available in WallManager")

    def capture_region(self, x: float | int, y: float | int, w: float | int, h: float | int):
        """Capture a region as numpy array."""
        img = self.get_screenshot()
        if x <= 1.0 and y <= 1.0 and w <= 1.0 and h <= 1.0:
            return crop_normalized(img, (float(x), float(y), float(w), float(h)))
        H, W = img.shape[:2]
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2, y2 = min(W, int(x + w)), min(H, int(y + h))
        return img[y1:y2, x1:x2]

    # ── Wall upgrade loop ───────────────────────────────────────────────

    def upgrade_walls(self, max_upgrades: int = 5) -> int:
        """Upgrade wall segments until resources drop or max reached."""
        if not self._auto_upgrade:
            logger.info("Wall auto-upgrade is disabled — skipping.")
            return 0

        logger.info("Starting wall upgrades (max=%d)…", max_upgrades)
        upgraded = 0

        try:
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            for attempt in range(max_upgrades):
                # Check if we still have enough resources
                if not self._has_enough_resources():
                    logger.info(
                        "Resources below threshold — stopping wall upgrades."
                    )
                    break

                # Find an upgradeable wall
                wall_pos = self.find_upgradeable_wall()
                if wall_pos is None:
                    logger.info("No upgradeable wall found — stopping.")
                    break

                wx, wy = wall_pos
                logger.info(
                    "Upgrading wall #%d at (%d, %d)…", attempt + 1, wx, wy
                )

                # Tap the wall segment
                self.tap(wx, wy)
                time.sleep(self._dialog_wait)

                # Look for upgrade button in the wall info popup
                screenshot = self.get_screenshot()
                upg_btn = None
                if self.vision is not None:
                    upg_btn = self.vision.find(
                        screenshot, "wall_upgrade_btn", threshold=0.7
                    )
                if upg_btn is not None:
                    ux, uy, uw, uh = upg_btn
                    self.tap(ux + uw // 2, uy + uh // 2)
                else:
                    self.tap(*self.WALL_UPGRADE_BTN_POS)

                time.sleep(self._dialog_wait)

                # Gem guard: verify cost is resources, not gems
                screenshot = self.get_screenshot()
                gem_dialog = None
                if self.vision is not None:
                    gem_dialog = self.vision.find(
                        screenshot, "gem_purchase_dialog", threshold=0.75
                    )
                if gem_dialog is not None:
                    logger.warning(
                        "🚨 GEM COST detected on wall upgrade — CANCELLING!"
                    )
                    self.tap(*self.WALL_CANCEL_BTN_POS)
                    time.sleep(self._action_delay)
                    self.navigator.close_all_dialogs()
                    break

                if self.vision is not None:
                    gem_icon = self.vision.find(screenshot, "gem_icon", threshold=0.8)
                    if gem_icon is not None:
                        gx, gy, _, _ = gem_icon
                        cx, cy, cw, ch = self.WALL_COST_REGION
                        if cx <= gx <= cx + cw and cy <= gy <= cy + ch:
                            logger.warning(
                                "🚨 Gem icon in wall cost area — CANCELLING!"
                            )
                            self.tap(*self.WALL_CANCEL_BTN_POS)
                            time.sleep(self._action_delay)
                            self.navigator.close_all_dialogs()
                            break

                # Confirm the wall upgrade
                self.tap(*self.WALL_CONFIRM_BTN_POS)
                time.sleep(self._dialog_wait)

                upgraded += 1
                logger.info("✅ Wall #%d upgraded.", upgraded)

                # Close any popups and prepare for next
                self.navigator.close_all_dialogs()
                time.sleep(self._action_delay)

            logger.info("Wall upgrade pass complete: %d walls upgraded.", upgraded)
            return upgraded

        except Exception as exc:
            logger.error("Error during wall upgrades: %s", exc, exc_info=True)
            self.navigator.close_all_dialogs()
            return upgraded

    # ── Wall detection ──────────────────────────────────────────────────

    def find_upgradeable_wall(self) -> tuple[int, int] | None:
        """Scan the base for a wall segment that can be upgraded."""
        logger.info("Searching for upgradeable wall segment…")
        try:
            screenshot = self.get_screenshot()

            if self.vision is not None:
                # Primary: look for wall upgrade arrow indicators
                arrows = self.vision.find_all(
                    screenshot, "wall_upgrade_arrow", threshold=0.7
                )
                if arrows:
                    ax, ay, aw, ah = arrows[0]
                    center_x = ax + aw // 2
                    center_y = ay + ah // 2
                    logger.info(
                        "Found wall upgrade arrow at (%d, %d).", center_x, center_y
                    )
                    return (center_x, center_y)

                # Secondary: look for generic wall segment templates
                walls = self.vision.find_all(
                    screenshot, "wall_segment", threshold=0.75
                )
                if walls:
                    wx, wy, ww, wh = walls[0]
                    center_x = wx + ww // 2
                    center_y = wy + wh // 2
                    logger.info(
                        "Found wall segment at (%d, %d).", center_x, center_y
                    )
                    return (center_x, center_y)

            logger.info("No upgradeable wall found by template matching.")
            return None

        except Exception as exc:
            logger.error("Error finding upgradeable wall: %s", exc, exc_info=True)
            return None

    def _has_enough_resources(self) -> bool:
        """Check if we have enough resources to continue upgrading walls.

        Returns:
            True if gold or elixir is above the keep threshold.
        """
        try:
            gold = self.resource_reader.read_gold()
            elixir = self.resource_reader.read_elixir()

            threshold = self.DEFAULT_MIN_RESOURCE_KEEP
            has_enough = gold >= threshold or elixir >= threshold

            logger.debug(
                "Wall resource check: gold=%d, elixir=%d, threshold=%d → %s",
                gold, elixir, threshold, has_enough,
            )
            return has_enough
        except Exception as exc:
            logger.error("Error checking resources for walls: %s", exc, exc_info=True)
            return False
