"""
auto_healer.py
--------------
Takes a structured error dict (category + line number + source code) and
returns a patched version of the source, or None if the error cannot be
auto-fixed.

Each public function follows the signature:
    fix_<category>(source: str, error: dict) -> str | None
"""

import re
from dataclasses import dataclass
from typing import Optional

# ── Symbol → header mapping ──────────────────────────────────────────────────
_SYMBOL_TO_HEADER = {
    "cout": "iostream", "cin": "iostream", "cerr": "iostream",
    "endl": "iostream", "ostream": "iostream", "istream": "iostream",
    "printf": "cstdio",  "scanf": "cstdio",  "fprintf": "cstdio",
    "string": "string",  "getline": "string",
    "vector": "vector",  "map": "map",       "set": "set",
    "unordered_map": "unordered_map",         "unordered_set": "unordered_set",
    "list": "list",      "queue": "queue",   "stack": "stack",
    "deque": "deque",    "array": "array",   "pair": "utility",
    "tuple": "tuple",    "optional": "optional",
    "sort": "algorithm", "find": "algorithm", "max": "algorithm",
    "min": "algorithm",  "reverse": "algorithm", "count": "algorithm",
    "fill": "algorithm", "copy": "algorithm",
    "abs": "cmath",  "sqrt": "cmath",  "pow": "cmath",
    "ceil": "cmath", "floor": "cmath",
    "unique_ptr": "memory", "shared_ptr": "memory", "make_unique": "memory",
    "make_shared": "memory",
    "assert": "cassert",  "INT_MAX": "climits",  "INT_MIN": "climits",
    "size_t": "cstddef",  "nullptr": "",          "NULL": "cstdlib",
}

_STD_SYMBOLS = {
    "cout", "cin", "cerr", "endl", "string", "vector", "map", "set",
    "pair", "tuple", "sort", "find", "max", "min", "reverse", "count",
    "make_unique", "make_shared", "unique_ptr", "shared_ptr",
    "list", "queue", "stack", "deque", "array",
    "unordered_map", "unordered_set", "optional",
}

_CPP_KEYWORDS = [
    "int", "float", "double", "char", "bool", "void", "return", "if", "else",
    "while", "for", "do", "switch", "case", "break", "continue", "class",
    "struct", "public", "private", "protected", "new", "delete", "true",
    "false", "const", "static", "namespace", "include", "template", "virtual",
]
_VALID_KEYWORDS = set(_CPP_KEYWORDS)

_PROTECTED_TYPO_FIX = (
    set(_STD_SYMBOLS)
    | set(_SYMBOL_TO_HEADER)
    | set(_CPP_KEYWORDS)
    | {
        "std", "clog", "printf", "scanf", "sprintf", "fprintf", "puts",
        "main", "this", "size_t", "nullptr", "NULL",
        "include", "define", "ifdef", "ifndef", "endif", "pragma",
    }
)

_DECLARATION_RE = re.compile(
    r"\b(?:auto|bool|char|double|float|int|long|short|signed|unsigned|void|"
    r"size_t|string|vector|const|static|struct|class|enum|union)"
    r"[\w:<>,\s\*&]*?\b(?P<name>[A-Za-z_]\w*)\b"
)
_QUALIFIED_RE = re.compile(r"\b[\w:]+::(?P<name>[A-Za-z_]\w*)\b")
_FUNC_DECL_RE = re.compile(
    r"\b(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const|noexcept|override|final|->|;|\{)"
)


def _is_declared_in_source(source: str, token: str) -> bool:
    if not source or not token:
        return False
    for regex in (_DECLARATION_RE, _QUALIFIED_RE, _FUNC_DECL_RE):
        for match in regex.finditer(source):
            if match.group("name") == token:
                return True
    return False


def _typo_is_plausible(token: str, distance: int) -> bool:
    if distance <= 1:
        return True
    return len(token) >= 7 and len(set(token)) >= 5


@dataclass
class Patch:
    source: str
    strategy: str
    rationale: str
    line_no: Optional[int] = None
    confidence: float = 0.7


def _build_keyword_variant_map() -> dict:
    variants = {}
    for keyword in _CPP_KEYWORDS:
        variants[keyword] = keyword
        chars = list(keyword)
        for i in range(len(chars) - 1):
            swapped = chars.copy()
            swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
            variants["".join(swapped)] = keyword
    return variants

_KEYWORD_VARIANTS = _build_keyword_variant_map()


def _keyword_edit_distance(a: str, b: str, limit: int = 2) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        row_min = curr[0]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
            row_min = min(row_min, curr[-1])
        if row_min > limit:
            return limit + 1
        prev = curr
    return prev[-1]


def _lines(source: str):
    return source.splitlines(keepends=True)


def _join(lines: list) -> str:
    return "".join(lines)


def _extract_symbol(message: str) -> Optional[str]:
    m = re.search(r"'([a-zA-Z_][a-zA-Z0-9_:]*)'", message)
    return m.group(1).split("::")[-1] if m else None


def _fix_keyword_typo(line: str, target_token: Optional[str] = None,
                      source: Optional[str] = None) -> Optional[str]:
    if not target_token:
        return None
    if len(target_token) < 3:
        return None
    if target_token in _PROTECTED_TYPO_FIX:
        return None
    if source and _is_declared_in_source(source, target_token):
        return None

    for match in re.finditer(r"\b[A-Za-z_]\w*\b", line):
        token = match.group(0)
        if token != target_token:
            continue
        if token in _VALID_KEYWORDS:
            continue
        canonical = _KEYWORD_VARIANTS.get(token)
        if canonical and canonical != token:
            return line[:match.start()] + canonical + line[match.end():]
        best_keyword = None
        best_distance = 3
        for keyword in _CPP_KEYWORDS:
            if token == keyword:
                continue
            if token[0].lower() != keyword[0].lower():
                continue
            if abs(len(token) - len(keyword)) > 2:
                continue
            distance = _keyword_edit_distance(token.lower(), keyword.lower(), limit=2)
            if distance < best_distance:
                best_distance = distance
                best_keyword = keyword
                if distance == 1:
                    break
        if (best_keyword is not None and best_distance <= 2
                and _typo_is_plausible(token, best_distance)):
            return line[:match.start()] + best_keyword + line[match.end():]
    return None


def _append_missing_semicolon(line: str) -> Optional[str]:
    stripped = line.rstrip("\n\r")
    content = stripped.strip()
    if not content:
        return None
    if re.search(r"[;{},:]\s*$", stripped):
        return None
    if content.startswith(("#", "//")):
        return None
    if re.match(r"^(if|else|for|while|switch|case|do|class|struct|namespace|template|public|private|protected)\b", content):
        return None
    if re.search(r"\)\s*$", stripped) and re.match(r"^(if|for|while|switch)\b", content):
        return None
    return stripped + ";\n"


