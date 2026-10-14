"""
review package - the review pipeline that runs inside the worker.

Contents: graph.py (the LangGraph orchestrator: which node runs when, the
parallel fan-out and the barrier), state.py (ReviewState shared by every
node), synthesizer.py (findings -> PR comment), retriever.py (legacy RAG
node), and agents/ (the security, logic and quality review agents).

Why this split: graph.py and state.py are the LangGraph-specific parts;
the reviewers and the synthesizer are plain Python that only follow the
node contract (take ReviewState, return a partial update), so they can be
tested and evaluated without building the graph.
"""
