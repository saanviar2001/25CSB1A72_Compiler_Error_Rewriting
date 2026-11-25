import sys, os
sys.path.insert(0, "src")

from heal_agent import HealingAgent, make_compiler
from heal_loop import _pre_scan
from error_classifier import get_default_classifier

compiler = make_compiler()
classifier = get_default_classifier()

code_type_conv = """#include <iostream>
#include <string>
int main() {
    std::string s = 5;
    std::cout << s << std::endl;
    return 0;
}
"""

print("=== TRACING TEST 1: type conversion (std::string s = 5;) ===")
agent = HealingAgent(
    code_type_conv,
    compile_fn=compiler,
    pre_scan=_pre_scan,
    max_attempts=4,
    classify_fn=classifier.predict,
)

obs = agent._observe(code_type_conv)
print(f"Observation blocking: {len(obs.blocking)}")
for d in obs.blocking:
    print(f"  diagnostic: {d}")

target = agent._pick_target(obs)
print(f"Picked target: {target}")

plan, patch = agent._plan(target, obs)
print(f"Plan result: plan={plan}, patch={patch}")
print(f"len(agent.attempts) before giving up: {len(agent.attempts)}")
print(f"agent.tried before giving up: {agent.tried}")

result = agent.run()
print(f"\nFinal result verdict: {result.verdict}")
print(f"Final result explanation:\n{result.explanation}")
print(f"Final result attempts count: {len(result.attempts)}")
