# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import itertools
import logging
from dataclasses import fields
from typing import Any, Optional

from pyrit.identifiers import TARGET_BEHAVIORAL_PARAM_FALLBACKS, TARGET_BEHAVIORAL_PARAMS, ComponentIdentifier
from pyrit.models import Message
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.target_requirements import CHAT_TARGET_REQUIREMENTS

logger = logging.getLogger(__name__)


class RoundRobinTarget(PromptTarget):
    """
    A prompt target that distributes requests across multiple inner targets
    using weighted round-robin selection.

    All inner targets must be the same concrete class and must support
    multi-turn conversations with editable history. The round-robin target's
    capabilities are the intersection (lower bound) of all inner targets'
    capabilities.

    Requests are distributed per-call, not per-conversation. Because all inner
    targets support editable history, conversation history is reconstructed from
    shared memory on each request regardless of which target handled prior turns.

    Note: switching targets mid-conversation defeats provider-side prompt
    prefix caching (e.g., OpenAI cached input tokens can give cost
    reduction on long conversations). For multi-turn attacks like Crescendo
    with many objectives, this can significantly increase API cost compared
    to pinning each conversation to a single target. This is a cost/latency
    vs. throughput trade-off — round-robin avoids per-endpoint rate limits at
    the expense of caching. Users who need cache-efficient multi-turn
    conversations should assign individual targets at the attack or scenario
    level rather than using round-robin for those workloads.

    Memory entries are stamped with the round-robin's own identifier (not the
    inner target's). The inner target that handled each specific request is
    recorded in ``prompt_metadata["inner_target_identifier"]`` for traceability.
    The eval hash (used for scorer evaluation grouping) unwraps through the
    round-robin to the inner target's behavioral params, so scoring results
    are comparable whether a round-robin or direct target is used.

    Not thread-safe. Safe for concurrent use within a single asyncio event loop
    (all mutable state is modified in synchronous code blocks).
    """

    def __init__(
        self,
        *,
        targets: list[PromptTarget],
        weights: list[int] | None = None,
        custom_configuration: Optional[TargetConfiguration] = None,
    ) -> None:
        """
        Initialize the RoundRobinTarget.

        Args:
            targets: Inner targets to round-robin across. Must all be the same
                concrete class, contain at least 2 entries, and support both
                multi-turn and editable history capabilities.
            weights: Optional relative integer weights for each target. When
                provided, must be the same length as ``targets`` with all values
                > 0. For example, ``weights=[2, 1]`` sends roughly twice as many
                requests to the first target. Defaults to equal weight.
            custom_configuration (TargetConfiguration, Optional): Optional override
                for the target configuration. When ``None`` (the default), the configuration
                is built from the intersection of all inner targets' capabilities with
                the default policy. When provided, the caller's configuration is used as-is
                — the caller is responsible for ensuring it is compatible with
                the inner targets.

        Raises:
            ValueError: If fewer than 2 targets are provided, targets are
                different classes, weights length doesn't match, weights contain
                non-positive values, targets lack required capabilities, or
                capability intersection yields empty modalities.
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

        intersected = _intersect_capabilities([t.capabilities for t in targets])

        effective_configuration = custom_configuration or TargetConfiguration(capabilities=intersected)

        super().__init__(
            custom_configuration=effective_configuration,
        )

        # Validate that the intersected capabilities meet chat target requirements
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
        """
        first_target = self._next_target()
        tried_indices: set[int] = set()
        last_exception: BaseException | None = None

        # Build ordered fallback list following the rotation sequence.
        # Start with the selected target, then continue through the rotation
        # to try remaining unique targets in their natural order.
        first_idx = self._targets.index(first_target)
        tried_indices.add(first_idx)
        targets_to_try: list[PromptTarget] = [first_target]

        # Walk forward through the rotation from the current counter position
        # to pick up remaining unique targets in rotation order.
        for offset in range(len(self._rotation)):
            idx = self._rotation[(self._counter + offset) % len(self._rotation)]
            if idx not in tried_indices:
                targets_to_try.append(self._targets[idx])
                tried_indices.add(idx)
            if len(tried_indices) == len(self._targets):
                break

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

        # All targets failed — propagate the last exception
        assert last_exception is not None, "targets_to_try is never empty"
        raise last_exception

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this round-robin target.

        Includes the weights as a behavioral parameter and all inner target
        identifiers as children.

        Returns:
            ComponentIdentifier: The identifier for this target.
        """
        return self._create_identifier(
            params={"weights": self._weights},
            children={"targets": [t.get_identifier() for t in self._targets]},
        )


def _intersect_capabilities(caps: list[TargetCapabilities]) -> TargetCapabilities:
    """
    Compute the intersection (lower bound) of multiple TargetCapabilities.

    Boolean fields are AND-ed. Modality frozensets are intersected.

    Args:
        caps: List of TargetCapabilities to intersect.

    Returns:
        TargetCapabilities: The intersected capabilities.

    Raises:
        ValueError: If the intersection of input or output modalities is empty.
    """
    _capability_flags = [f.name for f in fields(TargetCapabilities) if f.type == "bool" or f.type is bool]

    kwargs: dict[str, Any] = {}
    for field_name in _capability_flags:
        kwargs[field_name] = all(getattr(c, field_name) for c in caps)

    input_intersection = caps[0].input_modalities
    output_intersection = caps[0].output_modalities
    for c in caps[1:]:
        input_intersection = input_intersection & c.input_modalities
        output_intersection = output_intersection & c.output_modalities

    if not input_intersection:
        raise ValueError(
            "The intersection of input modalities across all targets is empty. "
            "The targets have no common input modalities."
        )
    if not output_intersection:
        raise ValueError(
            "The intersection of output modalities across all targets is empty. "
            "The targets have no common output modalities."
        )

    kwargs["input_modalities"] = input_intersection
    kwargs["output_modalities"] = output_intersection

    return TargetCapabilities(**kwargs)


def _validate_behavioral_consistency(targets: list[PromptTarget]) -> None:
    """
    Validate that all inner targets have the same behavioral parameters.

    Checks the params that affect model output quality (underlying_model_name,
    temperature, top_p). These must be identical across targets because the
    round-robin distributes requests arbitrarily — inconsistent behavioral
    params would make scores non-comparable.

    Args:
        targets: The inner targets to validate.

    Raises:
        ValueError: If any behavioral param differs across targets.
    """
    first_id = targets[0].get_identifier()

    def _resolve_param(identifier: ComponentIdentifier, param: str) -> Any:
        value = identifier.params.get(param)
        if (value is None or value == "") and param in TARGET_BEHAVIORAL_PARAM_FALLBACKS:
            value = identifier.params.get(TARGET_BEHAVIORAL_PARAM_FALLBACKS[param])
        return value

    reference = {p: _resolve_param(first_id, p) for p in TARGET_BEHAVIORAL_PARAMS}

    for i, t in enumerate(targets[1:], start=1):
        t_id = t.get_identifier()
        for param in TARGET_BEHAVIORAL_PARAMS:
            actual = _resolve_param(t_id, param)
            if actual != reference[param]:
                raise ValueError(
                    f"Behavioral parameter '{param}' differs across targets: "
                    f"target 0 has {reference[param]!r}, target {i} has {actual!r}. "
                    f"All inner targets must have the same behavioral configuration."
                )
