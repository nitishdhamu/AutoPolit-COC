"""Mock backend for deterministic testing and replay."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

import cv2
import numpy as np

from backends.base import Backend

logger = logging.getLogger(__name__)


class MockBackend(Backend):
    """Mock backend that captures no real hardware and replays provided images.
    
    All inputs (tap, swipe, keypress, etc.) are recorded into `recorded_actions`
    for test assertions without triggering any operating system or hardware events.
    """

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        screen_size: Tuple[int, int] = (1920, 1080),
        images: Optional[List[np.ndarray]] = None,
    ) -> None:
        super().__init__(config or {}, backend_name="mock")
        self.screen_size: Tuple[int, int] = screen_size
        self._images: List[np.ndarray] = list(images) if images else []
        self._image_index: int = 0
        self._connected: bool = True
        self._game_foreground: bool = True
        self.recorded_actions: List[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Image feeding & state configuration for tests
    # ------------------------------------------------------------------

    def set_screenshot(self, image: np.ndarray | str | Path) -> None:
        """Set a single image to be returned by get_screenshot."""
        if isinstance(image, (str, Path)):
            img_path = str(image)
            loaded = cv2.imread(img_path)
            if loaded is None:
                raise FileNotFoundError(f"Failed to read image at {img_path}")
            image = loaded
        self._images = [image]
        self._image_index = 0
        self.screen_size = (image.shape[1], image.shape[0])

    def set_screenshots(self, images: List[np.ndarray]) -> None:
        """Set a sequence of images to cycle through on subsequent get_screenshot calls."""
        if not images:
            raise ValueError("images list cannot be empty")
        self._images = list(images)
        self._image_index = 0
        self.screen_size = (images[0].shape[1], images[0].shape[0])

    def load_screenshot_dir(self, directory: str | Path) -> int:
        """Load all PNG files in directory sorted alphabetically.
        
        Returns
        -------
        int
            Number of images loaded.
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise NotADirectoryError(f"Directory not found: {dir_path}")
        png_files = sorted(dir_path.glob("*.png"))
        loaded: List[np.ndarray] = []
        for p in png_files:
            img = cv2.imread(str(p))
            if img is not None:
                loaded.append(img)
        if loaded:
            self.set_screenshots(loaded)
        return len(loaded)

    def clear_actions(self) -> None:
        """Clear the history of recorded actions."""
        self.recorded_actions.clear()

    def get_actions_by_type(self, action_name: str) -> List[dict[str, Any]]:
        """Filter recorded actions by action name."""
        return [a for a in self.recorded_actions if a.get("action") == action_name]

    # ------------------------------------------------------------------
    # Backend ABC implementation
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._connected = True
        self.recorded_actions.append({"action": "connect", "timestamp": time.time()})
        return True

    def disconnect(self) -> None:
        self._connected = False
        self.recorded_actions.append({"action": "disconnect", "timestamp": time.time()})

    def is_connected(self) -> bool:
        return self._connected

    def get_screenshot(self) -> np.ndarray:
        if not self._connected:
            raise RuntimeError("MockBackend is not connected")
        if not self._images:
            # Return a blank frame of specified dimensions
            w, h = self.screen_size
            return np.zeros((h, w, 3), dtype=np.uint8)

        img = self._images[self._image_index]
        if len(self._images) > 1:
            self._image_index = (self._image_index + 1) % len(self._images)
        return img.copy()

    def tap(self, nx: float, ny: float) -> None:
        self.recorded_actions.append({
            "action": "tap",
            "nx": float(nx),
            "ny": float(ny),
            "timestamp": time.time(),
        })

    def long_press(self, nx: float, ny: float, duration_ms: int = 500) -> None:
        self.recorded_actions.append({
            "action": "long_press",
            "nx": float(nx),
            "ny": float(ny),
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
        self.recorded_actions.append({
            "action": "swipe",
            "nx1": float(nx1),
            "ny1": float(ny1),
            "nx2": float(nx2),
            "ny2": float(ny2),
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        })

    def press_back(self) -> None:
        self.recorded_actions.append({"action": "press_back", "timestamp": time.time()})

    def press_home(self) -> None:
        self.recorded_actions.append({"action": "press_home", "timestamp": time.time()})

    def press_key(self, key: str) -> None:
        self.recorded_actions.append({
            "action": "press_key",
            "key": key,
            "timestamp": time.time(),
        })

    def select_troop_slot(self, slot_idx: int) -> None:
        self.recorded_actions.append({
            "action": "select_troop_slot",
            "slot_idx": slot_idx,
            "timestamp": time.time(),
        })
        super().select_troop_slot(slot_idx)

    def get_screen_size(self) -> Tuple[int, int]:
        return self.screen_size

    def launch_game(self) -> bool:
        self._game_foreground = True
        self.recorded_actions.append({"action": "launch_game", "timestamp": time.time()})
        return True

    def close_game(self) -> None:
        self._game_foreground = False
        self.recorded_actions.append({"action": "close_game", "timestamp": time.time()})

    def is_game_foreground(self) -> bool:
        return self._game_foreground