def _has_include(source: str, header: str) -> bool:
    return bool(re.search(rf'#include\s*[<"]{re.escape(header)}[>"]', source))


def _insert_include(source: str, header: str) -> str:
    inc_line = f"#include <{header}>\n"
    lines = _lines(source)
    last_inc = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("#include"):
            last_inc = i
    insert_pos = last_inc + 1 if last_inc >= 0 else 0
    lines.insert(insert_pos, inc_line)
    return _join(lines)


_RAW_STRING_START = re.compile(r'(?:u8|u|U|L)?R"([^\s()\\]{0,16})\(')
_UNINIT_MESSAGE_RE = re.compile(
    r"used uninitialized"
    r"|may be used uninitialized"
    r"|is uninitialized when used"
    r"|uninitialized when used here"
    r"|wuninitialized"
    r"|uninitialized variable",
    re.IGNORECASE,
)
_UNDECLARED_IDENTIFIER_PHRASES = (
    "was not declared in this scope",
    "use of undeclared identifier",
    "undeclared identifier",
    "identifier not found",
    "is undefined",
)
_INTEGER_OVERFLOW_PHRASES = (
    "integer overflow",
    "overflow in expression",
    "result is undefined",
)
_DIVISION_BY_ZERO_PHRASES = (
    "division by zero",
    "divide by zero",
)
_STREAM_OPERATOR_PHRASES = (
    "no match for 'operator<<'",
    "no match for 'operator>>'",
    "operand types are 'std::istream'",
    "operand types are 'std::ostream'",
    "invalid operands to binary expression",
)
_UNUSED_VARIABLE_PHRASES = (
    "set but not used",
    "unused variable",
    "unused but set variable",
    "declared but never used",
)


def _is_undeclared_identifier_error(message: str) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in _UNDECLARED_IDENTIFIER_PHRASES)


def _is_integer_overflow_error(message: str) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in _INTEGER_OVERFLOW_PHRASES)


def _is_division_by_zero_error(message: str) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in _DIVISION_BY_ZERO_PHRASES)


def _is_stream_operator_error(message: str) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in _STREAM_OPERATOR_PHRASES)


def _is_unused_variable_error(message: str) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in _UNUSED_VARIABLE_PHRASES)


def _infer_undeclared_initializer(line: str, var: str) -> Optional[str]:
    stripped = line.strip()
    assign_match = re.match(rf"^[A-Za-z_]\w*\s*$", stripped)
    if assign_match:
        return None
    direct_assign = re.match(rf"^\s*{re.escape(var)}\s*=\s*(.+?)\s*;\s*$", line)
    if direct_assign:
        return direct_assign.group(1).strip()
    compound_assign = re.match(
        rf"^\s*{re.escape(var)}\s*([+\-*/%&|^]|<<|>>)=\s*(.+?)\s*;\s*$", line
    )
    if compound_assign:
        return "0"
    increment = re.match(rf"^\s*(\+\+|--){re.escape(var)}\s*;\s*$", line)
    if increment:
        return "0"
    postfix = re.match(rf"^\s*{re.escape(var)}(\+\+|--)\s*;\s*$", line)
    if postfix:
        return "0"
    return None


def _brace_context(prefix_text: str, previous_text: str) -> str:
    text = f"{previous_text} {prefix_text}".strip()
    if not text:
        return "anonymous"
    if re.search(r"\b(?:class|struct|enum|namespace)\b", text):
        return "class"
    if re.search(r"\bif\s*\(", text) or re.search(r"\belse\b", text):
        return "if"
    if re.search(r"\bfor\s*\(", text):
        return "for"
    if re.search(r"\bwhile\s*\(", text):
        return "while"
    func_like = re.search(
        r"\b(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*\s*\([^;{}]*\)\s*"
        r"(?:const\b|noexcept\b|override\b|final\b|\->[^{};]+)?\s*$", text,
    )
    if func_like:
        return "function"
    return "anonymous"


def fix_missing_closing_braces(source: str, _error: dict = None) -> Optional[str]:
    lines = [
        line for line in _lines(source)
        if not re.search(r"}\s*//\s*closes\s+\w+\s+opened at line\s+\d+", line)
    ]
    if not lines:
        return source

    stack = []
    in_block_comment = False
    raw_delim = None
    previous_code_text = ""
    last_code_line_idx = None

    for line_idx, line in enumerate(lines):
        i = 0
        code_prefix = []
        while i < len(line):
            if raw_delim is not None:
                raw_end = line.find(")" + raw_delim + '"', i)
                if raw_end == -1:
                    i = len(line)
                    break
                i = raw_end + len(raw_delim) + 2
                raw_delim = None
                continue
            if in_block_comment:
                comment_end = line.find("*/", i)
                if comment_end == -1:
                    i = len(line)
                    break
                i = comment_end + 2
                in_block_comment = False
                continue
            if line.startswith("//", i):
                break
            if line.startswith("/*", i):
                in_block_comment = True
                i += 2
                continue
            raw_match = _RAW_STRING_START.match(line, i)
            if raw_match:
                raw_delim = raw_match.group(1)
                i = raw_match.end()
                continue
            ch = line[i]
            if ch in ("'", '"'):
                quote = ch
                i += 1
                while i < len(line):
                    if line[i] == "\\":
                        i += 2
                        continue
                    if line[i] == quote:
                        i += 1
                        break
                    i += 1
                continue
            if ch == "{":
                indent = re.match(r"[ \t]*", line).group(0)
                prefix_text = "".join(code_prefix).strip()
                context = _brace_context(prefix_text, previous_code_text)
                stack.append({"line": line_idx + 1, "indent": indent, "context": context})
                code_prefix.append(ch)
                i += 1
                continue
            if ch == "}":
                if stack:
                    stack.pop()
                code_prefix.append(ch)
                i += 1
                continue
            code_prefix.append(ch)
            i += 1
        code_text = "".join(code_prefix).strip()
        if code_text:
            previous_code_text = code_text
            last_code_line_idx = line_idx

    if not stack:
        return source

    insert_at = (last_code_line_idx + 1) if last_code_line_idx is not None else len(lines)
    closing_lines = [
        f"{entry['indent']}}} // closes {entry['context']} opened at line {entry['line']}\n"
        for entry in reversed(stack)
    ]
    lines[insert_at:insert_at] = closing_lines
    return _join(lines)


