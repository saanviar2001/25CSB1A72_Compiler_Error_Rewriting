"""Toy recursive-descent parser.

Grammar:
    program    -> stmt*
    stmt       -> decl | assign | if_stmt | while_stmt | call ';' | block
    decl       -> type IDENT ('=' expr)? ';'
    assign     -> IDENT '=' expr ';'
    if_stmt    -> 'if' '(' expr ')' stmt ('else' stmt)?
    while_stmt -> 'while' '(' expr ')' stmt
    block      -> '{' stmt* '}'
    call       -> IDENT '(' (expr (',' expr)*)? ')'
    expr       -> term (('+'|'-'|'=='|'<'|'>') term)*
    term       -> factor (('*'|'/') factor)*
    factor     -> NUMBER | IDENT | call | '(' expr ')'
    type       -> 'int' | 'float' | 'double' | 'char' | 'bool'
"""


class GrammarParseError(Exception):
    def __init__(self, rule, expected, found, line, outside_grammar=False):
        self.rule = rule
        self.expected = expected
        self.found = found
        self.line = line
        self.outside_grammar = outside_grammar
        super().__init__(
            f"rule {rule}: expected {expected}, found {found} at line {line}"
        )


class Token:
    def __init__(self, value, kind, line):
        self.value = value
        self.kind = kind
        self.line = line


_TOY_TYPES = {"int", "float", "double", "char", "bool"}
_OUTSIDE_KEYWORDS = {
    "class", "struct", "template", "namespace", "using", "return",
    "for", "do", "switch", "case", "break", "continue", "const",
    "void", "long", "short", "unsigned", "signed", "public",
    "private", "protected", "virtual", "auto", "new", "delete",
    "include",
}


def _tokenize(source):
    tokens = []
    index = 0
    line = 1
    while index < len(source):
        char = source[index]
        if char in " \t\r":
            index += 1
            continue
        if char == "\n":
            line += 1
            index += 1
            continue
        if char.isalpha() or char == "_":
            start = index
            while index < len(source) and (source[index].isalnum() or source[index] == "_"):
                index += 1
            value = source[start:index]
            tokens.append(Token(value, "IDENT", line))
            continue
        if char.isdigit():
            start = index
            while index < len(source) and source[index].isdigit():
                index += 1
            if index < len(source) and source[index] == ".":
                index += 1
                while index < len(source) and source[index].isdigit():
                    index += 1
            tokens.append(Token(source[start:index], "NUMBER", line))
            continue
        if source.startswith("==", index):
            tokens.append(Token("==", "SYMBOL", line))
            index += 2
            continue
        if char in "{}();,=+-*/<>":
            tokens.append(Token(char, "SYMBOL", line))
            index += 1
            continue
        tokens.append(Token(char, "UNSUPPORTED", line))
        index += 1
    tokens.append(Token("<EOF>", "EOF", line))
    return tokens


class GrammarParser:
    def __init__(self, source):
        self.tokens = _tokenize(source)
        self.position = 0

    @property
    def current(self):
        return self.tokens[self.position]

    def _found(self):
        return "end of input" if self.current.kind == "EOF" else repr(self.current.value)

    def _error(self, rule, expected):
        outside = self.current.kind == "UNSUPPORTED" or self.current.value in _OUTSIDE_KEYWORDS
        raise GrammarParseError(rule, expected, self._found(), self.current.line, outside)

    def _accept(self, value):
        if self.current.value == value:
            token = self.current
            self.position += 1
            return token
        return None

    def _expect(self, value, rule):
        token = self._accept(value)
        if token is None:
            self._error(rule, repr(value))
        return token

    def parse(self):
        self.program()
        self._expect("<EOF>", "program")
        return True

    def program(self):
        while self.current.kind != "EOF":
            if self.current.kind == "UNSUPPORTED" or self.current.value in _OUTSIDE_KEYWORDS:
                self._error("stmt", "a toy-grammar statement")
            self.stmt()

    def stmt(self):
        if self.current.value in _TOY_TYPES:
            self.decl()
        elif self.current.value == "if":
            self.if_stmt()
        elif self.current.value == "while":
            self.while_stmt()
        elif self.current.value == "{":
            self.block()
        elif self.current.kind == "IDENT":
            if self.tokens[self.position + 1].value == "(":
                self.call()
                self._expect(";", "stmt")
            else:
                self.assign()
        else:
            self._error("stmt", "type, IDENT, 'if', 'while', or '{'")

    def decl(self):
        self.type()
        self._expect_kind("IDENT", "decl")
        if self._accept("="):
            self.expr()
        self._expect(";", "decl")

    def assign(self):
        self._expect_kind("IDENT", "assign")
        self._expect("=", "assign")
        self.expr()
        self._expect(";", "assign")

    def if_stmt(self):
        self._expect("if", "if_stmt")
        self._expect("(", "if_stmt")
        self.expr()
        self._expect(")", "if_stmt")
        self.stmt()
        if self._accept("else"):
            self.stmt()

    def while_stmt(self):
        self._expect("while", "while_stmt")
        self._expect("(", "while_stmt")
        self.expr()
        self._expect(")", "while_stmt")
        self.stmt()

    def block(self):
        self._expect("{", "block")
        while self.current.value != "}" and self.current.kind != "EOF":
            self.stmt()
        self._expect("}", "block")

    def call(self):
        self._expect_kind("IDENT", "call")
        self._expect("(", "call")
        if self.current.value != ")":
            self.expr()
            while self._accept(","):
                self.expr()
        self._expect(")", "call")

    def expr(self):
        self.term()
        while self.current.value in {"+", "-", "==", "<", ">"}:
            self.position += 1
            self.term()

    def term(self):
        self.factor()
        while self.current.value in {"*", "/"}:
            self.position += 1
            self.factor()

    def factor(self):
        if self.current.kind == "NUMBER":
            self.position += 1
        elif self.current.kind == "IDENT":
            if self.tokens[self.position + 1].value == "(":
                self.call()
            else:
                self.position += 1
        elif self._accept("("):
            self.expr()
            self._expect(")", "factor")
        else:
            self._error("factor", "NUMBER, IDENT, call, or '('")

    def type(self):
        if self.current.value not in _TOY_TYPES:
            self._error("type", "'int', 'float', 'double', 'char', or 'bool'")
        self.position += 1

    def _expect_kind(self, kind, rule):
        if self.current.kind != kind:
            self._error(rule, kind)
        token = self.current
        self.position += 1
        return token


def parse(source):
    """Parse source text, raising GrammarParseError on invalid or unsupported input."""
    return GrammarParser(source).parse()