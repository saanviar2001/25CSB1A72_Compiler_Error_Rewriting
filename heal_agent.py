"""
heal_agent.py
-------------
The agentic auto-healing loop:  OBSERVE -> PLAN -> ACT -> OBSERVE -> REPLAN.

WHY A LOOP AND NOT A SINGLE TRY
-------------------------------
The old healer did "compile once, run one repair rule, show a diff, stop".
That cannot work in general, because a C++ build is a *sequence* of surprises:
repairing the first error usually uncovers a second one, and the second one is
frequently a different category of mistake entirely.  A single shot therefore
either stopped far too early, or replayed a rule that had already failed and
showed the student a diff that changed nothing.

This module turns that into a real agent loop with four separate stages:

    OBSERVE   compile the current source (`g++ -std=c++17 -Wall -fsyntax-only`)
              and merge in the pre-emptive source scan, then read which
              diagnostics are actually blocking the build.

    PLAN      pick ONE diagnostic and ONE strategy for it, using the error's
              category, its exact wording, the source context, the user's hint,
              and every strategy already tried for this same error.  A plan
              always carries a plain-English reason, so the UI can explain the
              decision instead of looking like a black box.

    ACT       apply exactly that one strategy (one Patch from auto_healer) and
              record what it changed.

    OBSERVE   recompile and compare the new diagnostics against the old ones.

    REPLAN    the *new* diagnostics decide the next plan.  A strategy that did
              not make the error go away is added to an exclusion set keyed to
              that error, so the next plan is forced to be genuinely different.

HONEST STOPPING
---------------
The loop stops for one of a small number of honest reasons, mirrored by the
VERDICT_* constants below, and the loop never invents progress:

  * an attempt is only recorded when the source really changed, so an empty
    diff can never be presented as a fix;
  * a clean compile is NOT the same as a correct program - if no test data is
    available (or the binary cannot be built in this environment) the verdict
    says "compiles but not behaviourally verified";
  * when the strategy pool is empty the verdict is "not_fixable" and the result
    explains what *kind* of error this is and what a human should look at.

This file is deliberately verbose in its comments: it is meant to be read by
the next student, not just executed.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from auto_healer import attempt_fix_detailed, UNFIXABLE_CATEGORIES
from healing_suggester import suggest_failed_heal

# ── Verdicts ─────────────────────────────────────────────────────────────────
# These strings are part of the UI contract (webapp.py / gui.py show them), so
# they are named once here rather than spelled out in three places.
VERDICT_ALREADY_CLEAN = "already_clean"
VERDICT_FIXED_AND_VERIFIED = "fixed_and_verified"
VERDICT_COMPILES_UNVERIFIED = "compiles_not_behaviorally_verified"
VERDICT_COMPILES_BEHAVIOR_FAILED = "compiles_but_behavioral_verification_failed"
VERDICT_NOT_FIXABLE = "not_fixable"

VERDICT_LABELS = {
    VERDICT_ALREADY_CLEAN: "Nothing to fix - the code already compiles",
    VERDICT_FIXED_AND_VERIFIED: "Fixed and verified",
    VERDICT_COMPILES_UNVERIFIED: "Compiles but not behaviorally verified",
    VERDICT_COMPILES_BEHAVIOR_FAILED: "Compiles but fails behavioral verification",
    VERDICT_NOT_FIXABLE: "Could not be automatically fixed - requires manual correction",
}

MAX_ATTEMPTS = 4          # the user-facing promise: never thrash for longer

# How a free-text hint maps onto repair strategies.  Keeping the mapping as
# data (rather than a chain of if-statements) makes it easy to extend, and it
# is what turns the "Give me a hint" text box into real planner input.
_HINT_STRATEGY_HINTS = (
    (("semicolon", ";"), ("missing_semicolon",)),
    (("include", "header", "iostream", "vector", "string", "library"),
     ("missing_include_rule",)),
    (("brace", "bracket", "closing"), ("missing_closing_brace",)),
    (("comparison", "==", "assign"), ("assignment_in_condition",)),
    (("initial", "uninitial", "garbage"), ("uninitialized_memory_rule",)),
    (("declare", "undeclared", "definition"), ("undeclared_variable_rule",)),
    (("return",), ("return_type_error_rule",)),
)

# Warnings that are really bugs in disguise.  `-Wall` reports them as warnings,
# so the compiler's exit code alone would call the program healthy; treating
# them as blocking is what lets the agent catch `if (x = 5)` on a plain int.
DANGEROUS_WARNING_RE = (
    "assignment used as truth value",
    "used uninitialized",
    "may be used uninitialized",
    "array subscript is out of bounds",
    "division by zero",
    "infinite recursion",
    "comparison is always",
)


def _is_dangerous(diagnostic: dict) -> bool:
    lowered = diagnostic.get("message", "").lower()
    return any(phrase in lowered for phrase in DANGEROUS_WARNING_RE)

# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class Observation:
    """Everything the agent knows about the build at one point in time."""
    diagnostics: list = field(default_factory=list)   # compiler + pre-scan, merged
    raw: str = ""                                     # untouched compiler output
    source: str = ""

    @property
    def blocking(self) -> list:
        """Hard errors, plus warnings that are certain bugs."""
        return [d for d in self.diagnostics
                if d.get("type") == "error" or _is_dangerous(d)]

    @property
    def clean(self) -> bool:
        return not self.blocking

    @staticmethod
    def key_for(diagnostic: Optional[dict]) -> str:
        """
        A stable identity for an error so "the same error came back" can be
        detected even when the wording shifts slightly.  Line + category +
        wording, whitespace-normalised.  (The old loop compared the raw message
        only, so a fix that moved the error to the next line looked identical.)
        """
        if not diagnostic:
            return ""
        message = " ".join(diagnostic.get("message", "").lower().split())
        return f"{diagnostic.get('line')}|{diagnostic.get('category', 'other')}|{message}"

    @property
    def key(self) -> str:
        return self.key_for(self.blocking[0] if self.blocking else None)

    @staticmethod
    def headline_for(diagnostic: Optional[dict]) -> str:
        if not diagnostic:
            return "no blocking diagnostic"
        where = f"line {diagnostic.get('line', '?')}"
        return f"{where}: {diagnostic.get('type', 'error')}: {diagnostic.get('message', '')}".strip()

    @property
    def headline(self) -> str:
        return self.headline_for(self.blocking[0] if self.blocking else None)


@dataclass
class Plan:
    """What the agent decided to do next, and - just as important - why."""
    target: dict
    strategy: str
    reason: str
    confidence: float = 0.0

    @property
    def target_headline(self) -> str:
        return Observation.headline_for(self.target)

@dataclass
class Attempt:
    """One full round of the loop.  This is what the UI renders as a step."""
    number: int
    observed: str                      # what the compiler said beforehand
    planned: str                       # plain-English reason for the strategy
    strategy: str = ""
    action: str = ""                   # what was actually changed, with a line
    outcome: str = ""                  # what happened afterwards
    error_before: str = ""
    error_after: str = ""
    compiled: bool = False
    changed: bool = False
    lines_changed: list = field(default_factory=list)
    diff: str = ""
    confidence: float = 0.0
    duration: float = 0.0
    source_after: str = ""

    @property
    def reasoning(self) -> str:
        """The sentence the UI shows as the step's body text."""
        if not self.changed:
            return f"Attempt {self.number}: {self.observed} - {self.outcome}"
        return (f"Attempt {self.number}: {self.observed}; {self.planned}. "
                f"{self.action}. {self.outcome}")

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "strategy": self.strategy,
            "observed": self.observed,
            "planned": self.planned,
            "action": self.action,
            "outcome": self.outcome,
            "error_before": self.error_before,
            "error_after": self.error_after,
            "compiled": self.compiled,
            "changed": self.changed,
            "lines_changed": list(self.lines_changed),
            "diff": self.diff,
            "confidence": round(self.confidence, 3),
            "duration": round(self.duration, 3),
            "reasoning": self.reasoning,
        }


