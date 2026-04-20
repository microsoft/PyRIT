# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import json
import re
from typing import Any, Callable, Optional

import requests


def get_http_target_json_response_callback_function(key: str) -> Callable[[requests.Response], str]:
    """
    Determine proper parsing response function for an HTTP Request.

    Parameters:
        key (str): this is the path pattern to follow for parsing the output response
            (ie for AOAI this would be choices[0].message.content)
            (for BIC this needs to be a regex pattern for the desired output)
        response_type (ResponseType): this is the type of response (ie HTML or JSON)

    Returns:
        Callable: proper output parsing response
    """

    def parse_json_http_response(response: requests.Response) -> str:
        """
        Parse JSON outputs.

        Parameters:
            response (response): the HTTP Response to parse

        Returns:
            str: parsed output from response given a "key" path to follow
        """
        json_response = json.loads(response.content)
        data_key = _fetch_key(data=json_response, key=key)
        return str(data_key)

    return parse_json_http_response


def get_http_target_regex_matching_callback_function(
    key: str, url: Optional[str] = None
) -> Callable[[requests.Response], str]:
    """
    Get a callback function that parses HTTP responses using regex matching.

    Args:
        key (str): The regex pattern to use for parsing the response.
        url (str, Optional): The original URL to prepend to matches if needed.

    Returns:
        Callable: A function that parses responses using the provided regex pattern.
    """

    def parse_using_regex(response: requests.Response) -> str:
        """
        Parse text outputs using regex.

        Parameters:
            url (optional str): the original URL if this is needed to get a full URL response back (ie BIC)
            key (str): this is the regex pattern to follow for parsing the output response
            response (response): the HTTP Response to parse

        Returns:
            str: parsed output from response given a regex pattern to follow
        """
        re_pattern = re.compile(key)
        match = re.search(re_pattern, str(response.content))
        if match:
            if url:
                return url + match.group()
            else:
                return match.group()
        else:
            return str(response.content)

    return parse_using_regex


def _fetch_key(data: dict[str, Any], key: str) -> Any:
    """
    Fetch the answer from the HTTP JSON response based on the path.

    Args:
        data (dict[str, Any]): HTTP response data.
        key (str): The key path to fetch the value.

    Returns:
        Any: The fetched value.
    """
    pattern = re.compile(r"([a-zA-Z_]+)|\[(-?\d+)\]")
    keys = pattern.findall(key)
    result: Any = data
    for key_part, index_part in keys:
        if key_part:
            result = result.get(key_part, None) if isinstance(result, dict) else None
        elif index_part and isinstance(result, list):
            result = result[int(index_part)] if -len(result) <= int(index_part) < len(result) else None
        if result is None:
            return ""
    return result
