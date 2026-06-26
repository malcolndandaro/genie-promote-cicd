"""Databricks Apps entrypoint for the engine API.

Binds the platform-assigned port by reading DATABRICKS_APP_PORT in code — this avoids the
app.yaml env-substitution pitfall (the platform does NOT expand `${VAR:-default}`, which would
crash the app with a literal-string port).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # so `main` is importable

import uvicorn  # noqa: E402
from main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", "8000")))
