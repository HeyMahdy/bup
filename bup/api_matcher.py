import json
import math
import os
import sys
from pathlib import Path

import requests


TOLERANCE = 0.01
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/optimize-energy")
CASES_FILE = Path(__file__).with_name(
    "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
)


def is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def close(actual, expected):
    return is_number(actual) and is_number(expected) and math.isclose(
        actual, expected, rel_tol=0.0, abs_tol=TOLERANCE
    )


def validate_interpretations(actual, expected):
    errors = []
    if not isinstance(actual, list) or len(actual) != len(expected):
        return [
            f"directive_interpretation count: expected {len(expected)}, "
            f"got {len(actual) if isinstance(actual, list) else 'non-array'}"
        ]

    for index, (got, wanted) in enumerate(zip(actual, expected)):
        if not isinstance(got, dict):
            errors.append(f"Note {index}: interpretation must be an object")
            continue

        for field in ("note_index", "applies", "directive_type"):
            if got.get(field) != wanted.get(field):
                errors.append(
                    f"Note {index} {field}: expected {wanted.get(field)!r}, "
                    f"got {got.get(field)!r}"
                )

        explanation = got.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            errors.append(f"Note {index}: explanation must be a non-empty string")

        got_adjustment = got.get("structured_adjustment")
        wanted_adjustment = wanted.get("structured_adjustment")
        if wanted_adjustment is None:
            if got_adjustment is not None:
                errors.append(f"Note {index}: structured_adjustment must be null")
            continue

        if not isinstance(got_adjustment, dict):
            errors.append(f"Note {index}: structured_adjustment must be an object")
            continue
        if set(got_adjustment) != set(wanted_adjustment):
            errors.append(
                f"Note {index}: adjustment fields expected "
                f"{sorted(wanted_adjustment)}, got {sorted(got_adjustment)}"
            )
            continue

        for field, expected_value in wanted_adjustment.items():
            actual_value = got_adjustment[field]
            if is_number(expected_value):
                if not close(actual_value, expected_value):
                    errors.append(
                        f"Note {index} {field}: expected {expected_value}, "
                        f"got {actual_value}"
                    )
            elif actual_value != expected_value:
                errors.append(
                    f"Note {index} {field}: expected {expected_value}, "
                    f"got {actual_value}"
                )

    return errors


