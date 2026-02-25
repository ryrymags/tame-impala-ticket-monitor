"""Tests for the monitoring scheduler — check cycles, price range detection, and API call persistence."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

from src.config import MonitorConfig, EventConfig
from src.models import EventStatus, EventStatusCode, PageData, PriceRange
from src.scheduler import MonitorScheduler
from src.ticketmaster import NetworkError, RateLimitError, APIError


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


def _setup_state_mocks(scheduler, last_status=None, last_price_key=None):
    """Wire up all the state mocks needed for _check_event."""
    scheduler.state.get_last_status.return_value = last_status
    scheduler.state.get_had_price_ranges.return_value = False
    scheduler.state.get_last_price_key.return_value = last_price_key
    scheduler.state.get_daily_activity.return_value = {}
    scheduler.state.record_daily_activity.return_value = None
    scheduler.state.set_last_check.return_value = None
    scheduler.state.set_last_successful_check.return_value = None
    scheduler.state.set_last_status.return_value = None
    scheduler.state.set_had_price_ranges.return_value = None
    scheduler.state.set_last_price_key.return_value = None
    # Default: notifications succeed
    scheduler.notifier.send_status_change.return_value = True
    scheduler.notifier.send_sold_out_again.return_value = True
    scheduler.notifier.send_price_range_appeared.return_value = True


class TestStatusChangeDetection:
    """Test status change and first-check notification logic."""

    def _make_scheduler_for_check(self, last_status=None):
        config = _make_config(events=[_make_event_cfg()])
        scheduler = _make_scheduler(config)
        scheduler.client.is_budget_exhausted.return_value = False
        scheduler.client.is_budget_warning.return_value = False
        _setup_state_mocks(scheduler, last_status=last_status)
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
    """Test that price range VALUE changes trigger notifications."""

    def _make_scheduler_for_check(self, last_price_key=None):
        config = _make_config(events=[_make_event_cfg()])
        scheduler = _make_scheduler(config)
        scheduler.client.is_budget_exhausted.return_value = False
        scheduler.client.is_budget_warning.return_value = False
        _setup_state_mocks(scheduler, last_status="offsale", last_price_key=last_price_key)
        return scheduler

    def test_price_range_change_triggers_notification(self):
        """When price range values change, notify — catches FVE appearing alongside original prices."""
        original_key = "standard:59.50-209.50"
        scheduler = self._make_scheduler_for_check(last_price_key=original_key)
        # New prices include FVE range
        new_ranges = [
            PriceRange(type="standard", currency="USD", min_price=59.50, max_price=209.50),
            PriceRange(type="standard", currency="USD", min_price=419.60, max_price=419.60),
        ]
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=new_ranges)

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_price_range_appeared.assert_called_once()

    def test_price_range_no_notification_on_first_ever_check(self):
        """If last_price_key is None (never been checked), don't alert — we don't know if this is new."""
        scheduler = self._make_scheduler_for_check(last_price_key=None)
        price_range = PriceRange(type="standard", currency="USD", min_price=50.0, max_price=150.0)
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=[price_range])

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_price_range_appeared.assert_not_called()

    def test_same_prices_no_notification(self):
        """If prices haven't changed, don't re-alert."""
        price_range = PriceRange(type="standard", currency="USD", min_price=50.0, max_price=150.0)
        key = MonitorScheduler._price_range_key([price_range])
        scheduler = self._make_scheduler_for_check(last_price_key=key)
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=[price_range])

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_price_range_appeared.assert_not_called()

    def test_price_range_no_notification_when_no_ranges(self):
        """No alert when there are no price ranges even if key differs."""
        scheduler = self._make_scheduler_for_check(last_price_key="standard:50.00-150.00")
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=[])

        scheduler._check_event(_make_event_cfg())

        scheduler.notifier.send_price_range_appeared.assert_not_called()

    def test_price_key_recorded_each_check(self):
        """set_last_price_key is always called so state stays up to date."""
        scheduler = self._make_scheduler_for_check(last_price_key=None)
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=[])

        scheduler._check_event(_make_event_cfg())

        scheduler.state.set_last_price_key.assert_called_once_with("test-event", "")

    def test_had_price_ranges_recorded_each_check(self):
        """set_had_price_ranges is always called so state stays up to date."""
        scheduler = self._make_scheduler_for_check(last_price_key=None)
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=[])

        scheduler._check_event(_make_event_cfg())

        scheduler.state.set_had_price_ranges.assert_called_once_with("test-event", False)


class TestPriceRangeKey:
    """Test the _price_range_key helper method."""

    def test_empty_ranges(self):
        assert MonitorScheduler._price_range_key([]) == ""

    def test_single_range(self):
        pr = PriceRange(type="standard", currency="USD", min_price=59.50, max_price=209.50)
        assert MonitorScheduler._price_range_key([pr]) == "standard:59.50-209.50"

    def test_multiple_ranges_sorted(self):
        pr1 = PriceRange(type="standard", currency="USD", min_price=59.50, max_price=209.50)
        pr2 = PriceRange(type="resale", currency="USD", min_price=419.60, max_price=419.60)
        key = MonitorScheduler._price_range_key([pr2, pr1])
        # Should be sorted alphabetically
        assert key == "resale:419.60-419.60|standard:59.50-209.50"

    def test_same_ranges_different_order_same_key(self):
        pr1 = PriceRange(type="a", currency="USD", min_price=10.0, max_price=20.0)
        pr2 = PriceRange(type="b", currency="USD", min_price=30.0, max_price=40.0)
        assert MonitorScheduler._price_range_key([pr1, pr2]) == MonitorScheduler._price_range_key([pr2, pr1])


class TestNotificationFailureRetry:
    """Test that failed Discord notifications don't update state (so we retry next cycle)."""

    def _make_scheduler_for_check(self, last_status=None, last_price_key=None):
        config = _make_config(events=[_make_event_cfg()])
        scheduler = _make_scheduler(config)
        scheduler.client.is_budget_exhausted.return_value = False
        scheduler.client.is_budget_warning.return_value = False
        _setup_state_mocks(scheduler, last_status=last_status, last_price_key=last_price_key)
        return scheduler

    def test_failed_status_notification_does_not_update_state(self):
        """If send_status_change fails, set_last_status must NOT be called."""
        scheduler = self._make_scheduler_for_check(last_status="offsale")
        scheduler.client.get_event_status.return_value = _make_event_status(EventStatusCode.ONSALE)
        scheduler.notifier.send_status_change.return_value = False  # Notification fails

        scheduler._check_event(_make_event_cfg())

        # State should NOT be updated — we want to retry next cycle
        scheduler.state.set_last_status.assert_not_called()

    def test_successful_status_notification_updates_state(self):
        """If send_status_change succeeds, set_last_status IS called."""
        scheduler = self._make_scheduler_for_check(last_status="offsale")
        scheduler.client.get_event_status.return_value = _make_event_status(EventStatusCode.ONSALE)
        scheduler.notifier.send_status_change.return_value = True

        scheduler._check_event(_make_event_cfg())

        scheduler.state.set_last_status.assert_called_once_with("test-event", "onsale")

    def test_failed_price_notification_does_not_update_price_key(self):
        """If send_price_range_appeared fails, set_last_price_key must NOT be called."""
        original_key = "standard:59.50-209.50"
        scheduler = self._make_scheduler_for_check(last_status="offsale", last_price_key=original_key)
        new_ranges = [
            PriceRange(type="standard", currency="USD", min_price=59.50, max_price=209.50),
            PriceRange(type="standard", currency="USD", min_price=419.60, max_price=419.60),
        ]
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=new_ranges)
        scheduler.notifier.send_price_range_appeared.return_value = False  # Notification fails

        scheduler._check_event(_make_event_cfg())

        # Price key should NOT be updated — we want to retry next cycle
        scheduler.state.set_last_price_key.assert_not_called()

    def test_successful_price_notification_updates_price_key(self):
        """If send_price_range_appeared succeeds, set_last_price_key IS called."""
        original_key = "standard:59.50-209.50"
        scheduler = self._make_scheduler_for_check(last_status="offsale", last_price_key=original_key)
        new_ranges = [
            PriceRange(type="standard", currency="USD", min_price=59.50, max_price=209.50),
            PriceRange(type="standard", currency="USD", min_price=419.60, max_price=419.60),
        ]
        scheduler.client.get_event_status.return_value = _make_event_status(price_ranges=new_ranges)
        scheduler.notifier.send_price_range_appeared.return_value = True

        scheduler._check_event(_make_event_cfg())

        scheduler.state.set_last_price_key.assert_called_once()


