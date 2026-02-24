"""Polling scheduler — main monitoring loop with adaptive intervals."""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from dateutil import tz

from .config import EventConfig, MonitorConfig
from .models import EventStatusCode
from .notifier import DiscordNotifier
from .state import MonitorState
from .ticketmaster import (
    APIError,
    EventNotFoundError,
    NetworkError,
    RateLimitError,
    TicketmasterClient,
)

logger = logging.getLogger(__name__)


class MonitorScheduler:
    """Orchestrates the monitoring loop."""

    def __init__(
        self,
        config: MonitorConfig,
        client: TicketmasterClient,
        notifier: DiscordNotifier,
        state: MonitorState,
        start_time: datetime,
    ):
        self.config = config
        self.client = client
        self.notifier = notifier
        self.state = state
        self.start_time = start_time

        self._running = True
        self._consecutive_errors = 0
        self._current_backoff = 0.0
        self._network_down = False
        self._last_successful_check: Optional[datetime] = state.get_last_successful_check()
        self._last_persisted_call_count: int = 0

    def stop(self):
        """Signal the loop to stop."""
        self._running = False

    def run(self):
        """Main loop — runs until stop() is called or interrupted."""
        logger.info("Monitor started. Checking %d event(s).", len(self.config.events))

        while self._running:
            try:
                self._maybe_send_heartbeat()
                self._maybe_send_recap()
                self._run_cycle()
                self._persist_api_calls()
                self._consecutive_errors = 0
                self._current_backoff = 0.0

                if self._network_down:
                    logger.info("Network recovered")
                    self._network_down = False

            except NetworkError as e:
                if not self._network_down:
                    logger.warning("Network lost: %s", e)
                    self._network_down = True
                # Don't count network errors toward backoff aggressively
                self._consecutive_errors += 1
                self._current_backoff = min(
                    self._current_backoff + 30,
                    self.config.max_backoff_seconds,
                )

            except RateLimitError as e:
                logger.warning("Rate limited: %s", e)
                self._current_backoff = min(e.retry_after, self.config.max_backoff_seconds)
                self._consecutive_errors += 1

            except EventNotFoundError as e:
                logger.warning("Event not found (skipping): %s", e)
                # Don't backoff for 404s, just skip that event this cycle

            except APIError as e:
                logger.error("API error: %s", e)
                self._consecutive_errors += 1
                self._current_backoff = min(
                    self.config.daytime_interval_seconds * (self.config.backoff_multiplier ** self._consecutive_errors),
                    self.config.max_backoff_seconds,
                )

            except Exception as e:
                logger.exception("Unexpected error: %s", e)
                self._consecutive_errors += 1
                self._current_backoff = min(60 * self._consecutive_errors, self.config.max_backoff_seconds)

            if not self._running:
                break

            # If network just recovered, run an immediate check
            if self._network_down is False and self._consecutive_errors == 0 and self._current_backoff == 0:
                sleep_time = self._get_interval()
            elif self._current_backoff > 0:
                sleep_time = self._current_backoff
            else:
                sleep_time = self._get_interval()

            logger.debug("Next check in %.0f seconds", sleep_time)
            self._interruptible_sleep(sleep_time)

    def run_once(self):
        """Run a single check cycle and return (for --once mode)."""
        self._maybe_send_heartbeat()
        self._maybe_send_recap()
        self._run_cycle()
        self._persist_api_calls()

    def send_recap(self) -> bool:
        """Send the daily recap immediately, ignoring the hour check."""
        event_summaries = self._build_recap_summaries()
        return self.notifier.send_daily_recap(
            event_summaries=event_summaries,
            daily_calls=self.state.get_daily_api_calls(),
        )

    def _persist_api_calls(self):
        """Save new API calls from this run to persistent state."""
        current = self.client.get_daily_call_count()
        if current < self._last_persisted_call_count:
            # Client counter was reset at midnight UTC — re-sync our baseline
            self._last_persisted_call_count = 0
        delta = current - self._last_persisted_call_count
        if delta > 0:
            self.state.add_daily_api_calls(delta)
            self._last_persisted_call_count = current

    # ---- Core logic ----

    def _run_cycle(self):
        """Check all events once."""
        if self.client.is_budget_exhausted():
            logger.warning("Daily API budget exhausted (%d calls). Skipping cycle.",
                           self.client.get_daily_call_count())
            return

        if self.client.is_budget_warning():
            logger.warning("API budget warning: %d / 5,000 calls used today",
                           self.client.get_daily_call_count())

        for event_cfg in self.config.events:
            if not self._running:
                break
            try:
                self._check_event(event_cfg)
            except EventNotFoundError as e:
                logger.warning("Skipping event %s: %s", event_cfg.event_id, e)
            # Let other exceptions bubble up to the main loop

    def _check_event(self, event_cfg: EventConfig):
        """Full check cycle for one event."""
        event_id = event_cfg.event_id

        # Tier 1: Discovery API — status and price ranges
        status = self.client.get_event_status(event_id)
        logger.info(
            "[%s] Status: %s | Price ranges: %d",
            event_cfg.name,
            status.status_code.value,
            len(status.price_ranges),
        )

        # Use the URL from the Discovery API if available, otherwise fall back to config
        event_url = status.event_url or event_cfg.url

        # Detect status changes
        old_status = self.state.get_last_status(event_id)
        new_status_value = status.status_code.value

        # Fire when status has changed from a known state, OR when we first see the
        # event as onsale (covers restarts/fresh state where old_status is None).
        status_changed = old_status is not None and old_status != new_status_value
        first_seen_onsale = old_status is None and status.status_code == EventStatusCode.ONSALE

        if status_changed or first_seen_onsale:
            if status_changed:
                logger.info("[%s] Status changed: %s -> %s", event_cfg.name, old_status, new_status_value)
            else:
                logger.info("[%s] First check: event is already %s — notifying", event_cfg.name, new_status_value)

            if self.config.notify_on_status_change:
                if status.status_code == EventStatusCode.OFFSALE:
                    self.notifier.send_sold_out_again(event_cfg.name, event_cfg.date, event_url)
                else:
                    self.notifier.send_status_change(
                        event_cfg.name, event_cfg.date, event_url,
                        old_status or "unknown", new_status_value,
                    )

        self.state.set_last_status(event_id, status.status_code.value)

        # Detect price range appearances — catches cases where Ticketmaster doesn't
        # flip the status code but FVE listings show up with price data
        has_ranges = len(status.price_ranges) > 0
        had_ranges = self.state.get_had_price_ranges(event_id)
        if had_ranges is False and has_ranges:
            # Price ranges appeared where there were none before
            mins = [p.min_price for p in status.price_ranges if p.min_price is not None]
            maxs = [p.max_price for p in status.price_ranges if p.max_price is not None]
            if mins and maxs:
                logger.info("[%s] Price ranges appeared: $%.0f – $%.0f", event_cfg.name, min(mins), max(maxs))
                self.notifier.send_price_range_appeared(
                    event_cfg.name, event_cfg.date, event_url, min(mins), max(maxs),
                )
        self.state.set_had_price_ranges(event_id, has_ranges)

        # Track for daily recap
        self.state.record_daily_activity(event_id, status.status_code.value, has_ranges)

        self.state.set_last_check(event_id)
        self._last_successful_check = datetime.now(timezone.utc)
        self.state.set_last_successful_check()

    def _build_recap_summaries(self) -> list[dict]:
        """Build event summaries for the daily recap."""
        activity = self.state.get_daily_activity()
        summaries = []
        for event_cfg in self.config.events:
            ev_activity = activity.get(event_cfg.event_id, {})
            summaries.append({
                "name": event_cfg.name,
                "statuses_seen": ev_activity.get("statuses_seen", []),
                "price_ranges_seen": ev_activity.get("price_ranges_seen", False),
            })
        return summaries

    # ---- Scheduling helpers ----

    def _get_interval(self) -> float:
        """Return the polling interval based on time of day."""
        venue_tz = tz.gettz(self.config.timezone)
        now = datetime.now(venue_tz)
        hour = now.hour

        # Daytime: daytime_start_hour to daytime_end_hour
        # If end < start (e.g., 8 to 1), daytime wraps past midnight
        start = self.config.daytime_start_hour
        end = self.config.daytime_end_hour

        if start < end:
            is_daytime = start <= hour < end
        else:
            # Wraps past midnight: daytime is start..23 and 0..end
            is_daytime = hour >= start or hour < end

        if is_daytime:
            return float(self.config.daytime_interval_seconds)
        else:
            return float(self.config.overnight_interval_seconds)

    def _interruptible_sleep(self, seconds: float):
        """Sleep in small increments so we can respond to stop()."""
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            remaining = end - time.monotonic()
            time.sleep(min(remaining, 1.0))

    def _maybe_send_heartbeat(self):
        """Send a daily heartbeat if it hasn't been sent today."""
        venue_tz = tz.gettz(self.config.timezone)
        now = datetime.now(venue_tz)
        today_str = now.strftime("%Y-%m-%d")

        if self.state.get_last_heartbeat_date() == today_str:
            return  # Already sent today

        if now.hour == self.config.daily_heartbeat_hour:
            monitor_started = self.state.get_monitor_start_time() or self.start_time
            uptime_hours = (datetime.now(timezone.utc) - monitor_started).total_seconds() / 3600
            if self.notifier.send_heartbeat(
                daily_calls=self.state.get_daily_api_calls(),
                uptime_hours=uptime_hours,
                last_check=self._last_successful_check,
            ):
                self.state.set_last_heartbeat_date(today_str)
                logger.info("Daily heartbeat sent")
            else:
                logger.error("Failed to send daily heartbeat — will retry next cycle")

    def _maybe_send_recap(self):
        """Send a daily recap at the configured hour (default 11PM)."""
        venue_tz = tz.gettz(self.config.timezone)
        now = datetime.now(venue_tz)
        today_str = now.strftime("%Y-%m-%d")

        if self.state.get_last_recap_date() == today_str:
            return  # Already sent today

        if now.hour == self.config.daily_recap_hour:
            event_summaries = self._build_recap_summaries()

            if self.notifier.send_daily_recap(
                event_summaries=event_summaries,
                daily_calls=self.state.get_daily_api_calls(),
            ):
                self.state.set_last_recap_date(today_str)
                self.state.reset_daily_activity()
                logger.info("Daily recap sent")
            else:
                logger.error("Failed to send daily recap — will retry next cycle")
