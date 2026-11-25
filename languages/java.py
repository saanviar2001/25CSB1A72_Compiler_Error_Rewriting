"""Java diagnostics, bounded execution, tree-sitter AST extraction, and healing."""

from __future__ import annotations

import base64
from functools import lru_cache
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .base import LanguageAdapter


_OUTPUT_LIMIT_BYTES = 1024 * 1024
_TIMEOUT_SECONDS = 5
_COMPILE_TIMEOUT_SECONDS = 30
_DIAGNOSTIC = re.compile(r"^(.*\.java):(\d+): (error|warning):\s*(.*)$")
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_MAIN_METHOD = re.compile(r"\bstatic\s+(?:public\s+)?void\s+main\s*\(\s*String\s*\[\s*\]\s+\w+\s*\)")

@lru_cache(maxsize=1)
def java_runtime_executable() -> str | None:
    """Return the first launchable Java runtime, skipping invalid JDK launchers."""
    compiler = shutil.which("javac")
    candidates: list[str] = []

    if compiler:
        jdk_dir = Path(compiler).resolve().parent
        candidates.append(str(jdk_dir / ("java.exe" if os.name == "nt" else "java")))

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(str(Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")))

    for path in ("C:/Program Files/Java/latest/jre-1.8/bin/java.exe", "C:/Program Files/Java/jre1.8.0_431/bin/java.exe"):
        candidates.append(path)

    # PATH-supplied java is often a stale JRE; keep it as a last fallback.
    path_java = shutil.which("java")
    if path_java:
        candidates.append(path_java)

    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            if not Path(candidate).is_file() or Path(candidate).stat().st_size == 0:
                continue
            completed = subprocess.run(
                [candidate, "-version"], capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0 and (completed.stderr or completed.stdout).strip():
            return candidate
    return None


@lru_cache(maxsize=1)
def _java_target_release() -> str | None:
    runtime = java_runtime_executable()
    if runtime is None:
        return None
    try:
        completed = subprocess.run(
            [runtime, "-version"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    version = completed.stderr or completed.stdout
    match = re.search(r'version "([^"]+)"', version)
    if not match:
        return None
    value = match.group(1)
    if value.startswith("1."):
        runtime_major = int(value.split(".", 2)[1])
    else:
        runtime_major = int(value.split(".", 1)[0])
    compiler = shutil.which("javac")
    if not compiler:
        return str(runtime_major)
    try:
        compiler_result = subprocess.run(
            [compiler, "-version"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return str(runtime_major)
    compiler_match = re.search(r"javac\s+(\d+)", compiler_result.stderr or compiler_result.stdout)
    if compiler_match and runtime_major < int(compiler_match.group(1)):
        return str(runtime_major)
    return None


@lru_cache(maxsize=1)
def _target_options() -> list[str]:
    release = _java_target_release()
    compiler = shutil.which("javac")
    if not release or not compiler:
        return []
    try:
        completed = subprocess.run([compiler, "-version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    match = re.search(r"javac\s+(\d+)", completed.stderr or completed.stdout)
    compiler_major = int(match.group(1)) if match else 0
    if compiler_major >= 9:
        return ["--release", release]
    return ["-source", release, "-target", release, "-Xlint:-options"]


def _bounded_process(
    command: list[str],
    cwd: Path,
    timeout: int = _TIMEOUT_SECONDS,
    input_text: str | None = None,
) -> dict[str, Any]:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
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
        try:
            process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
    except OSError as error:
        process.kill()
        process.wait()
        raise RuntimeError(f"Could not run Java tool `{command[0]}`: {error}") from error
    finally:
        for reader in readers:
            reader.join()
        if writer is not None:
            writer.join()

    return {
        "returncode": process.returncode,
        "stdout": output["stdout"].decode("utf-8", errors="replace").replace("\r\n", "\n"),
        "stderr": output["stderr"].decode("utf-8", errors="replace").replace("\r\n", "\n"),
        "timed_out": timed_out,
        "output_limited": state["limited"],
    }


def _diagnostic_category(message: str) -> str:
    lowered = message.lower()
    if any(fragment in lowered for fragment in ("';' expected", "illegal start", "reached end of file", "unclosed", "not a statement")):
        return "syntax_error"
    if "package " in lowered and " does not exist" in lowered:
        return "import_error"
    if "cannot find symbol" in lowered:
        return "name_resolution"
    if "does not exist" in lowered:
        return "import_error"
    if "incompatible types" in lowered or "bad operand" in lowered or "no suitable method" in lowered:
        return "type_error"
    if "has private access" in lowered or "is not public" in lowered:
        return "access_error"
    return "compile_error"


def _parse_diagnostics(output: str, source_path: Path) -> list[dict[str, Any]]:
    diagnostics = []
    current: dict[str, Any] | None = None
    lines = output.splitlines()
    for index, line in enumerate(lines):
        match = _DIAGNOSTIC.match(line)
        if match:
            if current is not None:
                diagnostics.append(current)
            source_name, line_number, severity, message = match.groups()
            column = None
            if index + 2 < len(lines) and lines[index + 2].lstrip().startswith("^"):
                column = len(lines[index + 2]) - len(lines[index + 2].lstrip())
            category = _diagnostic_category(message)
            current = {
                "type": "WARNING" if severity == "warning" else "ERROR",
                "exception_type": "CompilerWarning" if severity == "warning" else "CompilationError",
                "raw": line,
                "message": message,
                "line": int(line_number),
                "column": column,
                "caret": lines[index + 2] if column is not None else None,
                "category": category,
                "confidence": 1.0,
            }
        elif current is not None and re.match(r"\s*(?:symbol|location|reason|required|found):", line):
            current["message"] += "\n" + line.strip()
    if current is not None:
        diagnostics.append(current)
    return diagnostics


def _unclosed_java_braces(source: str) -> int:
    tokens = re.findall(
        r"//[^\r\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[{}]",
        source,
        flags=re.DOTALL,
    )
    depth = 0
    for token in tokens:
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
    return depth


def _missing_semicolon_offset(source: str) -> int | None:
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_java
    except ImportError:
        return None
    source_bytes = source.encode("utf-8")
    root = Parser(Language(tree_sitter_java.language())).parse(source_bytes).root_node
    pending = [root]
    while pending:
        node = pending.pop()
        if node.is_missing and node.type == ";":
            return node.start_byte
        pending.extend(reversed(node.children))
    return None


class JavaAdapter(LanguageAdapter):
    name = "java"
    display_name = "Java"
    extensions = (".java",)
    primary_tools = ("javac", "java")
    categorization_engine = "rule-based categorizer (javac messages)"
    implemented = True

    def _tool(self, name: str) -> str:
        tool = java_runtime_executable() if name == "java" else shutil.which(name)
        if tool is None:
            raise RuntimeError(f"Java support requires `{name}`, but it was not found on PATH.")
        return tool

    @staticmethod
    def has_main_method(source: str) -> bool:
        return bool(_MAIN_METHOD.search(source))

    def _compile(self, source_path: Path, output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        return _bounded_process(
            [
                self._tool("javac"), "-encoding", "UTF-8", "-proc:none",
                *_target_options(),
                "-Xmaxerrs", "50", "-Xmaxwarns", "50",
                "-d", str(output_dir), str(source_path),
            ],
            source_path.parent,
            timeout=_COMPILE_TIMEOUT_SECONDS,
        )

    def _main_class(self, source_path: Path) -> str:
        source = source_path.read_text(encoding="utf-8")
        package = _PACKAGE.search(source)
        return f"{package.group(1)}.{source_path.stem}" if package else source_path.stem

    def _run_compiled(
        self,
        source_path: Path,
        output_dir: Path,
        input_text: str | None = None,
    ) -> dict[str, Any]:
        return _bounded_process(
            [self._tool("java"), "-cp", str(output_dir), self._main_class(source_path)],
            source_path.parent,
            input_text=input_text,
        )

    def check(self, source_path: Path, execute_runtime: bool = True) -> list[dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix="java-compile-") as directory:
            output_dir = Path(directory) / "classes"
            compiled = self._compile(source_path, output_dir)
            if compiled["timed_out"]:
                return [self._runtime_diagnostic("TimeoutError", f"Java compilation exceeded the {_COMPILE_TIMEOUT_SECONDS}-second limit.", None)]
            if compiled["output_limited"]:
                return [self._runtime_diagnostic("OutputLimitError", "Java compiler output exceeded the 1 MiB limit.", None)]
            if compiled["returncode"] != 0:
                return [self._enrich(item) for item in _parse_diagnostics(
                    compiled["stderr"] + "\n" + compiled["stdout"], source_path
                )] or [self._runtime_diagnostic(
                    "CompilationError", f"javac exited with status {compiled['returncode']}.", None
                )]

            source = source_path.read_text(encoding="utf-8")
            if not execute_runtime or not self.has_main_method(source):
                return []
            result = self._run_compiled(source_path, output_dir)
            if result["timed_out"]:
                return [self._runtime_diagnostic("TimeoutError", "Java runtime check exceeded the 5-second limit.", None)]
            if result["output_limited"]:
                return [self._runtime_diagnostic("OutputLimitError", "Java runtime output exceeded the 1 MiB limit.", None)]
            if result["returncode"] == 0:
                return []
            return [self._runtime_diagnostic(
                *_read_java_exception(result["stderr"], self._main_class(source_path), result["returncode"])
            )]

    def _runtime_diagnostic(
        self,
        exception_type: str,
        message: str,
        line: int | None,
    ) -> dict[str, Any]:
        return self._enrich({
            "type": "ERROR",
            "exception_type": exception_type,
            "raw": f"{exception_type}: {message}",
            "message": message,
            "line": line,
            "column": None,
        })

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
            category = diagnostic.get("category")
            exception_type = diagnostic.get("exception_type", "")
            message = str(diagnostic.get("message") or "")
        else:
            category = None
            exception_type = type(diagnostic).__name__
            message = str(diagnostic)
        if not category:
            category = _diagnostic_category(message)
        if exception_type in {"ArithmeticException"} or "division by zero" in message.lower() or "/ by zero" in message.lower():
            category = "arithmetic_error"
        elif exception_type in {"NullPointerException", "ArrayIndexOutOfBoundsException", "IndexOutOfBoundsException"}:
            category = "runtime_error"
        return category, self.categorization_engine

    def explain(self, diagnostic: Any) -> str:
        if isinstance(diagnostic, dict):
            kind = diagnostic.get("exception_type", "CompilationError")
            message = str(diagnostic.get("message") or "")
            category = diagnostic.get("category", "")
        else:
            kind, message, category = type(diagnostic).__name__, str(diagnostic), ""
        lowered = message.lower()
        if "';' expected" in lowered:
            what, why, how = (
                "javac found a Java statement without its required semicolon.",
                "The statement ends before a closing brace or the next statement begins.",
                "Add a semicolon after the complete statement.",
            )
        elif "reached end of file while parsing" in lowered:
            what, why, how = (
                "javac reached the end of the file while parsing an unfinished construct.",
                "A closing brace is likely missing from a class, method, or control block.",
                "Match each opening brace with its closing brace and compile again.",
            )
        elif "illegal start of" in lowered or "not a statement" in lowered:
            what, why, how = (
                "javac found a token where a Java expression or declaration cannot begin.",
                "A preceding delimiter, keyword, or statement may be missing or misplaced.",
                "Review the reported line and the statement immediately before it.",
            )
        elif "unclosed string literal" in lowered or "unclosed character literal" in lowered:
            what, why, how = (
                "A string or character literal is missing its closing quote.",
                "The lexer reached the line ending before finding a matching quote.",
                "Close the literal and escape any quote that belongs inside it.",
            )
        elif "cannot find symbol" in lowered:
            what, why, how = (
                "javac cannot resolve a referenced class, method, or variable.",
                "The symbol may be misspelled, undeclared, out of scope, or missing an import.",
                "Correct the name or declaration, or add the appropriate import.",
            )
        elif "package " in lowered and " does not exist" in lowered:
            what, why, how = (
                "javac cannot find the requested package.",
                "The package may be misspelled, unavailable in the JDK, or absent from the classpath.",
                "Correct the import or add the required dependency to the compile classpath.",
            )
        elif "incompatible types" in lowered:
            what, why, how = (
                "javac found a value whose type cannot be assigned to the target type.",
                "Java does not implicitly convert between these incompatible types.",
                "Use the expected type or an explicit conversion that preserves the intended value.",
            )
        elif "bad operand types" in lowered or "bad operand type" in lowered:
            what, why, how = (
                "An operator was applied to operands with unsupported Java types.",
                "The operator has no defined operation for the reported operand types.",
                "Convert or restructure the operands to use a valid operation.",
            )
        elif "no suitable method found" in lowered:
            what, why, how = (
                "javac could not find a method overload that accepts these arguments.",
                "The argument count or argument types do not match an available overload.",
                "Pass arguments matching an overload or update the method declaration.",
            )
        elif "might not have been initialized" in lowered or "might not have been assigned" in lowered:
            what, why, how = (
                "A local variable may be read before it receives a value.",
                "Java definite-assignment analysis cannot prove every path initializes it.",
                "Initialize the variable at declaration or on every path before reading it.",
            )
        elif "class " in lowered and " is public, should be declared in a file named " in lowered:
            what, why, how = (
                "The public class name does not match the Java source filename.",
                "Java requires a public top-level class to be stored in a same-named .java file.",
                "Rename the file to the public class name followed by .java.",
            )
        elif category == "syntax_error":
            what, why, how = (
                "The Java compiler could not parse this source.",
                "A token or statement does not match Java's grammar at the reported location.",
                "Correct the indicated syntax and compile the source again.",
            )
        elif category == "name_resolution":
            what, why, how = (
                "The source refers to a symbol javac cannot resolve.",
                "The name may be misspelled, out of scope, or not declared or imported.",
                "Correct the name, declaration, scope, or import.",
            )
        elif category == "import_error":
            what, why, how = (
                "Java could not resolve a referenced package or class.",
                "The dependency may be missing or the package name may be incorrect.",
                "Check the package/import name and compile with the required dependency on the classpath.",
            )
        elif category == "type_error":
            what, why, how = (
                "An expression has a type incompatible with the operation or target.",
                "Java's static type checker rejected the conversion, operand, or method invocation.",
                "Use a value of the expected type or perform an explicit, valid conversion.",
            )
        elif category == "arithmetic_error":
            what, why, how = (
                "The Java program attempted an invalid arithmetic operation.",
                "An integer division or remainder operation used a zero divisor.",
                "Validate the divisor or handle the zero case before the operation.",
            )
        elif kind == "TimeoutError":
            what, why, how = (
                "The Java operation exceeded its time limit.",
                message,
                "Review long-running loops or reduce the work performed by the source.",
            )
        else:
            what, why, how = (
                f"The Java compiler or runtime reported {kind}.",
                message or "The operation failed while the program was running.",
                "Review the reported location and fix the underlying Java error.",
            )
        return f"What happened:\n {what}\n\nWhy it happened:\n {why}\n\nHow to fix:\n {how}"

    def heal_strategies(self) -> tuple[str, ...]:
        return (
            "insert_missing_semicolon",
            "add_missing_import",
            "convert_string_int_literal",
            "append_missing_closing_brace",
        )

    def run_tests(self, source_path: Path, test_spec: str | None = None) -> dict[str, Any]:
        if not self.has_main_method(source_path.read_text(encoding="utf-8")):
            return {
                "checked": False, "passed": None, "cases": [],
                "note": "Java behavioral tests require a main method; no code was executed.",
            }
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
            with tempfile.TemporaryDirectory(prefix="java-test-") as directory:
                output_dir = Path(directory) / "classes"
                compiled = self._compile(source_path, output_dir)
                if compiled["returncode"] != 0 or compiled["timed_out"] or compiled["output_limited"]:
                    results.append({
                        "number": number, "passed": False, "status": "compile_failed",
                        "stdin": case.get("stdin", ""), "expected_stdout": case["expected_stdout"],
                        "actual_stdout": "", "stderr": compiled["stderr"],
                        "note": "The Java source did not compile for this behavioral test.",
                    })
                    continue
                result = self._run_compiled(source_path, output_dir, case.get("stdin", ""))
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
                "number": number, "passed": passed, "status": status,
                "stdin": case.get("stdin", ""), "expected_stdout": case["expected_stdout"],
                "actual_stdout": result["stdout"], "stderr": result["stderr"],
                "note": (
                    "Output matched exactly." if passed else
                    "Timed out after 5 seconds." if result["timed_out"] else
                    "Exceeded the 1 MiB output limit." if result["output_limited"] else
                    f"Exited with status {result['returncode']} or output differed."
                ),
            })
        failed = [case for case in results if not case["passed"]]
        if failed:
            numbers = ", ".join(str(case["number"]) for case in failed)
            note = f"Behavioral test case(s) {numbers} failed in the Java runtime."
        else:
            note = f"All {len(results)} behavioral test case(s) passed in the Java runtime."
        return {"checked": True, "passed": not failed, "cases": results, "note": note}

    def build_ast(self, source_path: Path, **options: Any) -> dict[str, Any]:
        source = source_path.read_text(encoding="utf-8")
        node_limit = max(1, min(int(options.get("node_limit", 150)), 10000))
        depth_limit = max(0, min(int(options.get("depth_limit", 12)), 100))
        expand_full = bool(options.get("expand_full", False))
        try:
            from importlib.metadata import version
            from tree_sitter import Language, Parser
            import tree_sitter_java

            parser = Parser(Language(tree_sitter_java.language()))
            parsed = parser.parse(source.encode("utf-8"))
            root = parsed.root_node
            pending_nodes = [root]
            nodes_before_limits = 0
            while pending_nodes:
                node = pending_nodes.pop()
                nodes_before_limits += 1
                pending_nodes.extend(node.children)

            display_node_limit = 10000 if expand_full else node_limit
            display_depth_limit = 100 if expand_full else depth_limit
            converted_nodes = 0

            def convert(node, depth: int) -> dict[str, Any] | None:
                nonlocal converted_nodes
                if converted_nodes >= display_node_limit or depth > display_depth_limit:
                    return None
                converted_nodes += 1
                start = node.start_point
                details = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                details = re.sub(r"\s+", " ", details).strip()
                if len(details) > 120:
                    details = details[:117] + "..."
                if node.is_missing:
                    details = "missing " + node.type
                children = []
                for child in node.children:
                    converted = convert(child, depth + 1)
                    if converted is None:
                        if converted_nodes < display_node_limit:
                            converted_nodes += 1
                            children.append({
                                "type": "...", "details": "AST display limit reached",
                                "line": None, "children": [],
                            })
                        break
                    children.append(converted)
                return {
                    "type": node.type,
                    "details": details or None,
                    "line": start.row + 1,
                    "children": children,
                }

            converted_root = convert(root, 0)
            roots = [converted_root] if converted_root else []
            engine = f"tree-sitter-java {version('tree-sitter-java')} + Graphviz"
            approximation = False
            has_error = parsed.root_node.has_error
            before_limits = nodes_before_limits
        except (ImportError, AttributeError, TypeError, ValueError):
            roots = [{
                "type": "CompilationUnit",
                "details": f"{source_path.name} (tree-sitter-java unavailable)",
                "line": 1,
                "children": [],
            }]
            engine = "regex fallback + Graphviz (approximate; tree-sitter-java unavailable)"
            approximation = True
            has_error = True
            before_limits = _count_nodes(roots)

        displayed = roots
        from graphviz import Digraph

        dot = Digraph(comment="Java AST")
        dot.attr(rankdir="TB")
        node_index = 0

        def add_nodes(node: dict[str, Any], parent: str | None = None) -> None:
            nonlocal node_index
            node_index += 1
            current_id = f"n{node_index}"
            label = str(node.get("type") or "node")
            if node.get("details"):
                label += "\n" + str(node["details"])
            dot.node(current_id, label[:180])
            if parent:
                dot.edge(parent, current_id)
            for child in node.get("children", []):
                add_nodes(child, current_id)

        for root_node in displayed:
            add_nodes(root_node)

        image_path = source_path.parent / f"java_ast_{uuid.uuid4().hex}.png"
        image_path.write_bytes(dot.pipe(format="png"))
        return {
            "available": True,
            "tree": displayed,
            "image": "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode("ascii"),
            "path": str(image_path),
            "engine": engine,
            "approximation": approximation,
            "has_error": has_error,
            "nodes_before_limits": before_limits,
            "nodes_displayed": _count_nodes(displayed),
            "truncation_markers": _truncation_markers(displayed),
        }

    def security_rules(self) -> tuple[str, ...]:
        return (
            "Runtime.exec/ProcessBuilder with non-literal arguments",
            "SQL built by string concatenation and passed to execute methods",
            "hardcoded credentials assigned from string literals",
            "java.util.Random used as a security-sensitive random source",
            "empty catch blocks that suppress exceptions",
            "printStackTrace() disclosure to process output",
            "ObjectInputStream.readObject() deserialization",
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
        strategies: list[str] = []
        diagnostics = self.check(source_path, execute_runtime=execute_runtime)
        
        # Loop up to 5 times to fix multiple errors in sequence
        for number in range(1, 6):
            if not diagnostics:
                break

            # Find the first error we ACTUALLY know how to fix (skip the ones we can't)
            patch = None
            target_diagnostic = None
            for diag in diagnostics:
                p = self._plan_patch(source, diag)
                if p is not None:
                    patch = p
                    target_diagnostic = diag
                    break

            if patch is None:
                # If we can't fix ANY of the remaining errors, stop
                break

            strategy, planned, updated = patch
            before = source
            source = updated
            source_path.write_text(source, encoding="utf-8")
            
            # Re-compile to see the new state
            diagnostics = self.check(source_path, execute_runtime=execute_runtime)
            
            if strategy not in strategies:
                strategies.append(strategy)
                
            diff = compute_diff(before, source)
            attempts.append({
                "number": number, 
                "strategy": strategy,
                "observed": f"{target_diagnostic['exception_type']}: {target_diagnostic['message']}",
                "planned": planned, 
                "action": f"Applied {strategy.replace('_', ' ')}.",
                "outcome": "The Java check passed after the patch." if not diagnostics else
                    f"The check still reports {diagnostics[0]['message']}",
                "error": target_diagnostic["message"],
                "before": before, 
                "after": source, 
                "changed": source != before,
                "compiles": not _has_compilation_failure(diagnostics), 
                "lines_changed": _changed_lines(before, source),
                "diff_html": format_diff_html(diff) if has_changes(diff) else "<i>No visible changes.</i>",
                "reasoning": planned, 
                "confidence": 0.9,
                "duration": 0.0,
            })

        if not diagnostics and not attempts:
            attempts.append({
                "number": 1, "strategy": "already_clean",
                "observed": "No Java compiler or runtime diagnostics were found.",
                "planned": "Keep the source unmodified.", "action": "No changes required.",
                "outcome": "Java checks passed without a patch.", "error": "",
                "before": original_source, "after": source, "changed": False,
                "compiles": True, "lines_changed": [], "diff_html": "",
                "reasoning": "No repair was necessary.", "confidence": 1.0, "duration": 0.0,
            })
        elif not attempts:
            item = diagnostics[0] if diagnostics else {}
            attempts.append({
                "number": 1, "strategy": "not_fixable",
                "observed": f"{item.get('exception_type', 'Error')}: {item.get('message', '')}",
                "planned": "Do not guess at program intent; leave the source unchanged.",
                "action": "No automatic patch was applied.",
                "outcome": "No supported, unambiguous Java repair strategy matched.",
                "error": item.get("message", ""), "before": original_source, "after": source,
                "changed": False, "compiles": False, "lines_changed": [], "diff_html": "",
                "reasoning": "No repair strategy matched safely.", "confidence": 1.0, "duration": 0.0,
            })

        can_execute = execute_runtime and self.has_main_method(source)
        behavior = self.run_tests(source_path, test_spec) if not diagnostics and can_execute else {
            "checked": False, "passed": None, "cases": [],
            "note": (
                "The Java check still reports an error, so behavioral tests were not run."
                if diagnostics else runtime_skip_reason or (
                    "Java runtime check skipped: no main method was found."
                    if execute_runtime else "Runtime check skipped."
                )
            ),
        }
        
        if diagnostics:
            verdict = "not_fixable"
            explanation = "The Java healer could not clear all diagnostics; manual correction is required."
        elif behavior["checked"] and behavior["passed"] is False:
            verdict = "compiles_but_behavioral_verification_failed"
            explanation = behavior["note"]
        elif behavior["checked"]:
            verdict = "fixed_and_verified" if attempts[0]["strategy"] != "already_clean" else "already_clean"
            explanation = behavior["note"]
        else:
            verdict = "compiles_not_behaviorally_verified" if attempts[0]["strategy"] != "already_clean" else "already_clean"
            explanation = (
                "The Java source passed static compiler checks; runtime check skipped."
                if not can_execute else
                "The Java source passed compiler and runtime checks, but no behavioral test specification was available."
            )
            
        with tempfile.TemporaryDirectory(prefix="java-final-check-") as directory:
            final_compile = self._compile(source_path, Path(directory) / "classes")
            compiles = final_compile["returncode"] == 0 and not final_compile["timed_out"]
            
        labels = {
            "already_clean": "Nothing to fix - the code already passes Java checks",
            "fixed_and_verified": "Fixed and verified",
            "compiles_not_behaviorally_verified": "Java checks passed but not behaviorally verified",
            "compiles_but_behavioral_verification_failed": "Java checks passed but behavioral verification failed",
            "not_fixable": "Could not be automatically fixed - requires manual correction",
        }
        
        return {
            "filename": source_path.name, "original_source": original_source, "fixed_source": source,
            "compiles": compiles, "verdict": verdict, "verdict_label": labels[verdict],
            "explanation": explanation, "manual_fix": diagnostics[0]["message"] if diagnostics else "",
            "attempts": attempts, "strategies_used": strategies, "behavior": behavior,
            "runtime_check": {
                "requested": execute_runtime if runtime_requested is None else runtime_requested,
                "executed": can_execute and not diagnostics,
                "message": "Runtime check completed." if can_execute and not diagnostics else
                    runtime_skip_reason or (
                        "Java runtime check skipped: no main method was found."
                        if execute_runtime and not diagnostics else "Runtime check skipped."
                    ),
            },
            "transparency": {
                "language": self.display_name,
                "classifier": self.categorization_engine,
                "repair_strategies": strategies or ["No repair strategy applied"],
                "compiler": {"name": "javac", "version": "21"}, # Fallback version text
                "behavioral_tests": "Ran" if behavior.get("checked") else "Did not run",
                "runtime_note": "Runtime check completed." if can_execute else runtime_skip_reason or (
                    "Java runtime check skipped: no main method was found."
                    if execute_runtime and not diagnostics else "Runtime check skipped."
                ),
            },
}

    def _plan_patch(self, source: str, diagnostic: dict[str, Any]) -> tuple[str, str, str] | None:
        message = diagnostic.get("message", "")
        line_number = diagnostic.get("line")
        lines = source.splitlines(keepends=True)
        
        if not line_number or not (1 <= line_number <= len(lines)):
            return None

        line = lines[line_number - 1]

        # 1. Fix undeclared variables (e.g., number = "100";)
        if "cannot find symbol" in message and "variable" in message:
            symbol_match = re.search(r"symbol:\s+variable\s+(\w+)", message)
            if symbol_match:
                var = symbol_match.group(1)
                indent = re.match(r"^(\s*)", line).group(1)
                assign_match = re.search(rf"\b{var}\s*=\s*(.+?);", line)
                if assign_match:
                    val = assign_match.group(1).strip()
                    if val.startswith('"') and val.endswith('"') and val[1:-1].isdigit():
                        fixed_line = line.replace(f"{var} = {val}", f"int {var} = {val[1:-1]}", 1)
                        lines[line_number - 1] = fixed_line
                        return "declare_and_fix_type", f"Declared '{var}' as an int and removed the string quotes.", "".join(lines)
                    else:
                        var_type = "String" if val.startswith('"') else "int"
                        fixed_line = line.replace(f"{var} =", f"{var_type} {var} =", 1)
                        lines[line_number - 1] = fixed_line
                        return "declare_missing_variable", f"Declared missing variable '{var}' as a {var_type}.", "".join(lines)

        # 2. Fix String to int conversion
        if "incompatible types" in message and "String cannot be converted to int" in message:
            match = re.search(r'=\s*"([+-]?\d+)"(?=\s*;)', line)
            if match:
                updated_line = line[:match.start()] + '= ' + match.group(1) + line[match.end():]
                lines[line_number - 1] = updated_line
                return "convert_string_int_literal", "Remove quotes from numeric string literal.", "".join(lines)

        # 3. Fix assignment in condition (e.g., if (x = 5) -> if (x == 5))
        if re.search(r"\b(if|while|for)\s*\([^=!<>]*(?<![=!<>])=(?!=)[^)]*\)", line):
            fixed_line = re.sub(r"([^=!<>])=(?!=)", r"\1==", line)
            if fixed_line != line:
                lines[line_number - 1] = fixed_line
                return "assignment_in_condition", "Fix assignment used as condition (changed = to ==).", "".join(lines)

        # 4. Fix missing semicolons (CRITICAL FIX: ignore comments when checking)
        if "';' expected" in message or "not a statement" in message:
            trimmed = line.rstrip("\r\n")
            
            # Find where the comment starts, and only look at the code before it
            comment_index = trimmed.find("//")
            if comment_index != -1:
                code_part = trimmed[:comment_index].rstrip()
                comment_part = trimmed[comment_index:]
            else:
                code_part = trimmed
                comment_part = ""

            if code_part and not code_part.endswith(";") and not re.match(
                r"\s*(?:if|else|for|while|switch|try|catch|finally|class|interface|enum|package|import)\b",
                code_part,
            ):
                # Add semicolon to the code part, then put the comment back
                new_line = code_part + ";" + (" " + comment_part if comment_part else "") + "\n"
                lines[line_number - 1] = new_line
                return "insert_missing_semicolon", "Insert the missing semicolon at the end of the Java statement.", "".join(lines)

        # 5. Add missing imports
        if "cannot find symbol" in message and "class" in message:
            symbol = re.search(r"symbol:\s+class\s+(\w+)", message)
            imports = {
                "ArrayList": "java.util.ArrayList", "List": "java.util.List",
                "Map": "java.util.Map", "HashMap": "java.util.HashMap",
                "Set": "java.util.Set", "HashSet": "java.util.HashSet",
                "Scanner": "java.util.Scanner"
            }
            if symbol and symbol.group(1) in imports:
                import_name = imports[symbol.group(1)]
                package = import_name.rsplit(".", 1)[0]
                if not re.search(rf"(?m)^\s*import\s+{re.escape(import_name)}\s*;", source) and not re.search(
                    rf"(?m)^\s*import\s+{re.escape(package)}\.\*\s*;", source
                ):
                    package_match = _PACKAGE.search(source)
                    insertion = package_match.end() if package_match else 0
                    prefix = source[:insertion]
                    suffix = source[insertion:]
                    separator = "" if not prefix or prefix.endswith("\n") else "\n"
                    updated = prefix + separator + f"import {import_name};\n" + suffix.lstrip("\r\n")
                    return "add_missing_import", f"Add the standard-library import for {symbol.group(1)}.", updated

        # 6. Append missing closing brace at the very end of the file
        if "reached end of file while parsing" in message:
            unclosed = _unclosed_java_braces(source)
            if unclosed > 0:
                updated = source.rstrip() + ("\n" if source.endswith(("\n", "\r")) else "\n")
                updated += "}\n" * unclosed
                return "append_missing_closing_brace", f"Append {unclosed} missing closing brace(s).", updated

        return None
def _read_java_exception(stderr: str, main_class: str, returncode: int) -> tuple[str, str, int | None]:
    exception = re.search(r"Exception in thread \"[^\"]+\" ([\w.$]+)(?:: (.*))?", stderr)
    stack_location = re.search(
        rf"\bat {re.escape(main_class)}\.[^(]+\([^:]+:(\d+)\)",
        stderr,
    )
    if exception:
        return exception.group(1).rsplit(".", 1)[-1], exception.group(2) or exception.group(1), (
            int(stack_location.group(1)) if stack_location else None
        )
    return "RuntimeError", stderr.strip() or f"Java exited with status {returncode}.", (
        int(stack_location.group(1)) if stack_location else None
    )


def _has_compilation_failure(diagnostics: list[dict[str, Any]]) -> bool:
    return any(
        item.get("exception_type") == "CompilationError"
        or (
            item.get("exception_type") == "TimeoutError"
            and "compilation" in str(item.get("message", "")).lower()
        )
        or (
            item.get("exception_type") == "OutputLimitError"
            and "compiler" in str(item.get("message", "")).lower()
        )
        for item in diagnostics
    )


def _count_nodes(nodes: list[dict[str, Any]]) -> int:
    return sum(1 + _count_nodes(node.get("children", [])) for node in nodes)


def _truncation_markers(nodes: list[dict[str, Any]]) -> list[str]:
    markers = []
    for node in nodes:
        if node.get("type") == "...":
            markers.append(node.get("details", "... more nodes"))
        markers.extend(_truncation_markers(node.get("children", [])))
    return markers


def _changed_lines(before: str, after: str) -> list[int]:
    import difflib

    matcher = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines())
    return sorted({
        line
        for tag, _start_before, _end_before, start_after, end_after in matcher.get_opcodes()
        if tag != "equal"
        for line in range(start_after + 1, end_after + 1)
    })
