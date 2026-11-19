import time
from pathlib import Path
import numpy as np
import pandas as pd

from .model import solve_pyomo_generalized
from .tariffs import individual_tariff_times

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
WEEK = 96 * 7  # number of 15 min intervals in one optimisation window


def load_inputs(load_file: str = "load_household2.csv", pv_size: float = 15, pv_operating_hours: float = 1100,
                data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """
	Loads consumption, PV and day-ahead prices and aligns them on a 15 min grid (last available year)

	Args:
	----------
		load_file: str
            Consumption file in the data folder (date_time, p in kW)
		pv_size: float
			PV size in kWp, 0 for no PV (load_household1.csv already includes a PV plant)
		pv_operating_hours: float
			Yearly full load hours of the PV plant, the PV profile is scaled to it

	Returns:
	----------
		inputs: pd.DataFrame
			Columns load (kW), pv (kW), price (EUR/MWh) and block (tariff block 1-5), index date_time
	"""
    df_model = pd.read_csv(data_dir / load_file, parse_dates=['date_time']).set_index('date_time')

    pv_data_original = pd.read_csv(data_dir / 'pv_data.csv', sep=';', decimal=',', parse_dates=['date_time'])
    pv_data_original.set_index("date_time", inplace=True)
    pv_data_original = pv_data_original[~pv_data_original.index.duplicated(keep='first')]

    # Day-ahead prices (EUR/MWh), date_time is the end of the interval. Update with scripts/download_prices.py
    prices_original = pd.read_csv(data_dir / 'day_ahead_prices.csv', parse_dates=['date_time'])
    prices_original.set_index('date_time', inplace=True)

    # Determine the common start and end between df_model, pv_data_original and prices_original
    end = min(df_model.index[-1], pv_data_original.index[-1], prices_original.index[-1])
    start = max(end - pd.DateOffset(years=1), df_model.index[0], pv_data_original.index[0])
    df_model = df_model.loc[start:end]

    pv_data_original = pv_data_original.resample('15min').ffill()
    pv_data = pv_data_original.loc[start:end] * pv_size * pv_operating_hours / 1000

    df_model = df_model[~df_model.index.duplicated(keep='first')]
    df_model = df_model.resample('15min').mean()
    # Missing days are filled with the same time one week before
    df_model = df_model.fillna(df_model.shift(WEEK)).ffill()

    df_prices = prices_original[['price']]
    df_prices = df_prices[~df_prices.index.duplicated(keep='first')]
    # Hourly prices are labelled with the end of the hour, so the 15 min intervals take the next value
    df_prices = df_prices.resample('15min').bfill()

    # Align the indices of all dataframes, ensuring they are consistent
    common_index = df_model.index.intersection(pv_data.index).intersection(df_prices.index)
    inputs = pd.DataFrame({
        'load': df_model.loc[common_index, 'p'],
        'pv': pv_data.loc[common_index, 'p'],
        'price': df_prices.loc[common_index, 'price'],
    })
    blocks_5d = individual_tariff_times(np.array(list(inputs.index)))
    inputs['block'] = sum(blocks_5d[i] * (i + 1) for i in range(5))
    return inputs


def run_optimisation(inputs: pd.DataFrame, battery_size: float, battery_power: float, battery_price: float,
                     battery_life_cycles: float, energy_grid_prices: dict, block_power_prices: dict,
                     max_grid_power: float = 70, min_grid_power: float = -10, min_settlement_power: float = 5.,
                     solver: str = 'cbc') -> pd.DataFrame:
    """
	Optimises the battery schedule week by week (all full weeks of the inputs)

	Args:
	----------
		inputs: pd.DataFrame
            Output of load_inputs
		battery_size, battery_power: float
			Battery capacity (kWh) and power (kW)
		battery_price, battery_life_cycles: float
			Battery price (EUR) and number of cycles in its lifetime (degradation cost)
		energy_grid_prices, block_power_prices: dict
			Network tariff prices for energy (EUR/kWh) and power (EUR/kW) per block

	Returns:
	----------
		df_res: pd.DataFrame
			Optimal schedule (power, soc, grid_power, ...), index date_time
	"""
    load_original = list(inputs['load'])
    production_original = list(inputs['pv'])
    energy_prices_original = list(inputs['price'] / 1000)  # EUR/kWh
    times_energy_blocks_original = inputs['block'].values

    df_res = pd.DataFrame()
    for i in range(len(inputs) // WEEK):  # all full weeks
        t_start = time.time()
        start = i * WEEK
        times_energy_blocks = times_energy_blocks_original[start:start + WEEK]
        times_power_blocks = np.ones(WEEK, dtype=int)
        return_dict, block_peaks, block_energies = solve_pyomo_generalized(
            range(WEEK),
            times_energy_blocks,
            times_power_blocks,
            load_original[start:start + WEEK],
            production_original[start:start + WEEK],
            energy_prices_original[start:start + WEEK],
            energy_prices_original[start:start + WEEK],
            battery_power,
            battery_size,
            energy_blocks=list(set(times_energy_blocks)),
            power_blocks=list(set(times_power_blocks)),
            block_power_prices=block_power_prices,
            energy_grid_prices=energy_grid_prices,
            max_grid_power=max_grid_power,
            min_grid_power=min_grid_power,
            battery_price=battery_price,
            battery_life_cycles=battery_life_cycles,
            soc_init=battery_size * 0.9,
            min_settlement_power=min_settlement_power,
            solver=solver)
        if return_dict is None:
            raise RuntimeError(f"Optimisation failed for the week starting {inputs.index[start]}")
        df_res = pd.concat([df_res, pd.DataFrame(return_dict)], axis=0)
        print(f"Week {inputs.index[start]:%Y-%m-%d} took {time.time() - t_start:.2f} seconds")

    df_res.index = inputs.index[:len(df_res)]
    return df_res


def calculate_bill(grid_power: pd.Series, price: pd.Series, block: pd.Series, energy_grid_prices: dict,
                   block_power_prices: dict, min_settlement_power: float = 5.) -> pd.DataFrame:
    """
	Monthly electricity bill with the same cost terms as the optimisation (energy, network energy, network power)

	The power charge is the peak import of every month, but at least min_settlement_power, times the power price
	of block 1. For months that are only partly in the data it is reduced by the share of days covered.

	Args:
	----------
		grid_power: pd.Series
            Power at the meter in kW, positive is import, index date_time
		price: pd.Series
			Day-ahead price (EUR/MWh), export is sold at the same price
		block: pd.Series
			Tariff block of every interval

	Returns:
	----------
		bill: pd.DataFrame
			Costs in EUR for every month, the sum gives the bill of the whole period
	"""
    dt = 0.25
    grid_import = grid_power.clip(lower=0)
    grid_export = (-grid_power).clip(lower=0)
    month = grid_power.index.to_period('M')

    energy = ((grid_import - grid_export) * price / 1000 * dt).groupby(month).sum()
    network_energy = (grid_import * dt * block.map(energy_grid_prices)).groupby(month).sum()

    # Power charge per tariff block: monthly peak in each block (at least min_settlement_power) times its price
    block_peaks = grid_import.groupby([month, block]).max().clip(lower=min_settlement_power)
    block_charge = block_peaks * block_peaks.index.get_level_values(1).map(block_power_prices)
    days_covered = grid_power.groupby(month).size() / 96
    share_of_month = (days_covered / days_covered.index.days_in_month).clip(upper=1)
    network_power = block_charge.groupby(level=0).sum() * share_of_month

    bill = pd.DataFrame({
        'energy': energy,
        'network_energy': network_energy,
        'network_power': network_power,
    })
    bill['total'] = bill.sum(axis=1)
    return bill
