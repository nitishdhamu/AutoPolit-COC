"""Unit tests for order-independent AccountSwitcher logic."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from backends import MockBackend
from bot.account_switcher import AccountSwitcher


class AccountSwitcherOrderIndependenceTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "timing": {
                "between_actions_delay_sec": 0.01,
                "screen_load_wait_sec": 0.01,
                "dialog_animation_wait_sec": 0.01,
            },
            "accounts": [
                {
                    "name": "Bravo",
                    "supercell_id_name": "Bravo_SCID",
                    "player_name": "Bravo_IGN",
                    "supercell_id_index": 2,
                    "enabled": True,
                },
                {
                    "name": "Alpha",
                    "supercell_id_name": "Alpha_SCID",
                    "player_name": "Alpha_IGN",
                    "supercell_id_index": 0,
                    "enabled": True,
                },
            ],
        }
        self.backend = MockBackend()
        self.vision = MagicMock()
        self.ocr = MagicMock()
        self.input_ctrl = MagicMock()
        self.navigator = MagicMock()

        self.switcher = AccountSwitcher(
            config=self.config,
            backend_or_screen=self.backend,
            vision=self.vision,
            ocr=self.ocr,
            input_ctrl=self.input_ctrl,
            navigator=self.navigator,
            backend=self.backend,
        )

    def test_candidate_names_resolution(self):
        # By dict
        cands_dict = self.switcher._get_candidate_names(self.config["accounts"][0])
        self.assertIn("Bravo_SCID", cands_dict)
        self.assertIn("Bravo", cands_dict)
        self.assertIn("Bravo_IGN", cands_dict)

        # By name string
        cands_str = self.switcher._get_candidate_names("Alpha")
        self.assertIn("Alpha_SCID", cands_str)
        self.assertIn("Alpha", cands_str)
        self.assertIn("Alpha_IGN", cands_str)

        # By index
        cands_idx = self.switcher._get_candidate_names(0)
        self.assertIn("Bravo_SCID", cands_idx)

    def test_find_index_by_name(self):
        self.assertEqual(self.switcher._find_index_by_name("Bravo"), 2)
        self.assertEqual(self.switcher._find_index_by_name("Alpha_IGN"), 0)

    def test_tap_account_locates_by_ocr_without_caring_about_order(self):
        dummy_screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.backend.get_screenshot = MagicMock(return_value=dummy_screen)
        self.switcher.screen.capture_screenshot = MagicMock(return_value=dummy_screen)

        # Simulate OCR finding "Alpha" at specific location regardless of index
        def mock_find_text(query, image, region=None):
            if "alpha" in query.lower():
                return (500, 300, 100, 40)
            return None

        self.ocr.find_text_location.side_effect = mock_find_text

        success = self.switcher._tap_account(
            account_index=5,  # even if slot index is wrong/unrelated
            target_name="Alpha",
            candidate_names=["Alpha_SCID", "Alpha", "Alpha_IGN"],
        )

        self.assertTrue(success)
        # Should click center of OCR match: (500 + 50, 300 + 20) = (550, 320)
        self.input_ctrl.safe_click.assert_called_with(550, 320)

    def test_verify_current_account_matches_any_alias(self):
        # Screen reads player name "Alpha_IGN"
        self.switcher.read_current_account_name = MagicMock(return_value="Alpha_IGN")
        # Verification asked for config name "Alpha"
        verified = self.switcher.verify_current_account("Alpha")
        self.assertTrue(verified)


if __name__ == "__main__":
    unittest.main()
