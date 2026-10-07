"""Enum-based state machine with per-account tracking and DB persistence.

Every state transition is logged and persisted in the ``bot_state`` table so
the bot can recover its position after a crash.  The machine is intentionally
*permissive* — any transition is allowed — so higher-level code doesn't need
to fight the state graph during error recovery.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.database import Database

logger = logging.getLogger(__name__)


# ======================================================================
# Bot states
# ======================================================================

class BotState(Enum):
    """All possible states the bot can be in.

    Each value is a lowercase, underscore-separated string that is
    stored directly in the database.
    """

    # --- Global / lifecycle ---
    SLEEPING = "sleeping"
    WAKING = "waking"
    LAUNCHING_EMULATOR = "launching_emulator"
    LOADING_GAME = "loading_game"

    # --- Account management ---
    SWITCHING_ACCOUNT = "switching_account"

    # --- Home village loop ---
    CHECKING_BUILDS = "checking_builds"
    COLLECTING = "collecting"
    MANAGING_SUPER_TROOPS = "managing_super_troops"
    TRAINING_ARMY = "training_army"
    FARMING = "farming"
    SEARCHING_TARGET = "searching_target"
    ATTACKING = "attacking"
    STARTING_UPGRADE = "starting_upgrade"
    STARTING_LAB = "starting_lab"
    UPGRADING_HEROES = "upgrading_heroes"
    UPGRADING_PETS = "upgrading_pets"
    UPGRADING_WALLS = "upgrading_walls"
    RECORDING_TIMERS = "recording_timers"

    # --- Builder Base ---
    SWITCHING_TO_BUILDER_BASE = "switching_to_builder_base"
    BB_COLLECTING = "bb_collecting"
    BB_VERSUS_BATTLE = "bb_versus_battle"
    BB_STARTING_UPGRADE = "bb_starting_upgrade"
    BB_STAR_LAB = "bb_star_lab"
    BB_BOOSTING_CLOCK_TOWER = "bb_boosting_clock_tower"
    SWITCHING_TO_HOME = "switching_to_home"

    # --- Shutdown / error ---
    SHUTTING_DOWN = "shutting_down"
    ERROR_RECOVERY = "error_recovery"


# Database keys used for persistence
_STATE_KEY_GLOBAL = "bot_state.current"
_STATE_KEY_ACCOUNT_PREFIX = "bot_state.account."


# ======================================================================
# State Machine
# ======================================================================

class StateMachine:
    """Track and persist the bot's operational state.

    Maintains both a *global* state (which phase the top-level loop is in)
    and *per-account* states so the orchestrator knows where it left off
    if it needs to resume after a crash.

    Parameters
    ----------
    db:
        A :class:`core.database.Database` instance used for persistence.

    Example
    -------
    >>> sm = StateMachine(db)
    >>> sm.transition(BotState.LAUNCHING_EMULATOR)
    >>> sm.current_state
    <BotState.LAUNCHING_EMULATOR: 'launching_emulator'>
    """

    def __init__(self, db: Database) -> None:
        """Initialise the state machine, restoring state from DB if available.

        Args:
            db: Database instance for reading/writing persistent state.
        """
        self._db = db
        self._current: BotState = self._load_state()
        self._account_states: dict[int, BotState] = {}
        logger.info("StateMachine initialised — current state: %s", self._current.name)

    # ------------------------------------------------------------------
    # Global state
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> BotState:
        """Return the current global bot state.

        Returns:
            The active :class:`BotState` enum member.
        """
        return self._current

    def transition(self, new_state: BotState) -> None:
        """Transition the global state and persist to DB.

        Args:
            new_state: The :class:`BotState` to move to.
        """
        old = self._current
        self._current = new_state
        self._persist_state(new_state)
        logger.info("State transition: %s → %s", old.name, new_state.name)

    # ------------------------------------------------------------------
    # Per-account state
    # ------------------------------------------------------------------

    def get_account_state(self, account_id: int) -> BotState:
        """Return the last known state for a specific account.

        Falls back to ``SLEEPING`` if the account has never been tracked.

        Args:
            account_id: The account row ID.

        Returns:
            The account's current :class:`BotState`.
        """
        # Check in-memory cache first
        if account_id in self._account_states:
            return self._account_states[account_id]

        # Fall back to DB
        key = f"{_STATE_KEY_ACCOUNT_PREFIX}{account_id}"
        try:
            stored = self._db.get_state(key)
            if stored is not None:
                state = BotState(stored)
                self._account_states[account_id] = state
                return state
        except (ValueError, KeyError):
            logger.warning(
                "Invalid account state in DB for account %d — resetting to SLEEPING",
                account_id,
            )

        # Default
        self._account_states[account_id] = BotState.SLEEPING
        return BotState.SLEEPING

    def set_account_state(self, account_id: int, state: BotState) -> None:
        """Set and persist the state for a specific account.

        Args:
            account_id: The account row ID.
            state: The :class:`BotState` to assign.
        """
        old = self._account_states.get(account_id, BotState.SLEEPING)
        self._account_states[account_id] = state

        key = f"{_STATE_KEY_ACCOUNT_PREFIX}{account_id}"
        try:
            self._db.set_state(key, state.value)
        except Exception as exc:
            logger.error(
                "Failed to persist account %d state to DB: %s", account_id, exc
            )

        logger.info(
            "Account %d state: %s → %s", account_id, old.name, state.name
        )

    # ------------------------------------------------------------------
    # Convenience queries
    # ------------------------------------------------------------------

    def is_in(self, *states: BotState) -> bool:
        """Check if the global state matches any of the given states.

        Args:
            *states: One or more :class:`BotState` members to check against.

        Returns:
            ``True`` if the current state is in *states*.
        """
        return self._current in states

    def is_error(self) -> bool:
        """Check if the bot is in the error-recovery state.

        Returns:
            ``True`` if the current state is ``ERROR_RECOVERY``.
        """
        return self._current is BotState.ERROR_RECOVERY

    def is_idle(self) -> bool:
        """Check if the bot is sleeping.

        Returns:
            ``True`` if the current state is ``SLEEPING``.
        """
        return self._current is BotState.SLEEPING

    def reset(self) -> None:
        """Reset the global state to ``SLEEPING`` and clear account caches.

        Useful during error recovery or clean shutdown.
        """
        logger.info("StateMachine reset — all states cleared to SLEEPING")
        self._current = BotState.SLEEPING
        self._persist_state(BotState.SLEEPING)
        self._account_states.clear()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> BotState:
        """Load the last persisted global state from the database.

        Returns:
            The restored :class:`BotState`, or ``SLEEPING`` if none is stored.
        """
        try:
            stored = self._db.get_state(_STATE_KEY_GLOBAL)
            if stored is not None:
                state = BotState(stored)
                logger.debug("Restored global state from DB: %s", state.name)
                return state
        except (ValueError, KeyError):
            logger.warning(
                "Invalid global state in DB — defaulting to SLEEPING"
            )
        except Exception as exc:
            logger.error("Failed to load state from DB: %s", exc)

        return BotState.SLEEPING

    def _persist_state(self, state: BotState) -> None:
        """Write the global state to the database.

        Args:
            state: The :class:`BotState` to persist.
        """
        try:
            self._db.set_state(_STATE_KEY_GLOBAL, state.value)
        except Exception as exc:
            logger.error("Failed to persist global state to DB: %s", exc)
