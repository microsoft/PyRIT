# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
View resolution, ``--limit`` policy, and attack selection for the
``scenario-results`` command.

Rendering is delegated to ``pyrit.output`` (the scenario, attacks, and conversation
printers); this module holds only the CLI-side flag policy and the objective-scorer
key helper (attack selection is shared via ``pyrit.output._derivation.select_attacks``).
``ScenarioResultView`` lives in ``pyrit.cli._cli_args`` so the argument parsers can
reference it cheaply.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from pyrit.cli._cli_args import ScenarioResultView

if TYPE_CHECKING:
    from pyrit.models import ScenarioResult
    from pyrit.output.sink import Sink

#: Default cap on how many attacks the transcript-fetching views (``conversations``
#: and ``full``) render when the user gives neither ``--attack-result-ids`` nor
#: ``--limit``. Unlike ``attacks`` (a single embedded read), these make a per-attack
#: message fetch, so an unbounded run could pull many transcripts.
_DEFAULT_HEAVY_VIEW_LIMIT = 5


def resolve_view(*, view: ScenarioResultView | None) -> ScenarioResultView:
    """
    Resolve an optional ``--view`` value to a concrete view.

    Args:
        view (ScenarioResultView | None): The parsed view, or ``None`` when the
            flag was omitted.

    Returns:
        ScenarioResultView: The explicit view, defaulting to ``OVERVIEW``.
    """
    return view if view is not None else ScenarioResultView.OVERVIEW


def apply_view_limit_policy(
    *,
    view: ScenarioResultView,
    limit: int | None,
    attack_result_ids: list[str] | None = None,
) -> int | None:
    """
    Apply the ``--limit`` policy for the chosen *view*.

    Each view treats ``--limit`` differently, so the policy is centralized here
    (rather than in a renderer) so every output format honors the same effective
    limit:

    - ``overview`` has no per-attack list, so a ``--limit`` is a no-op: warn and
      drop it.
    - ``attacks`` is a single embedded read, so it honors ``--limit`` verbatim
      and has no default cap (silent truncation would hide data).
    - ``conversations`` / ``full`` make a per-attack message fetch, so when the
      user scopes neither the attacks (``--attack-result-ids``) nor the count
      (``--limit``), fall back to ``_DEFAULT_HEAVY_VIEW_LIMIT`` and say so, to
      avoid accidentally pulling every transcript in a large run.

    Args:
        view (ScenarioResultView): The resolved view.
        limit (int | None): The requested row cap, if any.
        attack_result_ids (list[str] | None): The attacks the user scoped to, if
            any. Only consulted for the transcript views' default-limit fallback.
            Defaults to None.

    Returns:
        int | None: The effective limit (``None`` means "no cap").
    """
    if view is ScenarioResultView.OVERVIEW:
        if limit is not None:
            # Advisory notices go to stderr so stdout stays a single valid document (e.g. --format json).
            print("Note: --limit has no effect with --view overview; ignoring it.", file=sys.stderr)
        return None
    if view in (ScenarioResultView.CONVERSATIONS, ScenarioResultView.FULL):
        if limit is None and not attack_result_ids:
            print(
                f"Note: no --attack-result-ids or --limit given; showing at most "
                f"{_DEFAULT_HEAVY_VIEW_LIMIT} conversations. Pass --limit or "
                "--attack-result-ids to see more.",
                file=sys.stderr,
            )
            return _DEFAULT_HEAVY_VIEW_LIMIT
        return limit
    return limit


def warn_if_view_ignored_by_html(*, view: ScenarioResultView | None) -> None:
    """
    Warn when an explicit ``--view`` is discarded because ``--format html`` always
    renders the full report.

    ``html`` is not a rendering of a *view* — it is a fixed complete report — so any
    ``--view`` other than ``full`` is silently ignored. The parser defaults ``--view``
    to ``None``, so an explicit value is distinguishable from an omitted one.

    Args:
        view (ScenarioResultView | None): The raw parsed ``--view``, or ``None`` when omitted.
    """
    if view is not None and view is not ScenarioResultView.FULL:
        print(
            f"Note: --view {view.value} is ignored with --format html; rendering the full report.",
            file=sys.stderr,
        )


#: Formats that ``--output`` can write to a file. Pretty is terminal-oriented
#: (redirect with ``> file`` instead).
_FILE_OUTPUT_FORMATS = frozenset({"json", "html"})


def resolve_output_sink(*, output_path: str | None, output_format: str) -> Sink | None:
    """
    Resolve ``--output`` to a file sink, or ``None`` for stdout.

    Args:
        output_path (str | None): The ``--output`` path, or None when omitted.
        output_format (str): The resolved ``--format`` value.

    Returns:
        Sink | None: A ``FileSink`` for *output_path*, or None to use the default (stdout).

    Raises:
        ValueError: If ``html`` is requested without ``--output``, a file destination is
            requested for a terminal-oriented format, or its parent directory does not exist.
    """
    if output_format == "html" and output_path is None:
        raise ValueError("--format html writes a report file and requires --output PATH.")
    if output_path is None:
        return None
    if output_format not in _FILE_OUTPUT_FORMATS:
        raise ValueError(
            f"--output writes a document file and requires --format json or html, not {output_format!r}. "
            "For pretty output, redirect with '> file' instead."
        )
    from pathlib import Path

    from pyrit.output.sink import FileSink

    path = Path(output_path)
    if not path.parent.exists():
        raise ValueError(f"--output directory does not exist: {path.parent}")
    return FileSink(path=path)


def _objective_scorer_key(*, result: ScenarioResult) -> tuple[str | None, str | None]:
    """
    Extract the scenario objective scorer's ``(hash, class_name)`` match key.

    The objective scorer is the one whose verdict determines attack success, so
    its identity is how the transcript picks the single meaningful score out of
    the several attached to each response.

    Args:
        result (ScenarioResult): The scenario result whose objective scorer to read.

    Returns:
        tuple[str | None, str | None]: The identity hash and class name, or
            ``(None, None)`` when the scenario declares no objective scorer.
    """
    identifier = result.objective_scorer_identifier
    if identifier is None:
        return None, None
    return identifier.hash, identifier.class_name
