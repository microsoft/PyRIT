# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario preset storage."""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pyrit.models.catalog.scenario_preset import ScenarioPreset
from pyrit.registry.file_document_storage import _release_exclusive_lock, _try_acquire_exclusive_lock
from pyrit.registry.scenario_preset_storage import ScenarioPresetConflictError, ScenarioPresetStorage


def _make_preset(**overrides: object) -> ScenarioPreset:
    """
    Build a preset with test defaults.

    Returns:
        ScenarioPreset: The constructed preset.
    """
    fields: dict[str, object] = {"name": "nightly", "scenario_name": "foundry.red_team_agent"}
    fields.update(overrides)
    return ScenarioPreset(**fields)  # type: ignore[arg-type]


def test_local_storage_round_trips_a_preset(tmp_path: Path) -> None:
    """Test that a saved preset loads back with its fields intact."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    preset = _make_preset(
        techniques=["crescendo"],
        dataset_names=["harmbench"],
        max_dataset_size=25,
        dataset_filters={"harm_categories": ["violence"]},
        include_baseline=True,
        scenario_params={"max_turns": 3},
        description="Nightly smoke suite",
    )

    storage.save_preset(preset=preset, expected_version=None)
    loaded = storage.load_preset("nightly")

    assert loaded is not None
    assert loaded.preset.techniques == ["crescendo"]
    assert loaded.preset.dataset_names == ["harmbench"]
    assert loaded.preset.max_dataset_size == 25
    assert loaded.preset.dataset_filters == {"harm_categories": ["violence"]}
    assert loaded.preset.include_baseline is True
    assert loaded.preset.scenario_params == {"max_turns": 3}
    assert loaded.preset.description == "Nightly smoke suite"


def test_unset_fields_round_trip_as_none_not_false(tmp_path: Path) -> None:
    """Test the tri-state regression: an unset override must not deserialize as a disabled one."""
    storage = ScenarioPresetStorage(source=str(tmp_path))

    storage.save_preset(preset=_make_preset(), expected_version=None)
    loaded = storage.load_preset("nightly")

    assert loaded is not None
    assert loaded.preset.include_baseline is None
    assert loaded.preset.max_dataset_size is None
    assert loaded.preset.techniques is None

    stored = json.loads((tmp_path / "nightly.json").read_text(encoding="utf-8"))
    assert "include_baseline" not in stored
    assert "max_dataset_size" not in stored


def test_explicit_false_round_trips_as_false(tmp_path: Path) -> None:
    """Test that an explicitly disabled override survives storage."""
    storage = ScenarioPresetStorage(source=str(tmp_path))

    storage.save_preset(preset=_make_preset(include_baseline=False), expected_version=None)
    loaded = storage.load_preset("nightly")

    assert loaded is not None
    assert loaded.preset.include_baseline is False


def test_version_is_not_stored_inside_the_document(tmp_path: Path) -> None:
    """Test that the conflict token lives beside the document, never inside the file it guards."""
    storage = ScenarioPresetStorage(source=str(tmp_path))

    saved = storage.save_preset(preset=_make_preset(), expected_version=None)

    assert saved.version
    assert "version" not in json.loads((tmp_path / "nightly.json").read_text(encoding="utf-8"))


def test_each_save_produces_a_new_version(tmp_path: Path) -> None:
    """Test that an accepted save supersedes the token the caller passed in."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    first = storage.save_preset(preset=_make_preset(), expected_version=None)

    second = storage.save_preset(preset=_make_preset(description="changed"), expected_version=first.version)

    assert second.version != first.version
    reloaded = storage.load_preset("nightly")
    assert reloaded is not None
    assert reloaded.version == second.version


