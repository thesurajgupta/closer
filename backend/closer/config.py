"""Runtime configuration. Everything is env-driven; nothing secret is ever
baked into code, prompts, or the client bundle."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent
DATA_DIR = Path(__file__).resolve().parent / "demo" / "data"


def _env(name: str, default: str) -> str:
    """Read an env var, treating a blank value as unset. Hosting dashboards
    (Vercel's .env.example import, for one) create keys with empty values."""
    raw = os.getenv(name)
    return raw.strip() if raw and raw.strip() else default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _db_path() -> str:
    configured = _env("CLOSER_DB", str(PROJECT_ROOT / "closer.db"))
    # Serverless platforms (Vercel, Lambda) mount the code read-only; /tmp is the
    # only writable path, so any other location is redirected there.
    serverless = os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME")
    if serverless and not configured.startswith("/tmp"):
        return "/tmp/closer.db"
    return configured


@dataclass
class Settings:
    # DEMO uses the synthetic household dataset and local connectors.
    # LIVE swaps connectors for real adapters behind credentials.
    mode: str = field(default_factory=lambda: _env("CLOSER_MODE", "demo").lower())

    # Deterministic wall clock for a reproducible demo.
    demo_now: str = field(default_factory=lambda: _env("CLOSER_DEMO_NOW", "2026-09-10T09:00:00"))

    db_path: str = field(default_factory=lambda: _db_path())

    # Model provider: "auto" | "deterministic" | "bedrock" | "anthropic"
    model_provider: str = field(default_factory=lambda: _env("CLOSER_MODEL_PROVIDER", "auto").lower())
    bedrock_model_id: str = field(
        default_factory=lambda: _env("CLOSER_BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    )
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "us-west-2"))
    anthropic_model_id: str = field(
        default_factory=lambda: _env("CLOSER_ANTHROPIC_MODEL_ID", "claude-sonnet-4-5-20250929")
    )

    # Safety rails that a prompt can never widen.
    max_agent_iterations: int = field(default_factory=lambda: int(_env("CLOSER_MAX_ITERATIONS", "14")))
    max_action_amount: float = field(default_factory=lambda: float(_env("CLOSER_MAX_ACTION_AMOUNT", "25000")))
    tool_timeout_seconds: int = field(default_factory=lambda: int(_env("CLOSER_TOOL_TIMEOUT", "20")))
    max_retries: int = field(default_factory=lambda: int(_env("CLOSER_MAX_RETRIES", "3")))

    # Background execution
    scheduler_enabled: bool = field(default_factory=lambda: _bool("CLOSER_SCHEDULER", False))
    discovery_cron_minutes: int = field(default_factory=lambda: int(_env("CLOSER_DISCOVERY_MINUTES", "1440")))
    follow_up_cron_minutes: int = field(default_factory=lambda: int(_env("CLOSER_FOLLOW_UP_MINUTES", "360")))

    seed: int = field(default_factory=lambda: int(_env("CLOSER_SEED", "42")))

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    def resolve_model_provider(self) -> str:
        """Pick the model provider actually available in this environment."""
        if self.model_provider != "auto":
            return self.model_provider
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE") or os.getenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"):
            return "bedrock"
        return "deterministic"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
