"""Language support registry for the dashboard."""

from .base import LanguageAdapter
from .cpp import CppAdapter
from .java import JavaAdapter
from .python import PythonAdapter
from .registry import EXTENSION_TO_LANGUAGE, LANGUAGE_REGISTRY, TOOL_STATUS, all_language_metadata, get_adapter, language_metadata
from .stub import StubAdapter

__all__ = ["LanguageAdapter", "CppAdapter", "PythonAdapter", "JavaAdapter", "StubAdapter", "LANGUAGE_REGISTRY", "EXTENSION_TO_LANGUAGE", "TOOL_STATUS", "get_adapter", "language_metadata", "all_language_metadata"]