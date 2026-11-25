"""Python diagnostics, bounded execution, AST rendering, and rule-based healing."""

from __future__ import annotations

import ast
import base64
import difflib
import io
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import threading
import time
import tokenize
import uuid
from pathlib import Path
from typing import Any

from .base import LanguageAdapter


_OUTPUT_LIMIT_BYTES = 1024 * 1024
_TIMEOUT_SECONDS = 5
_STANDARD_MODULES = {
    "math", "os", "sys", "random", "json", "re", "datetime", "collections",
}
_EXPLANATIONS = {
    "SyntaxError": (
        "Python could not parse this statement.",
        "The source does not follow Python's grammar at the reported location.",
        "Correct the indicated syntax and rerun the analysis.",
    ),
    "IndentationError": (
        "Python found an invalid indentation level.",
        "Indentation defines Python code blocks, so inconsistent spacing changes their structure.",
        "Use consistent indentation, preferably four spaces per block.",
    ),
    "TabError": (
        "Python found inconsistent use of tabs and spaces.",
        "A block mixes indentation characters that Python cannot compare reliably.",
        "Replace indentation tabs with consistent spaces.",
    ),
    "NameError": (
        "The code refers to a name that is not defined.",
        "The name may be misspelled, out of scope, or its module may not have been imported.",
        "Define or import the name, or correct it to the intended existing name.",
    ),
    "TypeError": (
        "An operation received a value of an incompatible type.",
        "The operation's arguments do not support the requested combination or call signature.",
        "Check the value types and the function's expected arguments.",
    ),
    "ZeroDivisionError": (
        "The code attempted to divide by zero.",
        "The divisor evaluated to zero at runtime.",
        "Validate the divisor or handle the zero case before dividing.",
    ),
    "ImportError": (
        "Python could not import a requested module or name.",
        "The module may be missing, unavailable, or not export the requested name.",
        "Check the import spelling and install or select the intended module.",
    ),
    "ModuleNotFoundError": (
        "Python could not find a requested module.",
        "The module is not available in the active interpreter environment.",
        "Check the module name and interpreter environment before importing it.",
    ),
    "IndexError": (
        "The code accessed a sequence position that does not exist.",
        "The index is outside the current sequence bounds.",
        "Check the sequence length before indexing it.",
    ),
    "KeyError": (
        "The code requested a mapping key that is not present.",
        "The mapping has no entry for the requested key.",
        "Check membership first or use a default-aware lookup.",
    ),
    "AttributeError": (
        "The requested attribute does not exist on this value.",
        "The value has a different type or does not define that attribute.",
        "Check the value and use an attribute supported by its type.",
    ),
    "ValueError": (
        "A function received a value of the right type but an invalid value.",
        "The value cannot be used for the requested conversion or operation.",
        "Validate the value or handle this invalid-value case.",
    ),
    "EOFError": (
        "The program requested input, but no input was available.",
        "The dashboard's runtime check closes standard input to prevent an interactive run from hanging.",
        "Provide input through a behavioral test case or avoid interactive input during analysis.",
    ),
}

_CATEGORY_BY_EXCEPTION = {
    "SyntaxError": "syntax_error",
    "IndentationError": "syntax_error",
    "TabError": "syntax_error",
    "NameError": "name_resolution",
    "TypeError": "type_error",
    "ZeroDivisionError": "arithmetic_error",
    "ImportError": "import_error",
    "ModuleNotFoundError": "import_error",
    "IndexError": "index_error",
    "KeyError": "key_error",
    "AttributeError": "attribute_error",
    "ValueError": "value_error",
    "EOFError": "input_error",
}
_CATEGORY_BY_MESSAGE = (
    (re.compile(r"division by zero", re.IGNORECASE), "arithmetic_error"),
    (re.compile(r"\bnot defined\b", re.IGNORECASE), "name_resolution"),
    (re.compile(r"no module named|cannot import name", re.IGNORECASE), "import_error"),
    (re.compile(r"list index out of range|tuple index out of range", re.IGNORECASE), "index_error"),
    (re.compile(r"key not found", re.IGNORECASE), "key_error"),
)


