"""
config_loader.py — Configuration Loading & Validation
======================================================
Loads config.yaml, validates all fields, applies defaults,
and exposes a clean, strongly-typed config object to the rest
of the bot.  Accounts are fully dynamic — no hardcoded count.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── Per-account defaults ──────────────────────────────────────────────────────
# max_builders:
#   5 = standard 5 builders (no OTTO)
#   6 = OTTO Hutt unlocked (permanent 6th builder from maxing Builder Base)
# The Goblin Builder (temporary event) is detected separately at runtime.
ACCOUNT_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "th_level": 16,
    "max_builders": 5,
    "bb_max_builders": 3,
}

# ── Top-level section defaults ────────────────────────────────────────────────
SECTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "farming": {
        "min_gold": 400_000,
        "min_elixir": 400_000,
        "min_dark_elixir": 0,
        "max_next_presses": 50,
        "storage_full_threshold": 0.90,
        "final_farm_threshold": 0.95,
        "max_attacks_per_session": 30,
    },
    "army": {
        "preferred_troop": "super_dragon",
        "fallback_troop": "dragon",
        "super_dragon_de_cost": 25_000,
        "eq_spell_count": 5,
    },
    "heroes": {
        "auto_upgrade": True,
        "priority": [
            "Barbarian King",
            "Archer Queen",
            "Minion Prince",
            "Grand Warden",
            "Royal Champion",
        ],
    },
    "lab": {
        "auto_research": True,
    },
    "walls": {
        "auto_upgrade": True,
        "max_per_session": 10,
        "min_resource_keep": 500_000,
    },
    "builder_base": {
        "enabled": True,
        "farm_versus_battles": True,
        "max_versus_battles_per_session": 10,
        "star_lab_research": True,
        "boost_clock_tower": True,
    },
    "safety": {
        "gem_guard_enabled": True,
        "emergency_stop_on_gem_loss": True,
        "screenshot_on_error": True,
        "max_consecutive_errors": 5,
    },
    "emulator": {
        "window_title": "Clash of Clans",
        "process_name": "Clash of Clans",
        "launcher_path": "C:\\Program Files\\Google\\Play Games\\Bootstrapper.exe",
        "game_load_timeout_sec": 120,
        "launch_settle_sec": 5.0,
    },
    "timing": {
        "between_actions_delay_sec": 1.5,
        "screen_load_wait_sec": 3.0,
        "dialog_animation_wait_sec": 0.8,
        "click_delay_ms": 150,
        "wake_before_upgrade_sec": 60,
        "idle_check_interval_min": 30,
    },
    "ocr": {
        "tesseract_path": "C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
        "default_psm": 7,
        "number_whitelist": "0123456789KkMm.,/ ",
    },
    "paths": {
        "template_dir": "templates",
        "screenshot_dir": "Screenshots",
        "log_dir": "logs",
        "data_dir": "data",
        "db_file": "data/bot.db",
    },
    "logging": {
        "level": "INFO",
        "log_dir": "logs",
        "max_file_size_mb": 10,
        "backup_count": 5,
        "console": True,
    },
    "detection": {
        "strategy": "smart",
        "log_detection_method": True,
    },
    "backends": {
        "pc": {
            "window_title": "Google Play Games",
            "ocr_scale": 2,
            "tap_delay_sec": 0.15,
            "jitter_px": 3,
            "screenshot_interval_sec": 0.5,
        },
        "adb": {
            "device_serial": None,
            "adb_path": None,
            "package_name": "com.supercell.clashofclans",
            "activity_name": "com.supercell.clashofclans.GameApp",
            "ocr_scale": 2,
            "tap_delay_sec": 0.15,
            "jitter_px": 3,
            "screenshot_interval_sec": 0.5,
        },
        "mock": {
            "screen_size": [1920, 1080],
        },
        "dry_run": {
            "target_backend": "pc",
        },
    },
}


class ConfigLoader:
    """
    Loads, validates and exposes the bot configuration.

    Usage
    -----
    cfg = ConfigLoader()
    cfg = ConfigLoader("path/to/config.yaml")

    # Access whole config dict
    all_cfg = cfg.config

    # Access sub-sections directly
    accounts = cfg.accounts          # list[dict]
    farming  = cfg.farming           # dict
    timing   = cfg.timing            # dict

    # Convenience helpers
    n = cfg.account_count            # int
    acc = cfg.get_account(index)     # dict | None
    bld = cfg.get_max_builders(idx)  # int (home village)
    bbd = cfg.get_bb_max_builders(idx)  # int (builder base)
    """

    def __init__(self, config_path: str | os.PathLike | None = None) -> None:
        if config_path is None:
            # Resolve relative to the project root (parent of core/)
            project_root = Path(__file__).parent.parent
            config_path = project_root / "config.yaml"

        self._path = Path(config_path)
        self.config: dict[str, Any] = {}
        self._load()
        self._apply_defaults()
        self._validate()
        logger.info("Config loaded from %s (%d account(s))", self._path, self.account_count)

    # ── Public properties ─────────────────────────────────────────────────────

    @property
    def accounts(self) -> list[dict]:
        """Return only *enabled* accounts, in configured order."""
        raw = self.config.get("accounts", [])
        return [a for a in raw if a.get("enabled", True)]

    @property
    def account_count(self) -> int:
        return len(self.accounts)

    @property
    def farming(self) -> dict:
        return self.config["farming"]

    @property
    def army(self) -> dict:
        return self.config["army"]

    @property
    def heroes(self) -> dict:
        return self.config["heroes"]

    @property
    def lab(self) -> dict:
        return self.config["lab"]

    @property
    def walls(self) -> dict:
        return self.config["walls"]

    @property
    def builder_base(self) -> dict:
        return self.config["builder_base"]

    @property
    def safety(self) -> dict:
        return self.config["safety"]

    @property
    def emulator(self) -> dict:
        return self.config["emulator"]

    @property
    def timing(self) -> dict:
        return self.config["timing"]

    @property
    def ocr(self) -> dict:
        return self.config["ocr"]

    @property
    def paths(self) -> dict:
        return self.config["paths"]

    @property
    def logging_cfg(self) -> dict:
        return self.config["logging"]

    @property
    def active_backend(self) -> str:
        return str(self.config.get("active_backend", "pc"))

    @property
    def backends(self) -> dict:
        return self.config.get("backends", {})

    # ── Account convenience helpers ───────────────────────────────────────────

    def get_account(self, index: int) -> dict | None:
        """Return the account dict at *logical* index (into enabled+sorted list)."""
        accs = self.accounts
        if 0 <= index < len(accs):
            return accs[index]
        return None

    def get_account_by_name(self, name: str) -> dict | None:
        for acc in self.accounts:
            if acc.get("name", "").lower() == name.lower():
                return acc
        return None

    def get_max_builders(self, index: int) -> int:
        """Home village builder cap for account at *index*."""
        acc = self.get_account(index)
        if acc:
            return int(acc.get("max_builders", ACCOUNT_DEFAULTS["max_builders"]))
        return ACCOUNT_DEFAULTS["max_builders"]

    def get_bb_max_builders(self, index: int) -> int:
        """Builder Base builder cap for account at *index*."""
        acc = self.get_account(index)
        if acc:
            return int(acc.get("bb_max_builders", ACCOUNT_DEFAULTS["bb_max_builders"]))
        return ACCOUNT_DEFAULTS["bb_max_builders"]

    def get_th_level(self, index: int) -> int:
        """Town Hall level for account at *index*."""
        acc = self.get_account(index)
        if acc:
            return int(acc.get("th_level", ACCOUNT_DEFAULTS["th_level"]))
        return ACCOUNT_DEFAULTS["th_level"]

    # ── Path helpers ──────────────────────────────────────────────────────────

    @property
    def exclude_goblin_builder(self) -> bool:
        """Whether to exclude the 6th (Goblin) builder slot from automation."""
        return bool(self.config.get("safety", {}).get("exclude_goblin_builder", True))

    @property
    def template_dir(self) -> Path:
        return self._project_root / self.config["paths"]["template_dir"]

    @property
    def db_path(self) -> Path:
        return self._project_root / self.config["paths"]["db_file"]

    @property
    def log_dir(self) -> Path:
        return self._project_root / self.config["paths"]["log_dir"]

    @property
    def screenshot_dir(self) -> Path:
        return self._project_root / self.config["paths"]["screenshot_dir"]

    # ── Internal ──────────────────────────────────────────────────────────────

    @property
    def _project_root(self) -> Path:
        return self._path.parent

    def _load(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(f"Config file not found: {self._path}")
        with open(self._path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"Config must be a YAML mapping, got: {type(data)}")
        self.config = data

    def _apply_defaults(self) -> None:
        """Merge SECTION_DEFAULTS under every top-level section."""
        self.config.setdefault("active_backend", "pc")
        for section, defaults in SECTION_DEFAULTS.items():
            if section not in self.config:
                self.config[section] = {}
            self.config[section] = {**defaults, **self.config[section]}

        # Per-account defaults
        accounts_raw = self.config.get("accounts", [])
        if not isinstance(accounts_raw, list):
            logger.warning("'accounts' key is not a list — resetting to empty list")
            accounts_raw = []

        valid_accounts: list[dict[str, Any]] = []
        for i, acc in enumerate(accounts_raw):
            if not isinstance(acc, dict):
                logger.warning("Account entry %d is not a mapping — skipping", i)
                continue
            for key, default_val in ACCOUNT_DEFAULTS.items():
                acc.setdefault(key, default_val)
            # Supercell ID is what appears in the switcher; player_name is
            # what appears in the village HUD.  Keep legacy ``name`` working
            # by using it as the default for both.
            acc.setdefault("supercell_id_name", acc.get("name", f"Account_{i}"))
            acc.setdefault("player_name", acc.get("name", f"Account_{i}"))
            # Auto-name accounts that have no name
            if "name" not in acc:
                acc["name"] = f"Account_{i}"
            valid_accounts.append(acc)

        self.config["accounts"] = valid_accounts

    def _validate(self) -> None:
        """Sanity-check critical config values."""
        errors: list[str] = []

        # Accounts
        if not self.config.get("accounts"):
            errors.append("No accounts configured. Add at least one entry under 'accounts:'")

        accounts = self.config.get("accounts", [])
        for i, acc in enumerate([a for a in accounts if a.get("enabled", True)]):
            if "supercell_id_index" not in acc:
                acc["supercell_id_index"] = i
            try:
                mb = int(acc.get("max_builders", 5))
                bb = int(acc.get("bb_max_builders", 3))
            except (TypeError, ValueError):
                errors.append(
                    f"Account[{i}] '{acc.get('name')}' builder limits must be integers"
                )
                continue
            # Valid range: 1–5 (no OTTO) or 6 (OTTO unlocked).
            # 7 would be OTTO + Goblin, which is impossible to configure—
            # the bot detects the Goblin dynamically at runtime.
            if not (1 <= mb <= 6):
                errors.append(
                    f"Account[{i}] '{acc.get('name')}' max_builders={mb} "
                    f"out of range (1-6). Use 5 for standard, 6 if OTTO unlocked."
                )
            if not (1 <= bb <= 3):
                errors.append(
                    f"Account[{i}] bb_max_builders={bb} out of range (1-3)"
                )

        # Farming thresholds
        st = self.config["farming"].get("storage_full_threshold", 0.90)
        if not (0.0 < float(st) <= 1.0):
            errors.append(f"farming.storage_full_threshold={st} must be in (0, 1]")

        if errors:
            msg = "Config validation errors:\n" + "\n".join(f"  • {e}" for e in errors)
            raise ValueError(msg)

    def reload(self) -> None:
        """Hot-reload config from disk (useful for long-running sessions)."""
        logger.info("Reloading config from %s", self._path)
        self._load()
        self._apply_defaults()
        self._validate()
