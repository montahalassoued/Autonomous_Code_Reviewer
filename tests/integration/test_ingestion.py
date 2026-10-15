import os
from dotenv import load_dotenv
load_dotenv()

from rag.ingestion import clone_repository


# A tiny, stable public repo - fast to clone, unlikely to ever disappear.
SMALL_PUBLIC_REPO_URL = "https://github.com/octocat/Hello-World.git"


def test_clone_repository_clones_a_real_repo(tmp_path):
    target_dir = str(tmp_path / "cloned_repo")

    result_path = clone_repository(SMALL_PUBLIC_REPO_URL, target_dir)

    assert result_path == target_dir
    assert os.path.isdir(os.path.join(target_dir, ".git"))
    print(f"\nOK - cloned {SMALL_PUBLIC_REPO_URL} into {target_dir}")


def test_clone_repository_pulls_instead_of_recloning(tmp_path):
    target_dir = str(tmp_path / "cloned_repo")

    clone_repository(SMALL_PUBLIC_REPO_URL, target_dir)
    # Calling it again on an existing checkout must pull, not fail or
    # re-clone from scratch - this is the path app/main.py hits on
    # every restart against an already-cloned target repo.
    clone_repository(SMALL_PUBLIC_REPO_URL, target_dir)

    assert os.path.isdir(os.path.join(target_dir, ".git"))
    print("OK - calling clone_repository again on an existing checkout pulls instead of failing")


if __name__ == "__main__":
    print("Run under pytest: these tests use the tmp_path fixture.")
