# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import warnings
from typing import Any

from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


class PromptChatTarget(PromptTarget):
    """
    .. deprecated:: 0.14.0
        ``PromptChatTarget`` is deprecated and will be removed in v0.16.0. Use
        ``PromptTarget`` directly with a ``TargetConfiguration`` declaring
        ``supports_multi_turn=True`` and ``supports_editable_history=True``.

    Backwards-compatible alias for ``PromptTarget``. All chat-target functionality
    (``set_system_prompt``, ``is_response_format_json``) lives on ``PromptTarget``.
    Subclassing or instantiating this class emits a ``DeprecationWarning``.
    """

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
            supports_editable_history=True,
        )
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Call the superclass __init_subclass__ and emit a deprecation warning when subclassing PromptChatTarget.
        Use PromptTarget with an appropriate TargetConfiguration instead.
        """
        super().__init_subclass__(**kwargs)
        warnings.warn(
            f"Subclassing PromptChatTarget is deprecated and will be removed in v0.16.0. "
            f"Inherit from PromptTarget directly and declare supports_multi_turn=True and "
            f"supports_editable_history=True in your _DEFAULT_CONFIGURATION. "
            f"({cls.__name__})",
            DeprecationWarning,
            stacklevel=2,
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Initialize the PromptChatTarget. This constructor is deprecated and will emit a warning.
        Use PromptTarget with an appropriate TargetConfiguration instead.
        """
        warnings.warn(
            "PromptChatTarget is deprecated and will be removed in v0.16.0. "
            "Use PromptTarget directly with a TargetConfiguration declaring "
            "supports_multi_turn=True and supports_editable_history=True.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args, **kwargs)
