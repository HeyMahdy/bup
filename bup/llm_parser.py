import os
from typing import List
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel

load_dotenv()
# Import the exact schema you defined for the FastAPI response
from models import DirectiveInterpretation

class DirectiveExtractionResult(BaseModel):
    """Wrapper to force the LLM to return an array of interpretations."""
    interpretations: List[DirectiveInterpretation]

# Initialize the async client. It automatically picks up OPENAI_API_KEY from your .env
client = AsyncOpenAI()

SYSTEM_PROMPT = """
You are an expert energy grid controller for a smart campus microgrid. 
Your sole objective is to interpret natural-language operator notes into structured, machine-executable scheduling directives.

You will be given a list of operator notes with zero-based indexes: 0, 1, ..., N-1.
For EVERY note, output exactly one directive interpretation entry matching its note_index in strictly ascending order.

SUPPORTED DIRECTIVES AND ADJUSTMENT SCHEMAS:
1. 'solar_reduction': Usable solar output drops during specific hours.
   structured_adjustment: {'hours': [int, ...], 'factor': float}
   NOTE: 'factor' is the USABLE REMAINING FRACTION (between 0.0 and 1.0).
2. 'minimum_battery_reserve': Battery energy must remain at or above a floor.
   structured_adjustment: {'hours': [int, ...], 'minimum_energy_kwh': float}
3. 'no_charge_window': Battery charging is prohibited.
   structured_adjustment: {'hours': [int, ...]}
4. 'no_discharge_window': Battery discharging is prohibited.
   structured_adjustment: {'hours': [int, ...]}
5. 'max_grid_window': Grid import must not exceed a specified ceiling.
   structured_adjustment: {'hours': [int, ...], 'max_grid_kwh': float}
6. 'no_op': The note does not alter campus energy scheduling.
   applies: false, structured_adjustment: null

CRITICAL NORMALIZATION RULES:
- Time windows use whole-hour intervals where start hour is INCLUDED and end hour is EXCLUDED (e.g. 1 PM to 3 PM -> [13, 14]).
- All hours arrays must contain unique integers between 0 and 23 sorted in ascending order.
- 'applies' must be false ONLY for 'no_op', and true for all other 5 directive types.
"""

async def parse_operator_notes(notes: List[str]) -> List[DirectiveInterpretation]:
    """Asynchronously calls OpenAI to parse notes directly into Pydantic models."""
    if not notes:
        return []

    # Format notes with explicit zero-based indices
    formatted_notes = "\n".join(
        [f"[{i}]: {note}" for i, note in enumerate(notes)]
    )

    # Use the beta parse endpoint which enforces the Pydantic schema strictly
    response = await client.beta.chat.completions.parse(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.0, # 0.0 is critical for deterministic judge evaluation
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user", "content": f"Operator Notes:\n{formatted_notes}"}
        ],
        response_format=DirectiveExtractionResult
    )

    # The .parsed attribute safely contains your validated Pydantic objects
    return response.choices[0].message.parsed.interpretations