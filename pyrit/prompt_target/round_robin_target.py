# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import itertools
import logging
from typing import Any

from pyrit.identifiers import TARGET_EVAL_PARAM_FALLBACKS, TARGET_EVAL_PARAMS, ComponentIdentifier
from pyrit.models import Message
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_requirements import CHAT_TARGET_REQUIREMENTS

logger = logging.getLogger(__name__)


class RoundRobinTarget(PromptTarget):
    """
    A prompt target that distributes requests across multiple inner targets
    using weighted round-robin selection.

    All inner targets must be the same concrete class, share the same behavioral
    parameters for evaluation purposes, have the same TargetConfiguration,
    and must support multi-turn conversations with editable history.

    Requests are distributed per-call, not per-conversation. Because all inner
    targets support editable history, conversation history is reconstructed from
    shared memory on each request regardless of which target handled prior turns.

    Note: switching targets mid-conversation defeats provider-side prompt
    prefix caching (e.g., OpenAI cached input tokens can give cost
    reduction on long conversations). Users who need cache-efficient multi-turn
    conversations should assign individual targets at the attack or scenario
    level rather than using round-robin for those workloads.

    Memory entries are stamped with the round-robin's own identifier (not the
    inner target's). The inner target that handled each specific request is
    recorded in ``prompt_metadata["inner_target_identifier"]`` for traceability.

    The eval hash (used for scorer evaluation grouping) unwraps through the
    round-robin to the inner target's behavioral params, so evaluation results
    are comparable whether a round-robin or direct target is used.

    Not thread-safe. Safe for concurrent use within a single asyncio event loop
    (all mutable state is modified in synchronous code blocks).
    """

    def __init__(
        self,
        *,
        targets: list[PromptTarget],
        weights: list[int] | None = None,
    ) -> None:
        """
        Initialize the RoundRobinTarget.

        Args:
            targets: Inner targets to round-robin across. All targets must be the same
                concrete class and must have identical configurations (capabilities,
                policy, and normalization pipeline). This configuration must include
                supporting editable history and multi-turn conversations. The round-robin
                adopts this shared configuration so its pipeline matches what the inner
                targets expect. Must contain at least two entries.
            weights: Optional relative integer weights for each target. When
                provided, must be the same length as ``targets`` with all values
                > 0. For example, ``weights=[2, 1]`` sends roughly twice as many
                requests to the first target. Defaults to equal weight.

        Raises:
            ValueError: If fewer than 2 targets are provided, targets are
                different classes, a nested RoundRobinTarget is detected,
                weights length doesn't match, weights contain non-positive
                values, inner targets have different configurations, or
                targets lack required capabilities.
        """
        if len(targets) < 2:
            raise ValueError(f"RoundRobinTarget requires at least 2 targets, got {len(targets)}.")

        if any(isinstance(t, RoundRobinTarget) for t in targets):
            raise ValueError("Nesting RoundRobinTarget inside another RoundRobinTarget is not supported.")

        first_type = type(targets[0])
        mismatched = [(i, type(t).__name__) for i, t in enumerate(targets[1:], start=1) if type(t) is not first_type]
        if mismatched:
            details = ", ".join(f"target {i} is {name}" for i, name in mismatched)
            raise ValueError(
                f"All targets must be the same concrete class. Target 0 is {first_type.__name__}, but {details}."
            )

        weights = weights or [1] * len(targets)
        if len(weights) != len(targets):
            raise ValueError(f"weights length ({len(weights)}) must match targets length ({len(targets)}).")
        if any(w <= 0 for w in weights):
            raise ValueError("All weights must be positive integers.")

        # Validate all inner targets have identical configurations (capabilities,
        # policy, and normalization pipeline). We adopt the shared configuration so the
        # pipeline that actually runs matches what the user configured on their targets.
        _validate_configuration_consistency(targets)

        # The first target's configuration is representative since we've validated they are all identical.
        super().__init__(
            custom_configuration=targets[0].configuration,
        )

        # Validate that the adopted capabilities meet chat target requirements
        # (multi-turn + editable history).
        CHAT_TARGET_REQUIREMENTS.validate(target=self)

        # Ensure that for LLM scoring evaluation purposes, the inner targets have the equivalent behavioral params
        _validate_behavioral_consistency(targets)

        self._targets = targets
        self._weights = weights

        # Build rotation sequence from weights.
        # e.g. weights=[2, 1] -> rotation=[0, 0, 1] -> cycles: 0, 0, 1, 0, 0, 1, ...
        self._rotation: list[int] = list(itertools.chain.from_iterable([i] * w for i, w in enumerate(weights)))

        self._counter: int = 0

    def _next_target(self) -> PromptTarget:
        """
        Return the next inner target in the weighted rotation.

        Returns:
            PromptTarget: The next inner target.
        """
        idx = self._rotation[self._counter % len(self._rotation)]
        self._counter += 1
        return self._targets[idx]

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Select the next inner target and delegate the send, with fallback.

        Tries the next target in the weighted rotation. If the inner target
        raises an exception (e.g., endpoint down, rate limit exhausted after
        retries), falls back to the remaining unique targets before propagating
        the failure. This prevents a single unhealthy endpoint from blocking
        requests when other endpoints are available.

        The hash of the inner target that handled the request is recorded in
        ``prompt_metadata["inner_target_identifier"]`` on each response piece
        for traceability.

        Args:
            normalized_conversation: The normalized conversation from the pipeline.

        Returns:
            list[Message]: Response messages from the inner target.

        Raises:
            Exception: If all unique inner targets fail.
            RuntimeError: If no targets are available to try (should be unreachable).
        """
        first_target = self._next_target()
        targets_to_try = [first_target] + [t for t in self._targets if t is not first_target]
        last_exception: BaseException | None = None

        for target in targets_to_try:
            try:
                responses = await target._send_prompt_to_target_async(normalized_conversation=normalized_conversation)

                inner_id_hash = target.get_identifier().hash
                if inner_id_hash is not None:
                    for response in responses:
                        for piece in response.message_pieces:
                            piece.prompt_metadata["inner_target_identifier"] = inner_id_hash

                return responses
            except Exception as ex:
                logger.warning(
                    f"Inner target {type(target).__name__} (index {self._targets.index(target)}) "
                    f"failed: {ex}. Trying next target."
                )
                last_exception = ex

        # All targets failed — propagate the last exception.
        if last_exception is not None:
            raise last_exception
        raise RuntimeError("No targets to try — this should be unreachable.")

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this round-robin target.

        Includes the weights and all inner target identifiers as children.

        Returns:
            ComponentIdentifier: The identifier for this target.
        """
        return self._create_identifier(
            params={"weights": self._weights},
            children={"targets": [t.get_identifier() for t in self._targets]},
        )


