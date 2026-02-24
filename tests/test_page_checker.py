"""Tests for the page checker — HTML extraction and parsing logic."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.page_checker import PageChecker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _html_with_next_data(data: dict) -> str:
    payload = json.dumps(data)
    return f'<html><script id="__NEXT_DATA__" type="application/json">{payload}</script></html>'


def _html_with_json_ld(data) -> str:
    payload = json.dumps(data)
    return f'<html><script type="application/ld+json">{payload}</script></html>'


# ---------------------------------------------------------------------------
# _extract_next_data
# ---------------------------------------------------------------------------

class TestExtractNextData:
    def test_returns_dict_when_present(self):
        checker = PageChecker()
        html = _html_with_next_data({"key": "value"})
        result = checker._extract_next_data(html)
        assert result == {"key": "value"}

    def test_returns_none_when_absent(self):
        checker = PageChecker()
        result = checker._extract_next_data("<html><body>No data here</body></html>")
        assert result is None

    def test_returns_none_on_invalid_json(self):
        checker = PageChecker()
        html = '<script id="__NEXT_DATA__" type="application/json">{bad json}</script>'
        result = checker._extract_next_data(html)
        assert result is None


# ---------------------------------------------------------------------------
# _extract_json_ld
# ---------------------------------------------------------------------------

class TestExtractJsonLd:
    def test_returns_dict_when_present(self):
        checker = PageChecker()
        html = _html_with_json_ld({"@type": "Event", "name": "Test"})
        result = checker._extract_json_ld(html)
        assert result == {"@type": "Event", "name": "Test"}

    def test_unwraps_list(self):
        checker = PageChecker()
        html = _html_with_json_ld([{"@type": "MusicEvent"}])
        result = checker._extract_json_ld(html)
        assert result == {"@type": "MusicEvent"}

    def test_returns_none_when_absent(self):
        checker = PageChecker()
        result = checker._extract_json_ld("<html></html>")
        assert result is None

    def test_returns_none_on_invalid_json(self):
        checker = PageChecker()
        html = '<script type="application/ld+json">{bad json}</script>'
        result = checker._extract_json_ld(html)
        assert result is None

    def test_returns_none_on_empty_list(self):
        checker = PageChecker()
        html = _html_with_json_ld([])
        result = checker._extract_json_ld(html)
        assert result is None


# ---------------------------------------------------------------------------
# _parse_next_data
# ---------------------------------------------------------------------------

class TestParseNextData:
    def test_returns_none_when_no_relevant_data(self):
        checker = PageChecker()
        result = checker._parse_next_data({"props": {"pageProps": {}}})
        assert result is None

    def test_detects_resale_in_inventory(self):
        checker = PageChecker()
        data = {
            "props": {
                "pageProps": {
                    "inventory": {
                        "offer1": {"name": "Floor A", "type": "resale"}
                    }
                }
            }
        }
        result = checker._parse_next_data(data)
        assert result is not None
        assert result.resale_detected is True
        assert "Floor A" in result.sections_available

    def test_detects_face_value_exchange_in_inventory(self):
        checker = PageChecker()
        data = {
            "props": {
                "pageProps": {
                    "inventory": {
                        "offer1": {"name": "Face Value Exchange Ticket", "section": "119"}
                    }
                }
            }
        }
        result = checker._parse_next_data(data)
        assert result is not None
        assert result.resale_detected is True

    def test_detects_resale_via_availability_flag(self):
        checker = PageChecker()
        data = {
            "props": {
                "pageProps": {
                    "availability": {"faceValueExchange": True},
                    "inventory": {"s1": {"name": "Section 101"}},
                }
            }
        }
        result = checker._parse_next_data(data)
        assert result is not None
        assert result.resale_detected is True

    def test_no_resale_when_inventory_present_but_no_resale_keyword(self):
        checker = PageChecker()
        data = {
            "props": {
                "pageProps": {
                    "inventory": {
                        "offer1": {"name": "GA Floor", "type": "standard"}
                    }
                }
            }
        }
        result = checker._parse_next_data(data)
        assert result is not None
        assert result.resale_detected is False


# ---------------------------------------------------------------------------
# _parse_json_ld
# ---------------------------------------------------------------------------

class TestParseJsonLd:
    def test_returns_none_for_wrong_type(self):
        checker = PageChecker()
        result = checker._parse_json_ld({"@type": "Organization"})
        assert result is None

    def test_returns_none_when_no_offers(self):
        checker = PageChecker()
        result = checker._parse_json_ld({"@type": "Event", "offers": []})
        assert result is None

    def test_parses_single_offer_dict(self):
        checker = PageChecker()
        data = {
            "@type": "MusicEvent",
            "offers": {"name": "GA Floor", "price": "75"},
        }
        result = checker._parse_json_ld(data)
        assert result is not None
        assert "GA Floor" in result.sections_available
        assert result.price_info == "$75"

    def test_parses_offer_list(self):
        checker = PageChecker()
        data = {
            "@type": "Event",
            "offers": [
                {"name": "Section 101"},
                {"name": "Section 102"},
            ],
        }
        result = checker._parse_json_ld(data)
        assert result is not None
        assert len(result.sections_available) == 2

    def test_detects_resale_in_availability(self):
        checker = PageChecker()
        data = {
            "@type": "Event",
            "offers": [{"name": "FVE", "availability": "InStockResale"}],
        }
        result = checker._parse_json_ld(data)
        assert result is not None
        assert result.resale_detected is True

    def test_no_resale_for_normal_availability(self):
        checker = PageChecker()
        data = {
            "@type": "Event",
            "offers": [{"name": "Floor", "availability": "InStock"}],
        }
        result = checker._parse_json_ld(data)
        assert result is not None
        assert result.resale_detected is False

    def test_detects_fve_in_offer_name(self):
        checker = PageChecker()
        data = {
            "@type": "MusicEvent",
            "offers": [{"name": "Face Value Exchange Ticket", "price": "419.60"}],
        }
        result = checker._parse_json_ld(data)
        assert result is not None
        assert result.resale_detected is True
        assert result.price_info == "$419.60"


# ---------------------------------------------------------------------------
# check_page — network and HTTP error handling
# ---------------------------------------------------------------------------

class TestCheckPage:
    def test_returns_none_on_request_exception(self):
        checker = PageChecker()
        checker.session.get = MagicMock(side_effect=requests.RequestException("timeout"))
        result = checker.check_page("https://example.com/event/123")
        assert result is None

    def test_returns_none_on_non_200_status(self):
        checker = PageChecker()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        checker.session.get = MagicMock(return_value=mock_resp)
        result = checker.check_page("https://example.com/event/123")
        assert result is None

    def test_returns_none_when_no_structured_data(self):
        checker = PageChecker()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Sold out.</body></html>"
        checker.session.get = MagicMock(return_value=mock_resp)
        result = checker.check_page("https://example.com/event/123")
        assert result is None

    def test_parses_next_data_from_page(self):
        checker = PageChecker()
        data = {
            "props": {
                "pageProps": {
                    "inventory": {"offer1": {"name": "Floor", "type": "resale"}}
                }
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _html_with_next_data(data)
        checker.session.get = MagicMock(return_value=mock_resp)
        result = checker.check_page("https://example.com/event/123")
        assert result is not None
        assert result.resale_detected is True

    def test_falls_back_to_json_ld_when_no_next_data(self):
        checker = PageChecker()
        data = {
            "@type": "MusicEvent",
            "offers": [{"name": "Section 101"}],
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _html_with_json_ld(data)
        checker.session.get = MagicMock(return_value=mock_resp)
        result = checker.check_page("https://example.com/event/123")
        assert result is not None
        assert "Section 101" in result.sections_available
