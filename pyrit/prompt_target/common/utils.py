# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from typing import Any, Callable, Optional

from pyrit.exceptions import PyritException


def validate_temperature(temperature: Optional[float]) -> None:
    """
    Validate that temperature parameter is within valid range.

    Args:
        temperature: The temperature value to validate (0-2 inclusive).

    Raises:
        PyritException: If temperature is not between 0 and 2 (inclusive).
    """
    if temperature is not None and (temperature < 0 or temperature > 2):
        raise PyritException(message="temperature must be between 0 and 2 (inclusive).")


def validate_top_p(top_p: Optional[float]) -> None:
    """
    Validate that top_p parameter is within valid range.

    Args:
        top_p: The top_p value to validate (0-1 inclusive).

    Raises:
        PyritException: If top_p is not between 0 and 1 (inclusive).
    """
    if top_p is not None and (top_p < 0 or top_p > 1):
        raise PyritException(message="top_p must be between 0 and 1 (inclusive).")


def limit_requests_per_minute(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Enforce rate limit of the target through setting requests per minute.
    This should be applied to all send_prompt_async() functions on PromptTarget and PromptChatTarget.

    Args:
        func (Callable): The function to be decorated.

    Returns:
        Callable: The decorated function with a sleep introduced.
    """

    async def set_max_rpm(*args: Any, **kwargs: Any) -> Any:
        self = args[0]
        rpm = getattr(self, "_max_requests_per_minute", None)
        if rpm and rpm > 0:
            await asyncio.sleep(60 / rpm)

        return await func(*args, **kwargs)

    return set_max_rpm
