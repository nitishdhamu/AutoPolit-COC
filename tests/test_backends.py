"""Unit tests for backend abstractions, MockBackend, and DryRunBackend."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import numpy as np

from backends import DryRunBackend, MockBackend, PCBackend, create_backend


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "backends": {
                "pc": {"window_title": "Google Play Games"},
                "mock": {"screen_size": [1920, 1080]},
            }
        }

    def test_create_backend_factory(self):
        pc = create_backend("pc", self.config)
        self.assertIsInstance(pc, PCBackend)

        mock = create_backend("mock", self.config)
        self.assertIsInstance(mock, MockBackend)

        dry_run = create_backend("dry_run", self.config)
        self.assertIsInstance(dry_run, DryRunBackend)

        with self.assertRaises(ValueError):
            create_backend("nonexistent", self.config)

    def test_mock_backend_action_recording(self):
        mock = MockBackend(self.config, screen_size=(1920, 1080))
        self.assertTrue(mock.is_connected())

        mock.tap(0.25, 0.75)
        mock.long_press(0.5, 0.5, duration_ms=400)
        mock.swipe(0.1, 0.2, 0.3, 0.4, duration_ms=250)
        mock.press_key("q")
        mock.press_back()
        mock.press_home()

        actions = mock.recorded_actions
        self.assertEqual(len(actions), 6)
        self.assertEqual(actions[0]["action"], "tap")
        self.assertAlmostEqual(actions[0]["nx"], 0.25)
        self.assertAlmostEqual(actions[0]["ny"], 0.75)

        self.assertEqual(actions[1]["action"], "long_press")
        self.assertEqual(actions[1]["duration_ms"], 400)

        self.assertEqual(actions[2]["action"], "swipe")
        self.assertAlmostEqual(actions[2]["nx1"], 0.1)
        self.assertAlmostEqual(actions[2]["ny2"], 0.4)

        self.assertEqual(actions[3]["action"], "press_key")
        self.assertEqual(actions[3]["key"], "q")

        self.assertEqual(actions[4]["action"], "press_back")
        self.assertEqual(actions[5]["action"], "press_home")

    def test_mock_backend_screenshots(self):
        mock = MockBackend(self.config)
        # Default blank frame
        blank = mock.get_screenshot()
        self.assertEqual(blank.shape, (1080, 1920, 3))

        # Single custom frame
        frame1 = np.ones((720, 1280, 3), dtype=np.uint8) * 100
        mock.set_screenshot(frame1)
        self.assertEqual(mock.get_screen_size(), (1280, 720))
        out = mock.get_screenshot()
        self.assertEqual(out.shape, (720, 1280, 3))
        self.assertEqual(out[0, 0, 0], 100)

        # Frame sequence playlist
        frame2 = np.ones((720, 1280, 3), dtype=np.uint8) * 200
        mock.set_screenshots([frame1, frame2])
        out1 = mock.get_screenshot()
        out2 = mock.get_screenshot()
        out3 = mock.get_screenshot()
        self.assertEqual(out1[0, 0, 0], 100)
        self.assertEqual(out2[0, 0, 0], 200)
        self.assertEqual(out3[0, 0, 0], 100)  # loops

    def test_dry_run_backend_intercepts_inputs(self):
        mock = MockBackend(self.config)
        dry = DryRunBackend(config=self.config, wrapped=mock)

        # Trigger tap on dry-run
        dry.tap(0.4, 0.6)

        # Dry-run records action
        self.assertEqual(len(dry.recorded_actions), 1)
        self.assertEqual(dry.recorded_actions[0]["action"], "tap")
        self.assertAlmostEqual(dry.recorded_actions[0]["nx"], 0.4)

        # Underlying mock did NOT receive the tap
        self.assertEqual(len(mock.recorded_actions), 0)

    def test_safe_tap_with_gem_guard(self):
        mock = MockBackend(self.config)
        guard = MagicMock()

        # Case 1: GemGuard detects gem dialog -> blocks tap
        guard.check_for_gem_dialog.return_value = True
        mock.set_gem_guard(guard)

        success = mock.safe_tap(0.5, 0.5)
        self.assertFalse(success)
        guard.dismiss_gem_dialog.assert_called_once()
        self.assertEqual(len(mock.get_actions_by_type("tap")), 0)

        # Case 2: GemGuard detects no dialog -> allows tap
        guard.reset_mock()
        guard.check_for_gem_dialog.return_value = False

        success = mock.safe_tap(0.5, 0.5)
        self.assertTrue(success)
        self.assertEqual(len(mock.get_actions_by_type("tap")), 1)


if __name__ == "__main__":
    unittest.main()
