"""
Streamlit dashboard entrypoint for the autonomous review system.

Inputs: a local repo path to index (once, cached across reruns) and
either a pasted diff or a GitHub owner/repo + PR number to fetch the
real diff for.
Outputs: runs the real agents/graph.py pipeline live and renders the
final review markdown produced by agents/synthesizer.py - a manual
trigger UI for testing the whole system without needing a real GitHub
webhook delivery.

Ingestion (rag/ingestion.py) is the expensive, quota-consuming part
(one Gemini call per function, one Voyage call per repo), so it's
wrapped in st.cache_resource: it only re-runs when repo_path changes,
not on every button click or Streamlit rerun.
"""

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from rag.ingestion import ingest_repository
from agents.graph import build_graph
from tools.github_tools import get_pr_diff


st.set_page_config(page_title="Autonomous Code Reviewer", page_icon="🤖")
st.title("🤖 Autonomous Code Reviewer")
st.caption("Manual trigger UI - runs the real LangGraph review pipeline.")


@st.cache_resource(show_spinner=False)
def get_compiled_graph(repo_path: str):
    """
    Ingests repo_path once and compiles the review graph, cached per
    repo_path so re-running a review doesn't repeat ingestion (and its
    Gemini/Voyage API calls) on every click.
    """
    collection, all_documents, all_ids, graph_index = ingest_repository(repo_path)
    return build_graph(collection, all_documents, all_ids, graph_index)


def run_review(compiled_graph, diff: str) -> str:
    """
    Invokes the compiled graph on a single diff and returns the final
    review markdown.
    """
    initial_state = {
        "pr_number": 0,
        "repo_full_name": "local/manual-trigger",
        "pr_diff": diff,
        "changed_files": [],
        "retrieved_context": [],
        "retrieval_quality_score": 0.0,
        "retrieval_attempts": 0,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "retriever",
    }
    final_state = compiled_graph.invoke(initial_state)
    return final_state["final_review_markdown"]


with st.sidebar:
    st.header("1. Index a repository")
    repo_path = st.text_input(
        "Local repo path",
        placeholder=r"C:\path\to\a\small\repo",
        help="A local checkout to parse and index. Keep it small - "
             "ingestion makes one Gemini call per function.",
    )
    index_clicked = st.button("Index repository", disabled=not repo_path)

st.header("2. Provide a diff to review")

diff_source = st.radio(
    "Diff source",
    ["Paste diff manually", "Fetch from a real GitHub PR"],
    horizontal=True,
)

diff_text = ""
if diff_source == "Paste diff manually":
    diff_text = st.text_area(
        "PR diff",
        height=200,
        placeholder="+ def reset_password(email):\n+     ...",
    )
else:
    col1, col2 = st.columns([3, 1])
    with col1:
        repo_full_name = st.text_input("owner/repo", placeholder="pallets/flask")
    with col2:
        pr_number = st.number_input("PR #", min_value=1, step=1, value=1)
    if st.button("Fetch diff from GitHub"):
        with st.spinner("Fetching diff via GitHub API..."):
            diff_text = get_pr_diff(repo_full_name, int(pr_number))
        st.session_state["fetched_diff"] = diff_text
    diff_text = st.session_state.get("fetched_diff", "")
    if diff_text:
        st.text_area("Fetched diff", value=diff_text, height=200, disabled=True)

run_clicked = st.button("Run review", type="primary", disabled=not diff_text)

if index_clicked:
    with st.spinner(f"Indexing {repo_path} (parsing, contextualizing, embedding)..."):
        get_compiled_graph(repo_path)
    st.success("Repository indexed. You can now run a review.")

if run_clicked:
    if not repo_path:
        st.error("Enter and index a repository path in the sidebar first.")
    else:
        with st.spinner("Running the review pipeline (retriever -> 5 agents -> synthesizer)..."):
            compiled_graph = get_compiled_graph(repo_path)
            review_markdown = run_review(compiled_graph, diff_text)
        st.header("Review result")
        st.markdown(review_markdown)
