import os
import re
import subprocess


ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
NODE_RE = re.compile(r"^(?:[|` -]*)?([A-Za-z_][A-Za-z0-9_]*)(?:\s+(.*))?$")
ADDRESS_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")

MAX_TREE_DEPTH = 6
MAX_DETAIL_LENGTH = 220

VISIBLE_NODES = {
    "TranslationUnitDecl",
    "NamespaceDecl",
    "UsingDecl",
    "UsingDirectiveDecl",
    "TypedefDecl",
    "TypeAliasDecl",
    "CXXRecordDecl",
    "ClassTemplateDecl",
    "FunctionTemplateDecl",
    "FunctionDecl",
    "CXXMethodDecl",
    "CXXConstructorDecl",
    "CXXDestructorDecl",
    "FieldDecl",
    "VarDecl",
    "ParmVarDecl",
    "EnumDecl",
    "EnumConstantDecl",
    "CompoundStmt",
    "DeclStmt",
    "IfStmt",
    "ForStmt",
    "CXXForRangeStmt",
    "WhileStmt",
    "DoStmt",
    "SwitchStmt",
    "CaseStmt",
    "DefaultStmt",
    "ReturnStmt",
    "BreakStmt",
    "ContinueStmt",
    "BinaryOperator",
    "CompoundAssignOperator",
    "UnaryOperator",
    "ConditionalOperator",
    "CallExpr",
    "CXXOperatorCallExpr",
    "CXXMemberCallExpr",
    "CXXConstructExpr",
    "CXXNewExpr",
    "CXXDeleteExpr",
    "ArraySubscriptExpr",
    "MemberExpr",
    "DeclRefExpr",
    "RecoveryExpr",
    "IntegerLiteral",
    "FloatingLiteral",
    "CharacterLiteral",
    "StringLiteral",
    "CXXBoolLiteralExpr",
    "CXXNullPtrLiteralExpr",
    "CXXThrowExpr",
}


def _ast_depth(line):
    marker = re.search(r"(?:\|-|`-)", line)
    if not marker:
        return 0
    return marker.start() // 2 + 1


def _node_parts(line):
    stripped = line.lstrip(" |`-")
    match = NODE_RE.match(stripped)
    if not match:
        return None, ""
    return match.group(1), match.group(2) or ""


def _is_user_location(line, file_path):
    if not line or not file_path:
        return False
    filename = os.path.basename(file_path).lower()
    norm_file = os.path.normpath(file_path).replace("\\", "/").lower()
    norm_abs = os.path.abspath(file_path).replace("\\", "/").lower()
    norm_line = line.replace("\\", "/").lower()
    return (
        f"<{filename}:" in norm_line
        or f"/{filename}:" in norm_line
        or f"\\{filename}:" in norm_line
        or f", {filename}:" in norm_line
        or norm_file in norm_line
        or norm_abs in norm_line
    )


def _has_explicit_external_location(line, file_path):
    if _is_user_location(line, file_path):
        return False
    return bool(re.search(r"</|, /|<[A-Za-z]:", line))


def _has_relative_location(line):
    return "<line:" in line or "<col:" in line


def _trim_details(details):
    details = ADDRESS_RE.sub("", details)
    details = " ".join(details.split())
    if len(details) > MAX_DETAIL_LENGTH:
        details = details[: MAX_DETAIL_LENGTH - 3].rstrip() + "..."
    return details


def extract_ast(file_path):
    """Return a compact clang AST dump containing only user-file subtrees."""
    cmd = [
        "clang++",
        "-Xclang",
        "-ast-dump",
        "-fsyntax-only",
        "-fno-color-diagnostics",
        file_path,
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except OSError:
        return None

    filtered = []
    active_depth = None
    user_file_context = False

    try:
        for raw_line in process.stdout:
            line = ANSI_RE.sub("", raw_line.rstrip("\n"))
            if not line.strip():
                continue
            if "<built-in>" in line or "/usr/include/" in line or "bits/" in line:
                continue

            depth = _ast_depth(line)
            if active_depth is not None and depth <= active_depth:
                active_depth = None

            if _has_explicit_external_location(line, file_path):
                user_file_context = False

            if _is_user_location(line, file_path):
                user_file_context = True

            starts_user_subtree = _is_user_location(line, file_path) or (
                user_file_context and _has_relative_location(line)
            )

            if active_depth is None and starts_user_subtree:
                active_depth = depth

            if active_depth is not None:
                filtered.append(line)
    finally:
        process.wait()

    return "\n".join(filtered) if filtered else None


def parse_ast_to_tree(ast_text, file_path=None):
    if not ast_text:
        return []

    root_nodes = []
    stack = []

    for line in ast_text.splitlines():
        if not line.strip():
            continue

        depth = _ast_depth(line)
        node_type, details = _node_parts(line)
        if not node_type:
            continue

        while stack and stack[-1][0] >= depth:
            stack.pop()

        if depth > MAX_TREE_DEPTH or node_type not in VISIBLE_NODES:
            continue

        node = {
            "type": node_type,
            "details": _trim_details(details),
            "children": [],
        }

        if stack:
            stack[-1][1]["children"].append(node)
        else:
            root_nodes.append(node)

        stack.append((depth, node))

    return root_nodes


def extract_node_near_line(ast_text, line_number):
    if not ast_text or line_number is None:
        return None

    fallback = None

    for line in ast_text.splitlines():
        node_type, _ = _node_parts(line)
        if not node_type or node_type not in VISIBLE_NODES:
            continue

        if f":{line_number}:" in line or f"line:{line_number}" in line:
            if "Expr" in node_type or "Stmt" in node_type or "Decl" in node_type:
                return node_type
            fallback = fallback or node_type

    return fallback


def build_fallback_ast(source_text):
    """Simple parser for malformed or incomplete C++ snippets when clang is unavailable."""
    if not source_text:
        return []

    root = {"type": "TranslationUnitDecl", "details": "source", "children": []}
    stack = [(-1, root)]
    patterns = [
        ("ClassDecl", r"\b(?:class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ("FunctionDecl", r"\b(?:[A-Za-z_:<>\s]+\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:->\s*[A-Za-z_:<>]+)?\s*\{"),
        ("IfStmt", r"\bif\s*\("),
        ("ForStmt", r"\bfor\s*\("),
        ("WhileStmt", r"\bwhile\s*\("),
        ("ReturnStmt", r"\breturn\b"),
        ("CallExpr", r"\b[A-Za-z_][A-Za-z0-9_:]*\s*\("),
    ]

    for line_no, line in enumerate(source_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        brace_depth = stripped.count("{") - stripped.count("}")
        for node_type, pattern in patterns:
            match = re.search(pattern, stripped)
            if not match:
                continue
            details = match.group(0).strip()
            this_node = {"type": node_type, "details": details[:MAX_DETAIL_LENGTH], "children": []}
            while len(stack) > 1 and brace_depth <= stack[-1][0]:
                stack.pop()
            stack[-1][1]["children"].append(this_node)
            stack.append((brace_depth, this_node))
            break

    return root["children"]
