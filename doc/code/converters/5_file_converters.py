# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
# ---

# %% [markdown]
# # 5. File Converters
#
# File converters transform text into file outputs such as PDFs. These converters are useful for packaging prompts into distributable formats.
#
# ## Overview
#
# This notebook covers:
#
# - **PDFConverter**: Convert text to PDF documents with templates or direct generation
# - **WordDocConverter**: Convert text to Word (.docx) documents with optional placeholder injection

# %% [markdown]
# ## PDFConverter
#
# The `PDFConverter` generates PDF documents from text in multiple modes:
#
# 1. **Template-Based PDF Generation**: Use YAML templates to render dynamic content into PDFs
# 2. **Direct Prompt PDF Generation**: Convert plain text strings into PDFs without templates
# 3. **Modify Existing PDFs**: Inject text into existing PDF documents

# %% [markdown]
# ### Template-Based PDF Generation
#
# This mode populates placeholders in a YAML-based template and converts the rendered content into a PDF.

# %%
import pathlib

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.executor.attack import (
    AttackConverterConfig,
    ConsoleAttackResultPrinter,
    PromptSendingAttack,
)
from pyrit.models import SeedPrompt
from pyrit.prompt_converter import PDFConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration
from pyrit.prompt_target import TextTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

prompt_data = {
    "hiring_manager_name": "Jane Doe",
    "current_role": "AI Engineer",
    "company": "CyberDefense Inc.",
    "red_teaming_reason": "to creatively identify security vulnerabilities while enjoying free coffee",
    "applicant_name": "John Smith",
}

# Load the YAML template for the PDF generation
template_path = pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "pdf_converters" / "red_teaming_application_template.yaml"
if not template_path.exists():
    raise FileNotFoundError(f"Template file not found: {template_path}")

# Load the SeedPrompt from the YAML file
prompt_template = SeedPrompt.from_yaml_file(template_path)

# Initialize target
prompt_target = TextTarget()

# Initialize the PDFConverter
pdf_converter = PromptConverterConfiguration.from_converters(
    converters=[
        PDFConverter(
            prompt_template=prompt_template,
            font_type="Arial",
            font_size=12,
            page_width=210,
            page_height=297,
        )
    ]
)

converter_config = AttackConverterConfig(
    request_converters=pdf_converter,
)

# Define prompt for the attack
prompt = str(prompt_data)

# Initialize the attack
attack = PromptSendingAttack(
    objective_target=prompt_target,
    attack_converter_config=converter_config,
)

result = await attack.execute_async(objective=prompt)  # type: ignore
await ConsoleAttackResultPrinter().print_conversation_async(result=result)  # type: ignore

# %% [markdown]
# ### Direct Prompt PDF Generation (No Template)
#
# This mode converts plain text strings directly into PDFs without using templates.

# %%
# Define a simple string prompt (no templates)
prompt = "This is a simple test string for PDF generation. No templates here!"

# Initialize the PDFConverter without a template
pdf_converter = PromptConverterConfiguration.from_converters(
    converters=[
        PDFConverter(
            prompt_template=None,  # No template provided
            font_type="Arial",
            font_size=12,
            page_width=210,
            page_height=297,
        )
    ]
)

converter_config = AttackConverterConfig(
    request_converters=pdf_converter,
)

# Initialize the attack
attack = PromptSendingAttack(
    objective_target=prompt_target,
    attack_converter_config=converter_config,
)

result = await attack.execute_async(objective=prompt)  # type: ignore
await ConsoleAttackResultPrinter().print_conversation_async(result=result)  # type: ignore

# %% [markdown]
# ### Modifying Existing PDFs with Injection Items
#
# The `PDFConverter` can also inject text into existing PDF documents at specified locations.

# %%
import tempfile
from pathlib import Path

import requests

