"""Polling scheduler — main monitoring loop with scoring and adaptive intervals."""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from dateutil import tz

from .config import EventConfig, MonitorConfig
from .models import EventStatus, EventStatusCode, Offer, TicketAlert
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

# Scoring weights
SCORE_GA = 100
SCORE_LOGE = 60
SCORE_BALCONY = 30
SCORE_UNDER_100 = 50
SCORE_QTY_4_PLUS = 40
SCORE_QTY_2_PLUS = 20


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
        self._last_successful_check: Optional[datetime] = None
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
        activity = self.state.get_daily_activity()

        event_summaries = []
        for event_cfg in self.config.events:
            ev_activity = activity.get(event_cfg.event_id, {})
            event_summaries.append({
                "name": event_cfg.name,
                "statuses_seen": ev_activity.get("statuses_seen", []),
                "total_offers": ev_activity.get("total_offers", 0),
                "best_score": ev_activity.get("best_score", 0),
                "alerts_sent": ev_activity.get("alerts_sent", 0),
                "filtered_reasons": ev_activity.get("filtered_reasons", []),
            })

        return self.notifier.send_daily_recap(
            event_summaries=event_summaries,
            daily_calls=self.state.get_daily_api_calls(),
        )

    def _persist_api_calls(self):
        """Save new API calls from this run to persistent state."""
        current = self.client.get_daily_call_count()
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
        if old_status and self.state.has_status_changed(event_id, status.status_code.value):
            logger.info("[%s] Status changed: %s -> %s", event_cfg.name, old_status, status.status_code.value)

            if self.config.notify_on_status_change:
                if status.status_code == EventStatusCode.OFFSALE:
                    self.notifier.send_sold_out_again(event_cfg.name, event_cfg.date, event_url)
                else:
                    self.notifier.send_status_change(
                        event_cfg.name, event_cfg.date, event_url,
                        old_status, status.status_code.value,
                    )

        self.state.set_last_status(event_id, status.status_code.value)

        # Tier 2: Commerce API — offers (always run, don't gate on status)
        offers = self.client.get_event_offers(event_id)
        logger.info("[%s] Offers found: %d", event_cfg.name, len(offers))

        # Filter and score offers
        matching = self._filter_and_score(offers)

        if matching:
            # Build alert
            total_score = max(o.priority_score for o in matching)
            reasons = self._build_score_reasons(matching)

            logger.info(
                "[%s] Matching offers: %d, top score: %d",
                event_cfg.name, len(matching), total_score,
            )

            # Track for daily recap
            self.state.record_daily_activity(
                event_id, status.status_code.value, len(matching), total_score,
            )

            # Check score threshold and cooldown
            if total_score >= self.config.score_threshold:
                new_offer_ids = [o.offer_id for o in matching if self.state.is_offer_new(event_id, o.offer_id)]

                if new_offer_ids and self.state.can_notify(event_id, self.config.cooldown_minutes):
                    alert = TicketAlert(
                        event_name=event_cfg.name,
                        event_date=event_cfg.date,
                        event_url=event_url,
                        event_id=event_id,
                        status=status,
                        matching_offers=matching,
                        page_data=None,
                        timestamp=datetime.now(timezone.utc),
                        total_score=total_score,
                        score_reasons=reasons,
                    )

                    if self.notifier.send_ticket_alert(alert):
                        self.state.record_notification(event_id, [o.offer_id for o in matching])
                        self.state.increment_daily_alerts(event_id)
                        logger.info("[%s] Discord alert sent (score %d)", event_cfg.name, total_score)
                    else:
                        logger.error("[%s] Failed to send Discord alert", event_cfg.name)
                elif not new_offer_ids:
                    logger.debug("[%s] No new offers to notify about", event_cfg.name)
                else:
                    logger.debug("[%s] Cooldown active, skipping notification", event_cfg.name)
            else:
                self.state.record_daily_activity(
                    event_id, status.status_code.value, len(matching), total_score,
                    filtered_reason=f"score {int(total_score)} below threshold {self.config.score_threshold}",
                )
                logger.debug("[%s] Score %d below threshold %d", event_cfg.name, total_score, self.config.score_threshold)
        else:
            # Track no-offer status for daily recap
            filtered_reason = None
            if len(offers) > 0 and len(matching) == 0:
                filtered_reason = "above max price"
            self.state.record_daily_activity(
                event_id, status.status_code.value, 0, 0, filtered_reason=filtered_reason,
            )
            logger.debug("[%s] No matching offers", event_cfg.name)

        self.state.set_last_check(event_id)
        self._last_successful_check = datetime.now(timezone.utc)

    def _filter_and_score(self, offers: list[Offer]) -> list[Offer]:
        """Filter offers by max price and score by section/price/quantity preferences."""
        matching = []

        for offer in offers:
            # Hard filter: skip if above max price
            if offer.price_max is not None and offer.price_max > self.config.max_price:
                continue

            # If we have no price info at all, still include it (better to alert than miss)
            score = 0.0
            reasons = []

            # Section scoring
            offer_name_lower = (offer.name or "").lower()
            if "general admission" in offer_name_lower or "ga" == offer_name_lower or "floor" in offer_name_lower:
                score += SCORE_GA
                reasons.append("GA")
            elif "loge" in offer_name_lower:
                score += SCORE_LOGE
                reasons.append("LOGE")
            elif "balcony" in offer_name_lower or "bal" in offer_name_lower:
                score += SCORE_BALCONY
                reasons.append("Balcony")
            else:
                # Unknown section — give minimum score so it still shows up
                score += 10
                reasons.append(offer.name or "Unknown section")

            # Price scoring
            effective_price = offer.price_max or offer.price_min
            if effective_price is not None and effective_price < 100:
                score += SCORE_UNDER_100
                reasons.append("under $100")

            # Quantity scoring
            if offer.limit is not None:
                if offer.limit >= 4:
                    score += SCORE_QTY_4_PLUS
                    reasons.append(f"qty {offer.limit}+")
                elif offer.limit >= 2:
                    score += SCORE_QTY_2_PLUS
                    reasons.append(f"qty {offer.limit}+")

            offer.priority_score = score
            offer.score_reasons = reasons
            matching.append(offer)

        # Sort by score descending
        matching.sort(key=lambda o: o.priority_score, reverse=True)
        return matching

    def _build_score_reasons(self, offers: list[Offer]) -> list[str]:
        """Collect score reasons from the top-scoring offer."""
        if not offers:
            return []
        top = offers[0]
        return top.score_reasons if top.score_reasons else [f"score {int(top.priority_score)}"]

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
            uptime_hours = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600
            if self.notifier.send_heartbeat(
                daily_calls=self.state.get_daily_api_calls(),
                uptime_hours=uptime_hours,
                last_check=self._last_successful_check,
            ):
                self.state.set_last_heartbeat_date(today_str)
                self.state.prune_old_offers()
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
            activity = self.state.get_daily_activity()

            # Build summaries for each monitored event
            event_summaries = []
            for event_cfg in self.config.events:
                ev_activity = activity.get(event_cfg.event_id, {})
                event_summaries.append({
                    "name": event_cfg.name,
                    "statuses_seen": ev_activity.get("statuses_seen", []),
                    "total_offers": ev_activity.get("total_offers", 0),
                    "best_score": ev_activity.get("best_score", 0),
                    "alerts_sent": ev_activity.get("alerts_sent", 0),
                    "filtered_reasons": ev_activity.get("filtered_reasons", []),
                })

            if self.notifier.send_daily_recap(
                event_summaries=event_summaries,
                daily_calls=self.state.get_daily_api_calls(),
            ):
                self.state.set_last_recap_date(today_str)
                self.state.reset_daily_activity()
                logger.info("Daily recap sent")
            else:
                logger.error("Failed to send daily recap — will retry next cycle")
