# GridWise LLM API — contest Docker fallback (no secrets baked in)
FROM python:3.12.3-slim-bookworm

WORKDIR /app

# Install uv for reproducible dependency install from the lockfile.
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Dependency layer (cached unless lock/project metadata changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application source (flat modules under bup/)
COPY bup/ ./bup/

WORKDIR /app/bup

EXPOSE 8000

# Bind all interfaces so the judging harness can reach the service.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
