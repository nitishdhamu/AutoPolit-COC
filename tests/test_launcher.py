"""Unit tests for frontend launcher and CLI flags."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.config_loader import ConfigLoader
from frontend.launcher import filter_account, parse_args, prompt_interactive_backend, run_preflight_checks


class TestLauncher(unittest.TestCase):
    """Test suite for CLI argument parsing, interactive menu, and preflight."""

    def test_parse_args_defaults(self) -> None:
        args = parse_args([])
        self.assertIsNone(args.backend)
        self.assertIsNone(args.serial)
        self.assertFalse(args.dry_run)
        self.assertIsNone(args.account)
        self.assertFalse(args.preflight)
        self.assertFalse(args.status)
        self.assertFalse(args.once)
        self.assertEqual(args.config, "config.yaml")

    def test_parse_args_all_flags(self) -> None:
        args = parse_args([
            "--backend", "adb",
            "--serial", "emulator-5554",
            "--dry-run",
            "--account", "Player1",
            "--preflight",
            "--status",
            "--once",
            "--config", "custom.yaml",
        ])
        self.assertEqual(args.backend, "adb")
        self.assertEqual(args.serial, "emulator-5554")
        self.assertTrue(args.dry_run)
        self.assertEqual(args.account, "Player1")
        self.assertTrue(args.preflight)
        self.assertTrue(args.status)
        self.assertTrue(args.once)
        self.assertEqual(args.config, "custom.yaml")

    def test_filter_account_by_index(self) -> None:
        cfg = ConfigLoader()
        initial_count = cfg.account_count
        self.assertGreater(initial_count, 0)
        filter_account(cfg, "0")
        self.assertEqual(cfg.account_count, 1)

    def test_filter_account_by_name(self) -> None:
        cfg = ConfigLoader()
        first_name = cfg.accounts[0]["name"]
        filter_account(cfg, first_name)
        self.assertEqual(cfg.account_count, 1)
        self.assertEqual(cfg.accounts[0]["name"], first_name)

    def test_filter_account_invalid_exits(self) -> None:
        cfg = ConfigLoader()
        with self.assertRaises(SystemExit) as ctx:
            filter_account(cfg, "UnknownAccount999")
        self.assertEqual(ctx.exception.code, 1)

    @patch("builtins.input", return_value="2")
    def test_prompt_interactive_backend(self, mock_input: MagicMock) -> None:
        choice = prompt_interactive_backend()
        self.assertEqual(choice, "adb")

    @patch("builtins.input", return_value="1")
    def test_prompt_interactive_backend_pc(self, mock_input: MagicMock) -> None:
        choice = prompt_interactive_backend()
        self.assertEqual(choice, "pc")

    def test_preflight_checks_mock(self) -> None:
        cfg = ConfigLoader()
        ok = run_preflight_checks(cfg, "mock")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
