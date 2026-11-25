# Health score = 10*Critical + 6*High + 3*Medium + 1*Low.
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Literal, Optional

from models import CompilerError


Severity = Literal["Critical", "High", "Medium", "Low"]


@dataclass(frozen=True)
class SecurityFinding:
    file: str
    line: int | None
    column: int | None
    severity: Severity
    vulnerability_type: str
    description: str
    recommendation: str
    source: Literal["compiler_diagnostic", "static_scan"]

    def to_dict(self):
        return asdict(self)


_SEVERITY_ORDER: dict[Severity, int] = {
    "Critical": 0,
    "High": 1,
    "Medium": 2,
    "Low": 3,
}
_SEVERITY_WEIGHT: dict[Severity, int] = {
    "Critical": 10,
    "High": 6,
    "Medium": 3,
    "Low": 1,
}

_DIAGNOSTIC_RULES = [
    (
        re.compile(r"unused variable|variable .+ set but not used", re.IGNORECASE),
        "Low",
        "Unused Variable",
        "An unused variable can indicate dead code or an incomplete check.",
        "Remove the variable or use it in the intended logic.",
    ),
    (
        re.compile(r"may be used uninitialized", re.IGNORECASE),
        "Medium",
        "Uninitialized Variable",
        "The compiler detected a value that may be read before initialization.",
        "Initialize the variable before reading it.",
    ),
    (
        re.compile(r"format not a string literal|format string is not a string literal", re.IGNORECASE),
        "High",
        "Non-Literal Format String",
        "A non-literal format string can allow attacker-controlled format specifiers.",
        "Use a fixed format string and pass data as a separate argument.",
    ),
    (
        re.compile(r"ignoring return value", re.IGNORECASE),
        "Medium",
        "Unchecked Return Value",
        "A function result is being discarded, which can hide failures.",
        "Check the return value and handle errors explicitly.",
    ),
    (
        re.compile(r"comparison is always (?:true|false)", re.IGNORECASE),
        "Medium",
        "Suspicious Comparison",
        "The compiler believes this comparison cannot fail, which may indicate a logic error.",
        "Review the expression, types, and range assumptions.",
    ),
    (
        re.compile(r"integer overflow|overflow in expression|wraps around", re.IGNORECASE),
        "High",
        "Integer Overflow",
        "The compiler reported an overflow-prone arithmetic expression.",
        "Check bounds before arithmetic and use checked operations for allocation sizes.",
    ),
    (
        re.compile(r"deprecated", re.IGNORECASE),
        "Low",
        "Deprecated API",
        "Deprecated APIs may be removed or behave unsafely in future toolchains.",
        "Replace the deprecated API with a supported alternative.",
    ),
    (
        re.compile(r"conversion from .* may change sign", re.IGNORECASE),
        "Medium",
        "Signed/Unsigned Comparison",
        "Mixing signed and unsigned values can produce unexpected comparisons.",
        "Use a consistent signedness model or check bounds before conversion.",
    ),
    (
        re.compile(r"array subscript is above array bounds|array subscript is below array bounds", re.IGNORECASE),
        "Critical",
        "Out-of-Bounds Array Access",
        "An array access is outside the valid bounds of the buffer.",
        "Check the index against the array length before accessing it.",
    ),
    (
        re.compile(r"null pointer dereference", re.IGNORECASE),
        "High",
        "Null Pointer Dereference",
        "A pointer may be dereferenced without a null check.",
        "Check the pointer before dereferencing it.",
    ),
]

