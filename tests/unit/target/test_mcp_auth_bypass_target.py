# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest

from pyrit.prompt_target.http_target.mcp_auth_bypass_target import MCPAuthBypassTarget


def make_mock_request(text="test prompt"):
    req = MagicMock()
    req.converted_value = text
    return req


class TestMCPAuthBypassTargetInit:
    def test_valid_bypass_technique(self, sqlite_instance):
        target = MCPAuthBypassTarget(mcp_server_url="http://localhost:8080", bypass_technique="no_auth")
        assert target.bypass_technique == "no_auth"

    def test_invalid_bypass_technique_raises(self, sqlite_instance):
        with pytest.raises(ValueError, match="Invalid bypass_technique"):
            MCPAuthBypassTarget(mcp_server_url="http://localhost:8080", bypass_technique="invalid")

    def test_default_values(self, sqlite_instance):
        target = MCPAuthBypassTarget(mcp_server_url="http://localhost:8080")
        assert target.bypass_technique == "no_auth"
        assert target.mcp_method == "tools/list"
        assert target.timeout == 30


class TestMCPAuthBypassTargetHeaders:
    def test_no_auth_has_no_authorization_header(self, sqlite_instance):
        target = MCPAuthBypassTarget(mcp_server_url="http://localhost:8080", bypass_technique="no_auth")
        assert "Authorization" not in target._build_headers()

    def test_empty_token_has_empty_bearer(self, sqlite_instance):
        target = MCPAuthBypassTarget(mcp_server_url="http://localhost:8080", bypass_technique="empty_token")
        assert target._build_headers()["Authorization"] == "Bearer "

    def test_malformed_token_has_invalid_jwt(self, sqlite_instance):
        target = MCPAuthBypassTarget(mcp_server_url="http://localhost:8080", bypass_technique="malformed_token")
        assert "invalid" in target._build_headers()["Authorization"]

    def test_role_escalation_has_tampered_token(self, sqlite_instance):
        target = MCPAuthBypassTarget(mcp_server_url="http://localhost:8080", bypass_technique="role_escalation")
        assert "eyJhbGciOiJub25lIn0" in target._build_headers()["Authorization"]


class TestMCPAuthBypassTargetEvaluate:
    def test_200_detected_as_vulnerability(self, sqlite_instance):
        target = MCPAuthBypassTarget(mcp_server_url="http://localhost:8080", bypass_technique="no_auth")
        assert "VULNERABILITY DETECTED" in target._evaluate_response(200, "ok")

    def test_401_detected_as_secure(self, sqlite_instance):
        target = MCPAuthBypassTarget(mcp_server_url="http://localhost:8080", bypass_technique="no_auth")
        assert "SECURE" in target._evaluate_response(401, "Unauthorized")

    def test_403_detected_as_secure(self, sqlite_instance):
        target = MCPAuthBypassTarget(mcp_server_url="http://localhost:8080", bypass_technique="no_auth")
        assert "SECURE" in target._evaluate_response(403, "Forbidden")

    def test_500_flagged_for_investigation(self, sqlite_instance):
        target = MCPAuthBypassTarget(mcp_server_url="http://localhost:8080", bypass_technique="no_auth")
        assert "INVESTIGATE" in target._evaluate_response(500, "Server Error")
