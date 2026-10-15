import math
import re
import uuid
import zlib
from types import SimpleNamespace

import chromadb
from chromadb.config import Settings

import rag.hybrid_search as hybrid_search_module
from review.graph import build_graph, REVIEW_AGENTS
from review.retriever import MAX_RETRIEVAL_ATTEMPTS
from db.chroma import store_chunks, make_chunk_id
from rag.ast_parser import parse_file
from rag.contextual_chunker import ContextualChunk
from rag.graph_retriever import EnrichedResult
from rag.ingestion import delete_stale_ids
from rag.knowledge_graph import build_knowledge_graph
from rag.reranker import rerank


AUTH_PATH = "auth/service.py"
AUTH_SOURCE = '''class AuthService:
    def __init__(self, db):
        self.db = db

    def login(self, username, password):
        user = self.db.find(username)
        if user and verify_password(password, user.password_hash):
            return create_session(user)
        return None


def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_session(user):
    return {"user_id": user.id}
'''

BILLING_PATH = "billing/invoice.py"
BILLING_SOURCE = '''class Invoice:
    def __init__(self, amount):
        self.amount = amount

    def total(self):
        return apply_tax(self.amount)


def apply_tax(amount):
    return amount * 1.2
'''

# Stand-ins for the Gemini context sentences, keyed by qualified_name.
CONTEXTS = {
    "AuthService.__init__": "Stores the database handle on the auth service.",
    "AuthService.login": "Logs a user in and starts a session.",
    "verify_password": "Checks a plaintext password against its stored bcrypt hash.",
    "create_session": "Builds the session payload for a logged-in user.",
    "Invoice.__init__": "Stores the invoice amount.",
    "Invoice.total": "Returns the invoice total including tax.",
    "apply_tax": "Adds sales tax to an amount.",
}

PR_DIFF = """diff --git a/auth/service.py b/auth/service.py
--- a/auth/service.py
+++ b/auth/service.py
@@ -12,2 +12,4 @@
 def verify_password(password, hashed):
+    if not hashed:
+        return False
"""

DECOMPOSER_REPLY = '["bcrypt checkpw hashed verify_password"]'
CLEAN_AGENT_REPLY = "[]"
HIGH_JUDGE_REPLY = '{"score": 0.9}'
LOW_JUDGE_REPLY = '{"score": 0.1}'

EMBEDDING_DIMENSION = 64
RECURSION_LIMIT = 15


# Lowercase identifier-ish tokens, shared by the fake embedder and reranker.
def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


# Deterministic stand-in for Voyage: a hashed bag-of-words vector, normalized.
def fake_embed(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSION
    for token in _tokens(text):
        vector[zlib.crc32(token.encode()) % EMBEDDING_DIMENSION] += 1.0
    vector[-1] += 1.0  # never an all-zero vector
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector]


# Fake reranker: scores a pair by shared tokens and records the pairs.
class FakeCrossEncoder:

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []

    # Returns one overlap score per (query, document) pair.
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.pairs.extend(pairs)
        return [float(len(set(_tokens(query)) & set(_tokens(document)))) for query, document in pairs]


# Stand-in for a Groq client.
class FakeLLM:

    def __init__(self, role: str, calls: list, replies: dict) -> None:
        self.role = role
        self.calls = calls
        self.replies = replies
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model: str, messages: list[dict], temperature: float, **kwargs) -> SimpleNamespace:
        prompt = messages[0]["content"]
        self.calls.append((self.role, prompt))
        reply_key = "agent" if self.role in REVIEW_AGENTS else self.role
        message = SimpleNamespace(content=self.replies[reply_key])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


# Real hybrid_search with top_k=1, so a caller can only arrive via the graph.
def narrow_hybrid_search(query, collection, all_documents, all_ids, top_k=10):
    return hybrid_search_module.hybrid_search(query, collection, all_documents, all_ids, top_k=1)


# Fake every external dependency where it's used; returns the recorded LLM calls.
def install_fakes(monkeypatch, replies: dict) -> list:
    calls: list = []
    # Other tests load the real .env; make sure no tracing call goes out.
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    for agent in REVIEW_AGENTS:
        monkeypatch.setattr(
            f"review.agents.{agent}.agent._build_groq_client",
            lambda role=agent: FakeLLM(role, calls, replies),
        )
    monkeypatch.setattr("review.retriever._build_groq_client", lambda: FakeLLM("judge", calls, replies))
    monkeypatch.setattr("rag.query_decomposer._build_client", lambda: FakeLLM("decomposer", calls, replies))
    monkeypatch.setattr("rag.hybrid_search.embed_query", fake_embed)
    monkeypatch.setattr("rag.reranker._get_model", lambda: FakeCrossEncoder())
    monkeypatch.setattr("review.retriever.hybrid_search", narrow_hybrid_search)
    return calls


