"""Ticketmaster API client — Discovery API v2 and Commerce API v2."""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from .models import EventStatus, EventStatusCode, Offer, PriceRange, RateLimitInfo

logger = logging.getLogger(__name__)

# Public API limits: 5 req/sec, 5,000 req/day
MIN_REQUEST_GAP_SECONDS = 0.2  # Enforce max 5 req/sec
DAILY_BUDGET = 5000
DAILY_BUDGET_WARNING = 4000  # Start throttling here


class TicketmasterClient:
    """Client for Ticketmaster Discovery and Commerce APIs."""

    DISCOVERY_BASE = "https://app.ticketmaster.com/discovery/v2"
    COMMERCE_BASE = "https://app.ticketmaster.com/commerce/v2"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
        })

        # Rate limiting state
        self._last_request_time: float = 0.0
        self._daily_call_count: int = 0
        self._daily_reset_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._last_rate_limit_info: Optional[RateLimitInfo] = None

    # ---- Public methods ----

    def get_event_status(self, event_id: str) -> EventStatus:
        """Tier 1: Discovery API — get event status and price ranges."""
        url = f"{self.DISCOVERY_BASE}/events/{event_id}.json"
        params = {"apikey": self.api_key}

        data = self._request("GET", url, params=params)

        # Parse status
        dates = data.get("dates", {})
        status_info = dates.get("status", {})
        status_code = EventStatusCode.from_str(status_info.get("code", "unknown"))

        # Parse price ranges
        price_ranges = []
        for pr in data.get("priceRanges", []):
            price_ranges.append(PriceRange(
                type=pr.get("type", "standard"),
                currency=pr.get("currency", "USD"),
                min_price=float(pr.get("min", 0)),
                max_price=float(pr.get("max", 0)),
            ))

        # Extract the canonical event URL from the API response
        event_url = None
        links = data.get("_links", {})
        web_link = links.get("web", {})
        if isinstance(web_link, dict):
            event_url = web_link.get("href")
        # Also check top-level "url" field
        if not event_url:
            event_url = data.get("url")

        return EventStatus(
            event_id=event_id,
            status_code=status_code,
            price_ranges=price_ranges,
            event_url=event_url,
            raw_response=data,
        )

    def get_event_offers(self, event_id: str) -> list[Offer]:
        """Tier 2: Commerce API v2 — get ticket offers for an event.

        Returns an empty list if the Commerce API is not accessible with
        the current API key (401/403). The free Consumer Key may not have
        Commerce API access — this is expected and non-fatal.
        """
        url = f"{self.COMMERCE_BASE}/events/{event_id}/offers.json"
        params = {"apikey": self.api_key}

        try:
            data = self._request("GET", url, params=params)
        except RateLimitError:
            raise  # Don't swallow rate limits — let them bubble up
        except AuthenticationError as e:
            logger.debug("Commerce API not accessible (expected with free key): %s", e)
            return []
        except APIError as e:
            raise

        offers = []
        # The Commerce v2 response structure may vary; parse flexibly
        offers_data = data.get("offers", data.get("_embedded", {}).get("offers", []))
        if isinstance(offers_data, dict):
            # Sometimes offers is a dict keyed by offer ID
            offers_data = list(offers_data.values())

        for i, offer_raw in enumerate(offers_data):
            if isinstance(offer_raw, dict):
                offers.append(self._parse_offer(offer_raw, i))

        # Also check for "prices" at top level (alternative response shape)
        prices_data = data.get("prices", [])
        if prices_data and not offers:
            for i, price_raw in enumerate(prices_data):
                if isinstance(price_raw, dict):
                    offers.append(self._parse_price_as_offer(price_raw, i))

        return offers

    def get_daily_call_count(self) -> int:
        """Return how many API calls have been made today."""
        self._maybe_reset_daily_counter()
        return self._daily_call_count

    def is_budget_warning(self) -> bool:
        """True if daily calls are above the warning threshold."""
        self._maybe_reset_daily_counter()
        return self._daily_call_count >= DAILY_BUDGET_WARNING

    def is_budget_exhausted(self) -> bool:
        """True if daily calls have hit the hard limit."""
        self._maybe_reset_daily_counter()
        return self._daily_call_count >= DAILY_BUDGET

    # ---- Internal methods ----

    def _request(self, method: str, url: str, **kwargs) -> dict:
        """Make a rate-limited request, track budget, handle errors."""
        self._maybe_reset_daily_counter()
        self._throttle()

        # Increment counter BEFORE the request (counts toward budget even if it fails)
        self._daily_call_count += 1

        try:
            logger.debug("API %s %s (call #%d today)", method, url, self._daily_call_count)
            resp = self.session.request(method, url, timeout=15, **kwargs)
        except requests.ConnectionError as e:
            # Network failure — did NOT reach TM servers, so undo the counter
            self._daily_call_count -= 1
            raise NetworkError(f"Connection failed: {e}") from e
        except requests.Timeout as e:
            # Timeout — request likely did NOT reach the server or get processed, undo counter
            self._daily_call_count -= 1
            raise NetworkError(f"Request timed out: {e}") from e

        # Parse rate limit headers
        self._parse_rate_limit_headers(resp)

        # Handle error responses
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            raise RateLimitError(
                f"Rate limited (429). Retry after {retry_after}s.",
                retry_after=retry_after,
            )
        elif resp.status_code == 401:
            raise AuthenticationError("Invalid API key (401). Check ticketmaster.api_key in config.yaml.")
        elif resp.status_code == 403:
            raise AuthenticationError("Access forbidden (403). Your API key may be revoked or the endpoint restricted.")
        elif resp.status_code == 404:
            raise EventNotFoundError(f"Event not found (404) at {url}")
        elif resp.status_code >= 500:
            raise APIError(f"Server error ({resp.status_code}) from Ticketmaster.")
        elif resp.status_code != 200:
            raise APIError(f"Unexpected status {resp.status_code}: {resp.text[:200]}")

        return resp.json()

    def _throttle(self):
        """Enforce minimum gap between requests (5 req/sec = 200ms gap)."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < MIN_REQUEST_GAP_SECONDS:
            sleep_time = MIN_REQUEST_GAP_SECONDS - elapsed
            logger.debug("Throttling: sleeping %.2fs", sleep_time)
            time.sleep(sleep_time)
        self._last_request_time = time.monotonic()

    def _maybe_reset_daily_counter(self):
        """Reset daily call count at midnight UTC."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            logger.info("Daily counter reset: %d calls yesterday", self._daily_call_count)
            self._daily_call_count = 0
            self._daily_reset_date = today

    def _parse_rate_limit_headers(self, resp: requests.Response):
        """Extract rate limit info from response headers."""
        try:
            self._last_rate_limit_info = RateLimitInfo(
                limit=int(resp.headers.get("Rate-Limit", 0)),
                available=int(resp.headers.get("Rate-Limit-Available", 0)),
                over=int(resp.headers.get("Rate-Limit-Over", 0)),
                reset_seconds=int(resp.headers.get("Rate-Limit-Reset", 0)),
            )
        except (ValueError, TypeError):
            pass  # Headers may not always be present

    def _parse_offer(self, raw: dict, index: int) -> Offer:
        """Parse an offer from Commerce API v2 response."""
        # Try multiple possible field names for price data
        price_min = None
        price_max = None
        currency = "USD"

        # Check nested "prices" or "attributes.prices"
        prices = raw.get("prices", raw.get("attributes", {}).get("prices", []))
        if isinstance(prices, list) and prices:
            for p in prices:
                val = p.get("value", p.get("amount"))
                if val is not None:
                    val = float(val)
                    if price_min is None or val < price_min:
                        price_min = val
                    if price_max is None or val > price_max:
                        price_max = val
                    currency = p.get("currency", currency)

        # Check top-level price fields
        if price_min is None:
            for key in ("totalPrice", "price", "faceValue", "listPrice"):
                if key in raw:
                    val = raw[key]
                    if isinstance(val, dict):
                        price_min = float(val.get("amount", val.get("value", 0)))
                        currency = val.get("currency", currency)
                    elif isinstance(val, (int, float)):
                        price_min = float(val)
                    if price_min is not None:
                        price_max = price_max or price_min
                        break

        # Offer limit (max tickets per order)
        limit = raw.get("limit", raw.get("attributes", {}).get("limit"))
        if isinstance(limit, dict):
            limit = limit.get("max", limit.get("multiples"))
        if limit is not None:
            try:
                limit = int(limit)
            except (ValueError, TypeError):
                limit = None

        return Offer(
            offer_id=str(raw.get("id", raw.get("offerId", f"offer_{index}"))),
            name=raw.get("name", raw.get("description", f"Offer {index + 1}")),
            description=raw.get("description", raw.get("name")),
            price_min=price_min,
            price_max=price_max,
            currency=currency,
            ticket_type=raw.get("ticketTypeId", raw.get("type")),
            limit=limit,
            raw_data=raw,
        )

    def _parse_price_as_offer(self, raw: dict, index: int) -> Offer:
        """Parse a price entry as an Offer (fallback for alternative response shapes)."""
        val = raw.get("value", raw.get("amount", raw.get("total")))
        price = float(val) if val is not None else None
        return Offer(
            offer_id=str(raw.get("id", f"price_{index}")),
            name=raw.get("section", raw.get("name", f"Price {index + 1}")),
            description=raw.get("description"),
            price_min=price,
            price_max=price,
            currency=raw.get("currency", "USD"),
            ticket_type=raw.get("type"),
            limit=None,
            raw_data=raw,
        )


# ---- Custom exceptions ----

class APIError(Exception):
    """General Ticketmaster API error."""
    pass


class NetworkError(Exception):
    """Network connectivity error — request never reached Ticketmaster."""
    pass


class AuthenticationError(APIError):
    """HTTP 401/403 authentication or authorization error."""
    pass


class RateLimitError(APIError):
    """HTTP 429 rate limit exceeded."""

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


class EventNotFoundError(APIError):
    """Event ID not found (404)."""
    pass
