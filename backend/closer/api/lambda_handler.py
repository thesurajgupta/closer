"""Lambda entrypoint for the API, via Mangum."""

from __future__ import annotations

try:  # pragma: no cover - only present in the deployed image
    from mangum import Mangum

    from .app import app

    handler = Mangum(app, lifespan="auto")
except ImportError:  # pragma: no cover
    def handler(event, context):  # type: ignore[misc]
        raise RuntimeError("mangum is required to run the CLOSER API on Lambda")
