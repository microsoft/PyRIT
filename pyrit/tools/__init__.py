# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Generic tool-use scaffolding for :class:`~pyrit.prompt_target.PromptTarget`.

This package provides a transport-agnostic tool-calling loop. The
:func:`tool_loop` decorator, when applied to ``send_prompt_async``, runs
the standard PyRIT validate+normalize work once and then repeatedly
re-enters the target's protected ``_send_prompt_to_target_async`` until
the model issues a stop response (or a configured limit is hit).

A target opts in by declaring two collaborators:

* ``self._tool_parser`` — a :class:`ToolCallParser` that walks a
  response message and extracts pending :class:`ToolCall` instances.
* ``self.configuration.tool_event_policy`` — a :class:`ToolEventPolicy`
  whose :class:`ToolEventBehavior` decides whether to ``EXECUTE``,
  ``RAISE``, or ``RETURN_RAW`` on each detected call.

When the policy is ``EXECUTE``, calls are dispatched through
``self.configuration.tool_backend``, an implementation of
:class:`ToolBackend`. :class:`LocalToolBackend` is the in-process
backend shipped here; :class:`MCPToolBackend` ships in C3 and proxies
through one or more MCP servers.

The :class:`ToolBackend` Protocol is intentionally distinct from
:mod:`pyrit.registry` — that namespace is reserved for framework-level
identity registries (``TargetRegistry``, ``ScorerRegistry``) that
register named singletons for CLI lookup, which a per-target tool
dispatch table is not.

Wiring of ``@tool_loop`` onto :class:`PromptTarget.send_prompt_async`
and of the ``tool_event_policy`` / ``tool_backend`` fields onto
:class:`TargetConfiguration` lands in C4/C5.

The two exception types the loop raises
(:class:`~pyrit.exceptions.ToolCallNotSupported` and
:class:`~pyrit.exceptions.ToolCallLoopLimitExceeded`) live in
:mod:`pyrit.exceptions` alongside the rest of PyRIT's exception
catalog, so non-tools callers (attacks, normalizers) can import them
without taking a subsystem-level dependency on ``pyrit.tools``.
"""

from pyrit.tools.backend import ToolBackend
from pyrit.tools.local_backend import LocalToolBackend
from pyrit.tools.mcp_backend import MCPToolBackend
from pyrit.tools.mcp_client import (
    DockerMCPServerSpec,
    LocalMCPServerSpec,
    MCPClient,
    MCPServerSpec,
    RemoteMCPServerSpec,
)
from pyrit.tools.models import ToolCall, ToolEventBehavior, ToolEventPolicy, tool_loop
from pyrit.tools.parsers import CanonicalEnvelopeParser, ToolCallParser

__all__ = [
    "CanonicalEnvelopeParser",
    "DockerMCPServerSpec",
    "LocalMCPServerSpec",
    "LocalToolBackend",
    "MCPClient",
    "MCPServerSpec",
    "MCPToolBackend",
    "RemoteMCPServerSpec",
    "ToolBackend",
    "ToolCall",
    "ToolCallParser",
    "ToolEventBehavior",
    "ToolEventPolicy",
    "tool_loop",
]
