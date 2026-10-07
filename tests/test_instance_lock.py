"""Unit tests for InstanceLock and single-instance protection."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.instance_lock import InstanceLock, is_pid_running


class TestInstanceLock(unittest.TestCase):
    """Test suite for InstanceLock acquire, release, stale cleanup, and context manager."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.lock_path = Path(self.temp_dir.name) / "bot.lock"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_acquire_and_release(self) -> None:
        lock = InstanceLock(self.lock_path)
        self.assertTrue(lock.acquire())
        self.assertTrue(self.lock_path.exists())

        # Inspect lock file content
        content = json.loads(self.lock_path.read_text(encoding="utf-8"))
        self.assertEqual(content["pid"], os.getpid())

        # Release
        lock.release()
        self.assertFalse(self.lock_path.exists())

    @patch("core.instance_lock.is_pid_running", return_value=True)
    def test_acquire_blocks_when_pid_active(self, mock_is_running) -> None:
        # Pre-seed lock file with another PID
        self.lock_path.write_text(json.dumps({"pid": 99999, "started_at": "test"}), encoding="utf-8")

        lock = InstanceLock(self.lock_path)
        acquired = lock.acquire()
        self.assertFalse(acquired)

    @patch("core.instance_lock.is_pid_running", return_value=False)
    def test_acquire_overwrites_stale_lock(self, mock_is_running) -> None:
        # Pre-seed lock file with a dead PID
        self.lock_path.write_text(json.dumps({"pid": 99999, "started_at": "old"}), encoding="utf-8")

        lock = InstanceLock(self.lock_path)
        acquired = lock.acquire()
        self.assertTrue(acquired)
        content = json.loads(self.lock_path.read_text(encoding="utf-8"))
        self.assertEqual(content["pid"], os.getpid())
        lock.release()

    def test_context_manager(self) -> None:
        with InstanceLock(self.lock_path) as lock:
            self.assertTrue(self.lock_path.exists())
        self.assertFalse(self.lock_path.exists())

    def test_context_manager_raises_if_blocked(self) -> None:
        with patch("core.instance_lock.is_pid_running", return_value=True):
            self.lock_path.write_text(json.dumps({"pid": 99999, "started_at": "active"}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                with InstanceLock(self.lock_path):
                    pass


if __name__ == "__main__":
    unittest.main()