# Rule metadata is language-scoped so support for other languages can be added
# without applying C/C++ syntax rules to their source.
RULE_REGISTRY: dict[str, dict[str, object]] = {
    "cpp": {
        "suffixes": {".c", ".cc", ".cpp", ".cxx", ".c++"},
        "checks": {
            # ── MEMORY SAFETY & BUFFERS ──
            "gets": ("Critical", "Unsafe Input Function", "gets() reads input without bounds checking.", "Replace gets() with fgets(), std::getline, or another bounded input routine."),
            "strcpy": ("High", "Unsafe String Function", "strcpy() can write past the destination buffer.", "Use a size-aware operation or std::string."),
            "strcat": ("High", "Unsafe String Function", "strcat() can write past the destination buffer.", "Use a size-aware operation or std::string."),
            "sprintf": ("High", "Unsafe Format Function", "sprintf() does not limit writes to the destination buffer.", "Use snprintf() with the destination buffer size."),
            "scanf_width": ("High", "Unbounded scanf Input", "scanf() reads a string with %s and no field-width limit.", "Add a maximum field width."),
            "fscanf": ("High", "Unbounded File Read", "fscanf() without width limits can overflow buffers.", "Use width limits with fscanf()."),
            "sscanf": ("High", "Unbounded String Read", "sscanf() without width limits can overflow buffers.", "Use width limits with sscanf()."),
            "memcpy": ("Medium", "Unsafe Memory Copy", "memcpy() requires correct bounds validation.", "Validate the copy length against both objects."),
            "memmove": ("Medium", "Unsafe Memory Move", "memmove() requires correct bounds validation.", "Validate the copy length."),
            "alloca": ("High", "Stack Exhaustion", "alloca() can cause stack overflow if size is user-controlled.", "Use dynamic allocation (malloc/new) or fixed-size buffers."),
            "new_delete_mismatch": ("High", "new[] / delete Mismatch", "An array allocated with new[] is released with delete.", "Use delete[] or a standard container."),
            "use_after_free": ("High", "Use After Free", "Memory is accessed after it was released.", "Do not use the pointer after release; use smart pointers."),
            "double_free": ("High", "Double Free", "The same allocation is released more than once.", "Release each allocation once and clear the pointer."),
            "memory_leak": ("Medium", "Potential Memory Leak", "An allocation has no matching release.", "Release the allocation or use a smart pointer."),
            "array_bounds": ("Critical", "Out-of-Bounds Array Access", "A constant array index is outside the valid array bounds.", "Check the index is non-negative and less than the array length."),
            
            # ── INJECTION & SYSTEM COMMANDS ──
            "command_injection": ("High", "Command Injection", "A non-literal shell command may contain untrusted input.", "Use a direct process API with validated argument arrays."),
            "system": ("High", "System Command Execution", "system() executes shell commands and is prone to injection.", "Avoid system(); use exec/posix_spawn."),
            "popen": ("High", "Shell Pipe Execution", "popen() executes shell commands.", "Avoid popen(); use standard file I/O or IPC."),
            "execl": ("Critical", "Process Execution", "execl() executes external binaries.", "Ensure the binary path and arguments are sanitized."),
            "execv": ("Critical", "Process Execution", "execv() executes external binaries.", "Ensure the binary path and arguments are sanitized."),
            "fork": ("Medium", "Process Spawning", "fork() can cause denial of service if unbounded.", "Implement process limits and reap children properly."),
            "getenv": ("Medium", "Environment Injection", "getenv() returns untrusted data.", "Validate environment variables before use."),
            "mktemp": ("High", "Insecure Temp File", "mktemp() is vulnerable to race conditions.", "Use mkstemp()."),
            "tmpnam": ("High", "Insecure Temp Name", "tmpnam() is vulnerable to race conditions.", "Use mkstemp()."),
            
            # ── FORMAT & LOGIC ──
            "format_string": ("High", "Non-Literal Format String", "A non-literal format string can contain attacker-controlled specifiers.", "Use a constant format string."),
            "assignment_condition": ("Medium", "Assignment in Condition", "Assignment in a condition can silently change the value being tested.", "Use == for comparison."),
            "uninitialized": ("Medium", "Uninitialized Variable", "A variable may be read before it receives a value.", "Initialize it at declaration."),
            "division_zero": ("High", "Division by Zero", "The expression divides by the literal zero.", "Validate the divisor and handle zero explicitly."),
            "signed_unsigned": ("Medium", "Signed/Unsigned Comparison", "Mixing signed and unsigned values can change comparison results.", "Use compatible signedness."),
            "unchecked_return": ("Medium", "Unchecked Return Value", "A function result is not checked.", "Check the result and handle the failure path."),
            "unused_variable": ("Low", "Unused Variable", "A variable is declared but never read.", "Remove it or use it."),
            "atoi": ("Medium", "Missing Error Check", "atoi() returns 0 on failure.", "Use strtol() for error checking."),
            "atol": ("Medium", "Missing Error Check", "atol() returns 0 on failure.", "Use strtol() for error checking."),
            "atof": ("Medium", "Missing Error Check", "atof() returns 0.0 on failure.", "Use strtod() for error checking."),
            
            # ── CRYPTOGRAPHY & PRIVILEGES ──
            "weak_randomness": ("High", "Weak Randomness", "rand() is not cryptographically secure.", "Use a cryptographically secure random-number generator."),
            "srand": ("Low", "Deterministic Seed", "srand() with a constant time seed is predictable.", "Seed from a secure entropy source."),
            "md5": ("High", "Broken Cryptography", "MD5 is cryptographically broken.", "Use SHA-256."),
            "sha1": ("High", "Broken Cryptography", "SHA-1 is cryptographically broken.", "Use SHA-256."),
            "des": ("High", "Broken Cryptography", "DES is insecure.", "Use AES."),
            "setuid": ("Critical", "Privilege Escalation", "setuid() drops privileges; ensure it's used correctly.", "Drop privileges as early as possible."),
            "setgid": ("Critical", "Privilege Escalation", "setgid() drops privileges; ensure it's used correctly.", "Drop privileges as early as possible."),
            "chmod": ("Medium", "Insecure Permissions", "chmod() with broad permissions (e.g., 0777) is insecure.", "Use the principle of least privilege."),
            "chown": ("Medium", "Privilege Escalation", "chown() can lead to privilege escalation.", "Validate the target user/group."),
            
            # ── CONCURRENCY & SIGNALS ──
            "pthread_create": ("Medium", "Unmanaged Thread", "Thread creation without joining can cause resource leaks.", "Detach or join threads."),
            "pthread_detach": ("Low", "Detached Thread", "Detached threads cannot be joined.", "Ensure the thread completes safely."),
            "signal": ("Medium", "Unsafe Signal Handler", "signal() handlers must be async-signal-safe.", "Use sigaction() and async-signal-safe functions."),
            "raise": ("Low", "Signal Generation", "raise() sends a signal to the current process.", "Use it carefully."),
            
            # ── FILE I/O ──
            "fopen": ("Low", "File Open", "fopen() requires error checking.", "Check if the file pointer is NULL."),
            "open": ("Low", "File Open", "open() requires error checking.", "Check the file descriptor return value."),
            "remove": ("Medium", "File Deletion", "remove() permanently deletes files.", "Validate the path before deletion."),
            "rename": ("Medium", "File Rename", "rename() can overwrite existing files.", "Check if the destination exists first."),
        },
        "unsafe_calls": {
            "gets": ("Critical", "Unsafe Input Function", "gets() reads input without bounds checking.", "Replace gets() with fgets()."),
            "strcpy": ("High", "Unsafe String Function", "strcpy() can write past the destination buffer.", "Use a size-aware operation."),
            "strcat": ("High", "Unsafe String Function", "strcat() can write past the destination buffer.", "Use a size-aware operation."),
            "sprintf": ("High", "Unsafe Format Function", "sprintf() does not limit writes.", "Use snprintf()."),
        },
    },
    "python": {
        "suffixes": {".py"},
        "checks": {
            "dynamic_code": ("Critical", "Dynamic Code Execution", "eval() or exec() executes dynamically supplied code.", "Avoid dynamic code execution."),
            "unsafe_deserialization": ("High", "Unsafe Deserialization", "pickle.load() can execute code.", "Use JSON for untrusted data."),
            "shell_execution": ("High", "Shell Command Execution", "subprocess with shell=True enables shell injection.", "Keep shell=False."),
            "dynamic_system_command": ("High", "Dynamic OS Command", "os.system() receives a non-literal command.", "Use subprocess with an argument list."),
            "unsafe_yaml": ("High", "Unsafe YAML Loading", "yaml.load() without SafeLoader is unsafe.", "Use yaml.safe_load()."),
            "hardcoded_secret": ("High", "Hardcoded Credential", "A credential-like variable is assigned a string literal.", "Load credentials from a secret manager."),
            "assert_validation": ("Medium", "Assert Used for Validation", "assert is used for input validation.", "Use explicit conditionals."),
            "user_path": ("High", "User-Controlled File Path", "open() receives a user-controlled path.", "Validate and constrain the path."),
            "md5": ("High", "Broken Cryptography", "hashlib.md5 is broken.", "Use hashlib.sha256."),
            "sha1": ("High", "Broken Cryptography", "hashlib.sha1 is broken.", "Use hashlib.sha256."),
            "tempfile": ("Low", "Temp File Usage", "Ensure temp files are securely created.", "Use tempfile.NamedTemporaryFile()."),
            "chmod": ("Medium", "Insecure Permissions", "os.chmod with broad permissions is insecure.", "Use least privilege."),
            "rmtree": ("Medium", "Directory Deletion", "shutil.rmtree permanently deletes directories.", "Validate the path before deletion."),
            "requests_verify": ("High", "Disabled SSL Verification", "requests(verify=False) disables certificate validation.", "Always verify SSL certificates."),
        },
    },
    "java": {
        "suffixes": {".java"},
        "checks": {
            "runtime_exec": ("Critical", "Command Execution", "Runtime.exec() executes system commands.", "Use ProcessBuilder with validated arguments."),
            "process_builder": ("High", "Process Execution", "ProcessBuilder starts external processes.", "Validate all arguments."),
            "deserialization": ("High", "Unsafe Deserialization", "ObjectInputStream can execute code.", "Avoid deserializing untrusted data."),
            "sql_concat": ("High", "SQL Injection", "SQL query string concatenation is vulnerable.", "Use PreparedStatement."),
            "hardcoded_secret": ("High", "Hardcoded Credential", "A credential-like variable is assigned a string literal.", "Load credentials from a secret manager."),
            "md5": ("High", "Broken Cryptography", "MessageDigest MD5 is broken.", "Use SHA-256."),
            "weak_random": ("Medium", "Weak Randomness", "java.util.Random is not cryptographically secure.", "Use SecureRandom."),
            "empty_catch": ("Medium", "Empty Catch Block", "Empty catch blocks swallow exceptions.", "Handle or log exceptions."),
            "print_stack": ("Low", "Stack Trace Disclosure", "printStackTrace() exposes sensitive info.", "Use structured logging."),
            "assert_validation": ("Medium", "Assert Used for Validation", "assert is used for input validation.", "Use explicit conditionals."),
        },
    },
    "javascript": {},
}