def test_identical_content_keeps_the_same_version(tmp_path: Path) -> None:
    """Test that the token describes stored content, so a no-op save does not invalidate other readers."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    first = storage.save_preset(preset=_make_preset(), expected_version=None)

    second = storage.save_preset(preset=_make_preset(), expected_version=first.version)

    assert second.version == first.version


def test_stale_version_save_is_rejected(tmp_path: Path) -> None:
    """Test that a save based on a superseded version loses the concurrency check."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    created = storage.save_preset(preset=_make_preset(), expected_version=None)
    winner = storage.save_preset(preset=_make_preset(description="first writer"), expected_version=created.version)

    with pytest.raises(ScenarioPresetConflictError) as error:
        storage.save_preset(preset=_make_preset(description="second writer"), expected_version=created.version)

    assert error.value.expected_version == created.version
    assert error.value.actual_version == winner.version


def test_out_of_band_edit_invalidates_the_version(tmp_path: Path) -> None:
    """Test that a file edited outside this class is detected, not silently overwritten."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    created = storage.save_preset(preset=_make_preset(description="original"), expected_version=None)
    (tmp_path / "nightly.json").write_text(
        json.dumps({"scenario_name": "foundry.red_team_agent", "description": "edited by hand"}),
        encoding="utf-8",
    )

    with pytest.raises(ScenarioPresetConflictError):
        storage.save_preset(preset=_make_preset(description="stale client"), expected_version=created.version)

    loaded = storage.load_preset("nightly")
    assert loaded is not None
    assert loaded.preset.description == "edited by hand"


def test_stale_save_does_not_overwrite_the_winner(tmp_path: Path) -> None:
    """Test that a rejected save leaves stored content untouched."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    storage.save_preset(preset=_make_preset(description="original"), expected_version=None)

    with pytest.raises(ScenarioPresetConflictError):
        storage.save_preset(preset=_make_preset(description="clobber"), expected_version="not_the_stored_version")

    loaded = storage.load_preset("nightly")
    assert loaded is not None
    assert loaded.preset.description == "original"


def test_create_over_existing_name_is_rejected(tmp_path: Path) -> None:
    """Test that creating a preset that already exists is a conflict, not an overwrite."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    created = storage.save_preset(preset=_make_preset(), expected_version=None)

    with pytest.raises(ScenarioPresetConflictError) as error:
        storage.save_preset(preset=_make_preset(description="second"), expected_version=None)

    assert error.value.expected_version is None
    assert error.value.actual_version == created.version


def test_create_over_malformed_file_is_rejected(tmp_path: Path) -> None:
    """Test that an unreadable file still blocks a create, so hand-written content is not discarded."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    (tmp_path / "nightly.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ScenarioPresetConflictError):
        storage.save_preset(preset=_make_preset(), expected_version=None)

    assert (tmp_path / "nightly.json").read_text(encoding="utf-8") == "{not json"


def test_update_of_missing_preset_is_rejected(tmp_path: Path) -> None:
    """Test that updating a preset deleted by someone else is a conflict."""
    storage = ScenarioPresetStorage(source=str(tmp_path))

    with pytest.raises(ScenarioPresetConflictError) as error:
        storage.save_preset(preset=_make_preset(), expected_version="some_version")

    assert error.value.actual_version is None


def test_default_source_is_under_the_configuration_directory(tmp_path: Path) -> None:
    """Test that omitting the source stores presets under the PyRIT configuration directory."""
    with patch("pyrit.common.path.CONFIGURATION_DIRECTORY_PATH", tmp_path):
        storage = ScenarioPresetStorage()
        storage.save_preset(preset=_make_preset(), expected_version=None)

    assert (tmp_path / "scenario_presets" / "nightly.json").is_file()


def test_presets_written_by_another_process_are_visible(tmp_path: Path) -> None:
    """Test that reads go through to the source, so a shared directory is never served stale."""
    reader = ScenarioPresetStorage(source=str(tmp_path))
    assert reader.load_preset("nightly") is None

    ScenarioPresetStorage(source=str(tmp_path)).save_preset(preset=_make_preset(), expected_version=None)

    assert reader.load_preset("nightly") is not None
    assert list(reader.list_presets()) == ["nightly"]


