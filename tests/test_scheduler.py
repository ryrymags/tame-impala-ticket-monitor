"""Tests for the monitoring scheduler — check cycles, price range detection, and API call persistence."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call

from src.config import MonitorConfig, EventConfig
from src.models import EventStatus, EventStatusCode, PriceRange
from src.scheduler import MonitorScheduler


def _make_config(**overrides) -> MonitorConfig:
    defaults = dict(
        api_key="test", discord_webhook_url="http://test", discord_username="Test",
        discord_ping_user_id="",
        events=[],
        daytime_interval_seconds=30, overnight_interval_seconds=300,
        daytime_start_hour=8, daytime_end_hour=1, backoff_multiplier=1.5,
        max_backoff_seconds=600, timezone="US/Eastern",
        notify_on_status_change=True, daily_heartbeat_hour=9, daily_recap_hour=23,
        enable_page_check=False, page_check_interval_multiplier=5,
        log_level="INFO", log_file="logs/test.log", log_max_file_size_mb=10,
        log_backup_count=3,
    )
    defaults.update(overrides)
    return MonitorConfig(**defaults)


def _make_scheduler(config=None) -> MonitorScheduler:
    config = config or _make_config()
    return MonitorScheduler(
        config=config,
        client=MagicMock(),
        notifier=MagicMock(),
        state=MagicMock(),
        start_time=datetime.now(timezone.utc),
    )


class TestPersistApiCalls:
    def test_persist_adds_delta_to_state(self):
        scheduler = _make_scheduler()
        scheduler.client.get_daily_call_count.return_value = 4
        scheduler._persist_api_calls()
        scheduler.state.add_daily_api_calls.assert_called_once_with(4)

    def test_persist_tracks_delta_across_cycles(self):
        scheduler = _make_scheduler()
        scheduler.client.get_daily_call_count.return_value = 4
        scheduler._persist_api_calls()

        scheduler.client.get_daily_call_count.return_value = 8
        scheduler._persist_api_calls()
        # Second call should only add the delta (8 - 4 = 4)
        assert scheduler.state.add_daily_api_calls.call_args_list[-1].args == (4,)

    def test_persist_skips_zero_delta(self):
        scheduler = _make_scheduler()
        scheduler.client.get_daily_call_count.return_value = 0
        scheduler._persist_api_calls()
        scheduler.state.add_daily_api_calls.assert_not_called()

    def test_send_recap_uses_state_api_calls(self):
        config = _make_config(events=[
            EventConfig(event_id="e1", name="Night 1", date="2026-09-01",
                        url="http://test"),
        ])
        scheduler = _make_scheduler(config)
        scheduler.state.get_daily_activity.return_value = {}
        scheduler.state.get_daily_api_calls.return_value = 120
        scheduler.notifier.send_daily_recap.return_value = True

        scheduler.send_recap()
        # Verify it used the state count (120), not the client count
        scheduler.notifier.send_daily_recap.assert_called_once()
        _, kwargs = scheduler.notifier.send_daily_recap.call_args
        assert kwargs["daily_calls"] == 120


def _make_event_status(status_code=EventStatusCode.OFFSALE, price_ranges=None) -> EventStatus:
    return EventStatus(
        event_id="test-event",
        status_code=status_code,
        price_ranges=price_ranges or [],
        event_url="https://ticketmaster.com/event/test",
        raw_response={},
    )


def _make_event_cfg() -> EventConfig:
    return EventConfig(
        event_id="test-event",
        name="Test Event",
        date="2026-07-28",
        url="https://ticketmaster.com/event/test",
    )


class TestStatusChangeDetection:
    """Test status change and first-check notification logic."""

    def _make_scheduler_for_check(self, last_status=None):
        config = _make_config(events=[_make_event_cfg()])
        scheduler = _make_scheduler(config)
        scheduler.client.is_budget_exhausted.return_value = False
        scheduler.client.is_budget_warning.return_value = False
        scheduler.state.get_last_status.return_value = last_status
        scheduler.state.get_had_price_ranges.return_value = False
        scheduler.state.get_daily_activity.return_value = {}
        scheduler.state.record_daily_activity.return_value = None
        scheduler.state.set_last_check.return_value = None
        scheduler.state.set_last_successful_check.return_value = None
        scheduler.state.set_last_status.return_value = None
        scheduler.state.set_had_price_ranges.return_value = None
        return scheduler

    def test_status_change_triggers_notification(self):
        """Normal case: offsale → onsale sends a status change notification."""
        scheduler = self._make_scheduler_for_check(last_status="offsale")
        scheduler.client.get_event_status.return_value = _make_event_status(EventStatusCode.ONSALE)

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_status_change.assert_called_once()

    def test_first_check_onsale_triggers_notification(self):
        """If old_status is None and the event is onsale, notify — covers restarts with fresh state."""
        scheduler = self._make_scheduler_for_check(last_status=None)
        scheduler.client.get_event_status.return_value = _make_event_status(EventStatusCode.ONSALE)

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_status_change.assert_called_once()

    def test_first_check_offsale_no_notification(self):
        """If old_status is None and the event is offsale, don't alert — nothing actionable."""
        scheduler = self._make_scheduler_for_check(last_status=None)
        scheduler.client.get_event_status.return_value = _make_event_status(EventStatusCode.OFFSALE)

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_status_change.assert_not_called()
        scheduler.notifier.send_sold_out_again.assert_not_called()

    def test_no_change_no_notification(self):
        """If status hasn't changed, no notification is sent."""
        scheduler = self._make_scheduler_for_check(last_status="offsale")
        scheduler.client.get_event_status.return_value = _make_event_status(EventStatusCode.OFFSALE)

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_status_change.assert_not_called()
        scheduler.notifier.send_sold_out_again.assert_not_called()


