"""
Gem Guard — Critical Safety Module
====================================
Prevents accidental gem spending by detecting and dismissing gem purchase
dialogs. This is integrated as middleware into every click action via the
InputController.

Detection methods:
    1. Template match for gem purchase confirmation dialog
    2. Template match for "Not enough resources, use Gems?" dialog
    3. Template match for green gem icon next to cost displays
    4. OCR check: verify upgrade/action shows resource cost, not gem cost

Response:
    1. Immediately click Cancel / X / outside dialog
    2. Log WARNING with diagnostic screenshot
    3. Return False to caller (action aborted)
    4. Monitor gem count: if gems decrease unexpectedly → EMERGENCY STOP
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from backends.base import Backend
from backends.coordinates import crop_normalized, to_normalized
from bot.ocr import GameOCR

logger = logging.getLogger(__name__)


class GemGuard:
    """Prevents accidental gem spending by detecting gem purchase dialogs.

    This module is the single most critical safety mechanism in the bot.
    Every click goes through gem guard checks to ensure we never accidentally
    confirm a gem purchase.

    Attributes:
        config: Bot configuration dictionary.
        backend: Backend instance for device communication.
        vision: Vision engine for template matching (optional/legacy).
        ocr: OCR engine for reading text/numbers.
    """

    # ── Template names for gem-related dialogs ──────────────────────────
    DIALOG_TEMPLATES: list[str] = [
        "gem_purchase_dialog",   # "Not enough resources, use Gems?"
        "gem_confirm",           # Any dialog showing gem cost confirmation
    ]

    GEM_ICON_TEMPLATE: str = "gem_icon"

    # A gem-purchase dialog has very stable human-readable wording even when
    # artwork/templates change.  Any of these phrases is enough to block
    # input; false positives are safer than spending gems.
    GEM_DIALOG_TEXT: tuple[str, ...] = (
        "you need more",
        "buy the missing",
        "use gems",
        "gem",
    )

    # ── Close/cancel button templates ───────────────────────────────────
    DISMISS_TEMPLATES: list[str] = [
        "gem_purchase_x_btn",    # X close button on gem dialog
        "dialog_close_x",        # X button on dialogs
        "cancel_btn",            # Generic cancel button
    ]

    # ── Resource icon templates for cost verification ───────────────────
    RESOURCE_ICON_MAP: dict[str, str] = {
        "gold": "gold_icon",
        "elixir": "elixir_icon",
        "dark_elixir": "dark_elixir_icon",
    }

    # ── Gem count region on resource bar (normalized: nx, ny, nw, nh) ──
    # Top-right corner: 1780/1920, 115/1080, 140/1920, 30/1080
    GEM_COUNT_REGION_NORM: tuple[float, float, float, float] = (0.9271, 0.1065, 0.0729, 0.0278)
    GEM_COUNT_REGION: tuple[int, int, int, int] = (1780, 115, 140, 30)  # Legacy (x, y, w, h)

    # ── Detection confidence thresholds ─────────────────────────────────
    DIALOG_THRESHOLD: float = 0.75
    ICON_THRESHOLD: float = 0.80

    def __init__(
        self,
        config: dict,
        backend_or_screen: Any,
        vision: Any = None,
        ocr: Any = None,
        backend: Optional[Backend] = None,
    ) -> None:
        """Initialize the GemGuard safety module.

        Args:
            config: Bot configuration dictionary.
            backend_or_screen: Backend instance (or legacy ScreenCapture).
            vision: Vision engine for template matching.
            ocr: OCR engine for reading numbers/text.
            backend: Explicit Backend instance if passing legacy parameters.
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

        if ocr is None and isinstance(vision, GameOCR):
            self.ocr = vision
            self.vision = None
        else:
            self.vision = vision
            self.ocr = ocr or GameOCR(config)

        # Safety settings from config
        safety_cfg = config.get("safety", {})
        self.enabled: bool = safety_cfg.get("gem_guard_enabled", True)
        self.screenshot_on_error: bool = safety_cfg.get("screenshot_on_error", True)

        # Timing from config
        timing_cfg = config.get("timing", {})
        self._click_delay: float = timing_cfg.get("click_delay_ms", 200) / 1000.0
        self._action_delay: float = timing_cfg.get("between_actions_delay_sec", 1.5)

        # Diagnostic screenshot directory
        log_dir = config.get("logging", {}).get("log_dir", "logs")
        self._diag_dir = Path(log_dir) / "gem_guard_diagnostics"
        self._diag_dir.mkdir(parents=True, exist_ok=True)

        # Baseline gem count for drift detection
        self._baseline_gems: int | None = None

        logger.info(
            "GemGuard initialized — enabled=%s, diag_dir=%s",
            self.enabled,
            self._diag_dir,
        )

    def get_screenshot(self) -> np.ndarray:
        """Capture screenshot via backend or screen helper."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None and hasattr(self.screen, "capture_screenshot"):
            return self.screen.capture_screenshot()
        raise RuntimeError("GemGuard has no backend or screen capture handle")


    # ── Public API ──────────────────────────────────────────────────────

    def check_for_gem_dialog(self) -> bool:
        """Check whether a gem purchase dialog is currently visible.

        Captures a fresh screenshot and searches for any gem purchase
        dialog templates. Saves a diagnostic screenshot if detected.

        Returns:
            True if a gem purchase dialog is detected on screen.
        """
        if not self.enabled:
            return False

        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                logger.warning("GemGuard: Failed to capture screenshot for check")
                return False

            if self.vision is not None:
                for template_name in self.DIALOG_TEMPLATES:
                    match = self.vision.find(
                        screenshot, template_name, threshold=self.DIALOG_THRESHOLD
                    )
                    if match is not None:
                        logger.warning(
                            "🚨 GEM DIALOG DETECTED via template '%s' at %s",
                            template_name,
                            match,
                        )
                        self._save_diagnostic(screenshot, f"dialog_{template_name}")
                        return True

            # OCR backup: screenshots show messages such as "You need more
            # Gold" and "Buy the missing ... Gold?" above a green gem-cost
            # control.  Search only the central dialog area to avoid normal
            # HUD gem counters creating false positives.
            for text in self.GEM_DIALOG_TEXT:
                match = self.ocr.find_text_location(
                    text, screenshot, region=(450, 180, 600, 520)  # (x, y, w, h)
                )
                if match is not None:
                    logger.warning(
                        "🚨 GEM DIALOG DETECTED via OCR phrase %r at %s", text, match
                    )
                    self._save_diagnostic(screenshot, "dialog_ocr")
                    return True

            # Also check for a standalone gem icon in the center area
            # (could indicate a gem cost on a confirm button)
            if self.vision is not None:
                gem_matches = self.vision.find_all(
                    screenshot, self.GEM_ICON_TEMPLATE, threshold=self.ICON_THRESHOLD
                )
                if gem_matches:
                    for gem_match in gem_matches:
                        # Gem icon in the center-ish area is suspicious
                        gx, gy, gw, gh = gem_match
                        center_x = gx + gw // 2
                        center_y = gy + gh // 2
                        # Only flag if the gem icon is in the dialog region (center of screen)
                        if 400 < center_x < 1520 and 200 < center_y < 880:
                            logger.warning(
                                "🚨 GEM ICON detected in dialog region at (%d, %d)",
                                center_x,
                                center_y,
                            )
                            self._save_diagnostic(screenshot, "gem_icon_in_dialog")
                            return True

            return False

        except Exception as e:
            logger.error("GemGuard check_for_gem_dialog failed: %s", e, exc_info=True)
            # On error, assume dialog might be present (fail-safe)
            return True

    def dismiss_gem_dialog(self) -> bool:
        """Dismiss a gem purchase dialog by clicking Cancel/X.

        Tries multiple dismiss strategies:
        1. Click known Cancel button templates
        2. Click X close button
        3. Press Escape key as fallback

        Returns:
            True if the dialog was successfully dismissed.
        """
        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                logger.error("GemGuard: Cannot capture screen to dismiss dialog")
                return False

            # Save diagnostic screenshot BEFORE dismissing
            self._save_diagnostic(screenshot, "before_dismiss")

            # Strategy 1: Try known dismiss button templates
            if self.vision is not None:
                for template_name in self.DISMISS_TEMPLATES:
                    match = self.vision.find(
                        screenshot, template_name, threshold=self.DIALOG_THRESHOLD
                    )
                    if match is not None:
                        x, y, w, h = match
                        click_x = x + w // 2
                        click_y = y + h // 2
                        logger.info(
                            "GemGuard: Dismissing via '%s' at (%d, %d)",
                            template_name,
                            click_x,
                            click_y,
                        )
                        # Use raw click — no gem guard recursion!
                        self._raw_click(click_x, click_y)
                        time.sleep(self._action_delay)

                        # Verify dialog is gone
                        if not self.check_for_gem_dialog():
                            logger.info("GemGuard: Dialog dismissed successfully")
                            return True

            # Strategy 2: Press Escape key as fallback
            logger.info("GemGuard: Trying Escape key to dismiss dialog")
            self._press_escape()
            time.sleep(self._action_delay)

            if not self.check_for_gem_dialog():
                logger.info("GemGuard: Dialog dismissed via Escape")
                return True

            # Strategy 3: Click outside the dialog area (top-left corner)
            logger.info("GemGuard: Trying click-outside to dismiss dialog")
            self._raw_click(0.026, 0.046)
            time.sleep(self._action_delay)

            if not self.check_for_gem_dialog():
                logger.info("GemGuard: Dialog dismissed via click-outside")
                return True

            logger.error("GemGuard: FAILED to dismiss gem dialog after all strategies!")
            self._save_diagnostic(
                self.get_screenshot(), "dismiss_failed"
            )
            return False

        except Exception as e:
            logger.error(
                "GemGuard dismiss_gem_dialog failed: %s", e, exc_info=True
            )
            return False

    def is_gem_cost(self, screenshot: np.ndarray, region: tuple[int, int, int, int]) -> bool:
        """Check if a specific region shows a gem cost (green gem icon).

        Used to verify whether a button/area is displaying a gem cost
        vs. a normal resource cost before confirming an action.

        Args:
            screenshot: Full screenshot as numpy array.
            region: (x, y, width, height) region to inspect.

        Returns:
            True if a gem icon is detected in the specified region.
        """
        if screenshot is None:
            logger.warning("Received None screenshot — blocking action for safety")
            return True
        try:
            x, y, w, h = region
            # Validate region bounds
            img_h, img_w = screenshot.shape[:2]
            if x < 0 or y < 0 or x + w > img_w or y + h > img_h:
                logger.warning(
                    "GemGuard: Region %s out of bounds for image (%d x %d)",
                    region, img_w, img_h,
                )
                return True  # Fail-safe: assume gem cost if region is invalid

            cropped = screenshot[y : y + h, x : x + w]

            # Look for gem icon template in the cropped region
            match = self.vision.find(
                cropped, self.GEM_ICON_TEMPLATE, threshold=self.ICON_THRESHOLD
            )

            if match is not None:
                logger.warning(
                    "🚨 GEM COST detected in region %s", region
                )
                self._save_diagnostic(screenshot, "gem_cost_detected")
                return True

            return False

        except Exception as e:
            logger.error("GemGuard is_gem_cost failed: %s", e, exc_info=True)
            # Fail-safe: treat errors as potential gem cost
            return True

    def verify_resource_cost(
        self, screenshot: np.ndarray, expected_type: str
    ) -> bool:
        """Verify that a confirm dialog shows the expected resource cost, not gems.

        Before confirming an upgrade, checks that the cost icon matches
        the expected resource type (gold, elixir, or dark_elixir) and
        that no gem icon is present.

        Args:
            screenshot: Full screenshot as numpy array.
            expected_type: Expected cost type — 'gold', 'elixir', or 'dark_elixir'.

        Returns:
            True if the cost display matches the expected resource type
            (no gem icon found). False if gems are shown or verification fails.
        """
        if screenshot is None:
            logger.warning("Received None screenshot — blocking action for safety")
            return False
        try:
            if expected_type not in self.RESOURCE_ICON_MAP:
                logger.error(
                    "GemGuard: Unknown resource type '%s'. "
                    "Expected one of: %s",
                    expected_type,
                    list(self.RESOURCE_ICON_MAP.keys()),
                )
                return False

            expected_template = self.RESOURCE_ICON_MAP[expected_type]

            # Search the center area of the screen where confirm dialogs appear
            # Typical confirm dialog is centered roughly (560, 240) to (1360, 840)
            dialog_region = screenshot[240:840, 560:1360]

            # Check 1: The expected resource icon SHOULD be visible
            resource_match = self.vision.find(
                dialog_region, expected_template, threshold=self.ICON_THRESHOLD
            )

            # Check 2: The gem icon should NOT be visible
            gem_match = self.vision.find(
                dialog_region, self.GEM_ICON_TEMPLATE, threshold=self.ICON_THRESHOLD
            )

            if gem_match is not None:
                logger.warning(
                    "🚨 GEMS detected in cost area! Expected '%s' but found gem icon.",
                    expected_type,
                )
                self._save_diagnostic(screenshot, f"gem_instead_of_{expected_type}")
                return False

            if resource_match is None:
                logger.warning(
                    "GemGuard: Expected resource icon '%s' not found in dialog. "
                    "Proceeding with caution — cannot confirm resource type.",
                    expected_template,
                )
                self._save_diagnostic(screenshot, f"missing_{expected_type}_icon")
                # Don't block if we simply can't find the resource icon,
                # as long as no gem icon is present either
                return False

            logger.debug(
                "GemGuard: Verified cost type '%s' — resource icon found, no gems.",
                expected_type,
            )
            return True

        except Exception as e:
            logger.error(
                "GemGuard verify_resource_cost failed: %s", e, exc_info=True
            )
            # Fail-safe: block the action on error
            return False

    def check_and_dismiss(self) -> bool:
        """Combined check + dismiss: detect gem dialog and dismiss if found.

        This is the primary convenience method used by InputController
        before and after every safe_click.

        Returns:
            True if a gem dialog was found AND dismissed.
            False if no dialog was found (i.e., all clear).
        """
        if not self.enabled:
            return False

        try:
            if self.check_for_gem_dialog():
                logger.warning("GemGuard: Gem dialog found — attempting to dismiss")
                dismissed = self.dismiss_gem_dialog()
                if dismissed:
                    logger.info("GemGuard: Successfully dismissed gem dialog")
                else:
                    logger.error(
                        "GemGuard: FAILED to dismiss gem dialog — action should be aborted"
                    )
                return True  # Dialog WAS found (regardless of dismiss success)

            return False  # No dialog found

        except Exception as e:
            logger.error(
                "GemGuard check_and_dismiss failed: %s", e, exc_info=True
            )
            # Fail-safe: report as if dialog was found
            return True

    def get_gem_count(self, screenshot: np.ndarray) -> int | None:
        """Read the current gem count from the resource bar.

        Gems are displayed at the bottom of the resource stack in the
        top-right corner of the screen. This is used to detect unexpected
        gem spending by comparing against a baseline.

        Args:
            screenshot: Full screenshot as numpy array.

        Returns:
            Current gem count as integer, or None if reading failed.
        """
        try:
            gem_region = crop_normalized(screenshot, self.GEM_COUNT_REGION_NORM)
            if gem_region.size == 0:
                logger.warning("GemGuard: Cropped gem count region has 0 size")
                return None

            gem_count = self.ocr.read_number(gem_region)

            if gem_count is not None and gem_count >= 0:
                logger.debug("GemGuard: Read gem count = %d", gem_count)
                return gem_count

            logger.warning("GemGuard: OCR returned invalid gem count: %s", gem_count)
            return None


        except Exception as e:
            logger.error(
                "GemGuard get_gem_count failed: %s", e, exc_info=True
            )
            return None

    def set_baseline_gems(self, gem_count: int) -> None:
        """Set the baseline gem count for drift detection.

        Call this at the start of each session to establish expected
        gem count. Any unexpected decrease triggers an alarm.

        Args:
            gem_count: Current gem count to use as baseline.
        """
        self._baseline_gems = gem_count
        logger.info("GemGuard: Baseline gems set to %d", gem_count)

    def check_gem_drift(self, screenshot: np.ndarray) -> bool:
        """Check if gem count has decreased from baseline.

        Args:
            screenshot: Full screenshot as numpy array.

        Returns:
            True if gems are OK (no unexpected spending).
            False if gems have decreased (EMERGENCY!).
        """
        if self._baseline_gems is None:
            logger.debug("GemGuard: No baseline gems set — skipping drift check")
            return True

        current = self.get_gem_count(screenshot)
        if current is None:
            logger.warning("GemGuard: Could not read gem count for drift check")
            return True  # Can't verify, assume OK

        if current < self._baseline_gems:
            logger.critical(
                "🚨🚨🚨 GEM SPENDING DETECTED! Baseline=%d, Current=%d, Lost=%d gems!",
                self._baseline_gems,
                current,
                self._baseline_gems - current,
            )
            self._save_diagnostic(screenshot, "GEM_SPENDING_EMERGENCY")
            return False

        if current > self._baseline_gems:
            # Gems increased (clan games reward, etc.) — update baseline
            logger.info(
                "GemGuard: Gems increased from %d to %d — updating baseline",
                self._baseline_gems,
                current,
            )
            self._baseline_gems = current

        return True

    # ── Internal helpers ────────────────────────────────────────────────

    def _save_diagnostic(
        self, screenshot: np.ndarray | None, label: str
    ) -> Path | None:
        """Save a diagnostic screenshot with a timestamped filename.

        Args:
            screenshot: Screenshot to save, or None.
            label: Short label describing the event.

        Returns:
            Path to saved file, or None if save failed.
        """
        if not self.screenshot_on_error or screenshot is None:
            return None

        try:
            import cv2

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"gem_guard_{label}_{timestamp}.png"
            filepath = self._diag_dir / filename

            cv2.imwrite(str(filepath), screenshot)
            logger.info("GemGuard: Diagnostic screenshot saved → %s", filepath)
            return filepath

        except Exception as e:
            logger.error(
                "GemGuard: Failed to save diagnostic screenshot: %s", e
            )
            return None

    def _raw_click(self, x: int | float, y: int | float) -> None:
        """Perform a raw tap/click without gem guard checks (avoids recursion).

        Parameters
        ----------
        x : int or float
            Normalized X (if float in [0, 1]) or pixel X.
        y : int or float
            Normalized Y (if float in [0, 1]) or pixel Y.
        """
        try:
            if self.backend is not None:
                if isinstance(x, float) and 0.0 <= x <= 1.0 and isinstance(y, float) and 0.0 <= y <= 1.0:
                    self.backend.tap(x, y)
                else:
                    w, h = self.backend.get_screen_size()
                    nx, ny = to_normalized(x, y, w, h)
                    self.backend.tap(nx, ny)
            time.sleep(self._click_delay)
        except Exception as e:
            logger.error("GemGuard: Raw click failed at (%s, %s): %s", x, y, e)

    def _press_escape(self) -> None:
        """Press Back / Escape action to dismiss dialogs."""
        try:
            if self.backend is not None:
                self.backend.press_back()
            time.sleep(self._click_delay)
        except Exception as e:
            logger.error("GemGuard: Escape / Back action failed: %s", e)

