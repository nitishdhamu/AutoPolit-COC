# Clash of Clans Autonomous Bot (v2.0)

A multi-account, 24/7 autonomous Clash of Clans bot written in Python with Tesseract OCR (zero template image dependencies, no ML models required). Features a modular 3-layer architecture supporting both **Windows PC (Google Play Games)** and **Android devices/emulators via ADB**.

---

## Architecture Overview

```
                               ┌─────────────────────────────────┐
                               │   frontend/launcher.py (CLI)    │
                               │   main.py entry point           │
                               └────────────────┬────────────────┘
                                                │
                               ┌────────────────▼────────────────┐
                               │   core/orchestrator.py          │
                               │   bot/* (decision & vision)     │
                               │   • ScreenDetector (Tesseract)  │
                               │   • GemGuard (Accidental gem)   │
                               │   • AttackEngine, Upgrades      │
                               └────────────────┬────────────────┘
                                                │
                     ┌──────────────────────────┴──────────────────────────┐
                     │                                                     │
      ┌──────────────▼──────────────┐                       ┌──────────────▼──────────────┐
      │   backends/pc_backend.py    │                       │   backends/adb_backend.py   │
      │   (Windows PC / Google Play)│                       │   (Android Devices / ADB)   │
      └─────────────────────────────┘                       └─────────────────────────────┘
                     │                                                     │
      ┌──────────────▼──────────────┐                       ┌──────────────▼──────────────┐
      │  backends/dry_run.py (Safe) │                       │  backends/mock_backend.py   │
      └─────────────────────────────┘                       └─────────────────────────────┘
```

1. **Frontend (`frontend/`)**: CLI flags, interactive menu, diagnostics preflight, and status reporting.
2. **Bot Brain (`bot/`, `core/`)**: Village upgrading, farming, troop training, wall management, builder base, and multi-signal `ScreenDetector` using Tesseract OCR.
3. **Backends (`backends/`)**: Hardware/platform abstraction conforming to `Backend` ABC with normalized `(0.0 - 1.0)` coordinate math.

---

## Quick Start

### 1. Requirements

- **Python 3.10+** (Tested on Python 3.12, 3.14)
- **Tesseract OCR**: Installed at `C:\Program Files\Tesseract-OCR\tesseract.exe` or available on system `PATH`.
- For **PC Mode**: Windows 10/11, Google Play Games PC emulator.
- For **ADB Mode**: Android device with USB Debugging enabled, or Android emulator. Android SDK platform-tools `adb.exe`.

### 2. Configuration

Copy the example configuration to `config.yaml`:
```bash
copy config.example.yaml config.yaml
```
Edit `config.yaml` with your accounts, Town Hall levels, builder limits, and preferred farming settings.

---

## Running the Bot

### 1. Preflight Diagnostics
Verify that your OCR, database, accounts, and device connection are ready:
```bash
# Check PC environment
python main.py --preflight --backend pc

# Check ADB environment
python main.py --preflight --backend adb
```

### 2. Status & Timers
View active upgrade timers, account statuses, and recent farm stats without launching the game:
```bash
python main.py --status
```

### 3. Windows PC Mode (Google Play Games)
```bash
# Live run
python main.py --backend pc

# Dry-run test mode (intercepts and logs all actions without clicking)
python main.py --backend pc --dry-run
```

### 4. Android Device Mode (ADB)
```bash
# Auto-detect single connected Android device
python main.py --backend adb

# Target a specific device serial (e.g. if multiple phones/emulators connected)
python main.py --backend adb --serial emulator-5554

# Safe test on Android
python main.py --backend adb --dry-run
```

### 5. Targeting a Specific Account
```bash
# Run only for account 0 or specific account name
python main.py --account 0 --backend pc
python main.py --account "Player1" --backend adb
```

### 6. Interactive Mode
If no flags are supplied and no backend is set in `config.yaml`, the bot displays an interactive console menu:
```bash
python main.py
```

---

## CLI Flags Reference

| Flag | Description |
|---|---|
| `--backend {pc,adb,mock,dry_run}` | Selects target device platform (overrides `config.yaml`). |
| `--serial <serial>` | Specifies ADB device serial number for multi-device environments. |
| `--dry-run` | Intercepts and logs all inputs; prevents any real clicks or taps. |
| `--account <index_or_name>` | Executes bot loop for only one specific account instead of all. |
| `--preflight` | Runs readiness verification across OCR, DB, accounts, and devices then exits. |
| `--status` | Displays database account info, active builder timers, and next wake time. |
| `--once` | Runs a single maintenance/farming cycle and exits cleanly. |
| `--config <path>` | Path to custom YAML configuration file (default: `config.yaml`). |

---

## Safety Features

- **GemGuard**: Inspects dialogs with OCR before and after every tap; automatically detects and dismisses accidental gem spending confirmation prompts.
- **Normalized Coordinates**: Game logic calculates viewport percentages `(0.0 - 1.0)`, automatically scaled to the target device's physical resolution and landscape orientation.
- **Graceful Shutdown**: Pressing `Ctrl+C` cleanly finishes the current operation, saves database state, releases locks, and exits without leaving orphaned processes.

---

## Testing

Run the automated test suite across all modules:
```bash
python -m unittest discover tests
```
Tests mock device interaction and operate offline without requiring connected hardware or live game windows.
