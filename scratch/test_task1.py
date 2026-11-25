"""Task 1 validation: every give-up message must be accurate and non-contradictory.

Part A: 3 real 'unfixable' error types compiled with the real g++.
Part B: one scripted-compiler run that forces a strategy to be tried and fail,
        so the 'Tried N strategies (...)' branch is exercised too.
"""
import sys
sys.path.insert(0, "src")

from heal_agent import HealingAgent, make_compiler
from heal_loop import _pre_scan
from error_classifier import get_default_classifier
from auto_healer import attempt_fix_detailed

compiler = make_compiler()
classifier = get_default_classifier()


def show(name, agent, result):
    print("=" * 74)
    print(name)
    print("  verdict   :", result.verdict)
    print("  attempts  :", len(result.attempts),
          [a.strategy for a in result.attempts])
    print("  explanation:", result.explanation)
    # Contradiction guard: an explanation may not say "0 attempt(s)" next to a
    # claim that strategies were tried.
    low = result.explanation.lower()
    assert not ("0 attempt(s)" in low and "already been tried" in low), \
        "CONTRADICTION still present!"
    if "no repair strategy exists" in low:
        assert "tried" not in low.split("no repair strategy exists")[0], \
            "count/prose mismatch"


# ── Part A: three real unfixable errors ─────────────────────────────────────
CASES = {
    "A1. type conversion  (std::string s = 5;)":
        '#include <iostream>\n#include <string>\nint main() {\n'
        '    std::string s = 5;\n    std::cout << s << std::endl;\n    return 0;\n}\n',
    "A2. undeclared variable  (unknown_magic_number)":
        '#include <iostream>\nint main() {\n'
        '    int y = unknown_magic_number + 1;\n'
        '    std::cout << y << std::endl;\n    return 0;\n}\n',
    "A3. logic error  (break; outside a loop)":
        '#include <iostream>\nusing namespace std;\nint main() {\n'
        '    break;\n    return 0;\n}\n',
}

for name, code in CASES.items():
    agent = HealingAgent(code, compile_fn=compiler, pre_scan=_pre_scan,
                         max_attempts=4, classify_fn=classifier.predict)
    show(name, agent, agent.run())


# ── Part B: a strategy really tried and really failed ───────────────────────
# The agent supports an injected compiler precisely for deterministic tests.
# This fake compiler keeps reporting the same syntax error, so the missing-
# semicolon rule fires, changes the text, and still fails - which is what the
# 'Tried N strategies (...)' sentence must describe.
STUCK = '#include <iostream>\nint main() {\n    int x = 5\n    return 0;\n}\n'
STUCK_DIAG = {
    "file": "stuck.cpp", "line": 4, "column": 12, "type": "error",
    "category": "syntax_error",
    "message": "expected ',' or ';' before 'return'", "raw": "stuck.cpp:4:12: error: expected ',' or ';' before 'return'",
}


def stuck_compiler(_source):
    return [dict(STUCK_DIAG)], STUCK_DIAG["raw"]


print()
print("direct attempt_fix_detailed on the stuck source:",
      attempt_fix_detailed(STUCK, STUCK_DIAG))
agent = HealingAgent(STUCK, compile_fn=stuck_compiler, pre_scan=None,
                     max_attempts=4, classify_fn=None)
show("B1. scripted: a strategy was tried and did not resolve the error",
     agent, agent.run())

print()
print("ALL TASK 1 ASSERTIONS PASSED (no contradiction).")