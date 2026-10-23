from app.bootstrap import check_allowed_repos_match_checkout, repo_full_name_from_url


def test_the_repo_name_is_read_from_https_and_ssh_urls():
    assert repo_full_name_from_url("https://github.com/Owner/Repo.git") == "owner/repo"
    assert repo_full_name_from_url("https://github.com/owner/repo/") == "owner/repo"
    assert repo_full_name_from_url("git@github.com:owner/repo.git") == "owner/repo"
    assert repo_full_name_from_url("https://gitlab.com/owner/repo.git") is None
    print("OK - owner/repo from every GitHub clone URL form")


def test_the_worker_refuses_an_allowed_repo_it_has_no_checkout_of(monkeypatch):
    monkeypatch.setenv("TARGET_REPO_URL", "https://github.com/owner/repo.git")
    monkeypatch.setenv("ALLOWED_REPOS", "owner/repo, owner/other")

    try:
        check_allowed_repos_match_checkout()
    except RuntimeError as error:
        assert "owner/other" in str(error) and "owner/repo" in str(error)
        print("OK - a second allowed repo stops the worker at startup, with the fix in the message")
    else:
        raise AssertionError("a PR of owner/other would be reviewed against owner/repo's code")


def test_matching_or_unknown_checkouts_start_normally(monkeypatch):
    monkeypatch.setenv("ALLOWED_REPOS", "Owner/Repo")
    monkeypatch.setenv("TARGET_REPO_URL", "https://github.com/owner/repo")
    check_allowed_repos_match_checkout()

    monkeypatch.setenv("TARGET_REPO_URL", "")
    monkeypatch.setenv("ALLOWED_REPOS", "owner/a, owner/b")
    check_allowed_repos_match_checkout()  # REPO_PATH: the local checkout's name is unknown
    print("OK - the same repo (any case) or a local REPO_PATH checkout passes")


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
