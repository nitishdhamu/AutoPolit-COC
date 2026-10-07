"""
Builder Base Manager — Full Builder Base lifecycle management.
==============================================================
Manages the complete Builder Base cycle: switching villages, collecting
BB resources, farming Versus Battles with pre-trained troops, starting
suggested upgrades, Star Lab research, and free Clock Tower boosts.

Key design decisions:
- Uses pre-trained troops (no retraining in BB).
- Detects idle builders dynamically (2 or 3 depending on account).
- Clock Tower boost is FREE (no gem cost, but safety check still runs).
- Upgrade priority uses the game's "Recommended" / "Suggested" feature.
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


class BuilderBaseManager:
    """Manages the full Builder Base cycle: collect, battle, upgrade, boost."""

    # ── Screen coordinate constants (1920×1080) ─────────────────────────

    # ── Navigation ──────────────────────────────────────────────────────
    # Boat button to switch to Builder Base (on home village)
    BOAT_BUTTON_POS: tuple[int, int] = (120, 800)
    BOAT_BUTTON_POS_NORM: tuple[float, float] = (120 / 1920, 800 / 1080)

    # Button to return to Home Village (on Builder Base)
    HOME_VILLAGE_BUTTON_POS: tuple[int, int] = (120, 800)
    HOME_VILLAGE_BUTTON_POS_NORM: tuple[float, float] = (120 / 1920, 800 / 1080)

    # ── Builder Base HUD ────────────────────────────────────────────────
    # Builder Gold icon region on BB HUD: (x, y, w, h)
    BB_GOLD_REGION: tuple[int, int, int, int] = (1590, 20, 210, 30)
    BB_GOLD_REGION_NORM: tuple[float, float, float, float] = (1590 / 1920, 20 / 1080, 210 / 1920, 30 / 1080)

    # Builder Elixir icon region on BB HUD: (x, y, w, h)
    BB_ELIXIR_REGION: tuple[int, int, int, int] = (1590, 60, 210, 30)
    BB_ELIXIR_REGION_NORM: tuple[float, float, float, float] = (1590 / 1920, 60 / 1080, 210 / 1920, 30 / 1080)

    # Builder count region on BB HUD: (x, y, w, h)
    BB_BUILDER_REGION: tuple[int, int, int, int] = (230, 10, 110, 25)
    BB_BUILDER_REGION_NORM: tuple[float, float, float, float] = (230 / 1920, 10 / 1080, 110 / 1920, 25 / 1080)

    # Builder icon position (to open BB builder menu)
    BB_BUILDER_ICON_POS: tuple[int, int] = (295, 18)
    BB_BUILDER_ICON_POS_NORM: tuple[float, float] = (295 / 1920, 18 / 1080)

    # ── BB Resource collection ──────────────────────────────────────────
    # Sweep positions for BB collectors (smaller base than home village)
    BB_COLLECTOR_POSITIONS: list[tuple[int, int]] = [
        (500, 400), (650, 350), (800, 320), (950, 350), (1100, 400),
        (450, 500), (600, 470), (750, 450), (900, 470), (1050, 500),
        (500, 580), (650, 560), (800, 550), (950, 560), (1100, 580),
    ]
    BB_COLLECTOR_POSITIONS_NORM: list[tuple[float, float]] = [
        (round(x / 1920, 4), round(y / 1080, 4)) for x, y in BB_COLLECTOR_POSITIONS
    ]

    # ── Attack (Versus Battle) ──────────────────────────────────────────
    # "Attack" / "Battle!" button on BB
    BB_ATTACK_BUTTON_POS: tuple[int, int] = (80, 900)
    BB_ATTACK_BUTTON_POS_NORM: tuple[float, float] = (80 / 1920, 900 / 1080)

    # "Find Now" button for Versus Battle matchmaking
    BB_FIND_NOW_POS: tuple[int, int] = (960, 550)
    BB_FIND_NOW_POS_NORM: tuple[float, float] = (960 / 1920, 550 / 1080)

    # Edge deployment points for BB attacks (smaller area than home)
    BB_EDGE_DEPLOY_POINTS: list[tuple[int, int]] = [
        (250, 500),
        (350, 420),
        (450, 360),
        (550, 320),
        (650, 300),
        (750, 290),
        (850, 300),
        (950, 320),
        (1050, 360),
        (1150, 420),
    ]
    BB_EDGE_DEPLOY_POINTS_NORM: list[tuple[float, float]] = [
        (round(x / 1920, 4), round(y / 1080, 4)) for x, y in BB_EDGE_DEPLOY_POINTS
    ]

    # Troop bar keyboard shortcuts for BB
    BB_TROOP_KEYS: list[str] = ["q", "w", "e", "a", "s", "d"]

    # "Return Home" / "OK" button on Versus Battle results
    BB_RESULTS_OK_POS: tuple[int, int] = (960, 750)
    BB_RESULTS_OK_POS_NORM: tuple[float, float] = (960 / 1920, 750 / 1080)

    # Versus Battle result gold region: (x, y, w, h)
    BB_RESULT_GOLD_REGION: tuple[int, int, int, int] = (700, 400, 250, 35)
    BB_RESULT_GOLD_REGION_NORM: tuple[float, float, float, float] = (700 / 1920, 400 / 1080, 250 / 1920, 35 / 1080)

    # Versus Battle result elixir region: (x, y, w, h)
    BB_RESULT_ELIXIR_REGION: tuple[int, int, int, int] = (700, 440, 250, 35)
    BB_RESULT_ELIXIR_REGION_NORM: tuple[float, float, float, float] = (700 / 1920, 440 / 1080, 250 / 1920, 35 / 1080)

    # ── BB Builder menu / upgrades ──────────────────────────────────────
    BB_BUILDER_MENU_REGION: tuple[int, int, int, int] = (300, 80, 400, 520)
    BB_BUILDER_MENU_REGION_NORM: tuple[float, float, float, float] = (300 / 1920, 80 / 1080, 400 / 1920, 520 / 1080)
    BB_SUGGESTED_REGION: tuple[int, int, int, int] = (320, 400, 360, 200)
    BB_SUGGESTED_REGION_NORM: tuple[float, float, float, float] = (320 / 1920, 400 / 1080, 360 / 1920, 200 / 1080)
    BB_FIRST_SUGGESTED_POS: tuple[int, int] = (500, 440)
    BB_FIRST_SUGGESTED_POS_NORM: tuple[float, float] = (500 / 1920, 440 / 1080)
    BB_CONFIRM_BTN_POS: tuple[int, int] = (960, 640)
    BB_CONFIRM_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 640 / 1080)
    BB_CANCEL_BTN_POS: tuple[int, int] = (960, 700)
    BB_CANCEL_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 700 / 1080)
    BB_TIMER_REGION: tuple[int, int, int, int] = (800, 400, 320, 40)
    BB_TIMER_REGION_NORM: tuple[float, float, float, float] = (800 / 1920, 400 / 1080, 320 / 1920, 40 / 1080)
    BB_COST_REGION: tuple[int, int, int, int] = (870, 580, 180, 40)
    BB_COST_REGION_NORM: tuple[float, float, float, float] = (870 / 1920, 580 / 1080, 180 / 1920, 40 / 1080)
    BB_NAME_REGION: tuple[int, int, int, int] = (780, 280, 360, 40)
    BB_NAME_REGION_NORM: tuple[float, float, float, float] = (780 / 1920, 280 / 1080, 360 / 1920, 40 / 1080)

    # ── Star Lab ────────────────────────────────────────────────────────
    STAR_LAB_POS: tuple[int, int] = (1100, 550)
    STAR_LAB_POS_NORM: tuple[float, float] = (1100 / 1920, 550 / 1080)
    STAR_LAB_RESEARCH_BTN_POS: tuple[int, int] = (960, 600)
    STAR_LAB_RESEARCH_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 600 / 1080)
    STAR_LAB_FIRST_SUGGESTED_POS: tuple[int, int] = (530, 450)
    STAR_LAB_FIRST_SUGGESTED_POS_NORM: tuple[float, float] = (530 / 1920, 450 / 1080)
    STAR_LAB_CONFIRM_POS: tuple[int, int] = (960, 640)
    STAR_LAB_CONFIRM_POS_NORM: tuple[float, float] = (960 / 1920, 640 / 1080)
    STAR_LAB_CANCEL_POS: tuple[int, int] = (960, 700)
    STAR_LAB_CANCEL_POS_NORM: tuple[float, float] = (960 / 1920, 700 / 1080)
    STAR_LAB_SUGGESTED_REGION: tuple[int, int, int, int] = (350, 400, 350, 200)
    STAR_LAB_SUGGESTED_REGION_NORM: tuple[float, float, float, float] = (350 / 1920, 400 / 1080, 350 / 1920, 200 / 1080)
    STAR_LAB_TIMER_REGION: tuple[int, int, int, int] = (800, 400, 320, 40)
    STAR_LAB_TIMER_REGION_NORM: tuple[float, float, float, float] = (800 / 1920, 400 / 1080, 320 / 1920, 40 / 1080)
    STAR_LAB_NAME_REGION: tuple[int, int, int, int] = (780, 280, 360, 40)
    STAR_LAB_NAME_REGION_NORM: tuple[float, float, float, float] = (780 / 1920, 280 / 1080, 360 / 1920, 40 / 1080)
    STAR_LAB_COST_REGION: tuple[int, int, int, int] = (870, 580, 180, 40)
    STAR_LAB_COST_REGION_NORM: tuple[float, float, float, float] = (870 / 1920, 580 / 1080, 180 / 1920, 40 / 1080)

    # ── Clock Tower ─────────────────────────────────────────────────────
    CLOCK_TOWER_POS: tuple[int, int] = (700, 450)
    CLOCK_TOWER_POS_NORM: tuple[float, float] = (700 / 1920, 450 / 1080)
    CLOCK_TOWER_BOOST_BTN_POS: tuple[int, int] = (960, 600)
    CLOCK_TOWER_BOOST_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 600 / 1080)

    # ── Timing ──────────────────────────────────────────────────────────
    VERSUS_MATCHMAKING_TIMEOUT: int = 60
    VERSUS_BATTLE_TIMEOUT: int = 180
    BB_DEPLOY_CLICK_INTERVAL: float = 0.12

    # Absolute max builders possible on Builder Base
    BB_ABSOLUTE_CAP: int = 3

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
        """Initialise BuilderBaseManager."""
        self.config = config
        self.backend = backend
        self.screen = screen
        self.vision = vision
        self.ocr = ocr
        self.input_ctrl = input_ctrl
        self.navigator = navigator
        self.resource_reader = resource_reader

        # Config
        bb_cfg = config.get("builder_base", {})
        self._enabled: bool = bb_cfg.get("enabled", True)
        self._farm_versus: bool = bb_cfg.get("farm_versus_battles", True)
        self._boost_clock_tower: bool = bb_cfg.get("boost_clock_tower", True)
        self._star_lab_research: bool = bb_cfg.get("star_lab_research", True)
        self._max_versus: int = bb_cfg.get("max_versus_battles_per_session", 10)

        timing_cfg = config.get("timing", {})
        self._action_delay: float = timing_cfg.get("between_actions_delay_sec", 1.5)
        self._dialog_wait: float = timing_cfg.get("dialog_animation_wait_sec", 0.8)
        self._screen_load_wait: float = timing_cfg.get("screen_load_wait_sec", 3.0)

        logger.info(
            "BuilderBaseManager initialised (enabled=%s, farm=%s, boost=%s, lab=%s).",
            self._enabled, self._farm_versus, self._boost_clock_tower,
            self._star_lab_research,
        )

    def get_screenshot(self):
        """Capture screenshot via backend or screen fallback."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None:
            return self.screen.capture_screenshot()
        raise RuntimeError("No backend or screen capture available in BuilderBaseManager")

    def tap(self, x: float | int, y: float | int, checks: bool = True) -> bool:
        """Tap at coordinate via backend or input_ctrl fallback."""
        if self.backend is not None:
            return self.backend.safe_click(x, y, checks=checks)
        if self.input_ctrl is not None:
            return self.input_ctrl.safe_click(x, y, checks=checks)
        raise RuntimeError("No backend or input controller available in BuilderBaseManager")

    def press_key(self, key: str) -> None:
        """Press keyboard shortcut via backend or input_ctrl."""
        if self.backend is not None:
            self.backend.press_key(key)
        elif self.input_ctrl is not None:
            self.input_ctrl.press_key(key)
        else:
            raise RuntimeError("No backend or input controller available in BuilderBaseManager")

    def capture_region(self, x: float | int, y: float | int, w: float | int, h: float | int):
        """Capture a region as numpy array."""
        img = self.get_screenshot()
        if x <= 1.0 and y <= 1.0 and w <= 1.0 and h <= 1.0:
            return crop_normalized(img, (float(x), float(y), float(w), float(h)))
        H, W = img.shape[:2]
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2, y2 = min(W, int(x + w)), min(H, int(y + h))
        return img[y1:y2, x1:x2]

    # ── Master BB cycle ─────────────────────────────────────────────────

    def manage_builder_base(self, account: dict, db: Any) -> dict:
        """Run the full Builder Base management cycle.

        Workflow:
        1. Switch to Builder Base.
        2. Collect resources.
        3. Loop: while has_idle_builder → farm_versus → start_upgrade.
        4. Start Star Lab research.
        5. Boost Clock Tower.
        6. Switch back to Home Village.
        7. Return summary dict.

        Args:
            account: Account configuration dict.
            db: Database instance for recording timers.

        Returns:
            Summary dict with keys: battles, upgrades_started, lab_started,
            clock_tower_boosted.
        """
        account_name = account.get("name", "unknown")
        # Per-account BB builder cap (from config, always <= 3)
        bb_max_builders = min(
            int(account.get("bb_max_builders", 3)),
            self.BB_ABSOLUTE_CAP,
        )
        logger.info(
            "🏗️ Starting Builder Base cycle for %s (bb_builders=%d)…",
            account_name, bb_max_builders,
        )

        summary: dict = {
            "battles": 0,
            "upgrades_started": 0,
            "lab_started": False,
            "clock_tower_boosted": False,
        }

        if not self._enabled:
            logger.info("Builder Base management is disabled — skipping.")
            return summary

        try:
            # Step 1: switch to Builder Base
            logger.info("Switching to Builder Base…")
            self.navigator.switch_to_builder_base()
            time.sleep(self._screen_load_wait)
            time.sleep(self._screen_load_wait)  # Extra wait for village load

            # Step 2: collect resources
            self.collect_resources()

            # Step 3: loop — farm and upgrade while builders are idle
            upgrade_loop_count = 0
            max_upgrade_loops = 5  # Safety cap

            while self.has_idle_builder(bb_max_builders) and upgrade_loop_count < max_upgrade_loops:
                upgrade_loop_count += 1
                logger.info(
                    "BB upgrade loop #%d — idle builder detected.", upgrade_loop_count
                )

                # Farm Versus Battles until storages are full
                if self._farm_versus:
                    battles = self.farm_versus_until_full(
                        max_battles=self._max_versus
                    )
                    summary["battles"] += battles

                # Start suggested upgrade
                upgrade_result = self.start_suggested_upgrade()
                if upgrade_result is not None:
                    summary["upgrades_started"] += 1

                    # Record upgrade timer in DB if available
                    if db is not None:
                        try:
                            timer = upgrade_result.get("timer", timedelta(0))
                            # account_id must be an integer (supercell_id_index or DB id)
                            db_account_id = account.get("_db_id",
                                               account.get("supercell_id_index", 0))
                            timer_sec = int(timer.total_seconds()) if isinstance(timer, timedelta) else int(timer)
                            db.record_upgrade(
                                account_id=db_account_id,
                                building=upgrade_result.get("name", "BB Upgrade"),
                                village="builder",
                                duration_sec=timer_sec,
                            )
                        except Exception as db_exc:
                            logger.warning(
                                "Failed to record BB upgrade in DB: %s", db_exc
                            )
                else:
                    # No upgrade available — stop looping
                    logger.info("No more BB upgrades available.")
                    break

            # Step 4: Star Lab research
            if self._star_lab_research:
                lab_result = self.start_star_lab_research()
                if lab_result is not None:
                    summary["lab_started"] = True
                    if db is not None:
                        try:
                            timer = lab_result.get("timer", timedelta(0))
                            db_account_id = account.get("_db_id",
                                               account.get("supercell_id_index", 0))
                            timer_sec = int(timer.total_seconds()) if isinstance(timer, timedelta) else int(timer)
                            db.record_upgrade(
                                account_id=db_account_id,
                                building=lab_result.get("name", "BB Lab Research"),
                                village="builder",
                                duration_sec=timer_sec,
                            )
                        except Exception as db_exc:
                            logger.warning(
                                "Failed to record BB lab research in DB: %s", db_exc
                            )

            # Step 5: boost Clock Tower (LAST — after all builders assigned)
            if self._boost_clock_tower:
                boosted = self.boost_clock_tower()
                summary["clock_tower_boosted"] = boosted

            # Step 6: switch back to Home Village
            logger.info("Switching back to Home Village…")
            self.navigator.switch_to_home_village()
            time.sleep(self._screen_load_wait)

            logger.info("🏗️ Builder Base cycle complete: %s", summary)
            return summary

        except Exception as exc:
            logger.error(
                "Error in Builder Base cycle: %s", exc, exc_info=True
            )
            # Try to get back to home village
            try:
                self.navigator.switch_to_home_village()
            except Exception:
                logger.error("Failed to return to Home Village!", exc_info=True)
            return summary

    # ── Resource collection ─────────────────────────────────────────────

    def collect_resources(self) -> bool:
        """Tap on BB resource collectors to collect Builder Gold/Elixir.

        Uses a sweep pattern across known collector positions.

        Returns:
            True if collection taps were performed.
        """
        logger.info("Collecting BB resources…")
        collected = False

        try:
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            # Template-based detection first
            screenshot = self.get_screenshot()
            if self.vision is not None:
                indicators = self.vision.find_all(
                    screenshot, "bb_collector_ready", threshold=0.7
                )
                if indicators:
                    logger.info("Found %d BB collector indicators.", len(indicators))
                    for rx, ry, rw, rh in indicators:
                        self.tap(
                            rx + rw // 2, ry + rh // 2, checks=False
                        )
                        time.sleep(0.25)
                        collected = True

            # Sweep pattern
            logger.info("Running BB collector sweep (%d positions)…",
                        len(self.BB_COLLECTOR_POSITIONS))
            for px, py in self.BB_COLLECTOR_POSITIONS:
                self.tap(px, py, checks=False)
                time.sleep(0.15)
                collected = True

            time.sleep(self._action_delay)
            logger.info("BB resource collection complete.")
            return collected

        except Exception as exc:
            logger.error("Error collecting BB resources: %s", exc, exc_info=True)
            return False

    # ── Builder status ──────────────────────────────────────────────────

    # In Builder Base, CoC shows builder count as "free/total" in the HUD.
    # We read this OCR text and cap it to bb_max_builders (always <= 3).
    # There is NO Goblin Builder in Builder Base — but we still enforce the
    # per-account cap from config (bb_max_builders) to be safe.
    BB_ABSOLUTE_CAP: int = 3   # Maximum possible BB builders

    def has_idle_builder(self, bb_max_builders: int = BB_ABSOLUTE_CAP) -> bool:
        """Check if there is at least one idle builder in the Builder Base."""
        effective_cap = min(bb_max_builders, self.BB_ABSOLUTE_CAP)

        logger.info(
            "Checking BB builder status (cap=%d)…", effective_cap
        )
        try:
            region_img = self.capture_region(*self.BB_BUILDER_REGION)
            text = self.ocr.read_text(region_img).strip()
            logger.debug("BB builder OCR text: '%s'", text)

            if "/" in text:
                parts = text.split("/")
                try:
                    free  = int(parts[0].strip())
                    total = int(parts[1].strip())

                    # Cap total to account limit and adjust free accordingly
                    if total > effective_cap:
                        overshoot = total - effective_cap
                        free = max(0, free - overshoot)
                        total = effective_cap

                    has_idle = free > 0
                    logger.info(
                        "BB builders (cap=%d): %d free / %d total → idle=%s",
                        effective_cap, free, total, has_idle,
                    )
                    return has_idle
                except ValueError:
                    logger.warning(
                        "Could not parse BB builder count: '%s'", text
                    )

            # Fallback: template-based detection of the idle builder icon
            if self.vision is not None:
                screenshot = self.get_screenshot()
                if screenshot is not None:
                    idle_icon = self.vision.find(
                        screenshot, "bb_builder_idle", threshold=0.72
                    )
                    if idle_icon is not None:
                        logger.info("BB idle builder detected via template.")
                        return True

            logger.warning("Could not determine BB builder status — assuming busy.")
            return False

        except Exception as exc:
            logger.error(
                "Error checking BB builder status: %s", exc, exc_info=True
            )
            return False

    # ── Versus Battle farming ───────────────────────────────────────────

    def farm_versus_until_full(self, max_battles: int = 10) -> int:
        """Farm Versus Battles until BB storages are full or max reached."""
        logger.info(
            "Farming Versus Battles (max=%d)…", max_battles
        )
        battles_fought = 0

        try:
            for i in range(max_battles):
                # Check if storages are full
                bb_resources = self.read_bb_resources()
                if self._are_bb_storages_full(bb_resources):
                    logger.info("BB storages are full — stopping Versus farming.")
                    break

                logger.info("Starting Versus Battle #%d…", i + 1)
                result = self.do_versus_battle()
                battles_fought += 1

                logger.info(
                    "Versus Battle #%d result: %s", battles_fought, result
                )

                time.sleep(self._action_delay)

            logger.info(
                "Versus farming complete: %d battles fought.", battles_fought
            )
            return battles_fought

        except Exception as exc:
            logger.error(
                "Error farming Versus Battles: %s", exc, exc_info=True
            )
            return battles_fought

    def do_versus_battle(self) -> dict:
        """Execute a single Versus Battle."""
        logger.info("⚔️ Executing Versus Battle…")
        result: dict = {
            "gold_looted": 0,
            "elixir_looted": 0,
            "success": False,
        }

        try:
            # Step 1: tap Attack button
            self.tap(*self.BB_ATTACK_BUTTON_POS)
            time.sleep(self._dialog_wait)

            # Step 2: tap Find Now
            screenshot = self.get_screenshot()
            find_btn = None
            if self.vision is not None:
                find_btn = self.vision.find(screenshot, "bb_find_now_btn", threshold=0.7)
            if find_btn is not None:
                fx, fy, fw, fh = find_btn
                self.tap(fx + fw // 2, fy + fh // 2)
            else:
                self.tap(*self.BB_FIND_NOW_POS)

            # Wait for matchmaking
            logger.info("Waiting for Versus Battle match…")
            match_found = self._wait_for_versus_match(
                timeout=self.VERSUS_MATCHMAKING_TIMEOUT
            )
            if not match_found:
                logger.warning("Versus Battle matchmaking timed out.")
                self.navigator.close_all_dialogs()
                return result

            # Step 3: deploy all pre-trained troops along one edge
            logger.info("Deploying pre-trained BB troops…")
            for idx, troop_key in enumerate(self.BB_TROOP_KEYS):
                if hasattr(self.backend, "select_troop_slot"):
                    self.backend.select_troop_slot(idx)
                else:
                    self.press_key(troop_key)
                time.sleep(0.2)

                for point in self.BB_EDGE_DEPLOY_POINTS_NORM:
                    self.tap(*point, checks=False)
                    time.sleep(self.BB_DEPLOY_CLICK_INTERVAL)

                time.sleep(0.3)

            # Step 4: wait for attack phase to end
            logger.info("Waiting for BB attack phase to end…")
            self._wait_for_versus_end(timeout=self.VERSUS_BATTLE_TIMEOUT)

            # Step 5: wait for opponent's result and final results screen
            time.sleep(self._screen_load_wait)

            # Step 6: read results and dismiss
            time.sleep(5.0)

            # Try to read loot from results
            try:
                gold_img = self.capture_region(*self.BB_RESULT_GOLD_REGION)
                result["gold_looted"] = self.ocr.read_number(gold_img)
            except Exception:
                pass

            try:
                elixir_img = self.capture_region(*self.BB_RESULT_ELIXIR_REGION)
                result["elixir_looted"] = self.ocr.read_number(elixir_img)
            except Exception:
                pass

            result["success"] = True

            # Tap OK / Return to dismiss results
            self._dismiss_versus_results()

            logger.info(
                "⚔️ Versus Battle complete: Gold=%d, Elixir=%d",
                result["gold_looted"], result["elixir_looted"],
            )

        except Exception as exc:
            logger.error(
                "Error during Versus Battle: %s", exc, exc_info=True
            )
            self.navigator.close_all_dialogs()

        return result

    def _wait_for_versus_match(self, timeout: int = 60) -> bool:
        """Wait for Versus Battle matchmaking to complete."""
        start = time.time()
        while time.time() - start < timeout:
            screenshot = self.get_screenshot()

            if self.vision is not None:
                # Look for the attack/deploy phase (troop bar appears)
                troop_bar = self.vision.find(
                    screenshot, "bb_troop_bar", threshold=0.6
                )
                if troop_bar is not None:
                    logger.info("Versus match found — troop bar detected.")
                    return True

                # Check for "VS" or match indicator
                vs_indicator = self.vision.find(
                    screenshot, "vs_indicator", threshold=0.7
                )
                if vs_indicator is not None:
                    time.sleep(3.0)
                    return True

            time.sleep(2.0)

        return False

    def _wait_for_versus_end(self, timeout: int = 180) -> bool:
        """Wait for the BB attack phase to end."""
        start = time.time()
        while time.time() - start < timeout:
            screenshot = self.get_screenshot()

            if self.vision is not None:
                results = self.vision.find(
                    screenshot, "bb_versus_results", threshold=0.65
                )
                if results is not None:
                    return True

                waiting = self.vision.find(
                    screenshot, "bb_waiting_opponent", threshold=0.65
                )
                if waiting is not None:
                    return True

            time.sleep(3.0)

        return False

    def _dismiss_versus_results(self) -> None:
        """Dismiss the Versus Battle results screen."""
        try:
            for _ in range(3):
                screenshot = self.get_screenshot()

                ok_btn = None
                if self.vision is not None:
                    ok_btn = self.vision.find(screenshot, "bb_results_ok_btn", threshold=0.7)
                if ok_btn is not None:
                    ox, oy, ow, oh = ok_btn
                    self.tap(ox + ow // 2, oy + oh // 2)
                else:
                    self.tap(*self.BB_RESULTS_OK_POS)

                time.sleep(self._dialog_wait)

            time.sleep(self._screen_load_wait)
            self.navigator.close_all_dialogs()

        except Exception as exc:
            logger.error("Error dismissing Versus results: %s", exc, exc_info=True)

    # ── BB Upgrades ─────────────────────────────────────────────────────

    def start_suggested_upgrade(self) -> dict | None:
        """Start the first suggested upgrade from the BB builder menu.

        Workflow:
        1. Open BB builder menu.
        2. Find 'Suggested upgrades:' section.
        3. Tap first suggested.
        4. Verify cost is NOT gems.
        5. Confirm.
        6. Read timer.

        Returns:
            Upgrade info dict or None.
        """
        logger.info("Starting BB suggested upgrade…")
        try:
            # Open builder menu
            self.tap(*self.BB_BUILDER_ICON_POS)
            time.sleep(self._dialog_wait)

            # Check for suggested section
            area_img = self.capture_region(*self.BB_SUGGESTED_REGION)
            area_text = self.ocr.read_text(area_img).lower()

            if "suggested" not in area_text:
                logger.info("No BB suggested upgrade found.")
                self.navigator.close_all_dialogs()
                return None

            # Tap first suggested
            self.tap(*self.BB_FIRST_SUGGESTED_POS)
            time.sleep(self._dialog_wait)
            time.sleep(self._screen_load_wait)

            # Gem guard
            screenshot = self.get_screenshot()
            gem_dialog = None
            if self.vision is not None:
                gem_dialog = self.vision.find(
                    screenshot, "gem_purchase_dialog", threshold=0.75
                )
            if gem_dialog is not None:
                logger.warning("🚨 GEM COST on BB upgrade — CANCELLING!")
                self.tap(*self.BB_CANCEL_BTN_POS)
                time.sleep(self._action_delay)
                self.navigator.close_all_dialogs()
                return None

            if self.vision is not None:
                gem_icon = self.vision.find(screenshot, "gem_icon", threshold=0.8)
                if gem_icon is not None:
                    gx, gy, _, _ = gem_icon
                    cx, cy, cw, ch = self.BB_COST_REGION
                    if cx <= gx <= cx + cw and cy <= gy <= cy + ch:
                        logger.warning("🚨 Gem icon in BB cost area — CANCELLING!")
                        self.tap(*self.BB_CANCEL_BTN_POS)
                        time.sleep(self._action_delay)
                        self.navigator.close_all_dialogs()
                        return None

            # Read name and timer
            name_img = self.capture_region(*self.BB_NAME_REGION)
            upgrade_name = self.ocr.read_text(name_img).strip() or "BB Upgrade"

            timer_img = self.capture_region(*self.BB_TIMER_REGION)
            upgrade_timer = self.ocr.read_timer(timer_img)

            # Confirm
            logger.info(
                "Confirming BB upgrade: '%s' (timer: %s)", upgrade_name, upgrade_timer
            )
            self.tap(*self.BB_CONFIRM_BTN_POS)
            time.sleep(self._dialog_wait)
            self.navigator.close_all_dialogs()

            result = {
                "name": upgrade_name,
                "timer": upgrade_timer if upgrade_timer else timedelta(0),
            }
            logger.info("✅ BB upgrade started: %s", result)
            return result

        except Exception as exc:
            logger.error("Error starting BB upgrade: %s", exc, exc_info=True)
            self.navigator.close_all_dialogs()
            return None

    # ── Star Lab research ───────────────────────────────────────────────

    def start_star_lab_research(self) -> dict | None:
        """Open the BB Star Lab and start suggested research."""
        if not self._star_lab_research:
            logger.info("BB Star Lab research is disabled — skipping.")
            return None

        logger.info("Starting Star Lab research…")
        try:
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            # Find Star Lab building
            screenshot = self.get_screenshot()
            lab_match = None
            if self.vision is not None:
                lab_match = self.vision.find(screenshot, "star_lab", threshold=0.7)
            if lab_match is not None:
                lx, ly, lw, lh = lab_match
                self.tap(lx + lw // 2, ly + lh // 2)
            else:
                self.tap(*self.STAR_LAB_POS)

            time.sleep(self._dialog_wait)

            # Look for research button
            screenshot = self.get_screenshot()
            res_btn = None
            if self.vision is not None:
                res_btn = self.vision.find(screenshot, "star_lab_research_btn", threshold=0.7)
            if res_btn is not None:
                rx, ry, rw, rh = res_btn
                self.tap(rx + rw // 2, ry + rh // 2)
            else:
                self.tap(*self.STAR_LAB_RESEARCH_BTN_POS)

            time.sleep(self._screen_load_wait)

            # Check for suggested research
            area_img = self.capture_region(*self.STAR_LAB_SUGGESTED_REGION)
            area_text = self.ocr.read_text(area_img).lower()

            if "suggested" not in area_text:
                logger.info("No suggested Star Lab research found.")
                self.navigator.close_all_dialogs()
                return None

            # Tap first suggested
            self.tap(*self.STAR_LAB_FIRST_SUGGESTED_POS)
            time.sleep(self._dialog_wait)
            time.sleep(self._screen_load_wait)

            # Gem guard
            screenshot = self.get_screenshot()
            gem_dialog = None
            if self.vision is not None:
                gem_dialog = self.vision.find(
                    screenshot, "gem_purchase_dialog", threshold=0.75
                )
            if gem_dialog is not None:
                logger.warning("🚨 GEM COST on Star Lab research — CANCELLING!")
                self.tap(*self.STAR_LAB_CANCEL_POS)
                time.sleep(self._action_delay)
                self.navigator.close_all_dialogs()
                return None

            if self.vision is not None:
                gem_icon = self.vision.find(screenshot, "gem_icon", threshold=0.8)
                if gem_icon is not None:
                    gx, gy, _, _ = gem_icon
                    cx, cy, cw, ch = self.STAR_LAB_COST_REGION
                    if cx <= gx <= cx + cw and cy <= gy <= cy + ch:
                        logger.warning("🚨 Gem icon in Star Lab cost — CANCELLING!")
                        self.tap(*self.STAR_LAB_CANCEL_POS)
                        time.sleep(self._action_delay)
                        self.navigator.close_all_dialogs()
                        return None

            # Read name and timer
            name_img = self.capture_region(*self.STAR_LAB_NAME_REGION)
            research_name = self.ocr.read_text(name_img).strip() or "BB Research"

            timer_img = self.capture_region(*self.STAR_LAB_TIMER_REGION)
            research_timer = self.ocr.read_timer(timer_img)

            # Confirm
            logger.info(
                "Confirming Star Lab research: '%s' (timer: %s)",
                research_name, research_timer,
            )
            self.tap(*self.STAR_LAB_CONFIRM_POS)
            time.sleep(self._dialog_wait)
            self.navigator.close_all_dialogs()

            result = {
                "name": research_name,
                "timer": research_timer if research_timer else timedelta(0),
            }
            logger.info("✅ Star Lab research started: %s", result)
            return result

        except Exception as exc:
            logger.error("Error starting Star Lab research: %s", exc, exc_info=True)
            self.navigator.close_all_dialogs()
            return None

    # ── Clock Tower ─────────────────────────────────────────────────────

    def boost_clock_tower(self) -> bool:
        """Boost the Clock Tower (FREE — no gem cost)."""
        logger.info("Attempting to boost Clock Tower…")
        try:
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            # Find Clock Tower
            screenshot = self.get_screenshot()
            ct_match = None
            if self.vision is not None:
                ct_match = self.vision.find(screenshot, "bb_clock_tower", threshold=0.7)
            if ct_match is not None:
                cx, cy, cw, ch = ct_match
                self.tap(cx + cw // 2, cy + ch // 2)
            else:
                self.tap(*self.CLOCK_TOWER_POS)

            time.sleep(self._dialog_wait)

            # Look for Boost button
            screenshot = self.get_screenshot()
            boost_btn = None
            if self.vision is not None:
                boost_btn = self.vision.find(
                    screenshot, "bb_clock_tower_boost", threshold=0.7
                )
            if boost_btn is not None:
                bx, by, bw, bh = boost_btn
                boost_x = bx + bw // 2
                boost_y = by + bh // 2
            else:
                boost_x, boost_y = self.CLOCK_TOWER_BOOST_BTN_POS

            # Safety gem check (even though it's free)
            gem_dialog = None
            if self.vision is not None:
                gem_dialog = self.vision.find(
                    screenshot, "gem_purchase_dialog", threshold=0.75
                )
            if gem_dialog is not None:
                logger.warning(
                    "🚨 Unexpected gem dialog on Clock Tower — CANCELLING!"
                )
                self.navigator.close_all_dialogs()
                return False

            # Tap boost
            self.tap(boost_x, boost_y)
            time.sleep(self._dialog_wait)

            # Verify boost activated (look for boost active indicator)
            screenshot = self.get_screenshot()
            boost_active = None
            if self.vision is not None:
                boost_active = self.vision.find(
                    screenshot, "bb_clock_tower_active", threshold=0.65
                )
            if boost_active is not None:
                logger.info("✅ Clock Tower boost activated!")
                self.navigator.close_all_dialogs()
                return True

            logger.info("Clock Tower boost attempted (could not verify).")
            self.navigator.close_all_dialogs()
            return True

        except Exception as exc:
            logger.error("Error boosting Clock Tower: %s", exc, exc_info=True)
            self.navigator.close_all_dialogs()
            return False

    # ── BB resource reading ─────────────────────────────────────────────

    def read_bb_resources(self) -> dict:
        """Read Builder Gold and Builder Elixir from the BB HUD."""
        resources: dict = {"builder_gold": 0, "builder_elixir": 0}

        try:
            gold_img = self.capture_region(*self.BB_GOLD_REGION)
            resources["builder_gold"] = self.ocr.read_number(gold_img)
        except Exception as exc:
            logger.debug("Failed to read BB gold: %s", exc)

        try:
            elixir_img = self.capture_region(*self.BB_ELIXIR_REGION)
            resources["builder_elixir"] = self.ocr.read_number(elixir_img)
        except Exception as exc:
            logger.debug("Failed to read BB elixir: %s", exc)

        logger.info(
            "BB resources: Gold=%d, Elixir=%d",
            resources["builder_gold"], resources["builder_elixir"],
        )
        return resources

    def _are_bb_storages_full(self, resources: dict | None = None) -> bool:
        """Check if BB storages are approximately full."""
        if resources is None:
            resources = self.read_bb_resources()

        bb_full_threshold = 2_000_000
        gold = resources.get("builder_gold", 0)
        elixir = resources.get("builder_elixir", 0)

        is_full = gold >= bb_full_threshold and elixir >= bb_full_threshold
        logger.debug(
            "BB storage check: gold=%d, elixir=%d, threshold=%d → full=%s",
            gold, elixir, bb_full_threshold, is_full,
        )
        return is_full
