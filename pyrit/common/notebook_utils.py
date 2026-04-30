# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


def is_in_ipython_session() -> bool:
    """
    Determine if the code is running in an IPython session.

    This may be useful if the behavior of the code should change when running in an IPython session.
    For example, the code may display additional information or plots when running in an IPython session.

    Returns:
        bool: True if the code is running in an IPython session, False otherwise.
    """
    try:
        __IPYTHON__  # type: ignore[ty:unresolved-reference]  # noqa: B018
        return True
    except NameError:
        return False
