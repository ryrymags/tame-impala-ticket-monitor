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


class TestParseOffer:
    def setup_method(self):
        self.client = TicketmasterClient(api_key="test-key")

    def test_parse_basic_offer(self):
        raw = {
            "id": "offer-1",
            "name": "General Admission",
            "prices": [{"value": 75.0, "currency": "USD"}],
            "limit": 4,
        }
        offer = self.client._parse_offer(raw, 0)
        assert offer.offer_id == "offer-1"
        assert offer.name == "General Admission"
        assert offer.price_min == 75.0
        assert offer.price_max == 75.0
        assert offer.limit == 4

    def test_parse_offer_with_price_range(self):
        raw = {
            "id": "offer-2",
            "name": "LOGE",
            "prices": [
                {"value": 60.0, "currency": "USD"},
                {"value": 120.0, "currency": "USD"},
            ],
        }
        offer = self.client._parse_offer(raw, 0)
        assert offer.price_min == 60.0
        assert offer.price_max == 120.0

    def test_parse_offer_with_top_level_price(self):
        raw = {
            "id": "offer-3",
            "name": "Test",
            "totalPrice": {"amount": 99.99, "currency": "USD"},
        }
        offer = self.client._parse_offer(raw, 0)
        assert offer.price_min == 99.99

    def test_parse_offer_no_price(self):
        raw = {"id": "offer-4", "name": "Mystery"}
        offer = self.client._parse_offer(raw, 0)
        assert offer.price_min is None
        assert offer.price_max is None

    def test_parse_offer_fallback_id(self):
        raw = {"name": "Test"}
        offer = self.client._parse_offer(raw, 3)
        assert offer.offer_id == "offer_3"

    def test_parse_offer_dict_limit(self):
        raw = {"id": "offer-5", "name": "Test", "limit": {"max": 8}}
        offer = self.client._parse_offer(raw, 0)
        assert offer.limit == 8

    def test_parse_offer_nested_attributes(self):
        raw = {
            "id": "offer-6",
            "name": "Test",
            "attributes": {
                "prices": [{"value": 50.0}],
                "limit": 2,
            },
        }
        offer = self.client._parse_offer(raw, 0)
        assert offer.price_min == 50.0
        assert offer.limit == 2


class TestParsePriceAsOffer:
    def setup_method(self):
        self.client = TicketmasterClient(api_key="test-key")

    def test_parse_price_entry(self):
        raw = {"id": "price-1", "section": "Floor", "value": 85.0, "currency": "USD"}
        offer = self.client._parse_price_as_offer(raw, 0)
        assert offer.offer_id == "price-1"
        assert offer.name == "Floor"
        assert offer.price_min == 85.0
        assert offer.price_max == 85.0

    def test_parse_price_no_value(self):
        raw = {"section": "Unknown"}
        offer = self.client._parse_price_as_offer(raw, 0)
        assert offer.price_min is None


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
