"""State persistence — tracks what has been seen and notified."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class MonitorState:
    """Persists monitor state to JSON to survive restarts."""

    def __init__(self, state_file: str = "state.json"):
        self.state_file = state_file
        self._state: dict = {"events": {}}
        self.load()

    def get_last_status(self, event_id: str) -> Optional[str]:
        """Get the last known status code for an event."""
        return self._event(event_id).get("last_status")

    def set_last_status(self, event_id: str, status: str):
        """Update the stored status for an event."""
        self._event(event_id)["last_status"] = status
        self.save()

    def has_status_changed(self, event_id: str, new_status: str) -> bool:
        """True if the new status differs from what we last recorded."""
        old = self.get_last_status(event_id)
        return old is not None and old != new_status

    def is_offer_new(self, event_id: str, offer_id: str) -> bool:
        """True if we haven't notified about this offer yet."""
        ev = self._event(event_id)
        notified = ev.get("notified_offers", {})
        if isinstance(notified, dict):
            return offer_id not in notified
        # Fallback for legacy list format
        return offer_id not in ev.get("notified_offer_ids", [])

    def record_notification(self, event_id: str, offer_ids: list[str]):
        """Mark offers as notified and update the notification timestamp."""
        ev = self._event(event_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        # Store offer IDs with timestamps for TTL-based pruning
        notified = ev.get("notified_offers", {})
        if not isinstance(notified, dict):
            # Migrate from old list format to timestamped dict
            notified = {oid: now_iso for oid in ev.get("notified_offer_ids", [])}
        for oid in offer_ids:
            notified[oid] = now_iso
        ev["notified_offers"] = notified
        # Keep legacy key in sync for backwards compatibility
        ev["notified_offer_ids"] = list(notified.keys())
        ev["last_notification"] = now_iso
        self.save()

    def can_notify(self, event_id: str, cooldown_minutes: int) -> bool:
        """True if enough time has passed since the last notification."""
        ev = self._event(event_id)
        last_notif = ev.get("last_notification")
        if not last_notif:
            return True
        try:
            last_dt = datetime.fromisoformat(last_notif)
            elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
            return elapsed >= cooldown_minutes * 60
        except (ValueError, TypeError):
            return True

    def get_last_check(self, event_id: str) -> Optional[datetime]:
        """Get the timestamp of the last successful check."""
        val = self._event(event_id).get("last_check")
        if val:
            try:
                return datetime.fromisoformat(val)
            except (ValueError, TypeError):
                pass
        return None

    def set_last_check(self, event_id: str):
        """Record a successful check timestamp."""
        self._event(event_id)["last_check"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def get_last_heartbeat_date(self) -> Optional[str]:
        """Get the date of the last heartbeat (YYYY-MM-DD)."""
        return self._state.get("last_heartbeat_date")

    def set_last_heartbeat_date(self, date_str: str):
        """Record the heartbeat date."""
        self._state["last_heartbeat_date"] = date_str
        self.save()

    def prune_old_offers(self, max_age_days: int = 7):
        """Remove notified offer IDs older than max_age_days to prevent unbounded growth."""
        now = datetime.now(timezone.utc)
        pruned_total = 0
        for event_id, ev in self._state.get("events", {}).items():
            notified = ev.get("notified_offers", {})
            if not isinstance(notified, dict):
                continue
            to_remove = []
            for oid, ts in notified.items():
                try:
                    recorded = datetime.fromisoformat(ts)
                    if (now - recorded).days >= max_age_days:
                        to_remove.append(oid)
                except (ValueError, TypeError):
                    to_remove.append(oid)  # Remove entries with bad timestamps
            for oid in to_remove:
                del notified[oid]
            ev["notified_offers"] = notified
            ev["notified_offer_ids"] = list(notified.keys())
            pruned_total += len(to_remove)
        if pruned_total > 0:
            logger.info("Pruned %d old notified offer IDs", pruned_total)
            self.save()

    # ---- Persistence ----

    def load(self):
        """Load state from disk."""
        if not os.path.exists(self.state_file):
            logger.debug("No state file found, starting fresh")
            return

        try:
            with open(self.state_file, "r") as f:
                self._state = json.load(f)
            logger.debug("Loaded state from %s", self.state_file)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not load state file %s: %s — starting fresh", self.state_file, e)
            self._state = {"events": {}}

    def save(self):
        """Atomic save: write to temp file, then rename."""
        try:
            dir_name = os.path.dirname(self.state_file) or "."
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(self._state, f, indent=2)
            os.replace(tmp_path, self.state_file)
        except OSError as e:
            logger.error("Failed to save state: %s", e)

    # ---- Helpers ----

    def _event(self, event_id: str) -> dict:
        """Get or create the state dict for an event."""
        events = self._state.setdefault("events", {})
        if event_id not in events:
            events[event_id] = {}
        return events[event_id]
