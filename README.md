# ree_python
Tool python to obtain electricity prices in Spain - to be used by an Openclaw agent.

The API of REE (Red Electrica Española) is documented in [https://www.ree.es/es/datos/apidatos](https://www.ree.es/es/datos/apidatos)

## Installation & Environment

This project uses [`uv`](https://docs.astral.sh/uv/) for Python packaging and environment management.

1. Create virtual environment and install dependencies:
   ```bash
   uv venv
   uv sync
   ```

## Usage

Run the daily electricity price analyzer for the current day:
```bash
uv run python ree_checkdiario.py
```

### Options

- **Specify period** (`--period` / `-p`, integer between 1 and 12 hours, default is 3):
  ```bash
  # Check 2-hour windows:
  uv run python ree_checkdiario.py --period 2

  # Check 1-hour windows (single hour cheapest / most expensive):
  uv run python ree_checkdiario.py -p 1
  ```

- **Specify a date** (`--date` / `-d` in `YYYY-MM-DD` format):
  ```bash
  uv run python ree_checkdiario.py --date 2026-10-02
  ```

- **Use a local JSON file** (`--file` / `-f` for offline testing or evaluation):
  ```bash
  uv run python ree_checkdiario.py --file tmp/resp.json --period 4
  ```

### Output Example

```json
{
   "min": {
      "time": "14:00",
      "period": "3h",
      "avg": "137.19"
   },
   "max": {
      "time": "19:00",
      "period": "3h",
      "avg": "359.38"
   }
}
```

---

## Daily Telegram Broadcast (`broadcast_daily.py`)

The script `broadcast_daily.py` sends a daily alert to all bot users informing them of the cheapest and most expensive 3-hour windows for the electricity price (PVPC).

### Date Logic (23:05 Madrid Time)

Because Red Eléctrica de España (REE) publishes the next day's electricity prices daily around 20:15–20:30 CET, when the broadcast runs at **23:05**, it automatically queries **tomorrow's prices** so users can plan their electricity consumption for the day ahead.

### How to Pair and Add Family Members (Telegram Users)

Since the bot is configured with `"dmPolicy": "pairing"`, new users must be authorized before they can converse with the bot.

Follow these steps to grant access:

1. **Family member initiates chat on Telegram**:
   - Search for the bot (e.g. `@Your_bot`) on Telegram.
   - Tap **Start** or send `/start` or `Hola`.
   - The bot will reply with a **Pairing Code** (e.g. `R7F4GXXX`) indicating access requires approval.

2. **View pending pairing requests**:
   ```bash
   openclaw pairing list telegram --account ree_bot
   ```
   This displays the user's name, Telegram User ID (e.g. `711111111`), and the pairing code.

3. **Approve access**:
   ```bash
   openclaw pairing approve telegram <PAIRING_CODE> --account ree_bot --notify
   ```
   *(The `--notify` flag automatically sends a confirmation message to the user on Telegram).*

4. **Add the user to the daily 23:05 broadcast**:
   ```bash
   uv run python broadcast_daily.py --add-subscriber <TELEGRAM_USER_ID>
   ```
   *(New users are also automatically discovered from Telegram updates when they message the bot).*

5. **Verify registered subscribers**:
   ```bash
   uv run python broadcast_daily.py --list-subscribers
   ```


### Bot Token Detection

The bot token is resolved automatically in the following order:
1. `--token <TOKEN>` CLI parameter.
2. `TELEGRAM_BOT_TOKEN` environment variable.
3. OpenClaw configuration file (`~/.openclaw/openclaw.json`) under `channels.telegram.accounts.ree_bot.botToken`.

### Broadcast Commands

```bash
# Standard broadcast (defaults to 3h window and tomorrow's prices after 20:00):
uv run python broadcast_daily.py

# Preview the formatted message and recipient list without sending (dry-run):
uv run python broadcast_daily.py --dry-run

# Send test message only to a specific Telegram chat ID:
uv run python broadcast_daily.py --chat-id <YOUR_CHAT_ID>

# List all registered subscribers:
uv run python broadcast_daily.py --list-subscribers

# Send with custom period (e.g. 2 hours):
uv run python broadcast_daily.py --period 2
```

### Scheduling the Broadcast (at 23:05 Daily)

#### Option A: Via OpenClaw Cron Scheduler
```bash
openclaw cron add \
  --name "daily-ree-broadcast" \
  --cron "5 23 * * *" \
  --tz "Europe/Madrid" \
  --command "/home/uname/.local/bin/uv run --project /home/uname/Documents/repos/ree_python python /home/uname/Documents/repos/ree_python/broadcast_daily.py"
```

#### Option B: Via Standard Linux Crontab (`crontab -e`)
```cron
5 23 * * * /home/uname/.local/bin/uv run --project /home/uname/Documents/repos/ree_python python /home/uname/Documents/repos/ree_python/broadcast_daily.py >> /home/uname/Documents/repos/ree_python/broadcast.log 2>&1
```


