"""
Pet Manager — Pet House upgrades for all pets.
==================================================
Manages pet upgrades through the Pet House building. Supports all ten
pets available at TH16.

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


class PetManager:
    """Manages pet upgrades via the Pet House building."""

    # ── Pet priority order ──────────────────────────────────────────────
    # Upgrades follow this priority. First idle affordable pet wins.
    PET_PRIORITY: list[str] = [
        "L.A.S.S.I",
        "Electro Owl",
        "Mighty Yak",
        "Unicorn",
        "Frosty",
        "Diggy",
        "Poison Lizard",
        "Phoenix",
        "Spirit Fox",
        "Angry Jelly",
    ]

    # Short names for matching OCR output (case-insensitive)
    PET_SHORT_NAMES: dict[str, str] = {
        "lassi": "L.A.S.S.I",
        "l.a.s.s.i": "L.A.S.S.I",
        "owl": "Electro Owl",
        "electro": "Electro Owl",
        "yak": "Mighty Yak",
        "mighty": "Mighty Yak",
        "unicorn": "Unicorn",
        "frosty": "Frosty",
        "diggy": "Diggy",
        "poison": "Poison Lizard",
        "lizard": "Poison Lizard",
        "phoenix": "Phoenix",
        "fox": "Spirit Fox",
        "spirit": "Spirit Fox",
        "jelly": "Angry Jelly",
        "angry": "Angry Jelly",
    }

    # ── Screen coordinate constants (1920×1080) ─────────────────────────

    # Pet House building fallback position
    PET_HOUSE_POS: tuple[int, int] = (650, 450)
    PET_HOUSE_POS_NORM: tuple[float, float] = (650 / 1920, 450 / 1080)

    # Pet House info panel — pet list region
    PET_LIST_REGION: tuple[int, int, int, int] = (350, 250, 750, 450)  # (x, y, w, h)
    PET_LIST_REGION_NORM: tuple[float, float, float, float] = (350 / 1920, 250 / 1080, 750 / 1920, 450 / 1080)

    # Individual pet entry regions (Y offsets from top of pet list)
    PET_ENTRY_HEIGHT: int = 75
    PET_ENTRY_X: int = 370
    PET_ENTRY_WIDTH: int = 700

    # Pet name region within an entry (left portion)
    PET_NAME_OFFSET_X: int = 0
    PET_NAME_WIDTH: int = 250
    PET_NAME_HEIGHT: int = 30

    # Pet level region within an entry
    PET_LEVEL_OFFSET_X: int = 260
    PET_LEVEL_WIDTH: int = 80
    PET_LEVEL_HEIGHT: int = 30

    # Pet status region within an entry (idle/upgrading)
    PET_STATUS_OFFSET_X: int = 350
    PET_STATUS_WIDTH: int = 200
    PET_STATUS_HEIGHT: int = 30

    # Pet upgrade cost region within an entry
    PET_COST_OFFSET_X: int = 550
    PET_COST_WIDTH: int = 120
    PET_COST_HEIGHT: int = 30

    # Pet entry Y start (within the list region)
    PET_LIST_Y_START: int = 280

    # Upgrade button position in the pet detail/upgrade dialog
    PET_UPGRADE_BTN_POS: tuple[int, int] = (960, 640)
    PET_UPGRADE_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 640 / 1080)

    # Confirm upgrade button
    PET_CONFIRM_BTN_POS: tuple[int, int] = (960, 580)
    PET_CONFIRM_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 580 / 1080)

    # Cancel button in upgrade dialog
    PET_CANCEL_BTN_POS: tuple[int, int] = (960, 700)
    PET_CANCEL_BTN_POS_NORM: tuple[float, float] = (960 / 1920, 700 / 1080)

    # Timer region in upgrade confirmation dialog
    PET_TIMER_REGION: tuple[int, int, int, int] = (800, 400, 320, 40)  # (x, y, w, h)
    PET_TIMER_REGION_NORM: tuple[float, float, float, float] = (800 / 1920, 400 / 1080, 320 / 1920, 40 / 1080)

    # Cost region in upgrade confirmation dialog
    PET_COST_CONFIRM_REGION: tuple[int, int, int, int] = (870, 560, 180, 40)  # (x, y, w, h)
    PET_COST_CONFIRM_REGION_NORM: tuple[float, float, float, float] = (870 / 1920, 560 / 1080, 180 / 1920, 40 / 1080)

    # Name region in upgrade confirmation dialog
    PET_NAME_CONFIRM_REGION: tuple[int, int, int, int] = (780, 280, 360, 40)  # (x, y, w, h)
    PET_NAME_CONFIRM_REGION_NORM: tuple[float, float, float, float] = (780 / 1920, 280 / 1080, 360 / 1920, 40 / 1080)

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
        """Initialise PetManager."""
        self.config = config
        self.backend = backend
        self.screen = screen
        self.vision = vision
        self.ocr = ocr
        self.input_ctrl = input_ctrl
        self.navigator = navigator
        self.resource_reader = resource_reader

        # Config
        self._auto_upgrade: bool = config.get("pets", {}).get("auto_upgrade", True)

        timing_cfg = config.get("timing", {})
        self._action_delay: float = timing_cfg.get("between_actions_delay_sec", 1.5)
        self._dialog_wait: float = timing_cfg.get("dialog_animation_wait_sec", 0.8)
        self._screen_load_wait: float = timing_cfg.get("screen_load_wait_sec", 3.0)

        logger.info("PetManager initialised (auto_upgrade=%s).", self._auto_upgrade)

    def get_screenshot(self):
        """Capture screenshot via backend or screen fallback."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None:
            return self.screen.capture_screenshot()
        raise RuntimeError("No backend or screen capture available in PetManager")

    def tap(self, x: float | int, y: float | int, checks: bool = True) -> bool:
        """Tap at coordinate via backend or input_ctrl fallback."""
        if self.backend is not None:
            return self.backend.safe_click(x, y, checks=checks)
        if self.input_ctrl is not None:
            return self.input_ctrl.safe_click(x, y, checks=checks)
        raise RuntimeError("No backend or input controller available in PetManager")

    def capture_region(self, x: float | int, y: float | int, w: float | int, h: float | int):
        """Capture a region as numpy array."""
        img = self.get_screenshot()
        if x <= 1.0 and y <= 1.0 and w <= 1.0 and h <= 1.0:
            return crop_normalized(img, (float(x), float(y), float(w), float(h)))
        H, W = img.shape[:2]
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2, y2 = min(W, int(x + w)), min(H, int(y + h))
        return img[y1:y2, x1:x2]

    # ── Pet House navigation ────────────────────────────────────────────

    def open_pet_house(self) -> bool:
        """Find and tap the Pet House building on the base."""
        logger.info("Opening Pet House…")
        try:
            self.navigator.close_all_dialogs()
            time.sleep(self._action_delay)

            # Try template matching first
            screenshot = self.get_screenshot()
            ph_match = None
            if self.vision is not None:
                ph_match = self.vision.find(screenshot, "pet_house", threshold=0.7)

            if ph_match is not None:
                px, py, pw, ph = ph_match
                tap_x = px + pw // 2
                tap_y = py + ph // 2
                logger.info("Found Pet House at (%d, %d).", tap_x, tap_y)
            else:
                tap_x, tap_y = self.PET_HOUSE_POS
                logger.info(
                    "Pet House not found by template — fallback (%d, %d).",
                    tap_x, tap_y,
                )

            self.tap(tap_x, tap_y)
            time.sleep(self._dialog_wait)

            # Verify Pet House opened — look for pet list or House text
            screenshot = self.get_screenshot()
            if self.vision is not None:
                house_ui = self.vision.find(screenshot, "pet_house_ui", threshold=0.65)
                if house_ui is not None:
                    logger.info("Pet House UI detected.")
                    return True

            # Fallback: OCR check
            region_img = self.capture_region(*self.PET_LIST_REGION)
            text = (self.ocr.read_text(region_img) or "").lower()
            if any(keyword in text for keyword in ["pet", "unicorn", "upgrade"]):
                logger.info("Pet House detected via OCR.")
                return True

            logger.warning("Could not verify Pet House opened.")
            return False

        except Exception as exc:
            logger.error("Error opening Pet House: %s", exc, exc_info=True)
            return False

    # ── Pet status reading ──────────────────────────────────────────────

    def get_pet_status(self) -> list[dict]:
        """Read each pet's status from the Pet House."""
        logger.info("Reading pet statuses…")
        pets: list[dict] = []

        for i in range(len(self.PET_PRIORITY)):
            try:
                entry_y = self.PET_LIST_Y_START + i * self.PET_ENTRY_HEIGHT

                # Read pet name
                name_img = self.capture_region(
                    self.PET_ENTRY_X + self.PET_NAME_OFFSET_X,
                    entry_y,
                    self.PET_NAME_WIDTH,
                    self.PET_NAME_HEIGHT,
                )
                name_text = (self.ocr.read_text(name_img) or "").strip()

                if not name_text or len(name_text) < 2:
                    continue

                # Read level
                level_img = self.capture_region(
                    self.PET_ENTRY_X + self.PET_LEVEL_OFFSET_X,
                    entry_y,
                    self.PET_LEVEL_WIDTH,
                    self.PET_LEVEL_HEIGHT,
                )
                level_text = (self.ocr.read_text(level_img) or "").strip()
                try:
                    level = int("".join(c for c in level_text if c.isdigit()))
                except ValueError:
                    level = 0

                # Read status
                status_img = self.capture_region(
                    self.PET_ENTRY_X + self.PET_STATUS_OFFSET_X,
                    entry_y,
                    self.PET_STATUS_WIDTH,
                    self.PET_STATUS_HEIGHT,
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
                    self.PET_ENTRY_X + self.PET_COST_OFFSET_X,
                    entry_y,
                    self.PET_COST_WIDTH,
                    self.PET_COST_HEIGHT,
                )
                cost_text = (self.ocr.read_text(cost_img) or "").strip()

                # Resolve canonical name
                canonical_name = self._resolve_pet_name(name_text)

                pet_entry = {
                    "name": canonical_name,
                    "level": level,
                    "status": status,
                    "cost": cost_text,
                }
                pets.append(pet_entry)
                logger.info(
                    "  Pet: %s Lv%d [%s] cost=%s",
                    canonical_name, level, status, cost_text,
                )

            except Exception as exc:
                logger.warning("Failed to parse pet entry: %s", exc)
                continue

        logger.info("Total pets read: %d", len(pets))
        return pets

    def _resolve_pet_name(self, ocr_name: str) -> str:
        """Match OCR text to a canonical pet name.

        Args:
            ocr_name: Raw OCR text from the pet entry.

        Returns:
            Canonical pet name or the original text if no match.
        """
        lower = ocr_name.lower()
        for keyword, canonical in self.PET_SHORT_NAMES.items():
            if keyword in lower:
                return canonical
        return ocr_name

    # ── Pet upgrades ────────────────────────────────────────────────────

    def upgrade_next_pet(self) -> dict | None:
        """Find and upgrade the first idle, affordable pet."""
        if not self._auto_upgrade:
            logger.info("Pet auto-upgrade is disabled — skipping.")
            return None

        logger.info("Looking for upgradeable pet…")
        try:
            # Open Pet House
            if not self.open_pet_house():
                logger.warning("Cannot upgrade pet — Pet House won't open.")
                return None

            time.sleep(self._screen_load_wait)

            # Read pet statuses
            pets = self.get_pet_status()
            if not pets:
                logger.info("No pet data read — cannot upgrade.")
                self.navigator.close_all_dialogs()
                return None

            # Find first idle pet in priority order
            target_pet: dict | None = None
            target_index: int = -1

            for priority_name in self.PET_PRIORITY:
                for idx, pet in enumerate(pets):
                    if pet["name"] == priority_name and pet["status"] == "idle":
                        target_pet = pet
                        target_index = idx
                        break
                if target_pet is not None:
                    break

            if target_pet is None:
                logger.info("No idle pet found for upgrade.")
                self.navigator.close_all_dialogs()
                return None

            logger.info(
                "Target pet for upgrade: %s Lv%d (cost=%s)",
                target_pet["name"],
                target_pet["level"],
                target_pet["cost"],
            )

            # Tap the pet entry to open its upgrade screen
            entry_y = self.PET_LIST_Y_START + target_index * self.PET_ENTRY_HEIGHT
            tap_x = self.PET_ENTRY_X + self.PET_ENTRY_WIDTH // 2
            tap_y = entry_y + self.PET_ENTRY_HEIGHT // 2

            self.tap(tap_x, tap_y)
            time.sleep(self._dialog_wait)

            # Look for and tap the upgrade button
            screenshot = self.get_screenshot()
            upg_btn = None
            if self.vision is not None:
                upg_btn = self.vision.find(screenshot, "pet_upgrade_btn", threshold=0.7)
            if upg_btn is not None:
                ux, uy, uw, uh = upg_btn
                self.tap(ux + uw // 2, uy + uh // 2)
            else:
                self.tap(*self.PET_UPGRADE_BTN_POS)

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
                logger.warning("🚨 GEM COST detected on pet upgrade — CANCELLING!")
                self.tap(*self.PET_CANCEL_BTN_POS)
                time.sleep(self._action_delay)
                self.navigator.close_all_dialogs()
                return None

            # Check for gem icon in cost area
            if self.vision is not None:
                gem_icon = self.vision.find(screenshot, "gem_icon", threshold=0.8)
                if gem_icon is not None:
                    gx, gy, _, _ = gem_icon
                    cx, cy, cw, ch = self.PET_COST_CONFIRM_REGION
                    if cx <= gx <= cx + cw and cy <= gy <= cy + ch:
                        logger.warning("🚨 Gem icon in pet cost area — CANCELLING!")
                        self.tap(*self.PET_CANCEL_BTN_POS)
                        time.sleep(self._action_delay)
                        self.navigator.close_all_dialogs()
                        return None

            # Read name and timer from confirmation dialog
            name_img = self.capture_region(*self.PET_NAME_CONFIRM_REGION)
            upgrade_name = (self.ocr.read_text(name_img) or "").strip()
            if not upgrade_name:
                upgrade_name = target_pet["name"]

            timer_img = self.capture_region(*self.PET_TIMER_REGION)
            upgrade_timer = self.ocr.read_timer(timer_img)

            # Confirm upgrade
            logger.info(
                "Confirming pet upgrade: %s (timer: %s)",
                upgrade_name, upgrade_timer,
            )
            self.tap(*self.PET_CONFIRM_BTN_POS)
            time.sleep(self._dialog_wait)
            self.navigator.close_all_dialogs()

            result = {
                "name": upgrade_name,
                "level": target_pet["level"],
                "cost": target_pet["cost"],
                "timer": upgrade_timer if upgrade_timer else timedelta(0),
            }
            logger.info("✅ Pet upgrade started: %s", result)
            return result

        except Exception as exc:
            logger.error("Error upgrading pet: %s", exc, exc_info=True)
            self.navigator.close_all_dialogs()
            return None

    def is_any_pet_upgradeable(self, current_de: int) -> bool:
        """Check if any pet can be upgraded with the current DE.

        Args:
            current_de: Current Dark Elixir amount.

        Returns:
            True if at least one pet is idle and affordable.
        """
        logger.info("Checking if any pet is upgradeable (DE=%d)…", current_de)
        try:
            if not self.open_pet_house():
                logger.warning("Cannot check pets — Pet House won't open.")
                return False

            time.sleep(self._screen_load_wait)
            pets = self.get_pet_status()
            self.navigator.close_all_dialogs()

            for pet in pets:
                if pet["status"] != "idle":
                    continue

                # Try to parse the cost
                cost_text = pet.get("cost", "0")
                try:
                    cost_value = int("".join(c for c in cost_text if c.isdigit()))
                except ValueError:
                    cost_value = 0

                if cost_value > 0 and current_de >= cost_value:
                    logger.info(
                        "Pet '%s' is upgradeable (cost=%d, DE=%d).",
                        pet["name"], cost_value, current_de,
                    )
                    return True

            logger.info("No pet is upgradeable with DE=%d.", current_de)
            return False

        except Exception as exc:
            logger.error("Error checking pet upgradeability: %s", exc, exc_info=True)
            return False