_SUFFIX_LANGUAGE = {
    suffix: language
    for language, rules in RULE_REGISTRY.items()
    for suffix in rules.get("suffixes", set())
}
_RAW_STRING_START = re.compile(r'(?:u8|u|U|L)?R"([^\s()\\]{0,16})\(')
_SENSITIVE_WORDS = re.compile(r"\b(key|token|password|secret|credential)\b", re.IGNORECASE)
_IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")


def _empty_finding(
    file: str,
    severity: Severity,
    vulnerability_type: str,
    description: str,
    recommendation: str,
    line: int | None = None,
    column: int | None = None,
    source: str = "static_scan",
) -> SecurityFinding:
    return SecurityFinding(
        file=file,
        line=line,
        column=column,
        severity=severity,
        vulnerability_type=vulnerability_type,
        description=description,
        recommendation=recommendation,
        source=source,  # type: ignore[arg-type]
    )


def _normalize_path(source_path: str) -> str:
    try:
        return str(Path(source_path).resolve())
    except (OSError, RuntimeError, ValueError):
        return source_path


def _make_finding(
    source_path: str,
    line: int | None,
    column: int | None,
    severity: Severity,
    vulnerability_type: str,
    description: str,
    recommendation: str,
    source: Literal["compiler_diagnostic", "static_scan"],
) -> SecurityFinding:
    return SecurityFinding(
        file=_normalize_path(source_path),
        line=line,
        column=column,
        severity=severity,
        vulnerability_type=vulnerability_type,
        description=description,
        recommendation=recommendation,
        source=source,
    )


def _strip_code(line: str, state: dict[str, object], preserve_literals: bool = False) -> str:
    """Mask comments and literals while retaining columns for precise locations."""
    in_block_comment = bool(state.get("in_block_comment"))
    raw_delim = state.get("raw_delim")
    out: list[str] = []
    i = 0

    while i < len(line):
        if raw_delim is not None:
            raw_end = line.find(")" + str(raw_delim) + '"', i)
            if raw_end == -1:
                if preserve_literals:
                    out.extend(line[i:])
                else:
                    out.extend(" " * (len(line) - i))
                state["raw_delim"] = raw_delim
                return "".join(out)
            end = raw_end + len(str(raw_delim)) + 2
            out.extend(line[i:end] if preserve_literals else " " * (end - i))
            i = end
            raw_delim = None
            continue

        if in_block_comment:
            comment_end = line.find("*/", i)
            if comment_end == -1:
                out.extend(" " * (len(line) - i))
                state["in_block_comment"] = True
                return "".join(out)
            end = comment_end + 2
            out.extend(" " * (end - i))
            i = end
            in_block_comment = False
            continue

        if line.startswith("//", i):
            out.extend(" " * (len(line) - i))
            break
        if line.startswith("/*", i):
            in_block_comment = True
            out.extend("  ")
            i += 2
            continue

        raw_match = _RAW_STRING_START.match(line, i)
        if raw_match:
            raw_delim = raw_match.group(1)
            out.extend(line[i:raw_match.end()] if preserve_literals else " " * (raw_match.end() - i))
            i = raw_match.end()
            continue

        ch = line[i]
        if ch in ("'", '"'):
            quote = ch
            start = i
            i += 1
            while i < len(line):
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == quote:
                    i += 1
                    break
                i += 1
            literal = line[start:i]
            out.extend(literal if preserve_literals else " " * len(literal))
            continue

        out.append(ch)
        i += 1

    state["in_block_comment"] = in_block_comment
    state["raw_delim"] = raw_delim
    return "".join(out)


def _split_args(argument_blob: str) -> list[str]:
    args: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    escape = False
    for ch in argument_blob:
        if quote:
            buf.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")" and depth:
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        args.append(tail)
    return args


def _string_literal_value(text: str) -> Optional[str]:
    match = re.match(r'\s*[u8uUL]*"((?:\\.|[^"\\])*)"', text)
    return match.group(1) if match else None


def _call_arguments(line: str, open_paren: int) -> tuple[list[str], int]:
    depth = 0
    quote: str | None = None
    escape = False
    for i in range(open_paren, len(line)):
        char = line[i]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return _split_args(line[open_paren + 1:i]), i + 1
    return [], len(line)


def _assignment_target(prefix: str) -> str | None:
    match = re.search(r"\b([A-Za-z_]\w*)\s*=\s*(?:(?:\([^()]*\)\s*)*)$", prefix)
    return match.group(1) if match else None


def _diagnostic_findings(error: CompilerError) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    message = (error.message or "").lower()
    for pattern, severity, vuln_type, description, recommendation in _DIAGNOSTIC_RULES:
        if pattern.search(message):
            findings.append(
                _make_finding(
                    error.file or "",
                    error.line,
                    error.column,
                    severity,
                    vuln_type,
                    description,
                    recommendation,
                    "compiler_diagnostic",
                )
            )
    return findings


