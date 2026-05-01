# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import json
import logging
import re
from collections.abc import Callable
from typing import Any, Optional

import httpx

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    Message,
    MessagePiece,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute

logger = logging.getLogger(__name__)


RequestBody = dict[str, Any] | str


class HTTPTarget(PromptTarget):
    """
    HTTP_Target is for endpoints that do not have an API and instead require HTTP request(s) to send a prompt.

    """

    def __init__(
        self,
        http_request: str,
        prompt_regex_string: str = "{PROMPT}",
        use_tls: bool = True,
        callback_function: Optional[Callable[..., Any]] = None,
        max_requests_per_minute: Optional[int] = None,
        client: Optional[httpx.AsyncClient] = None,
        model_name: str = "",
        custom_configuration: Optional[TargetConfiguration] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
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
            custom_configuration (TargetConfiguration, Optional): Override the default configuration for
                this target instance. Defaults to None.
            custom_capabilities (TargetCapabilities, Optional): **Deprecated.** Use
                ``custom_configuration`` instead. Will be removed in v0.14.0.
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

        super().__init__(
            max_requests_per_minute=max_requests_per_minute,
            endpoint=endpoint,
            model_name=model_name,
            custom_configuration=custom_configuration,
            custom_capabilities=custom_capabilities,
        )
        self.http_request = http_request
        self.callback_function = callback_function
        self.prompt_regex_string = prompt_regex_string
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
            },
        )

    @classmethod
    def with_client(
        cls,
        client: httpx.AsyncClient,
        http_request: str,
        prompt_regex_string: str = "{PROMPT}",
        callback_function: Callable[..., Any] | None = None,
        max_requests_per_minute: Optional[int] = None,
    ) -> "HTTPTarget":
        """
        Alternative constructor that accepts a pre-configured httpx client.

        Parameters:
            client: Pre-configured httpx.AsyncClient instance
            http_request: the header parameters as a request (i.e., from Burp)
            prompt_regex_string: the placeholder for the prompt
            callback_function: function to parse HTTP response
            max_requests_per_minute: Optional rate limiting

        Returns:
            HTTPTarget: an instance of HTTPTarget
        """
        return cls(
            http_request=http_request,
            prompt_regex_string=prompt_regex_string,
            callback_function=callback_function,
            max_requests_per_minute=max_requests_per_minute,
            client=client,
        )

    def _inject_prompt_into_request(self, request: MessagePiece) -> str:
        """
        Add the prompt into the URL if the prompt_regex_string is found in the
        http_request.

        Args:
            request: The message piece containing the prompt to inject.

        Returns:
            str: the http request with the prompt added in
        """
        re_pattern = re.compile(self.prompt_regex_string)
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

        if "Content-Length" in header_dict:
            header_dict["Content-Length"] = str(len(http_body))

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
            match http_body:
                case dict():
                    response = await client.request(
                        method=http_method,
                        url=url,
                        headers=header_dict,
                        data=http_body,
                        follow_redirects=True,
                    )
                case str():
                    response = await client.request(
                        method=http_method,
                        url=url,
                        headers=header_dict,
                        content=http_body,
                        follow_redirects=True,
                    )

            response_content = response.content

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

        # Split the request into headers and body by finding the double newlines (\n\n).
        # Preserve body whitespace exactly as provided in the raw request.
        # Support both LF and CRLF raw HTTP requests (e.g. copied from Burp).
        normalized = http_request.replace("\r\n", "\n")
        request_parts = normalized.split("\n\n", 1)

        # Parse out the header components
        header_lines = request_parts[0].strip().split("\n")
        http_req_info_line = header_lines[0].split(" ")  # get 1st line like POST /url_ending HTTP_VSN
        header_lines = header_lines[1:]  # rest of the raw request is the headers info

        # Loop through each line and split into key-value pairs
        for line in header_lines:
            key, value = line.split(":", 1)
            headers_dict[key.strip().lower()] = value.strip()

        if "content-length" in headers_dict:
            del headers_dict["content-length"]

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
