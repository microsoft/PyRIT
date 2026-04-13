# Find all .ipynb files in the docs directory as this script and convert them to .py
# This excludes the deployment directory

import os
import subprocess
from pathlib import Path

exec_dir = Path(os.getcwd())
file_type = ".ipynb"
excluded_dir = {"deployment", "generate_docs"}


def main():
    found_files = find_files(exec_dir.parent, file_type)
    for file in found_files:
        print(f"Processing {file}")
        result = subprocess.run(["jupytext", "--to", "py:percent", file], stdout=subprocess.DEVNULL)
        if result.returncode != 0:
            print(f"Error processing {file}")
            continue
        print(f"Successfully processed {file}")
    return


def find_files(directory, file_extension):
    matches = []
    for root, dirs, files in os.walk(directory):
        for dir in excluded_dir:
            if dir in dirs:
                dirs.remove(dir)
        matches.extend(os.path.join(root, file) for file in files if file.endswith(file_extension))
    return matches


if __name__ == "__main__":
    main()
