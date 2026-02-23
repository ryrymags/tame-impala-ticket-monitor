# Tame Impala Face Value Exchange Monitor

Automated monitor that checks Ticketmaster for Face Value Exchange resale tickets to two sold-out **Tame Impala "The Deadbeat Tour"** concerts at **TD Garden, Boston** and sends Discord notifications when tickets matching your criteria appear.

## What This Does

Both shows (July 28 & 29, 2026) are sold out. Ticketmaster's **Face Value Exchange** allows original buyers to resell their tickets at face value through Ticketmaster's official platform — no scalper markups. This monitor polls the Ticketmaster Discovery API every **30 seconds** during active hours looking for:

- Status changes (e.g., `offsale` → `onsale`, which signals resale listings may have appeared)
- Price range data appearing on previously sold-out events

When something changes, it sends a Discord notification with a direct purchase link.

## Events Monitored

| Show | Date | Venue | Discovery API ID |
|------|------|-------|-----------------|
| Night 1 | July 28, 2026 | TD Garden, Boston | `Za5ju3rKuqZDexDqkBlMyehlJWXnwBnVa-` |
| Night 2 | July 29, 2026 | TD Garden, Boston | `Za5ju3rKuqZDdqr7bcuPs7uyUCc6YkjYH2` |

> **Note:** The Discovery API uses different event IDs than the ones in website URLs. The website legacy IDs (e.g., `01006430FEAADAD2`) do not work with the API.

## How It Detects Tickets

The monitor uses the **Ticketmaster Discovery API v2** (free Consumer Key):

**Endpoint:** `https://app.ticketmaster.com/discovery/v2/events/{id}.json?apikey=KEY`

Two independent detection triggers, either of which pings you on Discord:

1. **Status change** — The event status flips from `offsale` → `onsale`. For a sold-out show, this typically means Face Value Exchange resale has opened.
2. **Price range appearance** — `priceRanges` data shows up in the API response where there was none before. Catches cases where FVE listings appear without a status change.

### Discord Notifications
- **Blue** (`#3498DB`) — Status changes, price range appearances, heartbeat, and daily recap
- **Red** (`#E74C3C`) — Back to sold out, or monitor errors
- **Green** (`#00FF00`) — Test notification (from `--test`)

## Polling Strategy

Two-tier polling based on time of day (US/Eastern timezone):

| Period | Hours | Interval | Rationale |
|--------|-------|----------|-----------|
| Daytime | 8 AM – 1 AM ET | Every 30 seconds | Active hours, most likely time for listings |
| Overnight | 1 AM – 8 AM ET | Every 5 minutes | Low activity, save API budget |

### API Budget
- **Rate limit:** 5 requests/second, 5,000 requests/day
- Each check cycle makes 2 Discovery API calls (one per event)
- At 30-second intervals during daytime (17 hours): ~4,080 calls + ~168 overnight = **~4,250 calls/day**
- Leaves ~750 calls of headroom for manual tests and edge cases
- The monitor tracks daily usage and warns at 4,000 calls, stops at 5,000

## File Structure

```
tame-impala-ticket-monitor/
├── .github/
│   └── workflows/
│       ├── monitor.yml          # CI: runs test suite on PRs and pushes to main
│       ├── deploy.yml           # CD: SSH deploys to Oracle VM and restarts service
│       └── auto-merge.yml       # Squash-merges claude/** branches to main, triggers deploy
├── deploy/
│   └── setup.sh                 # One-time VM setup script (venv, deps, systemd service)
├── src/
│   ├── __init__.py              # Package marker
│   ├── config.py                # YAML config loader with env var overrides
│   ├── models.py                # Dataclasses (EventStatus, PriceRange, etc.)
│   ├── notifier.py              # Discord webhook sender
│   ├── page_checker.py          # Optional HTML page scraper (disabled by default)
│   ├── scheduler.py             # Main monitoring loop with adaptive intervals
│   ├── state.py                 # JSON state persistence (status, price ranges, checks)
│   └── ticketmaster.py          # Discovery API client with rate limiting
├── tests/                       # pytest test suite
├── .gitignore
├── config.example.yaml          # Template config (copy to config.yaml and fill in values)
├── monitor.py                   # Entry point (--test, --once, --recap, --heartbeat, --verbose)
├── pyproject.toml               # Project metadata and ruff/pytest config
├── requirements.txt             # Python dependencies
└── README.md                    # This file
```

