"""Explicit placeholders for languages not implemented in Phase 5a."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import LanguageAdapter


class StubAdapter(LanguageAdapter):
    implemented = False
    categorization_engine = "not implemented yet"

    def __init__(self, name: str, display_name: str, extensions: tuple[str, ...], primary_tools: tuple[str, ...]):
        self.name = name
        self.display_name = display_name
        self.extensions = extensions
        self.primary_tools = primary_tools

    @property
    def support_message(self) -> str:
        return f"{self.display_name} support coming: analysis, classification, AST, security, and auto-heal are not implemented yet."

    def check(self, source_path: Path) -> list[dict[str, Any]]:
        return [{"status": "support_coming", "message": self.support_message}]

    def classify(self, diagnostic: Any) -> tuple[str, str]:
        return "not_implemented", f"support coming: {self.categorization_engine}"

    def explain(self, diagnostic: Any) -> str:
        return self.support_message

    def heal_strategies(self) -> tuple[str, ...]:
        return (self.support_message,)

    def run_tests(self, source_path: Path, test_spec: str | None = None) -> dict[str, Any]:
        return {"available": False, "message": self.support_message}

    def build_ast(self, source_path: Path, **options: Any) -> dict[str, Any]:
        return {"available": False, "message": self.support_message}

    def security_rules(self) -> tuple[str, ...]:
        return (self.support_message,)