# Battery optimisation

Mixed-integer linear programming (MILP) model for the optimal operation of a behind-the-meter battery
with PV, built with [Pyomo](https://www.pyomo.org/). The model schedules the battery in 15-minute steps so that
the customer's total electricity bill is as low as possible under the Slovenian network tariff system.

## What the model optimises

The objective minimises the sum of:

- **energy cost** – grid import at day-ahead prices (BSP SouthPool, SI), minus revenue for exported energy,
- **network energy charge** – imported energy in each of the 5 tariff time blocks times the block price,
- **network power charge** – the peak import power in each power block times the block power price,
- **battery degradation cost** – throughput divided by capacity and lifetime cycles, times the battery price.

Main constraints:

- power balance at the meter: battery power + load − (curtailable) PV = grid power,
- state of charge dynamics with charging and discharging efficiency (default 95 % each way), SoC and power limits,
- a binary variable per time step that prevents simultaneous charging and discharging (big-M),
- grid import/export limits and a minimum settlement (contracted) power,
- the state of charge at the end of every optimisation window is fixed (90 % of capacity).

The tariff time blocks (high/low season, working day/weekend and holidays) are generated in
[`research/utils.py`](research/utils.py) from the Slovenian network tariff regulation.

## Repository structure

```
research/
├── pyomo_generalization.py   general model (separate energy and power blocks, settlement power)
├── pv_battery_pyomo.py       first version of the model
├── utils.py                  Slovenian tariff time blocks
├── generate_pv.py            synthetic PV profile with pvlib (clear sky, random clouds, PVWatts)
├── download_prices.py        day-ahead prices from the Energy-Charts API
├── pyomio_testing.ipynb      data preparation, optimisation over weekly windows, plots
└── original_algday1.csv      example result for one day
data/
├── load_sencur.csv           measured household consumption, 15 min (kW)
├── load_besnica.csv          measured household consumption with an existing PV plant, 15 min (kW)
├── pv_data.csv               synthetic PV profile for 1 kWp (generate_pv.py)
└── day_ahead_prices.csv      SI day-ahead prices (EUR/MWh)
```

All timestamps are local time (Europe/Ljubljana) and mark the end of the interval.

## Data

- **Consumption** – smart meter data of two households (active power, 15 min).
- **PV** – `python generate_pv.py` creates a profile for 1 kWp in Ljubljana with [pvlib](https://pvlib-python.readthedocs.io):
  Ineichen clear sky, random daily cloudiness, Erbs decomposition, transposition to a 30° south-facing panel
  and the PVWatts model with cell temperature (~1050 kWh/kWp per year).
  Location, orientation, yield and random seed are command line options (`python generate_pv.py --help`).
- **Prices** – `python download_prices.py --start 2024-01-01` downloads Slovenian day-ahead prices
  from [Energy-Charts](https://energy-charts.info) (CC BY 4.0, Bundesnetzagentur | SMARD.de).

## Running

```bash
pip install -r requirements.txt
# the model uses the GLPK solver: install glpk (e.g. conda install -c conda-forge glpk)
cd research
python generate_pv.py
python download_prices.py 
jupyter notebook pyomio_testing.ipynb
```

The notebook takes the last year for which consumption, PV and prices are available, scales the PV profile
(15 kWp, 1100 full-load hours) and solves the model for consecutive one-week windows (672 time steps).
One week solves in a few seconds.

## License

[Apache 2.0](LICENSE)
