# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
import sys

# Matches the kernelspec block inside a jupytext YAML header comment.
# The block starts with "#   kernelspec:" and includes all following
# lines that are indented deeper (i.e. start with "#     ").
_KERNELSPEC_PATTERN = re.compile(
    r"^#   kernelspec:\n(?:#     .+\n)+",
    re.MULTILINE,
)


def strip_kernelspec(file_path: str) -> bool:
    """
    Remove the kernelspec block from a jupytext .py file's YAML header.

    Args:
        file_path (str): Path to the .py file.

    Returns:
        bool: True if the file was modified.
    """
    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    new_content = _KERNELSPEC_PATTERN.sub("", content)

    if new_content == content:
        return False

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return True


if __name__ == "__main__":
    modified_files = [fp for fp in sys.argv[1:] if strip_kernelspec(fp)]
    if modified_files:
        print("Stripped kernelspec from:", modified_files)
        sys.exit(1)
