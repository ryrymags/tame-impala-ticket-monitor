"""Tests for Discord notifier — embed colors and notification methods."""

from unittest.mock import MagicMock, patch

from src.notifier import (
    DiscordNotifier,
    COLOR_GREEN,
    COLOR_BLUE,
    COLOR_RED,
)


class TestNotificationColors:
    """Verify that each notification type uses the correct embed color."""

    def test_status_change_uses_blue(self):
        notifier = DiscordNotifier(webhook_url="https://test")
        with patch.object(notifier, "_send", return_value=True) as mock_send:
            notifier.send_status_change("Test", "2026-07-28", "http://test", "offsale", "onsale")
            embed = mock_send.call_args[1]["embeds"][0]
            assert embed["color"] == COLOR_BLUE

    def test_price_range_appeared_uses_blue(self):
        notifier = DiscordNotifier(webhook_url="https://test")
        with patch.object(notifier, "_send", return_value=True) as mock_send:
            notifier.send_price_range_appeared("Test", "2026-07-28", "http://test", 50.0, 150.0)
            embed = mock_send.call_args[1]["embeds"][0]
            assert embed["color"] == COLOR_BLUE

    def test_sold_out_again_uses_red(self):
        notifier = DiscordNotifier(webhook_url="https://test")
        with patch.object(notifier, "_send", return_value=True) as mock_send:
            notifier.send_sold_out_again("Test", "2026-07-28", "http://test")
            embed = mock_send.call_args[1]["embeds"][0]
            assert embed["color"] == COLOR_RED

    def test_heartbeat_uses_blue(self):
        notifier = DiscordNotifier(webhook_url="https://test")
        with patch.object(notifier, "_send", return_value=True) as mock_send:
            notifier.send_heartbeat(daily_calls=100, uptime_hours=24.0, last_check=None)
            embed = mock_send.call_args[1]["embeds"][0]
            assert embed["color"] == COLOR_BLUE

    def test_test_notification_uses_green(self):
        notifier = DiscordNotifier(webhook_url="https://test")
        with patch.object(notifier, "_send", return_value=True) as mock_send:
            notifier.send_test()
            embed = mock_send.call_args[1]["embeds"][0]
            assert embed["color"] == COLOR_GREEN

    def test_error_uses_red(self):
        notifier = DiscordNotifier(webhook_url="https://test")
        with patch.object(notifier, "_send", return_value=True) as mock_send:
            notifier.send_error("Something broke")
            embed = mock_send.call_args[1]["embeds"][0]
            assert embed["color"] == COLOR_RED


class TestStatusChangeMention:
    """Verify that onsale status changes include a user mention."""

    def test_onsale_includes_mention(self):
        notifier = DiscordNotifier(webhook_url="https://test")
        with patch.object(notifier, "_send", return_value=True) as mock_send:
            notifier.send_status_change("Test", "2026-07-28", "http://test", "offsale", "onsale")
            content = mock_send.call_args[1].get("content", "")
            assert "<@" in content

    def test_offsale_no_mention(self):
        notifier = DiscordNotifier(webhook_url="https://test")
        with patch.object(notifier, "_send", return_value=True) as mock_send:
            notifier.send_status_change("Test", "2026-07-28", "http://test", "onsale", "offsale")
            content = mock_send.call_args[1].get("content", "")
            assert content == ""
