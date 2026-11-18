# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Fixed rapid-response suite: light techniques swept across each packaged harm dataset."""

from typing import ClassVar
from uuid import NAMESPACE_URL, uuid5

from pyrit.models import (
    DatasetFetchPolicy,
    DatasetSourceInput,
    ScenarioDatasetRequest,
    ScenarioPreset,
    ScenarioSuite,
    ScenarioSuiteEntry,
)


class RapidResponseSuite:
    """
    A fixed snapshot of per-harm ``technique.sweep`` presets over the packaged harm datasets.

    Each harm is its own preset, so a launch produces one run per harm. Every preset lists its
    techniques explicitly and runs every objective in its dataset with a baseline, so results
    stay comparable across runs and PyRIT releases. Presets name no scorer: the launch's objective
    scorer, or the deployment's default objective scorer, judges every harm.
    """

    NAME: ClassVar[str] = "rapid_response"
    DESCRIPTION: ClassVar[str] = (
        "Runs a fixed set of single- and multi-turn attack techniques against every objective in "
        "each packaged harm dataset, one preset per harm."
    )
    HARMS: ClassVar[tuple[str, ...]] = (
        "hate",
        "fairness",
        "violence",
        "sexual",
        "harassment",
        "misinformation",
        "leakage",
    )

    # The core catalog's light techniques at the time of this snapshot.
    TECHNIQUES: ClassVar[tuple[str, ...]] = (
        "bijection",
        "code_attack",
        "context_compliance",
        "flip",
        "many_shot",
        "red_teaming",
        "role_play_movie_script",
        "role_play_persuasion",
        "role_play_persuasion_written",
        "role_play_trivia_game",
        "role_play_video_game",
    )

    @classmethod
    def build(cls) -> tuple[ScenarioSuite, list[ScenarioPreset]]:
        """
        Build fresh copies of the suite and the presets its entries reference.

        Returns:
            tuple[ScenarioSuite, list[ScenarioPreset]]: The suite and its presets in launch order.
        """
        presets = [cls._build_preset(harm=harm) for harm in cls.HARMS]
        suite = ScenarioSuite(
            name=cls.NAME,
            description=cls.DESCRIPTION,
            entries=[
                ScenarioSuiteEntry(
                    entry_id=uuid5(NAMESPACE_URL, f"pyrit:scenario_suite:{cls.NAME}:{preset.name}"),
                    preset_name=preset.name,
                )
                for preset in presets
            ],
        )
        return suite, presets

    @classmethod
    def _build_preset(cls, *, harm: str) -> ScenarioPreset:
        """
        Build the sweep preset for one harm dataset.

        Args:
            harm (str): The harm, matching the ``airt_<harm>`` dataset name.

        Returns:
            ScenarioPreset: The preset for that harm.
        """
        return ScenarioPreset(
            name=f"{cls.NAME}_{harm}",
            scenario_name="technique.sweep",
            show_in_scanner=False,
            techniques=list(cls.TECHNIQUES),
            datasets=ScenarioDatasetRequest(
                sources=[DatasetSourceInput(name=f"airt_{harm}")],
                fetch=DatasetFetchPolicy.IF_MISSING,
                max_per_dataset="all",
                max_total="all",
            ),
            include_baseline=True,
        )
