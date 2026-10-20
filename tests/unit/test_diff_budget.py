from review.diff_budget import DEFAULT_MAX_DIFF_CHARS, fit_diff, max_diff_chars, split_diff_files
from review.synthesizer import synthesizer_node


def file_diff(path: str, added: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n+{added}"


def test_files_are_kept_whole_and_a_big_one_does_not_hide_the_rest():
    small_a, big, small_b = file_diff("a.py", "x = 1"), file_diff("big.py", "y" * 400), file_diff("b.py", "z = 2")

    kept, dropped = fit_diff("\n".join([small_a, big, small_b]), max_chars=200)

    assert kept == f"{small_a}\n{small_b}" and dropped == ["big.py"]
    assert [path for path, _text in split_diff_files(kept)] == ["a.py", "b.py"], "never a file cut in half"
    print("OK - whole files only; a file over the budget is skipped, the next ones still fit")


def test_the_budget_comes_from_the_environment_with_a_safe_default(monkeypatch):
    monkeypatch.setenv("REVIEW_MAX_DIFF_CHARS", "5000")
    assert max_diff_chars() == 5000
    monkeypatch.setenv("REVIEW_MAX_DIFF_CHARS", "lots")
    assert max_diff_chars() == DEFAULT_MAX_DIFF_CHARS
    print("OK - REVIEW_MAX_DIFF_CHARS overrides, a bad value falls back")


def test_the_review_names_the_files_it_did_not_see():
    state = {"findings": [], "failed_agents": [], "pr_diff": file_diff("a.py", "x = 1"), "changed_files": ["a.py"],
             "unreviewed_files": ["data/huge_fixture.py", "evil`name.py"]}

    markdown = synthesizer_node(state)["final_review_markdown"]

    assert "Not reviewed:** 2 file(s)" in markdown and "data/huge_fixture.py" in markdown
    assert "``evil`name.py``" in markdown, "file names are shown as escaped code"
    assert "Nice work" not in markdown, "no all-clear while files were left out"
    print("OK - unreviewed files are listed and the clean verdict is toned down")
