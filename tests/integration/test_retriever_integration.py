from dotenv import load_dotenv
load_dotenv()

from rag.ast_parser import parse_file
from rag.contextual_chunker import contextualize_functions
from rag.embedder import embed_chunks
from db.chroma import get_client, get_collection, store_chunks
from rag.knowledge_graph import build_knowledge_graph
from review.retriever import run_retrieval


EXISTING_REPO_CODE = '''def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt())


def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())


def login(username, password):
    user = db.find_by_username(username)
    if user and verify_password(password, user.password):
        return create_session(user)
    return None


def create_session(user):
    return {"user_id": user.id}


def send_email(to, subject, body):
    return mailer.send(to, subject, body)
'''


NEW_PR_DIFF = """
+ def reset_password(email):
+     user = db.find_by_email(email)
+     if not user:
+         return None
+     token = generate_reset_token()
+     send_email(user.email, "Reset your password", token)
+     return token
"""


def setup_repo_index():
    """
    Runs the full Step 3-6 ingestion pipeline on EXISTING_REPO_CODE,
    simulating a repo that was already indexed before this new PR
    (with reset_password) was opened.
    """
    print("Parsing existing repo...")
    functions = parse_file("auth/utils.py", EXISTING_REPO_CODE)

    print("Contextualizing (Gemini, 5 calls)...")
    chunks = contextualize_functions(functions)

    print("Embedding (Voyage, 1 batched call)...")
    embeddings = embed_chunks(chunks)

    print("Storing in Chroma...")
    client = get_client(persist_directory="./test_chroma_data")
    collection = get_collection(client)
    store_chunks(collection, chunks, embeddings)

    print("Building knowledge graph...")
    graph = build_knowledge_graph(functions)

    all_documents = [c.embeddable_text for c in chunks]
    all_ids = [f"{c.function.file_path}::{c.function.name}" for c in chunks]

    return collection, all_documents, all_ids, graph


def test_full_retrieval_pipeline():
    collection, all_documents, all_ids, graph = setup_repo_index()

    print("\n" + "=" * 60)
    print("RUNNING run_retrieval() ON NEW PR DIFF")
    print("=" * 60)
    print(NEW_PR_DIFF)

    retrieved_chunks, quality_score = run_retrieval(
        diff=NEW_PR_DIFF,
        collection=collection,
        all_documents=all_documents,
        all_ids=all_ids,
        graph=graph,
    )

    print(f"\nQuality score: {quality_score:.2f}")
    print(f"Retrieved {len(retrieved_chunks)} chunks:")
    for chunk in retrieved_chunks:
        print(f"  - {chunk['function_name']} ({chunk['file_path']}) "
              f"score={chunk['relevance_score']:.3f} callers={chunk['callers']}")

    assert len(retrieved_chunks) > 0, "Should retrieve at least some context"
    assert quality_score > 0.0, "Judge should not score genuinely relevant context as 0"

    # reset_password() conceptually relates most to send_email() (it
    # calls it) - this is the strongest signal the pipeline could find,
    # since send_email is the only EXISTING function literally
    # referenced by name in the new diff.
    retrieved_names = {c["function_name"] for c in retrieved_chunks}
    assert "send_email" in retrieved_names, (
        f"Expected send_email (directly called in the diff) in "
        f"retrieved context, got {retrieved_names}"
    )
    print(f"\nOK - send_email correctly retrieved as directly relevant context")
    print(f"OK - full pipeline (decompose -> search -> enrich -> rerank -> judge) "
          f"produced {len(retrieved_chunks)} chunks with quality score {quality_score:.2f}")


if __name__ == "__main__":
    test_full_retrieval_pipeline()
    print("\nFull retriever integration test passed.")