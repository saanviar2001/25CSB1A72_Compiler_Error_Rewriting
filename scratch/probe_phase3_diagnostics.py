"""Probe: what does g++ ACTUALLY say for the Phase 3 demo candidates?

Nothing here asserts anything - it only records compiler behaviour so the
planner can be designed around real diagnostics instead of guesses.
"""
import os
import subprocess
import tempfile

GPP = "g++"

CASES = {
    "A_if_assign_int": """#include <iostream>
using namespace std;
int main() {
    int x = 5;
    if (x = 5) {
        cout << x << endl;
    }
    return 0;
}
""",
    "B_missing_semi_plus_if_assign": """#include <iostream>
using namespace std;
int main() {
    int x = 5
    if (x = 5) {
        cout << x << endl;
    }
    return 0;
}
""",
    "C_missing_header": """#include "definitely_not_here.h"
#include <iostream>
int main() { std::cout << 1; }
""",
    "D_no_include_cout": """int main() {
    cout << 5 << endl;
    return 0;
}
""",
    "E_if_assign_const": """#include <iostream>
int main() {
    const int x = 5;
    if (x = 5) { std::cout << "y"; }
    return 0;
}
""",
    "F_less_equal_typo": """#include <iostream>
int main() {
    int x = 5;
    if (x =< 5) { std::cout << "y"; }
    return 0;
}
""",
}

FLAGS = ["-std=c++17", "-Wall", "-fsyntax-only"]

for name, code in CASES.items():
    handle, path = tempfile.mkstemp(suffix=".cpp")
    os.close(handle)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(code)
    proc = subprocess.run([GPP, *FLAGS, path], capture_output=True, text=True)
    print("=" * 72)
    print(f"{name}   exit={proc.returncode}   {'CLEAN' if proc.returncode == 0 else 'DIAGNOSTICS'}")
    for line in (proc.stderr or "").splitlines():
        low = line.strip().lower()
        if path in line or "error:" in low or "warning:" in low or "fatal error" in low:
            print("   ", line.replace(path, "FILE").strip())
    os.remove(path)
