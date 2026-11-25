#!/usr/bin/env python3
"""
webapp.py
---------
Interactive Web Dashboard & Agentic Auto-Healing Workbench.
Supports:
  - Multi-tab navigation (Overview, Diagnostics, Auto-Heal, Security, DFA Trace, AST, Grammar, Source)
  - Visual Error Percentage Graph & Segmented Distribution Bars
  - Phase 4 Agentic Auto-Healing loop with Plan->Act->Observe->Replan live progression
  - Numbered, expandable attempt cards with plain-English reasoning and real diffs
  - Interactive DFA trace inspection & AST tree diagram display
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request, send_file


SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from clang_ast import _count_ast_nodes, _trim_tree, extract_ast_tree, render_ast_png
from compiler_runner import run_cpp_compiler
from dfa_lexer import DEFAULT_TRACE_LIMIT, scan_detailed as scan_dfa
from error_explainer import enrich_error
from error_parser import parse_errors
from error_classifier import get_default_classifier
from grammar_parser import GrammarParseError, parse as parse_grammar
from security_analyzer import (
    analyze as analyze_security,
    classify_security_health,
)
from languages import EXTENSION_TO_LANGUAGE, TOOL_STATUS, JavaAdapter, PythonAdapter, all_language_metadata, get_adapter, language_metadata
from languages.registry import JAVA_COMPATIBILITY


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
ALLOWED_SUFFIXES = set(EXTENSION_TO_LANGUAGE)
OPERATION_TIMEOUT_SECONDS = 60
EVALUATION_REPORT = PROJECT_ROOT / "reports" / "evaluation.json"
_evaluation_lock = threading.Lock()
_evaluation_state = {"running": False, "error": None}
STALE_WORKDIR_MAX_AGE_SECONDS = 60 * 60


def _run_classifier_evaluation() -> None:

    try:
        completed = subprocess.run(
            [sys.executable, str(SRC_DIR / "evaluate_classifier.py")],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        error = None
        if completed.returncode:
            error = (completed.stderr or completed.stdout or "Evaluation failed.").strip()[-4000:]
    except OSError as exc:
        error = f"Could not start evaluation: {exc}"
    with _evaluation_lock:
        _evaluation_state["error"] = error
        _evaluation_state["running"] = False


def _compiler_provenance(language: str = "cpp") -> dict[str, object]:
    """Return the actual primary diagnostic tool version detected at startup."""
    adapter = get_adapter(language)
    tool_name = adapter.primary_tools[0] if adapter.primary_tools else "tool"
    detected = TOOL_STATUS.get(tool_name, {"version": "tool not installed"})
    provenance: dict[str, object] = {
        "name": tool_name,
        "version": detected["version"],
        "path": detected.get("path", "path unavailable"),
    }
    if language == "java":
        compatibility = JAVA_COMPATIBILITY
        provenance["compile_target"] = compatibility.get("compile_target")
        provenance["compile_target_message"] = compatibility.get("message")
        provenance["tool_warning"] = compatibility.get("warning")
        provenance["runtime"] = TOOL_STATUS.get("java", {})
    return provenance


def _remove_work_dir(work_dir: str) -> None:
    try:
        shutil.rmtree(work_dir)
    except FileNotFoundError:
        pass
    except OSError:
        app.logger.exception("Could not remove request workspace %s", work_dir)


def _cleanup_stale_workdirs() -> int:
    cutoff = time.time() - STALE_WORKDIR_MAX_AGE_SECONDS
    removed = 0
    for work_dir in Path(tempfile.gettempdir()).glob("compiler-workbench-*"):
        try:
            if work_dir.is_dir() and not work_dir.is_symlink() and work_dir.stat().st_mtime < cutoff:
                shutil.rmtree(work_dir)
                removed += 1
        except OSError:
            app.logger.exception("Could not remove stale compiler workspace %s", work_dir)
    return removed


def _categorization_engine_label(adapter, components: list[dict[str, str]]) -> str:
    """Name the C++ categorizer only when its real pipeline components are loaded."""
    names = {component["name"] for component in components}
    has_tfidf = any(name.startswith("TF-IDF") for name in names)
    if "Logistic Regression" in names and has_tfidf:
        return adapter.categorization_engine
    return "Classifier unavailable"


def _diagnostics_transparency(classifier, decision_sources: list[dict], language: str = "cpp") -> list[dict[str, str]]:
    """Build concise UI entries from the actual loaded pipeline and decisions."""
    adapter = get_adapter(language)
    if not adapter.implemented:
        return [
            {"name": adapter.categorization_engine, "role": f"{adapter.display_name} categorization support coming"},
            {"name": _compiler_provenance(adapter.name)["version"], "role": f"{adapter.primary_tools[0]} toolchain detected at startup"},
        ]
    if adapter.name in {"python", "java"}:
        entries = [
            {"name": adapter.categorization_engine, "role": f"categorizes real {adapter.display_name} diagnostics"},
        ]
        if adapter.name == "java":
            javac_version = TOOL_STATUS.get("javac", {}).get("version", "version unavailable")
            entries.append({"name": javac_version, "role": "javac diagnostic engine version"})
            from importlib.metadata import PackageNotFoundError, version
            try:
                parser_version = version("tree-sitter-java")
            except PackageNotFoundError:
                parser_version = "unavailable"
            entries.append({"name": f"tree-sitter-java {parser_version}", "role": "Java AST parser version"})
        return entries
    entries = list(classifier.transparency_components())
    engine_label = _categorization_engine_label(adapter, entries)
    if engine_label != "Classifier unavailable":
        entries.insert(0, {"name": engine_label, "role": f"{adapter.display_name} error categorization engine"})
    if any(source.get("source") == "rule" for source in decision_sources):
        entries = [entry for entry in entries if entry["name"] != "Logistic Regression"]
        entries.append({
            "name": "Rule-based override",
            "role": "decided this error instead of the ML model",
        })
    entries.append({"name": "Template-based explainer", "role": "writes the what/why/how text"})
    return entries


def _autoheal_transparency(classifier, attempts: list[dict], behavior: dict, language: str = "cpp") -> dict:
    """Build the compact Auto-Heal provenance box for one completed run."""
    adapter = get_adapter(language)
    if not adapter.implemented:
        return {
            "language": adapter.display_name,
            "classifier": adapter.categorization_engine,
            "repair_strategies": ["not implemented yet"],
            "compiler": _compiler_provenance(adapter.name),
            "behavioral_tests": "Did not run",
        }
    classifier_components = classifier.transparency_components()
    strategy_names = []
    for attempt in attempts:
        strategy = attempt.get("strategy")
        if strategy and strategy not in {
            "already_clean", "not_fixable", "compiles_not_behaviorally_verified"
        }:
            if strategy not in strategy_names:
                strategy_names.append(strategy)
    return {
        "classifier": _categorization_engine_label(adapter, classifier_components),
        "repair_strategies": strategy_names or ["No repair strategy applied"],
        "compiler": _compiler_provenance(),
        "behavioral_tests": "Ran" if behavior.get("checked") else "Did not run",
    }


def _combined_code_health(
    security_health: dict, compile_success: bool, errors: list[dict]
) -> dict:
    """Combine the independent security verdict and compiler result."""
    security_verdict = security_health.get("verdict", "HEALTHY")
    if security_verdict == "UNHEALTHY":
        verdict = "UNHEALTHY"
    elif security_verdict == "MODERATE" or not compile_success:
        verdict = "MODERATE"
    else:
        verdict = "HEALTHY"

    rules_by_line: dict[int, set[str]] = {}
    for item in security_health.get("offending_lines", []):
        line = item.get("line")
        if isinstance(line, int):
            rules_by_line.setdefault(line, set()).update(item.get("rules", []))
    for error in errors:
        line = error.get("line")
        if error.get("type") == "ERROR" and isinstance(line, int):
            rules_by_line.setdefault(line, set()).add("Compiler-reported line")
            fault_line = error.get("likely_fault_line")
            if isinstance(fault_line, int):
                rules_by_line.setdefault(fault_line, set()).add(
                    "Likely fault line (previous non-empty line)"
                )

    offending_lines = [
        {"line": line, "rules": sorted(rules)}
        for line, rules in sorted(rules_by_line.items())
    ]
    return {
        "verdict": verdict,
        "security_verdict": security_verdict,
        "compile_success": compile_success,
        "score": security_health.get("score", 0),
        "line_numbers": [item["line"] for item in offending_lines],
        "offending_lines": offending_lines,
    }


def _likely_missing_semicolon_fault_line(source: str, line: int | None, message: str) -> int | None:
    if line is None or not re.search(r"expected\b.*;.*\bbefore\b", message, re.IGNORECASE):
        return None
    lines = source.splitlines()
    for candidate in range(min(line - 1, len(lines)), 0, -1):
        if lines[candidate - 1].strip():
            return candidate
    return None


SAMPLE_FILES = {
    "semicolon": {
        "filename": "test_semicolon.cpp",
        "description": "Missing Semicolon (Syntax Error)",
        "code": """#include <iostream>
using namespace std;

int main() {
    int x = 5
    cout << "Value of x is: " << x << endl;
    return 0;
}
"""
    },
    "include": {
        "filename": "missing_include.cpp",
        "description": "Missing Header & Namespace",
        "code": """int main() {
    cout << "Hello from C++ compiler error workbench!" << endl;
    return 0;
}
"""
    },
    "brace": {
        "filename": "missing_brace.cpp",
        "description": "Unclosed Control Block Brace",
        "code": """#include <iostream>
using namespace std;

