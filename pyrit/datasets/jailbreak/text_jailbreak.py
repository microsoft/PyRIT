# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import random
import threading
from pathlib import Path
from typing import Any, Optional

from pyrit.common.path import JAILBREAK_TEMPLATES_PATH
from pyrit.models import SeedPrompt

logger = logging.getLogger(__name__)


class TextJailBreak:
    """
    A class that manages jailbreak datasets (like DAN, etc.).
    """

    _template_cache: Optional[dict[str, list[Path]]] = None
    _cache_lock: threading.Lock = threading.Lock()

    @classmethod
    def _scan_template_files(cls) -> dict[str, list[Path]]:
        """
        Scan the jailbreak templates directory for YAML files.

        Excludes files under the multi_parameter subdirectory. Groups results
        by filename so duplicate names across subdirectories are detectable.

        Returns:
            Dict[str, List[Path]]: Mapping of filename to list of matching paths.
        """
        result: dict[str, list[Path]] = {}
        for path in JAILBREAK_TEMPLATES_PATH.rglob("*.yaml"):
            if "multi_parameter" not in path.parts:
                result.setdefault(path.name, []).append(path)
        return result

    @classmethod
    def _get_template_cache(cls) -> dict[str, list[Path]]:
        """
        Return the cached filename-to-path lookup, building it on first access.

        Thread-safe: uses a lock to prevent concurrent scans from racing.

        Returns:
            Dict[str, List[Path]]: Cached mapping of filename to list of matching paths.
        """
        if cls._template_cache is None:
            with cls._cache_lock:
                # Double-checked locking: re-test after acquiring the lock
                if cls._template_cache is None:
                    cls._template_cache = cls._scan_template_files()
        return cls._template_cache

    @classmethod
    def _resolve_template_by_name(cls, template_file_name: str) -> Path:
        """
        Look up a single template file by name from the cached directory scan.

        Args:
            template_file_name (str): Exact filename (e.g. "aim.yaml") to find.

        Returns:
            Path: The resolved path to the template file.

        Raises:
            ValueError: If the file is not found or if multiple files share the same name.
        """
        cache = cls._get_template_cache()
        paths = cache.get(template_file_name)
        if not paths:
            raise ValueError(
                f"Template file '{template_file_name}' not found in jailbreak directory or its subdirectories"
            )
        if len(paths) > 1:
            raise ValueError(f"Multiple files named '{template_file_name}' found in jailbreak directory")
        return paths[0]

    @classmethod
    def _get_all_template_paths(cls) -> list[Path]:
        """
        Return a flat list of all cached template file paths.

        Returns:
            List[Path]: All template paths (excluding multi_parameter), in no particular order.

        Raises:
            ValueError: If no templates are available.
        """
        cache = cls._get_template_cache()
        all_paths = [path for paths in cache.values() for path in paths]
        if not all_paths:
            raise ValueError("No YAML templates found in jailbreak directory (excluding multi_parameter subdirectory)")
        return all_paths

    def __init__(
        self,
        *,
        template_path: Optional[str] = None,
        template_file_name: Optional[str] = None,
        string_template: Optional[str] = None,
        random_template: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Initialize a Jailbreak instance with exactly one template source.

        Args:
            template_path (str, optional): Full path to a YAML template file.
            template_file_name (str, optional): Name of a template file in datasets/jailbreak directory.
            string_template (str, optional): A string template to use directly.
            random_template (bool, optional): Whether to use a random template from datasets/jailbreak.
            **kwargs: Additional parameters to apply to the template. The 'prompt' parameter will be preserved
                     for later use in get_jailbreak().

        Raises:
            ValueError: If more than one template source is provided or if no template source is provided.
        """
        # Track the template source for error reporting
        self.template_source: str = "<unknown>"
        # Count how many template sources are provided
        template_sources = [template_path, template_file_name, string_template, random_template]
        provided_sources = [source for source in template_sources if source]

        if len(provided_sources) != 1:
            raise ValueError(
                "Exactly one of template_path, template_file_name, string_template, or random_template must be provided"
            )

        if template_path:
            self.template = SeedPrompt.from_yaml_file(template_path)
            self.template_source = str(template_path)
        elif string_template:
            self.template = SeedPrompt(value=string_template, is_general_technique=True, is_jinja_template=True)
            self.template_source = "<string_template>"
        elif template_file_name:
            resolved_path = self._resolve_template_by_name(template_file_name)
            self.template = SeedPrompt.from_yaml_file(resolved_path)
            self.template_source = str(resolved_path)
        else:
            self._load_random_template()

        self._validate_required_kwargs(kwargs)
        self._apply_extra_kwargs(kwargs)

    def _load_random_template(self) -> None:
        """
        Select and load a random single-parameter jailbreak template.

        Picks templates at random until one with exactly a ``prompt`` parameter
        is found, then validates it can render successfully.

        Raises:
            ValueError: If no templates are available, no single-parameter template is found
                after exhausting all candidates, or the chosen template has syntax errors.
        """
        all_paths = self._get_all_template_paths()
        random.shuffle(all_paths)

        for candidate_path in all_paths:
            self.template = SeedPrompt.from_yaml_file(candidate_path)

            if self.template.parameters == ["prompt"]:
                self.template_source = str(candidate_path)
                try:
                    self.template.render_template_value(prompt="test")
                    return
                except ValueError as e:
                    raise ValueError(f"Invalid jailbreak template '{candidate_path}': {str(e)}") from e

        raise ValueError("No jailbreak template with a single 'prompt' parameter found among available templates.")

    def _validate_required_kwargs(self, kwargs: dict[str, Any]) -> None:
        """
        Verify that all template parameters (except 'prompt') are present in kwargs.

        Args:
            kwargs (dict): Keyword arguments supplied by the caller.

        Raises:
            ValueError: If any required template parameter is missing from kwargs.
        """
        parameters = self.template.parameters
        if parameters is None:
            logger.warning("Template '%s' has no parameter metadata defined.", self.template_source)
            parameters = []
        required_params = [p for p in parameters if p != "prompt"]
        missing_params = [p for p in required_params if p not in kwargs]
        if missing_params:
            raise ValueError(
                f"Template requires parameters that were not provided: {missing_params}. "
                f"Required parameters (excluding 'prompt'): {required_params}"
            )

    def _apply_extra_kwargs(self, kwargs: dict[str, Any]) -> None:
        """
        Apply additional keyword arguments to the template, preserving the prompt placeholder.

        Args:
            kwargs (dict): Keyword arguments whose values are rendered into the template.
                The 'prompt' key is skipped so it remains available for later rendering.
        """
        if kwargs:
            kwargs.pop("prompt", None)
            self.template.value = self.template.render_template_value_silent(**kwargs)

    @classmethod
    def get_jailbreak_templates(cls, num_templates: Optional[int] = None) -> list[str]:
        """
        Retrieve all jailbreaks from the JAILBREAK_TEMPLATES_PATH.

        Args:
            num_templates (int, optional): Number of jailbreak templates to return. None to get all.

        Returns:
            List[str]: List of jailbreak template file names.

        Raises:
            ValueError: If no jailbreak templates are found in the jailbreak directory.
            ValueError: If n is larger than the number of templates that exist.
        """
        jailbreak_template_names = [str(f.stem) + ".yaml" for f in JAILBREAK_TEMPLATES_PATH.glob("*.yaml")]
        if not jailbreak_template_names:
            raise ValueError("No jailbreak templates found in the jailbreak directory")

        if num_templates:
            if num_templates > len(jailbreak_template_names):
                raise ValueError(
                    f"Attempted to pull {num_templates} jailbreaks from a dataset"
                    f" with only {len(jailbreak_template_names)} jailbreaks!"
                )
            jailbreak_template_names = random.choices(jailbreak_template_names, k=num_templates)
        return jailbreak_template_names

    def get_jailbreak_system_prompt(self) -> str:
        """
        Get the jailbreak template as a system prompt without a specific user prompt.

        Returns:
            str: The rendered jailbreak template with an empty prompt parameter.
        """
        return self.get_jailbreak(prompt="")

    def get_jailbreak(self, prompt: str) -> str:
        """
        Render the jailbreak template with the provided user prompt.

        Args:
            prompt (str): The user prompt to insert into the jailbreak template.

        Returns:
            str: The rendered jailbreak template with the prompt parameter filled in.

        Raises:
            ValueError: If the template fails to render.
        """
        return self.template.render_template_value(prompt=prompt)
