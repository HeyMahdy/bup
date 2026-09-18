"""LLM interpretation of free-text operator notes into structured directives."""

from __future__ import annotations

import re
from typing import List

from openai import APIError, AsyncOpenAI, AuthenticationError, RateLimitError
from pydantic import BaseModel

from config import get_settings
from exceptions import LLMInterpretationError
from models import DirectiveInterpretation, OptimizationRequest, StructuredAdjustment
from time_windows import hours_from_note

SYSTEM_PROMPT = """
You are an expert energy grid controller for a smart campus microgrid.
Your sole objective is to interpret natural-language operator notes into structured,
machine-executable scheduling directives.

You will be given a list of operator notes with zero-based indexes: 0, 1, ..., N-1.
For EVERY note, output exactly one directive interpretation entry matching its note_index
in strictly ascending order.

SUPPORTED DIRECTIVES AND ADJUSTMENT SCHEMAS:
1. 'solar_reduction': Usable solar output drops during specific hours.
   structured_adjustment: ONLY {'hours': [int, ...], 'factor': float}
   'factor' is the USABLE REMAINING FRACTION in [0, 1].
   "80% reduction" / "drops by 80%" => factor = 0.2
   "drop to about 20%" / "treated as roughly 25%" / "about half" => factor = 0.2 / 0.25 / 0.5
2. 'minimum_battery_reserve': Battery energy must remain at or above a floor.
   structured_adjustment: ONLY {'hours': [int, ...], 'minimum_energy_kwh': float}
   NEVER include 'factor'. Convert percentages using battery capacity first.
   "50% of capacity" with capacity 200 kWh => minimum_energy_kwh = 100
3. 'no_charge_window': Battery charging is prohibited.
   structured_adjustment: ONLY {'hours': [int, ...]}
4. 'no_discharge_window': Battery discharging is prohibited.
   structured_adjustment: ONLY {'hours': [int, ...]}
5. 'max_grid_window': Grid import must not exceed a specified ceiling.
   structured_adjustment: ONLY {'hours': [int, ...], 'max_grid_kwh': float}
6. 'no_op': The note does not alter campus energy scheduling.
   applies: false, structured_adjustment: null

TIME WINDOW RULES (whole hours, start inclusive / end exclusive):
- "from 1 PM to 3 PM" => [13, 14]
- "from 6 PM until 9 PM" => [18, 19, 20]
- "from 6 PM until 10 PM" => [18, 19, 20, 21]
- "from 7 PM until 10 PM" => [19, 20, 21]
- "between 11 AM and 2 PM" => [11, 12, 13]
- "from noon until 2 PM" => [12, 13]
Hours must be unique integers 0-23 in ascending order.

OTHER RULES:
- 'applies' is false ONLY for 'no_op'; true for every other directive.
- Do not invent demand, solar, tariff, battery hardware limits, or unsupported types.
- Extract operator-stated numeric values exactly even when they exceed an allowed
  range. Never clamp, repair, or turn such a directive into no_op; deterministic
  guardrails are responsible for rejecting invalid values.
- Distractors that do not affect this 24-hour energy schedule must be no_op.
- A directive is actionable only when the note supplies every required value and a
  concrete whole-hour window. Never guess a number or translate vague periods such
  as "during the evening" into hours.
- A vague request such as "maintain some emergency reserve during the evening"
  has no numeric reserve or exact time window and MUST be no_op.
""".strip()


_EXPLICIT_RESERVE_VALUE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:kwh|%|percent)|"
    r"(?:half|quarter|third|three[- ]quarters?)\s+(?:of\s+)?(?:the\s+)?"
    r"(?:battery(?:'s)?\s+)?capacity)",
    re.IGNORECASE,
)


class DirectiveExtractionResult(BaseModel):
    """Wrapper so the model returns an ordered list of interpretations."""

    interpretations: List[DirectiveInterpretation]


