import sys, os
sys.path.insert(0, "src")

from heal_agent import HealingAgent, make_compiler
from heal_loop import _pre_scan
from error_classifier import get_default_classifier

compiler = make_compiler()
classifier = get_default_classifier()

tests = [
    ("1. Type Conversion", """#include <iostream>
#include <string>
int main() {
    std::string s = 5;
    std::cout << s << std::endl;
    return 0;
}
"""),
    ("2. Undeclared Variable (Uninferable)", """#include <iostream>
int main() {
    int y = unknown_magic_number + 1;
    std::cout << y << std::endl;
    return 0;
}
"""),
    ("3. Logic Error (Break Outside Loop)", """#include <iostream>
using namespace std;
int main() {
    break;
    return 0;
}
"""),
]

for name, code in tests:
    print(f"\n================ {name} ================")
    agent = HealingAgent(code, compile_fn=compiler, pre_scan=_pre_scan, max_attempts=4, classify_fn=classifier.predict)
    res = agent.run()
    print("Verdict:", res.verdict)
    print("Attempts length:", len(res.attempts))
    print("Explanation:\n" + res.explanation)