def _read_traceback(stderr: str, script_path: str) -> tuple[str, str, int | None, int | None]:
    lines = stderr.splitlines()
    exception_line = next(
        (line.strip() for line in reversed(lines) if line.strip() and not line.startswith("  ")),
        "",
    )
    match = re.match(r"([\w.]+)(?::\s*(.*))?$", exception_line)
    if not match:
        return "RuntimeError", exception_line or "Python process exited unsuccessfully.", None, None

    exception_type = match.group(1).rsplit(".", 1)[-1]
    message = match.group(2) or exception_type
    traceback_lines = []
    for line in lines:
        location = re.match(r'\s*File "([^"]+)", line (\d+)', line)
        if location:
            traceback_lines.append((location.group(1), int(location.group(2))))
    script_line = next(
        (line for path, line in reversed(traceback_lines) if os.path.normcase(path) == os.path.normcase(script_path)),
        traceback_lines[-1][1] if traceback_lines else None,
    )
    return exception_type, message, script_line, None


def _run_python(
    source_path: Path,
    input_text: str | None = None,
    timeout: int = _TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run a copy of the source in a temporary directory with bounded output."""
    with tempfile.TemporaryDirectory(prefix="python-runtime-check-") as directory:
        run_path = Path(directory) / source_path.name
        run_path.write_bytes(source_path.read_bytes())
        process = subprocess.Popen(
            [sys.executable, str(run_path)],
            cwd=directory,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output = {"stdout": bytearray(), "stderr": bytearray()}
        state = {"total": 0, "limited": False}
        lock = threading.Lock()

        def capture(pipe, key: str) -> None:
            while True:
                chunk = pipe.read(8192)
                if not chunk:
                    break
                with lock:
                    remaining = max(0, _OUTPUT_LIMIT_BYTES - state["total"])
                    output[key].extend(chunk[:remaining])
                    state["total"] += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        state["limited"] = True
                        try:
                            process.kill()
                        except OSError:
                            pass

        readers = [
            threading.Thread(target=capture, args=(process.stdout, "stdout"), daemon=True),
            threading.Thread(target=capture, args=(process.stderr, "stderr"), daemon=True),
        ]
        for reader in readers:
            reader.start()
        writer = None
        try:
            if input_text is not None and process.stdin is not None:
                def provide_input() -> None:
                    try:
                        process.stdin.write(input_text.encode("utf-8"))
                        process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        pass
                    finally:
                        process.stdin.close()

                writer = threading.Thread(target=provide_input, daemon=True)
                writer.start()
            return_code = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait()
            timed_out = True
        for reader in readers:
            reader.join(timeout=1)
        if writer is not None:
            writer.join(timeout=1)

        stdout = bytes(output["stdout"]).decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        stderr = bytes(output["stderr"]).decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        return {
            "returncode": return_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "output_limited": state["limited"],
            "script_path": str(run_path),
        }


def _syntax_diagnostic(error: SyntaxError, source_path: Path) -> dict[str, Any]:
    exception_type = type(error).__name__
    message = error.msg or exception_type
    line = error.lineno
    column = error.offset
    return {
        "exception_type": exception_type,
        "type": "ERROR",
        "message": message,
        "raw": f"{source_path.name}:{line or 0}:{(column or 0) + 1}: {exception_type}: {message}",
        "line": line,
        "column": column,
        "category": _CATEGORY_BY_EXCEPTION.get(exception_type, "syntax_error"),
    }


class PythonAdapter(LanguageAdapter):
    name = "python"
    display_name = "Python"
    extensions = (".py",)
    primary_tools = ("python",)
    categorization_engine = "rule-based categorizer (Python exception types)"
    implemented = True

    def check(self, source_path: Path, execute_runtime: bool = True) -> list[dict[str, Any]]:
        bytecode_path = None
        try:
            with tempfile.TemporaryDirectory(prefix="python-pycompile-") as directory:
                bytecode_path = Path(directory) / "checked.pyc"
                py_compile.compile(
                    str(source_path),
                    cfile=str(bytecode_path),
                    doraise=True,
                )
        except py_compile.PyCompileError as error:
            original = error.exc_value
            if isinstance(original, SyntaxError):
                diagnostic = _syntax_diagnostic(original, source_path)
            else:
                diagnostic = {
                    "exception_type": type(original).__name__,
                    "type": "ERROR",
                    "message": str(original),
                    "raw": f"{source_path.name}: {type(original).__name__}: {original}",
                    "line": getattr(original, "lineno", None),
                    "column": getattr(original, "offset", None),
                    "category": "syntax_error",
                }
            return [self._enrich(diagnostic)]

        if not execute_runtime:
            return []

        result = _run_python(source_path)
        if result["timed_out"]:
            diagnostic = {
                "exception_type": "TimeoutError",
                "type": "ERROR",
                "message": "Runtime check exceeded the 5-second limit.",
                "raw": "Python runtime check timed out after 5 seconds.",
                "line": None,
                "column": None,
                "category": "runtime_error",
            }
            return [self._enrich(diagnostic)]
        if result["output_limited"]:
            diagnostic = {
                "exception_type": "OutputLimitError",
                "type": "ERROR",
                "message": "Runtime check output exceeded the 1 MiB limit.",
                "raw": "Python runtime check stopped after exceeding the 1 MiB output limit.",
                "line": None,
                "column": None,
                "category": "runtime_error",
            }
            return [self._enrich(diagnostic)]
        if result["returncode"] == 0:
            return []

        exception_type, message, line, column = _read_traceback(
            result["stderr"], result["script_path"]
        )
        if not message or message == exception_type:
            message = result["stderr"].strip().splitlines()[-1] if result["stderr"].strip() else (
                f"Program exited with status {result['returncode']}."
            )
        diagnostic = {
            "exception_type": exception_type,
            "type": "ERROR",
            "message": message,
            "raw": f"{source_path.name}:{line or 0}: {exception_type}: {message}",
            "line": line,
            "column": column,
            "category": _CATEGORY_BY_EXCEPTION.get(exception_type, "runtime_error"),
        }
        return [self._enrich(diagnostic)]

    def _enrich(self, diagnostic: dict[str, Any]) -> dict[str, Any]:
        category, engine = self.classify(diagnostic)
        diagnostic["category"] = category
        diagnostic["engine"] = engine
        diagnostic["explanation"] = self.explain(diagnostic)
        diagnostic["suggestion"] = diagnostic["explanation"].split("How to fix:\n", 1)[-1]
        diagnostic["confidence"] = 1.0
        return diagnostic

    def classify(self, diagnostic: Any) -> tuple[str, str]:
        if isinstance(diagnostic, dict):
            exception_type = diagnostic.get("exception_type", "")
            message = str(diagnostic.get("message") or "")
        else:
            exception_type = type(diagnostic).__name__
            message = str(diagnostic)
        category = _CATEGORY_BY_EXCEPTION.get(exception_type)
        if category is None or exception_type in {"RuntimeError", "Exception"}:
            category = next(
                (matched for pattern, matched in _CATEGORY_BY_MESSAGE if pattern.search(message)),
                category,
            )
        return category or "runtime_error", self.categorization_engine

    def explain(self, diagnostic: Any) -> str:
        if isinstance(diagnostic, dict):
            exception_type = diagnostic.get("exception_type", "RuntimeError")
            message = diagnostic.get("message", "")
        else:
            exception_type = type(diagnostic).__name__
            message = str(diagnostic)
        what, why, how = _EXPLANATIONS.get(
            exception_type,
            (
                f"Python raised {exception_type}.",
                message or "The operation failed while the program was running.",
                "Review the reported line and handle this exception condition.",
            ),
        )
        return f"What happened:\n {what}\n\nWhy it happened:\n {why}\n\nHow to fix:\n {how}"

    def heal_strategies(self) -> tuple[str, ...]:
        return (
            "insert_missing_colon",
            "balance_delimiters",
            "correct_close_defined_name",
            "import_missing_standard_module",
        )

    def run_tests(self, source_path: Path, test_spec: str | None = None) -> dict[str, Any]:
        spec_path = Path(str(source_path) + ".tests.json")
        if test_spec is None:
            try:
                test_spec = spec_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return {"checked": False, "passed": None, "cases": [], "note": f"No test specification exists at `{spec_path.name}`, so behavior was not verified."}
        try:
            document = json.loads(test_spec)
        except json.JSONDecodeError as error:
            raise ValueError(f"The behavioral test specification is invalid JSON: {error}") from error
        cases = document.get("tests") if isinstance(document, dict) else document
        if not isinstance(cases, list) or not cases:
            raise ValueError("The behavioral test specification must contain a non-empty test list.")
        for number, case in enumerate(cases, start=1):
            if (
                not isinstance(case, dict)
                or not isinstance(case.get("stdin", ""), str)
                or not isinstance(case.get("expected_stdout"), str)
            ):
                raise ValueError(f"Behavioral test {number} must provide string stdin and expected_stdout values.")

        results = []
        for number, case in enumerate(cases, start=1):
            result = _run_python(source_path, case.get("stdin", ""), _TIMEOUT_SECONDS)
            passed = (
                not result["timed_out"]
                and not result["output_limited"]
                and result["returncode"] == 0
                and result["stdout"] == case["expected_stdout"]
            )
            status = (
                "timeout" if result["timed_out"] else
                "output_limit" if result["output_limited"] else
                "passed" if passed else "failed"
            )
            results.append({
                "number": number,
                "passed": passed,
                "status": status,
                "stdin": case.get("stdin", ""),
                "expected_stdout": case["expected_stdout"],
                "actual_stdout": result["stdout"],
                "stderr": result["stderr"],
                "note": (
                    "Output matched exactly." if passed else
                    "Timed out after 5 seconds." if result["timed_out"] else
                    "Exceeded the 1 MiB output limit." if result["output_limited"] else
                    f"Exited with status {result['returncode']} or output differed."
                ),
            })
        failed = [case for case in results if not case["passed"]]
        if failed:
            failed_numbers = ", ".join(str(case["number"]) for case in failed)
            note = f"Behavioral test case(s) {failed_numbers} failed in the Python interpreter."
        else:
            note = f"All {len(results)} behavioral test case(s) passed in the Python interpreter."
        return {
            "checked": True,
            "passed": not failed,
            "cases": results,
            "note": note,
        }

    def build_ast(self, source_path: Path, **options: Any) -> dict[str, Any]:
        from clang_ast import _count_ast_nodes, _trim_tree, render_ast_png

        source = source_path.read_text(encoding="utf-8")
        try:
            parsed = ast.parse(source, filename=source_path.name)
        except SyntaxError as error:
            return {"available": False, "message": f"Python AST unavailable: {error.msg}", "engine": "Python ast + Graphviz"}

        def convert(node: ast.AST) -> dict[str, Any]:
            details = []
            children = []
            for field, value in ast.iter_fields(node):
                if isinstance(value, ast.AST):
                    children.append(convert(value))
                elif isinstance(value, list):
                    children.extend(convert(item) for item in value if isinstance(item, ast.AST))
                elif isinstance(value, (str, int, float, bool)) and value is not None:
                    details.append(f"{field}={value!r}")
            return {
                "type": type(node).__name__,
                "details": ", ".join(details) if details else None,
                "line": getattr(node, "lineno", None),
                "children": children,
            }

        tree = [convert(parsed)]
        node_limit = int(options.get("node_limit", 150))
        depth_limit = int(options.get("depth_limit", 12))
        expand_full = bool(options.get("expand_full", False))
        displayed = tree if expand_full else _trim_tree(tree, node_limit, depth_limit)
        image_path = source_path.parent / f"python_ast_{uuid.uuid4().hex}.png"
        render_ast_png(displayed, str(image_path), expand_full=True)
        return {
            "available": True,
            "tree": displayed,
            "image": "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode("ascii"),
            "path": str(image_path),
            "engine": "Python ast + Graphviz",
            "nodes_before_limits": _count_ast_nodes(tree),
            "nodes_displayed": _count_ast_nodes(displayed),
            "approximation": False,
            "truncation_markers": self._truncation_markers(displayed),
        }

    @staticmethod
    def _truncation_markers(nodes: list[dict[str, Any]]) -> list[str]:
        markers = []
        for node in nodes:
            if node.get("type") == "...":
                markers.append(node.get("details", "... more nodes"))
            markers.extend(PythonAdapter._truncation_markers(node.get("children", [])))
        return markers

    def security_rules(self) -> tuple[str, ...]:
        return (
            "dynamic code execution (eval/exec)",
            "unsafe deserialization (pickle)",
            "shell command execution",
            "unsafe YAML loading",
            "hardcoded credentials",
            "assert-based input validation",
            "user-controlled file paths",
        )

    def fix_file(
        self,
        source_path: Path,
        original_source: str,
        test_spec: str | None = None,
        execute_runtime: bool = True,
        runtime_skip_reason: str | None = None,
        runtime_requested: bool | None = None,
    ) -> dict[str, Any]:
        from diff_viewer import compute_diff, format_diff_html, has_changes

        source = original_source
        attempts: list[dict[str, Any]] = []
        strategies = []
        diagnostics = self.check(source_path, execute_runtime=execute_runtime)
        if not diagnostics:
            behavior = self.run_tests(source_path, test_spec) if execute_runtime else {
                "checked": False, "passed": None, "cases": [],
                "note": runtime_skip_reason or "Runtime check skipped.",
            }
            attempts.append({
                "number": 1,
                "strategy": "already_clean",
                "observed": "No Python syntax diagnostics were found.",
                "planned": "Keep the source unmodified.",
                "action": "No changes required.",
                "outcome": "Python checks passed without a patch.",
                "error": "",
                "before": original_source,
                "after": source,
                "changed": False,
                "compiles": True,
                "lines_changed": [],
                "diff_html": "",
                "reasoning": "No repair was necessary.",
                "confidence": 1.0,
                "duration": 0.0,
            })
            return self._healing_result(
                source_path, original_source, source, "already_clean",
                "The Python source passed static syntax checks; no patch was needed.",
                behavior, attempts, strategies,
                runtime_check={
                    "requested": execute_runtime if runtime_requested is None else runtime_requested,
                    "executed": execute_runtime,
                    "message": "Runtime check completed." if execute_runtime else runtime_skip_reason or "Runtime check skipped.",
                },
            )

        for number in range(1, 15):
            current_diagnostic = diagnostics[0]
            started = time.monotonic()
            patch = self._plan_patch(source, current_diagnostic)
            if patch is None:
                break
            strategy, planned, updated = patch
            before = source
            source = updated
            source_path.write_text(source, encoding="utf-8")
            diagnostics = self.check(source_path, execute_runtime=execute_runtime)
            changed = source != before
            if strategy not in strategies:
                strategies.append(strategy)
            diff = compute_diff(before, source)
            attempts.append({
                "number": number,
                "strategy": strategy,
                "observed": f"{current_diagnostic['exception_type']}: {current_diagnostic['message']}",
                "planned": planned,
                "action": f"Applied {strategy.replace('_', ' ')}.",
                "outcome": (
                    "The Python check passed after the patch." if not diagnostics else
                    f"The check still reports {diagnostics[0]['exception_type']}: {diagnostics[0]['message']}"
                ),
                "error": current_diagnostic["message"],
                "before": before,
                "after": source,
                "changed": changed,
                "compiles": not any(item["exception_type"] in {"SyntaxError", "IndentationError", "TabError"} for item in diagnostics),
                "lines_changed": self._changed_lines(before, source),
                "diff_html": format_diff_html(diff) if has_changes(diff) else "<i>No visible changes.</i>",
                "reasoning": planned,
                "confidence": 0.9,
                "duration": round(time.monotonic() - started, 3),
            })
            if not diagnostics:
                break

        if not attempts:
            diagnostic = diagnostics[0] if diagnostics else {}
            observed = (
                f"{diagnostic.get('exception_type', 'No diagnostics')}: {diagnostic.get('message', 'No supported repair strategy matched.')}"
            )
            attempts.append({
                "number": 1,
                "strategy": "not_fixable",
                "observed": observed,
                "planned": "Do not guess at program intent; leave the source unchanged.",
                "action": "No automatic patch was applied.",
                "outcome": "No supported, unambiguous Python repair strategy matched this diagnostic.",
                "error": observed,
                "before": original_source,
                "after": source,
                "changed": False,
                "compiles": False,
                "lines_changed": [],
                "diff_html": "",
                "reasoning": "No repair strategy matched safely.",
                "confidence": 1.0,
                "duration": 0.0,
            })

        behavior = self.run_tests(source_path, test_spec) if not diagnostics and execute_runtime else {
            "checked": False,
            "passed": None,
            "cases": [],
            "note": (
                "The Python check still reports an error, so behavioral tests were not run."
                if diagnostics else runtime_skip_reason or "Runtime check skipped."
            ),
        }
        syntax_error = any(
            item["exception_type"] in {"SyntaxError", "IndentationError", "TabError"}
            for item in diagnostics
        )
        if diagnostics:
            verdict = "not_fixable"
            explanation = (
                "The rule-based healer could not clear all Python diagnostics. "
                "No unambiguous supported patch remains; manual correction is required."
            )
        elif behavior["checked"] and behavior["passed"] is False:
            verdict = "compiles_but_behavioral_verification_failed"
            explanation = behavior["note"]
        elif behavior["checked"] and behavior["passed"]:
            verdict = "fixed_and_verified"
            explanation = f"The Python checks passed after repair. {behavior['note']}"
        else:
            verdict = "compiles_not_behaviorally_verified"
            explanation = "The Python checks passed after repair, but no behavioral test specification was available."
        if syntax_error:
            explanation = "The Python syntax check still fails. " + explanation
        return self._healing_result(
            source_path, original_source, source, verdict, explanation,
            behavior, attempts, strategies,
            runtime_check={
                "requested": execute_runtime if runtime_requested is None else runtime_requested,
                "executed": execute_runtime and not diagnostics,
                "message": "Runtime check completed." if execute_runtime and not diagnostics else
                    runtime_skip_reason or "Runtime check skipped.",
            },
        )

    def _healing_result(
        self,
        source_path: Path,
        original_source: str,
        source: str,
        verdict: str,
        explanation: str,
        behavior: dict[str, Any],
        attempts: list[dict[str, Any]],
        strategies: list[str],
        runtime_check: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        labels = {
            "already_clean": "Nothing to fix - the code already passes Python checks",
            "fixed_and_verified": "Fixed and verified",
            "compiles_not_behaviorally_verified": "Python checks passed but not behaviorally verified",
            "compiles_but_behavioral_verification_failed": "Python checks passed but behavioral verification failed",
            "not_fixable": "Could not be automatically fixed - requires manual correction",
        }
        syntax_compiles = True
        try:
            with tempfile.TemporaryDirectory(prefix="python-final-compile-") as directory:
                py_compile.compile(
                    str(source_path),
                    cfile=str(Path(directory) / "final.pyc"),
                    doraise=True,
                )
        except py_compile.PyCompileError:
            syntax_compiles = False
        return {
            "filename": source_path.name,
            "original_source": original_source,
            "fixed_source": source,
            "compiles": syntax_compiles,
            "verdict": verdict,
            "verdict_label": labels[verdict],
            "explanation": explanation,
            "manual_fix": "" if verdict != "not_fixable" else explanation,
            "behavior": behavior,
            "runtime_check": runtime_check or {
                "requested": behavior.get("checked", False),
                "executed": behavior.get("checked", False),
                "message": "Runtime check completed." if behavior.get("checked") else "Runtime check skipped.",
            },
            "attempts": attempts,
            "transparency": {
                "language": self.display_name,
                "classifier": self.categorization_engine,
                "repair_strategies": strategies or ["No repair strategy applied"],
                "compiler": {"name": "python", "version": f"Python {sys.version.split()[0]}"},
                "behavioral_tests": "Ran" if behavior.get("checked") else "Did not run",
                "runtime_note": (runtime_check or {}).get("message", "Runtime check skipped."),
            },
        }

    def _plan_patch(self, source: str, diagnostic: dict[str, Any]) -> tuple[str, str, str] | None:
        exception_type = diagnostic.get("exception_type")
        message = diagnostic.get("message", "")
        if exception_type in {"SyntaxError", "IndentationError", "TabError"}:
            if "expected ':'" in message:
                patched = self._insert_missing_colon(source, diagnostic.get("line"))
                if patched is not None:
                    return "missing_colon", "Add the missing colon to the reported block header.", patched
            if "never closed" in message or "unexpected EOF" in message:
                patched = self._close_open_delimiters(source)
                if patched is not None:
                    return "missing_closing_delimiter", "Close the unmatched opening parenthesis or bracket.", patched
            if "unmatched" in message:
                patched = self._remove_unmatched_closer(source, diagnostic)
                if patched is not None:
                    return "unmatched_closing_delimiter", "Remove the unmatched closing parenthesis or bracket.", patched
        if exception_type == "NameError":
            name_match = re.search(r"name '([^']+)' is not defined", message)
            if name_match:
                missing = name_match.group(1)
                if missing in _STANDARD_MODULES and self._uses_name(source, missing) and not self._module_imported(source, missing):
                    return "missing_standard_library_import", f"Import the referenced standard-library module {missing}.", self._add_import(source, missing)
                corrected = self._correct_defined_name(source, missing)
                if corrected:
                    return "correct_defined_name", f"Replace the undefined name with the uniquely close defined name {corrected[0]}.", corrected[1]
        return None

    @staticmethod
    def _insert_missing_colon(source: str, line_number: int | None) -> str | None:
        lines = source.splitlines(keepends=True)
        if not line_number or line_number > len(lines):
            return None
        line = lines[line_number - 1]
        body = line.rstrip("\r\n")
        comment_offset = len(body)
        try:
            for token in tokenize.generate_tokens(io.StringIO(body).readline):
                if token.type == tokenize.COMMENT:
                    comment_offset = token.start[1]
                    break
        except (tokenize.TokenError, IndentationError):
            pass
        code = body[:comment_offset]
        comment = body[comment_offset:]
        match = re.match(r"^\s*(if|elif|else|for|while|def|class)\b.*$", code)
        if not match or code.rstrip().endswith(":"):
            return None
        newline = line[len(body):]
        repaired = code.rstrip() + ":" + code[len(code.rstrip()):] + comment + newline
        return "".join(lines[:line_number - 1] + [repaired] + lines[line_number:])

    @staticmethod
    def _close_open_delimiters(source: str) -> str | None:
        stack: list[str] = []
        matching = {")": "(", "]": "[", "}": "{"}
        try:
            tokens = tokenize.generate_tokens(io.StringIO(source).readline)
            for token in tokens:
                if token.type == tokenize.OP:
                    if token.string in "([{":
                        stack.append(token.string)
                    elif token.string in matching and stack and stack[-1] == matching[token.string]:
                        stack.pop()
        except (tokenize.TokenError, IndentationError):
            pass
        if not stack:
            return None
        closing = {"(": ")", "[": "]", "{": "}"}
        suffix = "".join(closing[item] for item in reversed(stack))
        return source + ("" if not source or source.endswith("\n") else "\n") + suffix + "\n"

    @staticmethod
    def _remove_unmatched_closer(source: str, diagnostic: dict[str, Any]) -> str | None:
        line_number = diagnostic.get("line")
        column = diagnostic.get("column")
        lines = source.splitlines(keepends=True)
        if not line_number or column is None or line_number > len(lines):
            return None
        line = lines[line_number - 1]
        index = column - 1
        if index < 0 or index >= len(line) or line[index] not in ")]}":
            return None
        return "".join(lines[:line_number - 1] + [line[:index] + line[index + 1:]] + lines[line_number:])

    @staticmethod
    def _defined_names(source: str) -> set[str]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set()
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.update(argument.arg for argument in node.args.posonlyargs + node.args.args + node.args.kwonlyargs)
            elif isinstance(node, ast.Import):
                names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.update(alias.asname or alias.name for alias in node.names)
        return names

    def _correct_defined_name(self, source: str, missing: str) -> tuple[str, str] | None:
        candidates = difflib.get_close_matches(missing, self._defined_names(source), n=2, cutoff=0.85)
        if len(candidates) != 1:
            return None
        corrected = candidates[0]
        tree = ast.parse(source)
        lines = source.splitlines(keepends=True)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and node.id == missing
                and isinstance(node.ctx, ast.Load)
                and hasattr(node, "end_lineno")
                and node.end_lineno == node.lineno
            ):
                line_index = node.lineno - 1
                line = lines[line_index]
                start = node.col_offset
                end = node.end_col_offset
                lines[line_index] = line[:start] + corrected + line[end:]
                return corrected, "".join(lines)
        return None

    @staticmethod
    def _uses_name(source: str, name: str) -> bool:
        try:
            return any(isinstance(node, ast.Name) and node.id == name for node in ast.walk(ast.parse(source)))
        except SyntaxError:
            return False

    @staticmethod
    def _module_imported(source: str, module: str) -> bool:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return True
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(alias.name.split(".", 1)[0] == module for alias in node.names):
                return True
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".", 1)[0] == module:
                return True
        return False

    @staticmethod
    def _add_import(source: str, module: str) -> str:
        tree = ast.parse(source)
        insertion_line = 0
        if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
            insertion_line = getattr(tree.body[0], "end_lineno", tree.body[0].lineno)
        lines = source.splitlines(keepends=True)
        if insertion_line == 0 and lines and (lines[0].startswith("#!") or "coding" in lines[0]):
            insertion_line = 1
        lines.insert(insertion_line, f"import {module}\n")
        return "".join(lines)

    @staticmethod
    def _changed_lines(before: str, after: str) -> list[int]:
        import difflib

        changed = []
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm=""):
            if line.startswith("@@"):
                match = re.search(r"\+(\d+)", line)
                if match:
                    changed.append(int(match.group(1)))
        return sorted(set(changed))
