"""Runtime settings for the CrossFlow AI V4 LLM-enhanced backend.

This module centralises ALL LLM-related configuration. It only reads
environment variables (optionally overridden by a local `.env` file that is
git-ignored). No real API key is ever hard-coded here, in the README, in the
tests, or in the frontend.

By default the LLM is **DISABLED** (`LLM_ENABLED=false`): the backend runs
exactly like the pure mock version, generating reply drafts from deterministic
rule templates in `service.py`.

To enable (see `docs/LLM_INTEGRATION.md`):

    LLM_ENABLED=true
    LLM_BASE_URL=https://api.example.com/v1
    LLM_API_KEY=sk-...
    LLM_MODEL=some-model

The `.env` file is for local convenience only; it is never committed.
"""
from __future__ import annotations

import os
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _load_dotenv(path: Path | None = None) -> None:
    """Minimal `.env` loader (KEY=VALUE lines, ``#`` comments).

    Does NOT overwrite variables already present in the real environment, so
    process-level env vars always take precedence over a local `.env` file.
    """
    if path is None:
        path = _BACKEND_DIR / ".env"
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        # A missing / unreadable .env must never crash startup.
        pass


_load_dotenv()

LLM_ENABLED: bool = _truthy(os.getenv("LLM_ENABLED"))
LLM_BASE_URL: str = (os.getenv("LLM_BASE_URL") or "").strip()
LLM_API_KEY: str = (os.getenv("LLM_API_KEY") or "").strip()
LLM_MODEL: str = (os.getenv("LLM_MODEL") or "").strip()

# Hard timeout (seconds) applied to EVERY LLM call.
LLM_TIMEOUT_SECONDS: float = 10.0
try:
    LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS") or "10")
except ValueError:
    LLM_TIMEOUT_SECONDS = 10.0


def llm_configured() -> bool:
    """True only when the LLM is enabled AND has the minimum required config."""
    return bool(
        LLM_ENABLED and LLM_BASE_URL and LLM_API_KEY and LLM_MODEL
    )
