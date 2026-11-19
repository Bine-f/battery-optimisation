import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import pvlib

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def cloud_factor(times: pd.DatetimeIndex, rng: np.random.Generator) -> np.array:
    """
	Random cloudiness: one factor per day (more clouds in winter) and short term fluctuations

	Args:
	----------
		times: pd.DatetimeIndex
            Times
		rng: np.random.Generator
			Random generator

	Returns:
	----------
		factor: np.array
			Clear sky index between 0 and 1 that multiplies the clear sky global irradiance
	"""
    days = times.normalize().unique()
    # Mean clear sky index is about 0.5 in winter and 0.85 in summer
    mean = 0.675 - 0.175 * np.cos(2 * np.pi * (days.dayofyear.values - 15) / 365)
    daily = pd.Series(rng.beta(4 * mean, 4 * (1 - mean)), index=days)
    factor = daily.reindex(times.normalize()).values

    # Fluctuations of passing clouds are larger on partly cloudy days
    noise = rng.normal(0, 1, len(times))
    noise = pd.Series(noise).rolling(4, min_periods=1).mean().values
    factor = factor * (1 + 0.5 * (1 - factor) * noise)
    return np.clip(factor, 0.05, 1)


def generate_pv(start: str, end: str, latitude: float = 46.05, longitude: float = 14.5, altitude: float = 300,
                tilt: float = 30, panel_azimuth: float = 180, losses: float = 0.14, annual_yield: float = None,
                seed: int = 0) -> pd.DataFrame:
    """
	Generates a synthetic 15 min PV production profile for 1 kWp with pvlib

	Clear sky irradiance (Ineichen) is reduced by random clouds, split into direct and diffuse part (Erbs),
	transposed to the tilted panel and converted to DC power with the PVWatts model and cell temperature.

	Args:
	----------
		start, end: str
            First and last timestamp (local time, Europe/Ljubljana)
		latitude, longitude, altitude: float
			Location (default Ljubljana)
		tilt, panel_azimuth: float
			Panel tilt and azimuth in degrees (180 = south)
		losses: float
			System losses (inverter, cables, soiling)
		annual_yield: float
			If given, the profile is scaled to this yearly production in kWh per kWp
		seed: int
			Random seed

	Returns:
	----------
		pv_data: pd.DataFrame
			Columns p (kW per kWp, average power of the 15 min interval) and date_time (end of the interval)
	"""
    rng = np.random.default_rng(seed)
    times = pd.date_range(start, end, freq="15min", tz="Europe/Ljubljana")
    # The power of the interval is evaluated in its middle
    middle = times - pd.Timedelta(minutes=7.5)

    location = pvlib.location.Location(latitude, longitude, tz="Europe/Ljubljana", altitude=altitude)
    solar_position = location.get_solarposition(middle)
    clear_sky = location.get_clearsky(middle, model="ineichen", solar_position=solar_position)

    ghi = clear_sky["ghi"] * cloud_factor(times, rng)
    split = pvlib.irradiance.erbs(ghi, solar_position["zenith"], middle)
    poa = pvlib.irradiance.get_total_irradiance(tilt, panel_azimuth, solar_position["apparent_zenith"],
                                                solar_position["azimuth"], split["dni"], ghi, split["dhi"])

    # Simple seasonal air temperature for the cell temperature
    temp_air = 11 - 10 * np.cos(2 * np.pi * (middle.dayofyear.values - 15) / 365)
    temp_cell = pvlib.temperature.faiman(poa["poa_global"], temp_air)
    power = pvlib.pvsystem.pvwatts_dc(poa["poa_global"], temp_cell, pdc0=1, gamma_pdc=-0.004)
    power = (power * (1 - losses)).fillna(0).clip(lower=0).values

    if annual_yield is not None:
        years = (times[-1] - times[0]) / pd.Timedelta(days=365)
        power = power * annual_yield * years / (power.sum() / 4)

    return pd.DataFrame({"p": power.round(4), "date_time": times.tz_localize(None)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a synthetic PV production profile for 1 kWp (pvlib)")
    parser.add_argument("--start", default="2024-01-01 00:15")
    parser.add_argument("--end", default="2026-12-31 23:45")
    parser.add_argument("--latitude", type=float, default=46.05)
    parser.add_argument("--longitude", type=float, default=14.5)
    parser.add_argument("--altitude", type=float, default=300)
    parser.add_argument("--tilt", type=float, default=30)
    parser.add_argument("--azimuth", type=float, default=180, help="panel azimuth, 180 = south")
    parser.add_argument("--losses", type=float, default=0.14)
    parser.add_argument("--annual-yield", type=float, default=None, help="scale to kWh per kWp and year")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default=str(DATA_DIR / "pv_data.csv"))
    args = parser.parse_args()

    pv_data = generate_pv(args.start, args.end, args.latitude, args.longitude, args.altitude, args.tilt,
                          args.azimuth, args.losses, args.annual_yield, args.seed)
    # Same format as the measured PV data used before (semicolon separated, decimal comma)
    pv_data.to_csv(args.out, sep=";", decimal=",", index=False)
    print(f"Saved {len(pv_data)} rows to {args.out}")
