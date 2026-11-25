"""
scratch/diagnose_heal_phase1.py
--------------------------------
PHASE 1 diagnostic harness (read-only w.r.t. the real source tree).

Calls the core healing machinery directly - no webapp.py, no gui.py - on
test_semicolon.cpp and prints the exact input, the exact returned "fixed"
code, whether they differ, and the full internal path attempt_fix() took.

Run from the repo root:  python scratch/diagnose_heal_phase1.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

# sklearn/numpy pull in OpenBLAS which over-reserves threads on this box.
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import auto_healer                                        # noqa: E402
import heal_loop                                          # noqa: E402
import diff_viewer                                        # noqa: E402
from error_classifier import get_default_classifier       # noqa: E402

TEST_FILE = os.path.join(ROOT, "test_semicolon.cpp")
SEMI_RE = re.compile(r"""expected\s+['"]?;['"]?""")

TRACE_NAMES = [n for n in dir(auto_healer)
               if n.startswith("fix_") or n in
               ("_append_missing_semicolon", "_fix_keyword_typo",
                "_insert_include", "_initializer_for_declaration",
                "inject_division_guard")]

CALL_LOG = []


def _install_tracer():
    """Wrap every healing helper so we can see the exact path taken."""
    for name in TRACE_NAMES:
        original = getattr(auto_healer, name)
        if not callable(original) or getattr(original, "_traced", False):
            continue

        def make(fn, fname):
            def wrapper(*args, **kwargs):
                result = fn(*args, **kwargs)
                src_in = args[0] if args else kwargs.get("source", "")
                if isinstance(result, str):
                    verdict = "CHANGED" if result != src_in else "NO-OP (returned input unchanged)"
                else:
                    verdict = "None" if result is None else repr(result)
                CALL_LOG.append((fname, verdict))
                return result
            wrapper._traced = True
            wrapper.__name__ = fname
            return wrapper

        setattr(auto_healer, name, make(original, name))

    # _HANDLERS holds direct references captured at import time - repoint them.
    for category, fn in list(auto_healer._HANDLERS.items()):
        traced = getattr(auto_healer, getattr(fn, "__name__", ""), None)
        if traced is not None and getattr(traced, "_traced", False):
            auto_healer._HANDLERS[category] = traced


def numbered(text):
    return "\n".join(f"{i:>3} | {ln}" for i, ln in enumerate(text.splitlines(), 1))


def banner(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)




def main():
    original = open(TEST_FILE, "r", encoding="utf-8").read()

    banner("1. EXACT INPUT CODE  (test_semicolon.cpp)")
    print(repr(original))
    print(numbered(original))

    # Work on a copy so the repo file is never mutated.
    work_dir = tempfile.mkdtemp(prefix="phase1-")
    work_file = os.path.join(work_dir, "test_semicolon.cpp")
    shutil.copyfile(TEST_FILE, work_file)

    try:
        banner("2. RAW g++ OUTPUT FOR THIS FILE (same flags as heal_loop)")
        res = subprocess.run(["g++", "-std=c++17", "-Wall", work_file],
                             capture_output=True, text=True)
        print(res.stderr.strip() or "(no stderr)")

        banner("3. heal_loop._compile() -> parsed error dicts")
        errors, _raw = heal_loop._compile(work_file)
        for e in errors:
            print(f"  line={e['line']} col={e['column']} type={e['type']}")
            print(f"    message = {e['message']!r}")
        if not errors:
            print("  (no diagnostics parsed)")

        banner("4. heal_loop._pre_scan() -> synthetic warnings")
        pre = heal_loop._pre_scan(original)
        for e in pre:
            print(f"  line={e['line']} type={e['type']} category={e['category']} "
                  f"message={e['message']!r}")
        if not pre:
            print("  (pre-scan produced nothing)")

        all_errors = pre + errors

        banner("5. ML classification (_classify_errors)")
        classifier = get_default_classifier()
        heal_loop._classify_errors(all_errors, classifier)
        for e in all_errors:
            print(f"  line={e['line']} type={e['type']} "
                  f"category={e.get('category')!r} conf={e.get('confidence')} "
                  f"msg={e['message']!r}")

        banner("6. _pick_fix_target()")
        target = heal_loop._pick_fix_target(all_errors)
        if target is None:
            print("  TARGET = None  -> heal_loop emits give_up immediately")
        else:
            print(f"  TARGET = line {target['line']} "
                  f"[{target.get('category')}] {target['message']!r}")
            print(f"  UNFIXABLE_CATEGORIES = {sorted(auto_healer.UNFIXABLE_CATEGORIES)}")

        banner("7. TRACE OF attempt_fix() INTERNAL PATH")
        _install_tracer()
        CALL_LOG.clear()
        patched = None
        if target is None:
            print("  skipped - no target selected")
        else:
            patched = auto_healer.attempt_fix(original, target)
            handler = auto_healer._HANDLERS.get(target.get("category"))
            print(f"  category dispatched : {target.get('category')!r}")
            print(f"  _HANDLERS entry     : {getattr(handler, '__name__', None)}")
        print(f"  helper calls made ({len(CALL_LOG)}):")
        for name, verdict in CALL_LOG:
            print(f"     -> {name:<32} returned {verdict}")

        banner("8. EXACT RETURNED 'FIXED' CODE")
        if patched is None:
            print("  attempt_fix returned None  (NO PATCH AT ALL)")
        else:
            print(repr(patched))
            print(numbered(patched))

        banner("9. ARE INPUT AND OUTPUT DIFFERENT STRINGS?")
        same = (patched is None) or (patched == original)
        print(f"  patched is None           : {patched is None}")
        print(f"  patched == original       : {patched == original}")
        print(f"  DIFFERENT (i.e. real edit): {not same}")
        if patched and patched != original:
            for item in diff_viewer.compute_diff(original, patched):
                print(f"    diff kind={item.kind!r} new_line={item.line_no_new} "
                      f"content={item.content!r}")

        banner("10. WHAT webapp.py _fix_file() WOULD DISPLAY")
        before = open(work_file, "r", encoding="utf-8").read()
        if patched is not None and patched != original:
            open(work_file, "w", encoding="utf-8").write(patched)
        after = open(work_file, "r", encoding="utf-8").read()
        print(f"  before == after : {before == after}")
        print(f"  'changed' flag  : {before != after}")

        banner("11. LINE-OF-FAULT CHECK (reported line vs. real fault line)")
        if target is not None:
            lines = original.splitlines()
            idx = int(target["line"]) - 1
            print(f"  diagnostic points at line {target['line']} -> {lines[idx]!r}")
            if idx - 1 >= 0:
                print(f"  previous line {target['line'] - 1}                -> "
                      f"{lines[idx - 1]!r}")
            print(f"  _append_missing_semicolon(reported line)    -> "
                  f"{auto_healer._append_missing_semicolon(lines[idx])!r}")
            if idx - 1 >= 0:
                print(f"  _append_missing_semicolon(previous line)  -> "
                      f"{auto_healer._append_missing_semicolon(lines[idx - 1])!r}")
            print(f"  regex {SEMI_RE.pattern!r} matches message? -> "
                  f"{bool(SEMI_RE.search(target['message'].lower()))}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

