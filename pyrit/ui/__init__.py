# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.common.deprecation import print_deprecation_message

print_deprecation_message(
    old_item="pyrit.ui module (Gradio-based GUI)",
    new_item="the React-based GUI (CoPyRIT); see https://microsoft.github.io/PyRIT/code/gui/0_gui.html",
    removed_in="0.13.0",
)
