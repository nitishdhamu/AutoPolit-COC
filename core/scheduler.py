"""
Scheduler — Timer management and next-wake-time calculation.
=============================================================
Manages upgrade timers, calculates optimal sleep duration,
and tracks when the bot should next wake up.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.database import Database

logger = logging.getLogger(__name__)


class Scheduler:
    """Manages upgrade timers and calculates next wake time across all accounts."""

    def __init__(self, db: Database, config: dict):
        """
        Initialize scheduler with database and config.
        
        Args:
            db: Database instance for reading/writing timers.
            config: Bot configuration dict.
        """
        self.db = db
        self.config = config
        self.wake_before_sec = config.get("timing", {}).get("wake_before_upgrade_sec", 60)
        self.idle_check_min = config.get("timing", {}).get("idle_check_interval_min", 30)
        logger.info(f"Scheduler initialized: wake {self.wake_before_sec}s early, "
                     f"idle check every {self.idle_check_min}m")

    def get_next_wake_time(self) -> tuple[datetime, Optional[int], Optional[str]]:
        """
        Calculate when the bot should next wake up.
        
        Checks all active upgrades across all accounts and both villages.
        Returns the earliest completion time minus the wake_before buffer.
        If no active upgrades, returns now + idle_check_interval.
        
        Returns:
            Tuple of (wake_time, account_id, village) where account_id and 
            village identify which upgrade triggered the wake.
            If no upgrades, account_id and village are None.
        """
        earliest = self.db.get_earliest_completion()

        if earliest is None:
            # No active upgrades — wake up after idle check interval
            wake_time = datetime.now(timezone.utc) + timedelta(minutes=self.idle_check_min)
            logger.info(f"No active upgrades. Next check at {wake_time.strftime('%H:%M:%S')} "
                        f"({self.idle_check_min}m)")
            return (wake_time, None, None)

        end_time, account_id, village = earliest

        # Parse end_time if it's a string
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time)

        # Wake up early by the configured buffer
        wake_time = end_time - timedelta(seconds=self.wake_before_sec)

        # Don't schedule in the past
        if wake_time < datetime.now(timezone.utc):
            wake_time = datetime.now(timezone.utc)

        time_until = wake_time - datetime.now(timezone.utc)
        logger.info(
            f"Next wake: {wake_time.strftime('%H:%M:%S')} "
            f"(in {self._format_duration(time_until)}) — "
            f"Account {account_id}, {village} village upgrade completes at "
            f"{end_time.strftime('%H:%M:%S')}"
        )

        return (wake_time, account_id, village)

    def get_sleep_duration(self) -> float:
        """
        Calculate how many seconds the bot should sleep.
        
        Returns:
            Number of seconds to sleep. Minimum 0.
        """
        wake_time, _, _ = self.get_next_wake_time()
        duration = (wake_time - datetime.now(timezone.utc)).total_seconds()
        return max(0, duration)

    def record_upgrade(self, account_id: int, building: str,
                       village: str, duration_sec: int) -> int:
        """
        Record a new upgrade timer.
        
        Args:
            account_id: Account that started the upgrade.
            building: Name of the building being upgraded.
            village: 'home' or 'builder'.
            duration_sec: Duration in seconds.
            
        Returns:
            Database ID of the recorded upgrade.
        """
        upgrade_id = self.db.record_upgrade(account_id, building, village, duration_sec)
        end_time = datetime.now(timezone.utc) + timedelta(seconds=duration_sec)
        logger.info(
            f"📋 Recorded upgrade: {building} ({village}) for account {account_id} — "
            f"Duration: {self._format_duration(timedelta(seconds=duration_sec))}, "
            f"Completes: {end_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return upgrade_id

    def check_completed_upgrades(self, account_id: Optional[int] = None) -> list[dict]:
        """
        Find upgrades that should have completed by now.
        
        Args:
            account_id: Optionally filter by account. None = all accounts.
            
        Returns:
            List of upgrade dicts that have passed their end_time.
        """
        active = self.db.get_active_upgrades(account_id=account_id)
        now = datetime.now(timezone.utc)
        completed = []

        for upgrade in active:
            end_time = upgrade.get("end_time")
            if isinstance(end_time, str):
                try:
                    end_time = datetime.fromisoformat(end_time)
                except ValueError:
                    logger.warning(f"Skipping corrupt end_time format: {end_time}")
                    continue

            if end_time and end_time <= now:
                completed.append(upgrade)
                self.db.mark_upgrade_completed(upgrade["id"])
                logger.info(
                    f"✅ Upgrade completed: {upgrade.get('building_name', 'unknown')} "
                    f"({upgrade.get('village', 'unknown')}) for account {upgrade.get('account_id')}"
                )

        if completed:
            logger.info(f"Found {len(completed)} completed upgrades")
        else:
            logger.debug("No completed upgrades found")

        return completed

    def get_all_timers(self, account_id: Optional[int] = None,
                       village: Optional[str] = None) -> list[dict]:
        """
        Get all active upgrade timers, optionally filtered.
        
        Args:
            account_id: Filter by account.
            village: Filter by 'home' or 'builder'.
            
        Returns:
            List of active upgrade dicts sorted by end_time.
        """
        upgrades = self.db.get_active_upgrades(account_id=account_id, village=village)

        # Sort by end_time
        def sort_key(u):
            et = u.get("end_time", "")
            if isinstance(et, str):
                try:
                    return datetime.fromisoformat(et)
                except ValueError:
                    return datetime.max.replace(tzinfo=timezone.utc)
            return et or datetime.max.replace(tzinfo=timezone.utc)

        upgrades.sort(key=sort_key)
        return upgrades

    def get_timer_summary(self, account_id: Optional[int] = None) -> str:
        """
        Get a human-readable summary of all active timers.
        
        Returns:
            Formatted string showing all active upgrades and time remaining.
        """
        timers = self.get_all_timers(account_id=account_id)
        if not timers:
            return "No active upgrades"

        now = datetime.now(timezone.utc)
        lines = []
        for t in timers:
            end_time = t.get("end_time", "")
            if isinstance(end_time, str):
                try:
                    end_time = datetime.fromisoformat(end_time)
                except ValueError:
                    end_time = None

            remaining = ""
            if end_time:
                delta = end_time - now
                if delta.total_seconds() > 0:
                    remaining = self._format_duration(delta)
                else:
                    remaining = "DONE"

            building = t.get("building_name", "unknown")
            village = t.get("village", "?")
            acct = t.get("account_id", "?")
            lines.append(f"  [{village}] {building} — {remaining} (account {acct})")

        return f"Active upgrades ({len(timers)}):\n" + "\n".join(lines)

    @staticmethod
    def _format_duration(td: timedelta) -> str:
        """Format a timedelta as a human-readable string like '2h 15m' or '1d 3h'."""
        total_seconds = int(td.total_seconds())
        if total_seconds < 0:
            return "0s"

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if not parts or (days == 0 and hours == 0 and minutes == 0):
            parts.append(f"{seconds}s")

        return " ".join(parts)
