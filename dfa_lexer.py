import re


CODE = "CODE"
SLASH = "SLASH"
IN_STRING = "IN_STRING"
IN_ESCAPE = "IN_ESCAPE"
IN_CHAR = "IN_CHAR"
IN_CHAR_ESCAPE = "IN_CHAR_ESCAPE"
IN_LINE_COMMENT = "IN_LINE_COMMENT"
IN_BLOCK_COMMENT = "IN_BLOCK_COMMENT"
IN_BLOCK_COMMENT_STAR = "IN_BLOCK_COMMENT_STAR"

TRANSITIONS = {
    CODE: {
        "OTHER": CODE,
        "QUOTE": IN_STRING,
        "APOSTROPHE": IN_CHAR,
        "BACKSLASH": CODE,
        "NEWLINE": CODE,
        "STAR": CODE,
        "SLASH": SLASH,
    },
    SLASH: {
        "OTHER": CODE,
        "QUOTE": CODE,
        "APOSTROPHE": CODE,
        "BACKSLASH": CODE,
        "NEWLINE": CODE,
        "STAR": IN_BLOCK_COMMENT,
        "SLASH": IN_LINE_COMMENT,
    },
    IN_STRING: {
        "OTHER": IN_STRING,
        "QUOTE": CODE,
        "APOSTROPHE": IN_STRING,
        "BACKSLASH": IN_ESCAPE,
        "NEWLINE": IN_STRING,
        "STAR": IN_STRING,
        "SLASH": IN_STRING,
    },
    IN_ESCAPE: {
        "OTHER": IN_STRING,
        "QUOTE": IN_STRING,
        "APOSTROPHE": IN_STRING,
        "BACKSLASH": IN_STRING,
        "NEWLINE": IN_STRING,
        "STAR": IN_STRING,
        "SLASH": IN_STRING,
    },
    IN_CHAR: {
        "OTHER": IN_CHAR,
        "QUOTE": IN_CHAR,
        "APOSTROPHE": CODE,
        "BACKSLASH": IN_CHAR_ESCAPE,
        "NEWLINE": IN_CHAR,
        "STAR": IN_CHAR,
        "SLASH": IN_CHAR,
    },
    IN_CHAR_ESCAPE: {
        "OTHER": IN_CHAR,
        "QUOTE": IN_CHAR,
        "APOSTROPHE": IN_CHAR,
        "BACKSLASH": IN_CHAR,
        "NEWLINE": IN_CHAR,
        "STAR": IN_CHAR,
        "SLASH": IN_CHAR,
    },
    IN_LINE_COMMENT: {
        "OTHER": IN_LINE_COMMENT,
        "QUOTE": IN_LINE_COMMENT,
        "APOSTROPHE": IN_LINE_COMMENT,
        "BACKSLASH": IN_LINE_COMMENT,
        "NEWLINE": CODE,
        "STAR": IN_LINE_COMMENT,
        "SLASH": IN_LINE_COMMENT,
    },
    IN_BLOCK_COMMENT: {
        "OTHER": IN_BLOCK_COMMENT,
        "QUOTE": IN_BLOCK_COMMENT,
        "APOSTROPHE": IN_BLOCK_COMMENT,
        "BACKSLASH": IN_BLOCK_COMMENT,
        "NEWLINE": IN_BLOCK_COMMENT,
        "STAR": IN_BLOCK_COMMENT_STAR,
        "SLASH": IN_BLOCK_COMMENT,
    },
    IN_BLOCK_COMMENT_STAR: {
        "OTHER": IN_BLOCK_COMMENT,
        "QUOTE": IN_BLOCK_COMMENT,
        "APOSTROPHE": IN_BLOCK_COMMENT,
        "BACKSLASH": IN_BLOCK_COMMENT,
        "NEWLINE": IN_BLOCK_COMMENT,
        "STAR": IN_BLOCK_COMMENT_STAR,
        "SLASH": CODE,
    },
}


