# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import logging

from PIL import Image

from pyrit.common.notebook_utils import is_in_ipython_session
from pyrit.memory import CentralMemory
from pyrit.models import AzureBlobStorageIO, DiskStorageIO, MessagePiece

logger = logging.getLogger(__name__)


async def display_image_response(response_piece: MessagePiece) -> None:
    """
    Display response images if running in notebook environment.

    Args:
        response_piece (MessagePiece): The response piece to display.

    Raises:
        RuntimeError: If storage IO is not initialized.
    """
    memory = CentralMemory.get_memory_instance()
    if (
        response_piece.response_error == "none"
        and response_piece.converted_value_data_type == "image_path"
        and is_in_ipython_session()
    ):
        image_location = response_piece.converted_value

        try:
            if memory.results_storage_io is None:
                raise RuntimeError("Storage IO not initialized")
            image_bytes = await memory.results_storage_io.read_file(image_location)
        except Exception as e:
            if isinstance(memory.results_storage_io, AzureBlobStorageIO):
                try:
                    # Fallback to reading from disk if the storage IO fails
                    image_bytes = await DiskStorageIO().read_file(image_location)
                except Exception as exc:
                    logger.error(f"Failed to read image from {image_location}. Full exception: {str(exc)}")
                    return
            else:
                logger.error(f"Failed to read image from {image_location}. Full exception: {str(e)}")
                return

        image_stream = io.BytesIO(image_bytes)
        image = Image.open(image_stream)

        # Jupyter built-in display function only works in notebooks.
        display(image)  # type: ignore[ty:unresolved-reference] # noqa: F821
    if response_piece.response_error == "blocked":
        logger.info("---\nContent blocked, cannot show a response.\n---")
