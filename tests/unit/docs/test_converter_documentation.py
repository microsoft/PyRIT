# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests to verify all converters are documented in the converter notebooks.
"""

from pathlib import Path

import pytest

from pyrit.prompt_converter.prompt_converter import get_converter_modalities


def get_all_converter_classes():
    """Get all converter class names from the codebase."""
    converters = []
    for name, _, _ in get_converter_modalities():
        converters.append(name)
    return set(converters)


def get_converters_mentioned_in_notebooks():
    """Parse converter notebooks to find which converters are mentioned."""
    # tests/unit/docs -> tests/unit -> tests -> workspace_root
    doc_path = Path(__file__).parent.parent.parent.parent / "doc" / "code" / "converters"

    mentioned_converters = set()

    # Read all converter notebook files
    for notebook_file in doc_path.glob("*.py"):
        if notebook_file.name.startswith("_"):
            continue

        content = notebook_file.read_text(encoding="utf-8")

        # Look for converter imports and class names
        # Pattern 1: from pyrit.prompt_converter import ConverterName
        # Pattern 2: ConverterName() in code
        for line in content.split("\n"):
            # Find imports
            if "from pyrit.prompt_converter import" in line:
                # Extract converter names from import
                import_part = line.split("import")[1]
                for part in import_part.split(","):
                    converter_name = part.strip().strip("()")
                    if converter_name and "Converter" in converter_name:
                        mentioned_converters.add(converter_name)

            # Find direct usage (e.g., Base64Converter())
            elif "Converter(" in line and "from" not in line and "import" not in line:
                # Extract converter class names
                words = line.split()
                for word in words:
                    if "Converter(" in word:
                        converter_name = word.split("(")[0]
                        if converter_name.endswith("Converter"):
                            mentioned_converters.add(converter_name)

    return mentioned_converters


def test_all_converters_are_documented():
    """Test that all converter classes are mentioned in the documentation notebooks."""
    all_converters = get_all_converter_classes()
    documented_converters = get_converters_mentioned_in_notebooks()

    # Find converters that are not documented
    undocumented = all_converters - documented_converters

    # Some converters might be intentionally not documented (abstract base classes, etc.)
    # We can add exceptions here if needed
    exceptions = {
        "PromptConverter",  # Base class
        "LLMGenericTextConverter",  # Base class
        "WordLevelConverter",  # Base class
        "SmugglerConverter",  # Base class (in subdirectory)
        "get_converter_modalities",  # Function, not a converter class
    }

    undocumented = undocumented - exceptions

    if undocumented:
        pytest.fail(
            f"The following converters are not documented in any converter notebook:\n"
            f"{sorted(undocumented)}\n\n"
            f"Please add examples of these converters to the appropriate notebook in doc/code/converters/"
        )


if __name__ == "__main__":
    # For quick testing
    all_converters = get_all_converter_classes()
    documented = get_converters_mentioned_in_notebooks()

    print(f"Total converters: {len(all_converters)}")
    print(f"Documented converters: {len(documented)}")
    print(f"Undocumented: {all_converters - documented}")
