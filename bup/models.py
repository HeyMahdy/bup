"""
Pydantic v2 data models for the Smart Microgrid Energy Optimization API.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class BatteryConfig(BaseModel):
    """Static configuration describing the battery's physical limits."""

    capacity_kwh: float = Field(..., gt=0, description="Total usable battery capacity in kWh.")
    initial_energy_kwh: float = Field(..., ge=0, description="Energy stored in the battery at hour 0.")
    minimum_energy_kwh: float = Field(..., ge=0, description="Floor the battery must never discharge below.")
    max_charge_kwh_per_hour: float = Field(..., ge=0, description="Max energy that can be charged in one hour.")
    max_discharge_kwh_per_hour: float = Field(..., ge=0, description="Max energy that can be discharged in one hour.")

    @field_validator("minimum_energy_kwh")
    @classmethod
    def minimum_not_greater_than_capacity(cls, v: float, info) -> float:
        capacity = info.data.get("capacity_kwh")
        if capacity is not None and v > capacity:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        return v

    @field_validator("initial_energy_kwh")
    @classmethod
    def initial_within_capacity(cls, v: float, info) -> float:
        capacity = info.data.get("capacity_kwh")
        if capacity is not None and v > capacity:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        return v


class HourData(BaseModel):
    """Forecast data for a single hour of the day."""

    hour: int = Field(..., ge=0, le=23, description="Hour of day, 0-23.")
    demand_kwh: float = Field(..., ge=0, description="Forecasted load demand in kWh.")
    solar_kwh: float = Field(..., ge=0, description="Forecasted solar generation in kWh.")
    tariff_bdt_per_kwh: float = Field(..., ge=0, description="Grid tariff in BDT per kWh for this hour.")


class OptimizationRequest(BaseModel):
    """Top-level request payload for /optimize-energy."""

    scenario_id: str = Field(..., min_length=1, description="Caller-supplied identifier for this scenario.")
    operator_notes: List[str] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="1-3 free-text operator directives to be interpreted (e.g. by an LLM) before optimizing.",
    )
    hours: List[HourData] = Field(
        ..., min_length=24, max_length=24, description="Exactly 24 hourly forecast entries, one per hour of day."
    )
    battery: BatteryConfig

    @field_validator("hours")
    @classmethod
    def hours_cover_0_to_23_exactly_once(cls, v: List[HourData]) -> List[HourData]:
        hours_seen = [h.hour for h in v]
        if sorted(hours_seen) != list(range(24)):
            raise ValueError("hours must contain exactly one entry for each hour 0-23")
        return v


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------

class StructuredAdjustment(BaseModel):
    """
    Machine-actionable form of an operator directive, once interpreted.
    Different directive types populate different optional fields.
    """

    hours: List[int] = Field(..., description="Unique hours (0-23) this adjustment applies to.")
    factor: Optional[float] = Field(
        None, description="Multiplicative factor (e.g. for solar_reduction directives)."
    )
    minimum_energy_kwh: Optional[float] = Field(
        None, description="Override minimum battery reserve for the given hours."
    )
    max_grid_kwh: Optional[float] = Field(
        None, description="Cap on grid draw for the given hours."
    )

    @field_validator("hours")
    @classmethod
    def hours_valid_and_unique(cls, v: List[int]) -> List[int]:
        if len(v) != len(set(v)):
            raise ValueError("hours must be unique")
        if any(h < 0 or h > 23 for h in v):
            raise ValueError("each hour must be between 0 and 23")
        return v

    @model_serializer(mode="wrap")
    def serialize_without_unused_fields(self, handler):
        """Keep the API adjustment object limited to its directive-specific fields."""
        return {
            key: value
            for key, value in handler(self).items()
            if value is not None
        }


class DirectiveInterpretation(BaseModel):
    """Interpretation of a single operator note (by index into operator_notes)."""

    note_index: int = Field(..., ge=0, description="Index of the note within operator_notes.")
    applies: bool = Field(..., description="Whether this note resulted in an actionable directive.")
    directive_type: Literal[
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    ]
    structured_adjustment: Optional[StructuredAdjustment] = None
    explanation: str = Field(..., description="Human-readable explanation of the interpretation.")


class HourlyPlan(BaseModel):
    """Optimized dispatch plan for a single hour."""

    hour: int = Field(..., ge=0, le=23)
    grid_kwh: float = Field(..., description="Energy drawn from the grid this hour.")
    solar_used_kwh: float = Field(..., description="Solar energy actually used this hour.")
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: float = Field(..., description="Magnitude of battery charge/discharge this hour.")
    battery_energy_after_kwh: float = Field(..., description="Battery state of charge after this hour.")


class OptimizationResponse(BaseModel):
    """Top-level response payload for /optimize-energy."""

    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlan] = Field(..., min_length=24, max_length=24)
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
