"""
Pydantic v2 request/response contracts for the GridWise optimization API.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer


class StrictRequestModel(BaseModel):
    """Base for request objects that must match the published JSON schema exactly."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class BatteryConfig(StrictRequestModel):
    """Static battery limits for the 24-hour planning horizon."""

    capacity_kwh: float = Field(..., gt=0)
    initial_energy_kwh: float = Field(..., ge=0)
    minimum_energy_kwh: float = Field(..., ge=0)
    max_charge_kwh_per_hour: float = Field(..., ge=0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0)

    @field_validator("minimum_energy_kwh", "initial_energy_kwh")
    @classmethod
    def within_capacity(cls, value: float, info) -> float:
        capacity = info.data.get("capacity_kwh")
        if capacity is not None and value > capacity:
            raise ValueError(f"{info.field_name} cannot exceed capacity_kwh")
        return value


class HourData(StrictRequestModel):
    """Forecast for a single hour of the day."""

    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float = Field(..., ge=0)
    solar_kwh: float = Field(..., ge=0)
    tariff_bdt_per_kwh: float = Field(..., ge=0)


class OptimizationRequest(StrictRequestModel):
    """POST /optimize-energy request body."""

    scenario_id: str = Field(..., min_length=1)
    operator_notes: List[str] = Field(..., min_length=1, max_length=3)
    hours: List[HourData] = Field(..., min_length=24, max_length=24)
    battery: BatteryConfig

    @field_validator("scenario_id")
    @classmethod
    def scenario_id_non_empty(cls, scenario_id: str) -> str:
        if not scenario_id.strip():
            raise ValueError("scenario_id must be a non-empty string")
        return scenario_id

    @field_validator("operator_notes")
    @classmethod
    def notes_non_empty(cls, notes: List[str]) -> List[str]:
        if any(not isinstance(note, str) or not note.strip() for note in notes):
            raise ValueError("operator_notes must contain non-empty strings")
        return notes

    @field_validator("hours")
    @classmethod
    def hours_cover_day_sorted(cls, hours: List[HourData]) -> List[HourData]:
        seen = [entry.hour for entry in hours]
        if sorted(seen) != list(range(24)):
            raise ValueError("hours must contain exactly one entry for each hour 0-23")
        # Normalize so hour index == list index for downstream solvers.
        return sorted(hours, key=lambda entry: entry.hour)


class StructuredAdjustment(BaseModel):
    """Machine-actionable form of one operator directive."""

    hours: List[int] = Field(...)
    factor: Optional[float] = None
    minimum_energy_kwh: Optional[float] = None
    max_grid_kwh: Optional[float] = None

    @field_validator("hours")
    @classmethod
    def hours_unique_sorted(cls, hours: List[int]) -> List[int]:
        if any(not isinstance(hour, int) or isinstance(hour, bool) for hour in hours):
            raise ValueError("hours must be integers")
        if any(hour < 0 or hour > 23 for hour in hours):
            raise ValueError("each hour must be between 0 and 23")
        if len(hours) != len(set(hours)):
            raise ValueError("hours must be unique")
        return sorted(hours)

    @model_serializer(mode="wrap")
    def omit_unused_fields(self, handler):
        return {
            key: value
            for key, value in handler(self).items()
            if value is not None
        }


class DirectiveInterpretation(BaseModel):
    """Interpretation of a single operator note."""

    note_index: int = Field(..., ge=0)
    applies: bool
    directive_type: Literal[
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    ]
    structured_adjustment: Optional[StructuredAdjustment] = None
    explanation: str = Field(..., min_length=1)


class HourlyPlan(BaseModel):
    """Optimized dispatch decision for one hour."""

    hour: int = Field(..., ge=0, le=23)
    grid_kwh: float
    solar_used_kwh: float
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizationResponse(BaseModel):
    """POST /optimize-energy response body."""

    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlan] = Field(..., min_length=24, max_length=24)
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
