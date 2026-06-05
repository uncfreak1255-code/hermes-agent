# Sawyer Slack Front Door Completion Audit

Date: 2026-06-04

## Goal

Finish the Sawyer Slack front-door hardening lane with live proof:

- launchd service loaded and running
- Slack connected
- operator receipt and front-door health receipt green
- Slack adapter hardened with a global Bolt error handler
- assistant threads get suggested prompts
- public channel replies stay concise by default
- Slack shortcuts route work into Hermes DM
- inactive legacy platform surfaces stay hidden by default

## Code Changes

Repo:

- `gateway/platforms/slack.py`
  - global Bolt `app.error(...)`
  - assistant suggested prompts
  - Slack shortcuts
  - concise public-channel guidance prompt
- `hermes_cli/slack_cli.py`
  - Slack manifest shortcuts
- `gateway/channel_directory.py`
  - default display filters to active platforms
- `gateway/config.py`
  - plugin platform auto-enable now respects explicit `enabled: false`

Profile:

- `config.yaml`
  - `discord.enabled: false`
- `scripts/operator_status_receipt.py`
  - machine-readable front-door health fields
  - focused JSON/front-door output
- `bin/hermes-slack-front-door-health`
  - Slack front-door health wrapper
- `docs/RUNBOOK.md`
  - daily/restart checks updated

## Proof Gate

Use a post-start window and verify:

```bash
HERMES_HOME=/Users/sawbeck/.hermes/profiles/sawyer sawyer gateway restart
sleep 8
SINCE=$(date '+%Y-%m-%d %H:%M:%S')
sleep 5
/Users/sawbeck/.hermes/profiles/sawyer/bin/hermes-slack-front-door-health --since "$SINCE"
```

Healthy means:

- `service_loaded: true`
- `service_state: "running"`
- `slack_configured: true`
- `slack_connected: true`
- `stdout_issue_window_clean: true`
- `stderr_issue_window_clean: true`
- `healthy: true`
