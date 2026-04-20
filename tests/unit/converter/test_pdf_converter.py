# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pypdf import PageObject, PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pyrit.models import DataTypeSerializer, SeedPrompt
from pyrit.prompt_converter import ConverterResult, PDFConverter


@pytest.fixture
def pdf_converter_no_template():
    """A PDFConverter with no template path provided."""
    return PDFConverter(prompt_template=None)


@pytest.fixture
def pdf_converter_with_template(
    tmp_path,
):
    """A PDFConverter with a valid template file."""
    template_content = "This is a test template. Prompt: {{ prompt }}"
    template_file = tmp_path / "test_template.txt"
    template_file.write_text(template_content)
    template = SeedPrompt(
        value=template_content,
        data_type="text",
        name="test_template",
        parameters=["prompt"],
    )
    converter = PDFConverter(prompt_template=template)

    yield converter
    if template_file.exists():
        template_file.unlink()


@pytest.fixture
def pdf_converter_nonexistent_template():
    """A PDFConverter with a non-existent template path."""
    return PDFConverter(prompt_template=None)


@pytest.mark.asyncio
async def test_convert_async_no_template(pdf_converter_no_template):
    """Test converting a prompt without a template."""
    prompt = "Hello, PDF!"
    mock_pdf_bytes = BytesIO(b"mock_pdf_content")

    # Mock internal methods
    with (
        patch.object(pdf_converter_no_template, "_prepare_content", return_value=prompt) as mock_prepare,
        patch.object(pdf_converter_no_template, "_generate_pdf", return_value=mock_pdf_bytes) as mock_generate,
        patch.object(pdf_converter_no_template, "_serialize_pdf") as mock_serialize,
    ):
        serializer_mock = MagicMock()
        serializer_mock.value = "mock_url"
        mock_serialize.return_value = serializer_mock

        result = await pdf_converter_no_template.convert_async(prompt=prompt)

        # Assertions
        mock_prepare.assert_called_once_with(prompt)
        mock_generate.assert_called_once_with(prompt)
        mock_serialize.assert_called_once_with(mock_pdf_bytes, prompt)

        assert isinstance(result, ConverterResult)
        assert result.output_type == "binary_path"
        assert result.output_text == "mock_url"


@pytest.mark.asyncio
async def test_convert_async_with_template(pdf_converter_with_template):
    """Test converting a prompt using a provided template."""
    prompt = {"prompt": "TemplateTest"}
    expected_rendered_content = "This is a test template. Prompt: TemplateTest"
    mock_pdf_bytes = BytesIO(b"mock_pdf_content")

    # Mock internal methods
    with (
        patch.object(
            pdf_converter_with_template, "_prepare_content", return_value=expected_rendered_content
        ) as mock_prepare,
        patch.object(pdf_converter_with_template, "_generate_pdf", return_value=mock_pdf_bytes) as mock_generate,
        patch.object(pdf_converter_with_template, "_serialize_pdf") as mock_serialize,
    ):
        serializer_mock = MagicMock()
        serializer_mock.value = "mock_url"
        mock_serialize.return_value = serializer_mock

        result = await pdf_converter_with_template.convert_async(prompt=prompt)

        # Assertions
        mock_prepare.assert_called_once_with(prompt)
        mock_generate.assert_called_once_with(expected_rendered_content)
        mock_serialize.assert_called_once_with(mock_pdf_bytes, expected_rendered_content)

        assert isinstance(result, ConverterResult)
        assert result.output_type == "binary_path"
        assert result.output_text == "mock_url"


@pytest.mark.asyncio
async def test_convert_async_nonexistent_template(pdf_converter_nonexistent_template):
    """Test behavior when the template file does not exist."""
    with patch.object(pdf_converter_nonexistent_template, "_prepare_content", side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError):
            await pdf_converter_nonexistent_template.convert_async(prompt="This will fail")


