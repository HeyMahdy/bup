"""
Smart Microgrid Energy Optimization API (GridWise LLM).

Pipeline: LLM interpretation → deterministic guardrails → LP optimization.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from exceptions import (
    GuardrailValidationError,
    LLMInterpretationError,
    OptimizationInfeasibleError,
)
from guardrails import validate_interpretations
from llm_parser import parse_operator_notes
from lp_solver import optimize_schedule
from models import OptimizationRequest, OptimizationResponse

logger = logging.getLogger("gridwise")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Smart Microgrid Energy Optimization API",
    description=(
        "Accepts 24-hour demand/solar/tariff forecasts plus operator notes and "
        "returns an interpreted directive set plus a cost-minimizing hourly plan."
    ),
    version="0.1.0",
)


def _error_body(detail: str) -> dict:
    return {"detail": detail}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    logger.info("Request validation failed for %s: %s", request.url.path, exc.errors())
    return JSONResponse(
        status_code=400,
        content=jsonable_encoder({"detail": exc.errors()}),
    )


@app.exception_handler(GuardrailValidationError)
async def guardrail_exception_handler(
    request: Request,
    exc: GuardrailValidationError,
) -> JSONResponse:
    logger.warning("Guardrail rejection on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=422, content=_error_body(str(exc)))


@app.exception_handler(OptimizationInfeasibleError)
async def infeasible_exception_handler(
    request: Request,
    exc: OptimizationInfeasibleError,
) -> JSONResponse:
    logger.warning("Infeasible schedule on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=422, content=_error_body(str(exc)))


@app.exception_handler(LLMInterpretationError)
async def llm_exception_handler(
    request: Request,
    exc: LLMInterpretationError,
) -> JSONResponse:
    logger.error("LLM interpretation failure on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=500, content=_error_body(str(exc)))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=_error_body("Internal Server Error"),
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizationResponse)
async def optimize_energy(payload: OptimizationRequest) -> OptimizationResponse:
    interpretations = await parse_operator_notes(payload)
    interpretations = validate_interpretations(payload, interpretations)
    hourly_plan = optimize_schedule(request=payload, interpretations=interpretations)

    total_grid_kwh = sum(entry.grid_kwh for entry in hourly_plan)
    peak_grid_kwh = max((entry.grid_kwh for entry in hourly_plan), default=0.0)
    total_cost_bdt = sum(
        hourly_plan[hour].grid_kwh * payload.hours[hour].tariff_bdt_per_kwh
        for hour in range(24)
    )

    return OptimizationResponse(
        scenario_id=payload.scenario_id,
        directive_interpretation=interpretations,
        hourly_plan=hourly_plan,
        total_grid_kwh=round(total_grid_kwh, 4),
        total_cost_bdt=round(total_cost_bdt, 4),
        peak_grid_kwh=round(peak_grid_kwh, 4),
        plan_summary=(
            "Schedule optimized successfully respecting all operator directives "
            "and battery constraints."
        ),
    )
