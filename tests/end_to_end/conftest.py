# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio

from pyrit.setup import IN_MEMORY, initialize_pyrit_async

# Initialize PyRIT with in-memory storage so providers that save images
# (e.g. multimodal datasets) can use CentralMemory / DataTypeSerializer.
asyncio.run(initialize_pyrit_async(memory_db_type=IN_MEMORY))
