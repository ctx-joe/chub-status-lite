# Chub Status Lite

A lightweight Discord bot for monitoring Chub.ai service status with smart outage notifications.

## Features

### Status Monitoring
- **Configurable polling** - Checks Chub.ai status every 60 seconds
- **Visual embed** - Shows last 10 status checks as emoji timeline
- **Persistent history via db** - Survives bot restarts via database
- **Legend:**
  - 🟢 Healthy
  - 🟡 Warning (degraded but operational)
  - 🔴 Degraded/Down

### Outage Notifications
- **Reaction-based subscriptions** - Users react on status embed to subscribe
- **Alert logic** - Only notifies after ~10 minutes of confirmed downtime (2 consecutive red ticks)
- **Recovery notification** - Notifies when service is stable for ~15 minutes (3 consecutive green ticks)
- **Multi-model health logic** - Multiple models grouped into single messages
- **Role-assignment handling** - Configurable role for alert mentions

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show bot commands and information |
| `/uptime [model] [days]` | View uptime statistics |
| `/ping` | Check bot latency |

### Admin Commands (Requires Administrator)

| Command | Description |
|---------|-------------|
| `/setup status #channel` | Set the channel for the status embed |
| `/setup notifications #channel @role [emoji]` | Configure outage alerts |
| `/setup view` | View current configuration |

## Quick Start

1. [Invite the bot](#discord-bot-setup) to your server
2. Run `/setup status #status-channel` to create the status embed
3. Run `/setup notifications #alerts @Outage-Alerts` to enable notifications
4. Users react with 🔔 on the status embed to subscribe to alerts

## Installation

### Prerequisites
- Python 3.10+
- Discord bot token

### Setup

```bash
# Clone the repository
git clone https://github.com/ctx-joe/chub-status-lite.git
cd chub-status-lite

# Install dependencies
pip install -r requirements.txt

# Configure
cp config.yaml.example config.yaml
# Edit config.yaml with your Discord bot token

# Run
python bot.py
```

## Configuration

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN"
  allowed_guilds: []  # Optional: restrict to specific guild IDs

status:
  endpoint: "https://gateway.chub.ai/monitoring/health/public/status"
  poll_interval_seconds: 60
  history_depth: 10

database:
  path: "chub_bot.db"
  retention_days: 30
```

## Discord Bot Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Navigate to **Bot** section and create a bot
4. Enable **Server Members Intent** (required for role management)
5. Copy the bot token to your `config.yaml`
-# There are other steps not mentioned here (perms, etc.)

### Required Bot Permissions
- Send Messages
- Embed Links
- Add Reactions
- Read Message History
- Manage Roles
- Use Slash Commands

### Invite URL
Generate an invite URL with scopes: `bot` and `applications.commands`

## Status Embed Preview

```
╔═══════════════════════════════════════╗
║  Chub.ai Status                       ║
╠═══════════════════════════════════════╣
║  API: 🟢 Healthy                      ║
║                                       ║
║  Models (← older │ newer →)           ║
║  Asha     🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢   ║
║  Soji     🟢🟢🟢🟢🔴🔴🟡🟡🟢🟢   ║
║  Mobile   🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢   ║
║  Mistral  🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢   ║
║  Mixtral  🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢   ║
║  Mythomax 🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢   ║
║                                       ║
║  For more information: chub.ai/status ║
║                                       ║
║  Outage Alerts                        ║
║  React with 🔔 to subscribe.          ║
║  Remove your reaction to unsubscribe. ║
╚═══════════════════════════════════════╝
```

## Notification Logic

### How Alerts Work

The bot tracks consecutive status ticks from the Chub API (updates every ~5 minutes):

| Event | Threshold | Approx Time |
|-------|-----------|-------------|
| **Down Alert** | 2 consecutive 🔴 | ~10 minutes |
| **Recovery Alert** | 3 consecutive 🟢 | ~15 minutes |

### Yellow (Warning) Status

Yellow status is treated as **neutral**:
- Does NOT reset the red counter
- Does NOT count toward recovery

### Example Scenarios

```
Scenario 1: Outage and recovery
🟢 🟢 🔴 🔴 → DOWN ALERT → 🔴 🟢 🟢 🟢 → RECOVERY ALERT

Scenario 2: Yellow event
🟢 🔴 🔴 → DOWN ALERT → 🟡 🟡 🟢 🟢 🟢 → RECOVERY ALERT
       ↑                 ↑
       Alert fires       Yellow doesn't count, need 3 greens

Scenario 3: Brief issue (no alert)
🟢 🟢 🔴 🟢 🟢 → No alert (only 1 red tick, need 2)
```

## Important Notes

### Notification Role Hierarchy
- The bot's role must be **above** the notification role in Discord's role list
- Server Settings → Roles → Drag bot role above the notification role
- Otherwise, the bot cannot assign/remove the role from users

### Reaction Debounce
- Cooldown per user on role changes
- Prevents spam in large servers

### Database
- SQLite database auto-creates on first run
- Migrates schema left in case of updates
- For a fresh start, delete `chub_bot.db`

## Troubleshooting

### Notifications not sending
1. Check `/setup view` - is notification channel/role configured?
2. Check bot has permission to send in the notification channel
3. Check bot's role is above the notification role
4. Check database: `sqlite3 chub_bot.db "SELECT * FROM alert_state;"`

### Role assignment not working
1. Verify bot has "Manage Roles" permission
2. Verify bot's role is higher than the notification role
3. Check logs for "Missing permissions" errors

### Status embed not updating
1. Verify the status message still exists (wasn't deleted)
2. Check logs for API fetch errors
3. Verify endpoint is reachable: `curl https://gateway.chub.ai/monitoring/health/public/status`

## License

MIT

## Credits
ctx-joe
https://chub.ai/users/_joe
Built with Claude