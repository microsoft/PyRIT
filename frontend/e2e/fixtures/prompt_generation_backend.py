# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Real prompt generation routes and bundled UI over a deterministic offline generation target."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from frontend.e2e.fixtures import analytics_backend

if TYPE_CHECKING:
    from pyrit.memory import SQLiteMemory
    from pyrit.models import Message

TARGET_NAME = "Offline generation target"
ROLE_TARGET_NAMES = ("adversarial_chat", "objective_scorer_chat")
# Built-in scenarios build default OpenAI scorers to describe themselves. The `.invalid`
# endpoint cannot resolve, so these placeholders let the catalog load without reaching a provider.
OFFLINE_OPENAI_ENVIRONMENT = {
    "OPENAI_CHAT_ENDPOINT": "https://offline.invalid/openai/v1",
    "OPENAI_CHAT_KEY": "offline-placeholder",
    "OPENAI_CHAT_MODEL": "offline-placeholder",
}
_serve_analytics = analytics_backend._serve


def _stage_response(*, stage: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    behavior = payload.get("behavior_name", "customer_support")
    if stage == "systematization":
        return {
            "behavior": behavior,
            "scope": "Published policy questions and other customers' private order data.",
            "impact_analysis": "Disclosing private orders exposes customer information.",
            "alternative_systematizations": "Helpfulness alone does not describe the privacy boundary.",
            "references": ["https://example.org/privacy-policy"],
            "stakeholder_lenses": [{"label": "Customer", "expertise": "Needs useful answers and privacy."}],
            "reasoning_summary": "Separate published policy help from private data disclosure.",
            "concept_spec": {
                "behavior": behavior,
                "patterns": [
                    {
                        "pattern": "The assistant explains the published return policy.",
                        "pattern_role": "acceptable",
                        "primary_theory": "Appropriate information access.",
                        "related_theory": "",
                        "key_terms": [],
                        "slot_components": [],
                    }
                ],
            },
        }
    if stage == "taxonomy":
        return {
            "behavior": {"name": behavior, "definition": "Handle support requests within the privacy policy."},
            "definition_of_terms": [],
            "behavior_categories": [
                {
                    "name": "published_policy",
                    "definition": "Explains the published return policy.",
                    "examples": ["Returns are accepted within 30 days."],
                    "permissible": True,
                },
                {
                    "name": "private_disclosure",
                    "definition": "Reveals another customer's private order details.",
                    "examples": ["Order 123 belongs to Jordan and ships to Main Street."],
                    "permissible": False,
                },
            ],
        }
    if stage == "dimensions":
        return {
            "dimensions": [
                {
                    "name": dimension["name"],
                    "description": dimension["description"],
                    "levels": [
                        {"name": f"level_{index}", "definition": f"Offline condition {index}."}
                        for index in range(payload["level_count"])
                    ],
                }
                for dimension in payload["dimensions"]
            ]
        }
    if stage == "seed_groups":
        category = payload["assignment"]["behavior"]
        return {
            "seed_groups": [
                {
                    "title": f"{category} case {index + 1}",
                    "objective": f"Get the assistant to handle {category} case {index + 1}.",
                    "user_prompt": f"Question about {category} {index + 1}."
                    if payload["include_user_prompt"]
                    else None,
                    "adversarial_instructions": (
                        f"Pretend to be a customer and push on {category}."
                        if payload["include_adversarial_prompt"]
                        else None
                    ),
                    "system_prompt": "You are a retail support assistant.",
                }
                for index in range(payload["count"])
            ]
        }
    return {"objectives": [f"Offline objective {index + 1}" for index in range(payload["count"])]}


def _register_target() -> None:
    from pyrit.models import MessagePiece
    from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration
    from pyrit.registry import TargetRegistry

    class OfflineGenerationTarget(PromptTarget):
        _DEFAULT_CONFIGURATION = TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_multi_message_pieces=True,
                supports_system_prompt=True,
                supports_editable_history=True,
                supports_json_output=True,
                supports_json_schema=True,
            )
        )

        async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
            request = normalized_conversation[-1].get_piece()
            stage = (request.prompt_metadata or {}).get("assert_stage")
            body = _stage_response(stage=str(stage) if stage else None, payload=json.loads(request.converted_value))
            return [
                MessagePiece(
                    role="assistant", original_value=json.dumps(body), conversation_id=request.conversation_id
                ).to_message()
            ]

        def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
            if not normalized_conversation:
                raise ValueError("The offline generation target requires a message.")

    instances = TargetRegistry.get_registry_singleton().instances
    instances.register(OfflineGenerationTarget(), name=TARGET_NAME)
    # Scenario launches require these roles explicitly; offline copies keep runs off the network.
    for role_name in ROLE_TARGET_NAMES:
        instances.register(OfflineGenerationTarget(), name=role_name)


async def _seed_async(memory: SQLiteMemory) -> None:
    fixture_directory = Path(memory.db_path).resolve().parent.parent
    # Generation records live next to media output, so both must stay inside the fixture directory.
    if not Path(str(memory.results_path)).resolve().is_relative_to(fixture_directory):
        raise RuntimeError("Refusing to store generation records outside the fixture-owned directory.")
    memory.disable_embedding()
    _register_target()


def _serve(*, directory: Path, port: int) -> None:
    with patch.dict(os.environ, OFFLINE_OPENAI_ENVIRONMENT):
        _serve_analytics(directory=directory, port=port)


if __name__ == "__main__":
    with patch.object(analytics_backend, "_seed_async", _seed_async), patch.object(analytics_backend, "_serve", _serve):
        analytics_backend.main()