@dataclass
class BehaviorReport:
    """Did we get to run the program, and did it behave as expected?"""
    checked: bool = False
    passed: Optional[bool] = None
    note: str = ""
    cases: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "passed": self.passed,
            "note": self.note,
            "cases": list(self.cases),
        }


@dataclass
class HealingResult:
    """The agent's final answer - the only object callers need to keep."""
    verdict: str
    original: str = ""
    source: str = ""
    attempts: list = field(default_factory=list)
    explanation: str = ""
    behavior: BehaviorReport = field(default_factory=BehaviorReport)
    manual_fix: str = ""

    @property
    def verdict_label(self) -> str:
        return VERDICT_LABELS.get(self.verdict, self.verdict)

    @property
    def changed(self) -> bool:
        return bool(self.source) and self.source != self.original

    @property
    def lines_fixed(self) -> list:
        seen: list = []
        for attempt in self.attempts:
            for line in attempt.lines_changed:
                if line not in seen:
                    seen.append(line)
        return sorted(seen)

    def diff(self) -> str:
        return "\n".join(difflib.unified_diff(
            self.original.splitlines(), self.source.splitlines(),
            fromfile="before", tofile="after", lineterm="", n=2))

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "verdict_label": self.verdict_label,
            "changed": self.changed,
            "source": self.source,
            "diff": self.diff(),
            "lines_fixed": self.lines_fixed,
            "explanation": self.explanation,
            "manual_fix": self.manual_fix,
            "behavior": self.behavior.to_dict(),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }

# ── The agent ────────────────────────────────────────────────────────────────

