# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for pyrit.cli._config_reader.
"""

from unittest.mock import patch

import pytest

from pyrit.cli import _config_reader
from pyrit.cli._config_reader import (
    DEFAULT_SERVER_URL,
    ConfigError,
    read_server_url,
    warn_on_client_ignored_blocks,
)


def test_default_server_url_constant():
    assert DEFAULT_SERVER_URL == "http://localhost:8000"


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


def test_warn_on_client_ignored_blocks_prints_deprecation(tmp_path, capsys):
    cfg = tmp_path / "conf.yaml"
    cfg.write_text("scenario:\n  name: test\n", encoding="utf-8")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        warn_on_client_ignored_blocks(config_file=cfg)
    assert "Deprecation" in capsys.readouterr().out


def test_warn_on_client_ignored_blocks_raises_on_malformed_yaml(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(": :\nnot yaml: [unbalanced\n", encoding="utf-8")
    with patch.object(_config_reader, "_DEFAULT_CONFIG_FILE", tmp_path / "missing.yaml"):
        with pytest.raises(ConfigError, match="not valid YAML"):
            warn_on_client_ignored_blocks(config_file=bad)
