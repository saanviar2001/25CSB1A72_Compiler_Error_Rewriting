"""
scratch/test_phase2_auto_healer.py
----------------------------------
PHASE 2 regression test for src/auto_healer.py - UI-free, thread-free.

Phase 1 proved that "healing" test_semicolon.cpp returned code that did not
compile, and traced it to four root causes.  Each one gets a check here:

  RC1  the keyword-typo rule may never rewrite a legitimate name
       (it was turning `cout` into `const`, i.e. inventing a brand new error);
  RC2  message-shape repairs must run BEFORE that heuristic, not after it,
       otherwise the bogus typo patch short-circuits the correct one;
  RC3  GCC's real wording - expected ',' or ';' - must be recognised
       (the old regex only matched a bare "expected ';'");
  RC4  the ';' must land on the statement the compiler actually stopped on,
       which is normally the line ABOVE the line in the diagnostic.

It also pins down the new Patch object (rule id + rationale + line) and the
`exclude` argument, which is the hook Phase 3's replan step uses to demand a
different strategy after a failed attempt.

Run from the repo root:   python scratch/test_phase2_auto_healer.py
Exit code 0 = every check passed.
"""

import difflib
import os
import subprocess
import sys
import tempfile

# sklearn/numpy pull in OpenBLAS which over-reserves threads on this box.
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import auto_healer as ah                                   # noqa: E402
import heal_loop                                           # noqa: E402
from error_classifier import get_default_classifier        # noqa: E402

TEST_FILE = os.path.join(ROOT, "test_semicolon.cpp")
CLASSIFIER = get_default_classifier()

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition and detail:
        for row in str(detail).splitlines():
            print(f"          {row}")
    return bool(condition)


