# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for pyrit.cli._config_reader.
"""

from unittest.mock import patch

import pytest

from pyrit.cli import _config_reader
from pyrit.cli._config_reader import (
    DEFAULT_AUTH_MODE,
    DEFAULT_SERVER_STARTUP_TIMEOUT,
    DEFAULT_SERVER_URL,
    ConfigError,
    ServerSettings,
    read_server_settings,
    read_server_url,
    validate_client_config,
)


def test_default_server_url_constant():
    assert DEFAULT_SERVER_URL == "http://localhost:8000"
    assert DEFAULT_SERVER_STARTUP_TIMEOUT == 120.0
    assert DEFAULT_AUTH_MODE == "auto"


def test_read_server_url_returns_none_when_no_files(tmp_path):
    nonexistent = tmp_path / "missing.yaml"
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing_default.yaml"):
        assert read_server_url(config_file=nonexistent) is None


def test_read_server_url_reads_from_default_when_no_overlay(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text("server:\n  url: http://default-host:9000\n")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", default):
        assert read_server_url(config_file=None) == "http://default-host:9000"


def test_read_server_url_overlay_overrides_default(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text("server:\n  url: http://default-host:9000\n")
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("server:\n  url: http://overlay-host:5000\n")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", default):
        assert read_server_url(config_file=overlay) == "http://overlay-host:5000"


def test_read_server_url_overlay_missing_field_falls_back(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text("server:\n  url: http://default-host:9000\n")
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("other_block: {}\n")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", default):
        # Overlay doesn't have server.url, so default wins.
        assert read_server_url(config_file=overlay) == "http://default-host:9000"


def test_read_server_url_strips_whitespace(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text("server:\n  url: '  http://padded:9000  '\n")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", default):
        assert read_server_url(config_file=None) == "http://padded:9000"


def test_read_server_url_non_string_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("server:\n  url: 12345\n")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        with pytest.raises(ConfigError, match="server.url"):
            read_server_url(config_file=bad)


def test_read_server_url_server_block_not_mapping_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("server: http://oops:9000\n")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        with pytest.raises(ConfigError, match="'server' must be a mapping"):
            read_server_url(config_file=bad)


def test_read_server_url_handles_malformed_yaml(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(": :\nnot yaml: [unbalanced\n")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        with pytest.raises(ConfigError, match="not valid YAML"):
            read_server_url(config_file=bad)


def test_read_server_url_handles_non_dict_root(tmp_path):
    odd = tmp_path / "odd.yaml"
    odd.write_text("- 1\n- 2\n- 3\n")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        with pytest.raises(ConfigError, match="top-level mapping"):
            read_server_url(config_file=odd)


def test_read_server_url_empty_file_returns_none(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        assert read_server_url(config_file=empty) is None


def test_read_server_url_empty_string_treated_as_missing(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("server:\n  url: ''\n")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        assert read_server_url(config_file=empty) is None


def test_read_server_settings_returns_defaults_when_no_files(tmp_path):
    nonexistent = tmp_path / "missing.yaml"
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing_default.yaml"):
        assert read_server_settings(config_file=nonexistent) == ServerSettings(
            url=None,
            startup_timeout=120.0,
        )


def test_read_server_settings_overlay_overrides_startup_timeout(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text("server:\n  url: http://default:8000\n  startup_timeout: 180\n", encoding="utf-8")
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("server:\n  url: http://overlay:9000\n  startup_timeout: 45\n", encoding="utf-8")

    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", default):
        assert read_server_settings(config_file=overlay) == ServerSettings(
            url="http://overlay:9000",
            startup_timeout=45.0,
        )


def test_read_server_settings_overlay_overrides_auth_mode(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text("server:\n  url: http://default:8000\n  auth_mode: auto\n", encoding="utf-8")
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("server:\n  auth_mode: azure_cli\n", encoding="utf-8")

    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", default):
        assert read_server_settings(config_file=overlay) == ServerSettings(
            url="http://default:8000",
            auth_mode="azure_cli",
        )


def test_read_server_settings_overlay_timeout_preserves_default_url(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text("server:\n  url: http://default:8000\n  startup_timeout: 180\n", encoding="utf-8")
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("server:\n  startup_timeout: 45\n", encoding="utf-8")

    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", default):
        assert read_server_settings(config_file=overlay) == ServerSettings(
            url="http://default:8000",
            startup_timeout=45.0,
        )


def test_read_server_settings_overlay_url_preserves_default_timeout(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text("server:\n  url: http://default:8000\n  startup_timeout: 180\n", encoding="utf-8")
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("server:\n  url: http://overlay:9000\n", encoding="utf-8")

    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", default):
        assert read_server_settings(config_file=overlay) == ServerSettings(
            url="http://overlay:9000",
            startup_timeout=180.0,
        )


def test_read_server_settings_empty_overlay_block_preserves_defaults(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text("server:\n  url: http://default:8000\n  startup_timeout: 180\n", encoding="utf-8")
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("server: {}\n", encoding="utf-8")

    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", default):
        assert read_server_settings(config_file=overlay) == ServerSettings(
            url="http://default:8000",
            startup_timeout=180.0,
        )


def test_read_server_settings_null_overlay_block_resets_defaults(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text("server:\n  url: http://default:8000\n  startup_timeout: 180\n", encoding="utf-8")
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("server: null\n", encoding="utf-8")

    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", default):
        assert read_server_settings(config_file=overlay) == ServerSettings()


@pytest.mark.parametrize("startup_timeout", ["slow", True, 0, -1, float("inf")])
def test_read_server_settings_rejects_invalid_startup_timeout(tmp_path, startup_timeout):
    bad = tmp_path / "bad.yaml"
    bad.write_text(f"server:\n  startup_timeout: {startup_timeout}\n", encoding="utf-8")

    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        with pytest.raises(ConfigError, match="server.startup_timeout"):
            read_server_settings(config_file=bad)


@pytest.mark.parametrize("auth_mode", ["interactive", "", 123, True])
def test_read_server_settings_rejects_invalid_auth_mode(tmp_path, auth_mode):
    bad = tmp_path / "bad.yaml"
    bad.write_text(f"server:\n  auth_mode: {auth_mode}\n", encoding="utf-8")

    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        with pytest.raises(ConfigError, match="server.auth_mode"):
            read_server_settings(config_file=bad)


def test_validate_client_config_rejects_removed_scenario_block(tmp_path):
    cfg = tmp_path / "conf.yaml"
    cfg.write_text("scenario:\n  name: test\n", encoding="utf-8")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        with pytest.raises(ConfigError, match="'scenario' is no longer supported"):
            validate_client_config(config_file=cfg)


def test_validate_client_config_raises_on_malformed_yaml(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(": :\nnot yaml: [unbalanced\n", encoding="utf-8")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        with pytest.raises(ConfigError, match="not valid YAML"):
            validate_client_config(config_file=bad)
