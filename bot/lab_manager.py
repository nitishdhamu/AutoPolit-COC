"""
Lab Manager — Laboratory research management.
==============================================
Manages starting and tracking troop/spell research upgrades in the
Laboratory.  Uses the game's 'Suggested upgrades:' feature to pick
the next research and includes gem-guard safety checks.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from backends.base import Backend
from backends.coordinates import crop_normalized, to_device_px

if TYPE_CHECKING:
    from bot.ocr import GameOCR
    from bot.navigation import Navigator

logger = logging.getLogger(__name__)


class LabManager:
    """Manages Laboratory research: check status, start suggested research."""

    # ── Screen coordinate constants (1920×1080) ─────────────────────────

    # Lab building approximate position on a typical base layout
    LAB_BUILDING_POS: tuple[int, int] = (1200, 600)
    LAB_BUILDING_POS_NORM: tuple[float, float] = (1200 / 1920, 600 / 1080)

    # Builder icon position (to open builder menu for lab status check)
    BUILDER_ICON_POS: tuple[int, int] = (295, 18)
    BUILDER_ICON_POS_NORM: tuple[float, float] = (295 / 1920, 18 / 1080)

    # Builder menu region for scanning lab-related entries
    BUILDER_MENU_REGION: tuple[int, int, int, int] = (300, 80, 400, 520)  # (x, y, w, h)
    BUILDER_MENU_REGION_NORM: tuple[float, float, float, float] = (300 / 1920, 80 / 1080, 400 / 1920, 520 / 1080)

    # 'Upgrades in progress' region in builder menu
    UPGRADES_IN_PROGRESS_REGION: tuple[int, int, int, int] = (320, 150, 360, 250)  # (x, y, w, h)
    UPGRADES_IN_PROGRESS_REGION_NORM: tuple[float, float, float, float] = (320 / 1920, 150 / 1080, 360 / 1920, 250 / 1080)

    # Lab research UI — "Suggested upgrades:" region inside lab
    LAB_SUGGESTED_REGION: tuple[int, int, int, int] = (350, 400, 350, 200)  # (x, y, w, h)
    LAB_SUGGESTED_REGION_NORM: tuple[float, float, float, float] = (350 / 1920, 400 / 1080, 350 / 1920, 200 / 1080)

    # First suggested research entry tap position
    LAB_FIRST_SUGGESTED_POS: tuple[int, int] = (530, 450)
    LAB_FIRST_SUGGESTED_POS_NORM: tuple[float, float] = (530 / 1920, 450 / 1080)

    # Confirm research button in the confirmation dialog
    LAB_CONFIRM_BTN_POS: tuple[int, int] = (960, 640)
    LAB_CONFIRM_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 640 / 1080)

    # Timer region in the lab confirmation dialog
    LAB_TIMER_REGION: tuple[int, int, int, int] = (800, 400, 320, 40)  # (x, y, w, h)
    LAB_TIMER_REGION_NORM: tuple[float, float, float, float] = (800 / 1920, 400 / 1080, 320 / 1920, 40 / 1080)

    # Research name region in confirmation dialog
    LAB_NAME_REGION: tuple[int, int, int, int] = (780, 280, 360, 40)  # (x, y, w, h)
    LAB_NAME_REGION_NORM: tuple[float, float, float, float] = (780 / 1920, 280 / 1080, 360 / 1920, 40 / 1080)

    # Cost region in confirmation dialog (for gem check)
    LAB_COST_REGION: tuple[int, int, int, int] = (870, 580, 180, 40)  # (x, y, w, h)
    LAB_COST_REGION_NORM: tuple[float, float, float, float] = (870 / 1920, 580 / 1080, 180 / 1920, 40 / 1080)

    # Cancel button position in confirmation dialog
    LAB_CANCEL_BTN_POS: tuple[int, int] = (960, 700)
    LAB_CANCEL_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 700 / 1080)

    # Entry height for builder menu scanning
    UPGRADE_ENTRY_HEIGHT: int = 50
    UPGRADE_ENTRY_HEIGHT_NORM: float = round(50 / 1080, 4)

    # Max upgrade entries to scan
    MAX_UPGRADE_ENTRIES: int = 6

    def __init__(
        self,
        config: dict,
        screen: Optional[Any] = None,
        vision: Optional[Any] = None,
        ocr: Optional[Any] = None,
        input_ctrl: Optional[Any] = None,
        navigator: Optional[Any] = None,
        backend: Optional[Backend] = None,
    ) -> None:
        """Initialise LabManager."""
        self.config = config
        self.backend = backend
        self.screen = screen
        self.vision = vision
        self.ocr = ocr
        self.input_ctrl = input_ctrl
        self.navigator = navigator

        # Config
        lab_cfg = config.get("lab", {})
        self._auto_research: bool = lab_cfg.get("auto_research", True)

        timing_cfg = config.get("timing", {})
        self._action_delay: float = timing_cfg.get("between_actions_delay_sec", 1.5)
        self._dialog_wait: float = timing_cfg.get("dialog_animation_wait_sec", 0.8)
        self._screen_load_wait: float = timing_cfg.get("screen_load_wait_sec", 3.0)

        logger.info("LabManager initialised (auto_research=%s).", self._auto_research)

    def get_screenshot(self):
        """Capture screenshot via backend or screen fallback."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None:
            return self.screen.capture_screenshot()
        raise RuntimeError("No backend or screen capture available in LabManager")

    def tap(self, x: float | int, y: float | int, checks: bool = True) -> bool:
        """Tap at coordinate via backend or input_ctrl fallback."""
        if self.backend is not None:
            return self.backend.safe_click(x, y, checks=checks)
        if self.input_ctrl is not None:
            return self.input_ctrl.safe_click(x, y, checks=checks)
        raise RuntimeError("No backend or input controller available in LabManager")

    def capture_region(self, x: float | int, y: float | int, w: float | int, h: float | int):
        """Capture a region as numpy array."""
        img = self.get_screenshot()
        if x <= 1.0 and y <= 1.0 and w <= 1.0 and h <= 1.0:
            return crop_normalized(img, (float(x), float(y), float(w), float(h)))
        H, W = img.shape[:2]
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2, y2 = min(W, int(x + w)), min(H, int(y + h))
        return img[y1:y2, x1:x2]

    # ── Lab status ──────────────────────────────────────────────────────

    def is_lab_free(self) -> bool:
        """Check whether the Laboratory is free (no research in progress)."""
        logger.info("Checking if Lab is free…")
        try:
            # Open builder menu
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            self.tap(*self.BUILDER_ICON_POS)
            time.sleep(self._dialog_wait)

            # Scan upgrades in progress for lab-related entries
            rx, ry, rw, rh = self.UPGRADES_IN_PROGRESS_REGION

            for i in range(self.MAX_UPGRADE_ENTRIES):
                entry_y = ry + i * self.UPGRADE_ENTRY_HEIGHT
                name_img = self.capture_region(rx, entry_y, 220, self.UPGRADE_ENTRY_HEIGHT)
                name_text = (self.ocr.read_text(name_img) or "").strip().lower()

                if not name_text or len(name_text) < 2:
                    break

                # Check if this entry is a lab research
                if "lab" in name_text or "research" in name_text:
                    logger.info("Lab is BUSY — found entry: '%s'", name_text)
                    self.navigator.close_all_dialogs()
                    return False

            logger.info("Lab is FREE — no research detected in builder menu.")
            self.navigator.close_all_dialogs()
            return True

        except Exception as exc:
            logger.error("Error checking lab status: %s", exc, exc_info=True)
            self.navigator.close_all_dialogs()
            return False

    # ── Lab navigation ──────────────────────────────────────────────────

    def open_lab(self) -> bool:
        """Navigate to the Laboratory building."""
        logger.info("Opening Laboratory…")
        try:
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            # Try to find Lab building by template
            screenshot = self.get_screenshot()
            lab_match = None
            if self.vision is not None:
                lab_match = self.vision.find(screenshot, "lab_building", threshold=0.7)

            if lab_match is not None:
                lx, ly, lw, lh = lab_match
                tap_x = lx + lw // 2
                tap_y = ly + lh // 2
                logger.info("Found Lab building at (%d, %d).", tap_x, tap_y)
            else:
                tap_x, tap_y = self.LAB_BUILDING_POS
                logger.info(
                    "Lab building not found by template — using fallback (%d, %d).",
                    tap_x, tap_y,
                )

            # Tap the lab building
            self.tap(tap_x, tap_y)
            time.sleep(self._dialog_wait)

            # Look for the "Research" button inside lab info popup
            screenshot = self.get_screenshot()
            research_btn = None
            if self.vision is not None:
                research_btn = self.vision.find(
                    screenshot, "lab_research_btn", threshold=0.7
                )
            if research_btn is not None:
                rbx, rby, rbw, rbh = research_btn
                self.tap(rbx + rbw // 2, rby + rbh // 2)
                time.sleep(self._screen_load_wait)
                logger.info("Lab research screen opened.")
                return True

            # Maybe the lab UI opened directly
            lab_area = self.capture_region(*self.LAB_SUGGESTED_REGION)
            lab_text = (self.ocr.read_text(lab_area) or "").lower()
            if "suggested" in lab_text or "upgrade" in lab_text or "research" in lab_text:
                logger.info("Lab screen appears to be open (detected via OCR).")
                return True

            logger.warning("Could not verify Lab screen opened.")
            return False

        except Exception as exc:
            logger.error("Error opening Lab: %s", exc, exc_info=True)
            return False

    # ── Start research ──────────────────────────────────────────────────

    def start_research(self) -> dict | None:
        """Start the first suggested research in the Lab.

        Workflow:
        1. Open Lab (via building or builder menu).
        2. Look for 'Suggested upgrades:' in Lab.
        3. Tap the first suggested research.
        4. Verify cost is NOT gems (gem guard).
        5. Confirm.
        6. Read timer.
        7. Return research info.

        Returns:
            Dict with ``'name'``, ``'cost'``, ``'timer'`` keys, or None
            if research could not be started.
        """
        if not self._auto_research:
            logger.info("Auto-research is disabled in config — skipping.")
            return None

        logger.info("Starting Lab research…")
        try:
            # Step 1: open Lab
            if not self.open_lab():
                logger.warning("Cannot start research — Lab won't open.")
                return None

            time.sleep(self._action_delay)

            # Step 2: look for suggested research
            area_img = self.capture_region(*self.LAB_SUGGESTED_REGION)
            area_text = (self.ocr.read_text(area_img) or "").strip().lower()

            if "suggested" not in area_text:
                logger.info("No 'Suggested upgrades' found in Lab.")
                self.navigator.close_all_dialogs()
                return None

            # Step 3: tap first suggested entry
            logger.info("Tapping first suggested research…")
            self.tap(*self.LAB_FIRST_SUGGESTED_POS)
            time.sleep(self._dialog_wait)
            time.sleep(self._screen_load_wait)

            # Step 4: gem guard — check for gem cost
            screenshot = self.get_screenshot()
            gem_dialog = None
            if self.vision is not None:
                gem_dialog = self.vision.find(
                    screenshot, "gem_purchase_dialog", threshold=0.75
                )
            if gem_dialog is not None:
                logger.warning("🚨 GEM COST detected on Lab research — CANCELLING!")
                self.tap(*self.LAB_CANCEL_BTN_POS)
                time.sleep(self._action_delay)
                self.navigator.close_all_dialogs()
                return None

            # Check for gem icon near cost area
            if self.vision is not None:
                gem_icon = self.vision.find(screenshot, "gem_icon", threshold=0.8)
                if gem_icon is not None:
                    gx, gy, _, _ = gem_icon
                    cx, cy, cw, ch = self.LAB_COST_REGION
                    if cx <= gx <= cx + cw and cy <= gy <= cy + ch:
                        logger.warning("🚨 Gem icon in cost area — CANCELLING research!")
                        self.tap(*self.LAB_CANCEL_BTN_POS)
                        time.sleep(self._action_delay)
                        self.navigator.close_all_dialogs()
                        return None

            # Step 5 & 6: read name and timer before confirming
            name_img = self.capture_region(*self.LAB_NAME_REGION)
            research_name = (self.ocr.read_text(name_img) or "").strip()
            if not research_name:
                research_name = "Unknown Research"

            timer_img = self.capture_region(*self.LAB_TIMER_REGION)
            research_timer = self.ocr.read_timer(timer_img)

            cost_img = self.capture_region(*self.LAB_COST_REGION)
            cost_text = (self.ocr.read_text(cost_img) or "").strip()

            # Step 7: confirm
            logger.info(
                "Confirming research: '%s' (timer: %s, cost: %s)",
                research_name, research_timer, cost_text,
            )
            self.tap(*self.LAB_CONFIRM_BTN_POS)
            time.sleep(self._dialog_wait)

            self.navigator.close_all_dialogs()

            result = {
                "name": research_name,
                "cost": cost_text,
                "timer": research_timer if research_timer else timedelta(0),
            }
            logger.info("✅ Lab research started: %s", result)
            return result

        except Exception as exc:
            logger.error("Error starting Lab research: %s", exc, exc_info=True)
            self.navigator.close_all_dialogs()
            return None
