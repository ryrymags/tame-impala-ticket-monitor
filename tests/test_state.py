"""Tests for MonitorState — persistence, deduplication, cooldowns, and pruning."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from src.state import MonitorState


@pytest.fixture
def state_file(tmp_path):
    return str(tmp_path / "test_state.json")


@pytest.fixture
def state(state_file):
    return MonitorState(state_file=state_file)


class TestStatusTracking:
    def test_initial_status_is_none(self, state):
        assert state.get_last_status("event-1") is None

    def test_set_and_get_status(self, state):
        state.set_last_status("event-1", "onsale")
        assert state.get_last_status("event-1") == "onsale"

    def test_status_changed(self, state):
        state.set_last_status("event-1", "offsale")
        assert state.has_status_changed("event-1", "onsale") is True

    def test_status_unchanged(self, state):
        state.set_last_status("event-1", "onsale")
        assert state.has_status_changed("event-1", "onsale") is False

    def test_status_changed_from_none(self, state):
        """First time seeing an event — not a 'change'."""
        assert state.has_status_changed("event-1", "onsale") is False


class TestOfferDeduplication:
    def test_new_offer_is_new(self, state):
        assert state.is_offer_new("event-1", "offer-abc") is True

    def test_notified_offer_is_not_new(self, state):
        state.record_notification("event-1", ["offer-abc"])
        assert state.is_offer_new("event-1", "offer-abc") is False

    def test_different_offer_is_new(self, state):
        state.record_notification("event-1", ["offer-abc"])
        assert state.is_offer_new("event-1", "offer-xyz") is True

    def test_different_event_is_new(self, state):
        state.record_notification("event-1", ["offer-abc"])
        assert state.is_offer_new("event-2", "offer-abc") is True


class TestCooldown:
    def test_can_notify_when_never_notified(self, state):
        assert state.can_notify("event-1", cooldown_minutes=5) is True

    def test_cannot_notify_during_cooldown(self, state):
        state.record_notification("event-1", ["offer-1"])
        assert state.can_notify("event-1", cooldown_minutes=5) is False

    def test_can_notify_after_cooldown(self, state):
        state.record_notification("event-1", ["offer-1"])
        # Manually backdate the notification timestamp
        ev = state._event("event-1")
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        ev["last_notification"] = past
        assert state.can_notify("event-1", cooldown_minutes=5) is True

    def test_can_notify_with_corrupt_timestamp(self, state):
        ev = state._event("event-1")
        ev["last_notification"] = "not-a-date"
        assert state.can_notify("event-1", cooldown_minutes=5) is True


class TestPersistence:
    def test_save_and_load(self, state_file):
        state = MonitorState(state_file=state_file)
        state.set_last_status("event-1", "onsale")
        state.record_notification("event-1", ["offer-1"])

        # Create new state from same file
        state2 = MonitorState(state_file=state_file)
        assert state2.get_last_status("event-1") == "onsale"
        assert state2.is_offer_new("event-1", "offer-1") is False

    def test_missing_file_starts_fresh(self, tmp_path):
        state = MonitorState(state_file=str(tmp_path / "nonexistent.json"))
        assert state.get_last_status("event-1") is None

    def test_corrupt_file_starts_fresh(self, state_file):
        with open(state_file, "w") as f:
            f.write("not valid json{{{")
        state = MonitorState(state_file=state_file)
        assert state.get_last_status("event-1") is None

    def test_atomic_save_creates_file(self, state_file):
        state = MonitorState(state_file=state_file)
        state.set_last_status("event-1", "test")
        assert os.path.exists(state_file)
        with open(state_file) as f:
            data = json.load(f)
        assert data["events"]["event-1"]["last_status"] == "test"


class TestLastCheck:
    def test_initial_last_check_is_none(self, state):
        assert state.get_last_check("event-1") is None

    def test_set_and_get_last_check(self, state):
        state.set_last_check("event-1")
        result = state.get_last_check("event-1")
        assert result is not None
        assert isinstance(result, datetime)


class TestHeartbeat:
    def test_initial_heartbeat_is_none(self, state):
        assert state.get_last_heartbeat_date() is None

    def test_set_and_get_heartbeat(self, state):
        state.set_last_heartbeat_date("2026-02-21")
        assert state.get_last_heartbeat_date() == "2026-02-21"


class TestPruneOldOffers:
    def test_prune_removes_old_entries(self, state):
        # Record a notification, then backdate it
        state.record_notification("event-1", ["old-offer"])
        ev = state._event("event-1")
        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        ev["notified_offers"]["old-offer"] = old_time

        state.prune_old_offers(max_age_days=7)
        assert state.is_offer_new("event-1", "old-offer") is True

    def test_prune_keeps_recent_entries(self, state):
        state.record_notification("event-1", ["new-offer"])
        state.prune_old_offers(max_age_days=7)
        assert state.is_offer_new("event-1", "new-offer") is False

    def test_prune_removes_bad_timestamps(self, state):
        state.record_notification("event-1", ["bad-offer"])
        ev = state._event("event-1")
        ev["notified_offers"]["bad-offer"] = "not-a-date"

        state.prune_old_offers(max_age_days=7)
        assert state.is_offer_new("event-1", "bad-offer") is True
