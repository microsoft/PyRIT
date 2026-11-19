# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared big-integer binary-to-text encoder for the Base-N converters (Base58, Base62).

Base58 and Base62 are not simple block transforms like Base64: the whole input is treated as a
single big-endian integer and repeatedly divided by the radix. Because that integer conversion
drops leading ``0x00`` bytes, each one is re-emitted as the first alphabet character so the
encoding round-trips losslessly. Both converters share this exact algorithm; only their alphabet
differs. Base91 uses a different bit-buffer algorithm and is intentionally not built on this.

This module is intentionally private (underscore prefix): it is an internal helper shared by the
base-N converters, not part of the public ``pyrit.converter`` API.
"""


def encode_base_n(data: bytes, *, alphabet: str) -> str:
    """
    Encode bytes as a base-``len(alphabet)`` string using the big-integer scheme.

    Args:
        data (bytes): The bytes to encode.
        alphabet (str): The ordered alphabet; its length is the radix and ``alphabet[0]`` is the
            character used for leading zero bytes.

    Returns:
        str: The base-N encoded representation (empty string for empty input).
    """
    if not data:
        return ""

    radix = len(alphabet)

    # Each leading zero byte (which the big-integer conversion would drop) becomes alphabet[0].
    leading_zeros = 0
    for byte in data:
        if byte == 0:
            leading_zeros += 1
        else:
            break

    value = int.from_bytes(data, byteorder="big")
    digits: list[str] = []
    while value > 0:
        value, remainder = divmod(value, radix)
        digits.append(alphabet[remainder])
    digits.reverse()

    return alphabet[0] * leading_zeros + "".join(digits)
