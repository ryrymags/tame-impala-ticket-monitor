# Tame Impala Face Value Exchange Monitor

> **STATUS: PAUSED** — Migrated to `gui-concert-monitor` as the primary monitor. All LaunchAgents for this version have been unloaded and moved to `~/Library/LaunchAgents/disabled/`. Do not re-enable without disabling the GUI version first to avoid duplicate webhook notifications.

Browser-first monitor for Ticketmaster Face Value Exchange inventory on:

- July 28, 2026 (TD Garden, Boston)
- July 29, 2026 (TD Garden, Boston)

This rebuild uses a persisted authenticated browser session (Playwright) as the primary signal source.

Session persistence is best-effort. You can run with:
- `browser.session_mode: storage_state` (legacy `secrets/tm_storage_state.json`)
- `browser.session_mode: persistent_profile` (legacy `secrets/tm_profile`)
- `browser.session_mode: cdp_attach` (recommended with dedicated real Google Chrome profile)

Ticketmaster can still expire/invalidate sessions or present anti-bot challenges.

## What Changed

- Primary detection now comes from real page/session signals (DOM + network responses).
- Alerts are deduped by availability signature with cooldown.
- Explicit outage detection for blocked/challenge/blind checks.
- Critical outage alerts + recovery alerts so failures are never silent.

## Quick Start

1. Install dependencies:

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

Install Google Chrome if using `cdp_attach` mode:

```bash
open "https://www.google.com/chrome/"
```

2. Configure:

```bash
cp config.example.yaml config.yaml
# edit config.yaml with Discord webhook
```

3. Bootstrap Ticketmaster session (local, headed):

```bash
python3 monitor.py --bootstrap-session
```

In `storage_state` mode this creates `secrets/tm_storage_state.json` with `0600` permissions.
In `persistent_profile` mode this initializes your dedicated profile directory (default `secrets/tm_profile`).
In `cdp_attach` mode this opens the event page in your dedicated Chrome host profile for manual login.

4. Validate setup:

```bash
python3 monitor.py --doctor
```

5. Run monitor:

```bash
python3 monitor.py
```

## Run 24/7 On Mac (Recommended for your setup)

If Ticketmaster blocks cloud VM IPs, run from your Mac directly:

```bash
cd /Users/rymag/code/tame-impala-ticket-monitor-main
bash deploy/setup_macos.sh
```

That script will:
- enforce Python 3.11+ and rebuild `venv` when needed
- install dependencies + Playwright Chromium
- validate config/session with `--doctor-lite`
- install three `launchd` agents:
- `com.rymag.ticket-monitor` (main monitor)
- `com.rymag.ticket-monitor.guardian` (watchdog auto-fix)
- `com.rymag.ticket-monitor.reloader` (local code-change restart)
- install Desktop one-click shortcuts:
- `Ticket Monitor Status.command`
- `Ticket Monitor Verify.command`
- `Ticket Monitor Fix.command`
- `Ticket Monitor Restart.command`
- `Ticket Monitor Reauth.command`
- `Ticket Monitor Logs.command`

### Prevent Sleep Interruptions

Run once on your Mac:

```bash
sudo pmset -a sleep 0 disksleep 0 displaysleep 10
sudo pmset -a tcpkeepalive 1 powernap 0
```

Check current settings:

```bash
pmset -g
```

### launchd Commands

```bash
launchctl list | rg ticket-monitor
launchctl kickstart -k gui/$(id -u)/com.rymag.ticket-monitor
launchctl kickstart -k gui/$(id -u)/com.rymag.ticket-monitor.guardian
launchctl kickstart -k gui/$(id -u)/com.rymag.ticket-monitor.reloader
```

### Mac Logs

```bash
tail -f logs/launchd.out.log
tail -f logs/launchd.err.log
tail -f logs/guardian.log
tail -f logs/reloader.log
```

## Commands

- `python3 monitor.py --test` basic config/session/Discord checks
- `python3 monitor.py --test-ticket-alert` synthetic ticket alert (validates @mention path)
- `python3 monitor.py --test-ticket-alert-matrix` send 3 synthetic ticket alerts (LOGE bingo, budget bingo, non-bingo)
- `python3 monitor.py --doctor` full health check (session + probe + Discord)
- `python3 monitor.py --doctor-lite` quick local health check (no Discord webhook check)
- `python3 monitor.py --health-json` machine-readable monitor health snapshot
- `python3 monitor.py --bootstrap-session` one-time manual Ticketmaster login
- `python3 monitor.py --restart-browser` request browser recycle without full process restart
- `python3 monitor.py --version` runtime/build info
- `python3 monitor.py --once` run one cycle and exit
- `python3 monitor.py --verbose` enable debug logs

Control shortcuts:

- `scripts/monitorctl.sh status`
- `scripts/monitorctl.sh verify` one-command service + health + auto-heal verification
- `scripts/monitorctl.sh verify-webhook` verify + run full doctor + send 3 sample ticket alerts
- `scripts/monitorctl.sh fix`
- `scripts/monitorctl.sh restart`
- `scripts/monitorctl.sh doctor`
- `scripts/monitorctl.sh reauth` interactive one-command re-login flow
- `scripts/monitorctl.sh logs`

