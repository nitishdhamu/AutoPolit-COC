"""
Orchestrator — Master control loop for the CoC bot.
=====================================================
Manages the full sleep/wake/farm cycle across all accounts.
Accounts are fully dynamic — driven by config.yaml, no hardcoding.
This is the brain of the bot that coordinates all other modules.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core.config_loader import ConfigLoader
from core.database import Database
from core.scheduler import Scheduler
from core.state_machine import StateMachine, BotState
from backends import create_backend, Backend
from bot.vision import VisionEngine
from bot.ocr import GameOCR
from bot.gem_guard import GemGuard
from bot.navigation import Navigator
from bot.resource_reader import ResourceReader
from bot.account_switcher import AccountSwitcher
from bot.base_manager import BaseManager
from bot.army_manager import ArmyManager
from bot.attack_engine import AttackEngine
from bot.lab_manager import LabManager
from bot.hero_manager import HeroManager
from bot.pet_manager import PetManager
from bot.wall_manager import WallManager
from bot.builder_base import BuilderBaseManager

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Master control loop — the brain of the bot.
    
    Cycle:
    1. Calculate next wake time from DB (earliest upgrade completion)
    2. Sleep until wake time
    3. Launch emulator → load game
    4. For each enabled account:
       a. Switch to account
       b. Check/verify completed upgrades
       c. Collect resources
       d. Manage Super Dragon boost
       e. Train army
       f. Farm until storages full (400k+ loot filter)
       g. Start next upgrade (Recommended)
       h. Record timer → REPEAT until all builders busy
       i. Start Lab research if free
       j. Upgrade Heroes if affordable
       k. Upgrade Pets if Pet House free
       l. Final farm: fill storages to 100%
       m. Spend excess loot on Walls
       n. Builder Base cycle:
          - Collect → Farm Versus → Assign all builders → Star Lab → Boost CT
       o. Switch back to Home Village
    5. Close emulator
    6. Go back to step 1
    """

    def __init__(self, config: dict, db: Database,
                 dry_run: bool = False, single_cycle: bool = False,
                 backend: Optional[Backend] = None):
        """
        Initialize the orchestrator with all dependencies.
        
        Args:
            config: Loaded bot configuration dict.
            db: Initialized Database instance.
            dry_run: If True, log actions but don't send real clicks.
            single_cycle: If True, run one full cycle then exit.
            backend: Optional Backend instance. If None, created from config.
        """
        self.config = config
        self.db = db
        self.dry_run = dry_run
        self.single_cycle = single_cycle
        self._shutdown_requested = False
        self._consecutive_errors = 0
        self._max_errors = config.get("safety", {}).get("max_consecutive_errors", 5)
        # Cache of account_name → DB integer account_id (resolved at session start)
        self._account_db_ids: dict[str, int] = {}
        # Track which account the bot believes is currently active on screen
        self._current_account_name: str | None = None

        # Core modules
        self.scheduler = Scheduler(db, config)
        self.state_machine = StateMachine(db)

        # Backend initialization (unified device/platform driver)
        active_backend_name = config.get("active_backend", "pc")
        self.backend: Backend = backend if backend is not None else create_backend(active_backend_name, config)

        # Vision & OCR engines
        template_dir = str(Path(__file__).parent.parent / config.get("paths", {}).get("template_dir", "templates"))
        self.vision = VisionEngine(template_dir)
        self.ocr = GameOCR(config)

        # Screen & input handles (delegated directly to active backend)
        self.screen = self.backend
        self.input_ctrl = self.backend

        # Safety layer
        self.gem_guard = GemGuard(config, self.screen, self.vision, self.ocr, backend=self.backend)
        self.backend.set_gem_guard(self.gem_guard)

        # Game logic layer
        self.navigator = Navigator(
            config, self.screen, self.vision, self.ocr, self.input_ctrl, backend=self.backend
        )
        self.resource_reader = ResourceReader(config, self.screen, self.ocr, backend=self.backend)
        self.account_switcher = AccountSwitcher(
            config, self.screen, self.vision, self.ocr, self.input_ctrl, self.navigator,
            backend=self.backend
        )
        self.base_manager = BaseManager(
            config, self.screen, self.vision, self.ocr, self.input_ctrl,
            self.navigator, self.resource_reader, backend=self.backend
        )
        self.army_manager = ArmyManager(
            config, self.screen, self.vision, self.ocr, self.input_ctrl, self.navigator,
            backend=self.backend
        )
        self.attack_engine = AttackEngine(
            config, self.screen, self.vision, self.ocr, self.input_ctrl,
            self.navigator, self.resource_reader, self.army_manager, backend=self.backend
        )
        self.lab_manager = LabManager(
            config, self.screen, self.vision, self.ocr, self.input_ctrl, self.navigator,
            backend=self.backend
        )
        self.hero_manager = HeroManager(
            config, self.screen, self.vision, self.ocr, self.input_ctrl,
            self.navigator, self.resource_reader, backend=self.backend
        )
        self.pet_manager = PetManager(
            config, self.screen, self.vision, self.ocr, self.input_ctrl,
            self.navigator, self.resource_reader, backend=self.backend
        )
        self.wall_manager = WallManager(
            config, self.screen, self.vision, self.ocr, self.input_ctrl,
            self.navigator, self.resource_reader, backend=self.backend
        )
        self.builder_base = BuilderBaseManager(
            config, self.screen, self.vision, self.ocr, self.input_ctrl,
            self.navigator, self.resource_reader, backend=self.backend
        )

        # Synchronize accounts with database
        self._sync_accounts_to_db()

        logger.info(
            "Orchestrator initialized (backend=%s, dry_run=%s, single_cycle=%s)",
            self.backend.backend_name, dry_run, single_cycle
        )

    def _sync_accounts_to_db(self) -> None:
        """Synchronize accounts from configuration into database."""
        accounts = self.config.get("accounts", [])
        for acc in accounts:
            acc_name = acc.get("name")
            if not acc_name:
                continue
            enabled = acc.get("enabled", True)
            try:
                db_id = self.db.upsert_account(
                    name=acc_name,
                    supercell_id_index=acc.get("supercell_id_index", 0),
                    th_level=acc.get("th_level", 16),
                    enabled=enabled,
                )
                self._account_db_ids[acc_name] = db_id
            except Exception as e:
                logger.warning("Could not sync account '%s' to database: %s", acc_name, e)

    def request_shutdown(self):
        """Request graceful shutdown — the main loop will exit on next check."""
        logger.warning("Shutdown requested")
        self._shutdown_requested = True

    def run(self):
        """
        Main infinite loop — the bot's heartbeat.
        
        Cycle: Sleep → Wake → Launch → Process all accounts → Close → Repeat.
        """
        self.state_machine.transition(BotState.WAKING)
        logger.info("=" * 60)
        logger.info("🚀 Orchestrator main loop started")
        logger.info("=" * 60)

        cycle_count = 0

        while not self._shutdown_requested:
            cycle_count += 1
            logger.info(f"\n{'='*60}")
            logger.info(f"🔄 CYCLE {cycle_count} starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"{'='*60}")

            try:
                self._run_one_cycle()
                self._consecutive_errors = 0  # Reset on success

                if self.single_cycle:
                    logger.info("Single cycle mode — exiting after one cycle")
                    break

                # Calculate sleep duration
                sleep_seconds = self.scheduler.get_sleep_duration()

                if sleep_seconds > 0:
                    self.state_machine.transition(BotState.SLEEPING)
                    logger.info(f"💤 Sleeping for {Scheduler._format_duration(timedelta(seconds=sleep_seconds))}")
                    logger.info(self.scheduler.get_timer_summary())
                    self._interruptible_sleep(sleep_seconds)
                else:
                    logger.info("⚡ No sleep needed — immediate next cycle")

            except Exception as e:
                self._consecutive_errors += 1
                logger.error(
                    f"💥 Error in cycle {cycle_count} "
                    f"(consecutive: {self._consecutive_errors}/{self._max_errors}): {e}",
                    exc_info=True
                )

                if self._consecutive_errors >= self._max_errors:
                    logger.critical(
                        f"🚨 EMERGENCY SHUTDOWN: {self._consecutive_errors} consecutive errors!"
                    )
                    self._emergency_shutdown()
                    break

                # Error recovery: close emulator, wait, retry
                self._handle_error_recovery()

        logger.info("Main loop ended")

    def _run_one_cycle(self):
        """Execute one complete wake-farm-sleep cycle across all accounts."""
        # Step 1: Launch target game / emulator
        self.state_machine.transition(BotState.LAUNCHING_EMULATOR)
        logger.info("🖥️ Launching target environment (%s)...", self.backend.backend_name)

        if not self.dry_run:
            connected = self.backend.is_connected()
            if not connected:
                connected = self.backend.connect()
            if not connected or not self.backend.is_game_foreground():
                if not self.backend.launch_game():
                    raise RuntimeError(f"Failed to launch game on backend '{self.backend.backend_name}'")
                if not self.backend.is_connected():
                    self.backend.connect()
        else:
            logger.info("[DRY RUN] Skipping target launch")

        self.state_machine.transition(BotState.LOADING_GAME)
        logger.info("✅ Game loaded successfully")

        # Step 2: Resolve DB account IDs for all enabled accounts
        accounts = self.config.get("accounts", [])
        enabled_accounts = [a for a in accounts if a.get("enabled", True)]

        for acc in enabled_accounts:
            acc_name = acc["name"]
            if acc_name not in self._account_db_ids:
                db_id = self.db.upsert_account(
                    name=acc_name,
                    supercell_id_index=acc.get("supercell_id_index", 0),
                    th_level=acc.get("th_level", 16),
                    enabled=True,
                )
                self._account_db_ids[acc_name] = db_id
                logger.debug("Resolved account '%s' → DB id=%d", acc_name, db_id)

        # Step 3: Process each enabled account
        failed_accounts: list[str] = []
        for i, account in enumerate(enabled_accounts):
            if self._shutdown_requested:
                logger.warning("Shutdown requested — skipping remaining accounts")
                break

            logger.info("\n%s", "─" * 50)
            logger.info(
                "👤 Processing account %d/%d: %s (TH%s)",
                i + 1, len(enabled_accounts),
                account['name'], account.get('th_level', '?')
            )
            logger.info("%s", "─" * 50)

            try:
                self._process_account(account, is_first=(i == 0))
                self.db.update_account_active(self._account_db_ids[account["name"]])
            except Exception as e:
                logger.error("Error processing account %s: %s",
                             account['name'], e, exc_info=True)
                # Try to recover to home screen for next account
                self._try_recover_to_home()
                failed_accounts.append(account["name"])

        # Step 4: Close target environment / emulator
        self.state_machine.transition(BotState.SHUTTING_DOWN)
        logger.info("🔌 Closing target environment (%s)...", self.backend.backend_name)
        if not self.dry_run:
            try:
                self.backend.close_game()
            except Exception as e:
                logger.warning("Backend close_game error: %s", e)
            try:
                self.emulator.close()
            except Exception:
                pass
        else:
            logger.info("[DRY RUN] Skipping emulator/game close")

        logger.info("✅ Cycle complete!")
        if failed_accounts:
            raise RuntimeError(
                "Account processing failed for: " + ", ".join(failed_accounts)
            )

    def _process_account(self, account: dict, is_first: bool = False):
        """
        Full processing cycle for a single account.
        
        Args:
            account: Account config dict.
            is_first: True if this is the first account (skip switching).
        """
        account_name = account["name"]
        player_name = account.get("player_name", account_name)
        th_level = account.get("th_level", 16)
        max_builders = account.get("max_builders", 5)
        scid_index = account.get("supercell_id_index", 0)
        # Use DB integer id (registered during _run_one_cycle)
        account_id = self._account_db_ids.get(account_name)
        if account_id is None:
            logger.warning("Account '%s' not pre-registered; upserting now.", account_name)
            account_id = self.db.upsert_account(
                name=account_name,
                supercell_id_index=scid_index,
                th_level=th_level,
                enabled=True,
            )
            self._account_db_ids[account_name] = account_id

        # --- Switch account (skip if first and already loaded) ---
        if not is_first:
            self.state_machine.transition(BotState.SWITCHING_ACCOUNT)
            logger.info("🔄 Switching to account: %s", account_name)
            if not self.dry_run:
                if not self.account_switcher.switch_to(account_target=account, account_name=account_name):
                    raise RuntimeError("Failed to switch to account " + account_name)
                # Verify we actually landed on the right account
                time.sleep(1.5)  # brief settle before reading screen
                verified = self.account_switcher.verify_current_account(player_name)
                if verified:
                    self._current_account_name = account_name
                    logger.info("✅ Switched to %s (verified on screen)", account_name)
                else:
                    read_name, _ = self.account_switcher.identify_current_account()
                    raise RuntimeError(
                        "Account switch verification failed: expected '%s', got '%s'"
                        % (player_name, read_name or "<unreadable>")
                    )
            else:
                logger.info("[DRY RUN] Skipping switch to %s", account_name)
                self._current_account_name = account_name
        else:
            # First account — just read screen to confirm which one is loaded
            if not self.dry_run:
                time.sleep(1.5)
                read_name = self.account_switcher.read_current_account_name()
                if read_name:
                    self._current_account_name = read_name
                    _, matched_idx = self.account_switcher.identify_current_account()
                    if matched_idx is not None:
                        cfg_name = self.account_switcher._get_account_name(matched_idx)
                        if cfg_name.lower() == account_name.lower():
                            logger.info(
                                "✅ First account confirmed on screen: '%s'", cfg_name
                            )
                        else:
                            logger.warning(
                                "First account is '%s', expected '%s'; switching explicitly.",
                                cfg_name, account_name,
                            )
                            if not self.account_switcher.switch_to(account_target=account, account_name=account_name):
                                raise RuntimeError(
                                    "Failed to switch initial account to " + account_name
                                )
                            if not self.account_switcher.verify_current_account(player_name):
                                raise RuntimeError(
                                    "Initial account switch could not be verified: " + account_name
                                )
                            self._current_account_name = account_name
                    else:
                        logger.warning(
                            "First account name '%s' was not recognised; switching explicitly.",
                            read_name,
                        )
                        if not self.account_switcher.switch_to(account_target=account, account_name=account_name):
                            raise RuntimeError(
                                "Failed to switch unrecognised initial account to " + account_name
                            )
                        if not self.account_switcher.verify_current_account(player_name):
                            raise RuntimeError(
                                "Initial account switch could not be verified: " + account_name
                            )
                        self._current_account_name = account_name
                else:
                    logger.warning("First account name unreadable; switching explicitly.")
                    if not self.account_switcher.switch_to(account_target=account, account_name=account_name):
                        raise RuntimeError(
                            "Failed to switch unreadable initial account to " + account_name
                        )
                    if not self.account_switcher.verify_current_account(player_name):
                        raise RuntimeError(
                            "Initial account switch could not be verified: " + account_name
                        )
                    self._current_account_name = account_name
            else:
                self._current_account_name = account_name
                logger.info("[DRY RUN] First account: '%s'", account_name)

        # --- Check completed upgrades ---
        self.state_machine.transition(BotState.CHECKING_BUILDS)
        logger.info(
            "═══ NOW PROCESSING: %s (screen confirmed: %s) ═══",
            account_name,
            self._current_account_name or "unverified",
        )
        completed = self.scheduler.check_completed_upgrades(account_id=account_id)
        if completed:
            logger.info(f"Found {len(completed)} completed upgrades")

        # --- Collect resources ---
        self.state_machine.transition(BotState.COLLECTING)
        logger.info("💰 Collecting resources...")
        if not self.dry_run:
            self.base_manager.collect_resources()
        resources = self._read_and_log_resources(account_id)

        # --- Manage Super Dragon boost ---
        self.state_machine.transition(BotState.MANAGING_SUPER_TROOPS)
        current_de = resources.get("dark_elixir", 0)

        if not self.dry_run:
            if not self.army_manager.is_super_dragon_active():
                if self.army_manager.can_afford_super_dragon(current_de):
                    logger.info("🐉 Activating Super Dragon boost...")
                    self.army_manager.activate_super_dragon()
                    # record_super_troop expects a datetime object (not a string)
                    expires_dt = datetime.now(timezone.utc) + timedelta(days=3)
                    self.db.record_super_troop(account_id, "super_dragon", expires_dt)
                else:
                    logger.info(
                        "💎 Not enough DE for Super Dragon (%d/25000)", current_de
                    )
            else:
                logger.info("🐉 Super Dragon already active")

        # --- Main farm → upgrade loop ---
        self._farm_and_upgrade_loop(account, account_id, th_level, max_builders)

        # --- Lab research ---
        self.state_machine.transition(BotState.STARTING_LAB)
        if self.config.get("lab", {}).get("auto_research", True):
            logger.info("🔬 Checking Laboratory...")
            if not self.dry_run:
                try:
                    research = self.lab_manager.start_research()
                    if research:
                        logger.info("Started research: %s", research)
                        # record_lab_research expects a datetime object for end_time
                        timer_td = research.get("timer", timedelta(0))
                        if isinstance(timer_td, timedelta):
                            end_dt = datetime.now(timezone.utc) + timer_td
                        elif isinstance(timer_td, (int, float)):
                            end_dt = datetime.now(timezone.utc) + timedelta(seconds=int(timer_td))
                        else:
                            end_dt = datetime.now(timezone.utc) + timedelta(hours=1)
                        self.db.record_lab_research(
                            account_id, "home",
                            research.get("name", "unknown"),
                            end_dt,
                        )
                except Exception as e:
                    logger.warning("Lab research failed: %s", e)

        # --- Hero upgrades ---
        self.state_machine.transition(BotState.UPGRADING_HEROES)
        if self.config.get("heroes", {}).get("auto_upgrade", True):
            logger.info("🦸 Checking Hero upgrades...")
            if not self.dry_run:
                try:
                    hero = self.hero_manager.upgrade_next_hero()
                    if hero:
                        logger.info("Started hero upgrade: %s", hero)
                        # record_hero_upgrade expects a datetime object for end_time
                        timer_td = hero.get("timer", timedelta(0))
                        if isinstance(timer_td, timedelta):
                            end_dt = datetime.now(timezone.utc) + timer_td
                        elif isinstance(timer_td, (int, float)):
                            end_dt = datetime.now(timezone.utc) + timedelta(seconds=int(timer_td))
                        else:
                            end_dt = datetime.now(timezone.utc) + timedelta(hours=1)
                        self.db.record_hero_upgrade(
                            account_id,
                            hero.get("name", "unknown"),
                            end_dt,
                        )
                except Exception as e:
                    logger.warning("Hero upgrade failed: %s", e)

        # --- Pet upgrades ---
        self.state_machine.transition(BotState.UPGRADING_PETS)
        if self.config.get('pets', {}).get('auto_upgrade', True):
            logger.info('🐾 Checking Pet upgrades...')
            if not self.dry_run:
                try:
                    pet = self.pet_manager.upgrade_next_pet()
                    if pet:
                        logger.info('Started pet upgrade: %s', pet)
                        timer_td = pet.get('timer', timedelta(0))
                        if isinstance(timer_td, timedelta):
                            end_dt = datetime.now(timezone.utc) + timer_td
                        elif isinstance(timer_td, (int, float)):
                            end_dt = datetime.now(timezone.utc) + timedelta(seconds=int(timer_td))
                        else:
                            end_dt = datetime.now(timezone.utc) + timedelta(hours=1)
                        self.db.record_hero_upgrade(
                            account_id,
                            pet.get('name', 'unknown'),
                            end_dt,
                        )
                except Exception as e:
                    logger.warning('Pet upgrade failed: %s', e)

        # --- Wall upgrades (excess loot) ---
        self.state_machine.transition(BotState.UPGRADING_WALLS)
        if self.config.get("walls", {}).get("auto_upgrade", True):
            logger.info("🧱 Checking Wall upgrades...")
            if not self.dry_run:
                try:
                    free, total = self.base_manager.get_builder_status()
                    # Walls are INSTANT upgrades in COC — they don't use a builder.
                    # We only wall-dump when all builders are busy (free==0),
                    # because otherwise the loot should go toward building upgrades.
                    if free == 0:
                        walls_done = self.wall_manager.upgrade_walls(max_upgrades=5)
                        logger.info(f"Upgraded {walls_done} wall segments")
                except Exception as e:
                    logger.warning(f"Wall upgrade failed: {e}")

        # --- Final farm: top off storages ---
        self.state_machine.transition(BotState.FARMING)
        logger.info("💰 Final farm: topping off storages...")
        if not self.dry_run:
            try:
                final_threshold = self.config.get("farming", {}).get(
                    "final_farm_threshold", 0.95
                )
                if not self.resource_reader.are_storages_full(
                    th_level, threshold=final_threshold
                ):
                    attacks = self.attack_engine.farm_until_full(account)
                    logger.info("Final farm: %d attacks", attacks)
            except Exception as e:
                logger.warning("Final farm failed: %s", e)

        # --- Builder Base cycle ---
        if self.config.get("builder_base", {}).get("enabled", True):
            self.state_machine.transition(BotState.SWITCHING_TO_BUILDER_BASE)
            logger.info("⛵ Switching to Builder Base...")
            if not self.dry_run:
                try:
                    # Inject DB id so builder_base can record timers correctly
                    account_with_id = dict(account)
                    account_with_id["_db_id"] = account_id
                    bb_result = self.builder_base.manage_builder_base(account_with_id, self.db)
                    logger.info(f"Builder Base result: {bb_result}")
                except Exception as e:
                    logger.warning(f"Builder Base cycle failed: {e}")
                    # Try to get back to home village
                    try:
                        self.navigator.switch_to_home_village()
                    except Exception:
                        pass

            self.state_machine.transition(BotState.SWITCHING_TO_HOME)

        logger.info(f"✅ Account {account_name} processing complete!")

    def _farm_and_upgrade_loop(self, account: dict, account_id: int,
                                th_level: int, max_builders: int):
        """
        Core farm-upgrade loop: farm until full, start upgrade, repeat.
        Continues until all builders are busy or max_builders reached.
        """
        self.state_machine.transition(BotState.FARMING)
        loop_count = 0
        max_loops = max_builders + 1  # Safety cap

        while loop_count < max_loops and not self._shutdown_requested:
            loop_count += 1

            # Check builder status using the per-account cap.
            # has_idle_builder() handles goblin exclusion internally.
            if not self.dry_run:
                idle = self.base_manager.has_idle_builder(max_builders)
                raw_free, raw_total = self.base_manager.get_builder_status()
                logger.info(
                    "🔨 Builders (raw HUD): %d free / %d total  |  cap: %d  |  idle: %s",
                    raw_free, raw_total, max_builders, idle,
                )

                if not idle:
                    logger.info("All builders busy — exiting farm loop")
                    break

            else:
                logger.info("[DRY RUN] Skipping builder check")
                break

            # Train army
            self.state_machine.transition(BotState.TRAINING_ARMY)
            logger.info("⚔️ Training army...")
            if not self.dry_run:
                current_de = self.resource_reader.read_dark_elixir()
                is_boosted = self.army_manager.is_super_dragon_active()
                troop_type = self.army_manager.get_troop_type(current_de, is_boosted)
                self.army_manager.train_army(troop_type)

            # Farm until storages are sufficiently full
            self.state_machine.transition(BotState.FARMING)
            logger.info("🌾 Farming until storages full...")
            if not self.dry_run:
                attacks = self.attack_engine.farm_until_full(account)
                logger.info(f"Farming done: {attacks} attacks")

            # Start upgrade
            self.state_machine.transition(BotState.STARTING_UPGRADE)
            logger.info("🏗️ Starting upgrade (Recommended)...")
            if not self.dry_run:
                upgrade_info = self.base_manager.start_suggested_upgrade()
                if upgrade_info:
                    # timer may be a timedelta or int seconds
                    raw_timer = upgrade_info.get("timer", timedelta(0))
                    if isinstance(raw_timer, timedelta):
                        timer_sec = int(raw_timer.total_seconds())
                    elif isinstance(raw_timer, (int, float)):
                        timer_sec = int(raw_timer)
                    else:
                        timer_sec = 0
                    self.scheduler.record_upgrade(
                        account_id,
                        upgrade_info.get("name", "unknown"),
                        "home",
                        timer_sec,
                    )
                    logger.info("✅ Upgrade started: %s", upgrade_info)
                else:
                    logger.warning("No upgrade available — breaking loop")
                    break

            self.state_machine.transition(BotState.RECORDING_TIMERS)

    def _read_and_log_resources(self, account_id: int) -> dict:
        """Read current resources and log + record them."""
        if self.dry_run:
            return {"gold": 0, "elixir": 0, "dark_elixir": 0, "gems": 0}

        try:
            resources = self.resource_reader.read_all()
            logger.info(
                f"💰 Resources: Gold={resources.get('gold', 0):,} | "
                f"Elixir={resources.get('elixir', 0):,} | "
                f"DE={resources.get('dark_elixir', 0):,} | "
                f"Gems={resources.get('gems', 0)}"
            )
            self.db.record_resources(
                account_id,
                resources.get("gold", 0),
                resources.get("elixir", 0),
                resources.get("dark_elixir", 0)
            )
            return resources
        except Exception as e:
            logger.warning(f"Failed to read resources: {e}")
            return {"gold": 0, "elixir": 0, "dark_elixir": 0, "gems": 0}

    def _interruptible_sleep(self, seconds: float):
        """
        Sleep for the given duration, checking for shutdown every 10 seconds.
        
        This allows graceful shutdown during long sleep periods.
        """
        end_time = time.time() + seconds
        check_interval = 10  # Check every 10 seconds

        while time.time() < end_time and not self._shutdown_requested:
            remaining = end_time - time.time()
            sleep_chunk = min(remaining, check_interval)
            if sleep_chunk > 0:
                time.sleep(sleep_chunk)

        if self._shutdown_requested:
            logger.info("Sleep interrupted by shutdown request")

    def _handle_error_recovery(self):
        """Attempt to recover from an error by restarting the target game/emulator."""
        self.state_machine.transition(BotState.ERROR_RECOVERY)
        logger.warning("🔧 Attempting error recovery...")

        try:
            self.backend.close_game()
        except Exception:
            pass
        try:
            self.emulator.close()
        except Exception:
            pass

        # Wait before retry with exponential backoff (15s, 30s, 60s, max 120s)
        recovery_wait = min(120, int(15 * (2 ** max(0, self._consecutive_errors - 1))))
        logger.info(f"Waiting {recovery_wait}s before retry (backoff attempt {self._consecutive_errors})...")
        self._interruptible_sleep(recovery_wait)

    def _try_recover_to_home(self):
        """Try to get back to the home screen after an error."""
        try:
            self.navigator.close_all_dialogs()
            self.navigator.go_home()
        except Exception as e:
            logger.warning(f"Recovery to home failed: {e}")

    def _emergency_shutdown(self):
        """Emergency shutdown — kill everything and exit."""
        logger.critical("🚨 EMERGENCY SHUTDOWN INITIATED")
        try:
            self.backend.close_game()
        except Exception:
            pass

        self.state_machine.transition(BotState.SHUTTING_DOWN)
        self.db.set_state("last_emergency", datetime.now(timezone.utc).isoformat())
        self.db.set_state("emergency_reason",
                          f"Too many consecutive errors: {self._consecutive_errors}")

    def cleanup(self):
        """Clean up resources on shutdown."""
        logger.info("Cleaning up orchestrator resources...")
        try:
            self.backend.disconnect()
        except Exception as e:
            logger.warning(f"Backend disconnect error: {e}")
