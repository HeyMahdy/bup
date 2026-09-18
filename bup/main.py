"""
Smart Microgrid Energy Optimization API — foundational shell.

This module wires up the API surface, strict request/response schemas,
and global error handling. The LLM directive-interpretation logic and the
math optimizer are intentionally left as TODOs.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from llm_parser import parse_operator_notes
from optimizer import optimize_schedule
from models import OptimizationRequest, OptimizationResponse

logger = logging.getLogger("microgrid_api")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Smart Microgrid Energy Optimization API",
    description="Accepts 24-hour demand/solar/tariff forecasts plus operator "
    "directives and returns an optimized hourly dispatch plan.",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Global error handling
# ---------------------------------------------------------------------------
# Catch-all handler: ensures no unhandled exception ever leaks internal
# details (stack traces, secrets, file paths) back to the client. FastAPI's
# own validation errors (422) and HTTPExceptions are unaffected by this and
# keep their normal, more specific responses.

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception while processing %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


from llm_parser import parse_operator_notes
from optimizer import optimize_schedule # Assuming you saved the pulp function in optimizer.py

@app.post("/optimize-energy", response_model=OptimizationResponse)
async def optimize_energy(payload: OptimizationRequest) -> OptimizationResponse:
    
    # 1. Interpret the operator notes via the LLM
    interpretations = await parse_operator_notes(payload.operator_notes)
    
    # 2. Run the math optimizer
    # We pass the original payload (which has the battery and hour data) 
    # and the structured interpretations we just generated.
    hourly_plan = optimize_schedule(request=payload, interpretations=interpretations)
    
    # 3. Compute aggregate metrics required by the response schema
    total_grid_kwh = sum(plan.grid_kwh for plan in hourly_plan)
    peak_grid_kwh = max(plan.grid_kwh for plan in hourly_plan)
    
    # To calculate total cost, multiply each hour's grid usage by that hour's tariff
    total_cost_bdt = 0.0
    for h in range(24):
        tariff = payload.hours[h].tariff_bdt_per_kwh
        grid_used = hourly_plan[h].grid_kwh
        total_cost_bdt += grid_used * tariff
        
    # 4. Return the fully assembled OptimizationResponse
    return OptimizationResponse(
        scenario_id=payload.scenario_id,
        directive_interpretation=interpretations,
        hourly_plan=hourly_plan,
        total_grid_kwh=round(total_grid_kwh, 4),
        total_cost_bdt=round(total_cost_bdt, 4),
        peak_grid_kwh=round(peak_grid_kwh, 4),
        plan_summary="Schedule optimized successfully respecting all operator directives and battery constraints."
    )
    raise NotImplementedError("optimize-energy processing logic not yet implemented")
