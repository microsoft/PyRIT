# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import json
import logging
import re
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pyrit.common.path import DB_DATA_PATH
from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.regex.regex_scorer import RegexScorer
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc, TrueFalseScoreAggregator

logger = logging.getLogger(__name__)

# Pinned by default so a PyRIT release scores against a known ruleset. Callers
# that want to track ATR's main branch pass ref="main", cache=False and accept
# that their results move when ATR does.
DEFAULT_ATR_REF = "54d3e13e94f8980d7b36f9d79511b26174954dfc"

_DIGEST_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/Agent-Threat-Rule/agent-threat-rules/{ref}/data/pyrit-digest.json"
)

# The digest schema this scorer understands. A mismatch means ATR changed the
# contract; failing loudly beats silently scoring against a shape we guessed at.
SUPPORTED_DIGEST_SCHEMA = 1

_CACHE_SUBDIR = "atr-digest"


class AgentThreatRulesScorer(RegexScorer):
    """
    Scores text against the Agent Threat Rules (ATR) detection ruleset.

    ATR is an open detection-rule standard for AI agent attacks — prompt
    injection, tool poisoning, context exfiltration and related categories.
    This scorer consumes a precompiled digest that ATR's CI publishes, so it
    adds no dependency: every pattern in the digest is plain Python ``re``
    syntax and is compiled by ``RegexScorer`` exactly as any other
    pattern set would be.

    The digest is fetched from a pinned commit by default and cached under
    ``DB_DATA_PATH``, the same mechanism the ATR seed dataset already uses.

    ``fields`` selects rules, not evidence. Message roles and data types select
    the evidence: text supplies ``content`` and its role-specific field,
    ``function_call`` pieces supply ``tool_name`` and ``tool_args``, and
    ``function_call_output`` pieces supply ``content`` and ``tool_response``.
    Loose text is scored as user text. A call match describes a requested call,
    not proof that a tool executed.

    Note that ATR's own precision figures are measured on corpora that ATR
    rules were partly mined from, so they do not transfer to this setting.
    Treat this scorer as a fast pre-filter, not as a calibrated detector.
    """

    _DEFAULT_CATEGORIES: tuple[str, ...] = ("agent_threat",)
    _SUPPORTED_FIELDS = frozenset({"content", "agent_output", "user_input", "tool_name", "tool_args", "tool_response"})
    _DEFAULT_VALIDATOR = ScorerPromptValidator(
        supported_data_types=["text", "function_call", "function_call_output"],
    )
    _ROLE_FIELDS: dict[str, str] = {
        "assistant": "agent_output",
        "simulated_assistant": "agent_output",
        "user": "user_input",
        "tool": "tool_response",
    }

    def __init__(
        self,
        *,
        ref: str = DEFAULT_ATR_REF,
        fields: Sequence[str] | None = None,
        categories: Sequence[str] | None = None,
        cache: bool = True,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Args:
            ref: ATR git ref to load the digest from. Defaults to a pinned
                commit; pass ``"main", cache=False`` to track ATR's default branch.
            fields: ATR detection fields to load conditions for. Defaults to
                the digest's own ``default_fields``.
            categories: Score categories. Defaults to ``("agent_threat",)``.
            cache: Whether to cache the digest indefinitely under ``DB_DATA_PATH``.
                Disable this for a fresh download from a mutable ref.
            validator: Passed through to ``RegexScorer``.
            score_aggregator: Passed through to ``RegexScorer``.

        Raises:
            ValueError: If the digest is unreadable, carries an unsupported
                schema, or yields no patterns for the requested fields.
        """
        digest = _load_digest(ref=ref, cache=cache)
        selected_fields = fields if fields is not None else digest.get("default_fields")
        if (
            not isinstance(selected_fields, Sequence)
            or isinstance(selected_fields, str)
            or not all(isinstance(field, str) for field in selected_fields)
        ):
            raise ValueError("fields must be a sequence of field names, not a string")
        unsupported = set(selected_fields) - self._SUPPORTED_FIELDS
        if unsupported:
            raise ValueError(f"No message extraction is available for ATR fields: {sorted(unsupported)}")
        patterns = _patterns_from_digest(digest, fields=selected_fields)

        present = {condition["field"] for condition in digest["conditions"].values()}
        missing = set(selected_fields) - present
        if missing:
            raise ValueError(
                f"ATR digest at ref {ref!r} yielded no patterns for fields {sorted(missing)!r}. "
                f"Fields present in this digest: {sorted(present)}"
            )

        self._atr_fields = tuple(sorted(set(selected_fields)))
        self._digest_hash = hashlib.sha256(
            json.dumps(digest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self._provenance: dict[str, str | int | float] = {
            "atr_ref": ref,
            "atr_digest_url": _DIGEST_URL_TEMPLATE.format(ref=ref),
            "atr_digest_sha256": self._digest_hash,
        }
        for key in ("atr_commit", "atr_version"):
            if isinstance(digest.get(key), str):
                self._provenance[key] = digest[key]

        logger.info(
            "AgentThreatRulesScorer loaded %d patterns from ATR %s (%s), fields=%s",
            len(patterns),
            digest.get("atr_version", "unknown"),
            str(digest.get("atr_commit", ref))[:8],
            ",".join(self._atr_fields),
        )

        super().__init__(
            patterns=patterns,
            categories=list(categories) if categories is not None else list(self._DEFAULT_CATEGORIES),
            validator=validator,
            score_aggregator=score_aggregator,
        )
        self._patterns_by_field = {
            field: {name: self._compiled[name] for name in patterns if digest["conditions"][name]["field"] == field}
            for field in self._atr_fields
        }

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "digest_sha256": self._digest_hash,
                "fields": list(self._atr_fields),
                "categories": self._score_categories,
            },
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        values = self._extract_fields(message_piece)
        fields = self._patterns_by_field.keys() & values.keys()
        if not fields:
            return []
        texts: dict[str, list[str]] = {}
        for field in fields:
            text = values[field]
            if text is None:
                continue
            texts[field] = [text]
            if field == "tool_args" and (arguments := _json_object(text)) is not None:
                normalized = _field_text(arguments)
                if normalized != text:
                    texts[field].append(normalized)
        matched = sorted(
            name
            for field, candidates in texts.items()
            for name, pattern in self._patterns_by_field[field].items()
            if any(pattern.search(text) for text in candidates)
        )
        unreadable = sorted(field for field in fields if values[field] is None)
        if not matched and unreadable:
            score = self._build_undetermined_score(
                rationale=f"Could not read ATR fields: {', '.join(unreadable)}.",
                message_piece_id=message_piece.id,
                objective=objective,
                score_category=self._score_categories,
            )
        else:
            score = self._build_match_score(
                message_piece=message_piece,
                matched=matched,
                objective=objective,
                description="True if an ATR pattern matched its message field; not proof of tool execution.",
            )
        return self._with_provenance([score])

    def _build_fallback_score(self, *, message: Message, objective: str | None) -> list[Score]:
        return self._with_provenance(super()._build_fallback_score(message=message, objective=objective))

    def _with_provenance(self, scores: list[Score]) -> list[Score]:
        for score in scores:
            score.score_metadata = {**(score.score_metadata or {}), **self._provenance}
        return scores

    @classmethod
    def _extract_fields(cls, piece: MessagePiece) -> dict[str, str | None]:
        data_type = piece.converted_value_data_type
        if data_type == "text":
            values: dict[str, str | None] = {"content": piece.converted_value}
            if role_field := cls._ROLE_FIELDS.get(piece.role):
                values[role_field] = piece.converted_value
            return values
        if data_type == "function_call_output" and piece.role == "tool":
            payload = _json_object(piece.converted_value)
            output = _field_text(payload["output"]) if payload is not None and "output" in payload else None
            return {"content": output, "tool_response": output}
        if data_type != "function_call" or piece.role != "assistant":
            return {}
        payload = _json_object(piece.converted_value)
        # Chat Completions nests the call under "function"; Responses keeps it flat.
        if payload is not None and payload.get("type") == "function":
            payload = payload.get("function")
        if not isinstance(payload, dict):
            return {"tool_name": None, "tool_args": None}
        name = payload.get("name")
        arguments = payload.get("arguments")
        return {
            "tool_name": name if isinstance(name, str) and name.strip() else None,
            "tool_args": _field_text(arguments) if isinstance(arguments, (str, dict)) else None,
        }


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _field_text(value: Any) -> str:
    """
    Keep strings intact; encode structured values as compact, stable JSON.

    Returns:
        str: The field's text representation.
    """
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    )


def _patterns_from_digest(
    digest: dict[str, Any],
    *,
    fields: Sequence[str] | None = None,
) -> dict[str, str]:
    """
    Select the digest conditions that apply to ``fields`` and return them as
    the ``{name: pattern}`` mapping ``RegexScorer`` expects.

    Condition keys are already unique in the digest (``<rule-id>#<index>``),
    so they double as pattern names and keep a match traceable to its rule.

    Args:
        digest: A parsed ATR digest.
        fields: Detection fields to select. Defaults to the digest's own
            ``default_fields``.

    Returns:
        dict[str, str]: A ``{condition_name: pattern}`` mapping.

    Raises:
        ValueError: If the digest has no conditions object, or no fields were
            requested and the digest declares no ``default_fields``.
    """
    conditions = digest.get("conditions")
    if not isinstance(conditions, dict):
        raise ValueError("ATR digest has no 'conditions' object")

    wanted = set(fields) if fields is not None else set(digest.get("default_fields", ()))
    if not wanted:
        raise ValueError("No fields requested and the digest declares no 'default_fields'")

    return {
        name: condition["pattern"]
        for name, condition in conditions.items()
        if condition.get("field") in wanted and condition.get("pattern")
    }


def _load_digest(*, ref: str, cache: bool) -> dict[str, Any]:
    """
    Fetch the ATR digest for ``ref``, reading from cache when available.

    Args:
        ref: ATR git ref to load from.
        cache: Whether to read from and write to the on-disk cache.

    Returns:
        dict[str, Any]: The parsed, validated digest.

    Raises:
        ValueError: If the digest cannot be fetched, parsed, or validated.
    """
    cache_file = _cache_path(ref)

    if cache and cache_file.exists():
        try:
            digest = json.loads(cache_file.read_text(encoding="utf-8"))
            _validate_digest(digest, source=str(cache_file))
            return digest
        except (OSError, ValueError) as exc:
            # A corrupt cache entry must not be fatal, but it must be visible:
            # silently refetching hides a disk problem that will recur.
            logger.warning("Discarding unreadable ATR digest cache %s: %s", cache_file, exc)

    url = _DIGEST_URL_TEMPLATE.format(ref=ref)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed https host
            raw = response.read().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Could not fetch the ATR digest from {url}: {exc}") from exc

    try:
        digest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ATR digest at {url} is not valid JSON: {exc}") from exc

    _validate_digest(digest, source=url)

    if cache:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(raw, encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not cache the ATR digest at %s: %s", cache_file, exc)

    return digest


def _validate_digest(digest: Any, *, source: str) -> None:
    """
    Reject a digest this scorer cannot score against.

    Every pattern is compiled here rather than at match time, so an ATR-side
    regression surfaces as a construction error naming the offending rule
    instead of a scorer that silently matches less than it reports.

    Args:
        digest: The parsed digest to validate.
        source: Where it came from, for error messages.

    Raises:
        ValueError: If the digest is not an object, declares an unsupported
            schema, has no conditions, or carries a pattern that does not
            compile under Python ``re``.
    """
    if not isinstance(digest, dict):
        raise ValueError(f"ATR digest from {source} is not a JSON object")

    schema = digest.get("schema")
    if schema != SUPPORTED_DIGEST_SCHEMA:
        raise ValueError(
            f"ATR digest from {source} declares schema {schema!r}; "
            f"this scorer supports schema {SUPPORTED_DIGEST_SCHEMA}"
        )

    conditions = digest.get("conditions")
    if not isinstance(conditions, dict) or not conditions:
        raise ValueError(f"ATR digest from {source} has no conditions")

    for name, condition in conditions.items():
        pattern = condition.get("pattern") if isinstance(condition, dict) else None
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"ATR digest condition {name!r} has no pattern")
        if not isinstance(condition.get("field"), str) or not condition["field"]:
            raise ValueError(f"ATR digest condition {name!r} has no field")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"ATR digest condition {name!r} does not compile under Python re: {exc}") from exc


def _cache_path(ref: str) -> Path:
    """
    Cache file for ``ref``, hashed so a branch name cannot escape the directory.

    Args:
        ref: ATR git ref the digest was fetched for.

    Returns:
        Path: The on-disk cache location for that ref.
    """
    digest_name = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:16]
    return Path(DB_DATA_PATH) / _CACHE_SUBDIR / f"pyrit-digest-{digest_name}.json"
