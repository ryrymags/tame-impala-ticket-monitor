"""Configuration loader and validator."""

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class EventConfig:
    event_id: str
    name: str
    date: str
    url: str


@dataclass
class MonitorConfig:
    # Ticketmaster
    api_key: str

    # Discord
    discord_webhook_url: str
    discord_username: str

    # Events
    events: list[EventConfig]

    # Preferences
    max_price: float
    currency: str
    preferred_sections: list[str]

    # Polling
    daytime_interval_seconds: int
    overnight_interval_seconds: int
    daytime_start_hour: int
    daytime_end_hour: int
    backoff_multiplier: float
    max_backoff_seconds: int
    timezone: str

    # Notifications
    cooldown_minutes: int
    score_threshold: int
    notify_on_status_change: bool
    daily_heartbeat_hour: int

    # Optional
    enable_page_check: bool
    page_check_interval_multiplier: int

    # Logging
    log_level: str
    log_file: str
    log_max_file_size_mb: int
    log_backup_count: int


def load_config(path: str = "config.yaml") -> MonitorConfig:
    """Load and validate configuration from a YAML file."""
    if not os.path.exists(path):
        print(f"Error: Config file not found: {path}")
        sys.exit(1)

    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    errors = []

    # Required top-level keys
    tm = raw.get("ticketmaster", {})
    discord = raw.get("discord", {})
    events_raw = raw.get("events", [])
    prefs = raw.get("preferences", {})
    polling = raw.get("polling", {})
    notif = raw.get("notifications", {})
    optional = raw.get("optional", {})
    logging_cfg = raw.get("logging", {})

    # Validate required fields — environment variables override config file.
    # This lets GitHub Actions pass secrets via TM_API_KEY and DISCORD_WEBHOOK_URL.
    api_key = os.environ.get("TM_API_KEY") or tm.get("api_key", "")
    if not api_key or api_key == "YOUR_API_KEY_HERE":
        errors.append("ticketmaster.api_key is required — get one at https://developer.ticketmaster.com/")

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL") or discord.get("webhook_url", "")
    if not webhook_url or webhook_url == "YOUR_WEBHOOK_URL_HERE":
        errors.append("discord.webhook_url is required — create one in Server Settings > Integrations > Webhooks")

    if not events_raw:
        errors.append("events: at least one event must be configured")

    # Parse events
    events = []
    for i, ev in enumerate(events_raw):
        eid = ev.get("event_id", "")
        ename = ev.get("name", f"Event {i + 1}")
        edate = ev.get("date", "")
        eurl = ev.get("url", "")
        if not eid:
            errors.append(f"events[{i}].event_id is required")
        if not eurl:
            eurl = f"https://www.ticketmaster.com/event/{eid}"
        events.append(EventConfig(event_id=eid, name=ename, date=edate, url=eurl))

    if errors:
        print("Configuration errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    return MonitorConfig(
        api_key=api_key,
        discord_webhook_url=webhook_url,
        discord_username=discord.get("username", "Ticket Monitor"),
        events=events,
        max_price=float(prefs.get("max_price", 175.0)),
        currency=prefs.get("currency", "USD"),
        preferred_sections=prefs.get("preferred_sections", ["General Admission", "LOGE", "Balcony"]),
        daytime_interval_seconds=int(polling.get("daytime_interval_seconds", 90)),
        overnight_interval_seconds=int(polling.get("overnight_interval_seconds", 300)),
        daytime_start_hour=int(polling.get("daytime_start_hour", 8)),
        daytime_end_hour=int(polling.get("daytime_end_hour", 1)),
        backoff_multiplier=float(polling.get("backoff_multiplier", 1.5)),
        max_backoff_seconds=int(polling.get("max_backoff_seconds", 600)),
        timezone=polling.get("timezone", "US/Eastern"),
        cooldown_minutes=int(notif.get("cooldown_minutes", 5)),
        score_threshold=int(notif.get("score_threshold", 30)),
        notify_on_status_change=bool(notif.get("notify_on_status_change", True)),
        daily_heartbeat_hour=int(notif.get("daily_heartbeat_hour", 9)),
        enable_page_check=bool(optional.get("enable_page_check", False)),
        page_check_interval_multiplier=int(optional.get("page_check_interval_multiplier", 5)),
        log_level=logging_cfg.get("level", "INFO"),
        log_file=logging_cfg.get("file", "logs/monitor.log"),
        log_max_file_size_mb=int(logging_cfg.get("max_file_size_mb", 10)),
        log_backup_count=int(logging_cfg.get("backup_count", 3)),
    )
