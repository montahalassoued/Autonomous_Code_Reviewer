import os
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from app.jobs.models import ReviewJob
from review.agents.tools.tool_definition import ToolContext


DEFAULT_CHECKOUTS_ROOT = "./target_repo"
# A call graph holds every function of a repo; a few stay in memory for the reviews that follow (a retry, a re-push).
MAX_CACHED_CONTEXTS = 3


def checkouts_root() -> str:
    return os.environ.get("CHECKOUTS_ROOT", "").strip() or DEFAULT_CHECKOUTS_ROOT


# The agents' tools for one job: that repo at that commit, and its call graph. The repo comes from the event,
# so one worker reviews any repo it is allowed to.
class RepoCheckouts:

    def __init__(self, root: str, token_for: Callable[[ReviewJob], str | None] = lambda job: None,
                 checkout: Callable[..., str] | None = None, build_graph: Callable[[str], object] | None = None) -> None:
        from rag.ingestion import checkout_commit, ingest_repository  # heavy imports stay out of module import time

        self._root, self._token_for = root, token_for
        self._checkout = checkout or checkout_commit
        self._build_graph = build_graph or ingest_repository
        self._contexts: OrderedDict[tuple[str, str], ToolContext] = OrderedDict()

    def tool_context(self, job: ReviewJob) -> ToolContext:
        key = (job.repo_full_name.lower(), job.head_sha)
        if key in self._contexts:
            self._contexts.move_to_end(key)
            return self._contexts[key]
        path = self._checkout(job.repo_full_name, job.head_sha, self._root, self._token_for(job))
        context = ToolContext(repo_root=Path(path), code_graph=self._build_graph(path))
        self._contexts[key] = context
        if len(self._contexts) > MAX_CACHED_CONTEXTS:
            self._contexts.popitem(last=False)
        return context
