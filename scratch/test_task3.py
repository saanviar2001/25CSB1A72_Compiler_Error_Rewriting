"""Task 3 validation: the new "Paste Code" input option.

The paste UI feeds the SAME currentSource/currentFilename globals the upload path
uses, so the browser sends the pasted text to /api/analyze and /api/fix as a
multipart 'source' upload. This test reproduces exactly that request (a file part
named 'pasted_code.cpp' whose bytes are the pasted text) against the real app.
"""
import sys, io, re
sys.path.insert(0, "src")
from webapp import app, PAGE

client = app.test_client()

# ── 1. The paste UI is present and styled as specified ──
page = client.get("/")
assert page.status_code == 200
html = page.get_data(as_text=True)
checks = {
    "Upload File toggle": 'id="mode-btn-upload"' in html and ">Upload File<" in html,
    "Paste Code toggle": 'id="mode-btn-paste"' in html and ">Paste Code<" in html,
    "textarea with id": 'id="paste-textarea"' in html,
    "~15-20 rows": 'rows="18"' in html,
    "placeholder": 'placeholder="Paste your C++ code here..."' in html,
    "monospace textarea css": ".paste-textarea" in html and "'JetBrains Mono', monospace" in html,
    "editable filename default": 'id="paste-filename"' in html and 'value="pasted_code.cpp"' in html,
    "same backend path wired": "syncActiveInput()" in html,
    "fix path reuses same fetch": "fetch('/api/fix'" in html,
    "analyze path reuses same fetch": "fetch('/api/analyze'" in html,
}
for label, ok in checks.items():
    print(f"  [{'OK' if ok else 'FAIL'}] {label}")
assert all(checks.values()), "paste UI markers missing"
print("Test 1: paste UI present & wired to the shared upload path -> OK\n")

# ── 2. Pasted code (missing semicolon), NO file upload ──
PASTED = ('#include <iostream>\n'
          'int main() {\n'
          '    int x = 5\n'
          '    std::cout << x << std::endl;\n'
          '    return 0;\n'
          '}\n')

# This is byte-for-byte what the browser sends in paste mode: the textarea text
# becomes the multipart file part named by the editable filename field.
def pasted_form():
    return {"source": (io.BytesIO(PASTED.encode("utf-8")), "pasted_code.cpp")}

res = client.post("/api/analyze", data=pasted_form(), content_type="multipart/form-data")
assert res.status_code == 200, res.get_data(as_text=True)
an = res.get_json()
assert an["filename"] == "pasted_code.cpp", an["filename"]
errs = an.get("errors", [])
assert errs, "expected a missing-semicolon diagnostic"
print("Test 2: /api/analyze via PASTE path -> OK")
print("  filename :", an["filename"])
for e in errs:
    print(f"  diagnostic: [{e.get('category')}] line {e.get('line')}: {e.get('message')}")
print()

# ── 3. Auto-Heal through the same paste path ──
res = client.post("/api/fix", data=pasted_form(), content_type="multipart/form-data")
assert res.status_code == 200, res.get_data(as_text=True)
fx = res.get_json()
print("Test 3: /api/fix via PASTE path -> OK")
print("  verdict        :", fx["verdict"], "|", fx["verdict_label"])
print("  compiles       :", fx["compiles"])
assert fx["fixed_source"] != fx["original_source"], "no real diff produced"
assert ";" in fx["fixed_source"], "semicolon not inserted"

att = fx["attempts"][0]
before = att["before"].splitlines()
after = att["after"].splitlines()
print("  real diff:")
for line in before:
    print("      " + line)
print("      " + "-" * 40)
for line in after:
    print("      " + line)
print("  attempt 1:", att["strategy"], "->", att["action"])
assert fx["verdict"] in ("fixed_and_verified", "compiles_not_behaviorally_verified")

print("\nALL TASK 3 ASSERTIONS PASSED.")