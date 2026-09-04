# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
CLI-side data sources for the ``pyrit.output`` printers.

The thin REST client can't use the framework's ``CentralMemory``-backed sources,
so this module supplies a ``ConversationSource`` that hydrates ``pyrit.models``
objects from the ``/messages`` view JSON and serves the objective score inline
(no extra endpoint). It lives here — not in ``pyrit.output`` — so the framework
output layer never imports ``pyrit.cli``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrit.models import Message, MessagePiece, Score

if TYPE_CHECKING:
    from pyrit.cli.api_client import PyRITApiClient


def _only_known(model_cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """
    Keep only the fields the domain model declares (drops view-only extras).

    Args:
        model_cls (type): The pydantic model whose fields to keep.
        data (dict[str, Any]): The raw view JSON.

    Returns:
        dict[str, Any]: The subset of ``data`` whose keys are model fields.
    """
    return {key: value for key, value in data.items() if key in model_cls.model_fields}


class RestApiConversationSource:
    """
    ``ConversationSource`` backed by the REST api client (thin CLI path).

    ``get_messages_async`` hydrates ``pyrit.models`` messages from the
    ``/messages`` view JSON and, in the same pass, captures the **objective**
    score per piece (matched by the scenario's objective scorer identity) so
    ``get_scores_async`` needs no additional endpoint. Only the objective score is
    surfaced — the response also carries auxiliary sub-scores (refusal, etc.)
    whose verdicts read backwards relative to attack success.
    """

    def __init__(
        self,
        *,
        client: PyRITApiClient,
        attack_result_id: str,
        objective_hash: str | None = None,
        objective_class: str | None = None,
    ) -> None:
        """
        Args:
            client (PyRITApiClient): Transport used to fetch the conversation messages.
            attack_result_id (str): The attack whose conversation is fetched.
            objective_hash (str | None): The objective scorer's identity hash, used
                to select which score to surface. Defaults to None.
            objective_class (str | None): The objective scorer's class name, used as
                a fallback match when the hash is unavailable. Defaults to None.
        """
        self._client = client
        self._attack_result_id = attack_result_id
        self._objective_hash = objective_hash
        self._objective_class = objective_class
        self._scores_by_piece: dict[str, list[Score]] = {}

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        """
        Fetch and hydrate the conversation's messages, capturing objective scores.

        Args:
            conversation_id (str): The conversation whose messages to fetch.

        Returns:
            list[Message]: The hydrated messages in order.
        """
        response = await self._client.get_conversation_messages_async(
            attack_result_id=self._attack_result_id,
            conversation_id=conversation_id,
        )
        self._scores_by_piece = {}
        messages: list[Message] = []
        for message_json in response.get("messages", []):
            role = message_json.get("role")
            pieces: list[MessagePiece] = []
            for piece_json in message_json.get("message_pieces") or []:
                piece = self._hydrate_piece(piece_json=piece_json, message_role=role)
                pieces.append(piece)
                objective = self._select_objective_score(piece_json=piece_json)
                if objective is not None:
                    self._scores_by_piece[str(piece.id)] = [objective]
            if pieces:
                messages.append(Message(message_pieces=pieces))
        return messages

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Return the objective scores captured for the given piece ids.

        Args:
            prompt_ids (list[str]): The message-piece ids to fetch scores for.

        Returns:
            list[Score]: The objective scores for those pieces (empty if none).
        """
        return [score for prompt_id in prompt_ids for score in self._scores_by_piece.get(prompt_id, [])]

    def _hydrate_piece(self, *, piece_json: dict[str, Any], message_role: str | None) -> MessagePiece:
        """
        Hydrate one ``MessagePieceView`` payload into a domain ``MessagePiece``.

        Args:
            piece_json (dict[str, Any]): A single piece from the view JSON.
            message_role (str | None): The enclosing message's role, used when the
                piece omits its own (keeps ``Message``'s role invariant satisfied).

        Returns:
            MessagePiece: The hydrated piece.
        """
        data = _only_known(MessagePiece, piece_json)
        if not data.get("role"):
            data["role"] = message_role or "user"
        if not data.get("original_value"):
            data["original_value"] = piece_json.get("converted_value") or piece_json.get("original_value") or ""
        return MessagePiece.model_validate(data)

    def _select_objective_score(self, *, piece_json: dict[str, Any]) -> Score | None:
        """
        Pick the objective score from a piece's scores, matched by scorer identity.

        Matches by identity hash first, then class name, rather than list order.

        Args:
            piece_json (dict[str, Any]): A single piece from the view JSON.

        Returns:
            Score | None: The hydrated objective score, or None when absent.
        """
        fallback: Score | None = None
        for score_json in piece_json.get("scores") or []:
            identifier = score_json.get("scorer_class_identifier") or {}
            if self._objective_hash and identifier.get("hash") == self._objective_hash:
                return self._hydrate_score(score_json=score_json)
            if self._objective_class and fallback is None and score_json.get("scorer_type") == self._objective_class:
                fallback = self._hydrate_score(score_json=score_json)
        return fallback

    def _hydrate_score(self, *, score_json: dict[str, Any]) -> Score | None:
        """
        Hydrate one ``ScoreView`` payload into a domain ``Score``, best-effort.

        Args:
            score_json (dict[str, Any]): A single score from the view JSON.

        Returns:
            Score | None: The hydrated score, or None if it can't be validated.
        """
        try:
            return Score.model_validate(_only_known(Score, score_json))
        except Exception:
            return None
