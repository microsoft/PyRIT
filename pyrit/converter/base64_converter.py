# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._byte_encoding_converter import _ByteEncodingConverter


class Base64Converter(_ByteEncodingConverter):
    """
    Converter that encodes text to base64 format.

    This converter takes input text and converts it to base64 encoding,
    which can be useful for obfuscating text or testing how systems
    handle encoded content.

    It is the base-64 member of the standard "base-N" byte-encoding family
    (see ``_ByteEncodingConverter``) and still exposes the full ``encoding_func``
    option, so its less common
    relatives (``b16encode``, ``a85encode``, ``b85encode``, ...) remain reachable
    through it in addition to the dedicated named converters.
    """
