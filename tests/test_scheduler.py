"""Tests for the scoring and filtering logic in MonitorScheduler."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.config import MonitorConfig, EventConfig
from src.models import Offer, EventStatus, EventStatusCode
from src.scheduler import MonitorScheduler, SCORE_GA, SCORE_LOGE, SCORE_BALCONY, SCORE_UNDER_100, SCORE_QTY_4_PLUS, SCORE_QTY_2_PLUS


def _make_config(**overrides) -> MonitorConfig:
    defaults = dict(
        api_key="test", discord_webhook_url="http://test", discord_username="Test",
        events=[], max_price=175.0, currency="USD",
        preferred_sections=["General Admission", "LOGE", "Balcony"],
        daytime_interval_seconds=90, overnight_interval_seconds=300,
        daytime_start_hour=8, daytime_end_hour=1, backoff_multiplier=1.5,
        max_backoff_seconds=600, timezone="US/Eastern", cooldown_minutes=5,
        score_threshold=30, notify_on_status_change=True, daily_heartbeat_hour=9,
        enable_page_check=False, page_check_interval_multiplier=5,
        log_level="INFO", log_file="logs/test.log", log_max_file_size_mb=10,
        log_backup_count=3,
    )
    defaults.update(overrides)
    return MonitorConfig(**defaults)


def _make_scheduler(config=None) -> MonitorScheduler:
    config = config or _make_config()
    return MonitorScheduler(
        config=config,
        client=MagicMock(),
        notifier=MagicMock(),
        state=MagicMock(),
        start_time=datetime.now(timezone.utc),
    )


def _make_offer(name="Test Offer", price_min=50.0, price_max=50.0, limit=2, **kwargs) -> Offer:
    defaults = dict(
        offer_id="test-1", name=name, description=None, price_min=price_min,
        price_max=price_max, currency="USD", ticket_type=None, limit=limit,
        raw_data={},
    )
    defaults.update(kwargs)
    return Offer(**defaults)


class TestFilterAndScore:
    def test_ga_gets_max_section_score(self):
        scheduler = _make_scheduler()
        offers = [_make_offer(name="General Admission")]
        result = scheduler._filter_and_score(offers)
        assert len(result) == 1
        assert result[0].priority_score >= SCORE_GA

    def test_ga_keyword_floor(self):
        scheduler = _make_scheduler()
        result = scheduler._filter_and_score([_make_offer(name="Floor")])
        assert SCORE_GA in [SCORE_GA]  # Floor maps to GA
        assert result[0].priority_score >= SCORE_GA

    def test_loge_section_score(self):
        scheduler = _make_scheduler()
        result = scheduler._filter_and_score([_make_offer(name="LOGE 12")])
        assert len(result) == 1
        assert any("LOGE" in r for r in result[0].score_reasons)

    def test_balcony_section_score(self):
        scheduler = _make_scheduler()
        result = scheduler._filter_and_score([_make_offer(name="Balcony 301")])
        assert any("Balcony" in r for r in result[0].score_reasons)

    def test_unknown_section_gets_minimum_score(self):
        scheduler = _make_scheduler()
        # Use price above $100 and limit=1 to isolate the section score
        result = scheduler._filter_and_score([_make_offer(name="Club Suite", price_max=150.0, limit=1)])
        assert result[0].priority_score == 10

    def test_under_100_price_bonus(self):
        scheduler = _make_scheduler()
        result = scheduler._filter_and_score([_make_offer(name="GA", price_max=99.99)])
        assert any("under $100" in r for r in result[0].score_reasons)

    def test_over_100_no_price_bonus(self):
        scheduler = _make_scheduler()
        result = scheduler._filter_and_score([_make_offer(name="GA", price_max=150.0)])
        assert not any("under $100" in r for r in result[0].score_reasons)

    def test_qty_4_plus_bonus(self):
        scheduler = _make_scheduler()
        result = scheduler._filter_and_score([_make_offer(name="GA", limit=4)])
        assert any("qty" in r for r in result[0].score_reasons)
        assert result[0].priority_score >= SCORE_GA + SCORE_QTY_4_PLUS

    def test_qty_2_plus_bonus(self):
        scheduler = _make_scheduler()
        result = scheduler._filter_and_score([_make_offer(name="GA", limit=2)])
        assert any("qty" in r for r in result[0].score_reasons)

    def test_qty_1_no_bonus(self):
        scheduler = _make_scheduler()
        result = scheduler._filter_and_score([_make_offer(name="GA", limit=1)])
        assert not any("qty" in r for r in result[0].score_reasons)

    def test_over_max_price_filtered_out(self):
        scheduler = _make_scheduler(_make_config(max_price=100.0))
        offers = [_make_offer(name="GA", price_max=150.0)]
        result = scheduler._filter_and_score(offers)
        assert len(result) == 0

    def test_at_max_price_included(self):
        scheduler = _make_scheduler(_make_config(max_price=100.0))
        offers = [_make_offer(name="GA", price_max=100.0)]
        result = scheduler._filter_and_score(offers)
        assert len(result) == 1

    def test_no_price_info_still_included(self):
        scheduler = _make_scheduler()
        offers = [_make_offer(name="GA", price_min=None, price_max=None)]
        result = scheduler._filter_and_score(offers)
        assert len(result) == 1

    def test_sorted_by_score_descending(self):
        scheduler = _make_scheduler()
        offers = [
            _make_offer(offer_id="low", name="Balcony 301", price_max=150.0, limit=1),
            _make_offer(offer_id="high", name="General Admission", price_max=50.0, limit=6),
        ]
        result = scheduler._filter_and_score(offers)
        assert len(result) == 2
        assert result[0].offer_id == "high"
        assert result[1].offer_id == "low"

    def test_score_reasons_populated(self):
        scheduler = _make_scheduler()
        result = scheduler._filter_and_score([
            _make_offer(name="General Admission", price_max=80.0, limit=4)
        ])
        reasons = result[0].score_reasons
        assert "GA" in reasons
        assert "under $100" in reasons
        assert any("qty" in r for r in reasons)

    def test_empty_offers_returns_empty(self):
        scheduler = _make_scheduler()
        assert scheduler._filter_and_score([]) == []

    def test_combined_max_score(self):
        """GA + under $100 + qty 4+ should produce the highest possible score."""
        scheduler = _make_scheduler()
        result = scheduler._filter_and_score([
            _make_offer(name="General Admission", price_max=50.0, limit=6)
        ])
        expected = SCORE_GA + SCORE_UNDER_100 + SCORE_QTY_4_PLUS
        assert result[0].priority_score == expected


class TestBuildScoreReasons:
    def test_returns_reasons_from_top_offer(self):
        scheduler = _make_scheduler()
        offer = _make_offer(name="GA")
        offer.score_reasons = ["GA", "under $100"]
        offer.priority_score = 150
        result = scheduler._build_score_reasons([offer])
        assert result == ["GA", "under $100"]

    def test_empty_list_returns_empty(self):
        scheduler = _make_scheduler()
        assert scheduler._build_score_reasons([]) == []

    def test_fallback_when_no_reasons(self):
        scheduler = _make_scheduler()
        offer = _make_offer(name="GA")
        offer.score_reasons = []
        offer.priority_score = 42
        result = scheduler._build_score_reasons([offer])
        assert result == ["score 42"]
