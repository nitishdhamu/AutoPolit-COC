# Clash of Clans Bot — Progress Log

Living document tracking refactoring and improvements.
Updated after each step.

---

- **Step 0**: Completed (Git initialized, `config.yaml` sanitized/gitignored, `refactor/backend-split` branch created).
- **Step 1**: Completed (Full audit across screen, input, actions, features, bugs, structure, and screens).
- **Step 2**: Completed (Implementation plan approved by user review policy).
- **Step 3**: Completed (PC Backend isolation & coordinate normalization):
  - Created `backends/base.py` (`Backend` ABC), `backends/coordinates.py` (normalization math), `backends/pc_backend.py` (Windows PC backend), and `backends/__init__.py` (`create_backend` factory).
  - Moved game modules from `game/` to `bot/` and `automation/ocr.py` to `bot/ocr.py` with backward compatibility shims.
  - Eliminated direct `pyautogui` leaks from `bot/gem_guard.py` (P0-3).
  - Standardized coordinate conventions across all 12 bot modules from mixed coordinates to `(x, y, w, h)` + normalized coordinates `(0.0 - 1.0)`.
  - Wired `Backend` dependency into `core/orchestrator.py` and connected all managers, lifecycle hooks, and safety checks.
  - Added `--backend {pc,adb,mock,dry_run}` CLI flag and Windows UTF-8 stdout encoding fix in `main.py`.
  - Fixed screenshot resolution in `tests/test_screenshot_detection.py`; regression tests pass.
- **Step 4**: Completed (Test Tooling: Mock & Dry-Run Backends, Capture Tool, and Unit Tests):
  - Created `backends/mock_backend.py` with frame playlist injection, deterministic action recording, and state controls.
  - Created `backends/dry_run.py` wrapping backends to suppress all physical inputs while logging actions.
  - Created `tools/capture_samples.py` CLI tool for tagging and capturing screenshots from target devices/backends.
  - Added `backends` and `active_backend` properties and default sections to `core/config_loader.py`.
  - Added comprehensive unit tests in `tests/test_coordinates.py`, `tests/test_config.py`, and `tests/test_backends.py`.
  - All 20 tests passing via `python -m unittest discover tests`.
- **Step 5**: Completed (Multi-Signal ScreenDetector: Template-Free Vision):
  - Created `bot/screen_detector.py` implementing landmark color detection (Supercell splash, loading bar geometry, HSV grass ratio for Home vs Builder Base), targeted OCR phrase signatures, declarative scoring, and frame stability check.
  - Resolved Tesseract PSM 7 full-screen failure and false-positive loading bar purple elixir detection by introducing horizontal aspect ratio and position gating.
  - Refactored `bot/navigation.py` to use `ScreenDetector` for `detect_current_screen`, `detect_which_village`, and `handle_loading_screen` with legacy fallback guards.
  - Updated `bot/gem_guard.py` to guard legacy template vision calls and rely on OCR phrase matching.
  - Removed template existence check blocker in `automation/preflight.py`.
  - Added comprehensive test suite `tests/test_screen_detector.py` passing across all screenshot classes, synthetic frames, and stability checks.
- **Step 6**: Completed (Pure ADB Backend Implementation):
  - Created `backends/adb_backend.py` using pure ADB CLI via subprocess (zero extra third-party dependencies).
  - Implemented auto-detection of ADB binary from configuration, PATH, or platform SDK locations.
  - Implemented device discovery via `adb devices`, auto-selecting single attached devices, validating authorized state, or allowing explicit serial selection.
  - Handled binary screencap capture with fast-path `exec-out screencap -p`, PNG validation, Windows CRLF fallback, auto-rotation from portrait to landscape, and startup capture latency benchmarking.
  - Handled landscape resolution normalization from `wm size` (`width = max(w, h)`, `height = min(w, h)`).
  - Implemented tap, long-press, swipe, Back (keycode 4), Home (keycode 3), Escape (keycode 111), and bottom tray troop selection.
  - Implemented game lifecycle management (`monkey` and `am start` launch, `am force-stop` close, `pidof` running check, and `dumpsys` foreground detection).
  - Implemented auto-reconnect and server restart on transient errors (`restart_adb_server()`).
  - Added 12 unit tests in `tests/test_adb_backend.py` with mocked subprocess. All 41 tests passing repo-wide.
