from app.jobs.models import ReviewJob
from app.repo_checkouts import MAX_CACHED_CONTEXTS, RepoCheckouts


def job(repo: str, sha: str) -> ReviewJob:
    return ReviewJob(repo_full_name=repo, pr_number=1, head_sha=sha)


def test_each_job_gets_its_own_repo_at_its_commit_and_repeats_are_cached():
    fetched = []

    def checkout(repo, sha, root, token):
        fetched.append((repo, sha))
        return f"{root}/{repo}"

    checkouts = RepoCheckouts("/checkouts", checkout=checkout, build_graph=lambda path: f"graph of {path}")

    first = checkouts.tool_context(job("me/api", "a" * 40))
    other = checkouts.tool_context(job("me/web", "b" * 40))
    again = checkouts.tool_context(job("Me/API", "a" * 40))

    assert str(first.repo_root).replace("\\", "/") == "/checkouts/me/api" and first.code_graph == "graph of /checkouts/me/api"
    assert str(other.repo_root).replace("\\", "/") == "/checkouts/me/web", "a second repo, no setting needed"
    assert again is first and fetched == [("me/api", "a" * 40), ("me/web", "b" * 40)], "a retry reuses the checkout"
    print("OK - any repo from the event, checked out at its commit; repeats come from the cache")


def test_only_the_last_few_call_graphs_stay_in_memory():
    checkouts = RepoCheckouts("/r", checkout=lambda repo, sha, root, token: f"{root}/{repo}/{sha}", build_graph=lambda p: p)
    for index in range(MAX_CACHED_CONTEXTS + 2):
        checkouts.tool_context(job("me/repo", f"{index:040x}"))

    assert len(checkouts._contexts) == MAX_CACHED_CONTEXTS
    print(f"OK - at most {MAX_CACHED_CONTEXTS} call graphs are kept")


def test_the_worker_refuses_agent_models_whose_provider_is_not_configured(monkeypatch):
    from app.bootstrap import check_agent_model_providers

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    for name in ("REVIEW_AGENT_MODEL", "SECURITY_AGENT_MODEL", "LOGIC_AGENT_MODEL", "QUALITY_AGENT_MODEL", "FALLBACK_AGENT_MODEL"):
        monkeypatch.setenv(name, "ollama:qwen2.5-coder:7b")
    check_agent_model_providers()  # every model is local: no Groq key needed

    monkeypatch.setenv("FALLBACK_AGENT_MODEL", "openai/gpt-oss-20b")
    try:
        check_agent_model_providers()
    except RuntimeError as error:
        assert "GROQ_API_KEY" in str(error)
        print("OK - a model routed to Groq without a key stops the worker at startup")
    else:
        raise AssertionError("every review would fail on its first Groq call")