def _scan_source_findings(source_path: str) -> list[SecurityFinding]:
    path = Path(source_path)
    language = _SUFFIX_LANGUAGE.get(path.suffix.lower())
    if language == "python":
        return _scan_python_findings(path)
    if language == "java":
        return _scan_java_findings(path)
    if language != "cpp":
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    findings: list[SecurityFinding] = []
    mask_state: dict[str, object] = {"in_block_comment": False, "raw_delim": None}
    literal_state: dict[str, object] = {"in_block_comment": False, "raw_delim": None}
    masked_lines: list[str] = []
    visible_lines: list[str] = []
    for raw_line in lines:
        masked_lines.append(_strip_code(raw_line, mask_state))
        visible_lines.append(_strip_code(raw_line, literal_state, preserve_literals=True))

    def emit(line_no: int, column: int | None, name: str, severity: Severity, why: str, fix: str):
        findings.append(
            _make_finding(
                source_path, line_no, column, severity, name, why, fix, "static_scan"
            )
        )

    def calls_on_line(masked: str, visible: str):
        for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", masked):
            args, end = _call_arguments(visible, masked.find("(", match.start()))
            yield match.group(1), args, match.start(1), end

    all_masked = "\n".join(masked_lines)
    declared_types: dict[str, str] = {}
    declaration_pattern = re.compile(
        r"\b((?:unsigned|signed)\s+)?(char|short|int|long|float|double|bool|"
        r"size_t|std::size_t|u?int\d+_t)\s+"
        r"(?:\*\s*)?([A-Za-z_]\w*)\b(?!\s*\()"
        r"(?:\s*\[[^\]]*\])?\s*(?:=\s*([^;,]+))?"
    )
    arrays: dict[str, int] = {}
    declarations: list[tuple[str, int, bool, int]] = []
    for line_index, code in enumerate(masked_lines):
        for declaration in declaration_pattern.finditer(code):
            signedness, base_type, name, initializer = declaration.groups()
            type_text = (signedness or "") + base_type
            declared_types[name] = type_text
            declarations.append(
                (name, line_index, initializer is not None, declaration.start(3))
            )
            array_match = re.search(rf"\b{re.escape(name)}\s*\[\s*(\d+)\s*\]", code)
            if array_match:
                arrays[name] = int(array_match.group(1))

    freed_at: dict[str, tuple[int, bool]] = {}
    allocations: list[tuple[str, int, str, bool, int]] = []
    input_write_positions: set[tuple[int, str, int]] = set()
    for line_index, (masked, visible) in enumerate(zip(masked_lines, visible_lines)):
        if "__asm__" in masked or "asm(" in masked or "__asm" in masked:
            asm_match = re.search(r"__?asm_?_?\s*\(", masked)
            if asm_match:
                emit(
                    line_no, asm_match.start() + 1, "Inline Assembly", "High",
                    "Inline assembly bypasses compiler safety checks and is a security risk.",
                    "Remove inline assembly; use C/C++ standard library functions instead.",
                )
        line_no = line_index + 1
        active_calls = list(calls_on_line(masked, visible))
        for name, (free_index, was_array) in list(freed_at.items()):
            if line_index <= free_index:
                continue
            if re.search(rf"\b{re.escape(name)}\s*(?:->|\[)", masked) or re.search(
                rf"\*\s*{re.escape(name)}\b", masked
            ):
                emit(
                    line_no, masked.find(name) + 1, "Use After Free", "High",
                    f"{name} is accessed after it was released.",
                    "Do not use the pointer after release; set it to nullptr or use an owning smart pointer.",
                )
            release = re.search(rf"\b(?:free\s*\(\s*{re.escape(name)}\s*\)|delete\s*(?:\[\])?\s*{re.escape(name)}\s*;)", masked)
            if release:
                emit(
                    line_no, release.start() + 1, "Double Free", "High",
                    f"{name} appears to be released more than once.",
                    "Release each allocation once and clear or transfer the owning pointer.",
                )
                freed_at[name] = (line_index, was_array)

        for function, args, start, _ in active_calls:
            column = start + 1
            output_args = args[1:] if function == "scanf" else args[:1] if function == "gets" else []
            for argument in output_args:
                output_variable = re.search(r"\b([A-Za-z_]\w*)\b", argument)
                if output_variable:
                    visible_arg_start = visible.find(argument, start)
                    if visible_arg_start >= 0:
                        input_write_positions.add(
                            (line_index, output_variable.group(1), visible_arg_start + output_variable.start(1))
                        )
            if function in RULE_REGISTRY["cpp"]["unsafe_calls"]:
                severity, name, why, fix = RULE_REGISTRY["cpp"]["unsafe_calls"][function]
                emit(line_no, column, name, severity, why, fix)

            if function == "scanf":
                if args:
                    fmt = _string_literal_value(args[0])
                    if fmt is not None and any(
                        not width for width in re.findall(r"%(?:\*?)(\d*)s", fmt)
                    ):
                        emit(
                            line_no, column, "Unbounded scanf Input", "High",
                            "scanf() reads a string with %s and no field-width limit.",
                            "Add a maximum field width or use a bounded string-input API.",
                        )
                call_text = visible[start:]
                checked = bool(re.search(r"\b(?:if|while)\s*\([^)]*\bscanf\s*\(", visible))
                checked = checked or bool(re.search(r"\bscanf\s*\([^;]*\)\s*(?:!=|==|<|>)", call_text))
                assigned_name = _assignment_target(masked[:start])
                if assigned_name:
                    checked = checked or any(
                        re.search(rf"\b(?:if|while)\s*\([^)]*\b{re.escape(assigned_name)}\b[^)]*(?:!=|==|<|>)", later)
                        for later in masked_lines[line_index + 1:]
                    )
                if not checked:
                    emit(
                        line_no, column, "Unchecked scanf Return Value", "Medium",
                        "Ignoring scanf()'s conversion count can leave inputs unset or invalid.",
                        "Check that scanf() returned the expected number of converted fields.",
                    )

            if function in {"fopen", "malloc"}:
                variable = _assignment_target(masked[:start])
                checked = bool(re.search(
                    rf"\b(?:if|while)\s*\([^)]*\b{function}\s*\(", masked
                ))
                checked = checked or bool(variable and any(
                    re.search(
                        rf"\b(?:if|while)\s*\([^)]*(?:!\s*{re.escape(variable)}\b|"
                        rf"\b{re.escape(variable)}\s*(?:==|!=)\s*(?:NULL|nullptr|0))",
                        later,
                    )
                    for later in masked_lines[line_index + 1:]
                ))
                if not checked:
                    emit(
                        line_no, column, f"Unchecked {function} Return Value", "Medium",
                        f"{function}() can fail; its result is not guarded against failure.",
                        "Check the returned pointer for null and handle the failure path.",
                    )

            if function in {"printf", "fprintf", "sprintf", "snprintf"} and args:
                format_index = {"printf": 0, "fprintf": 1, "sprintf": 1, "snprintf": 2}[function]
                format_arg = args[format_index] if len(args) > format_index else ""
                if _string_literal_value(format_arg) is None:
                    emit(
                        line_no, column, "Non-Literal Format String", "High",
                        f"{function}() receives a format string that is not a string literal.",
                        "Use a constant format string and pass variable text as a data argument.",
                    )

            if function in {"system", "popen"} and args:
                arg_is_literal = _string_literal_value(args[0]) is not None
                if arg_is_literal:
                    emit(
                        line_no, column, "Command Injection (Literal)", "High",
                        f"{function}() executes a shell command; even a literal command is dangerous.",
                        "Avoid shell commands entirely; use a direct process API with validated argument arrays.",
                    )
                else:
                    emit(
                        line_no, column, "Command Injection", "High",
                        f"{function}() receives a non-literal command that may include untrusted input.",
                        "Avoid shell commands; use a direct process API with validated argument arrays.",
                    )
            if function == "rand" and (
                _SENSITIVE_WORDS.search(all_masked)
                or any(_SENSITIVE_WORDS.search(item) for item in masked_lines[max(0, line_index - 2):line_index + 3])
            ):
                emit(
                    line_no, column, "Weak Randomness", "High",
                    "rand() is used in code associated with credentials or security-sensitive values.",
                    "Use a cryptographically secure random-number generator for security-sensitive values.",
                )

            if function in {"malloc", "calloc", "realloc"}:
                variable = _assignment_target(masked[:start])
                if variable:
                    allocations.append((variable, line_index, "malloc", False, start))
            if function == "free" and args:
                freed_name = re.search(r"\b([A-Za-z_]\w*)\b", args[0])
                if freed_name:
                    name = freed_name.group(1)
                    freed_at[name] = (line_index, False)

        new_match = re.search(r"\b([A-Za-z_]\w*)\s*=\s*new\b", masked)
        if new_match:
            new_tail = masked[new_match.end():]
            is_array_allocation = bool(
                re.match(r"\s*(?:const\s+)?[\w:<>,\s*&]+\s*\[[^\]]*\]", new_tail)
            )
            allocations.append(
                (new_match.group(1), line_index, "new", is_array_allocation, new_match.start())
            )

        delete_match = re.search(r"\bdelete\s*(\[\s*\])?\s*([A-Za-z_]\w*)\s*;", masked)
        if delete_match:
            is_array_delete = bool(delete_match.group(1))
            name = delete_match.group(2)
            allocation = next((item for item in reversed(allocations) if item[0] == name), None)
            if allocation and allocation[2] == "new" and allocation[3] and not is_array_delete:
                emit(
                    line_no, delete_match.start() + 1, "new[] / delete Mismatch", "High",
                    f"{name} was allocated with new[] but released with delete.",
                    "Release arrays with delete[] or, preferably, store them in a standard container.",
                )
            freed_at[name] = (line_index, is_array_delete)

        if re.search(r"\bassert\s*\(", masked) and not any(
            re.search(r"#\s*(?:if|ifdef|ifndef)\s+.*NDEBUG", source_line, re.IGNORECASE)
            for source_line in lines
        ):
            emit(
                line_no, masked.find("assert") + 1, "Disabled Safety Check", "Low",
                "assert() should not be relied on as a production safety check.",
                "Use an explicit runtime check for input validation and security boundaries.",
            )

        if re.search(r"\*\s*\(\s*[A-Za-z_]\w*\s*\+\s*[^)]+\)", masked):
            emit(
                line_no, None, "Unsafe Pointer Arithmetic", "Medium",
                "Raw pointer arithmetic may move beyond the valid object bounds.",
                "Prefer a container with bounds-aware access or validate the computed offset.",
            )

        if re.search(r"/\s*0(?:[uUlL]*)\b", masked):
            match = re.search(r"/\s*0(?:[uUlL]*)\b", masked)
            emit(
                line_no, match.start() + 1 if match else None, "Division by Zero", "High",
                "The expression divides by the literal zero, causing undefined behavior or a runtime fault.",
                "Validate the divisor before division and handle zero explicitly.",
            )

        if re.search(r"\b(if|while|for)\s*\([^)]*(?<![=!<>])=(?!=)[^)]*\)", masked):
            match = re.search(r"\b(if|while|for)\s*\([^)]*(?<![=!<>])=(?!=)[^)]*\)", masked)
            emit(
                line_no, match.start() + 1 if match else None, "Assignment in Condition", "Medium",
                "Assignment in a condition can silently change the value being tested.",
                "Use == for comparison, or parenthesize and document an intentional assignment.",
            )

        for name, size in arrays.items():
            if re.search(rf"\b{re.escape(name)}\s*\[\s*-?\d+\s*\]", masked):
                access = re.search(rf"\b{re.escape(name)}\s*\[\s*(-?\d+)\s*\]", masked)
                if access and int(access.group(1)) == size and not re.search(
                    rf"\b(?:char|short|int|long|float|double)\b[^;]*\b{re.escape(name)}\s*\[\s*{size}\s*\]\s*;",
                    masked,
                ):
                    emit(
                        line_no, access.start() + 1, "Out-of-Bounds Array Access", "Critical",
                        f"Index {size} is outside {name}, whose valid indices are 0 through {size - 1}.",
                        "Check that every index is non-negative and strictly less than the array length.",
                    )
                elif access and int(access.group(1)) > size:
                    emit(
                        line_no, access.start() + 1, "Out-of-Bounds Array Access", "Critical",
                        f"Index {access.group(1)} is outside {name}, whose valid indices are 0 through {size - 1}.",
                        "Check that every index is non-negative and strictly less than the array length.",
                    )
                elif access and int(access.group(1)) < 0:
                    emit(
                        line_no, access.start() + 1, "Out-of-Bounds Array Access", "Critical",
                        f"Negative index {access.group(1)} is outside {name}.",
                        "Check that every index is non-negative and strictly less than the array length.",
                    )

        if re.search(r"\b(memcpy|memmove)\s*\(", masked):
            memory_call = re.search(r"\b(memcpy|memmove)\s*\(", masked)
            if memory_call:
                emit(
                    line_no, memory_call.start() + 1, "Unsafe Memory Copy", "Medium",
                    f"{memory_call.group(1)}() requires correct source and destination bounds.",
                    "Validate the copy length against both objects or use a bounds-aware container.",
                )
        for line_index, masked in enumerate(masked_lines):
            line_no = line_index + 1
            if re.search(r"__asm__|__asm\b|\basm\s*\(", masked):
                asm_match = re.search(r"__asm__|__asm\b|\basm\s*\(", masked)
                if asm_match:
                    emit(
                        line_no, asm_match.start() + 1, "Inline Assembly", "High",
                        "Inline assembly bypasses compiler safety checks and is a security risk.",
                        "Remove inline assembly; use C/C++ standard library functions instead.",
                    )
    # Report allocations lacking a matching release. This conservative
    # whole-file pairing avoids mistaking a free in another block for a leak.
    for name, allocated_index, kind, _is_array, allocation_column in allocations:
        released = any(
            free_name == name and free_index > allocated_index
            for free_name, (free_index, _array) in freed_at.items()
        )
        if not released:
            emit(
                allocated_index + 1, allocation_column + 1, "Potential Memory Leak", "Medium",
                f"{kind} allocation assigned to {name} has no matching release in this file.",
                "Release the allocation on every exit path or use an owning smart pointer/container.",
            )

    # Definite scalar reads-before-write and unused locals, using the sanitized
    # source so words inside comments and string literals never count as code.
    for name, declaration_index, initialized, declaration_start in declarations:
        events: list[tuple[int, int, bool]] = []
        for index in range(declaration_index, len(masked_lines)):
            code = masked_lines[index]
            for occurrence in re.finditer(rf"\b{re.escape(name)}\b", code):
                if index == declaration_index and occurrence.start() == declaration_start:
                    continue
                suffix = code[occurrence.end():]
                is_write = (
                    (index, name, occurrence.start()) in input_write_positions
                    or bool(re.match(r"\s*(?:\[[^\]]+\]\s*)?(?:[+\-*/%]?=(?!=))", suffix))
                )
                events.append((index, occurrence.start(), is_write))
        reads = [event for event in events if not event[2]]
        if not reads:
            emit(
                declaration_index + 1,
                declaration_start + 1,
                "Unused Variable", "Low",
                f"{name} is declared but never used.",
                "Remove the variable or use it in the intended logic.",
            )
            continue
        if initialized:
            continue
        assigned = False
        for index, column, is_write in events:
            if is_write:
                assigned = True
                continue
            if not assigned:
                emit(
                    index + 1, column + 1, "Uninitialized Variable", "Medium",
                    f"{name} may be read before it receives a value.",
                    "Initialize the variable at declaration or assign it before every read.",
                )
                break

    # Compare identifiers with known signedness for common relational operators.
    for line_index, code in enumerate(masked_lines):
        for comparison in re.finditer(
            r"\b([A-Za-z_]\w*)\s*(?:<|>|<=|>=)\s*([A-Za-z_]\w*)\b", code
        ):
            left_type = declared_types.get(comparison.group(1), "")
            right_type = declared_types.get(comparison.group(2), "")
            left_unsigned = "unsigned" in left_type or left_type in {
                "size_t", "std::size_t"
            } or left_type.startswith("uint")
            right_unsigned = "unsigned" in right_type or right_type in {
                "size_t", "std::size_t"
            } or right_type.startswith("uint")
            if left_type and right_type and left_unsigned != right_unsigned:
                emit(
                    line_index + 1, comparison.start() + 1, "Signed/Unsigned Comparison", "Medium",
                    "A signed value is compared with an unsigned value, which can change the comparison result.",
                    "Convert only after validating range, or use compatible signedness for both operands.",
                )
        for comparison in re.finditer(
            r"\b([A-Za-z_]\w*)\s*(?:<|>|<=|>=)\s*[A-Za-z_]\w*\s*\.\s*size\s*\(\s*\)",
            code,
        ):
            left_type = declared_types.get(comparison.group(1), "")
            if left_type and "unsigned" not in left_type and not left_type.startswith("uint"):
                emit(
                    line_index + 1, comparison.start() + 1, "Signed/Unsigned Comparison", "Medium",
                    "A signed value is compared with an unsigned container size.",
                    "Use a compatible unsigned index type or validate before conversion.",
                )

    return findings


