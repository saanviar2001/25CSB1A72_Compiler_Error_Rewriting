"""Task 4 endpoint validation for behavioral stdin/stdout test sidecars."""
import io
import json
import sys

sys.path.insert(0, "src")
from webapp import app

client = app.test_client()

page = client.get("/")
assert page.status_code == 200
html = page.get_data(as_text=True)
assert 'id="test-spec-input"' in html
assert '&lt;source&gt;.tests.json' in html
assert 'currentTestSpec' in html
assert 'timeout=5' in open("src/heal_agent.py", encoding="utf-8").read()
print("Dashboard behavioral-test input and 5-second case timeout -> OK")


def post_fix(filename, source, tests):
    """Match the dashboard's multipart source + optional test_spec payload."""
    return client.post(
        "/api/fix",
        data={
            "source": (io.BytesIO(source.encode("utf-8")), filename),
            "test_spec": (
                io.BytesIO(json.dumps({"tests": tests}).encode("utf-8")),
                f"{filename}.tests.json",
            ),
        },
        content_type="multipart/form-data",
    )


# Demo 1: the mechanical semicolon repair compiles, but the adder still subtracts.
ADDER_WITH_SYNTAX_ERROR = """extern "C" int scanf(const char*, ...);
extern "C" int printf(const char*, ...);
int main() {
    int a, b;
    scanf("%d%d", &a, &b);
    printf("%d\\n", a - b)
    return 0;
}
"""
adder_tests = [{"stdin": "8 3\n", "expected_stdout": "11\n"}]
response = post_fix("adder.cpp", ADDER_WITH_SYNTAX_ERROR, adder_tests)
assert response.status_code == 200, response.get_data(as_text=True)
adder = response.get_json()
assert adder["compiles"] is True
assert adder["fixed_source"] != adder["original_source"]
assert 'printf("%d\\n", a - b);' in adder["fixed_source"]
assert adder["verdict"] == "compiles_but_behavioral_verification_failed", adder
assert adder["behavior"]["checked"] is True
assert adder["behavior"]["passed"] is False
assert adder["behavior"]["cases"][0]["actual_stdout"] == "5\n"
print("Demo 1 — adder compiles but behavioral verification fails")
print("  verdict:", adder["verdict_label"])
print("  expected:", repr(adder["behavior"]["cases"][0]["expected_stdout"]))
print("  actual  :", repr(adder["behavior"]["cases"][0]["actual_stdout"]))


# Demo 2: the same semicolon class of repair yields a correct doubled value.
DOUBLE_WITH_SYNTAX_ERROR = """extern "C" int scanf(const char*, ...);
extern "C" int printf(const char*, ...);
int main() {
    int n;
    scanf("%d", &n);
    printf("%d\\n", n * 2)
    return 0;
}
"""
double_tests = [
    {"stdin": "7\n", "expected_stdout": "14\n"},
    {"stdin": "0\n", "expected_stdout": "0\n"},
]
response = post_fix("double_value.cpp", DOUBLE_WITH_SYNTAX_ERROR, double_tests)
assert response.status_code == 200, response.get_data(as_text=True)
verified = response.get_json()
assert verified["compiles"] is True
assert verified["fixed_source"] != verified["original_source"]
assert verified["verdict"] == "fixed_and_verified", verified
assert verified["behavior"]["checked"] is True
assert verified["behavior"]["passed"] is True
assert [case["actual_stdout"] for case in verified["behavior"]["cases"]] == ["14\n", "0\n"]
print("Demo 2 — correctly healed and behaviorally verified")
print("  verdict:", verified["verdict_label"])
for case in verified["behavior"]["cases"]:
    print(f"  case {case['number']}: stdin={case['stdin']!r}, stdout={case['actual_stdout']!r}")


# The sidecar is intentionally source-specific rather than an arbitrary JSON upload.
response = client.post(
    "/api/fix",
    data={
        "source": (io.BytesIO(DOUBLE_WITH_SYNTAX_ERROR.encode("utf-8")), "double_value.cpp"),
        "test_spec": (io.BytesIO(b"[]"), "wrong-name.tests.json"),
    },
    content_type="multipart/form-data",
)
assert response.status_code == 400
assert "double_value.cpp.tests.json" in response.get_json()["error"]
print("Sidecar filename validation -> OK")
print("ALL TASK 4 ASSERTIONS PASSED.")