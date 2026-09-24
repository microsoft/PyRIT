# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pyrit.models import ComponentIdentifier, Message, construct_response_from_request
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from copilot import CopilotClient, CopilotSession, GetStatusResponse, SystemMessageConfig


@dataclass
class _ConversationState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session: "CopilotSession | None" = None
    retired: bool = False


class GitHubCopilotTarget(PromptTarget):
    """
    Send text requests through the GitHub Copilot SDK.

    Capture INFO logs for session mapping and SDK/runtime version diagnostics.
    """

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    )

    def __init__(
        self,
        *,
        model_name: str,
        github_token: str | None = None,
        working_directory: str | Path | None = None,
        retain_session: bool = False,
        response_timeout_seconds: float = 60.0,
        max_requests_per_minute: int | None = None,
    ) -> None:
        """
        Initialize the target with an explicit token or normal SDK login discovery.

        Args:
            model_name (str): Explicit Copilot model ID.
            github_token (str | None): Nonblank GitHub token, forwarded unchanged with precedence over other
                authentication methods. Defaults to None for SDK environment-token and saved-login discovery.
            working_directory (str | Path | None): Existing local directory; no Git repository is required.
                Supplied paths are resolved once against the current directory at construction and included
                in saved target identifiers. Defaults to None for the SDK's current directory at each client start.
            retain_session (bool): Keep each Copilot session on disk instead of deleting it. Retained
                session IDs are logged so they can be found later. Defaults to False.
            response_timeout_seconds (float): Shared time budget for dispatch and completion, in seconds.
                Excludes client/session creation, startup status lookup, and cleanup. Defaults to 60.
            max_requests_per_minute (int | None): PyRIT per-send pacing. Positive values delay each send by
                60 / value seconds before SDK client creation, outside the response deadline.
                None or nonpositive values disable pacing. Defaults to None.

        Raises:
            ValueError: If model_name or a supplied github_token is blank, or response_timeout_seconds
                is not finite and positive, or a supplied working_directory is blank, missing, or not a directory.
            OSError: If the working directory cannot be resolved or inspected.
            RuntimeError: If the optional GitHub Copilot SDK is not installed.
        """
        if not model_name.strip():
            raise ValueError("model_name must not be empty.")
        if github_token is not None and not github_token.strip():
            raise ValueError("github_token must not be blank when supplied.")
        if not math.isfinite(response_timeout_seconds) or response_timeout_seconds <= 0:
            raise ValueError("response_timeout_seconds must be a finite positive number.")

        self._working_directory: str | None = None
        if working_directory is not None:
            if isinstance(working_directory, str) and not working_directory.strip():
                raise ValueError("working_directory must not be blank when supplied.")
            resolved_directory = Path(working_directory).resolve()
            if not resolved_directory.is_dir():
                raise ValueError("working_directory must be an existing directory.")
            self._working_directory = str(resolved_directory)

        try:
            import copilot
        except ModuleNotFoundError as e:
            raise RuntimeError("Could not import copilot. Install it with 'pip install pyrit[github-copilot]'.") from e

        super().__init__(model_name=model_name, max_requests_per_minute=max_requests_per_minute)
        self._sdk = copilot
        self._github_token = github_token
        self._retain_session = retain_session
        self._response_timeout_seconds = response_timeout_seconds
        self._client: CopilotClient | None = None
        self._runtime_status: GetStatusResponse | None = None
        self._client_start_lock = asyncio.Lock()
        self._lifecycle_condition = asyncio.Condition()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._conversations: dict[str, _ConversationState] = {}
        self._active_target_operations = 0

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with the selected working directory.

        Returns:
            ComponentIdentifier: The target identifier, including the resolved selected path when provided.
        """
        return self._create_identifier(params={"working_directory": self._working_directory})

    def set_model_name(self, *, model_name: str) -> None:
        """
        Set the model before identity capture; afterward, create a new target to change it.

        Args:
            model_name (str): The nonblank Copilot model ID.

        Raises:
            ValueError: If model_name is blank.
            RuntimeError: If the target identity has already been captured and model_name differs.
        """
        if not model_name.strip():
            raise ValueError("model_name must not be empty.")
        if self._identifier is not None and model_name != self._model_name:
            raise RuntimeError("model_name is frozen after identity capture; create a new target to change it.")
        super().set_model_name(model_name=model_name)

    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.get_identifier()
        async with self._lifecycle_condition:
            if self._cleanup_task is not None:
                raise RuntimeError("GitHubCopilotTarget has been cleaned up and cannot send more prompts.")
            self._active_target_operations += 1
        try:
            request = normalized_conversation[-1].get_piece()
            conversation_id = request.conversation_id or ""
            conversation = self._conversations.setdefault(conversation_id, _ConversationState())
            async with conversation.lock:
                async with self._lifecycle_condition:
                    if self._cleanup_task is not None:
                        raise RuntimeError("GitHubCopilotTarget has been cleaned up and cannot send more prompts.")
                initial_system_prompt: str | None = None
                if normalized_conversation[0].api_role == "system":
                    initial_system_prompt = "\n\n".join(
                        piece.converted_value for piece in normalized_conversation[0].message_pieces
                    )
                session = await self._get_or_create_session_async(
                    conversation_id=conversation_id,
                    initial_system_prompt=initial_system_prompt,
                )
                try:
                    reply_text = await self._send_text_async(
                        session=session,
                        prompt=request.converted_value,
                    )
                except BaseException:
                    retirement_task = asyncio.create_task(self._retire_conversation_async(conversation=conversation))
                    try:
                        await asyncio.shield(retirement_task)
                    except asyncio.CancelledError:
                        await retirement_task
                        raise
                    raise
            return [construct_response_from_request(request=request, response_text_pieces=[reply_text])]
        finally:
            async with self._lifecycle_condition:
                self._active_target_operations -= 1
                if self._active_target_operations == 0:
                    self._lifecycle_condition.notify_all()

    async def _send_text_async(self, *, session: "CopilotSession", prompt: str) -> str:
        from copilot.generated.session_events import (
            AbortData,
            AssistantMessageData,
            SessionEvent,
            SessionIdleData,
            ToolExecutionStartData,
        )

        aborted = False
        tool_execution_started = False

        def _record_turn_events(event: SessionEvent) -> None:
            nonlocal aborted, tool_execution_started
            if isinstance(event.data, AbortData) or (isinstance(event.data, SessionIdleData) and event.data.aborted):
                aborted = True
            if isinstance(event.data, ToolExecutionStartData):
                tool_execution_started = True

        unsubscribe = session.on(_record_turn_events)
        try:
            reply = await asyncio.wait_for(
                session.send_and_wait(prompt, timeout=self._response_timeout_seconds),
                timeout=self._response_timeout_seconds,
            )
        finally:
            unsubscribe()

        if aborted:
            raise RuntimeError("Copilot turn was aborted.")
        if tool_execution_started:
            raise RuntimeError("Copilot turn reported tool execution.")
        if (
            reply is None
            or reply.agent_id is not None
            or not isinstance(reply.data, AssistantMessageData)
            or not isinstance(reply.data.content, str)
            or not reply.data.content
        ):
            raise ValueError("Copilot did not return a non-empty root assistant text reply.")
        return reply.data.content

    async def cleanup_target_async(self) -> None:
        """
        Stop accepting target work, drain active operations, and release owned SDK resources.

        Cleanup is terminal and idempotent. Retained sessions are preserved while the shared
        client is always stopped. Cleanup attempts every owned resource before surfacing failures.
        """
        async with self._lifecycle_condition:
            if self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(self._cleanup_owned_resources_async())
            cleanup_task = self._cleanup_task
        await asyncio.shield(cleanup_task)

    async def reset_conversation_async(self, *, conversation_id: str) -> None:
        """
        Release one established Copilot conversation without stopping the shared client.

        Unknown or already released conversations are no-ops. Established conversations
        become retired before release, so a later send cannot silently create a new native
        history for the same PyRIT conversation ID.

        Args:
            conversation_id (str): The PyRIT conversation ID to release.

        Raises:
            asyncio.CancelledError: If the caller is cancelled while waiting for cleanup.
        """
        async with self._lifecycle_condition:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or (conversation.retired and conversation.session is None):
                return
            cleanup_task = self._cleanup_task
            if cleanup_task is None:
                self._active_target_operations += 1

        if cleanup_task is not None:
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                raise
            except Exception:
                async with self._lifecycle_condition:
                    selected_conversation_released = conversation.retired and conversation.session is None
                if not selected_conversation_released:
                    raise
            return

        try:
            async with conversation.lock:
                await self._retire_conversation_async(conversation=conversation)
        finally:
            async with self._lifecycle_condition:
                self._active_target_operations -= 1
                if self._active_target_operations == 0:
                    self._lifecycle_condition.notify_all()

    async def _get_or_create_session_async(
        self,
        *,
        conversation_id: str,
        initial_system_prompt: str | None,
    ) -> "CopilotSession":
        async with self._lifecycle_condition:
            conversation = self._conversations[conversation_id]
            if conversation.retired:
                raise RuntimeError(
                    f"Copilot conversation {conversation_id} was retired and cannot accept further sends; "
                    "use a new conversation ID."
                )
            existing_session = conversation.session
        if existing_session is not None:
            return existing_session

        client = await self._get_or_start_client_async()
        session_id = str(uuid4())
        status = self._runtime_status
        if status is None:
            raise RuntimeError("Copilot runtime status is unavailable after client startup.")
        logger.info(
            "Attempting Copilot session creation: pyrit_conversation_id=%s requested_sdk_session_id=%s "
            "sdk_version=%s runtime_version=%s runtime_protocol_version=%s retain_session=%s remote_mode=OFF",
            conversation_id,
            session_id,
            self._sdk.__version__,
            status.version,
            status.protocol_version,
            self._retain_session,
        )

        try:
            system_message: SystemMessageConfig
            if initial_system_prompt is None:
                system_message = {
                    "mode": "customize",
                    "sections": {
                        "environment_context": {"action": "remove"},
                        "custom_instructions": {"action": "remove"},
                    },
                }
            else:
                system_message = {"mode": "replace", "content": initial_system_prompt}
            session = await client.create_session(
                session_id=session_id,
                model=self._model_name,
                system_message=system_message,
                remote_session=self._sdk.RemoteSessionMode.OFF,
                available_tools=[],
                skip_custom_instructions=True,
                instruction_directories=[],
                enable_host_git_operations=False,
                enable_config_discovery=False,
                organization_custom_instructions="",
                enable_on_demand_instruction_discovery=False,
                infinite_sessions={"enabled": False},
                memory={"enabled": False},
                enable_session_store=False,
                enable_file_hooks=False,
            )
            async with self._lifecycle_condition:
                conversation.session = session
            return session
        except BaseException:
            await self._cleanup_allocated_session_async(client=client, session_id=session_id)
            raise

    async def _get_or_start_client_async(self) -> "CopilotClient":
        async with self._client_start_lock:
            if self._client is not None:
                return self._client

            client = await asyncio.to_thread(
                self._sdk.CopilotClient,
                github_token=self._github_token,
                working_directory=self._working_directory,
            )
            try:
                await client.start()
                self._runtime_status = await client.get_status()
            except BaseException as error:
                try:
                    await client.stop()
                except BaseException as cleanup_error:
                    if isinstance(error, asyncio.CancelledError):
                        raise error from cleanup_error
                    raise BaseExceptionGroup(
                        "Copilot client startup and cleanup failed",
                        [error, cleanup_error],
                    ) from error
                raise

            async with self._lifecycle_condition:
                self._client = client
            return client

    async def _retire_conversation_async(self, *, conversation: _ConversationState) -> None:
        async with self._lifecycle_condition:
            session = conversation.session
            client = self._client
            if session is None or client is None:
                return
            conversation.retired = True
        await self._release_session_async(client=client, conversation=conversation)

    async def _cleanup_allocated_session_async(self, *, client: "CopilotClient", session_id: str) -> None:
        if await client.get_session_metadata(session_id) is None:
            return
        if self._retain_session:
            logger.info("Retaining Copilot session %s as requested; delete it manually.", session_id)
        else:
            await client.delete_session(session_id)

    async def _release_session_async(
        self,
        *,
        client: "CopilotClient",
        conversation: _ConversationState,
    ) -> None:
        session = conversation.session
        if session is None:
            return
        session_id = session.session_id
        if self._retain_session:
            await session.disconnect()
            logger.info("Retaining Copilot session %s as requested; delete it manually.", session_id)
        else:
            await client.delete_session(session_id)
        async with self._lifecycle_condition:
            if conversation.session is session:
                conversation.session = None
                conversation.retired = True

    async def _cleanup_owned_resources_async(self) -> None:
        async with self._lifecycle_condition:
            await self._lifecycle_condition.wait_for(lambda: self._active_target_operations == 0)
            conversations = [
                conversation for conversation in self._conversations.values() if conversation.session is not None
            ]
            client = self._client
            self._client = None
            self._runtime_status = None

        errors: list[BaseException] = []
        if client is not None:
            for conversation in conversations:
                try:
                    await self._release_session_async(client=client, conversation=conversation)
                except BaseException as error:
                    errors.append(error)
            try:
                await client.stop()
            except BaseException as error:
                errors.append(error)

        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Copilot target cleanup failed", errors)
