"""Configuration loader and validator."""

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import yaml
from dateutil import tz


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
    daily_recap_hour: int

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

    # Safe type conversion helper — collects errors instead of crashing
    def safe_int(section: dict, key: str, default: int, label: str) -> int:
        val = section.get(key, default)
        try:
            return int(val)
        except (ValueError, TypeError):
            errors.append(f"{label} must be an integer, got: {val!r}")
            return default

    def safe_float(section: dict, key: str, default: float, label: str) -> float:
        val = section.get(key, default)
        try:
            return float(val)
        except (ValueError, TypeError):
            errors.append(f"{label} must be a number, got: {val!r}")
            return default

    # Validate timezone
    timezone_str = polling.get("timezone", "US/Eastern")
    if tz.gettz(timezone_str) is None:
        errors.append(f"polling.timezone is invalid: {timezone_str!r}")

    # Run all type conversions before checking errors, so all issues are reported at once
    max_price = safe_float(prefs, "max_price", 175.0, "preferences.max_price")
    daytime_interval_seconds = safe_int(polling, "daytime_interval_seconds", 90, "polling.daytime_interval_seconds")
    overnight_interval_seconds = safe_int(polling, "overnight_interval_seconds", 300, "polling.overnight_interval_seconds")
    daytime_start_hour = safe_int(polling, "daytime_start_hour", 8, "polling.daytime_start_hour")
    daytime_end_hour = safe_int(polling, "daytime_end_hour", 1, "polling.daytime_end_hour")
    backoff_multiplier = safe_float(polling, "backoff_multiplier", 1.5, "polling.backoff_multiplier")
    max_backoff_seconds = safe_int(polling, "max_backoff_seconds", 600, "polling.max_backoff_seconds")
    cooldown_minutes = safe_int(notif, "cooldown_minutes", 5, "notifications.cooldown_minutes")
    score_threshold = safe_int(notif, "score_threshold", 30, "notifications.score_threshold")
    daily_heartbeat_hour = safe_int(notif, "daily_heartbeat_hour", 9, "notifications.daily_heartbeat_hour")
    daily_recap_hour = safe_int(notif, "daily_recap_hour", 23, "notifications.daily_recap_hour")
    page_check_interval_multiplier = safe_int(optional, "page_check_interval_multiplier", 5, "optional.page_check_interval_multiplier")
    log_max_file_size_mb = safe_int(logging_cfg, "max_file_size_mb", 10, "logging.max_file_size_mb")
    log_backup_count = safe_int(logging_cfg, "backup_count", 3, "logging.backup_count")

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
        max_price=max_price,
        currency=prefs.get("currency", "USD"),
        preferred_sections=prefs.get("preferred_sections", ["General Admission", "LOGE", "Balcony"]),
        daytime_interval_seconds=daytime_interval_seconds,
        overnight_interval_seconds=overnight_interval_seconds,
        daytime_start_hour=daytime_start_hour,
        daytime_end_hour=daytime_end_hour,
        backoff_multiplier=backoff_multiplier,
        max_backoff_seconds=max_backoff_seconds,
        timezone=timezone_str,
        cooldown_minutes=cooldown_minutes,
        score_threshold=score_threshold,
        notify_on_status_change=bool(notif.get("notify_on_status_change", True)),
        daily_heartbeat_hour=daily_heartbeat_hour,
        daily_recap_hour=daily_recap_hour,
        enable_page_check=bool(optional.get("enable_page_check", False)),
        page_check_interval_multiplier=page_check_interval_multiplier,
        log_level=logging_cfg.get("level", "INFO"),
        log_file=logging_cfg.get("file", "logs/monitor.log"),
        log_max_file_size_mb=log_max_file_size_mb,
        log_backup_count=log_backup_count,
    )
