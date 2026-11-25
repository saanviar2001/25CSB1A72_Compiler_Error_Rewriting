"""
heal_loop.py
------------
Orchestrates the auto-healing pipeline.

Flow:
  1. Compile  → parse errors
  2. Classify each error
  3. Call auto_healer.attempt_fix() for the first fixable error
  4. Write patched source  →  show diff
  5. Recompile  →  if clean, done ✅
  6. After MAX_ATTEMPTS failures  →  emit give_up signal

Signals emitted (for the GUI):
  - attempt_started(attempt_no: int, error: dict)
  - diff_ready(attempt_no: int, diff_html: str)
  - compile_clean()
  - give_up(history: list[dict])
  - error_signal(message: str)
"""

import json
import os
import sys
import subprocess
import re
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from auto_healer import attempt_fix, UNFIXABLE_CATEGORIES
from diff_viewer import compute_diff, format_diff_html, has_changes
from healing_suggester import suggest_failed_heal
import time
try:
    from codecarbon import EmissionsTracker
    HAS_CODECARBON = True
except ImportError:
    HAS_CODECARBON = False

MAX_ATTEMPTS = 10

# ── GCC error pattern ────────────────────────────────────────────────────────
_ERR_PATTERN = re.compile(
    r"^(.+?):(\d+):(\d+):\s*(error|warning):\s*(.*)$"
)


def _compile(file_path: str) -> tuple[list[dict], str]:
    """
    Run g++ and return (error_list, raw_stderr).
    Each error dict has: file, line, column, type, message, raw.
    """
    from compiler_runner import _sanitize_input
    if not _sanitize_input(file_path):
        return [{
            "file": file_path,
            "line": 1,
            "column": 1,
            "type": "error",
            "message": "Security Error: Malicious command injection or forbidden system call detected.",
            "raw": "error: Malicious command injection or forbidden system call detected.",
            "category": "security_violation",
            "confidence": 1.0
        }], "error: Malicious code blocked"

    res = subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-fsyntax-only", file_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    errors = []
    for line in res.stderr.splitlines():
        m = _ERR_PATTERN.match(line.strip())
        if m:
            errors.append({
                "file":    m.group(1),
                "line":    int(m.group(2)),
                "column":  int(m.group(3)),
                "type":    m.group(4),
                "message": m.group(5),
                "raw":     line,
            })
    return errors, res.stderr


def _pre_scan(source: str) -> list[dict]:
    """
    Catch a small set of obvious issues before compile diagnostics.
    Returns synthetic diagnostics in the same shape as compiler errors.
    """
    errors = []
    lines = source.splitlines()

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue

        if re.search(r"\bint\b.*=\s*2147483647\b", stripped):
            errors.append({
                "file": "source",
                "line": i,
                "column": 0,
                "type": "warning",
                "message": "integer overflow: value at INT_MAX, addition will overflow",
                "raw": line,
                "category": "integer_overflow",
                "confidence": 1.0,
            })

        if re.search(r"if\s*\([^=!<>]*(?<![=!<>])=(?!=)[^)]*\)", stripped):
            if not re.search(r"if\s*\(.*==.*\)", stripped):
                errors.append({
                    "file": "source",
                    "line": i,
                    "column": 0,
                    "type": "warning",
                    "message": "assignment used as truth value in if condition",
                    "raw": line,
                    "category": "syntax_error",
                    "confidence": 1.0,
                })

        if re.search(r"/\s*0\b", stripped):
            errors.append({
                "file": "source",
                "line": i,
                "column": 0,
                "type": "warning",
                "message": "division by zero: literal 0 used as divisor",
                "raw": line,
                "category": "division_by_zero",
                "confidence": 1.0,
            })

        zero_assign = re.search(r"\bint\s+([A-Za-z_]\w*)\s*=\s*0\s*;", stripped)
        if zero_assign:
            var = zero_assign.group(1)
            guard_pattern = re.compile(rf"if\s*\(\s*{re.escape(var)}\s*==\s*0\s*\)")
            if guard_pattern.search(source):
                continue
            for j, future_line in enumerate(lines[i:], start=i + 1):
                future_stripped = future_line.strip()
                if future_stripped.startswith("//"):
                    continue
                if re.search(rf"/\s*{re.escape(var)}\b", future_line):
                    errors.append({
                        "file": "source",
                        "line": j,
                        "column": 0,
                        "type": "warning",
                        "message": f"division by zero: '{var}' is 0",
                        "raw": future_line,
                        "category": "division_by_zero",
                        "confidence": 0.9,
                    })
                    break

    return errors


def _classify_errors(errors: list[dict], classifier) -> list[dict]:
    """Attach 'category' to each error using the ML classifier."""
    for e in errors:
        if e.get("category"):
            e["confidence"] = round(float(e.get("confidence", 1.0)), 3)
            continue
        cat, conf = classifier.predict(e["message"])
        e["category"] = cat or "other"
        e["confidence"] = round(conf, 3)
    return errors