class TestErrorRecovery:
    """Test main loop error handling and Discord alerts for unexpected errors."""

    def _make_looping_scheduler(self):
        """Scheduler wired to run one iteration then stop."""
        config = _make_config(events=[_make_event_cfg()])
        scheduler = _make_scheduler(config)
        scheduler.client.is_budget_exhausted.return_value = False
        scheduler.client.is_budget_warning.return_value = False
        scheduler.client.get_daily_call_count.return_value = 0
        scheduler.state.get_last_heartbeat_date.return_value = "2099-01-01"
        scheduler.state.get_last_recap_date.return_value = "2099-01-01"
        return scheduler

    def test_unexpected_exception_sends_discord_alert(self):
        """When an unexpected exception escapes the cycle, send_error is called."""
        scheduler = self._make_looping_scheduler()
        scheduler.client.get_event_status.side_effect = [RuntimeError("boom"), None]

        # Stop after first error so we don't loop forever
        call_count = 0
        original_sleep = scheduler._interruptible_sleep
        def stop_after_first(seconds):
            scheduler.stop()
        scheduler._interruptible_sleep = stop_after_first

        scheduler.run()

        scheduler.notifier.send_error.assert_called_once()
        error_msg = scheduler.notifier.send_error.call_args[0][0]
        assert "RuntimeError" in error_msg
        assert "boom" in error_msg

    def test_network_recovery_sends_discord_alert(self):
        """When network recovers after being down, send_error is called with recovery message."""
        scheduler = self._make_looping_scheduler()
        # First call raises NetworkError (sets _network_down), second succeeds
        scheduler.client.get_event_status.side_effect = [
            NetworkError("connection refused"),
            _make_event_status(),
        ]
        scheduler.state.get_last_status.return_value = "offsale"
        scheduler.state.get_had_price_ranges.return_value = None
        scheduler.state.get_last_price_key.return_value = None

        call_count = 0
        def stop_after_two(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                scheduler.stop()
        scheduler._interruptible_sleep = stop_after_two

        scheduler.run()

        # send_error should have been called for the network recovery
        recovery_calls = [
            c for c in scheduler.notifier.send_error.call_args_list
            if "recovered" in c[0][0].lower()
        ]
        assert len(recovery_calls) == 1

    def test_budget_exhausted_skips_cycle(self):
        """When the API budget is exhausted, no event checks are made."""
        config = _make_config(events=[_make_event_cfg()])
        scheduler = _make_scheduler(config)
        scheduler.client.is_budget_exhausted.return_value = True
        scheduler.state.get_last_heartbeat_date.return_value = "2099-01-01"
        scheduler.state.get_last_recap_date.return_value = "2099-01-01"

        scheduler._run_cycle()

        scheduler.client.get_event_status.assert_not_called()


class TestPageCheckerResetLogic:
    """Verify that a None page_data (blocked request) does not reset the resale flag."""

    def _make_scheduler_for_page_check(self):
        config = _make_config(events=[_make_event_cfg()], enable_page_check=True,
                              page_check_interval_multiplier=1)
        scheduler = _make_scheduler(config)
        scheduler.client.is_budget_exhausted.return_value = False
        scheduler.client.is_budget_warning.return_value = False
        _setup_state_mocks(scheduler, last_status="offsale", last_price_key=None)
        scheduler.state.get_had_price_ranges.return_value = None
        scheduler.state.get_had_page_resale.return_value = True
        return scheduler

    def test_none_page_data_does_not_reset_resale_flag(self):
        """A blocked/errored page fetch (None) must not clear had_page_resale."""
        scheduler = self._make_scheduler_for_page_check()
        scheduler.client.get_event_status.return_value = _make_event_status()
        scheduler._page_checker = MagicMock()
        scheduler._page_checker.check_page.return_value = None  # blocked

        scheduler._check_event(_make_event_cfg())

        # set_had_page_resale must NOT have been called with False
        for call_args in scheduler.state.set_had_page_resale.call_args_list:
            assert call_args[0][1] is not False, \
                "had_page_resale was reset to False on a blocked (None) page response"

    def test_false_resale_detected_does_reset_flag(self):
        """A successful page fetch with resale_detected=False should reset the flag."""
        scheduler = self._make_scheduler_for_page_check()
        scheduler.client.get_event_status.return_value = _make_event_status()
        scheduler._page_checker = MagicMock()
        scheduler._page_checker.check_page.return_value = PageData(
            sections_available=[], price_info=None, resale_detected=False, raw_snippet=""
        )

        scheduler._check_event(_make_event_cfg())

        scheduler.state.set_had_page_resale.assert_called_with("test-event", False)