# Creates an empty, uniquely named collection in an in-memory Chroma.
def new_collection():
    client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
    # EphemeralClient instances share one in-memory store per process,
    # so a unique name keeps tests from seeing each other's chunks.
    return client.get_or_create_collection(name=f"offline_{uuid.uuid4().hex}", embedding_function=None)


# Builds the same four objects rag/ingestion.py returns, offline.
def build_index() -> tuple:
    functions = parse_file(AUTH_PATH, AUTH_SOURCE) + parse_file(BILLING_PATH, BILLING_SOURCE)
    chunks = [ContextualChunk(function=fn, context=CONTEXTS[fn.qualified_name]) for fn in functions]

    collection = new_collection()
    store_chunks(collection, chunks, [fake_embed(chunk.embeddable_text) for chunk in chunks])

    all_documents = [chunk.embeddable_text for chunk in chunks]
    all_ids = [make_chunk_id(chunk) for chunk in chunks]
    graph = build_knowledge_graph(functions)
    return collection, all_documents, all_ids, graph, {fn.qualified_name: fn for fn in functions}


# Builds the same initial ReviewState app/worker.py passes to the graph.
def build_state() -> dict:
    return {
        "pr_number": 7,
        "repo_full_name": "test-owner/test-repo",
        "pr_diff": PR_DIFF,
        "changed_files": [],
        "retrieved_context": [],
        "retrieval_quality_score": 0.0,
        "retrieval_attempts": 0,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "retriever",
    }


# Compiles the real graph over a fresh offline index and runs one review.
def run_review() -> tuple[dict, dict]:
    collection, all_documents, all_ids, graph, functions = build_index()
    compiled = build_graph(collection, all_documents, all_ids, graph)
    final_state = compiled.invoke(build_state(), config={"recursion_limit": RECURSION_LIMIT})
    return final_state, functions


# Returns just the role of every recorded LLM call.
def roles_of(calls: list) -> list[str]:
    return [role for role, _prompt in calls]


def test_clean_pr_terminates_and_calls_each_agent_once(monkeypatch):
    calls = install_fakes(
        monkeypatch,
        {"decomposer": DECOMPOSER_REPLY, "judge": HIGH_JUDGE_REPLY, "agent": CLEAN_AGENT_REPLY},
    )

    final_state, _functions = run_review()
    roles = roles_of(calls)

    for agent in REVIEW_AGENTS:
        assert roles.count(agent) == 1, f"{agent} should run exactly once, ran {roles.count(agent)} times"
    assert roles.count("decomposer") == 1, "good retrieval on the first try needs no retry"
    assert roles.count("judge") == 1
    assert final_state["retrieval_attempts"] == 1
    assert final_state["findings"] == []
    assert "No issues found" in final_state["final_review_markdown"], (
        "A clean PR must still produce a review comment"
    )
    print(f"OK - clean PR finished in one pass, LLM calls: {roles}")


def test_pr_with_findings_reaches_the_synthesizer(monkeypatch):
    finding = (
        '[{"severity": "HIGH", "file_path": "auth/service.py", "line_number": 13, '
        '"title": "Empty hash accepted", "description": "d", "evidence": "e", "suggestion": "s"}]'
    )
    calls = install_fakes(
        monkeypatch,
        {"decomposer": DECOMPOSER_REPLY, "judge": HIGH_JUDGE_REPLY, "agent": finding},
    )

    final_state, _functions = run_review()
    roles = roles_of(calls)

    for agent in REVIEW_AGENTS:
        assert roles.count(agent) == 1, f"{agent} should run exactly once"
    assert len(final_state["findings"]) == len(REVIEW_AGENTS), "one finding per agent, merged"
    assert "Empty hash accepted" in final_state["final_review_markdown"]
    print("OK - findings from all review agents merged and rendered once")


def test_low_judge_score_stops_after_max_attempts(monkeypatch):
    calls = install_fakes(
        monkeypatch,
        {"decomposer": DECOMPOSER_REPLY, "judge": LOW_JUDGE_REPLY, "agent": CLEAN_AGENT_REPLY},
    )

    final_state, _functions = run_review()
    roles = roles_of(calls)

    assert final_state["retrieval_attempts"] == MAX_RETRIEVAL_ATTEMPTS
    assert roles.count("decomposer") == MAX_RETRIEVAL_ATTEMPTS
    assert roles.count("judge") == MAX_RETRIEVAL_ATTEMPTS
    for agent in REVIEW_AGENTS:
        assert roles.count(agent) == 1, f"{agent} should still run once after retries give up"
    assert "No issues found" in final_state["final_review_markdown"]
    print(f"OK - retrieval retried {MAX_RETRIEVAL_ATTEMPTS} times, then the review finished")