def test_presets_deleted_by_another_process_disappear(tmp_path: Path) -> None:
    """Test that a preset removed outside this instance stops resolving."""
    reader = ScenarioPresetStorage(source=str(tmp_path))
    reader.save_preset(preset=_make_preset(), expected_version=None)

    ScenarioPresetStorage(source=str(tmp_path)).delete_preset("nightly")

    assert reader.load_preset("nightly") is None


def test_load_missing_preset_returns_none(tmp_path: Path) -> None:
    """Test that an absent preset loads as None rather than raising."""
    storage = ScenarioPresetStorage(source=str(tmp_path))

    assert storage.load_preset("absent") is None


def test_list_and_delete_presets(tmp_path: Path) -> None:
    """Test the storage lifecycle for multiple presets."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    storage.save_preset(preset=_make_preset(name="nightly"), expected_version=None)
    storage.save_preset(preset=_make_preset(name="weekly"), expected_version=None)

    assert sorted(storage.list_presets()) == ["nightly", "weekly"]
    assert storage.delete_preset("nightly") is True
    assert sorted(storage.list_presets()) == ["weekly"]


def test_delete_missing_preset_is_not_an_error(tmp_path: Path) -> None:
    """Test that deleting an absent preset reports absence rather than raising."""
    storage = ScenarioPresetStorage(source=str(tmp_path))

    assert storage.delete_preset("absent") is False


def test_delete_reports_the_outcome_of_the_delete_itself(tmp_path: Path) -> None:
    """Test that only the delete which removed the document reports that it did.

    Two callers racing to remove the same preset must not both be told they removed it,
    so the answer has to come from the delete rather than from a preceding existence
    check.
    """
    storage = ScenarioPresetStorage(source=str(tmp_path))
    storage.save_preset(preset=_make_preset(name="nightly"), expected_version=None)

    assert storage.delete_preset("nightly") is True
    assert storage.delete_preset("nightly") is False


@pytest.mark.parametrize("name", ["../victim", "..\\victim", "nested/victim", "Nightly", "night-ly", ""])
def test_document_operations_reject_illegal_names(tmp_path: Path, name: str) -> None:
    """Test that a name which would escape the configured source is refused on every path."""
    source = tmp_path / "presets"
    source.mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text("do not touch", encoding="utf-8")
    storage = ScenarioPresetStorage(source=str(source))

    with pytest.raises(ValueError, match="Invalid registry name"):
        storage.load_preset(name)
    with pytest.raises(ValueError, match="Invalid registry name"):
        storage.delete_preset(name)
    with pytest.raises(ValueError, match="Invalid registry name"):
        storage.get_preset_source(name)

    assert victim.read_text(encoding="utf-8") == "do not touch"


def test_malformed_preset_is_skipped_not_fatal(tmp_path: Path) -> None:
    """Test that one unparseable file does not prevent the rest of the library from loading."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    storage.save_preset(preset=_make_preset(name="valid"), expected_version=None)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "wrong_shape.json").write_text('["a list"]', encoding="utf-8")

    presets = storage.list_presets()

    assert sorted(presets) == ["valid"]
    assert storage.load_preset("broken") is None


def test_document_name_overrides_payload_name(tmp_path: Path) -> None:
    """Test that the file name is authoritative, so a load and its later save agree on the key."""
    (tmp_path / "actual_key.json").write_text(
        json.dumps({"name": "different", "scenario_name": "foundry.red_team_agent"}),
        encoding="utf-8",
    )
    storage = ScenarioPresetStorage(source=str(tmp_path))

    loaded = storage.load_preset("actual_key")

    assert loaded is not None
    assert loaded.preset.name == "actual_key"
    assert sorted(storage.list_presets()) == ["actual_key"]


