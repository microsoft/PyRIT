# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import queue
from typing import Optional

from pyrit.models import Message, construct_response_from_request
from pyrit.prompt_target.common.prompt_target import PromptTarget


class ExternalControlTarget(PromptTarget):
    """
    ExternalControlTarget allows external programs to control attack execution flow step-by-step.
    This target intercepts prompts from PyRIT executors and waits for external programs to provide
    responses via a polling-based API, enabling integration with systems like browser automation,
    manual testing interfaces, or REST APIs.
    """

    def __init__(self) -> None:
        """
        Initialize the ExternalControlTarget.

        This target uses a queue-based synchronization mechanism to enable disconnected
        request/response flows between PyRIT executors and external control programs.
        """
        super().__init__()
        self._current_prompt: Optional[Message] = None
        self._response_queue: queue.Queue = queue.Queue()
        self._waiting: bool = False

    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Asynchronously send a message and wait for external response.

        This method stores the prompt for external retrieval via get_current_prompt(),
        then blocks (using async polling) until the external program provides a response
        via submit_response().

        Args:
            message (Message): The message object containing the prompt to send.

        Returns:
            list[Message]: A list containing a single Message with the external response.
        """
        self._validate_request(message=message)
        request = message.message_pieces[0]

        # Store prompt for external retrieval
        self._current_prompt = message
        self._waiting = True

        # Poll queue until response arrives (non-blocking async)
        while True:
            try:
                response_text = self._response_queue.get_nowait()
                break
            except queue.Empty:
                await asyncio.sleep(0.05)  # Check every 50ms

        # Clean up state
        self._waiting = False
        self._current_prompt = None

        # Build and return response message
        response_message = construct_response_from_request(
            request=request, response_text_pieces=[response_text]
        )
        return [response_message]

    def get_current_prompt(self) -> Optional[Message]:
        """
        Get the current prompt waiting for a response.

        This method is called by external programs (e.g., Flask API handlers) to retrieve
        the prompt that the executor is currently waiting on. Returns None if no prompt
        is waiting.

        Returns:
            Optional[Message]: The current waiting prompt, or None if not waiting.

        Example:
            >>> prompt = target.get_current_prompt()
            >>> if prompt:
            >>>     text = prompt.message_pieces[0].converted_value
            >>>     # Process text externally...
        """
        return self._current_prompt

    def submit_response(self, response_text: str) -> None:
        """
        Submit a response to unblock the executor.

        This method is called by external programs (e.g., Flask API handlers) to provide
        the response text. The response is placed in a thread-safe queue, which unblocks
        the send_prompt_async() method.

        Args:
            response_text (str): The response text to send back to the executor.

        Example:
            >>> target.submit_response("This is the response from the browser")
        """
        self._response_queue.put(response_text)

    def is_waiting(self) -> bool:
        """
        Check if the target is currently waiting for a response.

        Returns:
            bool: True if waiting for an external response, False otherwise.

        Example:
            >>> if target.is_waiting():
            >>>     print("Executor is blocked, waiting for response")
        """
        return self._waiting

    def _validate_request(self, *, message: Message) -> None:
        """
        Validate the incoming message.

        Args:
            message (Message): The message to validate.
        """
        if not message or not message.message_pieces:
            raise ValueError("Message must contain at least one message piece")

    async def cleanup_target(self) -> None:
        """
        Clean up target resources.

        Clears any remaining state in the response queue.
        """
        # Clear any remaining items in queue
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except queue.Empty:
                break

        self._current_prompt = None
        self._waiting = False
