import pulp
from typing import List
from models import OptimizationRequest, DirectiveInterpretation, HourlyPlan

def optimize_schedule(
    request: OptimizationRequest, 
    interpretations: List[DirectiveInterpretation]
) -> List[HourlyPlan]:
    
    # 1. Initialize Baseline Arrays
    # We create arrays for the bounds that might be modified by the LLM directives
    effective_solar = [h.solar_kwh for h in request.hours]
    min_reserve = [request.battery.minimum_energy_kwh for _ in range(24)]
    max_grid = [None for _ in range(24)] # None means infinity/no cap
    charge_allowed = [True for _ in range(24)]
    discharge_allowed = [True for _ in range(24)]

    # 2. Apply the LLM Directives as Pre-Processing
    # We iterate through the interpretations and tighten the physical bounds
    for interp in interpretations:
        if not interp.applies or interp.structured_adjustment is None:
            continue
            
        adj = interp.structured_adjustment
        dtype = interp.directive_type
        
        for h in adj.hours:
            if dtype == "solar_reduction":
                effective_solar[h] = request.hours[h].solar_kwh * adj.factor
            elif dtype == "minimum_battery_reserve":
                min_reserve[h] = max(min_reserve[h], adj.minimum_energy_kwh)
            elif dtype == "no_charge_window":
                charge_allowed[h] = False
            elif dtype == "no_discharge_window":
                discharge_allowed[h] = False
            elif dtype == "max_grid_window":
                max_grid[h] = adj.max_grid_kwh

    # 3. Initialize the LP Problem
    prob = pulp.LpProblem("GridWise_Optimization", pulp.LpMinimize)

    # 4. Define Decision Variables
    grid = {}
    solar_used = {}
    charge = {}
    discharge = {}
    battery = {}

    for h in range(24):
        # Grid cap applies if max_grid_window was triggered
        grid[h] = pulp.LpVariable(f"grid_{h}", lowBound=0, upBound=max_grid[h])
        
        # Solar used cannot exceed the effective solar after panel cleaning/reductions
        solar_used[h] = pulp.LpVariable(f"solar_used_{h}", lowBound=0, upBound=effective_solar[h])

        # Hardware limits + Operator directive overrides (no_charge / no_discharge)
        c_limit = request.battery.max_charge_kwh_per_hour if charge_allowed[h] else 0.0
        d_limit = request.battery.max_discharge_kwh_per_hour if discharge_allowed[h] else 0.0
        
        charge[h] = pulp.LpVariable(f"charge_{h}", lowBound=0, upBound=c_limit)
        discharge[h] = pulp.LpVariable(f"discharge_{h}", lowBound=0, upBound=d_limit)

        # Battery capacity and dynamic reserve limits
        battery[h] = pulp.LpVariable(
            f"battery_{h}", 
            lowBound=min_reserve[h], 
            upBound=request.battery.capacity_kwh
        )

    # 5. The Objective: Minimize total BDT cost
    prob += pulp.lpSum(grid[h] * request.hours[h].tariff_bdt_per_kwh for h in range(24))

    # 6. Apply Hard Constraints
    for h in range(24):
        # A. Energy Balance Equation
        prob += grid[h] + solar_used[h] + discharge[h] == request.hours[h].demand_kwh + charge[h]

        # B. Battery State Transition
        prev_energy = request.battery.initial_energy_kwh if h == 0 else battery[h-1]
        prob += battery[h] == prev_energy + charge[h] - discharge[h]

    # C. End-of-Day Neutrality Constraint
    prob += battery[23] == request.battery.initial_energy_kwh

    # 7. Execute the Solver
    prob.solve(pulp.PULP_CBC_CMD(msg=False)) # msg=False hides terminal spam
    
    if pulp.LpStatus[prob.status] != 'Optimal':
        raise ValueError("Solver could not find a feasible schedule. Constraints may be conflicting.")

    # 8. Post-Processing & The "Netting" Cleanup
    hourly_plan = []
    
    for h in range(24):
        # Extract raw values, rounding to 4 decimals to drop floating point noise
        raw_charge = round(charge[h].varValue, 4)
        raw_discharge = round(discharge[h].varValue, 4)
        
        # Netting out the charge/discharge to enforce mutual exclusivity
        net_battery = raw_charge - raw_discharge
        
        if net_battery > 0.001:
            action = "charge"
            action_kwh = net_battery
        elif net_battery < -0.001:
            action = "discharge"
            action_kwh = abs(net_battery)
        else:
            action = "idle"
            action_kwh = 0.0

        plan = HourlyPlan(
            hour=h,
            grid_kwh=round(grid[h].varValue, 4),
            solar_used_kwh=round(solar_used[h].varValue, 4),
            battery_action=action,
            battery_kwh=action_kwh,
            battery_energy_after_kwh=round(battery[h].varValue, 4)
        )
        hourly_plan.append(plan)

    return hourly_plan