## How It Runs

### Production (Oracle VM + systemd)

The monitor runs as a persistent `systemd` service on an Oracle Cloud free-tier VM:

1. `monitor.py` runs continuously, polling in a loop with adaptive intervals (30s daytime / 5min overnight)
2. `state.json` is stored on disk — survives restarts, tracks statuses and price ranges
3. Logs go to `journald` (`sudo journalctl -u ticket-monitor -f`)

**Deploy pipeline:**
- Push to a `claude/**` branch → `auto-merge.yml` squash-merges to `main`
- Push to `main` → `deploy.yml` SSH's into the VM, runs `git pull`, reinstalls deps, and restarts the service
- `monitor.yml` runs the `pytest` test suite on every PR and push to `main`

**Secrets** are stored in GitHub repo Settings → Secrets:
- `TM_API_KEY` — Ticketmaster Consumer Key
- `DISCORD_WEBHOOK_URL` — Discord webhook URL
- `VM_HOST`, `VM_USER`, `VM_SSH_KEY` — SSH credentials for the Oracle VM

`TM_API_KEY` and `DISCORD_WEBHOOK_URL` override `config.yaml` values via environment variables (see `src/config.py`). On the VM, `config.yaml` holds the actual values directly.

**First-time VM setup:**
```bash
git clone https://github.com/ryrymags/tame-impala-ticket-monitor
cd tame-impala-ticket-monitor
cp config.example.yaml config.yaml
nano config.yaml  # fill in API key and webhook URL
bash deploy/setup.sh
```

### Local Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Test everything (config, API key, event IDs, Discord webhook)
python monitor.py --test

# Run one check and exit
python monitor.py --once

# Run continuously (polls until interrupted)
python monitor.py

# Debug mode
python monitor.py --verbose
```

## Configuration

All settings are in `config.yaml`. Key sections:

- **`ticketmaster.api_key`** — Your Ticketmaster Consumer Key (free from developer.ticketmaster.com)
- **`discord.webhook_url`** — Your Discord webhook URL
- **`events`** — List of events to monitor (Discovery API IDs, names, dates, URLs)
- **`polling`** — Interval timing (30s daytime / 5min overnight), backoff settings, timezone
- **`notifications`** — Status change alerts, daily heartbeat/recap hours

## State Management

The monitor persists state to `state.json` (excluded from git via `.gitignore`):

- **Last known status** per event — detects `offsale` → `onsale` transitions
- **Price range presence** per event — detects when price data appears
- **Last heartbeat date** — ensures only one heartbeat per day
- **Last check timestamp** — reported in heartbeat messages

State is saved atomically (write to temp file, then `os.replace`) to prevent corruption. On the VM, `state.json` lives on disk and persists across service restarts automatically.

## Error Handling

- **Network errors** (connection failures, timeouts): Do NOT count toward daily API budget. Monitor retries with increasing backoff (30s increments, capped at 10 min). Logs recovery when network returns.
- **Rate limiting** (HTTP 429): Respects `Retry-After` header. Backs off for the specified duration.
- **Authentication errors (401/403)**: Indicates revoked or invalid API key. Logged and surfaced.
- **Event not found (404)**: Skips that event for the current cycle, continues checking others.
- **Server errors (5xx)**: Exponential backoff with multiplier from config.

## Dependencies

- **requests** — HTTP client for API calls and Discord webhooks
- **pyyaml** — YAML config file parsing
- **python-dateutil** — Timezone handling for polling schedule

All are standard, well-maintained Python packages. No Selenium, Playwright, or browser automation required.

## Limitations

- The Discovery API provides event-level status and price ranges, not individual ticket listings (sections, seat numbers, per-ticket prices). When you get a ping, you'll need to check Ticketmaster yourself to see what's available.
- **Face Value Exchange** listings may appear and disappear quickly. The 30-second polling interval means there could be a brief window where tickets are posted and sold before the next check.
- The **page checker** (Tier 3) may be blocked by Ticketmaster's anti-bot/anti-scraping systems and is disabled by default.
