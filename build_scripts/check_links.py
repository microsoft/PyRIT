# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit, urlunsplit

import requests

skipped_urls = [
    "https://cognitiveservices.azure.com/.default",
    "https://gandalf.lakera.ai/api/send-message",
    "https://code.visualstudio.com/Download",  # This will block python requests
    "https://platform.openai.com/docs/api-reference/introduction",  # blocks python requests
    "https://platform.openai.com/docs/api-reference/responses",  # blocks python requests
    "https://platform.openai.com/docs/guides/function-calling",  # blocks python requests
    "https://platform.openai.com/docs/guides/structured-outputs",  # blocks python requests
    "https://www.anthropic.com/research/many-shot-jailbreaking",  # blocks python requests
    "https://code.visualstudio.com/docs/devcontainers/containers",
    "https://stackoverflow.com/questions/77134272/pip-install-dev-with-pyproject-toml-not-working",
    "https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers",
]

custom_myst_references = ["notebook_tests"]

# Updated regex pattern to capture URLs from Markdown and HTML
URL_PATTERN = re.compile(r'\[.*?\]\((.*?)\)|href="([^"]+)"|src="([^"]+)"')

# Pattern to capture :link: directives from MyST grid-item-cards
GRID_LINK_PATTERN = re.compile(r"^:link:\s+(.+)$", re.MULTILINE)


def extract_urls(file_path):
    with open(file_path, encoding="utf-8") as file:
        content = file.read()
    matches = URL_PATTERN.findall(content)
    # Flatten the list of tuples and filter out empty strings
    urls = [strip_fragment(url) for match in matches for url in match if url]

    # Extract :link: directives from MyST grid-item-cards
    grid_links = GRID_LINK_PATTERN.findall(content)
    urls.extend(grid_links)

    return urls


def strip_fragment(url):
    """
    Removes the fragment (#...) from the URL, so the base URL can be checked.
    """
    parsed_url = urlsplit(url)
    return urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, parsed_url.query, ""))


def resolve_relative_url(base_path, url):
    if not url.startswith(("http://", "https://", "mailto:", "attachment:")):
        # Handle MyST doc references (e.g., setup/1b_install_docker)
        # These can be .md, .rst, or directory paths
        abs_path = os.path.abspath(os.path.join(os.path.dirname(base_path), url))

        # Check various possible file extensions for doc links
        if not os.path.exists(abs_path):
            for ext in [".md", ".ipynb"]:
                if os.path.exists(abs_path + ext):
                    return abs_path + ext

        return abs_path
    return url


def check_url(url, retries=2, delay=2):
    """
    Check the validity of a URL, with retries if it fails.

    Args:
        url (str): URL to check.
        retries (int, optional): Number of retries if the URL check fails. Defaults to 2.
        delay (int, optional): Delay in seconds between retries. Defaults to 2.
    Returns:
        tuple: A tuple containing the URL and a boolean indicating whether it is valid.
    """

    if (
        "http://localhost:" in url
        or url in skipped_urls
        or any(url.endswith(reference) for reference in custom_myst_references)
        or os.path.isfile(url)
        or os.path.isdir(url)
        or url.startswith(("mailto:", "attachment:"))
    ):
        return url, True

    # If it's not an HTTP URL at this point, it's likely a broken local file reference
    if not url.startswith(("http://", "https://")):
        return url, False

    attempts = 0
    while attempts <= retries:
        try:
            response = requests.head(url, allow_redirects=True, timeout=5)
            if response.status_code >= 400:
                attempts += 1
                if attempts > retries:
                    return url, False
                time.sleep(delay)
            else:
                return url, True
        except requests.RequestException:
            attempts += 1
            if attempts > retries:
                return url, False
            time.sleep(delay)

    # If we exit the loop without returning, the URL is broken
    return url, False


def extract_all_urls_from_files(files):
    """
    Extract all URLs from all files, returning a dict of {file_path: [urls]}.
    """
    file_urls = {}
    skipped_files = ["doc/blog/"]

    for file_path in files:
        if any(file_path.startswith(skipped) for skipped in skipped_files):
            continue
        urls = extract_urls(file_path)
        resolved_urls = [resolve_relative_url(file_path, url) for url in urls]
        if resolved_urls:
            file_urls[file_path] = resolved_urls

    return file_urls


def check_all_links_parallel(file_urls, max_workers=20):
    """
    Check all URLs across all files in parallel with a shared thread pool.

    Args:
        file_urls: Dict of {file_path: [urls]}
        max_workers: Max concurrent HTTP requests across ALL files

    Returns:
        Dict of {file_path: [broken_urls]}
    """
    all_broken_urls = {}

    # Create a mapping of url -> file_path for tracking which file each URL came from
    url_to_files = {}
    for file_path, urls in file_urls.items():
        for url in urls:
            if url not in url_to_files:
                url_to_files[url] = []
            url_to_files[url].append(file_path)

    # Check all unique URLs in parallel
    url_results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_url, url): url for url in url_to_files}
        for future in as_completed(futures):
            url = futures[future]
            _, is_valid = future.result()
            url_results[url] = is_valid

    # Map broken URLs back to their files
    for url, is_valid in url_results.items():
        if not is_valid:
            for file_path in url_to_files[url]:
                if file_path not in all_broken_urls:
                    all_broken_urls[file_path] = []
                all_broken_urls[file_path].append(url)

    return all_broken_urls


if __name__ == "__main__":
    files = sys.argv[1:]

    print(f"Extracting URLs from {len(files)} file(s)...")
    file_urls = extract_all_urls_from_files(files)

    if not file_urls:
        print("No URLs found to check.")
        sys.exit(0)

    total_urls = sum(len(urls) for urls in file_urls.values())
    unique_urls = len({url for urls in file_urls.values() for url in urls})
    print(f"Checking {unique_urls} unique URL(s) across {len(file_urls)} file(s) (total: {total_urls})...")

    all_broken_urls = check_all_links_parallel(file_urls, max_workers=30)

    if all_broken_urls:
        print("\n" + "=" * 80)
        for file_path, urls in all_broken_urls.items():
            print(f"Broken links in {file_path}:")
            for url in urls:
                print(f"  - {url}")
        print("=" * 80)
        sys.exit(1)
    else:
        print("No broken links found.")
