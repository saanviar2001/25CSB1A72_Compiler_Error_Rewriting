"""Compatibility imports; Phase 5a adapters live in :mod:`languages`."""

from languages import CppAdapter, LanguageAdapter, StubAdapter, all_language_metadata, get_adapter


def supported_languages():
    """Return the Phase 5a language metadata used by the dashboard."""
    return {item["name"]: item for item in all_language_metadata()}


__all__ = ["LanguageAdapter", "CppAdapter", "StubAdapter", "get_adapter", "supported_languages"]