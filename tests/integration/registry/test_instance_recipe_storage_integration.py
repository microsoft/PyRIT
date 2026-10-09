# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Saved instance recipes in a real Azure Blob container."""

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest
from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient

from pyrit.models import ComponentType
from pyrit.models.catalog.instance_recipe import InstanceRecipe
from pyrit.registry.file_document_storage import DocumentConflictError
from pyrit.registry.instance_recipe_storage import InstanceRecipeStorage

_CONTAINER_URL_VARIABLE = "AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL_TEST"


@pytest.fixture
def blob_source() -> Iterator[str]:
    container_url = os.getenv(_CONTAINER_URL_VARIABLE)
    if not container_url:
        pytest.skip(f"Set {_CONTAINER_URL_VARIABLE} to a test Blob container.")
    parsed = urlparse(container_url)
    prefix = f"pyrit-instance-recipes-test-{uuid.uuid4().hex}"
    yield parsed._replace(path=f"{parsed.path.rstrip('/')}/{prefix}").geturl()
    credential = None if parsed.query else DefaultAzureCredential()
    with ContainerClient.from_container_url(container_url, credential=credential) as client:
        for blob in client.list_blobs(name_starts_with=f"{prefix}/"):
            client.delete_blob(blob.name)


@pytest.mark.run_only_if_all_tests
def test_blob_recipes_round_trip_with_conditional_writes(blob_source: str) -> None:
    storage = InstanceRecipeStorage(source=blob_source)
    recipe = InstanceRecipe(kind=ComponentType.TARGET, name="Team-Chat.1", type="TextTarget")

    created = storage.save_recipe(recipe=recipe, expected_version=None)
    with pytest.raises(DocumentConflictError):
        storage.save_recipe(recipe=recipe, expected_version=None)

    replaced = storage.save_recipe(
        recipe=recipe.model_copy(update={"params": {"text_stream": None}}), expected_version=created.version
    )
    with pytest.raises(DocumentConflictError):
        storage.save_recipe(recipe=recipe, expected_version=created.version)
    assert storage.list_recipes() == ([replaced], [])
    assert storage.load_recipe(kind=ComponentType.TARGET, name="Team-Chat.1") == replaced

    with pytest.raises(DocumentConflictError):
        storage.delete_recipe(kind=ComponentType.TARGET, name="Team-Chat.1", expected_version=created.version)
    storage.delete_recipe(kind=ComponentType.TARGET, name="Team-Chat.1", expected_version=replaced.version)
    assert storage.list_recipes() == ([], [])


@pytest.mark.run_only_if_all_tests
def test_blob_recipes_put_back_exact_bytes_and_delete_unreadable_documents(blob_source: str) -> None:
    storage = InstanceRecipeStorage(source=blob_source)
    hand_written = b'{"kind":"target","name":"chat","type":"TextTarget"}'
    chat_document = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="chat")
    broken_document = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="broken")
    storage._save_document_conditional(name=chat_document, content=hand_written, expected_version=None)
    storage._save_document_conditional(name=broken_document, content=b"{not json", expected_version=None)

    [previous], [broken] = storage.list_recipes()
    assert broken.name == broken_document
    replacement = storage.save_recipe(
        recipe=previous.recipe.model_copy(update={"params": {"text_stream": None}}), expected_version=previous.version
    )
    storage.restore_recipe(previous=previous, expected_version=replacement.version)

    assert storage._read_document_bytes(chat_document) == hand_written
    assert broken.version is not None
    storage.delete_recipe(kind=ComponentType.TARGET, name=broken.name, expected_version=broken.version)
    assert storage.list_recipes() == ([previous], [])
