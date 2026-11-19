import argparse
import json
import urllib.request
import pandas as pd

API_URL = "https://api.energy-charts.info/price?bzn={zone}&start={start}&end={end}"


def fetch_prices(zone: str, start: str, end: str) -> pd.DataFrame:
    """
	Downloads day-ahead prices for one bidding zone from the Energy-Charts API (Fraunhofer ISE)

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
    with urllib.request.urlopen(url, timeout=60) as response:
        data = json.load(response)

    times = pd.to_datetime(data["unix_seconds"], unit="s", utc=True)
    prices = pd.DataFrame({"start": times, "price": data["price"]}).dropna()
    # Interval length: 1 h before October 2025, 15 min after (15 min day-ahead market)
    length = prices["start"].diff().shift(-1).fillna(prices["start"].diff().iloc[-1])
    end_time = (prices["start"] + length).dt.tz_convert("Europe/Ljubljana").dt.tz_localize(None)
    return pd.DataFrame({"date_time": end_time, "price": prices["price"]})


def download_prices(zone: str, start: str, end: str) -> pd.DataFrame:
    """
	Downloads the prices month by month to keep the requests small
	"""
    months = pd.date_range(pd.Timestamp(start).replace(day=1), end, freq="MS")
    frames = []
    for month in months:
        month_end = min(month + pd.offsets.MonthEnd(0), pd.Timestamp(end))
        print(f"Downloading {month:%Y-%m}")
        frames.append(fetch_prices(zone, f"{max(month, pd.Timestamp(start)):%Y-%m-%d}", f"{month_end:%Y-%m-%d}"))
    prices = pd.concat(frames).drop_duplicates("date_time").sort_values("date_time")
    return prices


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download day-ahead electricity prices (CC BY 4.0, Energy-Charts)")
    parser.add_argument("--zone", default="SI")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    parser.add_argument("--out", default="../data/day_ahead_prices.csv")
    args = parser.parse_args()

    prices = download_prices(args.zone, args.start, args.end)
    prices.to_csv(args.out, index=False)
    print(f"Saved {len(prices)} rows to {args.out}")