class HealingAgent:
    """
    A small, honest, bounded agent that repairs C++ by compiling rather than by
    guessing blindly.

    Usage
    -----
        agent = HealingAgent(source, compile_fn=my_compile, pre_scan=my_scan,
                             hint="maybe the header is missing")
        result = agent.run(on_attempt=lambda attempt: print(attempt.reasoning))
        print(result.verdict_label, result.diff())

    `compile_fn` is injected instead of hard-coding g++ here, for two reasons:
    the loop becomes unit-testable with a scripted compiler (no 20-second
    machine-learning / compiler start-up per case), and heal_loop stays the only
    place that knows how this machine invokes g++.

        compile_fn(source) -> (list_of_diagnostics, raw_stderr_text)

    The loop body is intentionally written as four named steps so a reader can
    point at a line and say "that's the OBSERVE step".
    """

    def __init__(self, source: str, compile_fn: Callable[[str], tuple],
                 pre_scan: Optional[Callable[[str], list]] = None,
                 hint: Optional[str] = None,
                 max_attempts: int = MAX_ATTEMPTS,
                 file_path: Optional[str] = None,
                 check_behavior: bool = True,
                 classify_fn: Optional[Callable[[str], tuple]] = None):
        self.original = source
        self.source = source
        self.compile_fn = compile_fn
        self.pre_scan = pre_scan
        self.classify_fn = classify_fn     # message -> (category, confidence)
        self.hint = (hint or "").strip()
        self.max_attempts = max(1, int(max_attempts))
        self.file_path = file_path
        self.check_behavior = check_behavior
        # error key -> the strategies already spent on it (the REPLAN memory)
        self.tried: dict[str, set] = {}
        self.attempts: list = []

    # ── the loop ────────────────────────────────────────────────────────────
    def run(self, on_attempt: Optional[Callable] = None) -> HealingResult:
        """
        Run plan -> act -> observe -> replan until the build is clean, the
        strategy pool runs dry, or MAX_ATTEMPTS rounds have been spent.
        """
        # ── OBSERVE (round 0) ───────────────────────────────────────────────
        observation = self._observe(self.source)
        if observation.clean:
            # Nothing to do, and saying so is better than showing a fake diff.
            behavior = self._check_behavior(self.source) if self.check_behavior else BehaviorReport()
            explanation = "The code already compiles cleanly, so no patch was attempted and no diff is shown."
            if behavior.checked and behavior.passed:
                explanation += " Its supplied behavioral tests also passed."
            elif behavior.checked and behavior.passed is False:
                explanation += " " + behavior.note
            return HealingResult(
                verdict=VERDICT_ALREADY_CLEAN, original=self.original,
                source=self.source, behavior=behavior, explanation=explanation,
            )

        for number in range(1, self.max_attempts + 1):
            target = self._pick_target(observation)
            if target is None:
                # Nothing left that this tool will patch: either no rule ever
                # existed for these diagnostics, or the ones that did were spent.
                return self._give_up(
                    observation, None, self._no_target_reason())

            # ── PLAN ────────────────────────────────────────────────────────
            plan, patch = self._plan(target, observation)
            if plan is None:
                # PLAN found no strategy for this error.  Two very different
                # situations reach this line, and they must NOT share one
                # sentence.  That conflation is exactly what produced the old
                # "Stopped after 0 attempt(s): every strategy ... already been
                # tried" contradiction - a true count (0) beside a false claim
                # (that strategies had been tried):
                #   * no rule exists for this error type at all, so nothing was
                #     ever attempted and the count is genuinely zero;
                #   * rules were attempted for this error, every one is now
                #     excluded, and we must report the real number and names.
                attempted = self._strategies_attempted(target)
                if attempted:
                    why = (f"Tried {len(attempted)} strategies "
                           f"({', '.join(attempted)}) — none of them resolved the "
                           f"error because the compiler still reports it")
                else:
                    why = (f"No repair strategy exists for this error type "
                           f"({self._describe_error_type(target)}) — this requires "
                           f"knowing your intended logic")
                return self._give_up(observation, target, why)

            # ── ACT ─────────────────────────────────────────────────────────
            started = time.time()
            attempt = self._act(number, plan, patch, observation)

            # ── OBSERVE again: did the patch actually help? ─────────────────
            next_observation = self._observe(patch.source)
            attempt.compiled = next_observation.clean
            attempt.error_after = (next_observation.headline
                                   if not next_observation.clean
                                   else "the build is now free of blocking diagnostics")

            # ── REPLAN: the new diagnostics decide what happens next ────────
            same_error = (Observation.key_for(next_observation.blocking[0]
                                              if next_observation.blocking else None)
                          == Observation.key_for(target))
            if same_error:
                # The patch changed text but not the compiler's mind: retire
                # this strategy for this error so the next plan differs.
                self.tried.setdefault(Observation.key_for(target), set()).add(plan.strategy)
                attempt.outcome = (f"Still blocked - the compiler repeats the same "
                                   f"error, so '{plan.strategy}' is ruled out")
            elif next_observation.clean:
                attempt.outcome = "The error is gone and the build is clean"
            else:
                attempt.outcome = ("The error is gone, but the compiler now reports "
                                   "a different one - replanning for that")
            attempt.duration = time.time() - started
            attempt.diff = self._unified_diff(observation.source, patch.source)

            # An attempt is only ever recorded when text really changed, so the
            # UI can never show an empty diff as if something had happened.
            if attempt.changed:
                self.attempts.append(attempt)
                if on_attempt:
                    on_attempt(attempt)

            if next_observation.clean:
                return self._finish_clean(patch.source)

            self.source = patch.source
            observation = next_observation

        attempted = self._strategies_attempted()
        reason = (f"Reached the {self.max_attempts}-attempt budget after trying "
                  f"{len(attempted)} strategies ({', '.join(attempted)}) without "
                  f"reaching a clean build" if attempted
                  else f"Reached the {self.max_attempts}-attempt budget without "
                       f"reaching a clean build")
        return self._give_up(observation, self._pick_target(observation), reason)

    # ── OBSERVE ─────────────────────────────────────────────────────────────
    def _observe(self, source: str) -> Observation:
        """
        OBSERVE step: find out what is actually wrong right now.

        Three sources of truth are merged:

          1. the compiler (`-fsyntax-only`), which is authoritative;
          2. the pre-emptive source scan, which catches bugs the compiler only
             whispers about (`if (x = 5)` is a *warning*, not an error, because
             it is legal C++);
          3. the ML classifier, which gives every diagnostic a category - the
             same error_classifier.py the rest of the project uses.
        """
        diagnostics, raw = self.compile_fn(source)

        merged = list(self.pre_scan(source)) if self.pre_scan else []
        merged += [dict(diagnostic) for diagnostic in diagnostics]

        # classify anything the compiler produced (the pre-scan sets its own)
        if self.classify_fn:
            for diagnostic in merged:
                if diagnostic.get("category"):
                    continue
                try:
                    category, confidence = self.classify_fn(diagnostic.get("message", ""))
                except Exception:
                    category, confidence = "other", 0.0
                diagnostic["category"] = category or "other"
                diagnostic["confidence"] = round(float(confidence), 3)

        # GCC repeats the same message several times; identical rows would make
        # the UI look like it made progress it did not make.
        unique, signatures = [], set()
        for diagnostic in merged:
            signature = (diagnostic.get("line"), diagnostic.get("type"),
                         diagnostic.get("message"))
            if signature in signatures:
                continue
            signatures.add(signature)
            unique.append(diagnostic)

        return Observation(diagnostics=unique, raw=raw, source=source)

    # ── PLAN ────────────────────────────────────────────────────────────────
    def _pick_target(self, observation: Observation) -> Optional[dict]:
        """
        PLAN, step 1 of 2: choose WHICH diagnostic to attack.

        Hard errors first (they stop the build), then warnings that are certain
        bugs.  Categories the tool is honest about not being able to patch
        (linker problems, private-member access, pure style warnings) are never
        selected - saying "I cannot fix this" beats pretending.
        """
        for wanted_type in ("error", "warning"):
            for diagnostic in observation.blocking:
                if diagnostic.get("type") != wanted_type:
                    continue
                if diagnostic.get("category") in UNFIXABLE_CATEGORIES:
                    continue
                return diagnostic
        return None

        # ── PLAN ────────────────────────────────────────────────────────────────
    def _plan(self, target: dict, observation: Observation):
        """
        PLAN, step 2 of 2: choose WHICH strategy to apply to that diagnostic.

        This is what makes the loop agentic instead of blind:

        * `exclude` holds every strategy already spent on THIS error, so the
          rule that just failed can never be selected again;
        * the user's hint is tried first (`only=`), so guidance such as "the
          header is missing" is actually acted on instead of being stored and
          ignored;
        * the plan carries a plain-English reason, which is what the UI shows.
        """
        error_key = Observation.key_for(target)
        exclude = set(self.tried.get(error_key, ()))
        hint_strategies = self._hint_strategies()

        patch = None
        if hint_strategies:
            patch = attempt_fix_detailed(observation.source, target,
                                         exclude=exclude, only=hint_strategies)
        if patch is None:
            patch = attempt_fix_detailed(observation.source, target, exclude=exclude)
        if patch is None:
            return None, None

        reasons = [patch.rationale]
        hint_note = self._hint_note(patch.strategy, hint_strategies)
        if hint_note:
            reasons.append(hint_note)
        if exclude:
            reasons.append("earlier fix(es) already ruled out for this error: "
                           + ", ".join(sorted(exclude)))

        plan = Plan(target=target, strategy=patch.strategy,
                    reason=". ".join(reasons), confidence=patch.confidence)
        return plan, patch

    def _hint_strategies(self) -> set:
        """
        Turn the free-text hint from the "Give me a hint" dialog into repair
        strategies.  Without this the hint was dead weight: it was passed into
        the worker and then only appended to a log the user never saw.
        """
        if not self.hint:
            return set()
        lowered = self.hint.lower()
        found = set()
        for needles, strategies in _HINT_STRATEGY_HINTS:
            if any(needle in lowered for needle in needles):
                found.update(strategies)
        return found

    def _hint_note(self, strategy: str, hint_strategies: set) -> str:
        """Explain, in one sentence, how the hint did or did not influence the plan."""
        if not self.hint:
            return ""
        if not hint_strategies:
            return (f'your hint "{self.hint}" was recorded, but it does not name a '
                    f"repair this tool knows how to apply")
        if strategy in hint_strategies:
            return f'this follows your hint "{self.hint}"'
        return (f'your hint "{self.hint}" pointed at {sorted(hint_strategies)}, but '
                f"that repair does not apply to this compiler message, so the "
                f"closest matching rule was used instead")

        # ── ACT ─────────────────────────────────────────────────────────────────
    def _act(self, number: int, plan: Plan, patch, observation: Observation) -> Attempt:
        """
        ACT step: perform the single edit the plan chose, and describe it.

        The line number comes from the Patch (the first line that really
        differs), not from the compiler's reported line - GCC reports the token
        *after* the fault, which is why the semicolon case points at line 5
        while the statement needing the ';' is line 4.
        """
        before_lines = observation.source.splitlines()
        after_lines = patch.source.splitlines()
        changed = self._changed_lines(before_lines, after_lines)

        if len(after_lines) > len(before_lines):
            action = (f"inserted {after_lines[patch.line_no - 1].strip()!r} "
                      f"at line {patch.line_no}")
        elif len(after_lines) < len(before_lines):
            action = (f"removed {before_lines[patch.line_no - 1].strip()!r} "
                      f"from line {patch.line_no}")
        elif patch.line_no:
            action = (f"rewrote line {patch.line_no}: "
                      f"{before_lines[patch.line_no - 1].strip()!r} -> "
                      f"{after_lines[patch.line_no - 1].strip()!r}")
        else:
            action = f"applied the '{plan.strategy}' rule"

        return Attempt(
            number=number,
            observed=Observation.headline_for(plan.target),
            planned=plan.reason,
            strategy=plan.strategy,
            action=action,
            error_before=Observation.headline_for(plan.target),
            changed=patch.source != observation.source,
            lines_changed=changed,
            confidence=plan.confidence,
            source_after=patch.source,
        )

    @staticmethod
    def _changed_lines(before_lines: list, after_lines: list) -> list:
        """1-based numbers of every line that differs between the two versions."""
        numbers = [index for index, (old, new)
                   in enumerate(zip(before_lines, after_lines), start=1)
                   if old != new]
        if len(after_lines) > len(before_lines):
            numbers += list(range(len(before_lines) + 1, len(after_lines) + 1))
        return numbers

    @staticmethod
    def _unified_diff(before: str, after: str) -> str:
        """A small unified diff (one line of context) for the UI to render."""
        return "\n".join(difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile="before", tofile="after", lineterm="", n=1))

        # ── verification & honest reporting ─────────────────────────────────────
    def _finish_clean(self, source: str) -> HealingResult:
        """
        The build is clean.  Now be precise about what that proves.

        "It compiles" and "it is correct" are different claims, so the verdict
        distinguishes them: a behavioural pass is required for
        VERDICT_FIXED_AND_VERIFIED, and anything less says so out loud.
        """
        behavior = self._check_behavior(source) if self.check_behavior else BehaviorReport()

        if behavior.checked and behavior.passed is False:
            return HealingResult(
                verdict=VERDICT_COMPILES_BEHAVIOR_FAILED, original=self.original, source=source,
                attempts=list(self.attempts), behavior=behavior,
                explanation=(behavior.note + " The code now compiles, but compiling "
                             "is not the same as being correct; this needs a semantic "
                             "human-authored fix."))

        if behavior.passed:
            verdict = VERDICT_FIXED_AND_VERIFIED
            explanation = ("All blocking diagnostics are gone and the program's "
                           "output matched the expected result.")
        else:
            verdict = VERDICT_COMPILES_UNVERIFIED
            explanation = ("Every blocking diagnostic is gone, so the code now "
                           "compiles cleanly. " + (behavior.note
                           or " No test data was available, so this is NOT a claim "
                              "that the program's logic is correct."))
        # A mechanical conversion fix makes the program compile; it does NOT
        # decide whether the conversion is what the author meant, so say so.
        caveat = self._conversion_caveat()
        if caveat:
            explanation = f"{explanation.strip()} {caveat}"
        return HealingResult(verdict=verdict, original=self.original, source=source,
                             attempts=list(self.attempts),
                             explanation=explanation.strip(), behavior=behavior)

    def _check_behavior(self, source: str) -> BehaviorReport:
        """
        Build and run the program against `<source filename>.tests.json`.

        The sidecar accepts either a list or `{"tests": [...]}`.  Each test must
        provide string `stdin` and `expected_stdout` values.  A test process gets
        at most five seconds, preventing a hanging program from blocking the
        heal operation while keeping the report specific about which case failed.
        """
        if not self.file_path or not os.path.exists(self.file_path):
            return BehaviorReport(checked=False, note=(
                "No source file on disk, so the program could not be run; "
                "behaviour is unverified."))

        tests_path = self.file_path + ".tests.json"
        if not os.path.exists(tests_path):
            return BehaviorReport(checked=False, note=(
                f"No test specification exists at `{os.path.basename(tests_path)}`, "
                "so behavior was not verified."))

        try:
            with open(tests_path, "r", encoding="utf-8") as handle:
                test_document = json.load(handle)
            tests = test_document.get("tests") if isinstance(test_document, dict) else test_document
            if not isinstance(tests, list) or not tests:
                raise ValueError("it must contain a non-empty test list")
            for number, test in enumerate(tests, start=1):
                if (not isinstance(test, dict)
                        or not isinstance(test.get("stdin", ""), str)
                        or not isinstance(test.get("expected_stdout"), str)):
                    raise ValueError(f"test {number} must provide string stdin and expected_stdout values")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return BehaviorReport(checked=False, note=(
                f"The test specification `{os.path.basename(tests_path)}` is invalid ({exc}), "
                "so behavior was not verified."))

        try:
            # Web and desktop callbacks already persist each patch, but direct
            # HealingAgent users do not have to.  Verify this exact candidate,
            # never a stale pre-heal file left on disk by another integration.
            with open(self.file_path, "w", encoding="utf-8") as handle:
                handle.write(source)
            with tempfile.TemporaryDirectory() as workdir:
                executable = os.path.join(workdir, "healed_program.exe")
                build = subprocess.run(
                    ["g++", "-std=c++17", "-Wall", self.file_path, "-o", executable],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, timeout=120)
                if build.returncode != 0:
                    # Some Windows teaching setups have a broken C++ runtime
                    # import archive even though the C runtime remains usable.
                    # Keep g++ as the primary build, but let C-ABI-only C++
                    # programs (for example, scanf/printf exercises) execute
                    # through gcc's C++ frontend before giving up honestly.
                    fallback = subprocess.run(
                        ["gcc", "-x", "c++", "-std=c++17", self.file_path, "-o", executable],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, timeout=120)
                    if fallback.returncode != 0:
                        first = next((line.strip() for line in build.stderr.splitlines()
                                      if "error" in line.lower()), "the g++ build failed")
                        return BehaviorReport(checked=False, note=(
                            "The program could not be built in this environment "
                            f"({first}), so its behaviour is unverified."))
                cases = []
                for number, test in enumerate(tests, start=1):
                    try:
                        run = subprocess.run(
                            [executable], input=test["stdin"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=5)
                    except subprocess.TimeoutExpired:
                        cases.append({
                            "number": number, "passed": False, "status": "timeout",
                            "stdin": test["stdin"], "expected_stdout": test["expected_stdout"],
                            "actual_stdout": "", "note": "Timed out after 5 seconds.",
                        })
                        continue
                    passed = run.returncode == 0 and run.stdout == test["expected_stdout"]
                    note = ("Output matched exactly." if passed else
                            (f"Exited with status {run.returncode}." if run.returncode else
                             "Output did not match exactly."))
                    cases.append({
                        "number": number, "passed": passed,
                        "status": "passed" if passed else "failed",
                        "stdin": test["stdin"], "expected_stdout": test["expected_stdout"],
                        "actual_stdout": run.stdout, "stderr": run.stderr, "note": note,
                    })
        except (OSError, subprocess.SubprocessError) as exc:
            return BehaviorReport(checked=False, note=(
                f"The program could not be built or run here ({exc}); behaviour is "
                "unverified."))

        failures = [case for case in cases if not case["passed"]]
        if not failures:
            return BehaviorReport(checked=True, passed=True, cases=cases,
                                  note=f"All {len(cases)} behavioral test case(s) passed.")
        failed_numbers = ", ".join(str(case["number"]) for case in failures)
        return BehaviorReport(checked=True, passed=False, cases=cases, note=(
            f"Behavioral test case(s) {failed_numbers} failed after the program compiled. "
            "This is a logic error requiring semantic understanding beyond pattern-based repair."))

    def _give_up(self, observation: Observation, target: Optional[dict],
                 why: str) -> HealingResult:
        """
        Stop honestly rather than showing a fake or empty diff.

        The result names what is still broken, why the agent stopped, the KIND
        of problem it is, and passes on the manual suggestion from
        healing_suggester so the student is not left stuck.
        """
        manual = ""
        if target:
            try:
                manual = suggest_failed_heal(target, observation.source, self._history())
            except Exception:
                manual = ""
        # The count is no longer glued on as a prefix that can contradict the
        # sentence: every caller now supplies a `why` that already states the
        # honest number (or explicitly says no strategy exists), so the message
        # can never read "0 attempt(s): every strategy ... already been tried".
        explanation = (f"{why.rstrip().rstrip('.')}. "
                       f"The build is still blocked by {observation.headline}. "
                       f"{self._difficulty_note(target)}")
        return HealingResult(verdict=VERDICT_NOT_FIXABLE, original=self.original,
                             source=self.source, attempts=list(self.attempts),
                             explanation=explanation, manual_fix=manual)

    def _history(self) -> list:
        """The history shape healing_suggester expects for its advice."""
        return [{"attempt": attempt.number,
                 "error": {"message": attempt.error_before,
                           "category": attempt.strategy},
                 "patch": attempt.source_after}
                for attempt in self.attempts]

    # ── honest count + description helpers (used by the give-up messages) ───
    def _strategies_attempted(self, target: Optional[dict] = None) -> list:
        """
        The strategy ids really spent on this error - or, when no target is
        given, every strategy spent anywhere in this run - as a sorted,
        de-duplicated list.

        This is the single source of truth for the count quoted in a give-up
        message, so the number can never contradict the sentence around it.
        """
        if target is not None:
            return sorted(self.tried.get(Observation.key_for(target), ()))
        return sorted({attempt.strategy for attempt in self.attempts
                       if attempt.strategy})

    def _no_target_reason(self) -> str:
        """Honest sentence for 'nothing left here that this tool will patch'."""
        attempted = self._strategies_attempted()
        if attempted:
            return (f"Tried {len(attempted)} strategies "
                    f"({', '.join(attempted)}) — the diagnostics that remain are "
                    f"ones this tool deliberately does not auto-patch (linker "
                    f"problems, private member access, or pure style warnings)")
        return ("No repair strategy exists for these diagnostics — they are ones "
                "this tool deliberately does not auto-patch (linker problems, "
                "private member access, or pure style warnings)")

    def _conversion_caveat(self) -> str:
        """
        Honest note for a mechanical type-conversion fix: the tool made the
        code compile, but it did not decide whether the conversion is what the
        author intended.  Empty when no conversion strategy was applied.
        """
        for attempt in reversed(self.attempts):
            wrapper = _CONVERSION_STRATEGIES.get(attempt.strategy)
            if wrapper:
                return (f"Applied {wrapper} — this conversion error is resolved. "
                        f"Verify this matches your intended logic, as the correct "
                        f"fix depends on what the program is meant to do.")
        return ""

    @staticmethod
    def _describe_error_type(target: Optional[dict]) -> str:
        """
        A short, plain-English name for the KIND of error, used when no rule
        exists at all (e.g. "int-to-string conversion").  It never claims a fix
        is possible; it only describes what the tool cannot guess.
        """
        if not target:
            return "this diagnostic"
        message = target.get("message", "")
        lowered = message.lower()

        # int <-> std::string conversions: GCC words these several ways, e.g.
        # "conversion from 'int' to non-scalar type 'std::string' ... requested".
        conversion = None
        for pattern in (
            r"(?:cannot|invalid)\s+conversion\s+from\s+'([^']+)'\s+to\s+(?:non-scalar\s+type\s+)?'([^']+)'",
            r"conversion\s+from\s+'([^']+)'\s+to\s+(?:non-scalar\s+type\s+)?'([^']+)'",
        ):
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                conversion = match
                break
        if conversion:
            source_type, dest_type = conversion.group(1), conversion.group(2)
            source_type = "string" if "string" in source_type.lower() else source_type
            dest_type = "string" if "string" in dest_type.lower() else dest_type
            return f"{source_type}-to-{dest_type} conversion"

        if ("was not declared in this scope" in lowered
                or "has not been declared" in lowered
                or "undeclared" in lowered):
            symbol = re.search(r"'([^']+)'", message)
            name = f" '{symbol.group(1)}'" if symbol else ""
            return f"undeclared identifier{name}"

        if ("break statement not within" in lowered
                or "continue statement not within" in lowered):
            return "break/continue outside a loop (a control-flow logic error)"

        return _TYPE_DESCRIPTIONS.get(target.get("category", "other"),
                                      "this error type")

    @staticmethod
    def _difficulty_note(target: Optional[dict]) -> str:
        """Name the KIND of problem, so the student knows what they are facing."""
        if not target:
            return _DIFFICULTY_NOTES["other"]
        return _DIFFICULTY_NOTES.get(target.get("category", "other"),
                                     _DIFFICULTY_NOTES["other"])

# ── What kind of problem is this? ────────────────────────────────────────────
# Item 7 of the brief: when no fix can be found, say so honestly and explain the
# category of error, instead of pretending or showing an empty diff.
_DIFFICULTY_NOTES = {
    "logic_error": ("This is a logic error requiring semantic understanding beyond "
                    "pattern-based repair: the code is legal C++ and the compiler "
                    "accepts it, but only you know which value or condition was "
                    "intended."),
    "linker_error": ("This is a linker error. It depends on the whole project "
                     "(missing object files, libraries or definitions) rather than "
                     "on one line, so rewriting a single statement cannot fix it."),
    "access_error": ("This is about class design: a private member is used from "
                     "outside the class, and only the author can decide whether it "
                     "should be public or whether a friend declaration is right."),
    "unused_variable": ("This is a style warning rather than a bug, so the tool "
                        "leaves the design decision (use it, or delete it) to you."),
    "other": ("This error needs information the pattern-based healer does not have - "
              "the intended algorithm, the type of a value, or a missing file - so "
              "it is reported rather than guessed at."),
}

# Short names for `_describe_error_type` when the message carries no clearer clue.
_TYPE_DESCRIPTIONS = {
    "type_error": "a type mismatch",
    "name_resolution": "an unresolved name",
    "undeclared_variable": "an undeclared variable",
    "syntax_error": "a syntax error",
    "logic_error": "a logic error",
    "linker_error": "a linker/whole-project problem",
    "access_error": "a class-access (private member) problem",
    "unused_variable": "an unused-variable style warning",
}

# Mechanical conversion strategies -> the standard-library call they insert.
# Used only to phrase the honest "verify your intent" caveat; the repair itself
# lives in auto_healer.py.
_CONVERSION_STRATEGIES = {
    "int_to_string_conversion": "std::to_string()",
    "string_to_int_conversion": "std::stoi()",
}


# ── g++ adapter ──────────────────────────────────────────────────────────────
# One shared parser for compiler output.  It also catches `fatal error:` lines
# (for example a missing #include file), which the previous pattern silently
# dropped - so a program that could not even be read looked "clean" to the old
# loop and it reported success on a file that does not build at all.
_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<column>\d+):\s*"
    r"(?P<kind>fatal error|error|warning|note):\s*(?P<message>.*)$"
)


