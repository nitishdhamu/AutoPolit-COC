"""PC Backend for Windows Google Play Games emulator."""

from __future__ import annotations

import ctypes
import logging
import random
import subprocess
import time
from typing import Any, Optional, Tuple

import cv2
import numpy as np

try:
    import mss
except ImportError:
    mss = None

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    import win32con
    import win32gui
except ImportError:
    win32con = None
    win32gui = None

from backends.base import Backend
from backends.coordinates import to_device_px

logger = logging.getLogger(__name__)


def fix_dpi() -> None:
    """Set Windows DPI awareness to ensure physical pixel accuracy."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        logger.info("DPI awareness set to per-monitor (level 2)")
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            logger.info("DPI awareness set via SetProcessDPIAware (legacy)")
        except Exception as e:
            logger.warning("Could not set DPI awareness: %s", e)


class PCBackend(Backend):
    """Controls Clash of Clans running on Windows Google Play Games."""

    # Troop slot shortcut keys on PC Google Play Games
    _TROOP_KEYS = ("q", "w", "e", "r", "a", "s", "d", "f", "z", "x", "c", "v")
    _KILL_PROCESSES = ("crosvm.exe", "Bootstrapper.exe", "client.exe")

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize PCBackend with configuration.

        Parameters
        ----------
        config : dict[str, Any]
            Top-level bot configuration.
        """
        super().__init__(config, backend_name="pc")

        # Set DPI awareness immediately
        fix_dpi()

        # Disable pyautogui failsafe for unattended botting
        if pyautogui is not None:
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = self.tap_delay

        # Read PC-specific or legacy screen/emulator settings
        backends_cfg = config.get("backends", {})
        pc_cfg = backends_cfg.get("pc", {})
        screen_cfg = config.get("screen", {})
        emu_cfg = config.get("emulator", {})

        self._width: int = int(pc_cfg.get("resolution_width", screen_cfg.get("resolution_width", 1920)))
        self._height: int = int(pc_cfg.get("resolution_height", screen_cfg.get("resolution_height", 1080)))

        self._window_title: str = str(
            pc_cfg.get(
                "window_title",
                emu_cfg.get("window_title_contains", emu_cfg.get("gpg_window_title", "Google Play Games")),
            )
        )
        self._launcher_path: str = str(
            pc_cfg.get(
                "launcher_path",
                emu_cfg.get("launcher_path", emu_cfg.get("exe_path", r"C:\Program Files\Google\Play Games\Bootstrapper.exe")),
            )
        )
        self._process_name: str = str(
            pc_cfg.get("process_name", emu_cfg.get("gpg_process_name", "Bootstrapper.exe"))
        )
        self._coc_process: str = str(
            emu_cfg.get("coc_process_name", "crosvm.exe")
        )
        self._launch_timeout: int = int(emu_cfg.get("launch_timeout_sec", 60))
        self._close_cooldown: int = int(emu_cfg.get("close_cooldown_sec", 10))

        self._sct: Optional[Any] = None
        self._connected: bool = False

        logger.info(
            "PCBackend initialized (target resolution=%dx%d, title='%s')",
            self._width,
            self._height,
            self._window_title,
        )

    # ------------------------------------------------------------------
    # Connection & Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to PC game window and verify foreground state."""
        hwnd = self._find_window(self._window_title)
        if hwnd is None:
            # Also try searching for "Clash of Clans"
            hwnd = self._find_window("Clash of Clans")

        if hwnd is not None:
            self._focus_window(hwnd)
            self._connected = True
            logger.info("Connected to PC window (hwnd=%d)", hwnd)
            return True

        logger.warning("Could not find PC game window matching '%s'", self._window_title)
        self._connected = False
        return False

    def disconnect(self) -> None:
        """Release screen capture handles."""
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
        self._connected = False
        logger.info("PCBackend disconnected")

    def is_connected(self) -> bool:
        """Check whether the game window exists."""
        hwnd = self._find_window(self._window_title) or self._find_window("Clash of Clans")
        return hwnd is not None

    def launch_game(self) -> bool:
        """Ensure Google Play Games is started and the game window is ready."""
        if self.is_game_foreground():
            logger.info("Game is already in the foreground")
            self._connected = True
            return True

        # Check if process is already running
        if not self._is_process_running(self._process_name) and not self._is_process_running(self._coc_process):
            logger.info("Starting Google Play Games: %s", self._launcher_path)
            try:
                subprocess.Popen(
                    [self._launcher_path],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                logger.error("Failed to launch executable '%s': %s", self._launcher_path, e)
                return False

        # Wait for window to appear
        deadline = time.monotonic() + self._launch_timeout
        hwnd: Optional[int] = None
        while time.monotonic() < deadline:
            hwnd = self._find_window("Clash of Clans") or self._find_window(self._window_title)
            if hwnd is not None:
                break
            time.sleep(2.0)

        if hwnd is not None:
            self._focus_window(hwnd)
            time.sleep(2.0)
            self._connected = True
            logger.info("Game window found and focused (hwnd=%d)", hwnd)
            return True

        logger.error("Timed out waiting for game window to appear")
        return False

    def close_game(self) -> None:
        """Terminate emulator processes."""
        logger.info("Closing emulator processes: %s", ", ".join(self._KILL_PROCESSES))
        for proc in self._KILL_PROCESSES:
            try:
                subprocess.run(
                    ["taskkill", "/f", "/t", "/im", proc],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception as e:
                logger.warning("Error killing process %s: %s", proc, e)

        time.sleep(2.0)
        self._connected = False
        time.sleep(self._close_cooldown)

    def is_game_foreground(self) -> bool:
        """Check whether the game window is currently focused in the foreground."""
        if win32gui is None:
            return False
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return False
            title = win32gui.GetWindowText(hwnd).lower()
            if "clash of clans" in title or self._window_title.lower() in title:
                return True

            # Also check if foreground window matches fullscreen dimensions
            rect = win32gui.GetWindowRect(hwnd)
            win_w = rect[2] - rect[0]
            win_h = rect[3] - rect[1]
            return abs(win_w - self._width) <= 15 and abs(win_h - self._height) <= 15
        except Exception as e:
            logger.debug("Failed to check foreground window: %s", e)
            return False

    def get_screen_size(self) -> Tuple[int, int]:
        """Return the active game viewport dimensions in device pixels."""
        vp = self.get_game_viewport()
        if vp is not None:
            return vp[2], vp[3]
        return self._width, self._height

    def get_game_viewport(self) -> Optional[Tuple[int, int, int, int]]:
        """Return the visible game viewport rectangle (x, y, w, h)."""
        if win32gui is None:
            return None
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd or not win32gui.IsWindowVisible(hwnd):
                hwnd = self._find_window("Clash of Clans") or self._find_window(self._window_title)
            if not hwnd or not win32gui.IsWindowVisible(hwnd):
                return None

            client_rect = win32gui.GetClientRect(hwnd)
            client_w = client_rect[2]
            client_h = client_rect[3]
            if client_w <= 0 or client_h <= 0:
                return None
            origin = win32gui.ClientToScreen(hwnd, (0, 0))
            return origin[0], origin[1], client_w, client_h
        except Exception as e:
            logger.debug("Could not determine viewport: %s", e)
            return None

    # ------------------------------------------------------------------
    # Screen Capture
    # ------------------------------------------------------------------

    def _get_sct(self) -> Any:
        if self._sct is None:
            if mss is None:
                raise RuntimeError("mss library is required for PC screen capture")
            self._sct = mss.mss()
        return self._sct

    def get_screenshot(self) -> np.ndarray:
        """Capture the current game screen.

        Returns
        -------
        np.ndarray
            HxWx3 BGR image.
        """
        try:
            sct = self._get_sct()
            vp = self.get_game_viewport()
            if vp is not None and (vp[2] != self._width or vp[3] != self._height):
                region = {"left": vp[0], "top": vp[1], "width": vp[2], "height": vp[3]}
                raw = sct.grab(region)
            else:
                if len(sct.monitors) < 2:
                    raise RuntimeError("No primary monitor detected by mss")
                raw = sct.grab(sct.monitors[1])

            frame = np.array(raw, dtype=np.uint8)
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            logger.error("Failed to capture screenshot: %s", e)
            raise RuntimeError(f"Screen capture failed: {e}") from e

    # ------------------------------------------------------------------
    # Input Operations
    # ------------------------------------------------------------------

    def _to_pixel_coords(self, nx: float, ny: float) -> Tuple[int, int]:
        """Convert normalized (nx, ny) to physical screen coordinates."""
        vp = self.get_game_viewport()
        if vp is not None:
            vx, vy, vw, vh = vp
            px, py = to_device_px(nx, ny, vw, vh)
            return vx + px, vy + py
        return to_device_px(nx, ny, self._width, self._height)

    def _human_delay(self) -> None:
        """Small humanized random delay."""
        delay = random.randint(40, 120) / 1000.0
        time.sleep(delay)

    def tap(self, nx: float, ny: float) -> None:
        """Tap at normalized coordinate (nx, ny)."""
        if pyautogui is None:
            raise RuntimeError("pyautogui is required for PC input")

        px, py = self._to_pixel_coords(nx, ny)
        if self.jitter_px > 0:
            px += random.randint(-self.jitter_px, self.jitter_px)
            py += random.randint(-self.jitter_px, self.jitter_px)

        self._human_delay()
        pyautogui.click(px, py)
        time.sleep(self.tap_delay)

    def long_press(self, nx: float, ny: float, duration_ms: int = 500) -> None:
        """Long-press at normalized coordinate (nx, ny)."""
        if pyautogui is None:
            raise RuntimeError("pyautogui is required for PC input")

        px, py = self._to_pixel_coords(nx, ny)
        self._human_delay()
        pyautogui.mouseDown(px, py)
        time.sleep(max(0.1, duration_ms / 1000.0))
        pyautogui.mouseUp(px, py)
        time.sleep(self.tap_delay)

    def swipe(
        self,
        nx1: float,
        ny1: float,
        nx2: float,
        ny2: float,
        duration_ms: int = 300,
    ) -> None:
        """Swipe / drag from (nx1, ny1) to (nx2, ny2)."""
        if pyautogui is None:
            raise RuntimeError("pyautogui is required for PC input")

        x1, y1 = self._to_pixel_coords(nx1, ny1)
        x2, y2 = self._to_pixel_coords(nx2, ny2)

        self._human_delay()
        pyautogui.moveTo(x1, y1)
        pyautogui.drag(x2 - x1, y2 - y1, duration=max(0.1, duration_ms / 1000.0))
        time.sleep(self.tap_delay)

    def press_back(self) -> None:
        """Press Escape on PC to close dialogs or back out."""
        if pyautogui is not None:
            self._human_delay()
            pyautogui.press("escape")
            time.sleep(self.tap_delay)

    def press_home(self) -> None:
        """Home action on PC."""
        logger.debug("press_home called on PC backend")

    def press_key(self, key: str) -> None:
        """Press a keyboard key."""
        if pyautogui is not None:
            self._human_delay()
            pyautogui.press(key)
            time.sleep(self.tap_delay)

    def select_troop_slot(self, slot_idx: int) -> None:
        """Select troop slot by shortcut key or tapping tray."""
        if 0 <= slot_idx < len(self._TROOP_KEYS):
            key = self._TROOP_KEYS[slot_idx]
            self.press_key(key)
        else:
            # Fallback to normalized tray tap
            super().select_troop_slot(slot_idx)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_window(self, title_contains: str) -> Optional[int]:
        if win32gui is None:
            return None
        result: list[int] = []
        needle = title_contains.lower()

        def _enum_callback(hwnd: int, _extra: object) -> bool:
            try:
                title = win32gui.GetWindowText(hwnd)
                if needle in title.lower() and win32gui.IsWindowVisible(hwnd):
                    result.append(hwnd)
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(_enum_callback, None)
        except Exception:
            return None

        return result[0] if result else None

    def _focus_window(self, hwnd: int) -> None:
        if win32gui is None or win32con is None:
            return
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            logger.debug("Could not focus window hwnd=%d: %s", hwnd, e)

    @staticmethod
    def _is_process_running(proc_name: str) -> bool:
        try:
            res = subprocess.run(
                ["tasklist", "/fi", f"imagename eq {proc_name}", "/nh"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return proc_name.lower() in res.stdout.lower()
        except Exception:
            return False