def test_local_storage_returns_preset_path(tmp_path: Path) -> None:
    """Test resolving the displayed path for a local preset."""
    storage = ScenarioPresetStorage(source=str(tmp_path))

    assert storage.get_preset_source("nightly") == str(tmp_path / "nightly.json")
    assert storage.display_source == str(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "https://account.blob.attacker.example/presets",
        "https://user@account.blob.core.windows.net/presets",
        "https://blob.core.windows.net/presets",
    ],
)
def test_blob_storage_rejects_untrusted_authorities(source: str) -> None:
    """Test rejecting Blob lookalikes before Azure credentials are acquired."""
    with pytest.raises(ValueError, match="local directory or Azure Blob container URI"):
        ScenarioPresetStorage(source=source)


def test_blob_storage_round_trips_and_ignores_other_extensions() -> None:
    """Test container storage operations and that only JSON documents are listed."""
    document = json.dumps({"name": "nightly", "scenario_name": "foundry.red_team_agent"})
    client = MagicMock()
    client.__enter__.return_value = client
    client.list_blobs.return_value = [
        SimpleNamespace(name="nightly.json"),
        SimpleNamespace(name="notes.txt"),
        SimpleNamespace(name="archive/ignored.json"),
    ]
    client.download_blob.return_value.readall.return_value = document.encode("utf-8")
    source = "https://account.blob.core.windows.net/presets?sp=rwd&sig=secret"
    storage = ScenarioPresetStorage(source=source)

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        presets = storage.list_presets()
        saved = storage.save_preset(
            preset=_make_preset(description="updated"), expected_version=presets["nightly"].version
        )

    assert sorted(presets) == ["nightly"]
    assert saved.version != presets["nightly"].version
    assert storage.display_source == "https://account.blob.core.windows.net/presets"
    assert client.upload_blob.call_args.kwargs["name"] == "nightly.json"


