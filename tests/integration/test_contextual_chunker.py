"""
Manual test for rag/contextual_chunker.py

IMPORTANT: this test makes REAL calls to the Gemini API. You need a
valid GOOGLE_API_KEY set in your environment (or a .env file loaded
via python-dotenv) for this to work.

Goal: verify that generate_context() produces a short, relevant
sentence for a real function, and that embeddable_text correctly
prepends it to the original code.
"""

from dotenv import load_dotenv
load_dotenv()  # reads .env file in the project root, if present

from rag.ast_parser import parse_file
from rag.contextual_chunker import contextualize_functions


SAMPLE_CODE = '''def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt())


def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())


def login(username, password):
    query = "SELECT * FROM users WHERE username='" + username + "'"
    user = db.execute(query).fetchone()
    if user and verify_password(password, user.password):
        return create_session(user)
    return None
'''


def test_contextualize_real_functions():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)

    print("=" * 60)
    print("CALLING GEMINI API FOR EACH FUNCTION...")
    print("=" * 60)

    chunks = contextualize_functions(functions)

    assert len(chunks) == 3, f"Expected 3 chunks, got {len(chunks)}"
    print(f"\nOK - got {len(chunks)} contextual chunks back")

    for chunk in chunks:
        # Sanity checks on the generated context - we can't assert exact
        # wording (it's an LLM, non-deterministic), but we can check
        # basic quality signals.
        assert len(chunk.context) > 0, "Context should not be empty"
        assert len(chunk.context) < 300, (
            f"Context suspiciously long ({len(chunk.context)} chars) - "
            f"prompt may not be constraining the model enough"
        )
        print(f"\n--- {chunk.function.name} ---")
        print(f"Context: {chunk.context}")

    login_chunk = next(c for c in chunks if c.function.name == "login")
    assert login_chunk.function.code in login_chunk.embeddable_text
    assert login_chunk.context in login_chunk.embeddable_text
    print("\nOK - embeddable_text correctly combines context + raw code")

    print("\n--- Full embeddable_text for login() ---")
    print(login_chunk.embeddable_text)


if __name__ == "__main__":
    test_contextualize_real_functions()
    print("\nAll contextual_chunker tests passed.")