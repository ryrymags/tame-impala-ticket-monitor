"""Tests for MonitorState — persistence, status tracking, and price ranges."""

import json
import os
from datetime import datetime, timezone

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


class TestPersistence:
    def test_save_and_load(self, state_file):
        state = MonitorState(state_file=state_file)
        state.set_last_status("event-1", "onsale")
        state.set_had_price_ranges("event-1", True)

        # Create new state from same file
        state2 = MonitorState(state_file=state_file)
        assert state2.get_last_status("event-1") == "onsale"
        assert state2.get_had_price_ranges("event-1") is True

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


class TestPriceRangeTracking:
    def test_initial_had_price_ranges_is_none(self, state):
        assert state.get_had_price_ranges("event-1") is None

    def test_set_true_and_get(self, state):
        state.set_had_price_ranges("event-1", True)
        assert state.get_had_price_ranges("event-1") is True

    def test_set_false_and_get(self, state):
        state.set_had_price_ranges("event-1", False)
        assert state.get_had_price_ranges("event-1") is False

    def test_persists_across_instances(self, state_file):
        state1 = MonitorState(state_file=state_file)
        state1.set_had_price_ranges("event-1", True)
        state2 = MonitorState(state_file=state_file)
        assert state2.get_had_price_ranges("event-1") is True

    def test_independent_per_event(self, state):
        state.set_had_price_ranges("event-1", True)
        state.set_had_price_ranges("event-2", False)
        assert state.get_had_price_ranges("event-1") is True
        assert state.get_had_price_ranges("event-2") is False


class TestHeartbeat:
    def test_initial_heartbeat_is_none(self, state):
        assert state.get_last_heartbeat_date() is None

    def test_set_and_get_heartbeat(self, state):
        state.set_last_heartbeat_date("2026-02-21")
        assert state.get_last_heartbeat_date() == "2026-02-21"


class TestDailyApiCalls:
    def test_initial_api_calls_is_zero(self, state):
        assert state.get_daily_api_calls() == 0

    def test_add_and_get_api_calls(self, state):
        state.add_daily_api_calls(4)
        assert state.get_daily_api_calls() == 4

    def test_accumulates_across_adds(self, state):
        state.add_daily_api_calls(4)
        state.add_daily_api_calls(4)
        assert state.get_daily_api_calls() == 8

    def test_persists_across_instances(self, state_file):
        state1 = MonitorState(state_file=state_file)
        state1.add_daily_api_calls(10)

        state2 = MonitorState(state_file=state_file)
        assert state2.get_daily_api_calls() == 10

    def test_resets_on_new_day(self, state):
        state.add_daily_api_calls(100)
        # Simulate yesterday's date
        state._state["daily_api_date"] = "2020-01-01"
        assert state.get_daily_api_calls() == 0