_PRIMITIVE_DEFAULTS = [
    (re.compile(r"\bbool\b"), " = false"),
    (re.compile(r"\bchar\b"), " = '\\0'"),
    (re.compile(r"\bfloat\b"), " = 0.0f"),
    (re.compile(r"\b(double|long double)\b"), " = 0.0"),
    (re.compile(r"\b(?:short|int|long|long long|unsigned|signed|size_t|ptrdiff_t|ssize_t)\b"), " = 0"),
]


def _initializer_for_declaration(line: str) -> Optional[str]:
    stripped = line.strip()
    if "=" in stripped or not stripped.endswith(";") or "(" in stripped:
        return None
    if re.search(r"\[[^\]]*\]\s*;$", stripped):
        return " = {}"
    if "*" in stripped:
        return " = nullptr"
    lhs = stripped[:-1].strip()
    for pattern, initializer in _PRIMITIVE_DEFAULTS:
        if pattern.search(lhs):
            return initializer
    if re.search(r"\bstruct\b|\bclass\b|\benum\b", lhs) or re.match(r"^[A-Z]\w*(?:::\w+)*\s+\w+$", lhs):
        return " = {}"
    return " = {}"


def fix_uninitialized_variable(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "")
    if not _UNINIT_MESSAGE_RE.search(msg):
        return None
    var = _extract_symbol(msg)
    if not var:
        return None
    lines = _lines(source)
    if not lines:
        return None
    start_idx = int(error.get("line") or len(lines)) - 1
    start_idx = max(0, min(start_idx, len(lines) - 1))
    decl_idx = None
    for i in range(start_idx, -1, -1):
        line = lines[i]
        if var not in line:
            continue
        stripped = line.strip()
        bare_init = re.match(r"^(?P<indent>\s*)(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<rhs>.+);\s*$", line)
        if not stripped.endswith(";") or "(" in line or ")" in line:
            continue
        if "=" in line and not bare_init:
            continue
        if re.match(r"^(return|if|for|while|switch|case|do|else|goto|throw|printf|fprintf|scanf|cout|cin|cerr)\b", stripped):
            continue
        match = re.search(rf"\b{re.escape(var)}\b", line)
        if not match:
            continue
        decl_idx = i
        break
    if decl_idx is None:
        return None
    line = lines[decl_idx]
    stripped = line.strip()
    bare_init = re.match(r"^(?P<indent>\s*)(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<rhs>.+);\s*$", line)
    if bare_init and bare_init.group("name") == var:
        fixed = f"{bare_init.group('indent')}auto {var} = {bare_init.group('rhs').strip()};\n"
        if fixed != line:
            lines[decl_idx] = fixed
            return _join(lines)
    if re.search(rf"\b{re.escape(var)}\s*\[", line):
        fixed = re.sub(
            rf"^(\s*.*?\b{re.escape(var)}\b(?:\s*(?:\[[^\]]*\]\s*)+))\s*;\s*$",
            rf"\1 = {{}};", line, count=1,
        )
    elif "*" in stripped:
        fixed = re.sub(
            rf"^(\s*)(?:[\w:<>*&\s]+?)\b{re.escape(var)}\b\s*;\s*$",
            rf"\1auto {var} = nullptr;", line, count=1,
        )
    else:
        init = _initializer_for_declaration(line)
        if init is None:
            return None
        fixed = re.sub(
            rf"^(\s*)(?:[\w:<>*&\s]+?)\b{re.escape(var)}\b\s*;\s*$",
            rf"\1auto {var}{init};", line, count=1,
        )
    if fixed == line:
        return None
    if not fixed.endswith(("\n", "\r")):
        fixed += "\n"
    lines[decl_idx] = fixed
    return _join(lines)


def fix_missing_include(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "")
    sym = _extract_symbol(msg)
    if not sym:
        return None
    header = _SYMBOL_TO_HEADER.get(sym)
    if not header:
        return None
    if _has_include(source, header):
        return None
    patched = _insert_include(source, header)
    if (sym in _STD_SYMBOLS
            and "using namespace std;" not in patched
            and not re.search(r"\bstd\s*::", patched)):
        lines = _lines(patched)
        last_inc = max(
            (i for i, ln in enumerate(lines) if ln.strip().startswith("#include")),
            default=-1,
        )
        insert_pos = last_inc + 1 if last_inc >= 0 else 0
        lines.insert(insert_pos, "using namespace std;\n")
        patched = _join(lines)
    return patched


_MISSING_SEMICOLON_RE = re.compile(
    r"""expected\s+(?:'[^']*'\s+or\s+)?['"]?;['"]?"""
    r"""|expected\s+['"]?,['"]?"""
)

_NON_STATEMENT_START_RE = re.compile(
    r"^(if|else|for|while|switch|case|default|do|try|catch|class|struct|enum|"
    r"union|namespace|template|typename|typedef|using|public|private|protected|"
    r"static_assert|throw|return|goto|extern|friend|operator)\b"
)
_CONTINUATION_TAIL_RE = re.compile(r"(?:[+\-*/%<>=&|^!?:,]|&&|\|\||\\)\s*$")


def _needs_semicolon(line: str) -> bool:
    stripped = line.rstrip("\n\r")
    content = stripped.strip()
    if not content:
        return False
    if content.startswith(("#", "//", "/*", "*", "*/")):
        return False
    if re.search(r"[;{},:]\s*$", stripped):
        return False
    if _CONTINUATION_TAIL_RE.search(stripped):
        return False
    if _NON_STATEMENT_START_RE.match(content):
        return False
    return True


def _semicolon_fault_candidates(source: str, error: dict, look_back: int = 4) -> list:
    lines = _lines(source)
    if not lines:
        return []
    reported = int(error.get("line") or 0) - 1
    if reported < 0 or reported >= len(lines):
        reported = len(lines) - 1
    candidates = [reported - offset for offset in range(1, look_back + 1)]
    candidates.append(reported)
    return [i for i in candidates if 0 <= i < len(lines) and _needs_semicolon(lines[i])]


