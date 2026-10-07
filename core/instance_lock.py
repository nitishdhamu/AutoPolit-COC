"""Single-instance lockfile protection for Clash of Clans bot.

Prevents multiple concurrent bot processes from running simultaneously
and corrupting state or conflicting over device controls.
Automatically cleans up stale lockfiles from terminated processes.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def is_pid_running(pid: int) -> bool:
    """Check whether a process with the given PID is currently active.

    Parameters
    ----------
    pid : int
        Process ID to test.

    Returns
    -------
    bool
        True if the process exists and is active, False otherwise.
    """
    if pid <= 0:
        return False

    if sys.platform.startswith("win"):
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle != 0:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            try:
                out = subprocess.run(
                    ["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv"],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                )
                return str(pid) in out.stdout
            except Exception:
                return True
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


class InstanceLock:
    """Manages an exclusive lockfile to prevent multiple instances from running."""

    def __init__(self, lock_path: Path | str = "data/bot.lock") -> None:
        """Initialize InstanceLock.

        Parameters
        ----------
        lock_path : Path | str
            Filesystem location of the lockfile.
        """
        self.lock_path = Path(lock_path).resolve()
        self._acquired: bool = False

    def acquire(self) -> bool:
        """Attempt to acquire exclusive instance lock.

        Returns
        -------
        bool
            True if acquired successfully, False if another instance is active.
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        if self.lock_path.exists():
            try:
                content = json.loads(self.lock_path.read_text(encoding="utf-8"))
                existing_pid = int(content.get("pid", -1))
                started_at = content.get("started_at", "unknown")

                if is_pid_running(existing_pid):
                    logger.warning(
                        "Active bot instance detected with PID %d (started at %s). Cannot acquire lock.",
                        existing_pid,
                        started_at,
                    )
                    return False
                else:
                    logger.info("Found stale lockfile from terminated PID %d. Overwriting.", existing_pid)
            except Exception as e:
                logger.warning("Could not read existing lockfile: %s. Overwriting.", e)

        # Write lock file
        lock_data = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "platform": sys.platform,
        }
        try:
            self.lock_path.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
            self._acquired = True
            logger.debug("Acquired instance lockfile at %s (PID %d)", self.lock_path, os.getpid())
            return True
        except Exception as e:
            logger.error("Failed to write lockfile at %s: %s", self.lock_path, e)
            return False

    def release(self) -> None:
        """Release and delete the instance lockfile."""
        if not self._acquired:
            return

        try:
            if self.lock_path.exists():
                try:
                    content = json.loads(self.lock_path.read_text(encoding="utf-8"))
                    if content.get("pid") == os.getpid():
                        self.lock_path.unlink()
                        logger.debug("Released instance lockfile at %s", self.lock_path)
                except Exception:
                    self.lock_path.unlink()
        except Exception as e:
            logger.warning("Error releasing instance lockfile: %s", e)
        finally:
            self._acquired = False

    def __enter__(self) -> InstanceLock:
        if not self.acquire():
            raise RuntimeError(
                f"Another bot instance is currently running! Check lockfile: {self.lock_path}"
            )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()
