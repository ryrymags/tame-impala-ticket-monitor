#!/usr/bin/env python3
"""Tame Impala Face Value Exchange Monitor.

Usage:
    python3 monitor.py              # Start monitoring
    python3 monitor.py --test       # Validate config, API key, and Discord webhook
    python3 monitor.py --once       # Run one check cycle and exit
    python3 monitor.py --verbose    # Override log level to DEBUG
    python3 monitor.py --config /path/to/config.yaml
"""

import argparse
import logging
import logging.handlers
import os
import signal
import sys
from datetime import datetime, timezone

from src.config import load_config
from src.notifier import DiscordNotifier
from src.scheduler import MonitorScheduler
from src.state import MonitorState
from src.ticketmaster import APIError, EventNotFoundError, NetworkError, TicketmasterClient


def setup_logging(log_level: str, log_file: str, max_mb: int, backup_count: int):
    """Configure logging to both console and rotating file."""
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root_logger.addHandler(console)

    # Rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_mb * 1024 * 1024,
        backupCount=backup_count,
    )
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)


def run_test(config_path: str):
    """Test mode: validate config, API, and Discord webhook."""
    print("Running setup checks...\n")

    # 1. Config
    print("[1/4] Loading config...")
    config = load_config(config_path)
    print(f"      Config loaded: {len(config.events)} event(s), max price ${config.max_price:.2f}")
    print(f"      Sections: {', '.join(config.preferred_sections)}")
    print()

    # 2. API key + Event IDs (combined to avoid redundant calls)
    print("[2/4] Testing API key and event IDs...")
    client = TicketmasterClient(config.api_key)
    api_ok = False
    for ev in config.events:
        try:
            s = client.get_event_status(ev.event_id)
            api_ok = True
            price_str = ""
            if s.price_ranges:
                mins = [p.min_price for p in s.price_ranges if p.min_price]
                maxs = [p.max_price for p in s.price_ranges if p.max_price]
                if mins and maxs:
                    price_str = f" | Prices: ${min(mins):.2f} - ${max(maxs):.2f}"
            url_str = f" | API URL: {s.event_url}" if s.event_url else ""
            print(f"      {ev.name}: {s.status_code.value}{price_str}{url_str}")
        except EventNotFoundError:
            print(f"      {ev.name}: NOT FOUND (check event_id: {ev.event_id})")
        except APIError as e:
            print(f"      {ev.name}: ERROR — {e}")
    if not api_ok:
        print("      FAILED: Could not reach the Discovery API. Check your API key.")
        sys.exit(1)
    print()

    # 3. Commerce API
    print("[3/4] Testing Commerce API v2...")
    commerce_accessible = False
    for ev in config.events:
        try:
            offers = client.get_event_offers(ev.event_id)
            if offers:
                commerce_accessible = True
                print(f"      {ev.name}: {len(offers)} offer(s) found")
                for offer in offers[:3]:
                    price_str = ""
                    if offer.price_min is not None:
                        price_str = f" — ${offer.price_min:.2f}"
                        if offer.price_max and offer.price_max != offer.price_min:
                            price_str = f" — ${offer.price_min:.2f}-${offer.price_max:.2f}"
                    print(f"        - {offer.name}{price_str}")
            else:
                print(f"      {ev.name}: No offers (API may require partner access, or event has no current offers)")
        except APIError as e:
            print(f"      {ev.name}: Commerce API error — {e}")
    if not commerce_accessible:
        print("      Note: Commerce API may not be available with free API keys.")
        print("      The monitor will still work using Discovery API status and price range changes.")
    print()

    # 4. Discord webhook
    print("[4/4] Sending test Discord notification...")
    notifier = DiscordNotifier(config.discord_webhook_url, config.discord_username)
    if notifier.send_test():
        print("      Discord webhook working — check your channel!")
    else:
        print("      FAILED: Could not send Discord notification. Check webhook URL.")
        sys.exit(1)
    print()

    print(f"All checks passed. API calls used: {client.get_daily_call_count()}")
    print("Run 'python3 monitor.py' to start monitoring.")


def run_monitor(config_path: str, once: bool = False):
    """Start the monitoring loop."""
    config = load_config(config_path)

    setup_logging(config.log_level, config.log_file, config.log_max_file_size_mb, config.log_backup_count)
    logger = logging.getLogger("monitor")

    client = TicketmasterClient(config.api_key)
    notifier = DiscordNotifier(config.discord_webhook_url, config.discord_username)
    state = MonitorState()
    start_time = datetime.now(timezone.utc)

    scheduler = MonitorScheduler(
        config=config,
        client=client,
        notifier=notifier,
        state=state,
        start_time=start_time,
    )

    # Graceful shutdown on SIGINT / SIGTERM
    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — shutting down...", sig_name)
        scheduler.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if once:
        logger.info("Running single check cycle (--once)")
        try:
            scheduler.run_once()
        except (APIError, NetworkError) as e:
            logger.error("Check failed: %s", e)
            sys.exit(1)
        logger.info("Done. API calls used: %d", client.get_daily_call_count())
    else:
        logger.info(
            "Starting monitor — %d event(s), polling every %ds (day) / %ds (night)",
            len(config.events),
            config.daytime_interval_seconds,
            config.overnight_interval_seconds,
        )
        scheduler.run()
        logger.info("Monitor stopped. Total API calls this session: %d", client.get_daily_call_count())


def main():
    parser = argparse.ArgumentParser(description="Tame Impala Face Value Exchange Monitor")
    parser.add_argument("--test", action="store_true", help="Validate config, API key, and Discord webhook")
    parser.add_argument("--once", action="store_true", help="Run one check cycle and exit")
    parser.add_argument("--config", default="config.yaml", help="Path to config file (default: config.yaml)")
    parser.add_argument("--verbose", action="store_true", help="Override log level to DEBUG")
    args = parser.parse_args()

    if args.verbose:
        os.environ["LOG_LEVEL_OVERRIDE"] = "DEBUG"

    if args.test:
        run_test(args.config)
    else:
        if args.verbose:
            # Temporarily set up logging for verbose mode before config loads
            logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        run_monitor(args.config, once=args.once)


if __name__ == "__main__":
    main()