Refresh Desktop shortcuts anytime:

```bash
scripts/install_desktop_shortcuts.sh
```

## Config Highlights

See `config.example.yaml`. New key sections:

- `browser.storage_state_path`
- `browser.session_mode` (`storage_state`, `persistent_profile`, or `cdp_attach`)
- `browser.user_data_dir` (dedicated profile path for persistent mode)
- `browser.channel` (default `chrome`)
- `browser.cdp_endpoint_url` / `browser.cdp_connect_timeout_seconds`
- `browser.reuse_event_tabs`
- `browser.poll_min_seconds` / `browser.poll_max_seconds`
- `browser.poll_interval_seconds` / `browser.poll_jitter_seconds` (legacy fallback)
- `browser.challenge_threshold`
- `browser.challenge_retry_seconds`
- `browser_host.*` (dedicated Chrome host process configuration)
- `alerts.ticket_cooldown_seconds`
- `alerts.operational_heartbeat_hours`
- `alerts.event_check_stale_seconds` (alert if any configured event stops getting checks)
- `alerts.operational_state_cooldown_seconds` (dedupe repeated outage/auth incidents)
- `self_heal.*`
- `auth.*` (optional Keychain-backed session auto re-login)
- `watchdog.*`
- `updates.*`

## Recommended: Google Chrome CDP Mode

For best re-auth durability on macOS, use a dedicated real Chrome host process and attach via CDP:

```yaml
browser:
  session_mode: "cdp_attach"
  cdp_endpoint_url: "http://127.0.0.1:9222"
  poll_min_seconds: 45
  poll_max_seconds: 60

browser_host:
  enabled: true
  chrome_executable_path: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  user_data_dir: "secrets/tm_chrome_profile"
  remote_debugging_port: 9222

auth:
  auto_login_enabled: false
```

Notes:
- Keep profile dedicated to this monitor (do not reuse your daily browsing profile).
- First run `scripts/monitorctl.sh reauth` to initialize/login once in the same dedicated profile.
- CAPTCHA/anti-bot pages may still require manual intervention.
- `scripts/monitorctl.sh verify` now fails fast with explicit CDP host reasons (`browser_host_running=false`, `cdp_connected=false`) instead of looping browser restarts.

Manual reauth runbook in CDP mode:

1. `scripts/browser_host.sh status --config config.yaml`
2. `scripts/monitorctl.sh reauth`
3. `scripts/monitorctl.sh verify`

## Optional: Auto Re-Login (macOS Keychain)

If your Ticketmaster session expires while you're away, you can enable unattended re-login auto-fix.

1. Save Ticketmaster email in Keychain:

```bash
security add-generic-password -U -s "tame-impala-ticket-monitor" -a "ticketmaster-email" -w "YOUR_EMAIL"
```

2. Save Ticketmaster password in Keychain:

```bash
security add-generic-password -U -s "tame-impala-ticket-monitor" -a "ticketmaster-password" -w "YOUR_PASSWORD"
```

3. Enable in `config.yaml`:

```yaml
auth:
  auto_login_enabled: true
  keychain_service: "tame-impala-ticket-monitor"
  keychain_email_account: "ticketmaster-email"
  keychain_password_account: "ticketmaster-password"
  max_auto_login_attempts_per_hour: 3
  auto_login_cooldown_seconds: 1800
```

4. Validate setup:

```bash
python3 monitor.py --doctor-lite --config config.yaml
python3 monitor.py --doctor --config config.yaml
```

## Operational Model

- Normal loop: every 45-60s (recommended) with randomized cadence.
- Event checks are staggered by 6s by default.
- Per-event staleness guard alerts if one configured event stops being checked.
- Blocked/challenge/blind checks are tracked per event.
- At threshold breach, monitor enters outage state and retries at 60s until recovery.
- Repeated browser failures trigger staged self-healing:
- browser context recycle first
- process restart request if browser keeps failing
- external guardian watchdog handles stale/non-running process recovery
- local code/config updates trigger automatic safe restart after `--doctor-lite` preflight

## If Away From Mac

- Keep Mac power settings from the setup section to avoid sleep interruptions.
- Guardian will automatically kickstart the monitor when health goes stale.
- Reloader will auto-restart monitor on local code/config changes.
- If `auth.auto_login_enabled: true` and Keychain credentials are configured, monitor will attempt bounded unattended re-login.
- If CAPTCHA/anti-bot challenge appears, auto-login will back off and alert; manual recovery is still:
- run `scripts/monitorctl.sh reauth`

## Testing

```bash
python3 -m pytest -q
```

The suite includes tests for:

- Browser probe available/blocked/challenge behavior
- Detector dedupe + cooldown logic
- Scheduler outage/recovery/backoff behavior
- State migration compatibility
