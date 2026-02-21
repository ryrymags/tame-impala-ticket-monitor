"""Optional Tier 3 — extract structured data from Ticketmaster event page HTML.

Disabled by default. Enable with optional.enable_page_check: true in config.yaml.
This uses plain HTTP requests (no Selenium/Playwright) and may get blocked by
Ticketmaster's anti-bot systems. The monitor works fine without this.
"""

import json
import logging
import re
from typing import Optional

import requests

from .models import PageData

logger = logging.getLogger(__name__)

# Realistic browser headers to reduce chance of being blocked
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class PageChecker:
    """Attempt to extract ticket data from the Ticketmaster event page."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def check_page(self, event_url: str) -> Optional[PageData]:
        """Fetch the event page and try to extract embedded JSON data.

        Returns None if blocked, errored, or no useful data found.
        """
        try:
            resp = self.session.get(event_url, timeout=15, allow_redirects=True)
        except requests.RequestException as e:
            logger.debug("Page check failed for %s: %s", event_url, e)
            return None

        if resp.status_code != 200:
            logger.debug("Page check got status %d for %s", resp.status_code, event_url)
            return None

        html = resp.text

        # Try to extract __NEXT_DATA__ (Next.js embedded JSON)
        data = self._extract_next_data(html)
        if data:
            return self._parse_next_data(data)

        # Try JSON-LD structured data
        data = self._extract_json_ld(html)
        if data:
            return self._parse_json_ld(data)

        logger.debug("No structured data found on page %s", event_url)
        return None

    def _extract_next_data(self, html: str) -> Optional[dict]:
        """Extract __NEXT_DATA__ JSON from a Next.js page."""
        match = re.search(
            r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.debug("Failed to parse __NEXT_DATA__ JSON")
            return None

    def _extract_json_ld(self, html: str) -> Optional[dict]:
        """Extract JSON-LD structured data."""
        match = re.search(
            r'<script\s+type="application/ld\+json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                data = data[0] if data else None
            return data
        except json.JSONDecodeError:
            logger.debug("Failed to parse JSON-LD")
            return None

    def _parse_next_data(self, data: dict) -> Optional[PageData]:
        """Try to extract ticket info from __NEXT_DATA__."""
        sections = []
        resale = False
        price_info = None

        # Walk the data looking for ticket/offer/inventory info
        raw_str = json.dumps(data)[:2000]  # Keep a snippet for debugging

        # Look for common keys in Ticketmaster's Next.js data
        props = data.get("props", {}).get("pageProps", {})

        # Check for inventory/offers in page props
        inventory = props.get("inventory", props.get("offers", props.get("ticketOffers", {})))
        if isinstance(inventory, dict):
            for key, val in inventory.items():
                if isinstance(val, dict):
                    name = val.get("name", val.get("section", key))
                    sections.append(str(name))
                    if "resale" in str(val).lower():
                        resale = True

        # Check for availability info
        avail = props.get("availability", {})
        if isinstance(avail, dict):
            if avail.get("resale") or avail.get("faceValueExchange"):
                resale = True

        if not sections and not resale:
            return None

        return PageData(
            sections_available=sections,
            price_info=price_info,
            resale_detected=resale,
            raw_snippet=raw_str,
        )

    def _parse_json_ld(self, data: dict) -> Optional[PageData]:
        """Try to extract ticket info from JSON-LD."""
        if data.get("@type") not in ("Event", "MusicEvent"):
            return None

        sections = []
        resale = False
        price_info = None

        offers = data.get("offers", [])
        if isinstance(offers, dict):
            offers = [offers]

        for offer in offers:
            if isinstance(offer, dict):
                name = offer.get("name", offer.get("description", ""))
                if name:
                    sections.append(name)
                price = offer.get("price")
                if price:
                    price_info = f"${price}"
                avail = offer.get("availability", "")
                if "resale" in str(avail).lower() or "exchange" in str(avail).lower():
                    resale = True

        if not sections:
            return None

        return PageData(
            sections_available=sections,
            price_info=price_info,
            resale_detected=resale,
            raw_snippet=json.dumps(data)[:2000],
        )
