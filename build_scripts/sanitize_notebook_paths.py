# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import re
import sys
from re import Match

# Windows path: captures user prefix AND remaining path for normalization
_WINDOWS_PATH_PATTERN = re.compile(
    r"[A-Za-z]:\\+Users\\+[^\\]+\\+((?:[^\\\s\"',:;]+\\+)*[^\\\s\"',:;]*)",
    re.IGNORECASE,
)
# Unix paths: just match the prefix
_UNIX_PATH_PATTERNS = [
    re.compile(r"/Users/[^/]+/"),  # macOS
    re.compile(r"/home/[^/]+/"),  # Linux
]


def _windows_path_replacer(match: Match[str]) -> str:
    """Replace Windows user path prefix with ./ and normalize backslashes to forward slashes."""
    remainder = match.group(1)
    normalized = remainder.replace("\\", "/")
    # Collapse multiple forward slashes from double-backslash paths
    return "./" + re.sub(r"/+", "/", normalized)


def sanitize_notebook_paths(file_path: str) -> bool:
    """
    Remove user-specific path prefixes from notebook cell outputs.

    Replaces paths like C:\\Users\\username\\project\\file.py with ./project/file.py.

    Args:
        file_path (str): Path to the .ipynb file.

    Returns:
        bool: True if the file was modified.
    """
    if not file_path.endswith(".ipynb"):
        return False

    with open(file_path, encoding="utf-8") as f:
        content = json.load(f)

    modified = False

    for cell in content.get("cells", []):
        for output in cell.get("outputs", []):
            modified = _sanitize_output_field(output, "text") or modified
            modified = _sanitize_output_field(output, "traceback") or modified
            modified = _sanitize_output_field(output, "evalue") or modified
            if "data" in output:
                for mime_type in output["data"]:
                    if mime_type.startswith("text/"):
                        modified = _sanitize_output_field(output["data"], mime_type) or modified

    if not modified:
        return False

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=1, ensure_ascii=False)
        f.write("\n")

    return True


def _sanitize_output_field(obj: dict, key: str) -> bool:
    """
    Sanitize a single output field by replacing user path prefixes with ./ normalized paths.

    Args:
        obj (dict): The dict containing the field.
        key (str): The key to sanitize.

    Returns:
        bool: True if the field was modified.
    """
    value = obj.get(key)
    if value is None:
        return False

    modified = False

    if isinstance(value, list):
        new_list = []
        for line in value:
            if isinstance(line, str):
                sanitized = _strip_user_paths(line)
                if sanitized != line:
                    modified = True
                new_list.append(sanitized)
            else:
                new_list.append(line)
        obj[key] = new_list
    elif isinstance(value, str):
        sanitized = _strip_user_paths(value)
        if sanitized != value:
            modified = True
        obj[key] = sanitized

    return modified


def _strip_user_paths(text: str) -> str:
    """
    Replace user-specific path prefixes with ./ and normalize separators.

    Windows paths are normalized to forward slashes. For example,
    C:\\Users\\alice\\project\\file.py becomes ./project/file.py.

    Args:
        text (str): The text to sanitize.

    Returns:
        str: The sanitized text.
    """
    text = _WINDOWS_PATH_PATTERN.sub(_windows_path_replacer, text)
    for pattern in _UNIX_PATH_PATTERNS:
        text = pattern.sub("./", text)
    return text


if __name__ == "__main__":
    modified_files = [file_path for file_path in sys.argv[1:] if sanitize_notebook_paths(file_path)]
    if modified_files:
        print("Sanitized user paths in:", modified_files)
        sys.exit(1)