@pytest.mark.asyncio
async def test_convert_async_custom_font_and_size():
    """Test PDF generation with custom font and size parameters."""
    converter = PDFConverter(
        prompt_template=None,
        font_type="Courier",
        font_size=14,
        page_width=200,
        page_height=280,
    )

    prompt = "Custom font and size test."
    with patch("pyrit.prompt_converter.pdf_converter.data_serializer_factory") as mock_factory:
        serializer_mock = MagicMock(spec=DataTypeSerializer)
        serializer_mock.value = "mock_url"
        mock_factory.return_value = serializer_mock

        result = await converter.convert_async(prompt=prompt)
        assert isinstance(result, ConverterResult)
        assert result.output_text == "mock_url"
        serializer_mock.save_data.assert_called_once()


def test_input_supported(pdf_converter_no_template):
    """Test that only 'text' input types are supported."""
    assert pdf_converter_no_template.input_supported("text") is True
    assert pdf_converter_no_template.input_supported("image") is False


@pytest.mark.asyncio
async def test_convert_async_end_to_end_no_reader(tmp_path, sqlite_instance):
    prompt = "Test for PDF generation."
    pdf_file_path = tmp_path / "output.pdf"
    converter = PDFConverter(prompt_template=None)

    result = await converter.convert_async(prompt=prompt)
    with open(pdf_file_path, "wb") as pdf_file:
        pdf_file.write(result.output_text.encode("latin1"))

    assert pdf_file_path.exists()
    assert pdf_file_path.stat().st_size > 0
    pdf_file_path.unlink()


@pytest.fixture
def mock_pdf_path(tmp_path):
    """Create and return the path for a mock PDF with multiple pages."""
    pdf_path = tmp_path / "mock_test.pdf"

    # Create a multi-page PDF using ReportLab
    pdf_bytes = BytesIO()
    c = canvas.Canvas(pdf_bytes, pagesize=A4)
    width, height = A4
    c.setFont("Helvetica", 12)
    c.drawString(50, height - 50, "Page 1 content")
    c.showPage()
    c.setFont("Helvetica", 12)
    c.drawString(50, height - 50, "Page 2 content")
    c.save()
    pdf_bytes.seek(0)

    with open(pdf_path, "wb") as pdf_file:
        pdf_file.write(pdf_bytes.getvalue())

    return pdf_path


@pytest.mark.asyncio
async def test_injection_into_mock_pdf(mock_pdf_path):
    """Test injecting text into a generic mock PDF."""
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
        }
    ]

    # Create the PDFConverter with the mock PDF and injection items
    converter = PDFConverter(existing_pdf=mock_pdf_path, injection_items=injection_items)

    # Perform the injection
    result = await converter.convert_async(prompt="")

    # Get the local file path from the result
    modified_pdf_path = Path(result.output_text)
    assert modified_pdf_path.exists(), "Modified PDF file does not exist"

    # Open and validate the modified PDF
    with open(modified_pdf_path, "rb") as modified_pdf_file:
        modified_pdf = PdfReader(modified_pdf_file)
        page_content = modified_pdf.pages[0].extract_text()

    # Validate the injected text
    assert "Injected Text" in page_content, "Injected text not found on page 0"
    modified_pdf_path.unlink()  # Clean up after the test


@pytest.mark.asyncio
async def test_multiple_injections_into_mock_pdf(mock_pdf_path):
    """Test injecting text into multiple pages of a generic mock PDF."""
    # Define multiple injection items
    injection_items = [
        {
            "page": 0,
            "x": 50,
            "y": 700,
            "text": "Page 0 Injection",
            "font_size": 12,
            "font": "Helvetica",
            "font_color": (255, 0, 0),
        },
        {
            "page": 1,
            "x": 75,
            "y": 650,
            "text": "Page 1 Injection",
            "font_size": 10,
            "font": "Helvetica",
            "font_color": (0, 255, 0),
        },
    ]

    # Create the PDFConverter with the mock PDF and multiple injection items
    converter = PDFConverter(existing_pdf=mock_pdf_path, injection_items=injection_items)

    # Perform the injections
    result = await converter.convert_async(prompt="")

    # Get the local file path from the result
    modified_pdf_path = Path(result.output_text)
    assert modified_pdf_path.exists(), "Modified PDF file does not exist"

    # Open and validate the modified PDF
    with open(modified_pdf_path, "rb") as modified_pdf_file:
        modified_pdf = PdfReader(modified_pdf_file)
        page_0_content = modified_pdf.pages[0].extract_text()
        page_1_content = modified_pdf.pages[1].extract_text()

    # Validate the injected text
    assert "Page 0 Injection" in page_0_content, "Injected text not found on page 0"
    assert "Page 1 Injection" in page_1_content, "Injected text not found on page 1"
    modified_pdf_path.unlink()  # Clean up after the test