- **Step 7**: Completed (Frontend Launcher & CLI Backend Selector):
  - Created `frontend/launcher.py` with CLI flags: `--backend`, `--serial`, `--dry-run`, `--account`, `--preflight`, `--status`, `--once`, `--config`.
  - Implemented interactive console backend selection fallback when run interactively without CLI flags or configured backend.
  - Implemented comprehensive preflight diagnostics checking OCR binary, SQLite database, configured accounts, and backend targets.
  - Implemented `--status` database query showing active accounts, builder upgrade timers, and next wake time.
  - Handled account filtering by index or name via `--account`.
  - Re-factored `main.py` entry point to delegate directly to `frontend.launcher.run()`.
  - Created `README.md` with complete documentation, architecture diagrams, quick start, and CLI reference.
  - Added unit test suite `tests/test_launcher.py`. All 49 tests passing repo-wide.
- **Step 8**: Completed (P1-P5 Backlog Improvements & Final Polish):
  - Created `core/instance_lock.py` (`InstanceLock`) preventing multiple concurrent bot runs via `data/bot.lock`, with stale process detection and auto-recovery across Windows and POSIX.
  - Wired `InstanceLock` into `frontend/launcher.py` and Orchestrator execution lifecycle.
  - Resolved Builder Base troop deployment relying on PC-only keys (`press_key('q')`) by migrating to `self.backend.select_troop_slot(idx)` and normalized deploy points (`BB_EDGE_DEPLOY_POINTS_NORM`).
  - Implemented automatic database account synchronization on startup (`_sync_accounts_to_db`).
  - Added exponential backoff to `Orchestrator._handle_error_recovery` (15s, 30s, 60s, max 120s).
  - Added unit test suite `tests/test_instance_lock.py`. All 54 tests passing repo-wide.

---

## Architectural Decisions
1. **Three strictly separated layers**:
   - `frontend/`: launcher, backend selector, CLI flags (`--backend pc|adb`), status, graceful stop.
   - `bot/`: game logic, decision loops, timers, OCR calls, `ScreenDetector`.
   - `backends/`: `PCBackend`, `ADBBackend`, `MockBackend`, `DryRunBackend` behind `backends/base.py:Backend` ABC.
2. **Normalized Coordinates**:
   - Brain passes normalized floats `(0.0 <= nx <= 1.0, 0.0 <= ny <= 1.0)`.
   - Bounding boxes are normalized rectangles `(nx, ny, nw, nh)`.
   - Backend converts to device pixels `(px, py)` using active viewport dimensions.
3. **OCR & Screen Detection**:
   - Tesseract only (`bot/ocr.py`). No template matching or ML.
   - `ScreenDetector` combines foreground check, landmark colors, targeted OCR, declarative scored signatures, and frame stability.
4. **Secret Protection**:
   - `config.yaml` is gitignored.
   - `config.example.yaml` committed with placeholder values.

---

## Bugs Found During Audit & Status
1. **Direct `pyautogui` leak in game layer**: FIXED. Abstracted via `Backend` ABC; removed all direct leaks from `bot/gem_guard.py`.
2. **Region coordinate convention mismatch**: FIXED. Standardized all regions to `(x, y, w, h)` and added normalized equivalents `(nx, ny, nw, nh)` across `bot/builder_base.py` and `bot/wall_manager.py`.
3. **Unit test path resolution failure**: FIXED. Auto-resolves parent screenshots directory in `tests/test_screenshot_detection.py`.
4. **Preflight template blocker**: FIXED. Replaced broken template checks with `ScreenDetector` and live OCR validation in `automation/preflight.py`.
5. **Fixed sleeps**: Mitigated with interruptible sleep, configurable delays, and frame stability checks.
6. **No instance lock**: FIXED. Added `core/instance_lock.py` with stale lock detection and wired into `frontend/launcher.py`.
7. **PC-only keyboard deployment**: FIXED. Implemented `select_troop_slot(idx)` mapping to shortcuts on PC and deployment tray taps on ADB, and normalized deploy points.

---

## Ideas List (Deferred / Feature Backlog)
- Machine learning screen classifier (constrained to Tesseract OCR currently).
- Smart base layout detection for automatic building locator instead of fixed fallback coordinates.
- Live attack pathing algorithms and smart spell placement based on defense density.
- Discord/Telegram webhook notifications for upgrade completion and gem drift alerts.

---

## Open Questions
- None (all goals completed).

---

## Manual Test Checklist
- [x] Run `--dry-run` on PC backend and verify actions logged without clicks.
- [x] Run `--dry-run` on ADB backend and verify screencap decoding without clicks.
- [x] Verify `ScreenDetector` correctly identifies all core screens without templates.
- [x] Verify single-instance lock (`core/instance_lock.py`).
- [x] Verify preflight diagnostics (`python main.py --preflight`).
- [x] Verify database status reporting (`python main.py --status`).
- [x] Verify full unit test suite (54/54 tests passing).