PYTHON_TRANSITIONS = {
    CODE: {
        "OTHER": CODE,
        "HASH": IN_LINE_COMMENT,
        "QUOTE": IN_STRING,
        "APOSTROPHE": IN_CHAR,
        "BACKSLASH": CODE,
        "NEWLINE": CODE,
    },
    IN_LINE_COMMENT: {
        "OTHER": IN_LINE_COMMENT,
        "HASH": IN_LINE_COMMENT,
        "QUOTE": IN_LINE_COMMENT,
        "APOSTROPHE": IN_LINE_COMMENT,
        "BACKSLASH": IN_LINE_COMMENT,
        "NEWLINE": CODE,
    },
    IN_STRING: {
        "OTHER": IN_STRING,
        "HASH": IN_STRING,
        "QUOTE": CODE,
        "APOSTROPHE": IN_STRING,
        "BACKSLASH": IN_ESCAPE,
        "NEWLINE": CODE,
    },
    IN_ESCAPE: {
        "OTHER": IN_STRING,
        "HASH": IN_STRING,
        "QUOTE": IN_STRING,
        "APOSTROPHE": IN_STRING,
        "BACKSLASH": IN_STRING,
        "NEWLINE": IN_STRING,
    },
    IN_CHAR: {
        "OTHER": IN_CHAR,
        "HASH": IN_CHAR,
        "QUOTE": IN_CHAR,
        "APOSTROPHE": CODE,
        "BACKSLASH": IN_CHAR_ESCAPE,
        "NEWLINE": CODE,
    },
    IN_CHAR_ESCAPE: {
        "OTHER": IN_CHAR,
        "HASH": IN_CHAR,
        "QUOTE": IN_CHAR,
        "APOSTROPHE": IN_CHAR,
        "BACKSLASH": IN_CHAR,
        "NEWLINE": IN_CHAR,
    },
}


def _input_class(char: str) -> str:
    if char == '"':
        return "QUOTE"
    if char == "'":
        return "APOSTROPHE"
    if char == "\\":
        return "BACKSLASH"
    if char in "\r\n":
        return "NEWLINE"
    if char == "*":
        return "STAR"
    if char == "/":
        return "SLASH"
    return "OTHER"


def _input_class_python(char: str) -> str:
    if char == "#":
        return "HASH"
    if char == '"':
        return "QUOTE"
    if char == "'":
        return "APOSTROPHE"
    if char == "\\":
        return "BACKSLASH"
    if char in "\r\n":
        return "NEWLINE"
    return "OTHER"


def trace(source: str, language: str = "cpp") -> list[tuple[int, str, str, str]]:
    lang = (language or "cpp").lower()
    if lang in ("python", "py"):
        state = CODE
        result = []
        for index, char in enumerate(source):
            state_before = state
            input_class = _input_class_python(char)
            state = PYTHON_TRANSITIONS[state_before][input_class]
            result.append((index, char, state_before, state))
        return result

    state = CODE
    result = []
    for index, char in enumerate(source):
        state_before = state
        input_class = _input_class(char)
        state = TRANSITIONS[state_before][input_class]
        result.append((index, char, state_before, state))

    return result


DEFAULT_TRACE_LIMIT = 600

_CPP_KEYWORDS = frozenset(
    "alignas alignof asm auto bool break case catch char class const constexpr "
    "continue decltype default delete do double else enum explicit export extern "
    "false float for friend goto if inline int long mutable namespace new noexcept "
    "nullptr operator private protected public register reinterpret_cast return "
    "short signed sizeof static static_assert static_cast struct switch template "
    "this thread_local throw true try typedef typeid typename union unsigned using "
    "virtual void volatile wchar_t while".split()
)
_JAVA_KEYWORDS = frozenset(
    "abstract assert boolean break byte case catch char class const continue default "
    "do double else enum extends final finally float for goto if implements import "
    "instanceof int interface long native new package private protected public return "
    "short static strictfp super switch synchronized this throw throws transient try "
    "void volatile while true false null".split()
)
_PYTHON_KEYWORDS = frozenset(
    "False None True and as assert async await break class continue def del elif else "
    "except finally for from global if import in is lambda nonlocal not or pass raise "
    "return try while with yield".split()
)
_NUMBER_RE = re.compile(
    r"(?:0[xX][0-9A-Fa-f]+|0[bB][01]+|(?:\d+(?:\.\d*)?|\.\d+))"
    r"(?:[eE][+-]?\d+)?[uUlLfF]*"
)
_OPERATORS = (
    "->*", "<<=", ">>=", "...", "++", "--", "->", ".*", "::", "&&", "||",
    "<=", ">=", "==", "!=", "+=", "-=", "*=", "/=", "%=", "&=", "|=",
    "^=", "<<", ">>", "##",
)
_SINGLE_OPERATORS = frozenset("+-*/%<>=!&|^~?:.#")
_DELIMITERS = frozenset("(){}[],;")


