# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Experimental auxiliary attack implementations (e.g., GCG).

This subpackage is **experimental**: APIs may change in any release without a
deprecation cycle. Pin pyrit to a specific version if you depend on it. To
silence the warning emitted on import::

    import warnings
    from pyrit.exceptions import ExperimentalWarning
    warnings.filterwarnings("ignore", category=ExperimentalWarning)
"""

import warnings

from pyrit.exceptions import ExperimentalWarning

warnings.warn(
    "pyrit.auxiliary_attacks is experimental: APIs may change in any release "
    "without a deprecation cycle. Pin pyrit to a specific version if you depend "
    "on this module. To silence: "
    "warnings.filterwarnings('ignore', category=pyrit.exceptions.ExperimentalWarning).",
    ExperimentalWarning,
    stacklevel=2,
)
