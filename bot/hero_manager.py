"""
Hero Manager — Hero Hall upgrades for all heroes.
==================================================
Manages hero upgrades through the Hero Hall building. Supports all six
heroes available at TH16: Barbarian King, Archer Queen, Minion Prince,
Grand Warden, Royal Champion, and Dragon Duke.

Upgrades are prioritised in the order listed in config.yaml.
Uses gem-guard safety checks before confirming any upgrade.
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


class HeroManager:
    """Manages hero upgrades via the Hero Hall building."""

    # ── Hero priority order ─────────────────────────────────────────────
    # Upgrades follow this priority. First idle affordable hero wins.
    # Dragon Duke is last — BK/AQ/MP/GW/RC get priority over DE spending.
    HERO_PRIORITY: list[str] = [
        "Barbarian King",
        "Archer Queen",
        "Minion Prince",
        "Grand Warden",
        "Royal Champion",
        "Dragon Duke",
    ]

    # Short names for matching OCR output (case-insensitive)
    HERO_SHORT_NAMES: dict[str, str] = {
        "king": "Barbarian King",
        "barbarian": "Barbarian King",
        "queen": "Archer Queen",
        "archer": "Archer Queen",
        "minion": "Minion Prince",
        "prince": "Minion Prince",
        "warden": "Grand Warden",
        "grand": "Grand Warden",
        "champion": "Royal Champion",
        "rc": "Royal Champion",
        "royal": "Royal Champion",
        "duke": "Dragon Duke",
        "dragon": "Dragon Duke",
        "dragon duke": "Dragon Duke",
    }

    # ── Screen coordinate constants (1920×1080) ─────────────────────────

    # Hero Hall building fallback position
    HERO_HALL_POS: tuple[int, int] = (800, 500)
    HERO_HALL_POS_NORM: tuple[float, float] = (800 / 1920, 500 / 1080)

    # Hero Hall info panel — hero list region
    HERO_LIST_REGION: tuple[int, int, int, int] = (350, 250, 750, 450)  # (x, y, w, h)
    HERO_LIST_REGION_NORM: tuple[float, float, float, float] = (350 / 1920, 250 / 1080, 750 / 1920, 450 / 1080)

    # Individual hero entry regions (Y offsets from top of hero list)
    HERO_ENTRY_HEIGHT: int = 80
    HERO_ENTRY_X: int = 370
    HERO_ENTRY_WIDTH: int = 700

    # Hero name region within an entry (left portion)
    HERO_NAME_OFFSET_X: int = 0
    HERO_NAME_WIDTH: int = 250
    HERO_NAME_HEIGHT: int = 30

    # Hero level region within an entry
    HERO_LEVEL_OFFSET_X: int = 260
    HERO_LEVEL_WIDTH: int = 80
    HERO_LEVEL_HEIGHT: int = 30

    # Hero status region within an entry (idle/upgrading)
    HERO_STATUS_OFFSET_X: int = 350
    HERO_STATUS_WIDTH: int = 200
    HERO_STATUS_HEIGHT: int = 30

    # Hero upgrade cost region within an entry
    HERO_COST_OFFSET_X: int = 550
    HERO_COST_WIDTH: int = 120
    HERO_COST_HEIGHT: int = 30

    # Hero entry Y start (within the list region)
    HERO_LIST_Y_START: int = 280

    # Upgrade button position in the hero detail/upgrade dialog
    HERO_UPGRADE_BTN_POS: tuple[int, int] = (960, 640)
    HERO_UPGRADE_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 640 / 1080)

    # Confirm upgrade button
    HERO_CONFIRM_BTN_POS: tuple[int, int] = (960, 580)
    HERO_CONFIRM_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 580 / 1080)

    # Cancel button in upgrade dialog
    HERO_CANCEL_BTN_POS: tuple[int, int] = (960, 700)
    HERO_CANCEL_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 700 / 1080)

    # Timer region in upgrade confirmation dialog
    HERO_TIMER_REGION: tuple[int, int, int, int] = (800, 400, 320, 40)  # (x, y, w, h)
    HERO_TIMER_REGION_NORM: tuple[float, float, float, float] = (800 / 1920, 400 / 1080, 320 / 1920, 40 / 1080)

    # Cost region in upgrade confirmation dialog
    HERO_COST_CONFIRM_REGION: tuple[int, int, int, int] = (870, 560, 180, 40)  # (x, y, w, h)
    HERO_COST_CONFIRM_REGION_NORM: tuple[float, float, float, float] = (870 / 1920, 560 / 1080, 180 / 1920, 40 / 1080)

    # Name region in upgrade confirmation dialog
    HERO_NAME_CONFIRM_REGION: tuple[int, int, int, int] = (780, 280, 360, 40)  # (x, y, w, h)
    HERO_NAME_CONFIRM_REGION_NORM: tuple[float, float, float, float] = (780 / 1920, 280 / 1080, 360 / 1920, 40 / 1080)

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
        """Initialise HeroManager."""
        self.config = config
        self.backend = backend
        self.screen = screen
        self.vision = vision
        self.ocr = ocr
        self.input_ctrl = input_ctrl
        self.navigator = navigator
        self.resource_reader = resource_reader

        # Config
        self._auto_upgrade: bool = config.get("heroes", {}).get("auto_upgrade", True)

        timing_cfg = config.get("timing", {})
        self._action_delay: float = timing_cfg.get("between_actions_delay_sec", 1.5)
        self._dialog_wait: float = timing_cfg.get("dialog_animation_wait_sec", 0.8)
        self._screen_load_wait: float = timing_cfg.get("screen_load_wait_sec", 3.0)

        logger.info("HeroManager initialised (auto_upgrade=%s).", self._auto_upgrade)

    def get_screenshot(self):
        """Capture screenshot via backend or screen fallback."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None:
            return self.screen.capture_screenshot()
        raise RuntimeError("No backend or screen capture available in HeroManager")

    def tap(self, x: float | int, y: float | int, checks: bool = True) -> bool:
        """Tap at coordinate via backend or input_ctrl fallback."""
        if self.backend is not None:
            return self.backend.safe_click(x, y, checks=checks)
        if self.input_ctrl is not None:
            return self.input_ctrl.safe_click(x, y, checks=checks)
        raise RuntimeError("No backend or input controller available in HeroManager")

    def capture_region(self, x: float | int, y: float | int, w: float | int, h: float | int):
        """Capture a region as numpy array."""
        img = self.get_screenshot()
        if x <= 1.0 and y <= 1.0 and w <= 1.0 and h <= 1.0:
            return crop_normalized(img, (float(x), float(y), float(w), float(h)))
        H, W = img.shape[:2]
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2, y2 = min(W, int(x + w)), min(H, int(y + h))
        return img[y1:y2, x1:x2]

    # ── Hero Hall navigation ────────────────────────────────────────────

    def open_hero_hall(self) -> bool:
        """Find and tap the Hero Hall building on the base."""
        logger.info("Opening Hero Hall…")
        try:
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            # Try template matching first
            screenshot = self.get_screenshot()
            hh_match = None
            if self.vision is not None:
                hh_match = self.vision.find(screenshot, "hero_hall", threshold=0.7)

            if hh_match is not None:
                hx, hy, hw, hh = hh_match
                tap_x = hx + hw // 2
                tap_y = hy + hh // 2
                logger.info("Found Hero Hall at (%d, %d).", tap_x, tap_y)
            else:
                tap_x, tap_y = self.HERO_HALL_POS
                logger.info(
                    "Hero Hall not found by template — fallback (%d, %d).",
                    tap_x, tap_y,
                )

            self.tap(tap_x, tap_y)
            time.sleep(self._dialog_wait)

            # Verify Hero Hall opened — look for hero list or Hall text
            screenshot = self.get_screenshot()
            if self.vision is not None:
                hall_ui = self.vision.find(screenshot, "hero_hall_ui", threshold=0.65)
                if hall_ui is not None:
                    logger.info("Hero Hall UI detected.")
                    return True

            # Fallback: OCR check
            region_img = self.capture_region(*self.HERO_LIST_REGION)
            text = (self.ocr.read_text(region_img) or "").lower()
            if any(keyword in text for keyword in ["king", "queen", "hero", "upgrade"]):
                logger.info("Hero Hall detected via OCR.")
                return True

            logger.warning("Could not verify Hero Hall opened.")
            return False

        except Exception as exc:
            logger.error("Error opening Hero Hall: %s", exc, exc_info=True)
            return False

    # ── Hero status reading ─────────────────────────────────────────────

    def get_hero_status(self) -> list[dict]:
        """Read each hero's status from the Hero Hall."""
        logger.info("Reading hero statuses…")
        heroes: list[dict] = []

        for i in range(len(self.HERO_PRIORITY)):
            try:
                entry_y = self.HERO_LIST_Y_START + i * self.HERO_ENTRY_HEIGHT

                # Read hero name
                name_img = self.capture_region(
                    self.HERO_ENTRY_X + self.HERO_NAME_OFFSET_X,
                    entry_y,
                    self.HERO_NAME_WIDTH,
                    self.HERO_NAME_HEIGHT,
                )
                name_text = (self.ocr.read_text(name_img) or "").strip()

                if not name_text or len(name_text) < 2:
                    continue

                # Read level
                level_img = self.capture_region(
                    self.HERO_ENTRY_X + self.HERO_LEVEL_OFFSET_X,
                    entry_y,
                    self.HERO_LEVEL_WIDTH,
                    self.HERO_LEVEL_HEIGHT,
                )
                level_text = (self.ocr.read_text(level_img) or "").strip()
                try:
                    level = int("".join(c for c in level_text if c.isdigit()))
                except ValueError:
                    level = 0

                # Read status
                status_img = self.capture_region(
                    self.HERO_ENTRY_X + self.HERO_STATUS_OFFSET_X,
                    entry_y,
                    self.HERO_STATUS_WIDTH,
                    self.HERO_STATUS_HEIGHT,
                )
                status_text = (self.ocr.read_text(status_img) or "").strip().lower()

                if "upgrading" in status_text or "upgrade" in status_text:
                    status = "upgrading"
                elif "sleep" in status_text:
                    status = "sleeping"
                else:
                    status = "idle"

                # Read cost
                cost_img = self.capture_region(
                    self.HERO_ENTRY_X + self.HERO_COST_OFFSET_X,
                    entry_y,
                    self.HERO_COST_WIDTH,
                    self.HERO_COST_HEIGHT,
                )
                cost_text = (self.ocr.read_text(cost_img) or "").strip()

                # Resolve canonical name
                canonical_name = self._resolve_hero_name(name_text)

                hero_entry = {
                    "name": canonical_name,
                    "level": level,
                    "status": status,
                    "cost": cost_text,
                }
                heroes.append(hero_entry)
                logger.info(
                    "  Hero: %s Lv%d [%s] cost=%s",
                    canonical_name, level, status, cost_text,
                )

            except Exception as exc:
                logger.warning("Failed to parse hero entry: %s", exc)
                continue

        logger.info("Total heroes read: %d", len(heroes))
        return heroes

    def _resolve_hero_name(self, ocr_name: str) -> str:
        """Match OCR text to a canonical hero name.

        Args:
            ocr_name: Raw OCR text from the hero entry.

        Returns:
            Canonical hero name or the original text if no match.
        """
        lower = ocr_name.lower()
        for keyword, canonical in self.HERO_SHORT_NAMES.items():
            if keyword in lower:
                return canonical
        return ocr_name

    # ── Hero upgrades ───────────────────────────────────────────────────

    def upgrade_next_hero(self) -> dict | None:
        """Find and upgrade the first idle, affordable hero.

        Priority order: King → Queen → Minion Prince → Warden → RC.

        Workflow:
        1. Open Hero Hall.
        2. Read all hero statuses.
        3. Find first idle hero in priority order.
        4. Tap that hero's upgrade button.
        5. Verify cost is DE, not gems (gem guard).
        6. Confirm upgrade.
        7. Return upgrade info.

        Returns:
            Dict with upgrade info, or None if no hero was upgraded.
        """
        if not self._auto_upgrade:
            logger.info("Hero auto-upgrade is disabled — skipping.")
            return None

        logger.info("Looking for upgradeable hero…")
        try:
            # Open Hero Hall
            if not self.open_hero_hall():
                logger.warning("Cannot upgrade hero — Hero Hall won't open.")
                return None

            time.sleep(self._screen_load_wait)

            # Read hero statuses
            heroes = self.get_hero_status()
            if not heroes:
                logger.info("No hero data read — cannot upgrade.")
                self.navigator.close_all_dialogs()
                return None

            # Find first idle hero in priority order
            target_hero: dict | None = None
            target_index: int = -1

            for priority_name in self.HERO_PRIORITY:
                for idx, hero in enumerate(heroes):
                    if hero["name"] == priority_name and hero["status"] == "idle":
                        target_hero = hero
                        target_index = idx
                        break
                if target_hero is not None:
                    break

            if target_hero is None:
                logger.info("No idle hero found for upgrade.")
                self.navigator.close_all_dialogs()
                return None

            logger.info(
                "Target hero for upgrade: %s Lv%d (cost=%s)",
                target_hero["name"],
                target_hero["level"],
                target_hero["cost"],
            )

            # Tap the hero entry to open its upgrade screen
            entry_y = self.HERO_LIST_Y_START + target_index * self.HERO_ENTRY_HEIGHT
            tap_x = self.HERO_ENTRY_X + self.HERO_ENTRY_WIDTH // 2
            tap_y = entry_y + self.HERO_ENTRY_HEIGHT // 2

            self.tap(tap_x, tap_y)
            time.sleep(self._dialog_wait)

            # Look for and tap the upgrade button
            screenshot = self.get_screenshot()
            upg_btn = None
            if self.vision is not None:
                upg_btn = self.vision.find(screenshot, "hero_upgrade_btn", threshold=0.7)
            if upg_btn is not None:
                ux, uy, uw, uh = upg_btn
                self.tap(ux + uw // 2, uy + uh // 2)
            else:
                self.tap(*self.HERO_UPGRADE_BTN_POS)

            time.sleep(self._dialog_wait)
            time.sleep(self._screen_load_wait)

            # Gem guard: check for gem purchase dialog
            screenshot = self.get_screenshot()
            gem_dialog = None
            if self.vision is not None:
                gem_dialog = self.vision.find(
                    screenshot, "gem_purchase_dialog", threshold=0.75
                )
            if gem_dialog is not None:
                logger.warning("🚨 GEM COST detected on hero upgrade — CANCELLING!")
                self.tap(*self.HERO_CANCEL_BTN_POS)
                time.sleep(self._action_delay)
                self.navigator.close_all_dialogs()
                return None

            # Check for gem icon in cost area
            if self.vision is not None:
                gem_icon = self.vision.find(screenshot, "gem_icon", threshold=0.8)
                if gem_icon is not None:
                    gx, gy, _, _ = gem_icon
                    cx, cy, cw, ch = self.HERO_COST_CONFIRM_REGION
                    if cx <= gx <= cx + cw and cy <= gy <= cy + ch:
                        logger.warning("🚨 Gem icon in hero cost area — CANCELLING!")
                        self.tap(*self.HERO_CANCEL_BTN_POS)
                        time.sleep(self._action_delay)
                        self.navigator.close_all_dialogs()
                        return None

            # Read name and timer from confirmation dialog
            name_img = self.capture_region(*self.HERO_NAME_CONFIRM_REGION)
            upgrade_name = (self.ocr.read_text(name_img) or "").strip()
            if not upgrade_name:
                upgrade_name = target_hero["name"]

            timer_img = self.capture_region(*self.HERO_TIMER_REGION)
            upgrade_timer = self.ocr.read_timer(timer_img)

            # Confirm upgrade
            logger.info(
                "Confirming hero upgrade: %s (timer: %s)",
                upgrade_name, upgrade_timer,
            )
            self.tap(*self.HERO_CONFIRM_BTN_POS)
            time.sleep(self._dialog_wait)
            self.navigator.close_all_dialogs()

            result = {
                "name": upgrade_name,
                "level": target_hero["level"],
                "cost": target_hero["cost"],
                "timer": upgrade_timer if upgrade_timer else timedelta(0),
            }
            logger.info("✅ Hero upgrade started: %s", result)
            return result

        except Exception as exc:
            logger.error("Error upgrading hero: %s", exc, exc_info=True)
            self.navigator.close_all_dialogs()
            return None

    def is_any_hero_upgradeable(self, current_de: int) -> bool:
        """Check if any hero can be upgraded with the current DE.

        Args:
            current_de: Current Dark Elixir amount.

        Returns:
            True if at least one hero is idle and affordable.
        """
        logger.info("Checking if any hero is upgradeable (DE=%d)…", current_de)
        try:
            if not self.open_hero_hall():
                logger.warning("Cannot check heroes — Hero Hall won't open.")
                return False

            time.sleep(self._screen_load_wait)
            heroes = self.get_hero_status()
            self.navigator.close_all_dialogs()

            for hero in heroes:
                if hero["status"] != "idle":
                    continue

                # Try to parse the cost
                cost_text = hero.get("cost", "0")
                try:
                    cost_value = int("".join(c for c in cost_text if c.isdigit()))
                except ValueError:
                    cost_value = 0

                if cost_value > 0 and current_de >= cost_value:
                    logger.info(
                        "Hero '%s' is upgradeable (cost=%d, DE=%d).",
                        hero["name"], cost_value, current_de,
                    )
                    return True

            logger.info("No hero is upgradeable with DE=%d.", current_de)
            return False

        except Exception as exc:
            logger.error("Error checking hero upgradeability: %s", exc, exc_info=True)
            return False
