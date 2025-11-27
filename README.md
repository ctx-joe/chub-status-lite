# Chub Status Bot Lite

A lightweight Discord bot for monitoring Chub.ai service status.

## Features

- **Real-time status monitoring** - Polls Chub.ai status endpoint every 10 seconds
- **Visual status history** - Shows last 50 minutes of status as emoji dots
- **Uptime statistics** - Track model uptime over time
- **Single embed** - Continuously updated, no message spam

## Commands

| Command | Description |
|---------|-------------|
| `/setup status #channel` | Configure status monitoring channel (Admin) |
| `/setup view` | View current configuration (Admin) |
| `/uptime [model] [days]` | View uptime statistics |
| `/ping` | Check bot latency |

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/chub-status-lite.git
cd chub-status-lite
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure:
```bash
cp config.yaml.example config.yaml
# Edit config.yaml with your Discord bot token
```

4. Run:
```bash
python bot.py
```

## Configuration

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN"
  allowed_guilds: []  # Optional: restrict to specific guilds

status:
  endpoint: "https://gateway.chub.ai/monitoring/health/public/status"
  poll_interval_seconds: 10
  history_depth: 10  # 10 snapshots = 50 minutes

database:
  path: "chub_bot.db"
  retention_days: 30
```

## Discord Bot Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Go to Bot section and create a bot
4. Copy the bot token to your config.yaml
5. Generate invite URL with permissions:
   - Send Messages
   - Embed Links
   - Use Slash Commands

Invite URL scopes: `bot` and `applications.commands`

## Status Embed

```
╔═══════════════════════════════════════╗
║  Chub.ai Status                       ║
║  Wednesday, November 26, 2025 10:15PM ║
╠═══════════════════════════════════════╣
║  API: 🟢 Healthy                      ║
║                                       ║
║  Models (← older │ newer →)           ║
║  Asha     🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢   ║
║  Soji     🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢   ║
║  Mobile   🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢   ║
║  Mistral  🟢🟢🟠🟢🟢🟢🟢🟢🟢🟢   ║
║  Mixtral  🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢   ║
║  Mythomax 🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢   ║
║                                       ║
║  For more information: chub.ai/status ║
║  Last update from Chub (5 min)        ║
╚═══════════════════════════════════════╝
```

## License

MIT
