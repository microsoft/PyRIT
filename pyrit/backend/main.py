# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
FastAPI application entry point for PyRIT backend.
"""

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import pyrit
from pyrit.backend.routes import health, version
from pyrit.setup.initialization import initialize_pyrit_async

# Check for development mode from environment variable
DEV_MODE = os.getenv("PYRIT_DEV_MODE", "false").lower() == "true"

app = FastAPI(
    title="PyRIT API",
    description="Python Risk Identification Tool for LLMs - REST API",
    version=pyrit.__version__,
)


# Initialize PyRIT on startup to load .env and .env.local files
@app.on_event("startup")
async def startup_event_async() -> None:
    """Initialize PyRIT on application startup."""
    # Use in-memory to avoid database initialization delays
    await initialize_pyrit_async(memory_db_type="SQLite")


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],  # Vite default ports
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routers
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(version.router, tags=["version"])


def setup_frontend() -> None:
    """Set up frontend static file serving (only called when running as main script)."""
    frontend_path = Path(__file__).parent / "frontend"

    if DEV_MODE:
        # Development mode: frontend served separately by Vite
        print("🔧 Running in DEVELOPMENT mode - frontend should be running on port 3000")
    elif frontend_path.exists():
        # Production mode: serve bundled frontend
        print(f"✅ Serving frontend from {frontend_path}")
        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
    else:
        # Production mode but no frontend found - this is an error
        print("❌ ERROR: Frontend not found!")
        print(f"   Expected location: {frontend_path}")
        print("   The frontend must be built and included in the package.")
        print("   Run: python build_scripts/prepare_package.py")
        sys.exit(1)


@app.exception_handler(Exception)
async def global_exception_handler_async(request: object, exc: Exception) -> JSONResponse:
    """
    Handle all unhandled exceptions globally.

    Returns:
        JSONResponse: Error response with 500 status code.
    """
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )


if __name__ == "__main__":
    import uvicorn

    setup_frontend()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