def banner(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def diagnostics(source):
    """The real, classifier-annotated diagnostics for `source`."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "case.cpp")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        errors, raw = heal_loop._compile(path)
        errors = [e for e in errors if e["type"] == "error"]
        heal_loop._classify_errors(errors, CLASSIFIER)
        return errors, raw


def compiles_cleanly(source):
    """Frontend compile check (-fsyntax-only).

    Linking is deliberately skipped: this MSYS2/UCRT64 toolchain cannot link a
    C++ binary at all right now ('libstdc++.dll.a: file not recognized' even for
    a trivially correct program), which is an environment fault rather than a
    source fault.  A healer's job ends where the compiler frontend's does, so
    -fsyntax-only is the honest oracle here.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "case.cpp")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        res = subprocess.run(
            ["g++", "-std=c++17", "-Wall", "-fsyntax-only", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        return res.returncode == 0, res.stderr


def numbered(text):
    return "\n".join(f"{i:>3} | {ln}" for i, ln in enumerate(text.splitlines(), 1))


# ── 1. The headline case: `int x = 5` must become `int x = 5;` ───────────────
banner("1. test_semicolon.cpp  ->  real diagnostic  ->  Patch")

original = open(TEST_FILE, "r", encoding="utf-8").read()
errors, raw = diagnostics(original)
print("compiler said:")
for err in errors:
    print(f"    line {err['line']}:{err['column']}  [{err.get('category')}]  {err['message']}")
print("\nsource:")
print(numbered(original))
print()

target = errors[0]
patch = ah.attempt_fix_detailed(original, target)

check("attempt_fix_detailed returned a Patch", patch is not None, raw)
if patch:
    print("patch chosen:")
    print(f"    strategy   : {patch.strategy}")
    print(f"    line_no    : {patch.line_no}")
    print(f"    confidence : {patch.confidence}")
    print(f"    rationale  : {patch.rationale}")
    print("\npatched source:")
    print(numbered(patch.source))

    # ── The explicit before/after proof ──────────────────────────────────────
    # Everything the UI must show and everything the grader must see: one
    # unified diff, and the guarantee that the ONLY change is the ';'.
    diff_rows = list(difflib.unified_diff(original.splitlines(),
                                          patch.source.splitlines(),
                                          fromfile="before (test_semicolon.cpp)",
                                          tofile="after  (healed)",
                                          lineterm="", n=1))
    changed = [row for row in diff_rows
               if row[:1] in "+-" and not row.startswith(("+++", "---"))]
    print("\nBEFORE / AFTER DIFF (unified):")
    for row in diff_rows:
        print("    " + row)
    print()
    check("the diff is non-empty (UI must never show an identical before/after)",
          bool(changed), "every line was unchanged - nothing actually happened")
    check("the diff is EXACTLY '-    int x = 5' / '+    int x = 5;'",
          changed == ["-    int x = 5", "+    int x = 5;"], repr(changed))
    print()

    check("the fault line gained its ';'", "int x = 5;" in patch.source,
          "expected 'int x = 5;' in the patched source")
    check("RC1 - 'cout' was NOT rewritten to 'const'", "const" not in patch.source,
          "the old bug: typo healing turned cout into const")
    check("RC1 - the 'cout' statement is untouched",
          "cout << x << endl;" in patch.source)
    check("no lines were added or deleted",
          len(patch.source.splitlines()) == len(original.splitlines()))
    check("RC4 - the edit landed on the fault line, not the reported line",
          patch.line_no == original.splitlines().index("    int x = 5") + 1,
          f"patch.line_no={patch.line_no}")
    check("the Patch reports the missing_semicolon rule",
          patch.strategy == "missing_semicolon", f"strategy={patch.strategy}")
    check("the Patch carries a plain-English rationale",
          "';'" in patch.rationale and len(patch.rationale) > 10, patch.rationale)

    ok, stderr = compiles_cleanly(patch.source)
    check("THE PATCHED CODE COMPILES", ok, stderr)
    ok_orig, _ = compiles_cleanly(original)
    check("baseline - the unpatched source really did fail to compile", not ok_orig)

    check("attempt_fix() (string API) agrees with the Patch",
          ah.attempt_fix(original, target) == patch.source)

    check("exclude= makes the planner withhold that strategy",
          ah.attempt_fix_detailed(original, target, exclude=["missing_semicolon"]) is None,
          "a strategy already tried must not be offered again")
else:
    check("the fault line gained its ';'", False, "no patch at all was produced")


# ── 2. RC4 in isolation: which lines are believable fault sites? ─────────────
banner("2. RC4 - fault-site search")

fault_index = original.splitlines().index("    int x = 5")
candidates = ah._semicolon_fault_candidates(original, target)
print(f"    reported line (1-based) : {target['line']}")
print(f"    fault line     (1-based): {fault_index + 1}")
print(f"    candidate indices       : {candidates}  (0-based, most likely first)")

check("the fault line is tried first", bool(candidates) and candidates[0] == fault_index)

# Two statements both missing ';'.  GCC stops at the first one only, so a heal
# loop has to keep going after a patch that did not silence the build - which is
# exactly what Phase 3's replan step will do.
multi = ("#include <iostream>\n"
         "int main() {\n"
         "    int a = 1\n"
         "    int b = 2\n"
         "    std::cout << a << b << std::endl;\n"
         "    return 0;\n"
         "}\n")
multi_errors, multi_raw = diagnostics(multi)
if check("two-fault case reproduces", bool(multi_errors), multi_raw):
    print(f"    first diagnostic: line {multi_errors[0]['line']}  "
          f"{multi_errors[0]['message']}")
stage = multi
for step in range(1, 4):
    errs, _ = diagnostics(stage)
    if not errs:
        break
    step_patch = ah.attempt_fix_detailed(stage, errs[0])
    if step_patch is None:
        print(f"    pass {step}: no patch available")
        break
    print(f"    pass {step}: {step_patch.strategy} on line {step_patch.line_no} "
          f"(error was on line {errs[0]['line']})")
    stage = step_patch.source
check("RC4 - both fault lines are cleared across repeated replans",
      "int a = 1;" in stage and "int b = 2;" in stage, numbered(stage))
ok_multi, err_multi = compiles_cleanly(stage)
check("RC4 - the two-pass result compiles", ok_multi, err_multi)

for line in ["if (x > 0)", "for (int i = 0; i < n; i++)", "#include <iostream>",
             "// a comment", "int main() {", "} else {", "case 3:", "total +=",
             "    x = 5;"]:
    check(f"_needs_semicolon rejects {line!r}", not ah._needs_semicolon(line))
for line in ["int x = 5", "total = a + b", 'printf("hi")', "foo(bar)"]:
    check(f"_needs_semicolon accepts {line!r}", ah._needs_semicolon(line))


# ── 3. RC1 in isolation: legitimate names are never "repaired" ───────────────
banner("3. RC1 - keyword-typo guard")

check("cout is protected from typo healing",
      ah._fix_keyword_typo("    cout << x << endl;", "cout", original) is None)
check("std::cout's 'cout' is protected too",
      ah._fix_keyword_typo("    std::cout << x;", "cout", original) is None)
check("'cout' is not a plausible 2-edit typo", not ah._typo_is_plausible("cout", 2))
check("a long garbled token IS a plausible 2-edit typo",
      ah._typo_is_plausible("recieveed", 2))
check("a real keyword is never a typo candidate",
      ah._fix_keyword_typo("    const int x = 1;", "const", original) is None)

typo_source = "int main() {\n    itn count = 3;\n    return count;\n}\n"
typo_errors, typo_raw = diagnostics(typo_source)
if not check("a genuine keyword typo still reproduces", bool(typo_errors), typo_raw):
    pass
else:
    print(f"    diagnostic: {typo_errors[0]['message']}  "
          f"[{typo_errors[0].get('category')}]")
    typo_fix = ah.attempt_fix(typo_source, typo_errors[0])
    check("RC1 did not block a genuine typo fix", typo_fix is not None)
    if typo_fix:
        print(numbered(typo_fix))
        check("'itn' was healed to 'int'", "int count = 3;" in typo_fix)


# ── 4. RC2 + RC3 in isolation: branch order and message shape ────────────────
banner("4. RC2/RC3 - message shape wins over the typo heuristic")

# A diagnostic that BOTH quotes an identifier and demands a ';'.  The old code
# answered it with cout->const; it must now answer with a ';'.
tricky = {"line": 5, "column": 5, "category": "syntax_error",
          "message": "expected ',' or ';' before 'cout' token"}
patch2 = ah.attempt_fix_detailed(original, tricky)
check("RC3 - GCC's comma-or-semicolon wording is recognised",
      patch2 is not None and patch2.strategy == "missing_semicolon")
check("RC2 - no keyword rewrite happened on the way",
      patch2 is not None and "const" not in patch2.source)
check("RC3 - a bare \"expected ';'\" still matches",
      bool(ah._MISSING_SEMICOLON_RE.search("expected ';' before '}' token")))
check("RC3 - \"expected ')'\" is not mistaken for a semicolon",
      not ah._MISSING_SEMICOLON_RE.search("expected ')' before ';' token"))


# ── 5. Patch contract ────────────────────────────────────────────────────────
banner("5. Patch object contract")
fields = set(ah.Patch.__dataclass_fields__)
check("Patch exposes source/strategy/rationale/line_no/confidence",
      {"source", "strategy", "rationale", "line_no", "confidence"} <= fields,
      str(sorted(fields)))
check("attempt_fix accepts exclude without breaking its signature",
      ah.attempt_fix(original, target, exclude=[]) == (patch.source if patch else None))


# ── Summary ──────────────────────────────────────────────────────────────────
banner("SUMMARY")
passed = sum(1 for _, ok in RESULTS if ok)
for name, ok in RESULTS:
    if not ok:
        print(f"    FAILED: {name}")
print(f"\n    {passed}/{len(RESULTS)} checks passed")
sys.exit(0 if passed == len(RESULTS) else 1)
