"""
Account Switcher — Supercell ID Account Management
=====================================================
Handles switching between Supercell ID linked accounts through the
in-game settings menu. Navigates: Settings → Supercell ID "Open" →
SWITCH ID → tap account in list → wait for reload.

Accounts are fully dynamic — driven by config.yaml. Any number of
accounts are supported; the switcher will scroll the list as needed.
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


class AccountSwitcher:
    """Switches between Supercell ID linked accounts.

    Performs the full account switch flow through the settings menu,
    waits for the game to fully reload, and verifies we landed on the
    correct account.

    Attributes:
        config: Bot configuration dictionary.
        backend: Backend instance for device communication.
        vision: Vision engine for template matching.
        ocr: OCR engine for reading text.
        navigator: Navigator for screen navigation.
    """

    # ── Template names for Supercell ID flow ────────────────────────────
    SCID_OPEN_BUTTON_TEMPLATE: str = "scid_open_btn"
    SWITCH_ID_BUTTON_TEMPLATE: str = "switch_id_btn"
    SCID_PANEL_TEMPLATE: str = "supercell_id_panel"

    # ── Normalized account list layout (nx, ny) ─────────────────────────
    ACCOUNT_LIST_CENTER_X_NORM: float = 0.5000
    ACCOUNT_LIST_FIRST_Y_NORM: float = 0.3056
    ACCOUNT_LIST_SPACING_Y_NORM: float = 0.0741

    # Legacy pixel values at 1920x1080
    ACCOUNT_LIST_CENTER_X: int = 960
    ACCOUNT_LIST_FIRST_Y: int = 330
    ACCOUNT_LIST_SPACING_Y: int = 80

    # ── Open button position fallback (normalized: nx, ny) ──────────────
    SCID_OPEN_BUTTON_FALLBACK_NORM: tuple[float, float] = (0.5729, 0.4167)
    SCID_OPEN_BUTTON_FALLBACK: tuple[int, int] = (1100, 450)

    # ── SWITCH ID button fallback (normalized: nx, ny) ──────────────────
    SWITCH_ID_BUTTON_FALLBACK_NORM: tuple[float, float] = (0.5000, 0.6481)
    SWITCH_ID_BUTTON_FALLBACK: tuple[int, int] = (960, 700)

    # ── Timing and thresholds ───────────────────────────────────────────
    DETECTION_THRESHOLD: float = 0.75
    MAX_RELOAD_WAIT_SEC: int = 120
    POST_SWITCH_SETTLE_SEC: float = 5.0

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
        """Initialize the AccountSwitcher.

        Args:
            config: Bot configuration dictionary.
            backend_or_screen: Backend instance (or legacy ScreenCapture).
            vision: Vision engine for template matching.
            ocr: OCR engine for reading text.
            input_ctrl: Input controller for clicks/keys.
            navigator: Navigator instance for screen navigation.
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

        # Timing from config
        timing_cfg = config.get("timing", {})
        self._action_delay: float = timing_cfg.get("between_actions_delay_sec", 1.5)
        self._screen_load_wait: float = timing_cfg.get("screen_load_wait_sec", 3.0)
        self._dialog_wait: float = timing_cfg.get("dialog_animation_wait_sec", 0.8)

        # SCID names are used to select an entry in the switcher.  Player
        # names are separately OCR-verified on the village HUD.
        self._accounts_cfg: list[dict] = config.get("accounts", [])
        self._account_names: list[str] = [
            acc.get("supercell_id_name", acc.get("name", f"Account_{i}"))
            for i, acc in enumerate(self._accounts_cfg)
        ]
        self._player_names: list[str] = [
            acc.get("player_name", acc.get("name", f"Account_{i}"))
            for i, acc in enumerate(self._accounts_cfg)
        ]

        logger.info(
            "AccountSwitcher initialized — %d accounts configured: %s",
            len(self._account_names),
            self._account_names,
        )

    def get_screenshot(self) -> np.ndarray:
        """Capture screenshot via backend or screen helper."""
        if self.backend is not None:
            return self.backend.get_screenshot()
        if self.screen is not None and hasattr(self.screen, "capture_screenshot"):
            return self.screen.capture_screenshot()
        raise RuntimeError("AccountSwitcher has no backend or screen capture handle")


    # ── Public API ──────────────────────────────────────────────────────

    def switch_to(
        self,
        account_target: int | str | dict = 0,
        account_name: Optional[str] = None,
    ) -> bool:
        """Switch to a specific account using OCR name detection (order-independent).

        Full switch flow:
            1. Navigate to Settings
            2. Tap "Open" button next to Supercell ID
            3. Wait for SCID panel to appear
            4. Tap "SWITCH ID" button
            5. Wait for account list to appear
            6. Tap on target account (detected by OCR name anywhere on screen, scrolls if needed)
            7. Wait for game to reload (Supercell logo → loading → home)
            8. Verify home screen reached
            9. Handle any post-login popups

        Args:
            account_target: Target account index, name string, or account dict.
            account_name: Optional explicit account name override.

        Returns:
            True if the account switch was successful.
        """
        if isinstance(account_target, dict):
            account_index = account_target.get("supercell_id_index", 0)
            target_name = (
                account_name
                or account_target.get("supercell_id_name")
                or account_target.get("name")
                or account_target.get("player_name")
                or f"Account_{account_index}"
            )
        elif isinstance(account_target, str):
            target_name = account_target
            account_index = self._find_index_by_name(target_name)
        else:
            account_index = int(account_target)
            target_name = account_name or self._get_account_name(account_index)

        candidates = self._get_candidate_names(account_target)
        if target_name and target_name not in candidates:
            candidates.insert(0, target_name)

        logger.info(
            "═══ Account Switch: → %s (candidates: %s, fallback slot: %d) ═══",
            target_name,
            candidates,
            account_index,
        )

        try:
            # Step 1: Navigate to Settings
            logger.info("Step 1/8: Opening Settings")
            if not self.navigator.go_to_settings():
                logger.error("AccountSwitcher: Failed to open settings")
                return False
            time.sleep(self._dialog_wait)

            # Step 2: Tap "Open" button next to Supercell ID
            logger.info("Step 2/8: Tapping Supercell ID 'Open' button")
            if not self._tap_scid_open():
                logger.error("AccountSwitcher: Failed to tap SCID Open button")
                return False
            time.sleep(self._screen_load_wait)

            # Step 3: Wait for SCID panel to appear
            logger.info("Step 3/8: Waiting for SCID panel")
            if not self._wait_for_scid_panel():
                logger.error("AccountSwitcher: SCID panel did not appear")
                return False

            # Step 4: Tap "SWITCH ID" button
            logger.info("Step 4/8: Tapping 'SWITCH ID'")
            if not self._tap_switch_id():
                logger.error("AccountSwitcher: Failed to tap SWITCH ID")
                return False
            time.sleep(self._screen_load_wait)

            # Step 5: Wait for account list to appear
            logger.info("Step 5/8: Waiting for account list")
            time.sleep(self._dialog_wait)

            # Step 6: Tap on the target account
            logger.info(
                "Step 6/8: Selecting account '%s' (fallback slot %d)",
                target_name,
                account_index,
            )
            if not self._tap_account(
                account_index=account_index,
                target_name=target_name,
                candidate_names=candidates,
            ):
                logger.error(
                    "AccountSwitcher: Failed to select account '%s'",
                    target_name,
                )
                return False

            # Step 7: Wait for game to reload
            logger.info("Step 7/8: Waiting for game to reload")
            time.sleep(self.POST_SWITCH_SETTLE_SEC)
            if not self._wait_for_game_reload():
                logger.error("AccountSwitcher: Game failed to reload after switch")
                return False

            # Step 8: Verify home screen
            logger.info("Step 8/8: Verifying home screen")
            current_screen = self.navigator.detect_current_screen()
            if current_screen == "home":
                logger.info(
                    "✅ Account switch successful → %s", target_name
                )
                return True

            # Handle popups that might appear after switch
            self.navigator.handle_popup()
            time.sleep(self._action_delay)

            if self.navigator.detect_current_screen() == "home":
                logger.info(
                    "✅ Account switch successful → %s (after popup)", target_name
                )
                return True

            logger.error(
                "AccountSwitcher: After switch, screen is '%s' instead of 'home'",
                self.navigator.detect_current_screen(),
            )
            return False

        except Exception as e:
            logger.error(
                "AccountSwitcher switch_to('%s') failed: %s",
                target_name,
                e,
                exc_info=True,
            )
            return False

    # ── Account identity reading ─────────────────────────────────────────

    # Region where the player's name appears on the home screen HUD.
    # In 1920×1080 CoC, the name is displayed top-left below the XP bar.
    # Calibrated from the supplied 1920x1080 Home Village screenshots.
    # The name begins to the right of the XP badge at the top-left HUD.
    PLAYER_NAME_REGION: tuple[int, int, int, int] = (110, 14, 280, 42)

    # Also try the trophy/league row which shows the name slightly larger
    PLAYER_NAME_REGION_ALT: tuple[int, int, int, int] = (105, 8, 330, 58)

    def read_current_account_name(self) -> str:
        """OCR-read the player name from the current home screen.

        Tries two HUD regions (main and alternate) and returns the best
        match.  The raw text is cleaned of common OCR noise characters.

        Returns:
            Cleaned player name string, or empty string if unreadable.
        """
        raw = ""
        try:
            # Primary region
            region_img = self.screen.capture_region(*self.PLAYER_NAME_REGION)
            raw = self.ocr.read_text(region_img).strip()
            logger.debug("Player name OCR (primary): '%s'", raw)

            if len(raw) < 2:
                # Fallback to alternate region
                region_img2 = self.screen.capture_region(*self.PLAYER_NAME_REGION_ALT)
                raw = self.ocr.read_text(region_img2).strip()
                logger.debug("Player name OCR (alt): '%s'", raw)

            # Strip common OCR noise
            cleaned = raw.strip(".|,!@#$%^&*()[]{}\\/<>\"' \t\n")
            return cleaned

        except Exception as exc:
            logger.error("read_current_account_name failed: %s", exc, exc_info=True)
            return ""

    def identify_current_account(self) -> tuple[str | None, int | None]:
        """Identify which account is currently active by reading the player name.

        Reads the name from the HUD and performs fuzzy matching against
        all configured account names (case-insensitive, partial match).

        Returns:
            Tuple of (matched_name, account_index) if identified,
            or (read_name, None) if the name was read but not matched,
            or (None, None) if the screen could not be read at all.
        """
        read_name = self.read_current_account_name()

        if not read_name:
            logger.warning("identify_current_account: could not read any name from screen.")
            return (None, None)

        # Exact match against the in-game player name or SCID name
        for idx, cfg_name in enumerate(self._player_names):
            if cfg_name.lower() == read_name.lower():
                logger.info(
                    "Account identified (exact player): '%s' → index %d", cfg_name, idx
                )
                return (cfg_name, idx)

        for idx, cfg_name in enumerate(self._account_names):
            if cfg_name.lower() == read_name.lower():
                logger.info(
                    "Account identified (exact SCID): '%s' → index %d", cfg_name, idx
                )
                return (cfg_name, idx)

        # Partial/substring match (handles OCR clipping a few chars)
        # Guard: only try partial if the OCR result is at least 3 chars
        if len(read_name) >= 3:
            for idx, cfg_name in enumerate(self._player_names):
                if (cfg_name.lower() in read_name.lower()
                        or read_name.lower() in cfg_name.lower()):
                    logger.info(
                        "Account identified (partial player): read='%s' → '%s' (index %d)",
                        read_name, cfg_name, idx,
                    )
                    return (cfg_name, idx)

            for idx, cfg_name in enumerate(self._account_names):
                if (cfg_name.lower() in read_name.lower()
                        or read_name.lower() in cfg_name.lower()):
                    logger.info(
                        "Account identified (partial SCID): read='%s' → '%s' (index %d)",
                        read_name, cfg_name, idx,
                    )
                    return (cfg_name, idx)

        logger.warning(
            "Account name '%s' read from screen did not match any configured account: %s",
            read_name, self._player_names + self._account_names,
        )
        return (read_name, None)

    def verify_current_account(self, expected_name: str) -> bool:
        """Verify the current on-screen account matches the expected one.

        Reads the player name from screen and compares against all
        aliases for the expected account.

        Args:
            expected_name: Expected account name or identifier.

        Returns:
            True if confirmed on the correct account, False otherwise.
        """
        matched_name, matched_idx = self.identify_current_account()

        if matched_name is None:
            logger.warning(
                "verify_current_account('%s'): could not read screen — "
                "assuming switch failed.",
                expected_name,
            )
            return False

        # Gather all valid aliases for the expected account
        expected_candidates = {expected_name.lower()}
        for cand in self._get_candidate_names(expected_name):
            expected_candidates.add(cand.lower())

        if matched_name.lower() in expected_candidates:
            logger.info(
                "✅ Account verified: on '%s' as expected.", expected_name
            )
            return True

        # Check partial match against candidates
        for cand in expected_candidates:
            if len(cand) >= 3 and (cand in matched_name.lower() or matched_name.lower() in cand):
                logger.info(
                    "✅ Account verified (partial match): '%s' ~ '%s'", matched_name, cand
                )
                return True

        logger.warning(
            "⚠️  ACCOUNT MISMATCH: expected '%s' (candidates: %s) but screen shows '%s'. "
            "The switch may have failed or the name region is misread.",
            expected_name, list(expected_candidates), matched_name,
        )
        return False

    def get_current_account_index(self) -> int | None:
        """Return the 0-based index of the currently active account.

        Returns:
            Account index, or None if unknown.
        """
        _, idx = self.identify_current_account()
        return idx

    def is_correct_account(self, expected_name: str) -> bool:
        """Alias for ``verify_current_account`` — kept for back-compat."""
        return self.verify_current_account(expected_name)



    # ── Internal helpers ────────────────────────────────────────────────

    def _get_account_name(self, index: int) -> str:
        """Get the account name for a given index.

        Args:
            index: Zero-based account index.

        Returns:
            Account name string, or a generated placeholder if out of range.
        """
        if 0 <= index < len(self._account_names):
            return self._account_names[index]
        return f"Account_{index}"

    def _find_index_by_name(self, name: str) -> int:
        """Find the index or supercell_id_index for an account by name."""
        if not name:
            return 0
        target = name.strip().lower()
        for i, acc in enumerate(self._accounts_cfg):
            if (
                acc.get("name", "").strip().lower() == target
                or acc.get("player_name", "").strip().lower() == target
                or acc.get("supercell_id_name", "").strip().lower() == target
            ):
                return acc.get("supercell_id_index", i)
        for i, n in enumerate(self._account_names):
            if n.strip().lower() == target:
                return i
        for i, n in enumerate(self._player_names):
            if n.strip().lower() == target:
                return i
        return 0

    def _get_candidate_names(self, target: Any) -> list[str]:
        """Collect potential display names for an account to match via OCR."""
        candidates: list[str] = []
        if isinstance(target, dict):
            for key in ("supercell_id_name", "name", "player_name"):
                val = target.get(key)
                if val and val not in candidates:
                    candidates.append(val)
        elif isinstance(target, str):
            candidates.append(target)
            target_lower = target.strip().lower()
            for acc in self._accounts_cfg:
                if (
                    acc.get("name", "").strip().lower() == target_lower
                    or acc.get("player_name", "").strip().lower() == target_lower
                    or acc.get("supercell_id_name", "").strip().lower() == target_lower
                ):
                    for key in ("supercell_id_name", "name", "player_name"):
                        val = acc.get(key)
                        if val and val not in candidates:
                            candidates.append(val)
        elif isinstance(target, int):
            if 0 <= target < len(self._accounts_cfg):
                acc = self._accounts_cfg[target]
                for key in ("supercell_id_name", "name", "player_name"):
                    val = acc.get(key)
                    if val and val not in candidates:
                        candidates.append(val)
            elif 0 <= target < len(self._account_names):
                candidates.append(self._account_names[target])
        return candidates

    def _tap_scid_open(self) -> bool:
        """Tap the 'Open' button next to Supercell ID in settings.

        Returns:
            True if the button was found and clicked.
        """
        try:
            screenshot = self.screen.capture_screenshot()
            if screenshot is not None:
                # The supplied Settings capture places OPEN in the upper
                # Supercell ID card. OCR is more stable than a whole-card
                # template when the account portrait changes.
                text_match = self.ocr.find_text_location(
                    "open", screenshot, region=(1200, 150, 420, 150)
                )
                if text_match is not None:
                    x, y, w, h = text_match
                    self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                    logger.debug("Tapped SCID Open via OCR")
                    return True
                # Try template match
                match = self.vision.find(
                    screenshot,
                    self.SCID_OPEN_BUTTON_TEMPLATE,
                    threshold=self.DETECTION_THRESHOLD,
                )
                if match is not None:
                    x, y, w, h = match
                    self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                    logger.debug("Tapped SCID Open via template match")
                    return True

            logger.error("AccountSwitcher: Could not locate SCID Open control")
            return False

        except Exception as e:
            logger.error("AccountSwitcher _tap_scid_open failed: %s", e)
            return False

    def _wait_for_scid_panel(self, timeout: int = 15) -> bool:
        """Wait for the Supercell ID panel to appear.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            True if the SCID panel was detected.
        """
        start = time.time()
        while time.time() - start < timeout:
            screenshot = self.screen.capture_screenshot()
            if screenshot is not None:
                text_match = self.ocr.find_text_location(
                    "switch id", screenshot, region=(1300, 350, 620, 220)
                )
                if text_match is not None:
                    x, y, w, h = text_match
                    self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                    logger.debug("Tapped SWITCH ID via OCR")
                    return True
                match = self.vision.find(
                    screenshot,
                    self.SCID_PANEL_TEMPLATE,
                    threshold=self.DETECTION_THRESHOLD,
                )
                if match is not None:
                    logger.debug("SCID panel detected")
                    return True

                # Also check for SWITCH ID button (means panel is open)
                match = self.vision.find(
                    screenshot,
                    self.SWITCH_ID_BUTTON_TEMPLATE,
                    threshold=self.DETECTION_THRESHOLD,
                )
                if match is not None:
                    logger.debug("SWITCH ID button visible — SCID panel is open")
                    return True

            time.sleep(1.0)

        logger.warning("AccountSwitcher: SCID panel did not appear within %ds", timeout)
        return False

    def _tap_switch_id(self) -> bool:
        """Tap the 'SWITCH ID' button on the SCID panel.

        Returns:
            True if the button was found and clicked.
        """
        try:
            screenshot = self.screen.capture_screenshot()
            if screenshot is not None:
                match = self.vision.find(
                    screenshot,
                    self.SWITCH_ID_BUTTON_TEMPLATE,
                    threshold=self.DETECTION_THRESHOLD,
                )
                if match is not None:
                    x, y, w, h = match
                    self.input_ctrl.safe_click(x + w // 2, y + h // 2)
                    logger.debug("Tapped SWITCH ID via template match")
                    return True

            logger.error("AccountSwitcher: Could not locate SWITCH ID control")
            return False

        except Exception as e:
            logger.error("AccountSwitcher _tap_switch_id failed: %s", e)
            return False

    def _tap_account(
        self,
        account_index: int = 0,
        target_name: Optional[str] = None,
        candidate_names: Optional[list[str]] = None,
    ) -> bool:
        """Tap on a specific account entry in the account list.

        Searches for the account name dynamically using OCR across the
        visible screen and scrolls if necessary. This makes account
        switching completely independent of in-game or config order.
        If OCR does not locate any candidate names, it safely falls back
        to the slot position for the provided account_index.

        Args:
            account_index: Zero-based fallback slot index.
            target_name: Optional primary account name to locate.
            candidate_names: Optional list of candidate names/aliases.

        Returns:
            True if an account entry was tapped.
        """
        try:
            candidates: list[str] = []
            if candidate_names:
                candidates.extend(candidate_names)
            if target_name and target_name not in candidates:
                candidates.append(target_name)
            if not candidates:
                candidates.append(self._get_account_name(account_index))

            # Default fallback tap coordinates based on row slot
            slot_y = self.ACCOUNT_LIST_FIRST_Y + (account_index % 3) * self.ACCOUNT_LIST_SPACING_Y
            target_x = self.ACCOUNT_LIST_CENTER_X
            target_y = slot_y

            located = False
            max_scroll_attempts = 4

            for scroll_idx in range(max_scroll_attempts):
                screenshot = self.screen.capture_screenshot()
                if screenshot is None:
                    logger.error("AccountSwitcher: Cannot capture account list screenshot")
                    return False

                # 1. Search for each candidate name anywhere on screen via OCR
                for cand in candidates:
                    name_match = self.ocr.find_text_location(cand, screenshot)
                    if name_match is not None:
                        x, y, w, h = name_match
                        target_x = x + w // 2
                        target_y = y + h // 2
                        located = True
                        logger.info(
                            "Found account '%s' on screen via OCR at (%d, %d)",
                            cand, target_x, target_y,
                        )
                        break

                if located:
                    break

                # 2. If not visible yet, scroll down to reveal more accounts
                if scroll_idx < max_scroll_attempts - 1:
                    logger.debug(
                        "AccountSwitcher: Account not visible on screen yet; scrolling down (attempt %d)...",
                        scroll_idx + 1,
                    )
                    self.input_ctrl.drag(
                        start=(
                            self.ACCOUNT_LIST_CENTER_X,
                            self.ACCOUNT_LIST_FIRST_Y + self.ACCOUNT_LIST_SPACING_Y * 2,
                        ),
                        end=(
                            self.ACCOUNT_LIST_CENTER_X,
                            self.ACCOUNT_LIST_FIRST_Y,
                        ),
                        duration=0.3,
                    )
                    time.sleep(0.4)

            # Fallback 1: Template match if OCR did not find it
            if not located:
                screenshot = self.screen.capture_screenshot()
                if screenshot is not None:
                    template_name = f"account_{account_index}"
                    match = self.vision.find(
                        screenshot, template_name, threshold=0.70
                    )
                    if match is not None:
                        x, y, w, h = match
                        target_x = x + w // 2
                        target_y = y + h // 2
                        located = True
                        logger.debug(
                            "Found account template at (%d, %d)", target_x, target_y
                        )

            # Fallback 2: Row position
            if not located:
                logger.warning(
                    "AccountSwitcher: OCR did not detect candidates %s. "
                    "Falling back to slot index %d at (%d, %d)",
                    candidates,
                    account_index,
                    target_x,
                    target_y,
                )

            logger.info(
                "AccountSwitcher: Tapping account at (%d, %d)",
                target_x,
                target_y,
            )
            self.input_ctrl.safe_click(target_x, target_y)
            return True

        except Exception as e:
            logger.error(
                "AccountSwitcher _tap_account failed: %s",
                e,
                exc_info=True,
            )
            return False

    def _wait_for_game_reload(self) -> bool:
        """Wait for the game to fully reload after an account switch.

        After switching Supercell IDs, the game shows:
        Supercell logo → loading screen → home base.

        This waits through the entire reload sequence.

        Returns:
            True if the game successfully reloaded to the home screen.
        """
        try:
            timeout = self.MAX_RELOAD_WAIT_SEC
            start_time = time.time()
            poll_interval = 3.0
            saw_loading = False

            logger.info(
                "AccountSwitcher: Waiting for game reload (timeout=%ds)", timeout
            )

            while time.time() - start_time < timeout:
                current = self.navigator.detect_current_screen()

                if current == "loading":
                    saw_loading = True
                    elapsed = time.time() - start_time
                    logger.debug(
                        "AccountSwitcher: Loading... (%.1fs elapsed)", elapsed
                    )
                    time.sleep(poll_interval)
                    continue

                if current == "home":
                    elapsed = time.time() - start_time
                    logger.info(
                        "AccountSwitcher: Game reloaded successfully (%.1fs)",
                        elapsed,
                    )
                    return True

                if current == "disconnected":
                    logger.warning(
                        "AccountSwitcher: Disconnected during reload — handling"
                    )
                    self.navigator.handle_disconnected()
                    continue

                # Unknown screen — might be a popup or Supercell logo
                if saw_loading:
                    # We were loading but now see something else
                    # Try handling popups
                    self.navigator.handle_popup()
                    time.sleep(self._action_delay)

                    if self.navigator.detect_current_screen() == "home":
                        return True

                time.sleep(poll_interval)

            logger.error(
                "AccountSwitcher: Game did not reload within %ds (saw_loading=%s)",
                timeout,
                saw_loading,
            )
            return False

        except Exception as e:
            logger.error(
                "AccountSwitcher _wait_for_game_reload failed: %s",
                e,
                exc_info=True,
            )
            return False