def _keyword_set(language: str) -> frozenset[str]:
    if language in {"python", "py"}:
        return _PYTHON_KEYWORDS
    if language in {"java", "j"}:
        return _JAVA_KEYWORDS
    return _CPP_KEYWORDS


def _operator_state(token: str) -> str:
    if token in {"+", "-", "*", "/", "%", "++", "--"}:
        return "ARITHMETIC_OPERATOR"
    if token in {"<", ">", "<=", ">=", "==", "!="}:
        return "RELATIONAL_OPERATOR"
    if token in {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}:
        return "ASSIGNMENT_OPERATOR"
    if token in {"&&", "||", "!"}:
        return "LOGICAL_OPERATOR"
    if token in {"&", "|", "^", "~", "<<", ">>", "##"}:
        return "BITWISE_OPERATOR"
    return "OPERATOR"


def scan_detailed(
    source: str,
    language: str = "cpp",
    max_transitions: int | None = DEFAULT_TRACE_LIMIT,
) -> dict:
    """Scan source and return real character transitions plus trace metadata.

    Unlike :func:`trace`, this richer representation classifies C-like tokens
    while preserving a row for each source character. The optional limit only
    limits retained rows; scanning and counting always continue through EOF.
    """
    if not isinstance(source, str):
        raise TypeError("source must be text")
    if max_transitions is not None and max_transitions < 0:
        raise ValueError("max_transitions must be non-negative or None")

    lang = (language or "cpp").lower()
    keywords = _keyword_set(lang)
    python_comments = lang in {"python", "py"}
    transitions = []
    diagnostics = []
    diagnostic_count = 0
    total_count = 0
    state = CODE
    line = 1
    column = 1
    index = 0

    def emit(
        char: str,
        next_state: str,
        description: str,
        input_class: str,
        token: str | None = None,
        token_type: str | None = None,
    ):
        nonlocal state, line, column, total_count
        if max_transitions is None or total_count < max_transitions:
            transitions.append({
                "index": total_count,
                "character": repr(char),
                "before": state,
                "after": next_state,
                "input": input_class,
                "token": token,
                "token_type": token_type,
                "description": description,
                "line": line,
                "column": column,
            })
        state = next_state
        total_count += 1
        if char == "\n" or (char == "\r" and (index + 1 >= len(source) or source[index + 1] != "\n")):
            line += 1
            column = 1
        else:
            column += 1

    def report_diagnostic(message: str, start_line: int, start_column: int, token_type: str):
        nonlocal diagnostic_count
        if diagnostic_count < 20:
            diagnostics.append({
                "message": message,
                "line": start_line,
                "column": start_column,
                "token_type": token_type,
            })
        diagnostic_count += 1

    def emit_span(
        start: int,
        end: int,
        next_state: str,
        input_class: str,
        token: str,
        token_type: str,
        description: str,
    ):
        nonlocal index
        for offset in range(start, end):
            char = source[offset]
            emit(char, next_state, description, input_class, token, token_type)
            index = offset

    while index < len(source):
        char = source[index]

        if char in " \t\f\v":
            emit(char, "WHITESPACE", "Skip whitespace between tokens.", "WHITESPACE", char, "whitespace")
            index += 1
            continue
        if char in "\r\n":
            emit(char, "CODE", "End the current line.", "NEWLINE", char, "newline")
            index += 1
            continue

        if python_comments and char == "#":
            end = index
            while end < len(source) and source[end] not in "\r\n":
                end += 1
            comment = source[index:end]
            emit(char, "IN_LINE_COMMENT", "Start a Python line comment.", "COMMENT_START", comment, "comment")
            index += 1
            for offset in range(index, end):
                emit(source[offset], "IN_LINE_COMMENT", "Read comment text; token markers are ignored.", "COMMENT_TEXT", comment, "comment")
                index += 1
            continue

        is_cpp_comment = not python_comments and source.startswith("//", index)
        is_block_comment = not python_comments and source.startswith("/*", index)
        if is_cpp_comment:
            end = index + 2
            while end < len(source) and source[end] not in "\r\n":
                end += 1
            comment = source[index:end]
            emit(char, "COMMENT_START", "Begin a single-line comment.", "COMMENT_START", comment, "comment")
            emit(source[index + 1], "IN_LINE_COMMENT", "Enter single-line comment state.", "COMMENT_DELIMITER", comment, "comment")
            index += 2
            while index < end:
                emit(source[index], "IN_LINE_COMMENT", "Read comment text; token markers are ignored.", "COMMENT_TEXT", comment, "comment")
                index += 1
            continue
        if is_block_comment:
            end = source.find("*/", index + 2)
            closed = end >= 0
            end = end + 2 if closed else len(source)
            comment = source[index:end]
            start_line, start_column = line, column
            emit(char, "COMMENT_START", "Begin a block comment.", "COMMENT_START", comment, "comment")
            emit(source[index + 1], "IN_BLOCK_COMMENT", "Enter block comment state.", "COMMENT_DELIMITER", comment, "comment")
            index += 2
            while index < end:
                current = source[index]
                if state == "IN_BLOCK_COMMENT_STAR" and current == "/":
                    next_state = "CODE"
                    description = "Close the block comment."
                    input_class = "COMMENT_END"
                elif current == "*":
                    next_state = "IN_BLOCK_COMMENT_STAR"
                    description = "Check whether this star begins the comment terminator."
                    input_class = "COMMENT_TEXT"
                else:
                    next_state = "IN_BLOCK_COMMENT"
                    description = "Read block comment text; token markers are ignored."
                    input_class = "COMMENT_TEXT"
                emit(current, next_state, description, input_class, comment, "comment")
                index += 1
            if not closed:
                report_diagnostic("Unterminated block comment.", start_line, start_column, "comment")
            continue

        if char in {'"', "'"}:
            quote = char
            triple = python_comments and source.startswith(char * 3, index)
            delimiter_length = 3 if triple else 1
            start = index
            start_line, start_column = line, column
            emitted_records = []
            for offset in range(delimiter_length):
                records_before = len(transitions)
                emit(source[index], "IN_STRING" if quote == '"' else "IN_CHAR",
                     "Open a string literal." if quote == '"' else "Open a character literal.",
                     "STRING_DELIMITER" if quote == '"' else "CHAR_DELIMITER",
                     None, "string" if quote == '"' else "character")
                if len(transitions) > records_before:
                    emitted_records.append(transitions[-1])
                index += 1

            closed = False
            newline_terminated = False
            while index < len(source):
                if source.startswith(char * delimiter_length, index):
                    token_end = index + delimiter_length
                    for offset in range(index, token_end):
                        final_char = offset == token_end - 1
                        records_before = len(transitions)
                        emit(source[offset], "CODE" if final_char else state,
                             "Close the string literal." if final_char and quote == '"' else
                             "Close the character literal." if final_char else
                             "Finish the string delimiter.",
                             "STRING_DELIMITER" if quote == '"' else "CHAR_DELIMITER",
                             None, "string" if quote == '"' else "character")
                        if len(transitions) > records_before:
                            emitted_records.append(transitions[-1])
                    index = token_end
                    closed = True
                    break

                current = source[index]
                if current == "\\":
                    records_before = len(transitions)
                    emit(current, "IN_ESCAPE", "Escape the next character in the literal.", "ESCAPE", None,
                         "string" if quote == '"' else "character")
                    if len(transitions) > records_before:
                        emitted_records.append(transitions[-1])
                    index += 1
                    if index < len(source):
                        records_before = len(transitions)
                        emit(source[index], "IN_STRING" if quote == '"' else "IN_CHAR",
                             "Read an escaped character; it does not close the literal.", "ESCAPED_CHARACTER",
                             None, "string" if quote == '"' else "character")
                        if len(transitions) > records_before:
                            emitted_records.append(transitions[-1])
                        index += 1
                    continue
                if current in "\r\n" and not triple:
                    emit(current, "CODE", "End an unterminated literal at the line break.", "NEWLINE",
                         None, "string" if quote == '"' else "character")
                    index += 1
                    newline_terminated = True
                    break
                records_before = len(transitions)
                emit(current, "IN_STRING" if quote == '"' else "IN_CHAR",
                     "Read a character inside the literal.", "STRING_TEXT" if quote == '"' else "CHAR_TEXT",
                     None, "string" if quote == '"' else "character")
                if len(transitions) > records_before:
                    emitted_records.append(transitions[-1])
                index += 1

            literal = source[start:index]
            for record in emitted_records:
                record["token"] = literal
            if not closed:
                report_diagnostic(
                    "Unterminated string literal." if quote == '"' else "Unterminated character literal.",
                    start_line,
                    start_column,
                    "string" if quote == '"' else "character",
                )
            if newline_terminated:
                state = CODE
            continue

        if char.isalpha() or char == "_":
            end = index + 1
            while end < len(source) and (source[end].isalnum() or source[end] == "_"):
                end += 1
            token = source[index:end]
            is_keyword = token in keywords
            next_state = "KEYWORD" if is_keyword else "IDENTIFIER"
            token_type = "keyword" if is_keyword else "identifier"
            description = (
                f"Recognize keyword {token!r}." if is_keyword
                else f"Read identifier {token!r}."
            )
            emit_span(index, end, next_state, "WORD_CHARACTER", token, token_type, description)
            index = end
            continue

        if char.isdigit() or (char == "." and index + 1 < len(source) and source[index + 1].isdigit()):
            match = _NUMBER_RE.match(source, index)
            if match:
                token = match.group(0)
                end = match.end()
                is_hex_or_binary = token.lower().startswith(("0x", "0b"))
                is_float = not is_hex_or_binary and (
                    any(marker in token for marker in ".eE") or token[-1:] in {"f", "F"}
                )
                token_type = "floating_point" if is_float else "integer"
                description = f"Read {'floating-point' if is_float else 'integer'} literal {token!r}."
                for offset in range(index, end):
                    current = source[offset]
                    if is_hex_or_binary:
                        next_state = (
                            "NUMBER_SUFFIX"
                            if current in "uUlL" and offset > index + 1
                            else "INTEGER"
                        )
                    elif current in ".eE":
                        next_state = "DECIMAL_POINT" if current == "." else "EXPONENT"
                    elif current in "+-" and offset > index and source[offset - 1] in "eE":
                        next_state = "EXPONENT"
                    elif current in "uUlLfF" and offset > index and not source[offset - 1].isdigit():
                        next_state = "NUMBER_SUFFIX"
                    else:
                        next_state = "FLOAT" if is_float else "INTEGER"
                    emit(current, next_state, description, "NUMBER_CHARACTER", token, token_type)
                    index += 1
                continue

        operator = next((candidate for candidate in _OPERATORS if source.startswith(candidate, index)), None)
        if operator is None and char in _SINGLE_OPERATORS:
            operator = char
        if operator is not None:
            token_type = _operator_state(operator)
            description = f"Recognize {token_type.lower().replace('_', ' ')} {operator!r}."
            operator_end = index + len(operator)
            emit_span(index, operator_end, token_type, "OPERATOR_CHARACTER", operator, "operator", description)
            index = operator_end
            continue

        if char in _DELIMITERS:
            if char in "()":
                next_state = "PARENTHESIS"
            elif char in "{}":
                next_state = "BRACE"
            elif char in "[]":
                next_state = "BRACKET"
            else:
                next_state = "DELIMITER"
            emit(char, next_state, f"Recognize delimiter {char!r}.", "DELIMITER", char, "delimiter")
            index += 1
            continue

        emit(char, "UNKNOWN", f"Unrecognized source character {char!r}.", "UNKNOWN", char, "unknown")
        index += 1

    return {
        "transitions": transitions,
        "total_count": total_count,
        "displayed_count": len(transitions),
        "truncated": total_count > len(transitions),
        "limit": max_transitions,
        "diagnostics": diagnostics,
        "diagnostic_count": diagnostic_count,
        "diagnostics_truncated": diagnostic_count > len(diagnostics),
    }