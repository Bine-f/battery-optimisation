import pyomo.environ as pyo


def solve_pyomo_generalized(times,
                            times_energy_blocks,
                            times_power_blocks,
                            load,
                            production,
                            energy_buy_prices,
                            energy_sell_prices,
                            max_battery_power,
                            C_battery,
                            energy_blocks=[1, 2, 3, 4, 5],
                            power_blocks=[1, 2, 3, 4, 5],
                            energy_grid_prices={
                                1: 0.1,
                                2: 0.2,
                                3: 0.3,
                                4: 0.4,
                                5: 0.5
                            },
                            block_power_prices={
                                1: 0.5,
                                2: 0.5,
                                3: 0.5,
                                4: 0.5,
                                5: 0.5
                            },
                            dt=0.25,
                            max_grid_power=None,
                            min_grid_power=None,
                            soc_init=0,
                            battery_life_cycles=500,
                            battery_price=1000,
                            min_settlement_power = 0.,
                            efficiency=0.95,
                            solver='cbc'):

    model = pyo.ConcreteModel()
    pv_times = [t for t in times if production[t] > 0]
    T = times
    B_E = energy_blocks
    B_P = power_blocks
    model.T = pyo.Set(initialize=T)
    model.B_E = pyo.Set(initialize=B_E)
    model.B_P = pyo.Set(initialize=B_P)
    model.pv_T = pyo.Set(initialize=pv_times)

    tbe_map = {t: times_energy_blocks[i] for i, t in enumerate(times)}
    tbp_map = {t: times_power_blocks[i] for i, t in enumerate(times)}
    model.tbe = pyo.Param(model.T, initialize=tbe_map)
    model.tbp = pyo.Param(model.T, initialize=tbp_map)

    # Variables
    model.power = pyo.Var(model.T,
                          bounds=(-max_battery_power, max_battery_power))
    model.soc = pyo.Var(model.T, bounds=(0, C_battery))
    if max_grid_power is None:
        # inf
        max_grid_power = pyo.infinity
    if min_grid_power is None:
        # -inf
        min_grid_power = -pyo.infinity
    model.grid_power = pyo.Var(model.T,
                               bounds=(min_grid_power, max_grid_power))
    model.charging_power = pyo.Var(model.T, bounds=(0, max_battery_power))
    model.discharging_power = pyo.Var(model.T, bounds=(0, max_battery_power))
    model.grid_import = pyo.Var(model.T, bounds=(0, None))
    # Binary variable to indicate if PV is active
    model.block_peaks = pyo.Var(model.B_P, bounds=(0, None))
    model.block_energy = pyo.Var(model.B_E)
    model.active_pv = pyo.Var(
        model.pv_T,
        bounds=(0,
                1))  # If binary, solver is not able to find optimal solution

    # Constraints
    def power_balance_rule(model, t):
        pv_term = production[t] * model.active_pv[t] if t in model.pv_T else 0

        return model.power[t] + load[t] - pv_term == model.grid_power[t]

    model.power_balance = pyo.Constraint(model.T, rule=power_balance_rule)

    # Energy stored in the battery: charging losses and discharging losses (one-way efficiency)
    def soc_change(model, t):
        return (efficiency * model.charging_power[t] -
                model.discharging_power[t] / efficiency) * dt

    def soc_balance_rule(model, t):
        if t == times[0]:
            return model.soc[t] == soc_init + soc_change(model, t)
        else:
            return model.soc[t] == model.soc[t - 1] + soc_change(model, t)

    model.soc_balance = pyo.Constraint(model.T, rule=soc_balance_rule)

    def charge_discharge_rule(model, t):
        return model.charging_power[t] - model.discharging_power[
            t] == model.power[t]

    model.charge_discharge = pyo.Constraint(model.T,
                                            rule=charge_discharge_rule)

    model.charge_mode = pyo.Var(
        model.T, bounds=(0, 1),
        within=pyo.Binary)  # Binary variable to indicate charge/discharge mode
    bigM = max_battery_power

    def charge_limit(model, t):
        return model.charging_power[t] <= bigM * model.charge_mode[t]

    model.charge_limit = pyo.Constraint(model.T, rule=charge_limit)

    def discharge_limit(model, t):
        return model.discharging_power[t] <= bigM * (1 - model.charge_mode[t])

    model.discharge_limit = pyo.Constraint(model.T, rule=discharge_limit)

    def grid_import_def(model, t):
        return model.grid_import[t] >= model.grid_power[t]

    model.grid_import_lb = pyo.Constraint(model.T, rule=grid_import_def)

    def grid_import_zero_floor(model, t):
        return model.grid_import[t] >= 0

    model.grid_import_nonneg = pyo.Constraint(model.T,
                                              rule=grid_import_zero_floor)


    def block_energy_sum(model, b):
        return model.block_energy[b] == sum(model.grid_import[t] * dt
                                            for t in model.T
                                            if model.tbe[t] == b)

    model.block_energy_sum = pyo.Constraint(model.B_E, rule=block_energy_sum)

    def full_battery_at_end(model):
        return model.soc[times[-1]] == C_battery * 0.9

    model.full_battery_at_end = pyo.Constraint(rule=full_battery_at_end)

    model.block_peak_max = pyo.ConstraintList()
    for b in power_blocks:
        for t in times:
            if tbp_map[t] == b:
                model.block_peak_max.add(
                    model.block_peaks[b] >= model.grid_import[t])
    model.block_peak_min = pyo.ConstraintList()
    for b in power_blocks:
        model.block_peak_min.add(
            model.block_peaks[b] >= min_settlement_power)  # Add minimum settlement power constraint

    # Objective function
    block_energy_cost = sum(model.block_energy[b] * energy_grid_prices[b]
                            for b in B_E)
    block_peak_cost = sum(model.block_peaks[b] * block_power_prices[b]
                          for b in B_P)
    energy_buy_cost = sum(model.grid_import[t] * energy_buy_prices[t] * dt
                          for t in T)
    energy_sell_cost = sum((model.grid_import[t] - model.grid_power[t]) *
                           energy_sell_prices[t] * dt for t in T)
    battery_cost = (
        (sum(model.charging_power[t] * dt
             for t in T) / 2 + sum(model.discharging_power[t] * dt
                                   for t in T) / 2) / C_battery /
        battery_life_cycles * battery_price)
    fullness_reward = sum(model.soc[t] * 0.00001
                          for t in T)  # Reward for keeping the battery full
    # active_pv_reward = sum(model.active_pv[t] * 0.01 for t in model.pv_T)  # Reward for using PV
    model.obj = pyo.Objective(expr=block_energy_cost + block_peak_cost +
                              energy_buy_cost - energy_sell_cost +
                              battery_cost - fullness_reward,
                              sense=pyo.minimize)
    # model.obj = pyo.Objective(expr=block_energy_cost + block_peak_cost + grid_energy_cost + battery_cost, sense=pyo.minimize)

    # Solve
    # cbc, glpk or appsi_highs (pip install highspy)
    results = pyo.SolverFactory(solver).solve(model, tee=False)
    if not (results.solver.status == pyo.SolverStatus.ok
            and results.solver.termination_condition
            == pyo.TerminationCondition.optimal):
        print("Solver failed! Status:", results.solver.status)
        return None, None, None

    block_peaks = {b: pyo.value(model.block_peaks[b]) for b in power_blocks}
    block_energy = {b: pyo.value(model.block_energy[b]) for b in energy_blocks}
    return ({
        "power": [pyo.value(model.power[t]) for t in times],
        "soc": [pyo.value(model.soc[t]) for t in times],
        "grid_power": [pyo.value(model.grid_power[t]) for t in times],
        "load":
        load,
        "original_production": [production[t] for t in times],
        "limited_production": [
            production[t] * model.active_pv[t].value
            if t in model.pv_T and model.active_pv[t].value is not None else 0
            for t in times
        ],
        "energy_price": [energy_buy_prices[t] for t in times],
    }, block_peaks, block_energy)
