"""Abstract base class for all bot device/platform backends."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class Backend(ABC):
    """Abstract interface between the bot brain and the underlying device/emulator."""

    def __init__(self, config: dict[str, Any], backend_name: str = "base") -> None:
        """Initialize common backend settings from configuration.

        Parameters
        ----------
        config : dict[str, Any]
            Top-level bot configuration dictionary.
        backend_name : str
            Name of this backend (e.g. 'pc', 'adb', 'mock').
        """
        self.config = config
        self.backend_name = backend_name

        # Read backend-specific settings or fall back to sensible defaults
        backends_cfg = config.get("backends", {})
        backend_cfg = backends_cfg.get(backend_name, {})

        self.ocr_scale: int = int(backend_cfg.get("ocr_scale", 2))
        self.preprocessing: dict[str, Any] = backend_cfg.get("preprocessing", {})
        self.tap_delay: float = float(backend_cfg.get("tap_delay_sec", 0.15))
        self.jitter_px: int = int(backend_cfg.get("jitter_px", 3))
        self.screenshot_interval: float = float(backend_cfg.get("screenshot_interval_sec", 0.5))
        self._gem_guard: Optional[Any] = None

    def set_gem_guard(self, gem_guard: Any) -> None:
        """Attach a GemGuard instance for pre/post-click safety checks."""
        self._gem_guard = gem_guard
        logger.info("GemGuard attached to %s backend", self.backend_name)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to the target device, emulator or window.

        Returns
        -------
        bool
            True if connection was successful, False otherwise.
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect and clean up any resources or process handles."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check whether the backend is currently connected and operational.

        Returns
        -------
        bool
            True if connected, False otherwise.
        """
        ...

    # ------------------------------------------------------------------
    # Screen capture
    # ------------------------------------------------------------------

    @abstractmethod
    def get_screenshot(self) -> np.ndarray:
        """Capture the current game screen.

        Returns
        -------
        np.ndarray
            HxWx3 BGR image suitable for OpenCV and OCR processing.

        Raises
        ------
        RuntimeError
            If screen capture fails.
        """
        ...

    def capture_screenshot(self) -> np.ndarray:
        """Compatibility alias for get_screenshot."""
        return self.get_screenshot()

    # ------------------------------------------------------------------
    # Input operations (all coordinates are normalized floats: 0.0 - 1.0)
    # ------------------------------------------------------------------

    @abstractmethod
    def tap(self, nx: float, ny: float) -> None:
        """Tap/click at normalized coordinate (nx, ny).

        Parameters
        ----------
        nx : float
            Normalized X coordinate (0.0 to 1.0).
        ny : float
            Normalized Y coordinate (0.0 to 1.0).
        """
        ...

    def safe_tap(self, nx: float, ny: float, checks: bool = True) -> bool:
        """Tap at normalized (nx, ny) with GemGuard pre/post safety checks.

        Returns
        -------
        bool
            True if action completed without gem dialog, False if gem dialog was dismissed.
        """
        if checks and self._gem_guard is not None:
            try:
                if self._gem_guard.check_for_gem_dialog():
                    logger.warning("GemGuard detected gem dialog BEFORE tap at (%.4f, %.4f)", nx, ny)
                    self._gem_guard.dismiss_gem_dialog()
                    return False
            except Exception as e:
                logger.error("GemGuard pre-tap check failed: %s", e)

        self.tap(nx, ny)

        if checks and self._gem_guard is not None:
            try:
                import time
                time.sleep(0.3)
                if self._gem_guard.check_for_gem_dialog():
                    logger.warning("GemGuard detected gem dialog AFTER tap at (%.4f, %.4f)", nx, ny)
                    self._gem_guard.dismiss_gem_dialog()
                    return False
            except Exception as e:
                logger.error("GemGuard post-tap check failed: %s", e)

        return True

    def safe_click(self, x: float | int, y: float | int, checks: bool = True) -> bool:
        """Compatibility alias for safe_tap supporting normalized and pixel coordinates."""
        nx, ny = self._normalize_point(x, y)
        return self.safe_tap(nx, ny, checks=checks)

    def click(self, x: float | int, y: float | int) -> None:
        """Compatibility alias for tap supporting normalized and pixel coordinates."""
        nx, ny = self._normalize_point(x, y)
        self.tap(nx, ny)

    def _normalize_point(self, x: float | int, y: float | int) -> Tuple[float, float]:
        """Convert input coordinate to normalized floats in [0.0, 1.0]."""
        if (isinstance(x, float) and 0.0 <= x <= 1.0) and (isinstance(y, float) and 0.0 <= y <= 1.0):
            return x, y
        w, h = self.get_screen_size()
        from backends.coordinates import to_normalized
        return to_normalized(x, y, w, h)

    @abstractmethod
    def long_press(self, nx: float, ny: float, duration_ms: int = 500) -> None:
        """Long-press at normalized coordinate (nx, ny).

        Parameters
        ----------
        nx : float
            Normalized X coordinate (0.0 to 1.0).
        ny : float
            Normalized Y coordinate (0.0 to 1.0).
        duration_ms : int
            Duration of the press in milliseconds.
        """
        ...

    @abstractmethod
    def swipe(
        self,
        nx1: float,
        ny1: float,
        nx2: float,
        ny2: float,
        duration_ms: int = 300,
    ) -> None:
        """Swipe or drag from normalized (nx1, ny1) to (nx2, ny2).

        Parameters
        ----------
        nx1, ny1 : float
            Normalized start coordinates (0.0 to 1.0).
        nx2, ny2 : float
            Normalized end coordinates (0.0 to 1.0).
        duration_ms : int
            Duration of swipe movement in milliseconds.
        """
        ...

    def drag(
        self,
        start: Tuple[float | int, float | int],
        end: Tuple[float | int, float | int],
        duration: float = 0.5,
    ) -> None:
        """Compatibility alias for swipe."""
        nx1, ny1 = self._normalize_point(start[0], start[1])
        nx2, ny2 = self._normalize_point(end[0], end[1])
        self.swipe(nx1, ny1, nx2, ny2, duration_ms=int(duration * 1000))

    def wait(self, seconds: float) -> None:
        """Sleep for specified seconds."""
        import time
        time.sleep(seconds)


    @abstractmethod
    def press_back(self) -> None:
        """Trigger the platform Back action (e.g. Escape on PC, KEYCODE_BACK on Android)."""
        ...

    @abstractmethod
    def press_home(self) -> None:
        """Trigger the platform Home action."""
        ...

    def press_key(self, key: str) -> None:
        """Press a keyboard key or platform key shortcut if supported.

        Parameters
        ----------
        key : str
            Key name (e.g. 'q', 'escape', 'enter').
        """
        logger.debug("press_key('%s') called on %s", key, self.backend_name)

    def select_troop_slot(self, slot_idx: int) -> None:
        """Select a troop slot from the bottom deployment bar.

        Parameters
        ----------
        slot_idx : int
            Zero-indexed slot position (0 = first troop/hero slot).
        """
        # Default implementation: tap the normalized slot coordinate on the tray
        slot_nx = 0.12 + (slot_idx * 0.065)
        slot_ny = 0.93
        self.tap(min(0.95, slot_nx), slot_ny)

    # ------------------------------------------------------------------
    # Environment & game state
    # ------------------------------------------------------------------

    @abstractmethod
    def get_screen_size(self) -> Tuple[int, int]:
        """Return the physical device/window pixel dimensions (width, height).

        Returns
        -------
        Tuple[int, int]
            (width, height) in device pixels.
        """
        ...

    @abstractmethod
    def launch_game(self) -> bool:
        """Ensure the game application/emulator is launched and running.

        Returns
        -------
        bool
            True if game is launched, False on failure.
        """
        ...

    @abstractmethod
    def close_game(self) -> None:
        """Close/terminate the game application or emulator."""
        ...

    @abstractmethod
    def is_game_foreground(self) -> bool:
        """Check whether the game is currently focused and in the foreground.

        Returns
        -------
        bool
            True if game is foregrounded, False otherwise.
        """
        ...

