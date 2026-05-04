# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import re
import sys

# tqdm text-mode progress bar patterns:
# - "%|" separates percentage from the bar
# - Block characters (━, █, ▏-▉) are used for the bar itself
# - "\r" carriage returns are used for in-place updates
_TQDM_PATTERNS = [
    re.compile(r"%\|"),  # "  0%|" or " 50%|..."
    re.compile(r"[━█▏▎▍▌▋▊▉]"),  # progress bar block characters
]


def _is_tqdm_line(line: str) -> bool:
    """
    Check if a line is part of a tqdm progress bar output.

    Args:
        line (str): A single line of text from stderr output.

    Returns:
        bool: True if the line matches tqdm progress bar patterns.
    """
    stripped = line.strip()
    if not stripped or stripped == "\r":
        # Bare carriage returns or blank lines between tqdm updates
        return False
    return any(pattern.search(line) for pattern in _TQDM_PATTERNS)


def strip_notebook_progress_bars(file_path: str) -> bool:
    """
    Remove tqdm progress bar outputs from notebook cell stderr streams.

    Strips stderr stream outputs that contain tqdm progress bar patterns.
    If all lines in a stderr output are tqdm lines, the entire output is removed.
    If only some lines are tqdm, those lines are stripped and the output is kept.

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
        outputs = cell.get("outputs", [])
        new_outputs = []

        for output in outputs:
            if output.get("output_type") == "stream" and output.get("name") == "stderr":
                text_lines = output.get("text", [])
                non_tqdm_lines = [line for line in text_lines if not _is_tqdm_line(line)]

                if len(non_tqdm_lines) < len(text_lines):
                    modified = True
                    # Keep output only if there are meaningful non-tqdm lines
                    remaining = [line for line in non_tqdm_lines if line.strip()]
                    if remaining:
                        output["text"] = non_tqdm_lines
                        new_outputs.append(output)
                    # else: drop the entire output (all tqdm or only whitespace left)
                else:
                    new_outputs.append(output)
            else:
                new_outputs.append(output)

        if len(new_outputs) != len(outputs):
            cell["outputs"] = new_outputs

    if not modified:
        return False

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=1, ensure_ascii=False)
        f.write("\n")

    return True


if __name__ == "__main__":
    modified_files = [file_path for file_path in sys.argv[1:] if strip_notebook_progress_bars(file_path)]
    if modified_files:
        print("Stripped tqdm progress bars from:", modified_files)
        sys.exit(1)
