"""Tiny helper: syntax-check files without importing them (avoids PyQt6 etc.).

Usage:  python scratch/pycheck.py src/heal_agent.py src/auto_healer.py
"""
import ast
import sys

failed = False
for path in sys.argv[1:]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            ast.parse(handle.read(), filename=path)
        print(f"PARSE OK   {path}")
    except SyntaxError as exc:
        failed = True
        print(f"PARSE FAIL {path}: line {exc.lineno}: {exc.msg}")
sys.exit(1 if failed else 0)