def fix_syntax_error(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "").lower()
    line_no = error.get("line")
    if not line_no:
        return None
    lines = _lines(source)
    idx = int(line_no) - 1
    if idx < 0 or idx >= len(lines):
        return None
    line = lines[idx]
    stripped = line.rstrip("\n\r")
    target_token = _extract_symbol(error.get("message", ""))

    stream_fixed = fix_stream_operator(source, error)
    if stream_fixed is not None and stream_fixed != source:
        return stream_fixed

    if "expected '}' at end of input" in msg or "expected '}'" in msg:
        patched = fix_missing_closing_braces(source, error)
        if patched is not None and patched != source:
            return patched
        lines.append("}\n")
        return _join(lines)

    if "expected ')'" in msg:
        paren_line = line.rstrip("\n\r")
        if paren_line.endswith(";"):
            lines[idx] = paren_line[:-1] + ");\n"
        else:
            lines[idx] = paren_line + ")\n"
        return _join(lines)

    if _MISSING_SEMICOLON_RE.search(msg):
        for target_idx in _semicolon_fault_candidates(source, error):
            semicolon_fixed = _append_missing_semicolon(lines[target_idx])
            if semicolon_fixed and semicolon_fixed != lines[target_idx]:
                lines[target_idx] = semicolon_fixed
                return _join(lines)

    if target_token and "expected" not in msg:
        typo_fixed = _fix_keyword_typo(line, target_token=target_token, source=source)
        if typo_fixed and typo_fixed != line:
            lines[idx] = _append_missing_semicolon(typo_fixed) or typo_fixed
            return _join(lines)

    if "suggest parentheses" in msg or "assignment used as truth value" in msg:
        fixed = re.sub(
            r'(if\s*\()([^)]*?)(?<![=!<>])=(?!=)([^)]*?)(\))',
            r'\1\2==\3\4', stripped,
        )
        if fixed != stripped:
            lines[idx] = fixed + "\n"
            return _join(lines)

    if "main" in stripped and "int main" not in stripped and "void main" not in stripped:
        lines[idx] = re.sub(r'\bmain\s*\(', 'int main(', stripped) + "\n"
        return _join(lines)

    if "missing terminating" in msg:
        quote = '"' if '\"' in msg or '" character' in msg else "'"
        stripped = line.rstrip("\n\r;")
        lines[idx] = stripped + quote + ";\n"
        return _join(lines)
    return None


def fix_name_resolution(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "")
    if _is_undeclared_identifier_error(msg):
        line_no = error.get("line")
        sym = _extract_symbol(msg)
        if line_no and sym:
            lines = _lines(source)
            idx = int(line_no) - 1
            if 0 <= idx < len(lines):
                typo_fixed = _fix_keyword_typo(lines[idx], target_token=sym, source=source)
                if typo_fixed and typo_fixed != lines[idx]:
                    lines[idx] = _append_missing_semicolon(typo_fixed) or typo_fixed
                    return _join(lines)
        return fix_undeclared_variable(source, error)
    sym = _extract_symbol(msg)
    if not sym:
        return None
    line_no = error.get("line")
    if line_no:
        lines = _lines(source)
        idx = int(line_no) - 1
        if 0 <= idx < len(lines):
            typo_fixed = _fix_keyword_typo(lines[idx], target_token=sym, source=source)
            if typo_fixed and typo_fixed != lines[idx]:
                lines[idx] = _append_missing_semicolon(typo_fixed) or typo_fixed
                return _join(lines)
    if sym in ("end1", "endI"):
        return re.sub(rf'\b{sym}\b', 'endl', source)
    if sym not in _STD_SYMBOLS:
        return None
    bare_count = sum(
        1 for s in _STD_SYMBOLS
        if re.search(rf'(?<!:)\b{re.escape(s)}\b', source)
        and f"std::{s}" not in source
    )
    if bare_count >= 3 and "using namespace std;" not in source:
        lines = _lines(source)
        last_inc = -1
        for i, l in enumerate(lines):
            if l.strip().startswith("#include"):
                last_inc = i
        insert_pos = last_inc + 1 if last_inc >= 0 else 0
        lines.insert(insert_pos, "using namespace std;\n")
        return _join(lines)
    patched = re.sub(rf'(?<!:)\b{re.escape(sym)}\b', f"std::{sym}", source)
    if patched == source:
        return None
    return patched


def fix_undeclared_variable(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "")
    if not _is_undeclared_identifier_error(msg):
        return None
    var = _extract_symbol(msg)
    if not var or var in _VALID_KEYWORDS:
        return None
    lines = _lines(source)
    line_no = error.get("line")
    if not line_no:
        return None
    idx = int(line_no) - 1
    if idx < 0 or idx >= len(lines):
        return None
    decl_pattern = re.compile(
        rf"\b(?:auto|bool|char|double|float|int|long|short|signed|unsigned|"
        rf"size_t|const|static|string|vector|map|set|list|queue|stack|deque|"
        rf"array|pair|optional|std::\w+)\b"
        rf"(?:\s*<[^>]*>)?[\s\*&]+{re.escape(var)}\b"
    )
    for existing in lines[:idx]:
        if decl_pattern.search(existing):
            return None
    indent = re.match(r"^(\s*)", lines[idx]).group(1)
    initializer = _infer_undeclared_initializer(lines[idx], var)
    if initializer is None:
        return None
    lines.insert(idx, f"{indent}auto {var} = {initializer};\n")
    return _join(lines)


def fix_integer_overflow(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "")
    if not _is_integer_overflow_error(msg):
        return None
    line_no = error.get("line")
    if not line_no:
        return None
    lines = _lines(source)
    idx = int(line_no) - 1
    if idx < 0 or idx >= len(lines):
        return None
    line = lines[idx]
    fixed = re.sub(r"\bint\b", "long long", line, count=1)
    if fixed != line:
        lines[idx] = fixed
        return _join(lines)
    expr = re.search(r"(\b\w+\b)\s*(\+|\-|\*)\s*(\w+)", line)
    if expr:
        fixed = (
            line[:expr.start()] +
            f"(long long){expr.group(1)} {expr.group(2)} {expr.group(3)}" +
            line[expr.end():]
        )
        lines[idx] = fixed
        return _join(lines)
    return None


def inject_division_guard(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "")
    if not _is_division_by_zero_error(msg):
        return None
    lines = _lines(source)
    var_match = re.search(r"'([A-Za-z_]\w*)'\s+is\s+0", msg)
    divisor = var_match.group(1) if var_match else None
    line_no = error.get("line")
    if divisor is None and line_no:
        idx = int(line_no) - 1
        if 0 <= idx < len(lines):
            line = lines[idx]
            match = re.search(r"/\s*([a-zA-Z_]\w*)", line)
            if match:
                divisor = match.group(1)
    if not divisor:
        return None
    guard_pattern = re.compile(rf"if\s*\(\s*{re.escape(divisor)}\s*==\s*0\s*\)")
    if guard_pattern.search(source):
        return None
    div_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        if re.search(rf"/\s*{re.escape(divisor)}\b", line):
            div_idx = i
            break
    if div_idx is None:
        return None
    indent = re.match(r"^(\s*)", lines[div_idx]).group(1)
    guard = (
        f"{indent}if ({divisor} == 0) {{\n"
        f"{indent}    std::cerr << \"Error: division by zero\\n\";\n"
        f"{indent}    return 1;\n"
        f"{indent}}}\n"
    )
    lines.insert(div_idx, guard)
    return _join(lines)


