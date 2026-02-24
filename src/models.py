"""Data models for the ticket monitor."""

from dataclasses import dataclass
from enum import Enum


class EventStatusCode(Enum):
    ONSALE = "onsale"
    OFFSALE = "offsale"
    CANCELLED = "cancelled"
    POSTPONED = "postponed"
    RESCHEDULED = "rescheduled"
    UNKNOWN = "unknown"

    @classmethod
    def from_str(cls, value: str) -> "EventStatusCode":
        try:
            return cls(value.lower())
        except ValueError:
            return cls.UNKNOWN


@dataclass
class PriceRange:
    type: str        # "standard", "resale", etc.
    currency: str
    min_price: float
    max_price: float


@dataclass
class EventStatus:
    event_id: str
    status_code: EventStatusCode
    price_ranges: list[PriceRange]
    event_url: str | None      # URL from Discovery API (_links.web.href)
    raw_response: dict


@dataclass
class PageData:
    sections_available: list[str]
    price_info: str | None
    resale_detected: bool
    raw_snippet: str


@dataclass
class RateLimitInfo:
    limit: int
    available: int
    over: int
    reset_seconds: int
