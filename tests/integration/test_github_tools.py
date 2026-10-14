import os
import pytest
from dotenv import load_dotenv
load_dotenv()

from tools.github_tools import get_pr_diff


# A small, merged, historical PR on a stable public repo - unlikely to
# ever be deleted or force-pushed away.
STABLE_PUBLIC_REPO = "pallets/flask"
STABLE_PUBLIC_PR = 6013


requires_github_token = pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN"),
    reason="GITHUB_TOKEN not set in environment",
)


@requires_github_token
def test_get_pr_diff_returns_real_content():
    diff = get_pr_diff(STABLE_PUBLIC_REPO, STABLE_PUBLIC_PR)

    print(f"\nFetched diff ({len(diff)} chars) for {STABLE_PUBLIC_REPO}#{STABLE_PUBLIC_PR}")
    assert isinstance(diff, str)
    assert len(diff) > 0, "Should return non-empty diff content for a real PR"
    print("OK - get_pr_diff returned real diff content from the GitHub API")


if __name__ == "__main__":
    test_get_pr_diff_returns_real_content()
    print("\nAll github_tools tests passed.")
