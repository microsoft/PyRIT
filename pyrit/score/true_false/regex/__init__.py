# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Regex-based true/false scorers for detecting credential leaks, OWASP LLM02
insecure-output payloads (XSS, SQL injection, shell commands, path traversal,
SSRF, SSTI, XXE, open redirect, and LDAP injection), prompt injection,
markdown injection, and CBRN/illicit-substance keywords.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.score.true_false.regex.anthrax_keyword_scorer import AnthraxKeywordScorer
    from pyrit.score.true_false.regex.credential_leak_scorer import CredentialLeakScorer
    from pyrit.score.true_false.regex.fentanyl_keyword_scorer import FentanylKeywordScorer
    from pyrit.score.true_false.regex.ldap_injection_output_scorer import LDAPInjectionOutputScorer
    from pyrit.score.true_false.regex.markdown_injection import MarkdownInjectionScorer
    from pyrit.score.true_false.regex.meth_keyword_scorer import MethKeywordScorer
    from pyrit.score.true_false.regex.nerve_agent_keyword_scorer import NerveAgentKeywordScorer
    from pyrit.score.true_false.regex.open_redirect_output_scorer import OpenRedirectOutputScorer
    from pyrit.score.true_false.regex.package_hallucination_scorer import PackageEcosystem, PackageHallucinationScorer
    from pyrit.score.true_false.regex.path_traversal_output_scorer import PathTraversalOutputScorer
    from pyrit.score.true_false.regex.regex_scorer import RegexScorer
    from pyrit.score.true_false.regex.shell_command_output_scorer import ShellCommandOutputScorer
    from pyrit.score.true_false.regex.sql_injection_output_scorer import SQLInjectionOutputScorer
    from pyrit.score.true_false.regex.ssrf_output_scorer import SSRFOutputScorer
    from pyrit.score.true_false.regex.ssti_output_scorer import SSTIOutputScorer
    from pyrit.score.true_false.regex.static_prompt_injection_scorer import StaticPromptInjectionScorer
    from pyrit.score.true_false.regex.xss_output_scorer import XSSOutputScorer
    from pyrit.score.true_false.regex.xxe_output_scorer import XXEOutputScorer

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AnthraxKeywordScorer": "pyrit.score.true_false.regex.anthrax_keyword_scorer",
    "CredentialLeakScorer": "pyrit.score.true_false.regex.credential_leak_scorer",
    "FentanylKeywordScorer": "pyrit.score.true_false.regex.fentanyl_keyword_scorer",
    "LDAPInjectionOutputScorer": "pyrit.score.true_false.regex.ldap_injection_output_scorer",
    "MarkdownInjectionScorer": "pyrit.score.true_false.regex.markdown_injection",
    "MethKeywordScorer": "pyrit.score.true_false.regex.meth_keyword_scorer",
    "NerveAgentKeywordScorer": "pyrit.score.true_false.regex.nerve_agent_keyword_scorer",
    "OpenRedirectOutputScorer": "pyrit.score.true_false.regex.open_redirect_output_scorer",
    "PackageEcosystem": "pyrit.score.true_false.regex.package_hallucination_scorer",
    "PackageHallucinationScorer": "pyrit.score.true_false.regex.package_hallucination_scorer",
    "PathTraversalOutputScorer": "pyrit.score.true_false.regex.path_traversal_output_scorer",
    "RegexScorer": "pyrit.score.true_false.regex.regex_scorer",
    "ShellCommandOutputScorer": "pyrit.score.true_false.regex.shell_command_output_scorer",
    "SQLInjectionOutputScorer": "pyrit.score.true_false.regex.sql_injection_output_scorer",
    "SSRFOutputScorer": "pyrit.score.true_false.regex.ssrf_output_scorer",
    "SSTIOutputScorer": "pyrit.score.true_false.regex.ssti_output_scorer",
    "StaticPromptInjectionScorer": "pyrit.score.true_false.regex.static_prompt_injection_scorer",
    "XSSOutputScorer": "pyrit.score.true_false.regex.xss_output_scorer",
    "XXEOutputScorer": "pyrit.score.true_false.regex.xxe_output_scorer",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
