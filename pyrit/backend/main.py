# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
FastAPI application entry point for PyRIT backend.
"""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

import pyrit
from pyrit.backend.middleware import RequestIdMiddleware, SecurityHeadersMiddleware, register_error_handlers
from pyrit.backend.middleware.auth import EntraAuthMiddleware
from pyrit.backend.routes import (
    attacks,
    auth,
    converters,
    health,
    initializers,
    labels,
    media,
    scenarios,
    targets,
    version,
)
from pyrit.setup.configuration_loader import ConfigurationLoader

# Check for development mode from environment variable
DEV_MODE = os.getenv("PYRIT_DEV_MODE", "false").lower() == "true"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Initialize PyRIT on startup using the config file, then yield.

    Config resolution order:
    1. ``PYRIT_CONFIG_FILE`` env var (if set)
    2. ``~/.pyrit/.pyrit_conf`` (if it exists)
    3. Built-in defaults (SQLite, no initializers)
    """
    config_file_env = os.getenv("PYRIT_CONFIG_FILE")
    config_file = Path(config_file_env) if config_file_env else None

    config = ConfigurationLoader.load_with_overrides(config_file=config_file)
    await config.initialize_pyrit_async()

    # Expose config values to route handlers via app.state
    default_labels: dict[str, str] = {}
    if config.operator:
        default_labels["operator"] = config.operator
    if config.operation:
        default_labels["operation"] = config.operation
    app.state.default_labels = default_labels
    app.state.max_concurrent_scenario_runs = config.max_concurrent_scenario_runs
    app.state.allow_custom_initializers = config.allow_custom_initializers

    if config.allow_custom_initializers:
        logger.warning("Custom initializer registration is ENABLED (allow_custom_initializers: true).")

    # Mount the bundled frontend (or print a dev/missing-frontend notice).
    # Done here rather than at module load so test imports of `pyrit.backend.main`
    # don't emit noise and don't perform filesystem side effects.
    setup_frontend()

    yield


app = FastAPI(
    title="PyRIT API",
    description="Python Risk Identification Tool for LLMs - REST API",
    version=pyrit.__version__,
    lifespan=lifespan,
    docs_url="/docs" if DEV_MODE else None,
    redoc_url="/redoc" if DEV_MODE else None,
    openapi_url="/openapi.json" if DEV_MODE else None,
)

# Register RFC 7807 error handlers
register_error_handlers(app)

# Security response headers (CSP, HSTS, X-Frame-Options, etc.)
# Registered first so headers are applied even on early returns (e.g. auth 401s)
app.add_middleware(SecurityHeadersMiddleware, dev_mode=DEV_MODE)

# Attach X-Request-ID to every request/response for log correlation
app.add_middleware(RequestIdMiddleware)

# Entra ID JWT validation (PKCE — no client secrets needed)
# Disabled automatically if ENTRA_TENANT_ID / ENTRA_CLIENT_ID are not set
app.add_middleware(EntraAuthMiddleware)


# Configure CORS
_default_origins = "http://localhost:3000,http://localhost:5173"
_cors_origins = [o.strip() for o in os.getenv("PYRIT_CORS_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


# Include API routes
app.include_router(attacks.router, prefix="/api", tags=["attacks"])
app.include_router(targets.router, prefix="/api", tags=["targets"])
app.include_router(converters.router, prefix="/api", tags=["converters"])
app.include_router(scenarios.router, prefix="/api", tags=["scenarios"])
app.include_router(initializers.router, prefix="/api", tags=["initializers"])
app.include_router(labels.router, prefix="/api", tags=["labels"])
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(media.router, prefix="/api", tags=["media"])
app.include_router(version.router, tags=["version"])


class SPAStaticFiles(StaticFiles):
    """Serve index.html for unmatched non-API paths so client-side routes survive a refresh."""

    async def get_response(self, path: str, scope: Scope) -> Response:  # pyrit-async-suffix-exempt
        """Return the static file for ``path``, falling back to index.html for unmatched non-API paths."""
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # ``path`` arrives OS-normalized (backslashes on Windows), so compare
            # against a forward-slash form to reliably detect the /api namespace.
            normalized = path.replace(os.sep, "/")
            if exc.status_code == 404 and not (normalized == "api" or normalized.startswith("api/")):
                return await super().get_response("index.html", scope)
            raise


def setup_frontend() -> None:
    """Set up frontend static file serving."""
    frontend_path = Path(__file__).parent / "frontend"

    if DEV_MODE:
        # Development mode: frontend served separately by Vite
        print("🔧 Running in DEVELOPMENT mode - frontend should be running on port 3000")
    elif frontend_path.exists():
        # Production mode: serve bundled frontend
        print(f"✅ Serving frontend from {frontend_path}")
        app.mount("/", SPAStaticFiles(directory=str(frontend_path), html=True), name="frontend")
    else:
        # Production mode but no frontend found - warn but don't exit
        # This allows API-only usage
        print("⚠️ WARNING: Frontend not found!")
        print(f"   Expected location: {frontend_path}")
        print("   The frontend must be built and included in the package.")
        print("   Run: python build_scripts/prepare_package.py")
        print("   API endpoints will still work but the UI won't be available.")