def parse_compiler_output(stderr: str) -> list:
    """
    Turn raw g++ stderr into the project's diagnostic dictionaries.

    `fatal error` is normalised to type "error" because, for the student, a
    missing header is every bit as build-stopping as a syntax error.  `note`
    lines are dropped: they merely annotate an error already captured.
    """
    diagnostics = []
    for raw_line in (stderr or "").splitlines():
        match = _DIAGNOSTIC_RE.match(raw_line.strip())
        if not match:
            continue
        kind = match.group("kind")
        if kind == "note":
            continue
        diagnostics.append({
            "file": match.group("file"),
            "line": int(match.group("line")),
            "column": int(match.group("column")),
            "type": "error" if kind in ("error", "fatal error") else "warning",
            "message": match.group("message").strip(),
            "raw": raw_line.strip(),
        })
    return diagnostics


def make_compiler(working_dir: Optional[str] = None,
                  flags=("-std=c++17", "-Wall", "-fsyntax-only")) -> Callable:
    """
    Build a `compile_fn(source) -> (diagnostics, raw_stderr)` that runs g++.

    Two deliberate choices:

    * `-fsyntax-only` - the loop must answer "does this source parse and
      type-check?", and stopping there keeps that answer independent of whether
      this machine's linker can produce an executable (it currently cannot).
      Linking is checked separately, and only when there is data to run.
    * the source is written to a temporary .cpp inside `working_dir` (the
      project folder) so `#include "myheader.h"` still resolves, and it is
      deleted immediately - the user's real file is never rewritten by an
      OBSERVE step.

    SECURITY: Before g++ ever runs, the source is passed through the same
    compiler_runner._sanitize_code() guard that protects the CLI. A file
    containing system(), popen(), exec(), __asm__, or a <sys/...> include is
    rejected with a security_violation diagnostic, and the heal loop refuses
    to patch it (see UNFIXABLE_CATEGORIES in auto_healer.py).
    """
    def compile_source(source: str):
        # ── STEP 1: Security sanitizer runs BEFORE g++ ──
        # This is what makes test_sanitization.cpp get blocked instead of
        # compiling cleanly and looking like "nothing to fix".
        from compiler_runner import _sanitize_code
        if not _sanitize_code(source):
            return [{
                "file": "source",
                "line": 1,
                "column": 1,
                "type": "error",
                "message": "Security violation: forbidden system call, inline assembly, or <sys/...> include detected. Auto-heal refused.",
                "raw": "error: Security violation - system call blocked",
                "category": "security_violation",
                "confidence": 1.0,
            }], "error: Security violation - system call blocked"

        # ── STEP 2: No forbidden constructs — compile as usual ──
        handle, path = tempfile.mkstemp(suffix=".cpp", dir=working_dir)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(source)
            try:
                result = subprocess.run(["g++", *flags, path],
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        text=True, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                return [{
                    "file": path, "line": 1, "column": 1, "type": "error",
                    "message": f"could not run the compiler: {exc}",
                    "raw": str(exc), "category": "linker_error", "confidence": 1.0,
                }], str(exc)
            return parse_compiler_output(result.stderr), result.stderr
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    return compile_source