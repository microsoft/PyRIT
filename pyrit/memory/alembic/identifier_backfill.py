# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Frozen, model-independent helpers for identifier migration backfills."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)


def run_best_effort_backfill(*, bind: Any, name: str, backfill: Callable[[], None]) -> None:
    """Run a data backfill in a savepoint without blocking the schema upgrade."""
    try:
        with bind.begin_nested():
            backfill()
    except Exception:
        logger.warning(f"{name} backfill failed; leaving new identifier links nullable", exc_info=True)


def load_identifier(raw_identifier: Any) -> dict[str, Any] | None:
    """
    Load a retained identifier JSON value without importing domain models.

    Returns:
        dict[str, Any] | None: The identifier dictionary when it has a usable hash.
    """
    try:
        value = json.loads(raw_identifier) if isinstance(raw_identifier, str) else raw_identifier
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    identifier_hash = value.get("hash")
    if not isinstance(identifier_hash, str) or len(identifier_hash) != 64:
        return None
    return value


def load_identifier_list(raw_identifiers: Any) -> list[dict[str, Any]]:
    """
    Load the valid identifiers from a retained JSON list.

    Returns:
        list[dict[str, Any]]: Identifier dictionaries carrying usable hashes.
    """
    try:
        values = json.loads(raw_identifiers) if isinstance(raw_identifiers, str) else raw_identifiers
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    return [identifier for value in values if (identifier := load_identifier(value)) is not None]


