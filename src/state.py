"""State persistence — tracks what has been seen and notified."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
logger = logging.getLogger(__name__)


class MonitorState:
    """Persists monitor state to JSON to survive restarts."""

    def __init__(self, state_file: str = "state.json"):
        self.state_file = state_file
        self._state: dict = {"events": {}}
        self.load()

    def get_last_status(self, event_id: str) -> str | None:
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

    def get_had_price_ranges(self, event_id: str) -> bool | None:
        """Return True/False based on whether price ranges were present last check, or None if never checked."""
        val = self._event(event_id).get("had_price_ranges")
        if val is None:
            return None
        return bool(val)

    def set_had_price_ranges(self, event_id: str, had_ranges: bool):
        """Record whether the event had price ranges in the last check."""
        self._event(event_id)["had_price_ranges"] = had_ranges
        self.save()

    def get_last_price_key(self, event_id: str) -> str | None:
        """Get the last recorded price range key (for detecting value changes)."""
        return self._event(event_id).get("last_price_key")

    def set_last_price_key(self, event_id: str, key: str):
        """Record the current price range key."""
        self._event(event_id)["last_price_key"] = key
        self.save()

    def get_last_check(self, event_id: str) -> datetime | None:
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

    def get_last_successful_check(self) -> datetime | None:
        """Get the global last successful check timestamp (across all runs)."""
        val = self._state.get("last_successful_check")
        if val:
            try:
                return datetime.fromisoformat(val)
            except (ValueError, TypeError):
                pass
        return None

    def set_last_successful_check(self):
        """Record a global successful check timestamp."""
        self._state["last_successful_check"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def get_monitor_start_time(self) -> datetime | None:
        """Get the timestamp when monitoring first started."""
        val = self._state.get("monitor_started")
        if val:
            try:
                return datetime.fromisoformat(val)
            except (ValueError, TypeError):
                pass
        return None

    def set_monitor_start_time(self, dt: datetime):
        """Record when monitoring first started (only sets once)."""
        if "monitor_started" not in self._state:
            self._state["monitor_started"] = dt.isoformat()
            self.save()

    def get_last_heartbeat_date(self) -> str | None:
        """Get the date of the last heartbeat (YYYY-MM-DD)."""
        return self._state.get("last_heartbeat_date")

    def set_last_heartbeat_date(self, date_str: str):
        """Record the heartbeat date."""
        self._state["last_heartbeat_date"] = date_str
        self.save()

    def get_last_recap_date(self) -> str | None:
        """Get the date of the last daily recap (YYYY-MM-DD)."""
        return self._state.get("last_recap_date")

    def set_last_recap_date(self, date_str: str):
        """Record the recap date."""
        self._state["last_recap_date"] = date_str
        self.save()

    def get_daily_api_calls(self) -> int:
        """Get the accumulated API call count for today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._state.get("daily_api_date") != today:
            # New day — reset
            self._state["daily_api_calls"] = 0
            self._state["daily_api_date"] = today
        return self._state.get("daily_api_calls", 0)

    def add_daily_api_calls(self, count: int):
        """Add API calls from this run to the persisted daily total."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._state.get("daily_api_date") != today:
            self._state["daily_api_calls"] = 0
            self._state["daily_api_date"] = today
        self._state["daily_api_calls"] = self._state.get("daily_api_calls", 0) + count
        self.save()

    def record_daily_activity(self, event_id: str, status: str, has_price_ranges: bool):
        """Track what happened today for the daily recap."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._state.get("daily_activity_date") != today:
            self._state["daily_activity"] = {}
            self._state["daily_activity_date"] = today
        activity = self._state.setdefault("daily_activity", {})
        ev_activity = activity.setdefault(event_id, {
            "statuses_seen": [],
            "price_ranges_seen": False,
        })
        if status not in ev_activity["statuses_seen"]:
            ev_activity["statuses_seen"].append(status)
        if has_price_ranges:
            ev_activity["price_ranges_seen"] = True
        self.save()

    def get_daily_activity(self) -> dict:
        """Get the daily activity data."""
        return self._state.get("daily_activity", {})

    def reset_daily_activity(self):
        """Reset daily activity tracking for a new day."""
        self._state["daily_activity"] = {}
        self._state["daily_activity_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.save()

    def get_had_page_resale(self, event_id: str) -> bool:
        """True if page checker has already detected and notified about resale for this event."""
        return bool(self._event(event_id).get("had_page_resale", False))

    def set_had_page_resale(self, event_id: str, value: bool):
        """Record whether page-check resale has been detected and notified."""
        self._event(event_id)["had_page_resale"] = value
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