def _scan_java_findings(path: Path) -> list[SecurityFinding]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    findings: list[SecurityFinding] = []
    mask_state: dict[str, object] = {"in_block_comment": False, "raw_delim": None}
    literal_state: dict[str, object] = {"in_block_comment": False, "raw_delim": None}
    masked_lines = [_strip_code(line, mask_state) for line in lines]
    visible_lines = [_strip_code(line, literal_state, preserve_literals=True) for line in lines]
    masked_source = "\n".join(masked_lines)
    visible_source = "\n".join(visible_lines)

    def emit(line_no: int, column: int, name: str, severity: Severity, description: str, recommendation: str):
        findings.append(_make_finding(
            str(path), line_no, column, severity, name, description, recommendation, "static_scan"
        ))

    process_pattern = re.compile(
        r"\b(?:Runtime\s*\.\s*getRuntime\s*\(\s*\)\s*\.\s*exec|new\s+ProcessBuilder)\s*\("
    )
    for process_match in process_pattern.finditer(masked_source):
        open_paren = masked_source.find("(", process_match.start())
        arguments, _end = _call_arguments(visible_source, open_paren)
        if arguments and all(re.fullmatch(r'\s*"(?:\\.|[^"\\])*"\s*', arg) for arg in arguments):
            continue
        line_no = masked_source.count("\n", 0, process_match.start()) + 1
        column = process_match.start() - masked_source.rfind("\n", 0, process_match.start())
        emit(
            line_no, column, "Process Execution", "High",
            "A process is started with at least one non-literal argument that may be influenced by untrusted input.",
            "Avoid process execution where possible; otherwise validate arguments and use a strict allowlist.",
        )

    if re.search(r"\bObjectInputStream\b", masked_source):
        for match in re.finditer(r"\breadObject\s*\(", masked_source):
            line_no = masked_source.count("\n", 0, match.start()) + 1
            column = match.start() - masked_source.rfind("\n", 0, match.start())
            emit(
                line_no, column, "Unsafe Deserialization", "High",
                "ObjectInputStream.readObject() can instantiate attacker-controlled object graphs.",
                "Do not deserialize untrusted Java objects; use a constrained data format and validate its schema.",
            )

    for line_no, visible in enumerate(visible_lines, start=1):
        secret_match = re.search(
            r"\b(?:password|passwd|secret|api[_-]?key|token|credential)\w*\s*=\s*\"[^\"\r\n]+\"",
            visible,
            re.IGNORECASE,
        )
        if secret_match:
            emit(
                line_no, secret_match.start() + 1, "Hardcoded Credential", "High",
                "A credential-like variable is assigned a non-empty string literal.",
                "Load credentials from a secret manager or protected environment configuration.",
            )

    random_variables = {
        match.group(1)
        for match in re.finditer(
            r"\b(?:java\.util\.)?Random\s+([A-Za-z_$]\w*)\s*=\s*new\s+(?:java\.util\.)?Random\s*\(",
            masked_source,
        )
    }
    random_consumers = {
        variable
        for variable in random_variables
        if re.search(
            rf"\b(?:token|secret|key|password|credential|nonce|auth|otp)\w*\s*=\s*{re.escape(variable)}\s*\.\s*next(?:Int|Long|Bytes|Double|Float|Gaussian)\s*\(",
            masked_source,
            re.IGNORECASE,
        )
    }
    for line_no, masked in enumerate(masked_lines, start=1):
        random_match = re.search(r"\bnew\s+(?:java\.util\.)?Random\s*\(", masked)
        sensitive_assignment = re.search(
            r"\b(?:token|secret|key|password|credential|nonce|auth|otp)\w*\s*=\s*new\s+(?:java\.util\.)?Random\s*\(",
            masked,
            re.IGNORECASE,
        )
        if random_match and sensitive_assignment:
            emit(
                line_no, random_match.start() + 1, "Weak Randomness", "Medium",
                "java.util.Random is not suitable for generating security-sensitive values.",
                "Use java.security.SecureRandom for tokens, keys, and other security-sensitive values.",
            )
    for variable in random_consumers:
        consumer = re.compile(
            rf"\b(?:token|secret|key|password|credential|nonce|auth|otp)\w*\s*=\s*{re.escape(variable)}\s*\.\s*next(?:Int|Long|Bytes|Double|Float|Gaussian)\s*\(",
            re.IGNORECASE,
        )
        for match in consumer.finditer(masked_source):
            line_no = masked_source.count("\n", 0, match.start()) + 1
            column = match.start() - masked_source.rfind("\n", 0, match.start())
            emit(
                line_no, column, "Weak Randomness", "Medium",
                "java.util.Random is not suitable for generating security-sensitive values.",
                "Use java.security.SecureRandom for tokens, keys, and other security-sensitive values.",
            )

    dynamic_sql_variables: dict[str, int] = {}
    for assignment in re.finditer(r"\b(?:String|var)\s+([A-Za-z_$]\w*)\s*=\s*([^;]+);", visible_source):
        expression = assignment.group(2)
        if (
            re.search(r"(?i)\b(?:select|insert|update|delete)\b", expression)
            and re.search(r'"(?:\\.|[^"\\])*"\s*\+\s*(?!")\b[A-Za-z_$]\w*', expression)
        ):
            dynamic_sql_variables[assignment.group(1)] = assignment.start()

    for match in re.finditer(r"\b(?:execute|executeQuery|executeUpdate)\s*\(", masked_source):
        open_paren = masked_source.find("(", match.start())
        arguments, _end = _call_arguments(visible_source, open_paren)
        query = arguments[0] if arguments else ""
        is_sql = re.search(r"(?i)\b(?:select|insert|update|delete)\b", query)
        has_dynamic_concat = re.search(
            r'"(?:\\.|[^"\\])*"\s*\+\s*(?!")\b[A-Za-z_$]\w*|\b[A-Za-z_$]\w*\s*\+\s*"',
            query,
        )
        assigned_query = next(
            (name for name in dynamic_sql_variables if re.search(rf"\b{re.escape(name)}\b", query)),
            None,
        )
        if (is_sql and has_dynamic_concat) or assigned_query:
            location = dynamic_sql_variables[assigned_query] if assigned_query else match.start()
            line_no = visible_source.count("\n", 0, location) + 1
            column = location - visible_source.rfind("\n", 0, location)
            emit(
                line_no, column, "SQL String Concatenation", "High",
                "A SQL execution call appears to concatenate a query string, which can allow SQL injection.",
                "Use a PreparedStatement with parameter binding instead of concatenating query values.",
            )

    for match in re.finditer(r"\bcatch\s*\([^)]*\)\s*\{\s*\}", masked_source):
        line_no = masked_source.count("\n", 0, match.start()) + 1
        column = match.start() - masked_source.rfind("\n", 0, match.start())
        emit(
            line_no, column, "Empty Catch Block", "Medium",
            "An empty catch block silently discards an exception and can hide failures.",
            "Handle the exception, add useful context, or rethrow it.",
        )

    for match in re.finditer(r"\bprintStackTrace\s*\(", masked_source):
        line_no = masked_source.count("\n", 0, match.start()) + 1
        column = match.start() - masked_source.rfind("\n", 0, match.start())
        emit(
            line_no, column, "Stack Trace Disclosure", "Low",
            "printStackTrace() writes exception details directly to the process output.",
            "Use structured application logging and avoid exposing stack traces to end users.",
        )

    return _dedupe_findings(findings)


