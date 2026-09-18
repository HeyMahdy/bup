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

## Docker fallback

The image listens on **port 8000**, binds **`0.0.0.0`**, and does **not** bake in
API keys. Pass credentials at runtime.

### Build locally

```bash
docker build -t gridwise-llm:0.1.0 .
```

### Run (verified command)

```bash
docker run --rm -p 8000:8000 \
  -e OPENAI_API_KEY=sk-your-key-here \
  -e OPENAI_MODEL=gpt-4o-mini \
  gridwise-llm:0.1.0
```

Windows PowerShell:

```powershell
docker run --rm -p 8000:8000 `
  -e OPENAI_API_KEY=sk-your-key-here `
  -e OPENAI_MODEL=gpt-4o-mini `
  gridwise-llm:0.1.0
```

Health check after start:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

### Registry image (submission reference)

Local image verified: `gridwise-llm:0.1.0`  
Image digest (local build): `sha256:2e3e00e7896bc6a95f3f9484afb2fb89706536a188905f1130d121ea4c84c019`

Tag and push to your registry (replace with your namespace, then keep the
pushed tag/digest in the submission form):

```bash
docker tag gridwise-llm:0.1.0 ghcr.io/heymahdy/gridwise-llm:0.1.0
docker push ghcr.io/heymahdy/gridwise-llm:0.1.0
```

Pull / run fallback for organizers (after push):

```bash
docker pull ghcr.io/heymahdy/gridwise-llm:0.1.0
docker run --rm -p 8000:8000 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_MODEL=gpt-4o-mini \
  ghcr.io/heymahdy/gridwise-llm:0.1.0
```

Required runtime environment variables: `OPENAI_API_KEY` (required),
`OPENAI_MODEL` (optional, default `gpt-4o-mini`). Exposed port: **8000**.
Binds to `0.0.0.0` (no secrets in the image layers).

## Render deploy (public API)

If the service fails with `.venv/bin/uvicorn: No such file or directory`, the
Start Command is wrong. Use these settings (also in `render.yaml`):

| Setting | Value |
|---------|-------|
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn main:app --app-dir bup --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/health` |

Environment variables on Render: `OPENAI_API_KEY` (required), `OPENAI_MODEL`
(optional). Do **not** prefix the start command with `.venv/bin/`.

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
- Do not commit `.env` or API keys. Never bake secrets into the Docker image.
- Rotate any key that was shared in chat or pasted into tickets.

## Credits

- Contest: BUP CSE Fest 2026 — GridWise LLM preliminary
- Libraries: FastAPI, Pydantic, OpenAI Python SDK, PuLP (CBC), Uvicorn, python-dotenv, requests
