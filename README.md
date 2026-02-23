# Tame Impala Face Value Exchange Monitor

Automated monitor that checks Ticketmaster for Face Value Exchange resale tickets to two sold-out **Tame Impala "The Deadbeat Tour"** concerts at **TD Garden, Boston** and sends Discord notifications when tickets matching your criteria appear.

## What This Does

Both shows (July 28 & 29, 2026) are sold out. Ticketmaster's **Face Value Exchange** allows original buyers to resell their tickets at face value through Ticketmaster's official platform — no scalper markups. This monitor polls the Ticketmaster Discovery API continuously (every 90 seconds during active hours) looking for:

- Status changes (e.g., `offsale` → `onsale`, which signals resale listings may have appeared)
- Price range data appearing on previously sold-out events
- Commerce API offer data (if accessible — requires partner-level API access)

When something changes, it sends a scored, color-coded Discord notification with a direct purchase link.

## Events Monitored

| Show | Date | Venue | Discovery API ID |
|------|------|-------|-----------------|
| Night 1 | July 28, 2026 | TD Garden, Boston | `Za5ju3rKuqZDexDqkBlMyehlJWXnwBnVa-` |
| Night 2 | July 29, 2026 | TD Garden, Boston | `Za5ju3rKuqZDdqr7bcuPs7uyUCc6YkjYH2` |

> **Note:** The Discovery API uses different event IDs than the ones in website URLs. The website legacy IDs (e.g., `01006430FEAADAD2`) do not work with the API.

## Architecture

The monitor has a three-tier data collection approach:

### Tier 1: Discovery API v2 (Primary)
- **Endpoint:** `https://app.ticketmaster.com/discovery/v2/events/{id}.json?apikey=KEY`
- Checks event status (`onsale`, `offsale`, `cancelled`, etc.)
- Reads `priceRanges` when available
- Extracts the canonical event URL from the API response
- **This is the main detection mechanism** — works with free Consumer Keys

### Tier 2: Commerce API v2 (Supplementary)
- **Endpoint:** `https://app.ticketmaster.com/commerce/v2/events/{id}/offers.json?apikey=KEY`
- Would provide detailed offer data (sections, prices, ticket limits)
- **Requires partner-level API access** — returns 401 with free Consumer Keys
- The monitor handles this gracefully (returns empty list, logs it, continues)

### Tier 3: Page Checker (Optional, Disabled by Default)
- Fetches the Ticketmaster event page HTML
- Extracts `__NEXT_DATA__` (Next.js) and JSON-LD structured data
- May be blocked by Ticketmaster's anti-bot systems
- Enable in `config.yaml` with `optional.enable_page_check: true`

## Scoring System

When offers are found, they're scored to prioritize notifications:

| Criteria | Points |
|----------|--------|
| General Admission / GA / Floor | +100 |
| LOGE section | +60 |
| Balcony section | +30 |
| Price under $100 | +50 |
| Quantity limit ≥ 4 | +40 |
| Quantity limit ≥ 2 | +20 |

### Discord Notification Colors
- **Green** (`#00FF00`) — Score ≥ 140: "DROP EVERYTHING" (e.g., GA + under $100)
- **Yellow** (`#FFFF00`) — Score ≥ 60: "Good Option" (e.g., LOGE section)
- **Orange** (`#FF8C00`) — Score ≥ 30: "Available" (e.g., Balcony section)

### Other Notifications
- **Blue** (`#3498DB`) — Status changes and daily heartbeat
- **Red** (`#E74C3C`) — Back to sold out, or monitor errors

## Polling Strategy

Two-tier polling based on time of day (US/Eastern timezone):

| Period | Hours | Interval | Rationale |
|--------|-------|----------|-----------|
| Daytime | 8 AM – 1 AM ET | Every 90 seconds | Active hours, most likely time for listings |
| Overnight | 1 AM – 8 AM ET | Every 5 minutes | Low activity, save API budget |

### API Budget
- **Rate limit:** 5 requests/second, 5,000 requests/day
- Each check cycle makes 2 Discovery API calls (one per event) + 2 Commerce API calls = ~4 calls per cycle
- At 90-second intervals during daytime (17 hours), that's ~2,720 calls/day — well within budget
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
│   ├── models.py                # Dataclasses (EventStatus, Offer, TicketAlert, etc.)
│   ├── notifier.py              # Discord webhook sender with scored embeds
│   ├── page_checker.py          # Optional Tier 3 HTML page scraper
│   ├── scheduler.py             # Main monitoring loop, scoring, adaptive intervals
│   ├── state.py                 # JSON state persistence (tracks seen offers, status)
│   └── ticketmaster.py          # Discovery + Commerce API client, rate limiting
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

1. `monitor.py` runs continuously, polling in a loop with adaptive intervals (90s daytime / 5min overnight)
2. `state.json` is stored on disk — survives restarts, tracks seen offers and last statuses
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
- **`preferences`** — Max price ($175), preferred sections (GA > LOGE > Balcony)
- **`polling`** — Interval timing, backoff settings, timezone
- **`notifications`** — Cooldown between alerts (5 min), score threshold, daily heartbeat hour

## State Management

The monitor persists state to `state.json` (excluded from git via `.gitignore`):

- **Last known status** per event — detects `offsale` → `onsale` transitions
- **Notified offer IDs** — prevents duplicate alerts for the same offers
- **Last notification timestamp** — enforces cooldown between alerts
- **Last heartbeat date** — ensures only one heartbeat per day
- **Last check timestamp** — reported in heartbeat messages

State is saved atomically (write to temp file, then `os.replace`) to prevent corruption. On the VM, `state.json` lives on disk and persists across service restarts automatically.

## Error Handling

- **Network errors** (connection failures, timeouts): Do NOT count toward daily API budget. Monitor retries with increasing backoff (30s increments, capped at 10 min). Logs recovery when network returns.
- **Rate limiting** (HTTP 429): Respects `Retry-After` header. Backs off for the specified duration.
- **Commerce API 401/403**: Expected with free API keys. Gracefully returns empty offer list and continues.
- **Event not found (404)**: Skips that event for the current cycle, continues checking others.
- **Server errors (5xx)**: Exponential backoff with multiplier from config.

## Dependencies

- **requests** — HTTP client for API calls and Discord webhooks
- **pyyaml** — YAML config file parsing
- **python-dateutil** — Timezone handling for polling schedule

All are standard, well-maintained Python packages. No Selenium, Playwright, or browser automation required.

## Limitations

- The **Commerce API** (detailed offer data) requires partner-level API access, which is not available with free Consumer Keys. The monitor works with the Discovery API alone, but can only detect status changes and price range appearances rather than individual ticket listings.
- **Face Value Exchange** listings may appear and disappear quickly. The 90-second polling interval means there could be a window where tickets are posted and sold before the next check.
- The **page checker** (Tier 3) may be blocked by Ticketmaster's anti-bot/anti-scraping systems and is disabled by default.
