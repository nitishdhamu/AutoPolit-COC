"""Dry-run backend wrapper that intercepts and logs all actions without sending physical inputs."""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional, Tuple

import numpy as np

from backends.base import Backend

logger = logging.getLogger(__name__)


class DryRunBackend(Backend):
    """Wraps an existing backend, capturing screens but suppressing all input actions."""

    def __init__(
        self,
        config_or_wrapped: Optional[dict[str, Any] | Backend] = None,
        wrapped: Optional[Backend] = None,
        config: Optional[dict[str, Any]] = None,
    ) -> None:
        if isinstance(config_or_wrapped, Backend):
            actual_wrapped = config_or_wrapped
            actual_config = config or actual_wrapped.config
        elif isinstance(config_or_wrapped, dict):
            actual_config = config_or_wrapped
            actual_wrapped = wrapped
        else:
            actual_config = config or {}
            actual_wrapped = wrapped

        super().__init__(actual_config, backend_name="dry_run")
        self.wrapped: Optional[Backend] = actual_wrapped
        self.recorded_actions: List[dict[str, Any]] = []

    def set_gem_guard(self, gem_guard: Any) -> None:
        super().set_gem_guard(gem_guard)
        if self.wrapped:
            self.wrapped.set_gem_guard(gem_guard)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        logger.info("[DRY RUN] Connect requested")
        if self.wrapped:
            return self.wrapped.connect()
        return True

    def disconnect(self) -> None:
        logger.info("[DRY RUN] Disconnect requested")
        if self.wrapped:
            self.wrapped.disconnect()

    def is_connected(self) -> bool:
        if self.wrapped:
            return self.wrapped.is_connected()
        return True

    # ------------------------------------------------------------------
    # Screen capture
    # ------------------------------------------------------------------

    def get_screenshot(self) -> np.ndarray:
        if self.wrapped:
            return self.wrapped.get_screenshot()
        # Fallback to empty 1080p frame if no wrapped backend
        return np.zeros((1080, 1920, 3), dtype=np.uint8)

    # ------------------------------------------------------------------
    # Inputs (intercepted, logged, and recorded; never dispatched)
    # ------------------------------------------------------------------

    def tap(self, nx: float, ny: float) -> None:
        w, h = self.get_screen_size()
        px = int(round(nx * w))
        py = int(round(ny * h))
        logger.info("[DRY RUN] Would tap at (nx=%.4f, ny=%.4f) -> px=(%d, %d)", nx, ny, px, py)
        self.recorded_actions.append({
            "action": "tap",
            "nx": float(nx),
            "ny": float(ny),
            "px": px,
            "py": py,
            "timestamp": time.time(),
        })

    def safe_tap(self, nx: float, ny: float, checks: bool = True) -> bool:
        logger.info("[DRY RUN] Safe tap check at (nx=%.4f, ny=%.4f)", nx, ny)
        if checks and self._gem_guard is not None:
            try:
                if self._gem_guard.check_for_gem_dialog():
                    logger.warning("[DRY RUN] GemGuard detected gem dialog before tap")
                    return False
            except Exception as e:
                logger.error("[DRY RUN] GemGuard check failed: %s", e)

        self.tap(nx, ny)
        return True

    def long_press(self, nx: float, ny: float, duration_ms: int = 500) -> None:
        w, h = self.get_screen_size()
        px = int(round(nx * w))
        py = int(round(ny * h))
        logger.info(
            "[DRY RUN] Would long_press at (nx=%.4f, ny=%.4f) -> px=(%d, %d) for %dms",
            nx, ny, px, py, duration_ms,
        )
        self.recorded_actions.append({
            "action": "long_press",
            "nx": float(nx),
            "ny": float(ny),
            "px": px,
            "py": py,
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        })

    def swipe(
        self,
        nx1: float,
        ny1: float,
        nx2: float,
        ny2: float,
        duration_ms: int = 300,
    ) -> None:
        w, h = self.get_screen_size()
        px1, py1 = int(round(nx1 * w)), int(round(ny1 * h))
        px2, py2 = int(round(nx2 * w)), int(round(ny2 * h))
        logger.info(
            "[DRY RUN] Would swipe from (%d, %d) to (%d, %d) over %dms",
            px1, py1, px2, py2, duration_ms,
        )
        self.recorded_actions.append({
            "action": "swipe",
            "nx1": float(nx1),
            "ny1": float(ny1),
            "nx2": float(nx2),
            "ny2": float(ny2),
            "px1": px1,
            "py1": py1,
            "px2": px2,
            "py2": py2,
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        })

    def press_back(self) -> None:
        logger.info("[DRY RUN] Would press Back")
        self.recorded_actions.append({"action": "press_back", "timestamp": time.time()})

    def press_home(self) -> None:
        logger.info("[DRY RUN] Would press Home")
        self.recorded_actions.append({"action": "press_home", "timestamp": time.time()})

    def press_key(self, key: str) -> None:
        logger.info("[DRY RUN] Would press key '%s'", key)
        self.recorded_actions.append({"action": "press_key", "key": key, "timestamp": time.time()})

    def select_troop_slot(self, slot_idx: int) -> None:
        logger.info("[DRY RUN] Would select troop slot %d", slot_idx)
        self.recorded_actions.append({
            "action": "select_troop_slot",
            "slot_idx": slot_idx,
            "timestamp": time.time(),
        })
        super().select_troop_slot(slot_idx)

    # ------------------------------------------------------------------
    # Environment & game state
    # ------------------------------------------------------------------

    def get_screen_size(self) -> Tuple[int, int]:
        if self.wrapped:
            return self.wrapped.get_screen_size()
        return 1920, 1080

    def launch_game(self) -> bool:
        logger.info("[DRY RUN] Would launch game")
        self.recorded_actions.append({"action": "launch_game", "timestamp": time.time()})
        return True

    def close_game(self) -> None:
        logger.info("[DRY RUN] Would close game")
        self.recorded_actions.append({"action": "close_game", "timestamp": time.time()})

    def is_game_foreground(self) -> bool:
        if self.wrapped:
            return self.wrapped.is_game_foreground()
        return True