int main() {
    int counter = 10;
    if (counter > 5) {
        cout << "Counter is high!" << endl;
    
    return 0;
}
"""
    },
    "condition": {
        "filename": "assignment_condition.cpp",
        "description": "Assignment in Condition [if(x=5)]",
        "code": """#include <iostream>
using namespace std;

int main() {
    int target = 0;
    if (target = 5) {
        cout << "Target reached 5!" << endl;
    }
    return 0;
}
"""
    },
    "security": {
        "filename": "double_free_security.cpp",
        "description": "Redefinition & Double-Free CWE Threat",
        "code": """#include <iostream>
using namespace std;

int computeScore(int base, int bonus) {
    return base + bonus;
}

computeScore(int base, int bonus) {
    return base * bonus;
}

int main() {
    int* buffer = new int(42);
    int score = computeScore(*buffer, 10);
    cout << "Score: " << score << endl;
    
    if (score > 20) {
        delete buffer;
    }
    delete buffer;
    return 0;
}
"""
    },
    "clean": {
        "filename": "clean_program.cpp",
        "description": "Clean Compiling Code",
        "code": """#include <iostream>
using namespace std;

int main() {
    cout << "System operational. All syntactic constraints satisfied." << endl;
    return 0;
}
"""
    }
}


PAGE = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Compiler Intelligence &amp; Agentic Auto-Healing Dashboard</title>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.13/codemirror.min.css">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.13/addon/lint/lint.min.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.13/codemirror.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.13/mode/clike/clike.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.13/addon/lint/lint.min.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-base: #F4F7FB;
      --bg-surface: #FFFFFF;
      --bg-card: rgba(255, 255, 255, 0.92);
      --bg-glass: rgba(255, 255, 255, 0.82);
      --border-subtle: rgba(30, 41, 59, 0.10);
      --border-card: rgba(30, 41, 59, 0.14);
      --border-focus: #6366F1;
      
      --text-main: #172033;
      --text-muted: #475569;
      --text-dim: #64748B;
      
      --primary: #6366F1;
      --primary-hover: #4F46E5;
      --primary-glow: rgba(99, 102, 241, 0.18);
      
      --accent-cyan: #0783A0;
      --accent-emerald: #07805B;
      --accent-rose: #D92D50;
      --accent-amber: #B96A08;
      --accent-purple: #8438B5;
      
      --code-bg: #F8FAFC;
      --code-border: #D9E1EC;
      
      --diff-add-bg: rgba(16, 185, 129, 0.12);
      --diff-add-border: #07805B;
      --diff-add-text: #176B4A;
      --diff-del-bg: rgba(244, 63, 94, 0.10);
      --diff-del-border: #D92D50;
      --diff-del-text: #A51D3D;
      
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 14px;
      --radius-xl: 18px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    body {
      background: var(--bg-base);
      background-image: 
        radial-gradient(circle at 15% 10%, rgba(99, 102, 241, 0.07) 0%, transparent 40%),
        radial-gradient(circle at 85% 20%, rgba(6, 182, 212, 0.06) 0%, transparent 45%),
        radial-gradient(circle at 50% 90%, rgba(168, 85, 247, 0.05) 0%, transparent 50%);
      background-attachment: fixed;
      color: var(--text-main);
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      line-height: 1.55;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    /* ── Horizontal Navigation Bar ── */
    .navbar {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(255, 255, 255, 0.92);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border-bottom: 1px solid var(--border-subtle);
      box-shadow: 0 4px 24px rgba(30, 41, 59, 0.08);
    }
    
    .nav-container {
      max-width: 1360px;
      margin: 0 auto;
      padding: 0 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 64px;
      gap: 16px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      color: var(--text-main);
      font-weight: 700;
      font-size: 16px;
      letter-spacing: -0.01em;
      white-space: nowrap;
    }

    .brand-icon {
      width: 34px;
      height: 34px;
      border-radius: var(--radius-sm);
      background: linear-gradient(135deg, var(--primary), var(--accent-cyan));
      display: flex;
      align-items: center;
      justify-content: center;
      color: white;
      box-shadow: 0 0 16px var(--primary-glow);
    }

    .brand-badge {
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      padding: 2px 7px;
      border-radius: 999px;
      background: rgba(99, 102, 241, 0.09);
      border: 1px solid rgba(99, 102, 241, 0.4);
      color: #4F46E5;
    }

    /* Nav Links Tabs */
    .nav-tabs {
      display: flex;
      align-items: center;
      gap: 4px;
      overflow-x: auto;
      scrollbar-width: none;
      -ms-overflow-style: none;
      padding: 4px 0;
    }
    .nav-tabs::-webkit-scrollbar { display: none; }

    .nav-tab {
      display: flex;
      align-items: center;
      gap: 7px;
      padding: 8px 14px;
      border-radius: var(--radius-sm);
      font-size: 13px;
      font-weight: 500;
      color: var(--text-muted);
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 180ms ease;
      white-space: nowrap;
      background: transparent;
    }

    .nav-tab:hover {
      color: var(--text-main);
      background: rgba(30, 41, 59, 0.04);
    }

    .nav-tab.active {
      color: #4338CA;
      background: rgba(99, 102, 241, 0.09);
      border-color: rgba(99, 102, 241, 0.24);
      box-shadow: 0 0 12px rgba(99, 102, 241, 0.08);
    }

    .nav-tab svg {
      width: 16px;
      height: 16px;
      stroke-width: 2;
    }

    .nav-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    /* Primary & Secondary Buttons */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 9px 18px;
      font-size: 13px;
      font-weight: 600;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all 180ms ease;
      text-decoration: none;
      border: 1px solid transparent;
      white-space: nowrap;
      font-family: inherit;
    }

    .btn:disabled {
      opacity: 0.55;
      cursor: not-allowed;
      pointer-events: none;
    }

    .btn-primary {
      background: linear-gradient(135deg, var(--primary), #4338CA);
      color: white;
      box-shadow: 0 4px 16px var(--primary-glow);
    }
    .btn-primary:hover {
      background: linear-gradient(135deg, #7174F8, #4F46E5);
      transform: translateY(-1px);
      box-shadow: 0 6px 20px rgba(99, 102, 241, 0.45);
    }

    .btn-emerald {
      background: linear-gradient(135deg, #10B981, #047857);
      color: white;
      box-shadow: 0 4px 16px rgba(16, 185, 129, 0.3);
    }
    .btn-emerald:hover {
      background: linear-gradient(135deg, #34D399, #059669);
      transform: translateY(-1px);
      box-shadow: 0 6px 20px rgba(16, 185, 129, 0.45);
    }

    .btn-secondary {
      background: #FFFFFF;
      color: var(--text-main);
      border-color: var(--border-card);
    }
    .btn-secondary:hover {
      background: #F8FAFC;
      border-color: rgba(30, 41, 59, 0.22);
    }

    /* ── Main Container ── */
    .main-wrapper {
      max-width: 1360px;
      margin: 0 auto;
      padding: 28px 20px 80px;
      flex: 1;
      width: 100%;
    }

    /* ── Upload & Header Section ── */
    .top-hero {
      background: var(--bg-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-card);
      border-radius: var(--radius-lg);
      padding: 24px;
      margin-bottom: 24px;
      box-shadow: 0 8px 32px rgba(30, 41, 59, 0.07);
      position: relative;
      overflow: hidden;
    }
    
    .top-hero::before {
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0; height: 3px;
      background: linear-gradient(90deg, var(--primary), var(--accent-cyan), var(--accent-purple));
    }

    .hero-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
      align-items: center;
    }
    @media (max-width: 900px) {
      .hero-grid { grid-template-columns: 1fr; }
    }

    .hero-title-area h1 {
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.02em;
      line-height: 1.25;
      margin-bottom: 8px;
    }
    .hero-title-area h1 span {
      background: linear-gradient(90deg, #4F46E5, #0783A0);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .hero-subtitle {
      color: var(--text-muted);
      font-size: 14px;
      margin-bottom: 16px;
    }

    .sample-pill-group {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }
    .sample-label {
      font-size: 11px;
      text-transform: uppercase;
      font-weight: 700;
      color: var(--text-dim);
      letter-spacing: 0.05em;
    }
    .sample-btn {
      padding: 4px 10px;
      border-radius: var(--radius-sm);
      background: #FFFFFF;
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      transition: all 140ms ease;
    }
    .sample-btn:hover {
      background: rgba(99, 102, 241, 0.15);
      border-color: rgba(99, 102, 241, 0.4);
      color: #4338CA;
    }

    /* Upload File / Paste Code mode toggle + paste panel */
    .input-mode-toggle {
      display: flex;
      gap: 6px;
      margin-bottom: 12px;
    }
    .input-mode-btn {
      flex: 1;
      padding: 8px 12px;
      border-radius: var(--radius-sm);
      background: #FFFFFF;
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 140ms ease;
    }
    .input-mode-btn:hover {
      color: #4338CA;
      border-color: rgba(99, 102, 241, 0.4);
    }
    .input-mode-btn.active {
      background: rgba(99, 102, 241, 0.08);
      border-color: var(--primary);
      color: #4338CA;
    }
    .paste-panel {
      display: none;
      flex-direction: column;
      gap: 10px;
    }
    .paste-textarea {
      width: 100%;
      min-height: 300px;
      resize: vertical;
      background: var(--code-bg);
      border: 1px solid var(--code-border);
      border-radius: var(--radius-sm);
      padding: 12px;
      color: var(--text-main);
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      line-height: 1.6;
      tab-size: 4;
    }
    .paste-textarea:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
    }
    .paste-filename-row {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .paste-filename-label {
      font-size: 12px;
      color: var(--text-muted);
      font-weight: 600;
      white-space: nowrap;
    }
    .paste-filename {
      flex: 1;
      background: #FFFFFF;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 8px 12px;
      color: var(--text-main);
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
    }
    .paste-filename:focus {
      outline: none;
      border-color: var(--primary);
    }

    /* Drag & Drop Zone */
    .dropzone {
      border: 2px dashed rgba(30, 41, 59, 0.20);
      border-radius: var(--radius-md);
      padding: 22px;
      background: rgba(255, 255, 255, 0.72);
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 10px;
      text-align: center;
      cursor: pointer;
      transition: all 180ms ease;
      position: relative;
    }
    .dropzone:hover, .dropzone.dragover {
      border-color: var(--primary);
      background: rgba(99, 102, 241, 0.08);
      box-shadow: 0 0 20px rgba(99, 102, 241, 0.2);
    }
    .dropzone-icon {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: rgba(99, 102, 241, 0.15);
      color: #4F46E5;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .dropzone-text {
      font-size: 13px;
      font-weight: 600;
      color: var(--text-main);
    }
    .source-line {
      display: block;
      white-space: pre;
      border-left: 3px solid transparent;
      padding-left: 6px;
    }
    .source-line.flagged {
      background: rgba(245, 158, 11, 0.12);
      border-left-color: var(--accent-amber);
    }
    .source-finding-label {
      margin-left: 12px;
      color: #995A08;
      font-size: 11px;
      white-space: normal;
    }
    .dropzone-sub {
      font-size: 11px;
      color: var(--text-dim);
    }
    .file-badge {
      display: none;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      background: rgba(6, 182, 212, 0.15);
      border: 1px solid rgba(6, 182, 212, 0.35);
      border-radius: var(--radius-sm);
      color: #08718A;
      font-size: 12px;
      font-family: 'JetBrains Mono', monospace;
    }

    /* ── Graph of Error Percentage & Health Metrics (NEW) ── */
    .analytics-banner {
      background: var(--bg-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-card);
      border-radius: var(--radius-lg);
      padding: 22px 24px;
      margin-bottom: 24px;
      box-shadow: 0 8px 32px rgba(30, 41, 59, 0.07);
    }

    .analytics-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
      flex-wrap: wrap;
      gap: 12px;
    }

    .analytics-title {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 16px;
      font-weight: 700;
    }
    .analytics-title svg {
      color: var(--accent-cyan);
    }

    .metrics-summary-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 14px;
      margin-bottom: 20px;
    }

    .metric-card {
      background: #FFFFFF;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      gap: 4px;
      position: relative;
      overflow: hidden;
      transition: transform 140ms ease;
    }
    .metric-card:hover {
      transform: translateY(-2px);
      border-color: var(--border-card);
    }
    .metric-card::before {
      content: '';
      position: absolute;
      top: 0; left: 0; bottom: 0; width: 3px;
    }
    .metric-card.errors::before { background: var(--accent-rose); }
    .metric-card.warnings::before { background: var(--accent-amber); }
    .metric-card.security::before { background: var(--accent-purple); }
    .metric-card.health::before { background: var(--accent-emerald); }

    .metric-label {
      font-size: 11px;
      text-transform: uppercase;
      font-weight: 700;
      color: var(--text-dim);
      letter-spacing: 0.05em;
    }
    .metric-value {
      font-size: 24px;
      font-weight: 800;
      letter-spacing: -0.02em;
      font-family: 'JetBrains Mono', monospace;
    }
    .metric-desc {
      font-size: 11px;
      color: var(--text-muted);
    }

    /* Segmented Multi-Color Error Distribution Bar */
    .error-graph-container {
      background: #F8FAFC;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 16px;
    }

    .bar-header {
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 8px;
    }

    .segmented-bar {
      height: 22px;
      border-radius: 999px;
      overflow: hidden;
      display: flex;
      background: #E8EDF4;
      box-shadow: inset 0 1px 3px rgba(30, 41, 59, 0.10);
      position: relative;
    }

    .bar-segment {
      height: 100%;
      transition: width 400ms cubic-bezier(0.4, 0, 0.2, 1);
      position: relative;
      cursor: pointer;
    }
    .bar-segment:hover {
      filter: brightness(1.2);
    }

    /* Category breakdown chips */
    .category-chips-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }

    .category-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 5px 12px;
      border-radius: var(--radius-sm);
      background: #F8FAFC;
      border: 1px solid var(--border-subtle);
      font-size: 12px;
    }
    .chip-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
    }
    .chip-name {
      font-weight: 600;
      color: var(--text-main);
    }
    .chip-pct {
      font-family: 'JetBrains Mono', monospace;
      color: var(--text-muted);
      font-size: 11px;
    }

    /* ── Content Panes & Tabs ── */
    .tab-pane {
      display: none;
      animation: fadeIn 220ms ease forwards;
    }
    .tab-pane.active {
      display: block;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .card {
      background: var(--bg-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-card);
      border-radius: var(--radius-lg);
      padding: 24px;
      margin-bottom: 20px;
      box-shadow: 0 6px 24px rgba(30, 41, 59, 0.06);
    }

    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 18px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--border-subtle);
    }
    .card-title {
      font-size: 18px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 10px;
    }

    /* ── Diagnostic Error Card ── */
    .error-item {
      background: #FFFFFF;
      border: 1px solid var(--border-subtle);
      border-left: 4px solid var(--accent-rose);
      border-radius: var(--radius-md);
      padding: 18px;
      margin-bottom: 16px;
      transition: all 160ms ease;
    }
    .error-item.warning {
      border-left-color: var(--accent-amber);
    }
    .error-item:hover {
      border-color: var(--border-card);
      transform: translateX(2px);
    }

    .error-header-line {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 9px;
      border-radius: var(--radius-sm);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }
    .badge-rose {
      background: rgba(244, 63, 94, 0.15);
      border: 1px solid rgba(244, 63, 94, 0.4);
      color: #B42343;
    }
    .badge-amber {
      background: rgba(245, 158, 11, 0.15);
      border: 1px solid rgba(245, 158, 11, 0.4);
      color: #995A08;
    }
    .badge-emerald {
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid rgba(16, 185, 129, 0.4);
      color: #08734F;
    }
    .badge-cyan {
      background: rgba(6, 182, 212, 0.15);
      border: 1px solid rgba(6, 182, 212, 0.4);
      color: #08718A;
    }
    .badge-purple {
      background: rgba(168, 85, 247, 0.15);
      border: 1px solid rgba(168, 85, 247, 0.4);
      color: #7531A3;
    }
    .badge-indigo {
      background: rgba(99, 102, 241, 0.15);
      border: 1px solid rgba(99, 102, 241, 0.4);
      color: #4338CA;
    }

    .error-code-raw {
      background: var(--code-bg);
      border: 1px solid var(--code-border);
      border-radius: var(--radius-sm);
      padding: 12px 14px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      color: #B42343;
      overflow-x: auto;
      margin-bottom: 12px;
      white-space: pre-wrap;
    }

    .nlp-explanation-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
      background: #F8FAFC;
      border-radius: var(--radius-sm);
      padding: 12px;
      border: 1px solid var(--border-subtle);
    }
    .nlp-row {
      display: flex;
      flex-direction: column;
      gap: 3px;
    }
    .nlp-label {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      color: var(--accent-cyan);
      letter-spacing: 0.05em;
    }
    .nlp-body {
      font-size: 13px;
      color: var(--text-main);
    }

    /* ── Phase 4 Agentic Auto-Healer UI ── */
    .healer-console-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
      margin-bottom: 20px;
    }

    /* Final Verdict Card */
    .verdict-banner {
      border-radius: var(--radius-md);
      padding: 20px;
      margin-bottom: 22px;
      display: flex;
      align-items: flex-start;
      gap: 16px;
      box-shadow: 0 4px 20px rgba(30, 41, 59, 0.08);
      position: relative;
      overflow: hidden;
    }
    .verdict-banner.verified {
      background: linear-gradient(135deg, rgba(16, 185, 129, 0.15), rgba(5, 150, 105, 0.1));
      border: 1px solid rgba(16, 185, 129, 0.4);
    }
    .verdict-banner.unverified {
      background: linear-gradient(135deg, rgba(99, 102, 241, 0.15), rgba(6, 182, 212, 0.1));
      border: 1px solid rgba(99, 102, 241, 0.4);
    }
    .verdict-banner.not_fixable {
      background: linear-gradient(135deg, rgba(244, 63, 94, 0.15), rgba(245, 158, 11, 0.1));
      border: 1px solid rgba(244, 63, 94, 0.4);
    }
    .verdict-banner.clean {
      background: linear-gradient(135deg, rgba(6, 182, 212, 0.15), rgba(16, 185, 129, 0.1));
      border: 1px solid rgba(6, 182, 212, 0.4);
    }

    .verdict-icon {
      width: 44px;
      height: 44px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .verdict-banner.verified .verdict-icon { background: rgba(16, 185, 129, 0.12); color: #08734F; }
    .verdict-banner.unverified .verdict-icon { background: rgba(99, 102, 241, 0.10); color: #4338CA; }
    .verdict-banner.not_fixable .verdict-icon { background: rgba(244, 63, 94, 0.10); color: #B42343; }
    .verdict-banner.clean .verdict-icon { background: rgba(6, 182, 212, 0.10); color: #08718A; }

    .verdict-content h3 {
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 4px;
    }
    .verdict-content p {
      font-size: 13px;
      color: var(--text-muted);
    }
    .verdict-note {
      margin-top: 8px;
      font-size: 12px;
      font-style: italic;
      color: var(--text-dim);
    }

    /* Attempt Timeline Cards */
    .attempt-card {
      background: #FFFFFF;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      margin-bottom: 16px;
      overflow: hidden;
      transition: all 180ms ease;
    }
    .attempt-card:hover {
      border-color: rgba(99, 102, 241, 0.35);
    }

    .attempt-summary {
      padding: 14px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      cursor: pointer;
      user-select: none;
      background: #F8FAFC;
      border-bottom: 1px solid transparent;
      transition: background 140ms ease;
    }
    .attempt-summary:hover {
      background: #F1F5F9;
    }
    .attempt-card.open .attempt-summary {
      border-bottom-color: var(--border-subtle);
      background: #F1F5F9;
    }

    .attempt-left {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .attempt-num-badge {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: var(--primary);
      color: white;
      font-weight: 700;
      font-size: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .attempt-reason-title {
      font-weight: 600;
      font-size: 14px;
      color: var(--text-main);
    }

    .attempt-body {
      padding: 18px;
      display: none;
    }
    .attempt-card.open .attempt-body {
      display: block;
    }

    /* Loop Stage Badges (Observe, Plan, Act, Replan) */
    .loop-cycle-steps {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .loop-step-box {
      background: #F8FAFC;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 10px 12px;
    }
    .loop-step-name {
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 4px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .loop-step-name.observe { color: var(--accent-cyan); }
    .loop-step-name.plan { color: var(--accent-purple); }
    .loop-step-name.act { color: var(--accent-amber); }
    .loop-step-name.outcome { color: var(--accent-emerald); }
    .loop-step-text {
      font-size: 12px;
      color: var(--text-muted);
    }

    /* Before vs After Diff Split */
    .diff-split-container {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
      margin-top: 14px;
    }
    @media (max-width: 800px) {
      .diff-split-container { grid-template-columns: 1fr; }
    }
    .diff-pane-title {
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-dim);
      margin-bottom: 6px;
      display: flex;
      justify-content: space-between;
    }

    pre.code-view {
      background: var(--code-bg);
      border: 1px solid var(--code-border);
      border-radius: var(--radius-sm);
      padding: 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      line-height: 1.6;
      overflow-x: auto;
      max-height: 380px;
      color: var(--text-main);
    }

    .diff-html-box {
      background: var(--code-bg);
      border: 1px solid var(--code-border);
      border-radius: var(--radius-sm);
      padding: 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      line-height: 1.6;
      overflow-x: auto;
      max-height: 380px;
    }
    .diff-html-box ins, .diff-html-box .diff-add {
      background: var(--diff-add-bg);
      color: var(--diff-add-text);
      text-decoration: none;
      display: block;
      border-left: 3px solid var(--diff-add-border);
      padding-left: 6px;
    }
    .diff-html-box del, .diff-html-box .diff-del {
      background: var(--diff-del-bg);
      color: var(--diff-del-text);
      text-decoration: line-through;
      display: block;
      border-left: 3px solid var(--diff-del-border);
      padding-left: 6px;
    }

    /* ── DFA Lexer Trace Table ── */
    .table-container {
      background: #FFFFFF;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      overflow: auto;
      max-height: 520px;
    }

    table.styled-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      text-align: left;
    }
    table.styled-table th {
      position: sticky;
      top: 0;
      background: #F1F5F9;
      color: var(--text-muted);
      font-weight: 700;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border-card);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      z-index: 10;
    }
    table.styled-table td {
      padding: 8px 14px;
      border-bottom: 1px solid var(--border-subtle);
      color: var(--text-main);
    }
    table.styled-table tr:hover td {
      background: #F8FAFC;
    }
    .table-char {
      font-family: 'JetBrains Mono', monospace;
      color: #08718A;
      font-weight: 700;
      background: rgba(6, 182, 212, 0.1);
      padding: 2px 6px;
      border-radius: 4px;
    }
    .table-state {
      font-family: 'JetBrains Mono', monospace;
      color: #4338CA;
      font-size: 12px;
    }
    .dfa-badge {
      display: inline-block;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      font-weight: 600;
      padding: 3px 8px;
      border-radius: 6px;
    }
    .dfa-state-code {
      background: #F1F5F9;
      color: #475569;
      border: 1px solid #CBD5E1;
    }
    .dfa-state-comment {
      background: #ECFDF5;
      color: #059669;
      border: 1px solid #A7F3D0;
    }

    /* ── AST Diagram ── */
    .ast-display-area {
      background: #FFFFFF;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 20px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 350px;
      position: relative;
    }
    .ast-image {
      max-width: 100%;
      height: auto;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-subtle);
      background: #FFFFFF;
      padding: 10px;
      box-shadow: 0 4px 20px rgba(30, 41, 59, 0.10);
    }

    /* ── Security Analysis Card ── */
    .security-finding-item {
      background: #FFFFFF;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 16px;
      margin-bottom: 14px;
    }
    .security-finding-item.critical { border-left: 4px solid var(--accent-rose); }
    .security-finding-item.high { border-left: 4px solid #F97316; }
    .security-finding-item.medium { border-left: 4px solid var(--accent-amber); }
    .security-finding-item.low { border-left: 4px solid var(--accent-cyan); }

    /* Footer */
    footer {
      border-top: 1px solid var(--border-subtle);
      padding: 20px;
      text-align: center;
      font-size: 12px;
      color: var(--text-dim);
      margin-top: auto;
    }

    /* Loading Spinner */
    .spinner {
      width: 18px;
      height: 18px;
      border: 2px solid rgba(30, 41, 59, 0.16);
      border-top-color: var(--primary);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      display: inline-block;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    .empty-state {
      padding: 48px 20px;
      text-align: center;
      color: var(--text-dim);
      font-size: 14px;
    }
    .empty-state svg {
      width: 48px;
      height: 48px;
      margin-bottom: 12px;
      color: var(--text-dim);
    }
  </style>
</head>
<body>

  <!-- ── Top Horizontal Navigation Bar ── -->
  <nav class="navbar">
    <div class="nav-container">
      <div class="brand">
        <div class="brand-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
          </svg>
        </div>
        <span>C++ Intelligence &amp; Healer</span>
        <span class="brand-badge">Phase 4 Agent</span>
      </div>

      <!-- Navigation Bar Tabs -->
      <div class="nav-tabs" role="tablist">
        <button class="nav-tab active" data-tab="tab-overview" role="tab">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
          Overview &amp; Metrics
        </button>
        <button class="nav-tab" data-tab="tab-diagnostics" role="tab">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
          Diagnostics &amp; NLP
        </button>
        <button class="nav-tab" data-tab="tab-evaluation" role="tab">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 3v18h18"></path><path d="m19 9-5 5-4-4-5 5"></path></svg>
          Model Evaluation
        </button>
        <button class="nav-tab" data-tab="tab-autoheal" role="tab">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 2v20M2 12h20"></path></svg>
          Agentic Auto-Heal
        </button>
        <button class="nav-tab" data-tab="tab-security" role="tab">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
          Security Audit
        </button>
        <button class="nav-tab" data-tab="tab-dfa" role="tab">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>
          DFA Trace
        </button>
        <button class="nav-tab" data-tab="tab-ast" role="tab">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="5" r="3"></circle><circle cx="6" cy="19" r="3"></circle><circle cx="18" cy="19" r="3"></circle><path d="M12 8v4M8.5 16.5L10 12M15.5 16.5L14 12"></path></svg>
          AST Diagram
        </button>
        <button class="nav-tab" data-tab="tab-grammar" role="tab">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>
          Grammar Check
        </button>
        <button class="nav-tab" data-tab="tab-source" role="tab">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>
          Source Code
        </button>
      </div>

      <!-- Quick Actions -->
      <div class="nav-actions">
        <button class="btn btn-emerald" id="btn-quick-heal" title="Attempt Auto-Heal with Agent Loop">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
          <span>Auto-Heal</span>
        </button>
      </div>
    </div>
  </nav>

  <!-- ── Main Workspace ── -->
  <main class="main-wrapper">

    <!-- Top Hero / Upload Card -->
    <div class="top-hero">
      <div class="hero-grid">
        <div class="hero-title-area">
          <h1>C++ Compiler Diagnostics &amp; <span>Self-Healing Engine</span></h1>
          <p class="hero-subtitle">
            Combines g++ compiler diagnostics, AST tree visualization, DFA lexer state tracking, NLP explanations, and an agentic OBSERVE &rarr; PLAN &rarr; ACT &rarr; REPLAN healing loop.
          </p>
          <div class="sample-pill-group">
            <span class="sample-label">Quick Samples:</span>
            <button class="sample-btn" data-sample="semicolon">Missing Semicolon</button>
            <button class="sample-btn" data-sample="include">Missing Header</button>
            <button class="sample-btn" data-sample="brace">Missing Brace</button>
            <button class="sample-btn" data-sample="condition">Assignment if(x=5)</button>
            <button class="sample-btn" data-sample="security">Double-Free Threat</button>
            <button class="sample-btn" data-sample="clean">Clean Code</button>
          </div>
        </div>

        <form id="upload-form">
          <div style="margin:0 0 12px;">
            <label for="language-selector" style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:6px;">Language</label>
            <select id="language-selector" aria-label="Programming language" style="width:100%; padding:9px 10px; border:1px solid var(--border-subtle); border-radius:8px; color:var(--text-main); background:#fff;">
              <option value="cpp">C++</option>
              <option value="python">Python</option>
              <option value="java">Java</option>
              <option value="c">C — support coming</option>
              <option value="javascript">JavaScript — support coming</option>
            </select>
            <div id="language-tool-status" style="font-size:11px; color:var(--text-dim); margin-top:5px;"></div>
          </div>
          <!-- Input mode toggle: Upload File (existing) vs Paste Code (new) -->
          <div class="input-mode-toggle" role="tablist" aria-label="Source input mode">
            <button type="button" class="input-mode-btn active" id="mode-btn-upload" data-mode="upload">Upload File</button>
            <button type="button" class="input-mode-btn" id="mode-btn-paste" data-mode="paste">Paste Code</button>
          </div>

          <div id="upload-panel">
            <label class="dropzone" id="drop-area" for="file-input">
              <div class="dropzone-icon">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
              </div>
              <div class="dropzone-text">Click to browse or drop your source file</div>
              <div class="dropzone-sub">Supports .cpp, .cc, .cxx, .py, .java, .c, .js (Up to 4 MB)</div>
              <div class="file-badge" id="file-badge">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path></svg>
                <span id="file-name-text">file.cpp</span>
              </div>
            </label>
            <input type="file" id="file-input" accept=".cpp,.cc,.cxx,.py,.java,.c,.js" style="display:none">
          </div>

          <div class="paste-panel" id="paste-panel">
            <textarea class="paste-textarea" id="paste-textarea" rows="18" spellcheck="false"
                      style="display:none;"></textarea>
            <div id="code-editor-container" style="border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); overflow: hidden; height: 400px;"></div>
            <div class="paste-filename-row">
              <span class="paste-filename-label">Filename</span>
              <input type="text" class="paste-filename" id="paste-filename"
                     value="pasted_code.cpp" placeholder="pasted_code.cpp">
            </div>
          </div>
          <div style="margin-top:12px;">
            <label style="display:flex; align-items:flex-start; gap:8px; font-size:12px; color:var(--text-main);">
              <input type="checkbox" id="execute-runtime-checkbox" style="margin-top:2px;">
              <span>Run my code to detect runtime errors (executes the file)</span>
            </label>
            <div id="runtime-check-status" style="font-size:11px; color:var(--text-dim); margin:5px 0 0 22px;">
              Runtime check skipped. Behavioral tests also stay off.
            </div>
            <div style="font-size:11px; color:var(--text-dim); margin:4px 0 0 22px;">
              High or Critical security findings always block execution.
            </div>
          </div>

          <div style="margin-top:12px;">
            <label for="test-spec-input" style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:6px;">
              Behavioral test spec (optional: <code>&lt;source&gt;.tests.json</code>)
            </label>
            <input type="file" id="test-spec-input" accept=".json,application/json"
                   style="font-size:12px; color:var(--text-muted);">
            <div id="test-spec-note" style="font-size:11px; color:var(--text-dim); margin-top:5px;">
              JSON cases use <code>stdin</code> and <code>expected_stdout</code>; tests run only after opt-in and have a 5-second limit.
            </div>
          </div>

          <div style="display: flex; gap: 10px; margin-top: 12px; justify-content: flex-end;">
            <button type="submit" class="btn btn-primary" id="btn-analyze">
              <span id="analyze-spinner" class="spinner" style="display:none"></span>
              <span>Run Full Analysis</span>
            </button>
          </div>
        </form>
      </div>
    </div>

    <!-- ── Error Percentage Graph & Visual Analytics Card ── -->
    <div class="analytics-banner" id="analytics-section" style="display: none;">
      <div class="analytics-head">
        <div class="analytics-title">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
          Error Distribution &amp; Code Quality Metrics
        </div>
        <div id="file-quick-stats" style="font-size: 13px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;">
          <!-- Loaded file info -->
        </div>
      </div>

      <!-- Quick Metric Counters -->
      <div class="metrics-summary-row">
        <div class="metric-card errors">
          <span class="metric-label">Hard Errors</span>
          <span class="metric-value" id="val-hard-errors" style="color: var(--accent-rose);">0</span>
          <span class="metric-desc" id="desc-hard-errors">0% of total compiler diagnostics</span>
        </div>
        <div class="metric-card warnings">
          <span class="metric-label">Warnings</span>
          <span class="metric-value" id="val-warnings" style="color: var(--accent-amber);">0</span>
          <span class="metric-desc" id="desc-warnings">0% of total compiler diagnostics</span>
        </div>
        <div class="metric-card security">
          <span class="metric-label">Security Threats</span>
          <span class="metric-value" id="val-security" style="color: var(--accent-purple);">0</span>
          <span class="metric-desc" id="desc-security">Risk Level: None</span>
        </div>
        <div class="metric-card health">
          <span class="metric-label">Code Health Score</span>
          <span class="metric-value" id="val-health" style="color: var(--accent-emerald);">100%</span>
          <span class="metric-desc">Syntax &amp; Safety index</span>
        </div>
      </div>

      <!-- Segmented Error Category Breakdown Bar -->
      <div class="error-graph-container">
        <div class="bar-header">
          <span>Error Category Composition (%)</span>
          <span id="bar-total-label">0 Total Issues Identified</span>
        </div>
        <div class="segmented-bar" id="error-segmented-bar">
          <!-- Filled dynamically via JS -->
          <div class="bar-segment" style="width: 100%; background: #10B981;" title="Clean"></div>
        </div>
        <div class="category-chips-row" id="category-chips-row">
          <!-- Category breakdown chips -->
        </div>
      </div>
    </div>

    <!-- ── TAB PANES ── -->

    <!-- TAB 1: Overview & Summary -->
    <div class="tab-pane active" id="tab-overview">
      <div class="card">
        <div class="card-header">
          <div class="card-title">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
            System Pipeline Overview
          </div>
          <span class="badge badge-indigo" id="overview-compile-status">Awaiting Input</span>
        </div>
        <p style="color: var(--text-muted); font-size: 14px; margin-bottom: 20px;">
          Upload a C++ file above or choose a sample to inspect compiler diagnostics, generate abstract syntax trees, monitor DFA lexer states, and trigger the agentic auto-healer.
        </p>
        <div id="overview-summary-content">
          <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
            <p>Upload a file or choose one of the quick samples above to begin analysis.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 2: Compiler Diagnostics & NLP Explanations -->
    <div class="tab-pane" id="tab-diagnostics">
      <div class="card">
        <div class="card-header">
          <div class="card-title">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            Compiler Diagnostics &amp; Plain-English Explanations
          </div>
          <span class="badge badge-rose" id="diag-count-badge">0 Issues</span>
        </div>
        <div id="nlp-models-used" class="card" style="display:none; margin:0 0 14px; padding:12px;"></div>
        <div id="diagnostics-content">
          <div class="empty-state">
            <p>No diagnostics available. Upload or analyze a C++ file.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Classifier evaluation metrics -->
    <div class="tab-pane" id="tab-evaluation">
          <div class="charts-grid">
            <figure>
              <figcaption>Confusion matrix chart</figcaption>
              <img src="/reports/confusion_matrix.png" alt="Confusion matrix">
            </figure>
            <figure>
              <figcaption>Per-class F1 chart</figcaption>
              <img src="/reports/per_class_f1.png" alt="Per-class F1">
            </figure>
          </div>
            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px dashed #ccc;">
              <h3 style="font-size: 18px; font-weight: bold; margin-bottom: 10px; color: #333;">Machine Learning Model Benchmarking</h3>
              <p style="color: #666; margin-bottom: 20px;">
                  To justify the architectural choice of using TF-IDF + Logistic Regression, four different models were evaluated on the same dataset. The grouped bar chart below compares their Accuracy and Weighted F1-Scores.
              </p>
              <img src="/reports/model_comparison.png" alt="Model Comparison" style="width: 100%; max-width: 800px; border: 1px solid #ddd; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
          </div>
      <div class="card">
        <div class="card-header">
          <div class="card-title">Model Evaluation</div>
          <button class="btn btn-secondary" id="btn-run-evaluation">Run evaluation</button>
        </div>
        <div id="evaluation-content" aria-live="polite">
          <p style="color:var(--text-dim);">Loading saved classifier evaluation...</p>
        </div>
      </div>
    </div>

    <!-- TAB 3: Agentic Auto-Heal (Phase 4 Display) -->
    <div class="tab-pane" id="tab-autoheal">
      <div class="card">
        <div class="healer-console-header">
          <div class="card-title">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
            Agentic Auto-Healer (OBSERVE &rarr; PLAN &rarr; ACT &rarr; REPLAN)
          </div>
          <button class="btn btn-emerald" id="btn-run-heal">
            <span id="heal-spinner" class="spinner" style="display:none"></span>
            <span>Execute Agentic Auto-Fix</span>
          </button>
        </div>

        <div id="healer-results-area">
          <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
            <p>Click "Execute Agentic Auto-Fix" to run the 4-phase reasoning repair cycle.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 4: Security Analysis -->
    <div class="tab-pane" id="tab-security">
            <div class="card" style="margin-bottom: 20px;">
            <div class="card-header">
              <div class="card-title">Security Rule Coverage &amp; Severity Distribution</div>
            </div>
            <p style="color: var(--text-muted); margin-bottom: 15px; font-size: 14px;">
                The SAST (Static Application Security Testing) engine implements over 55 distinct rules mapped to CWE categories. 
                The charts below illustrate the distribution of these rules across vulnerability types and their assigned severity levels.
            </p>
            <img src="/reports/security_threat_graph.png" alt="Security Threat Distribution" style="width: 100%; height: auto; border-radius: 8px; border: 1px solid var(--border-subtle);">
        </div>
      <div class="card">
        <div class="card-header">
          <div class="card-title">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
            Security Audit &amp; CWE Vulnerabilities
          </div>
          <span class="badge badge-emerald" id="security-risk-badge">HEALTHY · Score 0</span>
        </div>
        <div id="security-content">
          <div class="empty-state">
            <p>Run analysis to inspect potential security vulnerabilities and unsafe constructs.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 5: DFA Lexer Trace -->
    <div class="tab-pane" id="tab-dfa">
      <div class="card">
        <div class="card-header">
          <div class="card-title">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>
            Deterministic Finite Automaton (DFA) Lexer Trace
          </div>
          <input type="text" id="dfa-filter-input" placeholder="Filter character or state..." style="background:#FFFFFF; border:1px solid var(--border-card); color:var(--text-main); padding:6px 12px; border-radius:6px; font-size:12px; font-family:'JetBrains Mono',monospace;">
        </div>
        <p style="color:var(--text-muted); font-size:13px; margin-bottom:14px;">
          Tracks character-by-character state transitions across tokens, comments, and identifiers.
        </p>
        <p id="dfa-status" role="status" aria-live="polite" style="font-size:12px; color:var(--text-dim); margin-bottom:12px;">
          Run Analyze to generate a DFA trace.
        </p>
        <div class="table-container">
          <table class="styled-table" id="dfa-table">
            <thead>
              <tr>
                <th style="width: 70px;">Index</th>
                <th style="width: 90px;">Location</th>
                <th style="width: 100px;">Character</th>
                <th>State Before</th>
                <th>Token</th>
                <th>State After</th>
                <th>Transition</th>
              </tr>
            </thead>
            <tbody id="dfa-table-body">
              <tr><td colspan="7" style="text-align:center; color:var(--text-dim);">Run Analyze to generate a trace.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 6: Clang AST Diagram -->
    <div class="tab-pane" id="tab-ast">
      <div class="card">
        <div class="card-header">
          <div class="card-title">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="3"></circle><circle cx="6" cy="19" r="3"></circle><circle cx="18" cy="19" r="3"></circle><path d="M12 8v4M8.5 16.5L10 12M15.5 16.5L14 12"></path></svg>
            Abstract Syntax Tree (AST) Visualization
          </div>
          <span class="badge badge-cyan" id="ast-engine-badge">Clang Graphviz</span>
        </div>
        <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:12px;">
          <label style="font-size:12px; color:var(--text-dim);">Node limit
            <input id="ast-node-limit" type="number" min="20" max="500" value="150" style="width:72px; margin-left:6px; background:#fff; border:1px solid var(--border-card); color:var(--text-main); border-radius:6px; padding:5px 8px;">
          </label>
          <label style="font-size:12px; color:var(--text-dim);">Depth limit
            <input id="ast-depth-limit" type="number" min="4" max="20" value="12" style="width:64px; margin-left:6px; background:#fff; border:1px solid var(--border-card); color:var(--text-main); border-radius:6px; padding:5px 8px;">
          </label>
          <button class="btn btn-secondary" id="btn-ast-expand-full" type="button">Expand full tree</button>
        </div>
        <div class="ast-display-area" id="ast-container">
          <p style="color:var(--text-dim);">AST diagram will appear here after analysis.</p>
        </div>
      </div>
    </div>

    <!-- TAB 7: Grammar Check -->
    <div class="tab-pane" id="tab-grammar">
      <div class="card">
        <div class="card-header">
          <div class="card-title">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
            Toy Formal Grammar Parser Validation
          </div>
          <span class="badge badge-emerald" id="grammar-status-badge">PASS</span>
        </div>
        <div id="grammar-content" style="font-size:14px; color:var(--text-main);">
          <p style="color:var(--text-dim);">No grammar check executed yet.</p>
        </div>
      </div>
    </div>

    <!-- TAB 8: Source Code View -->
    <div class="tab-pane" id="tab-source">
      <div class="card">
        <div class="card-header">
          <div class="card-title">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>
            Uploaded C++ Source
          </div>
          <button class="btn btn-secondary" id="btn-copy-source" style="padding: 4px 10px; font-size:11px;">Copy Source</button>
        </div>
        <div style="font-size:12px; color:var(--text-muted); margin-bottom:10px;">
          <span style="display:inline-block;width:12px;height:12px;background:rgba(245,158,11,.18);border-left:3px solid #995A08;vertical-align:-2px;margin-right:5px;"></span>
          Flagged line; hover the rule label for the explanation and fix hint.
        </div>
        <pre class="code-view" id="source-code-view">// Source code will be displayed here.</pre>
      </div>
    </div>

  </main>

  <!-- ── Footer ── -->
  <footer>
   
    C++ Compiler Error Explanation &amp; Agentic Auto-Healing Engine &bull; Educational Engineering Project &bull; AST, NLP &amp; DFA Lexer Integration

  </footer>

  <script>
    // ── Global State ──
    let currentSource = "";
    let currentFilename = "file.cpp";
    let hasLoadedSource = false;
    let lastAnalysisData = null;
    let fullDfaTrace = [];
    let fullDfaTraceMetadata = null;
    let activeAnalysisRunId = 0;
    let activeHealRunId = 0;
    let activeHealAbortController = null;

    const CATEGORY_COLORS = {
      "syntax_error": "#F43F5E",
      "missing_semicolon": "#F43F5E",
      "name_resolution": "#06B6D4",
      "missing_include": "#3B82F6",
      "type_error": "#A855F7",
      "redefinition": "#EC4899",
      "division_by_zero": "#EF4444",
      "integer_overflow": "#F97316",
      "unused_variable": "#F59E0B",
      "warning": "#F59E0B",
      "other": "#64748B"
    };

    // ── Tab Switching ──
    const tabs = document.querySelectorAll('.nav-tab');
    const tabPanes = document.querySelectorAll('.tab-pane');

    tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        const targetId = tab.getAttribute('data-tab');
        tabs.forEach(t => t.classList.remove('active'));
        tabPanes.forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        const targetPane = document.getElementById(targetId);
        if (targetPane) targetPane.classList.add('active');
      });
    });

    function switchTab(tabId) {
      const tabBtn = document.querySelector(`.nav-tab[data-tab="${tabId}"]`);
      if (tabBtn) tabBtn.click();
    }

    const evaluationContent = document.getElementById('evaluation-content');
    const runEvaluationButton = document.getElementById('btn-run-evaluation');

    function evaluationNode(tag, text, className) {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    }

    function renderEvaluation(report) {
      evaluationContent.replaceChildren();
      if (report.running) {
        evaluationContent.appendChild(evaluationNode('p', 'Evaluation is running in the background.'));
      }
      if (report.error) {
        const errorBox = evaluationNode('p', `Evaluation error: ${report.error}`);
        errorBox.style.cssText = 'color:#A51D3D; background:#FFF1F2; padding:10px; border-radius:8px; margin-bottom:12px;';
        evaluationContent.appendChild(errorBox);
      }
      if (!report.available) {
        const message = evaluationNode(
          'p',
          'No evaluation report found. Run the evaluation to train a temporary model and generate the report.'
        );
        message.style.color = 'var(--text-muted)';
        evaluationContent.appendChild(message);
        return;
      }
      if (report.metrics?.accuracy === undefined || !report.dataset?.train_size) {
        const msg = evaluationNode('p', 'Report on disk is from an older evaluator. Re-run evaluation to regenerate it.');
        msg.style.color = 'var(--accent-amber)';
        evaluationContent.appendChild(msg);
        return;
      }

      const metrics = report.metrics;
      const dataset = report.dataset;
      const ruleOnlyMacroF1 = metrics.macro_f1_including_rule_only_classes !== undefined
        ? metrics.macro_f1_including_rule_only_classes
        : metrics.macro_f1;
      const values = [
        ['Accuracy', metrics.accuracy],
        ['macro-F1 (8 trained classes)', metrics.macro_f1_trained_classes],
        ['macro-F1 including rule-only classes', ruleOnlyMacroF1],
        ['Weighted-F1', metrics.weighted_f1],
        ['Rows before dedup', dataset.size_before_dedup],
        ['Rows after dedup', `${dataset.size_after_dedup} (${dataset.duplicates_removed} removed)`],
        ['Train / test rows', `${dataset.train_size} / ${dataset.test_size}`]
      ];
      const metricGrid = evaluationNode('div');
      metricGrid.style.cssText = 'display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:14px 0;';
      values.forEach(([label, value]) => {
        const card = evaluationNode('div');
        card.style.cssText = 'background:#F8FAFC; border:1px solid var(--border-subtle); border-radius:10px; padding:14px;';
        const title = evaluationNode('div', label);
        title.style.cssText = 'font-size:11px; color:var(--text-dim); text-transform:uppercase; font-weight:700;';
        const result = evaluationNode(
          'div',
          typeof value === 'number' && label.includes('F1') || typeof value === 'number' && label === 'Accuracy'
            ? `${(value * 100).toFixed(2)}%`
            : String(value)
        );
        result.style.cssText = 'font-size:20px; font-weight:700; color:var(--text-main); margin-top:5px;';
        if (label === 'macro-F1 (8 trained classes)') result.style.color = 'var(--primary)';
        card.append(title, result);
        metricGrid.appendChild(card);
      });
      evaluationContent.appendChild(metricGrid);

      const ruleStats = report.hard_rule_evaluation;
      const ruleSummary = evaluationNode(
        'p',
        `Hard-rule overrides: ${ruleStats.rule_handled_rows} test rows, accuracy ${ruleStats.rule_handled_accuracy === null ? 'N/A' : (ruleStats.rule_handled_accuracy * 100).toFixed(2) + '%'}; ML-handled: ${ruleStats.ml_handled_rows} rows, accuracy ${ruleStats.ml_handled_accuracy === null ? 'N/A' : (ruleStats.ml_handled_accuracy * 100).toFixed(2) + '%'}.`
      );
      ruleSummary.style.cssText = 'padding:12px; background:#EEF2FF; border-radius:8px; margin-bottom:18px;';
      evaluationContent.appendChild(ruleSummary);

      const evaluationNotes = report.evaluation_notes || {};
      const notes = evaluationNode('div');
      notes.style.cssText = 'padding:14px; background:#FFFBEB; border:1px solid #FDE68A; border-radius:10px; margin:16px 0;';
      notes.append(
        evaluationNode('p', evaluationNotes.unused_variable || 'unused_variable has no labeled examples.'),
        evaluationNode('strong', 'How the labels were made'),
        evaluationNode(
          'p',
          evaluationNotes.label_provenance ||
            'Labels come from regex rules in prepare_training_data.py applied to real GCC messages from the criyle/codenet-compile-errors dataset; scores measure agreement with those rule-based labels.'
        )
      );
      evaluationContent.appendChild(notes);

      const explanation = evaluationNode('div');
      explanation.style.cssText = 'padding:14px; background:#F0FDFA; border:1px solid #99F6E4; border-radius:10px; margin:16px 0;';
      explanation.append(
        evaluationNode('strong', 'How to read a confusion matrix'),
        evaluationNode(
          'p',
          'Rows = true category, columns = predicted category, the diagonal = correct predictions, anything off-diagonal is a mistake and tells you which two categories get confused.'
        )
      );
      evaluationContent.appendChild(explanation);

      const confusion = report.confusion_matrix;
      const matrixTable = evaluationNode('table', undefined, 'styled-table');
      const matrixHead = document.createElement('thead');
      const headerRow = document.createElement('tr');
      headerRow.appendChild(evaluationNode('th', 'True \\ Predicted'));
      confusion.labels.forEach(label => headerRow.appendChild(evaluationNode('th', label)));
      matrixHead.appendChild(headerRow);
      matrixTable.appendChild(matrixHead);
      const matrixBody = document.createElement('tbody');
      confusion.labels.forEach((label, row) => {
        const tr = document.createElement('tr');
        tr.appendChild(evaluationNode('th', label));
        confusion.labels.forEach((_predicted, column) => {
          const value = confusion.row_normalized[row][column];
          const cell = evaluationNode(
            'td',
            `${confusion.counts[row][column]} (${(value * 100).toFixed(1)}%)`
          );
          cell.style.backgroundColor = `rgba(99, 102, 241, ${0.04 + value * 0.42})`;
          cell.style.textAlign = 'center';
          tr.appendChild(cell);
        });
        matrixBody.appendChild(tr);
      });
      matrixTable.appendChild(matrixBody);
      const matrixTitle = evaluationNode('h3', 'Confusion matrix (count and row-normalized %)');
      matrixTitle.style.cssText = 'margin:18px 0 8px; font-size:15px;';
      evaluationContent.append(matrixTitle, matrixTable);

      const classTitle = evaluationNode('h3', 'Per-class precision, recall and F1');
      classTitle.style.cssText = 'margin:22px 0 8px; font-size:15px;';
      const classTable = evaluationNode('table', undefined, 'styled-table');
      const classHead = document.createElement('thead');
      const classHeader = document.createElement('tr');
      ['Category', 'Precision', 'Recall', 'F1', 'Support'].forEach(
        heading => classHeader.appendChild(evaluationNode('th', heading))
      );
      classHead.appendChild(classHeader);
      classTable.appendChild(classHead);
      const classBody = document.createElement('tbody');
      Object.entries(report.per_class).forEach(([category, row]) => {
        const tr = document.createElement('tr');
        [category, row.precision.toFixed(3), row.recall.toFixed(3), row.f1.toFixed(3), row.support]
          .forEach(value => tr.appendChild(evaluationNode('td', String(value))));
        classBody.appendChild(tr);
      });
      classTable.appendChild(classBody);
      evaluationContent.append(classTitle, classTable);

      const weakestTitle = evaluationNode('h3', 'Three weakest classes by F1');
      weakestTitle.style.cssText = 'margin:22px 0 8px; font-size:15px;';
      const weakestList = document.createElement('ul');
      weakestList.style.cssText = 'padding-left:22px; color:var(--text-muted);';
      report.weakest_classes.forEach(item => {
        weakestList.appendChild(evaluationNode(
          'li',
          `${item.category} (F1 ${item.f1.toFixed(3)}, support ${item.support}): ${item.likely_reason}`
        ));
      });
      evaluationContent.append(weakestTitle, weakestList);

      const charts = evaluationNode('div');
      charts.style.cssText = 'display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; margin-top:20px;';
      [
        ['Confusion matrix chart', report.artifacts.confusion_matrix_png],
        ['Per-class F1 chart', report.artifacts.per_class_f1_png]
      ].forEach(([title, path]) => {
        const figure = evaluationNode('figure');
        figure.appendChild(evaluationNode('figcaption', title));
        const image = document.createElement('img');
        image.src = `/${path}?v=${encodeURIComponent(report.generated_at)}`;
        image.alt = title;
        image.style.cssText = 'width:100%; height:auto; margin-top:8px; border:1px solid var(--border-subtle); border-radius:8px;';
        figure.appendChild(image);
        charts.appendChild(figure);
      });
      evaluationContent.appendChild(charts);
    }

    async function loadEvaluation() {
      try {
        const response = await fetch('/api/evaluation');
        const report = await response.json();
        if (!response.ok) throw new Error(report.error || 'Could not load evaluation report.');
        renderEvaluation(report);
      } catch (error) {
        evaluationContent.textContent = `Could not load evaluation report: ${error.message}`;
      }
    }

    async function startEvaluation() {
      runEvaluationButton.disabled = true;
      runEvaluationButton.textContent = 'Evaluation running...';
      try {
        const response = await fetch('/api/evaluation/run', { method: 'POST' });
        const report = await response.json();
        if (!response.ok) throw new Error(report.error || 'Could not start evaluation.');
        const poll = async () => {
          const statusResponse = await fetch('/api/evaluation');
          const status = await statusResponse.json();
          if (!statusResponse.ok) throw new Error(status.error || 'Could not check evaluation status.');
          renderEvaluation(status);
          if (status.running) {
            await new Promise(resolve => window.setTimeout(resolve, 1500));
            await poll();
          } else {
            runEvaluationButton.disabled = false;
            runEvaluationButton.textContent = 'Run evaluation';
          }
        };
        await poll();
      } catch (error) {
        runEvaluationButton.disabled = false;
        runEvaluationButton.textContent = 'Run evaluation';
        evaluationContent.textContent = `Could not run evaluation: ${error.message}`;
      }
    }

    runEvaluationButton.addEventListener('click', startEvaluation);
    document.querySelector('.nav-tab[data-tab="tab-evaluation"]').addEventListener('click', loadEvaluation);

    // ── Drag & Drop & File Selection ──
    const dropArea = document.getElementById('drop-area');
    const fileInput = document.getElementById('file-input');
    const fileBadge = document.getElementById('file-badge');
    const fileNameText = document.getElementById('file-name-text');
    const languageSelector = document.getElementById('language-selector');
    const languageToolStatus = document.getElementById('language-tool-status');
    const testSpecInput = document.getElementById('test-spec-input');
    const testSpecNote = document.getElementById('test-spec-note');
    const executeRuntimeCheckbox = document.getElementById('execute-runtime-checkbox');
    const runtimeCheckStatus = document.getElementById('runtime-check-status');
    let currentTestSpec = null;
    const languageDefaults = {
      cpp: 'pasted_code.cpp', python: 'pasted_code.py', java: 'Main.java', c: 'pasted_code.c', javascript: 'pasted_code.js'
    };
    const languageByExtension = {
      '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp', '.py': 'python', '.java': 'java', '.c': 'c', '.js': 'javascript'
    };
    const toolStatus = {{ tool_status|tojson }};
    const languageMetadata = {{ languages|tojson }};
    const languageTools = { cpp: ['g++', 'clang++'], python: ['python'], java: ['javac', 'java'], c: ['gcc'], javascript: ['node'] };

    function selectedLanguage() { return languageSelector.value || 'cpp'; }
    function updateLanguageStatus() {
      const tools = languageTools[selectedLanguage()] || [];
      if (selectedLanguage() === 'java') {
        const details = tools.map(tool => {
          const entry = toolStatus[tool] || {};
          return `${tool}: ${entry.version || 'tool not installed'} (${entry.path || 'path unavailable'})`;
        });
        const compatibility = toolStatus.java_compatibility || {};
        const javaMetadata = languageMetadata.find(language => language.name === 'java') || {};
        if (javaMetadata.categorization_engine) details.push(`Categorizer: ${javaMetadata.categorization_engine}`);
        if (javaMetadata.ast_engine) details.push(`AST parser: ${javaMetadata.ast_engine}`);
        if (compatibility.message) details.push(compatibility.message);
        if (compatibility.warning) details.push(`Warning: ${compatibility.warning}`);
        languageToolStatus.textContent = details.join(' · ');
      } else {
        languageToolStatus.textContent = tools.map(tool => `${tool}: ${(toolStatus[tool] || {}).version || 'tool not installed'}`).join(' · ');
      }
    }
    executeRuntimeCheckbox.addEventListener('change', () => {
      runtimeCheckStatus.textContent = executeRuntimeCheckbox.checked
        ? 'Runtime check enabled. High/Critical findings will still block execution.'
        : 'Runtime check skipped. Behavioral tests also stay off.';
      updateTestSpecNote();
    });
    function updateTestSpecNote() {
      testSpecNote.textContent = currentTestSpec
        ? executeRuntimeCheckbox.checked
          ? `Selected ${currentTestSpec.name}; it will run only if the security scan has no High/Critical findings.`
          : `Selected ${currentTestSpec.name}; behavioral tests are skipped until runtime execution is enabled.`
        : 'JSON cases use stdin and expected_stdout; each case has a 5-second limit. Tests run only after opt-in.';
    }
    function updatePasteFilenameForLanguage() {
      const filenameField = document.getElementById('paste-filename');
      if (filenameField.value === '' || Object.values(languageDefaults).includes(filenameField.value)) {
        filenameField.value = languageDefaults[selectedLanguage()];
      }
    }
    languageSelector.addEventListener('change', () => { updatePasteFilenameForLanguage(); updateLanguageStatus(); });
    updateLanguageStatus();

    testSpecInput.addEventListener('change', () => {
      currentTestSpec = testSpecInput.files.length ? testSpecInput.files[0] : null;
      updateTestSpecNote();
    });

    // ── Live Code Editor Setup (PASTE IT HERE) ──
    let codeEditor = null;
    let lintTimeout = null;

    function initCodeEditor() {
      const container = document.getElementById('code-editor-container');
      if (!container) return;
      if (typeof CodeMirror === 'undefined') {
        document.getElementById('paste-textarea').style.display = 'block';
        container.style.display = 'none';
        return;
      }

      codeEditor = CodeMirror(container, {
        value: "",
        mode: "text/x-c++src",
        lineNumbers: true,
        theme: "default",
        gutters: ["CodeMirror-lint-markers"],
        lint: { getAnnotations: () => [] },
        lineWrapping: true
      });

      codeEditor.on("change", () => {
        clearTimeout(lintTimeout);
        lintTimeout = setTimeout(async () => {
          const code = codeEditor.getValue();
          if (!code.trim()) return;

          try {
            const formData = new FormData();
            formData.append('source', new Blob([code], { type: 'text/plain' }), currentFilename);
            formData.append('language', selectedLanguage());

            const res = await fetch('/api/lint', { method: 'POST', body: formData });
            const data = await res.json();
            
            const lintErrors = (data.errors || []).map(e => ({
              from: CodeMirror.Pos(e.line - 1, e.column - 1),
              to: CodeMirror.Pos(e.line - 1, e.column + 10),
              message: e.message,
              severity: e.error_type === 'error' ? 'error' : 'warning'
            }));

            codeEditor.setOption("lint", { getAnnotations: () => lintErrors });

            if (lintErrors.length > 0) {
              document.getElementById('overview-compile-status').textContent = `${lintErrors.length} LIVE ERROR(S)`;
              document.getElementById('overview-compile-status').className = "badge badge-rose";
            } else {
              document.getElementById('overview-compile-status').textContent = "LIVE: CLEAN";
              document.getElementById('overview-compile-status').className = "badge badge-emerald";
            }
          } catch (err) {
            console.error("Live lint failed", err);
          }
        }, 1000);
      });
    }

    window.addEventListener('DOMContentLoaded', () => {
      initCodeEditor();
    });

    function handleFileSelect(file) {
      currentFilename = file.name;
      const dot = file.name.lastIndexOf('.');
      const inferredLanguage = dot >= 0 ? languageByExtension[file.name.slice(dot).toLowerCase()] : null;
      if (inferredLanguage) { languageSelector.value = inferredLanguage; updateLanguageStatus(); }
      fileNameText.textContent = file.name;
      fileBadge.style.display = 'inline-flex';
      const reader = new FileReader();
      reader.onload = (e) => {
        currentSource = e.target.result;
        hasLoadedSource = true;
        updateSourceDisplay(currentSource);
        runAnalysis();
      };
      reader.readAsText(file);
    }

    dropArea.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropArea.classList.add('dragover');
    });
    dropArea.addEventListener('dragleave', () => dropArea.classList.remove('dragover'));
    dropArea.addEventListener('drop', (e) => {
      e.preventDefault();
      dropArea.classList.remove('dragover');
      if (e.dataTransfer.files.length) handleFileSelect(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files.length) handleFileSelect(fileInput.files[0]);
    });

    // ── Input mode: "Upload File" vs "Paste Code" ──
    // Both modes feed the SAME currentSource/currentFilename globals, so pasted
    // text travels through the identical /api/analyze and /api/fix multipart
    // 'source' upload path (and the same temp-file writing) as a real upload.
    let inputMode = 'upload';

    function setInputMode(mode) {
      inputMode = mode;
      document.getElementById('mode-btn-upload').classList.toggle('active', mode === 'upload');
      document.getElementById('mode-btn-paste').classList.toggle('active', mode === 'paste');
      document.getElementById('upload-panel').style.display = (mode === 'upload') ? '' : 'none';
      document.getElementById('paste-panel').style.display = (mode === 'paste') ? 'flex' : 'none';
      if (mode === 'paste' && codeEditor) codeEditor.refresh();
    }

    document.getElementById('mode-btn-upload').addEventListener('click', () => setInputMode('upload'));
    document.getElementById('mode-btn-paste').addEventListener('click', () => setInputMode('paste'));

    function syncActiveInput() {
      if (inputMode === 'paste') {
        const filenameField = document.getElementById('paste-filename');
        const name = (filenameField.value || '').trim() || languageDefaults[selectedLanguage()];
        
        currentSource = codeEditor
          ? codeEditor.getValue()
          : document.getElementById('paste-textarea').value;
        currentFilename = name;
        
        if (currentSource) updateSourceDisplay(currentSource);
      }
      return inputMode === 'paste' ? !!currentSource.trim() : hasLoadedSource;
    }

    function updateSourceDisplay(code, findings = []) {
      const pre = document.getElementById('source-code-view');
      const lines = code.split('\n');
      const findingsByLine = new Map();
      findings.forEach(finding => {
        if (!finding.line) return;
        const list = findingsByLine.get(finding.line) || [];
        list.push(finding);
        findingsByLine.set(finding.line, list);
      });
      let html = '';
      lines.forEach((line, idx) => {
        const lineFindings = findingsByLine.get(idx + 1) || [];
        const num = String(idx + 1).padStart(3, ' ');
        const labels = lineFindings.map(finding => {
          const title = `${finding.description || ''} Fix: ${finding.recommendation || ''}`;
          return `<span class="source-finding-label" title="${escapeHtml(title)}">${escapeHtml(finding.vulnerability_type || 'Finding')} · ${escapeHtml(finding.severity || 'Low')}</span>`;
        }).join('');
        html += `<span class="source-line${lineFindings.length ? ' flagged' : ''}"><span style="color:var(--text-dim); user-select:none; margin-right:12px;">${num} |</span>${escapeHtml(line)}${labels}</span>\n`;
      });
      pre.innerHTML = html;
    }

    // ── Sample Files ──
    document.querySelectorAll('.sample-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const sampleKey = btn.getAttribute('data-sample');
        const res = await fetch(`/api/sample?name=${sampleKey}`);
        const data = await res.json();
        if (data.code) {
          if (inputMode === 'paste') {
            document.getElementById('paste-textarea').value = data.code;
            document.getElementById('paste-filename').value = data.filename;
            if (codeEditor) codeEditor.setValue(data.code);
          }
          currentSource = data.code;
          currentFilename = data.filename;
          hasLoadedSource = true;
          fileNameText.textContent = data.filename;
          fileBadge.style.display = 'inline-flex';
          updateSourceDisplay(currentSource);
          runAnalysis();
        }
      });
    });

    // ── Form Submit (Analyze) ──
    document.getElementById('upload-form').addEventListener('submit', (e) => {
      e.preventDefault();
      runAnalysis();
    });

    async function runAnalysis() {
      if (!syncActiveInput()) {
        alert(inputMode === 'paste' ? "Please paste some code first." : "Please select or drop a supported source file first.");
        return;
      }
      const runId = ++activeAnalysisRunId;
      const btn = document.getElementById('btn-analyze');
      const spinner = document.getElementById('analyze-spinner');
      btn.disabled = true;
      spinner.style.display = 'inline-block';
      renderDfaLoading();

      try {
        const formData = new FormData();
        const blob = new Blob([currentSource], { type: 'text/plain' });
        formData.append('source', blob, currentFilename);
        formData.append('language', selectedLanguage());
        formData.append('input_mode', inputMode);
        formData.append('execute_runtime', String(executeRuntimeCheckbox.checked));
        formData.append('ast_node_limit', document.getElementById('ast-node-limit').value || '150');
        formData.append('ast_depth_limit', document.getElementById('ast-depth-limit').value || '12');
        formData.append('ast_expand_full', document.getElementById('btn-ast-expand-full').dataset.expand || 'false');
        if (currentTestSpec) formData.append('test_spec', currentTestSpec, currentTestSpec.name);

        const res = await fetch('/api/analyze', { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Analysis failed");
        if (!Array.isArray(data.trace) || !data.trace_metadata ||
            data.trace_metadata.displayed_count !== data.trace.length) {
          throw new Error("The Analyze response did not contain complete DFA trace metadata.");
        }

        if (runId === activeAnalysisRunId) {
          lastAnalysisData = data;
          runtimeCheckStatus.textContent = (data.runtime_check || {}).message || (
            executeRuntimeCheckbox.checked ? 'Runtime check completed.' : 'Runtime check skipped.'
          );
          renderDashboard(data);
        }
      } catch (err) {
        if (runId === activeAnalysisRunId) {
          renderDfaError(err.message);
          alert("Error during analysis: " + err.message);
        }
      } finally {
        if (runId === activeAnalysisRunId) {
          btn.disabled = false;
          spinner.style.display = 'none';
          document.getElementById('btn-ast-expand-full').dataset.expand = 'false';
        }
      }
    }

    document.getElementById('btn-ast-expand-full').addEventListener('click', () => {
      document.getElementById('btn-ast-expand-full').dataset.expand = 'true';
      runAnalysis();
    });

    // ── Render Full Dashboard ──
        function renderDashboard(data) {
      document.getElementById('analytics-section').style.display = 'block';
      updateSourceDisplay(data.source || currentSource, data.findings || []);

      const renderers = [
        ['analytics',   () => renderAnalyticsGraph(data)],
        ['overview',    () => renderOverviewTab(data)],
        ['diagnostics', () => renderDiagnosticsTab(data)],
        ['security',    () => renderSecurityTab(data)],
        ['dfa',         () => renderDfaTab(data.trace, data.trace_metadata)],
        ['ast',         () => renderAstTab(data.ast)],
        ['grammar',     () => renderGrammarTab(data.grammar)],
      ];
      for (const [name, fn] of renderers) {
        try {
          fn();
        } catch (err) {
          console.error(`[renderDashboard] ${name} tab failed:`, err);
          if (name === 'dfa') {
            const el = document.getElementById('dfa-status');
            if (el) {
              el.textContent = `dfa tab error: ${err.message}`;
              el.style.color = 'var(--accent-rose)';
            }
          }
        }
      }
    }

    // ── Render Error Percentage Graph & Metrics ──
    function renderAnalyticsGraph(data) {
      const analytics = data.analytics || {};
      const total = analytics.total_issues || (data.errors ? data.errors.length : 0);
      const hardErrors = analytics.error_count || 0;
      const warnings = analytics.warning_count || 0;
      const securityCount = analytics.security_count || (data.findings ? data.findings.length : 0);
      const health = analytics.health_score !== undefined ? analytics.health_score : 100;

      document.getElementById('val-hard-errors').textContent = hardErrors;
      document.getElementById('desc-hard-errors').textContent = total ? `${analytics.error_percentage || 0}% of diagnostics` : 'No errors';

      document.getElementById('val-warnings').textContent = warnings;
      document.getElementById('desc-warnings').textContent = total ? `${analytics.warning_percentage || 0}% of diagnostics` : 'No warnings';

      document.getElementById('val-security').textContent = securityCount;
      document.getElementById('desc-security').textContent = `Risk Level: ${data.risk_level || 'None'}`;

      const healthEl = document.getElementById('val-health');
      healthEl.textContent = `${health}%`;
      healthEl.style.color = health > 80 ? 'var(--accent-emerald)' : health > 50 ? 'var(--accent-amber)' : 'var(--accent-rose)';

      document.getElementById('file-quick-stats').textContent = `${data.filename} &bull; ${data.source.split('\n').length} lines`;
      document.getElementById('bar-total-label').textContent = `${total} Total Issue(s) Identified`;

      // Segmented Bar
      const barEl = document.getElementById('error-segmented-bar');
      barEl.innerHTML = '';
      const chipsEl = document.getElementById('category-chips-row');
      chipsEl.innerHTML = '';

      if (total === 0) {
        barEl.innerHTML = `<div class="bar-segment" style="width: 100%; background: #10B981;" title="Clean: No Errors"></div>`;
        chipsEl.innerHTML = `
          <div class="category-chip">
            <span class="chip-dot" style="background:#10B981;"></span>
            <span class="chip-name">All Clean</span>
            <span class="chip-pct">100%</span>
          </div>`;
        return;
      }

      const catPercentages = analytics.category_percentages || {};
      for (const [cat, info] of Object.entries(catPercentages)) {
        const color = CATEGORY_COLORS[cat] || "#64748B";
        const seg = document.createElement('div');
        seg.className = 'bar-segment';
        seg.style.width = `${info.percentage}%`;
        seg.style.background = color;
        seg.title = `${formatCategoryName(cat)}: ${info.count} (${info.percentage}%)`;
        barEl.appendChild(seg);

        const chip = document.createElement('div');
        chip.className = 'category-chip';
        chip.innerHTML = `
          <span class="chip-dot" style="background:${color};"></span>
          <span class="chip-name">${formatCategoryName(cat)}</span>
          <span class="chip-pct">${info.count} (${info.percentage}%)</span>
        `;
        chipsEl.appendChild(chip);
      }
    }

    // ── Render Overview Tab ──
    function renderOverviewTab(data) {
      const statusBadge = document.getElementById('overview-compile-status');
      if (data.support_coming) {
        statusBadge.className = 'badge badge-amber';
        statusBadge.textContent = 'Support Coming';
        document.getElementById('overview-summary-content').innerHTML = `
          <div class="empty-state"><h3 style="color:var(--accent-amber); font-weight:700; margin-bottom:4px;">${escapeHtml((data.language || {}).display_name || 'Selected language')} support coming</h3>
          <p>${escapeHtml(data.support_message || 'This language is not implemented yet.')}</p></div>`;
        return;
      }
      if (data.compile_success) {
        statusBadge.className = 'badge badge-emerald';
        statusBadge.textContent = 'Compile Clean';
      } else {
        statusBadge.className = 'badge badge-rose';
        statusBadge.textContent = 'Build Failed';
      }

      const container = document.getElementById('overview-summary-content');
      container.innerHTML = `
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:16px;">
          <div style="background:#FFFFFF; padding:16px; border-radius:var(--radius-md); border:1px solid var(--border-subtle);">
            <h4 style="font-size:13px; font-weight:700; color:var(--text-dim); text-transform:uppercase; margin-bottom:8px;">Compiler Verdict</h4>
            <p style="font-size:15px; font-weight:600; color:${data.compile_success ? 'var(--accent-emerald)' : 'var(--accent-rose)'};">
              ${data.compile_success ? 'Compilation Succeeded with 0 Errors' : `${data.errors.length} Compiler Diagnostic(s) Blocking`}
            </p>
            <p style="font-size:12px; color:var(--text-muted); margin-top:4px;">
              ${data.compile_success ? 'The code passes syntax and semantic analysis.' : 'Review Diagnostics tab for line-by-line NLP guidance or click Auto-Heal.'}
            </p>
          </div>

          <div style="background:#FFFFFF; padding:16px; border-radius:var(--radius-md); border:1px solid var(--border-subtle);">
            <h4 style="font-size:13px; font-weight:700; color:var(--text-dim); text-transform:uppercase; margin-bottom:8px;">Code health</h4>
            <span class="badge ${verdictBadgeClass({verdict: (data.code_health || {}).verdict})}">Code health: ${escapeHtml((data.code_health || {}).verdict || 'HEALTHY')}</span>
            <span class="badge ${verdictBadgeClass(data.security_health)}" style="margin-left:6px;">Security health: ${escapeHtml((data.security_health || {}).verdict || 'HEALTHY')}</span>
            <p style="font-size:15px; font-weight:600; margin-top:8px;">
              Security score: ${Number((data.security_health || {}).score || 0)} (${(data.findings || []).length} findings)
            </p>
            <p style="font-size:12px; color:var(--text-muted); margin-top:4px;">
              Offending lines (security + compile): ${escapeHtml(((data.code_health || {}).offending_lines || []).map(item => `Line ${item.line}: ${(item.rules || []).join(', ')}`).join(' · ') || 'none')}
            </p>
            <p style="font-size:12px; color:var(--text-muted); margin-top:4px;">
              Score = 10*Critical + 6*High + 3*Medium + 1*Low
            </p>
          </div>

          <div style="background:#FFFFFF; padding:16px; border-radius:var(--radius-md); border:1px solid var(--border-subtle);">
            <h4 style="font-size:13px; font-weight:700; color:var(--text-dim); text-transform:uppercase; margin-bottom:8px;">Grammar &amp; AST</h4>
            <p style="font-size:15px; font-weight:600; color:var(--accent-cyan);">
              Grammar: ${(data.grammar && data.grammar.status) ? data.grammar.status.toUpperCase() : 'CHECKED'}
            </p>
            <p style="font-size:12px; color:var(--text-muted); margin-top:4px;">
              DFA lexer processed ${(data.trace_metadata || {}).total_count || 0} source characters.
            </p>
          </div>
        </div>
      `;
    }

    // ── Render Diagnostics Tab ──
    function renderDiagnosticsTab(data) {
      const container = document.getElementById('diagnostics-content');
      const countBadge = document.getElementById('diag-count-badge');
      const errors = data.errors || [];
      countBadge.textContent = `${errors.length} Issue(s)`;

      const modelsBox = document.getElementById('nlp-models-used');
      const models = (data.transparency || {}).nlp_models_used || [];
      if (models.length) {
        modelsBox.style.display = 'block';
        modelsBox.innerHTML = `
          <div style="font-size:13px; font-weight:700; margin-bottom:6px;">NLP models used</div>
          ${models.map(model => `<div style="font-size:12px; color:var(--text-muted); margin-top:3px;"><strong style="color:var(--text-main);">${escapeHtml(model.name)}</strong> — ${escapeHtml(model.role)}</div>`).join('')}
          <div style="font-size:12px; color:var(--text-muted); margin-top:3px;"><strong style="color:var(--text-main);">Compiler/interpreter:</strong> ${escapeHtml((((data.transparency || {}).compiler || {}).version) || 'tool not installed')} ${escapeHtml((((data.transparency || {}).compiler || {}).path) || '')}</div>
          ${(data.transparency || {}).compile_target_message ? `<div style="font-size:12px; color:var(--text-muted); margin-top:3px;">${escapeHtml(data.transparency.compile_target_message)}</div>` : ''}
          ${(data.transparency || {}).tool_warning ? `<div style="font-size:12px; color:var(--accent-amber); margin-top:3px;">Warning: ${escapeHtml(data.transparency.tool_warning)}</div>` : ''}
          ${(data.transparency || {}).runtime_note ? `<div style="font-size:12px; color:var(--accent-amber); margin-top:6px;"><strong>Runtime check:</strong> ${escapeHtml(data.transparency.runtime_note)}</div>` : ''}`;
      } else {
        modelsBox.style.display = 'none';
        modelsBox.replaceChildren();
      }

      if (data.support_coming) {
        container.innerHTML = `<div class="empty-state"><h3 style="color:var(--accent-amber); font-weight:700; margin-bottom:4px;">Support coming</h3><p>${escapeHtml(data.support_message || 'This language is not implemented yet.')}</p></div>`;
        return;
      }

      if (errors.length === 0) {
        container.innerHTML = `
          <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="var(--accent-emerald)" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="16 12 12 8 8 12"></polyline><line x1="12" y1="16" x2="12" y2="8"></line></svg>
            <h3 style="color:var(--accent-emerald); font-weight:700; margin-bottom:4px;">Clean Compilation</h3>
            <p>No compiler errors or warnings detected in this file.</p>
          </div>
        `;
        return;
      }

      let html = "";
      errors.forEach((err, idx) => {
        const isError = err.type === 'ERROR';
        const badgeClass = isError ? 'badge-rose' : 'badge-amber';
        const lineInfo = err.line ? `Line ${err.line}` : 'Global';

        html += `
          <div class="error-item ${isError ? 'error' : 'warning'}">
            <div class="error-header-line">
              <span class="badge ${badgeClass}">${err.type}</span>
              <span class="badge badge-cyan">${escapeHtml(formatCategoryName(err.category || 'other'))}</span>
              <span class="badge badge-purple">${lineInfo}</span>
            </div>
            <div class="error-code-raw">${escapeHtml(err.raw || err.message)}</div>
            <div class="nlp-explanation-grid">
              <div class="nlp-row">
                <span class="nlp-label">What Happened</span>
                <span class="nlp-body">${escapeHtml(err.explanation || 'Compiler detected a syntax constraint violation.')}</span>
              </div>
              ${err.suggestion ? `
                <div class="nlp-row">
                  <span class="nlp-label" style="color:var(--accent-emerald);">Suggested Fix</span>
                  <span class="nlp-body">${escapeHtml(err.suggestion)}</span>
                </div>` : ''}
              ${err.additional_detail ? `
                <div class="nlp-row">
                  <span class="nlp-label" style="color:var(--accent-amber);">Grammar &amp; AST Context</span>
                  <span class="nlp-body">${escapeHtml(err.additional_detail)}</span>
                </div>` : ''}
            </div>
          </div>
        `;
      });
      container.innerHTML = html;
    }

    function verdictBadgeClass(health) {
      const verdict = (health || {}).verdict || 'HEALTHY';
      if (verdict === 'UNHEALTHY') return 'badge-rose';
      if (verdict === 'MODERATE') return 'badge-amber';
      return 'badge-emerald';
    }

    // ── Phase 4 Agentic Auto-Heal Trigger & Display ──
    document.getElementById('btn-run-heal').addEventListener('click', runAutoHeal);
    document.getElementById('btn-quick-heal').addEventListener('click', () => {
      switchTab('tab-autoheal');
      runAutoHeal();
    });

    function replaceHealerResults(markup) {
      const container = document.getElementById('healer-results-area');
      // The Auto-Heal display is one atomic result surface. Remove every child
      // from the preceding run before inserting the next loading/error/result UI.
      container.replaceChildren();
      container.insertAdjacentHTML('afterbegin', markup);
    }

    async function runAutoHeal() {
      if (!syncActiveInput()) {
        alert(inputMode === 'paste' ? "Please paste some C++ code first." : "Please load or select a C++ file first.");
        return;
      }
      const btn = document.getElementById('btn-run-heal');
      const quickBtn = document.getElementById('btn-quick-heal');
      const spinner = document.getElementById('heal-spinner');
      const runId = ++activeHealRunId;
      const sourceForRun = currentSource;
      const filenameForRun = currentFilename;
      const testSpecForRun = currentTestSpec;
      const executeRuntime = executeRuntimeCheckbox.checked;

      if (activeHealAbortController) activeHealAbortController.abort();
      const abortController = new AbortController();
      activeHealAbortController = abortController;

      btn.disabled = true;
      quickBtn.disabled = true;
      spinner.style.display = 'inline-block';
      replaceHealerResults(`
        <div style="padding:40px; text-align:center;">
          <div class="spinner" style="width:36px; height:36px; border-width:3px; margin-bottom:14px;"></div>
          <h3 style="font-weight:700;">Running Agentic Healing Loop...</h3>
          <p style="color:var(--text-muted); font-size:13px; margin-top:4px;">
            ${executeRuntime ? 'Executing' : 'Static checks only'}: OBSERVE &rarr; PLAN &rarr; ACT &rarr; OBSERVE &rarr; REPLAN
          </p>
        </div>
      `);

      try {
        const formData = new FormData();
        const blob = new Blob([sourceForRun], { type: 'text/plain' });
        formData.append('source', blob, filenameForRun);
        formData.append('language', selectedLanguage());
        formData.append('input_mode', inputMode);
        formData.append('execute_runtime', String(executeRuntime));
        if (testSpecForRun) formData.append('test_spec', testSpecForRun, testSpecForRun.name);

        const res = await fetch('/api/fix', { method: 'POST', body: formData, signal: abortController.signal });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Auto-heal execution failed");

        runtimeCheckStatus.textContent = (data.runtime_check || {}).message || (
          executeRuntime ? 'Runtime check completed.' : 'Runtime check skipped.'
        );
        if (runId === activeHealRunId) renderPhase4HealerResults(data);
      } catch (err) {
        if (err.name === 'AbortError' || runId !== activeHealRunId) return;
        replaceHealerResults(`
          <div class="empty-state" style="color:var(--accent-rose);">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
            <p>Heal execution error: ${escapeHtml(err.message)}</p>
          </div>
        `);
      } finally {
        if (runId === activeHealRunId) {
          activeHealAbortController = null;
          btn.disabled = false;
          quickBtn.disabled = false;
          spinner.style.display = 'none';
        }
      }
    }

    function renderPhase4HealerResults(data) {
      const container = document.getElementById('healer-results-area');
      const attempts = data.attempts || [];
      const verdict = data.verdict || (data.compiles ? 'fixed_and_verified' : 'not_fixable');

      let verdictClass = 'verified';
      let verdictTitle = data.verdict_label || 'Fixed and verified';
      let verdictDesc = 'All compiler diagnostics cleared and verified.';
      const behavior = data.behavior || {};

      if (data.support_coming) {
        container.innerHTML = `<div class="empty-state"><h3 style="color:var(--accent-amber); font-weight:700; margin-bottom:4px;">Support coming</h3><p>${escapeHtml(data.support_message || 'Auto-heal is not implemented for this language yet.')}</p></div>`;
        return;
      }

      if (verdict === 'already_clean') {
        verdictClass = 'clean';
        verdictDesc = data.explanation || 'The input code was already syntactically clean; no patch was applied.';
      } else if (verdict === 'compiles_not_behaviorally_verified') {
        verdictClass = 'unverified';
        verdictDesc = data.explanation || 'Compilation succeeded, but behavioral runtime tests were not executed.';
      } else if (verdict === 'compiles_but_behavioral_verification_failed') {
        verdictClass = 'not_fixable';
        verdictDesc = data.explanation || 'Compilation succeeded, but one or more behavioral test cases failed.';
      } else if (verdict === 'not_fixable') {
        verdictClass = 'not_fixable';
        verdictDesc = data.explanation || 'Could not automatically patch after maximum attempts; requires manual human correction.';
      }

      let html = `
        <!-- Final Result Verdict Banner -->
        <div class="verdict-banner ${verdictClass}">
          <div class="verdict-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              ${(verdict === 'not_fixable' || verdict === 'compiles_but_behavioral_verification_failed')
                ? '<circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line>' 
                : '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline>'}
            </svg>
          </div>
          <div class="verdict-content">
            <h3>${escapeHtml(verdictTitle)}</h3>
            <p>${escapeHtml(verdictDesc)}</p>
            <p class="verdict-note">Notice: ${data.transparency && data.transparency.language === 'Python' ? 'Patches are rechecked with Python syntax and runtime checks.' : 'Patches are validated against syntactic compiler diagnostics.'} No claim of algorithmic correctness is made.</p>
          </div>
        </div>

        <div class="card" style="margin:0 0 16px; padding:12px;">
          <div style="font-size:13px; font-weight:700; margin-bottom:6px;">Engines used in this run</div>
          <div style="font-size:12px; color:var(--text-muted); margin-top:3px;"><strong style="color:var(--text-main);">Language:</strong> ${escapeHtml((data.transparency || {}).language || 'C++')}</div>
          <div style="font-size:12px; color:var(--text-muted); margin-top:3px;"><strong style="color:var(--text-main);">Categorization engine:</strong> ${escapeHtml((data.transparency || {}).classifier || 'Classifier unavailable')}</div>
          <div style="font-size:12px; color:var(--text-muted); margin-top:3px;"><strong style="color:var(--text-main);">Repair strategies tried:</strong> ${escapeHtml(((data.transparency || {}).repair_strategies || []).join(', '))}</div>
          <div style="font-size:12px; color:var(--text-muted); margin-top:3px;"><strong style="color:var(--text-main);">Compiler/interpreter:</strong> ${escapeHtml((((data.transparency || {}).compiler || {}).version) || 'version unavailable')} ${escapeHtml((((data.transparency || {}).compiler || {}).path) || '')}</div>
          ${(data.transparency || {}).compile_target_message ? `<div style="font-size:12px; color:var(--text-muted); margin-top:3px;">${escapeHtml(data.transparency.compile_target_message)}</div>` : ''}
          ${(data.transparency || {}).tool_warning ? `<div style="font-size:12px; color:var(--accent-amber); margin-top:3px;">Warning: ${escapeHtml(data.transparency.tool_warning)}</div>` : ''}
          ${(data.transparency || {}).runtime_note ? `<div style="font-size:12px; color:var(--accent-amber); margin-top:6px;"><strong>Runtime check:</strong> ${escapeHtml(data.transparency.runtime_note)}</div>` : ''}
          <div style="font-size:12px; color:var(--text-muted); margin-top:3px;"><strong style="color:var(--text-main);">Behavioral tests:</strong> ${escapeHtml((data.transparency || {}).behavioral_tests || 'Did not run')}</div>
          <div style="font-size:12px; color:var(--text-muted); margin-top:6px;">Fixes are rule-based; no generative/LLM model writes them.</div>
        </div>

        <h4 style="font-size:15px; font-weight:700; margin-bottom:12px; display:flex; align-items:center; gap:8px;">
          <span>Reasoning Progression &amp; Attempts (${attempts.length})</span>
        </h4>
      `;

      if (behavior.checked) {
        const cases = behavior.cases || [];
        html += `
          <div class="card" style="margin:0 0 16px; padding:14px;">
            <div style="font-weight:700; font-size:13px; margin-bottom:7px;">Behavioral I/O verification</div>
            <div style="color:var(--text-muted); font-size:12px; margin-bottom:10px;">${escapeHtml(behavior.note || '')}</div>
            ${cases.map(test => `
              <div style="border-top:1px solid var(--border-card); padding:9px 0; font-family:'JetBrains Mono', monospace; font-size:11px;">
                <span class="badge ${test.passed ? 'badge-emerald' : 'badge-rose'}">CASE ${test.number}: ${test.passed ? 'PASS' : (test.status || 'FAIL').toUpperCase()}</span>
                <div style="margin-top:6px; color:var(--text-muted);">stdin: <code>${escapeHtml(JSON.stringify(test.stdin || ''))}</code></div>
                <div style="margin-top:3px; color:var(--text-muted);">expected: <code>${escapeHtml(JSON.stringify(test.expected_stdout || ''))}</code></div>
                ${test.passed ? '' : `<div style="margin-top:3px; color:var(--accent-rose);">actual: <code>${escapeHtml(JSON.stringify(test.actual_stdout || ''))}</code> — ${escapeHtml(test.note || '')}</div>`}
              </div>`).join('')}
          </div>`;
      } else if (behavior.note) {
        html += `<div class="card" style="margin:0 0 16px; padding:14px; color:var(--text-muted); font-size:12px;"><strong>Behavioral I/O verification:</strong> ${escapeHtml(behavior.note)}</div>`;
      }

      // Render Numbered Attempts
      attempts.forEach((att, idx) => {
        const isLast = (idx === attempts.length - 1);
        const isOpen = isLast ? 'open' : '';
        const strat = att.strategy || 'repair_rule';
        const changedLines = (att.lines_changed && att.lines_changed.length) ? `Lines [${att.lines_changed.join(', ')}]` : '';

        html += `
          <div class="attempt-card ${isOpen}" id="attempt-card-${idx}">
            <div class="attempt-summary" onclick="toggleAttemptCard(${idx})">
              <div class="attempt-left">
                <div class="attempt-num-badge">${att.number}</div>
                <div>
                  <span class="badge badge-purple">${escapeHtml(formatCategoryName(strat))}</span>
                  <span class="attempt-reason-title">${escapeHtml(att.reasoning || `Attempt ${att.number}`)}</span>
                </div>
              </div>
              <div style="display:flex; align-items:center; gap:10px;">
                <span class="badge ${att.compiles ? 'badge-emerald' : 'badge-amber'}">${att.compiles ? 'COMPILES' : 'FAILED'}</span>
                <span class="badge ${att.changed ? 'badge-cyan' : 'badge-rose'}">${att.changed ? 'PATCHED' : 'NO CHANGE'}</span>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
              </div>
            </div>
            <div class="attempt-body">
              <div class="loop-cycle-steps">
                <div class="loop-step-box">
                  <div class="loop-step-name observe">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle></svg>
                    1. OBSERVED
                  </div>
                  <div class="loop-step-text">${escapeHtml(att.observed || att.error || 'Compiler error reported')}</div>
                </div>
                <div class="loop-step-box">
                  <div class="loop-step-name plan">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon></svg>
                    2. PLANNED
                  </div>
                  <div class="loop-step-text">${escapeHtml(att.planned || `Apply ${strat}`)}</div>
                </div>
                <div class="loop-step-box">
                  <div class="loop-step-name act">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
                    3. ACTED
                  </div>
                  <div class="loop-step-text">${escapeHtml(att.action || (att.changed ? `Applied patch to code` : 'No valid patch found'))}</div>
                </div>
                <div class="loop-step-box">
                  <div class="loop-step-name outcome">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>
                    4. OUTCOME
                  </div>
                  <div class="loop-step-text">${escapeHtml(att.outcome || (att.compiles ? 'Clean compile reached!' : 'Error persists; replanning.'))}</div>
                </div>
              </div>

              <!-- Side-by-Side Diff Comparison -->
              <div class="diff-split-container">
                <div>
                  <div class="diff-pane-title">
                    <span>Code Before Attempt</span>
                  </div>
                  <pre class="code-view">${escapeHtml(att.before || '')}</pre>
                </div>
                <div>
                  <div class="diff-pane-title">
                    <span>Patch Diff (Attempt ${att.number})</span>
                    <span style="color:var(--accent-cyan);">${changedLines}</span>
                  </div>
                  <div class="diff-html-box">${att.diff_html || '<i>No visual diff available</i>'}</div>
                </div>
              </div>
            </div>
          </div>
        `;
      });

      // Final Healed Code Box with Copy & Download
      if (data.fixed_source) {
        html += `
          <div style="margin-top:28px;">
            <div class="diff-pane-title" style="margin-bottom:8px;">
              <span style="font-size:14px; color:var(--text-main);">Final Healed Program Code</span>
              <button class="btn btn-secondary" onclick="copyHealedCode()" style="padding:4px 10px; font-size:12px;">Copy Healed Code</button>
            </div>
            <pre class="code-view" id="final-healed-code" style="max-height:450px;">${escapeHtml(data.fixed_source)}</pre>
          </div>
        `;
      }

      replaceHealerResults(html);
    }

    function toggleAttemptCard(idx) {
      const card = document.getElementById(`attempt-card-${idx}`);
      if (card) card.classList.toggle('open');
    }

    function copyHealedCode() {
      const el = document.getElementById('final-healed-code');
      if (el) {
        navigator.clipboard.writeText(el.textContent);
        alert("Healed code copied to clipboard!");
      }
    }

    // ── Render Security Tab ──
    function renderSecurityTab(data) {
      const container = document.getElementById('security-content');
      const badge = document.getElementById('security-risk-badge');
      const health = data.security_health || {};
      const verdict = health.verdict || 'HEALTHY';
      badge.className = `badge ${verdictBadgeClass(health)}`;
      badge.textContent = `Security health: ${verdict} · Score ${health.score || 0}`;

      const findings = data.findings || [];
      const combinedHealth = data.code_health || {};
      const lineSummary = (combinedHealth.offending_lines || health.offending_lines || []).map(item =>
        `Line ${item.line}: ${(item.rules || []).join(', ')}`
      ).join(' · ') || 'none';
      const scoreSummary = `
        <div style="margin-bottom:14px;">
          <span class="badge ${verdictBadgeClass(health)}">${verdict}</span>
          <strong style="margin-left:8px;">Score ${health.score || 0}</strong>
          <p style="font-size:12px;color:var(--text-muted);margin-top:8px;">Score = 10*Critical + 6*High + 3*Medium + 1*Low</p>
          <p style="font-size:12px;color:var(--text-muted);">Offending lines (security + compile): ${escapeHtml(lineSummary)}</p>
        </div>`;
      if (findings.length === 0) {
        container.innerHTML = `
          ${scoreSummary}
          <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="var(--accent-emerald)" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
            <h3 style="color:var(--accent-emerald); font-weight:700;">No Vulnerabilities Detected</h3>
            <p>No buffer overflows, double frees, or format string flaws identified.</p>
          </div>
        `;
        return;
      }

      let html = '';
      findings.forEach(f => {
        const sev = (f.severity || 'low').toLowerCase();
        html += `
          <div class="security-finding-item ${sev}">
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
              <span class="badge ${sev === 'critical' || sev === 'high' ? 'badge-rose' : 'badge-amber'}">${f.severity}</span>
              <strong style="font-size:14px;">${escapeHtml(f.vulnerability_type || 'Security Threat')}</strong>
              ${f.line ? `<span class="badge badge-purple">Line ${f.line}</span>` : ''}
            </div>
            <p style="font-size:13px; color:var(--text-muted); margin-bottom:8px;">${escapeHtml(f.description || '')}</p>
            <div style="background:#F8FAFC; padding:8px 12px; border-radius:6px; font-size:12px;">
              <strong style="color:var(--accent-cyan);">Remediation:</strong> ${escapeHtml(f.recommendation || '')}
            </div>
          </div>
        `;
      });
      container.innerHTML = html;
    }

    // ── Render DFA Tab ──
    function renderDfaLoading() {
      fullDfaTrace = [];
      fullDfaTraceMetadata = null;
      const status = document.getElementById('dfa-status');
      status.style.color = 'var(--text-dim)';
      status.textContent = 'Analyzing source and generating DFA transitions...';
      renderDfaTableRows([]);
    }

    function renderDfaError(message) {
      fullDfaTrace = [];
      fullDfaTraceMetadata = null;
      const status = document.getElementById('dfa-status');
      status.textContent = `DFA trace unavailable: ${message}`;
      status.style.color = 'var(--accent-rose)';
      renderDfaTableRows([]);
    }

    function renderDfaTab(trace, metadata) {
      const status = document.getElementById('dfa-status');
      status.style.color = 'var(--text-dim)';
      if (!Array.isArray(trace) || !metadata) {
        renderDfaError('the Analyze response omitted trace data.');
        return;
      }
      fullDfaTrace = trace;
      fullDfaTraceMetadata = metadata;
      if (metadata.truncated) {
        status.textContent = `Showing the first ${metadata.displayed_count} of ${metadata.total_count} transitions; the API limit is ${metadata.limit}.`;
      } else if (metadata.total_count === 0) {
        status.textContent = 'Analysis completed: the source is empty, so there are no character transitions.';
      } else {
        status.textContent = `Showing all ${metadata.displayed_count} DFA transitions.`;
      }
      if (metadata.diagnostic_count) {
        const diagnostics = (metadata.diagnostics || [])
          .map(item => `L${item.line}:C${item.column} ${item.message}`)
          .join(' ');
        const remaining = metadata.diagnostics_truncated
          ? ` ${metadata.diagnostic_count - metadata.diagnostics.length} more lexer warning(s) omitted.`
          : '';
        status.textContent += ` Lexer warning(s): ${diagnostics}${remaining}`;
        status.style.color = 'var(--accent-amber)';
      }
      renderDfaTableRows(trace);
    }

    function renderDfaTableRows(trace) {
      const tbody = document.getElementById('dfa-table-body');
      if (!trace || trace.length === 0) {
        const emptyMessage = fullDfaTraceMetadata && fullDfaTraceMetadata.total_count === 0
          ? 'No character transitions: source is empty.'
          : 'No character transitions to display.';
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--text-dim);">${escapeHtml(emptyMessage)}</td></tr>`;
        return;
      }
      let html = '';
      for (let i = 0; i < trace.length; i++) {
        const item = trace[i];
        const token = String(item.token || '');
        const shortToken = token.length > 48 ? `${token.slice(0, 45)}...` : token;
        html += `
          <tr>
            <td style="color:var(--text-dim);">${item.index}</td>
            <td style="color:var(--text-dim);">L${item.line}:C${item.column}</td>
            <td><span class="table-char">${escapeHtml(item.character)}</span></td>
            <td><span class="table-state">${escapeHtml(item.before)}</span></td>
            <td title="${escapeHtml(token)}"><span class="table-state">${escapeHtml(item.token_type || item.input || '')}</span> ${escapeHtml(shortToken)}</td>
            <td><span class="table-state">${escapeHtml(item.after)}</span></td>
            <td>${escapeHtml(item.description || '')}</td>
          </tr>
        `;
      }
      tbody.innerHTML = html;
    }

    document.getElementById('dfa-filter-input').addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase();
      if (!q) {
        renderDfaTableRows(fullDfaTrace);
        return;
      }
      const filtered = fullDfaTrace.filter(t => 
        String(t.character).toLowerCase().includes(q) ||
        String(t.token || '').toLowerCase().includes(q) ||
        String(t.description || '').toLowerCase().includes(q) ||
        String(t.before).toLowerCase().includes(q) ||
        String(t.after).toLowerCase().includes(q)
      );
      renderDfaTableRows(filtered);
    });

    // ── Render AST Tab ──
    function renderAstTab(ast) {
      const container = document.getElementById('ast-container');
      const badge = document.getElementById('ast-engine-badge');
      if (ast && ast.image) {
        badge.textContent = ast.engine || (ast.approximation ? 'Structural Approximation' : 'Clang++ Native');
        container.innerHTML = `
          ${ast.approximation ? `<p style="font-size:12px; color:var(--accent-amber); margin-bottom:10px;">Note: Clang native toolchain was substituted with structural parser.</p>` : ''}
          <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:10px 0 14px; font-size:12px; color:var(--text-dim);">
            <span><span style="display:inline-block; width:12px; height:12px; border-radius:3px; background:#DCEBFF; border:1px solid #1F4E79; margin-right:6px;"></span>Declaration</span>
            <span><span style="display:inline-block; width:12px; height:12px; border-radius:50%; background:#E8F7E6; border:1px solid #2E7D32; margin-right:6px;"></span>Statement</span>
            <span><span style="display:inline-block; width:14px; height:10px; background:#FDE7D9; border:1px solid #B8631D; margin-right:6px; transform:skewX(-18deg); display:inline-block;"></span>Expression</span>
          </div>
          <img src="${ast.image}" alt="${escapeHtml(ast.engine || 'Clang Abstract Syntax Tree')}" class="ast-image">
        `;
      } else {
        badge.textContent = 'Unavailable';
        container.innerHTML = `
          <div class="empty-state">
            <p>${escapeHtml(ast ? (ast.message || 'AST rendering could not be generated.') : 'No AST data.')}</p>
          </div>
        `;
      }
    }

    // ── Render Grammar Tab ──
    function renderGrammarTab(grammar) {
      const container = document.getElementById('grammar-content');
      const badge = document.getElementById('grammar-status-badge');
      if (!grammar) {
        badge.textContent = 'N/A';
        container.innerHTML = `<p style="color:var(--text-dim);">No grammar validation.</p>`;
        return;
      }
      const status = grammar.status || 'fail';
      badge.textContent = status.toUpperCase();
      badge.className = status === 'pass' ? 'badge badge-emerald' : ['outside', 'not_applicable'].includes(status) ? 'badge badge-cyan' : 'badge badge-rose';

      container.innerHTML = `
        <div style="background:#FFFFFF; padding:16px; border-radius:var(--radius-md); border:1px solid var(--border-subtle);">
          <h4 style="font-size:14px; font-weight:700; margin-bottom:6px;">Verdict: ${escapeHtml(grammar.message || '')}</h4>
          ${grammar.line ? `<p style="font-size:13px; color:var(--accent-rose);">Reported at line ${grammar.line}</p>` : ''}
          ${grammar.rule ? `<p style="font-size:12px; color:var(--text-dim); margin-top:4px;">Rule: ${escapeHtml(grammar.rule)}</p>` : ''}
        </div>
      `;
    }

    // ── Copy Source Button ──
    document.getElementById('btn-copy-source').addEventListener('click', () => {
      if (currentSource) {
        navigator.clipboard.writeText(currentSource);
        alert("Source code copied to clipboard!");
      }
    });

    // ── Utilities ──
    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    function formatCategoryName(name) {
      if (!name) return 'Other';
      return name.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    }

    // Auto-load semicolon sample on initial page view
    window.addEventListener('DOMContentLoaded', () => {
      const sampleBtn = document.querySelector('.sample-btn[data-sample="semicolon"]');
      if (sampleBtn) sampleBtn.click();
    });
  </script>
</body>
</html>'''


class OperationTimeout(Exception):
    """Raised when a backend operation exceeds the dashboard time limit."""


def _run_with_timeout(function, timeout: int, work_dir: str):
    finished = threading.Event()
    timed_out = threading.Event()
    result = {}

    def run():
        try:
            result["value"] = function()
        except BaseException as error:
            result["error"] = error
        finally:
            finished.set()
            if timed_out.is_set():
                _remove_work_dir(work_dir)

    threading.Thread(target=run, daemon=True).start()
    if not finished.wait(timeout):
        timed_out.set()
        if finished.is_set():
            _remove_work_dir(work_dir)
        raise OperationTimeout
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _read_upload():
    uploaded = request.files.get("source")
    if uploaded is None or not uploaded.filename:
        raise ValueError("Choose a supported source file to analyze.")
    filename = Path(uploaded.filename.replace("\\", "/")).name
    if Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError("Upload a source file with a .cpp, .cc, .cxx, .py, .java, .c, or .js extension.")
    raw = uploaded.read()
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("The source file must be UTF-8 encoded text.") from error
    if "\x00" in source:
        raise ValueError("The source file contains invalid binary data.")
    if request.form.get("input_mode") == "paste" and Path(filename).suffix.lower() == ".java":
        public_class = re.search(r"\bpublic\s+(?:abstract\s+|final\s+)?class\s+([A-Za-z_$][\w$]*)", source)
        if public_class:
            filename = public_class.group(1) + ".java"
    requested_language = request.form.get("language")
    adapter = get_adapter(requested_language, filename)
    expected_language = EXTENSION_TO_LANGUAGE[Path(filename).suffix.lower()]
    if requested_language and requested_language.strip().lower() != expected_language:
        raise ValueError(f"The selected language does not match the `{Path(filename).suffix.lower()}` file extension.")
    return filename, source, adapter


def _stub_response(filename: str, source: str, adapter, action: str) -> dict:
    language = language_metadata(adapter)
    message = adapter.support_message
    if action == "fix":
        return {
            "filename": filename, "original_source": source, "fixed_source": source, "compiles": False,
            "verdict": "not_implemented", "verdict_label": "Support coming", "explanation": message,
            "manual_fix": "", "attempts": [], "behavior": {"checked": False, "note": message},
            "support_coming": True, "support_message": message, "language": language,
            "transparency": _autoheal_transparency(None, [], {"checked": False}, adapter.name),
        }
    return _include_dfa_trace({
        "filename": filename, "source": source, "compile_success": False, "errors": [], "findings": [],
        "analytics": {"total_issues": 0, "error_count": 0, "warning_count": 0, "security_count": 0,
                      "category_percentages": {}, "error_percentage": 0, "warning_percentage": 0, "health_score": 0},
        "security_health": {"verdict": "NOT IMPLEMENTED", "score": 0},
        "code_health": {"verdict": "NOT IMPLEMENTED", "line_numbers": [], "offending_lines": []},
        "risk_level": "None", "summary": message,
        "ast": {"message": message}, "grammar": {"status": "not implemented", "message": message},
        "support_coming": True, "support_message": message, "language": language,
        "transparency": {"nlp_models_used": _diagnostics_transparency(None, [], adapter.name), "compiler": _compiler_provenance(adapter.name)},
    }, source, adapter.name)


def _include_dfa_trace(result: dict, source: str, language: str) -> dict:
    """Attach the same bounded, real lexer trace contract to every analysis response."""
    trace_result = scan_dfa(source, language=language, max_transitions=DEFAULT_TRACE_LIMIT)
    result["trace"] = trace_result["transitions"]
    result["trace_metadata"] = {
        "total_count": trace_result["total_count"],
        "displayed_count": trace_result["displayed_count"],
        "truncated": trace_result["truncated"],
        "limit": trace_result["limit"],
        "diagnostics": trace_result["diagnostics"],
        "diagnostic_count": trace_result["diagnostic_count"],
        "diagnostics_truncated": trace_result["diagnostics_truncated"],
    }
    return result


def _write_source_file(file_path: Path, source: str) -> None:
    with file_path.open("w", encoding="utf-8", newline="") as source_file:
        source_file.write(source)


def _read_test_spec(filename: str):
    """Read an optional JSON sidecar whose name must match the source exactly."""
    uploaded = request.files.get("test_spec")
    if uploaded is None or not uploaded.filename:
        return None
    spec_name = Path(uploaded.filename.replace("\\", "/")).name
    expected_name = f"{filename}.tests.json"
    if spec_name != expected_name:
        raise ValueError(f"The behavioral test spec must be named `{expected_name}`.")
    try:
        spec_text = uploaded.read().decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("The behavioral test specification must be UTF-8 encoded JSON text.") from error
    if "\x00" in spec_text:
        raise ValueError("The behavioral test specification contains invalid binary data.")
    return spec_text


def _grammar_result(source: str):
    try:
        parse_grammar(source)
    except GrammarParseError as error:
        if error.outside_grammar:
            return {
                "status": "outside",
                "line": error.line,
                "message": "This source uses C++ constructs outside the toy grammar; no grammar verdict is applicable.",
            }
        return {
            "status": "fail",
            "line": error.line,
            "rule": error.rule,
            "message": str(error),
        }
    return {"status": "pass", "line": None, "message": "Valid toy grammar."}


def _analyze_file(file_path: Path, filename: str, source: str, ast_node_limit: int = 150, ast_depth_limit: int = 12, ast_expand_full: bool = False):
    compiler_output = run_cpp_compiler(str(file_path))
    errors = parse_errors(compiler_output)
    grammar = _grammar_result(source)
    classifier = get_default_classifier()
    decision_sources = []

    for error in errors:
        if error.file and str(file_path) in error.file:
            error.file = filename
        if error.raw:
            error.raw = error.raw.replace(str(file_path), filename)
        enrich_error(error)
        try:
            cat, conf, provenance = classifier.predict_with_provenance(error.message)
            decision_sources.append(provenance)
            if not getattr(error, "category", None) or error.category == "other":
                error.category = cat or "other"
                error.confidence = conf
        except Exception:
            if not getattr(error, "category", None) or error.category == "other":
                error.category = "other"
                error.confidence = 1.0

    for error in errors:
        if error.line and grammar.get("status") == "fail" and error.line == grammar.get("line"):
            error.additional_detail = grammar["message"]

    compile_success = not any(error.error_type == "error" for error in errors)
    error_data = []
    category_counts = {}
    error_count = 0
    warning_count = 0

    for error in errors:
        is_err = (error.error_type == "error")
        if is_err:
            error_count += 1
        else:
            warning_count += 1

        cat = getattr(error, "category", "other") or "other"
        category_counts[cat] = category_counts.get(cat, 0) + 1

        likely_fault_line = (
            _likely_missing_semicolon_fault_line(source, error.line, error.message)
            if is_err else None
        )
        error_data.append({
            "type": error.error_type.upper(),
            "raw": error.raw or error.message,
            "message": error.message,
            "line": error.line,
            "likely_fault_line": likely_fault_line,
            "column": getattr(error, "column", None),
            "category": cat,
            "confidence": getattr(error, "confidence", 1.0),
            "explanation": error.explanation or "The compiler reported an issue, but no rewritten explanation was available.",
            "suggestion": error.suggestion,
            "additional_detail": getattr(error, "additional_detail", None),
        })

    findings = analyze_security(str(file_path), errors)
    security_health = classify_security_health(findings)
    code_health = _combined_code_health(security_health, compile_success, error_data)
    severities = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
    risk = max((finding.severity for finding in findings), key=lambda value: severities.get(value, 0), default="None")

    total_issues = len(errors)
    cat_percentages = {}
    if total_issues > 0:
        for cat, cnt in category_counts.items():
            cat_percentages[cat] = {
                "count": cnt,
                "percentage": round((cnt / total_issues) * 100, 1),
            }

    ast_result = {
        "image": None,
        "message": None,
        "approximation": False,
        "path": None,
        "nodes_before_limits": 0,
        "nodes_displayed": 0,
        "truncation_markers": [],
    }
    ast_path = file_path.parent / f"ast_{uuid.uuid4().hex}.png"
    try:
        tree, approximation = extract_ast_tree(str(file_path), expand_full=True)
        display_tree = (
            tree
            if ast_expand_full
            else _trim_tree(tree, ast_node_limit, ast_depth_limit)
        )
        ast_result["nodes_before_limits"] = _count_ast_nodes(tree)
        ast_result["nodes_displayed"] = _count_ast_nodes(display_tree)

        def find_truncation_markers(nodes):
            markers = []
            for node in nodes:
                if node.get("type") == "...":
                    markers.append(node.get("details", "... more nodes"))
                markers.extend(find_truncation_markers(node.get("children", [])))
            return markers

        ast_result["truncation_markers"] = find_truncation_markers(display_tree)
        render_ast_png(tree, str(ast_path), max_nodes=ast_node_limit, max_depth=ast_depth_limit, expand_full=ast_expand_full)
        ast_result["image"] = "data:image/png;base64," + base64.b64encode(ast_path.read_bytes()).decode("ascii")
        ast_result["approximation"] = approximation
        ast_result["path"] = str(ast_path)
        ast_result["engine"] = (
            "Fallback parser + Graphviz" if approximation
            else "clang++ JSON AST + Graphviz"
        )
    except (OSError, RuntimeError, ImportError, ValueError) as error:
        ast_result["message"] = "AST diagram unavailable: " + str(error)

    # Compute code health score (0 - 100)
    health_penalty = (error_count * 25) + (warning_count * 8)
    if risk == "Critical":
        health_penalty += 40
    elif risk == "High":
        health_penalty += 25
    elif risk == "Medium":
        health_penalty += 15
    elif risk == "Low":
        health_penalty += 5
    health_score = max(0, min(100, 100 - health_penalty))

    return {
        "filename": filename,
        "language": language_metadata(get_adapter("cpp")),
        "source": source,
        "compile_success": compile_success,
        "compiler_message": "The compiler did not return a parseable diagnostic." if compiler_output and not errors else None,
        "errors": error_data,
        "analytics": {
            "total_issues": total_issues,
            "error_count": error_count,
            "warning_count": warning_count,
            "security_count": len(findings),
            "category_percentages": cat_percentages,
            "error_percentage": round((error_count / total_issues * 100) if total_issues else 0, 1),
            "warning_percentage": round((warning_count / total_issues * 100) if total_issues else 0, 1),
            "health_score": health_score,
        },
        "findings": [finding.to_dict() for finding in findings],
        "security_health": security_health,
        "code_health": code_health,
        "risk_level": risk,
        "summary": (
            f"{security_health['verdict']} security health; score {security_health['score']}; "
            f"{len(findings)} finding(s)."
        ),
        "ast": ast_result,
        "grammar": grammar,
        "transparency": {
            "nlp_models_used": _diagnostics_transparency(classifier, decision_sources, "cpp"),
            "compiler": _compiler_provenance("cpp"),
        },
    }


def _analyze_python_file(
    file_path: Path,
    filename: str,
    source: str,
    ast_node_limit: int = 150,
    ast_depth_limit: int = 12,
    ast_expand_full: bool = False,
    execute_runtime: bool = False,
    test_spec: str | None = None,
):
    adapter = get_adapter("python")
    findings = analyze_security(str(file_path), [])
    high_risk = any(finding.severity in {"High", "Critical"} for finding in findings)
    runtime_enabled = execute_runtime and not high_risk
    runtime_message = (
        "Runtime check completed." if runtime_enabled else
        "not executed: high-risk code" if execute_runtime and high_risk else
        "runtime check skipped"
    )
    diagnostics = adapter.check(file_path, execute_runtime=runtime_enabled)
    errors = [{
        "type": "ERROR",
        "raw": item["raw"],
        "message": item["message"],
        "line": item.get("line"),
        "likely_fault_line": None,
        "column": item.get("column"),
        "category": item["category"],
        "confidence": item.get("confidence", 1.0),
        "explanation": item["explanation"],
        "suggestion": item["suggestion"],
        "additional_detail": None,
        "exception_type": item["exception_type"],
    } for item in diagnostics]

    security_health = classify_security_health(findings)
    compile_success = not errors
    code_health = _combined_code_health(security_health, compile_success, errors)
    severity_rank = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
    risk_level = max(
        (finding.severity for finding in findings),
        key=lambda severity: severity_rank.get(severity, 0),
        default="None",
    )
    category_counts: dict[str, int] = {}
    for error in errors:
        category = error["category"]
        category_counts[category] = category_counts.get(category, 0) + 1
    category_percentages = {
        category: {
            "count": count,
            "percentage": round(count / len(errors) * 100, 1),
        }
        for category, count in category_counts.items()
    }

    try:
        ast_result = adapter.build_ast(
            file_path,
            node_limit=ast_node_limit,
            depth_limit=ast_depth_limit,
            expand_full=ast_expand_full,
        )
    except (OSError, RuntimeError, ImportError, ValueError) as error:
        app.logger.exception("Python AST rendering failed")
        ast_result = {
            "image": None,
            "message": "Python AST diagram unavailable: " + str(error),
            "engine": "Python ast + Graphviz",
            "approximation": False,
            "path": None,
            "nodes_before_limits": 0,
            "nodes_displayed": 0,
            "truncation_markers": [],
        }

    health_score = max(0, min(100, 100 - len(errors) * 25 - security_health["score"]))
    behavior = (
        adapter.run_tests(file_path, test_spec)
        if runtime_enabled and test_spec is not None else
        {"checked": False, "passed": None, "cases": [], "note": runtime_message}
    )
    return {
        "filename": filename,
        "language": language_metadata(adapter),
        "source": source,
        "compile_success": compile_success,
        "compiler_message": None,
        "errors": errors,
        "analytics": {
            "total_issues": len(errors),
            "error_count": len(errors),
            "warning_count": 0,
            "security_count": len(findings),
            "category_percentages": category_percentages,
            "error_percentage": 100.0 if errors else 0.0,
            "warning_percentage": 0.0,
            "health_score": health_score,
        },
        "findings": [finding.to_dict() for finding in findings],
        "security_health": security_health,
        "code_health": code_health,
        "risk_level": risk_level,
        "summary": (
            f"{security_health['verdict']} security health; score {security_health['score']}; "
            f"{len(findings)} finding(s)."
        ),
        "ast": ast_result,
        "behavior": behavior,
        "runtime_check": {
            "requested": execute_runtime,
            "executed": runtime_enabled,
            "message": runtime_message,
        },
        "grammar": {
            "status": "not_applicable",
            "line": None,
            "message": "The toy grammar checker applies only to C++ samples.",
        },
        "transparency": {
            "nlp_models_used": _diagnostics_transparency(None, [], "python"),
            "compiler": _compiler_provenance("python"),
            "runtime_note": runtime_message,
        },
    }


def _analyze_java_file(
    file_path: Path,
    filename: str,
    source: str,
    ast_node_limit: int = 150,
    ast_depth_limit: int = 12,
    ast_expand_full: bool = False,
    execute_runtime: bool = False,
    test_spec: str | None = None,
):
    adapter = get_adapter("java")
    findings = analyze_security(str(file_path), [])
    high_risk = any(finding.severity in {"High", "Critical"} for finding in findings)
    runtime_enabled = execute_runtime and not high_risk and adapter.has_main_method(source)
    runtime_message = (
        "Runtime check completed." if runtime_enabled else
        "not executed: high-risk code" if execute_runtime and high_risk else
        "runtime check skipped: no Java main method" if execute_runtime else
        "runtime check skipped"
    )
    diagnostics = adapter.check(file_path, execute_runtime=runtime_enabled)
    errors = [{
        "type": item.get("type", "ERROR"),
        "raw": item["raw"],
        "message": item["message"],
        "line": item.get("line"),
        "likely_fault_line": None,
        "column": item.get("column"),
        "caret": item.get("caret"),
        "category": item["category"],
        "confidence": item.get("confidence", 1.0),
        "explanation": item["explanation"],
        "suggestion": item["suggestion"],
        "additional_detail": None,
        "exception_type": item.get("exception_type", "CompilationError"),
    } for item in diagnostics]

    security_health = classify_security_health(findings)
    compile_success = not any(item["type"] == "ERROR" for item in errors)
    code_health = _combined_code_health(security_health, compile_success, errors)
    severity_rank = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
    risk_level = max(
        (finding.severity for finding in findings),
        key=lambda severity: severity_rank.get(severity, 0),
        default="None",
    )
    category_counts: dict[str, int] = {}
    for error in errors:
        category = error["category"]
        category_counts[category] = category_counts.get(category, 0) + 1
    category_percentages = {
        category: {
            "count": count,
            "percentage": round(count / len(errors) * 100, 1),
        }
        for category, count in category_counts.items()
    }

    try:
        ast_result = adapter.build_ast(
            file_path,
            node_limit=ast_node_limit,
            depth_limit=ast_depth_limit,
            expand_full=ast_expand_full,
        )
    except (OSError, RuntimeError, ImportError, ValueError) as error:
        app.logger.exception("Java AST rendering failed")
        ast_result = {
            "image": None,
            "message": "Java AST diagram unavailable: " + str(error),
            "engine": "tree-sitter-java + Graphviz (unavailable)",
            "approximation": False,
            "path": None,
            "nodes_before_limits": 0,
            "nodes_displayed": 0,
            "truncation_markers": [],
        }

    health_score = max(0, min(100, 100 - len(errors) * 25 - security_health["score"]))
    behavior = (
        adapter.run_tests(file_path, test_spec)
        if runtime_enabled and test_spec is not None else
        {"checked": False, "passed": None, "cases": [], "note": runtime_message}
    )
    return {
        "filename": filename,
        "language": language_metadata(adapter),
        "source": source,
        "compile_success": compile_success,
        "compiler_message": None,
        "errors": errors,
        "analytics": {
            "total_issues": len(errors),
            "error_count": sum(error["type"] == "ERROR" for error in errors),
            "warning_count": sum(error["type"] == "WARNING" for error in errors),
            "security_count": len(findings),
            "category_percentages": category_percentages,
            "error_percentage": round(sum(error["type"] == "ERROR" for error in errors) / len(errors) * 100, 1) if errors else 0.0,
            "warning_percentage": round(sum(error["type"] == "WARNING" for error in errors) / len(errors) * 100, 1) if errors else 0.0,
            "health_score": health_score,
        },
        "findings": [finding.to_dict() for finding in findings],
        "security_health": security_health,
        "code_health": code_health,
        "risk_level": risk_level,
        "summary": (
            f"{security_health['verdict']} security health; score {security_health['score']}; "
            f"{len(findings)} finding(s)."
        ),
        "ast": ast_result,
        "behavior": behavior,
        "runtime_check": {
            "requested": execute_runtime,
            "executed": runtime_enabled,
            "message": runtime_message,
        },
        "grammar": {
            "status": "not_applicable",
            "line": None,
            "message": "The toy grammar checker applies only to C++ samples.",
        },
        "transparency": {
            "nlp_models_used": _diagnostics_transparency(None, [], "java"),
            "compiler": _compiler_provenance("java"),
            "runtime_note": runtime_message,
        },
    }


def _fix_file(
    file_path: Path,
    original_source: str,
    *,
    execute_runtime: bool = False,
    runtime_skip_reason: str | None = None,
    runtime_requested: bool = False,
):
    """
    Run the agentic heal loop synchronously in a worker thread.
    Uses HealingAgent directly (no Qt event loop needed).
    """
    from heal_agent import HealingAgent, make_compiler
    from heal_loop import _pre_scan

    classifier = get_default_classifier()
    classify_fn = classifier.predict if hasattr(classifier, "predict") else None

    project_dir = str(file_path.parent)
    compile_fn = make_compiler(working_dir=project_dir)

    agent = HealingAgent(
        original_source,
        compile_fn=compile_fn,
        pre_scan=_pre_scan,
        max_attempts=10,
        file_path=str(file_path),
        check_behavior=execute_runtime,
        classify_fn=classify_fn,
    )

    attempts = []
    current_source = [original_source]

    def on_attempt(attempt):
        from diff_viewer import compute_diff, format_diff_html, has_changes
        diff = compute_diff(current_source[0], attempt.source_after)
        diff_html = format_diff_html(diff) if has_changes(diff) else "<i>No visible changes.</i>"
        attempts.append({
            "number":        attempt.number,
            "strategy":      attempt.strategy,
            "observed":      attempt.observed,
            "planned":       attempt.planned,
            "action":        attempt.action,
            "outcome":       attempt.outcome,
            "error":         attempt.error_before or attempt.observed,
            "before":        current_source[0],
            "after":         attempt.source_after,
            "changed":       attempt.changed,
            "compiles":      attempt.compiled,
            "lines_changed": list(attempt.lines_changed),
            "diff_html":     diff_html,
            "reasoning":     attempt.reasoning,
            "confidence":    getattr(attempt, "confidence", 1.0),
            "duration":      getattr(attempt, "duration", 0.0),
        })
        current_source[0] = attempt.source_after
        _write_source_file(file_path, attempt.source_after)

    result = agent.run(on_attempt=on_attempt)

    _write_source_file(file_path, result.source)
    fixed_source = result.source

    final_diagnostics = run_cpp_compiler(str(file_path))
    final_errors = parse_errors(final_diagnostics)
    compiles = not any(e.error_type == "error" for e in final_errors)

    if not attempts:
        if result.verdict == "already_clean":
            observed = "No blocking compiler diagnostics were found"
            planned = "Keep the source unmodified"
            action = "No changes required"
            outcome = "Program already compiles cleanly"
        else:
            observed = result.explanation or "The compiler still reports a blocking diagnostic."
            planned = "Do not guess at the intended program logic"
            action = "No automatic patch was applied"
            outcome = "Build remains blocked; manual correction is required"
        attempts.append({
            "number":        1,
            "strategy":      result.verdict,
            "observed":      observed,
            "planned":       planned,
            "action":        action,
            "outcome":       outcome,
            "error":         result.explanation or "No automatic patch was possible.",
            "before":        original_source,
            "after":         fixed_source,
            "changed":       fixed_source != original_source,
            "compiles":      compiles,
            "lines_changed": [],
            "diff_html":     "",
            "reasoning":     result.verdict_label,
            "confidence":    1.0,
            "duration":      0.0,
        })

    behavior = result.behavior.to_dict()
    if not execute_runtime:
        behavior["note"] = runtime_skip_reason or "runtime check skipped"
    runtime_check = {
        "requested": runtime_requested,
        "executed": bool(behavior["checked"]),
        "message": behavior["note"] or "Runtime check completed.",
    }
    transparency = _autoheal_transparency(classifier, attempts, behavior, "cpp")
    transparency["runtime_note"] = runtime_check["message"]
    return {
        "original_source": original_source,
        "fixed_source":    fixed_source,
        "compiles":        compiles,
        "verdict":         result.verdict,
        "verdict_label":   result.verdict_label,
        "explanation":     result.explanation,
        "manual_fix":      result.manual_fix,
        "behavior":        behavior,
        "runtime_check":   runtime_check,
        "attempts":        attempts,
        "transparency":     transparency,
    }


@app.get("/")
def index():
    tool_status = {**TOOL_STATUS, "java_compatibility": JAVA_COMPATIBILITY}
    return render_template_string(PAGE, tool_status=tool_status, languages=all_language_metadata())


@app.get("/api/evaluation")
def get_classifier_evaluation():
    with _evaluation_lock:
        running = _evaluation_state["running"]
        error = _evaluation_state["error"]
    if not EVALUATION_REPORT.is_file():
        return jsonify(available=False, running=running, error=error)
    try:
        report = json.loads(EVALUATION_REPORT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        app.logger.exception("Could not read classifier evaluation report")
        return jsonify(error=f"Could not read evaluation report: {exc}"), 500
    return jsonify(available=True, running=running, error=error, **report)


@app.post("/api/evaluation/run")
def run_classifier_evaluation():
    with _evaluation_lock:
        if _evaluation_state["running"]:
            return jsonify(running=True), 202
        _evaluation_state["running"] = True
        _evaluation_state["error"] = None
        try:
            threading.Thread(target=_run_classifier_evaluation, daemon=True).start()
        except RuntimeError as exc:
            _evaluation_state["running"] = False
            _evaluation_state["error"] = f"Could not start evaluation: {exc}"
            return jsonify(running=False, error=_evaluation_state["error"]), 500
    return jsonify(running=True), 202


@app.get("/reports/<filename>")
def get_evaluation_chart(filename: str):
    chart_paths = {
        "confusion_matrix.png": PROJECT_ROOT / "reports" / "confusion_matrix.png",
        "per_class_f1.png": PROJECT_ROOT / "reports" / "per_class_f1.png",
        "model_comparison.png": PROJECT_ROOT / "reports" / "model_comparison.png",  # <-- ADD THIS
        "security_threat_graph.png": PROJECT_ROOT / "reports" / "security_threat_graph.png", # <-- ADD THIS

    }
    chart = chart_paths.get(filename)
    if chart is None or not chart.is_file():
        return jsonify(error="Evaluation chart not found."), 404
    return send_file(chart, mimetype="image/png")


@app.get("/api/sample")
def get_sample():
    name = request.args.get("name", "semicolon")
    sample = SAMPLE_FILES.get(name, SAMPLE_FILES["semicolon"])
    return jsonify(sample)

@app.post("/api/lint")
def lint_upload():
    try:
        # We only need the filename and source
        uploaded = request.files.get("source")
        if not uploaded:
            return jsonify(error="No file"), 400
        source = uploaded.read().decode("utf-8")
        filename = uploaded.filename
        adapter = get_adapter(request.form.get("language"), filename)

        # Save to a temp file
        import tempfile, os
        from pathlib import Path
        with tempfile.TemporaryDirectory() as work_dir:
            file_path = Path(work_dir) / filename
            file_path.write_text(source, encoding="utf-8")
            
            # Run the compiler, but ONLY parse errors. No heavy analysis.
            if adapter.name == "python":
                # For python, just use py_compile
                import py_compile
                try:
                    py_compile.compile(str(file_path), doraise=True)
                    errors = []
                except py_compile.PyCompileError as e:
                    err = e.exc_value
                    errors = [{"line": err.lineno or 1, "column": err.offset or 1, "message": err.msg, "error_type": "error"}]
            elif adapter.name == "java":
                # Use the adapter's check
                raw_errors = adapter.check(file_path, execute_runtime=False)
                errors = [{"line": e.get("line") or 1, "column": e.get("column") or 1, "message": e["message"], "error_type": e["type"].lower()} for e in raw_errors]
            else:
                # C++ compile
                from compiler_runner import run_cpp_compiler
                from error_parser import parse_errors
                raw_output = run_cpp_compiler(str(file_path))
                parsed = parse_errors(raw_output)
                errors = [{"line": e.line or 1, "column": e.column or 1, "message": e.message, "error_type": e.error_type} for e in parsed]

            return jsonify({"errors": errors})
    except Exception as e:
        return jsonify(error=str(e)), 500
@app.post("/api/analyze")
def analyze_upload():
    work_dir = None
    timed_out = False
    try:
        filename, source, adapter = _read_upload()
        test_spec = _read_test_spec(filename)
        if not adapter.implemented:
            return jsonify(_stub_response(filename, source, adapter, "analyze"))
        if isinstance(adapter, JavaAdapter):
            missing_tools = [
                tool["name"] for tool in language_metadata(adapter)["tools"]
                if not tool["installed"]
            ]
            if missing_tools:
                return jsonify(
                    error=f"Java tool not installed: {', '.join(missing_tools)}.",
                    tool_status="tool not installed",
                ), 503
        execute_runtime = request.form.get("execute_runtime", "false").strip().lower() in {"1", "true", "yes", "on"}
        ast_node_limit = int(request.form.get("ast_node_limit", "150") or "150")
        ast_depth_limit = int(request.form.get("ast_depth_limit", "12") or "12")
        ast_expand_full = str(request.form.get("ast_expand_full", "false")).lower() in {"1", "true", "yes"}
        work_dir = tempfile.mkdtemp(prefix="compiler-workbench-")
        file_path = Path(work_dir) / filename
        _write_source_file(file_path, source)
        if test_spec is not None:
            Path(str(file_path) + ".tests.json").write_text(test_spec, encoding="utf-8")
        analyze_function = (
            _analyze_python_file if isinstance(adapter, PythonAdapter) else
            _analyze_java_file if isinstance(adapter, JavaAdapter) else
            _analyze_file
        )
        analyze_options = {
            "ast_node_limit": ast_node_limit,
            "ast_depth_limit": ast_depth_limit,
            "ast_expand_full": ast_expand_full,
        }
        if isinstance(adapter, (PythonAdapter, JavaAdapter)):
            analyze_options["execute_runtime"] = execute_runtime
            analyze_options["test_spec"] = test_spec
        result = _run_with_timeout(
            lambda: analyze_function(file_path, filename, source, **analyze_options),
            OPERATION_TIMEOUT_SECONDS,
            work_dir,
        )
        _include_dfa_trace(result, source, adapter.name)
        if isinstance(adapter, JavaAdapter):
            result["transparency"]["language"] = "Java"
            result["transparency"]["compiler"] = _compiler_provenance("java")
            compatibility = JAVA_COMPATIBILITY
            result["transparency"]["compile_target_message"] = compatibility.get("message")
            result["transparency"]["tool_warning"] = compatibility.get("warning")
        return jsonify(result)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except OperationTimeout:
        timed_out = True
        return jsonify(error="Analysis timed out. Try a smaller source file."), 504
    except Exception:
        app.logger.exception("Analysis request failed")
        return jsonify(error="Analysis could not be completed."), 500
    finally:
        if work_dir and not timed_out:
            _remove_work_dir(work_dir)


@app.post("/api/fix")
def attempt_auto_fix():
    work_dir = None
    timed_out = False
    try:
        filename, source, adapter = _read_upload()
        test_spec = _read_test_spec(filename)
        if not adapter.implemented:
            return jsonify(_stub_response(filename, source, adapter, "fix"))
        if isinstance(adapter, JavaAdapter):
            missing_tools = [
                tool["name"] for tool in language_metadata(adapter)["tools"]
                if not tool["installed"]
            ]
            if missing_tools:
                return jsonify(
                    error=f"Java tool not installed: {', '.join(missing_tools)}.",
                    tool_status="tool not installed",
                ), 503
        execute_runtime = request.form.get("execute_runtime", "false").strip().lower() in {"1", "true", "yes", "on"}
        work_dir = tempfile.mkdtemp(prefix="compiler-workbench-fix-")
        file_path = Path(work_dir) / filename
        _write_source_file(file_path, source)
        if test_spec is not None:
            Path(str(file_path) + ".tests.json").write_text(test_spec, encoding="utf-8")
        if isinstance(adapter, (PythonAdapter, JavaAdapter)):
            security_findings = analyze_security(str(file_path), [])
            high_risk = any(finding.severity in {"High", "Critical"} for finding in security_findings)
            has_main = not isinstance(adapter, JavaAdapter) or adapter.has_main_method(source)
            runtime_enabled = execute_runtime and not high_risk and has_main
            runtime_skip_reason = (
                "not executed: high-risk code" if execute_runtime and high_risk else
                "runtime check skipped" if not execute_runtime else
                "runtime check skipped: no Java main method" if not has_main else None
            )
            result = _run_with_timeout(
                lambda: adapter.fix_file(
                    file_path,
                    source,
                    test_spec if runtime_enabled else None,
                    execute_runtime=runtime_enabled,
                    runtime_skip_reason=runtime_skip_reason,
                    runtime_requested=execute_runtime,
                ),
                OPERATION_TIMEOUT_SECONDS,
                work_dir,
            )
            if isinstance(adapter, JavaAdapter):
                result["transparency"]["language"] = "Java"
                result["transparency"]["compiler"] = _compiler_provenance("java")
                compatibility = JAVA_COMPATIBILITY
                result["transparency"]["compile_target_message"] = compatibility.get("message")
                result["transparency"]["tool_warning"] = compatibility.get("warning")
        else:
            security_findings = analyze_security(str(file_path), [])
            high_risk = any(finding.severity in {"High", "Critical"} for finding in security_findings)
            runtime_enabled = execute_runtime and not high_risk
            runtime_skip_reason = (
                "not executed: high-risk code" if execute_runtime and high_risk else
                "runtime check skipped" if not execute_runtime else None
            )
            result = _run_with_timeout(
                lambda: _fix_file(
                    file_path,
                    source,
                    execute_runtime=runtime_enabled,
                    runtime_skip_reason=runtime_skip_reason,
                    runtime_requested=execute_runtime,
                ),
                OPERATION_TIMEOUT_SECONDS,
                work_dir,
            )
        return jsonify(result)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except OperationTimeout:
        timed_out = True
        return jsonify(error="Auto-fix timed out."), 504
    except Exception:
        app.logger.exception("Auto-fix request failed")
        return jsonify(error="Auto-fix could not be completed."), 500
    finally:
        if work_dir and not timed_out:
            _remove_work_dir(work_dir)


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="The source file exceeds the maximum 4 MB size limit."), 413


if __name__ == "__main__":
    _cleanup_stale_workdirs()
    app.run(host="127.0.0.1", port=5000, debug=False)