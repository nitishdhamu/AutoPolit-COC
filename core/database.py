"""SQLite database wrapper for the Clash of Clans bot.

Manages all persistent state: upgrade timers, attack history, resource
snapshots, super-troop status, clock-tower boosts, lab research, hero
upgrades, and a generic key-value bot-state store.

Thread-safety is enabled via ``check_same_thread=False`` and all public
methods use a shared ``threading.Lock``.  Datetime values are stored as
ISO-8601 strings so they are human-readable in the DB file.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string.

    Returns:
        ISO-formatted UTC timestamp string.
    """
    return datetime.now(timezone.utc).isoformat()


def _now_utc() -> datetime:
    """Return current UTC time as a timezone-aware datetime.

    Returns:
        Current UTC datetime.
    """
    return datetime.now(timezone.utc)


class Database:
    """Lightweight SQLite wrapper tailored for the CoC bot.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Parent directories are created
        automatically.

    Example
    -------
    >>> db = Database(Path("data/bot.db"))
    >>> db.record_attack(1, "home", 450000, 500000, 3000, 2)
    >>> db.close()
    """

    # ------------------------------------------------------------------
    # Initialisation & schema
    # ------------------------------------------------------------------

    def __init__(self, db_path: Path | str) -> None:
        """Create / open the database and ensure all tables exist.

        Args:
            db_path: Filesystem path for the SQLite database file.
        """
        self._db_path = Path(db_path).resolve()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        logger.info("Opening database at %s", self._db_path)
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._create_tables()
        logger.info("Database initialised — all tables ready")

    def _create_tables(self) -> None:
        """Create all required tables if they don't already exist."""
        stmts = [
            # ---- accounts ----
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT    NOT NULL,
                th_level            INTEGER NOT NULL DEFAULT 1,
                supercell_id_index  INTEGER NOT NULL,
                last_active         TEXT,
                enabled             INTEGER NOT NULL DEFAULT 1
            )
            """,
            # ---- upgrades ----
            """
            CREATE TABLE IF NOT EXISTS upgrades (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id       INTEGER NOT NULL,
                building_name    TEXT    NOT NULL,
                village          TEXT    NOT NULL CHECK (village IN ('home', 'builder')),
                start_time       TEXT    NOT NULL,
                duration_seconds INTEGER NOT NULL,
                end_time         TEXT    NOT NULL,
                status           TEXT    NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active', 'completed', 'cancelled')),
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
            """,
            # ---- attacks ----
            """
            CREATE TABLE IF NOT EXISTS attacks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id    INTEGER NOT NULL,
                village       TEXT    NOT NULL DEFAULT 'home',
                gold_gained   INTEGER NOT NULL DEFAULT 0,
                elixir_gained INTEGER NOT NULL DEFAULT 0,
                de_gained     INTEGER NOT NULL DEFAULT 0,
                stars         INTEGER NOT NULL DEFAULT 0,
                timestamp     TEXT    NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
            """,
            # ---- resources ----
            """
            CREATE TABLE IF NOT EXISTS resources (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                gold            INTEGER NOT NULL DEFAULT 0,
                elixir          INTEGER NOT NULL DEFAULT 0,
                dark_elixir     INTEGER NOT NULL DEFAULT 0,
                builder_gold    INTEGER NOT NULL DEFAULT 0,
                builder_elixir  INTEGER NOT NULL DEFAULT 0,
                timestamp       TEXT    NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
            """,
            # ---- bot_state (key-value) ----
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT NOT NULL
            )
            """,
            # ---- super_troop_status ----
            """
            CREATE TABLE IF NOT EXISTS super_troop_status (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL,
                troop_name  TEXT    NOT NULL,
                activated_at TEXT   NOT NULL,
                expires_at  TEXT    NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
            """,
            # ---- clock_tower_status ----
            """
            CREATE TABLE IF NOT EXISTS clock_tower_status (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id          INTEGER NOT NULL,
                boosted_at          TEXT    NOT NULL,
                cooldown_expires_at TEXT    NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
            """,
            # ---- lab_research ----
            """
            CREATE TABLE IF NOT EXISTS lab_research (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL,
                village     TEXT    NOT NULL DEFAULT 'home',
                troop_name  TEXT    NOT NULL,
                start_time  TEXT    NOT NULL,
                end_time    TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'completed', 'cancelled')),
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
            """,
            # ---- hero_upgrades ----
            """
            CREATE TABLE IF NOT EXISTS hero_upgrades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL,
                hero_name   TEXT    NOT NULL,
                start_time  TEXT    NOT NULL,
                end_time    TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'completed', 'cancelled')),
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
            """,
        ]

        with self._lock:
            cursor = self._conn.cursor()
            for sql in stmts:
                cursor.execute(sql)
            self._conn.commit()
            logger.debug("All tables created / verified")

    # ------------------------------------------------------------------
    # Upgrade tracking
    # ------------------------------------------------------------------

    def record_upgrade(
        self,
        account_id: int,
        building: str,
        village: str,
        duration_sec: int,
    ) -> int:
        """Insert a new upgrade timer and return its row ID.

        Args:
            account_id: The account performing the upgrade.
            building: Name of the building being upgraded.
            village: ``'home'`` or ``'builder'``.
            duration_sec: Upgrade duration in seconds.

        Returns:
            The newly inserted upgrade row ID.
        """
        now = _now_utc()
        start = now.isoformat()
        end = (now + timedelta(seconds=duration_sec)).isoformat()

        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO upgrades
                    (account_id, building_name, village, start_time, duration_seconds, end_time, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
                """,
                (account_id, building, village, start, duration_sec, end),
            )
            self._conn.commit()
            row_id: int = cursor.lastrowid  # type: ignore[assignment]

        logger.info(
            "Recorded upgrade #%d: %s on account %d (%s) — %ds, ends %s",
            row_id, building, account_id, village, duration_sec, end,
        )
        return row_id

    def get_active_upgrades(
        self,
        account_id: int | None = None,
        village: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return all active upgrades, optionally filtered.

        Args:
            account_id: Filter to a specific account (or ``None`` for all).
            village: Filter to ``'home'`` or ``'builder'`` (or ``None`` for both).

        Returns:
            List of upgrade dicts with all column values.
        """
        query = "SELECT * FROM upgrades WHERE status = 'active'"
        params: list[Any] = []

        if account_id is not None:
            query += " AND account_id = ?"
            params.append(account_id)
        if village is not None:
            query += " AND village = ?"
            params.append(village)

        query += " ORDER BY end_time ASC"

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        result = [dict(row) for row in rows]
        logger.debug(
            "get_active_upgrades(account_id=%s, village=%s) → %d rows",
            account_id, village, len(result),
        )
        return result

    def get_earliest_completion(self) -> tuple[datetime, int, str] | None:
        """Find the upgrade that completes soonest across all accounts.

        Returns:
            ``(end_time_dt, account_id, village)`` or ``None`` if no active
            upgrades exist.
        """
        with self._lock:
            row = self._conn.execute(
                """
                SELECT end_time, account_id, village
                FROM upgrades
                WHERE status = 'active'
                ORDER BY end_time ASC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            logger.debug("No active upgrades — get_earliest_completion() → None")
            return None

        end_time_dt = datetime.fromisoformat(row["end_time"])
        account_id: int = row["account_id"]
        village: str = row["village"]
        logger.debug(
            "Earliest completion: %s for account %d (%s)",
            end_time_dt.isoformat(), account_id, village,
        )
        return end_time_dt, account_id, village

    def mark_upgrade_completed(self, upgrade_id: int) -> None:
        """Mark an upgrade as completed.

        Args:
            upgrade_id: The row ID of the upgrade to mark.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE upgrades SET status = 'completed' WHERE id = ?",
                (upgrade_id,),
            )
            self._conn.commit()
        logger.info("Upgrade #%d marked as completed", upgrade_id)

    # ------------------------------------------------------------------
    # Attack history
    # ------------------------------------------------------------------

    def record_attack(
        self,
        account_id: int,
        village: str,
        gold: int,
        elixir: int,
        de: int,
        stars: int,
    ) -> None:
        """Record the results of an attack.

        Args:
            account_id: Account that performed the attack.
            village: ``'home'`` or ``'builder'``.
            gold: Gold gained.
            elixir: Elixir gained.
            de: Dark Elixir gained.
            stars: Stars earned (0-3).
        """
        ts = _now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO attacks
                    (account_id, village, gold_gained, elixir_gained, de_gained, stars, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (account_id, village, gold, elixir, de, stars, ts),
            )
            self._conn.commit()
        logger.info(
            "Attack recorded: account %d (%s) — gold=%d, elixir=%d, de=%d, stars=%d",
            account_id, village, gold, elixir, de, stars,
        )

    # ------------------------------------------------------------------
    # Resource snapshots
    # ------------------------------------------------------------------

    def record_resources(
        self,
        account_id: int,
        gold: int,
        elixir: int,
        de: int,
        builder_gold: int = 0,
        builder_elixir: int = 0,
    ) -> None:
        """Record a resource snapshot for an account.

        Args:
            account_id: Target account.
            gold: Current gold amount.
            elixir: Current elixir amount.
            de: Current Dark Elixir amount.
            builder_gold: Builder Base gold (0 if home village snapshot).
            builder_elixir: Builder Base elixir (0 if home village snapshot).
        """
        ts = _now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO resources
                    (account_id, gold, elixir, dark_elixir, builder_gold, builder_elixir, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (account_id, gold, elixir, de, builder_gold, builder_elixir, ts),
            )
            self._conn.commit()
        logger.debug(
            "Resources snapshot: account %d — G=%d E=%d DE=%d BG=%d BE=%d",
            account_id, gold, elixir, de, builder_gold, builder_elixir,
        )

    # ------------------------------------------------------------------
    # Bot state (key-value store)
    # ------------------------------------------------------------------

    def get_state(self, key: str) -> str | None:
        """Retrieve a bot-state value by key.

        Args:
            key: The state key to look up.

        Returns:
            The stored string value, or ``None`` if not found.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM bot_state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            logger.debug("get_state('%s') → None", key)
            return None
        logger.debug("get_state('%s') → '%s'", key, row["value"])
        return row["value"]

    def set_state(self, key: str, value: str) -> None:
        """Insert or update a bot-state key-value pair.

        Args:
            key: The state key.
            value: The string value to store.
        """
        ts = _now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO bot_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                               updated_at = excluded.updated_at
                """,
                (key, value, ts),
            )
            self._conn.commit()
        logger.debug("set_state('%s', '%s')", key, value)

    # ------------------------------------------------------------------
    # Super troop tracking
    # ------------------------------------------------------------------

    def record_super_troop(
        self,
        account_id: int,
        troop: str,
        expires_at: datetime,
    ) -> None:
        """Record a Super Troop activation.

        Args:
            account_id: Account that activated the troop.
            troop: Name of the super troop (e.g. ``'super_dragon'``).
            expires_at: UTC datetime when the boost expires.
        """
        ts = _now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO super_troop_status
                    (account_id, troop_name, activated_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (account_id, troop, ts, expires_at.isoformat()),
            )
            self._conn.commit()
        logger.info(
            "Super troop '%s' recorded for account %d — expires %s",
            troop, account_id, expires_at.isoformat(),
        )

    def get_super_troop_status(self, account_id: int) -> dict[str, Any] | None:
        """Get the most recent super troop activation for an account.

        Args:
            account_id: Account to query.

        Returns:
            Dict with troop info, or ``None`` if no record exists.
        """
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM super_troop_status
                WHERE account_id = ?
                ORDER BY activated_at DESC
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()

        if row is None:
            logger.debug("No super troop status for account %d", account_id)
            return None
        result = dict(row)
        logger.debug("Super troop status for account %d: %s", account_id, result)
        return result

    # ------------------------------------------------------------------
    # Clock Tower tracking
    # ------------------------------------------------------------------

    def record_clock_tower_boost(
        self,
        account_id: int,
        cooldown_expires_at: datetime,
    ) -> None:
        """Record a Clock Tower boost activation.

        Args:
            account_id: Account that boosted.
            cooldown_expires_at: UTC datetime when cooldown ends.
        """
        ts = _now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO clock_tower_status
                    (account_id, boosted_at, cooldown_expires_at)
                VALUES (?, ?, ?)
                """,
                (account_id, ts, cooldown_expires_at.isoformat()),
            )
            self._conn.commit()
        logger.info(
            "Clock Tower boost recorded for account %d — cooldown until %s",
            account_id, cooldown_expires_at.isoformat(),
        )

    # ------------------------------------------------------------------
    # Lab research tracking
    # ------------------------------------------------------------------

    def record_lab_research(
        self,
        account_id: int,
        village: str,
        troop: str,
        end_time: datetime,
    ) -> None:
        """Record a Lab or Star Lab research start.

        Args:
            account_id: Account performing research.
            village: ``'home'`` or ``'builder'``.
            troop: Troop or spell being researched.
            end_time: UTC datetime when research completes.
        """
        ts = _now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO lab_research
                    (account_id, village, troop_name, start_time, end_time, status)
                VALUES (?, ?, ?, ?, ?, 'active')
                """,
                (account_id, village, troop, ts, end_time.isoformat()),
            )
            self._conn.commit()
        logger.info(
            "Lab research '%s' recorded for account %d (%s) — ends %s",
            troop, account_id, village, end_time.isoformat(),
        )

    # ------------------------------------------------------------------
    # Hero upgrade tracking
    # ------------------------------------------------------------------

    def record_hero_upgrade(
        self,
        account_id: int,
        hero: str,
        end_time: datetime,
    ) -> None:
        """Record a Hero (or Pet) upgrade start.

        Args:
            account_id: Account performing the upgrade.
            hero: Hero or pet name (e.g. ``'Barbarian King'``).
            end_time: UTC datetime when the upgrade completes.
        """
        ts = _now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO hero_upgrades
                    (account_id, hero_name, start_time, end_time, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (account_id, hero, ts, end_time.isoformat()),
            )
            self._conn.commit()
        logger.info(
            "Hero upgrade '%s' recorded for account %d — ends %s",
            hero, account_id, end_time.isoformat(),
        )

    # ------------------------------------------------------------------
    # Account helpers
    # ------------------------------------------------------------------

    def upsert_account(
        self,
        name: str,
        supercell_id_index: int,
        th_level: int = 1,
        enabled: bool = True,
    ) -> int:
        """Insert or update an account row by name.

        Args:
            name: Account display name.
            supercell_id_index: Position in the Supercell ID list.
            th_level: Town Hall level.
            enabled: Whether the account is active.

        Returns:
            The account row ID.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM accounts WHERE name = ?", (name,)
            ).fetchone()

            if row:
                self._conn.execute(
                    """
                    UPDATE accounts
                    SET th_level = ?, supercell_id_index = ?, enabled = ?
                    WHERE id = ?
                    """,
                    (th_level, supercell_id_index, int(enabled), row["id"]),
                )
                self._conn.commit()
                logger.debug("Updated account '%s' (id=%d)", name, row["id"])
                return row["id"]
            else:
                cursor = self._conn.execute(
                    """
                    INSERT INTO accounts (name, th_level, supercell_id_index, enabled)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, th_level, supercell_id_index, int(enabled)),
                )
                self._conn.commit()
                aid: int = cursor.lastrowid  # type: ignore[assignment]
                logger.info("Created account '%s' (id=%d)", name, aid)
                return aid

    def update_account_active(self, account_id: int) -> None:
        """Touch the ``last_active`` timestamp for an account.

        Args:
            account_id: Account to update.
        """
        ts = _now_iso()
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET last_active = ? WHERE id = ?",
                (ts, account_id),
            )
            self._conn.commit()
        logger.debug("Account %d last_active updated to %s", account_id, ts)

    def get_enabled_accounts(self) -> list[dict[str, Any]]:
        """Return all enabled accounts.

        Returns:
            List of account dicts.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM accounts WHERE enabled = 1 ORDER BY supercell_id_index"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        try:
            with self._lock:
                self._conn.close()
            logger.info("Database connection closed")
        except Exception as exc:
            logger.error("Error closing database: %s", exc)

    def __enter__(self) -> "Database":
        """Support ``with Database(...) as db:`` context manager.

        Returns:
            This Database instance.
        """
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Close the connection on context-manager exit.

        Args:
            exc_type: Exception type (unused).
            exc_val: Exception value (unused).
            exc_tb: Exception traceback (unused).
        """
        self.close()