_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    """Lazily construct the OpenAI client so /health can load without credentials."""

    global _client
    if _client is not None:
        return _client

    settings = get_settings()
    if not settings.has_openai_credentials:
        raise LLMInterpretationError(
            "OPENAI_API_KEY is not configured. Set it in the environment or a .env file."
        )

    _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def sanitize_interpretations(
    request: OptimizationRequest,
    interpretations: List[DirectiveInterpretation],
) -> List[DirectiveInterpretation]:
    """
    Deterministic cleanup of LLM structured output before guardrails.

    Keeps only the adjustment fields allowed for each directive type and converts
    percentage-style reserve mistakes (factor on a reserve note) into kWh.
    """

    cleaned: List[DirectiveInterpretation] = []
    for item in interpretations:
        if item.directive_type == "no_op" or item.structured_adjustment is None:
            cleaned.append(
                item.model_copy(
                    update={
                        "applies": False if item.directive_type == "no_op" else item.applies,
                        "structured_adjustment": None
                        if item.directive_type == "no_op"
                        else item.structured_adjustment,
                    }
                )
            )
            continue

        adj = item.structured_adjustment
        note_text = request.operator_notes[item.note_index]
        parsed_hours = hours_from_note(note_text)

        # A reserve constraint needs both a concrete time window and a reserve
        # quantity stated by the operator. Do not allow the model to invent either
        # value for vague requests such as "some reserve during the evening".
        if (
            item.directive_type == "minimum_battery_reserve"
            and (
                parsed_hours is None
                or _EXPLICIT_RESERVE_VALUE.search(note_text) is None
            )
        ):
            cleaned.append(
                item.model_copy(
                    update={
                        "applies": False,
                        "directive_type": "no_op",
                        "structured_adjustment": None,
                        "explanation": (
                            "The note does not provide both a numeric reserve "
                            "and a concrete whole-hour window."
                        ),
                    }
                )
            )
            continue

        hours = parsed_hours if parsed_hours is not None else list(adj.hours)
        if item.directive_type == "solar_reduction":
            new_adj = StructuredAdjustment(hours=hours, factor=adj.factor)
        elif item.directive_type == "minimum_battery_reserve":
            reserve = adj.minimum_energy_kwh
            if reserve is None and adj.factor is not None:
                reserve = adj.factor * request.battery.capacity_kwh
            new_adj = StructuredAdjustment(
                hours=hours,
                minimum_energy_kwh=reserve,
            )
        elif item.directive_type == "max_grid_window":
            new_adj = StructuredAdjustment(hours=hours, max_grid_kwh=adj.max_grid_kwh)
        else:
            new_adj = StructuredAdjustment(hours=hours)

        cleaned.append(
            item.model_copy(
                update={
                    "applies": True,
                    "structured_adjustment": new_adj,
                }
            )
        )
    return cleaned


async def parse_operator_notes(
    request: OptimizationRequest,
) -> List[DirectiveInterpretation]:
    """Interpret operator notes via a language model into structured directives."""

    settings = get_settings()
    formatted_notes = "\n".join(
        f"[{index}]: {note}" for index, note in enumerate(request.operator_notes)
    )

    try:
        response = await get_openai_client().beta.chat.completions.parse(
            model=settings.openai_model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Battery capacity: {request.battery.capacity_kwh} kWh\n"
                        f"Operator Notes:\n{formatted_notes}"
                    ),
                },
            ],
            response_format=DirectiveExtractionResult,
        )
    except AuthenticationError as exc:
        raise LLMInterpretationError("OpenAI authentication failed.") from exc
    except RateLimitError as exc:
        raise LLMInterpretationError("OpenAI rate limit exceeded.") from exc
    except APIError as exc:
        raise LLMInterpretationError("OpenAI API request failed.") from exc
    except Exception as exc:  # noqa: BLE001 - surface as controlled pipeline error
        raise LLMInterpretationError("Operator-note interpretation failed.") from exc

    message = response.choices[0].message
    if message.refusal:
        raise LLMInterpretationError("Model refused to interpret operator notes.")
    if message.parsed is None:
        raise LLMInterpretationError("Model returned no structured interpretations.")

    return sanitize_interpretations(request, message.parsed.interpretations)
