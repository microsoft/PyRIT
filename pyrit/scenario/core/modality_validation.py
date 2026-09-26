# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Derived modality compatibility for scenario attacks.

Modality is not stored on a technique: whether a run is compatible depends on the seed pieces,
the request converters configured for that run, and the concrete target. This module derives
the answer instead.

Two chains are checked, both for turn 0 only:

* the **request chain** — each first-turn root's data types, projected through the request
  converters, must be exactly one of the objective target's advertised input modality combinations;
* the **response chain** — each advertised response combination is checked against what the
  scorer declares it can read after the response converters run, including whether it can
  ignore unsupported pieces.

Anything indeterminate resolves to ``ModalityVerdict.UNKNOWN`` and never blocks a run. A
target whose capabilities cannot be read, an attack that exposes no scoring config, and a
scorer that never declared its data types are all "cannot tell", not "incompatible".

Turns after the first are not modelled here. Media routing across turns belongs to
``_ModalityFeedbackRouter``, which each multi-turn attack consults at execution time.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from enum import Enum
from itertools import product
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.executor.attack.core.attack_strategy import AttackStrategy
    from pyrit.models import AttackSeedGroup, AttackTechniqueSeedGroup
    from pyrit.models.literals import PromptDataType
    from pyrit.prompt_normalizer import ConverterConfiguration
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.score import Scorer

logger = logging.getLogger(__name__)


class ModalityPolicy(str, Enum):
    """
    Policy for what to do when an atomic attack's modality chain is incompatible.

    Mirrors ``ScorerOverridePolicy`` in vocabulary and meaning, with one difference: ``SKIP``
    logs a warning rather than staying silent, because dropping a whole atomic attack changes
    what a run covers and should be visible even when the user allows it.
    """

    #: Drop the incompatible atomic attack and continue with the rest.
    SKIP = "skip"

    #: Keep the attack and log a warning; it will fail later if the incompatibility is real.
    WARN = "warn"

    #: Abort initialization with ``ModalityValidationError``.
    RAISE = "raise"


class ModalityValidationError(ValueError):
    """
    Raised when an atomic attack's modality chain cannot reach its target or scorer.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers around
    ``initialize_async`` keep working, mirroring ``TechniqueResolutionError``.
    """


class ModalityVerdict(str, Enum):
    """The outcome of checking one atomic attack, or one leg of it."""

    #: Every checked chain can carry its payload end to end.
    COMPATIBLE = "compatible"

    #: At least one chain provably cannot.
    INCOMPATIBLE = "incompatible"

    #: Compatibility is indeterminate or only some first-turn roots can run.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModalityReport:
    """The result of validating one ``AtomicAttack``."""

    #: The attack this report describes, used in the policy's log line and error message.
    atomic_attack_name: str

    #: The overall verdict for the attack.
    verdict: ModalityVerdict

    #: Human-readable explanations, each naming the converter, target, or scorer that failed.
    reasons: tuple[str, ...] = field(default_factory=tuple)

    #: All possible request data types, not necessarily present in the same message.
    projected_request_types: frozenset[PromptDataType] = field(default_factory=frozenset)


def project_request_chain(
    *,
    start_types: Sequence[PromptDataType],
    request_converters: Sequence[ConverterConfiguration],
    piece_indexes_known: bool = True,
) -> tuple[set[PromptDataType], str | None]:
    """
    Return the possible output types for converter append checks.

    This union is conservative when the message shape is unknown. For target compatibility,
    use ``project_request_combinations`` so alternative types are not mistaken for pieces
    of one message.

    Args:
        start_types (Sequence[PromptDataType]): The ordered types of the request's message pieces.
        request_converters (Sequence[ConverterConfiguration]): The request converter chain, in
            application order.
        piece_indexes_known (bool): Whether ``start_types`` includes every actual piece in order.
            Set to ``False`` when projecting a factory without a concrete message.

    Returns:
        tuple[set[PromptDataType], str | None]: Possible output types, or an empty set
        and the reason a converter cannot accept a possible type.
    """
    combinations, failure_reason = project_request_combinations(
        start_types=start_types, request_converters=request_converters, piece_indexes_known=piece_indexes_known
    )
    if combinations is None or failure_reason is not None:
        return set(), failure_reason or "Too many possible converter output combinations to check safely"
    return {data_type for combination in combinations for data_type in combination}, None


