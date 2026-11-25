# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Keys on this server that an administrator lets users pick for targets."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalized_endpoint(url: str) -> str:
    """
    Return an address in the form two addresses are compared in.

    The scheme and host are lowercased, and a default port and a trailing slash are dropped. Everything else is kept,
    so an address with a user name, query, or fragment never matches a server credential's endpoint, which can't
    have one.

    Args:
        url (str): The address.

    Returns:
        str: The address to compare.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    # An IPv6 address keeps its brackets, so its last group can't be taken for the port.
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port is not None and parts.port != _DEFAULT_PORTS.get(scheme) else ""
    user_info = parts.netloc.rpartition("@")[0]
    user = f"{user_info}@" if user_info else ""
    query = f"?{parts.query}" if parts.query else ""
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    return f"{scheme}://{user}{host}{port}{parts.path.rstrip('/')}{query}{fragment}"


class ServerCredential(BaseModel):
    """A key in this server's environment that users can pick for targets that send requests to its endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,63}$", description="The name users pick")
    env_var: str = Field(
        ..., pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$", description="The environment variable that holds the key"
    )
    endpoint: str = Field(..., min_length=1, max_length=2048, description="The only address the key is sent to")

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, value: str) -> str:
        parts = urlsplit(value)
        try:
            port = parts.port
        except ValueError as error:
            raise ValueError("A server credential's endpoint has a port that isn't from 1 to 65535.") from error
        if parts.scheme not in ("http", "https") or not parts.hostname or port == 0:
            raise ValueError("A server credential's endpoint must be a full http or https address.")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("A server credential's endpoint can't hold a user name, password, query, or fragment.")
        return value
