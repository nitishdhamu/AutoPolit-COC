"""Launcher and CLI Frontend for Clash of Clans Autonomous Bot.

Provides command-line argument parsing, backend selection (PC, ADB, Mock, Dry-Run),
interactive backend menu, runtime preflight validation, status reporting,
and graceful shutdown handling.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

# Ensure UTF-8 output encoding across all Windows console environments
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import yaml
from colorama import Fore, Style, init as colorama_init

from backends.pc_backend import fix_dpi
from core.preflight import validate_live_runtime
from backends import create_backend
from core.config_loader import ConfigLoader
from core.database import Database
from core.instance_lock import InstanceLock
from core.orchestrator import Orchestrator

__version__ = "2.0.0"

logger = logging.getLogger(__name__)


def setup_logging(cfg: ConfigLoader) -> logging.Logger:
    """Configure rotating file and colored console logging."""
    log_cfg = cfg.logging_cfg
    log_dir = cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "bot.log"
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=log_cfg.get("max_file_size_mb", 10) * 1024 * 1024,
        backupCount=log_cfg.get("backup_count", 5),
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(
        logging.Formatter(
            f"{Fore.CYAN}%(asctime)s{Style.RESET_ALL} | "
            f"%(levelname)-8s | "
            f"{Fore.YELLOW}%(name)-25s{Style.RESET_ALL} | "
            f"%(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    if log_cfg.get("console", True):
        root_logger.addHandler(console_handler)

    return logging.getLogger("coc_bot")


def print_banner() -> None:
    """Print startup ASCII banner."""
    banner = f"""
{Fore.GREEN}+----------------------------------------------------------+
|       [CoC]   Clash of Clans Autonomous Bot   [CoC]      |
|              Super Dragon Farmer  v{__version__}                 |
|           PC (Windows) * Android (ADB) * Multi-Account   |
+----------------------------------------------------------+{Style.RESET_ALL}
"""
    print(banner)


def prompt_interactive_backend() -> str:
    """Prompt the user interactively to select a backend."""
    print(f"\n{Fore.YELLOW}Select Device / Automation Backend:{Style.RESET_ALL}")
    print("  [1] Windows PC (Google Play Games)")
    print("  [2] Android Device / Emulator (ADB)")
    print("  [3] Dry-Run (simulate inputs on PC)")
    print("  [4] Mock (offline tests)")

    choice_map = {
        "1": "pc",
        "2": "adb",
        "3": "dry_run",
        "4": "mock",
        "pc": "pc",
        "adb": "adb",
        "dry_run": "dry_run",
        "mock": "mock",
    }

    try:
        user_input = input(f"\nEnter choice [1-4] (default: 1): ").strip()
        return choice_map.get(user_input.lower(), "pc")
    except (EOFError, KeyboardInterrupt):
        print("\nExiting.")
        sys.exit(0)


def filter_account(cfg: ConfigLoader, account_spec: str) -> None:
    """Filter configuration to target a single account by index or name."""
    target_idx: Optional[int] = None
    if account_spec.isdigit():
        target_idx = int(account_spec)

    matched = None
    for i, acc in enumerate(cfg.accounts):
        if target_idx is not None and i == target_idx:
            matched = acc
            break
        if acc.get("name", "").lower() == account_spec.lower():
            matched = acc
            break
        if acc.get("player_name", "").lower() == account_spec.lower():
            matched = acc
            break

    if not matched:
        print(f"{Fore.RED}[ERROR] Account '{account_spec}' not found in configuration.{Style.RESET_ALL}")
        print("Available accounts:")
        for i, a in enumerate(cfg.accounts):
            print(f"  [{i}] {a.get('name')} (TH{a.get('th_level', '?')})")
        sys.exit(1)

    print(f"{Fore.CYAN}Targeting single account:{Style.RESET_ALL} [{target_idx if target_idx is not None else 0}] {matched.get('name')}")
    cfg.config["accounts"] = [matched]


def run_preflight_checks(cfg: ConfigLoader, backend_name: str, serial: Optional[str] = None) -> bool:
    """Run thorough preflight checks and print status table.

    Returns
    -------
    bool
        True if all critical checks passed, False otherwise.
    """
    print(f"\n{Fore.CYAN}=== Preflight Diagnostics ==={Style.RESET_ALL}")
    all_ok = True

    # 1. OCR Check
    ocr_cfg = cfg.config.get("ocr", {})
    tess_path = Path(ocr_cfg.get("tesseract_cmd", ocr_cfg.get("tesseract_path", "tesseract")))
    if tess_path.exists():
        print(f"  {Fore.GREEN}[OK]{Style.RESET_ALL} Tesseract OCR binary found: {tess_path}")
    else:
        # Check if tesseract is on PATH
        import shutil
        if shutil.which("tesseract"):
            print(f"  {Fore.GREEN}[OK]{Style.RESET_ALL} Tesseract found in system PATH")
        else:
            print(f"  {Fore.RED}[FAIL]{Style.RESET_ALL} Tesseract not found at: {tess_path}")
            all_ok = False

    # 2. Database Check
    db_path = cfg.db_path
    try:
        db = Database(str(db_path))
        db.close()
        print(f"  {Fore.GREEN}[OK]{Style.RESET_ALL} SQLite database verified: {db_path}")
    except Exception as e:
        print(f"  {Fore.RED}[FAIL]{Style.RESET_ALL} SQLite database error: {e}")
        all_ok = False

    # 3. Account Configuration
    accounts = [a for a in cfg.accounts if a.get("enabled", True)]
    if accounts:
        print(f"  {Fore.GREEN}[OK]{Style.RESET_ALL} Accounts configured: {len(accounts)} enabled ({len(cfg.accounts)} total)")
        for i, a in enumerate(accounts):
            print(f"       * Account #{i}: {a.get('name')} (TH{a.get('th_level')})")
    else:
        print(f"  {Fore.RED}[FAIL]{Style.RESET_ALL} No enabled accounts in configuration")
        all_ok = False

    # 4. Backend-specific Preflight
    print(f"  Target Backend: {Fore.YELLOW}{backend_name.upper()}{Style.RESET_ALL}")

    if backend_name == "pc":
        is_windows = sys.platform.startswith("win")
        if is_windows:
            print(f"  {Fore.GREEN}[OK]{Style.RESET_ALL} Windows platform detected for PC backend")
        else:
            print(f"  {Fore.RED}[FAIL]{Style.RESET_ALL} PC backend requires Windows")
            all_ok = False

    elif backend_name == "adb":
        from backends.adb_backend import find_adb_path, ADBBackend
        adb_binary = find_adb_path(cfg.config.get("backends", {}).get("adb", {}).get("adb_path"))
        print(f"  {Fore.GREEN}[OK]{Style.RESET_ALL} ADB executable located: {adb_binary}")
        try:
            temp_adb = ADBBackend(cfg.config)
            devices = temp_adb.discover_devices()
            if not devices:
                print(f"  {Fore.YELLOW}[WARN]{Style.RESET_ALL} No ADB devices currently attached")
            else:
                print(f"  {Fore.GREEN}[OK]{Style.RESET_ALL} ADB devices detected: {len(devices)}")
                for d in devices:
                    state_color = Fore.GREEN if d["state"] == "device" else Fore.YELLOW
                    print(f"       * {d['serial']} ({state_color}{d['state']}{Style.RESET_ALL})")
        except Exception as e:
            print(f"  {Fore.YELLOW}[WARN]{Style.RESET_ALL} ADB device query: {e}")

    elif backend_name in ("mock", "dry_run"):
        print(f"  {Fore.GREEN}[OK]{Style.RESET_ALL} Test mode backend selected ({backend_name})")

    print(f"{Fore.CYAN}============================={Style.RESET_ALL}\n")
    return all_ok


def show_bot_status(cfg: ConfigLoader) -> None:
    """Read database and print current bot status, upgrade timers, and stats."""
    print(f"\n{Fore.CYAN}=== Current Bot State & Database Status ==={Style.RESET_ALL}")
    db_path = cfg.db_path
    if not db_path.exists():
        print(f"Database file does not exist yet at {db_path}.")
        return

    db = Database(str(db_path))
    try:
        accounts = db.get_enabled_accounts()
        print(f"\n{Fore.YELLOW}Registered Accounts ({len(accounts)}):{Style.RESET_ALL}")
        if not accounts:
            print("  No accounts recorded in database yet.")
        else:
            for acc in accounts:
                last_act = acc.get("last_active") or "Never"
                print(f"  * ID {acc.get('id')}: {acc.get('name')} (TH{acc.get('th_level', '?')}) | Last Active: {last_act}")

        upgrades = db.get_active_upgrades()
        print(f"\n{Fore.YELLOW}Active Upgrades ({len(upgrades)}):{Style.RESET_ALL}")
        if not upgrades:
            print("  No active upgrades currently tracked.")
        else:
            now = datetime.now(timezone.utc)
            for u in upgrades:
                end_str = u.get("end_time")
                remaining_desc = "Unknown"
                if end_str:
                    try:
                        end_dt = datetime.fromisoformat(end_str)
                        rem_sec = int((end_dt - now).total_seconds())
                        if rem_sec > 0:
                            hrs = rem_sec // 3600
                            mins = (rem_sec % 3600) // 60
                            remaining_desc = f"{hrs}h {mins}m remaining"
                        else:
                            remaining_desc = "Completed / Pending check"
                    except Exception:
                        pass
                print(f"  * [{u.get('village', 'home')}] {u.get('building_name')} on Account #{u.get('account_id')} -- {remaining_desc}")

        earliest = db.get_earliest_completion()
        if earliest:
            edt, aid, v = earliest
            print(f"\n  {Fore.GREEN}Next builder free:{Style.RESET_ALL} {edt.strftime('%Y-%m-%d %H:%M:%S UTC')} (Account #{aid}, {v})")

    finally:
        db.close()
    print(f"\n{Fore.CYAN}==========================================={Style.RESET_ALL}\n")


def parse_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Clash of Clans Autonomous Bot Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--backend",
        choices=["pc", "adb", "mock", "dry_run"],
        default=None,
        help="Target device backend: 'pc', 'adb', 'mock', or 'dry_run'",
    )
    parser.add_argument(
        "--serial",
        type=str,
        default=None,
        help="ADB device serial number (for targeting specific phone/tablet/emulator)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test mode — intercept and log all inputs without clicking or tapping",
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Run for one specific account index or name (e.g. 0, 'Player1')",
    )
    parser.add_argument(
        "--preflight",
        "--preflight-only",
        action="store_true",
        dest="preflight",
        help="Run preflight diagnostics and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current bot state: accounts, builder timers, next wake time and exit",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one full cycle then exit cleanly",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML configuration file (default: config.yaml)",
    )
    return parser.parse_args(args)


def run(argv: Optional[list[str]] = None) -> int:
    """Entry point for the launcher."""
    colorama_init()
    fix_dpi()

    args = parse_args(argv)

    # Load configuration
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent.parent / args.config

    if not config_path.exists():
        # Fall back to config.example.yaml if config.yaml does not exist
        example_path = config_path.parent / "config.example.yaml"
        if example_path.exists():
            print(f"{Fore.YELLOW}[WARN] {config_path.name} not found; falling back to config.example.yaml{Style.RESET_ALL}")
            config_path = example_path

    try:
        cfg = ConfigLoader(config_path)
    except (FileNotFoundError, ValueError, yaml.YAMLError) as exc:
        print(f"{Fore.RED}[ERROR] Config loading failed: {exc}{Style.RESET_ALL}")
        return 1

    # Status check mode
    if args.status:
        print_banner()
        show_bot_status(cfg)
        return 0

    # Filter account if requested
    if args.account:
        filter_account(cfg, args.account)

    # Determine backend
    active_backend: Optional[str] = args.backend
    if not active_backend:
        configured_backend = cfg.config.get("backends", {}).get("active") or cfg.config.get("active_backend")
        if configured_backend:
            active_backend = configured_backend
        elif sys.stdin.isatty():
            print_banner()
            active_backend = prompt_interactive_backend()
        else:
            active_backend = "pc"

    # Handle --serial override for ADB
    if args.serial:
        backends_dict = cfg.config.setdefault("backends", {})
        adb_dict = backends_dict.setdefault("adb", {})
        adb_dict["device_serial"] = args.serial

    # Preflight check mode
    if args.preflight:
        print_banner()
        ok = run_preflight_checks(cfg, active_backend, args.serial)
        return 0 if ok else 1

    # Setup logging
    logger = setup_logging(cfg)
    print_banner()

    # Preflight validation before live run
    readiness_errors = validate_live_runtime(cfg.config, Path(__file__).parent.parent)
    if readiness_errors and not args.dry_run and active_backend not in ("mock", "dry_run"):
        logger.critical("Refusing live run due to preflight errors:")
        for err in readiness_errors:
            logger.critical("  • %s", err)
        print(f"\n{Fore.RED}Preflight check failed. Use --preflight for full report.{Style.RESET_ALL}\n")
        return 1

    # Determine whether Dry-Run wrapper is needed
    if args.dry_run:
        target_name = active_backend if active_backend != "dry_run" else "pc"
        backend_instance = create_backend("dry_run", cfg.config)
        logger.info("Running in DRY-RUN mode (intercepting actions for target: %s)", target_name)
    else:
        try:
            backend_instance = create_backend(active_backend, cfg.config)
        except Exception as e:
            logger.critical("Failed to create backend '%s': %s", active_backend, e)
            return 1

    logger.info("=" * 60)
    logger.info("CoC Bot v%s starting up", __version__)
    logger.info("Active Backend : %s (%s)", active_backend, backend_instance.__class__.__name__)
    logger.info("Config Path    : %s", config_path)
    logger.info("Dry Run Mode   : %s", args.dry_run)
    logger.info("Single Cycle   : %s", args.once)
    logger.info("=" * 60)

    # Acquire exclusive instance lock
    lock_path = cfg.data_dir / "bot.lock"
    lock = InstanceLock(lock_path)
    if not lock.acquire():
        logger.critical("Another bot instance is already active! Refusing to start concurrent instance.")
        print(f"\n{Fore.RED}[ERROR] Another bot instance is already running (check {lock_path}).{Style.RESET_ALL}\n")
        return 1

    # Initialize Database
    db_path = cfg.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(str(db_path))

    # Initialize Orchestrator
    orchestrator = Orchestrator(
        cfg.config,
        db,
        dry_run=args.dry_run,
        single_cycle=args.once,
        backend=backend_instance,
    )

    # Signal handlers for graceful shutdown
    shutdown_requested = False

    def _signal_handler(sig: int, frame: Any) -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            logger.warning("Force exiting on second interrupt signal...")
            sys.exit(1)
        shutdown_requested = True
        logger.warning("Shutdown signal received (%s) -- gracefully stopping orchestrator...", sig)
        orchestrator.request_shutdown()

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    exit_code = 0
    try:
        logger.info("Launching orchestrator main loop...")
        orchestrator.run()
    except KeyboardInterrupt:
        logger.warning("Interrupted by user (Ctrl+C)")
    except Exception as exc:
        logger.critical("Fatal error in orchestrator: %s", exc, exc_info=True)
        exit_code = 1
    finally:
        logger.info("Cleaning up orchestrator and database...")
        lock.release()
        orchestrator.cleanup()
        db.close()
        logger.info("Bot execution finished cleanly.")

    return exit_code


def main() -> None:
    """CLI script entry point."""
    sys.exit(run())


if __name__ == "__main__":
    main()
