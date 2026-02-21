"""Discord webhook notification sender."""

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from .models import TicketAlert

logger = logging.getLogger(__name__)

# Discord embed color codes
COLOR_GREEN = 0x00FF00    # Score >= 140: "DROP EVERYTHING"
COLOR_YELLOW = 0xFFFF00   # Score >= 60: "Good option"
COLOR_ORANGE = 0xFF8C00   # Score >= 30: "Something available"
COLOR_BLUE = 0x3498DB      # Status change / informational
COLOR_RED = 0xE74C3C       # Error or back to sold out


class DiscordNotifier:
    """Sends formatted notifications to a Discord webhook."""

    def __init__(self, webhook_url: str, username: str = "Ticket Monitor"):
        self.webhook_url = webhook_url
        self.username = username

    def send_ticket_alert(self, alert: TicketAlert) -> bool:
        """Send a ticket availability alert with scoring info."""
        color = self._color_for_score(alert.total_score)
        urgency = self._urgency_label(alert.total_score)

        # Build offer details
        offer_lines = []
        for offer in alert.matching_offers:
            price_str = ""
            if offer.price_min is not None:
                if offer.price_max and offer.price_max != offer.price_min:
                    price_str = f"${offer.price_min:.2f} - ${offer.price_max:.2f}"
                else:
                    price_str = f"${offer.price_min:.2f}"

            limit_str = f" (up to {offer.limit})" if offer.limit else ""
            offer_lines.append(f"**{offer.name}**: {price_str}{limit_str}")

        offers_text = "\n".join(offer_lines) if offer_lines else "Details unavailable — check Ticketmaster"
        score_text = f"**Score: {int(alert.total_score)}** — {', '.join(alert.score_reasons)}"

        embed = {
            "title": f"{urgency} {alert.event_name}",
            "url": alert.event_url,
            "color": color,
            "fields": [
                {"name": "Date", "value": alert.event_date, "inline": True},
                {"name": "Venue", "value": "TD Garden, Boston, MA", "inline": True},
                {"name": "Status", "value": alert.status.status_code.value.upper(), "inline": True},
                {"name": "Available Offers", "value": offers_text, "inline": False},
                {"name": "Score", "value": score_text, "inline": False},
                {
                    "name": "Buy Now",
                    "value": f"[Open on Ticketmaster]({alert.event_url})",
                    "inline": False,
                },
            ],
            "footer": {"text": "Face Value Exchange Monitor"},
            "timestamp": alert.timestamp.isoformat(),
        }

        return self._send(embeds=[embed], content="<@206908742770360320>")

    def send_status_change(self, event_name: str, event_date: str, event_url: str,
                           old_status: str, new_status: str) -> bool:
        """Notify when an event's status changes."""
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

        return self._send(embeds=[embed], content="<@206908742770360320>")

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

    def _color_for_score(self, score: float) -> int:
        if score >= 140:
            return COLOR_GREEN
        elif score >= 60:
            return COLOR_YELLOW
        elif score >= 30:
            return COLOR_ORANGE
        return COLOR_ORANGE

    def _urgency_label(self, score: float) -> str:
        if score >= 140:
            return "DROP EVERYTHING —"
        elif score >= 60:
            return "Good Option —"
        else:
            return "Available —"
