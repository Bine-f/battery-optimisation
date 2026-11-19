import numpy as np
import pandas as pd
import holidays

# Tariff time blocks of the Slovenian network tariff for every hour of a working day in the high season
# (November - February). In the low season and on weekends/holidays the block number increases by one,
# on weekends/holidays in the low season by two.
# https://www.uradni-list.si/glasilo-uradni-list-rs/vsebina/2022-01-3624
HIGH_SEASON_WORKING_DAY_BLOCKS = np.array([3] * 6 + [2] + [1] * 7 + [2] * 2 + [1] * 4 + [2] * 2 + [3] * 2)


def _interval_start(dates: np.array) -> pd.DatetimeIndex:
    """
	Timestamps in the data mark the end of the 15 min interval, the start of the interval defines its tariff.
	"""
    return pd.DatetimeIndex(dates) - pd.Timedelta(minutes=15)


def _is_day_off(start: pd.DatetimeIndex) -> np.array:
    """
	True for weekends and Slovenian public holidays.
	"""
    si_holidays = holidays.SI(years=range(start.year.min(), start.year.max() + 1))
    is_holiday = np.array([day in si_holidays for day in start.date])
    return (start.weekday > 4) | is_holiday


def individual_tariff_times(dates: np.array) -> np.array:
    """
	Generates tariff masks for the given dates

    Takes an array of dates and returns a matrix of 5 tariff masks from block 1 to 5.
    Each mask is a vector of 1 and 0. 1 means that the time datetime falls into the block.

	Args:
	----------
		dates: np.array
            Array of dates (end of 15 min intervals)

	Returns:
	----------
		tariff_mask: np.array
			Tariff mask of shape (5, len(dates))
	"""
    start = _interval_start(dates)
    is_low_season = (start.month > 2) & (start.month < 11)  # March - October
    block = HIGH_SEASON_WORKING_DAY_BLOCKS[start.hour] + is_low_season + _is_day_off(start)

    tariff_mask = np.stack([(block == b).astype(int) for b in range(1, 6)])
    return tariff_mask


def construct_high_tariff_time_mask(dates: np.array) -> np.array:
    """
	Function returns a mask of 1 and 0, 1 means that it is within high tariff time.
	High tariff time is from 6:00 to 22:00 except on holidays and weekends.

	Args:
	----------
		dates: np.array
            Array of dates (end of 15 min intervals)

	Returns:
	----------
		ht_mask: np.array
			Mask of 1 and 0 of the same length as dates
	"""
    start = _interval_start(dates)
    is_day_time = (start.hour >= 6) & (start.hour < 22)
    ht_mask = (is_day_time & ~_is_day_off(start)).astype(int)
    return ht_mask
