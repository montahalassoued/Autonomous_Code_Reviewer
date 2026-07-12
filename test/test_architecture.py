"""
Integration test for agents/architecture.py

Makes a REAL call to Groq (llama-3.3-70b-versatile) against a diff that
introduces an obvious God class (mixing auth, email, billing, and
logging responsibilities in one class), to check the agent actually
detects architecture issues rather than just returning valid-but-empty
output.
"""

from dotenv import load_dotenv
load_dotenv()

from agents.architecture import architecture_node


GOD_CLASS_DIFF = """
+ class UserManager:
+     def create_user(self, name, email, password):
+         self.db.insert(name, email, password)
+         self.send_welcome_email(email)
+         self.charge_signup_fee(email)
+         self.log_event(f"user created: {email}")
+
+     def send_welcome_email(self, email):
+         smtp.send(email, "Welcome!", "Thanks for joining")
+
+     def charge_signup_fee(self, email):
+         stripe.charge(email, amount=5)
+
+     def log_event(self, message):
+         print(message)
"""


def build_state(diff: str) -> dict:
    """Builds a minimal ReviewState-shaped dict for the node under test."""
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": diff,
        "changed_files": ["users/manager.py"],
        "retrieved_context": [],
        "retrieval_quality_score": 1.0,
        "retrieval_attempts": 1,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "parallel_agents",
    }


def test_architecture_node_detects_god_class():
    state = build_state(GOD_CLASS_DIFF)

    result = architecture_node(state)

    assert "findings" in result
    findings = result["findings"]
    print(f"\nDetected {len(findings)} architecture finding(s):")
    for f in findings:
        print(f"  - [{f['severity']}] {f['title']} ({f['file_path']}:{f['line_number']})")

    assert len(findings) > 0, "Should detect the God class / SRP violation"
    for f in findings:
        assert f["agent"] == "architecture"
        assert f["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
        assert isinstance(f["line_number"], int)
        assert f["title"]
        assert f["description"]
        assert f["suggestion"]

    print("OK - architecture agent detected the God class issue with valid Finding shape")


def test_architecture_node_handles_empty_context():
    """A trivial diff with no real architecture issue should not crash,
    even if the LLM returns an empty findings array."""
    state = build_state("+ def add(a, b):\n+     return a + b\n")

    result = architecture_node(state)

    assert "findings" in result
    assert isinstance(result["findings"], list)
    print(f"\nTrivial diff produced {len(result['findings'])} finding(s) (expected: few or none)")


if __name__ == "__main__":
    test_architecture_node_detects_god_class()
    test_architecture_node_handles_empty_context()
    print("\nAll architecture agent tests passed.")