def test_blob_storage_reads_missing_document_as_none() -> None:
    """Test that a missing blob loads as None instead of propagating an Azure error."""
    from azure.core.exceptions import ResourceNotFoundError

    client = MagicMock()
    client.__enter__.return_value = client
    client.download_blob.side_effect = ResourceNotFoundError("missing")
    storage = ScenarioPresetStorage(source="https://account.blob.core.windows.net/presets?sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        assert storage.load_preset("absent") is None


def test_blob_delete_reports_that_it_removed_the_document() -> None:
    """Test that deleting an existing blob reports the removal."""
    client = MagicMock()
    client.__enter__.return_value = client
    storage = ScenarioPresetStorage(source="https://account.blob.core.windows.net/presets?sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        assert storage.delete_preset("nightly") is True

    client.delete_blob.assert_called_once_with("nightly.json")


def test_blob_delete_of_a_missing_document_reports_absence_instead_of_raising() -> None:
    """Test that deleting a blob someone else already removed reports absence."""
    from azure.core.exceptions import ResourceNotFoundError

    client = MagicMock()
    client.__enter__.return_value = client
    client.delete_blob.side_effect = ResourceNotFoundError("missing")
    storage = ScenarioPresetStorage(source="https://account.blob.core.windows.net/presets?sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        assert storage.delete_preset("absent") is False


def test_name_is_not_written_into_the_document(tmp_path: Path) -> None:
    """Test that the storage key appears only in the file name, so no hand-edit can contradict it."""
    storage = ScenarioPresetStorage(source=str(tmp_path))

    storage.save_preset(preset=_make_preset(), expected_version=None)

    assert "name" not in json.loads((tmp_path / "nightly.json").read_text(encoding="utf-8"))
    loaded = storage.load_preset("nightly")
    assert loaded is not None
    assert loaded.preset.name == "nightly"


def test_misspelled_key_is_skipped_not_silently_dropped(tmp_path: Path) -> None:
    """Test that a typo in a hand-edited file is refused rather than quietly using the scenario default."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    (tmp_path / "nightly.json").write_text(
        json.dumps({"scenario_name": "foundry.red_team_agent", "techinques": ["crescendo"]}),
        encoding="utf-8",
    )

    assert storage.load_preset("nightly") is None
    assert storage.list_presets() == {}


def test_malformed_document_can_be_overwritten_through_its_version(tmp_path: Path) -> None:
    """Test that a file which cannot be parsed still has a recovery path rather than burning the name."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    (tmp_path / "nightly.json").write_text("{not json", encoding="utf-8")

    version = storage.get_preset_version("nightly")
    assert version is not None

    saved = storage.save_preset(preset=_make_preset(), expected_version=version)

    assert saved.preset.name == "nightly"
    assert storage.load_preset("nightly") is not None


def test_get_preset_version_returns_none_for_absent_document(tmp_path: Path) -> None:
    """Test that an absent document reports no version, so a create still reads as a create."""
    storage = ScenarioPresetStorage(source=str(tmp_path))

    assert storage.get_preset_version("nightly") is None


def test_listing_skips_documents_it_cannot_address(tmp_path: Path) -> None:
    """Test that a stray file whose stem is not a legal name does not fail the whole listing."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    storage.save_preset(preset=_make_preset(), expected_version=None)
    (tmp_path / "My-Preset.json").write_text(json.dumps({"scenario_name": "foundry.red_team_agent"}), encoding="utf-8")

    assert sorted(storage.list_presets()) == ["nightly"]


def test_document_that_is_not_text_does_not_hide_valid_presets(tmp_path: Path) -> None:
    """Test that a file which is not UTF-8 is skipped rather than failing the whole listing."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    storage.save_preset(preset=_make_preset(), expected_version=None)
    (tmp_path / "broken.json").write_bytes(b"\xff\xfe not utf-8")

    assert sorted(storage.list_presets()) == ["nightly"]
    assert storage.load_preset("broken") is None


def test_unreadable_document_with_an_ignorable_name_is_never_read(tmp_path: Path) -> None:
    """Test that a name the storage would refuse is skipped before its bytes are ever touched."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    storage.save_preset(preset=_make_preset(), expected_version=None)
    (tmp_path / "My-Preset.json").write_bytes(b"\xff")

    assert sorted(storage.list_presets()) == ["nightly"]


def test_version_is_readable_for_a_document_that_is_not_text(tmp_path: Path) -> None:
    """Test that the recovery path reaches a file too broken to decode, not just too broken to parse."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    (tmp_path / "nightly.json").write_bytes(b"\xff\xfe not utf-8")

    version = storage.get_preset_version("nightly")
    assert version is not None

    storage.save_preset(preset=_make_preset(), expected_version=version)

    loaded = storage.load_preset("nightly")
    assert loaded is not None


def test_version_matches_the_bytes_on_disk(tmp_path: Path) -> None:
    """Test that the token describes stored bytes, so a later read cannot disagree with the save."""
    storage = ScenarioPresetStorage(source=str(tmp_path))

    saved = storage.save_preset(preset=_make_preset(), expected_version=None)

    assert saved.version == hashlib.sha256((tmp_path / "nightly.json").read_bytes()).hexdigest()
    assert storage.get_preset_version("nightly") == saved.version


def test_failed_write_preserves_the_previous_preset(tmp_path: Path) -> None:
    """Test that a write that dies partway leaves the stored preset readable instead of empty."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    first = storage.save_preset(preset=_make_preset(description="first"), expected_version=None)

    def fail(source: object, target: object) -> None:
        raise OSError(28, "No space left on device")

    with patch("os.replace", fail):
        with pytest.raises(OSError):
            storage.save_preset(preset=_make_preset(description="second"), expected_version=first.version)

    loaded = storage.load_preset("nightly")
    assert loaded is not None
    assert loaded.preset.description == "first"
    assert loaded.version == first.version
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_reader_sees_the_previous_document_until_the_write_completes(tmp_path: Path) -> None:
    """Test that an update never truncates the destination, so a reader sees the old or new document."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    first = storage.save_preset(preset=_make_preset(description="first"), expected_version=None)
    observed: list[str] = []
    listed: list[list[str]] = []
    real_replace = os.replace

    def observe_then_replace(source: object, target: object) -> None:
        observed.append((tmp_path / "nightly.json").read_text(encoding="utf-8"))
        listed.append(sorted(path.name for path in tmp_path.glob("*.json")))
        real_replace(source, target)  # type: ignore[arg-type]

    with patch("os.replace", observe_then_replace):
        storage.save_preset(preset=_make_preset(description="second"), expected_version=first.version)

    assert json.loads(observed[0])["description"] == "first"
    assert listed == [["nightly.json"]]
    loaded = storage.load_preset("nightly")
    assert loaded is not None
    assert loaded.preset.description == "second"


def test_blob_listing_skips_a_document_that_is_not_text() -> None:
    """Test that one undecodable blob does not hide every other stored preset."""
    document = json.dumps({"scenario_name": "foundry.red_team_agent"}).encode("utf-8")
    client = MagicMock()
    client.__enter__.return_value = client
    client.list_blobs.return_value = [SimpleNamespace(name="broken.json"), SimpleNamespace(name="nightly.json")]
    client.download_blob.side_effect = lambda blob_name: SimpleNamespace(
        readall=lambda: b"\xff\xfe" if blob_name == "broken.json" else document
    )
    storage = ScenarioPresetStorage(source="https://account.blob.core.windows.net/presets?sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        presets = storage.list_presets()

    assert sorted(presets) == ["nightly"]


def test_blob_listing_does_not_download_a_name_it_would_refuse() -> None:
    """Test that an ignorable blob name is filtered before the download that could fail on it."""
    document = json.dumps({"scenario_name": "foundry.red_team_agent"}).encode("utf-8")
    client = MagicMock()
    client.__enter__.return_value = client
    client.list_blobs.return_value = [SimpleNamespace(name="My-Preset.json"), SimpleNamespace(name="nightly.json")]
    client.download_blob.return_value.readall.return_value = document
    storage = ScenarioPresetStorage(source="https://account.blob.core.windows.net/presets?sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        presets = storage.list_presets()

    assert sorted(presets) == ["nightly"]
    assert [call.args[0] for call in client.download_blob.call_args_list] == ["nightly.json"]


def test_a_second_writer_cannot_enter_while_a_save_is_in_flight(tmp_path: Path) -> None:
    """Test that the version check and the write cannot be separated by a competing writer."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    first = storage.save_preset(preset=_make_preset(description="first"), expected_version=None)
    competitor = ScenarioPresetStorage(source=str(tmp_path))
    refusals: list[str] = []
    real_replace_file = ScenarioPresetStorage._replace_file

    def replace_once_a_competitor_has_tried(*, path: Path, content: bytes) -> None:
        with pytest.raises(TimeoutError) as error:
            competitor.save_preset(preset=_make_preset(description="racer"), expected_version=first.version)
        refusals.append(str(error.value))
        real_replace_file(path=path, content=content)

    with patch.object(ScenarioPresetStorage, "LOCK_TIMEOUT_SECONDS", 0.1):
        with patch.object(ScenarioPresetStorage, "_replace_file", staticmethod(replace_once_a_competitor_has_tried)):
            storage.save_preset(preset=_make_preset(description="second"), expected_version=first.version)

    assert len(refusals) == 1
    loaded = storage.load_preset("nightly")
    assert loaded is not None
    assert loaded.preset.description == "second"


def test_a_delete_cannot_enter_while_a_save_is_in_flight(tmp_path: Path) -> None:
    """Test that a delete landing inside a save is refused rather than undone by that save."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    first = storage.save_preset(preset=_make_preset(description="first"), expected_version=None)
    competitor = ScenarioPresetStorage(source=str(tmp_path))
    refusals: list[str] = []
    real_replace_file = ScenarioPresetStorage._replace_file

    def replace_once_a_deleter_has_tried(*, path: Path, content: bytes) -> None:
        with pytest.raises(TimeoutError) as error:
            competitor.delete_preset("nightly")
        refusals.append(str(error.value))
        real_replace_file(path=path, content=content)

    with patch.object(ScenarioPresetStorage, "LOCK_TIMEOUT_SECONDS", 0.1):
        with patch.object(ScenarioPresetStorage, "_replace_file", staticmethod(replace_once_a_deleter_has_tried)):
            storage.save_preset(preset=_make_preset(description="second"), expected_version=first.version)

    assert len(refusals) == 1
    loaded = storage.load_preset("nightly")
    assert loaded is not None
    assert loaded.preset.description == "second"


def test_local_update_of_a_deleted_preset_is_a_conflict(tmp_path: Path) -> None:
    """Test that the local backend reports a removed preset the same way the blob backend does."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    first = storage.save_preset(preset=_make_preset(description="first"), expected_version=None)
    assert storage.delete_preset("nightly")

    with pytest.raises(ScenarioPresetConflictError, match="no longer exists") as error:
        storage.save_preset(preset=_make_preset(description="second"), expected_version=first.version)

    assert error.value.actual_version is None
    assert storage.load_preset("nightly") is None


def test_a_completed_save_releases_its_lock(tmp_path: Path) -> None:
    """Test that a save leaves nothing behind that would block the next writer."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    first = storage.save_preset(preset=_make_preset(description="first"), expected_version=None)

    with patch.object(ScenarioPresetStorage, "LOCK_TIMEOUT_SECONDS", 0.1):
        saved = storage.save_preset(preset=_make_preset(description="second"), expected_version=first.version)

    assert saved.preset.description == "second"


def test_a_lock_held_by_a_live_writer_is_respected(tmp_path: Path) -> None:
    """Test that a writer still holding the lock is waited for rather than overridden."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    held_lock = tmp_path / ".nightly.json.lock"
    descriptor = os.open(held_lock, os.O_CREAT | os.O_RDWR)
    assert _try_acquire_exclusive_lock(descriptor)

    try:
        with patch.object(ScenarioPresetStorage, "LOCK_TIMEOUT_SECONDS", 0.1):
            with pytest.raises(TimeoutError, match="nightly.json.lock"):
                storage.save_preset(preset=_make_preset(), expected_version=None)
    finally:
        _release_exclusive_lock(descriptor)
        os.close(descriptor)

    assert storage.load_preset("nightly") is None


def test_a_lock_left_by_a_dead_writer_is_reclaimed(tmp_path: Path) -> None:
    """Test that a writer that died holding the lock does not make a preset permanently unwritable."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    first = storage.save_preset(preset=_make_preset(description="first"), expected_version=None)
    # A writer that died leaves the file behind but not the lock, which the kernel released.
    (tmp_path / ".nightly.json.lock").write_text("4242", encoding="utf-8")

    with patch.object(ScenarioPresetStorage, "LOCK_TIMEOUT_SECONDS", 0.1):
        saved = storage.save_preset(preset=_make_preset(description="second"), expected_version=first.version)

    assert saved.preset.description == "second"


def test_lock_files_are_not_listed_as_presets(tmp_path: Path) -> None:
    """Test that the lock taken during a write can never be read back as a stored preset."""
    storage = ScenarioPresetStorage(source=str(tmp_path))
    storage.save_preset(preset=_make_preset(), expected_version=None)
    (tmp_path / ".nightly.json.lock").write_text("4242", encoding="utf-8")

    assert sorted(storage.list_presets()) == ["nightly"]


def test_blob_update_is_conditional_on_the_version_that_was_read() -> None:
    """Test that a blob update asks the service to reject a write over content that moved."""
    from azure.core import MatchConditions

    document = json.dumps({"scenario_name": "foundry.red_team_agent"}).encode("utf-8")
    client = MagicMock()
    client.__enter__.return_value = client
    client.download_blob.return_value.readall.return_value = document
    client.download_blob.return_value.properties.etag = '"0x8DCAFE"'
    storage = ScenarioPresetStorage(source="https://account.blob.core.windows.net/presets?sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        storage.save_preset(
            preset=_make_preset(description="updated"),
            expected_version=hashlib.sha256(document).hexdigest(),
        )

    assert client.upload_blob.call_args.kwargs["etag"] == '"0x8DCAFE"'
    assert client.upload_blob.call_args.kwargs["match_condition"] is MatchConditions.IfNotModified


def test_blob_create_refuses_to_overwrite_a_document_another_writer_just_created() -> None:
    """Test that a container create cannot clobber a preset that appeared after the check."""
    from azure.core.exceptions import ResourceExistsError

    document = json.dumps({"scenario_name": "foundry.red_team_agent"}).encode("utf-8")
    client = MagicMock()
    client.__enter__.return_value = client
    client.upload_blob.side_effect = ResourceExistsError("exists")
    client.download_blob.return_value.readall.return_value = document
    storage = ScenarioPresetStorage(source="https://account.blob.core.windows.net/presets?sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        with pytest.raises(ScenarioPresetConflictError, match="it already exists") as error:
            storage.save_preset(preset=_make_preset(), expected_version=None)

    assert client.upload_blob.call_args.kwargs["overwrite"] is False
    assert error.value.actual_version == hashlib.sha256(document).hexdigest()


def test_blob_update_rejected_by_the_service_is_reported_as_a_conflict() -> None:
    """Test that losing the precondition race surfaces as a conflict, not a raw Azure error."""
    from azure.core.exceptions import ResourceModifiedError

    document = json.dumps({"scenario_name": "foundry.red_team_agent"}).encode("utf-8")
    client = MagicMock()
    client.__enter__.return_value = client
    client.download_blob.return_value.readall.return_value = document
    client.upload_blob.side_effect = ResourceModifiedError("changed")
    storage = ScenarioPresetStorage(source="https://account.blob.core.windows.net/presets?sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        with pytest.raises(ScenarioPresetConflictError, match="changed by someone else"):
            storage.save_preset(preset=_make_preset(), expected_version=hashlib.sha256(document).hexdigest())


def test_blob_update_of_changed_content_never_reaches_upload() -> None:
    """Test that a stale container update is refused before any write is attempted."""
    document = json.dumps({"scenario_name": "foundry.red_team_agent"}).encode("utf-8")
    client = MagicMock()
    client.__enter__.return_value = client
    client.download_blob.return_value.readall.return_value = document
    storage = ScenarioPresetStorage(source="https://account.blob.core.windows.net/presets?sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        with pytest.raises(ScenarioPresetConflictError, match="changed by someone else"):
            storage.save_preset(preset=_make_preset(), expected_version="stale_version")

    client.upload_blob.assert_not_called()


def test_blob_update_of_a_deleted_document_is_a_conflict() -> None:
    """Test that updating a blob someone else removed reports the deletion instead of recreating it."""
    from azure.core.exceptions import ResourceNotFoundError

    client = MagicMock()
    client.__enter__.return_value = client
    client.download_blob.side_effect = ResourceNotFoundError("missing")
    storage = ScenarioPresetStorage(source="https://account.blob.core.windows.net/presets?sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        with pytest.raises(ScenarioPresetConflictError, match="no longer exists") as error:
            storage.save_preset(preset=_make_preset(), expected_version="some_version")

    assert error.value.actual_version is None
    client.upload_blob.assert_not_called()