def _validate_configuration_consistency(targets: list[PromptTarget]) -> None:
    """
    Validate that all inner targets have identical TargetConfigurations.

    Since RoundRobinTarget calls ``_send_prompt_to_target_async`` directly on
    inner targets (bypassing ``send_prompt_async``), the inner targets'
    normalization pipelines and policies never run. Only the round-robin's own
    pipeline runs. We adopt the first target's configuration so the pipeline
    matches what the user configured — but that is only valid if every inner
    target has the same configuration.

    Uses ``as_identifier_params()`` for comparison: two configurations that
    behave identically produce equal dicts.

    Args:
        targets: The inner targets to validate.

    Raises:
        ValueError: If any inner target has a different configuration.
    """
    reference = targets[0].configuration.as_identifier_params()
    for i, t in enumerate(targets[1:], start=1):
        other = t.configuration.as_identifier_params()
        if other != reference:
            raise ValueError(
                f"All inner targets must have identical configurations (capabilities, "
                f"policy, and normalization pipeline) because only the round-robin's "
                f"own pipeline runs. Target 0 configuration: {reference}, "
                f"target {i} configuration: {other}."
            )


def _validate_behavioral_consistency(targets: list[PromptTarget]) -> None:
    """
    Validate that all inner targets have the same behavioral parameters for
    scorer and attack evaluation purposes.

    Checks the params that affect model output quality (underlying_model_name,
    temperature, top_p). These must be identical across targets because the
    round-robin distributes requests arbitrarily — inconsistent behavioral
    params would make scorers non-comparable. This validation allows users
    to evaluate round-robin targets for scoring and attack evaluation with confidence
    that results are comparable to using the inner targets directly.

    Args:
        targets: The inner targets to validate.

    Raises:
        ValueError: If any behavioral param differs across targets.
    """
    first_id = targets[0].get_identifier()

    def _resolve_param(identifier: ComponentIdentifier, param: str) -> Any:
        value = identifier.params.get(param)
        if (value is None or value == "") and param in TARGET_EVAL_PARAM_FALLBACKS:
            value = identifier.params.get(TARGET_EVAL_PARAM_FALLBACKS[param])
        return value

    reference = {p: _resolve_param(first_id, p) for p in TARGET_EVAL_PARAMS}

    for i, t in enumerate(targets[1:], start=1):
        t_id = t.get_identifier()
        for param in TARGET_EVAL_PARAMS:
            actual = _resolve_param(t_id, param)
            if actual != reference[param]:
                raise ValueError(
                    f"Behavioral parameter '{param}' differs across targets: "
                    f"target 0 has {reference[param]!r}, target {i} has {actual!r}. "
                    f"All inner targets must have the same behavioral configuration."
                )
