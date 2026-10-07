"""Unit tests for multi-signal ScreenDetector."""

from __future__ import annotations

import unittest
from pathlib import Path

import cv2
import numpy as np

from bot.screen_detector import ScreenDetector
from core.config_loader import ConfigLoader

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS_DIR = ROOT / "screenshots"
if not SCREENSHOTS_DIR.exists() and (ROOT.parent / "screenshots").exists():
    SCREENSHOTS_DIR = ROOT.parent / "screenshots"


class ScreenDetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = ConfigLoader(ROOT / "config.yaml").config
        cls.detector = ScreenDetector(cls.config)

    def _image(self, filename: str) -> np.ndarray:
        p = SCREENSHOTS_DIR / filename
        self.assertTrue(p.exists(), f"Screenshot not found: {p}")
        img = cv2.imread(str(p))
        self.assertIsNotNone(img, f"Failed to load image: {filename}")
        return img

    def test_supercell_splash_detection(self):
        # 1. Real screenshot
        img = self._image("supercell logo.png")
        res = self.detector.detect_screen(img)
        self.assertEqual(res.screen_name, "supercell_splash")
        self.assertGreaterEqual(res.confidence, 0.90)

        # 2. Synthetic black frame
        black = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertEqual(self.detector.detect_current_screen(black), "supercell_splash")

    def test_loading_screen_detection(self):
        img = self._image("loding bar screen.png")
        res = self.detector.detect_screen(img)
        self.assertEqual(res.screen_name, "loading")
        self.assertTrue(self.detector.is_loading_or_splash(img))

    def test_gem_dialog_detection(self):
        img = self._image("not enough resources, use gem.png")
        res = self.detector.detect_screen(img)
        self.assertEqual(res.screen_name, "gem_dialog")
        self.assertTrue(self.detector.is_gem_dialog(img))

    def test_disconnected_dialog_detection(self):
        img = self._image("reload game.png")
        res = self.detector.detect_screen(img)
        self.assertEqual(res.screen_name, "disconnected")
        self.assertTrue(self.detector.is_disconnected(img))

    def test_supercell_id_menu_detection(self):
        img = self._image("coc acounts menu.png")
        res = self.detector.detect_screen(img)
        self.assertEqual(res.screen_name, "supercell_id")
        self.assertTrue(self.detector.is_supercell_id(img))

    def test_home_village_detection(self):
        img = self._image("home base unzoomed.png")
        res = self.detector.detect_screen(img)
        self.assertEqual(res.screen_name, "home")
        self.assertTrue(self.detector.is_home_screen(img))

    def test_builder_base_detection(self):
        img = self._image("builder base.png")
        res = self.detector.detect_screen(img)
        self.assertEqual(res.screen_name, "builder_base")
        self.assertTrue(self.detector.is_builder_base(img))

    def test_battle_results_detection(self):
        img = self._image("after attack result screen.png")
        res = self.detector.detect_screen(img)
        self.assertEqual(res.screen_name, "battle_results")
        self.assertTrue(self.detector.is_battle_results(img))

    def test_frame_stability_check(self):
        frame1 = np.ones((720, 1280, 3), dtype=np.uint8) * 128
        frame2 = frame1.copy()
        # Identical frames -> stable
        self.assertTrue(self.detector.check_frame_stability(frame1, frame2))

        # Radically different frames -> not stable
        frame3 = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertFalse(self.detector.check_frame_stability(frame1, frame3))


if __name__ == "__main__":
    unittest.main()
