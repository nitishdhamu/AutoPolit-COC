"""ADB (Android Debug Bridge) Backend for Clash of Clans bot.

Controls Clash of Clans running on a physical Android phone, tablet,
or Android emulator over ADB without requiring third-party libraries.
All interactions use the standard `adb` command-line utility via subprocess.
"""

from __future__ import annotations

import logging
import os
import random
import re
import shutil
import subprocess
import time
from typing import Any, Optional, Tuple

import cv2
import numpy as np

from backends.base import Backend
from backends.coordinates import to_device_px

logger = logging.getLogger(__name__)


def find_adb_path(configured_path: Optional[str] = None) -> str:
    """Locate the adb executable on the local system.

    Parameters
    ----------
    configured_path : Optional[str]
        User-provided path from configuration.

    Returns
    -------
    str
        Path to adb executable.
    """
    if configured_path and os.path.exists(configured_path):
        return configured_path

    # Check system PATH
    which_adb = shutil.which("adb")
    if which_adb:
        return which_adb

    # Common Windows installation locations
    candidate_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        os.path.expandvars(r"%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe"),
        r"C:\Program Files\Android\platform-tools\adb.exe",
        r"C:\platform-tools\adb.exe",
        # Common macOS / Linux installation locations
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
        "/usr/bin/adb",
        "/usr/local/bin/adb",
    ]

    for cand in candidate_paths:
        if os.path.isfile(cand):
            return cand

    return configured_path or "adb"


