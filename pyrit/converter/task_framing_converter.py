# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.common.deprecation import print_deprecation_message
from pyrit.converter.prompt_template_converter import PromptTemplateConverter
from pyrit.models import ComponentIdentifier


class TaskFramingConverter(PromptTemplateConverter):
    """
    Deprecated alias for PromptTemplateConverter; will be removed in 1.4.0.

    Use ``PromptTemplateConverter(template=...)`` instead. To keep the previous
    default behavior, pass ``template="TASK is '{{ prompt }}'"``.
    """

    #: Default template framing the input as a quoted task.
    DEFAULT_TASK_TEMPLATE = "TASK is '{{ prompt }}'"

    def __init__(
        self,
        *,
        task_template: str = DEFAULT_TASK_TEMPLATE,
        strip_characters: str = "",
    ) -> None:
        """
        Initialize the converter with a task-framing template.

        Args:
            task_template (str): A template containing a ``{{ prompt }}`` placeholder
                marking where the input is inserted. Defaults to ``TASK is '{{ prompt }}'``.
            strip_characters (str): Characters removed from the input before it is
                inserted into the template. Defaults to no stripping.
        """
        print_deprecation_message(
            old_item=TaskFramingConverter,
            new_item=PromptTemplateConverter,
            removed_in="1.4.0",
        )
        super().__init__(template=task_template, strip_characters=strip_characters)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier, keeping the original ``task_template`` param name.

        Keeping the old param name leaves identifiers (and eval hashes) of existing
        ``TaskFramingConverter`` usages unchanged until the class is removed.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "task_template": self._template,
                "strip_characters": self._strip_characters,
            },
        )
