# Find all .py files in the docs directory as this script and convert them to .ipynb
# This excludes the deployment directory

import argparse
import os
import subprocess
from pathlib import Path

skip_files = {
    "conf.py",
    # auxiliary_attacks
    "1_gcg_azure_ml.py",  # missing required env variables
    # converters
    "6_human_converter.py",  # requires human input
    # memory
    "6_azure_sql_memory.py",  # requires Azure SQL setup, remove following completion of #4001
    "7_azure_sql_memory_attacks.py",  # remove following completion of #4001
    "4_non_llm_targets.py",  # requires Azure SQL Storage IO for Azure Storage Account (see #4001)
    "playwright_target.py",  # Playwright installation takes too long
    "playwright_target_copilot.py",  # Playwright installation takes too long, plus requires M365 account
    "app.py",  # Flask app for playwright demo, not a notebook
    # scoring
    "5_human_in_the_loop_scorer.py",  # requires human input
    # executor
    "1_xpia_website.py",  # requires publicly accessible Azure Storage Account
    "2_xpia_ai_recruiter.py",  # requires AI recruiter service running locally
}

# Get the doc directory (parent of generate_docs where this script is located)
script_dir = Path(__file__).parent
doc_dir = script_dir.parent
pyrit_root = doc_dir.parent
file_type = ".py"
included_dirs = {"code", "cookbooks"}
cache_dir = os.path.join(pyrit_root, "dbdata")
kernel_name = "pyrit-dev"


def main():
    parser = argparse.ArgumentParser(description="Converts .py files in docs to .ipynb")
    parser.add_argument("-id", "--run_id", type=str, help="id used to cache processed files")
    parser.add_argument(
        "-kn",
        "--kernel_name",
        default=kernel_name,
        type=str,
        help=f"name of kernel to run notebooks. (default: {kernel_name})",
    )
    args = parser.parse_args()

    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"pct_to_ipynb_{args.run_id}.cache")
    processed_files = set()
    if os.path.isfile(cache_file):
        with open(cache_file) as f:
            for file_path in f:
                processed_files.add(file_path.strip())

    found_files = find_files(doc_dir, file_type)

    for file in found_files:
        if file in processed_files:
            print(f"Skipping already processed file: {file}")
            continue
        if any(skip_file in file for skip_file in skip_files):
            print(f"Skipping configured skipped file: {file}")
            continue
        print(f"Processing {file}")
        result = subprocess.run(
            ["jupytext", "--execute", "--set-kernel", args.kernel_name, "--to", "notebook", file],
            stdout=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            print(f"Error processing {file}")
            continue
        print(f"Successfully processed {file}")
        # Log to cache file
        with open(cache_file, "a") as f:
            f.write(file + "\n")
    return


def find_files(directory, file_extension):
    matches = []
    # Only search in included directories (code and cookbooks)
    for included_dir in included_dirs:
        dir_path = os.path.join(directory, included_dir)
        if not os.path.exists(dir_path):
            continue
        for root, _dirs, files in os.walk(dir_path):
            for file in files:
                if file.endswith("_helpers.py"):
                    continue
                if file.endswith(file_extension):
                    matches.append(os.path.join(root, file))
    return matches


if __name__ == "__main__":
    main()
