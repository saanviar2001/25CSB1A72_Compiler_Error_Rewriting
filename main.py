#!/usr/bin/env python3

import argparse
import json
import sys
import textwrap

from clang_ast import extract_ast_tree, print_ast_tree, render_ast_png
from dfa_lexer import trace as trace_dfa

RED="\033[91m"
YELLOW="\033[93m"
GREEN="\033[92m"
BLUE="\033[95m"
BOLD="\033[1m"
RESET="\033[0m"


def _is_security_only_diagnostic(error) -> bool:
    message = (getattr(error, "message", "") or "").lower()
    error_type = (getattr(error, "error_type", "") or "").lower()
    return error_type == "warning" and (
        "unused variable" in message
        or "set but not used" in message
        or "declared but never used" in message
    )


def parse_arguments():
    parser=argparse.ArgumentParser(description="C++ Error Explanation Tool")
    parser.add_argument("source_file", nargs="?", help="C++ source file to compile")
    parser.add_argument("--clang-ast", metavar="FILE", help="print a filtered Clang AST and save ast.png")
    parser.add_argument("-j","--json",action="store_true",help="print output in JSON format")
    parser.add_argument("-v","--verbose",action="store_true",help="show detailed context")
    parser.add_argument("-c","--context",type=int, default=2,help="number of context lines")
    parser.add_argument("--dfa-trace", action="store_true", help="print the string/comment DFA trace")
    parser.add_argument("--grammar-check", metavar="FILE", help="check a snippet against the toy grammar")

    return parser.parse_args()


def print_error(error, verbose=False):
    etype=error.error_type.lower()

    if etype=="error":
        color=RED
    elif etype=="warning":
        color=YELLOW
    else:
        color=BLUE

    print(f"\n{color}{BOLD}{etype.upper()}:{error.message}{RESET}")

    if error.file:
        location=f"{error.file}"
        if error.line is not None:
            location+=f" (line {error.line}"
            if error.column is not None:
                location+=f", column {error.column}"
            location+=")"
        print(f"  {BLUE}Location:{RESET} {location}")

    if error.explanation:
        print(f"\n  {BLUE}{BOLD}Explanation:{RESET}")
        for block in error.explanation.split("\n\n"):
            wrapped = textwrap.fill(block, width=80)
            print(f"  {BLUE}{wrapped}{RESET}\n")

    if error.suggestion:
        print(f"\n  {GREEN}{BOLD}Suggestion:{RESET}")
        wrapped=textwrap.fill(error.suggestion,width=80)
        for line in wrapped.split("\n"):
            print(f"  {GREEN}{line}{RESET}")

    if hasattr(error, "security_risk"):
        print(f"\n  {YELLOW}{BOLD}Security Assessment:{RESET}")
        print(f"  {YELLOW}Risk Level: {error.security_risk}{RESET}")

        if error.risk_reason:
            print(f"  {YELLOW}{error.risk_reason}{RESET}")

    if verbose and error.context and "lines" in error.context:
        print(f"\n  {BOLD}Code Context:{RESET}")
        start=error.context["start_line"]

        for i,line in enumerate(error.context["lines"],start=start):
            marker="→" if i==error.line else " "
            print(f"  {marker} {i}: {line.rstrip()}")

    print()

def main():
    args=parse_arguments()
    if args.grammar_check:
        from grammar_parser import GrammarParseError, parse

        try:
            with open(args.grammar_check, encoding="utf-8") as source_file:
                parse(source_file.read())
        except GrammarParseError as error:
            if error.outside_grammar:
                print("outside toy grammar")
                return 0
            print(error)
            return 1
        except OSError as error:
            print(f"Could not read {args.grammar_check}: {error}", file=sys.stderr)
            return 2
        print("valid toy grammar")
        return 0

    if args.clang_ast:
        try:
            tree, is_approximation = extract_ast_tree(args.clang_ast)
            print_ast_tree(tree, "Structural approximation" if is_approximation else None)
            output_path = render_ast_png(tree)
        except (OSError, RuntimeError, ImportError) as error:
            print(f"Could not generate AST: {error}", file=sys.stderr)
            return 2
        print(f"Saved AST image to {output_path}")
        return 0

    if not args.source_file:
        print("error: source_file is required unless --clang-ast is used", file=sys.stderr)
        return 2

    if args.dfa_trace:
        try:
            with open(args.source_file, encoding="utf-8") as source_file:
                trace = trace_dfa(source_file.read())
        except OSError as error:
            print(f"Could not read {args.source_file}: {error}", file=sys.stderr)
            return 2

        print("index | char | state_before | state_after")
        for index, char, state_before, state_after in trace:
            print(f"{index:5} | {char!r:4} | {state_before:23} | {state_after}")
        return 0

    from compiler_runner import run_cpp_compiler
    from error_parser import parse_errors
    from error_explainer import enrich_error
    from security_analyzer import analyze, format_security_report
    from dataset_logger import log_example
    from ast_extractor import extract_ast, extract_node_near_line

    output=run_cpp_compiler(args.source_file)
    if not output:
        security_findings = analyze(args.source_file, [])
        if args.json:
            print(json.dumps({
                "status": "success",
                "file": args.source_file,
                "security_findings": [finding.to_dict() for finding in security_findings],
                "security_report": format_security_report(security_findings),
            }, indent=2))
        else:
            print("Compilation successful")
            print()
            print(format_security_report(security_findings), end="")
        return 0

    errors=parse_errors(output)
    ast_text=extract_ast(args.source_file)

    for e in errors:

        if e.line:
            e.ast_node=extract_node_near_line(ast_text, e.line)
        else:
            e.ast_node=None

        enrich_error(e)
        log_example(e)

    security_findings = analyze(args.source_file, errors)
    for e in errors:
        if not getattr(e, "security_findings", None):
            e.security_findings = [
                finding for finding in security_findings
                if finding.source == "compiler_diagnostic"
                and finding.file == (e.file or "")
                and finding.line == e.line
            ]

    visible_errors = [e for e in errors if not _is_security_only_diagnostic(e)]
    visible_error_count = sum(1 for e in visible_errors if e.error_type == "error")
    visible_warning_count = sum(1 for e in visible_errors if e.error_type == "warning")

    #JSON mode

    if args.json:
        result={
            "status": "error" if visible_errors else "success",
            "file": args.source_file,
            "error_count": visible_error_count,
            "warning_count": visible_warning_count,
            "errors": [e.to_dict() for e in visible_errors],
            "security_findings": [finding.to_dict() for finding in security_findings],
            "security_report": format_security_report(security_findings),
        }

        print(json.dumps(result, indent=2))
        return 1 if visible_error_count > 0 else 0
    
    # Human readable output

    print(f"\n{len(visible_errors)} issue(s) found:\n")

    error_count=0
    warning_count=0

    for e in visible_errors:
        if e.error_type=="error":
            error_count+=1
        elif e.error_type=="warning":
            warning_count+=1

        print_error(e,verbose=args.verbose)

    print(f"{error_count} error(s), {warning_count} warning(s)")
    print()
    print(format_security_report(security_findings), end="")
    return 1 if error_count > 0 else 0

if __name__ == "__main__":
    sys.exit(main())