def _scan_python_findings(path: Path) -> list[SecurityFinding]:
    import ast

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path.name)
    except (OSError, UnicodeError, SyntaxError):
        return []

    rules = RULE_REGISTRY["python"]["checks"]
    findings: list[SecurityFinding] = []
    module_aliases: dict[str, str] = {}
    imported_functions: dict[str, tuple[str, str]] = {}
    safe_loader_names = {"SafeLoader"}
    tainted_names: set[str] = set()

    def dotted_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = dotted_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""

    def contains_user_input(node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in tainted_names:
                return True
            if isinstance(child, ast.Call) and (
                dotted_name(child.func) == "input"
                or dotted_name(child.func).startswith(("request.", "flask.request."))
            ):
                return True
            if isinstance(child, ast.Attribute) and dotted_name(child).startswith(("request.", "flask.request.")):
                return True
            if isinstance(child, ast.Subscript) and dotted_name(child.value).startswith(("request.", "flask.request.", "sys.argv")):
                return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported_functions[alias.asname or alias.name] = (node.module.split(".", 1)[0], alias.name)
                if node.module.split(".", 1)[0] == "yaml" and alias.name == "SafeLoader":
                    safe_loader_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.arg) and re.search(
            r"(?:user|input|request|payload|data|path|token|password|credential)",
            node.arg,
            re.IGNORECASE,
        ):
            tainted_names.add(node.arg)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if contains_user_input(value):
                for target in targets:
                    if isinstance(target, ast.Name):
                        tainted_names.add(target.id)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if contains_user_input(node.value):
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in tainted_names:
                        tainted_names.add(target.id)
                        changed = True

    def rule(name: str):
        return rules[name]

    def emit(node: ast.AST, name: str):
        severity, title, description, recommendation = rule(name)
        findings.append(_make_finding(
            str(path),
            getattr(node, "lineno", None),
            (getattr(node, "col_offset", -1) + 1) if hasattr(node, "col_offset") else None,
            severity, title, description, recommendation, "static_scan",
        ))

    def is_input_expression(node: ast.AST) -> bool:
        return contains_user_input(node)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = dotted_name(node.func)
            import_owner, imported_name = imported_functions.get(called, ("", ""))
            if called in {"eval", "exec"} or (import_owner == "builtins" and imported_name in {"eval", "exec"}):
                emit(node, "dynamic_code")
            elif called.split(".")[-1] in {"load", "loads"} and (
                (called.split(".", 1)[0] in module_aliases and module_aliases[called.split(".", 1)[0]] == "pickle")
                or (import_owner == "pickle" and imported_name in {"load", "loads"})
            ):
                emit(node, "unsafe_deserialization")
            elif called.split(".", 1)[0] in module_aliases and module_aliases[called.split(".", 1)[0]] == "subprocess":
                if any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True for keyword in node.keywords):
                    emit(node, "shell_execution")
            elif imported_name in {"run", "Popen", "call", "check_call", "check_output"} and import_owner == "subprocess":
                if any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True for keyword in node.keywords):
                    emit(node, "shell_execution")
            elif called.endswith(".system") and (
                called.split(".", 1)[0] in module_aliases and module_aliases[called.split(".", 1)[0]] == "os"
                or imported_name == "system" and import_owner == "os"
            ):
                argument = node.args[0] if node.args else None
                if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                    emit(node, "dynamic_system_command")
            elif called.endswith(".load") and (
                called.split(".", 1)[0] in module_aliases and module_aliases[called.split(".", 1)[0]] == "yaml"
                or imported_name == "load" and import_owner == "yaml"
            ):
                safe_loader = any(
                    keyword.arg == "Loader" and (
                        dotted_name(keyword.value).split(".")[-1] in safe_loader_names
                    )
                    for keyword in node.keywords
                )
                if not safe_loader:
                    emit(node, "unsafe_yaml")
            elif called.split(".")[-1] == "open" and node.args and is_input_expression(node.args[0]):
                emit(node, "user_path")

            if called in {"eval", "exec"} and node.args and is_input_expression(node.args[0]):
                emit(node, "dynamic_code")

        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                target_name = (
                    target.id if isinstance(target, ast.Name)
                    else target.attr if isinstance(target, ast.Attribute)
                    else ""
                )
                if re.search(r"(?:password|passwd|api[_-]?key|token|secret|credential)", target_name, re.IGNORECASE):
                    if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value:
                        emit(node, "hardcoded_secret")

        elif isinstance(node, ast.Assert) and is_input_expression(node.test):
            emit(node, "assert_validation")

    return _dedupe_findings(findings)


