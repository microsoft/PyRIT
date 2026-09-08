# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Async REST client for the PyRIT backend API.

Returns typed ``pyrit.models`` objects (canonical wire-data types defined in
``pyrit.models.catalog`` plus ``ScenarioResult``). Heavy imports — ``httpx``
and ``pyrit.models`` — are deferred to method bodies so that importing this
module does not trigger the CLI parse-time import-guard ban on either.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pyrit.cli._auth import AuthMode, TokenProvider
    from pyrit.models import ScenarioResult
    from pyrit.models.catalog import (
        RegisteredInitializer,
        RegisteredScenario,
        RunScenarioRequest,
        ScenarioRunListItem,
        ScenarioRunSummary,
        TargetInstance,
    )

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

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout: float | None = None,
        auth_mode: AuthMode = "none",
        interactive: bool | None = None,
    ) -> None:
        """
        Initialize the API client.

        Args:
            base_url (str): Base URL of the PyRIT backend (e.g., ``"http://localhost:8000"``).
            request_timeout (float | None): Read timeout in seconds applied to every
                non-polling request (catalog, results, cancel, start, etc.). Polling
                the live scenario-run endpoint always uses ``read=None`` regardless
                of this value, because the server may legitimately take many seconds
                to respond while a scenario is executing. Defaults to ``60.0``.
            auth_mode: Authentication behavior for protected remote backends.
            interactive: Optional terminal-interactivity override.
        """
        self._base_url = base_url.rstrip("/")
        self._request_timeout = request_timeout if request_timeout is not None else 60.0
        self._auth_mode = auth_mode
        self._interactive = interactive
        self._client: Any = None  # httpx.AsyncClient (typed Any to avoid top-level import)
        self._token_provider: TokenProvider | None = None

    async def __aenter__(self) -> PyRITApiClient:
        """
        Open the underlying ``httpx.AsyncClient``.

        Returns:
            PyRITApiClient: ``self``, with the HTTP client opened.

        Raises:
            CliAuthenticationError: If authentication discovery or login fails.
            httpx.HTTPError: If the authentication discovery request fails.
        """
        import httpx

        client_kwargs: dict[str, Any] = {
            "base_url": self._base_url,
            "timeout": self._request_timeout,
        }
        if self._auth_mode != "none":
            client_kwargs["event_hooks"] = {"request": [self._add_authorization_header_async]}
        self._client = httpx.AsyncClient(**client_kwargs)
        if self._auth_mode != "none":
            from pyrit.cli._auth import CliAuthenticationError

            try:
                await self._configure_authentication_async()
            except (CliAuthenticationError, httpx.HTTPError):
                await self.close_async()
                raise
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
            if resp.status_code != 200:
                return False
            payload = resp.json()
            return bool(payload.get("status") == "healthy" and payload.get("service") == "pyrit-backend")
        except httpx.ConnectError:
            return False
        except Exception:
            _logger.debug("Health check failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Scenarios
    # ------------------------------------------------------------------

    async def list_scenarios_async(self, *, limit: int = 200) -> list[RegisteredScenario]:
        """
        List all available scenarios.

        Returns:
            list[RegisteredScenario]: All scenarios in the catalog.
        """
        from pyrit.models.catalog import RegisteredScenario

        payload = await self._get_json_async(path="/api/scenarios/catalog", params={"limit": limit})
        return [RegisteredScenario.model_validate(item) for item in payload.get("items", [])]

    async def get_scenario_async(self, *, scenario_name: str) -> RegisteredScenario | None:
        """
        Get metadata for a single scenario.

        Returns:
            RegisteredScenario | None: The scenario, or ``None`` if 404.

        Raises:
            httpx.HTTPStatusError: For non-404 HTTP error responses.
        """
        import httpx

        from pyrit.models.catalog import RegisteredScenario

        try:
            payload = await self._get_json_async(path=f"/api/scenarios/catalog/{scenario_name}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return RegisteredScenario.model_validate(payload)

    # ------------------------------------------------------------------
    # Initializers
    # ------------------------------------------------------------------

    async def list_initializers_async(self, *, limit: int = 200) -> list[RegisteredInitializer]:
        """
        List all available initializers.

        Returns:
            list[RegisteredInitializer]: All initializers in the catalog.
        """
        from pyrit.models.catalog import RegisteredInitializer

        payload = await self._get_json_async(path="/api/initializers", params={"limit": limit})
        return [RegisteredInitializer.model_validate(item) for item in payload.get("items", [])]

    async def register_initializer_async(self, *, name: str, script_content: str) -> RegisteredInitializer:
        """
        Register a custom initializer by uploading Python source code.

        Args:
            name: Registry name for the initializer.
            script_content: Python source code containing a ``PyRITInitializer`` subclass.

        Returns:
            RegisteredInitializer: The newly registered initializer.

        Raises:
            ServerNotAvailableError: If custom initializers are disabled (403).
        """
        from pyrit.models.catalog import RegisteredInitializer

        client = self._get_client()
        resp = await client.post(
            "/api/initializers",
            json={"name": name, "script_content": script_content},
        )
        if resp.status_code == 403:
            detail = self._response_detail(resp) or "Custom initializer operations are disabled on the server."
            raise ServerNotAvailableError(detail)
        self._raise_for_status(resp)
        return RegisteredInitializer.model_validate(resp.json())

    # ------------------------------------------------------------------
    # Targets
    # ------------------------------------------------------------------

    async def list_targets_async(self, *, limit: int = 200) -> list[TargetInstance]:
        """
        List all available targets.

        Returns:
            list[TargetInstance]: All targets registered on the server.
        """
        from pyrit.models.catalog import TargetInstance

        payload = await self._get_json_async(path="/api/targets", params={"limit": limit})
        return [TargetInstance.model_validate(item) for item in payload.get("items", [])]

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    async def list_datasets_async(self) -> dict[str, Any]:
        """
        List all available datasets.

        Returns:
            dict: ``DatasetListResponse`` payload.
        """
        return await self._get_json_async(path="/api/datasets")

    # ------------------------------------------------------------------
    # Converters
    # ------------------------------------------------------------------

    async def list_converters_async(self) -> dict[str, Any]:
        """
        List all registered converter instances.

        Returns:
            dict: ``ConverterInstanceListResponse`` payload.
        """
        return await self._get_json_async(path="/api/converters")

    # ------------------------------------------------------------------
    # Scenario runs
    # ------------------------------------------------------------------

    async def start_scenario_run_async(self, *, request: RunScenarioRequest) -> ScenarioRunSummary:
        """
        Start a new scenario run.

        Args:
            request: Typed run request describing the scenario, initializers, and overrides.

        Returns:
            ScenarioRunSummary: The newly-created scenario run.
        """
        from pyrit.models.catalog import ScenarioRunSummary

        client = self._get_client()
        resp = await client.post(
            "/api/scenarios/runs",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        self._raise_for_status(resp)
        return ScenarioRunSummary.model_validate(resp.json())

    async def get_scenario_run_async(self, *, scenario_result_id: str) -> ScenarioRunSummary:
        """
        Get the current status of a scenario run.

        This is the endpoint the CLI polls while waiting for a run to finish.
        It uses ``read=None`` (wait indefinitely for a response) so a server
        busy executing a long-running scenario doesn't trip the client's
        default read timeout. The other endpoints keep the configured timeout.

        Returns:
            ScenarioRunSummary: The current state of the scenario run.

        Raises:
            ServerNotAvailableError: If the server cannot be reached.
        """
        import httpx

        from pyrit.models.catalog import ScenarioRunSummary

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
        return ScenarioRunSummary.model_validate(resp.json())

    async def get_scenario_run_results_async(self, *, scenario_result_id: str) -> ScenarioResult:
        """
        Get detailed results for a completed scenario run.

        Returns:
            ScenarioResult: The full scenario result deserialized from the server payload.
        """
        from pyrit.models import ScenarioResult

        payload = await self._get_json_async(path=f"/api/scenarios/runs/{scenario_result_id}/results")
        return ScenarioResult.model_validate(payload)

    async def cancel_scenario_run_async(self, *, scenario_result_id: str) -> ScenarioRunSummary:
        """
        Cancel a running scenario.

        Returns:
            ScenarioRunSummary: Updated summary reflecting the cancellation request.
        """
        from pyrit.models.catalog import ScenarioRunSummary

        client = self._get_client()
        resp = await client.post(f"/api/scenarios/runs/{scenario_result_id}/cancel")
        self._raise_for_status(resp)
        return ScenarioRunSummary.model_validate(resp.json())

    async def list_scenario_runs_async(self, *, limit: int = 100) -> list[ScenarioRunListItem]:
        """
        List tracked scenario runs.

        Returns:
            list[ScenarioRunListItem]: All tracked scenario runs.

        Raises:
            ValueError: If the requested limit is invalid or a paginated response has no cursor.
        """
        from pyrit.models.catalog import ScenarioRunListItem

        if limit < 1:
            raise ValueError("Scenario history limit must be positive.")

        runs: list[ScenarioRunListItem] = []
        cursor: str | None = None
        while len(runs) < limit:
            params: dict[str, int | str] = {"limit": min(100, limit - len(runs))}
            if cursor is not None:
                params["cursor"] = cursor
            payload = await self._get_json_async(path="/api/scenarios/runs", params=params)
            runs.extend(ScenarioRunListItem.model_validate(item) for item in payload.get("items", []))
            pagination = payload.get("pagination", {})
            if not pagination.get("has_more"):
                break
            next_cursor = pagination.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise ValueError("Scenario history response is missing its next-page cursor.")
            cursor = next_cursor
        return runs[:limit]

    # ------------------------------------------------------------------
    # Attacks / conversations
    # ------------------------------------------------------------------

    async def get_conversation_messages_async(
        self,
        *,
        attack_result_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        """
        Get all messages for one conversation belonging to an attack result.

        Returns the raw ``ConversationMessagesResponse`` payload (rather than a
        typed backend view model) so the thin client stays decoupled from
        ``pyrit.backend.models``; ``pyrit.cli._results`` maps it into its own
        view payload.

        Args:
            attack_result_id (str): The attack result whose conversation to read.
            conversation_id (str): The conversation whose messages to return.

        Returns:
            dict[str, Any]: The ``ConversationMessagesResponse`` payload
                (``conversation_id`` plus an ordered ``messages`` list).
        """
        return await self._get_json_async(
            path=f"/api/attacks/{attack_result_id}/messages",
            params={"conversation_id": conversation_id},
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close_async(self) -> None:
        """Close the underlying HTTP client."""
        client = self._client
        token_provider = self._token_provider
        self._client = None
        self._token_provider = None

        try:
            if client is not None:
                await client.aclose()
        finally:
            if token_provider is not None:
                await token_provider.close_async()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _configure_authentication_async(self) -> None:
        """
        Discover backend authentication requirements and select a credential.

        Raises:
            CliAuthenticationError: If the server contract or selected credential is invalid.
            httpx.HTTPError: If the discovery request fails.
        """
        from pyrit.cli._auth import BackendAuthConfig, CliAuthenticationError, create_token_provider_async

        client = self._get_client()
        response = await client.get("/api/auth/config")
        if response.status_code == 404:
            return
        self._raise_for_status(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CliAuthenticationError("The server returned invalid JSON from /api/auth/config.") from exc

        auth_config = BackendAuthConfig.from_payload(payload)
        if auth_config.enabled and not self._uses_secure_auth_transport():
            raise CliAuthenticationError(
                "Refusing to send an Entra access token over a non-HTTPS connection. Use HTTPS for remote backends."
            )
        self._token_provider = await create_token_provider_async(
            auth_config=auth_config,
            auth_mode=self._auth_mode,
            interactive=self._interactive,
        )

    def _uses_secure_auth_transport(self) -> bool:
        """Return whether the server URL protects bearer tokens in transit."""
        parsed = urlparse(self._base_url)
        if parsed.scheme == "https":
            return True
        return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}

    async def _add_authorization_header_async(self, request: Any) -> None:
        """Attach a current bearer token to protected backend requests."""
        if self._token_provider is None or request.url.path in {"/api/auth/config", "/api/health"}:
            return
        token = await self._token_provider.get_token_async()
        request.headers["Authorization"] = f"Bearer {token}"

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
            stripped_text: str = text.strip()
            if stripped_text:
                return stripped_text
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
