"""Regression tests using the supplied 1920x1080 screenshots."""

from __future__ import annotations

import unittest
from pathlib import Path

import cv2

from bot.ocr import GameOCR
from bot.vision import VisionEngine
from core.config_loader import ConfigLoader
from bot.gem_guard import GemGuard
from bot.navigation import Navigator


ROOT = Path(__file__).resolve().parents[1]


class _Screen:
    def __init__(self, image):
        self._image = image

    def capture_screenshot(self):
        return self._image

    def get_game_viewport(self):
        return 0, 0, 1920, 1080

    width = 1920
    height = 1080


class _Input:
    pass


SCREENSHOTS_DIR = ROOT / "screenshots"
if not SCREENSHOTS_DIR.exists() and (ROOT.parent / "screenshots").exists():
    SCREENSHOTS_DIR = ROOT.parent / "screenshots"


class ScreenshotDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = ConfigLoader(ROOT / "config.yaml").config
        cls.ocr = GameOCR(cls.config)

    def _image(self, filename: str):
        image = cv2.imread(str(SCREENSHOTS_DIR / filename))
        self.assertIsNotNone(image, filename)
        return image

    def test_home_screen_is_detected_by_ocr(self):
        nav = Navigator(
            self.config,
            _Screen(self._image("home base unzoomed.png")),
            VisionEngine(str(ROOT / "templates")),
            self.ocr,
            _Input(),
        )
        self.assertEqual(nav.detect_current_screen(), "home")

    def test_account_switcher_is_detected_by_ocr(self):
        nav = Navigator(
            self.config,
            _Screen(self._image("coc acounts menu.png")),
            VisionEngine(str(ROOT / "templates")),
            self.ocr,
            _Input(),
        )
        self.assertEqual(nav.detect_current_screen(), "supercell_id")

    def test_gem_purchase_dialog_blocks_input(self):
        guard = GemGuard(
            self.config,
            _Screen(self._image("not enough resources, use gem.png")),
            VisionEngine(str(ROOT / "templates")),
            self.ocr,
        )
        self.assertTrue(guard.check_for_gem_dialog())


if __name__ == "__main__":
    unittest.main()
