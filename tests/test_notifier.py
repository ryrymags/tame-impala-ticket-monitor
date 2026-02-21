"""Tests for Discord notifier — color selection and urgency labels."""

from src.notifier import (
    DiscordNotifier,
    COLOR_GREEN,
    COLOR_YELLOW,
    COLOR_ORANGE,
    COLOR_GREY,
)


class TestColorForScore:
    def setup_method(self):
        self.notifier = DiscordNotifier(webhook_url="https://test")

    def test_score_140_plus_is_green(self):
        assert self.notifier._color_for_score(140) == COLOR_GREEN
        assert self.notifier._color_for_score(200) == COLOR_GREEN

    def test_score_60_to_139_is_yellow(self):
        assert self.notifier._color_for_score(60) == COLOR_YELLOW
        assert self.notifier._color_for_score(139) == COLOR_YELLOW

    def test_score_30_to_59_is_orange(self):
        assert self.notifier._color_for_score(30) == COLOR_ORANGE
        assert self.notifier._color_for_score(59) == COLOR_ORANGE

    def test_score_below_30_is_grey(self):
        assert self.notifier._color_for_score(0) == COLOR_GREY
        assert self.notifier._color_for_score(29) == COLOR_GREY


class TestUrgencyLabel:
    def setup_method(self):
        self.notifier = DiscordNotifier(webhook_url="https://test")

    def test_high_score_is_drop_everything(self):
        label = self.notifier._urgency_label(140)
        assert "DROP EVERYTHING" in label

    def test_medium_score_is_good_option(self):
        label = self.notifier._urgency_label(60)
        assert "Good Option" in label

    def test_low_score_is_available(self):
        label = self.notifier._urgency_label(20)
        assert "Available" in label