def project_request_combinations(
    *,
    start_types: Sequence[PromptDataType],
    request_converters: Sequence[ConverterConfiguration],
    piece_indexes_known: bool = True,
    max_combinations: int = 256,
) -> tuple[set[frozenset[PromptDataType]] | None, str | None]:
    """
    Project individual message pieces without merging alternative converter outputs.

    Args:
        start_types (Sequence[PromptDataType]): Ordered input piece types.
        request_converters (Sequence[ConverterConfiguration]): Configurations in runtime order.
        piece_indexes_known (bool): Whether the input piece indexes are known.
        max_combinations (int): Maximum enumerated final piece-type combinations.

    Returns:
        tuple[set[frozenset[PromptDataType]] | None, str | None]: Possible final message
        type combinations and the first converter failure on any branch. ``None`` means
        the possibilities exceed the bound and compatibility cannot be determined. A
        failure alongside successful combinations makes the overall verdict indeterminate.
    """
    piece_types: list[set[PromptDataType]] = [{data_type} for data_type in start_types]
    failure_reason: str | None = None

    for configuration in request_converters:
        for index, types in enumerate(piece_types):
            if piece_indexes_known and configuration.indexes_to_apply and index not in configuration.indexes_to_apply:
                continue

            next_types: set[PromptDataType] = set()
            for data_type in sorted(types):
                if (
                    configuration.prompt_data_types_to_apply
                    and data_type not in configuration.prompt_data_types_to_apply
                ):
                    next_types.add(data_type)
                    continue

                converted_types: set[PromptDataType] = {data_type}
                for converter in configuration.converters:
                    unsupported = sorted(t for t in converted_types if not converter.input_supported(t))
                    if unsupported:
                        if failure_reason is None:
                            failure_reason = (
                                f"{type(converter).__name__} does not accept {unsupported}; "
                                f"it accepts {sorted(converter.supported_input_types)}"
                            )
                        converted_types = set()
                        break
                    converted_types = set(converter.supported_output_types)
                next_types.update(converted_types)

                if not piece_indexes_known and configuration.indexes_to_apply:
                    next_types.add(data_type)
            piece_types[index] = next_types

    count = 1
    for types in piece_types:
        count *= len(types)
        if count > max_combinations:
            return None, failure_reason
    return {frozenset(types) for types in product(*piece_types)}, failure_reason


def target_accepts(*, target: PromptTarget, request_types: set[PromptDataType]) -> ModalityVerdict:
    """
    Check a projected request against a target's advertised input modality combinations.

    A target advertises the *combinations* of data types it accepts in a single request, and the
    request's own set of data types must be exactly one of them. ``{text, image_path}`` means
    "text with an image", not "an image alone": a target that accepts a lone image also
    advertises ``{image_path}``, as the vision profiles do, while a video-generation target that
    needs a prompt does not. Reading the declarations literally keeps plan-time in step with
    ``_ModalityFeedbackRouter``, which treats a missing bare ``{text}`` combination as "media
    required on every request".

    Args:
        target (PromptTarget): The objective target.
        request_types (set[PromptDataType]): The data types reaching the target.

    Returns:
        ModalityVerdict: ``UNKNOWN`` when the target's capabilities cannot be read, otherwise
        whether ``request_types`` is one of the advertised combinations.
    """
    supported = _read_modalities(target=target, direction="input")
    if supported is None:
        return ModalityVerdict.UNKNOWN
    if not request_types:
        return ModalityVerdict.INCOMPATIBLE
    if frozenset(request_types) in supported:
        return ModalityVerdict.COMPATIBLE
    return ModalityVerdict.INCOMPATIBLE


def scorer_accepts(
    *,
    scorer: Scorer | None,
    target: PromptTarget,
    response_converters: Sequence[ConverterConfiguration] = (),
) -> tuple[ModalityVerdict, str | None]:
    """
    Check each target output combination against the scorer's declared data types.

    This is a type-compatibility check only. It establishes that the scorer can *read* the
    response, not that the resulting score is meaningful for the objective.

    Args:
        scorer (Scorer | None): The objective scorer, if the attack exposes one.
        target (PromptTarget): The objective target.
        response_converters (Sequence[ConverterConfiguration]): Converters applied to the final
            target response before scoring.

    Returns:
        tuple[ModalityVerdict, str | None]: The verdict, and a reason when incompatible.
    """
    if scorer is None:
        return ModalityVerdict.UNKNOWN, None

    declared = scorer.supported_data_types
    if declared is None:
        return ModalityVerdict.UNKNOWN, None

    output_modalities = _read_modalities(target=target, direction="output")
    if output_modalities is None:
        return ModalityVerdict.UNKNOWN, None

    if not output_modalities:
        return ModalityVerdict.UNKNOWN, None

    if any(configuration.indexes_to_apply for configuration in response_converters):
        return ModalityVerdict.UNKNOWN, None

    projected_modalities: list[frozenset[PromptDataType]] = []
    for combination in output_modalities:
        projected, failure = project_request_combinations(
            start_types=sorted(combination), request_converters=response_converters
        )
        if projected is None or failure is not None:
            return ModalityVerdict.UNKNOWN, None
        projected_modalities.extend(projected)

    if scorer.allows_unsupported_pieces:
        scorable = [bool(combination & declared) for combination in projected_modalities]
    else:
        scorable = [bool(combination) and combination <= declared for combination in projected_modalities]

    if all(scorable):
        return ModalityVerdict.COMPATIBLE, None
    if any(scorable):
        return ModalityVerdict.UNKNOWN, None

    return ModalityVerdict.INCOMPATIBLE, (
        f"{type(scorer).__name__} cannot score any objective target output combination "
        f"{[sorted(combination) for combination in projected_modalities]}; it declares {sorted(declared)}"
    )


