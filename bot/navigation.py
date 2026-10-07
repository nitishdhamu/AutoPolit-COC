"""
Navigation — Menu and Screen Navigation
=========================================
Handles all navigation between game screens: home base, army, settings,
builder base, and dialog/popup management. Uses template matching to
identify current screen state and navigate accordingly.

Screen detection identifies:
    home, builder_base, army, attack_scout, battle, battle_results,
    settings, supercell_id, loading, disconnected, unknown
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import numpy as np

from backends.base import Backend
from bot.ocr import GameOCR
from bot.screen_detector import ScreenDetector

logger = logging.getLogger(__name__)


class Navigator:
    """Manages navigation between game screens and menus.

    Uses screen detection and OCR to detect current screen state and
    performs normalized taps to navigate to target screens.
    Handles disconnection, loading, and popup dialogs.

    Attributes:
        config: Bot configuration dictionary.
        backend: Backend instance for device communication.
        vision: Vision engine for template matching.
        ocr: OCR engine for reading text.
    """

    # ── Screen-identifying template mappings ────────────────────────────
    # Each entry maps a screen name to template(s) that uniquely identify it.
    SCREEN_TEMPLATES: dict[str, list[str]] = {
        "home": ["home_base"],
        "builder_base": ["bb_home_base"],
        "army": ["army_screen", "train_troops_header"],
        "attack_scout": ["next_btn", "scout_screen"],
        "battle": ["end_battle_btn"],
        "battle_results": ["return_home_btn", "attack_results"],
        "settings": ["settings_panel"],
        "supercell_id": ["supercell_id_panel", "switch_id_btn"],
        "loading": ["loading_screen"],
        "disconnected": ["reload_game_btn", "connection_lost"],
    }

    # Textual state markers are the fallback when a template is unavailable
    # or a game update has changed its artwork.  These are deliberately
    # screen-specific phrases, not generic UI words such as "Upgrade".
    SCREEN_OCR_TEXT: dict[str, tuple[str, ...]] = {
        "disconnected": ("reload game", "connection lost", "anyone there"),
        "battle_results": ("return home", "victory", "defeat"),
        "attack_scout": ("next", "available loot"),
        "battle": ("end battle", "surrender"),
        "supercell_id": ("switch id", "supercell id"),
        "settings": ("settings", "sound effects"),
        "army": ("train troops", "quick train"),
        "home": ("attack",),
    }
    SCREEN_OCR_REGIONS_NORM: dict[str, tuple[float, float, float, float]] = {
        "home": (0.0, 0.7870, 0.1302, 0.2130),
        "attack_scout": (0.8333, 0.7870, 0.1667, 0.2130),
        "battle": (0.0, 0.4815, 0.1354, 0.2222),
    }
    SCREEN_OCR_REGIONS: dict[str, tuple[int, int, int, int]] = {
        # The supplied Home Village screenshots place ATTACK! bottom-left.
        "home": (0, 850, 250, 230),
        "attack_scout": (1600, 850, 320, 230),
        "battle": (0, 520, 260, 240),
    }

    # ── Button positions and regions (normalized: nx, ny, nw, nh) ──────
    SETTINGS_GEAR_REGION_NORM: tuple[float, float, float, float] = (0.9479, 0.5000, 0.0417, 0.0741)
    TRAIN_BUTTON_REGION_NORM: tuple[float, float, float, float] = (0.0104, 0.5741, 0.0417, 0.0741)
    HOME_BUTTON_REGION_NORM: tuple[float, float, float, float] = (0.0104, 0.6296, 0.0417, 0.0556)
    HV_BOAT_REGION_NORM: tuple[float, float, float, float] = (0.0312, 0.6481, 0.0625, 0.0741)
    BB_BOAT_REGION_NORM: tuple[float, float, float, float] = (0.0312, 0.6481, 0.0625, 0.0741)
    BUILDER_ICON_REGION_NORM: tuple[float, float, float, float] = (0.1354, 0.0093, 0.0417, 0.0463)

    # Legacy pixel regions at 1920x1080
    SETTINGS_GEAR_REGION: tuple[int, int, int, int] = (1820, 540, 80, 80)
    TRAIN_BUTTON_REGION: tuple[int, int, int, int] = (20, 620, 80, 80)
    HOME_BUTTON_REGION: tuple[int, int, int, int] = (20, 680, 80, 60)
    HV_BOAT_REGION: tuple[int, int, int, int] = (60, 700, 120, 80)
    BB_BOAT_REGION: tuple[int, int, int, int] = (60, 700, 120, 80)
    BUILDER_ICON_REGION: tuple[int, int, int, int] = (260, 10, 80, 50)

    # ── Configuration ───────────────────────────────────────────────────
    MAX_GO_HOME_ATTEMPTS: int = 5
    MAX_DIALOG_CLOSE_ATTEMPTS: int = 5
    LOADING_TIMEOUT_SEC: int = 120
    DETECTION_THRESHOLD: float = 0.75

    def __init__(
        self,
        config: dict,
        backend_or_screen: Any,
        vision: Any = None,
        ocr: Any = None,
        input_ctrl: Any = None,
        backend: Optional[Backend] = None,
    ) -> None:
        """Initialize the Navigator.

        Args:
            config: Bot configuration dictionary.
            backend_or_screen: Backend instance (or legacy ScreenCapture).
            vision: Vision engine for template matching.
            ocr: OCR engine for reading text.
            input_ctrl: Input controller (or None if backend provided).
            backend: Explicit Backend instance.
        """
        self.config = config

        if backend is not None:
            self.backend: Optional[Backend] = backend
            self.screen: Any = backend_or_screen
        elif isinstance(backend_or_screen, Backend):
            self.backend = backend_or_screen
            self.screen = backend_or_screen
        else:
            self.backend = getattr(backend_or_screen, "backend", None)
            self.screen = backend_or_screen

        self.input_ctrl = input_ctrl or self.backend
        self.vision = vision
        self.ocr = ocr or GameOCR(config)
        self.detector = ScreenDetector(config, ocr=self.ocr)

        # Timing from config
        timing_cfg = config.get("timing", {})
        self._action_delay: float = timing_cfg.get("between_actions_delay_sec", 1.5)
        self._screen_load_wait: float = timing_cfg.get("screen_load_wait_sec", 3.0)
        self._dialog_wait: float = timing_cfg.get("dialog_animation_wait_sec", 0.8)
        self._game_load_timeout: int = config.get("emulator", {}).get(
            "game_load_timeout_sec", self.LOADING_TIMEOUT_SEC
        )

        logger.info("Navigator initialized")

    def get_screenshot(self) -> np.ndarray:
        """Capture screenshot via backend or screen helper."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None and hasattr(self.screen, "capture_screenshot"):
            return self.screen.capture_screenshot()
        raise RuntimeError("Navigator has no backend or screen capture handle")


    # ── Screen Detection ────────────────────────────────────────────────

    def detect_current_screen(self) -> str:
        """Detect which game screen is currently displayed.

        Uses multi-signal ScreenDetector (color profiles, OCR text signatures,
        frame stability) as primary detection with optional template fallback.

        Returns:
            One of: 'home', 'builder_base', 'army', 'attack_scout',
            'battle', 'battle_results', 'settings', 'supercell_id',
            'loading', 'disconnected', 'unknown'.
        """
        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                logger.warning("Navigator: Failed to capture screenshot")
                return "unknown"

            # Primary: multi-signal screen detector (template-free)
            res = self.detector.detect_screen(screenshot)
            if res.screen_name == "supercell_splash":
                return "loading"
            if res.screen_name != "unknown":
                logger.debug(
                    "Screen detected via ScreenDetector: '%s' (conf=%.2f, signals=%s)",
                    res.screen_name, res.confidence, res.matched_signals
                )
                return res.screen_name

            # Secondary fallback: legacy templates if vision engine is present
            if self.vision is not None:
                priority_order = [
                    "disconnected",
                    "loading",
                    "battle",
                    "battle_results",
                    "attack_scout",
                    "supercell_id",
                    "settings",
                    "army",
                    "builder_base",
                    "home",
                ]
                for screen_name in priority_order:
                    templates = self.SCREEN_TEMPLATES.get(screen_name, [])
                    for template_name in templates:
                        match = self.vision.find(
                            screenshot,
                            template_name,
                            threshold=self.DETECTION_THRESHOLD,
                        )
                        if match is not None:
                            logger.debug(
                                "Screen detected: '%s' (via template '%s')",
                                screen_name,
                                template_name,
                            )
                            return screen_name

            logger.warning("Navigator: Could not identify current screen")
            return "unknown"

        except Exception as e:
            logger.error(
                "Navigator detect_current_screen failed: %s", e, exc_info=True
            )
            return "unknown"

    def detect_which_village(self) -> str:
        """Determine whether we are in Home Village or Builder Base.

        Uses terrain color analysis to differentiate daytime Home Village grass
        from nocturnal Builder Base terrain.

        Returns:
            'home' if Home Village, 'builder_base' if Builder Base,
            or 'unknown' if detection fails.
        """
        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                return "unknown"

            grass_pct = self.detector.get_green_grass_percentage(screenshot)
            if grass_pct >= 2.0:
                logger.debug("Village detected: home (grass_pct=%.1f%%)", grass_pct)
                return "home"
            else:
                logger.debug("Village detected: builder_base (grass_pct=%.1f%%)", grass_pct)
                return "builder_base"

        except Exception as e:
            logger.error(
                "Navigator detect_which_village failed: %s", e, exc_info=True
            )
            return "unknown"

    # ── Navigation Commands ─────────────────────────────────────────────

    def go_home(self) -> bool:
        """Navigate to the home base screen.

        If not already on the home screen, presses Escape and taps
        outside to close dialogs, then checks for the home base template.
        Retries up to MAX_GO_HOME_ATTEMPTS times.

        Returns:
            True if we successfully reached the home screen.
        """
        try:
            for attempt in range(1, self.MAX_GO_HOME_ATTEMPTS + 1):
                current = self.detect_current_screen()

                if current == "home":
                    logger.info("Navigator: Already on home screen")
                    return True

                if current == "disconnected":
                    logger.warning("Navigator: Game disconnected — handling first")
                    self.handle_disconnected()
                    continue

                if current == "loading":
                    logger.info("Navigator: Game loading — waiting")
                    self.handle_loading_screen()
                    continue

                logger.info(
                    "Navigator: Attempting to go home (attempt %d/%d, current='%s')",
                    attempt,
                    self.MAX_GO_HOME_ATTEMPTS,
                    current,
                )

                # Press Escape to close any open dialogs/menus
                self.input_ctrl.press_key("escape")
                time.sleep(self._action_delay)

                # Check again
                if self.detect_current_screen() == "home":
                    logger.info("Navigator: Reached home screen via Escape")
                    return True

                # Try clicking outside dialogs (top-left safe area)
                self.input_ctrl.click(50, 50)
                time.sleep(self._action_delay)

            # Final check
            if self.detect_current_screen() == "home":
                return True

            logger.error(
                "Navigator: Failed to reach home screen after %d attempts",
                self.MAX_GO_HOME_ATTEMPTS,
            )
            return False

        except Exception as e:
            logger.error("Navigator go_home failed: %s", e, exc_info=True)
            return False

    def go_to_army(self) -> bool:
        """Navigate to the army training screen.

        Taps the 'Train' button on the right side of the screen and
        verifies the army screen appears.

        Returns:
            True if the army screen was successfully opened.
        """
        try:
            # Make sure we're on the home screen first
            current = self.detect_current_screen()
            if current == "army":
                logger.info("Navigator: Already on army screen")
                return True

            if current != "home":
                logger.info(
                    "Navigator: Not on home screen (current='%s'), going home first",
                    current,
                )
                if not self.go_home():
                    return False

            # Resolve the control through the common template/OCR/viewport
            # strategy.  We are already on the verified home screen.
            screenshot = self.screen.capture_screenshot()
            target = self.ui.find("train_btn", screenshot)
            if target is None:
                logger.error("Navigator: Could not locate Train button")
                return False
            self.input_ctrl.safe_click(*target)
            time.sleep(self._screen_load_wait)

            if self.detect_current_screen() == "army":
                logger.info("Navigator: Army screen opened")
                return True

            logger.error("Navigator: Failed to open army screen")
            return False

        except Exception as e:
            logger.error("Navigator go_to_army failed: %s", e, exc_info=True)
            return False

    def go_to_settings(self) -> bool:
        """Open the settings panel.

        Taps the settings gear icon on the right side of the screen.

        Returns:
            True if the settings panel was successfully opened.
        """
        try:
            current = self.detect_current_screen()
            if current == "settings":
                logger.info("Navigator: Already on settings screen")
                return True

            if current != "home":
                if not self.go_home():
                    return False

            # We are on the verified home screen, so a viewport-scaled fixed
            # coordinate is an acceptable final fallback for the gear.
            screenshot = self.screen.capture_screenshot()
            target = self.ui.find("settings_gear", screenshot)
            if target is None:
                logger.error("Navigator: Could not locate settings gear")
                return False
            self.input_ctrl.safe_click(*target)
            time.sleep(self._screen_load_wait)

            if self.detect_current_screen() == "settings":
                logger.info("Navigator: Settings panel opened")
                return True

            logger.error("Navigator: Failed to open settings panel")
            return False

        except Exception as e:
            logger.error("Navigator go_to_settings failed: %s", e, exc_info=True)
            return False

    def close_all_dialogs(self) -> bool:
        """Close all open dialogs and popups.

        Presses Escape, then looks for any remaining X/close buttons
        and clicks them. Repeats up to MAX_DIALOG_CLOSE_ATTEMPTS times.

        Returns:
            True if all dialogs were closed (or none were open).
        """
        try:
            for attempt in range(self.MAX_DIALOG_CLOSE_ATTEMPTS):
                # Press Escape first
                self.input_ctrl.press_key("escape")
                time.sleep(self._dialog_wait)

                # Check for any X / close buttons still visible
                screenshot = self.screen.capture_screenshot()
                if screenshot is None:
                    break

                x_match = None
                if self.vision is not None:
                    x_match = self.vision.find(
                        screenshot, "dialog_close_x", threshold=self.DETECTION_THRESHOLD
                    )
                    if x_match is None:
                        # Also try cancel button
                        x_match = self.vision.find(
                            screenshot, "cancel_btn", threshold=self.DETECTION_THRESHOLD
                        )

                if x_match is None:
                    logger.debug(
                        "Navigator: No more dialogs found (after %d iterations)",
                        attempt + 1,
                    )
                    return True

                # Click the close button
                x, y, w, h = x_match
                self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                time.sleep(self._dialog_wait)

            logger.info("Navigator: close_all_dialogs completed")
            return True

        except Exception as e:
            logger.error(
                "Navigator close_all_dialogs failed: %s", e, exc_info=True
            )
            return False

    def handle_popup(self) -> bool:
        """Generic popup handler: look for X buttons, close buttons.

        Detects and dismisses any unexpected popup (clan mail, offers,
        event notifications, etc.) by finding and clicking close buttons.

        Returns:
            True if a popup was found and dismissed, or no popup present.
        """
        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                return False

            # Look for various close button templates if vision is active
            if self.vision is not None:
                close_templates = ["dialog_close_x", "cancel_btn", "okay_btn"]
                for template_name in close_templates:
                    match = self.vision.find(
                        screenshot, template_name, threshold=self.DETECTION_THRESHOLD
                    )
                    if match is not None:
                        x, y, w, h = match
                        logger.info(
                            "Navigator: Popup detected — dismissing via '%s'",
                            template_name,
                        )
                        self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                        time.sleep(self._dialog_wait)
                        return True

            logger.debug("Navigator: No popup detected")
            return True

        except Exception as e:
            logger.error("Navigator handle_popup failed: %s", e, exc_info=True)
            return False

    # ── Disconnection & Loading ─────────────────────────────────────────

    def handle_disconnected(self) -> bool:
        """Handle 'Anyone there?' or 'RELOAD GAME' disconnection dialogs.

        Detects the disconnection dialog and taps the RELOAD GAME button
        to reconnect, then waits for the game to load.

        Returns:
            True if reconnection was successful.
        """
        try:
            screenshot = self.get_screenshot()
            if screenshot is None:
                return False

            if self.detector.is_disconnected(screenshot):
                logger.info("Navigator: Disconnection detected, tapping reload")
                loc = self.ocr.find_text_location("reload game", screenshot)
                if loc is None:
                    loc = self.ocr.find_text_location("try again", screenshot)

                if loc is not None:
                    x, y, w, h = loc
                    self.safe_tap((x + w / 2) / screenshot.shape[1], (y + h / 2) / screenshot.shape[0])
                else:
                    # Standard center reload button coordinate
                    self.safe_tap(0.50, 0.60)

                time.sleep(self._action_delay)
                return self.handle_loading_screen()

            logger.warning("Navigator: No disconnect dialog found to handle")
            return False

        except Exception as e:
            logger.error(
                "Navigator handle_disconnected failed: %s", e, exc_info=True
            )
            return False

    def handle_loading_screen(self) -> bool:
        """Wait for the game loading screen to disappear.

        Polls until the loading screen template is no longer visible,
        up to the configured timeout (default 120 seconds).

        Returns:
            True if loading completed within the timeout.
        """
        try:
            timeout = self._game_load_timeout
            start_time = time.time()
            poll_interval = 3.0  # Check every 3 seconds

            logger.info(
                "Navigator: Waiting for loading screen to complete (timeout=%ds)",
                timeout,
            )

            while time.time() - start_time < timeout:
                screenshot = self.screen.capture_screenshot()
                if screenshot is None:
                    time.sleep(poll_interval)
                    continue

                # Check if loading screen or splash is still visible
                is_loading = self.detector.is_loading_or_splash(screenshot)
                if not is_loading and self.vision is not None:
                    is_loading = (
                        self.vision.find(
                            screenshot, "loading_screen", threshold=self.DETECTION_THRESHOLD
                        ) is not None
                    )

                if not is_loading:
                    # Loading screen gone — check if we're on a known screen
                    elapsed = time.time() - start_time
                    logger.info(
                        "Navigator: Loading completed after %.1fs", elapsed
                    )

                    # Give the game a moment to fully render
                    time.sleep(self._screen_load_wait)

                    # Handle any popups that appear after loading
                    self.handle_popup()
                    return True

                elapsed = time.time() - start_time
                logger.debug(
                    "Navigator: Still loading... (%.1fs / %ds)",
                    elapsed,
                    timeout,
                )
                time.sleep(poll_interval)

            logger.error(
                "Navigator: Loading screen did not complete within %ds timeout",
                timeout,
            )
            return False

        except Exception as e:
            logger.error(
                "Navigator handle_loading_screen failed: %s", e, exc_info=True
            )
            return False

    # ── Village Switching ───────────────────────────────────────────────

    def switch_to_builder_base(self) -> bool:
        """Switch from Home Village to Builder Base.

        Finds and taps the boat button on the right side of the Home
        Village, then verifies the Builder Base home screen loads.

        Returns:
            True if Builder Base was reached successfully.
        """
        try:
            current_village = self.detect_which_village()
            if current_village == "builder_base":
                logger.info("Navigator: Already in Builder Base")
                return True

            # Ensure we're on the home screen first
            if self.detect_current_screen() != "home":
                if not self.go_home():
                    return False

            # Find the boat button via template if vision is active
            if self.vision is not None:
                screenshot = self.get_screenshot()
                if screenshot is not None:
                    match = self.vision.find(
                        screenshot, "bb_boat_btn", threshold=self.DETECTION_THRESHOLD
                    )
                    if match is not None:
                        x, y, w, h = match
                        logger.info("Navigator: Tapping boat to switch to Builder Base")
                        self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                        time.sleep(self._screen_load_wait)

                        # Wait for loading/transition
                        self._wait_for_screen_transition("builder_base", timeout=30)

                        if self.detect_which_village() == "builder_base":
                            logger.info("Navigator: Successfully switched to Builder Base")
                            self.handle_popup()
                            return True

            # Fallback: click the boat region
            logger.info("Navigator: Falling back to region-based boat click")
            rx, ry, rw, rh = self.HV_BOAT_REGION
            self.input_ctrl.safe_click(rx + rw // 2, ry + rh // 2)
            time.sleep(self._screen_load_wait * 2)

            if self.detect_which_village() == "builder_base":
                logger.info("Navigator: Switched to Builder Base (via fallback)")
                self.handle_popup()
                return True

            logger.error("Navigator: Failed to switch to Builder Base")
            return False

        except Exception as e:
            logger.error(
                "Navigator switch_to_builder_base failed: %s", e, exc_info=True
            )
            return False

    def switch_to_home_village(self) -> bool:
        """Switch from Builder Base back to Home Village.

        Finds and taps the boat in Builder Base to return to HV,
        then verifies the Home Village home screen loads.

        Returns:
            True if Home Village was reached successfully.
        """
        try:
            current_village = self.detect_which_village()
            if current_village == "home":
                logger.info("Navigator: Already in Home Village")
                return True

            # Ensure we're on BB home screen
            current_screen = self.detect_current_screen()
            if current_screen not in ("builder_base", "home"):
                self.close_all_dialogs()
                time.sleep(self._action_delay)

            # Find the HV boat button in Builder Base
            if self.vision is not None:
                screenshot = self.get_screenshot()
                if screenshot is not None:
                    match = self.vision.find(
                        screenshot,
                        "bb_home_village_btn",
                        threshold=self.DETECTION_THRESHOLD,
                    )
                    if match is not None:
                        x, y, w, h = match
                        logger.info("Navigator: Tapping boat to switch to Home Village")
                        self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                        time.sleep(self._screen_load_wait)

                        self._wait_for_screen_transition("home", timeout=30)

                        if self.detect_which_village() == "home":
                            logger.info(
                                "Navigator: Successfully switched to Home Village"
                            )
                            self.handle_popup()
                            return True

            # Fallback: click the boat region
            logger.info("Navigator: Falling back to region-based boat click")
            rx, ry, rw, rh = self.BB_BOAT_REGION
            self.input_ctrl.safe_click(rx + rw // 2, ry + rh // 2)
            time.sleep(self._screen_load_wait * 2)

            if self.detect_which_village() == "home":
                logger.info("Navigator: Switched to Home Village (via fallback)")
                self.handle_popup()
                return True

            logger.error("Navigator: Failed to switch to Home Village")
            return False

        except Exception as e:
            logger.error(
                "Navigator switch_to_home_village failed: %s", e, exc_info=True
            )
            return False

    # ── Builder Menu ────────────────────────────────────────────────────

    def open_builder_menu(self) -> bool:
        """Open the builder menu by tapping the builder/hammer icon.

        The builder icon is located near the top of the screen, showing
        the builder count (e.g., "2/5").

        Returns:
            True if the builder menu was opened.
        """
        try:
            # Ensure we're on the home screen
            if self.detect_current_screen() not in ("home", "builder_base"):
                if not self.go_home():
                    return False

            # Template-based click
            if self.vision is not None:
                screenshot = self.get_screenshot()
                if screenshot is not None:
                    match = self.vision.find(
                        screenshot, "builder_icon", threshold=self.DETECTION_THRESHOLD
                    )
                    if match is not None:
                        x, y, w, h = match
                        self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                        time.sleep(self._screen_load_wait)
                        logger.info("Navigator: Builder menu opened")
                        return True

            # Fallback: click builder region
            logger.info("Navigator: Falling back to region-based builder click")
            rx, ry, rw, rh = self.BUILDER_ICON_REGION
            self.input_ctrl.safe_click(rx + rw // 2, ry + rh // 2)
            time.sleep(self._screen_load_wait)

            logger.info("Navigator: Builder menu opened (via fallback)")
            return True

        except Exception as e:
            logger.error(
                "Navigator open_builder_menu failed: %s", e, exc_info=True
            )
            return False

    # ── Internal helpers ────────────────────────────────────────────────

    def _wait_for_screen_transition(
        self, target_screen: str, timeout: int = 30
    ) -> bool:
        """Wait until a specific screen is detected or timeout expires.

        Args:
            target_screen: The screen name to wait for.
            timeout: Maximum seconds to wait.

        Returns:
            True if the target screen was detected within timeout.
        """
        start_time = time.time()
        poll_interval = 2.0

        while time.time() - start_time < timeout:
            # Handle loading screens transparently
            current = self.detect_current_screen()
            if current == target_screen:
                return True
            if current == "loading":
                time.sleep(poll_interval)
                continue
            if current == "disconnected":
                self.handle_disconnected()
                continue

            time.sleep(poll_interval)

        logger.warning(
            "Navigator: Timed out waiting for screen '%s' after %ds",
            target_screen,
            timeout,
        )
        return False

    # ── Army & Super Troops navigation ──────────────────────────────────

    def go_to_super_troops(self) -> bool:
        """Navigate to the Super Troops selection screen.

        Opens the army training screen, then looks for the Super Troops
        tab/button and taps it.

        Returns:
            True if the Super Troops screen was successfully opened.
        """
        try:
            # First navigate to the army screen
            if not self.go_to_army():
                logger.error("Navigator: Could not reach army screen for super troops")
                return False

            time.sleep(self._screen_load_wait)

            # Look for Super Troops tab button
            screenshot = self.get_screenshot()
            if screenshot is not None and self.vision is not None:
                match = self.vision.find(
                    screenshot, "super_troops_tab", threshold=self.DETECTION_THRESHOLD
                )
                if match is not None:
                    x, y, w, h = match
                    self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                    time.sleep(self._screen_load_wait)
                    logger.info("Navigator: Super Troops tab opened")
                    return True

            # Fallback: try common Super Troops tab positions
            super_troop_positions = [
                (300, 200),   # Left tab area
                (400, 180),
                (500, 180),
            ]
            for sx, sy in super_troop_positions:
                self.input_ctrl.safe_click(sx, sy)
                time.sleep(self._dialog_wait)

                screenshot = self.get_screenshot()
                if screenshot is not None and self.vision is not None:
                    match = self.vision.find(
                        screenshot, "super_troops_panel", threshold=0.70
                    )
                    if match is not None:
                        logger.info(
                            "Navigator: Super Troops panel detected at (%d, %d)", sx, sy
                        )
                        return True

            logger.warning("Navigator: Could not open Super Troops screen")
            return False

        except Exception as e:
            logger.error("Navigator go_to_super_troops failed: %s", e, exc_info=True)
            return False

    def go_to_lab(self) -> bool:
        """Navigate to and open the Laboratory building.

        Taps the Lab building on the home base. Falls back to template
        matching or OCR for the lab icon/text.

        Returns:
            True if the Laboratory dialog was opened.
        """
        try:
            # Ensure we're on the home screen
            if not self.go_home():
                return False

            time.sleep(self._action_delay)

            # Try template-based Lab tap
            screenshot = self.get_screenshot()
            if screenshot is not None and self.vision is not None:
                match = self.vision.find(
                    screenshot, "lab_building", threshold=self.DETECTION_THRESHOLD
                )
                if match is not None:
                    x, y, w, h = match
                    self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                    time.sleep(self._screen_load_wait)

                    # Verify lab dialog appeared via OCR or template
                    screenshot2 = self.get_screenshot()
                    if screenshot2 is not None:
                        if self.ocr.find_text_location("research", screenshot2) is not None:
                            logger.info("Navigator: Lab opened (verified via OCR)")
                            return True
                        if self.vision is not None and self.vision.find(screenshot2, "lab_ui", threshold=0.70) is not None:
                            logger.info("Navigator: Lab building opened (via template)")
                            return True

            # Fallback: approximate lab position
            logger.info("Navigator: Using fallback Lab position")
            self.input_ctrl.safe_click(1050, 450)
            time.sleep(self._screen_load_wait)

            screenshot = self.get_screenshot()
            if screenshot is not None:
                if self.ocr.find_text_location("research", screenshot) is not None:
                    logger.info("Navigator: Lab opened via fallback (verified via OCR)")
                    return True
                if self.vision is not None and self.vision.find(screenshot, "lab_ui", threshold=0.65) is not None:
                    logger.info("Navigator: Lab opened via fallback (via template)")
                    return True

            logger.warning("Navigator: Could not open Lab building")
            return False

        except Exception as e:
            logger.error("Navigator go_to_lab failed: %s", e, exc_info=True)
            return False