def replay_hourly_plan(payload, interpretations, actual):
    plan = actual.get("hourly_plan")
    if not isinstance(plan, list) or len(plan) != 24:
        return ["hourly_plan must contain exactly 24 entries"]
    if any(not isinstance(entry, dict) for entry in plan):
        return ["every hourly_plan entry must be an object"]

    plan_by_hour = {entry.get("hour"): entry for entry in plan}
    if set(plan_by_hour) != set(range(24)) or len(plan_by_hour) != len(plan):
        return ["hourly_plan must contain each hour 0-23 exactly once"]

    errors = []
    inputs_by_hour = {entry["hour"]: entry for entry in payload["hours"]}
    battery = payload["battery"]
    effective_solar = [inputs_by_hour[h]["solar_kwh"] for h in range(24)]
    minimum_energy = [battery["minimum_energy_kwh"] for _ in range(24)]
    grid_cap = [None for _ in range(24)]
    charge_allowed = [True for _ in range(24)]
    discharge_allowed = [True for _ in range(24)]

    for interpretation in interpretations:
        if not interpretation["applies"]:
            continue
        directive_type = interpretation["directive_type"]
        adjustment = interpretation["structured_adjustment"]
        for hour in adjustment["hours"]:
            if directive_type == "solar_reduction":
                effective_solar[hour] = (
                    inputs_by_hour[hour]["solar_kwh"] * adjustment["factor"]
                )
            elif directive_type == "minimum_battery_reserve":
                minimum_energy[hour] = max(
                    minimum_energy[hour], adjustment["minimum_energy_kwh"]
                )
            elif directive_type == "no_charge_window":
                charge_allowed[hour] = False
            elif directive_type == "no_discharge_window":
                discharge_allowed[hour] = False
            elif directive_type == "max_grid_window":
                cap = adjustment["max_grid_kwh"]
                grid_cap[hour] = (
                    cap if grid_cap[hour] is None else min(grid_cap[hour], cap)
                )

    previous_energy = battery["initial_energy_kwh"]
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0
    required_plan_fields = {
        "hour",
        "grid_kwh",
        "solar_used_kwh",
        "battery_action",
        "battery_kwh",
        "battery_energy_after_kwh",
    }

    for hour in range(24):
        entry = plan_by_hour[hour]
        missing = required_plan_fields - set(entry)
        if missing:
            errors.append(f"Hour {hour}: missing fields {sorted(missing)}")
            continue

        grid = entry["grid_kwh"]
        solar = entry["solar_used_kwh"]
        amount = entry["battery_kwh"]
        energy_after = entry["battery_energy_after_kwh"]
        action = entry["battery_action"]

        if any(
            not is_number(value) for value in (grid, solar, amount, energy_after)
        ):
            errors.append(f"Hour {hour}: energy values must be finite numbers")
            continue
        if grid < 0 or solar < 0 or amount < 0:
            errors.append(
                f"Hour {hour}: grid, solar, and battery_kwh must be non-negative"
            )
            continue
        if action not in {"charge", "discharge", "idle"}:
            errors.append(f"Hour {hour}: invalid battery_action {action!r}")
            continue

        charge = amount if action == "charge" else 0.0
        discharge = amount if action == "discharge" else 0.0
        if action == "idle" and not close(amount, 0.0):
            errors.append(f"Hour {hour}: idle requires battery_kwh=0")
        if action != "idle" and amount <= TOLERANCE:
            errors.append(f"Hour {hour}: {action} requires a positive battery_kwh")
        if charge > battery["max_charge_kwh_per_hour"] + TOLERANCE:
            errors.append(f"Hour {hour}: charge rate exceeded")
        if discharge > battery["max_discharge_kwh_per_hour"] + TOLERANCE:
            errors.append(f"Hour {hour}: discharge rate exceeded")
        if not charge_allowed[hour] and charge > TOLERANCE:
            errors.append(f"Hour {hour}: no-charge directive violated")
        if not discharge_allowed[hour] and discharge > TOLERANCE:
            errors.append(f"Hour {hour}: no-discharge directive violated")
        if solar > effective_solar[hour] + TOLERANCE:
            errors.append(f"Hour {hour}: effective solar limit exceeded")
        if grid_cap[hour] is not None and grid > grid_cap[hour] + TOLERANCE:
            errors.append(f"Hour {hour}: grid cap exceeded")
        if not (
            minimum_energy[hour] - TOLERANCE
            <= energy_after
            <= battery["capacity_kwh"] + TOLERANCE
        ):
            errors.append(f"Hour {hour}: battery energy is outside its active bounds")

        expected_energy = previous_energy + charge - discharge
        if not close(energy_after, expected_energy):
            errors.append(f"Hour {hour}: battery state transition is invalid")

        demand = inputs_by_hour[hour]["demand_kwh"]
        if not close(grid + solar + discharge, demand + charge):
            errors.append(f"Hour {hour}: energy balance is invalid")

        previous_energy = energy_after
        total_grid += grid
        total_cost += grid * inputs_by_hour[hour]["tariff_bdt_per_kwh"]
        peak_grid = max(peak_grid, grid)

    if not close(previous_energy, battery["initial_energy_kwh"]):
        errors.append("end-of-day battery neutrality is violated")

    for field, recalculated in (
        ("total_grid_kwh", total_grid),
        ("total_cost_bdt", total_cost),
        ("peak_grid_kwh", peak_grid),
    ):
        if not close(actual.get(field), recalculated):
            errors.append(
                f"{field}: reported {actual.get(field)!r}, "
                f"recalculated {recalculated:.4f}"
            )

    return errors


def validate_case(case, actual):
    if not isinstance(actual, dict):
        return ["response must be a JSON object"]

    expected = case["expected_output"]
    payload = case["input"]
    required_fields = {
        "scenario_id",
        "directive_interpretation",
        "hourly_plan",
        "total_grid_kwh",
        "total_cost_bdt",
        "peak_grid_kwh",
        "plan_summary",
    }
    errors = []
    missing = required_fields - set(actual)
    if missing:
        errors.append(f"missing response fields: {sorted(missing)}")

    if actual.get("scenario_id") != payload["scenario_id"]:
        errors.append("scenario_id does not echo the request")
    if not isinstance(actual.get("plan_summary"), str):
        errors.append("plan_summary must be a string")

    errors.extend(
        validate_interpretations(
            actual.get("directive_interpretation"),
            expected["directive_interpretation"],
        )
    )
    errors.extend(
        replay_hourly_plan(payload, expected["directive_interpretation"], actual)
    )

    if not close(actual.get("total_cost_bdt"), expected["total_cost_bdt"]):
        errors.append(
            f"optimization cost: expected {expected['total_cost_bdt']}, "
            f"got {actual.get('total_cost_bdt')!r}"
        )
    return errors


def run_matcher():
    try:
        with CASES_FILE.open(encoding="utf-8") as file:
            cases = json.load(file)["cases"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"Could not load sample cases: {exc}")
        return 1

    passed = 0
    failed = 0
    print(f"Testing {len(cases)} cases against {API_URL}\n")

    for case in cases:
        try:
            response = requests.post(API_URL, json=case["input"], timeout=30)
            response.raise_for_status()
            actual = response.json()
            errors = validate_case(case, actual)
        except (requests.RequestException, ValueError) as exc:
            errors = [f"API request failed: {exc}"]

        if errors:
            print(f"[{case['id']}] FAIL")
            for error in errors:
                print(f"  - {error}")
            failed += 1
        else:
            print(f"[{case['id']}] PASS")
            passed += 1

    print(f"\nTEST RUN COMPLETE: {passed} passed | {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_matcher())
