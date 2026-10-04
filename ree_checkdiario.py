"""REE Check Diario - Electricity Price Window Analyzer.

This script queries the Red Eléctrica de España (REE) API (APIDatos) to retrieve
the real-time hourly electricity prices (PVPC - Precio Voluntario al Pequeño Consumidor)
for a full day (by default, the current day in Spain).

It analyzes consecutive sliding windows of N hours (configurable from 1 to 12 hours,
default: 3 hours) across the day to determine:
1. The window with the lowest average price (cheapest period).
2. The window with the highest average price (most expensive period).

The output is printed to standard output in JSON format:
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

Usage:
    # Run for current day with default 3-hour window:
    uv run python ree_checkdiario.py

    # Specify a custom period (e.g., 2 hours, range 1-12):
    uv run python ree_checkdiario.py --period 2

    # Run for a specific date:
    uv run python ree_checkdiario.py --date 2026-10-02 --period 4

    # Run using a local JSON file (offline / testing):
    uv run python ree_checkdiario.py --file tmp/resp.json --period 3
"""

import argparse
from datetime import date, datetime, timedelta
import json
import sys
from zoneinfo import ZoneInfo

import requests

REE_API_BASE_URL = (
    "https://apidatos.ree.es/es/datos/mercados/precios-mercados-tiempo-real"
)
MADRID_TZ = ZoneInfo("Europe/Madrid")
DEFAULT_PERIOD = 3  # Default sliding window period in hours
MIN_PERIOD = 1
MAX_PERIOD = 12



def get_current_madrid_date() -> date:
    """Return the current calendar date in the Europe/Madrid timezone."""
    try:
        return datetime.now(MADRID_TZ).date()
    except Exception:
        # Fallback to local system date if timezone resolution fails
        return date.today()


def fetch_ree_prices(target_date: date) -> dict:
    """Query REE APIDatos for hourly electricity prices for a given date.

    Args:
        target_date: The date for which to fetch prices.

    Returns:
        Parsed JSON response dictionary from REE API.
    """
    next_day = target_date + timedelta(days=1)
    start_str = f"{target_date.isoformat()}T00:00"
    end_str = f"{next_day.isoformat()}T00:00"

    params = {
        "start_date": start_str,
        "end_date": end_str,
        "time_trunc": "hour",
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ree_checkdiario/1.0",
    }

    response = requests.get(
        REE_API_BASE_URL,
        params=params,
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def extract_pvpc_values(data: dict) -> list[dict]:
    """Extract the hourly price values for PVPC from REE API JSON response.

    The PVPC object is located inside the 'included' list.
    """
    included = data.get("included", [])
    if not included:
        raise ValueError("Response does not contain any 'included' data.")

    # Locate the PVPC item by type or title; fallback to the first object
    pvpc_item = next(
        (
            item
            for item in included
            if item.get("type") == "PVPC"
            or item.get("attributes", {}).get("title") == "PVPC"
        ),
        None,
    )

    if pvpc_item is None:
        # Default to the first object as described in API documentation
        pvpc_item = included[0]

    values = pvpc_item.get("attributes", {}).get("values", [])
    if not values:
        raise ValueError("No hourly price values found in PVPC data.")

    return values


def find_optimal_windows(
    values: list[dict], window_size: int = DEFAULT_PERIOD
) -> dict:
    """Calculate sliding window averages of duration `window_size` and find min/max windows.

    Args:
        values: List of dicts, each containing 'datetime' (ISO string) and 'value' (float).
        window_size: Number of consecutive hours per window (default: 3).

    Returns:
        Dictionary containing 'min' and 'max' window summaries.
    """
    if len(values) < window_size:
        raise ValueError(
            f"Not enough data points ({len(values)}) to compute a {window_size}h window."
        )

    windows = []
    # Slide across all consecutive windows of size window_size
    for i in range(len(values) - window_size + 1):
        window_slice = values[i : i + window_size]
        avg_price = sum(item["value"] for item in window_slice) / float(
            window_size
        )

        # Parse start datetime to extract 24h format HH:MM
        raw_datetime = window_slice[0]["datetime"]
        start_dt = datetime.fromisoformat(raw_datetime)
        time_str = start_dt.strftime("%H:%M")

        windows.append(
            {
                "time": time_str,
                "period": f"{window_size}h",
                "avg_value": avg_price,
            }
        )

    # Find the window with minimum average price and maximum average price
    min_window = min(windows, key=lambda w: w["avg_value"])
    max_window = max(windows, key=lambda w: w["avg_value"])

    return {
        "min": {
            "time": min_window["time"],
            "period": min_window["period"],
            "avg": f"{min_window['avg_value']:.2f}",
        },
        "max": {
            "time": max_window["time"],
            "period": max_window["period"],
            "avg": f"{max_window['avg_value']:.2f}",
        },
    }


def validate_period(value_str: str) -> int:
    """Validate that the period is an integer between MIN_PERIOD and MAX_PERIOD."""
    try:
        val = int(value_str)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid integer value for period: '{value_str}'"
        )
    if not (MIN_PERIOD <= val <= MAX_PERIOD):
        raise argparse.ArgumentTypeError(
            f"Period must be between {MIN_PERIOD} and {MAX_PERIOD} hours (got {val})."
        )
    return val


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Calculate min/max average electricity price windows (PVPC) from REE APIDatos."
    )
    parser.add_argument(
        "--period",
        "-p",
        type=validate_period,
        default=DEFAULT_PERIOD,
        help=f"Window period in hours between {MIN_PERIOD} and {MAX_PERIOD} (default: {DEFAULT_PERIOD}).",
    )
    parser.add_argument(
        "--date",
        "-d",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Date to query in YYYY-MM-DD format (defaults to current date in Spain).",
    )
    parser.add_argument(
        "--file",
        "-f",
        type=str,
        default=None,
        help="Path to a local JSON response file (useful for offline testing).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    try:
        if args.file:
            # Load data from local JSON file
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            # Fetch data from REE API for target date (default: today)
            target_date = args.date or get_current_madrid_date()
            data = fetch_ree_prices(target_date)

        # Extract PVPC hourly values and compute min/max windows for specified period
        pvpc_values = extract_pvpc_values(data)
        result = find_optimal_windows(pvpc_values, window_size=args.period)

        # Print the resulting JSON
        print(json.dumps(result, indent=3))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
