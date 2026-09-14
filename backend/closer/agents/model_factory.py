"""Model provider selection.

CLOSER's agents are ordinary Strands agents. Which model backs them is decided
here and nowhere else, so the same agent code runs against Claude on Amazon
Bedrock, the Anthropic API, or the local deterministic planner used for a
credential-free demo.
"""

from __future__ import annotations

from typing import Any

from strands.models.model import Model

from ..config import get_settings
from .local_model import LocalPlannerModel


def build_model(role: str, seed_facts: dict[str, Any] | None = None) -> tuple[Model, str]:
    """Return (model, provider_name) for a specialist role."""
    settings = get_settings()
    provider = settings.resolve_model_provider()

    if provider == "bedrock":  # pragma: no cover - requires AWS credentials
        from strands.models import BedrockModel

        return (
            BedrockModel(
                model_id=settings.bedrock_model_id,
                region_name=settings.aws_region,
                temperature=0.0,
                additional_request_fields={},
            ),
            f"bedrock:{settings.bedrock_model_id}",
        )

    if provider == "anthropic":  # pragma: no cover - requires an API key
        from strands.models.anthropic import AnthropicModel

        return (
            AnthropicModel(model_id=settings.anthropic_model_id, params={"temperature": 0.0, "max_tokens": 2048}),
            f"anthropic:{settings.anthropic_model_id}",
        )

    return LocalPlannerModel(role=role, seed_facts=seed_facts,
                             max_steps=settings.max_agent_iterations), "closer-local-planner"


def provider_label() -> str:
    provider = get_settings().resolve_model_provider()
    return {
        "bedrock": "Claude on Amazon Bedrock",
        "anthropic": "Claude (Anthropic API)",
        "deterministic": "Local deterministic planner (no credentials required)",
    }.get(provider, provider)
