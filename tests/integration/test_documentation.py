"""
Integration test for agents/documentation.py

Makes a REAL call to Groq (llama-3.3-70b-versatile) against a diff that
introduces a new public function with no docstring, no type hints, and
a cryptic parameter name, to check the agent actually detects
documentation gaps rather than just returning valid-but-empty output.
"""

from dotenv import load_dotenv
load_dotenv()

from agents.documentation import documentation_node


UNDOCUMENTED_DIFF = """
+ def proc(x, f):
+     r = []
+     for i in x:
+         if f(i):
+             r.append(i * 2)
+     return r
"""


def build_state(diff: str) -> dict:
    """Builds a minimal ReviewState-shaped dict for the node under test."""
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": diff,
        "changed_files": ["utils/transform.py"],
        "retrieved_context": [],
        "retrieval_quality_score": 1.0,
        "retrieval_attempts": 1,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "parallel_agents",
    }


def test_documentation_node_detects_missing_docstring():
    state = build_state(UNDOCUMENTED_DIFF)

    result = documentation_node(state)

    assert "findings" in result
    findings = result["findings"]
    print(f"\nDetected {len(findings)} documentation finding(s):")
    for f in findings:
        print(f"  - [{f['severity']}] {f['title']} ({f['file_path']}:{f['line_number']})")

    assert len(findings) > 0, "Should detect missing docstring/type hints/unclear naming"
    for f in findings:
        assert f["agent"] == "documentation"
        assert f["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
        assert isinstance(f["line_number"], int)
        assert f["title"]
        assert f["description"]
        assert f["suggestion"]

    print("OK - documentation agent detected the missing docstring issue with valid Finding shape")


def test_documentation_node_handles_empty_context():
    """A trivial, already well-documented diff should not crash, even
    if the LLM returns an empty findings array."""
    state = build_state(
        '+ def add(a: int, b: int) -> int:\n'
        '+     """Returns the sum of a and b."""\n'
        '+     return a + b\n'
    )

    result = documentation_node(state)

    assert "findings" in result
    assert isinstance(result["findings"], list)
    print(f"\nWell-documented diff produced {len(result['findings'])} finding(s) (expected: few or none)")


if __name__ == "__main__":
    test_documentation_node_detects_missing_docstring()
    test_documentation_node_handles_empty_context()
    print("\nAll documentation agent tests passed.")
