# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import sys


def remove_kernelspec_from_ipynb_files(file_path: str):
    modified_files = []

    if file_path.endswith(".ipynb"):
        # Iterate through all .ipynb files in the specified file
        with open(file_path, encoding="utf-8") as f:
            content = json.load(f)
            # Remove the "kernelspec" metadata section if it exists
            if "metadata" in content and "kernelspec" in content["metadata"]:
                modified_files.append(file_path)
                del content["metadata"]["kernelspec"]
                # Save the modified content back to the .ipynb file
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(content, f, indent=1)
        if modified_files:
            print("Modified files:", modified_files)
            sys.exit(1)


if __name__ == "__main__":
    files = sys.argv[1:]
    # # Remove the "kernelspec" section from all .ipynb files in the specified file
    for file in files:
        remove_kernelspec_from_ipynb_files(file)