def fix_division_by_zero(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "")
    if not _is_division_by_zero_error(msg):
        return None
    line_no = error.get("line")
    if not line_no:
        return None
    lines = _lines(source)
    idx = int(line_no) - 1
    if idx < 0 or idx >= len(lines):
        return None
    line = lines[idx]
    fixed = re.sub(r"/\s*0\b", "/ 1 /* FIX: was /0 */", line)
    if fixed != line:
        lines[idx] = fixed
        return _join(lines)
    return inject_division_guard(source, error)


def fix_stream_operator(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "")
    if not _is_stream_operator_error(msg):
        return None
    line_no = error.get("line")
    if not line_no:
        return None
    lines = _lines(source)
    idx = int(line_no) - 1
    if idx < 0 or idx >= len(lines):
        return None
    line = lines[idx]
    if re.search(r"(?:std::)?cin\s*<<", line):
        fixed = re.sub(r"((?:std::)?cin\s*)<<", r"\1>>", line)
        if fixed != line:
            lines[idx] = fixed
            return _join(lines)
    if re.search(r"(?:std::)?cout\s*>>", line):
        fixed = re.sub(r"((?:std::)?cout\s*)>>", r"\1<<", line)
        if fixed != line:
            lines[idx] = fixed
            return _join(lines)
    return None


def fix_unused_variable(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "")
    if not _is_unused_variable_error(msg):
        return None
    var = _extract_symbol(msg)
    if not var or var in _VALID_KEYWORDS:
        return None
    line_no = error.get("line")
    if not line_no:
        return None
    lines = _lines(source)
    idx = int(line_no) - 1
    if idx < 0 or idx >= len(lines):
        return None
    line = lines[idx]
    simple_decl = re.match(
        rf"^\s*(?:auto|int|float|double|char|bool|long|short)\s+{re.escape(var)}\s*=\s*.+;\s*$",
        line,
    )
    if simple_decl:
        lines.pop(idx)
        return _join(lines)
    bare_decl = re.match(
        rf"^\s*(?:auto|int|float|double|char|bool|long|short)\s+{re.escape(var)}\s*;\s*$",
        line,
    )
    if bare_decl:
        lines.pop(idx)
        return _join(lines)
    indent = re.match(r"^(\s*)", line).group(1)
    if idx + 1 < len(lines) and re.match(rf"^\s*\(void\){re.escape(var)}\s*;\s*$", lines[idx + 1]):
        return None
    lines.insert(idx + 1, f"{indent}(void){var};\n")
    return _join(lines)


def fix_type_error(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "").lower()
    stream_fixed = fix_stream_operator(source, error)
    if stream_fixed is not None and stream_fixed != source:
        return stream_fixed
    line_no = error.get("line")
    if not line_no:
        return None
    lines = _lines(source)
    idx = int(line_no) - 1
    if idx < 0 or idx >= len(lines):
        return None
    line = lines[idx]
    if "invalid conversion from 'const char*' to 'char'" in msg:
        fixed = re.sub(r'"([^"])"', r"'\1'", line)
        if fixed != line:
            lines[idx] = fixed
            return _join(lines)
    if "invalid conversion from 'int' to 'const char*'" in msg or "invalid conversion from 'char' to 'const char*'" in msg:
        fixed = re.sub(r"'([^']+)'", r'"\1"', line)
        if fixed != line:
            lines[idx] = fixed
            return _join(lines)
    if "maybe you meant to use '->'" in msg or ("request for member" in msg and "pointer type" in msg):
        m = re.search(r'([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)', line)
        if m:
            fixed = line[:m.start()] + f"{m.group(1)}->{m.group(2)}" + line[m.end():]
            lines[idx] = fixed
            return _join(lines)
    if "cannot be used as a function" in msg:
        m = re.search(r'([a-zA-Z_]\w*)\s*\(([^)]+)\)', line)
        if m:
            fixed = line[:m.start()] + f"{m.group(1)}[{m.group(2)}]" + line[m.end():]
            lines[idx] = fixed
            return _join(lines)
    conv = re.search(r"cannot convert '([^']+)' to '([^']+)'", error.get("message", ""))
    if conv:
        from_t, to_t = conv.group(1), conv.group(2)
        if from_t.endswith("*") and not to_t.endswith("*"):
            m = re.search(r'=\s*([a-zA-Z0-9_]+)', line)
            if m:
                var = m.group(1)
                lines[idx] = line[:m.start()] + f"= *{var}" + line[m.end():]
                return _join(lines)
        if "pointer" in msg or "address" in msg:
            m = re.search(r'\b([a-zA-Z_]\w*)\b\s*\)', line)
            if m:
                var = m.group(1)
                lines[idx] = line[:m.start()] + f"&{var})" + line[m.end():]
                return _join(lines)
        m = re.search(r'=\s*(.+);', line)
        if m:
            rhs = m.group(1).strip()
            fixed_line = line[: m.start()] + f"= static_cast<{to_t}>({rhs});\n"
            lines[idx] = fixed_line
            return _join(lines)
    if "pointer" in msg or "address" in msg:
        m = re.search(r'\b([a-zA-Z_]\w*)\b\s*\)', line)
        if m:
            var = m.group(1)
            lines[idx] = line.replace(var + ")", f"&{var})", 1)
            return _join(lines)
    return None


_STRING_CONVERSION_PATTERNS = (
    re.compile(
        r"(?:cannot|invalid)\s+conversion\s+from\s+'([^']+)'\s+to\s+(?:non-scalar\s+type\s+)?'([^']+)'",
        re.IGNORECASE,
    ),
    re.compile(
        r"conversion\s+from\s+'([^']+)'\s+to\s+(?:non-scalar\s+type\s+)?'([^']+)'",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:cannot|invalid)\s+convert\s+'([^']+)'\s+.*?\bto\s+'([^']+)'",
        re.IGNORECASE,
    ),
)


def _string_conversion_direction(message: str) -> Optional[str]:
    message = message or ""
    if "conver" not in message.lower() or "string" not in message.lower():
        return None
    match = None
    for pattern in _STRING_CONVERSION_PATTERNS:
        match = pattern.search(message)
        if match:
            break
    if not match:
        return None
    from_type, to_type = match.group(1).lower(), match.group(2).lower()
    from_is_string = "string" in from_type
    to_is_string = "string" in to_type
    if to_is_string and not from_is_string:
        return "int_to_string"
    if from_is_string and not to_is_string:
        return "string_to_int"
    return None


