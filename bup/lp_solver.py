"""24-hour microgrid dispatch optimizer (PuLP / CBC)."""

from __future__ import annotations

from typing import List

import pulp

from exceptions import OptimizationInfeasibleError
from models import DirectiveInterpretation, HourlyPlan, OptimizationRequest

_IDLE_EPS = 0.001


def optimize_schedule(
    request: OptimizationRequest,
    interpretations: List[DirectiveInterpretation],
) -> List[HourlyPlan]:
    """
    Minimize grid electricity cost subject to energy balance, battery physics,
    and all applicable operator directives.
    """

    # hours are normalized to hour-index order by OptimizationRequest.
    hours = request.hours
    effective_solar = [entry.solar_kwh for entry in hours]
    min_reserve = [request.battery.minimum_energy_kwh for _ in range(24)]
    max_grid: list[float | None] = [None for _ in range(24)]
    charge_allowed = [True for _ in range(24)]
    discharge_allowed = [True for _ in range(24)]

    for interpretation in interpretations:
        if not interpretation.applies or interpretation.structured_adjustment is None:
            continue

        adjustment = interpretation.structured_adjustment
        directive = interpretation.directive_type

        for hour in adjustment.hours:
            if directive == "solar_reduction":
                # Multiple solar reductions on the same hour: keep the stricter (lower) factor.
                effective_solar[hour] = min(
                    effective_solar[hour],
                    hours[hour].solar_kwh * adjustment.factor,
                )
            elif directive == "minimum_battery_reserve":
                min_reserve[hour] = max(min_reserve[hour], adjustment.minimum_energy_kwh)
            elif directive == "no_charge_window":
                charge_allowed[hour] = False
            elif directive == "no_discharge_window":
                discharge_allowed[hour] = False
            elif directive == "max_grid_window":
                cap = adjustment.max_grid_kwh
                max_grid[hour] = (
                    cap if max_grid[hour] is None else min(max_grid[hour], cap)
                )

    problem = pulp.LpProblem("GridWise_Optimization", pulp.LpMinimize)

    grid = {}
    solar_used = {}
    charge = {}
    discharge = {}
    battery = {}

    for hour in range(24):
        grid[hour] = pulp.LpVariable(
            f"grid_{hour}",
            lowBound=0,
            upBound=max_grid[hour],
        )
        solar_used[hour] = pulp.LpVariable(
            f"solar_used_{hour}",
            lowBound=0,
            upBound=effective_solar[hour],
        )

        charge_limit = (
            request.battery.max_charge_kwh_per_hour if charge_allowed[hour] else 0.0
        )
        discharge_limit = (
            request.battery.max_discharge_kwh_per_hour if discharge_allowed[hour] else 0.0
        )
        charge[hour] = pulp.LpVariable(f"charge_{hour}", lowBound=0, upBound=charge_limit)
        discharge[hour] = pulp.LpVariable(
            f"discharge_{hour}",
            lowBound=0,
            upBound=discharge_limit,
        )
        battery[hour] = pulp.LpVariable(
            f"battery_{hour}",
            lowBound=min_reserve[hour],
            upBound=request.battery.capacity_kwh,
        )

    problem += pulp.lpSum(
        grid[hour] * hours[hour].tariff_bdt_per_kwh for hour in range(24)
    )

    for hour in range(24):
        problem += (
            grid[hour] + solar_used[hour] + discharge[hour]
            == hours[hour].demand_kwh + charge[hour]
        )
        previous = (
            request.battery.initial_energy_kwh if hour == 0 else battery[hour - 1]
        )
        problem += battery[hour] == previous + charge[hour] - discharge[hour]

    problem += battery[23] == request.battery.initial_energy_kwh

    problem.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[problem.status] != "Optimal":
        raise OptimizationInfeasibleError(
            "No feasible 24-hour schedule under the given constraints."
        )

    plan: List[HourlyPlan] = []
    for hour in range(24):
        raw_charge = round(charge[hour].varValue or 0.0, 4)
        raw_discharge = round(discharge[hour].varValue or 0.0, 4)
        net = raw_charge - raw_discharge

        if net > _IDLE_EPS:
            action = "charge"
            action_kwh = net
        elif net < -_IDLE_EPS:
            action = "discharge"
            action_kwh = abs(net)
        else:
            action = "idle"
            action_kwh = 0.0

        plan.append(
            HourlyPlan(
                hour=hour,
                grid_kwh=round(grid[hour].varValue or 0.0, 4),
                solar_used_kwh=round(solar_used[hour].varValue or 0.0, 4),
                battery_action=action,
                battery_kwh=action_kwh,
                battery_energy_after_kwh=round(battery[hour].varValue or 0.0, 4),
            )
        )

    return plan