class TestPriceRangeDetection:
    """Test that price range appearances trigger notifications independent of status changes."""

    def _make_scheduler_for_check(self):
        config = _make_config(events=[_make_event_cfg()])
        scheduler = _make_scheduler(config)
        scheduler.client.is_budget_exhausted.return_value = False
        scheduler.client.is_budget_warning.return_value = False
        scheduler.state.get_last_status.return_value = "offsale"
        scheduler.state.has_status_changed.return_value = False
        scheduler.state.get_daily_activity.return_value = {}
        scheduler.state.record_daily_activity.return_value = None
        scheduler.state.set_last_check.return_value = None
        scheduler.state.set_last_successful_check.return_value = None
        scheduler.state.set_last_status.return_value = None
        scheduler.state.set_had_price_ranges.return_value = None
        return scheduler

    def test_price_range_appeared_triggers_notification(self):
        scheduler = self._make_scheduler_for_check()
        # First time we see price ranges (had_ranges was False = previously had none)
        scheduler.state.get_had_price_ranges.return_value = False
        price_range = PriceRange(type="standard", currency="USD", min_price=50.0, max_price=150.0)
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=[price_range])

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_price_range_appeared.assert_called_once()

    def test_price_range_no_notification_on_first_ever_check(self):
        """If had_ranges is None (never been checked), don't alert — we don't know if this is new."""
        scheduler = self._make_scheduler_for_check()
        scheduler.state.get_had_price_ranges.return_value = None
        price_range = PriceRange(type="standard", currency="USD", min_price=50.0, max_price=150.0)
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=[price_range])

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_price_range_appeared.assert_not_called()

    def test_price_range_no_notification_when_already_had_ranges(self):
        """If price ranges were already present last check, don't re-alert."""
        scheduler = self._make_scheduler_for_check()
        scheduler.state.get_had_price_ranges.return_value = True
        price_range = PriceRange(type="standard", currency="USD", min_price=50.0, max_price=150.0)
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=[price_range])

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_price_range_appeared.assert_not_called()

    def test_price_range_no_notification_when_no_ranges(self):
        """No alert when there are no price ranges."""
        scheduler = self._make_scheduler_for_check()
        scheduler.state.get_had_price_ranges.return_value = False
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=[])

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_price_range_appeared.assert_not_called()

    def test_had_price_ranges_recorded_each_check(self):
        """set_had_price_ranges is always called so state stays up to date."""
        scheduler = self._make_scheduler_for_check()
        scheduler.state.get_had_price_ranges.return_value = None
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=[])

        scheduler._check_event(_make_event_cfg())

        scheduler.state.set_had_price_ranges.assert_called_once_with("test-event", False)
