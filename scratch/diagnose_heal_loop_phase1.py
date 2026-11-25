"""
scratch/diagnose_heal_loop_phase1.py
------------------------------------
PHASE 1, part 2: run the REAL HealWorker._heal() loop on test_semicolon.cpp
with the exact same signal wiring webapp.py's _fix_file() uses, and print the
before/after pair for every attempt so we can see what the UI would render.
"""

import os
import shutil
import sys
import tempfile

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from heal_loop import HealWorker, MAX_ATTEMPTS                 # noqa: E402
from error_classifier import get_default_classifier            # noqa: E402
from compiler_runner import run_cpp_compiler                   # noqa: E402
from error_parser import parse_errors                          # noqa: E402

TEST_FILE = os.path.join(ROOT, "test_semicolon.cpp")


def show(label, text):
    print(f"--- {label} " + "-" * 40)
    for i, ln in enumerate(text.splitlines(), 1):
        print(f"  {i:>3} | {ln}")


def main():
    original = open(TEST_FILE, "r", encoding="utf-8").read()
    work_dir = tempfile.mkdtemp(prefix="phase1-loop-")
    work_file = os.path.join(work_dir, "test_semicolon.cpp")
    shutil.copyfile(TEST_FILE, work_file)

    try:
        attempts, pending = [], [None]

        def finish_pending(compiles):
            attempt = pending[0]
            if attempt is None:
                return
            try:
                attempt["after"] = open(work_file, encoding="utf-8").read()
            except OSError:
                attempt["after"] = attempt["before"]
            attempt["compiles"] = compiles
            pending[0] = None

        def attempt_started(number, error):
            if pending[0] is not None:
                finish_pending(False)
            before = open(work_file, encoding="utf-8").read()
            entry = {
                "number": number,
                "error": error.get("raw") or error.get("message") or "Compiler issue",
                "before": before, "after": before,
                "changed": False, "compiles": False,
            }
            attempts.append(entry)
            pending[0] = entry

        def diff_ready(_n, _d):
            if pending[0] is not None:
                pending[0]["changed"] = True

        worker = HealWorker(work_file, get_default_classifier())
        worker.attempt_started.connect(attempt_started)
        worker.diff_ready.connect(diff_ready)
        worker.compile_clean.connect(lambda: finish_pending(True))
        worker.give_up.connect(lambda _h: finish_pending(False))
        telemetry = worker._heal()
        finish_pending(False)

        fixed_source = open(work_file, encoding="utf-8").read()
        final_errors = parse_errors(run_cpp_compiler(work_file))
        compiles = not any(e.error_type == "error" for e in final_errors)

        print("=" * 78)
        print(f"FULL HealWorker._heal() RUN  (MAX_ATTEMPTS={MAX_ATTEMPTS})")
        print("=" * 78)
        print(f"attempts recorded : {len(attempts)}")
        for a in attempts:
            print(f"\nATTEMPT {a['number']}   changed={a['changed']}  "
                  f"compiles={a['compiles']}")
            print(f"  error   : {a['error']}")
            print(f"  before == after ? {a['before'] == a['after']}   "
                  f"<-- UI shows identical panes when True")
            show(f"attempt {a['number']} BEFORE", a["before"])
            show(f"attempt {a['number']} AFTER", a["after"])

        print("\n" + "=" * 78)
        print("FINAL STATE (what /api/fix would return)")
        print("=" * 78)
        print(f"  fixed_source == original_source : {fixed_source == original}")
        print(f"  compiles                        : {compiles}")
        print(f"  telemetry entries               : {telemetry}")
        show("FINAL FILE ON DISK", fixed_source)
        print("\nremaining diagnostics:")
        for e in final_errors:
            print(f"  {getattr(e, 'error_type', '?')} line={getattr(e, 'line', '?')}: "
                  f"{getattr(e, 'message', e)}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