def test_agent_prompts_contain_code_of_hits_and_graph_neighbours(monkeypatch):
    calls = install_fakes(
        monkeypatch,
        {"decomposer": DECOMPOSER_REPLY, "judge": HIGH_JUDGE_REPLY, "agent": CLEAN_AGENT_REPLY},
    )

    final_state, functions = run_review()

    # verify_password is the search hit; AuthService.login is only
    # reachable as its caller through the knowledge graph.
    hit_block = f"- verify_password ({AUTH_PATH}):\n{functions['verify_password'].code}"
    neighbour_block = f"- AuthService.login ({AUTH_PATH}):\n{functions['AuthService.login'].code}"

    agent_prompts = [prompt for role, prompt in calls if role in REVIEW_AGENTS]
    assert len(agent_prompts) == len(REVIEW_AGENTS)
    for prompt in agent_prompts:
        assert hit_block in prompt, "agent prompt is missing the search hit's code"
        assert neighbour_block in prompt, "agent prompt is missing the graph neighbour's code"

    for chunk in final_state["retrieved_context"]:
        assert chunk["code"].strip(), f"{chunk['function_name']} reached the agents without code"
    print("OK - every agent prompt contains real code for the hit and its graph caller")


def test_retrieved_context_has_no_duplicate_chunks(monkeypatch):
    install_fakes(
        monkeypatch,
        {"decomposer": DECOMPOSER_REPLY, "judge": HIGH_JUDGE_REPLY, "agent": CLEAN_AGENT_REPLY},
    )

    final_state, _functions = run_review()
    keys = [(chunk["file_path"], chunk["function_name"]) for chunk in final_state["retrieved_context"]]

    assert len(keys) >= 2, f"expected the hit plus its caller, got {keys}"
    assert len(keys) == len(set(keys)), f"duplicate chunks in retrieved_context: {keys}"
    print(f"OK - {len(keys)} unique chunks: {keys}")


def test_none_llm_content_does_not_crash(monkeypatch):
    calls = install_fakes(monkeypatch, {"decomposer": None, "judge": None, "agent": None})

    final_state, _functions = run_review()
    roles = roles_of(calls)

    # decomposer falls back to a diff-based query, the judge scores
    # 0.0 (so retrieval retries to the cap), agents yield no findings
    assert final_state["retrieval_attempts"] == MAX_RETRIEVAL_ATTEMPTS
    for agent in REVIEW_AGENTS:
        assert roles.count(agent) == 1
    assert final_state["findings"] == []
    assert "No issues found" in final_state["final_review_markdown"]
    print("OK - None content from every LLM call degraded gracefully")


def test_reranker_scores_context_and_code_without_duplicates(monkeypatch):
    fake_model = FakeCrossEncoder()
    monkeypatch.setattr("rag.reranker._get_model", lambda: fake_model)
    candidates = [
        EnrichedResult(
            chunk_id=f"a.py::f{index}",
            metadata={
                "file_path": "a.py",
                "function_name": f"f{index}",
                "context": f"context sentence {index}",
                "code": f"def f{index}():\n    return token_{index}",
            },
            source="hybrid_search",
            rrf_score=0.1,
        )
        for index in range(4)
    ]

    ranked = rerank("token_2 query", candidates, top_k=3)

    assert len(ranked) == 3, "rerank must return exactly top_k chunks, no duplicated best chunk"
    assert len({chunk.chunk_id for chunk in ranked}) == 3
    assert ranked[0].chunk_id == "a.py::f2", "the best-overlapping chunk ranks first"
    documents = [document for _query, document in fake_model.pairs]
    assert "context sentence 2\ndef f2():\n    return token_2" in documents, (
        "the cross-encoder must see context + code, not just the name"
    )
    print("OK - reranker scores context + code and returns unique top_k chunks")


def test_delete_stale_ids_removes_old_format_ids():
    functions = parse_file(AUTH_PATH, AUTH_SOURCE)
    chunks = [ContextualChunk(function=fn, context=CONTEXTS[fn.qualified_name]) for fn in functions]
    collection = new_collection()
    store_chunks(collection, chunks, [fake_embed(chunk.embeddable_text) for chunk in chunks])

    # an id left over from before methods were class-qualified
    old_id = f"{AUTH_PATH}::login"
    collection.upsert(
        ids=[old_id],
        embeddings=[fake_embed("old login")],
        documents=["old login"],
        metadatas=[{"file_path": AUTH_PATH, "function_name": "login"}],
    )
    current_ids = [make_chunk_id(chunk) for chunk in chunks]

    deleted = delete_stale_ids(collection, current_ids)

    assert deleted == [old_id]
    assert set(collection.get(include=[])["ids"]) == set(current_ids)
    assert delete_stale_ids(collection, current_ids) == [], "nothing left to delete on a second run"
    print(f"OK - stale id removed: {deleted}")


if __name__ == "__main__":
    print("Run under pytest: this module relies on monkeypatch fixtures.")
