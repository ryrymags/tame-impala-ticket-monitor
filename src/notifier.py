"""Discord webhook notification sender."""

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Discord embed color codes
COLOR_GREEN = 0x00FF00    # Test notification / success
COLOR_BLUE = 0x3498DB      # Status change / informational
COLOR_RED = 0xE74C3C       # Error or back to sold out


class DiscordNotifier:
    """Sends formatted notifications to a Discord webhook."""

    def __init__(self, webhook_url: str, username: str = "Ticket Monitor"):
        self.webhook_url = webhook_url
        self.username = username

    def send_status_change(self, event_name: str, event_date: str, event_url: str,
                           old_status: str, new_status: str) -> bool:
        """Notify when an event's status changes. Mentions user only for onsale."""
        embed = {
            "title": f"Status Change: {event_name}",
            "url": event_url,
            "color": COLOR_BLUE,
            "description": f"Event status changed from **{old_status}** to **{new_status}**.",
            "fields": [
                {"name": "Date", "value": event_date, "inline": True},
                {
                    "name": "Action",
                    "value": f"[Check Ticketmaster]({event_url})",
                    "inline": False,
                },
            ],
            "footer": {"text": "Face Value Exchange Monitor"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Only ping for onsale — that's when tickets are available
        mention = "<@206908742770360320>" if new_status == "onsale" else ""
        return self._send(embeds=[embed], content=mention)

    def send_price_range_appeared(self, event_name: str, event_date: str, event_url: str,
                                   price_min: float, price_max: float) -> bool:
        """Notify when price ranges appear on a previously sold-out event (status unchanged)."""
        embed = {
            "title": f"Price Range Appeared: {event_name}",
            "url": event_url,
            "color": COLOR_BLUE,
            "description": (
                f"Price data appeared in the API for this event — tickets may be available.\n"
                f"Price range: **${price_min:.0f} – ${price_max:.0f}**"
            ),
            "fields": [
                {"name": "Date", "value": event_date, "inline": True},
                {
                    "name": "Action",
                    "value": f"[Check Ticketmaster]({event_url})",
                    "inline": False,
                },
            ],
            "footer": {"text": "Face Value Exchange Monitor"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return self._send(embeds=[embed], content="<@206908742770360320>")

    def send_sold_out_again(self, event_name: str, event_date: str, event_url: str) -> bool:
        """Notify when an event goes back to sold out / offsale."""
        embed = {
            "title": f"Back to Sold Out: {event_name}",
            "url": event_url,
            "color": COLOR_RED,
            "description": "Tickets are no longer available. The monitor will keep checking.",
            "fields": [
                {"name": "Date", "value": event_date, "inline": True},
            ],
            "footer": {"text": "Face Value Exchange Monitor"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return self._send(embeds=[embed])

    def send_heartbeat(self, daily_calls: int, uptime_hours: float,
                       last_check: Optional[datetime]) -> bool:
        """Send a daily heartbeat to confirm the monitor is alive."""
        last_check_str = last_check.strftime("%I:%M %p ET") if last_check else "Never"
        daily_reset = "Midnight UTC"

        embed = {
            "title": "Monitor Heartbeat",
            "color": COLOR_BLUE,
            "description": (
                f"API calls used today: **{daily_calls}** / 5,000\n"
                f"Next reset: {daily_reset}\n"
                f"Uptime: **{uptime_hours:.1f} hours**\n"
                f"Last successful check: **{last_check_str}**"
            ),
            "footer": {"text": "Face Value Exchange Monitor"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return self._send(embeds=[embed])

    def send_test(self) -> bool:
        """Send a test notification to verify the webhook works."""
        embed = {
            "title": "Test Notification — Monitor Connected",
            "color": COLOR_GREEN,
            "description": (
                "Your Ticketmaster Face Value Exchange monitor is configured correctly.\n\n"
                "You will receive alerts here when tickets matching your criteria appear."
            ),
            "fields": [
                {"name": "Webhook", "value": "Working", "inline": True},
                {"name": "Status", "value": "Ready", "inline": True},
            ],
            "footer": {"text": "Face Value Exchange Monitor"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return self._send(embeds=[embed])

    def send_daily_recap(self, event_summaries: list[dict], daily_calls: int) -> bool:
        """Send a daily 11PM recap summarizing the day's monitoring activity."""
        lines = []
        for summary in event_summaries:
            name = summary["name"]
            statuses = summary.get("statuses_seen", ["unknown"])
            current_status = statuses[-1] if statuses else "unknown"
            price_ranges_seen = summary.get("price_ranges_seen", False)

            if current_status == "offsale" and not price_ranges_seen:
                lines.append(f"**{name}**: Still offsale. No ticket activity today.")
            elif price_ranges_seen:
                lines.append(f"**{name}**: Price data appeared! Status: **{current_status}**.")
            else:
                lines.append(f"**{name}**: Status: **{current_status}**. No price data today.")

        description = "\n".join(lines)
        description += f"\n\nAPI calls used today: **{daily_calls}** / 5,000"

        embed = {
            "title": "Daily Recap",
            "color": COLOR_BLUE,
            "description": description,
            "footer": {"text": "Face Value Exchange Monitor"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return self._send(embeds=[embed])

    def send_error(self, message: str) -> bool:
        """Send an error notification."""
        embed = {
            "title": "Monitor Error",
            "color": COLOR_RED,
            "description": message,
            "footer": {"text": "Face Value Exchange Monitor"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return self._send(embeds=[embed])

    # ---- Internal ----

    def _send(self, embeds: list[dict], content: str = "") -> bool:
        """Send a webhook payload to Discord."""
        payload = {
            "username": self.username,
            "embeds": embeds,
        }
        if content:
            payload["content"] = content

        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            if resp.status_code == 204:
                logger.debug("Discord notification sent successfully")
                return True
            elif resp.status_code == 429:
                logger.warning("Discord rate limited: %s", resp.text)
                return False
            else:
                logger.error("Discord webhook error %d: %s", resp.status_code, resp.text[:200])
                return False
        except requests.RequestException as e:
            logger.error("Discord webhook request failed: %s", e)
            return False

