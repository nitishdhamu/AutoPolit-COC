"""Unit tests for configuration loader and validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from core.config_loader import ConfigLoader

ROOT = Path(__file__).resolve().parents[1]


class ConfigLoaderTests(unittest.TestCase):
    def test_load_example_config(self):
        example_path = ROOT / "config.example.yaml"
        self.assertTrue(example_path.exists(), "config.example.yaml must exist")
        cfg = ConfigLoader(example_path)

        self.assertGreaterEqual(cfg.account_count, 1)
        self.assertIn("active_backend", cfg.config)
        self.assertIn("backends", cfg.config)
        self.assertIn("pc", cfg.config["backends"])
        self.assertIn("adb", cfg.config["backends"])

    def test_account_properties(self):
        sample_yaml = {
            "version": "2.0.0",
            "active_backend": "mock",
            "accounts": [
                {
                    "name": "TestAcc1",
                    "player_name": "Chief",
                    "supercell_id_index": 0,
                    "th_level": 15,
                    "max_builders": 6,
                }
            ],
            "ocr": {"tesseract_cmd": "tesseract"},
        }
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(sample_yaml, f)
            temp_path = Path(f.name)

        try:
            cfg = ConfigLoader(temp_path)
            self.assertEqual(cfg.account_count, 1)
            acc = cfg.accounts[0]
            self.assertEqual(acc["name"], "TestAcc1")
            self.assertEqual(acc["th_level"], 15)
            self.assertEqual(acc["max_builders"], 6)
            self.assertTrue(acc["enabled"])
            self.assertIn("farming", cfg.config)
            self.assertIn("safety", cfg.config)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_missing_accounts_raises_error(self):
        bad_yaml = {
            "version": "2.0.0",
            "active_backend": "pc",
            "accounts": [],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(bad_yaml, f)
            temp_path = Path(f.name)

        try:
            with self.assertRaises(ValueError):
                ConfigLoader(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_account_order_preservation_and_optional_index(self):
        sample_yaml = {
            "version": "2.0.0",
            "active_backend": "mock",
            "accounts": [
                {"name": "Bravo", "supercell_id_index": 5},
                {"name": "Alpha", "supercell_id_index": 1},
                {"name": "Charlie"},
            ],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(sample_yaml, f)
            temp_path = Path(f.name)

        try:
            cfg = ConfigLoader(temp_path)
            names = [a["name"] for a in cfg.accounts]
            self.assertEqual(names, ["Bravo", "Alpha", "Charlie"])
            self.assertEqual(cfg.accounts[2]["supercell_id_index"], 2)
        finally:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