class IdentifierGraphInserter:
    """Best-effort inserter for the frozen flat identifier JSON shape."""

    _TABLES = (
        "TargetIdentifiers",
        "ScorerIdentifiers",
        "ConverterIdentifiers",
        "ScenarioIdentifiers",
        "SeedIdentifiers",
        "AttackIdentifiers",
        "AttackTechniqueIdentifiers",
        "AtomicAttackIdentifiers",
    )

    def __init__(self, *, bind: Any) -> None:
        """Initialize the inserter from tables available at this migration revision."""
        self._bind = bind
        table_names = set(sa.inspect(bind).get_table_names())
        self._hashes = {
            table: set(bind.execute(sa.text(f'SELECT hash FROM "{table}"')).scalars())
            for table in self._TABLES
            if table in table_names
        }

    def insert_target(self, identifier: dict[str, Any]) -> str | None:
        """
        Insert a target graph.

        Returns:
            str | None: The stored hash when successful.
        """
        children = self._children(identifier, "targets")
        child_hashes = [child_hash for child in children if (child_hash := self.insert_target(child))]
        identifier_hash = self._insert_identifier(
            table="TargetIdentifiers",
            identifier=identifier,
            promoted=(
                "endpoint",
                "model_name",
                "underlying_model_name",
                "temperature",
                "top_p",
                "max_requests_per_minute",
                "supported_auth_modes",
            ),
        )
        if identifier_hash:
            self._insert_edges(
                table="TargetIdentifierChildren",
                parent_column="parent_hash",
                parent_hash=identifier_hash,
                child_column="child_hash",
                child_hashes=child_hashes,
            )
        return identifier_hash

    def insert_scorer(self, identifier: dict[str, Any]) -> str | None:
        """
        Insert a scorer graph.

        Returns:
            str | None: The stored hash when successful.
        """
        prompt_target = self._child(identifier, "prompt_target", aliases=("chat_target",))
        prompt_target_hash = self.insert_target(prompt_target) if prompt_target else None
        sub_scorers = self._children(identifier, "sub_scorers", aliases=("scorers",))
        child_hashes = [child_hash for child in sub_scorers if (child_hash := self.insert_scorer(child))]
        identifier_hash = self._insert_identifier(
            table="ScorerIdentifiers",
            identifier=identifier,
            promoted=("scorer_type", "score_aggregator"),
            extra={"prompt_target_hash": prompt_target_hash},
        )
        if identifier_hash:
            self._insert_edges(
                table="ScorerIdentifierChildren",
                parent_column="parent_hash",
                parent_hash=identifier_hash,
                child_column="child_hash",
                child_hashes=child_hashes,
            )
        return identifier_hash

    def insert_converter(self, identifier: dict[str, Any]) -> str | None:
        """
        Insert a converter graph.

        Returns:
            str | None: The stored hash when successful.
        """
        converter_target = self._child(identifier, "converter_target")
        sub_converter = self._child(identifier, "sub_converter")
        return self._insert_identifier(
            table="ConverterIdentifiers",
            identifier=identifier,
            promoted=("supported_input_types", "supported_output_types"),
            extra={
                "converter_target_hash": self.insert_target(converter_target) if converter_target else None,
                "sub_converter_hash": self.insert_converter(sub_converter) if sub_converter else None,
            },
        )

    def insert_scenario(self, identifier: dict[str, Any]) -> str | None:
        """
        Insert a scenario graph.

        Returns:
            str | None: The stored hash when successful.
        """
        objective_target = self._child(identifier, "objective_target")
        objective_scorer = self._child(identifier, "objective_scorer")
        return self._insert_identifier(
            table="ScenarioIdentifiers",
            identifier=identifier,
            promoted=("version", "techniques", "datasets"),
            extra={
                "objective_target_hash": self.insert_target(objective_target) if objective_target else None,
                "objective_scorer_hash": self.insert_scorer(objective_scorer) if objective_scorer else None,
            },
        )

    def insert_atomic_attack(self, identifier: dict[str, Any]) -> str | None:
        """
        Insert an atomic attack graph.

        Returns:
            str | None: The stored hash when successful.
        """
        attack_technique = self._child(identifier, "attack_technique")
        seeds = self._children(identifier, "seed_identifiers")
        seed_hashes = [seed_hash for seed in seeds if (seed_hash := self._insert_seed(seed))]
        identifier_hash = self._insert_identifier(
            table="AtomicAttackIdentifiers",
            identifier=identifier,
            extra={
                "attack_technique_identifier_hash": (
                    self._insert_attack_technique(attack_technique) if attack_technique else None
                )
            },
        )
        if identifier_hash:
            self._insert_edges(
                table="AtomicAttackSeedIdentifiers",
                parent_column="atomic_attack_identifier_hash",
                parent_hash=identifier_hash,
                child_column="seed_identifier_hash",
                child_hashes=seed_hashes,
            )
        return identifier_hash

    def _insert_attack_technique(self, identifier: dict[str, Any]) -> str | None:
        attack = self._child(identifier, "attack")
        seeds = self._children(identifier, "technique_seeds")
        seed_hashes = [seed_hash for seed in seeds if (seed_hash := self._insert_seed(seed))]
        identifier_hash = self._insert_identifier(
            table="AttackTechniqueIdentifiers",
            identifier=identifier,
            extra={"attack_identifier_hash": self._insert_attack(attack) if attack else None},
        )
        if identifier_hash:
            self._insert_edges(
                table="AttackTechniqueSeedIdentifiers",
                parent_column="attack_technique_identifier_hash",
                parent_hash=identifier_hash,
                child_column="seed_identifier_hash",
                child_hashes=seed_hashes,
            )
        return identifier_hash

    def _insert_attack(self, identifier: dict[str, Any]) -> str | None:
        objective_target = self._child(identifier, "objective_target")
        adversarial_chat = self._child(identifier, "adversarial_chat")
        objective_scorer = self._child(identifier, "objective_scorer")
        request_hashes = [
            value for item in self._children(identifier, "request_converters") if (value := self.insert_converter(item))
        ]
        response_hashes = [
            value
            for item in self._children(identifier, "response_converters")
            if (value := self.insert_converter(item))
        ]
        identifier_hash = self._insert_identifier(
            table="AttackIdentifiers",
            identifier=identifier,
            promoted=("adversarial_system_prompt", "adversarial_seed_prompt"),
            extra={
                "objective_target_hash": self.insert_target(objective_target) if objective_target else None,
                "adversarial_chat_hash": self.insert_target(adversarial_chat) if adversarial_chat else None,
                "objective_scorer_hash": self.insert_scorer(objective_scorer) if objective_scorer else None,
            },
        )
        if identifier_hash:
            self._insert_edges(
                table="AttackRequestConverterIdentifiers",
                parent_column="attack_identifier_hash",
                parent_hash=identifier_hash,
                child_column="converter_identifier_hash",
                child_hashes=request_hashes,
            )
            self._insert_edges(
                table="AttackResponseConverterIdentifiers",
                parent_column="attack_identifier_hash",
                parent_hash=identifier_hash,
                child_column="converter_identifier_hash",
                child_hashes=response_hashes,
            )
        return identifier_hash

    def _insert_seed(self, identifier: dict[str, Any]) -> str | None:
        return self._insert_identifier(
            table="SeedIdentifiers",
            identifier=identifier,
            promoted=("value", "value_sha256", "data_type", "dataset_name", "is_general_technique"),
        )

    def _insert_identifier(
        self,
        *,
        table: str,
        identifier: dict[str, Any],
        promoted: Sequence[str] = (),
        extra: dict[str, Any] | None = None,
    ) -> str | None:
        identifier_hash = identifier.get("hash")
        if not isinstance(identifier_hash, str) or len(identifier_hash) != 64 or table not in self._hashes:
            return None
        if identifier_hash in self._hashes[table]:
            return identifier_hash
        values: dict[str, Any] = {
            "hash": identifier_hash,
            "class_name": identifier.get("class_name"),
            "class_module": identifier.get("class_module"),
            "identifier_json": json.dumps(identifier, sort_keys=True),
            "pyrit_version": identifier.get("pyrit_version"),
        }
        values.update({name: self._json_value(identifier.get(name)) for name in promoted})
        values.update(extra or {})
        columns = list(values)
        statement = sa.text(
            f'INSERT INTO "{table}" ({", ".join(columns)}) '
            f'VALUES ({", ".join(f":{column}" for column in columns)})'
        )
        self._bind.execute(statement, values)
        self._hashes[table].add(identifier_hash)
        return identifier_hash

    def _insert_edges(
        self,
        *,
        table: str,
        parent_column: str,
        parent_hash: str,
        child_column: str,
        child_hashes: Sequence[str],
    ) -> None:
        statement = sa.text(
            f'INSERT INTO "{table}" ({parent_column}, position, {child_column}) '
            f'VALUES (:parent_hash, :position, :child_hash)'
        )
        for position, child_hash in enumerate(child_hashes):
            self._bind.execute(
                statement,
                {"parent_hash": parent_hash, "position": position, "child_hash": child_hash},
            )

    @staticmethod
    def _child(
        identifier: dict[str, Any],
        name: str,
        aliases: Sequence[str] = (),
    ) -> dict[str, Any] | None:
        children = identifier.get("children")
        children = children if isinstance(children, dict) else {}
        for key in (name, *aliases):
            value = identifier.get(key, children.get(key))
            if isinstance(value, dict):
                return load_identifier(value)
        return None

    @staticmethod
    def _children(
        identifier: dict[str, Any],
        name: str,
        aliases: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        children = identifier.get("children")
        children = children if isinstance(children, dict) else {}
        for key in (name, *aliases):
            value = identifier.get(key, children.get(key))
            if isinstance(value, list):
                return [child for item in value if (child := load_identifier(item)) is not None]
        return []

    @staticmethod
    def _json_value(value: Any) -> Any:
        return json.dumps(value) if isinstance(value, (list, dict)) else value
