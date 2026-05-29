# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import csv
import json
from pathlib import Path
from typing import Optional

from pyrit.models import MessagePiece


class MemoryExporter:
    """
    Handles the export of data to various formats, currently supporting only JSON format.
    This class utilizes the strategy design pattern to select the appropriate export format.
    """

    def __init__(self) -> None:
        """
        Initialize the MemoryExporter.

        Sets up the available export formats using the strategy design pattern.
        """
        # Using strategy design pattern for export functionality.
        self.export_strategies = {
            "json": self.export_to_json,
            "csv": self.export_to_csv,
            "md": self.export_to_markdown,
            # Future formats can be added here
        }

    def export_data(
        self, data: list[MessagePiece], *, file_path: Optional[Path] = None, export_type: str = "json"
    ) -> None:
        """
        Export the provided data to a file in the specified format.

        Args:
            data (list[MessagePiece]): The data to be exported, as a list of MessagePiece instances.
            file_path (str): The full path, including the file name, where the data will be exported.
            export_type (str, Optional): The format for exporting data. Defaults to "json".

        Raises:
            ValueError: If no file_path is provided or if the specified export format is not supported.
        """
        if not file_path:
            raise ValueError("Please provide a valid file path for exporting data.")

        export_func = self.export_strategies.get(export_type)
        if export_func:
            export_func(data, file_path)
        else:
            raise ValueError(f"Unsupported export format: {export_type}")

    def export_to_json(self, data: list[MessagePiece], file_path: Optional[Path] = None) -> None:
        """
        Export the provided data to a JSON file at the specified file path.
        Each item in the data list, representing a row from the table,
        is converted to a dictionary before being written to the file.

        Args:
            data (list[MessagePiece]): The data to be exported, as a list of MessagePiece instances.
            file_path (Path): The full path, including the file name, where the data will be exported.

        Raises:
            ValueError: If no file_path is provided.
        """
        if not file_path:
            raise ValueError("Please provide a valid file path for exporting data.")
        if not data:
            raise ValueError("No data to export.")
        export_data = [piece.to_dict() for piece in data]
        with open(file_path, "w") as f:
            json.dump(export_data, f, indent=4)

    def export_to_csv(self, data: list[MessagePiece], file_path: Optional[Path] = None) -> None:
        """
        Export the provided data to a CSV file at the specified file path.
        Each item in the data list, representing a row from the table,
        is converted to a dictionary before being written to the file.

        Args:
            data (list[MessagePiece]): The data to be exported, as a list of MessagePiece instances.
            file_path (Path): The full path, including the file name, where the data will be exported.

        Raises:
            ValueError: If no file_path is provided.
        """
        if not file_path:
            raise ValueError("Please provide a valid file path for exporting data.")
        if not data:
            raise ValueError("No data to export.")
        export_data = [piece.to_dict() for piece in data]
        fieldnames = list(export_data[0].keys())
        with open(file_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(export_data)

    def export_to_markdown(self, data: list[MessagePiece], file_path: Optional[Path] = None) -> None:
        """
        Export the provided data to a Markdown file at the specified file path.
        Each item in the data list is converted to a dictionary and formatted as a table.

        Args:
            data (list[MessagePiece]): The data to be exported, as a list of MessagePiece instances.
            file_path (Path): The full path, including the file name, where the data will be exported.

        Raises:
            ValueError: If no file_path is provided or if there is no data to export.
        """
        if not file_path:
            raise ValueError("Please provide a valid file path for exporting data.")
        if not data:
            raise ValueError("No data to export.")
        export_data = [piece.to_dict() for piece in data]
        fieldnames = list(export_data[0].keys())
        with open(file_path, "w", newline="") as f:
            f.write(f"| {' | '.join(fieldnames)} |\n")
            f.write(f"| {' | '.join(['---'] * len(fieldnames))} |\n")
            for row in export_data:
                f.write(f"| {' | '.join(str(row[field]) for field in fieldnames)} |\n")
