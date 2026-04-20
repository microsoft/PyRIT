# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from pypdf import PageObject, PdfReader, PdfWriter
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from pyrit.common.logger import logger
from pyrit.models import PromptDataType, SeedPrompt, data_serializer_factory
from pyrit.models.data_type_serializer import DataTypeSerializer
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class PDFConverter(PromptConverter):
    """
    Converts a text prompt into a PDF file.

    Supports various modes:
        - Template-Based Generation:
            If a ``SeedPrompt`` is provided, dynamic data can be injected into the template using
            the ``SeedPrompt.render_template_value`` method, and the resulting content is converted to a PDF.
        - Direct Text-Based Generation:
            If no template is provided, the raw string prompt is converted directly into a PDF.
        - Modify Existing PDFs (Overlay approach):
            Enables injecting text into existing PDFs at specified coordinates, merging a new "overlay layer"
            onto the original PDF.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("binary_path",)

    def __init__(
        self,
        prompt_template: Optional[SeedPrompt] = None,
        font_type: str = "Helvetica",
        font_size: int = 12,
        font_color: tuple[int, int, int] = (255, 255, 255),
        page_width: int = 210,
        page_height: int = 297,
        column_width: int = 0,
        row_height: int = 10,
        existing_pdf: Optional[Path] = None,
        injection_items: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """
        Initialize the converter with the specified parameters.

        Args:
            prompt_template (Optional[SeedPrompt], optional): A ``SeedPrompt`` object representing a template.
            font_type (str): Font type for the PDF. Defaults to "Helvetica".
            font_size (int): Font size for the PDF. Defaults to 12.
            font_color (tuple): Font color for the PDF in RGB format. Defaults to (255, 255, 255).
            page_width (int): Width of the PDF page in mm. Defaults to 210 (A4 width).
            page_height (int): Height of the PDF page in mm. Defaults to 297 (A4 height).
            column_width (int): Width of each column in the PDF. Defaults to 0 (full page width).
            row_height (int): Height of each row in the PDF. Defaults to 10.
            existing_pdf (Optional[Path], optional): Path to an existing PDF file. Defaults to None.
            injection_items (Optional[List[Dict]], optional): A list of injection items for modifying an existing PDF.

        Raises:
            ValueError: If the font color is invalid or the injection items are not provided as a list of dictionaries.
            FileNotFoundError: If the provided PDF file does not exist.
        """
        self._prompt_template = prompt_template
        self._font_type = font_type
        self._font_size = font_size
        self._font_color = font_color
        self._page_width = page_width
        self._page_height = page_height
        self._column_width = column_width
        self._row_height = row_height

        # Keeping the user's path here
        self._existing_pdf_path: Optional[Path] = existing_pdf
        # We store the file data in a separate BytesIO because of a mypy error
        self._existing_pdf_bytes: Optional[BytesIO] = None

        self._injection_items = injection_items or []

        # Validate font color
        if not (isinstance(font_color, tuple) and len(font_color) == 3 and all(0 <= c <= 255 for c in font_color)):
            raise ValueError(f"Invalid font_color: {font_color}. Must be a tuple of three integers (0-255).")

        # If a valid path is provided, load it into memory as BytesIO
        if existing_pdf is not None:
            if not existing_pdf.is_file():
                raise FileNotFoundError(f"PDF file not found at: {existing_pdf}")

            # Read the file contents into a BytesIO stream
            with open(existing_pdf, "rb") as pdf_file:
                self._existing_pdf_bytes = BytesIO(pdf_file.read())
        else:
            # No existing PDF path was provided
            self._existing_pdf = None

        # Validate injection items
        if not all(isinstance(item, dict) for item in self._injection_items):
            raise ValueError("Each injection item must be a dictionary.")

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt into a PDF.

        If a template is provided, it injects the prompt into the template, otherwise, it generates
        a simple PDF with the prompt as the content. Further it can modify existing PDFs.

        Args:
            prompt (str): The prompt to be embedded in the PDF.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the full file path to the generated PDF.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        # Step 1: Prepare content
        content = self._prepare_content(prompt)

        # Step 2: Generate or modify the PDF (Overlay, if existing PDF)
        if self._existing_pdf_bytes:
            pdf_bytes = self._modify_existing_pdf()
        else:
            pdf_bytes = self._generate_pdf(content)

        # Step 3: Serialize PDF
        pdf_serializer = await self._serialize_pdf(pdf_bytes, content)

        # Return the result
        return ConverterResult(output_text=pdf_serializer.value, output_type="binary_path")

    def _prepare_content(self, prompt: str) -> str:
        """
        Prepare the content for the PDF, either from a template or directly from the prompt.

        Args:
            prompt (str): The input prompt.

        Returns:
            str: The prepared content.

        Raises:
            ValueError: If the parsed dynamic data is not a dictionary.
            ValueError: If rendering the prompt fails.
            ValueError: If the prompt is not a string when no template is provided.
        """
        if self._prompt_template:
            logger.debug(f"Preparing content with template: {self._prompt_template.value}")
            try:
                # Parse string prompt to dictionary
                dynamic_data = ast.literal_eval(prompt) if isinstance(prompt, str) else prompt
                logger.debug(f"Parsed dynamic data: {dynamic_data}")

                if not isinstance(dynamic_data, dict):
                    raise ValueError("Prompt must be a dictionary-compatible object after parsing.")

                # Use SeedPrompt's render_template_value for rendering
                rendered_content = self._prompt_template.render_template_value(**dynamic_data)
                logger.debug(f"Rendered content: {rendered_content}")
                return rendered_content

            except (ValueError, KeyError) as e:
                logger.error(f"Error rendering prompt: {e}")
                raise ValueError(f"Failed to render the prompt: {e}")

        # If no template is provided, return the raw prompt as content
        if isinstance(prompt, str):
            logger.debug("No template provided. Using raw prompt.")
            return prompt
        else:
            logger.error("Prompt must be a string when no template is provided.")
            raise ValueError("Prompt must be a string when no template is provided.")

    def _generate_pdf(self, content: str) -> bytes:
        """
        Generate a PDF with the given content using ReportLab.

        Args:
            content (str): The text content to include in the PDF.

        Returns:
            bytes: The generated PDF content in bytes.
        """
        pdf_buffer = BytesIO()

        # Convert mm to points
        # 1mm = 2.83465 points
        page_width_pt = self._page_width * mm
        page_height_pt = self._page_height * mm

        c = canvas.Canvas(pdf_buffer, pagesize=(page_width_pt, page_height_pt))

        font_map = {
            "Arial": "Helvetica",
            "Times": "Times-Roman",
            "Courier": "Courier",
            "Helvetica": "Helvetica",
            "Symbol": "Symbol",
            "ZapfDingbats": "ZapfDingbats",
        }
        reportlab_font = font_map.get(self._font_type, "Helvetica")

        c.setFont(reportlab_font, self._font_size)

        margin = 10 * mm
        x = margin
        y = page_height_pt - margin  # ReportLab uses bottom-left origin

        # Calculate actual column width
        if self._column_width == 0:
            actual_width = page_width_pt - (2 * margin)
        else:
            actual_width = self._column_width * mm

        # Convert row_height from mm to points
        line_height = self._row_height * mm if self._row_height else self._font_size * 1.2

        # Split content into lines (handle existing newlines)
        paragraphs = content.split("\n")

        for paragraph in paragraphs:
            if not paragraph:  # Empty line
                y -= line_height
                continue

            # Word wrap the paragraph
            # simpleSplit handles word wrapping
            wrapped_lines = simpleSplit(paragraph, reportlab_font, self._font_size, actual_width)

            for line in wrapped_lines:
                # Check if we need a new page
                if y < margin + line_height:
                    c.showPage()
                    c.setFont(reportlab_font, self._font_size)
                    y = page_height_pt - margin

                # Draw the text (ReportLab origin is bottom-left)
                c.drawString(x, y, line)
                y -= line_height

        c.save()
        pdf_buffer.seek(0)
        return pdf_buffer.getvalue()

    def _modify_existing_pdf(self) -> bytes:
        """
        Loop over each page, check for matching injection items, and merge a small "overlay PDF" for each item.

        Returns:
            bytes: The modified PDF content in bytes.

        Raises:
            ValueError: If the existing PDF or injection items are not provided.
        """
        if not self._existing_pdf_bytes or not self._injection_items:
            raise ValueError("Existing PDF and injection items are required for modification.")

        reader = PdfReader(self._existing_pdf_bytes)
        writer = PdfWriter()

        # Keep a list of overlay buffers to close them after final write
        overlay_buffers = []

        for page_number, page in enumerate(reader.pages):
            # We know page_number is valid because enumerate() only provides indices in range(total_pages).
            # Therefore, no extra check needed here.

            logger.info(f"Processing page {page_number} with {len(self._injection_items)} injection items.")

            # Extract page dimensions for early coordinate checks
            page_width = float(page.mediabox[2] - page.mediabox[0])
            page_height = float(page.mediabox[3] - page.mediabox[1])

            # For each item that belongs on this page, create and merge an overlay
            for item in self._injection_items:
                if item.get("page", 0) == page_number:
                    # Default to a small offset (10 points) from the top-left corner if no coordinates are provided.
                    # This prevents injected text from starting at (0,0) and potentially running off the edges.
                    x = item.get("x", 10)
                    y = item.get("y", 10)
                    text = item.get("text", "")
                    font = item.get("font", self._font_type)
                    font_size = item.get("font_size", self._font_size)
                    font_color = item.get("font_color", self._font_color)

                    # Coordinate validation before calling _inject_text_into_page
                    if not (0 <= x <= page_width and 0 <= y <= page_height):
                        raise ValueError(f"Coordinates x={x}, y={y} out of bounds for page {page_number}.")

                    # (1) Build the overlay PageObject + buffer
                    overlay_page, overlay_buffer = self._inject_text_into_page(
                        page, x, y, text, font, font_size, font_color
                    )

                    # (2) Merge onto the page
                    page.merge_page(overlay_page)

                    # (3) Store overlay buffer to close later
                    overlay_buffers.append(overlay_buffer)

            # Add the modified page to the writer
            writer.add_page(page)

        # Finalize the PDF
        output_pdf = BytesIO()
        writer.write(output_pdf)
        output_pdf.seek(0)

        # Safe to close all overlays AFTER writing is finished
        for buf in overlay_buffers:
            buf.close()

        return output_pdf.getvalue()

    def _inject_text_into_page(
        self,
        page: PageObject,
        x: float,
        y: float,
        text: str,
        font: str,
        font_size: int,
        font_color: tuple[int, int, int],
    ) -> tuple[PageObject, BytesIO]:
        """
        Generate an overlay PDF with the given text using ReportLab.

        Args:
            page (PageObject): The original PDF page to overlay on.
            x (float): The x-coordinate for the text (in points).
            y (float): The y-coordinate for the text (in points).
            text (str): The text to inject.
            font (str): The font type.
            font_size (int): The font size.
            font_color (tuple): The font color in RGB format (0-255).

        Returns:
            tuple[PageObject, BytesIO]: The overlay page object and its corresponding buffer.

        Raises:
            ValueError: If the coordinates are out of bounds.
        """
        from reportlab.pdfgen import canvas

        # Determine page size from the original page's MediaBox
        page_width = float(page.mediabox[2] - page.mediabox[0])
        page_height = float(page.mediabox[3] - page.mediabox[1])

        # Out-of-Bounds Checks (same as original)
        if x < 0:
            logger.error(f"x_pos is less than 0 and therefore out of bounds: x={x}")
            raise ValueError(f"x_pos is less than 0 and therefore out of bounds: x={x}")
        if x > page_width:
            logger.error(f"x_pos exceeds page width and is out of bounds: x={x}, page_width={page_width}")
            raise ValueError(f"x_pos exceeds page width and is out of bounds: x={x}, page_width={page_width}")
        if y < 0:
            logger.error(f"y_pos is less than 0 and therefore out of bounds: y={y}")
            raise ValueError(f"y_pos is less than 0 and therefore out of bounds: y={y}")
        if y > page_height:
            logger.error(f"y_pos exceeds page height and is out of bounds: y={y}, page_height={page_height}")
            raise ValueError(f"y_pos exceeds page height and is out of bounds: y={y}, page_height={page_height}")

        # Create overlay buffer
        overlay_buffer = BytesIO()
        c = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))

        font_map = {
            "Arial": "Helvetica",
            "Times": "Times-Roman",
            "Courier": "Courier",
            "Helvetica": "Helvetica",
            "Symbol": "Symbol",
            "ZapfDingbats": "ZapfDingbats",
        }
        reportlab_font = font_map.get(font, "Helvetica")

        c.setFont(reportlab_font, font_size)

        # Set color - ReportLab uses 0-1 range
        r, g, b = font_color
        c.setFillColorRGB(r / 255.0, g / 255.0, b / 255.0)

        c.drawString(x, y, text)

        c.save()
        overlay_buffer.seek(0)

        # Create a pypdf PageObject from the overlay
        overlay_reader = PdfReader(overlay_buffer)
        overlay_page = overlay_reader.pages[0]

        return overlay_page, overlay_buffer

    async def _serialize_pdf(self, pdf_bytes: bytes, content: str) -> DataTypeSerializer:
        """
        Serialize the generated PDF using a data serializer.

        Args:
            pdf_bytes (bytes): The generated PDF content in bytes.
            content (str): The original content of the PDF.

        Returns:
            DataTypeSerializer: The serializer object containing metadata about the saved file.
        """
        original_filename_ending = self._existing_pdf_path.suffix if self._existing_pdf_path else ""

        if original_filename_ending:
            extension = original_filename_ending[1:]  # Remove the leading dot
        else:
            extension = "pdf"

        pdf_serializer = data_serializer_factory(
            category="prompt-memory-entries",
            data_type="binary_path",
            extension=extension,
        )
        await pdf_serializer.save_data(pdf_bytes)
        return pdf_serializer
