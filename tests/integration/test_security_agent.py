from dotenv import load_dotenv
load_dotenv()

from review.agents.security.agent import security_node  # noqa: E402 - must load .env before the agent reads its keys


SQL_INJECTION_DIFF = """
+ def get_user(username):
+     query = "SELECT * FROM users WHERE username = '" + username + "'"
+     return db.execute(query)
"""


# Builds a minimal ReviewState-shaped dict for the node under test.
def build_state(diff: str) -> dict:
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": diff,
        "changed_files": ["auth/login.py"],
        "review_context": "",
        "findings": [],
        "final_review_markdown": "",
    }


def test_security_node_detects_sql_injection():
    state = build_state(SQL_INJECTION_DIFF)

    result = security_node(state)

    assert "findings" in result
    findings = result["findings"]
    print(f"\nDetected {len(findings)} security finding(s):")
    for f in findings:
        print(f"  - [{f['severity']}] {f['title']} ({f['file_path']}:{f['line_number']})")

    assert len(findings) > 0, "Should detect the SQL injection vulnerability"
    assert any(f["severity"] in {"CRITICAL", "HIGH"} for f in findings), (
        "SQL injection should be flagged as CRITICAL or HIGH severity"
    )
    for f in findings:
        assert f["agent"] == "security"
        assert f["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
        assert isinstance(f["line_number"], int)
        assert f["title"]
        assert f["description"]
        assert f["suggestion"]

    print("OK - security agent detected the SQL injection issue with valid Finding shape")


IDOR_DIFF = """
+@router.get("/invoices/{invoice_id}")
+def get_invoice(invoice_id: int, current_user=Depends(get_current_user)):
+    invoice = session.get(Invoice, invoice_id)
+    if invoice is None:
+        raise HTTPException(status_code=404)
+    return invoice
"""


# An authenticated endpoint with no ownership check (IDOR) must be reported.
def test_security_node_detects_missing_authorization():
    state = build_state(IDOR_DIFF)

    result = security_node(state)

    findings = result["findings"]
    print(f"\nDetected {len(findings)} finding(s) on the IDOR diff:")
    for f in findings:
        print(f"  - [{f['severity']}] {f['title']}")

    text = " ".join(f"{f['title']} {f['description']}".lower() for f in findings)
    assert any(word in text for word in ("authoriz", "idor", "owner", "cwe-639", "cwe-862")), (
        "Should report the missing ownership/authorization check"
    )
    print("OK - security agent detected the missing authorization check")


# A safe diff must not crash, even if the LLM returns [].
def test_security_node_handles_empty_context():
    state = build_state("+ def add(a, b):\n+     return a + b\n")

    result = security_node(state)

    assert "findings" in result
    assert isinstance(result["findings"], list)
    print(f"\nSafe diff produced {len(result['findings'])} finding(s) (expected: few or none)")


if __name__ == "__main__":
    test_security_node_detects_sql_injection()
    test_security_node_handles_empty_context()
    print("\nAll security agent tests passed.")
