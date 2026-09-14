"""Vercel entrypoint. The application lives in backend/closer; this file only
exposes it where Vercel's FastAPI preset looks for `app`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from closer.api.app import app  # noqa: E402

__all__ = ["app"]
