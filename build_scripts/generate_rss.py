# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
import sys
from pathlib import Path

from feedgen.feed import FeedGenerator

BLOG_SOURCE_DIR = Path("doc/blog")
RSS_OUTPUT_DIR = Path("doc/_build/site/blog")


def parse_blog_markdown(filepath: Path) -> tuple[str, str]:
    """Extract title and first paragraph from a blog markdown file.

    Args:
        filepath: Path to the markdown blog file.

    Returns:
        tuple[str, str]: The title and description extracted from the file.
    """
    text = filepath.read_text(encoding="utf-8")
    lines = text.strip().split("\n")

    title = ""
    description = ""

    # Title is the first heading
    for line in lines:
        if line.startswith("# "):
            title = line.lstrip("# ").strip()
            break

    # Description is the first non-empty paragraph after the date line
    in_description = False
    desc_lines = []
    for line in lines[2:]:
        stripped = line.strip()
        if not stripped:
            if in_description and desc_lines:
                break
            continue
        if stripped.startswith(("<small>", "#")):
            continue
        in_description = True
        desc_lines.append(stripped)

    description = " ".join(desc_lines)
    return title, description


def extract_date_from_filename(filename: str) -> str:
    """Extract publication date from blog filename (e.g. 2024_12_3.md -> 2024-12-03).

    Args:
        filename: The blog filename.

    Returns:
        str: ISO date string.
    """
    match = re.match(r"(\d{4})_(\d{1,2})_(\d{1,2})", filename)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


# Generate the RSS feed structure
print("Generating RSS feed structure...")
fg = FeedGenerator()
fg.link(href="https://microsoft.github.io/PyRIT/blog/rss.xml", rel="self")
fg.title("PyRIT Blog")
fg.description("PyRIT Blog")
fg.logo("https://microsoft.github.io/PyRIT/_static/roakey.png")
fg.language("en")

# Iterate over the blog source markdown files
print("Pulling blog files...")
if not BLOG_SOURCE_DIR.exists():
    print(f"Error: Blog source directory {BLOG_SOURCE_DIR} not found. Exiting.")
    sys.exit(1)

files = [f for f in BLOG_SOURCE_DIR.iterdir() if f.is_file() and f.name.startswith("20") and f.suffix == ".md"]
if len(files) == 0:
    print("Error: No blog files found. Exiting.")
    sys.exit(1)
files.sort(key=lambda x: x.name)

# Add a feed entry for each file
for file in files:
    print(f"Parsing {file.name}...")
    fe = fg.add_entry()
    # Blog pages are served at blog/<filename_without_ext>
    page_name = file.stem
    fe.link(href=f"https://microsoft.github.io/PyRIT/blog/{page_name}")
    fe.guid(f"https://microsoft.github.io/PyRIT/blog/{page_name}")

    title, description = parse_blog_markdown(file)
    fe.title(title)
    fe.description(description)

    pub_date = extract_date_from_filename(file.name)
    if pub_date:
        fe.pubDate(f"{pub_date}T10:00:00Z")

# Validating the RSS feed
print("Validating RSS feed...")
first_entry = fg.entry()[-1]
if first_entry.title() != "Multi-Turn orchestrators":
    print(f"Error: Title parsing failed. Got: {first_entry.title()!r}. Exiting.")
    sys.exit(1)
expected_desc_start = "In PyRIT, orchestrators are typically seen as the top-level component."
if not first_entry.description().startswith(expected_desc_start):
    print(f"Error: Description parsing failed. Got: {first_entry.description()[:80]!r}. Exiting.")
    sys.exit(1)

# Export the RSS feed
print("Exporting RSS feed...")
RSS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
rss_path = RSS_OUTPUT_DIR / "rss.xml"
fg.rss_file(str(rss_path), pretty=True)
if not rss_path.exists() or rss_path.stat().st_size == 0:
    print("Error: RSS feed export failed. Exiting.")
    sys.exit(1)

print("RSS feed generated and exported successfully.")