def validate_atomic_attack(*, atomic_attack: AtomicAttack) -> ModalityReport:
    """
    Derive whether a built ``AtomicAttack`` can carry its payload to its target and scorer.

    Each seed group is projected independently — two groups are two separate requests, so their
    data types are never combined into one. TAP's independently generated text roots are checked
    separately from its seeded root. A mixed runnable/unrunnable TAP root set is unknown rather
    than incompatible, since skipping would discard runnable branches.

    Args:
        atomic_attack (AtomicAttack): The attack to check, after construction and before queuing.

    Returns:
        ModalityReport: The verdict and the reasons behind it.
    """
    name = getattr(atomic_attack, "atomic_attack_name", "<unknown>")
    technique = atomic_attack.attack_technique
    attack = technique.attack

    # The compound's nominal target does not send its children's requests.
    from pyrit.executor.attack.compound import SequentialAttack
    from pyrit.executor.attack.multi_turn.tree_of_attacks import TreeOfAttacksWithPruningAttack

    if isinstance(attack, SequentialAttack):
        return ModalityReport(atomic_attack_name=name, verdict=ModalityVerdict.UNKNOWN)

    target = attack.get_objective_target()
    request_converters = attack.get_request_converters() or []
    scoring_config = attack.get_attack_scoring_config()
    scorer = getattr(scoring_config, "objective_scorer", None) if scoring_config is not None else None

    reads_next_message = _reads_next_message(attack=attack)
    has_next_message_override, next_message_override = atomic_attack.get_next_message_override()
    verdicts: set[ModalityVerdict] = set()
    partially_runnable_roots = False
    reasons: list[str] = []
    projected_all: set[PromptDataType] = set()

    for seed_group in _seed_groups_of(atomic_attack):
        start_types = _effective_start_types(
            seed_group=seed_group,
            seed_technique=technique.seed_technique,
            reads_next_message=reads_next_message,
            has_next_message_override=has_next_message_override,
            next_message_override=next_message_override,
        )
        if start_types is None:
            verdicts.add(ModalityVerdict.UNKNOWN)
            continue

        root_types: list[list[PromptDataType]] = [start_types]
        if isinstance(attack, TreeOfAttacksWithPruningAttack) and attack.has_unseeded_first_turn_roots:
            root_types.append(["text"])

        root_verdicts: list[ModalityVerdict] = []
        root_reasons: list[str] = []
        for types in root_types:
            combinations, failure_reason = project_request_combinations(
                start_types=types, request_converters=request_converters
            )
            if combinations is None:
                root_verdicts.append(ModalityVerdict.UNKNOWN)
                continue
            if failure_reason is not None:
                root_verdicts.append(ModalityVerdict.INCOMPATIBLE)
                root_reasons.append(failure_reason)
            for projected in combinations:
                projected_all.update(projected)
                request_verdict = target_accepts(target=target, request_types=set(projected))
                root_verdicts.append(request_verdict)
                if request_verdict is ModalityVerdict.INCOMPATIBLE:
                    root_reasons.append(
                        f"objective target does not accept {sorted(projected)}; "
                        f"it accepts {_format_modalities(_read_modalities(target=target, direction='input'))}"
                    )

        if ModalityVerdict.COMPATIBLE in root_verdicts and ModalityVerdict.INCOMPATIBLE in root_verdicts:
            partially_runnable_roots = True
            verdicts.add(ModalityVerdict.UNKNOWN)
            mixed_reason = (
                "TAP seeded root and generated text roots have mixed compatibility"
                if isinstance(attack, TreeOfAttacksWithPruningAttack) and attack.has_unseeded_first_turn_roots
                else "Possible first-turn requests have mixed compatibility"
            )
            reasons.append(f"{mixed_reason}; at least one can run: " + "; ".join(root_reasons))
        else:
            verdicts.update(root_verdicts)
            for reason in root_reasons:
                if reason not in reasons:
                    reasons.append(reason)

    scorer_verdict, scorer_reason = scorer_accepts(
        scorer=scorer, target=target, response_converters=attack.get_response_converters()
    )
    verdicts.add(scorer_verdict)
    if scorer_reason is not None:
        reasons.append(scorer_reason)

    if ModalityVerdict.INCOMPATIBLE in verdicts:
        verdict = ModalityVerdict.INCOMPATIBLE
    elif partially_runnable_roots:
        verdict = ModalityVerdict.UNKNOWN
    elif ModalityVerdict.COMPATIBLE in verdicts:
        # A leg we could not determine does not cancel one we could.
        verdict = ModalityVerdict.COMPATIBLE
    else:
        verdict = ModalityVerdict.UNKNOWN

    return ModalityReport(
        atomic_attack_name=name,
        verdict=verdict,
        reasons=tuple(reasons),
        projected_request_types=frozenset(projected_all),
    )


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _seed_groups_of(atomic_attack: AtomicAttack) -> list[AttackSeedGroup]:
    """Return the attack's seed groups, or an empty list when they cannot be read."""
    try:
        return list(atomic_attack.seed_groups)
    except (AttributeError, TypeError):
        return []


