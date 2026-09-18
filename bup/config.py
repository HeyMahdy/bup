"""Application configuration loaded from environment / .env files."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_APP_DIR = Path(__file__).resolve().parent

# Prefer repo-root .env, then local bup/.env overrides.
load_dotenv(_ROOT / ".env")
load_dotenv(_APP_DIR / ".env", override=True)


@lru_cache(maxsize=1)
def get_settings() -> "Settings":
    return Settings()


class Settings:
    """Runtime settings for the GridWise API."""

    def __init__(self) -> None:
        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
        self.openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

    @property
    def has_openai_credentials(self) -> bool:
        return bool(self.openai_api_key)
