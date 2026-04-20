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

import pyrit
from pyrit.backend.middleware import RequestIdMiddleware, SecurityHeadersMiddleware, register_error_handlers
from pyrit.backend.middleware.auth import EntraAuthMiddleware
from pyrit.backend.routes import attacks, auth, converters, health, labels, media, targets, version
from pyrit.memory import CentralMemory

# Check for development mode from environment variable
DEV_MODE = os.getenv("PYRIT_DEV_MODE", "false").lower() == "true"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle."""
    # Initialization is handled by the pyrit_backend CLI before uvicorn starts.
    # Running 'uvicorn pyrit.backend.main:app' directly is not supported;
    # use 'pyrit_backend' instead.
    try:
        CentralMemory.get_memory_instance()
    except ValueError:
        logger.warning(
            "CentralMemory is not initialized. "
            "Start the server via 'pyrit_backend' CLI instead of running uvicorn directly."
        )
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
app.include_router(labels.router, prefix="/api", tags=["labels"])
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(media.router, prefix="/api", tags=["media"])
app.include_router(version.router, tags=["version"])


def setup_frontend() -> None:
    """Set up frontend static file serving."""
    frontend_path = Path(__file__).parent / "frontend"

    if DEV_MODE:
        # Development mode: frontend served separately by Vite
        print("🔧 Running in DEVELOPMENT mode - frontend should be running on port 3000")
    elif frontend_path.exists():
        # Production mode: serve bundled frontend
        print(f"✅ Serving frontend from {frontend_path}")
        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
    else:
        # Production mode but no frontend found - warn but don't exit
        # This allows API-only usage
        print("⚠️ WARNING: Frontend not found!")
        print(f"   Expected location: {frontend_path}")
        print("   The frontend must be built and included in the package.")
        print("   Run: python build_scripts/prepare_package.py")
        print("   API endpoints will still work but the UI won't be available.")


# Set up frontend at module load time (needed when running via uvicorn)
setup_frontend()
