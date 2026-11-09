import os

import git

from code_index.ingestion import checkout_commit


# A tiny, stable public repo - fast to fetch, unlikely to ever disappear.
SMALL_PUBLIC_REPO = "octocat/Hello-World"


def head_sha() -> str:
    return git.cmd.Git().ls_remote(f"https://github.com/{SMALL_PUBLIC_REPO}.git", "HEAD").split()[0]


def test_a_real_github_commit_is_fetched_and_checked_out(tmp_path):
    sha = head_sha()

    path = checkout_commit(SMALL_PUBLIC_REPO, sha, str(tmp_path))

    assert git.Repo(path).head.commit.hexsha == sha
    assert os.path.isfile(os.path.join(path, "README"))
    print(f"\nOK - fetched {SMALL_PUBLIC_REPO} at {sha[:7]} into {path}")


def test_a_second_review_of_the_same_repo_reuses_its_folder(tmp_path):
    sha = head_sha()

    first = checkout_commit(SMALL_PUBLIC_REPO, sha, str(tmp_path))
    second = checkout_commit(SMALL_PUBLIC_REPO, sha, str(tmp_path))

    assert first == second and git.Repo(second).head.commit.hexsha == sha
    print("OK - the existing checkout is fetched into, not cloned again")


if __name__ == "__main__":
    print("Run under pytest: these tests use the tmp_path fixture.")
