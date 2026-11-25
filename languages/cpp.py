"""C++ adapter delegating to the established C++ pipeline unchanged."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import LanguageAdapter


class CppAdapter(LanguageAdapter):
    name = "cpp"
    display_name = "C++"
    extensions = (".cpp", ".cc", ".cxx")
    primary_tools = ("g++", "clang++")
    categorization_engine = "TF-IDF + Logistic Regression"
    implemented = True

    def check(self, source_path: Path) -> list[dict[str, Any]]:
        from compiler_runner import run_cpp_compiler

        return run_cpp_compiler(str(source_path))

    def classify(self, diagnostic: Any) -> tuple[str, str]:
        from error_classifier import get_default_classifier

        message = getattr(diagnostic, "message", None)
        if message is None and isinstance(diagnostic, dict):
            message = diagnostic.get("message", "")
        category, _confidence = get_default_classifier().predict(message or "")
        return category or "other", self.categorization_engine

    def explain(self, diagnostic: Any) -> Any:
        from error_explainer import enrich_error

        return enrich_error(diagnostic)

    def heal_strategies(self) -> tuple[str, ...]:
        return ("C++ rule-based auto-heal",)

    def run_tests(self, source_path: Path, test_spec: str | None = None) -> dict[str, Any]:
        return {"available": True, "engine": "C++ HealingAgent behavioral verification"}

    def build_ast(self, source_path: Path, **options: Any) -> dict[str, Any]:
        from clang_ast import extract_ast_tree

        tree, approximation = extract_ast_tree(str(source_path), expand_full=options.get("expand_full", True))
        return {"tree": tree, "approximation": approximation, "engine": "clang++ AST"}

    def security_rules(self) -> tuple[str, ...]:
        return ("C/C++ static security analyzer",)