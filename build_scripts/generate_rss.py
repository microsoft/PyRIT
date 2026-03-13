# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sys
from html.parser import HTMLParser
from pathlib import Path

from feedgen.feed import FeedGenerator


# HTML parser to extract title and description
class BlogEntryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.description = ""
        self.current_tag = ""
        self.description_step = 0

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self.description_step += 1
        self.current_tag = tag

    def handle_endtag(self, tag):
        if tag == "p":
            self.description_step += 1
        self.current_tag = ""

    def handle_data(self, data):
        if self.current_tag == "title":
            self.title = data
        elif self.description_step == 3:
            self.description = self.description + data


# Generate the RSS feed structure
print("Generating RSS feed structure...")
fg = FeedGenerator()
fg.link(href="https://azure.github.io/PyRIT/blog/rss.xml", rel="self")
fg.title("PyRIT Blog")
fg.description("PyRIT Blog")
fg.logo("https://azure.github.io/PyRIT/_static/roakey.png")
fg.language("en")

# Iterate over the blog files and sort them
print("Pulling blog files...")
directory = Path("doc/_build/html/blog/")
files = [file for file in directory.iterdir() if file.is_file() and file.name.startswith("20")]
if len(files) == 0:
    print("Error: No blog files found. Exiting.")
    sys.exit(1)
files.sort(key=lambda x: x.name)

# Add a feed entry for each file
for file in files:
    print(f"Parsing {file.name}...")
    fe = fg.add_entry()
    fe.link(href=f"https://azure.github.io/PyRIT/blog/{file.name}")
    fe.guid(f"https://azure.github.io/PyRIT/blog/{file.name}")

    # Extract title and description from HTML content
    with open(file, encoding="utf-8") as f:
        parser = BlogEntryParser()
        parser.feed(f.read())
        fe.title(parser.title)
        fe.description(parser.description)

    # Extract publication date from file name
    fe.pubDate(f"{file.name[:10].replace('_', '-')}T10:00:00Z")

# Validating the RSS feed
print("Validating RSS feed...")
first_entry = fg.entry()[-1]
if first_entry.title() != "Multi-Turn orchestrators — PyRIT Documentation":
    print("Error: Title parsing failed. Exiting.")
    sys.exit(1)
if first_entry.description() != (
    "In PyRIT, orchestrators are typically seen as the top-level component. "
    "This is where your attack logic is implemented, while notebooks should "
    "primarily be used to configure orchestrators."
):
    print("Error: Description parsing failed. Exiting.")
    sys.exit(1)

# Export the RSS feed
print("Exporting RSS feed...")
fg.rss_file("doc/_build/html/blog/rss.xml", pretty=True)
file = Path("doc/_build/html/blog/rss.xml")
if not file.exists() or file.stat().st_size == 0:
    print("Error: RSS feed export failed. Exiting.")
    sys.exit(1)

print("RSS feed generated and exported successfully.")
