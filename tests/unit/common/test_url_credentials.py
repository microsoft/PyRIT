# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import time

import pytest

from pyrit.common.url_credentials import UrlCredentials


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pw@api.test/",
        "wss://ghp_token@api.test/realtime",
        "https://api.test/chat?api-key=abc",
        "https://api.test/chat?apiKey=abc",
        "https://account.blob.core.windows.net/container?sv=2024&sig=abc",
        "https://api.test/callback#access_token=abc",
        "ftp://user:pw@files.test/",
        "https://api.test/chat?auth_token=abc",
        "https://api.test/chat?refresh_token=abc",
        "https://api.test/chat?api_token=abc",
        "https://api.test/chat?cOde=abc",
        "https://api.test/chat#aPi_kEy=abc",
        "https://api.test/chat?xapikey=abc",
        "https://api.test/chat?webhook_secret=abc",
        "https://alice@example.com:pw@api.test/",
        "https://user:abc/def==@api.test/x",
        "https://user:pw#123@api.test/x",
        "https://alice@example.com:pw/abc@api.test/x",
        "https://api.test//user:pw@evil.test/x",
        "https://proxy.test/v1?upstream=https://user:pw@up.test/v1",
        "https://proxy.test/v1?upstream=https%3A%2F%2Fuser%3Apw%40up.test%2Fv1",
    ],
)
def test_has_credentials_finds_user_information_and_credential_parameters(url: str) -> None:
    assert UrlCredentials.has_credentials(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.test/openai?api-version=2024-10-21",
        "https://api.test/chat?key=",
        "https://api.test/login?reset_password=true&has_secret=False&forgot_password=1",
        "data:text/plain;base64,a2V5PXNlY3JldA==",
        "see https://user:pw@api.test/ for details",
        "not a url",
        "https://[::1",
        "https://api.test/users/alice@example.com/messages",
        "https://api.test:8443/users//@me?email=a@b.test",
        "https://proxy.test/v1?upstream=https://up.test/users/alice@contoso.com",
        "https://login.test/authorize?redirect_uri=https%3A%2F%2Fapp.test%2Fusers%2Falice%40contoso.com",
        "https://login.test/authorize?redirect_uri=https%3A%2F%2Fapp.test&login_hint=alice%40contoso.com",
        "https://login.test/authorize?redirect_uri=https://app.test&login_hint=alice@contoso.com",
        "https://login.test/authorize#redirect_uri=https://app.test&login_hint=alice@contoso.com",
        "https://login.test/authorize?redirect_uri=https://app.test/cb?state=abc&prompt=login",
        "https://login.test/authorize?redirect_uri=https://app.test/cb?code=&prompt=login",
        "https://proxy.test/v1?upstream=https://up.test&contact=alice@example.com",
        "https://outer.test/?text=1://owner@example.com",
        "https://api.test/search?q=error?code=404",
        "https://api.test/search?q=format;key=value",
        "https://search.test/?q=what%3Fcode%3Dexample",
        "https://search.test/?q=example%3Bcode%3Dsnippet",
        "https://docs.test/search?q=Explain%20%3Fcode%3D200",
        "https://proxy.test/redirect/https://app.test?login_hint=alice@contoso.com",
    ],
)
def test_has_credentials_ignores_urls_and_text_without_credentials(url: str) -> None:
    assert not UrlCredentials.has_credentials(url)


@pytest.mark.parametrize(
    ("url", "masked"),
    [
        ("https://user:pw@api.test/v1?code=abc&api-version=1", "https://***@api.test/v1?code=***&api-version=1"),
        ("https://api.test/v1#access_token=abc&state=x", "https://api.test/v1#access_token=***&state=x"),
        ("https://api.test/v1?cOde=abc&debug_password=off", "https://api.test/v1?cOde=***&debug_password=off"),
        (
            "https://s3.test/b/k?AWSAccessKeyId=AKIAEXAMPLE&Signature=abc&Expires=1",
            "https://s3.test/b/k?AWSAccessKeyId=***&Signature=***&Expires=1",
        ),
        ("https://api.test/v1?api-version=1", "https://api.test/v1?api-version=1"),
        ("not a url", "not a url"),
        ("https://api.test/v1?code=prefix#tail", "https://api.test/v1?code=***#***"),
        ("https://api.test/v1?api-version=1#access_token=tok", "https://api.test/v1?api-version=1#access_token=***"),
        ("https://api.test/v1?code=prefix#tail&state=x", "https://api.test/v1?code=***#***&state=x"),
        ("https://api.test/v1?code=prefix#tail==&state=x", "https://api.test/v1?code=***#***&state=x"),
        (
            "https://proxy.test/v1?upstream=https://user:pw@up.test/v1",
            "https://proxy.test/v1?upstream=https://***@up.test/v1",
        ),
    ],
)
def test_mask_hides_only_the_credentials(url: str, masked: str) -> None:
    assert UrlCredentials.mask(url) == masked


