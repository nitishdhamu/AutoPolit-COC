"""
Army Manager — Troop training and Super Dragon management.
==========================================================
Handles the full army lifecycle: checking/activating Super Dragon boost,
selecting troop type based on Dark Elixir, training troops and spells,
and waiting for the army to become ready for attack.

Keyboard shortcuts (Q/W/E/A/S/D) are used during attacks — this module
handles the training-screen UI interactions only.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import numpy as np

from backends.base import Backend
from bot.ocr import GameOCR
from bot.navigation import Navigator

logger = logging.getLogger(__name__)


class ArmyManager:
    """Manages troop training, Super Dragon activation, and army readiness."""

    # ── Normalized screen coordinate constants (0.0 - 1.0) ──────────────
    ARMY_TRAIN_BUTTON_POS_NORM: tuple[float, float] = (0.0188, 0.5926)
    TROOP_TAB_POS_NORM: tuple[float, float] = (0.1979, 0.1667)
    SPELL_TAB_POS_NORM: tuple[float, float] = (0.2812, 0.1667)
    TRAIN_CLOSE_POS_NORM: tuple[float, float] = (0.5833, 0.1204)
    SUPER_TROOP_BARREL_POS_NORM: tuple[float, float] = (0.1250, 0.7130)
    SUPER_DRAGON_ICON_POS_NORM: tuple[float, float] = (0.4688, 0.5556)
    ACTIVATE_SUPER_DRAGON_BTN_POS_NORM: tuple[float, float] = (0.5000, 0.6019)
    BOOST_CONFIRM_BTN_POS_NORM: tuple[float, float] = (0.5000, 0.5370)
    ARMY_CAPACITY_REGION_NORM: tuple[float, float, float, float] = (0.4062, 0.1204, 0.0833, 0.0278)
    TROOP_GRID_REGION_NORM: tuple[float, float, float, float] = (0.1458, 0.2037, 0.5729, 0.6481)
    SUPER_DRAGON_ACTIVE_REGION_NORM: tuple[float, float, float, float] = (0.1458, 0.1852, 0.5729, 0.6481)

    # Legacy pixel coordinates at 1920×1080
    ARMY_TRAIN_BUTTON_POS: tuple[int, int] = (36, 640)
    TROOP_TAB_POS: tuple[int, int] = (380, 180)
    SPELL_TAB_POS: tuple[int, int] = (540, 180)
    TRAIN_CLOSE_POS: tuple[int, int] = (1120, 130)
    SUPER_TROOP_BARREL_POS: tuple[int, int] = (240, 770)
    SUPER_DRAGON_ICON_POS: tuple[int, int] = (900, 600)
    ACTIVATE_SUPER_DRAGON_BTN_POS: tuple[int, int] = (960, 650)
    BOOST_CONFIRM_BTN_POS: tuple[int, int] = (960, 580)
    ARMY_CAPACITY_REGION: tuple[int, int, int, int] = (780, 130, 160, 30)
    TROOP_GRID_REGION: tuple[int, int, int, int] = (280, 220, 1100, 700)
    SUPER_DRAGON_ACTIVE_REGION: tuple[int, int, int, int] = (280, 200, 1100, 700)

    # DE cost for super dragon activation
    SUPER_DRAGON_DE_COST: int = 25000

    # Number of clicks to fill army camps with the main troop
    MAX_TROOP_FILL_CLICKS: int = 20

    # Number of earthquake spell clicks
    EQ_SPELL_COUNT: int = 5

    # Poll interval for army readiness check (seconds)
    ARMY_POLL_INTERVAL: float = 30.0

    def __init__(
        self,
        config: dict,
        backend_or_screen: Any,
        vision: Any = None,
        ocr: Any = None,
        input_ctrl: Any = None,
        navigator: Optional[Navigator] = None,
        backend: Optional[Backend] = None,
    ) -> None:
        """Initialise ArmyManager.

        Args:
            config: Bot configuration dictionary.
            backend_or_screen: Backend instance (or legacy ScreenCapture).
            vision: OpenCV template matching engine.
            ocr: Tesseract OCR engine for game text.
            input_ctrl: Mouse/keyboard input controller with gem guard.
            navigator: Game menu navigation helper.
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
        self.navigator = navigator or Navigator(config, self.backend or self.screen, vision, self.ocr)

        # Config-driven values
        army_cfg = config.get("army", {})
        self._preferred_troop: str = army_cfg.get("preferred_troop", "super_dragon")
        self._fallback_troop: str = army_cfg.get("fallback_troop", "dragon")
        self._de_cost: int = army_cfg.get("super_dragon_de_cost", self.SUPER_DRAGON_DE_COST)

        timing_cfg = config.get("timing", {})
        self._action_delay: float = timing_cfg.get("between_actions_delay_sec", 1.5)
        self._dialog_wait: float = timing_cfg.get("dialog_animation_wait_sec", 0.8)
        self._screen_load_wait: float = timing_cfg.get("screen_load_wait_sec", 3.0)

        logger.info(
            "ArmyManager initialised (preferred=%s, fallback=%s).",
            self._preferred_troop,
            self._fallback_troop,
        )

    def get_screenshot(self) -> np.ndarray:
        """Capture screenshot via backend or screen helper."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None and hasattr(self.screen, "capture_screenshot"):
            return self.screen.capture_screenshot()
        raise RuntimeError("ArmyManager has no backend or screen capture handle")


    # ── Super Dragon management ─────────────────────────────────────────

    def is_super_dragon_active(self) -> bool:
        """Check if the Super Dragon boost is currently active.

        Looks for the Super Dragon icon with an active/boosted indicator
        in the army screen or the super troop menu.

        Returns:
            True if Super Dragon is currently boosted.
        """
        logger.info("Checking if Super Dragon is active…")
        try:
            # Navigate to army screen to check
            self.navigator.go_to_army()
            time.sleep(self._screen_load_wait)

            screenshot = self.screen.capture_screenshot()

            # Look for active super dragon indicator
            result = self.vision.find(
                screenshot, "super_dragon_active", threshold=0.75
            )
            if result is not None:
                logger.info("✅ Super Dragon boost is ACTIVE.")
                self.navigator.close_all_dialogs()
                return True

            # Alternative: check the troop list for super dragon icon
            result = self.vision.find(
                screenshot, "super_dragon_icon", threshold=0.75
            )
            if result is not None:
                logger.info("Super Dragon icon found in army — boost is active.")
                self.navigator.close_all_dialogs()
                return True

            logger.info("Super Dragon boost is NOT active.")
            self.navigator.close_all_dialogs()
            return False

        except Exception as exc:
            logger.error("Error checking super dragon status: %s", exc, exc_info=True)
            return False

    def activate_super_dragon(self) -> bool:
        """Activate Super Dragon boost by spending 25,000 Dark Elixir.

        Workflow:
        1. Navigate to the Super Troop barrel/menu.
        2. Find 'Super Dragon' on page 1 (bottom-right).
        3. Tap it.
        4. On the activation screen, tap 'Activate 25,000'.
        5. Confirm on the 'Boost?' dialog (tap the green 25000 DE button).
        6. Verify activation succeeded.

        Returns:
            True if Super Dragon was successfully activated.
        """
        logger.info("Attempting to activate Super Dragon (cost: %d DE)…", self._de_cost)
        try:
            # Step 1: go to super troop menu
            self.navigator.go_to_super_troops()
            time.sleep(self._screen_load_wait)

            # Step 2: find Super Dragon on page 1 (bottom-right)
            screenshot = self.screen.capture_screenshot()
            sd_match = self.vision.find(
                screenshot, "super_dragon_icon", threshold=0.75
            )

            if sd_match is not None:
                sx, sy, sw, sh = sd_match
                tap_x = sx + sw // 2
                tap_y = sy + sh // 2
                logger.info("Found Super Dragon icon at (%d, %d) — tapping.", tap_x, tap_y)
            else:
                # Fallback: use the expected position
                tap_x, tap_y = self.SUPER_DRAGON_ICON_POS
                logger.info(
                    "Super Dragon icon not found by template — using expected pos (%d, %d).",
                    tap_x, tap_y,
                )

            # Step 3: tap Super Dragon
            self.input_ctrl.safe_click(tap_x, tap_y)
            time.sleep(self._dialog_wait)

            # Step 4: tap "Activate 25,000" button
            logger.info("Tapping 'Activate 25,000' button…")
            self.input_ctrl.safe_click(*self.ACTIVATE_SUPER_DRAGON_BTN_POS)
            time.sleep(self._dialog_wait)

            # Step 5: confirm on "Boost?" dialog
            screenshot = self.screen.capture_screenshot()
            boost_btn = self.vision.find(
                screenshot, "boost_confirm_btn", threshold=0.7
            )
            if boost_btn is not None:
                bx, by, bw, bh = boost_btn
                confirm_x = bx + bw // 2
                confirm_y = by + bh // 2
            else:
                confirm_x, confirm_y = self.BOOST_CONFIRM_BTN_POS

            # Check for gem purchase dialog before confirming
            gem_dialog = self.vision.find(
                screenshot, "gem_purchase_dialog", threshold=0.75
            )
            if gem_dialog is not None:
                logger.warning(
                    "🚨 GEM COST detected on Super Dragon activation — CANCELLING!"
                )
                self.input_ctrl.safe_click(960, 700)  # Cancel
                time.sleep(self._action_delay)
                self.navigator.close_all_dialogs()
                return False

            logger.info("Confirming Super Dragon boost…")
            self.input_ctrl.safe_click(confirm_x, confirm_y)
            time.sleep(self._screen_load_wait)

            # Step 6: verify activation
            is_active = self.is_super_dragon_active()
            if is_active:
                logger.info("✅ Super Dragon activated successfully!")
            else:
                logger.warning("Super Dragon activation may have failed — could not verify.")

            self.navigator.close_all_dialogs()
            return is_active

        except Exception as exc:
            logger.error("Error activating Super Dragon: %s", exc, exc_info=True)
            self.navigator.close_all_dialogs()
            return False

    def can_afford_super_dragon(self, current_de: int) -> bool:
        """Check if current Dark Elixir is enough to activate Super Dragon.

        Args:
            current_de: Current Dark Elixir amount.

        Returns:
            True if DE >= 25,000 (or the configured cost).
        """
        can_afford = current_de >= self._de_cost
        logger.info(
            "Can afford Super Dragon: %s (DE=%d, need=%d)",
            can_afford, current_de, self._de_cost,
        )
        return can_afford

    def get_troop_type(self, current_de: int, is_boosted: bool) -> str:
        """Determine which troop type to use for the next attack.

        Priority:
        1. If already boosted → 'super_dragon'
        2. If can afford boost → 'super_dragon' (will activate later)
        3. Otherwise → 'dragon' (fallback)

        Args:
            current_de: Current Dark Elixir amount.
            is_boosted: Whether Super Dragon is currently active.

        Returns:
            Troop type string: 'super_dragon' or 'dragon'.
        """
        if is_boosted:
            troop_type = self._preferred_troop
            logger.info("Troop type: %s (boost active).", troop_type)
        elif self.can_afford_super_dragon(current_de):
            troop_type = self._preferred_troop
            logger.info("Troop type: %s (can afford boost).", troop_type)
        else:
            troop_type = self._fallback_troop
            logger.info(
                "Troop type: %s (DE too low for super dragon).", troop_type
            )
        return troop_type

    # ── Army training ───────────────────────────────────────────────────

    def train_army(self, troop_type: str) -> bool:
        """Train a full army of the given troop type plus earthquake spells.

        Workflow:
        1. Open the army training screen.
        2. Navigate to the troop tab.
        3. Find the correct troop icon (super_dragon or dragon).
        4. Tap it repeatedly to fill all army camps.
        5. Switch to the spell tab.
        6. Find earthquake spell and tap 5 times.
        7. Close the training screen.

        Args:
            troop_type: Either 'super_dragon' or 'dragon'.

        Returns:
            True if training was initiated successfully.
        """
        logger.info("Training army: %s + 5x Earthquake…", troop_type)
        try:
            # Step 1: open army training screen
            self.navigator.go_to_army()
            time.sleep(self._screen_load_wait)

            # Step 2: tap troop tab
            self.input_ctrl.safe_click(*self.TROOP_TAB_POS)
            time.sleep(self._dialog_wait)

            # Step 3: find the troop icon
            template_name = (
                "super_dragon_icon" if troop_type == "super_dragon"
                else "dragon_icon"
            )
            screenshot = self.screen.capture_screenshot()
            troop_match = self.vision.find(screenshot, template_name, threshold=0.75)

            if troop_match is not None:
                tx, ty, tw, th = troop_match
                troop_tap_x = tx + tw // 2
                troop_tap_y = ty + th // 2
                logger.info("Found %s icon at (%d, %d).", template_name, troop_tap_x, troop_tap_y)
            else:
                logger.warning(
                    "Could not find %s icon by template — cannot train.", template_name
                )
                self.navigator.close_all_dialogs()
                return False

            # Step 4: tap troop icon repeatedly to fill camps
            logger.info("Filling camps with %s (%d clicks max)…",
                        troop_type, self.MAX_TROOP_FILL_CLICKS)
            for i in range(self.MAX_TROOP_FILL_CLICKS):
                self.input_ctrl.safe_click(troop_tap_x, troop_tap_y, checks=False)
                time.sleep(0.15)

            time.sleep(self._action_delay)

            # Step 5: switch to spell tab
            logger.info("Switching to spell tab…")
            self.input_ctrl.safe_click(*self.SPELL_TAB_POS)
            time.sleep(self._dialog_wait)

            # Step 6: find earthquake spell and tap 5 times
            screenshot = self.screen.capture_screenshot()
            eq_match = self.vision.find(screenshot, "earthquake_spell_icon", threshold=0.75)

            if eq_match is not None:
                ex, ey, ew, eh = eq_match
                spell_tap_x = ex + ew // 2
                spell_tap_y = ey + eh // 2
                logger.info("Found Earthquake Spell at (%d, %d).", spell_tap_x, spell_tap_y)
            else:
                logger.warning("Could not find Earthquake Spell icon — skipping spells.")
                self.navigator.close_all_dialogs()
                return True  # Troops trained, just no spells

            for i in range(self.EQ_SPELL_COUNT):
                self.input_ctrl.safe_click(spell_tap_x, spell_tap_y, checks=False)
                time.sleep(0.2)

            time.sleep(self._action_delay)

            # Step 7: close training screen
            logger.info("Closing training screen…")
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            logger.info("✅ Army training initiated: %s + 5x EQ", troop_type)
            return True

        except Exception as exc:
            logger.error("Error training army: %s", exc, exc_info=True)
            self.navigator.close_all_dialogs()
            return False

    def is_army_ready(self) -> bool:
        """Check if army camps are full and ready for attack.

        Opens the army tab briefly and reads the capacity display
        (e.g. '300/300').  If current equals max, the army is ready.

        Returns:
            True if the army is fully trained.
        """
        logger.info("Checking army readiness…")
        try:
            # Open army overview
            self.navigator.go_to_army()
            time.sleep(self._screen_load_wait)

            # Read capacity text (e.g. "300/300")
            cap_img = self.screen.capture_region(*self.ARMY_CAPACITY_REGION)
            cap_text = self.ocr.read_text(cap_img).strip()
            logger.debug("Army capacity text: '%s'", cap_text)

            self.navigator.close_all_dialogs()

            if "/" in cap_text:
                parts = cap_text.split("/")
                try:
                    current = int(parts[0].strip())
                    maximum = int(parts[1].strip())
                    is_ready = current >= maximum
                    logger.info(
                        "Army capacity: %d/%d — %s",
                        current, maximum,
                        "READY" if is_ready else "not ready",
                    )
                    return is_ready
                except ValueError:
                    logger.warning("Could not parse army capacity: '%s'", cap_text)
                    return False

            logger.warning("Army capacity format unexpected: '%s'", cap_text)
            return False

        except Exception as exc:
            logger.error("Error checking army readiness: %s", exc, exc_info=True)
            return False

    def wait_for_army(self, timeout: int = 600) -> bool:
        """Poll until the army is fully trained or timeout.

        Args:
            timeout: Maximum wait time in seconds (default 10 minutes).

        Returns:
            True if the army became ready within the timeout.
        """
        logger.info("Waiting for army to train (timeout=%ds)…", timeout)
        start = time.time()

        while time.time() - start < timeout:
            if self.is_army_ready():
                logger.info("✅ Army is ready!")
                return True

            elapsed = int(time.time() - start)
            remaining = timeout - elapsed
            logger.info(
                "Army not ready yet — waiting %ds (elapsed=%ds, remaining=%ds)…",
                int(self.ARMY_POLL_INTERVAL), elapsed, remaining,
            )
            time.sleep(self.ARMY_POLL_INTERVAL)

        logger.warning("⏰ Army wait timed out after %ds.", timeout)
        return False