@pytest.fixture
def dummy_page():
    """
    Create a dummy page with a known mediabox simulating an A4 page.
    An A4 page in points is approximately 595 x 842.
    """
    page = MagicMock(spec=PageObject)
    page.mediabox = [0, 0, 595, 842]
    return page


def test_inject_text_negative_x(dummy_page):
    """Test that a negative x coordinate raises a ValueError."""
    converter = PDFConverter(prompt_template=None)
    with pytest.raises(ValueError) as excinfo:
        converter._inject_text_into_page(
            page=dummy_page,
            x=-10,
            y=100,
            text="Test Text",
            font="Helvetica",
            font_size=12,
            font_color=(0, 0, 0),
        )
    assert "x_pos is less than 0" in str(excinfo.value)


def test_inject_text_x_exceeds_page_width(dummy_page):
    """Test that an x coordinate exceeding the A4 page width (595) raises a ValueError."""
    converter = PDFConverter(prompt_template=None)
    with pytest.raises(ValueError) as excinfo:
        converter._inject_text_into_page(
            page=dummy_page,
            x=600,  # 600 exceeds the A4 width of 595
            y=100,
            text="Test Text",
            font="Helvetica",
            font_size=12,
            font_color=(0, 0, 0),
        )
    assert "x_pos exceeds page width" in str(excinfo.value)


def test_inject_text_negative_y(dummy_page):
    """Test that a negative y coordinate raises a ValueError."""
    converter = PDFConverter(prompt_template=None)
    with pytest.raises(ValueError) as excinfo:
        converter._inject_text_into_page(
            page=dummy_page,
            x=100,
            y=-20,
            text="Test Text",
            font="Helvetica",
            font_size=12,
            font_color=(0, 0, 0),
        )
    assert "y_pos is less than 0" in str(excinfo.value)


def test_inject_text_y_exceeds_page_height(dummy_page):
    """Test that a y coordinate exceeding the A4 page height (842) raises a ValueError."""
    converter = PDFConverter(prompt_template=None)
    with pytest.raises(ValueError) as excinfo:
        converter._inject_text_into_page(
            page=dummy_page,
            x=100,
            y=850,  # 850 exceeds the A4 height of 842
            text="Test Text",
            font="Helvetica",
            font_size=12,
            font_color=(0, 0, 0),
        )
    assert "y_pos exceeds page height" in str(excinfo.value)


def test_pdf_reader_repeated_access():
    # Create a simple PDF in memory
    pdf_bytes = BytesIO()
    c = canvas.Canvas(pdf_bytes, pagesize=A4)
    width, height = A4
    c.setFont("Helvetica", 12)
    c.drawString(50, height - 50, "Test Content")
    c.save()
    pdf_bytes.seek(0)

    # Use PdfReader to read the PDF
    reader = PdfReader(pdf_bytes)
    first_access = reader.pages[0].extract_text()
    second_access = reader.pages[0].extract_text()

    # Assertions to verify behavior
    assert first_access == second_access, "Repeated access should return consistent data"


@pytest.mark.asyncio
async def test_empty_injection_items(mock_pdf_path):
    """Test that an empty list of injection items raises a ValueError."""
    converter = PDFConverter(existing_pdf=mock_pdf_path, injection_items=[])
    with pytest.raises(ValueError, match="Existing PDF and injection items are required"):
        await converter.convert_async(prompt="")


