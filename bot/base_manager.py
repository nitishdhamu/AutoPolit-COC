"""
Base Manager — Builder tracking, upgrades, and resource collection.
===================================================================
Manages the home village builders: reads builder status, opens the builder
menu, parses upgrade timers and suggested upgrades, starts upgrades via
the game's "Recommended" feature, and sweeps collectors for resources.

All pixel coordinates are defined as class constants for 1920×1080 resolution.
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
    from bot.resource_reader import ResourceReader

logger = logging.getLogger(__name__)


class BaseManager:
    """Manages home village builders, upgrades, and resource collection."""

    # ── Screen coordinate constants (1920×1080) ─────────────────────────
    # Builder icon in the top-left HUD area
    BUILDER_ICON_POS: tuple[int, int] = (295, 18)
    BUILDER_ICON_POS_NORM: tuple[float, float] = (295 / 1920, 18 / 1080)

    # Builder menu header region (for detecting the panel is open)
    BUILDER_MENU_REGION: tuple[int, int, int, int] = (300, 80, 400, 520)  # (x, y, w, h)
    BUILDER_MENU_REGION_NORM: tuple[float, float, float, float] = (300 / 1920, 80 / 1080, 400 / 1920, 520 / 1080)

    # Region where 'Upgrades in progress:' entries appear
    UPGRADES_IN_PROGRESS_REGION: tuple[int, int, int, int] = (320, 150, 360, 250)  # (x, y, w, h)
    UPGRADES_IN_PROGRESS_REGION_NORM: tuple[float, float, float, float] = (320 / 1920, 150 / 1080, 360 / 1920, 250 / 1080)

    # Region where 'Suggested upgrades:' entries appear (below in-progress)
    SUGGESTED_UPGRADES_REGION: tuple[int, int, int, int] = (320, 400, 360, 200)  # (x, y, w, h)
    SUGGESTED_UPGRADES_REGION_NORM: tuple[float, float, float, float] = (320 / 1920, 400 / 1080, 360 / 1920, 200 / 1080)

    # First suggested upgrade tap target (relative to menu)
    FIRST_SUGGESTED_TAP_POS: tuple[int, int] = (500, 440)
    FIRST_SUGGESTED_TAP_POS_NORM: tuple[float, float] = (500 / 1920, 440 / 1080)

    # Confirmation dialog button positions
    CONFIRM_UPGRADE_BTN_POS: tuple[int, int] = (960, 640)
    CONFIRM_UPGRADE_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 640 / 1080)

    # Timer display region inside confirmation dialog
    CONFIRM_TIMER_REGION: tuple[int, int, int, int] = (800, 400, 320, 40)  # (x, y, w, h)
    CONFIRM_TIMER_REGION_NORM: tuple[float, float, float, float] = (800 / 1920, 400 / 1080, 320 / 1920, 40 / 1080)

    # Upgrade cost region in confirmation dialog (to check gems vs resources)
    CONFIRM_COST_REGION: tuple[int, int, int, int] = (870, 580, 180, 40)  # (x, y, w, h)
    CONFIRM_COST_REGION_NORM: tuple[float, float, float, float] = (870 / 1920, 580 / 1080, 180 / 1920, 40 / 1080)

    # Upgrade name region in confirmation dialog
    CONFIRM_NAME_REGION: tuple[int, int, int, int] = (780, 280, 360, 40)  # (x, y, w, h)
    CONFIRM_NAME_REGION_NORM: tuple[float, float, float, float] = (780 / 1920, 280 / 1080, 360 / 1920, 40 / 1080)

    # Resource collector sweep positions — common collector locations on base
    COLLECTOR_SWEEP_POSITIONS: list[tuple[int, int]] = [
        # Row 1 — top area
        (400, 350), (550, 300), (700, 280), (850, 300), (1000, 350),
        # Row 2 — upper-mid
        (350, 450), (500, 420), (650, 400), (800, 420), (950, 450),
        # Row 3 — mid
        (300, 550), (480, 530), (660, 520), (840, 530), (1020, 550),
        # Row 4 — lower-mid
        (350, 650), (530, 630), (710, 620), (890, 630), (1050, 650),
        # Row 5 — bottom area
        (400, 730), (580, 720), (760, 710), (940, 720), (1100, 730),
    ]
    COLLECTOR_SWEEP_POSITIONS_NORM: list[tuple[float, float]] = [
        (round(x / 1920, 4), round(y / 1080, 4)) for x, y in COLLECTOR_SWEEP_POSITIONS
    ]

    # Close button for builder menu
    BUILDER_MENU_CLOSE_POS: tuple[int, int] = (720, 100)
    BUILDER_MENU_CLOSE_POS_NORM: tuple[float, float] = (720 / 1920, 100 / 1080)

    # Height per upgrade entry row in builder menu
    UPGRADE_ENTRY_HEIGHT: int = 50
    UPGRADE_ENTRY_HEIGHT_NORM: float = round(50 / 1080, 4)

    # Max entries we expect in upgrade timers list
    MAX_UPGRADE_ENTRIES: int = 6

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
        """Initialise BaseManager with all required dependencies."""
        self.config = config
        self.backend = backend
        self.screen = screen
        self.vision = vision
        self.ocr = ocr
        self.input_ctrl = input_ctrl
        self.navigator = navigator
        self.resource_reader = resource_reader

        # Timing from config
        self._action_delay: float = config.get("timing", {}).get(
            "between_actions_delay_sec", 1.5
        )
        self._dialog_wait: float = config.get("timing", {}).get(
            "dialog_animation_wait_sec", 0.8
        )
        self._screen_load_wait: float = config.get("timing", {}).get(
            "screen_load_wait_sec", 3.0
        )

        logger.info("BaseManager initialised.")

    def get_screenshot(self):
        """Capture screenshot via backend or screen fallback."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None:
            return self.screen.capture_screenshot()
        raise RuntimeError("No backend or screen capture available in BaseManager")

    def tap(self, x: float | int, y: float | int, checks: bool = True) -> bool:
        """Tap at coordinate via backend or input_ctrl fallback."""
        if self.backend is not None:
            return self.backend.safe_click(x, y, checks=checks)
        if self.input_ctrl is not None:
            return self.input_ctrl.safe_click(x, y, checks=checks)
        raise RuntimeError("No backend or input controller available in BaseManager")

    def capture_region(self, x: float | int, y: float | int, w: float | int, h: float | int):
        """Capture a region as numpy array."""
        img = self.get_screenshot()
        if x <= 1.0 and y <= 1.0 and w <= 1.0 and h <= 1.0:
            return crop_normalized(img, (float(x), float(y), float(w), float(h)))
        H, W = img.shape[:2]
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2, y2 = min(W, int(x + w)), min(H, int(y + h))
        return img[y1:y2, x1:x2]

    # ── Builder status ──────────────────────────────────────────────────

    # OTTO Hutt = permanent 6th builder (unlocked by maxing BB). USED by bot.
    # Goblin Builder = temporary event builder. NEVER used. Detected at runtime.
    #
    # Detection rule:
    #   raw_total (from HUD) > account_max_builders → Goblin is present.
    #   e.g. account max=5, HUD shows 3/6 → goblin present → treat as 3/5
    #   e.g. account max=6 (OTTO), HUD shows 4/7 → goblin present → treat as 4/6
    #   e.g. account max=6 (OTTO), HUD shows 4/6 → no goblin → use as-is

    # Absolute upper bound for any account (OTTO + 5 regular = 6 max)
    ABSOLUTE_BUILDER_MAX: int = 6

    def get_builder_status(self) -> tuple[int, int]:
        """Read raw builder count from the HUD (e.g. '2/5', '3/6', '2/7').

        Returns the raw OCR values WITHOUT goblin adjustment.
        Callers should use ``has_idle_builder(max_builders)`` which applies
        the per-account cap and goblin exclusion.

        Returns:
            Tuple of (raw_free, raw_total).
            Falls back to (0, 5) if the read fails.
        """
        try:
            free, total = self.resource_reader.read_builder_count()
            logger.debug("Raw HUD builder count: %d free / %d total", free, total)
            return (free, total)
        except Exception as exc:
            logger.error("Failed to read builder status: %s", exc, exc_info=True)
            return (0, 5)

    def has_idle_builder(self, max_builders: int = 5) -> bool:
        """Check whether at least one NORMAL builder is idle.

        Applies per-account cap and Goblin Builder exclusion:
        - If raw_total > max_builders → Goblin detected → subtract 1
          from both free and total so the goblin is invisible to callers.
        - Values > ABSOLUTE_BUILDER_MAX are clamped for safety.

        Args:
            max_builders: The account's configured builder cap (5 or 6).
                          Pass ``account['max_builders']`` here.

        Returns:
            True if at least one non-goblin builder is idle.
        """
        # Clamp caller's cap to absolute ceiling (defensive)
        effective_cap = min(max_builders, self.ABSOLUTE_BUILDER_MAX)

        raw_free, raw_total = self.get_builder_status()

        goblin_present = raw_total > effective_cap
        if goblin_present:
            # How many extra (goblin) slots does the HUD show?
            goblin_slots = raw_total - effective_cap
            logger.info(
                "Goblin Builder detected (HUD: %d/%d, account cap: %d, goblin_slots: %d) — excluding.",
                raw_free, raw_total, effective_cap, goblin_slots,
            )
            # Subtract goblin slots from free count. We can't know for sure
            # which specific builders are goblin vs normal, but the total
            # must be capped at effective_cap, so free gets reduced by the
            # same overshoot amount.
            free  = max(0, raw_free - goblin_slots)
            total = effective_cap
        else:
            free  = raw_free
            total = min(raw_total, effective_cap)

        has_idle = free > 0
        logger.info(
            "has_idle_builder(cap=%d, goblin=%s): %d free / %d total → %s",
            effective_cap, goblin_present, free, total, has_idle,
        )
        return has_idle

    # ── Builder menu interaction ────────────────────────────────────────

    def open_builder_menu(self) -> bool:
        """Open the builder menu by tapping the builder icon near top-left.

        Returns:
            True if the builder menu panel was detected after tap.
        """
        logger.info("Opening builder menu…")
        try:
            # Ensure we're on the home screen first
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            # Tap the builder icon
            self.tap(*self.BUILDER_ICON_POS)
            time.sleep(self._dialog_wait)

            # Verify the builder menu panel appeared
            screenshot = self.get_screenshot()
            if self.vision is not None and self.vision.find(screenshot, "builder_menu", threshold=0.7) is not None:
                logger.info("Builder menu opened successfully.")
                return True

            # Fallback: try OCR to find 'Upgrades in progress' or 'Suggested'
            region_img = self.capture_region(*self.BUILDER_MENU_REGION)
            text = (self.ocr.read_text(region_img) or "").lower()
            if "upgrade" in text or "suggested" in text or "progress" in text:
                logger.info("Builder menu detected via OCR text.")
                return True

            logger.warning("Builder menu may not have opened — no panel detected.")
            return False

        except Exception as exc:
            logger.error("Error opening builder menu: %s", exc, exc_info=True)
            return False

    def read_upgrade_timers(self) -> list[dict]:
        """Read active upgrade timers from the builder menu.

        Expects the builder menu to be open already.  Scans the
        'Upgrades in progress:' section and extracts each entry's name
        and remaining timer.

        Returns:
            List of dicts, each with keys ``'name'`` (str) and
            ``'timer'`` (timedelta).  Empty list if nothing found.
        """
        logger.info("Reading upgrade timers from builder menu…")
        timers: list[dict] = []

        try:
            rx, ry, rw, rh = self.UPGRADES_IN_PROGRESS_REGION
            entry_h = self.UPGRADE_ENTRY_HEIGHT

            for i in range(self.MAX_UPGRADE_ENTRIES):
                entry_y = ry + i * entry_h

                # Name region (left part of entry)
                name_img = self.capture_region(rx, entry_y, 220, entry_h)
                name_text = (self.ocr.read_text(name_img) or "").strip()

                if not name_text or len(name_text) < 2:
                    # No more entries
                    break

                # Timer region (right part of entry)
                timer_img = self.capture_region(
                    rx + 230, entry_y, rw - 230, entry_h
                )
                timer_td = self.ocr.read_timer(timer_img)

                if timer_td and timer_td.total_seconds() > 0:
                    entry = {"name": name_text, "timer": timer_td}
                    timers.append(entry)
                    logger.info(
                        "  Upgrade #%d: '%s' — %s remaining",
                        i + 1,
                        name_text,
                        timer_td,
                    )
                else:
                    logger.debug(
                        "  Entry #%d: name='%s' but timer unreadable — skipping.",
                        i + 1,
                        name_text,
                    )

            logger.info("Total upgrade timers read: %d", len(timers))
            return timers

        except Exception as exc:
            logger.error("Error reading upgrade timers: %s", exc, exc_info=True)
            return timers

    def get_suggested_upgrade(self) -> dict | None:
        """Read the first suggested upgrade from the builder menu.

        Looks for the 'Suggested upgrades:' section in the builder menu
        and returns the first entry with its name and cost.

        Returns:
            Dict with ``'name'`` and ``'cost'`` keys, or None if no
            suggestion found.
        """
        logger.info("Looking for suggested upgrade in builder menu…")
        try:
            rx, ry, rw, rh = self.SUGGESTED_UPGRADES_REGION

            # Read the full suggested area first
            area_img = self.capture_region(rx, ry, rw, rh)
            area_text = (self.ocr.read_text(area_img) or "").strip()

            if not area_text or "suggested" not in area_text.lower():
                logger.info("No 'Suggested upgrades' section found.")
                return None

            # Read first entry name (below the header)
            name_img = self.capture_region(rx, ry + 30, 220, 40)
            name_text = (self.ocr.read_text(name_img) or "").strip()

            # Read cost
            cost_img = self.capture_region(rx + 230, ry + 30, 150, 40)
            cost_text = (self.ocr.read_text(cost_img) or "").strip()

            if name_text:
                result = {"name": name_text, "cost": cost_text}
                logger.info("Suggested upgrade: '%s' costing %s", name_text, cost_text)
                return result

            logger.info("Could not parse suggested upgrade entry.")
            return None

        except Exception as exc:
            logger.error(
                "Error reading suggested upgrade: %s", exc, exc_info=True
            )
            return None

    def start_suggested_upgrade(self) -> dict | None:
        """Tap the first suggested upgrade and confirm it.

        Workflow:
        1. Open builder menu (if not already open).
        2. Tap the first suggested upgrade entry.
        3. Wait for the confirmation dialog.
        4. Verify the cost is resources (NOT gems) via gem guard.
        5. Confirm the upgrade.
        6. Read the upgrade timer.

        Returns:
            Dict with upgrade info (``'name'``, ``'cost'``, ``'timer'``)
            or None if the upgrade could not be started.
        """
        logger.info("Starting suggested upgrade…")
        try:
            # Step 1: ensure builder menu is open
            if not self.open_builder_menu():
                logger.warning("Cannot start upgrade — builder menu won't open.")
                return None

            time.sleep(self._action_delay)

            # Step 2: check if there's a suggested upgrade
            suggestion = self.get_suggested_upgrade()
            if suggestion is None:
                logger.info("No suggested upgrade available.")
                return None

            # Step 3: tap the first suggested entry
            logger.info("Tapping suggested upgrade: '%s'", suggestion.get("name"))
            self.tap(*self.FIRST_SUGGESTED_TAP_POS)
            time.sleep(self._dialog_wait)

            # Wait for confirmation dialog to appear
            time.sleep(self._screen_load_wait)

            # Step 4: gem guard — verify the cost is NOT gems
            screenshot = self.get_screenshot()
            gem_dialog = None
            if self.vision is not None:
                gem_dialog = self.vision.find(
                    screenshot, "gem_purchase_dialog", threshold=0.75
                )
            if gem_dialog is not None:
                logger.warning(
                    "🚨 GEM COST detected on upgrade dialog — CANCELLING!"
                )
                self.tap(960, 700)  # Cancel button
                time.sleep(self._action_delay)
                return None

            # Additional gem check: look for gem icon near cost area
            if self.vision is not None:
                cost_screenshot = self.get_screenshot()
                gem_icon = self.vision.find(
                    cost_screenshot, "gem_icon", threshold=0.8
                )
                if gem_icon is not None:
                    # Check if gem icon is within the cost region
                    gx, gy, _, _ = gem_icon
                    crx, cry, crw, crh = self.CONFIRM_COST_REGION
                    if crx <= gx <= crx + crw and cry <= gy <= cry + crh:
                        logger.warning(
                            "🚨 Gem icon found in cost area — CANCELLING upgrade!"
                        )
                        self.tap(960, 700)
                        time.sleep(self._action_delay)
                        return None

            # Step 5: read upgrade name from dialog
            name_img = self.capture_region(*self.CONFIRM_NAME_REGION)
            upgrade_name = (self.ocr.read_text(name_img) or "").strip()
            if not upgrade_name:
                upgrade_name = suggestion.get("name", "Unknown")

            # Step 6: read timer from dialog
            timer_img = self.capture_region(*self.CONFIRM_TIMER_REGION)
            upgrade_timer = self.ocr.read_timer(timer_img)

            # Step 7: confirm the upgrade
            logger.info(
                "Confirming upgrade: '%s' (timer: %s)",
                upgrade_name,
                upgrade_timer,
            )
            self.tap(*self.CONFIRM_UPGRADE_BTN_POS)
            time.sleep(self._dialog_wait)

            result = {
                "name": upgrade_name,
                "cost": suggestion.get("cost", "unknown"),
                "timer": upgrade_timer if upgrade_timer else timedelta(0),
            }
            logger.info("✅ Upgrade started: %s", result)
            return result

        except Exception as exc:
            logger.error(
                "Error starting suggested upgrade: %s", exc, exc_info=True
            )
            return None

    # ── Resource collection ─────────────────────────────────────────────

    def collect_resources(self) -> bool:
        """Collect resources from ready collectors using a sweep pattern.

        Taps across known collector positions on the base.  Collectors
        that are ready show golden (gold) or purple (elixir) bubbles
        which respond to taps.  We also attempt template-based detection
        for collector-ready indicators.

        Returns:
            True if at least one collection tap was performed.
        """
        logger.info("Collecting resources with sweep pattern…")
        collected_any = False

        try:
            # Ensure we are on the home base
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            # First pass: try template-based detection of ready indicators if vision available
            if self.vision is not None:
                screenshot = self.get_screenshot()
                ready_indicators = self.vision.find_all(
                    screenshot, "collector_ready", threshold=0.7
                )

                if ready_indicators:
                    logger.info(
                        "Found %d collector-ready indicators via template.",
                        len(ready_indicators),
                    )
                    for rx, ry, rw, rh in ready_indicators:
                        center_x = rx + rw // 2
                        center_y = ry + rh // 2
                        self.tap(center_x, center_y, checks=False)
                        time.sleep(0.3)
                        collected_any = True

            # Second pass: sweep pattern across known positions
            # This catches collectors that template matching might miss
            logger.info("Running collector sweep pattern (%d positions)…",
                        len(self.COLLECTOR_SWEEP_POSITIONS))
            for sx, sy in self.COLLECTOR_SWEEP_POSITIONS:
                self.tap(sx, sy, checks=False)
                time.sleep(0.15)
                collected_any = True

            # Brief pause to let animations complete
            time.sleep(self._action_delay)

            if collected_any:
                logger.info("✅ Resource collection sweep completed.")
            else:
                logger.info("No collectors detected or tapped.")

            return collected_any

        except Exception as exc:
            logger.error("Error during resource collection: %s", exc, exc_info=True)
            return False