@pytest.mark.parametrize(
    "url",
    [
        "https://api.test/users//alice@example.com/messages",
        "https://api.test/mail/to//bob@contoso.com",
        "https://api.test/v1?next=https://nested.test/path//alice@release/file",
        "https://api.test/users/@me",
        "https://graph.microsoft.com/v1.0/users/alice@contoso.com/messages",
        "https://api.test?email=a@b.test",
        "https://[::1]:8443/users/@me",
    ],
)
def test_mask_keeps_a_url_without_credentials_whole(url: str) -> None:
    assert UrlCredentials.mask(url) == url


@pytest.mark.parametrize(
    ("url", "masked"),
    [
        ("https://alice@example.com:pw-123@api.test/f", "https://***@api.test/f"),
        ("https://api.test//alice@example.com:pw-123@evil.test/f", "https://api.test//***@evil.test/f"),
        ("https://user:pw/123@api.test/f", "https://***@api.test/f"),
        ("https://api.test//user:pw/123@evil.test/f", "https://api.test//***@evil.test/f"),
        ("https://alice@example.com:pw/123@api.test/f", "https://***@api.test/f"),
        ("https://api.test//alice@example.com:pw/123@evil.test/f", "https://api.test//***@evil.test/f"),
        (
            "https://api.test/v1?next=https://alice@example.com:pw-123@nested.test/f",
            "https://api.test/v1?next=https://***@nested.test/f",
        ),
    ],
)
def test_mask_hides_user_information_that_holds_characters_a_url_must_encode(url: str, masked: str) -> None:
    assert UrlCredentials.mask(url) == masked


@pytest.mark.parametrize(
    ("url", "masked"),
    [
        ("https://api.testHTTPS://user:pw-123@api.test/f", "https://api.testHTTPS://***@api.test/f"),
        ("https://api.test//user:pw-123@api.test/f", "https://api.test//***@api.test/f"),
        ("https://user:pw-123@api.test\uff1a443/f?code=fn-123", "https://***@api.test\uff1a443/f?code=***"),
    ],
)
def test_mask_hides_the_credentials_of_a_url_inside_a_path_or_one_its_parser_rejects(url: str, masked: str) -> None:
    assert UrlCredentials.mask(url) == masked


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        (
            "Invalid port in HTTP destination: https://api.test:99999f?code=fn-123&api-version=1",
            "Invalid port in HTTP destination: https://api.test:99999f?code=***&api-version=1",
        ),
        ("bad 'https://api.test/f#access_token=tok-123' here", "bad 'https://api.test/f#access_token=***' here"),
        ("https://user:pw-123@api.test\uff1a443/f", "https://***@api.test\uff1a443/f"),
        ("GET https://api.test/users/@me?api-version=1", "GET https://api.test/users/@me?api-version=1"),
        ("Bad URL (https://api.test/f?code=abc123).", "Bad URL (https://api.test/f?code=***)."),
        ("https://api.test/f?code=abc123, retry", "https://api.test/f?code=***, retry"),
        ("Invalid port: https://user:pw/1?2#3@api.test/f", "Invalid port: https://***@api.test/f"),
        ("Invalid port: https://api.test:99999/users/@me", "Invalid port: https://api.test:99999/users/@me"),
        (
            "GET https://api.test:8443/users/alice@contoso.com?email=a@b.test",
            "GET https://api.test:8443/users/alice@contoso.com?email=a@b.test",
        ),
    ],
)
def test_mask_text_hides_the_credentials_of_urls_as_written(text: str, masked: str) -> None:
    assert UrlCredentials.mask_text(text) == masked


@pytest.mark.parametrize("text", ["a" * 65_536, "?a" * 32_768, "h://u:p" * 9_362, "x://u:p/" + "a" * 65_536])
def test_mask_text_takes_time_linear_in_the_length_of_the_text(text: str) -> None:
    start = time.perf_counter()
    masked = UrlCredentials.mask_text(text)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5
    assert masked == text


