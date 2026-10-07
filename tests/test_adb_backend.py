"""Unit tests for ADBBackend using mocked subprocess."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import MagicMock, call, patch

import cv2
import numpy as np

from backends.adb_backend import ADBBackend, find_adb_path


class TestADBBackend(unittest.TestCase):
    """Test suite for ADBBackend device communication, inputs, capture, and lifecycle."""

    def setUp(self) -> None:
        self.config = {
            "backends": {
                "adb": {
                    "adb_path": "adb",
                    "device_serial": None,
                    "screen_width": 1920,
                    "screen_height": 1080,
                    "tap_delay_sec": 0.0,
                    "jitter_px": 0,
                }
            }
        }
        self.backend = ADBBackend(self.config)
        self.backend.adb_path = "adb"

    def test_find_adb_path_configured_exists(self) -> None:
        with patch("os.path.exists", return_value=True):
            p = find_adb_path("/custom/path/to/adb")
            self.assertEqual(p, "/custom/path/to/adb")

    def test_find_adb_path_fallback_which(self) -> None:
        with patch("os.path.exists", return_value=False), \
             patch("shutil.which", return_value="/usr/bin/adb"):
            p = find_adb_path(None)
            self.assertEqual(p, "/usr/bin/adb")

    @patch("subprocess.run")
    def test_discover_devices(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "devices"],
            returncode=0,
            stdout="List of devices attached\nemulator-5554\tdevice\nphone123\tunauthorized\n\n",
            stderr="",
        )
        devices = self.backend.discover_devices()
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0], {"serial": "emulator-5554", "state": "device"})
        self.assertEqual(devices[1], {"serial": "phone123", "state": "unauthorized"})

    @patch("subprocess.run")
    def test_connect_auto_select_single_device(self, mock_run: MagicMock) -> None:
        # Create a valid synthetic PNG image matching 2400x1080
        test_img = np.zeros((1080, 2400, 3), dtype=np.uint8)
        _, png_bytes = cv2.imencode(".png", test_img)

        def run_side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "devices" in cmd_str:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="List of devices attached\nemulator-5554\tdevice\n",
                    stderr="",
                )
            elif "wm size" in cmd_str:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="Physical size: 1080x2400\n",
                    stderr="",
                )
            elif "wm density" in cmd_str:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="Physical density: 420\n",
                    stderr="",
                )
            elif "screencap" in cmd_str:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=png_bytes.tobytes(),
                    stderr=b"",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        connected = self.backend.connect()
        self.assertTrue(connected)
        self.assertEqual(self.backend.device_serial, "emulator-5554")
        # wm size was 1080x2400 portrait, verify landscape normalization
        self.assertEqual(self.backend.get_screen_size(), (2400, 1080))

    @patch("subprocess.run")
    def test_connect_no_devices_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "devices"],
            returncode=0,
            stdout="List of devices attached\n\n",
            stderr="",
        )
        with self.assertRaises(RuntimeError) as ctx:
            self.backend.connect()
        self.assertIn("No ADB devices connected", str(ctx.exception))

    @patch("subprocess.run")
    def test_connect_unauthorized_device_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "devices"],
            returncode=0,
            stdout="List of devices attached\nphone123\tunauthorized\n",
            stderr="",
        )
        with self.assertRaises(RuntimeError) as ctx:
            self.backend.connect()
        self.assertIn("unauthorized", str(ctx.exception).lower())

    @patch("subprocess.run")
    def test_connect_multiple_devices_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "devices"],
            returncode=0,
            stdout="List of devices attached\ndevice1\tdevice\ndevice2\tdevice\n",
            stderr="",
        )
        with self.assertRaises(RuntimeError) as ctx:
            self.backend.connect()
        self.assertIn("Multiple ADB devices connected", str(ctx.exception))

    @patch("subprocess.run")
    def test_connect_explicit_serial(self, mock_run: MagicMock) -> None:
        cfg = {
            "backends": {
                "adb": {
                    "adb_path": "adb",
                    "device_serial": "device2",
                    "screen_width": 1920,
                    "screen_height": 1080,
                    "tap_delay_sec": 0.0,
                    "jitter_px": 0,
                }
            }
        }
        b = ADBBackend(cfg)
        b.adb_path = "adb"

        test_img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        _, png_bytes = cv2.imencode(".png", test_img)

        def run_side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "devices" in cmd_str:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="List of devices attached\ndevice1\tdevice\ndevice2\tdevice\n",
                    stderr="",
                )
            elif "wm size" in cmd_str:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="Physical size: 1920x1080\n", stderr=""
                )
            elif "wm density" in cmd_str:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="Physical density: 320\n", stderr=""
                )
            elif "screencap" in cmd_str:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=png_bytes.tobytes(), stderr=b""
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect
        self.assertTrue(b.connect())
        self.assertEqual(b.device_serial, "device2")

    @patch("subprocess.run")
    def test_screen_capture_and_rotation(self, mock_run: MagicMock) -> None:
        self.backend.device_serial = "emulator-5554"
        self.backend._width = 2400
        self.backend._height = 1080

        # Create portrait image (H > W) to test auto-rotation to landscape
        portrait_img = np.zeros((2400, 1080, 3), dtype=np.uint8)
        _, png_bytes = cv2.imencode(".png", portrait_img)

        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "-s", "emulator-5554", "exec-out", "screencap", "-p"],
            returncode=0,
            stdout=png_bytes.tobytes(),
            stderr=b"",
        )

        frame = self.backend.get_screenshot()
        self.assertIsNotNone(frame)
        # Should have rotated so width > height
        self.assertEqual(frame.shape[1], 2400)
        self.assertEqual(frame.shape[0], 1080)

    @patch("subprocess.run")
    def test_inputs(self, mock_run: MagicMock) -> None:
        self.backend.device_serial = "emulator-5554"
        self.backend._width = 1920
        self.backend._height = 1080
        self.backend.jitter_px = 0
        self.backend.tap_delay = 0.0

        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        # Tap center (0.5, 0.5) -> (960, 540)
        self.backend.tap(0.5, 0.5)
        mock_run.assert_called_with(
            ["adb", "-s", "emulator-5554", "shell", "input", "tap", "960", "540"],
            capture_output=True,
            text=True,
            timeout=15.0,
            creationflags=mock_run.call_args[1]["creationflags"],
        )

        # Long press (0.25, 0.25) -> (480, 270)
        self.backend.long_press(0.25, 0.25, duration_ms=700)
        mock_run.assert_called_with(
            ["adb", "-s", "emulator-5554", "shell", "input", "swipe", "480", "270", "480", "270", "700"],
            capture_output=True,
            text=True,
            timeout=15.0,
            creationflags=mock_run.call_args[1]["creationflags"],
        )

        # Swipe from (0.1, 0.1) -> (0.9, 0.9)
        self.backend.swipe(0.1, 0.1, 0.9, 0.9, duration_ms=400)
        mock_run.assert_called_with(
            ["adb", "-s", "emulator-5554", "shell", "input", "swipe", "192", "108", "1727", "971", "400"],
            capture_output=True,
            text=True,
            timeout=15.0,
            creationflags=mock_run.call_args[1]["creationflags"],
        )

        # Press back (KEYCODE_BACK = 4)
        self.backend.press_back()
        mock_run.assert_called_with(
            ["adb", "-s", "emulator-5554", "shell", "input", "keyevent", "4"],
            capture_output=True,
            text=True,
            timeout=15.0,
            creationflags=mock_run.call_args[1]["creationflags"],
        )

        # Press home (KEYCODE_HOME = 3)
        self.backend.press_home()
        mock_run.assert_called_with(
            ["adb", "-s", "emulator-5554", "shell", "input", "keyevent", "3"],
            capture_output=True,
            text=True,
            timeout=15.0,
            creationflags=mock_run.call_args[1]["creationflags"],
        )

        # Select troop slot 0 (taps bottom tray)
        self.backend.select_troop_slot(0)
        # slot 0: nx=0.10, ny=0.93 -> px = 192, py = 1004
        mock_run.assert_called_with(
            ["adb", "-s", "emulator-5554", "shell", "input", "tap", "192", "1003"],
            capture_output=True,
            text=True,
            timeout=15.0,
            creationflags=mock_run.call_args[1]["creationflags"],
        )

    @patch("subprocess.run")
    def test_game_lifecycle(self, mock_run: MagicMock) -> None:
        self.backend.device_serial = "emulator-5554"

        # is_game_running: returns pid digits
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="12345 12346\n", stderr=""
        )
        self.assertTrue(self.backend.is_game_running())

        # is_game_running: empty output
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="\n", stderr=""
        )
        self.assertFalse(self.backend.is_game_running())

        # is_game_foreground: dumpsys window contains package
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="mCurrentFocus=Window{abc u0 com.supercell.clashofclans/com.supercell.clashofclans.GameApp}\n",
            stderr="",
        )
        self.assertTrue(self.backend.is_game_foreground())

        # close_game
        self.backend.close_game()
        mock_run.assert_called_with(
            ["adb", "-s", "emulator-5554", "shell", "am", "force-stop", "com.supercell.clashofclans"],
            capture_output=True,
            text=True,
            timeout=15.0,
            creationflags=mock_run.call_args[1]["creationflags"],
        )

    @patch("subprocess.run")
    def test_transient_error_retry(self, mock_run: MagicMock) -> None:
        self.backend.device_serial = "emulator-5554"

        # First call fails with "device offline", restart server succeeds, second call succeeds
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error: device offline"),
            subprocess.CompletedProcess(args=["adb", "kill-server"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["adb", "start-server"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]

        res = self.backend._run_adb(["shell", "echo", "test"], max_retries=2)
        self.assertEqual(res.stdout, "ok")


if __name__ == "__main__":
    unittest.main()
