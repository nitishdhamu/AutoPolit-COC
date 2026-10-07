"""
Attack Engine — Target finding, troop deployment, and farming loop.
===================================================================
Implements the core combat logic: finding targets that meet loot thresholds,
executing attacks with keyboard-shortcut troop/spell deployment, reading
battle results, and running the full farm-until-storages-full loop.

Troops are deployed using keyboard shortcuts (Q/W/E/A/S/D) mapped to the
bottom bar slots.  Earthquake spells are deployed SPREAD around the
centre of the enemy base (not clustered in one spot).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import numpy as np

from backends.base import Backend
from bot.ocr import GameOCR
from bot.navigation import Navigator
from bot.resource_reader import ResourceReader
from bot.army_manager import ArmyManager

logger = logging.getLogger(__name__)


class AttackEngine:
    """Handles target scouting, troop deployment, and the farming loop."""

    # ── Normalized Screen Coordinate Constants (0.0 - 1.0) ──────────────
    ATTACK_BUTTON_POS_NORM: tuple[float, float] = (0.0417, 0.8333)
    FIND_MATCH_BUTTON_POS_NORM: tuple[float, float] = (0.1302, 0.3704)
    NEXT_BUTTON_POS_NORM: tuple[float, float] = (0.9323, 0.9167)
    RETURN_HOME_BUTTON_POS_NORM: tuple[float, float] = (0.5000, 0.7870)
    END_BATTLE_BUTTON_POS_NORM: tuple[float, float] = (0.0521, 0.6019)

    EDGE_DEPLOY_POINTS_NORM: list[tuple[float, float]] = [
        (0.0938, 0.5000),
        (0.1458, 0.4074),
        (0.1979, 0.3519),
        (0.2500, 0.3148),
        (0.3021, 0.2870),
        (0.3542, 0.2685),
        (0.4062, 0.2593),
        (0.4583, 0.2685),
        (0.5104, 0.2870),
        (0.5625, 0.3148),
    ]

    CENTER_SPELL_POINTS_NORM: list[tuple[float, float]] = [
        (0.5000, 0.5000),
        (0.4479, 0.4444),
        (0.5521, 0.4444),
        (0.4479, 0.5556),
        (0.5521, 0.5556),
    ]

    HERO_DEPLOY_POINTS_NORM: list[tuple[float, float]] = [
        (0.3021, 0.2870),
        (0.3542, 0.2685),
        (0.4062, 0.2593),
    ]

    SCOUT_GOLD_REGION_NORM: tuple[float, float, float, float] = (0.0417, 0.0926, 0.1042, 0.0278)
    SCOUT_ELIXIR_REGION_NORM: tuple[float, float, float, float] = (0.0417, 0.1296, 0.1042, 0.0278)
    SCOUT_DE_REGION_NORM: tuple[float, float, float, float] = (0.0417, 0.1667, 0.1042, 0.0278)

    # Legacy pixel coordinates at 1920×1080
    ATTACK_BUTTON_POS: tuple[int, int] = (80, 900)
    FIND_MATCH_BUTTON_POS: tuple[int, int] = (250, 400)
    NEXT_BUTTON_POS: tuple[int, int] = (1790, 990)
    RETURN_HOME_BUTTON_POS: tuple[int, int] = (960, 850)
    END_BATTLE_BUTTON_POS: tuple[int, int] = (100, 650)
    EDGE_DEPLOY_POINTS: list[tuple[int, int]] = [
        (180, 540), (280, 440), (380, 380), (480, 340), (580, 310),
        (680, 290), (780, 280), (880, 290), (980, 310), (1080, 340),
    ]
    CENTER_SPELL_POINTS: list[tuple[int, int]] = [
        (960, 540), (860, 480), (1060, 480), (860, 600), (1060, 600),
    ]
    HERO_DEPLOY_POINTS: list[tuple[int, int]] = [
        (580, 310), (680, 290), (780, 280),
    ]
    SCOUT_GOLD_REGION: tuple[int, int, int, int] = (80, 100, 200, 30)
    SCOUT_ELIXIR_REGION: tuple[int, int, int, int] = (80, 140, 200, 30)
    SCOUT_DE_REGION: tuple[int, int, int, int] = (80, 180, 200, 30)
    RESULTS_GOLD_REGION: tuple[int, int, int, int] = (700, 380, 250, 35)
    RESULTS_ELIXIR_REGION: tuple[int, int, int, int] = (700, 420, 250, 35)
    RESULTS_DE_REGION: tuple[int, int, int, int] = (700, 460, 250, 35)
    RESULTS_STARS_REGION: tuple[int, int, int, int] = (800, 280, 320, 60)

    # ── Keyboard shortcuts for troop bar slots ──────────────────────────
    TROOP_BAR_KEYS: list[str] = ["q", "w", "e", "a", "s", "d"]
    MAIN_TROOP_KEY: str = "q"
    SPELL_KEY: str = "e"
    HERO_KEYS: list[str] = ["w", "a", "s"]

    # ── Timeout constants (seconds) ─────────────────────────────────────
    BATTLE_TIMEOUT: int = 240
    SCOUT_LOAD_WAIT: float = 3.0
    TROOP_SETTLE_DELAY: float = 2.5
    POST_SPELL_DELAY: float = 1.5
    DEPLOY_CLICK_INTERVAL: float = 0.12

    def __init__(
        self,
        config: dict,
        backend_or_screen: Any,
        vision: Any = None,
        ocr: Any = None,
        input_ctrl: Any = None,
        navigator: Optional[Navigator] = None,
        resource_reader: Optional[ResourceReader] = None,
        army_manager: Optional[ArmyManager] = None,
        backend: Optional[Backend] = None,
    ) -> None:
        """Initialise AttackEngine.

        Args:
            config: Bot configuration dictionary.
            backend_or_screen: Backend instance (or legacy ScreenCapture).
            vision: OpenCV template matching engine.
            ocr: Tesseract OCR engine for game text.
            input_ctrl: Mouse/keyboard input controller with gem guard.
            navigator: Game menu navigation helper.
            resource_reader: HUD resource reader.
            army_manager: Army training and management module.
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
        self.resource_reader = resource_reader or ResourceReader(config, self.backend or self.screen, self.ocr)
        self.army_manager = army_manager or ArmyManager(config, self.backend or self.screen, vision, self.ocr, self.input_ctrl, self.navigator)

        # Config-driven values
        farming_cfg = config.get("farming", {})
        self._min_gold: int = farming_cfg.get("min_gold", 400_000)
        self._min_elixir: int = farming_cfg.get("min_elixir", 400_000)
        self._max_next: int = farming_cfg.get("max_next_presses", 50)
        self._storage_threshold: float = farming_cfg.get("storage_full_threshold", 0.90)
        self._max_attacks: int = farming_cfg.get("max_attacks_per_session", 30)

        timing_cfg = config.get("timing", {})
        self._action_delay: float = timing_cfg.get("between_actions_delay_sec", 1.5)
        self._dialog_wait: float = timing_cfg.get("dialog_animation_wait_sec", 0.8)
        self._screen_load_wait: float = timing_cfg.get("screen_load_wait_sec", 3.0)

        logger.info(
            "AttackEngine initialised (min_gold=%d, min_elixir=%d, max_next=%d).",
            self._min_gold, self._min_elixir, self._max_next,
        )

    def get_screenshot(self) -> np.ndarray:
        """Capture screenshot via backend or screen helper."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None and hasattr(self.screen, "capture_screenshot"):
            return self.screen.capture_screenshot()
        raise RuntimeError("AttackEngine has no backend or screen capture handle")


    # ── Main farming loop ───────────────────────────────────────────────

    def farm_until_full(self, account: dict) -> int:
        """Main farming loop — attacks until storages are full.

        1. While storages not full:
           a. Ensure army is ready (wait if not).
           b. find_target() with loot filter.
           c. execute_attack().
           d. Return home, collect.
           e. Retrain army.
        2. Return attack count.

        Args:
            account: Account configuration dict with th_level, name, etc.

        Returns:
            Number of attacks executed.
        """
        th_level = account.get("th_level", 13)
        account_name = account.get("name", "unknown")
        logger.info("🚜 Starting farming loop for %s (TH%d)…", account_name, th_level)

        attack_count = 0

        try:
            while attack_count < self._max_attacks:
                # Check if storages are full
                if self.resource_reader.are_storages_full(
                    th_level, threshold=self._storage_threshold
                ):
                    logger.info(
                        "💰 Storages are ≥ %.0f%% full — farming complete!",
                        self._storage_threshold * 100,
                    )
                    break

                # Ensure army is ready
                if not self.army_manager.is_army_ready():
                    logger.info("Army not ready — waiting…")
                    if not self.army_manager.wait_for_army(timeout=600):
                        logger.warning("Army wait timed out — aborting farm loop.")
                        break

                # Find a target
                logger.info("Searching for target (attack #%d)…", attack_count + 1)
                target_found = self.find_target(
                    min_gold=self._min_gold,
                    min_elixir=self._min_elixir,
                    max_searches=self._max_next,
                )

                if not target_found:
                    logger.warning("No suitable target found — ending farm loop.")
                    # Try to return home from scout screen
                    self._return_home_from_scout()
                    break

                # Execute the attack
                result = self.execute_attack()
                attack_count += 1
                logger.info(
                    "Attack #%d complete: %s", attack_count, result,
                )

                # Brief pause after returning home
                time.sleep(self._action_delay)

                # Retrain army for next attack
                troop_type = self.army_manager.get_troop_type(
                    current_de=self.resource_reader.read_dark_elixir(),
                    is_boosted=self.army_manager.is_super_dragon_active(),
                )
                self.army_manager.train_army(troop_type)

            logger.info(
                "🏁 Farming loop finished: %d attacks for %s.",
                attack_count, account_name,
            )

        except Exception as exc:
            logger.error(
                "Error in farming loop: %s", exc, exc_info=True,
            )
            self.navigator.close_all_dialogs()

        return attack_count

    # ── Target finding ──────────────────────────────────────────────────

    def find_target(
        self,
        min_gold: int,
        min_elixir: int,
        max_searches: int = 50,
    ) -> bool:
        """Search for a base that meets the loot threshold.

        Workflow:
        1. Tap Attack button (bottom-left).
        2. Tap 'Find a Match' or multiplayer button.
        3. Wait for scout screen (enemy base visible).
        4. Read Available Loot from top-left.
        5. If gold >= min AND elixir >= min → return True.
        6. Else tap 'Next' button (bottom-right, costs gold).
        7. Repeat up to max_searches.
        8. If no good target found → return False.

        Args:
            min_gold: Minimum gold to consider a worthy target.
            min_elixir: Minimum elixir to consider a worthy target.
            max_searches: Max number of bases to skip before giving up.

        Returns:
            True if a good target is found and we're ready to attack.
        """
        logger.info(
            "Finding target: min_gold=%d, min_elixir=%d, max_searches=%d",
            min_gold, min_elixir, max_searches,
        )

        try:
            # Step 1: tap Attack button
            self.input_ctrl.safe_click(*self.ATTACK_BUTTON_POS)
            time.sleep(self._dialog_wait)

            # Step 2: tap Find a Match / multiplayer
            screenshot = self.screen.capture_screenshot()
            find_btn = self.vision.find(screenshot, "find_match_btn", threshold=0.7)
            if find_btn is not None:
                fx, fy, fw, fh = find_btn
                self.input_ctrl.safe_click(fx + fw // 2, fy + fh // 2)
            else:
                self.input_ctrl.safe_click(*self.FIND_MATCH_BUTTON_POS)
            time.sleep(self._screen_load_wait)

            # Wait for matchmaking
            time.sleep(self.SCOUT_LOAD_WAIT)

            for search_num in range(max_searches):
                # Step 4: read loot from scout screen
                gold, elixir, de = self._read_scout_loot()

                logger.info(
                    "  Scout #%d: Gold=%d, Elixir=%d, DE=%d",
                    search_num + 1, gold, elixir, de,
                )

                # Step 5: check thresholds
                if gold >= min_gold and elixir >= min_elixir:
                    logger.info(
                        "✅ Target found! Gold=%d (>=%d), Elixir=%d (>=%d)",
                        gold, min_gold, elixir, min_elixir,
                    )
                    return True

                # Step 6: tap Next
                logger.debug("Target not good enough — pressing Next…")
                self.input_ctrl.safe_click(*self.NEXT_BUTTON_POS, checks=False)
                time.sleep(self.SCOUT_LOAD_WAIT)

            logger.warning("No target found after %d searches.", max_searches)
            return False

        except Exception as exc:
            logger.error("Error finding target: %s", exc, exc_info=True)
            return False

    def _read_scout_loot(self) -> tuple[int, int, int]:
        """Read gold, elixir, and DE from the scout screen.

        Uses resource_reader if available, otherwise falls back to
        direct OCR on known regions.

        Returns:
            Tuple of (gold, elixir, dark_elixir).
        """
        try:
            loot = self.resource_reader.read_loot_from_scout()
            if isinstance(loot, dict):
                gold = loot.get("gold")
                elixir = loot.get("elixir")
                de = loot.get("dark_elixir")
                if all(isinstance(value, int) for value in (gold, elixir, de)):
                    return gold, elixir, de
                logger.warning("Scout loot OCR was incomplete: %s", loot)
            else:
                logger.warning("Scout loot reader returned an unexpected value: %r", loot)
        except Exception as exc:
            logger.debug("Scout loot reader failed — using direct OCR: %s", exc)

        gold = elixir = de = 0
        try:
            gold_img = self.screen.capture_region(*self.SCOUT_GOLD_REGION)
            gold = self.ocr.read_number(gold_img) or 0
        except Exception as exc:
            logger.debug("Failed to read scout gold: %s", exc)

        try:
            elixir_img = self.screen.capture_region(*self.SCOUT_ELIXIR_REGION)
            elixir = self.ocr.read_number(elixir_img) or 0
        except Exception as exc:
            logger.debug("Failed to read scout elixir: %s", exc)

        try:
            de_img = self.screen.capture_region(*self.SCOUT_DE_REGION)
            de = self.ocr.read_number(de_img) or 0
        except Exception as exc:
            logger.debug("Failed to read scout DE: %s", exc)

        return (gold, elixir, de)

    def _return_home_from_scout(self) -> None:
        """Cancel scouting and return to home base."""
        logger.info("Returning home from scout screen…")
        try:
            # Try tapping the end/cancel button area or pressing Escape
            self.input_ctrl.press_key("escape")
            time.sleep(self._dialog_wait)

            # Look for confirmation to end search
            screenshot = self.screen.capture_screenshot()
            confirm = self.vision.find(screenshot, "confirm_btn", threshold=0.7)
            if confirm is not None:
                cx, cy, cw, ch = confirm
                self.input_ctrl.safe_click(cx + cw // 2, cy + ch // 2)
                time.sleep(self._screen_load_wait)

            self.navigator.go_home()
        except Exception as exc:
            logger.error("Error returning home from scout: %s", exc, exc_info=True)

    # ── Attack execution ────────────────────────────────────────────────

    def execute_attack(self) -> dict:
        """Execute a full attack on the currently scouted base.

        Deployment strategy:
        1. Select main troop key (Q) → click along deployment edge.
        2. Wait 2–3s for troops to move in.
        3. Deploy heroes → click near centre of edge.
        4. Deploy EQ spells SPREAD around centre (5 different positions).
        5. Wait for battle to end (detect 'Return Home' or results).
        6. Read results.
        7. Tap 'Return Home'.

        Returns:
            Dict with attack results: stars, gold, elixir, de, etc.
        """
        logger.info("⚔️ Executing attack…")
        result: dict = {
            "stars": 0,
            "gold_looted": 0,
            "elixir_looted": 0,
            "de_looted": 0,
            "success": False,
        }

        try:
            # ── Step 1: deploy main troops along edge ───────────────────
            logger.info("Deploying main troops (slot=0, key='%s')…", self.MAIN_TROOP_KEY)
            self.backend.select_troop_slot(0)
            time.sleep(0.3)

            for point in self.EDGE_DEPLOY_POINTS_NORM:
                self.backend.tap(*point)
                time.sleep(self.DEPLOY_CLICK_INTERVAL)

            # ── Step 2: wait for troops to engage ───────────────────────
            logger.info("Waiting %.1fs for troops to engage…", self.TROOP_SETTLE_DELAY)
            time.sleep(self.TROOP_SETTLE_DELAY)

            # ── Step 3: deploy heroes ───────────────────────────────────
            logger.info("Deploying heroes…")
            for i, hero_key in enumerate(self.HERO_KEYS):
                self.backend.select_troop_slot(1 + i)
                time.sleep(0.2)
                if i < len(self.HERO_DEPLOY_POINTS_NORM):
                    self.backend.tap(*self.HERO_DEPLOY_POINTS_NORM[i])
                    time.sleep(0.2)

            time.sleep(1.0)

            # ── Step 4: deploy EQ spells SPREAD around centre ───────────
            logger.info(
                "Deploying Earthquake spells (key='%s') at %d positions…",
                self.SPELL_KEY, len(self.CENTER_SPELL_POINTS_NORM),
            )
            self.backend.select_troop_slot(1 + len(self.HERO_KEYS))
            time.sleep(0.3)

            for sx, sy in self.CENTER_SPELL_POINTS_NORM:
                self.backend.tap(sx, sy)
                time.sleep(0.4)  # Slightly longer between spell drops

            time.sleep(self.POST_SPELL_DELAY)

            # ── Step 5: wait for battle to end ──────────────────────────
            logger.info("Waiting for battle to end (timeout=%ds)…", self.BATTLE_TIMEOUT)
            battle_ended = self._wait_for_battle_end(timeout=self.BATTLE_TIMEOUT)

            if not battle_ended:
                logger.warning("Battle timeout — forcing end.")

            # ── Step 6: read results ────────────────────────────────────
            time.sleep(self._screen_load_wait)
            result = self._read_attack_results()
            result["success"] = True

            # ── Step 7: tap Return Home ─────────────────────────────────
            logger.info("Tapping 'Return Home'…")
            self._tap_return_home()

            logger.info(
                "⚔️ Attack complete: ⭐%d | Gold=%d | Elixir=%d | DE=%d",
                result.get("stars", 0),
                result.get("gold_looted", 0),
                result.get("elixir_looted", 0),
                result.get("de_looted", 0),
            )

        except Exception as exc:
            logger.error("Error during attack execution: %s", exc, exc_info=True)
            # Try to get back home regardless
            try:
                self._tap_return_home()
            except Exception:
                self.navigator.go_home()

        return result

    def _wait_for_battle_end(self, timeout: int = 240) -> bool:
        """Wait until the battle ends, detected by 'Return Home' button.

        Args:
            timeout: Maximum seconds to wait for battle to end.

        Returns:
            True if battle end was detected.
        """
        start = time.time()
        poll_interval = 5.0

        while time.time() - start < timeout:
            screenshot = self.screen.capture_screenshot()

            # Look for Return Home button (battle results screen)
            rh = self.vision.find(screenshot, "return_home_btn", threshold=0.7)
            if rh is not None:
                logger.info("Battle ended — 'Return Home' detected.")
                return True

            # Also look for the battle results screen
            results = self.vision.find(screenshot, "attack_results", threshold=0.7)
            if results is not None:
                logger.info("Battle ended — results screen detected.")
                return True

            # Check for "End Battle" prompt (3-star / time ran out)
            end_battle = self.vision.find(screenshot, "end_battle_btn", threshold=0.7)
            if end_battle is not None:
                logger.info("'End Battle' button detected — battle is over.")
                return True

            time.sleep(poll_interval)

        return False

    def _read_attack_results(self) -> dict:
        """Read loot and stars from the battle results screen.

        Returns:
            Dict with keys: stars, gold_looted, elixir_looted, de_looted.
        """
        result: dict = {
            "stars": 0,
            "gold_looted": 0,
            "elixir_looted": 0,
            "de_looted": 0,
        }

        try:
            # Read gold
            gold_img = self.screen.capture_region(*self.RESULTS_GOLD_REGION)
            result["gold_looted"] = self.ocr.read_number(gold_img) or 0
        except Exception as exc:
            logger.debug("Failed to read result gold: %s", exc)

        try:
            # Read elixir
            elixir_img = self.screen.capture_region(*self.RESULTS_ELIXIR_REGION)
            result["elixir_looted"] = self.ocr.read_number(elixir_img) or 0
        except Exception as exc:
            logger.debug("Failed to read result elixir: %s", exc)

        try:
            # Read DE
            de_img = self.screen.capture_region(*self.RESULTS_DE_REGION)
            result["de_looted"] = self.ocr.read_number(de_img) or 0
        except Exception as exc:
            logger.debug("Failed to read result DE: %s", exc)

        try:
            # Read stars (count star icons or OCR the number)
            stars_img = self.screen.capture_region(*self.RESULTS_STARS_REGION)
            screenshot = self.screen.capture_screenshot()
            star_matches = self.vision.find_all(
                screenshot, "star_icon", threshold=0.7
            )
            result["stars"] = len(star_matches) if star_matches else 0
        except Exception as exc:
            logger.debug("Failed to read stars: %s", exc)

        logger.info(
            "Battle results: ⭐%d G=%d E=%d DE=%d",
            result["stars"],
            result["gold_looted"],
            result["elixir_looted"],
            result["de_looted"],
        )
        return result

    def _tap_return_home(self) -> None:
        """Tap the 'Return Home' button and wait for home screen."""
        try:
            screenshot = self.screen.capture_screenshot()
            rh_btn = self.vision.find(screenshot, "return_home_btn", threshold=0.7)

            if rh_btn is not None:
                rx, ry, rw, rh_h = rh_btn
                self.input_ctrl.safe_click(rx + rw // 2, ry + rh_h // 2)
            else:
                self.input_ctrl.safe_click(*self.RETURN_HOME_BUTTON_POS)

            time.sleep(self._screen_load_wait)

            # Wait for home base screen
            self.navigator.go_home()

        except Exception as exc:
            logger.error("Error tapping return home: %s", exc, exc_info=True)
            self.navigator.go_home()
