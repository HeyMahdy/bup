# GridWise LLM — Smart Campus Energy Optimization

BUP CSE Fest 2026 preliminary solution. One HTTP API interprets natural-language
operator notes with an LLM, validates them with deterministic guardrails, then
solves a 24-hour microgrid dispatch LP that minimizes grid electricity cost.

## Architecture

```
operator_notes ──► LLM (OpenAI) ──► guardrails ──► PuLP/CBC optimizer ──► hourly_plan
                         │                │
                         ▼                ▼
              directive_interpretation   energy + battery + directive constraints
```

| Module | Role |
|--------|------|
| `bup/main.py` | FastAPI endpoints and controlled error mapping |
| `bup/llm_parser.py` | OpenAI structured parse of operator notes |
| `bup/guardrails.py` | Deterministic contract checks on LLM output |
| `bup/lp_solver.py` | Cost-minimizing 24-hour schedule |
| `bup/models.py` | Request/response schemas |
| `bup/config.py` | Environment / `.env` settings |
| `bup/api_matcher.py` | Public sample-case evaluator |

## Requirements

- Python **≥ 3.12.3**
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- An OpenAI API key with access to the configured model

## Environment variables

Copy `.env.example` to `.env` at the repository root (never commit secrets):

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `OPENAI_API_KEY` | Yes | — | OpenAI credentials for note interpretation |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Chat model used for structured parsing |
| `API_URL` | No | `http://127.0.0.1:8000/optimize-energy` | Target for `api_matcher.py` |

## Local quickstart

```bash
# from repository root
uv sync
cp .env.example .env   # then set OPENAI_API_KEY

cd bup
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

Sample optimize call (uses `payload.json` from the repo root):

```bash
curl -X POST http://127.0.0.1:8000/optimize-energy ^
  -H "Content-Type: application/json" ^
  --data-binary @../payload.json
```

On macOS/Linux, use `\` line continuations instead of `^`.

## Public sample tests

With the API running:

```bash
cd bup
uv run python api_matcher.py
```

Expected: `TEST RUN COMPLETE: 10 passed | 0 failed` against
`BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json`.

## LLM, guardrails, and optimizer

- **LLM role:** Mandatory interpretation of `operator_notes` into
  `directive_interpretation` (OpenAI structured outputs). Temperature `0.0`.
- **Guardrails:** Count/order, `applies`/`no_op` semantics, hour lists, and
  numeric ranges are checked before optimization. Invalid model output never
  silently becomes constraints.
- **Optimizer:** PuLP with the CBC solver. Objective =
  `Σ grid_kwh[h] * tariff_bdt_per_kwh[h]`. Enforces energy balance, battery
  transitions/bounds/rates, end-of-day neutrality, and all applicable
  directives (`solar_reduction`, `minimum_battery_reserve`,
  `no_charge_window`, `no_discharge_window`, `max_grid_window`).

## Dependencies

Declared in `pyproject.toml` / `uv.lock`: FastAPI, Uvicorn, Pydantic, OpenAI,
python-dotenv, PuLP, requests.

## Known limitations

- Requires a reachable OpenAI model during evaluation; provider outages or
  quota exhaustion return a controlled `500` without stack traces or secrets.
- Synchronous CBC solve runs on the request path (acceptable for the 24-hour
  LP; not designed for high concurrency).
- Docker / public deployment artifacts are out of scope for this local package
  (add separately for contest submission if required).
- Do not commit `.env` or API keys. Rotate any key that was shared in chat or
  pasted into tickets.

## Credits

- Contest: BUP CSE Fest 2026 — GridWise LLM preliminary
- Libraries: FastAPI, Pydantic, OpenAI Python SDK, PuLP (CBC), Uvicorn, python-dotenv, requests