def fix_string_conversion(source: str, error: dict) -> Optional[str]:
    direction = _string_conversion_direction(error.get("message", ""))
    if direction is None:
        return None
    line_no = error.get("line")
    if not line_no:
        return None
    lines = _lines(source)
    idx = int(line_no) - 1
    if idx < 0 or idx >= len(lines):
        return None
    line = lines[idx]
    wrapper = "std::to_string" if direction == "int_to_string" else "std::stoi"
    match = re.search(r"=\s*([^;]+);", line)
    if not match:
        return None
    rhs = match.group(1).strip()
    if not rhs or f"{wrapper}(" in line:
        return None
    lines[idx] = line[:match.start()] + f"= {wrapper}({rhs});" + line[match.end():]
    patched = _join(lines)
    if not _has_include(patched, "string"):
        patched = _insert_include(patched, "string")
    return patched


def fix_return_type_error(source: str, error: dict) -> Optional[str]:
    msg = error.get("message", "").lower()
    if "no return" not in msg and "return" not in msg:
        return None
    line_no = error.get("line")
    if not line_no:
        return None
    lines = _lines(source)
    idx = int(line_no) - 1
    if idx < 0 or idx >= len(lines):
        return None
    for i in range(idx, -1, -1):
        if lines[i].strip() == "}":
            lines.insert(i, "    return 0;\n")
            return _join(lines)
    return None


def fix_redefinition(source: str, error: dict) -> Optional[str]:
    line_no = error.get("line")
    if not line_no:
        return None
    lines = _lines(source)
    idx = int(line_no) - 1
    if idx < 0 or idx >= len(lines):
        return None
    line = lines[idx]
    m = re.match(r'^(\s*)(int|float|double|char|bool|auto|long|short|unsigned)\s+', line)
    if m:
        lines[idx] = m.group(1) + line[m.end():]
        return _join(lines)
    return None


def fix_linker_error(_source: str, _error: dict) -> None:
    return None


def fix_access_error(_source: str, _error: dict) -> None:
    return None
def fix_for_loop_semicolon(source: str, error: dict) -> Optional[str]:
    """Fix `for(int i=0; i<n i++)` -> `for(int i=0; i<n; i++)`"""
    msg = error.get("message", "").lower()
    if "expected" not in msg or ";" not in msg:
        return None
    lines = _lines(source)
    line_no = error.get("line")
    if not line_no or line_no > len(lines):
        return None
    line = lines[line_no - 1]
    m = re.search(r"for\s*\(([^)]*)\)", line)
    if not m:
        return None
    body = m.group(1)
    if body.count(";") >= 2:
        return None
    if body.count(";") == 1:
        parts = body.split(";")
        if len(parts) != 2:
            return None
        first, second = parts
        if not re.search(r"[+\-*/%]?=|[+\-]{2}", second):
            return None
        fixed_body = first + ";" + second + ";"
    else:
        return None
    fixed_line = line[:m.start(1)] + fixed_body + line[m.end(1):]
    lines[line_no - 1] = fixed_line
    return _join(lines)


def fix_missing_return(source: str, error: dict) -> Optional[str]:
    """Add `return 0;` to non-void functions that fall off the end."""
    msg = error.get("message", "").lower()
    if "no return" not in msg and "control reaches end" not in msg:
        return None
    lines = _lines(source)
    line_no = error.get("line")
    if not line_no:
        return None
    for i in range(min(int(line_no) - 1, len(lines) - 1), -1, -1):
        if lines[i].strip() == "}":
            indent = re.match(r"^(\s*)", lines[i]).group(1) + "    "
            lines.insert(i, f"{indent}return 0;\n")
            return _join(lines)
    return None


def fix_unmatched_paren(source: str, error: dict) -> Optional[str]:
    """Add missing `)` to `if (x > 5 {` -> `if (x > 5) {`"""
    msg = error.get("message", "").lower()
    if "expected ')'" not in msg and 'expected ")"' not in msg:
        return None
    lines = _lines(source)
    line_no = error.get("line")
    if not line_no or line_no > len(lines):
        return None
    line = lines[line_no - 1]
    # Count parens on the reported line and add the missing one before the '{'
    open_count = line.count("(")
    close_count = line.count(")")
    if open_count <= close_count:
        return None
    missing = open_count - close_count
    # Insert the missing `)` before the last `{` if present, else at end of line
    if "{" in line:
        insert_pos = line.rfind("{")
        fixed = line[:insert_pos].rstrip() + ")" * missing + " " + line[insert_pos:]
    else:
        fixed = line.rstrip("\n") + ")" * missing + "\n"
    lines[line_no - 1] = fixed
    return _join(lines)


def fix_wrong_quote_string(source: str, error: dict) -> Optional[str]:
    """Fix `string s = 'hello';` -> `string s = "hello";`"""
    msg = error.get("message", "").lower()
    if "multi-character character constant" not in msg:
        return None
    lines = _lines(source)
    line_no = error.get("line")
    if not line_no or line_no > len(lines):
        return None
    line = lines[line_no - 1]
    fixed = re.sub(r"'([A-Za-z][^']*)'", r'"\1"', line)
    if fixed != line:
        lines[line_no - 1] = fixed
        return _join(lines)
    return None


def fix_missing_main_return(source: str, error: dict) -> Optional[str]:
    """Add `return 0;` to `int main(){...}` that has no return."""
    msg = error.get("message", "").lower()
    if "return" not in msg:
        return None
    if "int main" not in source:
        return None
    if re.search(r"return\s+[\w\d]+\s*;", source):
        return None
    lines = _lines(source)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "}":
            indent = re.match(r"^(\s*)", lines[i]).group(1) + "    "
            lines.insert(i, f"{indent}return 0;\n")
            return _join(lines)
    return None


def fix_uninitialized_array(source: str, error: dict) -> Optional[str]:
    """Fix `int arr[5];` used uninitialized -> `int arr[5] = {};`"""
    msg = error.get("message", "").lower()
    if "uninitialized" not in msg and "may be used" not in msg:
        return None
    var = _extract_symbol(error.get("message", ""))
    if not var:
        return None
    lines = _lines(source)
    line_no = error.get("line")
    if not line_no:
        return None
    for i in range(min(int(line_no) - 1, len(lines) - 1), -1, -1):
        m = re.search(
            rf"^(\s*)([A-Za-z_]\w*(?:\s*<[^>]+>)?)\s+{re.escape(var)}\s*\[\s*\d+\s*\]\s*;",
            lines[i],
        )
        if m:
            lines[i] = re.sub(r";\s*$", " = {};", lines[i])
            return _join(lines)
    return None

