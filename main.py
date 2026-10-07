"""Clash of Clans Autonomous Bot — Main Entry Point.

Starts the bot frontend and orchestrator across configured accounts
on Windows PC or Android devices via ADB.

Usage:
    python main.py                           # Run using default backend from config
    python main.py --backend pc              # Run on Windows PC (Google Play Games)
    python main.py --backend adb             # Run on Android phone/tablet/emulator via ADB
    python main.py --backend adb --serial X  # Run on specific ADB device serial
    python main.py --dry-run                 # Intercept all inputs, log actions without clicking
    python main.py --account Player1         # Target single account
    python main.py --preflight               # Run readiness diagnostics and exit
    python main.py --status                  # Display database timers & attack statistics
    python main.py --once                    # Run one cycle then exit
"""

from __future__ import annotations

import sys

# Ensure UTF-8 output encoding on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

__version__ = "2.0.0"


def main() -> None:
    """Delegate execution to frontend.launcher."""
    from frontend.launcher import run
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
