"""Language registry and real toolchain probing performed at application startup."""

from __future__ import annotations

import shutil
import subprocess
import sys
import re
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .base import LanguageAdapter
from .cpp import CppAdapter
from .java import JavaAdapter, java_runtime_executable
from .python import PythonAdapter
from .stub import StubAdapter


TOOL_COMMANDS: dict[str, tuple[str, ...]] = {
    "g++": ("g++", "--version"), "gcc": ("gcc", "--version"),
    "clang++": ("clang++", "--version"), "python": (sys.executable, "--version"),
    "javac": ("javac", "-version"), "java": ("java", "-version"), "node": ("node", "--version"),
}


def detect_tools() -> dict[str, dict[str, Any]]:
    """Run each requested executable and retain its actual first version line."""
    results: dict[str, dict[str, Any]] = {}
    for tool, command in TOOL_COMMANDS.items():
        executable = java_runtime_executable() if tool == "java" else shutil.which(command[0])
        if executable is None:
            results[tool] = {"installed": False, "path": "not found", "version": "tool not installed"}
            continue
        try:
            resolved_command = (executable, *command[1:])
            completed = subprocess.run(resolved_command, capture_output=True, text=True, timeout=5, check=False)
            output = (completed.stdout or completed.stderr or "").strip().splitlines()
            version = output[0].strip() if output else "version unavailable"
            major = None
            match = re.search(r"(?:version\s+\")?(\d+)(?:\.\d+)?", version)
            if match:
                major = int(match.group(1))
                if version.startswith('java version "1.'):
                    legacy = re.search(r'version "1\.(\d+)', version)
                    if legacy:
                        major = int(legacy.group(1))
            results[tool] = {"installed": completed.returncode == 0, "path": executable, "version": version, "major": major}
        except (OSError, subprocess.SubprocessError):
            results[tool] = {"installed": False, "path": executable, "version": "version unavailable", "major": None}

    return results


def _java_compatibility(tools: dict[str, dict[str, Any]]) -> dict[str, str | None]:
    javac_major = tools.get("javac", {}).get("major")
    java_major = tools.get("java", {}).get("major")
    warning = (
        f"javac major version {javac_major} differs from java major version {java_major}."
        if javac_major is not None and java_major is not None and javac_major != java_major else None
    )
    target = (
        str(java_major)
        if java_major is not None and javac_major is not None and java_major < javac_major
        else None
    )
    uses_release = javac_major is not None and javac_major >= 9
    return {
        "warning": warning,
        "compile_target": target,
        "message": (
            f"Compiled for Java {target} with javac --release {target}."
            if target and uses_release else
            f"Compiled for Java {target} with javac -source/-target {target}."
            if target else None
        ),
    }


LANGUAGE_REGISTRY: dict[str, LanguageAdapter] = {
    "cpp": CppAdapter(),
    "python": PythonAdapter(),
    "java": JavaAdapter(),
    "c": StubAdapter("c", "C", (".c",), ("gcc",)),
    "javascript": StubAdapter("javascript", "JavaScript", (".js",), ("node",)),
}
EXTENSION_TO_LANGUAGE = {extension: language for language, adapter in LANGUAGE_REGISTRY.items() for extension in adapter.extensions}
TOOL_STATUS = detect_tools()
JAVA_COMPATIBILITY = _java_compatibility(TOOL_STATUS)


def get_adapter(language: str | None = None, filename: str | None = None) -> LanguageAdapter:
    selected = (language or "").strip().lower()
    if selected in LANGUAGE_REGISTRY:
        return LANGUAGE_REGISTRY[selected]
    if filename:
        detected = EXTENSION_TO_LANGUAGE.get(filename.lower()[filename.rfind("."):])
        if detected:
            return LANGUAGE_REGISTRY[detected]
    return LANGUAGE_REGISTRY["cpp"]


def language_metadata(adapter: LanguageAdapter) -> dict[str, Any]:
    tools = [{"name": tool, **TOOL_STATUS[tool]} for tool in adapter.primary_tools]
    metadata = {
        "name": adapter.name, "display_name": adapter.display_name, "implemented": adapter.implemented,
        "categorization_engine": adapter.categorization_engine, "tools": tools,
        "tool_warning": JAVA_COMPATIBILITY.get("warning") if adapter.name == "java" else None,
        "compile_target": JAVA_COMPATIBILITY.get("compile_target") if adapter.name == "java" else None,
        "tool_status": "installed" if all(tool["installed"] for tool in tools) else "tool not installed",
    }
    if adapter.name == "java":
        try:
            parser_version = version("tree-sitter-java")
        except PackageNotFoundError:
            metadata["ast_engine"] = "tree-sitter-java unavailable (regex fallback)"
        else:
            metadata["ast_engine"] = f"tree-sitter-java {parser_version}"
    return metadata


def all_language_metadata() -> list[dict[str, Any]]:
    return [language_metadata(adapter) for adapter in LANGUAGE_REGISTRY.values()]