"""
Offline end-to-end test for the review pipeline (agents/graph.py).

Runs the REAL compiled LangGraph - retriever (decompose -> hybrid
search -> graph expansion -> rerank -> judge), the 3 review agents,
join_agents and the synthesizer - over a tiny in-memory index, and
checks the things that broke in production: the graph terminates
(including for a clean PR), the retry loop is bounded, agent prompts
contain real code for search hits AND graph neighbours, retrieved
chunks are unique, and a None LLM response doesn't crash anything.

What is real: tree-sitter parsing, the knowledge graph, an ephemeral
ChromaDB collection, BM25 + RRF fusion, graph enrichment, reranker
ordering, the routing, every agent's prompt building and parsing, and
the synthesizer.

What is faked (no network, no API keys):
- Groq: every _build_groq_client / _build_client returns a FakeLLM
  that returns canned content per role and RECORDS each prompt.
- Voyage: rag.hybrid_search.embed_query is a deterministic hashed
  bag-of-words vector, the same function used to embed the chunks.
- Gemini: ContextualChunks are built directly with fixed sentences.
- The cross-encoder: rag.reranker._get_model returns a fake whose
  predict() scores (query, document) pairs by token overlap.

agents.retriever.hybrid_search is wrapped to search with top_k=1
(still the real hybrid_search). With 7 functions in the index, the
default top_k=10 would return every function as a direct hit, and no
chunk would ever arrive through graph expansion - the path we need to
test. With top_k=1 the single hit is BM25's best match
(verify_password), and AuthService.login arrives only as its caller.
"""

import math
import re
import uuid
import zlib
from types import SimpleNamespace

import chromadb
from chromadb.config import Settings

import rag.hybrid_search as hybrid_search_module
from agents.graph import build_graph, DOMAIN_AGENTS
from agents.retriever import MAX_RETRIEVAL_ATTEMPTS
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


def _tokens(text: str) -> list[str]:
    """Lowercase identifier-ish tokens, shared by the fake embedder and reranker."""
    return re.findall(r"[a-z0-9_]+", text.lower())


def fake_embed(text: str) -> list[float]:
    """
    Deterministic stand-in for Voyage: a hashed bag-of-words vector,
    normalized. crc32 is used instead of hash() because Python's str
    hash changes between processes.
    """
    vector = [0.0] * EMBEDDING_DIMENSION
    for token in _tokens(text):
        vector[zlib.crc32(token.encode()) % EMBEDDING_DIMENSION] += 1.0
    vector[-1] += 1.0  # never an all-zero vector
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector]