class ADBBackend(Backend):
    """Controls Clash of Clans running on an Android device via ADB."""

    DEFAULT_PACKAGE = "com.supercell.clashofclans"
    DEFAULT_ACTIVITY = "com.supercell.clashofclans.GameApp"

    # Android Keycodes
    KEYCODE_HOME = "3"
    KEYCODE_BACK = "4"
    KEYCODE_MENU = "82"
    KEYCODE_ESCAPE = "111"
    KEYCODE_ENTER = "66"
    KEYCODE_SPACE = "62"

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize ADBBackend with configuration.

        Parameters
        ----------
        config : dict[str, Any]
            Top-level bot configuration dictionary.
        """
        super().__init__(config, backend_name="adb")

        backends_cfg = config.get("backends", {})
        adb_cfg = backends_cfg.get("adb", {})

        self.configured_adb_path: Optional[str] = adb_cfg.get("adb_path")
        self.adb_path: str = find_adb_path(self.configured_adb_path)
        self.device_serial: Optional[str] = adb_cfg.get("device_serial")

        self._package_name: str = str(adb_cfg.get("package_name", self.DEFAULT_PACKAGE))
        self._activity_name: str = str(adb_cfg.get("activity_name", self.DEFAULT_ACTIVITY))

        self._width: int = int(adb_cfg.get("screen_width", 1920))
        self._height: int = int(adb_cfg.get("screen_height", 1080))
        self._density: Optional[int] = None
        self._connected: bool = False
        self._latency_benchmarked: bool = False

    # ------------------------------------------------------------------
    # Subprocess execution & reconnection
    # ------------------------------------------------------------------

    def _run_adb(
        self,
        args: list[str],
        binary_stdout: bool = False,
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> subprocess.CompletedProcess:
        """Execute an adb command with retries and auto-reconnect.

        Parameters
        ----------
        args : list[str]
            Arguments to pass to adb (e.g. ['shell', 'input', 'tap', ...]).
        binary_stdout : bool
            If True, capture raw bytes without decoding.
        timeout : float
            Subprocess timeout in seconds.
        max_retries : int
            Number of retries on transient errors.

        Returns
        -------
        subprocess.CompletedProcess
            Result of the command execution.
        """
        cmd = [self.adb_path]
        if self.device_serial:
            cmd.extend(["-s", self.device_serial])
        cmd.extend(args)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        for attempt in range(1, max_retries + 1):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=not binary_stdout,
                    timeout=timeout,
                    creationflags=creationflags,
                )

                if result.returncode == 0:
                    return result

                # Inspect stderr or stdout for transient ADB errors
                err_text = ""
                if binary_stdout:
                    err_text = result.stderr.decode("utf-8", errors="replace")
                else:
                    err_text = result.stderr or result.stdout or ""

                is_transient = any(
                    sig in err_text.lower()
                    for sig in ("device offline", "device not found", "cannot connect to daemon", "error: closed")
                )

                if is_transient and attempt < max_retries:
                    logger.warning(
                        "ADB transient error on attempt %d/%d: %s. Attempting reconnection...",
                        attempt,
                        max_retries,
                        err_text.strip(),
                    )
                    self.restart_adb_server()
                    time.sleep(0.5 * attempt)
                    continue

                raise RuntimeError(
                    f"ADB command failed (code {result.returncode}): {' '.join(cmd)}\n{err_text.strip()}"
                )

            except subprocess.TimeoutExpired as e:
                logger.warning("ADB command timed out after %.1fs on attempt %d/%d", timeout, attempt, max_retries)
                if attempt >= max_retries:
                    raise TimeoutError(f"ADB command timed out: {' '.join(cmd)}") from e
                time.sleep(0.5 * attempt)

        raise RuntimeError(f"ADB command failed after {max_retries} attempts: {' '.join(cmd)}")

    def restart_adb_server(self) -> bool:
        """Kill and restart the ADB server daemon.

        Returns
        -------
        bool
            True if server restarted successfully.
        """
        logger.info("Restarting ADB server daemon...")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.run(
                [self.adb_path, "kill-server"],
                capture_output=True,
                timeout=10.0,
                creationflags=creationflags,
            )
            time.sleep(1.0)
            subprocess.run(
                [self.adb_path, "start-server"],
                capture_output=True,
                timeout=10.0,
                creationflags=creationflags,
            )
            time.sleep(1.0)
            return True
        except Exception as e:
            logger.error("Failed to restart ADB server: %s", e)
            return False

    # ------------------------------------------------------------------
    # Device discovery & connection lifecycle
    # ------------------------------------------------------------------

    def discover_devices(self) -> list[dict[str, str]]:
        """List all attached ADB devices and their states.

        Returns
        -------
        list[dict[str, str]]
            List of dictionaries with 'serial' and 'state' keys.
        """
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            res = subprocess.run(
                [self.adb_path, "devices"],
                capture_output=True,
                text=True,
                timeout=10.0,
                creationflags=creationflags,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"ADB executable not found at '{self.adb_path}'. Please install Android SDK platform-tools."
            ) from e

        if res.returncode != 0:
            raise RuntimeError(f"Failed to query adb devices: {res.stderr}")

        devices: list[dict[str, str]] = []
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices attached") or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                devices.append({"serial": parts[0], "state": parts[1]})

        return devices

    def connect(self) -> bool:
        """Connect to target ADB device, query resolution, and benchmark capture.

        Returns
        -------
        bool
            True if connected successfully.
        """
        logger.info("Connecting to Android device via ADB (%s)...", self.adb_path)
        devices = self.discover_devices()

        if self.device_serial:
            # User specified a specific serial
            matched = [d for d in devices if d["serial"] == self.device_serial]
            if not matched:
                raise RuntimeError(
                    f"Configured ADB device serial '{self.device_serial}' not found in attached devices: "
                    f"{[d['serial'] for d in devices]}"
                )
            target = matched[0]
            if target["state"] == "unauthorized":
                raise RuntimeError(
                    f"ADB device '{self.device_serial}' is unauthorized. "
                    "Please check your phone/tablet screen and accept the 'Allow USB debugging' prompt."
                )
            if target["state"] != "device":
                raise RuntimeError(
                    f"ADB device '{self.device_serial}' is in invalid state: '{target['state']}'"
                )
            logger.info("Connected to configured ADB device: %s", self.device_serial)
        else:
            # Auto-selection
            ready_devices = [d for d in devices if d["state"] == "device"]
            if not ready_devices:
                unauth = [d for d in devices if d["state"] == "unauthorized"]
                if unauth:
                    raise RuntimeError(
                        f"Found device(s) {unauth} but unauthorized. "
                        "Please check your device screen and accept the 'Allow USB debugging' prompt."
                    )
                raise RuntimeError(
                    "No ADB devices connected. Please connect an Android device or emulator with USB debugging enabled."
                )

            if len(ready_devices) > 1:
                serials = [d["serial"] for d in ready_devices]
                raise RuntimeError(
                    f"Multiple ADB devices connected: {serials}. "
                    "Specify device_serial in config.yaml under backends.adb.device_serial or via CLI."
                )

            self.device_serial = ready_devices[0]["serial"]
            logger.info("Auto-selected ADB device: %s", self.device_serial)

        # Update physical display metrics
        self._update_display_metrics()

        # Benchmark capture latency once
        self._benchmark_capture_latency()

        self._connected = True
        return True

    def disconnect(self) -> None:
        """Disconnect and mark backend as offline."""
        logger.info("Disconnecting from ADB device: %s", self.device_serial)
        self._connected = False

    def is_connected(self) -> bool:
        """Check whether the device is currently connected and responsive."""
        if not self._connected or not self.device_serial:
            return False
        try:
            res = self._run_adb(["get-state"], timeout=3.0, max_retries=1)
            return "device" in res.stdout.strip()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Display metrics & resolution
    # ------------------------------------------------------------------

    def _update_display_metrics(self) -> Tuple[int, int]:
        """Query physical display resolution and density from device.

        Returns
        -------
        Tuple[int, int]
            (width, height) in landscape orientation.
        """
        try:
            res = self._run_adb(["shell", "wm", "size"])
            # Matches "Physical size: 1080x2400" or "Override size: 1080x2400"
            matches = re.findall(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", res.stdout)
            if matches:
                # Use last match (Override size takes precedence if present)
                w_str, h_str = matches[-1]
                w, h = int(w_str), int(h_str)

                # Clash of Clans is strictly landscape!
                # Ensure width >= height regardless of current device sensor orientation.
                self._width = max(w, h)
                self._height = min(w, h)
                logger.info(
                    "ADB device display resolution: %dx%d (Landscape)",
                    self._width,
                    self._height,
                )
            else:
                logger.warning("Could not parse wm size output: %r. Keeping %dx%d", res.stdout, self._width, self._height)
        except Exception as e:
            logger.warning("Failed to query wm size: %s. Using default %dx%d", e, self._width, self._height)

        try:
            res_density = self._run_adb(["shell", "wm", "density"])
            density_matches = re.findall(r"(?:Override|Physical) density:\s*(\d+)", res_density.stdout)
            if density_matches:
                self._density = int(density_matches[-1])
                logger.info("ADB device display density: %d dpi", self._density)
        except Exception as e:
            logger.debug("Failed to query wm density: %s", e)

        return self._width, self._height

    def get_screen_size(self) -> Tuple[int, int]:
        """Return the device landscape pixel dimensions (width, height)."""
        return self._width, self._height

    # ------------------------------------------------------------------
    # Screen capture
    # ------------------------------------------------------------------

    def get_screenshot(self) -> np.ndarray:
        """Capture the current game screen via ADB screencap.

        Returns
        -------
        np.ndarray
            HxWx3 BGR image.
        """
        try:
            # Fast path: exec-out screencap -p transmits raw binary PNG
            result = self._run_adb(["exec-out", "screencap", "-p"], binary_stdout=True, timeout=8.0)
            raw = result.stdout

            # Check for PNG header: \x89PNG\r\n\x1a\n
            if not raw or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                logger.debug("exec-out screencap returned non-PNG data, trying shell screencap fallback")
                fallback = self._run_adb(["shell", "screencap", "-p"], binary_stdout=True, timeout=8.0)
                # On Windows, stdout might have CRLF translation (\r\r\n or \r\n)
                raw = fallback.stdout.replace(b"\r\r\n", b"\r\n").replace(b"\r\n", b"\n")

            frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if frame is None or frame.size == 0:
                raise RuntimeError("Failed to decode screencap image buffer from ADB")

            # Landscape orientation verification:
            # If the device captured in portrait orientation (H > W), rotate counterclockwise
            if frame.shape[0] > frame.shape[1]:
                frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

            # Update dimensions from actual capture if different
            if (frame.shape[1], frame.shape[0]) != (self._width, self._height):
                self._width, self._height = frame.shape[1], frame.shape[0]

            return frame

        except Exception as e:
            logger.error("Failed to capture ADB screenshot: %s", e)
            raise RuntimeError(f"ADB screen capture failed: {e}") from e

    def _benchmark_capture_latency(self) -> None:
        """Benchmark capture latency once at startup and log performance."""
        if self._latency_benchmarked:
            return
        try:
            t0 = time.perf_counter()
            _ = self.get_screenshot()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            logger.info("ADB screencap capture benchmark: %.1f ms", elapsed_ms)
            self._latency_benchmarked = True
        except Exception as e:
            logger.warning("Could not benchmark screencap latency: %s", e)

    # ------------------------------------------------------------------
    # Input operations (all coordinates normalized 0.0 - 1.0)
    # ------------------------------------------------------------------

    def tap(self, nx: float, ny: float) -> None:
        """Tap at normalized coordinate (nx, ny)."""
        px, py = to_device_px(nx, ny, self._width, self._height)

        if self.jitter_px > 0:
            px += random.randint(-self.jitter_px, self.jitter_px)
            py += random.randint(-self.jitter_px, self.jitter_px)

        px = max(0, min(self._width - 1, px))
        py = max(0, min(self._height - 1, py))

        self._run_adb(["shell", "input", "tap", str(px), str(py)])
        if self.tap_delay > 0:
            time.sleep(self.tap_delay)

    def long_press(self, nx: float, ny: float, duration_ms: int = 500) -> None:
        """Long-press at normalized coordinate (nx, ny)."""
        px, py = to_device_px(nx, ny, self._width, self._height)
        px = max(0, min(self._width - 1, px))
        py = max(0, min(self._height - 1, py))

        # Emulated via swipe with identical start and end points
        self._run_adb(["shell", "input", "swipe", str(px), str(py), str(px), str(py), str(duration_ms)])
        if self.tap_delay > 0:
            time.sleep(self.tap_delay)

    def swipe(
        self,
        nx1: float,
        ny1: float,
        nx2: float,
        ny2: float,
        duration_ms: int = 300,
    ) -> None:
        """Swipe / drag from normalized (nx1, ny1) to (nx2, ny2)."""
        x1, y1 = to_device_px(nx1, ny1, self._width, self._height)
        x2, y2 = to_device_px(nx2, ny2, self._width, self._height)

        x1 = max(0, min(self._width - 1, x1))
        y1 = max(0, min(self._height - 1, y1))
        x2 = max(0, min(self._width - 1, x2))
        y2 = max(0, min(self._height - 1, y2))

        self._run_adb(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])
        if self.tap_delay > 0:
            time.sleep(self.tap_delay)

    def press_back(self) -> None:
        """Trigger Android Back button (KEYCODE_BACK)."""
        self._run_adb(["shell", "input", "keyevent", self.KEYCODE_BACK])
        if self.tap_delay > 0:
            time.sleep(self.tap_delay)

    def press_home(self) -> None:
        """Trigger Android Home button (KEYCODE_HOME)."""
        self._run_adb(["shell", "input", "keyevent", self.KEYCODE_HOME])
        if self.tap_delay > 0:
            time.sleep(self.tap_delay)

    def press_key(self, key: str) -> None:
        """Send keyevent by key name or keycode."""
        key_map = {
            "back": self.KEYCODE_BACK,
            "home": self.KEYCODE_HOME,
            "menu": self.KEYCODE_MENU,
            "escape": self.KEYCODE_ESCAPE,
            "enter": self.KEYCODE_ENTER,
            "space": self.KEYCODE_SPACE,
        }
        keycode = key_map.get(key.lower(), key)
        self._run_adb(["shell", "input", "keyevent", str(keycode)])
        if self.tap_delay > 0:
            time.sleep(self.tap_delay)

    def select_troop_slot(self, slot_idx: int) -> None:
        """Select a troop slot by tapping the bottom tray on Android.

        On Android there are no PC keyboard shortcuts (q, w, e...), so we tap
        the corresponding slot coordinate on the deployment tray.
        """
        # Tray starts around nx=0.10, each slot ~0.065 width, centered at ny=0.93
        slot_nx = 0.10 + (slot_idx * 0.065)
        slot_ny = 0.93
        self.tap(min(0.95, slot_nx), slot_ny)

    # ------------------------------------------------------------------
    # Game lifecycle
    # ------------------------------------------------------------------

    def launch_game(self) -> bool:
        """Launch Clash of Clans on Android device."""
        logger.info("Launching Clash of Clans (%s) on Android device...", self._package_name)

        # Primary approach: monkey launcher
        try:
            self._run_adb([
                "shell",
                "monkey",
                "-p",
                self._package_name,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ])
        except Exception as e:
            logger.debug("Monkey launch failed, trying am start: %s", e)
            try:
                self._run_adb([
                    "shell",
                    "am",
                    "start",
                    "-n",
                    f"{self._package_name}/{self._activity_name}",
                ])
            except Exception as start_err:
                logger.error("am start failed: %s", start_err)
                return False

        # Wait up to 15 seconds for game process to appear
        for _ in range(30):
            if self.is_game_running():
                logger.info("Game process detected running")
                return True
            time.sleep(0.5)

        logger.error("Timed out waiting for game to launch")
        return False

    def close_game(self) -> None:
        """Force-stop Clash of Clans process on Android device."""
        logger.info("Force-stopping Clash of Clans (%s)...", self._package_name)
        try:
            self._run_adb(["shell", "am", "force-stop", self._package_name])
            time.sleep(1.0)
        except Exception as e:
            logger.warning("Error force-stopping game: %s", e)

    def is_game_running(self) -> bool:
        """Check whether the Clash of Clans process is active."""
        try:
            res = self._run_adb(["shell", "pidof", self._package_name], timeout=3.0, max_retries=1)
            # Output contains PID numbers if running
            tokens = res.stdout.strip().split()
            return any(t.isdigit() for t in tokens)
        except Exception:
            # Fallback to ps
            try:
                ps_res = self._run_adb(["shell", "ps", "-A"], timeout=5.0, max_retries=1)
                return self._package_name in ps_res.stdout
            except Exception:
                return False

    def is_game_foreground(self) -> bool:
        """Check whether Clash of Clans is currently focused in the foreground."""
        try:
            # Query window dump
            res = self._run_adb(["shell", "dumpsys", "window"], timeout=5.0, max_retries=1)
            for line in res.stdout.splitlines():
                if any(k in line for k in ("mCurrentFocus", "mFocusedApp", "topResumedActivity")):
                    if self._package_name in line:
                        return True

            # Fallback to activity dump
            act_res = self._run_adb(["shell", "dumpsys", "activity", "activities"], timeout=5.0, max_retries=1)
            for line in act_res.stdout.splitlines():
                if "topResumedActivity" in line or "mResumedActivity" in line:
                    if self._package_name in line:
                        return True

            return False
        except Exception as e:
            logger.debug("Failed to check foreground activity: %s", e)
            return False
