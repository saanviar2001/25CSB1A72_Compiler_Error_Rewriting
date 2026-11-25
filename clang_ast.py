"""Clang-based AST extraction and Graphviz rendering.

Public functions kept compatible with the previous module:
    extract_ast_tree(...)
    print_ast_tree(...)
    render_ast_png(...)

Requirements for PNG diagrams:
    pip install graphviz
    Install the Graphviz desktop application and make sure `dot` is on PATH.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


AST_KINDS = {
    "TranslationUnitDecl", "NamespaceDecl", "FunctionDecl", "CXXMethodDecl",
    "CXXConstructorDecl", "CXXDestructorDecl", "FunctionTemplateDecl",
    "ClassTemplateDecl", "CXXRecordDecl", "RecordDecl", "EnumDecl",
    "VarDecl", "FieldDecl", "ParmVarDecl", "IfStmt", "ForStmt",
    "CXXForRangeStmt", "WhileStmt", "DoStmt", "SwitchStmt", "CaseStmt",
    "DefaultStmt", "ReturnStmt", "BreakStmt", "ContinueStmt", "CompoundStmt",
    "CXXTryStmt", "CXXCatchStmt", "CXXThrowExpr", "BinaryOperator",
    "CompoundAssignOperator", "UnaryOperator", "ConditionalOperator",
    "CallExpr", "CXXOperatorCallExpr", "CXXMemberCallExpr", "CXXNewExpr",
    "CXXDeleteExpr", "LambdaExpr", "DeclRefExpr", "MemberExpr",
    "IntegerLiteral", "FloatingLiteral", "StringLiteral", "CharacterLiteral",
    "CXXBoolLiteralExpr", "CXXNullPtrLiteralExpr",
}

DECL_FAMILIES = {
    "TranslationUnitDecl", "NamespaceDecl", "FunctionDecl", "CXXMethodDecl",
    "CXXConstructorDecl", "CXXDestructorDecl", "FunctionTemplateDecl",
    "ClassTemplateDecl", "CXXRecordDecl", "RecordDecl", "EnumDecl",
    "VarDecl", "FieldDecl", "ParmVarDecl",
}
STMT_FAMILIES = {
    "IfStmt", "ForStmt", "CXXForRangeStmt", "WhileStmt", "DoStmt",
    "SwitchStmt", "CaseStmt", "DefaultStmt", "ReturnStmt", "BreakStmt",
    "ContinueStmt", "CompoundStmt", "CXXTryStmt", "CXXCatchStmt",
}

LABEL_OVERRIDES = {
    "TranslationUnitDecl": "translation unit", "CXXRecordDecl": "class",
    "RecordDecl": "record", "DoStmt": "do-while",
    "CXXForRangeStmt": "range-for", "CXXTryStmt": "try",
    "CXXCatchStmt": "catch", "CXXThrowExpr": "throw",
    "CXXConstructorDecl": "constructor", "CXXDestructorDecl": "destructor",
    "CXXMethodDecl": "method", "CXXNewExpr": "new",
    "CXXDeleteExpr": "delete", "CXXOperatorCallExpr": "operator call",
    "CXXMemberCallExpr": "member call",
}


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _node_name(node: Dict[str, Any]) -> str:
    for key in ("name", "declName", "cxxCtorName", "cxxDtorName"):
        value = node.get(key)
        if value:
            return str(value)
    typ = node.get("type")
    if isinstance(typ, dict) and typ.get("qualType"):
        return str(typ["qualType"])
    return ""


def _node_label(kind: str, node: Dict[str, Any]) -> str:
    name = _node_name(node)
    typ = node.get("type") if isinstance(node.get("type"), dict) else {}
    qual_type = typ.get("qualType", "")
    opcode = node.get("opcode", "")
    value = node.get("value", "")

    if kind == "CXXRecordDecl":
        bases = []
        for base in node.get("bases", []) or []:
            if isinstance(base, dict):
                base_type = base.get("type", {})
                if isinstance(base_type, dict) and base_type.get("qualType"):
                    bases.append(base_type["qualType"])
        label = f"class {name}" if name else "class"
        return label + (f" : {', '.join(bases)}" if bases else "")
    if kind in {"FunctionDecl", "CXXMethodDecl", "CXXConstructorDecl", "CXXDestructorDecl"}:
        return f"{LABEL_OVERRIDES.get(kind, 'function')} {name}".strip()
    if kind in {"VarDecl", "FieldDecl", "ParmVarDecl"}:
        return f"{qual_type} {name}".strip() or name or kind
    if kind in {"NamespaceDecl", "EnumDecl", "ClassTemplateDecl", "FunctionTemplateDecl"}:
        return f"{LABEL_OVERRIDES.get(kind, kind)} {name}".strip()
    if kind in {"BinaryOperator", "CompoundAssignOperator", "UnaryOperator"}:
        return opcode or kind
    if kind == "CallExpr":
        return f"call {name}" if name else "function call"
    if kind == "CXXMemberCallExpr":
        return "member function call"
    if kind == "CXXOperatorCallExpr":
        return f"operator {opcode}" if opcode else "operator call"
    if kind == "CXXNewExpr":
        return f"new {qual_type}".strip()
    if kind == "CXXDeleteExpr":
        return "delete"
    if kind in {"IntegerLiteral", "FloatingLiteral", "StringLiteral", "CharacterLiteral", "CXXBoolLiteralExpr"}:
        return str(value) if value != "" else kind
    if kind == "DeclRefExpr":
        referenced = node.get("referencedDecl", {})
        return f"reference {referenced.get('name', '')}".strip()
    if kind == "MemberExpr":
        return f"member {name}".strip()
    if kind == "CaseStmt":
        return f"case {value}".strip()
    return LABEL_OVERRIDES.get(kind, name or kind)


def _node_family(kind: str) -> str:
    if kind in DECL_FAMILIES:
        return "declaration"
    if kind in STMT_FAMILIES:
        return "statement"
    return "expression" if kind in AST_KINDS else "other"


def _line_for_node(node: Dict[str, Any], source_text: str) -> Optional[int]:
    loc = node.get("loc") if isinstance(node.get("loc"), dict) else {}
    source_range = node.get("range") if isinstance(node.get("range"), dict) else {}
    candidates = [loc, source_range.get("begin", {}), source_range.get("end", {})]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("line") is not None:
            return _safe_int(candidate.get("line"))
        offset = _safe_int(candidate.get("offset"))
        if offset is not None:
            return source_text.encode("utf-8", errors="replace")[:offset].count(b"\n") + 1
    return None


def _collect_ast(node: Any, source_text: str) -> List[Dict[str, Any]]:
    """Convert Clang JSON into a compact, display-friendly tree."""
    if not isinstance(node, dict) or node.get("isImplicit"):
        return []

    kind = node.get("kind", "")
    children: List[Dict[str, Any]] = []
    for child in node.get("inner", []) or []:
        children.extend(_collect_ast(child, source_text))

    # Flatten uninteresting Clang wrapper nodes, but retain their descendants.
    if kind not in AST_KINDS:
        return children

    # Avoid duplicate forward declarations generated by Clang.
    if kind == "CXXRecordDecl" and node.get("isReferenced") is False and not node.get("completeDefinition"):
        return children

    return [{
        "type": kind,
        "details": _node_label(kind, node),
        "line": _line_for_node(node, source_text),
        "family": _node_family(kind),
        "children": children,
    }]


def _fallback_tree(source_path: Path) -> List[Dict[str, Any]]:
    """Try the project's existing fallback parser, if ast_extractor.py exists."""
    try:
        from ast_extractor import extract_ast, parse_ast_to_tree, build_fallback_ast

        raw_text = extract_ast(str(source_path))
        raw_nodes = parse_ast_to_tree(raw_text, str(source_path)) if raw_text else []
        if not raw_nodes:
            source = source_path.read_text(encoding="utf-8", errors="replace")
            raw_nodes = build_fallback_ast(source)

        def normalize(nodes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
            result = []
            for node in nodes or []:
                kind = node.get("type", "node")
                result.append({
                    "type": kind,
                    "details": node.get("details", kind),
                    "line": node.get("line"),
                    "family": _node_family(kind),
                    "children": normalize(node.get("children", [])),
                })
            return result

        normalized = normalize(raw_nodes)
        if normalized:
            return normalized
    except Exception:
        pass

    # Last-resort visible root; this is not a true parsed AST.
    return [{
        "type": "TranslationUnitDecl", "details": "source file",
        "line": 1, "family": "declaration", "children": [],
    }]


def _subtree_size(node: Dict[str, Any]) -> int:
    return 1 + sum(_subtree_size(child) for child in node.get("children", []))


def _count_ast_nodes(nodes: List[Dict[str, Any]]) -> int:
    """Count AST nodes, excluding visual truncation markers."""
    return sum(
        (0 if node.get("type") == "..." else 1)
        + _count_ast_nodes(node.get("children", []))
        for node in nodes
    )


def _trim_tree(nodes: List[Dict[str, Any]], max_nodes: int = 150, max_depth: int = 12) -> List[Dict[str, Any]]:
    max_nodes = max(1, int(max_nodes or 150))
    max_depth = max(1, int(max_depth or 12))
    remaining = [max_nodes]

    def visit(items: List[Dict[str, Any]], depth: int) -> List[Dict[str, Any]]:
        output = []
        omitted = 0
        for node in items:
            if remaining[0] <= 0:
                omitted += _subtree_size(node)
                continue
            remaining[0] -= 1
            clone = {key: value for key, value in node.items() if key != "children"}
            children = node.get("children", [])
            if children and depth >= max_depth:
                child_count = sum(_subtree_size(child) for child in children)
                clone["children"] = [{
                    "type": "...", "details": f"... {child_count} more nodes",
                    "line": None, "family": "other", "children": [],
                }]
            else:
                clone["children"] = visit(children, depth + 1)
            output.append(clone)
        if omitted:
            output.append({
                "type": "...", "details": f"... {omitted} more nodes",
                "line": None, "family": "other", "children": [],
            })
        return output

    return visit(nodes, 0)


def _find_error_line(stderr_text: str) -> Optional[int]:
    if not stderr_text:
        return None
    # Matches paths such as main.cpp:12:5: error: ... and <stdin>:12:5: error: ...
    match = re.search(r":(\d+):(\d+):\s*(?:fatal\s+)?error\s*:", stderr_text)
    return int(match.group(1)) if match else None


def _annotate_error_line(tree: List[Dict[str, Any]], line_number: int) -> None:
    best = None
    best_distance = None

    def walk(nodes: List[Dict[str, Any]]) -> None:
        nonlocal best, best_distance
        for node in nodes:
            line = node.get("line")
            if line is not None:
                distance = abs(line - line_number)
                if best is None or distance < best_distance:
                    best, best_distance = node, distance
            walk(node.get("children", []))

    walk(tree)
    if best is not None:
        best["is_error_node"] = True
        best["details"] = f"{best.get('details', '')} [error near line {line_number}]"


def extract_ast_tree(file_path, node_limit=150, depth_limit=12, expand_full=False):
    """Extract a C/C++ AST. Returns (tree, used_fallback)."""
    source_path = Path(file_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source file does not exist: {source_path}")
    source_text = source_path.read_text(encoding="utf-8", errors="replace")

    clang = shutil.which("clang++")
    if not clang:
        tree = _fallback_tree(source_path)
        return (tree if expand_full else _trim_tree(tree, node_limit, depth_limit)), True

    command = [
        clang, "-fno-color-diagnostics", "-Xclang", "-ast-dump=json",
        "-fsyntax-only", "-x", "c++", str(source_path),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=45, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Clang timed out while generating the AST (45 seconds).") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not start clang++: {exc}") from exc

    try:
        ast_json = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        tree = _fallback_tree(source_path)
        return (tree if expand_full else _trim_tree(tree, node_limit, depth_limit)), True

    tree = _collect_ast(ast_json, source_text)
    if not tree:
        tree = _fallback_tree(source_path)
        return (tree if expand_full else _trim_tree(tree, node_limit, depth_limit)), True

    error_line = _find_error_line(result.stderr)
    if error_line:
        _annotate_error_line(tree, error_line)

    return (tree if expand_full else _trim_tree(tree, node_limit, depth_limit)), False


def print_ast_tree(tree, title=None):
    """Print the AST in an indented text format (useful for debugging)."""
    if title:
        print(title)

    def visit(nodes, depth):
        for node in nodes or []:
            details = node.get("details", "")
            line = f" @ line {node['line']}" if node.get("line") is not None else ""
            error = "  <-- ERROR" if node.get("is_error_node") else ""
            print(f"{'  ' * depth}{node.get('type', 'node')}: {details}{line}{error}")
            visit(node.get("children", []), depth + 1)

    visit(tree, 0)


def render_ast_png(tree, output_path=None, max_nodes=None, max_depth=None, expand_full=False):
    """Render the AST to PNG and return the generated PNG path.

    Raises a clear RuntimeError if either the Python graphviz package or the
    Graphviz `dot` executable is missing, or if Graphviz cannot render.
    """
    if not tree:
        tree = [{
            "type": "TranslationUnitDecl", "details": "empty source",
            "line": None, "family": "declaration", "children": [],
        }]

    if not expand_full:
        tree = _trim_tree(
            tree,
            max_nodes=150 if max_nodes is None else max_nodes,
            max_depth=12 if max_depth is None else max_depth,
        )

    # Check the native Graphviz executable separately from the Python package.
    dot_executable = shutil.which("dot")
    if not dot_executable:
        raise RuntimeError(
            "Graphviz 'dot' was not found. The Python graphviz package is not enough. "
            "Install Graphviz for Windows, add its bin folder to PATH "
            "(commonly C:\\Program Files\\Graphviz\\bin), then restart VS Code/terminal."
        )

    try:
        from graphviz import Digraph
    except ImportError as exc:
        raise RuntimeError(
            "The Python 'graphviz' package is missing. Install it with: python -m pip install graphviz"
        ) from exc

    if output_path is None:
        output = Path(tempfile.gettempdir()) / f"ast_{uuid.uuid4().hex}.png"
    else:
        output = Path(output_path).expanduser().resolve()
        if output.suffix.lower() != ".png":
            output = output.with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)

    graph = Digraph(name="AST", format="png", engine="dot")
    graph.attr(rankdir="TB", pad="0.25", nodesep="0.30", ranksep="0.55", bgcolor="white")
    graph.attr("graph", dpi="160")
    graph.attr("node", fontname="Arial", fontsize="10", style="filled,rounded", margin="0.14,0.09")
    graph.attr("edge", color="#64748b", arrowsize="0.7")

    colors = {
        "declaration": ("#DCEBFF", "#1F4E79", "box"),
        "statement": ("#E8F7E6", "#2E7D32", "ellipse"),
        "expression": ("#FDE7D9", "#B8631D", "parallelogram"),
        "other": ("#F1F5F9", "#475569", "box"),
    }
    counter = [0]

    def add_nodes(nodes, parent_id=None):
        for node in nodes or []:
            node_id = str(counter[0])
            counter[0] += 1
            kind = node.get("type", "node")
            details = str(node.get("details", "") or "")
            line = node.get("line")
            label_parts = [kind]
            if details and details != kind:
                label_parts.append(details)
            if line is not None:
                label_parts.append(f"Line {line}")
            fill, border, shape = colors.get(node.get("family", _node_family(kind)), colors["other"])
            attrs = {
                "label": "\n".join(label_parts),
                "shape": shape,
                "style": "filled,rounded",
                "fillcolor": "#FEE2E2" if node.get("is_error_node") else fill,
                "color": "#DC2626" if node.get("is_error_node") else border,
                "fontcolor": "#111827",
                "penwidth": "2" if node.get("is_error_node") else "1",
            }
            graph.node(node_id, **attrs)
            if parent_id is not None:
                graph.edge(parent_id, node_id)
            add_nodes(node.get("children", []), node_id)

    add_nodes(tree)

    try:
        rendered_path = Path(graph.render(
            filename=output.stem,
            directory=str(output.parent),
            cleanup=True,
            view=False,
        )).resolve()
    except Exception as exc:
        raise RuntimeError(
            "Graphviz failed while rendering the AST. Check that `dot -V` works in "
            f"this same terminal. Original error: {exc}"
        ) from exc

    if not rendered_path.is_file() or rendered_path.stat().st_size == 0:
        raise RuntimeError(f"Graphviz reported success but no PNG was created at: {rendered_path}")

    return rendered_path
