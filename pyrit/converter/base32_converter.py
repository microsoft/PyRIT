# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._byte_encoding_converter import _ByteEncodingConverter


class Base32Converter(_ByteEncodingConverter):
    """
    Converter that encodes text to standard Base32 format.

    Base32 is a binary-to-text encoding defined in RFC 4648 section 6
    (https://www.rfc-editor.org/rfc/rfc4648). This converter takes input text,
    encodes its UTF-8 bytes with the standard Base32 alphabet, and returns the
    Base32 representation, which can be useful for obfuscating text or testing
    how systems handle encoded content.

    It is a named member of the standard "base-N" byte-encoding family and shares
    its implementation with ``Base64Converter``.
    """

    def __init__(self) -> None:
        """Initialize the Base32Converter (standard RFC 4648 Base32)."""
        super().__init__(encoding_func="b32encode")
