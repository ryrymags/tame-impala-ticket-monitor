"""Data models for the ticket monitor."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


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
    event_url: Optional[str]   # URL from Discovery API (_links.web.href)
    raw_response: dict


@dataclass
class Offer:
    offer_id: str
    name: str
    description: Optional[str]
    price_min: Optional[float]
    price_max: Optional[float]
    currency: str
    ticket_type: Optional[str]
    limit: Optional[int]           # max tickets per order
    raw_data: dict
    priority_score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)


@dataclass
class PageData:
    sections_available: list[str]
    price_info: Optional[str]
    resale_detected: bool
    raw_snippet: str


@dataclass
class RateLimitInfo:
    limit: int
    available: int
    over: int
    reset_seconds: int


@dataclass
class TicketAlert:
    event_name: str
    event_date: str
    event_url: str
    event_id: str
    status: EventStatus
    matching_offers: list[Offer]
    page_data: Optional[PageData]
    timestamp: datetime
    total_score: float
    score_reasons: list[str]
