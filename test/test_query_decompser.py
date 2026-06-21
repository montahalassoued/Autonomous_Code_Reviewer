"""
Manual test for rag/query_decomposer.py

IMPORTANT: makes a real Groq API call. Requires GROQ_API_KEY in .env.

Goal: verify that a diff touching multiple concerns (password reset
involving auth, database, and email) gets decomposed into multiple
targeted queries rather than one vague blended query.
"""

from dotenv import load_dotenv
load_dotenv()

from rag.query_decomposer import decompose_diff


SAMPLE_DIFF = """
+ def reset_password(email):
+     user = db.find_by_email(email)
+     if not user:
+         return None
+     token = generate_reset_token()
+     send_email(user.email, "Reset your password", token)
+     return token
"""


def test_decompose_multi_concern_diff():
    queries = decompose_diff(SAMPLE_DIFF)

    print(f"Generated {len(queries)} queries:")
    for q in queries:
        print(f"  - {q}")

    assert 1 <= len(queries) <= 3, f"Expected 1-3 queries, got {len(queries)}"
    assert all(isinstance(q, str) and len(q) > 0 for q in queries), (
        "Every query should be a non-empty string"
    )
    print("\nOK - decompose_diff returns 1-3 valid query strings")


if __name__ == "__main__":
    test_decompose_multi_concern_diff()
    print("\nAll query_decomposer tests passed.")