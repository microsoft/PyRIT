# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecation shim — the question-answering scoring helpers now live in
``pyrit.score``.

Importing names from ``pyrit.common.question_answer_helpers`` still works for one
release but emits a one-time ``DeprecationWarning`` per name. Import from
``pyrit.score.question_answer_helpers`` instead. This shim will be removed in
0.16.0.

NOTE: When this shim is removed, also drop the
``pyrit.common.question_answer_helpers`` entry from ``KNOWN_COMMON_VIOLATIONS`` in
``tests/unit/models/test_import_boundary.py`` if it has not already been removed,
so the reverse-guard ratchet bookkeeping is not missed.
"""

from __future__ import annotations

from pyrit.common.deprecation import module_deprecation_getattr

__all__ = [
    "construct_evaluation_prompt",
]

__getattr__ = module_deprecation_getattr(
    old_module="pyrit.common.question_answer_helpers",
    target_module="pyrit.score.question_answer_helpers",
    names=__all__,
    removed_in="0.16.0",
)


def __dir__() -> list[str]:
    return sorted(__all__)
