"""Task 2 validation: the new int<->std::string conversion strategy really fires,
really changes the source, really removes THIS error, and reports the honest caveat."""
import sys
sys.path.insert(0, "src")

from heal_agent import HealingAgent, make_compiler
from heal_loop import _pre_scan
from error_classifier import get_default_classifier
from auto_healer import attempt_fix_detailed

compiler = make_compiler()
classifier = get_default_classifier()

CASES = [
    ("int -> std::string   (std::string s = 5;)",
     '#include <iostream>\n#include <string>\nint main() {\n'
     '    std::string s = 5;\n    std::cout << s << std::endl;\n    return 0;\n}\n',
     "std::to_string"),
    ("std::string -> int   (std::string s=\"42\"; int n = s;)",
     '#include <iostream>\n#include <string>\nint main() {\n'
     '    std::string s = "42";\n    int n = s;\n'
     '    std::cout << n << std::endl;\n    return 0;\n}\n',
     "std::stoi"),
]

for name, code, wrapper in CASES:
    print("=" * 74)
    print("TEST:", name)
    agent = HealingAgent(code, compile_fn=compiler, pre_scan=_pre_scan,
                         max_attempts=4, classify_fn=classifier.predict)

    obs = agent._observe(code)
    for d in obs.blocking:
        print("  diagnostic: cat=%s conf=%s\n    msg=%r"
              % (d.get("category"), d.get("confidence"), d.get("message")))

    target = agent._pick_target(obs)
    print("  attempt_fix_detailed ->", attempt_fix_detailed(code, target))

    result = agent.run()
    print("  verdict   :", result.verdict)
    print("  changed   :", result.changed)
    print("  attempts  :", len(result.attempts))
    for a in result.attempts:
        print("    - strategy :", a.strategy)
        print("      action   :", a.action)
        print("      outcome  :", a.outcome)
    print("  DIFF:\n" + (result.diff() or "    (no diff)"))
    print("  explanation:", result.explanation)

    fixed_diags, _ = compiler(result.source)
    print("  recompile of fixed source, diagnostics:", fixed_diags)

    assert result.changed, "no diff produced"
    assert wrapper in result.diff(), f"{wrapper} not inserted"
    assert not fixed_diags, "the specific conversion error is NOT gone"
    assert f"Applied {wrapper}" in result.explanation, "honest caveat missing"

print()
print("ALL TASK 2 ASSERTIONS PASSED.")