def _pick_fix_target(issues: list[dict]) -> Optional[dict]:
    """
    Prefer real errors, but allow fixable warnings such as uninitialized
    variables when there are no patchable hard errors.
    """
    for issue_type in ("error", "warning"):
        for issue in issues:
            if issue["type"] != issue_type:
                continue
            if issue.get("category") in UNFIXABLE_CATEGORIES:
                continue
            return issue
    return None


class HealWorker(QThread):
    """
    QThread that runs the full heal loop asynchronously.
    Connect to its signals to receive live updates.
    """
    attempt_started = pyqtSignal(int, dict)          # (attempt_no, error)
    diff_ready      = pyqtSignal(int, str)            # (attempt_no, diff_html)
    compile_clean   = pyqtSignal()
    give_up         = pyqtSignal(list)                # history list
    error_signal    = pyqtSignal(str)                 # fatal/internal errors
    hint_required   = pyqtSignal(int, dict, list)     # (attempt_no, error, history)
    lines_fixed     = pyqtSignal(list)                # [line_no, ...]
    telemetry_ready = pyqtSignal(list)                # list[dict]

    def __init__(self, file_path: str, classifier, hint: Optional[str] = None):
        super().__init__()
        self.file_path  = file_path
        self.classifier = classifier
        self.hint       = hint          # optional user guidance from previous round

    def run(self):
        project_dir = os.path.dirname(os.path.abspath(self.file_path))
        main_tracker = None
        if HAS_CODECARBON:
            try:
                main_tracker = EmissionsTracker(
                    project_name="self_healing_compiler",
                    measure_power_secs=1,
                    log_level="error",
                    save_to_file=True,
                    output_dir=project_dir,
                    output_file="emissions.csv"
                )
                main_tracker.start()
            except Exception:
                main_tracker = None

        try:
            telemetry = self._heal()
            self.telemetry_ready.emit(telemetry)
        except Exception as exc:
            self.error_signal.emit(str(exc))
        finally:
            if main_tracker:
                try:
                    main_tracker.stop()
                except:
                    pass

    def _attach_failure_suggestion(self, history: list[dict], error: dict, source: str) -> None:
        if not error:
            return
        error["suggestion"] = suggest_failed_heal(error, source, history)

    def _heal(self) -> list[dict]:
        from heal_agent import (
            HealingAgent,
            make_compiler,
            VERDICT_FIXED_AND_VERIFIED,
            VERDICT_COMPILES_UNVERIFIED,
            VERDICT_COMPILES_BEHAVIOR_FAILED,
            VERDICT_ALREADY_CLEAN,
        )
        from diff_viewer import compute_diff, format_diff_html, has_changes
        from error_classifier import get_default_classifier

        with open(self.file_path, "r", encoding="utf-8") as f:
            original_source = f.read()

        project_dir = os.path.dirname(os.path.abspath(self.file_path))
        compile_fn = make_compiler(working_dir=project_dir)
        classifier = self.classifier or get_default_classifier()
        classify_fn = classifier.predict if hasattr(classifier, "predict") else None

        agent = HealingAgent(
            original_source,
            compile_fn=compile_fn,
            pre_scan=_pre_scan,
            hint=self.hint,
            max_attempts=4,
            file_path=self.file_path,
            classify_fn=classify_fn,
        )

        history = []
        telemetry = []
        current_source = original_source

        def on_attempt(attempt):
            nonlocal current_source
            err_dict = {
                "file": self.file_path,
                "line": attempt.lines_changed[0] if attempt.lines_changed else 1,
                "message": attempt.observed,
                "category": attempt.strategy,
                "raw": attempt.error_before,
            }
            self.attempt_started.emit(attempt.number, err_dict)

            diff = compute_diff(current_source, attempt.source_after)
            diff_html = format_diff_html(diff) if has_changes(diff) else "<i>No visible changes.</i>"
            self.diff_ready.emit(attempt.number, diff_html)

            self.lines_fixed.emit(attempt.lines_changed)

            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write(attempt.source_after)
            current_source = attempt.source_after

            history.append({
                "attempt": attempt.number,
                "error": err_dict,
                "patch": attempt.source_after,
                "diff_html": diff_html,
            })
            telemetry.append({
                "attempt": attempt.number,
                "category": attempt.strategy,
                "success": attempt.compiled,
                "duration": round(attempt.duration, 3),
                "emissions": 0.0,
            })

        result = agent.run(on_attempt=on_attempt)

        if result.verdict in (VERDICT_FIXED_AND_VERIFIED, VERDICT_COMPILES_UNVERIFIED,
                              VERDICT_COMPILES_BEHAVIOR_FAILED, VERDICT_ALREADY_CLEAN):
            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write(result.source)
            self.compile_clean.emit()
        else:
            last_err = {
                "file": self.file_path,
                "line": 1,
                "message": result.explanation,
                "category": "other",
                "suggestion": result.manual_fix,
            }
            history.append({
                "attempt": len(result.attempts),
                "error": last_err,
                "patch": None,
                "reason": "give_up",
            })
            self.give_up.emit(history)

        return telemetry
