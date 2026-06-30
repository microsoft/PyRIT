# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Async REST client for the PyRIT backend API.

Uses ``httpx`` internally but defers the import to method calls so that
importing this module does not trigger the import-guard ban on ``httpx``
at CLI parse time.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


class ServerNotAvailableError(Exception):
    """Raised when the CLI cannot reach the PyRIT backend server."""


class PyRITApiClient:
    """
    Lightweight async REST client for the PyRIT backend.

    No heavy pyrit imports.

    Use as an async context manager::

        async with PyRITApiClient(base_url="http://localhost:8000") as client:
            scenarios = await client.list_scenarios_async()
    """

    def __init__(self, *, base_url: str, request_timeout: float | None = None) -> None:
        """
        Initialize the API client.

        Args:
            base_url (str): Base URL of the PyRIT backend (e.g., ``"http://localhost:8000"``).
            request_timeout (float | None): Read timeout in seconds applied to every
                non-polling request (catalog, results, cancel, start, etc.). Polling
                the live scenario-run endpoint always uses ``read=None`` regardless
                of this value, because the server may legitimately take many seconds
                to respond while a scenario is executing. Defaults to ``60.0``.
        """
        self._base_url = base_url.rstrip("/")
        self._request_timeout = request_timeout if request_timeout is not None else 60.0
        self._client: Any = None  # httpx.AsyncClient (typed Any to avoid top-level import)

    async def __aenter__(self) -> PyRITApiClient:
        """
        Open the underlying ``httpx.AsyncClient``.

        Returns:
            PyRITApiClient: ``self``, with the HTTP client opened.
        """
        import httpx

        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._request_timeout)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Close the underlying HTTP client."""
        await self.close_async()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check_async(self) -> bool:
        """
        Probe the server health endpoint.

        Returns:
            bool: ``True`` if the server returned a healthy response.
        """
        import httpx

        try:
            client = self._get_client()
            resp = await client.get("/api/health")
            return resp.status_code == 200
        except httpx.ConnectError:
            return False
        except Exception:
            _logger.debug("Health check failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Scenarios
    # ------------------------------------------------------------------

    async def list_scenarios_async(self, *, limit: int = 200) -> dict[str, Any]:
        """
        List all available scenarios.

        Returns:
            dict: ``ListRegisteredScenariosResponse`` payload.
        """
        return await self._get_json_async(path="/api/scenarios/catalog", params={"limit": limit})

    async def get_scenario_async(self, *, scenario_name: str) -> dict[str, Any] | None:
        """
        Get metadata for a single scenario.

        Returns:
            dict | None: ``RegisteredScenario`` payload, or ``None`` if 404.

        Raises:
            httpx.HTTPStatusError: For non-404 HTTP error responses.
        """
        import httpx

        try:
            return await self._get_json_async(path=f"/api/scenarios/catalog/{scenario_name}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    # ------------------------------------------------------------------
    # Initializers
    # ------------------------------------------------------------------

    async def list_initializers_async(self, *, limit: int = 200) -> dict[str, Any]:
        """
        List all available initializers.

        Returns:
            dict: ``ListRegisteredInitializersResponse`` payload.
        """
        return await self._get_json_async(path="/api/initializers", params={"limit": limit})

    async def register_initializer_async(self, *, name: str, script_content: str) -> dict[str, Any]:
        """
        Register a custom initializer by uploading Python source code.

        Args:
            name: Registry name for the initializer.
            script_content: Python source code containing a ``PyRITInitializer`` subclass.

        Returns:
            dict: ``RegisteredInitializer`` payload.

        Raises:
            ServerNotAvailableError: If custom initializers are disabled (403).
        """
        client = self._get_client()
        resp = await client.post(
            "/api/initializers",
            json={"name": name, "script_content": script_content},
        )
        if resp.status_code == 403:
            detail = self._response_detail(resp) or "Custom initializer operations are disabled on the server."
            raise ServerNotAvailableError(detail)
        self._raise_for_status(resp)
        return resp.json()

    # ------------------------------------------------------------------
    # Targets
    # ------------------------------------------------------------------

    async def list_targets_async(self, *, limit: int = 200) -> dict[str, Any]:
        """
        List all available targets.

        Returns:
            dict: ``TargetListResponse`` payload.
        """
        return await self._get_json_async(path="/api/targets", params={"limit": limit})

    # ------------------------------------------------------------------
    # Scenario runs
    # ------------------------------------------------------------------

    async def start_scenario_run_async(self, *, request: dict[str, Any]) -> dict[str, Any]:
        """
        Start a new scenario run.

        Args:
            request: ``RunScenarioRequest``-shaped dict.

        Returns:
            dict: ``ScenarioRunSummary`` payload.
        """
        client = self._get_client()
        resp = await client.post("/api/scenarios/runs", json=request)
        self._raise_for_status(resp)
        return resp.json()

    async def get_scenario_run_async(self, *, scenario_result_id: str) -> dict[str, Any]:
        """
        Get the current status of a scenario run.

        This is the endpoint the CLI polls while waiting for a run to finish.
        It uses ``read=None`` (wait indefinitely for a response) so a server
        busy executing a long-running scenario doesn't trip the client's
        default read timeout. The other endpoints keep the configured timeout.

        Returns:
            dict: ``ScenarioRunSummary`` payload.

        Raises:
            ServerNotAvailableError: If the server cannot be reached.
        """
        import httpx

        client = self._get_client()
        try:
            resp = await client.get(
                f"/api/scenarios/runs/{scenario_result_id}",
                params=None,
                timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0),
            )
        except httpx.ConnectError as exc:
            raise ServerNotAvailableError(
                f"Cannot connect to PyRIT server at {self._base_url}.\n"
                "Hint: Use '--start-server' to launch a local backend, "
                "or pass '--server-url <url>'."
            ) from exc
        self._raise_for_status(resp)
        return resp.json()

    async def get_scenario_run_results_async(self, *, scenario_result_id: str) -> dict[str, Any]:
        """
        Get detailed results for a completed scenario run.

        Returns:
            dict: ``ScenarioResult.model_dump(mode="json", by_alias=True)`` payload.
        """
        return await self._get_json_async(path=f"/api/scenarios/runs/{scenario_result_id}/results")

    async def cancel_scenario_run_async(self, *, scenario_result_id: str) -> dict[str, Any]:
        """
        Cancel a running scenario.

        Returns:
            dict: Updated ``ScenarioRunSummary`` payload.
        """
        client = self._get_client()
        resp = await client.post(f"/api/scenarios/runs/{scenario_result_id}/cancel")
        self._raise_for_status(resp)
        return resp.json()

    async def list_scenario_runs_async(self, *, limit: int = 100) -> dict[str, Any]:
        """
        List tracked scenario runs.

        Returns:
            dict: ``ScenarioRunListResponse`` payload.
        """
        return await self._get_json_async(path="/api/scenarios/runs", params={"limit": limit})

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close_async(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """
        Return the ``httpx.AsyncClient``, raising if not opened.

        Returns:
            Any: The opened ``httpx.AsyncClient`` instance.

        Raises:
            ServerNotAvailableError: If the client has not been opened via ``__aenter__``.
        """
        if self._client is None:
            raise ServerNotAvailableError(
                f"API client is not connected to {self._base_url}. "
                "Use 'async with PyRITApiClient(...)' or call __aenter__ first."
            )
        return self._client

    async def _get_json_async(self, *, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        GET a JSON endpoint and return the parsed response.

        Returns:
            dict[str, Any]: The parsed JSON response body.

        Raises:
            ServerNotAvailableError: On connection failure.
        """
        import httpx

        client = self._get_client()
        try:
            resp = await client.get(path, params=params)
        except httpx.ConnectError as exc:
            raise ServerNotAvailableError(
                f"Cannot connect to PyRIT server at {self._base_url}.\n"
                "Hint: Use '--start-server' to launch a local backend, "
                "or pass '--server-url <url>'."
            ) from exc
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _response_detail(resp: Any) -> str | None:
        """
        Extract a user-facing error detail from a response body.

        Prefer FastAPI-style JSON ``detail`` values, then fall back to a plain
        text response body. Non-string mock/proxy attributes are ignored so
        callers can still use their default error messages.

        Returns:
            str | None: Extracted detail text, or ``None`` if the body has no
                usable error detail.
        """
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            detail_value = payload.get("detail")
            if isinstance(detail_value, str) and detail_value.strip():
                return detail_value
            if detail_value is not None:
                return str(detail_value)

        text = getattr(resp, "text", "")
        if isinstance(text, bytes):
            text = text.decode(errors="replace")
        if isinstance(text, str):
            text = text.strip()
            if text:
                return text
        return None

    @staticmethod
    def _raise_for_status(resp: Any) -> None:
        """
        Raise an HTTP error with the response body appended to the message.

        Behaves like ``httpx.Response.raise_for_status`` but includes the
        ``detail`` field from the response body (falling back to raw text) so
        CLI users can see the actual server-side reason instead of just the
        HTTP status line. The exception type is preserved so existing callers
        / tests continue to work.

        Raises:
            httpx.HTTPStatusError: When the response carries a 4xx or 5xx status.
        """
        import httpx

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = PyRITApiClient._response_detail(resp)
            if detail is None:
                raise
            message = f"{exc}: {detail}"
            raise httpx.HTTPStatusError(message, request=exc.request, response=exc.response) from exc
