# CLAUDE.md

## Status

This monitor is **paused**. The primary monitor is now `gui-concert-monitor`.

All 4 LaunchAgents were unloaded and moved to `~/Library/LaunchAgents/disabled/` on 2026-03-31:
- `com.rymag.ticket-monitor.plist`
- `com.rymag.ticket-monitor.guardian.plist`
- `com.rymag.ticket-monitor.reloader.plist`
- `com.rymag.ticket-monitor.browser-host.plist`

Do not re-enable or suggest re-enabling unless the user explicitly asks. Do not touch `gui-concert-monitor`.

## To Re-enable

```bash
mv ~/Library/LaunchAgents/disabled/com.rymag.ticket-monitor*.plist ~/Library/LaunchAgents/
for plist in ~/Library/LaunchAgents/com.rymag.ticket-monitor*.plist; do launchctl load "$plist"; done
```