# Download a sample PDF
url = (
    "https://raw.githubusercontent.com/microsoft/PyRIT/main/pyrit/datasets/prompt_converters/pdf_converters/fake_CV.pdf"
)

with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
    response = requests.get(url)
    tmp_file.write(response.content)

cv_pdf_path = Path(tmp_file.name)

# Define injection items
injection_items = [
    {
        "page": 0,
        "x": 50,
        "y": 700,
        "text": "Injected Text",
        "font_size": 12,
        "font": "Helvetica",
        "font_color": (255, 0, 0),
    },  # Red text
    {
        "page": 1,
        "x": 100,
        "y": 600,
        "text": "Confidential",
        "font_size": 10,
        "font": "Helvetica",
        "font_color": (0, 0, 255),
    },  # Blue text
]

# Initialize the PDFConverter with the existing PDF and injection items
pdf_converter = PromptConverterConfiguration.from_converters(
    converters=[
        PDFConverter(
            prompt_template=None,
            font_type="Arial",
            font_size=12,
            page_width=210,
            page_height=297,
            existing_pdf=cv_pdf_path,  # Provide the existing PDF
            injection_items=injection_items,  # Provide the injection items
        )
    ]
)

converter_config = AttackConverterConfig(
    request_converters=pdf_converter,
)

# Initialize the attack
attack = PromptSendingAttack(
    objective_target=prompt_target,
    attack_converter_config=converter_config,
)

result = await attack.execute_async(objective="Modify existing PDF")  # type: ignore
await ConsoleAttackResultPrinter().print_conversation_async(result=result)  # type: ignore

# %% [markdown]
# ## WordDocConverter
#
# The `WordDocConverter` generates Word (.docx) documents from text. It supports two main modes:
#
# 1. **New Document Generation**: Convert plain text strings into Word documents
# 2. **Placeholder-Based Injection**: Replace a placeholder in an existing `.docx` document with rendered content
#
# Placeholders must be fully contained within a single run in the document. If a placeholder spans
# multiple runs (e.g., due to mixed formatting), it will not be replaced.

# %% [markdown]
# ### Direct Word Document Generation (No Template)
#
# This mode converts plain text strings directly into `.docx` files.

# %%
from pyrit.prompt_converter import WordDocConverter

word_doc_converter = PromptConverterConfiguration.from_converters(converters=[WordDocConverter()])

converter_config = AttackConverterConfig(
    request_converters=word_doc_converter,
)

attack = PromptSendingAttack(
    objective_target=prompt_target,
    attack_converter_config=converter_config,
)

result = await attack.execute_async(objective="This is a simple test string for Word document generation.")  # type: ignore
await ConsoleAttackResultPrinter().print_conversation_async(result=result)  # type: ignore

# %% [markdown]
# ### Placeholder-Based Injection into Existing Word Documents
#
# This mode replaces a literal placeholder string in an existing `.docx` file with the prompt content.

# %%
import tempfile

from docx import Document  # type: ignore[import-untyped]

# Create a sample Word document with a placeholder
with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_file:
    doc = Document()
    doc.add_paragraph("Dear Hiring Manager,")
    doc.add_paragraph("I am writing to apply for the {{INJECTION_PLACEHOLDER}} position.")
    doc.add_paragraph("Sincerely, Applicant")
    doc.save(tmp_file.name)
    template_docx_path = Path(tmp_file.name)

word_doc_converter = PromptConverterConfiguration.from_converters(
    converters=[
        WordDocConverter(
            existing_docx=template_docx_path,
            placeholder="{{INJECTION_PLACEHOLDER}}",
        )
    ]
)

converter_config = AttackConverterConfig(
    request_converters=word_doc_converter,
)

attack = PromptSendingAttack(
    objective_target=prompt_target,
    attack_converter_config=converter_config,
)

result = await attack.execute_async(objective="AI Red Team Engineer")  # type: ignore
await ConsoleAttackResultPrinter().print_conversation_async(result=result)  # type: ignore
