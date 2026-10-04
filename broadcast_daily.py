"""REE Daily Broadcast - Telegram Electricity Price Notifier.

This script broadcasts the 3-hour minimum and maximum electricity price windows
(PVPC) to all registered Telegram bot users. It is designed to run automatically
every day at 23:05 (via cron or OpenClaw scheduler).

At 23:05, Red Eléctrica de España (REE) has already published the electricity
tariffs for the next day (published daily around 20:15–20:30 CET). Therefore,
when executed in the evening (or by default at 23:05), the script automatically
targets tomorrow's prices so users can plan their energy consumption ahead of time.

Features:
- Queries REE hourly prices using functions from `ree_checkdiario.py`.
- Formats a clean, readable Telegram alert with emojis and timestamps.
- Automatically retrieves the Telegram Bot Token from OpenClaw's configuration
  (~/.openclaw/openclaw.json under account 'ree_bot'), environment variable
  `TELEGRAM_BOT_TOKEN`, or CLI `--token`.
- Manages subscribers in `subscribers.json` and automatically discovers new users
  who initiated contact with the bot via Telegram's `getUpdates` API.
- Supports `--dry-run`, `--chat-id`, `--date`, `--add-subscriber`, and `--list-subscribers`.

How to Add & Authorize a Telegram User:
----------------------------------------
When OpenClaw is configured with "dmPolicy": "pairing" on the bot account:
1. User starts the bot:
   The family member searches for the bot on Telegram (e.g. @YalubaREE_bot) and
   sends /start. The bot will reply with a Pairing Code.
2. Find their User ID and Code:
   Run: openclaw pairing list telegram --account ree_bot
   This outputs the user's name, Telegram User ID, and Pairing Code.
3. Approve their access:
   Run: openclaw pairing approve telegram <CODE> --account ree_bot --notify
4. Register them for the daily broadcast:
   Run: uv run python broadcast_daily.py --add-subscriber <TELEGRAM_USER_ID>
   (Or they will be automatically registered when they send messages to the bot).

Usage Examples:
    # Standard broadcast (e.g. called by cron at 23:05):
    uv run python broadcast_daily.py

    # Dry run preview (prints message and target subscribers without sending):
    uv run python broadcast_daily.py --dry-run

    # Send test broadcast to a specific Telegram chat ID:
    uv run python broadcast_daily.py --chat-id 123456789

    # Add a subscriber manually by Telegram Chat ID:
    uv run python broadcast_daily.py --add-subscriber 123456789

    # List all current registered subscribers:
    uv run python broadcast_daily.py --list-subscribers
"""

import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

import requests

from ree_checkdiario import (
    DEFAULT_PERIOD,
    MADRID_TZ,
    extract_pvpc_values,
    fetch_ree_prices,
    find_optimal_windows,
    get_current_madrid_date,
)

DEFAULT_SUBSCRIBERS_FILE = (
    Path(__file__).parent / "subscribers.json"
)
OPENCLAW_CONFIG_PATH = (
    Path.home() / ".openclaw" / "openclaw.json"
)
DEFAULT_ACCOUNT_ID = "ree_bot"


