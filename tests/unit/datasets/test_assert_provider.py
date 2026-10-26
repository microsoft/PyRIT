# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from unit.assert_mocks import make_assert_config, make_assert_result
from unit.mocks import MockPromptTarget

from pyrit.datasets import AssertProvider, SeedDatasetProvider, TargetObjectiveProvider
from pyrit.datasets.seed_datasets import generated
from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.promptgen import AssertGenerator, AssertGeneratorResult
from pyrit.memory import MemoryInterface
from pyrit.models import AssertGroupMetadata, SeedOrigin, group_seeds_into_attack_groups
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource


@pytest.mark.usefixtures("patch_central_database")
class TestAssertProvider:
    @pytest.mark.parametrize("instructions", [("First role.", "Second role."), ("First role.", None), (None, "Role.")])
    @pytest.mark.parametrize("same_user_prompt", [True, False])
    async def test_separate_writes_retain_complete_behavioral_variants_async(
        self,
        *,
        sqlite_instance: MemoryInterface,
        instructions: tuple[str | None, str | None],
        same_user_prompt: bool,
    ) -> None:
        datasets = []
        expected_groups = []
        for index, role in enumerate(instructions):
            result = make_assert_result(
                config=make_assert_config(group_count=1, include_adversarial_prompt=role is not None),
                duplicate_text=True,
                system_prompt="Use the published support policy.",
            )
            group = result.seed_groups[0]
            if role is not None:
                group.adversarial_prompts.system_prompt.value += f"\n{role}"
                group.objective.metadata.update(group.adversarial_prompts.to_metadata())
            if not same_user_prompt:
                next(prompt for prompt in group.prompts if prompt.role == "user").value += f" Item {index}."
            dataset = await AssertProvider(dataset_name="assert_variants", result=result).fetch_dataset_async()
            datasets.append(dataset)
            expected_groups.extend(group_seeds_into_attack_groups(dataset.seeds))
            await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="operator")

        stored = await sqlite_instance.get_seeds_async(dataset_name="assert_variants")
        groups = group_seeds_into_attack_groups(stored)
        assert len(groups) == 2
        assert len(stored) == 6
        expected_by_id = {group.logical_id: group for group in expected_groups}
        assert {group.logical_id for group in groups} == expected_by_id.keys()
        for group in groups:
            expected = expected_by_id[group.logical_id]
            assert {seed.id for seed in group.seeds} == {seed.id for seed in expected.seeds}
            assert group.objective.metadata == expected.objective.metadata
            assert group.adversarial_prompts == expected.adversarial_prompts
            assert [(prompt.role, prompt.value) for prompt in group.prompts] == [
                (prompt.role, prompt.value) for prompt in expected.prompts
            ]
        await sqlite_instance.add_seed_datasets_to_memory_async(datasets=datasets, added_by="operator")
        assert len(await sqlite_instance.get_seeds_async(dataset_name="assert_variants")) == 6

    async def test_storage_deduplicates_identical_inputs_with_fresh_seed_ids_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        for _ in range(2):
            result = make_assert_result(config=make_assert_config(group_count=1))
            dataset = await AssertProvider(dataset_name="identical", result=result).fetch_dataset_async()
            await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="operator")

        stored = await sqlite_instance.get_seeds_async(dataset_name="identical")
        assert len(stored) == 2
        assert len(group_seeds_into_attack_groups(stored)) == 1

    async def test_storage_preserves_group_associations_and_duplicate_objectives_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        result = make_assert_result(duplicate_text=True, system_prompt="Use the support policy.")
        provider = AssertProvider(dataset_name="assert_duplicates", result=result)
        dataset = await provider.fetch_dataset_async()
        original_groups = group_seeds_into_attack_groups(dataset.seeds)
        assert not await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name)
        await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="operator")
        stored = await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name)
        groups = group_seeds_into_attack_groups(stored)
        assert len(groups) == 6
        assert all(seed.origin == SeedOrigin.GENERATED and seed.added_by == "operator" for seed in stored)
        assert len({group.objective.value for group in groups}) == 1
        assert {group.logical_id for group in groups} == {group.logical_id for group in original_groups}
        saved = [AssertGroupMetadata.from_metadata(group.objective.metadata) for group in groups]
        assert {item.group_id for item in saved} == {f"group_{index}" for index in range(6)}
        assert all(item.taxonomy == result.taxonomy and item.config == result.config for item in saved)
        assert all(group.adversarial_prompts.system_prompt.response_json_schema for group in groups)
        assert all(not seed.is_jinja_template and seed.data_type == "text" for seed in stored)
        assert all(seed.authors == ["Microsoft Responsible AI"] for seed in stored)
        for group in groups:
            assert [prompt.value for prompt in group.prompts if prompt.role == "system"] == ["Use the support policy."]
            assert [prompt.value for prompt in group.prompts if prompt.role == "user"] == [
                "Please explain the published return policy."
            ]
            assert group.adversarial_prompts.system_prompt not in group.prompts

    async def test_prepare_reuses_existing_rows_despite_changed_provider_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        provider = AssertProvider(dataset_name="assert_reuse", result=make_assert_result())
        configuration = DatasetAttackConfiguration(
            sources=[DatasetSource(name=provider.dataset_name, provider=provider)]
        )
        await configuration.prepare_async()
        original = await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name)
        replacement = AssertProvider(
            dataset_name=provider.dataset_name, result=make_assert_result(config=make_assert_config(group_count=1))
        )
        next_config = DatasetAttackConfiguration(
            sources=[DatasetSource(name=replacement.dataset_name, provider=replacement)],
            max_per_dataset="all",
            max_total="all",
        )
        with patch.object(replacement, "fetch_dataset_async", wraps=replacement.fetch_dataset_async) as fetch:
            await next_config.prepare_async()
        fetch.assert_not_called()
        stored = await sqlite_instance.get_seeds_async(dataset_name=replacement.dataset_name)
        assert {seed.id for seed in stored} == {seed.id for seed in original}

    @pytest.mark.parametrize("value", ["{{ 7 * 7 }} {% invalid %}", "image.png"])
    async def test_generated_user_input_is_literal_text_async(self, value: str, tmp_path: Path) -> None:
        if value == "image.png":
            path = tmp_path / value
            await asyncio.to_thread(path.write_bytes, b"fixture")
            value = str(path)
        raw = make_assert_result().model_dump(mode="json")
        raw["seed_groups"][0]["seeds"][1]["value"] = value
        dataset = await AssertProvider(
            dataset_name="literal", result=AssertGeneratorResult.model_validate(raw)
        ).fetch_dataset_async()
        literal = [seed for seed in dataset.seeds if seed.value == value]
        assert len(literal) == 1
        assert literal[0].data_type == "text"
        assert not literal[0].is_jinja_template

    async def test_metadata_is_bounded_and_scales_linearly_async(self) -> None:
        sizes = []
        for count in (100, 1000):
            result = make_assert_result(config=make_assert_config(group_count=count))
            dataset = await AssertProvider(dataset_name="size", result=result).fetch_dataset_async()
            sizes.append(sum(len(json.dumps(seed.metadata or {}).encode("utf-8")) for seed in dataset.seeds))
            for group in group_seeds_into_attack_groups(dataset.seeds):
                metadata = AssertGroupMetadata.from_metadata(group.objective.metadata)
                assert len(metadata.provenance.conversation_ids) == 3
                assert not any(prompt.metadata for prompt in group.prompts)
                assert set(metadata.model_dump()) == {
                    "group_id",
                    "behavior_category",
                    "dimension_values",
                    "config",
                    "taxonomy",
                    "dimensions",
                    "provenance",
                    "source_revision",
                }
                assert len(json.dumps(group.objective.metadata).encode("utf-8")) < 10_000
        assert 9.5 < sizes[1] / sizes[0] < 10.5

    @pytest.mark.parametrize("include_user", [True, False])
    async def test_prompt_sending_never_sends_adversarial_instructions_async(self, include_user: bool) -> None:
        result = make_assert_result(config=make_assert_config(group_count=1, include_user_prompt=include_user))
        group = result.seed_groups[0]
        target = MockPromptTarget()
        await PromptSendingAttack(objective_target=target).execute_async(
            objective=group.objective.value,
            next_message=group.next_message,
            prepended_conversation=group.prepended_conversation,
        )
        assert target.prompt_sent == [group.prompts[0].value if include_user else group.objective.value]
        assert group.adversarial_prompts.system_prompt.value not in target.prompt_sent

    async def test_incomplete_generation_is_rejected_before_storage_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        result = make_assert_result()
        incomplete = result.model_copy(update={"seed_groups": result.seed_groups[:-1]})
        generator = MagicMock(spec=AssertGenerator)
        generator.execute_async = AsyncMock(return_value=incomplete)
        provider = AssertProvider(dataset_name="failed", generator=generator, config=result.config)
        configuration = DatasetAttackConfiguration(
            sources=[DatasetSource(name=provider.dataset_name, provider=provider)]
        )
        with pytest.raises(ValidationError, match="Requested 6 seed groups, received 5"):
            await configuration.prepare_async()
        assert not await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name)

    async def test_provider_rejects_a_different_request_async(self) -> None:
        generator = MagicMock(spec=AssertGenerator)
        generator.execute_async = AsyncMock(return_value=make_assert_result())
        provider = AssertProvider(
            dataset_name="different", generator=generator, config=make_assert_config(group_count=1)
        )
        with pytest.raises(ValueError, match="does not match"):
            await provider.fetch_dataset_async()

    async def test_discovery_excludes_generated_providers_async(self) -> None:
        with (
            patch.object(AssertProvider, "fetch_dataset_async", new_callable=AsyncMock) as assert_fetch,
            patch.object(TargetObjectiveProvider, "fetch_dataset_async", new_callable=AsyncMock) as objective_fetch,
        ):
            providers = SeedDatasetProvider.get_all_providers()
            names = await SeedDatasetProvider.get_all_dataset_names_async()
        assert AssertProvider not in providers.values()
        assert TargetObjectiveProvider not in providers.values()
        assert names
        assert_fetch.assert_not_called()
        objective_fetch.assert_not_called()

    def test_generated_package_exports_preserve_public_provider_identity(self) -> None:
        assert generated.AssertProvider is AssertProvider
        assert generated.TargetObjectiveProvider is TargetObjectiveProvider

    @pytest.mark.parametrize("name", ["", " ", None, 1])
    def test_provider_rejects_invalid_names(self, name: str | None | int) -> None:
        with pytest.raises(ValueError, match="dataset_name"):
            AssertProvider(dataset_name=name, result=make_assert_result())

    def test_provider_rejects_an_incomplete_supplied_result(self) -> None:
        result = make_assert_result()
        incomplete = result.model_copy(update={"seed_groups": result.seed_groups[:-1]})
        with pytest.raises(ValidationError, match="Requested 6 seed groups, received 5"):
            AssertProvider(dataset_name="incomplete", result=incomplete)

    def test_provider_rejects_missing_or_conflicting_sources(self) -> None:
        with pytest.raises(ValueError, match="both generator and config"):
            AssertProvider(dataset_name="missing", config=make_assert_config())
        with pytest.raises(ValueError, match="not both"):
            AssertProvider(dataset_name="conflicting", result=make_assert_result(), config=make_assert_config())
