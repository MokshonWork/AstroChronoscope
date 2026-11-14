"""Planet X SPHEREx data acquisition backend entrypoint.

Re-exports the full FastAPI application from app.main so that both:
  - `fastapi dev main.py` / `uvicorn main:app`
  - `fastapi dev app/main.py` / `uvicorn app.main:app`
load all SPHEREx routes, health telemetry, and static file endpoints.
"""

from app.main import app

__all__ = ["app"]
