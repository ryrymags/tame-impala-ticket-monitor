"""Tests for Ticketmaster API client — response parsing and error handling."""

import pytest
from unittest.mock import MagicMock, patch

from src.ticketmaster import (
    TicketmasterClient,
    APIError,
    AuthenticationError,
    EventNotFoundError,
    NetworkError,
    RateLimitError,
)
from src.models import EventStatusCode


class TestDailyBudget:
    def test_initial_count_is_zero(self):
        client = TicketmasterClient(api_key="test-key")
        assert client.get_daily_call_count() == 0

    def test_budget_warning_threshold(self):
        client = TicketmasterClient(api_key="test-key")
        client._daily_call_count = 3999
        assert client.is_budget_warning() is False
        client._daily_call_count = 4000
        assert client.is_budget_warning() is True

    def test_budget_exhausted_threshold(self):
        client = TicketmasterClient(api_key="test-key")
        client._daily_call_count = 4999
        assert client.is_budget_exhausted() is False
        client._daily_call_count = 5000
        assert client.is_budget_exhausted() is True


class TestEventStatusParsing:
    def test_parse_event_status(self):
        client = TicketmasterClient(api_key="test-key")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {
            "dates": {"status": {"code": "onsale"}},
            "priceRanges": [
                {"type": "standard", "currency": "USD", "min": 50.0, "max": 150.0}
            ],
            "url": "https://ticketmaster.com/event/test",
        }

        with patch.object(client.session, "request", return_value=mock_response):
            status = client.get_event_status("test-event")

        assert status.status_code == EventStatusCode.ONSALE
        assert len(status.price_ranges) == 1
        assert status.price_ranges[0].min_price == 50.0
        assert status.event_url == "https://ticketmaster.com/event/test"

    def test_unknown_status_code(self):
        assert EventStatusCode.from_str("something_weird") == EventStatusCode.UNKNOWN
        assert EventStatusCode.from_str("onsale") == EventStatusCode.ONSALE
        assert EventStatusCode.from_str("OFFSALE") == EventStatusCode.OFFSALE