class FakeCrossEncoder:
    """Stand-in for the bge reranker: scores a pair by shared tokens and records pairs."""

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Returns one overlap score per (query, document) pair."""
        self.pairs.extend(pairs)
        return [float(len(set(_tokens(query)) & set(_tokens(document)))) for query, document in pairs]


class FakeLLM:
    """
    Stand-in for a Groq client. chat.completions.create() records
    (role, prompt) into the shared calls list and returns the canned
    content for this role (None is allowed, to test the None guard).
    """

    def __init__(self, role: str, calls: list, replies: dict) -> None:
        self.role = role
        self.calls = calls
        self.replies = replies
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model: str, messages: list[dict], temperature: float, **kwargs) -> SimpleNamespace:
        prompt = messages[0]["content"]
        self.calls.append((self.role, prompt))
        reply_key = "agent" if self.role in DOMAIN_AGENTS else self.role
        message = SimpleNamespace(content=self.replies[reply_key])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def narrow_hybrid_search(query, collection, all_documents, all_ids, top_k=10):
    """Runs the real hybrid_search with top_k=1 (see the module docstring for why)."""
    return hybrid_search_module.hybrid_search(query, collection, all_documents, all_ids, top_k=1)


def install_fakes(monkeypatch, replies: dict) -> list:
    """
    Replaces every external dependency where it is USED, and returns
    the shared list that collects (role, prompt) for every LLM call.

    Args:
        monkeypatch: pytest's monkeypatch fixture
        replies: canned content for "decomposer", "judge" and "agent"

    Returns:
        the list of recorded (role, prompt) tuples
    """
    calls: list = []
    # Other tests load the real .env; make sure no tracing call goes out.
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    for agent in DOMAIN_AGENTS:
        monkeypatch.setattr(
            f"agents.{agent}._build_groq_client",
            lambda role=agent: FakeLLM(role, calls, replies),
        )
    monkeypatch.setattr("agents.retriever._build_groq_client", lambda: FakeLLM("judge", calls, replies))
    monkeypatch.setattr("rag.query_decomposer._build_client", lambda: FakeLLM("decomposer", calls, replies))
    monkeypatch.setattr("rag.hybrid_search.embed_query", fake_embed)
    monkeypatch.setattr("rag.reranker._get_model", lambda: FakeCrossEncoder())
    monkeypatch.setattr("agents.retriever.hybrid_search", narrow_hybrid_search)
    return calls


def new_collection():
    """Creates an empty, uniquely named collection in an in-memory Chroma."""
    client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
    # EphemeralClient instances share one in-memory store per process,
    # so a unique name keeps tests from seeing each other's chunks.
    return client.get_or_create_collection(name=f"offline_{uuid.uuid4().hex}", embedding_function=None)


def build_index() -> tuple:
    """
    Builds the same four objects rag/ingestion.py returns, offline.

    Returns:
        (collection, all_documents, all_ids, graph, functions_by_qualified_name)
    """
    functions = parse_file(AUTH_PATH, AUTH_SOURCE) + parse_file(BILLING_PATH, BILLING_SOURCE)
    chunks = [ContextualChunk(function=fn, context=CONTEXTS[fn.qualified_name]) for fn in functions]

    collection = new_collection()
    store_chunks(collection, chunks, [fake_embed(chunk.embeddable_text) for chunk in chunks])

    all_documents = [chunk.embeddable_text for chunk in chunks]
    all_ids = [make_chunk_id(chunk) for chunk in chunks]
    graph = build_knowledge_graph(functions)
    return collection, all_documents, all_ids, graph, {fn.qualified_name: fn for fn in functions}


def build_state() -> dict:
    """Builds the same initial ReviewState app/worker.py passes to the graph."""
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


def run_review() -> tuple[dict, dict]:
    """
    Compiles the real graph over a fresh offline index and runs one review.

    Returns:
        (final_state, functions_by_qualified_name)
    """
    collection, all_documents, all_ids, graph, functions = build_index()
    compiled = build_graph(collection, all_documents, all_ids, graph)
    final_state = compiled.invoke(build_state(), config={"recursion_limit": RECURSION_LIMIT})
    return final_state, functions


def roles_of(calls: list) -> list[str]:
    """Returns just the role of every recorded LLM call."""
    return [role for role, _prompt in calls]


def test_clean_pr_terminates_and_calls_each_agent_once(monkeypatch):
    calls = install_fakes(
        monkeypatch,
        {"decomposer": DECOMPOSER_REPLY, "judge": HIGH_JUDGE_REPLY, "agent": CLEAN_AGENT_REPLY},
    )

    final_state, _functions = run_review()
    roles = roles_of(calls)

    for agent in DOMAIN_AGENTS:
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

    for agent in DOMAIN_AGENTS:
        assert roles.count(agent) == 1, f"{agent} should run exactly once"
    assert len(final_state["findings"]) == len(DOMAIN_AGENTS), "one finding per agent, merged"
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
    for agent in DOMAIN_AGENTS:
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

    agent_prompts = [prompt for role, prompt in calls if role in DOMAIN_AGENTS]
    assert len(agent_prompts) == len(DOMAIN_AGENTS)
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
    for agent in DOMAIN_AGENTS:
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
