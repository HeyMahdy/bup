"""Deterministic validation for untrusted LLM-produced operator directives."""

from __future__ import annotations

import math
import re
from typing import List

from exceptions import GuardrailValidationError
from models import DirectiveInterpretation, OptimizationRequest


_EXPLICIT_KWH = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*kwh\b", re.IGNORECASE)


def validate_interpretations(
    request: OptimizationRequest,
    interpretations: List[DirectiveInterpretation],
) -> List[DirectiveInterpretation]:
    """Validate LLM output before it reaches the optimizer."""

    # Cross-check explicit reserve quantities in the source note. The LLM must
    # not hide an invalid operator value by clamping it or returning no_op.
    for note_index, note in enumerate(request.operator_notes):
        if not re.search(r"\b(?:reserve|battery)\b", note, re.IGNORECASE):
            continue
        for match in _EXPLICIT_KWH.finditer(note):
            reserve = float(match.group("value"))
            if not math.isfinite(reserve) or reserve > request.battery.capacity_kwh:
                raise GuardrailValidationError(
                    f"Note {note_index}: battery reserve must be between 0 and battery capacity"
                )

    if len(interpretations) != len(request.operator_notes):
        raise GuardrailValidationError(
            "LLM output must contain exactly one interpretation per operator note"
        )

    expected_indexes = list(range(len(request.operator_notes)))
    actual_indexes = [item.note_index for item in interpretations]
    if actual_indexes != expected_indexes:
        raise GuardrailValidationError(
            "Interpretations must be in note_index order with no missing or duplicate notes"
        )

    for item in interpretations:
        if not item.explanation.strip():
            raise GuardrailValidationError(
                f"Note {item.note_index}: explanation must be a non-empty string"
            )

        if item.directive_type == "no_op":
            if item.applies or item.structured_adjustment is not None:
                raise GuardrailValidationError(
                    f"Note {item.note_index}: no_op requires applies=false and a null adjustment"
                )
            continue

        if not item.applies or item.structured_adjustment is None:
            raise GuardrailValidationError(
                f"Note {item.note_index}: actionable directives require applies=true and an adjustment"
            )

        adjustment = item.structured_adjustment
        hours = adjustment.hours
        if (
            not hours
            or any(isinstance(hour, bool) or not isinstance(hour, int) for hour in hours)
            or any(hour < 0 or hour > 23 for hour in hours)
            or hours != sorted(set(hours))
        ):
            raise GuardrailValidationError(
                f"Note {item.note_index}: hours must be unique integers from 0 to 23 in ascending order"
            )

        numeric_fields = {
            "factor": adjustment.factor,
            "minimum_energy_kwh": adjustment.minimum_energy_kwh,
            "max_grid_kwh": adjustment.max_grid_kwh,
        }

        if item.directive_type == "solar_reduction":
            _require_only(item.note_index, numeric_fields, "factor")
            if not 0 <= adjustment.factor <= 1:
                raise GuardrailValidationError(
                    f"Note {item.note_index}: solar factor must be between 0 and 1"
                )
        elif item.directive_type == "minimum_battery_reserve":
            _require_only(item.note_index, numeric_fields, "minimum_energy_kwh")
            if not 0 <= adjustment.minimum_energy_kwh <= request.battery.capacity_kwh:
                raise GuardrailValidationError(
                    f"Note {item.note_index}: battery reserve must be between 0 and battery capacity"
                )
        elif item.directive_type == "max_grid_window":
            _require_only(item.note_index, numeric_fields, "max_grid_kwh")
            if adjustment.max_grid_kwh < 0:
                raise GuardrailValidationError(
                    f"Note {item.note_index}: grid cap must be non-negative"
                )
        else:
            _require_only(item.note_index, numeric_fields, None)

    return interpretations


def _require_only(
    note_index: int,
    fields: dict[str, float | None],
    required: str | None,
) -> None:
    for name, value in fields.items():
        if name == required:
            if value is None or isinstance(value, bool) or not math.isfinite(value):
                raise GuardrailValidationError(
                    f"Note {note_index}: {name} must be a finite number"
                )
        elif value is not None:
            raise GuardrailValidationError(
                f"Note {note_index}: unexpected adjustment field {name}"
            )
