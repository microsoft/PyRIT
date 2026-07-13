# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Streaming attack strategies (barge-in over realtime audio targets)."""

from pyrit.executor.attack.streaming.barge_in import BargeInAttack, BargeInAttackContext

__all__ = [
    "BargeInAttack",
    "BargeInAttackContext",
]
