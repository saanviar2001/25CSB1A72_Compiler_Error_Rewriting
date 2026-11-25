"""Language-adapter contract for the dashboard's multi-language foundation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class LanguageAdapter(ABC):
    """Common boundary around language-specific analysis and repair engines."""

    name: str = "generic"
    display_name: str = "Generic"
    extensions: tuple[str, ...] = ()
    primary_tools: tuple[str, ...] = ()
    categorization_engine: str = "not implemented yet"
    implemented: bool = False

    @abstractmethod
    def check(self, source_path: Path) -> list[dict[str, Any]]:
        """Run the language diagnostic tool and return its diagnostics."""

    @abstractmethod
    def classify(self, diagnostic: Any) -> tuple[str, str]:
        """Return ``(category, engine_that_decided)`` for one diagnostic."""

    @abstractmethod
    def explain(self, diagnostic: Any) -> Any:
        """Enrich or explain a diagnostic using the language's real engine."""

    @abstractmethod
    def heal_strategies(self) -> tuple[str, ...]:
        """List the real repair strategies available for this language."""

    @abstractmethod
    def run_tests(self, source_path: Path, test_spec: str | None = None) -> dict[str, Any]:
        """Run available behavioral tests for this language."""

    @abstractmethod
    def build_ast(self, source_path: Path, **options: Any) -> dict[str, Any]:
        """Build the language AST representation used by the dashboard."""

    @abstractmethod
    def security_rules(self) -> tuple[str, ...]:
        """Return the real static-security rule families for this language."""