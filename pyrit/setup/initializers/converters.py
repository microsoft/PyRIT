# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Initializer for registering converters that need no user-supplied configuration."""

import logging

from pyrit.models.identifiers.class_name_utils import class_name_to_snake_case
from pyrit.models.parameter import ComponentType
from pyrit.registry import ConverterRegistry, TargetRegistry
from pyrit.registry.components import ConverterMetadata
from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


class ConverterInitializer(PyRITInitializer):
    """
    Register converters that can be created from defaults or the adversarial chat target.

    A converter is eligible when it has no required constructor parameters. A converter
    whose required parameters are all target references is also eligible when the
    ``adversarial_chat`` target is registered.
    """

    _ADVERSARIAL_CHAT_TARGET = "adversarial_chat"

    async def initialize_async(self) -> None:
        """Create and register all eligible converters."""
        converter_registry = ConverterRegistry.get_registry_singleton()
        target_registry = TargetRegistry.get_registry_singleton()
        adversarial_chat_available = self._ADVERSARIAL_CHAT_TARGET in target_registry.instances

        for metadata in converter_registry.get_all_registered_class_metadata():
            creation_kwargs = self._get_creation_kwargs(
                metadata=metadata,
                adversarial_chat_available=adversarial_chat_available,
            )
            if creation_kwargs is None:
                continue

            instance_name = class_name_to_snake_case(metadata.class_name, suffix="Converter")
            try:
                converter = converter_registry.create_instance(metadata.registry_name, **creation_kwargs)
                converter_registry.instances.register(converter, name=instance_name)
                logger.info("Registered converter: %s", instance_name)
            except (KeyError, TypeError, ValueError) as ex:
                logger.warning("Skipping converter '%s': %s", instance_name, ex)

    def _get_creation_kwargs(
        self,
        *,
        metadata: ConverterMetadata,
        adversarial_chat_available: bool,
    ) -> dict[str, object] | None:
        """
        Get constructor arguments for an eligible converter.

        Returns:
            dict[str, object] | None: Constructor arguments, or None when the
                converter needs configuration that this initializer cannot supply.
        """
        required_parameters = [parameter for parameter in metadata.parameters if parameter.required]
        if not required_parameters:
            return {}

        if not adversarial_chat_available or not all(
            parameter.is_reference_to(ComponentType.TARGET) for parameter in required_parameters
        ):
            return None

        return {parameter.name: self._ADVERSARIAL_CHAT_TARGET for parameter in required_parameters}
