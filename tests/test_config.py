"""Tests for config loading and validation."""

import os
import tempfile

import pytest
import yaml

from src.config import load_config


def _write_config(tmp_path, overrides=None):
    """Write a valid config file with optional overrides."""
    config = {
        "ticketmaster": {"api_key": "test-api-key"},
        "discord": {"webhook_url": "https://discord.com/api/webhooks/test"},
        "events": [
            {
                "event_id": "vvG1IZ9YbmdXqt",
                "name": "Test Event",
                "date": "July 28, 2026",
                "url": "https://ticketmaster.com/event/test",
            }
        ],
        "polling": {"timezone": "US/Eastern"},
    }
    if overrides:
        for key, val in overrides.items():
            parts = key.split(".")
            target = config
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = val

    path = str(tmp_path / "config.yaml")
    with open(path, "w") as f:
        yaml.dump(config, f)
    return path


class TestLoadConfig:
    def test_loads_valid_config(self, tmp_path):
        path = _write_config(tmp_path)
        config = load_config(path)
        assert config.api_key == "test-api-key"
        assert len(config.events) == 1
        assert config.events[0].name == "Test Event"

    def test_env_var_overrides_api_key(self, tmp_path, monkeypatch):
        path = _write_config(tmp_path)
        monkeypatch.setenv("TM_API_KEY", "env-api-key")
        config = load_config(path)
        assert config.api_key == "env-api-key"

    def test_env_var_overrides_webhook(self, tmp_path, monkeypatch):
        path = _write_config(tmp_path)
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://env-webhook")
        config = load_config(path)
        assert config.discord_webhook_url == "https://env-webhook"

    def test_missing_file_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            load_config(str(tmp_path / "nonexistent.yaml"))

    def test_missing_api_key_exits(self, tmp_path):
        path = _write_config(tmp_path, {"ticketmaster.api_key": ""})
        with pytest.raises(SystemExit):
            load_config(path)

    def test_placeholder_api_key_exits(self, tmp_path):
        path = _write_config(tmp_path, {"ticketmaster.api_key": "YOUR_API_KEY_HERE"})
        with pytest.raises(SystemExit):
            load_config(path)

    def test_missing_events_exits(self, tmp_path):
        config_data = {
            "ticketmaster": {"api_key": "test-key"},
            "discord": {"webhook_url": "https://discord.com/api/webhooks/test"},
            "events": [],
        }
        path = str(tmp_path / "config.yaml")
        with open(path, "w") as f:
            yaml.dump(config_data, f)
        with pytest.raises(SystemExit):
            load_config(path)

    def test_invalid_timezone_exits(self, tmp_path):
        path = _write_config(tmp_path, {"polling.timezone": "US/Hogwarts"})
        with pytest.raises(SystemExit):
            load_config(path)

    def test_invalid_interval_exits(self, tmp_path):
        path = _write_config(tmp_path, {"polling.daytime_interval_seconds": "fast"})
        with pytest.raises(SystemExit):
            load_config(path)

    def test_defaults_applied(self, tmp_path):
        path = _write_config(tmp_path)
        config = load_config(path)
        assert config.daytime_interval_seconds == 30
        assert config.overnight_interval_seconds == 300
        assert config.timezone == "US/Eastern"

    def test_auto_generates_event_url(self, tmp_path):
        config_data = {
            "ticketmaster": {"api_key": "test-key"},
            "discord": {"webhook_url": "https://discord.com/api/webhooks/test"},
            "events": [{"event_id": "abc123", "name": "Test"}],
        }
        path = str(tmp_path / "config.yaml")
        with open(path, "w") as f:
            yaml.dump(config_data, f)
        config = load_config(path)
        assert config.events[0].url == "https://www.ticketmaster.com/event/abc123"
