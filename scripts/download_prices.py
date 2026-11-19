import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
import pandas as pd

API_URL = "https://api.energy-charts.info/price?bzn={zone}&start={start}&end={end}"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

def fetch_prices(zone: str, start: str, end: str) -> pd.DataFrame:
    """
	Downloads day-ahead prices for one bidding zone from the Energy-Charts API (Fraunhofer ISE),
	retries when the API limits the number of requests (HTTP 429)

	Args:
	----------
		zone: str
            Bidding zone, e.g. SI
		start, end: str
			First and last day, YYYY-MM-DD

	Returns:
	----------
		prices: pd.DataFrame
			Columns date_time (end of the interval, local time) and price (EUR/MWh)
	"""
    url = API_URL.format(zone=zone, start=start, end=end)

    max_retries = 6

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = json.load(response)
            break

        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise

            if attempt == max_retries - 1:
                raise

            retry_after = e.headers.get("Retry-After")

            if retry_after and retry_after.isdigit():
                wait = int(retry_after)
            else:
                wait = min(10 * (2 ** attempt), 120)

            print(f"Rate limited (429). Retrying in {wait}s...")
            time.sleep(wait)

    times = pd.to_datetime(data["unix_seconds"], unit="s", utc=True)

    prices = pd.DataFrame({
        "start": times,
        "price": data["price"]
    }).dropna()

    length = (
        prices["start"]
        .diff()
        .shift(-1)
        .fillna(prices["start"].diff().iloc[-1])
    )

    end_time = (
        (prices["start"] + length)
        .dt.tz_convert("Europe/Ljubljana")
        .dt.tz_localize(None)
    )

    return pd.DataFrame({
        "date_time": end_time,
        "price": prices["price"]
    })


def download_prices(zone: str, start: str, end: str) -> pd.DataFrame:
    """
    Downloads all prices in a single API request.
    """
    print(f"Downloading prices from {start} to {end}")
    return fetch_prices(zone, start, end)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download day-ahead electricity prices (CC BY 4.0, Energy-Charts)")
    parser.add_argument("--zone", default="SI")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    parser.add_argument("--out", default=str(DATA_DIR / "day_ahead_prices.csv"))
    args = parser.parse_args()

    prices = download_prices(args.zone, args.start, args.end)
    prices.to_csv(args.out, index=False)
    print(f"Saved {len(prices)} rows to {args.out}")