def _dedupe_findings(findings: list[SecurityFinding]) -> list[SecurityFinding]:
    deduped: list[SecurityFinding] = []
    seen: set[tuple] = set()
    for finding in findings:
        key = (
            finding.file,
            finding.line,
            finding.column,
            finding.severity,
            finding.vulnerability_type,
        )
        if key not in seen:
            seen.add(key)
            deduped.append(finding)
    deduped.sort(
        key=lambda finding: (
            _SEVERITY_ORDER[finding.severity],
            finding.file or "",
            finding.line if finding.line is not None else 10**9,
            finding.column if finding.column is not None else 10**9,
            finding.vulnerability_type,
        )
    )
    return deduped


def analyze(source_path: str, compiler_errors: list[CompilerError]) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    for error in compiler_errors or []:
        findings.extend(_diagnostic_findings(error))
    findings.extend(_scan_source_findings(source_path))
    return _dedupe_findings(findings)


def _severity_counts(findings: list[SecurityFinding]) -> dict[Severity, int]:
    counts: dict[Severity, int] = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def _risk_label(score: int) -> str:
    if score == 0:
        return "Healthy"
    if score >= 10:
        return "Unhealthy"
    if score >= 4:
        return "Moderate"
    return "Healthy"


def classify_security_health(findings: list[SecurityFinding]) -> dict:
    deduped = _dedupe_findings(findings)
    score = sum(_SEVERITY_WEIGHT[finding.severity] for finding in deduped)
    counts = _severity_counts(deduped)
    line_numbers = sorted({finding.line for finding in deduped if finding.line is not None})
    if counts["High"] or counts["Critical"] or score >= 10:
        verdict = "UNHEALTHY"
        label = "High Risk"
        severity = "High"
    elif counts["Medium"] or 4 <= score <= 9:
        verdict = "MODERATE"
        label = "Moderate Risk"
        severity = "Moderate"
    else:
        verdict = "HEALTHY"
        label = "Healthy"
        severity = "Low" if deduped else "None"

    rules_by_line: dict[int, list[str]] = {}
    for finding in deduped:
        if finding.line is not None:
            rules_by_line.setdefault(finding.line, []).append(finding.vulnerability_type)
    offending_lines = [
        {"line": line, "rules": sorted(set(rules_by_line[line]))}
        for line in line_numbers
    ]
    if not deduped:
        explanation = "No compiler diagnostics or static security findings were detected."
    else:
        explanation = (
            f"{verdict.title()} verdict: {counts['Critical']} critical, {counts['High']} high, "
            f"{counts['Medium']} medium, and {counts['Low']} low findings across "
            f"{len(line_numbers)} offending line(s)."
        )
    recommendations = list(dict.fromkeys(
        finding.recommendation for finding in deduped
    ))
    return {
        "verdict": verdict,
        "label": label,
        "severity": severity,
        "score": score,
        "line_numbers": line_numbers,
        "offending_lines": offending_lines,
        "explanation": explanation,
        "remediation_suggestions": recommendations[:5] or [
            "Keep the codebase clean and continue security reviews with normal checks."
        ],
        "findings": [finding.to_dict() for finding in deduped],
    }


def analyze_security_health(source_path: str, compiler_errors: list[CompilerError]) -> dict:
    return classify_security_health(analyze(source_path, compiler_errors))


def format_security_report(findings: list[SecurityFinding]) -> str:
    findings = _dedupe_findings(findings)
    counts = _severity_counts(findings)
    health = classify_security_health(findings)
    lines = [
        f"{counts['Critical']} critical, {counts['High']} high, {counts['Medium']} medium, {counts['Low']} low findings",
        f"Health verdict: {health['verdict']} (score={health['score']})",
        "Score formula: 10*Critical + 6*High + 3*Medium + 1*Low",
        "Offending lines: " + (", ".join(map(str, health["line_numbers"])) or "none"),
        "",
    ]
    for severity in ("Critical", "High", "Medium", "Low"):
        for finding in findings:
            if finding.severity != severity:
                continue
            location = finding.file
            if finding.line is not None:
                location += f":{finding.line}"
            if finding.column is not None:
                location += f":{finding.column}"
            lines.extend([
                f"[{severity.upper()}] {finding.vulnerability_type} - {location}",
                f"  Why it matters: {finding.description}",
                f"  Fix: {finding.recommendation}",
                "",
            ])
    return "\n".join(lines).strip() + "\n"