@pytest.mark.parametrize(
    ("text", "user_information"),
    [
        ("user:pw@api.test/x", "user:pw"),
        ("alice@example.com:pw@api.test/x", "alice@example.com:pw"),
        ("user:abc/def==@api.test/x", "user:abc/def=="),
        ("user:pw?1#2@api.test/x", "user:pw?1#2"),
        ("alice@example.com:pw/abc@api.test/x", "alice@example.com:pw/abc"),
        ("user:pw@api.test:443/users/@me", "user:pw"),
        ("api.test/users/@me", None),
        ("api.test:99999/users/@me", None),
        ("[::1]:8443/users/@me", None),
        ("api.test:99999f?code=x", None),
        ("user:abc/x?next=https://nested@host/", None),
    ],
)
def test_user_information_reads_the_user_information_as_written(text: str, user_information: str | None) -> None:
    assert UrlCredentials.user_information(text) == user_information


def test_secrets_yield_credentials_as_written_and_decoded() -> None:
    secrets = set(UrlCredentials.secrets("https://tok%2Fen@api.test/?sig=a%2Bb&api-version=1"))

    assert secrets == {"tok%2Fen", "tok/en", "a%2Bb", "a+b"}


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pw@api.test/x",
        "https://alice@example.com:pw/abc@api.test/x",
        "https://api.test//user:pw@evil.test/x",
        "https://api.test//token@evil.test/x",
        "https://api.test/x?code=abc",
        "https://api.test/x?code=prefix#tail&state=x",
        "https://api.test/x?code=prefix#tail==&state=x",
        "https://proxy.test/v1?upstream=https://user:pw@up.test/v1",
        "https://example.com/cb#https://up.test/v1?code=abc",
        "https://api.test/users/@me",
        "https://api.test/users//alice@example.com/messages",
        "https://api.test:8443/users/alice@example.com?email=a@b.test",
        "https://[::1]:8443/users/@me",
    ],
)
def test_has_credentials_agrees_with_what_mask_hides(url: str) -> None:
    assert UrlCredentials.has_credentials(url) == (UrlCredentials.mask(url) != url)


@pytest.mark.parametrize(
    "url",
    [
        "https://proxy.test/v1?upstream=https://user:pw@up.test/v1",
        "https://proxy.test/v1?upstream=https%3A%2F%2Fuser%3Apw%40up.test%2Fv1",
        "https://proxy.test/v1?mode=1&upstream=https://user:pw@up.test/v1?a=1&b=2",
        "https://proxy.test/redirect/https://user:pw@up.test/v1",
        "https://proxy.test/v1#upstream=https://user:pw@up.test/v1",
        "https://outer.test/?repo=ssh://user:pw@git.test/org/repo.git",
    ],
)
def test_secrets_yield_the_user_information_of_a_url_written_inside_another(url: str) -> None:
    assert {"user", "pw"} <= set(UrlCredentials.secrets(url))


@pytest.mark.parametrize(
    ("url", "password"),
    [
        ("https://proxy.test/https://user:pw;tail@up.test", "pw;tail"),
        ("https://proxy.test/https://user:p?w@up.test/x", "p?w"),
        ("https://proxy.test/https://user:p#w@up.test/x", "p#w"),
    ],
)
def test_a_url_written_in_the_path_is_read_through_the_rest_of_the_url(url: str, password: str) -> None:
    assert password in set(UrlCredentials.secrets(url))


@pytest.mark.parametrize(
    "url",
    [
        "https://proxy.test/forward?upstream=https://up.test/v1?code=pw",
        "https://proxy.test/forward?mode=1&upstream=https://up.test/v1?api-version=1;sig=pw",
        "https://proxy.test/forward?upstream=https%3A%2F%2Fup.test%2Fv1%3Fcode%3Dpw",
        "https://example.com/cb#https://up.test/v1?code=pw",
    ],
)
def test_secrets_yield_the_credential_parameters_of_a_url_written_inside_another(url: str) -> None:
    assert "pw" in set(UrlCredentials.secrets(url))


def test_a_user_name_counts_in_a_url_written_inside_another_as_it_does_on_its_own() -> None:
    assert UrlCredentials.has_credentials("ssh://git@github.com/org/repo.git")
    assert UrlCredentials.has_credentials("https://outer.test/?repo=ssh://git@github.com/org/repo.git")


def test_secrets_yield_user_information_that_holds_an_unencoded_slash() -> None:
    assert set(UrlCredentials.secrets("https://user:abc/def==@api.test/x")) == {"user", "abc/def=="}


def test_query_credentials_yield_only_credential_values() -> None:
    query = "api-version=1&code=abc&session_token=t1&key=&has_secret=true"

    assert list(UrlCredentials.query_credentials(query)) == ["abc", "t1"]