@pytest.mark.asyncio
async def test_injection_items_non_existent_page_number(mock_pdf_path):
    """
    Test the PDFConverter's handling of injection items with a non-existent page number.
    """

    injection_items = [
        {
            "page": 2,  # Out of range for a 2-page PDF
            "x": 50,
            "y": 700,
            "text": "InvisibleText",
            "font_size": 12,
            "font": "Helvetica",
            "font_color": (255, 0, 0),
        }
    ]
    converter = PDFConverter(existing_pdf=mock_pdf_path, injection_items=injection_items)

    result = await converter.convert_async(prompt="")
    modified_pdf_path = Path(result.output_text)
    assert modified_pdf_path.exists(), "Modified PDF file not created"

    # Read final PDF and confirm 'InvisibleText' is nowhere
    with open(modified_pdf_path, "rb") as f:
        reader = PdfReader(f)
        page_0_text = reader.pages[0].extract_text()
        page_1_text = reader.pages[1].extract_text()

    assert "InvisibleText" not in page_0_text
    assert "InvisibleText" not in page_1_text

    modified_pdf_path.unlink()


@pytest.mark.asyncio
async def test_non_standard_font_usage():
    """
    Test the ability to use a non-standard font (Times) in PDF generation.
    """
    converter = PDFConverter(
        prompt_template=None,
        font_type="Times",
        font_size=12,
        page_width=210,
        page_height=297,
    )

    prompt = "Testing Times font usage."
    result = await converter.convert_async(prompt=prompt)
    assert isinstance(result, ConverterResult), "Expected a ConverterResult"

    output_path = Path(result.output_text)
    assert output_path.exists(), "No output PDF was created"

    # Optionally, read the PDF to confirm text presence
    with open(output_path, "rb") as f:
        reader = PdfReader(f)
        page_text = reader.pages[0].extract_text()
    assert "Testing Times font usage." in page_text

    output_path.unlink()


@pytest.mark.asyncio
async def test_injection_on_last_page(mock_pdf_path):
    """
    Test injecting text on the last page of a multi-page PDF
    """
    injection_items = [
        {
            "page": 1,
            "x": 40,
            "y": 600,
            "text": "LastPageText",
            "font_size": 12,
            "font": "Helvetica",
            "font_color": (0, 0, 255),
        }
    ]
    converter = PDFConverter(existing_pdf=mock_pdf_path, injection_items=injection_items)

    result = await converter.convert_async(prompt="")
    modified_pdf_path = Path(result.output_text)
    assert modified_pdf_path.exists(), "Modified PDF was not created"

    # Check that the text is on page 1 only
    with open(modified_pdf_path, "rb") as f:
        reader = PdfReader(f)
        page_0_text = reader.pages[0].extract_text()
        page_1_text = reader.pages[1].extract_text()

    assert "LastPageText" not in page_0_text
    assert "LastPageText" in page_1_text

    modified_pdf_path.unlink()


@pytest.mark.asyncio
async def test_filename_extension_default(sqlite_instance):
    converter = PDFConverter(
        prompt_template=None,
        font_type="Helvetica",
        font_size=12,
        page_width=210,
        page_height=297,
    )

    result = await converter.convert_async(prompt="test")
    assert result.output_text.endswith(".pdf"), "The output file should have a .pdf extension"


@pytest.mark.asyncio
async def test_filename_extension_existing_pdf(sqlite_instance):
    import tempfile

    import requests

    url = (
        "https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/datasets/prompt_converters/pdf_converters/fake_CV.pdf"
    )
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp_file:
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

    converter = PDFConverter(
        prompt_template=None,
        font_type="Helvetica",
        font_size=12,
        page_width=210,
        page_height=297,
        existing_pdf=cv_pdf_path,
        injection_items=injection_items,
    )

    result = await converter.convert_async(prompt="test")
    assert result.output_text.endswith(".tmp"), "The output file should have a .tmp extension"