def _reads_next_message(*, attack: AttackStrategy) -> bool:
    """
    Whether the attack builds its first request from the seed's ``next_message``.

    Attacks created via ``AttackParameters.excluding("next_message")`` build turn 0 from the
    objective text instead, so their seed media never reaches the target on the first turn.

    Returns:
        bool: ``True`` when the attack reads ``next_message``, including when it cannot be
        determined (the common case for every standard ``AttackParameters``).
    """
    try:
        return any(f.name == "next_message" for f in dataclasses.fields(attack.params_type))
    except (AttributeError, TypeError):
        return True


def _effective_start_types(
    *,
    seed_group: AttackSeedGroup,
    seed_technique: AttackTechniqueSeedGroup | None,
    reads_next_message: bool,
    has_next_message_override: bool,
    next_message_override: object,
) -> list[PromptDataType] | None:
    """
    Determine the data types the first request carries for one seed group.

    A constructor-supplied ``next_message`` replaces the seed-derived message. Otherwise the
    technique seed group is merged first, because a technique's prompts travel with the seed.
    ``with_technique`` raises when prompt sequences overlap a simulated conversation, so
    compatibility is checked first and the unmerged group is used otherwise — that pairing
    is rejected elsewhere and is not a modality problem.

    Returns:
        list[PromptDataType] | None: The ordered starting piece types, or ``None`` when undeterminable.
    """
    if not reads_next_message:
        return ["text"]

    if has_next_message_override:
        if next_message_override is None:
            return ["text"]
        from pyrit.models import Message

        if not isinstance(next_message_override, Message):
            return None
        return [piece.converted_value_data_type for piece in next_message_override.message_pieces] or ["text"]

    group = seed_group
    if seed_technique is not None:
        try:
            if group.is_compatible_with_technique(technique=seed_technique):
                group = group.with_technique(technique=seed_technique)
        except (AttributeError, TypeError, ValueError):
            return None

    try:
        message = group.next_message
    except (AttributeError, TypeError):
        return None

    if message is None:
        # No prompts on the group: the attack sends the objective text.
        return ["text"]

    try:
        types = [piece.converted_value_data_type for piece in message.message_pieces]
    except (AttributeError, TypeError):
        return None

    return types or ["text"]


def _read_modalities(*, target: PromptTarget, direction: str) -> frozenset[frozenset[PromptDataType]] | None:
    """
    Read a target's declared input or output modality combinations.

    Args:
        target (PromptTarget): The target to read.
        direction (str): Either ``"input"`` or ``"output"``.

    Returns:
        frozenset[frozenset[PromptDataType]] | None: The declared combinations, or ``None``
        when the value is not a real ``frozenset`` — most often a test double whose
        capabilities were never configured. Callers treat that as unknown, not as a failure.
    """
    try:
        capabilities = target.configuration.capabilities
        value = capabilities.input_modalities if direction == "input" else capabilities.output_modalities
    except AttributeError:
        return None
    # Statically this is always a frozenset, because ``TargetCapabilities`` validates it, and ty
    # says so. At runtime a test double that never configured capabilities yields a mock instead,
    # and this guard is what turns that into "unknown" rather than an empty combination set that
    # would wrongly read as incompatible. Deliberately redundant, like the defensive checks the
    # Alembic revisions keep.
    return value if isinstance(value, frozenset) else None  # ty: ignore[redundant-condition-strict]


def _format_modalities(modalities: frozenset[frozenset[PromptDataType]] | None) -> str:
    """
    Render modality combinations deterministically for an error message.

    Args:
        modalities (frozenset[frozenset[PromptDataType]] | None): The combinations to render.

    Returns:
        str: A sorted, stable rendering, or ``"<unknown>"``.
    """
    if modalities is None:
        return "<unknown>"
    return str([sorted(combination) for combination in sorted(modalities, key=sorted)])