_ASSIGN_IN_CONDITION_MESSAGE_RE = re.compile(
    r"assignment used as truth value|used as truth value", re.IGNORECASE
)
_CONDITION_HEAD_RE = re.compile(r"^\s*(?:if|while)\s*\(")
_LONE_ASSIGN_RE = re.compile(r"(?<![=!<>+\-*/%&|^])=(?!=)")


def _balanced_paren_span(line: str, open_index: int) -> Optional[int]:
    depth = 0
    for index in range(open_index, len(line)):
        if line[index] == "(":
            depth += 1
        elif line[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def fix_assignment_in_condition(source: str, error: dict) -> Optional[str]:
    line_no = error.get("line")
    lines = _lines(source)
    if not line_no or line_no > len(lines):
        return None
    line = lines[line_no - 1]
    head = _CONDITION_HEAD_RE.match(line)
    if not head:
        return None
    open_index = line.index("(", head.start())
    close_index = _balanced_paren_span(line, open_index)
    if close_index is None:
        return None
    body_start, body_end = open_index + 1, close_index
    body = line[body_start:body_end]
    if body.strip().startswith("(") or "," in body:
        return None
    new_body, count = _LONE_ASSIGN_RE.subn("==", body)
    if count == 0:
        return None
    lines[line_no - 1] = line[:body_start] + new_body + line[body_end:]
    return _join(lines)


def _is_assignment_in_condition_error(message: str) -> bool:
    return bool(_ASSIGN_IN_CONDITION_MESSAGE_RE.search(message))


def _is_missing_include_error(message: str) -> bool:
    if not _is_undeclared_identifier_error(message):
        return False
    symbol = _extract_symbol(message)
    if not symbol:
        return False
    STL_SYMBOLS = {
        "vector", "string", "map", "set", "list", "queue", "stack",
        "deque", "array", "pair", "tuple", "optional", "unordered_map",
        "unordered_set",
        "cout", "cin", "cerr", "endl", "ifstream", "ofstream",
        "stringstream", "sort", "find", "reverse", "max", "min",
        "unique_ptr", "shared_ptr", "make_unique", "make_shared",
    }
    if symbol in STL_SYMBOLS:
        return True
    return bool(_SYMBOL_TO_HEADER.get(symbol.split("::")[-1]))
def fix_typo_keyword_source(source: str, error: dict) -> Optional[str]:
    """Catch typos the compiler reports as 'not declared': whlie->while, retrun->return, etc."""
    msg = error.get("message", "").lower()
    if "was not declared" not in msg and "undeclared" not in msg:
        return None
    sym = _extract_symbol(error.get("message", ""))
    if not sym or len(sym) < 4:
        return None
    COMMON_TYPOS = {
        "whlie": "while", "whille": "while", "wihle": "while",
        "retrun": "return", "reutrn": "return", "retunr": "return",
        "cuntinue": "continue", "contineu": "continue",
        "breka": "break", "breek": "break",
        "swtich": "switch", "swich": "switch",
        "coutn": "count", "inculde": "include",
    }
    if sym not in COMMON_TYPOS:
        return None
    corrected = COMMON_TYPOS[sym]
    patched = re.sub(rf"\b{re.escape(sym)}\b", corrected, source)
    if patched == source:
        return None
    return patched

_HANDLERS = {
    "missing_include":  fix_missing_include,
    "missing_closing_brace": fix_missing_closing_braces,
    "uninitialized_memory": fix_uninitialized_variable,
    "syntax_error":     fix_syntax_error,
    "name_resolution":  fix_name_resolution,
    "undeclared_variable": fix_undeclared_variable,
    "type_error":       fix_type_error,
    "return_type_error": fix_return_type_error,
    "redefinition":     fix_redefinition,
    "integer_overflow": fix_integer_overflow,
    "division_by_zero": fix_division_by_zero,
    "stream_operator":  fix_stream_operator,
    "unused_variable":  fix_unused_variable,
    "linker_error":     fix_linker_error,
    "access_error":     fix_access_error,
    "for_loop_semicolon": fix_for_loop_semicolon,
    "missing_return":    fix_missing_return,
    "unmatched_paren":   fix_unmatched_paren,
    "wrong_quote_string": fix_wrong_quote_string,
    "missing_main_return": fix_missing_main_return,
    "uninitialized_array": fix_uninitialized_array,
    "typo_keyword_source": fix_typo_keyword_source,
}

UNFIXABLE_CATEGORIES = {"linker_error", "access_error", "unused_variable", "security_violation"}


_STRATEGY_NOTES = {
    "missing_semicolon": "Inserted the missing ';' at the statement the compiler stopped on",
    "missing_closing_brace": "Appended the closing '}' for the block the compiler left open",
    "assignment_in_condition": "Changed '=' to '==' so the condition compares instead of assigns",
    "missing_include_rule": "Added the #include that declares the missing symbol",
    "uninitialized_memory_rule": "Gave the variable a type-appropriate initial value",
    "syntax_error_rule": "Applied a syntax repair matched from the compiler message",
    "name_resolution_rule": "Resolved the missing name via a std:: prefix, header, or typo fix",
    "undeclared_variable_rule": "Declared the variable the code was already using",
    "type_error_rule": "Made an implicit or pointer conversion explicit",
    "int_to_string_conversion": ("Wrapped the value in std::to_string() so the "
                                 "int-to-string conversion is performed explicitly"),
    "string_to_int_conversion": ("Wrapped the value in std::stoi() so the "
                                 "string-to-int conversion is performed explicitly"),
    "return_type_error_rule": "Added the missing return statement",
    "redefinition_rule": "Turned the duplicate declaration into a plain assignment",
    "integer_overflow_rule": "Widened the arithmetic so the value cannot overflow",
    "division_by_zero_rule": "Guarded the divisor before the division runs",
    "stream_operator_rule": "Corrected the << / >> stream direction",
    "unused_variable_rule": "Removed the dead variable",
    "stream_operator": "Corrected the << / >> stream direction",
    "unused_variable": "Removed the dead variable",
    "integer_overflow": "Widened the arithmetic so the value cannot overflow",
    "division_by_zero": "Guarded the divisor before the division runs",
    "name_resolution": "Resolved the missing name via a std:: prefix, header, or typo fix",
    "uninitialized_variable": "Gave the variable a type-appropriate initial value",
}


def _note(strategy: str) -> str:
    return _STRATEGY_NOTES.get(
        strategy, f"Applied the '{strategy}' repair rule to the reported line"
    )


def _changed_line(old: str, new: str) -> Optional[int]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    for index, (before, after) in enumerate(zip(old_lines, new_lines), start=1):
        if before != after:
            return index
    if len(new_lines) > len(old_lines):
        return len(old_lines) + 1
    return None
def _is_for_loop_semicolon_error(message: str) -> bool:
    """Detect the 'expected ; in for-loop' case."""
    lowered = (message or "").lower()
    return "expected" in lowered and ";" in lowered and "for" in lowered

def attempt_fix_detailed(source: str, error: dict,
                         exclude=None, only=None) -> Optional[Patch]:
    skip = set(exclude or ())
    allow = set(only) if only else None
    category = error.get("category", "other")
    msg = error.get("message", "").lower()
        # ── HIGH PRIORITY: missing semicolon inside a simple declaration ──
    # "int choice" without ';' is a syntax error the healer should catch
    # BEFORE assignment-in-condition or other rules fire
    if "assignment used as truth value" in msg or "expected ';'" in msg:
        # First try the missing-semicolon fix (it catches declarations)
        _semicolon = fix_syntax_error(source, error)
        if _semicolon is not None and _semicolon != source:
            return Patch(
                source=_semicolon,
                strategy="missing_semicolon",
                rationale="Inserted the missing ';' at the statement the compiler stopped on",
                line_no=_changed_line(source, _semicolon),
                confidence=0.9,
            )

    # ── HIGH-PRIORITY: assignment in condition ──
    if _is_assignment_in_condition_error(msg):
        _patched = fix_assignment_in_condition(source, error)
        if _patched is not None and _patched != source:
            if "assignment_in_condition" not in skip:
                return Patch(
                    source=_patched,
                    strategy="assignment_in_condition",
                    rationale="Changed '=' to '==' so the condition compares instead of assigns",
                    line_no=_changed_line(source, _patched),
                    confidence=0.9,
                )

    # ── HIGH-PRIORITY: assignment in condition ──
    if _is_assignment_in_condition_error(msg):
        _patched = fix_assignment_in_condition(source, error)
        if _patched is not None and _patched != source:
            if "assignment_in_condition" not in skip:
                return Patch(
                    source=_patched,
                    strategy="assignment_in_condition",
                    rationale="Changed '=' to '==' so the condition compares instead of assigns",
                    line_no=_changed_line(source, _patched),
                    confidence=0.9,
                )

    def consider(strategy, patched, base, confidence):
        ...

    def consider(strategy, patched, base, confidence):
        if patched is None or patched == base or strategy in skip:
            return None
        if allow is not None and strategy not in allow:
            return None
        return Patch(
            source=patched, strategy=strategy, rationale=_note(strategy),
            line_no=_changed_line(base, patched), confidence=confidence,
        )

    if "not allowed here" in msg or category == "missing_closing_brace":
        patch = consider("missing_closing_brace",
                         fix_missing_closing_braces(source, error), source, 0.75)
        if patch:
            return patch

    if _is_assignment_in_condition_error(msg):
        patch = consider("assignment_in_condition",
                         fix_assignment_in_condition(source, error), source, 0.6)
        if patch:
            return patch

    if _is_missing_include_error(msg):
        patch = consider("missing_include_rule",
                         fix_missing_include(source, error), source, 0.85)
        if patch:
            return patch

    _direction = _string_conversion_direction(error.get("message", ""))
    if _direction:
        _strategy = ("int_to_string_conversion" if _direction == "int_to_string"
                     else "string_to_int_conversion")
        patch = consider(_strategy, fix_string_conversion(source, error), source, 0.7)
        if patch:
            return patch
    if _is_for_loop_semicolon_error(msg):
        patch = consider("for_loop_semicolon",
                         fix_for_loop_semicolon(source, error), source, 0.85)
        if patch: return patch

    if "control reaches end" in msg or "no return statement" in msg:
        patch = consider("missing_return",
                         fix_missing_return(source, error), source, 0.8)
        if patch: return patch

    if "expected ')'" in msg:
        patch = consider("unmatched_paren",
                         fix_unmatched_paren(source, error), source, 0.85)
        if patch: return patch

    if "multi-character character constant" in msg or "invalid conversion from 'int'" in msg:
        patch = consider("wrong_quote_string",
                         fix_wrong_quote_string(source, error), source, 0.75)
        if patch: return patch

    if "return" in msg and "main" in error.get("message", "").lower():
        patch = consider("missing_main_return",
                         fix_missing_main_return(source, error), source, 0.8)
        if patch: return patch

    if "uninitialized" in msg and "may be used" in msg:
        patch = consider("uninitialized_array",
                         fix_uninitialized_array(source, error), source, 0.7)
        if patch: return patch
    if _is_undeclared_identifier_error(msg):
        sym = _extract_symbol(error.get("message", ""))
        if sym in {"whlie", "whille", "retrun", "reutrn", "retunr", "breka", "swtich"}:
            patch = consider("typo_keyword_source",
                             fix_typo_keyword_source(source, error), source, 0.95)
            if patch:
                return patch
    handler = _HANDLERS.get(category)
    if handler is not None:
        strategy = ("missing_semicolon"
                    if handler is fix_syntax_error and _MISSING_SEMICOLON_RE.search(msg)
                    else f"{category}_rule")
        patch = consider(strategy, handler(source, error), source, 0.8)
        if patch:
            return patch

    message_rules = (
        ("stream_operator", _is_stream_operator_error(msg), fix_stream_operator),
        ("unused_variable", _is_unused_variable_error(msg), fix_unused_variable),
        ("integer_overflow", _is_integer_overflow_error(msg), fix_integer_overflow),
        ("division_by_zero", _is_division_by_zero_error(msg), fix_division_by_zero),
        ("name_resolution", _is_undeclared_identifier_error(msg), fix_name_resolution),
        ("uninitialized_variable", bool(_UNINIT_MESSAGE_RE.search(msg)),
         fix_uninitialized_variable),
    )
    for strategy, matched, handler_fn in message_rules:
        if not matched or strategy in skip:
            continue
        patch = consider(strategy, handler_fn(source, error), source, 0.65)
        if patch:
            return patch
    return None


def attempt_fix(source: str, error: dict, exclude=None) -> Optional[str]:
    patch = attempt_fix_detailed(source, error, exclude=exclude)
    return patch.source if patch is not None else None

