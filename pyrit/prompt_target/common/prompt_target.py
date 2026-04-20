# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import logging
from typing import Any, Dict, List, Optional

from pyrit.identifiers import LegacyIdentifiable
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import Message

logger = logging.getLogger(__name__)


class PromptTarget(LegacyIdentifiable):
    """
    Abstract base class for prompt targets.

    A prompt target is a destination where prompts can be sent to interact with various services,
    models, or APIs. This class defines the interface that all prompt targets must implement.
    """

    _memory: MemoryInterface

    #: A list of PromptConverters that are supported by the prompt target.
    #: An empty list implies that the prompt target supports all converters.
    supported_converters: List[Any]

    def __init__(
        self,
        verbose: bool = False,
        max_requests_per_minute: Optional[int] = None,
        endpoint: str = "",
        model_name: str = "",
        underlying_model: Optional[str] = None,
    ) -> None:
        """
        Initialize the PromptTarget.

        Args:
            verbose (bool): Enable verbose logging. Defaults to False.
            max_requests_per_minute (int, Optional): Maximum number of requests per minute.
            endpoint (str): The endpoint URL. Defaults to empty string.
            model_name (str): The model name. Defaults to empty string.
            underlying_model (str, Optional): The underlying model name (e.g., "gpt-4o") for
                identification purposes. This is useful when the deployment name in Azure differs
                from the actual model. If not provided, `model_name` will be used for the identifier.
                Defaults to None.
        """
        self._memory = CentralMemory.get_memory_instance()
        self._verbose = verbose
        self._max_requests_per_minute = max_requests_per_minute
        self._endpoint = endpoint
        self._model_name = model_name
        self._underlying_model = underlying_model

        if self._verbose:
            logging.basicConfig(level=logging.INFO)

    @abc.abstractmethod
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Send a normalized prompt async to the prompt target.

        Returns:
            list[Message]: A list of message responses. Most targets return a single message,
                but some (like response target with tool calls) may return multiple messages.
        """

    @abc.abstractmethod
    def _validate_request(self, *, message: Message) -> None:
        """
        Validate the provided message.

        Args:
            message: The message to validate.
        """

    def set_model_name(self, *, model_name: str) -> None:
        """
        Set the model name for this target.

        Args:
            model_name (str): The model name to set.
        """
        self._model_name = model_name

    def dispose_db_engine(self) -> None:
        """
        Dispose database engine to release database connections and resources.
        """
        self._memory.dispose_engine()

    def get_identifier(self) -> Dict[str, Any]:
        """
        Get an identifier dictionary for this prompt target.

        This includes essential attributes needed for scorer evaluation and registry tracking.
        Subclasses should override this method to include additional relevant attributes
        (e.g., temperature, top_p) when available.

        Returns:
            Dict[str, Any]: A dictionary containing identification attributes.

        Note:
            If the `self._underlying_model` is specified, either passed in during instantiation
            or via environment variable, it is used as the "model_name" for the identifier.
            Otherwise, `self._model_name` (which is often the deployment name in Azure) is used.
        """
        public_attributes: Dict[str, Any] = {}
        public_attributes["__type__"] = self.__class__.__name__
        public_attributes["__module__"] = self.__class__.__module__
        if self._endpoint:
            public_attributes["endpoint"] = self._endpoint
        # if the underlying model is specified, use it as the model name for identification
        # otherwise, use self._model_name (which is often the deployment name in Azure)
        if self._underlying_model:
            public_attributes["model_name"] = self._underlying_model
        elif self._model_name:
            public_attributes["model_name"] = self._model_name
        # Include temperature and top_p if available (set by subclasses)
        if hasattr(self, "_temperature") and self._temperature is not None:
            public_attributes["temperature"] = self._temperature
        if hasattr(self, "_top_p") and self._top_p is not None:
            public_attributes["top_p"] = self._top_p
        return public_attributes
