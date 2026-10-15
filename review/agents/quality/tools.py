from review.agents.tools.repo_files import READ_FILE, SEARCH_CODE
from review.agents.tools.related_tests import FIND_TESTS


# Quality looks for slow paths and duplicates in the repo, and checks tests before claiming one is missing.
QUALITY_TOOLS = (READ_FILE, SEARCH_CODE, FIND_TESTS)
