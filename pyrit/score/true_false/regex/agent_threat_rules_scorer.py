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
from pyrit.score.true_false.regex.regex_scorer import RegexScorer

logger = logging.getLogger(__name__)

# Pinned by default so a PyRIT release scores against a known ruleset. Callers
# that want to track ATR's main branch pass ref="main" explicitly and accept
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

    ATR rules are written against specific agent surfaces (``content``,
    ``tool_response``, ``tool_args`` and so on). A scorer sees one piece of
    text with no surface label, so by default only conditions written against
    the digest's ``default_fields`` are loaded. Pass ``fields`` to widen or
    narrow that selection when you know which surface your text came from.

    Note that ATR's own precision figures are measured on corpora that ATR
    rules were partly mined from, so they do not transfer to this setting.
    Treat this scorer as a fast pre-filter, not as a calibrated detector.
    """

    _DEFAULT_CATEGORIES: tuple[str, ...] = ("agent_threat",)

    def __init__(
        self,
        *,
        ref: str = DEFAULT_ATR_REF,
        fields: Sequence[str] | None = None,
        categories: Sequence[str] | None = None,
        cache: bool = True,
        validator: Any = None,
        score_aggregator: Any = None,
    ) -> None:
        """
        Args:
            ref: ATR git ref to load the digest from. Defaults to a pinned
                commit; pass ``"main"`` to track ATR's default branch.
            fields: ATR detection fields to load conditions for. Defaults to
                the digest's own ``default_fields``.
            categories: Score categories. Defaults to ``("agent_threat",)``.
            cache: Whether to cache the fetched digest under ``DB_DATA_PATH``.
            validator: Passed through to ``RegexScorer``.
            score_aggregator: Passed through to ``RegexScorer``.

        Raises:
            ValueError: If the digest is unreadable, carries an unsupported
                schema, or yields no patterns for the requested fields.
        """
        digest = _load_digest(ref=ref, cache=cache)
        patterns = _patterns_from_digest(digest, fields=fields)

        if not patterns:
            requested = list(fields) if fields is not None else digest.get("default_fields")
            raise ValueError(
                f"ATR digest at ref {ref!r} yielded no patterns for fields {requested!r}. "
                f"Fields present in this digest: {sorted(digest.get('conditions_by_field', {}))}"
            )

        self._atr_ref = ref
        self._atr_version = str(digest.get("atr_version", "unknown"))
        self._atr_commit = str(digest.get("atr_commit", ref))
        self._atr_fields = tuple(fields) if fields is not None else tuple(digest.get("default_fields", ()))
        self._atr_rule_count = len({v["rule_id"] for v in digest["conditions"].values() if "rule_id" in v})

        logger.info(
            "AgentThreatRulesScorer loaded %d patterns from ATR %s (%s), fields=%s",
            len(patterns),
            self._atr_version,
            self._atr_commit[:8],
            ",".join(self._atr_fields),
        )

        super().__init__(
            patterns=patterns,
            categories=list(categories) if categories is not None else list(self._DEFAULT_CATEGORIES),
            validator=validator,
            score_aggregator=score_aggregator,
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
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # A corrupt cache entry must not be fatal, but it must be visible:
            # silently refetching hides a disk problem that will recur.
            logger.warning("Discarding unreadable ATR digest cache %s: %s", cache_file, exc)

    url = _DIGEST_URL_TEMPLATE.format(ref=ref)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed https host
            raw = response.read().decode("utf-8")
    except Exception as exc:
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
        if not pattern:
            raise ValueError(f"ATR digest condition {name!r} has no pattern")
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
