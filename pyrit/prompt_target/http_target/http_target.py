# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import functools
import hashlib
import json
import logging
import re
from collections.abc import Callable
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from pyrit.models import (
    ComponentIdentifier,
    Message,
    MessagePiece,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.target_trace_config import TargetTraceConfig, request_trace_headers
from pyrit.prompt_target.common.utils import limit_requests_per_minute

logger = logging.getLogger(__name__)


RequestBody = dict[str, Any] | str


@functools.cache
def _httpx_default_headers() -> dict[str, str]:
    """
    Get the headers httpx adds to every client, so identity can tell them apart from configured ones.

    Returns:
        dict[str, str]: The default client headers, keyed by lower-case name.
    """
    with httpx.Client(transport=httpx.BaseTransport(), trust_env=False) as client:
        return dict(client.headers.items())


def _url_as_sent(*, url: str, method: str, client_params: httpx.QueryParams) -> httpx.URL:
    """
    Resolve the URL a client with these query parameters sends for a request to ``url``.

    httpx replaces the request's query with non-empty client parameters, so the parameters that reach
    the endpoint depend on both.

    Args:
        url (str): The request URL from the template.
        method (str): The request method.
        client_params (httpx.QueryParams): The client's query parameters.

    Returns:
        httpx.URL: The URL the request is sent to.
    """
    with httpx.Client(params=client_params, transport=httpx.BaseTransport(), trust_env=False) as client:
        return client.build_request(method, url).url


class HTTPTarget(PromptTarget):
    """
    HTTP_Target is for endpoints that do not have an API and instead require HTTP request(s) to send a prompt.

    """

    _CREDENTIAL_HEADERS: ClassVar[frozenset[str]] = frozenset(
        {
            "authorization",
            "proxy-authorization",
            "x-api-key",
            "api-key",
            "ocp-apim-subscription-key",
            "cookie",
            "set-cookie",
        }
    )

    _CREDENTIAL_QUERY_NAME_TOKENS: ClassVar[frozenset[str]] = frozenset(
        {
            "key",
            "keys",
            "token",
            "tokens",
            "secret",
            "secrets",
            "password",
            "passwords",
            "passwd",
            "pwd",
            "auth",
            "authorization",
            "credential",
            "credentials",
            "sig",
            "signature",
            "signatures",
            "jwt",
            "assertion",
            "assertions",
            "bearer",
            "apikey",
            "oauth",
        }
    )

    @classmethod
    def _is_sensitive_query_param(cls, key: str) -> bool:
        # Match credential parameter names by word, not by substring: `design`
        # contains "sig" and `author` contains "auth", but neither is a
        # credential, and stripping them collapses distinct URLs into one
        # identifier. Splitting on non-alphanumeric separators and on camelCase
        # word boundaries keeps compound credential names (`api_key`,
        # `client_secret`, `access_token`, `accessToken`, `clientSecret`)
        # sensitive while leaving unrelated names intact.
        tokens = {
            token.lower()
            for token in re.split(r"[^a-zA-Z0-9]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", key)
            if token
        }
        if tokens & cls._CREDENTIAL_QUERY_NAME_TOKENS:
            return True
        return key.lower() in {"code", "pass"}

    def __init__(
        self,
        *,
        http_request: str,
        prompt_regex_string: str = "{PROMPT}",
        use_tls: bool = True,
        callback_function: Callable[..., Any] | None = None,
        max_requests_per_minute: int | None = None,
        client: httpx.AsyncClient | None = None,
        model_name: str = "",
        follow_redirects: bool = True,
        custom_configuration: TargetConfiguration | None = None,
        trace_config: TargetTraceConfig | None = None,
        **httpx_client_kwargs: Any,
    ) -> None:
        """
        Initialize the HTTPTarget.

        Args:
            http_request (str): the header parameters as a request (i.e., from Burp)
            prompt_regex_string (str): the placeholder for the prompt
                (default is {PROMPT}) which will be replaced by the actual prompt.
                make sure to modify the http request to have this included, otherwise it will not be properly replaced!
            use_tls (bool): Whether to use TLS. Defaults to True.
            callback_function (Callable, Optional): Function to parse HTTP response.
            max_requests_per_minute (int, Optional): Maximum number of requests per minute.
            client (httpx.AsyncClient, Optional): Pre-configured httpx client.
            model_name (str): The model name. Defaults to empty string.
            follow_redirects (bool): Whether to follow HTTP redirects. Defaults to True for backward compatibility;
                set to False when redirects are unnecessary or the destination must remain fixed.
            custom_configuration (TargetConfiguration, Optional): Override the default configuration for
                this target instance. Defaults to None.
            trace_config: Request tracing configuration. Tracing is disabled by default because an
                arbitrary HTTP endpoint is not known to support W3C trace context. Pass
                ``TargetTraceConfig(enabled=True)`` for an instrumented endpoint.
            **httpx_client_kwargs: Additional keyword arguments for httpx.AsyncClient.

        Raises:
            ValueError: If both client and httpx_client_kwargs are provided.
        """
        # Initialize attributes needed by parse_raw_http_request before calling it
        self._client = client
        self.use_tls = use_tls

        # Parse the URL early to use as endpoint identifier
        # This will fail early if the http_request is malformed
        _, _, endpoint, _, _ = self.parse_raw_http_request(http_request)
        self._destination_origin = self._get_destination_origin(endpoint)

        super().__init__(
            max_requests_per_minute=max_requests_per_minute,
            endpoint=endpoint,
            model_name=model_name,
            custom_configuration=custom_configuration,
            trace_config=trace_config,
        )
        self.http_request = http_request
        self.callback_function = callback_function
        self.prompt_regex_string = prompt_regex_string
        self.follow_redirects = follow_redirects
        self.httpx_client_kwargs = httpx_client_kwargs or {}

        if client and httpx_client_kwargs:
            raise ValueError("Cannot provide both a pre-configured client and additional httpx client kwargs.")

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with HTTP target-specific parameters.

        Returns:
            ComponentIdentifier: The identifier for this target instance.
        """
        return self._create_identifier(
            params={
                "use_tls": self.use_tls,
                "prompt_regex_string": self.prompt_regex_string,
                "callback_function": getattr(self.callback_function, "__name__", None),
                "follow_redirects": self.follow_redirects,
                "http_request_fingerprint": self._http_request_fingerprint(),
            },
        )

    def _http_request_fingerprint(self) -> str:
        """
        Hash what every send asks the endpoint, without its deployment address or credentials.

        The method, encoded path, query, body and effective headers change how the endpoint responds, so
        they belong to the target's identity and to its evaluation identity. Query parameters and
        headers set on an injected client or passed as ``params`` and ``headers`` in the client
        kwargs are merged with the template the same way a send combines them. The scheme, host and
        port are left out, so the same request on another deployment keeps its evaluation identity,
        and so are unchanged httpx default headers. Credential headers and credential query
        parameters are left out so a secret never reaches the identifier and rotating it does not
        change the identity.

        Returns:
            str: The first 16 hex characters of the SHA-256 of the canonical request.
        """
        template_headers, body, url, method, _ = self.parse_raw_http_request(self.http_request)
        if self._client:
            headers: dict[str, str] = {}
            client_params = self._client.params
        else:
            headers = dict(httpx.Headers(self.httpx_client_kwargs.get("headers")).items())
            client_params = httpx.QueryParams(self.httpx_client_kwargs.get("params"))
        headers.update(template_headers)
        default_headers = _httpx_default_headers()
        kept_headers = {
            name: value
            for name, value in headers.items()
            if name != "host" and name not in self._CREDENTIAL_HEADERS and default_headers.get(name) != value
        }
        sent_url = _url_as_sent(url=url, method=method, client_params=client_params)
        kept_query = [
            [name, value] for name, value in sent_url.params.multi_items() if not self._is_sensitive_query_param(name)
        ]
        canonical = json.dumps(
            {
                "method": method,
                "path": sent_url.raw_path.partition(b"?")[0].decode("ascii"),
                "query": kept_query,
                "headers": kept_headers,
                "body": body if isinstance(body, str) else json.dumps(body, sort_keys=True),
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def with_client(
        cls,
        client: httpx.AsyncClient,
        http_request: str,
        prompt_regex_string: str = "{PROMPT}",
        callback_function: Callable[..., Any] | None = None,
        max_requests_per_minute: int | None = None,
        follow_redirects: bool = True,
        *,
        trace_config: TargetTraceConfig | None = None,
    ) -> "HTTPTarget":
        """
        Alternative constructor that accepts a pre-configured httpx client.

        Parameters:
            client: Pre-configured httpx.AsyncClient instance
            http_request: the header parameters as a request (i.e., from Burp)
            prompt_regex_string: the placeholder for the prompt
            callback_function: function to parse HTTP response
            max_requests_per_minute: Optional rate limiting
            follow_redirects: Whether to follow HTTP redirects. Defaults to True for backward compatibility; set to
                False when redirects are unnecessary or the destination must remain fixed.
            trace_config: Request tracing configuration.

        Returns:
            HTTPTarget: an instance of HTTPTarget
        """
        return cls(
            http_request=http_request,
            prompt_regex_string=prompt_regex_string,
            callback_function=callback_function,
            max_requests_per_minute=max_requests_per_minute,
            client=client,
            follow_redirects=follow_redirects,
            trace_config=trace_config,
        )

    def _inject_prompt_into_request(self, request: MessagePiece) -> str:
        """
        Add the prompt into the URL if the prompt_regex_string is found in the
        http_request.

        Args:
            request: The message piece containing the prompt to inject.

        Returns:
            str: the http request with the prompt added in

        Raises:
            ValueError: If a multiline prompt would be substituted into the request line or headers.
        """
        re_pattern = re.compile(self.prompt_regex_string)
        self._validate_prompt_for_template_context(prompt=request.converted_value, pattern=re_pattern)
        if re.search(self.prompt_regex_string, self.http_request):
            http_request_w_prompt = re_pattern.sub(lambda m: request.converted_value, self.http_request)
        else:
            http_request_w_prompt = self.http_request
        return http_request_w_prompt

    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Asynchronously send a message to the HTTP target.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: A list containing the response from the prompt target.
        """
        message = normalized_conversation[-1]
        request = message.message_pieces[0]

        http_request_w_prompt = self._inject_prompt_into_request(request)

        header_dict, http_body, url, http_method, http_version = self.parse_raw_http_request(http_request_w_prompt)
        self._validate_destination(url)

        http2_version = False
        if http_version and "HTTP/2" in http_version:
            http2_version = True

        if self._client is not None:
            client = self._client
            cleanup_client = False
        else:
            client = httpx.AsyncClient(http2=http2_version, **self.httpx_client_kwargs)
            cleanup_client = True

        try:
            header_dict = request_trace_headers(request=message, headers=header_dict, default_headers=client.headers)
            if isinstance(http_body, dict):
                response = await client.request(
                    method=http_method,
                    url=url,
                    headers=header_dict,
                    data=http_body,
                    follow_redirects=self.follow_redirects,
                )
            else:
                response = await client.request(
                    method=http_method,
                    url=url,
                    headers=header_dict,
                    content=http_body,
                    follow_redirects=self.follow_redirects,
                )

            response_content = response.text

            if self.callback_function:
                response_content = self.callback_function(response=response)

            response_message = construct_response_from_request(
                request=request, response_text_pieces=[str(response_content)]
            )
            return [response_message]
        finally:
            if cleanup_client:
                await client.aclose()

    def parse_raw_http_request(self, http_request: str) -> tuple[dict[str, str], RequestBody, str, str, str]:
        """
        Parse the HTTP request string into a dictionary of headers.

        Parameters:
            http_request: the header parameters as a request str with
                          prompt already injected

        Returns:
            headers_dict (dict): dictionary of all http header values
            body (str): string with body data
            url (str): string with URL
            http_method (str): method (ie GET vs POST)
            http_version (str): HTTP version to use

        Raises:
            ValueError: If the HTTP request line is invalid.
        """
        headers_dict: dict[str, str] = {}
        if self._client:
            headers_dict = dict(self._client.headers.copy())
        if not http_request:
            return {}, "", "", "", ""

        body = ""

        # Split before normalizing headers so CRLF in non-JSON bodies is preserved.
        # Support both LF and CRLF raw HTTP requests (e.g. copied from Burp).
        request_parts = re.split(r"\r?\n\r?\n", http_request, maxsplit=1)

        # Parse out the header components
        header_lines = request_parts[0].replace("\r\n", "\n").strip().split("\n")
        http_req_info_line = header_lines[0].split(" ")  # get 1st line like POST /url_ending HTTP_VSN
        header_lines = header_lines[1:]  # rest of the raw request is the headers info

        # Loop through each line and split into key-value pairs
        request_headers: dict[str, str] = {}
        for line in header_lines:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            # HTTP/2 can split cookies across lines, which must be rejoined with "; " (RFC 9113 8.2.3).
            if key == "cookie" and key in request_headers:
                value = "; ".join(part for part in (request_headers[key], value) if part)
            request_headers[key] = value
        headers_dict.update(request_headers)

        for framing_header in ("content-length", "transfer-encoding"):
            headers_dict.pop(framing_header, None)

        if len(request_parts) > 1:
            # Parse as JSON object if it can be parsed that way
            try:
                body = json.loads(request_parts[1], strict=False)  # Check if valid json
                body = json.dumps(body)
            except json.JSONDecodeError:
                body = request_parts[1]

        if len(http_req_info_line) != 3:
            raise ValueError("Invalid HTTP request line")

        # Capture info from 1st line of raw request
        http_method = http_req_info_line[0]

        url_path = http_req_info_line[1]
        full_url = self._infer_full_url_from_host(path=url_path, headers_dict=headers_dict)

        http_version = http_req_info_line[2]

        return headers_dict, body, full_url, http_method, http_version

    def _infer_full_url_from_host(
        self,
        path: str,
        headers_dict: dict[str, str],
    ) -> str:
        # If path is already a full URL, return it as is
        if path.startswith(("http://", "https://")):
            return path

        http_protocol = "http://"
        if self.use_tls is True:
            http_protocol = "https://"

        host = headers_dict["host"]
        return f"{http_protocol}{host}{path}"

    def _validate_destination(self, url: str) -> None:
        destination_origin = self._get_destination_origin(url)
        if destination_origin != self._destination_origin:
            raise ValueError("Prompt substitution cannot change the configured HTTP destination.")

    def _validate_prompt_for_template_context(self, *, prompt: str, pattern: re.Pattern[str]) -> None:
        if "\r" not in prompt and "\n" not in prompt:
            return

        separator = re.search(r"\r?\n\r?\n", self.http_request)
        header_end = separator.start() if separator else len(self.http_request)

        if any(match.start() < header_end for match in pattern.finditer(self.http_request)):
            raise ValueError("Prompts substituted into the HTTP request line or headers cannot contain CR or LF.")

    @staticmethod
    def _get_destination_origin(url: str) -> tuple[str, str | None, int | None]:
        parsed_url = urlsplit(url)
        try:
            port = parsed_url.port
        except ValueError as exc:
            raise ValueError(f"Invalid port in HTTP destination: {url}") from exc
        return parsed_url.scheme.lower(), parsed_url.hostname, port