def load_bot_token(
    token_arg: str | None = None, account_id: str = DEFAULT_ACCOUNT_ID
) -> str:
    """Retrieve the Telegram bot token with fallback priority:

    1. Explicit CLI argument (--token).
    2. Environment variable (TELEGRAM_BOT_TOKEN).
    3. OpenClaw configuration file (~/.openclaw/openclaw.json).
    """
    if token_arg:
        return token_arg

    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if env_token:
        return env_token

    if OPENCLAW_CONFIG_PATH.is_file():
        try:
            with open(OPENCLAW_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            tg_accounts = (
                config.get("channels", {})
                .get("telegram", {})
                .get("accounts", {})
            )
            # Try specific account or fallback to first available
            acc = tg_accounts.get(account_id) or next(
                iter(tg_accounts.values()), {}
            )
            token = acc.get("botToken")
            if token:
                return token
        except Exception as e:
            print(
                f"Warning: Failed to read token from {OPENCLAW_CONFIG_PATH}: {e}",
                file=sys.stderr,
            )

    raise ValueError(
        "Telegram bot token not found. Please provide --token, set TELEGRAM_BOT_TOKEN, "
        f"or configure '{account_id}' in {OPENCLAW_CONFIG_PATH}."
    )


def load_subscribers(file_path: Path) -> dict[str, dict[str, Any]]:
    """Load subscribers dictionary from JSON file."""
    if not file_path.is_file():
        return {}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        subscribers = {}
        if isinstance(data, list):
            # Backward compatibility: list of IDs
            for item in data:
                cid = str(item.get("chat_id") if isinstance(item, dict) else item)
                subscribers[cid] = {"chat_id": cid, "active": True}
        elif isinstance(data, dict):
            # Dict of subscribers
            items = data.get("subscribers", data)
            if isinstance(items, dict):
                for k, v in items.items():
                    subscribers[str(k)] = v if isinstance(v, dict) else {"chat_id": str(k), "active": True}
            elif isinstance(items, list):
                for item in items:
                    cid = str(item.get("chat_id") if isinstance(item, dict) else item)
                    subscribers[cid] = item if isinstance(item, dict) else {"chat_id": cid, "active": True}
        return subscribers
    except Exception as e:
        print(f"Warning: Could not read {file_path}: {e}", file=sys.stderr)
        return {}


def save_subscribers(file_path: Path, subscribers: dict[str, dict[str, Any]]) -> None:
    """Save subscribers dictionary to JSON file."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump({"subscribers": subscribers}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving subscribers to {file_path}: {e}", file=sys.stderr)


def discover_subscribers_from_telegram(
    bot_token: str, existing_subscribers: dict[str, dict[str, Any]]
) -> int:
    """Query Telegram's getUpdates API to automatically register users who messaged the bot."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    new_count = 0
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return 0

        updates = resp.json().get("result", [])
        for update in updates:
            msg = update.get("message") or update.get("channel_post") or {}
            chat = msg.get("chat", {})
            chat_id = chat.get("id")
            if not chat_id:
                continue

            cid_str = str(chat_id)
            if cid_str not in existing_subscribers:
                existing_subscribers[cid_str] = {
                    "chat_id": cid_str,
                    "first_name": chat.get("first_name", ""),
                    "username": chat.get("username", ""),
                    "type": chat.get("type", "private"),
                    "first_seen": datetime.now(MADRID_TZ).isoformat(),
                    "active": True,
                }
                new_count += 1
            else:
                # Re-activate if was marked inactive
                existing_subscribers[cid_str]["active"] = True

    except Exception as e:
        print(f"Notice: Could not poll getUpdates: {e}", file=sys.stderr)

    return new_count


def resolve_broadcast_date(explicit_date: date | None = None) -> tuple[date, str]:
    """Determine whether to fetch tomorrow's or today's prices.

    At 23:05 (and after ~20:15 CET), REE has published tomorrow's prices.
    Returns:
        (target_date, label) where label is e.g. "Mañana" or "Hoy".
    """
    madrid_now = datetime.now(MADRID_TZ)
    today = madrid_now.date()
    tomorrow = today + timedelta(days=1)

    if explicit_date:
        label = "Mañana" if explicit_date == tomorrow else ("Hoy" if explicit_date == today else explicit_date.strftime("%d/%m/%Y"))
        return explicit_date, label

    # If running at night (after 20:00 Madrid time), target tomorrow
    if madrid_now.hour >= 20:
        return tomorrow, "Mañana"
    return today, "Hoy"


def format_broadcast_message(
    target_date: date, date_label: str, window_data: dict, period: int
) -> str:
    """Build a user-friendly Telegram Markdown message."""
    min_w = window_data["min"]
    max_w = window_data["max"]

    # Calculate end hours for clarity
    min_start_hour = int(min_w["time"].split(":")[0])
    min_end_hour = min_start_hour + period
    max_start_hour = int(max_w["time"].split(":")[0])
    max_end_hour = max_start_hour + period

    date_str = target_date.strftime("%d/%m/%Y")

    lines = [
        "⚡ *PRECIOS DE LA ELECTRICIDAD (PVPC)*",
        f"📅 *{date_label}* ({date_str}) · Ventanas de *{period}h*",
        "",
        "🟢 *Periodo MÁS BARATO:*",
        f"• Horario: de *{min_w['time']}* a *{min_end_hour:02d}:00*",
        f"• Precio medio: *{min_w['avg']} €/MWh*",
        "",
        "🔴 *Periodo MÁS CARO:*",
        f"• Horario: de *{max_w['time']}* a *{max_end_hour:02d}:00*",
        f"• Precio medio: *{max_w['avg']} €/MWh*",
        "",
        "💡 _Consulta otros periodos (1-12h) enviando un mensaje al bot._",
    ]
    return "\n".join(lines)


def send_telegram_message(
    bot_token: str, chat_id: str, text: str
) -> tuple[bool, str | None]:
    """Send a message to a Telegram chat using Telegram Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=12)
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            return True, None

        desc = data.get("description", f"HTTP {resp.status_code}")
        # Detect if bot was blocked or chat deleted
        error_code = data.get("error_code")
        if error_code in (403, 400) and ("blocked" in desc.lower() or "chat not found" in desc.lower()):
            return False, "blocked"
        return False, desc
    except Exception as e:
        return False, str(e)


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Broadcast 3h electricity price windows to Telegram users at 23:05."
    )
    parser.add_argument(
        "--period",
        "-p",
        type=int,
        default=DEFAULT_PERIOD,
        help=f"Window period in hours between 1 and 12 (default: {DEFAULT_PERIOD}).",
    )
    parser.add_argument(
        "--date",
        "-d",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Date in YYYY-MM-DD format (defaults to tomorrow if run >=20:00, or today).",
    )
    parser.add_argument(
        "--token",
        "-t",
        type=str,
        default=None,
        help="Telegram Bot Token (defaults to ~/.openclaw/openclaw.json or env TELEGRAM_BOT_TOKEN).",
    )
    parser.add_argument(
        "--account",
        type=str,
        default=DEFAULT_ACCOUNT_ID,
        help=f"OpenClaw Telegram account ID to read token from (default: '{DEFAULT_ACCOUNT_ID}').",
    )
    parser.add_argument(
        "--subscribers",
        "-s",
        type=Path,
        default=DEFAULT_SUBSCRIBERS_FILE,
        help=f"Path to subscribers JSON file (default: {DEFAULT_SUBSCRIBERS_FILE}).",
    )
    parser.add_argument(
        "--chat-id",
        type=str,
        default=None,
        help="Send broadcast only to a single specific chat ID (for manual tests).",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Preview the formatted message and recipients without sending.",
    )
    parser.add_argument(
        "--add-subscriber",
        type=str,
        default=None,
        help="Add a chat ID to the subscribers list and exit.",
    )
    parser.add_argument(
        "--list-subscribers",
        action="store_true",
        help="List all active subscribers and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    subscribers_file: Path = args.subscribers

    # Load subscribers registry
    subscribers = load_subscribers(subscribers_file)

    # Handle --add-subscriber
    if args.add_subscriber:
        cid = str(args.add_subscriber)
        subscribers[cid] = {
            "chat_id": cid,
            "added_manually": True,
            "added_at": datetime.now(MADRID_TZ).isoformat(),
            "active": True,
        }
        save_subscribers(subscribers_file, subscribers)
        print(f"Subscriber {cid} added successfully to {subscribers_file}")
        sys.exit(0)

    # Handle --list-subscribers
    if args.list_subscribers:
        active = [s for s in subscribers.values() if s.get("active", True)]
        print(f"Total subscribers: {len(subscribers)} (Active: {len(active)})")
        for s in active:
            cid = s.get("chat_id")
            uname = f" (@{s['username']})" if s.get("username") else ""
            fname = f" - {s['first_name']}" if s.get("first_name") else ""
            print(f" - {cid}{uname}{fname}")
        sys.exit(0)

    # Resolve Telegram Bot Token
    try:
        bot_token = load_bot_token(args.token, account_id=args.account)
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    # Discover any newly arrived subscribers from getUpdates
    new_found = discover_subscribers_from_telegram(bot_token, subscribers)
    if new_found > 0:
        save_subscribers(subscribers_file, subscribers)
        print(f"Discovered {new_found} new subscriber(s) from Telegram.")

    # Determine target date
    target_date, date_label = resolve_broadcast_date(args.date)

    # Fetch prices for target date
    print(f"Fetching electricity prices for {date_label} ({target_date.isoformat()})...")
    try:
        api_data = fetch_ree_prices(target_date)
        pvpc_values = extract_pvpc_values(api_data)
        window_result = find_optimal_windows(pvpc_values, window_size=args.period)
    except Exception as e:
        # Fallback to today if tomorrow was requested but not yet published
        today = get_current_madrid_date()
        if target_date != today and args.date is None:
            print(f"Warning: Tomorrow prices unavailable ({e}). Falling back to today...", file=sys.stderr)
            target_date = today
            date_label = "Hoy"
            api_data = fetch_ree_prices(target_date)
            pvpc_values = extract_pvpc_values(api_data)
            window_result = find_optimal_windows(pvpc_values, window_size=args.period)
        else:
            print(f"Error fetching REE prices: {e}", file=sys.stderr)
            sys.exit(1)

    # Format message
    message_text = format_broadcast_message(
        target_date=target_date,
        date_label=date_label,
        window_data=window_result,
        period=args.period,
    )

    # Determine recipient list
    if args.chat_id:
        target_chat_ids = [args.chat_id]
    else:
        target_chat_ids = [
            cid for cid, info in subscribers.items() if info.get("active", True)
        ]

    # Handle dry run
    if args.dry_run:
        print("\n--- [DRY RUN PREVIEW] ---")
        print(message_text)
        print("-------------------------")
        print(f"Recipients ({len(target_chat_ids)}): {target_chat_ids}")
        sys.exit(0)

    if not target_chat_ids:
        print("No active subscribers found. Users can interact with the bot to auto-register,")
        print("or you can add a chat ID using: python broadcast_daily.py --add-subscriber <CHAT_ID>")
        sys.exit(0)

    # Send broadcasts
    sent_count = 0
    fail_count = 0
    subscribers_modified = False

    print(f"Broadcasting to {len(target_chat_ids)} subscriber(s)...")
    for cid in target_chat_ids:
        ok, error = send_telegram_message(bot_token, cid, message_text)
        if ok:
            sent_count += 1
            print(f" [OK] Sent to {cid}")
        else:
            fail_count += 1
            print(f" [FAIL] Could not send to {cid}: {error}", file=sys.stderr)
            if error == "blocked" and cid in subscribers:
                subscribers[cid]["active"] = False
                subscribers_modified = True

    if subscribers_modified:
        save_subscribers(subscribers_file, subscribers)

    print(f"Broadcast complete: {sent_count} succeeded, {fail_count} failed.")


if __name__ == "__main__":
    main()
