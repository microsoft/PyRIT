# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Provider-specific adapters shared across PyRIT component families."""

from pyrit.providers.hugging_face import (
    HuggingFaceModelSource,
    HuggingFaceSequenceClassificationResult,
    HuggingFaceSequenceClassifier,
)

__all__ = [
    "HuggingFaceModelSource",
    "HuggingFaceSequenceClassificationResult",
    "HuggingFaceSequenceClassifier",
]
