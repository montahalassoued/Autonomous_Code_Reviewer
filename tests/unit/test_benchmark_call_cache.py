from eval.benchmark.call_cache import CallCache


def test_answers_survive_a_restart_and_errors_are_not_kept(tmp_path):
    path = tmp_path / "calls.jsonl"
    key = CallCache.key("groq:m", 0.1, "prompt", 1)
    CallCache(path).put(key, {"text": "[]", "error": None})
    CallCache(path).put(CallCache.key("groq:m", 0.1, "other", 1), {"text": "", "error": "RateLimitError"})

    reloaded = CallCache(path)

    assert reloaded.get(key) == {"text": "[]", "error": None}
    assert reloaded.get(CallCache.key("groq:m", 0.1, "other", 1)) is None, "a failed call is retried next time"
    print("OK - successful answers are reused across runs")


def test_the_key_changes_with_prompt_model_and_run():
    base = CallCache.key("groq:m", 0.1, "prompt", 1)

    assert base != CallCache.key("groq:m", 0.1, "prompt v2", 1), "a prompt change invalidates the cache"
    assert base != CallCache.key("groq:other", 0.1, "prompt", 1)
    assert base != CallCache.key("groq:m", 0.1, "prompt", 2), "each run is its own sample"
    print("OK - nothing stale is reused")
